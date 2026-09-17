# Wavelength & Responsivity

Optical power depends on wavelength: a detector converts a given optical power into more
or less photocurrent depending on the colour of the light. Tell the instrument what
wavelength you are measuring and it applies the right correction automatically.

<span class="mk2-legend"><span class="mk2">Mk2</span> marks a feature available on coreDAQ Mk2 only.</span>

## Setting the wavelength

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.set_wavelength_nm(1550.0)
    print(coredaq.wavelength_nm())        # 1550.0
    print(coredaq.read_channel(0, unit="dbm"))
```

The valid range depends on the detector; a value outside it is clamped and a warning is
issued:

| Detector | Wavelength range |
| --- | --- |
| InGaAs | 910 – 1700 nm |
| Silicon | 400 – 1100 nm |

```python
print(coredaq.wavelength_limits_nm())     # (910.0, 1700.0) on an InGaAs unit
```

The default wavelength comes from the instrument's calibration, so many users never need
to set it. Change it whenever you move to a different line (for example 1310 nm ↔ 1550 nm).

## How the correction works

The instrument holds a responsivity curve (photocurrent per watt, A/W) for its detector.
When you set a wavelength, readings are scaled by the responsivity at that wavelength
relative to the calibration reference, so the reported power stays accurate across the
band. You can query the responsivity directly:

```python
print(coredaq.responsivity_a_per_w(1550.0))   # A/W at 1550 nm
```

Responsivity peaks near ~1520–1540 nm for InGaAs (~1.0 A/W) and near ~940 nm for Silicon
(~0.6 A/W), and rolls off toward the band edges — which is exactly why the correction
matters when you work away from the peak.

## On-device responsivity points <span class="mk2">Mk2</span>

Mk2 instruments can store measured **(wavelength, responsivity)** points — up to eight per
channel — taken during a wavelength calibration. This lets a unit carry its own measured
response across several lines instead of relying only on the generic curve. These points
are written during calibration; you do not need to manage them for everyday measurements.

## Related pages

- [Reading Power](readings.md)
- [Zeroing & Signal Health](zeroing.md)
