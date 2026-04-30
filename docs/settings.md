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

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_reading_unit("dbm")
    print(coredaq.read_channel(0))           # dBm — uses global default
    print(coredaq.read_channel(0, unit="w")) # W — per-call override
    print(coredaq.reading_unit())            # "dbm" — global is unchanged
```

A per-call `unit=` argument overrides the global for that call only; it does not change the stored default.

## Sample rate methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `set_sample_rate_hz(hz)` | `None` | Change the global sample rate |
| `sample_rate_hz()` | `int` | Inspect the active sample rate |

Initialization sets **500 Hz**. Typical supported values: 500, 1000, 2000, 5000, 10 000, 100 000 Hz.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_sample_rate_hz(2000)
    print(coredaq.sample_rate_hz())   # 2000
```

## Oversampling methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `set_oversampling(os_idx)` | `None` | Change the oversampling index |
| `oversampling()` | `int` | Inspect the active oversampling index |

Initialization sets **OS 1** (no oversampling). Higher OS indices increase effective resolution and noise reduction at the cost of temporal bandwidth.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_oversampling(2)
    print(coredaq.oversampling())
```

## Recommended setups

### Continuous monitoring in a while loop

**500 Hz is the recommended rate for streaming single-shot reads over USB.** At this rate the USB transfer keeps up with the instrument and you get a smooth data stream. Build your own time base using `time.time()`.

```python
import time
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_sample_rate_hz(500)
    coredaq.set_oversampling(1)

    t0 = time.time()
    while True:
        t = time.time() - t0
        power = coredaq.read_channel(0)
        print(f"{t:.3f}  {power:.6f} W")
        # the read itself takes ~2 ms; no sleep needed
```

At much higher rates the USB round-trip becomes the bottleneck. Use `capture()` for high-speed time-series data instead.

### Averaged single-shot read

```python
with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_sample_rate_hz(500)
    coredaq.set_oversampling(1)
    print(coredaq.read_channel(0, n_samples=8))  # average of 8 measurements
```

Keep in mind that averaging increases the time each read takes. At 500 Hz, `n_samples=8` takes about 16 ms per call. If another command arrives while the averaging is still in progress, the device returns busy and the driver raises `coreDAQTimeoutError`.

### High-speed capture

For rates above a few kHz, use `capture()` rather than a polling loop. The instrument records at full speed into internal memory and delivers the entire trace in one USB transfer.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_sample_rate_hz(10_000)
    coredaq.set_oversampling(1)
    result = coredaq.capture(frames=4096, unit="adc")
    print(result.trace(0)[:10])
```

## Related pages

- [Read Power](readings.md) — single-shot reads and averaging with `n_samples`
- [Capture Data](capture.md) — block acquisition
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max capture sizes
