# coreDAQ mk2 & Ethernet

coreDAQ mk2 is the second hardware generation: **5 channels** (channels 0–3 are
calibrated detector heads, channel 4 is the uncalibrated Analog IN aux input),
sample rates up to **1 MHz** (High Performance tier), USB **and Ethernet**, and
on-board environment sensors. The driver detects the generation automatically —
`generation()` returns `"mk1"` or `"mk2"`, `channel_count()` returns 4 or 5 —
and every mk1 script runs unchanged on mk2.

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

The full command set works identically over both transports (DFU entry is
USB-only). One client at a time per device.

## mk2 extras

| Method | Purpose |
|---|---|
| `tier()` | license tier info — see [Tiers & licensing](tiers.md) |
| `temperature()` / `humidity()` / `die_temperature()` | environment sensors (`None` if not fitted) |
| `uid()` / `sysstat()` | device identity & health |
| `sync_mode()` / `set_sync_mode()` | multi-unit sync — see [Multi-unit sync](sync.md) |
| `capture_overflowed()` | run-till-stop buffer overflow flag |
| `arm_masked_capture()` / `hop_count()` | masking trigger mode (windowed acquisition) |
