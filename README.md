# Module camera-tools

Viam components for seeing and sharing what a machine shows: its monitors
and its cameras.

## Models

- [`viam:camera-tools:scanout`](viam_camera-tools_scanout.md): a
  camera with one source per connected monitor (`HDMI-A-1`, `DP-2`, …).
  Each source shows that monitor's pixels, read back from Linux KMS.
- [`viam:camera-tools:recorder`](viam_camera-tools_recorder.md): a
  switch that records any camera streams to H.264 MP4 while it's on. Each
  source gets its own clip, plus one with all of them side by side. Clips
  are saved locally and can also be posted to Slack.

## Code layout

| File | Owns |
|---|---|
| `src/camera_tools/drm.py` | Reading a monitor's scanout from KMS (plain ioctls, no libdrm). |
| `src/camera_tools/scanout.py` | The `scanout` camera. |
| `src/camera_tools/recording.py` | Grabbing sources on a timer and encoding MP4s with PyAV/libx264, and stitching clips side by side afterwards. Has no Viam imports. |
| `src/camera_tools/recorder.py` | The `recorder` switch: config, starting and stopping, Slack upload. |

## How scanout works

For each connected connector, `drm.py` follows the connector to its encoder
and then its CRTC. It finds the framebuffer that CRTC is scanning out right
now, maps it and copies the pixels. That is the hardware's side of the
screen, whoever drew it.

It's read-only:
- The card is opened once and DRM master is dropped straight away, so it
  never holds the displays away from whatever drives them.
- It never asks a connector to re-probe.

It needs root (`CAP_SYS_ADMIN`), as viam-server modules normally run.

## Development

```sh
pip install -r requirements-dev.txt
pytest
```

Releases: run the **Release** workflow with a version, or push a `vX.Y.Z`
tag. Both publish through `viamrobotics/build-action`, which needs the
`viam_key_id` and `viam_key_value` repository secrets. The module ID
`viam:camera-tools` has to exist in the registry first (`viam module
create`).
