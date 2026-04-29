# Frames, Masking, and Memory Limits

Examples below use `coreDAQ.connect(simulator=True)`.

## What `frames` means

`frames` is the number of captured samples **per active capture channel**.

- `frames=1024` with one active channel → 1024 samples
- `frames=1024` with four active channels → 4 × 1024 samples (one set per channel)

Call `max_capture_frames()` before a large acquisition to avoid overflowing SDRAM.

## Capture mask methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `capture_channel_mask()` | `int` | Inspect the active capture mask |
| `set_capture_channel_mask(mask)` | `int` | Set the mask directly |
| `capture_channels()` | `tuple[int, ...]` | Inspect the channels enabled by the mask |
| `set_capture_channels(channels)` | `tuple[int, ...]` | Set the mask from channel numbers |
| `max_capture_frames(channels=None)` | `int` | Compute the largest safe capture length |

## Accepted mask formats

`set_capture_channel_mask()` accepts integers, hex strings, binary strings, and the space-separated binary notation shown on the instrument panel.

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    meter.set_capture_channel_mask("0000 0101")  # channels 0 and 2
    print(hex(meter.capture_channel_mask()))     # 0x5
    print(meter.capture_channels())              # (0, 2)

    meter.set_capture_channel_mask(0xF)          # all four channels
    meter.set_capture_channels([1, 3])           # channels 1 and 3
```

## Temporary mask override in `capture()`

Pass `channels=[...]` to `capture()` to override the mask for that call only. The API restores the previous mask after the transfer.

```python
with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture(frames=2048, channels=[0, 2], unit="mv")
    print(result.enabled_channels)   # (0, 2)
    print(meter.capture_channels())  # restored to whatever it was before
```

## Capture-only scope

The capture mask affects `capture()` and related acquisition methods only.

- `read_all()` always reads all four channels regardless of the mask
- `get_ranges()` and range-setting methods always cover all four channels

## Memory model

The coreDAQ has 32 MiB of SDRAM. Each frame is 2 bytes per active channel.

```
max_frames = 32 * 1024 * 1024 / (2 * active_channels)
```

| Active channels | Frame bytes | Maximum frames |
| --- | --- | --- |
| 1 | 2 | 16,777,216 |
| 2 | 4 | 8,388,608 |
| 3 | 6 | 5,592,405 |
| 4 | 8 | 4,194,304 |

## Programmatic check

```python
with coreDAQ.connect(simulator=True) as meter:
    print(meter.max_capture_frames())              # based on current mask
    print(meter.max_capture_frames(channels=[0, 2]))  # hypothetical two-channel capture
```

## Related pages

- [Capture Data](capture.md) — `capture()` and `CaptureResult`
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate and timing
