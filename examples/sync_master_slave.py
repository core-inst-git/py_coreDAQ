"""Two-unit lockstep capture over the sync link (mk2, High Performance).

Master is armed first (arming silences its idle pulses); the slave starts
first (it waits at 0 frames for the master's shared clock).
"""
from py_coreDAQ import coreDAQ

master = coreDAQ.connect()                                   # USB
slave = coreDAQ.connect(transport="ethernet", host="192.168.0.51")

master.set_sync_mode("master")
slave.set_sync_mode("slave")

n = 100_000
for d in (master, slave):
    d.reset()
    d.set_capture_channel_mask(0x1F)
    d.set_oversampling(0)                # slave settings must match the master
master.set_sample_rate_hz(500_000)       # the shared clock comes from the master

master.arm_capture(n)
slave.arm_capture(n)
slave.start_capture()
master.start_capture()

res_m = master.collect_capture(n)
res_s = slave.collect_capture(n)         # frame k == frame k on both units
print("master frames:", len(res_m.trace(0)), " slave frames:", len(res_s.trace(0)))

master.close(); slave.close()
