"""TCP/Ethernet transport for coreDAQ mk2.

``EthernetTransport`` implements the :class:`~py_coreDAQ._transport.Transport`
ABC over a TCP socket to the device's line server (default port 5025). The
wire protocol is byte-for-byte identical to :class:`SerialTransport`:

* commands are ``\\n``-terminated ASCII lines,
* replies are ``\\r\\n``-terminated ASCII lines prefixed ``OK`` / ``ERR`` /
  ``BUSY``,
* the two binary streams (``XFER`` and ``LOGCAL``) follow their ``OK ...``
  header line as a contiguous byte run of an exactly-known length.

Only the byte source changes. A raw TCP socket may return partial lines or
several lines in one ``recv``, so this transport reassembles lines itself and
buffers any leftover bytes (``self._rxbuf``) across line and binary reads —
the same buffer must feed the binary reader so a stream that arrived in the
same segment as its ``OK`` header line is not lost.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Optional

import numpy as np

from ._exceptions import CoreDAQError, coreDAQCalibrationError, coreDAQTimeoutError
from ._transport import Transport


class EthernetTransport(Transport):
    """Thread-safe TCP client transport for coreDAQ mk2 (port 5025).

    Parameters
    ----------
    host : str
        Device IP address or hostname.
    port : int
        TCP port (the firmware serves the line protocol on 5025).
    timeout : float
        Per-``recv`` socket timeout in seconds. Bulk transfers layer their own
        size-scaled overall/idle deadlines on top of this.
    """

    def __init__(self, host: str, port: int = 5025, timeout: float = 0.5,
                 bind_host: Optional[str] = None) -> None:
        self._host = str(host)
        self._port = int(port)
        self._timeout = max(0.05, float(timeout))
        try:
            # bind_host pins the egress interface on multi-homed hosts —
            # required for link-local (169.254/16) device addresses when
            # several interfaces carry that prefix.
            if bind_host:
                self._sock = socket.create_connection(
                    (self._host, self._port), timeout=self._timeout,
                    source_address=(bind_host, 0),
                )
            else:
                self._sock = socket.create_connection(
                    (self._host, self._port), timeout=self._timeout
                )
        except OSError as exc:
            raise CoreDAQError(
                f"Cannot connect to {self._host}:{self._port}: {exc}"
            ) from exc
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self._sock.settimeout(self._timeout)
        self._rxbuf = bytearray()          # leftover bytes after a line read
        self._lock = threading.Lock()
        self.drain()

    # ------------------------------------------------------------------
    # Low-level line I/O (TCP is a byte stream — reassemble lines ourselves)
    # ------------------------------------------------------------------

    def _writeln(self, s: str) -> None:
        if not s.endswith("\n"):
            s += "\n"
        self._sock.sendall(s.encode("ascii", "ignore"))

    def _readline(self) -> str:
        while b"\n" not in self._rxbuf:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                raise CoreDAQError("Device timeout")
            if not chunk:
                raise CoreDAQError("Device closed connection")
            self._rxbuf.extend(chunk)
        line, _, rest = self._rxbuf.partition(b"\n")
        self._rxbuf = bytearray(rest)
        return line.decode("ascii", "ignore").strip()

    def _read_exact(
        self, n: int, overall_timeout_s: float, idle_timeout_s: float
    ) -> bytes:
        """Read exactly *n* bytes, consuming buffered leftovers first."""
        out = bytearray()
        if self._rxbuf:
            take = min(n, len(self._rxbuf))
            out.extend(self._rxbuf[:take])
            del self._rxbuf[:take]
        deadline = time.time() + overall_timeout_s
        last = time.time()
        while len(out) < n:
            try:
                chunk = self._sock.recv(min(1 << 20, n - len(out)))
            except socket.timeout:
                chunk = b""
            if chunk:
                out.extend(chunk)
                last = time.time()
            else:
                now = time.time()
                if (now - last) > idle_timeout_s or now > deadline:
                    raise coreDAQTimeoutError(
                        f"TCP transfer stalled at {len(out):,}/{n:,} bytes. "
                        "Call coredaq.reset() before retrying."
                    )
        return bytes(out)

    def _raw_ask(self, cmd: str) -> tuple[str, str]:
        """Send command and read one response line.  Caller must hold lock."""
        self._writeln(cmd)
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
        self._rxbuf.clear()
        try:
            self._sock.setblocking(False)
            try:
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
            except (BlockingIOError, OSError):
                pass
        finally:
            try:
                self._sock.setblocking(True)
                self._sock.settimeout(self._timeout)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass

    def port_name(self) -> str:
        return f"{self._host}:{self._port}"

    # ------------------------------------------------------------------
    # LOGCAL binary protocol (InGaAs LOG only)
    # ------------------------------------------------------------------

    def logcal(self, head: int) -> tuple[list[int], list[int]]:
        """Execute LOGCAL {head} and return (V_mV_list, log10P_Q16_list).

        Identical state machine to :meth:`SerialTransport.logcal`, reading over
        the socket: send ``LOGCAL <head>``; read the header line containing
        ``N=``, ``RB=`` and ``H``; read ``n_pts * 6`` bytes; read ``OK DONE``.
        """
        with self._lock:
            self._writeln(f"LOGCAL {head}")

            header: Optional[str] = None
            for _ in range(120):
                line = self._readline()
                if (
                    line.startswith("OK")
                    and " N=" in line
                    and " RB=" in line
                    and " H" in line
                ):
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
                raise coreDAQCalibrationError(f"Malformed LOGCAL header: {header!r}")

            if rb != 6:
                raise coreDAQCalibrationError(
                    f"Unexpected LOGCAL RB={rb} (expected 6)"
                )

            payload_len = n_pts * rb
            to_s = max(10.0, payload_len / 100_000.0)
            payload = self._read_exact(payload_len, to_s, to_s)

            done_ok = False
            for _ in range(120):
                if self._readline() == "OK DONE":
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
        """Transfer *frames* captured ADC samples from device SDRAM over TCP."""
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
        # TCP handles flow control/retransmit, so there are no USB-style
        # mid-stream micro-stalls; lighter deadlines are safe. Keep generous
        # idle handling for the device's SDRAM read pacing.
        overall_timeout_s = max(10.0, mb * 3.0)
        idle_timeout_s = max(10.0, mb * 3.0)

        with self._lock:
            self._writeln(f"XFER {bytes_needed}")
            line = self._readline()
            if not line.startswith("OK"):
                payload = line[4:].strip() if line.upper().startswith("ERR") else line
                from ._exceptions import error_for_payload
                raise error_for_payload("XFER", payload)
            buf = self._read_exact(bytes_needed, overall_timeout_s, idle_timeout_s)

        # mk1 = ±5 V two's-complement int16; mk2 = 0-5 V straight-binary uint16.
        dtype = "<u2" if unsigned else "<i2"
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
