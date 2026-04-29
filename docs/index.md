# coreDAQ

`coreDAQ` is a photonic engineer-centric measurement device with accurate, low-noise power monitoring, a smart data acquisition system, and a fully programmable, documented Python API for fast lab prototyping.

The refreshed API is designed around the operations a photonics engineer reaches for first:

- Read optical power immediately
- Work in watts by default, or switch to prefered units, `dBm`, `V`, `mV`
- Zero dark offsets
- Capture traces without juggling low-level helpers
- Check clipping and low-signal conditions directly from the API

## Install

```bash
pip install .
```

## First Measurement

```python
from py_coreDAQ import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    meter.set_wavelength_nm(1550.0)
    power_w = meter.read_channel(0, n_samples=8)
    print(power_w, "W")
```

## Core Workflow

1. Connect to the instrument with `coreDAQ(...)`
2. Check `identify()`, `frontend()`, and `detector()`
3. Set wavelength with `set_wavelength_nm(...)`
4. Choose a default display unit with `set_reading_unit(...)`
5. Read live power using `read_all()` or `read_channel(...)`
6. Run `zero_dark()` when using a `LINEAR` head in dark conditions
7. Use `get_data(...)` when you need a DAQ trace instead of a single reading

## Documentation Map

- [Quickstart](quickstart.md)
- [Readings and Units](readings.md)
- [Dark Zero and Restore](zeroing.md)
- [Get Data](capture.md)
- [Migration Guide](migration.md)
