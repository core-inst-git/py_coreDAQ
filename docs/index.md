# coreDAQ Python API

coreDAQ is a low-noise opto-electronic data acquisition system for optical power measurement and programmable capture. `py_coreDAQ` is the Python driver for it.

**[→ coreDAQ product page](https://core-instrumentation.com/coredaq)**

## Quick Hardware Note

The instrument comes in four hardware variants, combining two detector types with two amplifier topologies.

**Detector type** determines the usable wavelength range:

- **InGaAs** — 910–1700 nm. Covers the near-infrared bands used in telecom and fibre-optic work (1310 nm, 1550 nm, O/C/L-band).
- **Silicon** — 400–1100 nm. Covers the visible and near-infrared, suited for free-space, Ti:Sapphire, and 850 nm datacom applications.

**Amplifier topology** determines how dynamic range is distributed:

- **Linear (transimpedance)** — eight overlapping gain ranges spanning roughly 300 pW to 3 mW. Each range gives fixed, uniform resolution across its window. Switching between ranges is handled automatically by the driver's autoRange algorithm. Best choice for precision tracking of a known power level, noise floor measurements, or any application where absolute resolution at a specific power matters. It is to be noted that autoRanging will not be active while the device is in Capture Mode.

- **Logarithmic** — the full 1 nW to 3 mW span is compressed into a single range by a logarithmic amplifier. There are no ranges to select or switch between. Resolution is highest at low input powers and decreases toward the upper end, matching the way optical power is perceived on a dB scale. Ideal for characterising high-dynamic-range devices — resonators, interferometers, swept-source measurements — where the signal can jump orders of magnitude and stopping to change ranges is not practical.

The driver handles all four variants transparently. You do not need to know which one is connected; it is detected at startup and the appropriate calibration and conversion path is selected automatically. A built-in simulator is included so every code example in this documentation is runnable on a laptop without hardware.

## Install

```bash
pip install py_coreDAQ
```

## Connect and read

```python
from py_coreDAQ import coreDAQ

coredaq = coreDAQ.connect(simulator=True)
print(coredaq.read_all())           # [W, W, W, W]
print(coredaq.channels[0].power_w)  # watts, one channel
coredaq.close()
```

Or use it as a context manager — the port closes automatically on exit:

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.read_all())
```

On real hardware, pass the port or let the driver find the device:

```python
coredaq = coreDAQ.connect("/dev/tty.usbmodem12401")
coredaq = coreDAQ.connect()   # auto-discovers the first coreDAQ on the bus
```

## Default behavior

- reads return watts unless changed with `set_reading_unit()`
- `autoRange=True` on all `read*()` methods
- sample rate: 500 Hz, oversampling: OS 1
- LINEAR readings always apply the active zero offset

## Hardware variants

Instruments differ in amplifier topology — logarithmic or transimpedance — and in detector, which determines the usable wavelength range. The driver detects the variant at connection time and selects the appropriate calibration and conversion path automatically.

| Amplifier topology | Detector | Wavelength range |
| --- | --- | --- |
| Logarithmic | InGaAs | 910 – 1700 nm |
| Transimpedance (linear) | InGaAs | 910 – 1700 nm |
| Logarithmic | Silicon | 400 – 1100 nm |
| Transimpedance (linear) | Silicon | 400 – 1100 nm |

Methods that are topology-specific — such as range control on transimpedance instruments — raise `coreDAQUnsupportedError` on incompatible hardware rather than silently no-opping.

## Simulator

Every code example in this documentation uses `coreDAQ.connect(simulator=True)` and is runnable as-is. The simulator supports all four variants and produces deterministic output (seeded RNG, `seed=42` by default).

```python
# Default: InGaAs LOG at 1550 nm
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.frontend())   # LOG
    print(coredaq.detector())   # INGAAS

# InGaAs LINEAR
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    print(coredaq.supported_ranges())

# Si LOG at 850 nm
with coreDAQ.connect(
    simulator=True, frontend="LOG", detector="SILICON", wavelength_nm=850.0
) as coredaq:
    print(coredaq.read_channel(0))
```

## Documentation map

| Page | What it covers |
| --- | --- |
| [Quickstart](quickstart.md) | First measurement in under 5 minutes |
| [Read Power](readings.md) | Single-shot reads, `ChannelProxy`, averaging, full-detail reads |
| [Capture Data](capture.md) | Block acquisition with `capture()`, `CaptureResult` |
| [Capture Data](capture.md) | Triggered capture via BNC input, channel masking, `CaptureResult` |
| [Ranges and AutoRange](ranges.md) | TIA gain ranges on LINEAR frontends |
| [Units, Sample Rate, and Oversampling](settings.md) | Global device settings, streaming setup |
| [Frames, Masking, and Memory Limits](frames.md) | Channel masks and SDRAM frame limits |
| [Zeroing and Signal Health](zeroing.md) | Dark zeroing, signal clipping |
| [Device State](state.md) | Instrument state machine, busy errors |
| [API Reference](api-reference.md) | Full method table |
