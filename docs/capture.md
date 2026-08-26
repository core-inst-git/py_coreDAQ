# Capture Data

Records a block of samples into the instrument's internal memory at the configured sample rate, then transfers them all at once. Use this instead of looped single-shot reads when you need a precise time-series trace.

All examples assume `coredaq` is an open connection — see [Quickstart](quickstart.md).

## Capture workflow

```python
import time

frames      = 5000
sample_rate = 10_000   # Hz

coredaq.set_sample_rate_hz(sample_rate)

coredaq.arm_capture(frames)
coredaq.start_capture()

# Do not send any commands while acquiring — see note below.
time.sleep(frames / sample_rate + 0.5)

result = coredaq.collect_capture(frames, unit="w")
```

> **Firmware note:** on firmware older than v4.2, polling the device during acquisition corrupts samples because the MCU DMA and SPI run at full speed. Sleep for the acquisition window instead and only call `capture_is_data_ready()` after the sleep.
>
> From **firmware v4.2+ (mk1; every mk2 firmware supports this)** this is fixed. If your device reports `firmware_version() >= (4, 2, 0)` you can replace the sleep with a proper blocking poll:
>
> ```python
> while not coredaq.capture_is_data_ready():
>     time.sleep(0.1)
> ```

## Simple blocking capture

If you do not need to do anything between arm and collect, `capture()` handles everything in one call:

```python
result = coredaq.capture(frames=4096, unit="w")
```

## Units

Pass `unit=` to `collect_capture()` or `capture()`:

| Token | Meaning |
| --- | --- |
| `"w"` | Watts (default) |
| `"dbm"` | dBm |
| `"mv"` | Millivolts |
| `"v"` | Volts |
| `"adc"` | Zero-corrected ADC codes |

## Channel selection

By default every channel is captured (4 on mk1, 5 on mk2). Pass `channels=` to capture a subset — the mask is applied for that call and restored afterwards:

```python
result = coredaq.collect_capture(frames, unit="mv", channels=[0, 2])

print(result.enabled_channels)   # (0, 2)
print(result.trace(0))
print(result.trace(2))
```

Or capture a single channel:

```python
result = coredaq.capture_channel(0, frames=2048, unit="dbm")
```

For memory limits per channel count see [Frames, Masking, and Memory Limits](frames.md).

## External trigger

To synchronise with an external source — a swept laser, voltage source, or current source — arm with `trigger=True`, start your instrument, then collect:

```python
import time

frames      = 5000
sample_rate = 10_000   # Hz

coredaq.set_sample_rate_hz(sample_rate)
coredaq.arm_capture(frames, trigger=True, trigger_rising=True)

# Start your instrument here — non-blocking, same script.
# source.start_sweep()

time.sleep(frames / sample_rate + 0.5)

result = coredaq.collect_capture(frames, unit="w")
```

`arm_capture()` returns immediately. The instrument waits at the trigger input while your script commands the external source.

## `CaptureResult`

```python
result.unit                  # unit token
result.sample_rate_hz        # sample rate at capture time
result.enabled_channels      # tuple of active channel indices
result.wavelength_nm         # wavelength used for power conversion
result.trace(0)              # list[float | int] — all samples for channel 0
result.status(0)             # CaptureChannelStatus
```

### `CaptureChannelStatus`

```python
s = result.status(0)
s.any_clipped           # bool — any sample over- or under-range
s.any_over_range
s.any_under_range
s.clipped_samples       # count
s.peak_signal_v         # peak absolute signal in volts
```

## Related pages

- [Frames, Masking, and Memory Limits](frames.md) — max frame counts per channel count
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate, oversampling
- [Device State](state.md) — instrument state machine

## Run-till-stop and overflow (mk2)

Masking-trigger-mode captures (`arm_masked_capture()`) run until the window
closes. If device memory fills first, the capture stops and
`capture_overflowed()` returns `True`. With `unit="adc"`, mk2 traces are
unsigned 16-bit (0–5 V straight binary); mk1 traces are signed 16-bit.
