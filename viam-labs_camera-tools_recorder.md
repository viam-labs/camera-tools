# Model viam-labs:camera-tools:recorder

A switch that records camera streams to H.264 MP4 while it is on. It's
meant for capturing what a machine's screens or cameras showed, so you can
share it afterwards.

- **Position 1 (`Recording`)** starts recording.
- **Position 0 (`Stopped`)** stops recording, finishes the files and, if
  configured, posts them to Slack.
- **Auto-stop:** a recording stops by itself after `max_seconds`.

The switch shows up in the Viam app's Control tab like any other switch.

## Output files

Each source gets its own clip, at its own resolution. With more than one
source there is also a **combined** clip: every source scaled to the same
height and placed side by side.

- **Location:** clips are always written to `out_dir`, by default
  `$VIAM_MODULE_DATA/recordings`. They are named
  `<YYYYmmdd-HHMMSS>-<camera>-<stream>.mp4` and `…-combined.mp4`.
- **Timestamps:** frames are stamped with wall-clock time, so the clip plays
  back in real time.
- **Failed grabs:** a source that fails a grab repeats its last frame. The
  recording carries on, and the failures are counted.

## Configuration

```json
{
  "sources": [
    {"camera": "scanout", "stream": "HDMI-A-1"},
    {"camera": "scanout", "stream": "HDMI-A-2"},
    {"camera": "webcam-4k"}
  ],
  "fps": 5,
  "max_seconds": 300,
  "slack": "slack",
  "slack_channel": "#build-package-buddy-testing"
}
```

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `sources` | list | **Required** | | What to record. Each entry is `{"camera": <camera name>, "stream": <named image>}`. `stream` picks one of the camera's sources, such as `HDMI-A-1` on a `scanout` camera or `debug` on package-buddy's display camera. Without `stream`, the camera's first image is used. |
| `fps` | number | Optional | `5` | Frames per second, from 0 to 30. Every source is grabbed this often. |
| `max_seconds` | number | Optional | `300` | Stop automatically after this long. |
| `combined` | bool | Optional | `true` | Also write the side-by-side clip when there is more than one source. |
| `out_dir` | string | Optional | `$VIAM_MODULE_DATA/recordings` | Where clips are written. |
| `slack` | string | Optional | | Name of a [slack-bot](https://app.viam.com/module/viam/slack-bot) generic service. When it is set, finished clips are also posted to Slack. |
| `slack_channel` | string | With `slack` | | Channel to post the clips to. |
| `max_upload_mb` | number | Optional | `25` | Clips larger than this are not posted to Slack. They are kept on disk and a warning is logged. |

The cameras are required dependencies. The Slack service is optional: if
it's missing, clips are only kept locally and a warning is logged.

## DoCommand

Any command returns the recorder's status:

```json
{
  "recording": true,
  "frames": 42,
  "source_errors": {},
  "dropped_frames": {},
  "last_files": {"combined": "…/20261009-151200-combined.mp4"},
  "last_seconds": 31.4,
  "last_error": null
}
```
