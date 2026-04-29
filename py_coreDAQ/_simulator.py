"""Simulator transport for coreDAQ — no hardware required.

SimTransport implements the Transport ABC and supports all four device variants:
  - InGaAs LOG  (default)
  - InGaAs LINEAR
  - Si LOG
  - Si LINEAR

The simulator generates physically-consistent ADC codes from a configurable
incident power level.  It is the backend used by coreDAQ.connect(simulator=True).
"""
from __future__ import annotations

import bisect
import math
import random
import struct
import threading
from typing import Optional

from ._exceptions import coreDAQCalibrationError, CoreDAQError
from ._transport import Transport

# ---------------------------------------------------------------------------
# Embedded responsivity curves (subset of _BUILTIN_RESPONSIVITY_CURVES)
# ---------------------------------------------------------------------------

_INGAAS_NM: list[float] = [
    910.0, 920.0, 930.0, 940.0, 950.0, 960.0, 970.0, 980.0, 990.0, 1000.0,
    1010.0, 1020.0, 1030.0, 1040.0, 1050.0, 1060.0, 1070.0, 1080.0, 1090.0, 1100.0,
    1110.0, 1120.0, 1130.0, 1140.0, 1150.0, 1160.0, 1170.0, 1180.0, 1190.0, 1200.0,
    1210.0, 1220.0, 1230.0, 1240.0, 1250.0, 1260.0, 1270.0, 1280.0, 1290.0, 1300.0,
    1310.0, 1320.0, 1330.0, 1340.0, 1350.0, 1360.0, 1370.0, 1380.0, 1390.0, 1400.0,
    1410.0, 1420.0, 1430.0, 1440.0, 1450.0, 1460.0, 1470.0, 1480.0, 1490.0, 1500.0,
    1510.0, 1520.0, 1530.0, 1540.0, 1550.0, 1560.0, 1570.0, 1580.0, 1590.0, 1600.0,
    1610.0, 1620.0, 1630.0, 1640.0, 1650.0, 1660.0, 1670.0, 1680.0, 1690.0, 1700.0,
]
_INGAAS_AW: list[float] = [
    0.37, 0.423, 0.508, 0.569, 0.602, 0.623, 0.657, 0.694, 0.712, 0.724,
    0.732, 0.738, 0.741, 0.742, 0.749, 0.755, 0.765, 0.775, 0.795, 0.814,
    0.845, 0.856, 0.863, 0.868, 0.856, 0.855, 0.848, 0.85, 0.857, 0.866,
    0.872, 0.879, 0.888, 0.899, 0.91, 0.921, 0.929, 0.938, 0.944, 0.95,
    0.954, 0.954, 0.954, 0.953, 0.95, 0.947, 0.946, 0.946, 0.945, 0.946,
    0.933, 0.937, 0.944, 0.949, 0.958, 0.966, 0.972, 0.978, 0.985, 0.992,
    0.997, 0.997, 0.997, 0.994, 0.99, 0.984, 0.98, 0.978, 0.971, 0.961,
    0.943, 0.921, 0.898, 0.877, 0.852, 0.806, 0.652, 0.41, 0.239, 0.145,
]

_SILICON_NM: list[float] = [
    400.0, 410.0, 420.0, 430.0, 440.0, 450.0, 460.0, 470.0, 480.0, 490.0,
    500.0, 510.0, 520.0, 530.0, 540.0, 550.0, 560.0, 570.0, 580.0, 590.0,
    600.0, 610.0, 620.0, 630.0, 640.0, 650.0, 660.0, 670.0, 680.0, 690.0,
    700.0, 710.0, 720.0, 730.0, 740.0, 750.0, 760.0, 770.0, 780.0, 790.0,
    800.0, 810.0, 820.0, 830.0, 840.0, 850.0, 860.0, 870.0, 880.0, 890.0,
    900.0, 910.0, 920.0, 930.0, 940.0, 950.0, 960.0, 970.0, 980.0, 990.0,
    1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1050.0, 1060.0, 1070.0, 1080.0, 1090.0, 1100.0,
]
_SILICON_AW: list[float] = [
    0.0918, 0.103, 0.1226, 0.1418, 0.1539, 0.1658, 0.1760, 0.1693, 0.1879, 0.1891,
    0.2079, 0.2089, 0.2093, 0.2175, 0.2277, 0.2432, 0.2559, 0.2723, 0.2885, 0.3060,
    0.3240, 0.3421, 0.3612, 0.3792, 0.3969, 0.4097, 0.4313, 0.4417, 0.4538, 0.4635,
    0.4746, 0.4871, 0.4964, 0.4932, 0.5242, 0.5251, 0.5275, 0.5288, 0.5326, 0.5508,
    0.5600, 0.5636, 0.5642, 0.5653, 0.5676, 0.5685, 0.5699, 0.5703, 0.5804, 0.5853,
    0.5906, 0.5959, 0.5979, 0.5985, 0.6021, 0.5988, 0.5969, 0.5874, 0.5742, 0.5590,
    0.5491, 0.5256, 0.4838, 0.4346, 0.3743, 0.3049, 0.2410, 0.1870, 0.1560, 0.1210, 0.0662,
]

