"""USB transfer speed benchmark for coreDAQ."""

import time
from py_coreDAQ import coreDAQ as coredaq

SAMPLE_RATE_HZ = 100_000
FRAMES         = 4_000_000

print("Connecting...")
dev = coredaq.connect()
print(f"  {dev.identify()}")

dev.set_sample_rate_hz(SAMPLE_RATE_HZ)
actual_hz = dev.sample_rate_hz()
n_ch = len(dev.capture_channels())
bytes_total = FRAMES * n_ch * 2
acq_s = FRAMES / actual_hz

print(f"  {actual_hz:,} Hz  |  {FRAMES:,} frames  |  {bytes_total/1e6:.1f} MB  |  {n_ch} ch")

print("\nArming + starting...")
dev.arm_capture(FRAMES)
dev.start_capture()
print(f"  Sleeping {acq_s:.1f} s for acquisition...")
time.sleep(acq_s + 0.5)

if not dev.capture_is_data_ready():
    print("ERROR: data not ready — aborting.")
    dev.close()
    raise SystemExit(1)

print("Collecting...")
t0 = time.perf_counter()
result = dev.collect_capture(FRAMES, unit="adc")
t1 = time.perf_counter()

total_s = t1 - t0
print()
print("=" * 50)
print(f"  Transfer time : {total_s:.3f} s")
print(f"  Data volume   : {bytes_total/1e6:.2f} MB")
print(f"  Throughput    : {bytes_total/total_s/1e6:.2f} MB/s")
print(f"  Frames/s      : {FRAMES/total_s:,.0f}")
print("=" * 50)

# Sanity check
for ch in dev.capture_channels():
    t = result.trace(ch)
    print(f"  CH{ch}: dtype={t.dtype}  first5={t[:5].tolist()}")

dev.close()
