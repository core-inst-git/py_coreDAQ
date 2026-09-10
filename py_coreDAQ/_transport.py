"""Transport layer for coreDAQ.

Defines the Transport ABC that all I/O backends must implement, and the
SerialTransport that wraps pyserial for real hardware.  SimTransport lives
in _simulator.py.
"""

from __future__ import annotations

import math
import re
import struct
import threading
import time
import zlib
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

import serial
import serial.tools.list_ports

from ._exceptions import (
    CoreDAQError,
    coreDAQCalibrationError,
    coreDAQTimeoutError,
    coreDAQUSBError,
    error_for_payload,
)

# ---------------------------------------------------------------------------
# Integrity-checked bulk transfer (XFERC, firmware v4.4+)
# ---------------------------------------------------------------------------
# Adaptive chunk ladder: pull the capture as 1 -> 2 -> 4 -> 8 -> 16 frame-aligned
# sub-ranges via `XFERC <off> <len>`, each verified by a 12-byte CRC32 trailer.
# A chunk failure -> XFERABORT (capture-preserving) + resync -> retry the whole
# transfer at the next-finer split; all levels exhausted -> coreDAQUSBError.
# Legacy `XFER` (firmware v4.2/v4.3) is auto-detected and left byte-identical.
_XFERC_LEVELS = (1, 2, 4, 8, 16)
_XFERC_TRAILER = 12   # b"CRC2" + len(u32 LE) + crc32(u32 LE)


class _ChunkFail(Exception):
    """One XFERC sub-range failed (timeout / short / CRC / magic)."""


class _LegacyDetected(Exception):
    """Firmware rejected XFERC (UNKNOWN_CMD) -> fall back to legacy XFER."""


def _xfer_timeouts(nbytes: int) -> tuple[float, float]:
    """Stock conservative (overall, idle) timeouts. Scales with size; never the
    5 s debug cap that produced the customer's false failures."""
    mb = nbytes / 1_000_000.0
    return (max(30.0, mb * 20.0), max(30.0, mb * 5.0))