# ADC constants
_ADC_VFS_VOLTS = 5.0
_ADC_LSB_VOLTS = (2.0 * _ADC_VFS_VOLTS) / 65536
_ADC_VFS_MV = 5000.0
_ADC_LSB_MV = _ADC_LSB_VOLTS * 1e3

# Log-amp model parameters (ADL5303 / equivalent)
_LOG_VY = 0.5       # V/decade
_LOG_IZ = 100e-12   # A

# Per-gain full-scale power (standard profile) used for LINEAR slope calculation
_GAIN_MAX_POWER_W = [5e-3, 1e-3, 500e-6, 100e-6, 50e-6, 10e-6, 5e-6, 500e-9]

# Wavelength limits by detector
_WL_LIMITS = {
    "INGAAS": (910.0, 1700.0),
    "SILICON": (400.0, 1100.0),
}

# Reference wavelength for InGaAs cal slope computation
_INGAAS_REF_NM = 1550.0


class SimTransport(Transport):
    """Simulated transport for all four coreDAQ device variants.

    Parameters
    ----------
    frontend : str
        ``"LOG"`` or ``"LINEAR"``.
    detector : str
        ``"INGAAS"`` or ``"SILICON"``.
    incident_power_w : float
        Optical power seen by all four channels.
    wavelength_nm : float
        Operating wavelength (nm). Must be within the detector's valid range.
    noise_sigma_adc : float
        Gaussian noise standard deviation in ADC counts.
    seed : int or None
        RNG seed for reproducibility. ``None`` = stochastic.
    """

    def __init__(
        self,
        frontend: str = "LOG",
        detector: str = "INGAAS",
        incident_power_w: float = 1e-4,
        wavelength_nm: float = 1550.0,
        noise_sigma_adc: float = 2.0,
        seed: Optional[int] = 42,
    ) -> None:
        self._frontend = frontend.strip().upper()
        self._detector = detector.strip().upper()

        if self._frontend not in ("LOG", "LINEAR"):
            raise ValueError(f"frontend must be 'LOG' or 'LINEAR', got {frontend!r}")
        if self._detector not in ("INGAAS", "SILICON"):
            raise ValueError(f"detector must be 'INGAAS' or 'SILICON', got {detector!r}")

        wl_min, wl_max = _WL_LIMITS[self._detector]
        if not (wl_min <= wavelength_nm <= wl_max):
            raise ValueError(
                f"wavelength_nm={wavelength_nm} outside {self._detector} range "
                f"[{wl_min}, {wl_max}] nm"
            )

        self._incident_power_w = float(incident_power_w)
        self._wavelength_nm = float(wavelength_nm)
        self._noise_sigma = float(noise_sigma_adc)
        self._rng = random.Random(seed)

        # Device register state
        self._gains = [2, 2, 2, 2]          # mid-range (100 µW)
        self._mask = 0x0F
        self._freq_hz = 1000                 # will be set to 500 by coreDAQ.__init__
        self._os_idx = 0                     # will be set to 1 by coreDAQ.__init__
        self._factory_zeros = [0, 0, 0, 0]

        # Acquisition state
        self._acq_frames = 0
        self._acq_armed = False
        self._acq_trigger = False
        self._acq_trigger_rising = True
        self._acq_started = False
        self._acq_complete = False

        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Responsivity interpolation
    # ------------------------------------------------------------------

    @staticmethod
    def _interp(xs: list[float], ys: list[float], x: float) -> float:
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        j = bisect.bisect_left(xs, x)
        x0, x1 = xs[j - 1], xs[j]
        y0, y1 = ys[j - 1], ys[j]
        if x1 == x0:
            return float(y0)
        return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0))

    def _resp(self, detector: str, wl: float) -> float:
        if detector == "INGAAS":
            return self._interp(_INGAAS_NM, _INGAAS_AW, wl)
        return self._interp(_SILICON_NM, _SILICON_AW, wl)

    # ------------------------------------------------------------------
    # Power → ADC code
    # ------------------------------------------------------------------

    def _power_to_adc(self, p_w: float, ch: int) -> int:
        resp = self._resp(self._detector, self._wavelength_nm)

        if self._frontend == "LOG":
            p_safe = max(p_w, _LOG_IZ / resp * 1e-6)  # avoid log(0)
            v_out = _LOG_VY * math.log10(p_safe * resp / _LOG_IZ)
            code_f = v_out / _ADC_LSB_VOLTS
        else:
            # LINEAR: slope = VFS_MV / max_power[gain]  (mV/W)
            gain = self._gains[ch]
            max_p = _GAIN_MAX_POWER_W[gain]
            slope_mv_w = _ADC_VFS_MV / max_p
            code_f = (p_w * slope_mv_w / _ADC_LSB_MV) + self._factory_zeros[ch]

        noise = self._rng.gauss(0.0, self._noise_sigma) if self._noise_sigma > 0 else 0.0
        return int(max(-32768, min(32767, round(code_f + noise))))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_idn(self) -> str:
        det = "InGaAs" if self._detector == "INGAAS" else "Silicon"
        return f"coreDAQ {det} {self._frontend} v3.2 SN0000"

    @staticmethod
    def _float_to_hex(f: float) -> str:
        bits = int.from_bytes(struct.pack("<f", float(f)), "little")
        return f"{bits:08X}"

    # ------------------------------------------------------------------
    # Transport ABC — main dispatch
    # ------------------------------------------------------------------

    def ask(self, cmd: str) -> tuple[str, str]:
        with self._lock:
            return self._dispatch(cmd.strip())

    def ask_with_busy_retry(
        self,
        cmd: str,
        retries: int = 20,
        delay_s: float = 0.05,
    ) -> tuple[str, str]:
        # Simulator never returns BUSY
        return self.ask(cmd)

    def _dispatch(self, cmd: str) -> tuple[str, str]:  # noqa: C901
        # --- Identity ---
        if cmd == "HEAD_TYPE?":
            return "OK", f"TYPE={self._frontend}"

        if cmd == "IDN?":
            return "OK", self._build_idn()

        if cmd == "I2C REFRESH":
            return "OK", ""

        # --- Settings queries ---
        if cmd == "OS?":
            return "OK", str(self._os_idx)

        if cmd == "FREQ?":
            return "OK", str(self._freq_hz)

        # --- Settings writes ---
        if cmd.startswith("OS "):
            try:
                self._os_idx = int(cmd[3:])
                return "OK", ""
            except ValueError:
                return "ERR", "bad OS value"

        if cmd.startswith("FREQ "):
            try:
                self._freq_hz = int(cmd[5:])
                return "OK", ""
            except ValueError:
                return "ERR", "bad FREQ value"

        # --- Channel mask ---
        if cmd == "CHMASK?":
            active = bin(self._mask).count("1")
            fb = active * 2
            return "OK", f"0x{self._mask:X} CH={active} FB={fb}"

        if cmd.startswith("CHMASK "):
            try:
                val = int(cmd[7:], 0) & 0x0F
                if val == 0:
                    return "ERR", "mask cannot be 0"
                self._mask = val
                return "OK", ""
            except ValueError:
                return "ERR", "bad CHMASK value"

        # --- LINEAR-only commands ---
        if cmd == "FACTORY_ZEROS?":
            if self._frontend != "LINEAR":
                return "ERR", "not a LINEAR device"
            return "OK", " ".join(str(z) for z in self._factory_zeros)

        if cmd.startswith("CAL "):
            if self._frontend != "LINEAR":
                return "ERR", "not a LINEAR device"
            parts = cmd.split()
            if len(parts) < 3:
                return "ERR", "bad CAL command"
            try:
                head, gain = int(parts[1]), int(parts[2])
            except ValueError:
                return "ERR", "bad CAL args"
            if not (1 <= head <= 4) or not (0 <= gain <= 7):
                return "ERR", "out of range"
            slope = _ADC_VFS_MV / _GAIN_MAX_POWER_W[gain]   # mV/W
            intercept = 0.0
            sh = self._float_to_hex(slope)
            ih = self._float_to_hex(intercept)
            return "OK", f"H{head} G{gain} S={sh} I={ih}"

        if cmd == "GAINS?":
            if self._frontend != "LINEAR":
                return "ERR", "not a LINEAR device"
            g = self._gains
            return "OK", f"HEAD1={g[0]} HEAD2={g[1]} HEAD3={g[2]} HEAD4={g[3]}"

        if cmd.startswith("GAIN "):
            if self._frontend != "LINEAR":
                return "ERR", "not a LINEAR device"
            parts = cmd.split()
            if len(parts) < 3:
                return "ERR", "bad GAIN command"
            try:
                head, val = int(parts[1]), int(parts[2])
            except ValueError:
                return "ERR", "bad GAIN args"
            if not (1 <= head <= 4) or not (0 <= val <= 7):
                return "ERR", "out of range"
            self._gains[head - 1] = val
            return "OK", ""

        # --- Snapshot ---
        if cmd.startswith("SNAP ") and not cmd.startswith("SNAP?"):
            return "OK", ""

        if cmd == "SNAP?":
            codes = [self._power_to_adc(self._incident_power_w, ch) for ch in range(4)]
            g = self._gains
            return (
                "OK",
                f"{codes[0]} {codes[1]} {codes[2]} {codes[3]} "
                f"G={g[0]} {g[1]} {g[2]} {g[3]}",
            )

        # --- Acquisition ---
        if cmd.startswith("ACQ ARM "):
            try:
                self._acq_frames = int(cmd[8:])
            except ValueError:
                return "ERR", "bad ACQ ARM frames"
            self._acq_armed = True
            self._acq_trigger = False
            self._acq_started = False
            self._acq_complete = False
            return "OK", ""

        if cmd.startswith("TRIGARM "):
            parts = cmd.split()
            if len(parts) < 3:
                return "ERR", "bad TRIGARM"
            try:
                self._acq_frames = int(parts[1])
            except ValueError:
                return "ERR", "bad TRIGARM frames"
            self._acq_trigger = True
            self._acq_trigger_rising = (parts[2].upper() == "R")
            self._acq_armed = True
            self._acq_started = False
            # Trigger fires immediately in simulator
            self._acq_complete = True
            return "OK", ""

        if cmd == "ACQ START":
            if not self._acq_armed:
                return "ERR", "not armed"
            self._acq_started = True
            self._acq_complete = True
            return "OK", ""

        if cmd == "ACQ STOP":
            self._acq_complete = True
            return "OK", ""

        if cmd == "STREAM?":
            return "OK", "DONE" if self._acq_complete else "RUNNING"

        if cmd == "LEFT?":
            return "OK", "0"

        if cmd == "STATE?":
            return "OK", "4"  # 4 = READY

        # --- Sensors ---
        if cmd == "TEMP?":
            return "OK", "25.0"

        if cmd == "HUM?":
            return "OK", "45.0"

        if cmd == "DIE_TEMP?":
            return "OK", "38.0"

        # --- Misc ---
        if cmd == "ADDR?":
            return "OK", "0"

        if cmd == "SOFTRESET":
            return "OK", ""

        if cmd == "DFU":
            return "OK", ""

        return "ERR", f"unknown command: {cmd!r}"

    # ------------------------------------------------------------------
    # Binary protocols
    # ------------------------------------------------------------------

    def logcal(self, head: int) -> tuple[list[int], list[int]]:
        """Generate a synthetic InGaAs LOG LUT (512 points, 0–4000 mV)."""
        if self._frontend != "LOG" or self._detector != "INGAAS":
            raise coreDAQCalibrationError(
                f"logcal() is only valid for InGaAs LOG; got {self._detector} {self._frontend}"
            )

        n_pts = 512
        resp_ref = self._resp("INGAAS", _INGAAS_REF_NM)
        v_mv_list: list[int] = []
        log10p_q16_list: list[int] = []

        for i in range(n_pts):
            v_mv = int(round(i * 4000.0 / max(1, n_pts - 1)))
            v_v = v_mv / 1000.0
            log10_p = math.log10(_LOG_IZ / resp_ref) + v_v / _LOG_VY
            q16 = int(round(log10_p * 65536))
            v_mv_list.append(v_mv)
            log10p_q16_list.append(q16)

        return v_mv_list, log10p_q16_list

    def read_frames(self, frames: int, mask: int) -> list[list[int]]:
        """Return simulated ADC frames per channel."""
        active_idx = [i for i in range(4) if (mask >> i) & 1]
        if not active_idx:
            raise CoreDAQError("No active channels in mask")

        out: list[list[int]] = [[0] * frames for _ in range(4)]
        for ch in active_idx:
            for f in range(frames):
                out[ch][f] = self._power_to_adc(self._incident_power_w, ch)
        return out

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    def port_name(self) -> str:
        return "simulator"
