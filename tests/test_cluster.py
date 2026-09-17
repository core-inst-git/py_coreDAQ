"""coreDAQCluster — sim-based tests (no hardware).

The simulator cannot detect lockstep-ordering violations by outcome, so
ordering tests spy on the transport command streams instead.
"""
import time

import numpy as np
import pytest

from py_coreDAQ import (CaptureResult, ClusterCaptureResult, coreDAQ,
                        coreDAQCluster, coreDAQError, coreDAQLicenseError,
                        coreDAQSyncError, coreDAQUnsupportedError)


def _mk2(**kw):
    kw.setdefault("generation", "mk2")
    kw.setdefault("tier", "HIGH")
    return coreDAQ.connect(simulator=True, **kw)


def _spy(dev, log, tag):
    real = dev._transport.ask

    def ask(cmd, *a, **k):
        log.append((tag, cmd))
        return real(cmd, *a, **k)

    dev._transport.ask = ask


# ---------------------------------------------------------------------------
# construction & refusals
# ---------------------------------------------------------------------------

def test_construction_forms():
    a, b = _mk2(), _mk2()
    c = coreDAQCluster(a, b)                     # varargs
    assert c.channel_count() == 10
    c2 = coreDAQCluster([a, b])                  # single iterable
    assert c2.channel_count() == 10
    assert c.channel_map()[7] == (1, 2)
    a.close(); b.close()


def test_empty_and_duplicate_refused():
    a = _mk2()
    with pytest.raises(ValueError):
        coreDAQCluster()
    with pytest.raises(ValueError, match="duplicate"):
        coreDAQCluster(a, a)
    a.close()


def test_mk1_refused():
    a, m1 = _mk2(), coreDAQ.connect(simulator=True)     # mk1 sim
    with pytest.raises(coreDAQUnsupportedError, match="unit 1"):
        coreDAQCluster(a, m1)
    a.close(); m1.close()


def test_base_tier_refused_master_and_slave():
    hi, lo = _mk2(), _mk2(tier="LOW")
    with pytest.raises(coreDAQLicenseError, match="unit 1"):
        coreDAQCluster(hi, lo)
    with pytest.raises(coreDAQLicenseError, match="unit 0"):
        coreDAQCluster(lo, hi)
    hi.close(); lo.close()


def test_role_hygiene_no_redundant_flash_writes():
    a, b = _mk2(), _mk2()
    a._transport.ask("SYNC MASTER")
    b._transport.ask("SYNC SLAVE")
    log = []
    _spy(a, log, 0); _spy(b, log, 1)
    coreDAQCluster(a, b)
    writes = [c for _, c in log if c.startswith("SYNC ") and "?" not in c]
    assert writes == []                          # roles already correct
    a.close(); b.close()


def test_roles_written_once_when_wrong():
    a, b = _mk2(), _mk2()
    b._transport.ask("SYNC MASTER")              # wrong for slave slot? default is MASTER anyway
    log = []
    _spy(a, log, 0); _spy(b, log, 1)
    coreDAQCluster(a, b)
    writes = [(t, c) for t, c in log if c.startswith("SYNC ") and "?" not in c]
    assert writes == [(1, "SYNC SLAVE")]         # only the mismatched unit
    a.close(); b.close()


# ---------------------------------------------------------------------------
# lockstep ordering (ask-spy)
# ---------------------------------------------------------------------------

