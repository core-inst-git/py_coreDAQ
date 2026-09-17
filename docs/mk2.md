# coreDAQ mk2 & Ethernet

coreDAQ mk2 is the second hardware generation: **5 channels** (channels 0–3 are
calibrated detector heads, channel 4 is the uncalibrated Analog IN aux input),
sample rates up to **1 MHz** (High Performance tier), USB **and Ethernet**, and
on-board environment sensors. The driver detects the generation automatically —
`generation()` returns `"mk1"` or `"mk2"`, `channel_count()` returns 4 or 5 —
and every mk1 script runs unchanged on mk2.

<span class="mk2-legend">This section covers coreDAQ Mk2. Pages here — Ethernet, tiers, multi-unit sync, sensors, firmware updates — describe features available on Mk2 only.</span>

## Connecting over USB

```python
from py_coreDAQ import coreDAQ

daq = coreDAQ.connect()            # USB auto-discovery, mk1 and mk2 alike
print(daq.identify(), daq.generation(), daq.channel_count())
```

## Setting up Ethernet (once, over USB)

```python
daq = coreDAQ.connect()                        # USB
daq.set_ip_static("192.168.0.50", "255.255.255.0", "192.168.0.1")
# or: daq.set_ip_dhcp()
print(daq.ip_config())
daq.close()
```

## Connecting over Ethernet

```python
daq = coreDAQ.connect(transport="ethernet", host="192.168.0.50")
print(daq.eth_status())
```

Everything works identically over both transports; one client at a time per
device. Firmware updates are done from your browser over USB — see
[Firmware Updates](firmware.md).

## mk2 extras

| Method | Purpose |
|---|---|
| `tier()` | license tier info — see [Performance Tiers & Licensing](tiers.md) |
| `temperature()` / `humidity()` / `die_temperature()` / `sysstat()` | environment & health — see [Sensors & Diagnostics](sensors.md) |
| `uid()` / `ip_config()` / `eth_status()` | device identity & network |
| `sync_mode()` / `set_sync_mode()` | multi-unit sync — see [Multi-unit Sync](sync.md) |
| `capture_overflowed()` | run-till-stop buffer overflow flag — see [Capture](capture.md) |
| `arm_masked_capture()` / `hop_count()` | masking / swept-laser mode — see [External Trigger](trigger.md) |
