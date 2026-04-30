# Quickstart

## Install

```bash
pip install py_coreDAQ
```

## Connect to a device

`coreDAQ.connect()` opens the connection and returns a device handle. Call `close()` when you are done, or use it as a context manager to close automatically.

```python
from py_coreDAQ import coreDAQ

# Open and close explicitly — useful in notebooks and scripts
coredaq = coreDAQ.connect(simulator=True)
print(coredaq.identify())
print(coredaq.frontend(), coredaq.detector())
coredaq.close()
```

```python
# Context manager — port closes on exit from the with block
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.identify())
```

```python
# Real hardware — auto-discover or specify port
coredaq = coreDAQ.connect()                          # finds first device on bus
coredaq = coreDAQ.connect("/dev/tty.usbmodem12401")  # explicit port
```

## Read power on one channel and all four channels

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_wavelength_nm(1550.0)

    power_w   = coredaq.read_channel(0)
    power_dbm = coredaq.read_channel(0, unit="dbm")
    all_w     = coredaq.read_all()

    print(power_w, "W")
    print(power_dbm, "dBm")
    print(all_w)
```

## Use ChannelProxy for per-channel ergonomics

`coredaq.channels[n]` returns a thin proxy that scopes all calls to one channel. Useful in a REPL or when tracking a single channel over time.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    ch0 = coredaq.channels[0]

    print(ch0.power_w)          # watts
    print(ch0.read(unit="dbm")) # dBm
    print(ch0.is_clipped())
```

## Average several samples

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.read_channel(0, n_samples=32))   # average of 32 measurements
    print(coredaq.read_all(n_samples=16))
```

## Capture a trace

`capture()` arms the ADC, records a block of samples, and returns a `CaptureResult`.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(frames=2048, unit="mv", channels=[0, 2])

    print(result.enabled_channels)    # (0, 2)
    print(result.trace(0)[:5])        # first 5 samples from channel 0
    print(result.status(0).any_clipped)
```

## Capture on an external trigger

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(
        frames=2048,
        unit="adc",
        trigger=True,
        trigger_rising=True,
    )
    print(result.trace(0)[:5])
```

Use `trigger_rising=False` to capture on a falling edge.

## Inspect range and set a manual range (LINEAR frontends)

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    coredaq.set_range_power(0, 1e-3)    # pick range for 1 mW
    print(coredaq.get_range(0))
    print(coredaq.get_ranges())
```

## Read full measurement details

```python
with coreDAQ.connect(simulator=True) as coredaq:
    r = coredaq.read_channel_full(0, unit="mv", n_samples=16)
    print(r.signal_mv)
    print(r.range_label)
    print(r.is_clipped)
    print(r.zero_source)
```

## What to read next

- [Read Power](readings.md) — every `read*` method and metadata fields
- [Capture Data](capture.md) — `CaptureResult` in detail
- [Capture with External Trigger](trigger.md) — external-trigger workflows
- [Ranges and AutoRange](ranges.md) — manual range selection
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max capture sizes