def test_lockstep_ordering():
    a, b, c3 = _mk2(), _mk2(), _mk2()
    log = []
    cl = coreDAQCluster(a, b, c3)
    for i, d in enumerate((a, b, c3)):
        _spy(d, log, i)
    cl.set_sample_rate_hz(200_000)
    cl.capture(64, unit="adc")
    cmds = [(t, c) for t, c in log]
    arm_idx = {t: i for i, (t, c) in enumerate(cmds) if c.startswith("ACQ ARM")}
    start_idx = {t: i for i, (t, c) in enumerate(cmds) if c == "ACQ START"}
    assert arm_idx[0] < arm_idx[1] and arm_idx[0] < arm_idx[2]     # master arms first
    assert start_idx[1] < start_idx[0] and start_idx[2] < start_idx[0]  # slaves start first
    freq_targets = {t for t, c in cmds if c.startswith("FREQ ")}
    assert freq_targets == {0}                                     # rate: master only
    os_targets = {t for t, c in cmds if c.startswith("OS ")}
    assert os_targets == {0, 1, 2}                                 # OS fanned out
    cl.close()


# ---------------------------------------------------------------------------
# merge & routing
# ---------------------------------------------------------------------------

def test_merge_maps_units_to_global_channels():
    a = _mk2(incident_power_w=1e-3)
    b = _mk2(incident_power_w=2e-3)
    cl = coreDAQCluster(a, b)
    r = cl.capture(128, unit="adc")
    assert isinstance(r, CaptureResult) and isinstance(r, ClusterCaptureResult)
    assert r.enabled_channels == tuple(range(10))
    # distinct incident power -> unit1's head channels read higher codes
    assert float(np.mean(r.trace(5))) > float(np.mean(r.trace(0)))
    assert r.status(7).channel == 7
    assert len(r.per_unit) == 2
    assert r.per_unit[1].enabled_channels == (0, 1, 2, 3, 4)
    assert r.sample_rate_hz == cl.sample_rate_hz()
    cl.close()


def test_channel_subset_and_arm_only_unit():
    a, b = _mk2(), _mk2()
    reads = []
    real = a._transport.read_frames
    a._transport.read_frames = lambda *ar, **kw: reads.append(1) or real(*ar, **kw)
    cl = coreDAQCluster(a, b)
    r = cl.capture(64, unit="adc", channels=[6])
    assert r.enabled_channels == (6,)
    assert len(r.trace(6)) == 64
    assert reads == []                            # unit0 armed but never XFERed
    with pytest.raises(ValueError):
        r.trace(0)
    cl.close()


def test_range_routing_by_global_channel():
    a = _mk2(frontend="LINEAR")
    b = _mk2(frontend="LOG")
    cl = coreDAQCluster(a, b)
    cl.set_range(1, 3)                            # unit0 LINEAR ch1
    assert cl.get_range(1) == 3
    with pytest.raises(coreDAQUnsupportedError):
        cl.set_range(6, 3)                        # unit1 is LOG: native refusal
    cl.close()


def test_mixed_variants_capture():
    a = _mk2(frontend="LINEAR")
    b = _mk2(frontend="LOG")
    cl = coreDAQCluster(a, b)
    r = cl.capture(64, unit="v")
    assert r.enabled_channels == tuple(range(10))
    assert r.per_unit[0].frontend == "LINEAR" and r.per_unit[1].frontend == "LOG"
    cl.close()


# ---------------------------------------------------------------------------
# rate semantics
# ---------------------------------------------------------------------------

def test_slave_rate_cache_stamped():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    cl.set_sample_rate_hz(750_000)
    cl.arm_capture(16)                            # triggers settings apply
    assert b._sample_rate_hz == a._sample_rate_hz == 750_000
    cl.stop_capture()
    cl.close()


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------

def test_stray_frames_raise_sync_error():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    cl.arm_capture(64)
    real = b._transport.ask
    b._transport.ask = lambda c: ("OK", "5 MISSED=0 OVFL=0") if c == "FRAMES?" else real(c)
    with pytest.raises(coreDAQSyncError, match="before the master started"):
        cl.start_capture()
    b._transport.ask = real
    cl.close()


