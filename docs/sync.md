# Multi-unit sync (mk2, High Performance)

Multiple mk2 units daisy-chain over the sync link (straight SATA cable,
OUT → IN) and sample from **one shared conversion clock**: no drift, no
sample slips, frame *k* on every unit taken at the same instant (~10 ns/link
deterministic skew).

## The cluster — one logical device

`coreDAQCluster` presents the whole chain as a single device with a flat
channel space (unit 0 → channels 0–4, unit 1 → 5–9, …). Device order is
chain order; the first device is the chain master:

```python
from py_coreDAQ import coreDAQ, coreDAQCluster

dev1 = coreDAQ.connect()                                      # chain master
dev2 = coreDAQ.connect(transport="ethernet", host="192.168.0.51")

cluster = coreDAQCluster(dev1, dev2)                          # 10 channels
cluster.set_sample_rate_hz(500_000)
res = cluster.capture(100_000)
res.trace(7)          # dev2, local channel 2 — frame-aligned with trace(0)
```

The cluster owns and keeps matched what lockstep requires (roles, sample
rate on the master, oversampling and channel masks everywhere) and runs the
arm/start ordering internally. `arm_capture()` / `start_capture()` /
`collect_capture()` are also available for split flows. Mixed variants
(LINEAR + LOG, different detectors) are fully supported — every unit
converts its channels with its own calibration before the merge. Per-channel
methods (`set_range`, `get_range`) take global channel numbers and route to
the owning unit.

What the cluster does NOT offer: live snapshot reads (`read_all`) — the
firmware refuses snapshot conversions on slave units; use captures, or reach
one unit directly via `cluster.devices[i]`. Triggered/stepped/masked modes
are likewise single-device features.

## Failure semantics

Lockstep failures raise `coreDAQSyncError` and mean **discard and re-run**:

- a slave counting frames *before* the master starts, or all slaves stuck at
  0 frames → check the cable orientation (OUT → IN; cluster order must match
  cable order, master first);
- a unit finishing short of the armed count → the shared clock was
  interrupted (master death or a firmware tier-pace abort).

One operational note: the **first lockstep capture after a slave unit
powers up** may abort short of its target (the firmware absorbs a known
first-capture condition and recovers gracefully) — simply re-run the
capture; subsequent runs are clean:

```python
for attempt in range(3):
    try:
        res = cluster.capture(n)
        break
    except coreDAQSyncError:
        if attempt == 2:
            raise
```

A Base-tier unit anywhere in the chain fails cluster construction with
`coreDAQLicenseError` — multi-unit sync requires the High Performance tier
on every unit (see [Tiers & licensing](tiers.md)).

## Appendix: the manual per-device protocol

The cluster runs exactly this sequence; use it directly for full control:

```python
for d in (master, slave):
    d.reset()
    d.set_capture_channel_mask(0x1F)
    d.set_oversampling(0)                    # slave settings MUST match
master.set_sample_rate_hz(500_000)           # rate comes from the master

master.arm_capture(n)                        # master arms FIRST
slave.arm_capture(n)
slave.start_capture()                        # slaves start FIRST
master.start_capture()

res_m = master.collect_capture(n)
res_s = slave.collect_capture(n)             # frame k == frame k
```
