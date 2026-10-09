"""Read back what each monitor is showing, from Linux KMS.

For every connected connector: connector -> encoder -> CRTC -> the
framebuffer that CRTC is scanning out right now -> map it and copy its
pixels. That is the hardware's side of the screen, whoever drew it (a
desktop, the console, a module writing /dev/fb0 or its own KMS buffers).

Read-only: the card is opened once and DRM master is dropped at once, so
this never holds the displays away from whatever drives them, and it never
asks a connector to re-probe. Needs root (CAP_SYS_ADMIN) to be handed the
framebuffer handles. Plain ioctls, no libdrm; layouts from
include/uapi/drm/drm.h and drm_mode.h.
"""
from __future__ import annotations

import ctypes
import fcntl
import glob
import mmap
import os
import struct
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional


def _io(nr: int) -> int:
    return (ord("d") << 8) | nr


def _iow(nr: int, size: int) -> int:
    return (1 << 30) | (size << 16) | (ord("d") << 8) | nr


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord("d") << 8) | nr


CARD_RES = struct.Struct("<4Q8I")
CONNECTOR = struct.Struct("<4Q12I")
MODEINFO = struct.Struct("<I10H3I32s")
ENCODER = struct.Struct("<5I")
CRTC = struct.Struct("<Q7I%ds" % MODEINFO.size)
FB_CMD = struct.Struct("<7I")
MAP_DUMB = struct.Struct("<IIQ")
GEM_CLOSE_ARG = struct.Struct("<II")

DROP_MASTER = _io(0x1F)
GEM_CLOSE = _iow(0x09, GEM_CLOSE_ARG.size)
GET_RESOURCES = _iowr(0xA0, CARD_RES.size)
GET_CRTC = _iowr(0xA1, CRTC.size)
GET_ENCODER = _iowr(0xA6, ENCODER.size)
GET_CONNECTOR = _iowr(0xA7, CONNECTOR.size)
GET_FB = _iowr(0xAD, FB_CMD.size)
MAP = _iowr(0xB3, MAP_DUMB.size)

CONNECTED = 1
# drm_connector_enum_list: how the kernel (and sysfs) names connector types
CONNECTOR_TYPES = ["Unknown", "VGA", "DVI-I", "DVI-D", "DVI-A", "Composite", "SVIDEO", "LVDS", "Component",
                   "DIN", "DP", "HDMI-A", "HDMI-B", "TV", "eDP", "Virtual", "DSI", "DPI", "Writeback", "SPI",
                   "USB"]


def _call(fd: int, req: int, st: struct.Struct, *fields) -> tuple:
    buf = bytearray(st.pack(*fields))
    fcntl.ioctl(fd, req, buf, True)
    return st.unpack(buf)


@dataclass
class Frame:
    """One monitor's scanout: XRGB8888 rows of ``stride`` bytes."""

    connector: str
    width: int
    height: int
    stride: int
    pixels: bytes

    def rgb(self):
        """As a PIL RGB image."""
        import numpy as np
        from PIL import Image

        px = np.frombuffer(self.pixels, dtype=np.uint8)[: self.stride * self.height]
        px = px.reshape(self.height, self.stride // 4, 4)[:, : self.width, 2::-1]  # BGRX -> RGB
        return Image.fromarray(np.ascontiguousarray(px))


def find_card() -> str:
    """The first /dev/dri/card* with a monitor connected (from sysfs, which
    doesn't make the connector re-probe)."""
    for card in sorted(glob.glob("/dev/dri/card*")):
        for status in glob.glob("/sys/class/drm/%s-*/status" % os.path.basename(card)):
            try:
                with open(status) as f:
                    if f.read().strip() == "connected":
                        return card
            except OSError:
                pass
    raise FileNotFoundError("no /dev/dri/card* has a monitor connected")


class Card:
    """A DRM card opened read-only for scanout capture."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or find_card()
        self._lock = threading.Lock()
        self._fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC)
        try:
            # The first opener of a card with no master becomes master; give
            # it back so whatever drives the monitors keeps (or can take) it.
            fcntl.ioctl(self._fd, DROP_MASTER)
        except OSError:
            pass  # not master: someone else drives the displays

    def _crtcs(self) -> Dict[str, int]:
        """Connected connector name -> the CRTC driving it (0 if none)."""
        fd = self._fd
        counts = _call(fd, GET_RESOURCES, CARD_RES, 0, 0, 0, 0, *([0] * 8))
        n = counts[6]
        ids = (ctypes.c_uint32 * max(n, 1))()
        got = _call(fd, GET_RESOURCES, CARD_RES, 0, 0, ctypes.addressof(ids), 0, 0, 0, n, 0, *([0] * 4))
        out: Dict[str, int] = {}
        for cid in list(ids)[: min(n, got[6])]:
            # count_modes stays 0 and we aren't master, so this doesn't probe.
            c = _call(fd, GET_CONNECTOR, CONNECTOR, 0, 0, 0, 0, *([0] * 4), cid, *([0] * 7))
            if c[11] != CONNECTED:
                continue
            kind = c[9]
            name = "%s-%d" % (CONNECTOR_TYPES[kind] if kind < len(CONNECTOR_TYPES) else "Unknown", c[10])
            crtc = 0
            if c[7]:  # its current encoder
                try:
                    crtc = _call(fd, GET_ENCODER, ENCODER, c[7], 0, 0, 0, 0)[2]
                except OSError:
                    pass
            out[name] = crtc
        return out

    def connectors(self) -> List[str]:
        """Connected monitors, in the kernel's order."""
        with self._lock:
            return list(self._crtcs())

    def capture(self, connector: str) -> Frame:
        with self._lock:
            crtcs = self._crtcs()
            if connector not in crtcs:
                raise KeyError("%s is not connected (connected: %s)" % (connector, ", ".join(crtcs) or "none"))
            crtc_id = crtcs[connector]
            if not crtc_id:
                raise RuntimeError("%s is connected but no CRTC drives it (monitor off?)" % connector)
            fd = self._fd
            fb_id = _call(fd, GET_CRTC, CRTC, 0, 0, crtc_id, 0, 0, 0, 0, 0, bytes(MODEINFO.size))[3]
            if not fb_id:
                raise RuntimeError("%s: CRTC %d is scanning out nothing" % (connector, crtc_id))
            _, w, h, pitch, bpp, _, handle = _call(fd, GET_FB, FB_CMD, fb_id, 0, 0, 0, 0, 0, 0)
            if not handle:
                raise PermissionError("%s: no handle for framebuffer %d (needs root)" % (connector, fb_id))
            try:
                if bpp != 32:
                    raise RuntimeError("%s: framebuffer is %d bpp; only 32 bpp reads back" % (connector, bpp))
                offset = _call(fd, MAP, MAP_DUMB, handle, 0, 0)[2]
                with mmap.mmap(fd, pitch * h, mmap.MAP_SHARED, mmap.PROT_READ, offset=offset) as mm:
                    pixels = bytes(mm)
            finally:
                try:
                    _call(fd, GEM_CLOSE, GEM_CLOSE_ARG, handle, 0)
                except OSError:
                    pass
            return Frame(connector, w, h, pitch, pixels)

    def close(self) -> None:
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
