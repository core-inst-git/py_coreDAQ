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

## Capture synchronised with an external source

Use this when you need to start another instrument — a swept laser, voltage source, or current source — and record the detector response in the same script, with the coreDAQ triggered by the instrument's sync output.

The key: `arm_capture()` returns immediately. The instrument sits at the trigger input while your script commands the external source. Once the trigger edge arrives, the ADC starts recording.

```python
import time
from py_coreDAQ import coreDAQ

# Pseudocode — replace VoltageSource with your actual instrument driver.
# from my_instruments import VoltageSource
# source = VoltageSource.connect("GPIB::7")

frames      = 5000
sample_rate = 10_000   # Hz — 0.5 s acquisition window

with coreDAQ.connect() as coredaq:
    coredaq.set_sample_rate_hz(sample_rate)

    # Arm — instrument waits at the trigger input, script continues.
    coredaq.arm_capture(frames, trigger=True, trigger_rising=True)

    # Start the voltage sweep. The source's sync output fires the coreDAQ trigger.
    # source.start_sweep()

    # Wait for the acquisition to complete.
    #
    # Do not poll the coreDAQ with capture_is_data_ready() or any other command
    # during this window. The MCU's DMA and SPI run at full speed while
    # recording and any USB command sent in that window will corrupt samples.
    # This limitation will be resolved in a future firmware release.
    #
    # Until then, either sleep for the estimated acquisition time:
    time.sleep(frames / sample_rate + 0.5)
    #
    # Or poll your signal source instead of sleeping blind — it is safe to
    # talk to the external instrument while the coreDAQ is acquiring:
    # source.wait_until_idle()
    #
    # Once the firmware fix lands, you will be able to replace the sleep with:
    # while not coredaq.capture_is_data_ready():
    #     time.sleep(0.05)

    # Transfer and convert — no arm or sleep needed here.
    result = coredaq.collect_capture(frames, unit="w", channels=[0, 1])

print(result.enabled_channels)      # (0, 1)
print(result.trace(0)[:5])          # first 5 samples from channel 0 in watts
print(result.status(0).any_clipped)
```

If there is a fixed delay between commanding the source and the sync pulse arriving, add it to the sleep:

```python
trigger_latency_s = 0.020   # 20 ms from sweep command to sync pulse
time.sleep(trigger_latency_s + frames / sample_rate + 0.5)
```

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
