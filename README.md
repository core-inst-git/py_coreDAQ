# py_coreDAQ

`py_coreDAQ` is a Python API and programmer's manual for the coreDAQ instrument.

The current driver keeps the existing command surface intact and now ships with the detector responsivity curves embedded directly in [`py_coreDAQ.py`](py_coreDAQ.py), so wavelength compensation no longer depends on an external JSON file at runtime.

## Install

```bash
pip install -r requirements.txt
```

## Files

- [`py_coreDAQ.py`](py_coreDAQ.py): main API module
- [`responsivity_curves.json`](responsivity_curves.json): source/reference copy of the responsivity tables
- [`docs/index.md`](docs/index.md): Read the Docs landing page
- [`.readthedocs.yaml`](.readthedocs.yaml): Read the Docs build configuration
- [`mkdocs.yml`](mkdocs.yml): documentation site configuration

## Quick Start

```python
from py_coreDAQ import CoreDAQ

with CoreDAQ("/dev/tty.usbmodemXXXX") as daq:
    print(daq.idn())
    print(daq.frontend_type(), daq.detector_type())

    daq.set_wavelength_nm(1550.0)
    watts = daq.snapshot_W(n_frames=8)
    print(watts)
```

## Responsivity Curves

The built-in responsivity data is loaded automatically during `CoreDAQ(...)` initialization.

- InGaAs: 910 nm to 1700 nm
- Silicon: 400 nm to 1100 nm
- You can still override the built-in curves with `load_responsivity_curves_json(path)` if you want to load a custom calibration table.

## Documentation

The documentation is set up for Read the Docs. After you connect the repository, the published site can live at:

`https://py-coredaq.readthedocs.io/`

If you choose a different Read the Docs project slug, update that URL here.
