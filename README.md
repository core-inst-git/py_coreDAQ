# py_coreDAQ

Python driver for the coreDAQ opto-electronic data acquisition system — optical power measurement, programmable capture, and Python-driven lab automation across four hardware variants (InGaAs / Silicon × Linear / Logarithmic).

## Install

```bash
pip install py_coreDAQ
```

## Quick start

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect() as meter:
    meter.set_wavelength_nm(1550.0)
    print(meter.read_all())           # [W, W, W, W] — all four channels
    print(meter.channels[0].power_w)  # one channel, in watts
```

## Documentation

**Full documentation, API reference, and examples:**
**https://py-coredaq.readthedocs.io/**
