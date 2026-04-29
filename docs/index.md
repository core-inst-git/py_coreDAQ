# coreDAQ

`coreDAQ` is a photonics-first Python API for using coreDAQ as a powermeter with a built-in DAQ.

The refreshed API is designed around the operations a photonics engineer reaches for first:

- Read optical power immediately
- Work in watts by default, or switch to `dBm`, `V`, `mV`, or `ADC`
- Zero dark offsets on `LINEAR` heads and restore factory zeros later
- Capture traces without juggling low-level transfer helpers
- Check clipping and low-signal conditions directly from the API

## Install

```bash
pip install .
```

## First Measurement

```python
from coredaq import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    meter.set_wavelength_nm(1550.0)
    reading = meter.read_channel1(autorange=True)
    print(reading.value, reading.unit)
    print(reading.power_dbm, "dBm")
```

## Core Workflow

1. Connect to the instrument with `coreDAQ(...)`
2. Check `device_info()` for frontend and detector
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
