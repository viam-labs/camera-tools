"""``viam:camera-tools:recorder``: a two-position switch that records
camera streams to H.264 MP4 while it is on, and posts the clips to Slack
when it is switched off.

Position 1 starts recording; 0 stops it. Each configured source is a
camera and optionally one of its named images (``stream``). Every source
gets its own clip and, with more than one source, there is also a combined
clip with all of them side by side. A recording stops on its own after
``max_seconds``.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from viam.components.camera import Camera
from viam.components.switch import Switch
from viam.logging import getLogger
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model, ModelFamily
from viam.services.generic import Generic
from viam.utils import ValueTypes, struct_to_dict

from .recording import Result, Session

LOGGER = getLogger("camera_tools.recorder")

POSITIONS = ["Stopped", "Recording"]


class Source:
    def __init__(self, attrs: Mapping[str, Any]) -> None:
        self.camera = str(attrs.get("camera") or "")
        if not self.camera:
            raise ValueError("every source needs a camera")
        self.stream = str(attrs["stream"]) if attrs.get("stream") else None
        self.label = "%s-%s" % (self.camera, self.stream) if self.stream else self.camera


class Settings:
    def __init__(self, attrs: Mapping[str, Any]) -> None:
        raw = attrs.get("sources") or []
        if not isinstance(raw, list) or not raw:
            raise ValueError('sources must list at least one {"camera": ..., "stream": ...}')
        self.sources = [Source(s if isinstance(s, Mapping) else {"camera": s}) for s in raw]
        labels = [s.label for s in self.sources]
        if len(set(labels)) != len(labels):
            raise ValueError("sources repeat a camera/stream pair: %s" % labels)
        self.fps = float(attrs.get("fps", 5.0))
        self.max_seconds = float(attrs.get("max_seconds", 300.0))
        if not 0 < self.fps <= 30 or self.max_seconds <= 0:
            raise ValueError("fps must be in (0, 30] and max_seconds positive")
        self.combined = bool(attrs.get("combined", True))
        self.slack = str(attrs["slack"]) if attrs.get("slack") else None
        self.slack_channel = str(attrs.get("slack_channel") or "")
        if self.slack and not self.slack_channel:
            raise ValueError("slack_channel is required with slack")
        self.max_upload_mb = float(attrs.get("max_upload_mb", 25.0))
        self.out_dir = str(attrs.get("out_dir") or os.path.join(
            os.environ.get("VIAM_MODULE_DATA") or tempfile.gettempdir(), "recordings"))

    def dependencies(self) -> Tuple[List[str], List[str]]:
        """(required cameras, optional slack service)."""
        return sorted({s.camera for s in self.sources}), [self.slack] if self.slack else []


class Recorder(Switch, EasyResource):
    MODEL: ClassVar[Model] = Model(ModelFamily("viam", "camera-tools"), "recorder")

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.settings: Optional[Settings] = None
        self.deps: Dict[str, ResourceBase] = {}
        self._session: Optional[Session] = None
        self._task: Optional[asyncio.Task] = None
        self.last: Optional[Result] = None
        self.last_error: Optional[str] = None

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> "Recorder":
        instance = cls(config.name)
        instance.reconfigure(config, dependencies)
        return instance

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        return Settings(struct_to_dict(config.attributes)).dependencies()

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> None:
        # A recording in progress keeps the sources it started with.
        self.settings = Settings(struct_to_dict(config.attributes))
        self.deps = {rn.name: res for rn, res in dependencies.items()}

    # --- switch ---

    @property
    def recording(self) -> bool:
        return self._task is not None and not self._task.done()

    async def get_position(self, *, extra: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None,
                           **kwargs) -> int:
        return 1 if self.recording else 0

    async def get_number_of_positions(self, *, extra: Optional[Mapping[str, Any]] = None,
                                      timeout: Optional[float] = None, **kwargs) -> Tuple[int, Sequence[str]]:
        return len(POSITIONS), POSITIONS

    async def set_position(self, position: int, *, extra: Optional[Mapping[str, Any]] = None,
                           timeout: Optional[float] = None, **kwargs) -> None:
        if position not in (0, 1):
            raise ValueError("position must be 0 (stop) or 1 (record)")
        if position == 1 and not self.recording:
            self._start()
        elif position == 0 and self._session is not None:
            self._session.stop()  # the task finishes the files and posts them

    # --- recording ---

    def _grabber(self, source: Source):
        camera = cast(Camera, self.deps.get(source.camera))
        if camera is None:
            raise ValueError("camera %r is not available" % source.camera)

        async def grab():
            from PIL import Image

            images, _ = await camera.get_images(filter_source_names=[source.stream] if source.stream else None)
            if not images:
                raise RuntimeError("%s returned no image" % source.label)
            img = images[0]
            return Image.open(io.BytesIO(img.data)).convert("RGB")

        return grab

    def _start(self) -> None:
        cfg = self.settings
        assert cfg is not None
        session = Session([(s.label, self._grabber(s)) for s in cfg.sources], cfg.out_dir,
                          fps=cfg.fps, max_seconds=cfg.max_seconds, combined=cfg.combined)
        self._session = session
        self.last_error = None
        LOGGER.info("recording %s at %g fps (max %gs)", ", ".join(s.label for s in cfg.sources),
                    cfg.fps, cfg.max_seconds)
        self._task = asyncio.get_running_loop().create_task(self._record(session))

    async def _record(self, session: Session) -> None:
        try:
            result = await session.run()
            self.last = result
            LOGGER.info("recorded %.1fs, %d frames: %s", result.seconds, result.frames, result.files)
            await self._post(result)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.last_error = repr(e)
            LOGGER.exception("recording failed")

    async def _post(self, result: Result) -> None:
        cfg = self.settings
        if cfg is None or not cfg.slack:
            return
        slack = cast(Generic, self.deps.get(cfg.slack))
        if slack is None:
            LOGGER.warning("slack service %r is not available; clips kept in %s", cfg.slack, cfg.out_dir)
            return
        minutes, seconds = divmod(int(round(result.seconds)), 60)
        for label, path in result.files.items():  # "combined" first, then each source, one post each
            mb = os.path.getsize(path) / 1e6
            if mb > cfg.max_upload_mb:
                LOGGER.warning("not posting %s: %.1f MB is over max_upload_mb (%g); kept at %s",
                               label, mb, cfg.max_upload_mb, path)
                continue
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            text = "Screen recording, %d:%02d (%s)" % (minutes, seconds, label)
            cmd: Dict[str, ValueTypes] = {"command": "send_file", "channel": cfg.slack_channel,
                                          "content_base64": content, "filename": os.path.basename(path),
                                          "text": text, "alt_text": text}
            resp = await slack.do_command(cmd) or {}
            if resp.get("error") or resp.get("ok") is False:
                raise RuntimeError("slack upload of %s failed: %s" % (label, resp.get("error") or resp))

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout: Optional[float] = None,
                         **kwargs) -> Mapping[str, ValueTypes]:
        last = self.last
        return {
            "recording": self.recording,
            "frames": self._session.frames if self.recording and self._session else None,
            "source_errors": dict(self._session.errors) if self._session else {},
            "dropped_frames": dict(self._session.dropped) if self._session else {},
            "last_files": dict(last.files) if last else {},
            "last_seconds": round(last.seconds, 1) if last else None,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        if self._session is not None and self.recording:
            self._session.stop()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 10)  # finish the files
            except Exception:
                pass
