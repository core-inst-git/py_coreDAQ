# Read Power

Single-shot reads return the current optical power. Use `capture()` when you need a time-series trace.

All examples assume `coredaq` is an open connection — see [Quickstart](quickstart.md).

## Read one channel or all four

```python
coredaq.read_channel(0)               # watts, channel 0
coredaq.read_channel(0, unit="dbm")   # dBm
coredaq.read_all()                    # [W, W, W, W]
coredaq.read_all(unit="mv")           # [mV, mV, mV, mV]
```

Channels are numbered `0..3`.

## Units

Pass `unit=` to any read method. Supported tokens:

| Token | Meaning |
| --- | --- |
| `"w"` | Watts (default) |
| `"dbm"` | dBm |
| `"mv"` | Millivolts |
| `"v"` | Volts |
| `"adc"` | Raw zero-corrected ADC code |

Change the default for all subsequent reads:

```python
coredaq.set_reading_unit("dbm")
coredaq.read_channel(0)   # now returns dBm without passing unit=
```

Set the wavelength before reading power — the driver uses it for the responsivity correction:

```python
coredaq.set_wavelength_nm(1310.0)
```

## Averaging

`n_samples` averages multiple measurements before returning. Up to 32 samples.

```python
coredaq.read_channel(0, n_samples=8)    # average of 8
coredaq.read_all(n_samples=32)          # average of 32, all channels
```

At 500 Hz each measurement takes 2 ms, so `n_samples=32` takes ~64 ms. Do not send another command while averaging is in progress — the device returns busy and the driver raises `coreDAQTimeoutError`.

## ChannelProxy

`coredaq.channels[n]` returns a proxy scoped to one channel. Useful in a REPL or when a script tracks a single channel over time.

```python
ch = coredaq.channels[0]

ch.power_w            # single-shot read in watts
ch.read(unit="dbm")
ch.read_full()        # ChannelReading with full metadata
ch.is_clipped()
```

## Continuous reads

500 Hz is the recommended rate for continuous monitoring. The read itself takes ~2 ms at that rate — no sleep needed.

```python
import time

t0 = time.time()
while True:
    t = time.time() - t0
    power = coredaq.read_channel(0)
    print(f"{t:.3f}  {power:.6f} W")
```

At much higher rates, USB overhead becomes the bottleneck. Use `capture()` for high-speed time-series data instead.

## Full-detail reads

Return a frozen dataclass with all measurement metadata alongside the power value.

```python
r = coredaq.read_channel_full(0, unit="mv", n_samples=8)

print(r.power_w)
print(r.power_dbm)
print(r.signal_mv)
print(r.range_label)
print(r.is_clipped)
print(r.zero_source)   # "factory", "user", or "not_applicable"
```

`read_all_full()` returns a `MeasurementSet` — iterable, index-accessible:

```python
ms = coredaq.read_all_full(unit="w")

for r in ms:
    print(r.power_w, r.is_clipped)

print(ms[0].signal_mv)
print(ms.values())     # [float, float, float, float]
```

## Signal health

```python
status = coredaq.signal_status(0)
print(status.signal_v, status.over_range, status.is_clipped)

coredaq.is_clipped()       # [bool, bool, bool, bool]
coredaq.is_clipped(0)      # bool, channel 0 only
```

Thresholds: over-range when `|signal| > 4.2 V`, under-range when `|signal| < 5.0 mV`.

## Related pages

- [Ranges and AutoRange](ranges.md) — manual range selection on LINEAR frontends
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate, oversampling
- [Zeroing and Signal Health](zeroing.md) — dark zero, factory zero
