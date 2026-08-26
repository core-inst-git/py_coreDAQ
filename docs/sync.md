# Multi-unit sync (mk2, High Performance)

Multiple mk2 units daisy-chain over the sync link (straight SATA cable,
OUT → IN) and sample from **one shared conversion clock**: no drift, no
sample slips, frame *k* on every unit taken at the same instant (~10 ns/link
deterministic skew).

## Roles

```python
master.set_sync_mode("master")     # or "standalone" (the default)
slave.set_sync_mode("slave")       # persists across power cycles
```

A Base-tier unit cannot participate — `set_sync_mode("slave")` raises
`coreDAQLicenseError` (see [Tiers & licensing](tiers.md)).

## Lockstep capture

Order matters — the master is armed first (arming silences its idle
conversion pulses), the slave starts first (it waits for the master's clock):

```python
for d in (master, slave):
    d.reset()
    d.set_capture_channel_mask(0x1F)
    d.set_oversampling(0)                    # slave settings MUST match master
master.set_sample_rate_hz(500_000)           # rate comes from the master

master.arm_capture(n)
slave.arm_capture(n)
slave.start_capture()                        # slave waits at 0 frames
master.start_capture()                       # both capture in lockstep

res_m = master.collect_capture(n)
res_s = slave.collect_capture(n)             # frame k == frame k
```

A slave with no master stays at 0 frames until `stop_capture()` — a slave
capture that ends short of its target should be discarded and re-run.
