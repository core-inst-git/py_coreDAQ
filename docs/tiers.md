# Performance Tiers & Licensing <span class="mk2">Mk2</span>

<span class="mk2-legend">Performance tiers are a coreDAQ Mk2 feature.</span>

Mk2 units ship in one of two licence tiers:

| | **Base** | **High Performance** |
|---|---|---|
| Sample rate | up to 100 kHz | up to 1 MHz |
| Bandwidth | standard | selectable high bandwidth |
| Multi-unit sync | — | yes |

Exact bandwidth figures are in the **[datasheet](https://core-instrumentation.com/datasheet)**.

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

## How tier enforcement works

Every tier limit — the sample-rate ceiling, the high-bandwidth mode, the
multi-unit-sync lockout — is enforced inside the instrument's firmware, keyed to
the individual unit. There is **nothing to unlock on the computer side**: the
instrument clamps rates and refuses gated features regardless of what software
requests, and a High-performance licence is cryptographically bound to one unit,
so it cannot be copied to another. This driver is open source and holds no
secrets — its openness does not affect the licensing.
