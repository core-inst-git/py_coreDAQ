# Connections & back panel

Everything the coreDAQ needs is on the back panel. A single USB-C cable powers the
instrument and carries data — nothing else is required to take a measurement.

<span class="mk2-legend"><span class="mk2">Mk2</span> marks a feature available on coreDAQ Mk2 only.</span>

| Connector | Function |
| --- | --- |
| **USB-C** | Power **and** data. One cable connects the instrument to your computer. |
| **Ethernet** <span class="mk2">Mk2</span> | Remote control and data over your network. See [Mk2 & Ethernet](mk2.md). |
| **Analog IN** <span class="mk2">Mk2</span> | Auxiliary analog input — the 5th channel (channel 4). |
| **TRIG 0** | Primary start-trigger input. See [External Trigger](trigger.md). |
| **TRIG 1** <span class="mk2">Mk2</span> | Masking / swept-laser trigger input. See [External Trigger](trigger.md). |
| **coreLINK clock** (SATA) <span class="mk2">Mk2</span> | Multi-unit synchronisation. See [Multi-unit Sync](sync.md). |

Mechanical drawings, panel layout, and connector specifications are in the
**[datasheet](https://core-instrumentation.com/datasheet)**.

## USB-C

The instrument is USB-powered — plug it into your computer and it appears as a serial
device. On real hardware the driver finds it automatically:

```python
from py_coreDAQ import coreDAQ

coredaq = coreDAQ.connect()      # auto-discovers the coreDAQ on USB
```

## Ethernet <span class="mk2">Mk2</span>

An Ethernet port lets you control the instrument and stream data over a LAN, using the
same commands as USB. Set it up over USB once, then reconnect over the network — see
[Mk2 & Ethernet](mk2.md).

## Analog IN — channel 5 <span class="mk2">Mk2</span>

Mk2 adds a fifth channel, labelled **Analog IN** on the panel, alongside the four optical
detector channels. It is a general-purpose 0–5 V auxiliary input — useful for logging an
external monitor voltage frame-aligned with your optical channels. It has no detector, so
it carries **no optical-power meaning**; read it in volts or raw ADC counts:

```python
aux = coredaq.read_channel(4, unit="v")     # channel 4 = Analog IN, in volts
```

The four optical channels (0–3) are always the photodiode heads. Channel 4 is opt-in for
capture — see [Frames & Memory](frames.md).

## TRIG 0 and TRIG 1

- **TRIG 0** is the primary **start trigger**: an external edge on this input starts an
  acquisition. Available on all generations.
- **TRIG 1** <span class="mk2">Mk2</span> is the **masking trigger**, used by the
  swept-laser (COMET) and masking capture modes to gate sampling during a scan.

Both are covered in [External Trigger](trigger.md).

## coreLINK clock — SATA <span class="mk2">Mk2</span>

Multiple Mk2 units share a single conversion clock over the **coreLINK** connectors so
they sample in lockstep. The two clock connectors use SATA cables and are **vertically
stacked**:

- **Bottom row = clock IN** — a follower (slave) unit takes its clock in here.
- **Top row = clock OUT** — the leader passes the clock on from here.

Chain units **OUT → IN** (top of one unit to the bottom of the next). Full wiring and the
`coreDAQCluster` workflow are in [Multi-unit Sync](sync.md).

## Related pages

- [Mk2 & Ethernet](mk2.md) — network setup
- [External Trigger](trigger.md) — TRIG 0 / TRIG 1
- [Multi-unit Sync](sync.md) — coreLINK clock wiring
