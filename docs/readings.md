# Read Power

Single-shot reads return the current optical power for one or all channels. Use `capture()` when you need a time-series trace.

Examples below use `coreDAQ.connect(simulator=True)`.

## Single-shot reads

| Method | Returns | Typical use |
| --- | --- | --- |
| `read_channel(channel, unit=None, autoRange=True, n_samples=1)` | One scalar | Single measurement on one channel |
| `read_all(unit=None, autoRange=True, n_samples=1)` | `list[float\|int]` (4 values) | Single measurement on all four channels |

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_wavelength_nm(1550.0)

    print(coredaq.read_channel(0))             # watts
    print(coredaq.read_channel(0, unit="dbm")) # dBm
    print(coredaq.read_all())                  # [W, W, W, W]
    print(coredaq.read_all(unit="mv"))         # [mV, mV, mV, mV]
```

- `read_all()` returns a list for all four channels regardless of the capture channel mask
- units default to watts unless changed with `set_reading_unit()`
- `autoRange=True` retunes only the channels being read (LINEAR frontends only; ignored on LOG)

## ChannelProxy — per-channel ergonomics

`coredaq.channels[n]` returns a `ChannelProxy` that scopes all reads to one channel. Useful in a REPL or when a script tracks a single channel.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    ch = coredaq.channels[0]

    print(ch.power_w)          # single-shot read in watts
    print(ch.read(unit="dbm")) # single-shot read in dBm
    print(ch.read_full())      # ChannelReading with all metadata
    print(ch.is_clipped())
```

`ChannelProxy` does not duplicate any state — it holds a reference to the parent `coreDAQ` and the channel index.

## Averaging with `n_samples`

`n_samples` averages multiple measurements before returning a single value. Up to 32 measurements can be averaged. More averaging reduces noise but takes longer.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.read_channel(0, n_samples=8))   # average of 8 measurements
    print(coredaq.read_all(n_samples=32))          # average of 32 measurements
```

**Timing:** at 500 Hz, each measurement takes 2 ms, so `n_samples=32` takes approximately 64 ms per call. If you send another command while a multi-sample read is still in progress, the device will return a busy error — see [Errors and device busy](#errors-and-device-busy).

## Streaming reads in a while loop

500 Hz is the recommended sample rate for continuous monitoring over USB. At this rate, reads are fast enough to keep up with the USB transfer and you get a smooth data stream. Build your own time base using `time.time()` or similar.

```python
import time
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_sample_rate_hz(500)

    t0 = time.time()
    while True:
        t = time.time() - t0
        power = coredaq.read_channel(0)
        print(f"{t:.3f}  {power:.6f} W")
        # no sleep needed — the read itself takes ~2 ms at 500 Hz
```

At rates much higher than 500 Hz, USB transfer overhead can become the bottleneck. Use `capture()` for high-speed time-series data instead.

## Errors and device busy

The device can only handle one command at a time. If a read is still in progress when the next command arrives — for example, you requested `n_samples=32` at 1 Hz, which takes 32 seconds — the device returns a busy status and the driver raises `coreDAQTimeoutError`.

Design your code so that reads complete before the next one starts. In practice this is only an issue when combining a very low sample rate with high `n_samples`.

```python
from py_coreDAQ import coreDAQTimeoutError

try:
    power = coredaq.read_channel(0, n_samples=16)
except coreDAQTimeoutError as e:
    print("Device busy:", e)
```

## Full-detail reads

Full-detail reads return a frozen dataclass with all measurement metadata alongside the power value.

| Method | Returns | Typical use |
| --- | --- | --- |
| `read_channel_full(channel, unit=None, autoRange=True, n_samples=1)` | `ChannelReading` | One-channel read with metadata and status flags |
| `read_all_full(unit=None, autoRange=True, n_samples=1)` | `MeasurementSet` | Four-channel read with metadata and status flags |

```python
with coreDAQ.connect(simulator=True) as coredaq:
    r = coredaq.read_channel_full(0, unit="mv", n_samples=8)

    print(r.value)        # value in the requested unit
    print(r.power_w)      # always in watts
    print(r.power_dbm)
    print(r.signal_mv)
    print(r.range_label)
    print(r.is_clipped)
    print(r.zero_source)  # "factory", "user", or "not_applicable"
```

### `ChannelReading` fields

| Field | Meaning |
| --- | --- |
| `value` | Reading in the requested unit |
| `unit` | Unit token used for `value` |
| `power_w` | Optical power in watts |
| `power_dbm` | Optical power in dBm |
| `signal_v` | Signal level in volts |
| `signal_mv` | Signal level in millivolts |
| `adc_code` | Zero-corrected ADC code (LINEAR) or raw ADC code (LOG) |
| `range_index` | Active range index |
| `range_label` | Human-readable range label (e.g. `"1 mW"`) |
| `wavelength_nm` | Wavelength used for power conversion |
| `detector` | `"INGAAS"` or `"SILICON"` |
| `frontend` | `"LINEAR"` or `"LOG"` |
| `zero_source` | `"factory"`, `"user"`, or `"not_applicable"` |
| `over_range` | `True` when `abs(signal_v) > 4.2` |
| `under_range` | `True` when `abs(signal_mv) < 5.0` |
| `is_clipped` | `True` when either threshold is violated |

### `MeasurementSet`

```python
with coreDAQ.connect(simulator=True) as coredaq:
    ms = coredaq.read_all_full(unit="w")

    for r in ms:
        print(r.power_w, r.is_clipped)

    print(ms[0].signal_mv)    # index by channel
    print(ms.values())         # [float, float, float, float] in the requested unit
```

## Signal health

```python
with coreDAQ.connect(simulator=True) as coredaq:
    status = coredaq.signal_status(channel=0)
    print(status.signal_v)
    print(status.over_range)
    print(status.under_range)
    print(status.is_clipped)

    all_clipped = coredaq.is_clipped()   # list[bool], one per channel
    print(all_clipped)
```

Thresholds:

- `over_range` when `abs(signal_v) > 4.2`
- `under_range` when `abs(signal_mv) < 5.0`
- `is_clipped` when either threshold is true

## Channel numbering

Public channel numbering is `0..3`. Channel `0` is the first channel.

## Related pages

- [Ranges and AutoRange](ranges.md) for manual range control
- [Units, Sample Rate, and Oversampling](settings.md) for units and global sample-rate settings
- [Zeroing and Signal Health](zeroing.md) for zero offset management
