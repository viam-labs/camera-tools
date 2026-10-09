import ctypes
import struct

import pytest

import camera_tools.drm as drm


def test_ioctl_numbers_match_the_kernel():
    assert (drm.GET_RESOURCES, drm.GET_CRTC, drm.GET_ENCODER, drm.GET_CONNECTOR) == (
        0xC04064A0, 0xC06864A1, 0xC01464A6, 0xC05064A7)
    assert (drm.GET_FB, drm.MAP, drm.GEM_CLOSE, drm.DROP_MASTER) == (0xC01C64AD, 0xC01064B3, 0x40086409, 0x641F)


class FakeCard:
    """HDMI-A-1 (connected, CRTC 50 scanning fb 100, 4x2 red) and DP-1
    (disconnected), answering the ioctls drm.Card makes."""

    def __init__(self):
        self.closed = []
        self.pixels = bytes([0, 0, 255, 0]) * 8  # BGRX red, stride 16

    def ioctl(self, fd, req, arg=0, mutate=True):
        if req == drm.DROP_MASTER:
            return 0
        if req == drm.GEM_CLOSE:
            self.closed.append(drm.GEM_CLOSE_ARG.unpack(arg)[0])
            return 0
        st = {drm.GET_RESOURCES: drm.CARD_RES, drm.GET_CONNECTOR: drm.CONNECTOR, drm.GET_ENCODER: drm.ENCODER,
              drm.GET_CRTC: drm.CRTC, drm.GET_FB: drm.FB_CMD, drm.MAP: drm.MAP_DUMB}[req]
        f = list(st.unpack(arg))
        if req == drm.GET_RESOURCES:
            if f[2] and f[6] >= 2:
                ctypes.memmove(f[2], struct.pack("<2I", 31, 32), 8)
            f[6] = 2
        elif req == drm.GET_CONNECTOR:
            hdmi = f[8] == 31
            f[7], f[9], f[10], f[11] = (40 if hdmi else 0), (11 if hdmi else 10), 1, (1 if hdmi else 2)
        elif req == drm.GET_ENCODER:
            f[2] = 50
        elif req == drm.GET_CRTC:
            f[3] = 100
        elif req == drm.GET_FB:
            f[1:] = [4, 2, 16, 32, 24, 7]
        arg[:] = st.pack(*f)
        return 0


@pytest.fixture
def card(monkeypatch):
    fake = FakeCard()
    monkeypatch.setattr(drm.fcntl, "ioctl", fake.ioctl)
    monkeypatch.setattr(drm.os, "open", lambda *a: 9)
    monkeypatch.setattr(drm.os, "close", lambda fd: None)

    class Map:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def __bytes__(self):
            return fake.pixels

    monkeypatch.setattr(drm.mmap, "mmap", Map)
    return fake, drm.Card("/dev/dri/card1")


def test_capture_reads_the_crtcs_framebuffer_and_closes_its_handle(card):
    fake, c = card
    assert c.connectors() == ["HDMI-A-1"]  # DP-1 is disconnected
    frame = c.capture("HDMI-A-1")
    assert (frame.width, frame.height, frame.stride) == (4, 2, 16)
    img = frame.rgb()
    assert img.size == (4, 2) and img.getpixel((3, 1)) == (255, 0, 0)
    assert fake.closed == [7]
    with pytest.raises(KeyError):
        c.capture("DP-1")
