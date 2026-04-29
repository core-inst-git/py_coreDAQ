# Capture Data

Use `capture()` when you need a time-series trace instead of a single power reading. It arms the ADC, waits for completion, transfers all samples, and returns a `CaptureResult`.

Examples below use `coreDAQ.connect(simulator=True)`.

## Primary capture methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `capture(frames, unit=None, channels=None, trigger=False, trigger_rising=True)` | `CaptureResult` | Multi-channel capture |
| `capture_channel(channel, frames, unit=None, trigger=False, trigger_rising=True)` | `CaptureResult` | Single-channel capture |

For triggered capture workflows, see [Capture with External Trigger](trigger.md).

## Basic capture

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture(frames=4096, unit="w")

    print(result.unit)               # "w"
    print(result.enabled_channels)   # (0, 1, 2, 3) — all active channels
    print(result.trace(0)[:10])      # first 10 samples from channel 0
    print(result.status(0).any_clipped)
```

## Capture one channel

```python
with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture_channel(0, frames=2048, unit="dbm")
    trace = result.trace(0)          # list of floats in dBm
```

## Temporarily override the capture mask

Pass `channels=[...]` to capture a specific subset of channels. The API applies the temporary mask for this call and restores the previous mask on return.

```python
with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture(frames=1024, channels=[0, 2], unit="mv")

    print(result.enabled_channels)   # (0, 2)
    print(result.trace(0)[:5])
    print(result.trace(2)[:5])
```

## `CaptureResult` fields

| Field | Meaning |
| --- | --- |
| `unit` | Output unit token used for this capture |
| `sample_rate_hz` | Sample rate at capture time |
| `enabled_channels` | `tuple[int, ...]` of active channels |
| `ranges` | Range index per captured channel |
| `range_labels` | Human-readable range label per captured channel |
| `wavelength_nm` | Wavelength setting used for power conversion |
| `detector` | `"INGAAS"` or `"SILICON"` |
| `frontend` | `"LINEAR"` or `"LOG"` |

### `.trace(channel)`

Returns a `list[float | int]` of samples for the given channel in the capture unit. `int` when `unit="adc"`, `float` otherwise.

### `.status(channel)`

Returns a `CaptureChannelStatus` with per-channel quality flags.

| Field | Meaning |
| --- | --- |
| `any_over_range` | At least one sample crossed the high threshold |
| `any_under_range` | At least one sample crossed the low threshold |
| `any_clipped` | At least one sample was over- or under-range |
| `over_range_samples` | Count of over-range samples |
| `under_range_samples` | Count of under-range samples |
| `clipped_samples` | Count of over- or under-range samples |
| `peak_signal_v` | Peak absolute signal in volts across the capture |

## Practical notes

- `frames` is the number of samples **per active capture channel**; the total SDRAM footprint is `frames × active_channels × 2 bytes`
- `unit=None` uses the global reading unit set by `set_reading_unit()`
- LINEAR captures subtract the active zero offset before converting to `mv`, `v`, `w`, or `dbm`; the `adc` unit returns zero-corrected ADC codes
- `capture()` follows the capture channel mask; live `read_*()` methods do not
- use `set_sample_rate_hz()` and `set_oversampling()` **before** calling `capture()` to control timing

## Advanced: manual capture control

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.arm_capture(frames=8192)
    meter.start_capture()
    meter.wait_until_complete(poll_s=0.05, timeout_s=10.0)

    # retrieve the data yourself if needed
    result = meter.capture(frames=8192)   # will be instant; data already in SDRAM
```

`capture()` wraps all three steps. Use the manual API only when you need to interleave arm and start with other device operations.

## Related pages

- [Capture with External Trigger](trigger.md) — triggered capture workflows
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max frame counts
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate and oversampling