def _split_frame_aligned(total: int, nchunks: int, frame_bytes: int) -> list[tuple[int, int]]:
    """Split [0,total) into nchunks frame-aligned (off, len) sub-ranges; the last
    absorbs the remainder (still frame-aligned since total is a whole # of frames)."""
    base = (total // nchunks)
    base -= base % frame_bytes
    if base == 0:
        base = frame_bytes
    out: list[tuple[int, int]] = []
    off = 0
    for _ in range(nchunks - 1):
        out.append((off, base))
        off += base
    out.append((off, total - off))
    return out


class Transport(ABC):
    """Abstract I/O backend for _CoreDAQDriver.

    Implementors: SerialTransport (real device) and SimTransport (simulator).

    All methods are thread-safe — implementations must hold an internal lock
    for the duration of each exchange.
    """

    @abstractmethod
    def ask(self, cmd: str) -> tuple[str, str]:
        """Send *cmd*, return (status, payload).

        status is one of "OK", "ERR", or "BUSY".
        """

    @abstractmethod
    def ask_with_busy_retry(
        self,
        cmd: str,
        retries: int = 20,
        delay_s: float = 0.05,
    ) -> tuple[str, str]:
        """Like ask(), but retry on BUSY up to *retries* times."""

    @abstractmethod
    def logcal(self, head: int) -> tuple[list[int], list[int]]:
        """Load the LOG LUT for *head* (1-indexed).

        Returns (V_mV_list, log10P_Q16_list).  Only valid on InGaAs LOG
        devices; Si LOG uses an analytical model and does not call this.
        """

    @abstractmethod
    def read_frames(
        self,
        frames: int,
        mask: int,
        *,
        n_channels: int | None = None,
        unsigned: bool = False,
    ) -> list[np.ndarray]:
        """Transfer *frames* captured ADC samples from device memory.

        *mask* is the channel mask (bit 0 = channel 0). *n_channels* is the
        device channel count (4 on mk1, 5 on mk2). *unsigned* selects the ADC
        wire format: ``False`` = mk1 two's-complement ``int16``; ``True`` =
        mk2 straight-binary ``uint16`` (0-5 V unipolar).

        Returns a list of *n_channels* numpy arrays of length *frames* in
        channel order; channels not set in *mask* return an empty array. The
        array dtype is ``int16`` when signed and ``uint16`` when unsigned.
        """

    @abstractmethod
    def drain(self) -> None:
        """Discard any buffered input (called during init and on errors)."""

    @abstractmethod
    def close(self) -> None:
        """Release the underlying I/O resource."""

    # ------------------------------------------------------------------
    # Optional extensions (default no-ops; overridden by SerialTransport)
    # ------------------------------------------------------------------

    def set_inter_command_gap_s(self, gap_s: float) -> None:
        """Set minimum gap between consecutive commands (serial timing aid)."""

    def get_inter_command_gap_s(self) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# SerialTransport
# ---------------------------------------------------------------------------

class SerialTransport(Transport):
    """pyserial-backed transport for real coreDAQ hardware (CDC USB-serial)."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.15,
        inter_command_gap_s: float = 0.0,
    ) -> None:
        self._ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            write_timeout=0.5,
        )
        self._lock = threading.Lock()
        self._inter_command_gap_s = max(0.0, float(inter_command_gap_s))
        self._last_cmd_ts = 0.0
        self.drain()

    def port_name(self) -> str:
        """Serial port path this transport is bound to (for reconnect specs)."""
        return str(self._ser.port or "")

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _writeln(self, s: str) -> None:
        if not s.endswith("\n"):
            s += "\n"
        self._ser.write(s.encode("ascii", errors="ignore"))

    def _readline(self) -> str:
        raw = self._ser.readline()
        if not raw:
            raise CoreDAQError("Device timeout")
        return raw.decode("ascii", "ignore").strip()

    def _raw_ask(self, cmd: str) -> tuple[str, str]:
        """Send command and read one response line.  Caller must hold lock."""
        if self._inter_command_gap_s > 0.0 and self._last_cmd_ts > 0.0:
            elapsed = time.perf_counter() - self._last_cmd_ts
            if elapsed < self._inter_command_gap_s:
                time.sleep(self._inter_command_gap_s - elapsed)
        self._writeln(cmd)
        self._last_cmd_ts = time.perf_counter()
        line = self._readline()
        if line.startswith("OK"):
            return "OK", line[2:].strip()
        if line.startswith("ERR"):
            return "ERR", line[3:].strip()
        if line.startswith("BUSY"):
            return "BUSY", ""
        return "ERR", line

    # ------------------------------------------------------------------
    # Transport ABC
    # ------------------------------------------------------------------

    def ask(self, cmd: str) -> tuple[str, str]:
        with self._lock:
            return self._raw_ask(cmd)

    def ask_with_busy_retry(
        self,
        cmd: str,
        retries: int = 20,
        delay_s: float = 0.05,
    ) -> tuple[str, str]:
        last_st, last_p = "BUSY", ""
        for _ in range(max(1, int(retries))):
            st, p = self.ask(cmd)
            if st != "BUSY":
                return st, p
            last_st, last_p = st, p
            time.sleep(max(0.0, float(delay_s)))
        return last_st, last_p

    def drain(self) -> None:
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._ser.is_open:
                self._ser.flush()
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()
                self._ser.close()
        except Exception:
            pass

    def set_inter_command_gap_s(self, gap_s: float) -> None:
        g = float(gap_s)
        if not math.isfinite(g) or g < 0.0:
            raise ValueError("inter-command gap must be >= 0")
        self._inter_command_gap_s = g

    def get_inter_command_gap_s(self) -> float:
        return float(self._inter_command_gap_s)

    # ------------------------------------------------------------------
    # LOGCAL binary protocol (InGaAs LOG only)
    # ------------------------------------------------------------------

    def logcal(self, head: int) -> tuple[list[int], list[int]]:
        """Execute LOGCAL {head} and return (V_mV_list, log10P_Q16_list)."""
        with self._lock:
            self._ser.reset_input_buffer()
            self._writeln(f"LOGCAL {head}")

            # Read header line
            header: Optional[str] = None
            for _ in range(120):
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", "ignore").strip()
                if line.startswith("OK") and " N=" in line and " RB=" in line and " H" in line:
                    header = line
                    break

            if not header:
                raise coreDAQCalibrationError(
                    f"LOGCAL header not received for head {head}"
                )

            parts = header.split()
            try:
                n_pts = int(
                    next(t for t in parts if t.startswith("N=")).split("=", 1)[1]
                )
                rb = int(
                    next(t for t in parts if t.startswith("RB=")).split("=", 1)[1]
                )
            except Exception:
                raise coreDAQCalibrationError(
                    f"Malformed LOGCAL header: {header!r}"
                )

            if rb != 6:
                raise coreDAQCalibrationError(
                    f"Unexpected LOGCAL RB={rb} (expected 6)"
                )

            payload_len = n_pts * rb
            payload = self._ser.read(payload_len)
            if len(payload) != payload_len:
                raise coreDAQCalibrationError(
                    f"Short LOGCAL payload for head {head}: "
                    f"got {len(payload)}/{payload_len} bytes"
                )

            done_ok = False
            for _ in range(120):
                raw = self._ser.readline()
                if not raw:
                    continue
                if raw.decode("ascii", "ignore").strip() == "OK DONE":
                    done_ok = True
                    break
            if not done_ok:
                raise coreDAQCalibrationError(
                    f"LOGCAL missing OK DONE for head {head}"
                )

        v_mv: list[int] = []
        log10p_q16: list[int] = []
        for i in range(n_pts):
            v, q = struct.unpack_from("<Hi", payload, i * rb)
            v_mv.append(int(v))
            log10p_q16.append(int(q))

        if not v_mv:
            raise coreDAQCalibrationError(f"LOG LUT empty for head {head}")

        return v_mv, log10p_q16

    # ------------------------------------------------------------------
    # XFER binary protocol (capture data transfer)
    # ------------------------------------------------------------------

    # coreDAQ.connect sets this from the IDN firmware version:
    #   self._transport.supports_xferc = self._fw_at_least(4, 4)
    # Default False -> any un-upgraded caller uses the legacy whole-XFER path.
    supports_xferc: bool = False

    def read_frames(
        self,
        frames: int,
        mask: int,
        *,
        n_channels: int | None = None,
        unsigned: bool = False,
    ) -> list[np.ndarray]:
        """Transfer *frames* captured ADC samples from device SDRAM.

        Firmware v4.4+ (``supports_xferc``): integrity-checked adaptive ladder
        (``XFERC`` + CRC32 trailer, ``XFERABORT`` on failure). Older firmware
        (v4.2/v4.3) or ``UNKNOWN_CMD``: legacy whole-capture ``XFER``. Legacy
        ``XFER`` on the wire is unchanged, so old drivers interoperate with new
        firmware and vice-versa.
        """
        if n_channels is None:
            n_channels = max(4, mask.bit_length())   # mk1 masks (<=0x0F) -> 4
        if frames <= 0:
            raise ValueError("frames must be > 0 (nothing captured to transfer)")

        active_idx = [i for i in range(n_channels) if (mask >> i) & 1]
        active_ch = len(active_idx)
        if active_ch == 0:
            raise CoreDAQError("No active channels in mask")

        frame_bytes = active_ch * 2
        total = frames * frame_bytes

        with self._lock:
            buf = self._pull_capture(total, frame_bytes)

        # mk1 = ±5 V two's-complement int16; mk2 = 0-5 V straight-binary uint16.
        dtype = "<u2" if unsigned else "<i2"   # explicit LE — no byteswap needed
        empty_dtype = np.uint16 if unsigned else np.int16
        raw = np.frombuffer(buf, dtype=dtype)
        out: list[np.ndarray] = [np.empty(0, dtype=empty_dtype)] * n_channels
        for pos, ch_idx in enumerate(active_idx):
            ch_data = np.ascontiguousarray(raw[pos::active_ch])
            if len(ch_data) != frames:
                raise CoreDAQError(
                    f"Parse mismatch on CH{ch_idx + 1}: "
                    f"expected {frames}, got {len(ch_data)}"
                )
            out[ch_idx] = ch_data

        return out

    # ------------------------------------------------------------------
    # Bulk transfer internals (caller holds self._lock)
    # ------------------------------------------------------------------

    def _pull_capture(self, total: int, frame_bytes: int) -> bytes:
        """Return *total* verified payload bytes. Caller holds the lock."""
        if not getattr(self, "supports_xferc", False):
            return self._legacy_whole(total)

        total_frames = total // frame_bytes
        buf = bytearray(total)
        last: Optional[str] = None
        for level in _XFERC_LEVELS:
            nchunks = min(level, total_frames)
            ranges = _split_frame_aligned(total, nchunks, frame_bytes)
            try:
                for off, ln in ranges:
                    buf[off:off + ln] = self._xferc_chunk(off, ln)
                return bytes(buf)
            except _LegacyDetected:
                self.supports_xferc = False        # never try XFERC again this session
                return self._legacy_whole(total)
            except _ChunkFail as e:
                last = str(e)
                self._xferabort_resync()
                continue
        raise coreDAQUSBError(
            f"Bulk transfer failed after splits {_XFERC_LEVELS} (last: {last}). "
            "Call coredaq.reset() and recapture."
        )

    def _xferc_chunk(self, off: int, ln: int) -> bytes:
        """Pull one CRC-verified sub-range [off, off+ln) via XFERC. Lock held."""
        overall, idle = _xfer_timeouts(ln)
        self._ser.reset_input_buffer()
        self._writeln(f"XFERC {off} {ln}")
        self._ser.flush()
        line = self._readline()
        if not line.startswith("OK"):
            if "UNKNOWN_CMD" in line.upper():
                raise _LegacyDetected()
            raise _ChunkFail(f"no OK START off={off} ln={ln}: {line!r}")

        want = ln + _XFERC_TRAILER
        buf = bytearray(want)
        mv = memoryview(buf)
        got = 0
        t_dead = time.time() + overall
        t_last = time.time()
        while got < want:
            r = self._ser.read(min(1 << 20, want - got))
            if not r:
                now = time.time()
                if (now - t_last) > idle or now > t_dead:
                    raise _ChunkFail(f"stall {got:,}/{want:,} off={off}")
                time.sleep(0.005)
                continue
            mv[got:got + len(r)] = r
            got += len(r)
            t_last = time.time()

        payload = bytes(buf[:ln])
        tr = bytes(buf[ln:])
        if tr[0:4] != b"CRC2":
            raise _ChunkFail(f"bad trailer magic {tr[0:4]!r} off={off}")
        length = struct.unpack("<I", tr[4:8])[0]
        crc = struct.unpack("<I", tr[8:12])[0]
        if length != ln:
            raise _ChunkFail(f"trailer len {length} != {ln}")
        if crc != (zlib.crc32(payload) & 0xFFFFFFFF):
            raise _ChunkFail(f"CRC mismatch off={off}")
        return payload

    def _xferabort_resync(self) -> bool:
        """Stop a failed/in-flight transfer WITHOUT losing the capture, then resync.
        Lock held. Returns True if the device reports DATA_READY afterwards."""
        try:
            self._writeln("XFERABORT")
            self._ser.flush()
        except Exception:
            pass
        # drain stale payload + the "OK ABORT" line until the link goes quiet
        t = time.time()
        while True:
            try:
                r = self._ser.read(65536)
            except Exception:
                break
            if r:
                t = time.time()
            elif time.time() - t > 0.15:
                break
        try:
            self._ser.reset_input_buffer()
            self._writeln("STATE?")
            self._ser.flush()
            return self._readline().endswith("4")   # DATA_READY
        except Exception:
            return False

    def _legacy_whole(self, total: int, retries: int = 2) -> bytes:
        """Original-firmware path: single whole-capture XFER, stock timeouts. Lock held."""
        overall, idle = _xfer_timeouts(total)
        last: Optional[str] = None
        for _ in range(max(1, retries)):
            self._ser.reset_input_buffer()
            self._writeln(f"XFER {total}")
            self._ser.flush()
            line = self._readline()
            if not line.startswith("OK"):
                payload = line[4:].strip() if line.upper().startswith("ERR") else line
                raise error_for_payload("XFER", payload)
            buf = bytearray(total)
            mv = memoryview(buf)
            got = 0
            t_dead = time.time() + overall
            t_last = time.time()
            stalled = False
            while got < total:
                r = self._ser.read(min(1 << 20, total - got))
                if not r:
                    now = time.time()
                    if (now - t_last) > idle or now > t_dead:
                        stalled = True
                        break
                    time.sleep(0.005)
                    continue
                mv[got:got + len(r)] = r
                got += len(r)
                t_last = time.time()
            if not stalled:
                return bytes(buf)
            last = f"stalled {got:,}/{total:,}"
            self.drain()
        raise coreDAQTimeoutError(
            f"USB transfer failed: {last}. Call coredaq.reset() before retrying."
        )

    # ------------------------------------------------------------------
    # Device discovery (class method for coreDAQ.discover())
    # ------------------------------------------------------------------

    @staticmethod
    def find_ports(
        baudrate: int = 115200,
        fast_timeout: float = 0.4,
        slow_timeout: float = 2.0,
    ) -> list[str]:
        """Return serial port paths of all responding coreDAQ devices.

        Windows-robust discovery (validated 5/15 -> 15/15 under CPU stress):

          * Descriptor-matched ports (VID/PID 0483:5740, or coreDAQ string hints)
            are probed FIRST with retries + a generous timeout, and are the ports
            re-tried on a miss — a slow first answer under load no longer causes a
            total miss (the old logic only re-probed the *other* ports).
          * The IDN probe drains stale lines and retries within one open handle.
          * The last-resort brute force skips Bluetooth SPP ports (they block for
            seconds and are never a coreDAQ), so a miss doesn't cost ~8 s.

        (``fast_timeout``/``slow_timeout`` are accepted for API compatibility.)
        """
        import threading as _threading

        # coreDAQ USB device descriptors (from firmware usbd_desc.c). On Windows
        # the in-box usbser.sys reports mfg "Microsoft" / desc "USB Serial Device",
        # so discovery rides on the exact VID/PID match.
        _COREDAQ_VID = 0x0483   # STM32 VID reused by coreDAQ
        _COREDAQ_PID = 0x5740   # coreDAQ PID (STM32 Virtual ComPort)
        _MAN_HINTS   = ("core_instrumentation", "coreinstrumentation",
                         "core instrumentation")
        _PROD_HINTS  = ("coredaq",)
        _SKIP_DESC   = ("bluetooth",)   # SPP: open blocks for seconds, never a coreDAQ

        def _descriptor_match(p: object) -> bool:
            vid = getattr(p, "vid", None)
            pid = getattr(p, "pid", None)
            if vid == _COREDAQ_VID and pid == _COREDAQ_PID:
                return True
            man  = (getattr(p, "manufacturer", "") or "").lower()
            prod = (getattr(p, "product",      "") or "").lower()
            desc = (getattr(p, "description",  "") or "").lower()
            return (
                any(h in man  for h in _MAN_HINTS)
                or any(h in prod for h in _PROD_HINTS)
                or any(h in desc for h in _PROD_HINTS)
            )

        def _probe(port: str, out: list, per_read: float, attempts: int) -> None:
            # Open once; try IDN a few times, draining stale input — robust to a
            # device that answers slowly right after the port opens (Windows).
            try:
                with serial.Serial(port, baudrate=baudrate,
                                   timeout=per_read, write_timeout=per_read) as ser:
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    for _ in range(attempts):
                        try:
                            ser.write(b"IDN?\n")
                            ser.flush()
                        except Exception:
                            return
                        for _ in range(2):   # skip a stale/partial line
                            line = ser.readline().decode("ascii", "ignore").strip()
                            if line.startswith("OK") and "coredaq" in line.lower():
                                out.append(port)
                                return
            except Exception:
                pass

        def _probe_list(port_list: list, per_read: float, attempts: int) -> list[str]:
            found: list[str] = []
            for port in port_list:
                result: list[str] = []
                t = _threading.Thread(
                    target=_probe, args=(port, result, per_read, attempts), daemon=True
                )
                t.start()
                t.join(timeout=per_read * attempts + 0.5)
                found.extend(result)
            return found

        all_ports = list(serial.tools.list_ports.comports())
        matched = [p.device for p in all_ports if _descriptor_match(p)]
        others  = [
            p.device for p in all_ports
            if not _descriptor_match(p)
            and not any(h in ((getattr(p, "description", "") or "").lower())
                        for h in _SKIP_DESC)
        ]

        # Pass 1: descriptor-matched (the device 99% of the time), retried.
        if matched:
            found = _probe_list(matched, per_read=0.6, attempts=3)
            if found:
                return found
            found = _probe_list(matched, per_read=1.0, attempts=3)  # loaded system
            if found:
                return found

        # Pass 2: last resort — brute-force remaining non-Bluetooth ports.
        return _probe_list(others, per_read=0.7, attempts=2)
