import asyncio

import av
from PIL import Image

from camera_tools.recording import Session, stitch


def source(color, size, fail_every=0):
    n = {"i": 0}

    async def grab():
        n["i"] += 1
        if fail_every and n["i"] % fail_every == 0:
            raise RuntimeError("frame dropped")
        return Image.new("RGB", size, color)

    return grab


async def test_records_each_source_then_stitches_them(tmp_path):
    session = Session([("main", source("red", (320, 180))), ("debug", source("blue", (240, 180), fail_every=3))],
                      str(tmp_path), fps=20, max_seconds=60)
    asyncio.get_running_loop().call_later(0.5, session.stop)
    result = await session.run()

    assert set(result.files) == {"main", "debug"}  # nothing combined while recording
    result.files["combined"] = stitch(list(result.files.values()), session.combined_path, height=180)
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


def test_stitch_waits_for_every_clip_and_matches_frames_by_time(tmp_path):
    from camera_tools.recording import Clip

    early, late = Clip(str(tmp_path / "early.mp4")), Clip(str(tmp_path / "late.mp4"))
    for i in range(30):
        early.add(Image.new("RGB", (64, 48), "red"), i * 200)
        if i >= 10:  # starts 2 s later
            late.add(Image.new("RGB", (32, 48), "blue"), i * 200)
    out = stitch([early.close(), late.close()], str(tmp_path / "combined.mp4"), height=48)
    with av.open(out) as f:
        frames = list(f.decode(video=0))
    assert (frames[0].width, frames[0].height) == (96, 48)
    assert len(frames) == 20 and abs(frames[0].time - 2.0) < 0.01 and abs(frames[-1].time - 5.8) < 0.01
    px = frames[5].to_image()
    assert px.getpixel((10, 24))[0] > 200 and px.getpixel((80, 24))[2] > 200  # red left, blue right
