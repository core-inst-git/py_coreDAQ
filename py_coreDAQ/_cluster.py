"""coreDAQCluster — several lockstep-chained mk2 units as one logical device.

Multiple mk2 units daisy-chained over the sync link share one conversion
clock; the cluster presents them as a single device whose channel space is
the concatenation of all units' channels (unit 0 → globals 0..4, unit 1 →
5..9, ...). The lockstep protocol — role assignment, matched settings,
master-first arming, slaves-first starting, per-unit collection and the
merge — happens inside; the public methods mirror ``coreDAQ``.

Device order IS chain order: the first device is the chain master (the unit
whose sync OUT feeds the next unit's IN). Mixed variants are fully supported
— each unit converts its own channels with its own calibration before the
merge, so LINEAR and LOG (or different detectors) coexist in one cluster.

Live single-shot reads (``read_all``) are not available on a cluster: the
firmware refuses snapshot conversions on slave units (their local conversion
strobe is disconnected from the shared clock). Use captures, or reach a
specific unit via ``cluster.devices[i]``.
"""
from __future__ import annotations

import dataclasses
import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from ._coredaq import CaptureResult, coreDAQ
from ._exceptions import (
    coreDAQError,
    coreDAQLicenseError,
    coreDAQSyncError,
    coreDAQUnsupportedError,
)

_QUIET_WINDOW_S = 0.05      # armed-master silence check before the master starts
_EXTRA_WAIT_S = 2.0         # grace beyond the sleep estimate before declaring short


@dataclasses.dataclass(frozen=True)
class ClusterCaptureResult(CaptureResult):
    """A merged lockstep capture. IS-A CaptureResult with global channel keys.

    ``trace(g)``/``status(g)`` take global channel indices. Scalars
    (``sample_rate_hz``, ``detector``, ``frontend``, ``wavelength_nm``) come
    from the chain master; per-unit detail lives in ``per_unit`` (raw
    single-device results in chain order, local channel keys).
    """

    per_unit: Tuple[CaptureResult, ...] = ()


