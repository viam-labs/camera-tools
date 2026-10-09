import asyncio

import av
from PIL import Image

from camera_tools.recording import Session


def source(color, size, fail_every=0):
    n = {"i": 0}

    async def grab():
        n["i"] += 1
        if fail_every and n["i"] % fail_every == 0:
            raise RuntimeError("frame dropped")
        return Image.new("RGB", size, color)

    return grab


async def test_records_each_source_and_a_combined_clip(tmp_path):
    session = Session([("main", source("red", (320, 180))), ("debug", source("blue", (240, 180), fail_every=3))],
                      str(tmp_path), fps=20, max_seconds=60, combined_height=180)
    asyncio.get_running_loop().call_later(0.5, session.stop)
    result = await session.run()

    assert set(result.files) == {"combined", "main", "debug"}
    assert result.dropped["debug"] == result.frames // 3 and "main" not in result.dropped
    sizes = {}
    for label, path in result.files.items():
        with av.open(path) as f:
            frames = list(f.decode(video=0))
        sizes[label] = (frames[0].width, frames[0].height)
        assert len(frames) == result.frames  # a failed grab repeats the last frame
    assert sizes == {"main": (320, 180), "debug": (240, 180), "combined": (560, 180)}


def test_clip_timestamps_survive_muxing_mid_stream(tmp_path):
    # Long enough for x264 to emit packets (and the MP4 header to be
    # written) before close; frame times must stay monotonic in the file.
    from camera_tools.recording import Clip

    clip = Clip(str(tmp_path / "c.mp4"))
    for i in range(80):
        clip.add(Image.new("RGB", (64, 48), (i * 3 % 255, 0, 0)), i * 200)
    path = clip.close()
    with av.open(path) as f:
        times = [fr.time for fr in f.decode(video=0)]
    assert len(times) == 80 and times == sorted(times) and abs(times[-1] - 15.8) < 0.01
