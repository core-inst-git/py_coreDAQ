# Tiers & licensing (mk2)

mk2 units ship in one of two license tiers:

| | **Base** | **High Performance** |
|---|---|---|
| Sample rate | up to 100 kHz | up to 1 MHz |
| Analog bandwidth mode | standard (~25 kHz) | selectable, high-bandwidth (~150 kHz) |
| Multi-unit sync | — | yes |

```python
t = daq.tier()
print(t["name"])     # "base" or "high-performance"
print(t["fmax"])     # firmware-enforced sample-rate ceiling in Hz
print(t["sync"])     # multi-unit sync available on this unit
```

Tier limits are **enforced in firmware**. The driver never gates features
locally and contains no unlock mechanism — a refused operation raises
`coreDAQLicenseError` (a subclass of `coreDAQUnsupportedError`):

```python
from py_coreDAQ import coreDAQLicenseError

try:
    daq.set_sync_mode("slave")
except coreDAQLicenseError as e:
    print(e)     # multi-unit sync requires the High Performance tier ...
```

Requesting a sample rate above the tier ceiling is not an error: the firmware
clamps it and the driver stores the applied rate, emitting a `RuntimeWarning`
so timing math always uses device truth.

Tier upgrades are handled by Core Instrumentation per unit (a firmware image
provisioned to the device); contact support with the output of `daq.uid()`.