class coreDAQCluster:
    """N lockstep-chained mk2 units driven as one device.

    Construct with connected devices in chain order (first = master)::

        cluster = coreDAQCluster(dev1, dev2, dev3)
        cluster.set_sample_rate_hz(500_000)
        res = cluster.capture(100_000)          # res.trace(7) = dev2, channel 2

    All units must be mk2 with the multi-unit-sync capability
    (``tier()['sync']``); roles are applied once here (flash-persisted, only
    written when they differ). Oversampling and channel masks are kept
    matched across units by the cluster; the sample rate lives on the master
    (slaves follow the shared clock).
    """

    def __init__(self, *devices: Union[coreDAQ, Sequence[coreDAQ]]) -> None:
        if len(devices) == 1 and not isinstance(devices[0], coreDAQ):
            devices = tuple(devices[0])          # single iterable form
        devs: Tuple[coreDAQ, ...] = tuple(devices)
        if not devs:
            raise ValueError("coreDAQCluster needs at least one device")
        if len({id(d) for d in devs}) != len(devs):
            raise ValueError("duplicate device object in cluster list")
        for i, d in enumerate(devs):
            if not isinstance(d, coreDAQ):
                raise TypeError(f"cluster element {i} is not a coreDAQ: {d!r}")
            if d.generation() != "mk2":
                raise coreDAQUnsupportedError(
                    f"cluster unit {i} ({d.identify()}) is a coreDAQ mk1 — "
                    f"multi-unit sync requires mk2 units throughout the chain")
        for i, d in enumerate(devs):
            if not d.tier().get("sync", False):
                raise coreDAQLicenseError(
                    f"cluster unit {i} ({d.identify()}) reports the Base tier "
                    f"— multi-unit sync requires the High Performance tier on "
                    f"every unit in the chain (see tier()). There is no "
                    f"software unlock; contact Core Instrumentation to "
                    f"upgrade the unit.")

        self._devices = devs
        # Global channel table: offsets from the actual per-unit counts.
        self._offsets: List[int] = []
        self._map: List[Tuple[int, int]] = []    # global -> (unit_idx, local)
        off = 0
        for ui, d in enumerate(devs):
            self._offsets.append(off)
            n = d.channel_count()
            self._map.extend((ui, lc) for lc in range(n))
            off += n
        self._n_channels = off

        # Roles: read first, write only on mismatch (flash-persisted + slow).
        want = ["MASTER"] + ["SLAVE"] * (len(devs) - 1)
        for ui, (d, role) in enumerate(zip(devs, want)):
            try:
                if d.sync_mode() != role:
                    d.set_sync_mode(role)
            except coreDAQError as exc:
                raise type(exc)(
                    f"cluster unit {ui} ({d.identify()}) failed during role "
                    f"assignment: {exc}") from exc

        # Simulator convenience: link sim slaves to the sim master so the
        # shared-clock completion is modeled. No effect on hardware.
        transports = [d._transport for d in devs]
        if all(hasattr(t, "sync_listeners") for t in transports):
            transports[0].sync_listeners = list(transports[1:])

        # Cluster-owned settings (defaults mirror a fresh coreDAQ.connect()).
        self._os = 1
        self._rate = 500
        self._selected: Tuple[int, ...] = tuple(range(self._n_channels))
        self._needs_config = True
        self._armed_frames = 0

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def connect(cls, specs: Sequence[Union[str, Dict[str, Any], coreDAQ]]
                ) -> "coreDAQCluster":
        """Open devices from *specs* (chain order) and build a cluster.

        Each spec is a USB port string, a dict of ``coreDAQ.connect``
        keyword arguments (e.g. ``{"transport": "ethernet", "host": ...}``),
        or an already-open ``coreDAQ``. Devices opened here are closed again
        if construction fails.
        """
        opened: List[coreDAQ] = []
        devs: List[coreDAQ] = []
        try:
            for spec in specs:
                if isinstance(spec, coreDAQ):
                    devs.append(spec)
                elif isinstance(spec, str) or spec is None:
                    d = coreDAQ.connect(spec)
                    opened.append(d)
                    devs.append(d)
                elif isinstance(spec, dict):
                    d = coreDAQ.connect(**spec)
                    opened.append(d)
                    devs.append(d)
                else:
                    raise TypeError(f"unsupported cluster spec: {spec!r}")
            return cls(*devs)
        except BaseException:
            for d in opened:
                try:
                    d.close()
                except Exception:
                    pass
            raise

    # ------------------------------------------------------------------
    # topology / bookkeeping
    # ------------------------------------------------------------------

    @property
    def devices(self) -> Tuple[coreDAQ, ...]:
        """The units in chain order (index 0 = master)."""
        return self._devices

    def channel_count(self) -> int:
        """Total channels across the cluster."""
        return self._n_channels

    def channel_map(self) -> Tuple[Tuple[int, int], ...]:
        """Per global channel: ``(unit_index, local_channel)``."""
        return tuple(self._map)

    def _route(self, channel: int) -> Tuple[coreDAQ, int, int]:
        ch = int(channel)
        if not (0 <= ch < self._n_channels):
            raise ValueError(
                f"channel must be 0..{self._n_channels - 1}, got {channel}")
        ui, lc = self._map[ch]
        return self._devices[ui], ui, lc

    def __repr__(self) -> str:
        return (f"<coreDAQCluster {len(self._devices)} units, "
                f"{self._n_channels} channels, master={self._devices[0]!r}>")

    # ------------------------------------------------------------------
    # settings (cluster-owned; matched across units)
    # ------------------------------------------------------------------

    def set_sample_rate_hz(self, hz: int) -> None:
        """Set the shared sample rate (applied on the chain master).

        Slaves follow the master's conversion clock in hardware; the cluster
        keeps their host-side rate caches in step so timing math and result
        metadata stay truthful. A tier clamp on the master warns exactly like
        the single-device call.
        """
        self._rate = int(hz)
        self._needs_config = True

    def sample_rate_hz(self) -> int:
        """The shared sample rate in Hz (master's applied value)."""
        if not self._needs_config:
            return self._devices[0].sample_rate_hz()
        return self._rate

    def set_oversampling(self, os_idx: int) -> None:
        """Set the oversampling index on every unit (must match chain-wide)."""
        self._os = int(os_idx)
        self._needs_config = True

    def oversampling(self) -> int:
        """The matched oversampling index."""
        return self._os

    def set_capture_channels(self, channels: Sequence[int]) -> Tuple[int, ...]:
        """Select global channels for capture (any mix across units)."""
        sel = tuple(sorted({int(c) for c in channels}))
        for c in sel:
            self._route(c)                       # bounds check
        if not sel:
            raise ValueError("at least one channel must be selected")
        self._selected = sel
        self._needs_config = True
        return sel

    def capture_channels(self) -> Tuple[int, ...]:
        """Currently selected global channels."""
        return self._selected

    def set_reading_unit(self, unit: str) -> None:
        """Set the default measurement unit on every device."""
        for d in self._devices:
            d.set_reading_unit(unit)

    def reading_unit(self) -> str:
        """The master's default measurement unit."""
        return self._devices[0].reading_unit()

    def set_range(self, channel: int, range_index: int) -> None:
        """Set a TIA range by GLOBAL channel (routed to the owning unit).

        Keeps the owning unit's native behavior — e.g. LOG frontends refuse
        exactly as they do on a single device.
        """
        d, _, lc = self._route(channel)
        d.set_range(lc, range_index)

    def get_range(self, channel: int) -> Optional[int]:
        """Read a TIA range by GLOBAL channel (None on non-TIA channels)."""
        d, _, lc = self._route(channel)
        return d.get_range(lc)

    def max_capture_frames(self) -> int:
        """Frame budget for the current selection (min across units)."""
        per_unit = self._per_unit_selection()
        vals = []
        for ui, d in enumerate(self._devices):
            local = per_unit[ui] or [0]          # arm-only units hold 1 channel
            vals.append(d.max_capture_frames(local))
        return min(vals)

    # ------------------------------------------------------------------
    # lockstep capture
    # ------------------------------------------------------------------

    def _per_unit_selection(self) -> List[List[int]]:
        sel: List[List[int]] = [[] for _ in self._devices]
        for c in self._selected:
            ui, lc = self._map[c]
            sel[ui].append(lc)
        return sel

    def _apply_settings(self) -> None:
        """(Re)apply cluster-owned settings on every unit, in firmware order.

        SOFTRESET wipes OS/mask/rate to boot defaults, so this runs after any
        reset/failure and before the first capture. Order matters: OS first
        (the firmware clamps FREQ to the OS ceiling), then masks, then the
        master's FREQ; finally slave rate caches are stamped with the
        master's applied rate so their metadata cannot go stale.
        """
        per_unit = self._per_unit_selection()
        for ui, d in enumerate(self._devices):
            try:
                d.set_oversampling(self._os)
                local = per_unit[ui] or [0]      # CHMASK 0 is invalid: arm ch0
                d.set_capture_channels(local)
            except BaseException as exc:
                raise self._unit_context(exc, ui, "settings apply") from exc
        self._devices[0].set_sample_rate_hz(self._rate)
        applied = self._devices[0].sample_rate_hz()
        self._rate = applied
        for d in self._devices[1:]:
            d._sample_rate_hz = applied          # in-package cache stamp
        self._needs_config = False

    def _abort_all(self) -> None:
        """Best-effort recovery: stop every unit, force a settings re-apply.

        Called whenever any unit fails mid-orchestration so the OTHER units
        are never left armed/active. Never raises.
        """
        self.stop_capture()
        self._needs_config = True

    def _unit_context(self, exc: BaseException, ui: int, phase: str) -> BaseException:
        """Re-raiseable error naming the failing unit; original chained."""
        if isinstance(exc, coreDAQError):
            try:
                idn = self._devices[ui].identify()
            except Exception:
                idn = "?"
            return type(exc)(f"cluster unit {ui} ({idn}) failed during "
                             f"{phase}: {exc}")
        return exc                                # non-driver errors: as-is

    def arm_capture(self, frames: int) -> None:
        """Arm a lockstep capture on every unit (master first).

        Plain synchronous captures only: trigger, stepped and masked modes
        are refused by the firmware on slave units, so the cluster does not
        offer them — use a single device for those.

        If any unit fails, every unit is stopped and the error re-raises
        naming the unit; the cluster stays usable (settings re-apply on the
        next arm).
        """
        n = int(frames)
        if n <= 0:
            raise ValueError("frames must be > 0")
        if self._needs_config:
            self._apply_settings()
        for ui, d in enumerate(self._devices):   # master first: silences its
            try:                                 # idle conversion pulses
                d.arm_capture(n)
            except BaseException as exc:
                self._abort_all()
                raise self._unit_context(exc, ui, "arm") from exc
        self._armed_frames = n

    def start_capture(self) -> None:
        """Start the lockstep capture: slaves first, then the master.

        Between the slave starts and the master start, a short quiet window
        verifies no slave is receiving frames — a slave counting frames while
        the master is still armed-silent means something else is clocking its
        sync input (usually a reversed cable). That raises
        ``coreDAQSyncError`` with everything stopped cleanly.
        """
        if self._armed_frames <= 0:
            raise coreDAQError("start_capture() without arm_capture()")
        for ui, d in enumerate(self._devices[1:], start=1):
            try:
                d.start_capture()
            except BaseException as exc:
                self._abort_all()
                raise self._unit_context(exc, ui, "start") from exc
        if len(self._devices) > 1:
            time.sleep(_QUIET_WINDOW_S)
            for ui, d in enumerate(self._devices[1:], start=1):
                try:
                    stray = d.captured_frames()
                except BaseException as exc:
                    self._abort_all()
                    raise self._unit_context(exc, ui, "quiet-window check") from exc
                if stray:
                    self._abort_all()
                    raise coreDAQSyncError(
                        f"slave unit {ui} captured {stray} frames before the "
                        f"master started — something else is clocking its "
                        f"sync input. Cluster order must match cable order "
                        f"(index 0 = chain master; cables run OUT → IN).")
        try:
            self._devices[0].start_capture()
        except BaseException as exc:
            self._abort_all()
            raise self._unit_context(exc, 0, "master start") from exc

    def collect_capture(self, frames: Optional[int] = None,
                        unit: Optional[str] = None) -> ClusterCaptureResult:
        """Wait for completion, verify every unit, collect and merge.

        *frames* defaults to the armed count. *unit* is the measurement unit
        (``"w"``, ``"dbm"``, ``"v"``, ``"mv"``, ``"adc"``), per device
        default when None — exactly like ``coreDAQ.collect_capture``.
        """
        n = self._armed_frames if frames is None else int(frames)
        if n <= 0:
            raise coreDAQError("collect_capture() without an armed capture")
        rate = max(1, self._rate)
        overhead = max(getattr(d._transport, "acq_overhead_s", 0.5)
                       for d in self._devices)
        deadline = time.monotonic() + n / rate + overhead + _EXTRA_WAIT_S
        while True:
            counts = []
            for ui, d in enumerate(self._devices):
                try:
                    counts.append(d.captured_frames())
                except BaseException as exc:
                    self._abort_all()
                    raise self._unit_context(exc, ui, "completion wait") from exc
            if all(c >= n for c in counts):
                break
            if time.monotonic() >= deadline:
                self.stop_capture()
                self._needs_config = True
                if all(c == 0 for c in counts[1:]) and len(counts) > 1:
                    raise coreDAQSyncError(
                        f"all slaves stayed at 0 frames (master stored "
                        f"{counts[0]}) — no shared clock reached them. Check "
                        f"the sync cable orientation (OUT → IN, master "
                        f"first in the cluster list).")
                raise coreDAQSyncError(
                    f"lockstep run incomplete after timeout: per-unit frames "
                    f"{counts} of {n} armed — the shared clock was "
                    f"interrupted (master death or tier-pace abort). Discard "
                    f"and re-run.")
            time.sleep(0.05)

        per_unit = self._per_unit_selection()
        results: List[Optional[CaptureResult]] = [None] * len(self._devices)
        for ui, d in enumerate(self._devices):
            if not per_unit[ui]:
                continue                         # arm-only unit: never XFERed
            try:
                results[ui] = d.collect_capture(n, unit=unit)
            except BaseException as exc:
                self._abort_all()                # failed unit was SOFTRESET by
                raise self._unit_context(        # its own driver already
                    exc, ui, "collect") from exc
        self._armed_frames = 0
        return self._merge(results, unit)

    def capture(self, frames: int, unit: Optional[str] = None,
                channels: Optional[Sequence[int]] = None
                ) -> ClusterCaptureResult:
        """One-line lockstep capture: arm, start, wait, collect, merge."""
        if channels is not None:
            self.set_capture_channels(channels)
        self.arm_capture(frames)
        self.start_capture()
        return self.collect_capture(frames, unit=unit)

    def _merge(self, results: Sequence[Optional[CaptureResult]],
               unit: Optional[str]) -> ClusterCaptureResult:
        master_res = next(r for r in results if r is not None)
        traces: Dict[int, np.ndarray] = {}
        statuses: Dict[int, Any] = {}
        ranges: Dict[int, Optional[int]] = {}
        labels: Dict[int, Optional[str]] = {}
        enabled: List[int] = []
        for ui, r in enumerate(results):
            if r is None:
                continue
            off = self._offsets[ui]
            for lc in r.enabled_channels:
                g = off + lc
                enabled.append(g)
                traces[g] = r.traces[lc]
                statuses[g] = dataclasses.replace(r.statuses[lc], channel=g)
                ranges[g] = r.ranges.get(lc)
                labels[g] = r.range_labels.get(lc)
        m = results[0] if results[0] is not None else master_res
        return ClusterCaptureResult(
            traces=traces,
            statuses=statuses,
            unit=master_res.unit,
            sample_rate_hz=self._rate,
            enabled_channels=tuple(sorted(enabled)),
            ranges=ranges,
            range_labels=labels,
            wavelength_nm=m.wavelength_nm,
            detector=m.detector,
            frontend=m.frontend,
            per_unit=tuple(r for r in results if r is not None),
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def stop_capture(self) -> None:
        """Abort on every unit (always safe)."""
        for d in self._devices:
            try:
                d.stop_capture()
            except Exception:
                pass
        self._armed_frames = 0

    def reset(self) -> None:
        """SOFTRESET every unit and re-apply the cluster settings.

        Best-effort: every unit is reset even if one fails; the first
        failure re-raises afterwards (settings stay marked for re-apply, so
        a later arm heals the survivors).
        """
        self._needs_config = True                # before anything can fail
        first: Optional[BaseException] = None
        first_ui = 0
        for ui, d in enumerate(self._devices):
            try:
                d.reset()
            except BaseException as exc:
                if first is None:
                    first, first_ui = exc, ui
        if first is not None:
            raise self._unit_context(first, first_ui, "reset") from first
        self._apply_settings()

    def close(self) -> None:
        """Close every device (unconditionally)."""
        for d in self._devices:
            try:
                d.close()
            except Exception:
                pass

    def __enter__(self) -> "coreDAQCluster":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
