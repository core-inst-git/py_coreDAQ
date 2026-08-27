"""Two-unit lockstep capture — the cluster way (mk2, High Performance).

Device order is chain order: the first device is the chain master (its sync
OUT feeds the next unit's IN over the straight SATA cable).
"""
from py_coreDAQ import coreDAQ, coreDAQCluster

dev1 = coreDAQ.connect()                                     # chain master
dev2 = coreDAQ.connect(transport="ethernet", host="192.168.0.51")

with coreDAQCluster(dev1, dev2) as cluster:                  # 10 channels: 0-9
    cluster.set_sample_rate_hz(500_000)
    res = cluster.capture(100_000)
    print("channels:", res.enabled_channels)
    print("frame-aligned traces:", len(res.trace(0)), len(res.trace(5)))

# ---------------------------------------------------------------------------
# Appendix — the manual per-device protocol the cluster automates:
#
#   for d in (master, slave):
#       d.reset(); d.set_capture_channel_mask(0x1F); d.set_oversampling(0)
#   master.set_sample_rate_hz(500_000)     # rate comes from the master
#   master.arm_capture(n)                  # master arms FIRST
#   slave.arm_capture(n)
#   slave.start_capture()                  # slaves start FIRST
#   master.start_capture()
#   res_m = master.collect_capture(n); res_s = slave.collect_capture(n)
