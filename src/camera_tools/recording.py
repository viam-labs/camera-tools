"""Record a set of image sources to H.264 MP4, one clip per source, and
afterwards stitch those clips side by side into one (``stitch``).

No Viam imports: a source is a label and an async function returning a PIL
image, so this runs (and is tested) without a robot. Frames are stamped
with wall-clock time, so a slow or failed grab stretches a frame instead of
speeding the clip up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from fractions import Fraction
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger(__name__)

Grab = Callable[[], Awaitable[Any]]  # -> PIL.Image
MS = Fraction(1, 1000)  # frame timestamps are milliseconds


def _even(n: int) -> int:
    return max(2, n - n % 2)


def slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "source"


class Clip:
    """One MP4 file. Size is fixed by the first frame; later frames that
    differ are resized to fit."""

    def __init__(self, path: str, crf: int = 26) -> None:
        self.path = path
        self.crf = crf
        self.frames = 0
        self._container: Any = None
        self._stream: Any = None
        self.size: Optional[Tuple[int, int]] = None

    def add(self, img: Any, ms: int) -> None:
        import av

        img = img.convert("RGB")
        if self._container is None:
            self.size = (_even(img.width), _even(img.height))
            self._container = av.open(self.path, mode="w", options={"movflags": "+faststart"})
            s = self._container.add_stream("libx264", rate=1000)
            s.width, s.height = self.size
            s.pix_fmt = "yuv420p"
            s.time_base = MS
            s.options = {"crf": str(self.crf), "preset": "superfast", "tune": "stillimage"}
            self._stream = s
        if img.size != self.size:
            img = img.resize(self.size)
        frame = av.VideoFrame.from_image(img)
        frame.pts = ms
        # Not the stream's time base: the MP4 muxer changes that (to
        # 1/16000) once the header is written, which would rescale every
        # later timestamp and send them backwards.
        frame.time_base = MS
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        self.frames += 1

    def close(self) -> Optional[str]:
        """Finish the file; its path, or None if no frame was ever added."""
        if self._container is None:
            return None
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()
        self._container = None
        return self.path


def side_by_side(images: Sequence[Any], height: int) -> Any:
    """Scale every image to ``height`` (keeping aspect) and tile left to right."""
    from PIL import Image

    scaled = [im.convert("RGB").resize((max(1, round(im.width * height / im.height)), height)) for im in images]
    out = Image.new("RGB", (_even(sum(im.width for im in scaled)), _even(height)))
    x = 0
    for im in scaled:
        out.paste(im, (x, 0))
        x += im.width
    return out


@dataclass
class Result:
    started_at: float
    seconds: float
    files: Dict[str, str] = field(default_factory=dict)  # label ("combined" or a source) -> path
    frames: int = 0
    errors: Dict[str, str] = field(default_factory=dict)  # source label -> last grab error
    dropped: Dict[str, int] = field(default_factory=dict)  # source label -> failed grabs


class Session:
    """Grabs every source ``fps`` times a second until ``stop()`` or
    ``max_seconds``, writing ``<out_dir>/<stamp>-<source>.mp4``. The
    side-by-side clip is not encoded live (it's the most expensive one):
    ``stitch`` builds it from these files afterwards, at ``combined_path``."""

    def __init__(
        self,
        sources: Sequence[Tuple[str, Grab]],
        out_dir: str,
        fps: float = 5.0,
        max_seconds: float = 300.0,
    ) -> None:
        self.sources = list(sources)
        self.out_dir = out_dir
        self.fps = fps
        self.max_seconds = max_seconds
        self._stop = asyncio.Event()
        self.started_at = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_at))
        os.makedirs(out_dir, exist_ok=True)
        self._clips = {label: Clip(os.path.join(out_dir, "%s-%s.mp4" % (stamp, slug(label))))
                       for label, _ in self.sources}
        self.combined_path = os.path.join(out_dir, "%s-combined.mp4" % stamp)
        self._last: Dict[str, Any] = {}
        self.errors: Dict[str, str] = {}
        self.dropped: Dict[str, int] = {}
        self.frames = 0

    def stop(self) -> None:
        self._stop.set()

    async def _grab(self, label: str, grab: Grab) -> Optional[Any]:
        try:
            return await grab()
        except Exception as e:
            if self.errors.get(label) != repr(e):  # once per distinct error
                LOGGER.warning("recording: %s failed: %r (repeating its last frame)", label, e)
            self.errors[label] = repr(e)
            self.dropped[label] = self.dropped.get(label, 0) + 1
            return None

    async def run(self) -> Result:
        period = 1.0 / max(self.fps, 0.1)
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        while not self._stop.is_set() and time.monotonic() - t0 < self.max_seconds:
            tick = time.monotonic()
            images = await asyncio.gather(*(self._grab(label, g) for label, g in self.sources))
            ms = int((tick - t0) * 1000)
            for (label, _), img in zip(self.sources, images):
                if img is not None:
                    self._last[label] = img
            await loop.run_in_executor(None, self._encode, ms)
            self.frames += 1
            try:
                await asyncio.wait_for(self._stop.wait(), max(0.0, period - (time.monotonic() - tick)))
            except asyncio.TimeoutError:
                pass
        seconds = time.monotonic() - t0
        files = await loop.run_in_executor(None, self._close)
        return Result(self.started_at, seconds, files, self.frames, dict(self.errors), dict(self.dropped))

    def _encode(self, ms: int) -> None:
        for label, clip in self._clips.items():
            if label in self._last:  # a source that failed repeats its last frame
                clip.add(self._last[label], ms)

    def _close(self) -> Dict[str, str]:
        files = {}
        for label, clip in self._clips.items():
            path = clip.close()
            if path:
                files[label] = path
        return files


def stitch(paths: Sequence[str], out_path: str, height: int = 1080) -> Optional[str]:
    """Tile clips side by side (in order, each scaled to ``height``) into
    ``out_path``. Frames are matched by timestamp: each output frame shows,
    for every clip, its latest frame at that moment, so clips that started
    late or repeat frames stay in sync. Output starts once every clip has a
    frame. Decodes lazily, one frame per clip in memory. Returns
    ``out_path``, or None if the clips never overlap."""
    import av

    containers = [av.open(p) for p in paths]
    try:
        streams = [iter(c.decode(video=0)) for c in containers]
        nxt: List[Any] = [next(s, None) for s in streams]
        cur: List[Any] = [None] * len(paths)
        out = Clip(out_path)
        while any(f is not None for f in nxt):
            t = min(f.time for f in nxt if f is not None)
            for i, f in enumerate(nxt):
                while f is not None and f.time <= t + 1e-6:
                    cur[i] = f.to_image()
                    f = next(streams[i], None)
                nxt[i] = f
            if all(img is not None for img in cur):
                out.add(side_by_side(cur, height), int(round(t * 1000)))
        return out.close()
    finally:
        for c in containers:
            c.close()
