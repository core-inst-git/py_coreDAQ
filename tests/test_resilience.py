"""Long-session resilience: poll-based wait, reset detection, auto-recover.

All sim-based (no hardware). The firmware-side XFER watchdog fix is HIL-verified
separately; these cover the host layer added in v2.1.
"""
import time

import pytest

from py_coreDAQ import (coreDAQ, coreDAQResetError, coreDAQError,
                        coreDAQTimeoutError)


def _mk2(**kw):
    kw.setdefault("generation", "mk2")
    kw.setdefault("tier", "HIGH")
    return coreDAQ.connect(simulator=True, **kw)


# ---------------------------------------------------------------------------
# poll-based long-capture wait
# ---------------------------------------------------------------------------

def test_long_capture_polls_not_sleeps(monkeypatch):
    # Force the long path: acq_s > 2 s. Sim completes instantly, so the poll
    # returns at once — but it must go through _poll_until_frames, not sleep.
    d = _mk2()
    d.set_capture_channels([0])
    d._sample_rate_hz = 100  # 300 frames / 100 Hz = 3 s > threshold
    called = {"poll": False, "sleep": False}
    real_poll = d._poll_until_frames
    monkeypatch.setattr(d, "_poll_until_frames",
                        lambda *a, **k: called.__setitem__("poll", True) or real_poll(*a, **k))
    monkeypatch.setattr(time, "sleep", lambda s: called.__setitem__("sleep", s > 1.0) if s > 1.0 else None)
    r = d.capture(300, unit="adc")
    assert called["poll"] and not called["sleep"]
    assert len(r.trace(0)) == 300
    d.close()


def test_short_capture_still_sleeps():
    # Short capture keeps the fast path (poll not engaged).
    d = _mk2()
    d.set_capture_channels([0])
    d._sample_rate_hz = 1_000_000
    r = d.capture(64, unit="adc")   # 64us -> way under 2s -> sleep path
    assert len(r.trace(0)) == 64
    d.close()


def test_progress_callback_fires(monkeypatch):
    d = _mk2()
    d.set_capture_channels([0])
    d._sample_rate_hz = 100
    seen = []
    d.capture(300, unit="adc", progress=lambda done, tgt: seen.append((done, tgt)))
    assert seen and seen[-1][1] == 300 and seen[-1][0] >= 300
    d.close()


# ---------------------------------------------------------------------------
# reset detection
# ---------------------------------------------------------------------------

def test_device_reset_detected():
    d = _mk2()
    assert d.device_reset_detected() is False        # baseline, no reset
    d._transport.sim_reset("WATCHDOG")               # simulate a reboot
    assert d.device_reset_detected() is True
    assert d._last_reset_cause == "WATCHDOG"
    assert d.device_reset_detected() is False         # baseline re-tracked
    d.close()


def test_reset_mid_capture_raises(monkeypatch):
    d = _mk2()
    d.set_capture_channels([0])
    n = 300
    # Device stays partial and is flagged reset on the health check.
    monkeypatch.setattr(d, "captured_frames", lambda: 10)     # never reaches n
    monkeypatch.setattr(d, "device_reset_detected",
                        lambda: (setattr(d, "_last_reset_cause", "WATCHDOG") or True))
    # health check fires when >3 s of wall-clock elapse; drive monotonic forward.
    seq = iter([0.0, 0.0, 0.1, 5.0])   # start, loop1 now, (health uses same now), next
    monkeypatch.setattr("py_coreDAQ._coredaq.time.monotonic",
                        lambda: next(seq, 100.0))
    monkeypatch.setattr("py_coreDAQ._coredaq.time.sleep", lambda s: None)
    with pytest.raises(coreDAQResetError) as ei:
        d._poll_until_frames(n, acq_s=1000.0, progress=None)  # big acq_s: deadline far off
    assert ei.value.reset_cause == "WATCHDOG"
    d.close()


# ---------------------------------------------------------------------------
# auto-reconnect
# ---------------------------------------------------------------------------

def test_reconnect_spec_and_light_reopen(monkeypatch):
    # A USB sim has no _conn spec -> reconnect refused; but we can exercise the
    # error path and the flags.
    d = _mk2()
    assert d._conn is None
    with pytest.raises(coreDAQError):
        d.reconnect()
    d.close()


def test_auto_reconnect_flag_plumbing():
    d = coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH",
                        auto_reconnect=True, on_event=lambda e, i: None)
    assert d._auto_reconnect is True
    assert d._on_event is not None
    d.close()


def test_emit_calls_callback_and_survives_bad_cb():
    events = []
    d = _mk2()
    d._on_event = lambda e, info: events.append(e)
    d._emit("reconnected", attempt=1)
    assert events == ["reconnected"]
    d._on_event = lambda e, info: (_ for _ in ()).throw(RuntimeError("boom"))
    d._emit("reconnected")   # must not raise
    d.close()


# ---------------------------------------------------------------------------
# no regression: mk1 keeps the sleep path (never polls)
# ---------------------------------------------------------------------------

def test_mk1_never_polls(monkeypatch):
    d = coreDAQ.connect(simulator=True)     # mk1
    d.set_capture_channels([0])
    d._sample_rate_hz = 10                  # would be "long" but mk1 must sleep
    called = {"poll": False}
    monkeypatch.setattr(d, "_poll_until_frames",
                        lambda *a, **k: called.__setitem__("poll", True))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    d.capture(64, unit="adc")
    assert called["poll"] is False
    d.close()
