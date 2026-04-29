# Read Power

Live reads return the current optical power without storing any samples. Use `capture()` when you need a time series.

Examples below use `coreDAQ.connect(simulator=True)`.

## Plain-value reads

| Method | Returns | Typical use |
| --- | --- | --- |
| `read_channel(channel, unit=None, autoRange=True, n_samples=1)` | One scalar | Fast read of one channel |
| `read_all(unit=None, autoRange=True, n_samples=1)` | `list[float\|int]` (4 values) | Fast read of all four channels |

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    meter.set_wavelength_nm(1550.0)

    print(meter.read_channel(0))             # watts
    print(meter.read_channel(0, unit="dbm")) # dBm
    print(meter.read_all())                  # [W, W, W, W]
    print(meter.read_all(unit="mv"))         # [mV, mV, mV, mV]
```

- `read_all()` returns a list for all four channels regardless of the capture channel mask
- units default to watts unless changed with `set_reading_unit()`
- `n_samples` controls the `SNAP n` count (1 – 32); the driver averages the snapshots before returning
- `autoRange=True` retunes only the channels being read (LINEAR frontends only; ignored on LOG)

## ChannelProxy — per-channel ergonomics

`meter.channels[n]` returns a `ChannelProxy` that scopes all reads to one channel. Use it in a REPL session or when writing code that tracks a single channel.

```python
with coreDAQ.connect(simulator=True) as meter:
    ch = meter.channels[0]

    print(ch.power_w)                # live read in watts
    print(ch.read(unit="dbm"))       # live read in dBm
    print(ch.read_full())            # ChannelReading with all metadata
    print(ch.is_clipped())           # True / False
```

`ChannelProxy` does not duplicate any state — it holds a reference to the parent `coreDAQ` and the channel index.

## Averaging with `n_samples`

```python
with coreDAQ.connect(simulator=True) as meter:
    print(meter.read_channel(0, n_samples=32))  # average of 32 snapshots
    print(meter.read_all(n_samples=16))
```

- `n_samples=1` is the default
- `n_samples=32` is the maximum
- each snapshot is a separate `SNAP n` firmware call; the driver averages the results host-side

## Full-detail reads

Full-detail reads return a frozen dataclass with all measurement metadata.

| Method | Returns | Typical use |
| --- | --- | --- |
| `read_channel_full(channel, unit=None, autoRange=True, n_samples=1)` | `ChannelReading` | One-channel read with metadata and status flags |
| `read_all_full(unit=None, autoRange=True, n_samples=1)` | `MeasurementSet` | Four-channel read with metadata and status flags |

```python
with coreDAQ.connect(simulator=True) as meter:
    r = meter.read_channel_full(0, unit="mv", n_samples=8)

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
with coreDAQ.connect(simulator=True) as meter:
    ms = meter.read_all_full(unit="w")

    for r in ms:
        print(r.power_w, r.is_clipped)

    print(ms[0].signal_mv)       # index by channel
    print(ms.values())            # [float, float, float, float] in the requested unit
```

## Signal health

```python
with coreDAQ.connect(simulator=True) as meter:
    status = meter.signal_status(channel=0)
    print(status.signal_v)
    print(status.over_range)
    print(status.under_range)
    print(status.is_clipped)

    all_clipped = meter.is_clipped()   # list[bool], one per channel
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
