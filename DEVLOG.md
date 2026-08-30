# DEVLOG — py_coreDAQ

## 2026-08-30 — v2.2.0 long-session resilience

### Scope
Harden the device + driver for long unattended acquisition (capture->dump->repeat
for days; a single low-rate capture can take ~24h to fill SDRAM).

### What changed
- FIRMWARE v1.2 (separate repo): feed the IWDG inside the XFER/XFERTEST stream loop.
  A full-buffer dump (~33MB, tens of seconds over USB-FS) exceeded the 8.19s watchdog
  and self-reset the device MID-DUMP, losing the capture. HIL-proven before/after.
- Poll-based long-capture wait: mk2 captures longer than 2s poll FRAMES? (capture-safe,
  firmware-verified) instead of one blind multi-hour time.sleep — progress callback,
  cancellation, and mid-capture reset/disconnect detection. Short captures + mk1 keep
  the fast sleep.
- coreDAQResetError + device_reset_detected(): watch SYSSTAT? BOOTS/uptime to catch a
  mid-session device reboot (which loses the in-progress capture).
- Auto-recover (opt-in): connect(auto_reconnect=True, on_event=...); reconnect() (full
  re-init) + internal transport-only reopen used mid-capture so a host-side USB/TCP drop
  is ridden out and the intact buffer collected via collect_capture(frames=None).
- Cluster: early reset detection in its collect poll loop (catch a unit reboot in
  seconds, not at the full-duration deadline).
- Corrected the stale capture_is_data_ready() docstring (mk2 poll IS safe).

### Not done / deferred
- Streaming-to-disk long_acquire helper (user deferred: the fixed single capture + safe
  poll-wait + auto-recover cover the need).

## 2026-08-26 — v2.0.0 coreDAQ mk2 GA

### Scope
mk2 hardware generation supported end to end (5 channels, USB + Ethernet,
1 MHz High Performance tier, sensors, multi-unit sync, masking trigger mode)
while mk1 behavior is preserved bit-for-bit. Two-tier licensing surfaced
with typed errors; no unlock path exists in the driver.

### What changed
- Generation auto-detection hardened; `_fw_at_least()` fixes the mk2-vs-v4.3
  firmware-gate misroute in `collect_capture` (D1).
- Typed error taxonomy: `coreDAQLicenseError`, `coreDAQStateError` via a
  single ERR-payload mapper; message format unchanged.
- Defect fixes D2-D7: zero-frame XFER guard, OVFL parse hardening +
  `capture_overflowed()` False on old mk1, channel-count-aware
  `set_ranges`/`set_range_powers`, per-generation signal-health thresholds
  (mk1 regression-locked), mask-derived `read_frames` channel count,
  cross-session trigger-armed refusal.
- Tier UX: `tier()` gains `name`/`sync`; clamped sample rates warn and store
  device truth; Base-tier `set_sync_mode("slave")` raises with guidance.
- New public APIs: `arm_masked_capture()` (masking trigger mode),
  `hop_count()`, `arm_capture(gate=True)`.
- LOG nominal fallback model (SN0020+): 10 pA / 200 mV per decade.
- Docs: three new pages (mk2/tiers/sync), all pages generation-aware.
- Release path: tag-driven GitHub Actions is canonical (manual twine is
  break-glass); MANIFEST is an allowlist; plugin.json tracks the package
  version.

### Not done / deferred
- Ethernet device discovery (host= remains required).
- Registered pytest hardware markers (HIL stays in mk2_integration_test.py).

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
