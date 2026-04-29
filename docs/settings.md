# Units, Sample Rate, and Oversampling

These are global device settings. They apply to all subsequent reads and captures until changed.

Examples below use `coreDAQ.connect(simulator=True)`.

## Reading units

`coreDAQ` reads in watts by default. The unit token controls what the driver returns.

| Unit token | Meaning |
| --- | --- |
| `"w"` | Optical power in watts |
| `"dbm"` | Optical power in dBm |
| `"v"` | Signal voltage |
| `"mv"` | Signal millivolts |
| `"adc"` | Raw ADC code (zero-corrected for LINEAR, raw for LOG) |

## Unit control methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `set_reading_unit(unit)` | `None` | Change the global default unit |
| `reading_unit()` | `str` | Inspect the current default unit |

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    meter.set_reading_unit("dbm")
    print(meter.read_channel(0))           # dBm — uses global default
    print(meter.read_channel(0, unit="w")) # W — per-call override
    print(meter.reading_unit())            # "dbm" — global is unchanged
```

A per-call `unit=` argument overrides the global for that call only; it does not change the stored default.

## Sample rate methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `set_sample_rate_hz(hz)` | `None` | Change the global sample rate |
| `sample_rate_hz()` | `int` | Inspect the active sample rate |

Initialization sets **500 Hz**. Typical supported values: 500, 1000, 2000, 5000, 10 000, 100 000 Hz.

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.set_sample_rate_hz(2000)
    print(meter.sample_rate_hz())   # 2000
```

## Oversampling methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `set_oversampling(os_idx)` | `None` | Change the oversampling index |
| `oversampling()` | `int` | Inspect the active oversampling index |

Initialization sets **OS 1** (no oversampling). Higher OS indices increase effective resolution and noise reduction at the cost of temporal bandwidth.

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.set_oversampling(2)
    print(meter.oversampling())
```

Oversampling changes can take a short moment to settle; the driver already retries through transient `BUSY` replies.

## Recommended setups

### Fast interactive read

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.set_sample_rate_hz(1000)
    meter.set_oversampling(1)
    print(meter.read_channel(0))
```

### Stable averaged read

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.set_sample_rate_hz(500)
    meter.set_oversampling(2)
    print(meter.read_channel(0, n_samples=32))
```

### High-speed capture

```python
with coreDAQ.connect(simulator=True) as meter:
    meter.set_sample_rate_hz(10_000)
    meter.set_oversampling(1)
    result = meter.capture(frames=4096, unit="adc")
```

## Related pages

- [Read Power](readings.md) — live reads and averaging with `n_samples`
- [Capture Data](capture.md) — block acquisition
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max capture sizes
