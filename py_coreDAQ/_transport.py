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
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

import serial
import serial.tools.list_ports

from ._exceptions import CoreDAQError, coreDAQCalibrationError, coreDAQTimeoutError


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

    def read_frames(
        self,
        frames: int,
        mask: int,
        *,
        n_channels: int | None = None,
        unsigned: bool = False,
    ) -> list[np.ndarray]:
        """Transfer *frames* captured ADC samples from device SDRAM."""
        if n_channels is None:
            n_channels = max(4, mask.bit_length())   # mk1 masks (<=0x0F) -> 4
        if frames <= 0:
            raise ValueError("frames must be > 0 (nothing captured to transfer)")

        active_idx = [i for i in range(n_channels) if (mask >> i) & 1]
        active_ch = len(active_idx)
        if active_ch == 0:
            raise CoreDAQError("No active channels in mask")

        frame_bytes = active_ch * 2
        bytes_needed = frames * frame_bytes
        mb = bytes_needed / 1_000_000.0
        # Overall: at least 30 s, then 20 s/MB (very conservative — firmware reads
        # from external SDRAM over FMC and streams over USB CDC with natural gaps).
        overall_timeout_s = max(30.0, mb * 20.0)
        # Idle: at least 30 s, then 5 s/MB — scales so a mid-transfer pause at any
        # SDRAM or USB buffer boundary doesn't trip the timeout on large captures.
        idle_timeout_s = max(30.0, mb * 5.0)

        with self._lock:
            self._ser.reset_input_buffer()
            self._writeln(f"XFER {bytes_needed}")
            self._ser.flush()

            line = self._readline()
            if not line.startswith("OK"):
                payload = line[4:].strip() if line.upper().startswith("ERR") else line
                from ._exceptions import error_for_payload
                raise error_for_payload("XFER", payload)

            buf = bytearray(bytes_needed)
            mv = memoryview(buf)
            got = 0
            chunk = 1 * 1024 * 1024   # 1 MB — reduces syscall overhead on large captures
            t_deadline = time.time() + overall_timeout_s
            t_last_rx = time.time()

            while got < bytes_needed:
                r = self._ser.read(min(chunk, bytes_needed - got))
                if not r:
                    now = time.time()
                    if (now - t_last_rx) > idle_timeout_s:
                        raise coreDAQTimeoutError(
                            f"USB transfer stalled at {got:,}/{bytes_needed:,} bytes "
                            f"(idle >{idle_timeout_s:.0f} s). "
                            "Call coredaq.reset() before retrying."
                        )
                    if now > t_deadline:
                        raise coreDAQTimeoutError(
                            f"USB transfer overall timeout at {got:,}/{bytes_needed:,} bytes. "
                            "Call coredaq.reset() before retrying."
                        )
                    time.sleep(0.005)
                    continue
                mv[got: got + len(r)] = r
                got += len(r)
                t_last_rx = time.time()

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
    # Device discovery (class method for coreDAQ.discover())
    # ------------------------------------------------------------------

    @staticmethod
    def find_ports(
        baudrate: int = 115200,
        fast_timeout: float = 0.4,
        slow_timeout: float = 2.0,
    ) -> list[str]:
        """Return serial port paths of all responding coreDAQ devices.

        Two-pass strategy for fast discovery without stalling on blocking ports:

        Pass 1 — USB descriptor match (free, no serial open):
            Ports whose manufacturer/product/description strings contain
            coreDAQ hints are probed first with a short 0.4 s timeout.
            On most systems the device is found here in < 0.5 s total.

        Pass 2 — brute-force IDN? scan (only if pass 1 found nothing):
            Every remaining port is probed sequentially with a hard 2 s
            per-port timeout so blocking ports (Bluetooth, virtual, etc.)
            never stall the scan.  Works on macOS, Windows, and Linux.
        """
        import threading as _threading

        # coreDAQ USB device descriptors (from firmware usbd_desc.c)
        _COREDAQ_VID = 0x0483   # STM32 VID reused by coreDAQ
        _COREDAQ_PID = 0x5740   # coreDAQ PID (STM32 Virtual ComPort)
        _MAN_HINTS   = ("core_instrumentation", "coreinstrumentation",
                         "core instrumentation")
        _PROD_HINTS  = ("coredaq",)

        def _descriptor_match(p: object) -> bool:
            # Exact VID/PID match is the fastest and most reliable check
            vid = getattr(p, "vid", None)
            pid = getattr(p, "pid", None)
            if vid == _COREDAQ_VID and pid == _COREDAQ_PID:
                return True
            # Fallback: string hints in manufacturer / product / description
            man  = (getattr(p, "manufacturer", "") or "").lower()
            prod = (getattr(p, "product",      "") or "").lower()
            desc = (getattr(p, "description",  "") or "").lower()
            return (
                any(h in man  for h in _MAN_HINTS)
                or any(h in prod for h in _PROD_HINTS)
                or any(h in desc for h in _PROD_HINTS)
            )

        def _probe(port: str, out: list, t_out: float) -> None:
            try:
                with serial.Serial(port, baudrate=baudrate,
                                   timeout=t_out, write_timeout=t_out) as ser:
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    ser.write(b"IDN?\n")
                    ser.flush()
                    line = ser.readline().decode("ascii", "ignore").strip()
                    if line.startswith("OK") and "coredaq" in line.lower():
                        out.append(port)
            except Exception:
                pass

        def _probe_list(port_list: list, t_out: float) -> list[str]:
            found: list[str] = []
            for port in port_list:
                result: list[str] = []
                t = _threading.Thread(
                    target=_probe, args=(port, result, t_out), daemon=True
                )
                t.start()
                t.join(timeout=t_out + 0.1)
                found.extend(result)
            return found

        all_ports  = list(serial.tools.list_ports.comports())
        fast_ports = [p.device for p in all_ports if _descriptor_match(p)]
        slow_ports = [p.device for p in all_ports if not _descriptor_match(p)]

        # Pass 1: descriptor-matched ports with fast timeout
        found = _probe_list(fast_ports, fast_timeout)
        if found:
            return found   # early exit — almost always the case

        # Pass 2: brute-force remaining ports (unknown adapters, generic USB-serial)
        return _probe_list(slow_ports, slow_timeout)
