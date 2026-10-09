import asyncio

from viam.module.module import Module

from camera_tools.recorder import Recorder  # noqa: F401  (registers the models)
from camera_tools.scanout import Scanout  # noqa: F401

if __name__ == "__main__":
    asyncio.run(Module.run_from_registry())
