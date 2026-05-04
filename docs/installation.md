# Installation

## Requirements

- Python 3.10 or later
- [pyserial](https://pypi.org/project/pyserial/) ≥ 3.5 — installed automatically as a dependency

No other packages are required. `py_coreDAQ` is pure Python.

## Install from PyPI

```bash
pip install py_coreDAQ
```

## Verify

```python
import py_coreDAQ
print(py_coreDAQ.__version__)

from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.read_all())   # [W, W, W, W]
```

No hardware needed — the simulator runs on any machine.

## Serial port permissions (Linux)

On Linux the serial device is owned by the `dialout` group. Add your user once and re-log:

```bash
sudo usermod -aG dialout $USER
```

## Editable install for development

```bash
git clone https://github.com/coreinstrumentation/py_coreDAQ.git
cd py_coreDAQ
pip install -e ".[dev]"
pytest tests/
```
