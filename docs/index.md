# coreDAQ Python API

`py_coreDAQ` is the Python driver for the coreDAQ 4-channel optical power meter. It runs on all four hardware variants — InGaAs LOG, InGaAs LINEAR, Si LOG, and Si LINEAR — and ships with a built-in simulator so every code example in this documentation is runnable on a laptop without hardware.

## Install

```bash
pip install py_coreDAQ
```

## Five-line start

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    print(meter.read_all())           # [W, W, W, W]
    print(meter.channels[0].power_w)  # watts, one channel
```

Replace `simulator=True` with your serial port to use real hardware:

```python
with coreDAQ.connect("/dev/tty.usbmodem12401") as meter:
    print(meter.read_all())
```

Or let the driver find the device automatically:

```python
with coreDAQ.connect() as meter:   # auto-discovers the first coreDAQ on the bus
    print(meter.read_all())
```

## Default behavior

- reads return watts unless changed with `set_reading_unit()`
- `autoRange=True` on all `read*()` methods
- sample rate: 500 Hz, oversampling: OS 1
- LINEAR readings always apply the active zero offset

## Hardware variants

| Variant | Frontend | Detector | Wavelength range |
| --- | --- | --- | --- |
| InGaAs LOG | LOG | InGaAs | 910 – 1700 nm |
| InGaAs LINEAR | LINEAR | InGaAs | 910 – 1700 nm |
| Si LOG | LOG | Silicon | 400 – 1100 nm |
| Si LINEAR | LINEAR | Silicon | 400 – 1100 nm |

The same `coreDAQ` class handles all four variants. Methods that are frontend-specific — such as `set_range()` on a LOG instrument — raise `coreDAQUnsupportedError` with a clear message rather than silently no-opping.

## Simulator

Every code example in this documentation uses `coreDAQ.connect(simulator=True)` and is runnable as-is. The simulator supports all four variants and produces deterministic output (seeded RNG, `seed=42` by default).

```python
# Default: InGaAs LOG at 1550 nm
with coreDAQ.connect(simulator=True) as meter:
    print(meter.frontend())   # LOG
    print(meter.detector())   # INGAAS

# InGaAs LINEAR
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    print(meter.supported_ranges())

# Si LOG at 850 nm
with coreDAQ.connect(
    simulator=True, frontend="LOG", detector="SILICON", wavelength_nm=850.0
) as meter:
    print(meter.read_channel(0))
```

## Documentation map

| Page | What it covers |
| --- | --- |
| [Quickstart](quickstart.md) | First capture in under 5 minutes |
| [Read Power](readings.md) | Live reads, `ChannelProxy`, averaging, full-detail reads |
| [Capture Data](capture.md) | Block acquisition with `capture()`, `CaptureResult` |
| [Capture with External Trigger](trigger.md) | BNC-triggered capture workflows |
| [Ranges and AutoRange](ranges.md) | TIA gain ranges on LINEAR frontends |
| [Units, Sample Rate, and Oversampling](settings.md) | Global device settings |
| [Frames, Masking, and Memory Limits](frames.md) | Channel masks and SDRAM frame limits |
| [Zeroing and Signal Health](zeroing.md) | Dark zeroing, signal clipping |
| [API Reference](api-reference.md) | Full method table |
| [Migration Guide](migration.md) | Upgrading from v0.1 |
