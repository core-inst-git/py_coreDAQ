# coreDAQ

`coreDAQ` is a photonics-first Python API for using coreDAQ as a powermeter with an integrated DAQ.

The runtime ships with embedded InGaAs and silicon responsivity curves, defaults to watt readings, and exposes a clean package import:

```python
from coredaq import coreDAQ
```

## Install

```bash
pip install .
```

For editable local development:

```bash
pip install -e .
```

## Quick Start

```python
from coredaq import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    info = meter.device_info()
    print(info.raw_idn)
    print(info.frontend, info.detector)

    meter.set_wavelength_nm(1550.0)
    meter.set_reading_unit("w")

    readings = meter.read_all(autorange=True)
    for reading in readings:
        print(reading.channel, reading.value, reading.unit, reading.power_dbm)
```

## Main User APIs

- `read_all()` and `read_channel()` for live powermeter readings
- `get_data()` and `get_data_channel()` for DAQ traces
- `set_reading_unit("w" | "dbm" | "v" | "mv" | "adc")`
- `zero_dark()` and `restore_factory_zero()`
- `is_clipped()` and `signal_status()`

## Package Layout

- [`coredaq/__init__.py`](coredaq/__init__.py): public package export
- [`py_coreDAQ.py`](py_coreDAQ.py): driver engine plus the new `coreDAQ` wrapper surface
- [`docs/index.md`](docs/index.md): Read the Docs home page
- [`mkdocs.yml`](mkdocs.yml): MkDocs site configuration
- [`.readthedocs.yaml`](.readthedocs.yaml): Read the Docs build configuration

## Documentation

The docs site is configured for Read the Docs:

`https://py-coredaq.readthedocs.io/`
