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
    print(meter.read_all())           # one reading per channel (4 on mk1, 5 on mk2)
    print(meter.channels[0].power_w)  # one channel, in watts
```

## Documentation

**Full documentation, API reference, and examples:**
**https://py-coredaq.readthedocs.io/**

## Using coreDAQ with AI agents (Claude skill)

This repo ships an agent guide and a ready-made **Claude Code skill** so an AI agent can
drive the instrument correctly ("make a live plot", "capture 2 s at 10 kHz", ...).

**Claude Code users** — install as a plugin (recommended):

```
/plugin marketplace add core-inst-git/py_coreDAQ
/plugin install coredaq@core-instrumentation
```

Or copy the skill manually into your personal skills folder:

```bash
git clone https://github.com/core-inst-git/py_coreDAQ /tmp/py_coreDAQ
mkdir -p ~/.claude/skills
cp -r /tmp/py_coreDAQ/claude-plugin/skills/coredaq ~/.claude/skills/
```

**Any other AI agent** — give it [`py_coredaq_agent.md`](claude-plugin/skills/coredaq/references/py_coredaq_agent.md) (same
content as the skill's reference file): device functionality, hard usage rules, and
verified script recipes in one file.
