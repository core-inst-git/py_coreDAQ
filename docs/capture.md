# Capture Data

Use `capture()` to record a block of samples into the instrument's internal memory and retrieve them in one transfer. This is distinct from single-shot reads — capture stores many time-stamped points at the configured sample rate, then delivers them all at once.

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

with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(frames=4096, unit="w")

    print(result.unit)               # "w"
    print(result.enabled_channels)   # (0, 1, 2, 3) — all active channels
    print(result.trace(0)[:10])      # first 10 samples from channel 0
    print(result.status(0).any_clipped)
```

## Capture one channel

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture_channel(0, frames=2048, unit="dbm")
    trace = result.trace(0)          # list of floats in dBm
```

## Temporarily override the capture mask

Pass `channels=[...]` to capture a specific subset of channels. The API applies the temporary mask for this call and restores the previous mask on return.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(frames=1024, channels=[0, 2], unit="mv")

    print(result.enabled_channels)   # (0, 2)
    print(result.trace(0)[:5])
    print(result.trace(2)[:5])
```

## Frame count must match

If you use the low-level `arm_capture(N)` directly and then call `capture()` to retrieve the data, the frame count you pass to `capture()` must match the count you armed with. A mismatch will put the instrument in an undefined state.

The high-level `capture(N)` method handles arm and transfer together and avoids this issue entirely — use it unless you have a specific reason to separate the two steps.

```python
# Correct: arm and transfer use the same frame count
coredaq.arm_capture(frames=1024)
coredaq.start_capture()
result = coredaq.capture(frames=1024)   # must match what was armed
```

## Device busy errors

The instrument processes one command at a time. Sending a new command while the instrument is still recording raises `coreDAQTimeoutError`. This can happen if you call `capture()` before a previous acquisition has finished, or if you call a read method while the instrument is mid-acquisition.

```python
from py_coreDAQ import coreDAQTimeoutError

try:
    result = coredaq.capture(frames=8192)
except coreDAQTimeoutError as e:
    print("Device busy — previous acquisition still in progress:", e)
```

Similarly, calling a single-shot read while a capture is running will raise `coreDAQTimeoutError`. Always wait for `capture()` to return before issuing further commands.

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

Returns a `list[float | int]` of samples for the given channel in the capture unit. Returns `int` when `unit="adc"`, `float` otherwise.

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

- `frames` is the number of samples **per active capture channel**; total memory used is `frames × active_channels × 2 bytes`
- `unit=None` uses the global reading unit set by `set_reading_unit()`
- LINEAR captures subtract the active zero offset before converting to `mv`, `v`, `w`, or `dbm`; `adc` returns zero-corrected codes
- `capture()` respects the capture channel mask; single-shot `read_*()` methods do not
- set `sample_rate_hz` and `oversampling` **before** calling `capture()` to control timing

## Related pages

- [Capture with External Trigger](trigger.md) — triggered capture workflows
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max frame counts
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate and oversampling
- [Device State](state.md) — inspect what the instrument is doing before issuing commands