def test_short_slave_raises_and_reconfigures():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    cl.arm_capture(64)
    cl.start_capture()
    real = b._transport.ask
    b._transport.ask = lambda c: ("OK", "0 MISSED=0 OVFL=0") if c == "FRAMES?" else real(c)
    with pytest.raises(coreDAQSyncError, match="cable"):
        cl.collect_capture()
    b._transport.ask = real
    log = []
    _spy(a, log, 0)
    cl.arm_capture(16)                            # settings phase re-runs
    assert any(c.startswith("OS ") for _, c in log)
    cl.stop_capture()
    cl.close()


def test_single_unit_cluster_degenerates():
    a = _mk2()
    cl = coreDAQCluster(a)
    r = cl.capture(32, unit="adc")
    assert r.enabled_channels == (0, 1, 2, 3, 4)
    cl.close()


def test_split_flow_matches_capture():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    cl.arm_capture(64)
    cl.start_capture()
    r = cl.collect_capture(unit="adc")
    assert r.enabled_channels == tuple(range(10))
    assert len(r.trace(9)) == 64
    cl.close()


# ---------------------------------------------------------------------------
# partial-failure handling: any unit's error -> abort all, name unit, reusable
# ---------------------------------------------------------------------------

def _raise_on(dev, cmd_prefix, exc):
    real = dev._transport.ask

    def ask(cmd, *a, **k):
        if cmd.startswith(cmd_prefix):
            raise exc
        return real(cmd, *a, **k)

    dev._transport.ask = ask
    return real


def test_arm_failure_aborts_all_and_names_unit():
    a, b = _mk2(), _mk2()
    log = []
    cl = coreDAQCluster(a, b)
    _spy(a, log, 0)
    real = _raise_on(b, "ACQ ARM", coreDAQError("boom"))
    with pytest.raises(coreDAQError, match=r"unit 1 .*during arm"):
        cl.arm_capture(64)
    assert any(c == "ACQ STOP" for _, c in log)      # master got stopped too
    b._transport.ask = real
    r = cl.capture(32, unit="adc")                   # cluster still usable
    assert len(r.trace(0)) == 32
    cl.close()


def test_slave_start_failure_aborts_all():
    a, b = _mk2(), _mk2()
    log = []
    cl = coreDAQCluster(a, b)
    cl.arm_capture(64)
    _spy(a, log, 0)
    real = _raise_on(b, "ACQ START", coreDAQError("boom"))
    with pytest.raises(coreDAQError, match=r"unit 1 .*during start"):
        cl.start_capture()
    assert any(c == "ACQ STOP" for _, c in log)
    b._transport.ask = real
    r = cl.capture(32, unit="adc")
    assert len(r.trace(5)) == 32
    cl.close()


def test_collect_failure_names_unit_and_recovers():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    cl.arm_capture(64)
    cl.start_capture()
    orig = b.collect_capture
    b.collect_capture = lambda *ar, **kw: (_ for _ in ()).throw(coreDAQError("xfer died"))
    with pytest.raises(coreDAQError, match=r"unit 1 .*during collect"):
        cl.collect_capture(unit="adc")
    b.collect_capture = orig
    r = cl.capture(32, unit="adc")                   # settings re-applied, works
    assert len(r.trace(9)) == 32
    cl.close()


def test_error_type_preserved_with_cause():
    from py_coreDAQ import coreDAQTimeoutError
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    real = _raise_on(b, "ACQ ARM", coreDAQTimeoutError("slow"))
    with pytest.raises(coreDAQTimeoutError) as ei:
        cl.arm_capture(64)
    assert isinstance(ei.value.__cause__, coreDAQTimeoutError)
    b._transport.ask = real
    cl.close()


def test_reset_best_effort_all_units():
    a, b = _mk2(), _mk2()
    cl = coreDAQCluster(a, b)
    log = []
    _spy(b, log, 1)
    orig = a.reset
    a.reset = lambda: (_ for _ in ()).throw(coreDAQError("dead"))
    with pytest.raises(coreDAQError, match=r"unit 0 .*during reset"):
        cl.reset()
    assert any(c == "SOFTRESET" for _, c in log)     # unit 1 still reset
    a.reset = orig
    cl.close()
