# Capture with External Trigger

The instrument arms the capture buffer and waits at the trigger input. When the edge arrives, DMA starts and the MCU records exactly `frames` samples at the configured sample rate. Recording stops when the buffer is full.

The trigger controls **when recording starts**, not the timing between samples.

## Why triggered capture must use the manual workflow

`capture()` is a blocking call — it arms, starts, waits, and collects in one go. With an external trigger you need to:

1. Arm the instrument
2. **Start your trigger source from the same script** (e.g. command a signal generator, pulse a GPIO)
3. Wait for the acquisition to finish
4. Collect the data

Step 2 is impossible if the driver is blocking on step 1. Use `arm_capture()` + `collect_capture()` instead:

```python
import time
from py_coreDAQ import coreDAQ

frames = 4096
with coreDAQ.connect() as coredaq:
    sample_rate = coredaq.sample_rate_hz()

    # Arm — returns immediately, instrument is now waiting at the trigger input.
    coredaq.arm_capture(frames, trigger=True, trigger_rising=True)

    # Fire your trigger source — runs in the same script, no blocking.
    my_signal_generator.trigger()

    # Sleep for the acquisition window.
    # Do not send any commands to the device while DMA is running.
    time.sleep(frames / sample_rate + 0.5)

    # Acquisition is done — transfer and convert.
    result = coredaq.collect_capture(frames, unit="w")

    print(result.trace(0)[:10])
```

## Edge polarity

```python
coredaq.arm_capture(frames, trigger=True, trigger_rising=True)   # rising edge (default)
coredaq.arm_capture(frames, trigger=True, trigger_rising=False)  # falling edge
```

## Selecting channels

Pass `channels=` to `collect_capture()` to temporarily override the capture mask for this transfer:

```python
coredaq.arm_capture(frames, trigger=True)
my_signal_generator.trigger()
time.sleep(frames / sample_rate + 0.5)
result = coredaq.collect_capture(frames, unit="mv", channels=[0, 2])
```

## Acquisition timing constraint

The MCU's DMA and SPI run at full speed during acquisition. Any UART command sent during this window corrupts samples. The sleep between `arm_capture` and `collect_capture` is the acquisition window — do not send any commands to the device during it.

## Simulator

In the simulator the trigger fires immediately, so you can test the same workflow without hardware:

```python
with coreDAQ.connect(simulator=True) as coredaq:
    frames = 1024
    coredaq.arm_capture(frames, trigger=True)
    # no external source needed — trigger fires at arm time in simulator
    time.sleep(frames / coredaq.sample_rate_hz() + 0.5)
    result = coredaq.collect_capture(frames, unit="w")
    print(result.trace(0)[:5])
```

## Troubleshooting

- **`collect_capture` returns garbage or raises**: the sleep was too short — increase the margin beyond 0.5 s or verify `sample_rate_hz()` matches what the device is actually running
- **Capture never fills**: verify the trigger source is wired to the BNC trigger input and is producing the expected edge polarity
- **Polarity wrong**: swap `trigger_rising=True` ↔ `trigger_rising=False`

## Related pages

- [Capture Data](capture.md) — non-triggered capture and the manual arm/collect pattern
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max frame counts
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate configuration
