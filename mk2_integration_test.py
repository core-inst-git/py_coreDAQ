#!/usr/bin/env python3
"""py_coreDAQ <-> mk2 hardware integration test (USB + Ethernet).

Runs the actual package against a live mk2 device on both transports, proving
the public API behaves identically. Assumes a Silicon LINEAR HIGHBW unit with a
linear cal flashed. Ethernet section auto-skips unless --host is given.

Usage:
  python3 mk2_integration_test.py
  python3 mk2_integration_test.py --host 192.168.0.12
"""
import argparse
import sys
import time

from py_coreDAQ import coreDAQ

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def exercise(daq, label):
    print(f"\n== {label} ==")
    idn = daq.identify()
    check(f"{label}: identify() mk2", "Mk2" in idn, idn)
    check(f"{label}: generation()==mk2", daq.generation() == "mk2", daq.generation())
    check(f"{label}: channel_count()==5", daq.channel_count() == 5, str(daq.channel_count()))

    # tier — read-only, no unlock path
    t = daq.tier()
    check(f"{label}: tier() HIGH", t["tier"] == "HIGH", str(t))
    check(f"{label}: no unlock/license attr on API",
          not any("unlock" in a.lower() or "license" in a.lower() for a in dir(daq)))

    # sensors
    temp, die, hum = daq.temperature(), daq.die_temperature(), daq.humidity()
    check(f"{label}: temperature sane", temp is None or 0 < temp < 60, f"{temp}")
    check(f"{label}: die_temperature sane", die is None or 20 < die < 90, f"{die}")
    check(f"{label}: humidity sane", hum is None or 0 <= hum <= 100, f"{hum}")

    # identity / status
    uid = daq.uid()
    check(f"{label}: uid() 24hex", len(uid) == 24, uid)
    ss = daq.sysstat()
    check(f"{label}: sysstat() dict", isinstance(ss, dict) and "uptime" in {k.lower() for k in ss}, str(ss)[:60])
    ipc = daq.ip_config()
    check(f"{label}: ip_config() has mode", "mode" in {k.lower() for k in ipc}, str(ipc))

    # gain (TIA range) round-trip on the 4 heads
    try:
        daq.set_range(0, 5)
        rs = daq.get_ranges()
        check(f"{label}: gain set/readback (head0=5)", rs[0] == 5, str(rs))
        daq.set_range(2, 3)
        rs = daq.get_ranges()
        check(f"{label}: cross-head preserved", rs[0] == 5 and rs[2] == 3, str(rs))
        for h in range(4):
            daq.set_range(h, 0)
    except Exception as e:
        check(f"{label}: gain path", False, str(e))

    # live read of all channels
    try:
        vals = daq.read_all(unit="v")
        check(f"{label}: read_all() 5 values", len(vals) == 5, str([round(v, 3) for v in vals]))
    except Exception as e:
        check(f"{label}: read_all()", False, str(e))

    # 5-channel block capture round-trip at 1 MSPS (enable the aux channel first;
    # default mask is 0x0F = 4 TIA heads, Analog_IN is opt-in)
    try:
        daq.set_capture_channel_mask(0x1F)
        daq.set_sample_rate_hz(1_000_000)
        n = 20000
        daq.arm_capture(n)
        daq.start_capture()
        time.sleep(n / daq.sample_rate_hz() + 0.4)
        res = daq.collect_capture(n, unit="v")
        chans = res.enabled_channels
        check(f"{label}: capture 5 channels", len(chans) == 5, str(chans))
        lens = [len(res.trace(c)) for c in chans]
        check(f"{label}: capture frame length", all(l == n for l in lens), str(lens))
    except Exception as e:
        check(f"{label}: capture path", False, str(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    a = ap.parse_args()

    daq = coreDAQ.connect()
    try:
        exercise(daq, "USB")
    finally:
        daq.close()

    if a.host:
        daq = coreDAQ.connect(transport="ethernet", host=a.host)
        try:
            exercise(daq, "Ethernet")
        finally:
            daq.close()
    else:
        print("\n(no --host; skipping Ethernet)")

    print(f"\n===== {len(PASS)} passed, {len(FAIL)} failed =====")
    if FAIL:
        print("FAILURES:", ", ".join(FAIL)); sys.exit(1)
    print("ALL GREEN — py_coreDAQ mk2 integration verified on hardware")


if __name__ == "__main__":
    main()
