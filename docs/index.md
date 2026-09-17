# coreDAQ Python API

coreDAQ is a low-noise opto-electronic data acquisition system for optical power measurement and programmable capture. `py_coreDAQ` is the Python driver for it.

The instrument provides four optical measurement channels (five on Mk2), sample rates up to 1 MHz, 32 MB of on-board capture memory, and trigger inputs for synchronised acquisition. It connects over USB, or Ethernet on Mk2, and is USB-powered.

<span class="mk2-legend">Throughout these docs, <span class="mk2">Mk2</span> marks a feature available on coreDAQ Mk2 only. Everything else works on both generations.</span>

Full specifications, electrical characteristics, and mechanical drawings are in the **[datasheet](https://core-instrumentation.com/datasheet)**.

If you would like to buy one, visit **[core-instrumentation.com/coredaq](https://core-instrumentation.com/coredaq)**.

If you find any issues with the driver, or anything odd in your measurements, please **[raise an issue on GitHub](https://github.com/core-inst-git/py_coreDAQ/issues/new)** and our engineers will look into it as fast as we can.

## Hardware generations

| | mk1 | mk2 |
|---|---|---|
| Channels | 4 | 5 (ch 4 = Analog IN aux) |
| Max sample rate | 100 kHz | 1 MHz (High Performance tier) |
| Transports | USB | USB + Ethernet |
| Firmware line | v4.x | v1.x |
| Extras | — | tiers, sensors, multi-unit sync, masking trigger mode |

The driver auto-detects the generation (`generation()`, `channel_count()`);
mk1 scripts run unchanged on mk2. See [coreDAQ mk2 & Ethernet](mk2.md).

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

**Getting started**

| Page | What it covers |
| --- | --- |
| [Installation](installation.md) | Install the driver and connect |
| [Quickstart](quickstart.md) | First measurement in under 5 minutes |
| [Connections & Back Panel](connections.md) | Every connector: USB-C, Ethernet, Analog IN, TRIG 0/1, coreLINK |

**Measuring**

| Page | What it covers |
| --- | --- |
| [Read Power](readings.md) | Single-shot reads, `ChannelProxy`, averaging, full-detail reads |
| [Units, Sample Rate & Bandwidth](settings.md) | Reading units, sample rate, oversampling, bandwidth |
| [Ranges & AutoRange](ranges.md) | TIA gain ranges on LINEAR frontends |
| [Zeroing & Signal Health](zeroing.md) | Dark zeroing, signal clipping |
| [Wavelength & Responsivity](wavelength.md) | Setting wavelength, responsivity correction |

**Capturing data**

| Page | What it covers |
| --- | --- |
| [Capture](capture.md) | Block acquisition with `capture()`, `CaptureResult` |
| [External Trigger](trigger.md) | Start trigger (TRIG 0), stepped, masking (TRIG 1) |
| [Frames & Memory](frames.md) | Channel masks and on-board frame limits |

**coreDAQ Mk2** <span class="mk2">Mk2</span>

| Page | What it covers |
| --- | --- |
| [Mk2 & Ethernet](mk2.md) | Mk2 overview, USB + Ethernet setup |
| [Performance Tiers & Licensing](tiers.md) | Base vs High-performance tiers |
| [Multi-unit Sync](sync.md) | coreLINK lockstep clusters |
| [Sensors & Diagnostics](sensors.md) | Temperature, humidity, system status |
| [Firmware Updates](firmware.md) | Browser-based field updates |

**Reference**

| Page | What it covers |
| --- | --- |
| [Device State](state.md) | Instrument state machine, busy errors |
| [API Reference](api-reference.md) | Full method table |
