"""``viam-labs:camera-tools:scanout``: a camera with one source per connected
monitor, each showing what that monitor is displaying right now (drm.py).

An unfiltered GetImages returns every monitor, which is how the Viam app's
source picker discovers them (as the RealSense module does with color and
depth); ``filter_source_names`` picks monitors by connector name
(``HDMI-A-1``, ``DP-2``, ...). The first source is the default view.
"""
from __future__ import annotations

import asyncio
import io
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from google.protobuf.timestamp_pb2 import Timestamp
from viam.components.camera import Camera
from viam.media.video import CameraMimeType, NamedImage
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName, ResponseMetadata
from viam.proto.component.camera import GetPropertiesResponse
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model, ModelFamily
from viam.utils import struct_to_dict

from .drm import Card


class Scanout(Camera, EasyResource):
    MODEL: ClassVar[Model] = Model(ModelFamily("viam-labs", "camera-tools"), "scanout")

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.card_path: Optional[str] = None
        self.quality = 90
        self._card: Optional[Card] = None

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> "Scanout":
        instance = cls(config.name)
        instance.reconfigure(config, dependencies)
        return instance

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        attrs = struct_to_dict(config.attributes)
        card = attrs.get("card")
        if card is not None and not str(card).startswith("/dev/dri/card"):
            raise ValueError("card must be a /dev/dri/cardN path, not %r" % card)
        quality = int(attrs.get("jpeg_quality", 90))
        if not 1 <= quality <= 100:
            raise ValueError("jpeg_quality must be 1-100, not %d" % quality)
        return [], []

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> None:
        attrs = struct_to_dict(config.attributes)
        self.card_path = str(attrs["card"]) if attrs.get("card") else None
        self.quality = int(attrs.get("jpeg_quality", 90))
        self._close_card()  # reopened on the next read, possibly a different card

    def _open(self) -> Card:
        if self._card is None:
            self._card = Card(self.card_path)
        return self._card

    def _close_card(self) -> None:
        if self._card is not None:
            self._card.close()
            self._card = None

    def _jpeg(self, connector: str) -> bytes:
        buf = io.BytesIO()
        self._open().capture(connector).rgb().save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    async def get_images(
        self,
        *,
        filter_source_names: Optional[Sequence[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> Tuple[Sequence[NamedImage], ResponseMetadata]:
        run = asyncio.get_running_loop().run_in_executor
        try:
            connected = await run(None, lambda: self._open().connectors())
        except OSError:
            self._close_card()  # card went away (driver reload): reopen next time
            raise
        wanted = [n for n in (filter_source_names or connected) if n in connected]
        if not wanted:
            raise Exception("unknown source %s; connected monitors: %s"
                            % (list(filter_source_names or []), connected or "none"))
        images = []
        for name in wanted:
            images.append(NamedImage(name, await run(None, self._jpeg, name), CameraMimeType.JPEG))
        captured = Timestamp()
        captured.GetCurrentTime()
        return images, ResponseMetadata(captured_at=captured)

    async def get_point_cloud(
        self, *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Tuple[bytes, str]:
        raise NotImplementedError("scanout has no point cloud")

    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> GetPropertiesResponse:
        return GetPropertiesResponse(supports_pcd=False, mime_types=["image/jpeg"])

    async def close(self) -> None:
        self._close_card()
