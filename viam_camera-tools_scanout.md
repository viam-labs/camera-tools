# Model viam:camera-tools:scanout

A camera that shows what each of the machine's monitors is displaying,
read back from the hardware (Linux KMS). It's useful for checking a kiosk
or status screen remotely, or for telling "the app rendered the wrong thing"
apart from "the right frame never reached the screen".

## Configuration

```json
{
  "card": "/dev/dri/card1",
  "jpeg_quality": 90
}
```

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `card` | string | Optional | first card with a monitor connected | DRM card to read. |
| `jpeg_quality` | int | Optional | `90` | JPEG quality (1–100) of the images. |

## Sources

Each connected monitor is a named image, named the way the kernel names its
connector. Run `ls /sys/class/drm` to see the names. Examples are `HDMI-A-1`
and `DP-2`.

- **Unfiltered GetImages:** returns every connected monitor, which is how the
  Viam app's source picker finds them. The first is the default view.
- **`filter_source_names`:** picks monitors by name.
- **Resolution:** each image is the monitor's full resolution.
- **When it plugs or unplugs:** a monitor plugged in later appears on the
  next read, and an unplugged one disappears.

## Errors

| Message | Meaning |
|---|---|
| `X is not connected (connected: …)` | That connector has no monitor. |
| `X is connected but no CRTC drives it` | The monitor is plugged in but nothing is displaying on it (it is turned off in software). |
| `X: CRTC n is scanning out nothing` | The output is enabled with no framebuffer attached. |
| `X: no handle for framebuffer n (needs root)` | The module isn't running as root. |
| `X: framebuffer is n bpp` | Only 32 bpp (XRGB8888) framebuffers are read back. |

## Limitations

- **Only linear framebuffers read back cleanly.** The console, `/dev/fb0`,
  and dumb buffers are linear. A desktop compositor that scans out tiled
  buffers reads back garbled.
- **A capture can tear.** Each one is a single copy and isn't synchronized
  with whoever is drawing, so it can catch a frame half-written.
