# Quickstart

## Install

```bash
pip install py_coreDAQ
```

## Connect

```python
from py_coreDAQ import coreDAQ

coredaq = coreDAQ.connect()   # auto-discovers the first coreDAQ on the bus
```

Or use as a context manager — port closes automatically on exit:

```python
with coreDAQ.connect() as coredaq:
    ...
```

No hardware? Use the built-in simulator:

```python
coredaq = coreDAQ.connect(simulator=True)
```

## Read power

```python
print(coredaq.read_channel(0))        # watts, channel 0
print(coredaq.read_channel(0, unit="dbm"))
print(coredaq.read_all())             # [W, W, W, W]
```

For units, averaging, continuous reads, and full measurement metadata see [Read Power](readings.md).

## Capture a trace

```python
import time

frames      = 5000
sample_rate = 10_000   # Hz

coredaq.set_sample_rate_hz(sample_rate)

coredaq.arm_capture(frames)
coredaq.start_capture()

# On firmware < v4.2: sleep for the acquisition window.
# Polling during acquisition corrupts samples on older firmware.
time.sleep(frames / sample_rate + 0.5)

# On firmware >= v4.2: polling is safe — replace the sleep with:
# while not coredaq.capture_is_data_ready():
#     time.sleep(0.1)

result = coredaq.collect_capture(frames, unit="w")

print(result.enabled_channels)     # (0, 1, 2, 3)
print(result.trace(0))             # list of floats in watts, channel 0
print(result.status(0).any_clipped)
```

For channel masking, units, triggered capture, and `CaptureResult` details see [Capture Data](capture.md).

## Soft reset

If the device is in an unexpected state, a soft reset returns it to idle without power-cycling:

```python
coredaq.reset()
```
