# Sensors & Diagnostics <span class="mk2">Mk2</span>

coreDAQ Mk2 reports its own environmental and health information, so you can log conditions
alongside your measurements and confirm the instrument is running cleanly during a long
session.

<span class="mk2-legend">Everything on this page is available on coreDAQ Mk2 only.</span>

## Temperature & humidity

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect() as coredaq:
    print(coredaq.temperature())        # head temperature, °C  (or None)
    print(coredaq.humidity())           # relative humidity, %  (or None)
    print(coredaq.die_temperature())    # internal die temperature, °C
```

These readings are cached and refreshed about once a second, so they are safe to poll and
never interrupt a measurement. The head temperature/humidity sensor is fitted on
transimpedance (LINEAR) instruments; on a unit without it, `temperature()` and
`humidity()` return `None` rather than raising.

Logging conditions next to your data is a common use:

```python
p = coredaq.read_channel(0, unit="dbm")
t = coredaq.temperature()
print(f"{p:.2f} dBm at {t:.1f} °C")
```

## System status

`sysstat()` returns a snapshot of instrument health — uptime, how many times it has
booted, why it last reset, and free memory:

```python
info = coredaq.sysstat()
print(info["uptime"], info["boots"], info["reset"])   # seconds, boot count, last reset cause
```

This is useful for unattended or long-running setups: a change in the boot count
(`boots`) or a last-reset cause (`reset`) other than a normal power-on tells you the
instrument restarted.

## Detecting a reset mid-session

Over a long session you may want to know if the instrument restarted (for example after a
power glitch) so you can re-apply settings. The driver tracks this for you:

```python
if coredaq.device_reset_detected():
    coredaq.set_wavelength_nm(1550.0)   # re-apply your configuration
```

The driver can also reconnect automatically after a dropout — see
[Device State](state.md).

## Related pages

- [Device State](state.md) — the instrument state machine and resilience helpers
- [Performance Tiers & Licensing](tiers.md)
