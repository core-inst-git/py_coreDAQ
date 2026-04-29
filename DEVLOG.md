# DEVLOG — py_coreDAQ

## 2026-04-29 — v0.2.0 API redesign

**Scope:** Full rewrite from single-file module to proper package.

### What changed

**Package structure**
- `py_coreDAQ.py` (2 819 lines) → `py_coreDAQ/` package
- `_exceptions.py` — exception hierarchy: `coreDAQError`, `coreDAQConnectionError`, `coreDAQTimeoutError`, `coreDAQCalibrationError`, `coreDAQUnsupportedError`
- `_transport.py` — `Transport` ABC + `SerialTransport` (real hardware); LOGCAL and XFER binary protocols live here
- `_simulator.py` — `SimTransport` for all four device variants (InGaAs LOG/LINEAR, Si LOG/LINEAR); physically consistent power→ADC conversion
- `_driver.py` — refactored `_CoreDAQDriver`; now accepts a `Transport` instance instead of a port string; stripped of redundant conversion wrappers (`transfer_frames_W/mV/volts`, `snapshot_W/mV/volts`)
- `_device.py` — all public dataclasses, `ChannelProxy`, and `coreDAQ` class with `connect()` classmethod and deprecation shims
- `__init__.py` — clean public API surface

**New public API**
- `coreDAQ.connect(port=None, *, simulator=False, **sim_kwargs)` — auto-discovers hardware or returns a simulator
- `meter.channels[n].power_w` — live read via ChannelProxy
- `zero_dark()` raises `coreDAQUnsupportedError` on LOG frontends instead of silently no-opping
- Deprecation `DeprecationWarning` on: `get_data()`, `get_data_channel()`, `read_all_details()`, `read_channel_details()`, `get_range_all()`, `set_power_range()`, `current_ranges()`, `enabled_channels()`, `set_enabled_channels()`

**Simulator design decisions**
- Default variant: InGaAs LOG (most-popular SKU)
- LOG power→code: `V = Vy * log10(P * R(λ) / Iz)` → `code = V / ADC_LSB_VOLTS`
- LINEAR power→code: `slope = ADC_VFS_MV / max_power_w[gain]`; `code = P * slope / ADC_LSB_MV`
- Triggered capture fires immediately (no real edge detection)
- Seeded RNG (`seed=42` by default) for deterministic doc examples

**Key invariant (from user):** The device firmware only returns raw ADC codes.  All unit conversions (mV, W, dBm) happen host-side in `capture()` / `read_*()`.

**Tests**
- Migrated from `unittest` to `pytest`
- Kept 11 existing fake-driver tests unchanged in behavior
- Added 14 simulator smoke tests covering all four variants, sensors, ChannelProxy, seeded reproducibility

### Not done / deferred
- `CalibrationStrategy` pattern (Section 3.2 of API_REDESIGN_PLAN.md) — `_driver.py` still uses inline calibration; can be extracted in a future pass
- Hardware-in-loop test fixtures (needs `COREDAQ_HARDWARE_PORT` env var plumbing)
- `numpy` output option for `capture()` (open question #1 in plan)
