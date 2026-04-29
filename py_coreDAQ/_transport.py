"""Transport layer for coreDAQ.

Defines the Transport ABC that all I/O backends must implement, and the
SerialTransport that wraps pyserial for real hardware.  SimTransport lives
in _simulator.py.
"""

from __future__ import annotations

import math
import re
import struct
import sys
import threading
import time
from abc import ABC, abstractmethod
from array import array
from typing import Optional

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
    def read_frames(self, frames: int, mask: int) -> list[list[int]]:
        """Transfer *frames* captured ADC samples from device memory.

        *mask* is the 4-bit channel mask (bit 0 = channel 0).
        Returns a list of four sub-lists of length *frames* in channel order;
        channels not set in *mask* return a list of zeros.
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

    def read_frames(self, frames: int, mask: int) -> list[list[int]]:
        """Transfer *frames* captured ADC samples from device SDRAM."""
        active_idx = [i for i in range(4) if (mask >> i) & 1]
        active_ch = len(active_idx)
        if active_ch == 0:
            raise CoreDAQError("No active channels in mask")

        frame_bytes = active_ch * 2
        bytes_needed = frames * frame_bytes
        overall_timeout_s = max(8.0, bytes_needed / 1_000_000.0 * 12.0)
        idle_timeout_s = 6.0

        with self._lock:
            self._ser.reset_input_buffer()
            self._writeln(f"XFER {bytes_needed}")
            self._ser.flush()

            line = self._readline()
            if not line.startswith("OK"):
                raise CoreDAQError(f"XFER refused: {line}")

            buf = bytearray(bytes_needed)
            mv = memoryview(buf)
            got = 0
            chunk = 262144
            t_deadline = time.time() + overall_timeout_s
            t_last_rx = time.time()

            while got < bytes_needed:
                r = self._ser.read(min(chunk, bytes_needed - got))
                if not r:
                    now = time.time()
                    if (now - t_last_rx) > idle_timeout_s:
                        raise coreDAQTimeoutError(
                            f"USB read idle timeout at {got}/{bytes_needed} bytes"
                        )
                    if now > t_deadline:
                        raise coreDAQTimeoutError(
                            f"USB read overall timeout at {got}/{bytes_needed} bytes"
                        )
                    time.sleep(0.01)
                    continue
                mv[got: got + len(r)] = r
                got += len(r)
                t_last_rx = time.time()

        samples: array[int] = array("h")
        samples.frombytes(buf)
        if sys.byteorder != "little":
            samples.byteswap()

        out = [[0] * frames for _ in range(4)]
        for pos, ch_idx in enumerate(active_idx):
            vals = list(samples[pos::active_ch])
            if len(vals) != frames:
                raise CoreDAQError(
                    f"Parse mismatch on CH{ch_idx + 1}: "
                    f"expected {frames}, got {len(vals)}"
                )
            out[ch_idx] = vals

        return out

    # ------------------------------------------------------------------
    # Device discovery (class method for coreDAQ.discover())
    # ------------------------------------------------------------------

    @staticmethod
    def find_ports(baudrate: int = 115200, timeout: float = 0.15) -> list[str]:
        """Return serial port paths of all responding coreDAQ devices."""
        MANUFACTURER_HINTS = ("coreinstrumentation", "core instrumentation")
        PRODUCT_HINTS = ("coredaq",)

        def _descriptor_match(p: object) -> bool:
            man = (getattr(p, "manufacturer", "") or "").lower()
            prod = (getattr(p, "product", "") or "").lower()
            desc = (getattr(p, "description", "") or "").lower()
            return (
                any(h in man for h in MANUFACTURER_HINTS)
                or any(h in prod for h in PRODUCT_HINTS)
                or any(h in desc for h in PRODUCT_HINTS)
            )

        def _probe_idn(port: str) -> bool:
            try:
                with serial.Serial(
                    port,
                    baudrate=baudrate,
                    timeout=timeout,
                    write_timeout=timeout,
                ) as ser:
                    try:
                        ser.reset_input_buffer()
                    except Exception:
                        pass
                    ser.write(b"IDN?\n")
                    ser.flush()
                    line = ser.readline().decode("ascii", "ignore").strip()
                    return line.startswith("OK") and "coredaq" in line.lower()
            except Exception:
                return False

        ports = list(serial.tools.list_ports.comports())
        found: list[str] = []

        for p in ports:
            if _descriptor_match(p) and _probe_idn(p.device):
                found.append(p.device)

        if not found:
            for p in ports:
                if p.device not in found and _probe_idn(p.device):
                    found.append(p.device)

        return found
