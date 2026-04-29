"""Low-level coreDAQ device driver.

_CoreDAQDriver translates the firmware's ASCII + binary command protocol
into Python method calls.  It owns all calibration state and unit-conversion
math.  The public coreDAQ class in _device.py delegates to this layer.

The driver accepts a Transport instance at construction, so it works
equally with SerialTransport (real hardware) and SimTransport (simulator).
"""
from __future__ import annotations

import bisect
import math
import re
import struct
import time
import warnings
from typing import Dict, List, Optional, Tuple, Union

from ._exceptions import CoreDAQError, coreDAQCalibrationError, coreDAQTimeoutError
from ._transport import Transport

Number = Union[int, float]

# ---------------------------------------------------------------------------
# Built-in responsivity curves
# ---------------------------------------------------------------------------

_BUILTIN_RESPONSIVITY_CURVES = {
    "schema": "coredaq.responsivity.v1",
    "detectors": {
        "INGAAS": {
            "points": [
                [910.0, 0.37], [920.0, 0.423], [930.0, 0.508], [940.0, 0.569], [950.0, 0.602],
                [960.0, 0.623], [970.0, 0.657], [980.0, 0.694], [990.0, 0.712], [1000.0, 0.724],
                [1010.0, 0.732], [1020.0, 0.738], [1030.0, 0.741], [1040.0, 0.742], [1050.0, 0.749],
                [1060.0, 0.755], [1070.0, 0.765], [1080.0, 0.775], [1090.0, 0.795], [1100.0, 0.814],
                [1110.0, 0.845], [1120.0, 0.856], [1130.0, 0.863], [1140.0, 0.868], [1150.0, 0.856],
                [1160.0, 0.855], [1170.0, 0.848], [1180.0, 0.85],  [1190.0, 0.857], [1200.0, 0.866],
                [1210.0, 0.872], [1220.0, 0.879], [1230.0, 0.888], [1240.0, 0.899], [1250.0, 0.91],
                [1260.0, 0.921], [1270.0, 0.929], [1280.0, 0.938], [1290.0, 0.944], [1300.0, 0.95],
                [1310.0, 0.954], [1320.0, 0.954], [1330.0, 0.954], [1340.0, 0.953], [1350.0, 0.95],
                [1360.0, 0.947], [1370.0, 0.946], [1380.0, 0.946], [1390.0, 0.945], [1400.0, 0.946],
                [1410.0, 0.933], [1420.0, 0.937], [1430.0, 0.944], [1440.0, 0.949], [1450.0, 0.958],
                [1460.0, 0.966], [1470.0, 0.972], [1480.0, 0.978], [1490.0, 0.985], [1500.0, 0.992],
                [1510.0, 0.997], [1520.0, 0.997], [1530.0, 0.997], [1540.0, 0.994], [1550.0, 0.99],
                [1560.0, 0.984], [1570.0, 0.98],  [1580.0, 0.978], [1590.0, 0.971], [1600.0, 0.961],
                [1610.0, 0.943], [1620.0, 0.921], [1630.0, 0.898], [1640.0, 0.877], [1650.0, 0.852],
                [1660.0, 0.806], [1670.0, 0.652], [1680.0, 0.41],  [1690.0, 0.239], [1700.0, 0.145],
            ],
        },
        "SILICON": {
            "points": [
                [400.0, 0.0918],  [410.0, 0.103],   [420.0, 0.1226],  [430.0, 0.1418],
                [440.0, 0.1539],  [450.0, 0.1658],  [460.0, 0.1760],  [470.0, 0.1693],
                [480.0, 0.1879],  [490.0, 0.1891],  [500.0, 0.2079],  [510.0, 0.2089],
                [520.0, 0.2093],  [530.0, 0.2175],  [540.0, 0.2277],  [550.0, 0.2432],
                [560.0, 0.2559],  [570.0, 0.2723],  [580.0, 0.2885],  [590.0, 0.3060],
                [600.0, 0.3240],  [610.0, 0.3421],  [620.0, 0.3612],  [630.0, 0.3792],
                [640.0, 0.3969],  [650.0, 0.4097],  [660.0, 0.4313],  [670.0, 0.4417],
                [680.0, 0.4538],  [690.0, 0.4635],  [700.0, 0.4746],  [710.0, 0.4871],
                [720.0, 0.4964],  [730.0, 0.4932],  [740.0, 0.5242],  [750.0, 0.5251],
                [760.0, 0.5275],  [770.0, 0.5288],  [780.0, 0.5326],  [790.0, 0.5508],
                [800.0, 0.5600],  [810.0, 0.5636],  [820.0, 0.5642],  [830.0, 0.5653],
                [840.0, 0.5676],  [850.0, 0.5685],  [860.0, 0.5699],  [870.0, 0.5703],
                [880.0, 0.5804],  [890.0, 0.5853],  [900.0, 0.5906],  [910.0, 0.5959],
                [920.0, 0.5979],  [930.0, 0.5985],  [940.0, 0.6021],  [950.0, 0.5988],
                [960.0, 0.5969],  [970.0, 0.5874],  [980.0, 0.5742],  [990.0, 0.5590],
                [1000.0, 0.5491], [1010.0, 0.5256], [1020.0, 0.4838], [1030.0, 0.4346],
                [1040.0, 0.3743], [1050.0, 0.3049], [1060.0, 0.2410], [1070.0, 0.1870],
                [1080.0, 0.1560], [1090.0, 0.1210], [1100.0, 0.0662],
            ],
        },
    },
}


class _CoreDAQDriver:
    """Low-level coreDAQ protocol driver.

    Accepts a Transport instance; does not create serial connections itself.
    All firmware I/O is routed through the transport's ask() / logcal() /
    read_frames() methods.
    """

    # --- ADC constants ---
    ADC_BITS = 16
    ADC_VFS_VOLTS = 5.0
    ADC_LSB_VOLTS = (2.0 * ADC_VFS_VOLTS) / (2 ** ADC_BITS)
    ADC_LSB_MV = ADC_LSB_VOLTS * 1e3
    MV_OUTPUT_DECIMALS = 3
    POWER_OUTPUT_DECIMALS_MAX = 12
    CODES_PER_FS = 32768.0

    NUM_HEADS = 4
    NUM_GAINS = 8
    SDRAM_BYTES = 32 * 1024 * 1024

    FRONTEND_LINEAR = "LINEAR"
    FRONTEND_LOG = "LOG"
    DETECTOR_INGAAS = "INGAAS"
    DETECTOR_SILICON = "SILICON"

    DEFAULT_WAVELENGTH_NM = 1550.0
    DEFAULT_RESPONSIVITY_REF_NM = 1550.0
    DEFAULT_SILICON_LOG_VY_V_PER_DECADE = 0.5
    DEFAULT_SILICON_LOG_IZ_A = 100e-12
    MAX_INGAAS_LOG_POWER_W = 3e-3
    MIN_INGAAS_LOG_POWER_W = 1e-9
    INGAAS_WAVELENGTH_RANGE_NM = (910.0, 1700.0)
    SILICON_WAVELENGTH_RANGE_NM = (400.0, 1100.0)

    GAIN_PROFILE_STANDARD = "standard"
    GAIN_PROFILE_LINEAR_LEGACY = "linear_legacy"

    GAIN_MAX_POWER_W = [5e-3, 1e-3, 500e-6, 100e-6, 50e-6, 10e-6, 5e-6, 500e-9]
    LEGACY_LINEAR_GAIN_MAX_POWER_W = [3.5e-3, 1.5e-3, 750e-6, 350e-6, 75e-6, 35e-6, 3.5e-6, 350e-9]

    GAIN_LABELS = ["5 mW", "1 mW", "500 uW", "100 uW", "50 uW", "10 uW", "5 uW", "500 nW"]
    LEGACY_LINEAR_GAIN_LABELS = ["3.5 mW", "1.5 mW", "750 uW", "350 uW", "75 uW", "35 uW", "3.5 uW", "350 nW"]

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._idn_cache: str = ""

        # Detect frontend and detector once at init
        self._frontend_type: str = self._detect_frontend_type_once()
        try:
            self._idn_cache = self.idn()
        except Exception:
            self._idn_cache = ""
        self._detector_type: str = self._detect_detector_type_once(self._idn_cache)
        self._gain_profile: str = self.gain_profile_from_idn(self._idn_cache, self._frontend_type)

        # LINEAR calibration tables
        self._cal_slope: List[List[float]] = [[0.0] * self.NUM_GAINS for _ in range(self.NUM_HEADS)]
        self._cal_intercept: List[List[float]] = [[0.0] * self.NUM_GAINS for _ in range(self.NUM_HEADS)]

        # Zero offsets (LINEAR only)
        self._factory_zero_adc: List[int] = [0, 0, 0, 0]
        self._linear_zero_adc: List[int] = [0, 0, 0, 0]

        # LOG LUT storage (InGaAs LOG only)
        self._loglut_V_V_by_head: List[Optional[List[float]]] = [None] * self.NUM_HEADS
        self._loglut_log10P_by_head: List[Optional[List[float]]] = [None] * self.NUM_HEADS
        self._loglut_V_mV_by_head: List[Optional[List[int]]] = [None] * self.NUM_HEADS
        self._loglut_log10P_Q16_by_head: List[Optional[List[int]]] = [None] * self.NUM_HEADS
        # Head-0 shortcuts (backward compat)
        self._loglut_V_V: Optional[List[float]] = None
        self._loglut_log10P: Optional[List[float]] = None

        # Wavelength / responsivity
        self._wavelength_nm: float = self.DEFAULT_WAVELENGTH_NM
        self._responsivity_ref_nm: float = self.DEFAULT_RESPONSIVITY_REF_NM
        self._resp_curve_nm: Dict[str, List[float]] = {}
        self._resp_curve_aw: Dict[str, List[float]] = {}

        # Silicon model parameters
        self._silicon_log_vy_v_per_decade: float = self.DEFAULT_SILICON_LOG_VY_V_PER_DECADE
        self._silicon_log_iz_a: float = self.DEFAULT_SILICON_LOG_IZ_A
        self._silicon_linear_tia_ohm: List[List[float]] = self._build_default_tia_ohm_table()

        # Initialize device
        self.i2c_refresh()
        self._load_calibration_for_frontend()
        if self._frontend_type == self.FRONTEND_LINEAR:
            self._load_factory_zeros()
        try:
            self._load_builtin_responsivity_curves()
            self._bootstrap_silicon_tia_from_linear_cal()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Class-level gain helpers
    # ------------------------------------------------------------------

    @classmethod
    def gain_profile_from_idn(cls, idn_payload: str = "", frontend_type: str = "") -> str:
        idn = str(idn_payload or "").upper()
        frontend = str(frontend_type or "").upper()
        if frontend == cls.FRONTEND_LINEAR and (
            "LINEAR_LEGACY" in idn or ("LINEAR" in idn and "LEGACY" in idn)
        ):
            return cls.GAIN_PROFILE_LINEAR_LEGACY
        return cls.GAIN_PROFILE_STANDARD

    @classmethod
    def gain_max_power_table(cls, gain_profile: str = GAIN_PROFILE_STANDARD) -> List[float]:
        if gain_profile == cls.GAIN_PROFILE_LINEAR_LEGACY:
            return list(cls.LEGACY_LINEAR_GAIN_MAX_POWER_W)
        return list(cls.GAIN_MAX_POWER_W)

    @classmethod
    def gain_labels(cls, gain_profile: str = GAIN_PROFILE_STANDARD) -> List[str]:
        if gain_profile == cls.GAIN_PROFILE_LINEAR_LEGACY:
            return list(cls.LEGACY_LINEAR_GAIN_LABELS)
        return list(cls.GAIN_LABELS)

    @classmethod
    def gain_label(cls, gain_index: int, gain_profile: str = GAIN_PROFILE_STANDARD) -> str:
        labels = cls.gain_labels(gain_profile)
        idx = max(0, min(len(labels) - 1, int(gain_index or 0)))
        return labels[idx]

    @classmethod
    def _build_default_tia_ohm_table(cls) -> List[List[float]]:
        per_gain = [
            cls.ADC_VFS_VOLTS / pmax if pmax > 0 else 1.0
            for pmax in cls.GAIN_MAX_POWER_W
        ]
        return [list(per_gain) for _ in range(cls.NUM_HEADS)]

    # ------------------------------------------------------------------
    # Transport delegation
    # ------------------------------------------------------------------

    def _ask(self, cmd: str) -> Tuple[str, str]:
        return self._transport.ask(cmd)

    def _ask_with_busy_retry(
        self, cmd: str, retries: int = 20, delay_s: float = 0.05
    ) -> Tuple[str, str]:
        return self._transport.ask_with_busy_retry(cmd, retries=retries, delay_s=delay_s)

    def port_name(self) -> str:
        fn = getattr(self._transport, "port_name", None)
        if callable(fn):
            return fn()
        # Fallback for SerialTransport which exposes _ser.port
        ser = getattr(self._transport, "_ser", None)
        return str(getattr(ser, "port", "")) if ser is not None else ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._transport.close()

    # ------------------------------------------------------------------
    # Frontend / detector detection
    # ------------------------------------------------------------------

    def _detect_frontend_type_once(self) -> str:
        time.sleep(0.05)
        self._transport.drain()
        st, p = self._ask("HEAD_TYPE?")
        if st != "OK":
            raise CoreDAQError(f"HEAD_TYPE? failed: {p}")
        txt = p.strip().upper().replace(" ", "")
        if "TYPE=LOG" in txt:
            return self.FRONTEND_LOG
        if "TYPE=LINEAR" in txt:
            return self.FRONTEND_LINEAR
        raise CoreDAQError(f"Unexpected HEAD_TYPE? reply: {p!r}")

    def frontend_type(self) -> str:
        return self._frontend_type

    def _require_frontend(self, expected: str, feature: str) -> None:
        if self._frontend_type != expected:
            raise CoreDAQError(
                f"{feature} not supported on {self._frontend_type} front end (expected {expected})."
            )

    @staticmethod
    def _normalize_detector_type(detector: str) -> str:
        txt = str(detector or "").strip().upper()
        if txt in ("INGAAS", "INGAAS_PD", "INGAASPD"):
            return _CoreDAQDriver.DETECTOR_INGAAS
        if txt in ("SILICON", "SI", "SIPD", "SI_PD"):
            return _CoreDAQDriver.DETECTOR_SILICON
        raise ValueError(f"Unknown detector type: {detector!r}")

    def _detect_detector_type_once(self, idn_payload: str = "") -> str:
        txt = str(idn_payload or "").upper()
        if "INGAAS" in txt:
            return self.DETECTOR_INGAAS
        if "SILICON" in txt:
            return self.DETECTOR_SILICON
        toks = [t for t in re.split(r"[^A-Z0-9]+", txt) if t]
        if "SI" in toks:
            return self.DETECTOR_SILICON
        return self.DETECTOR_INGAAS

    def detector_type(self) -> str:
        return self._detector_type

    # ------------------------------------------------------------------
    # Wavelength / responsivity
    # ------------------------------------------------------------------

    def _detector_wavelength_limits_nm(self, detector: Optional[str] = None) -> Tuple[float, float]:
        det = self._detector_type if detector is None else self._normalize_detector_type(detector)
        if det == self.DETECTOR_SILICON:
            return self.SILICON_WAVELENGTH_RANGE_NM
        return self.INGAAS_WAVELENGTH_RANGE_NM

    def get_wavelength_limits_nm(self, detector: Optional[str] = None) -> Tuple[float, float]:
        return self._detector_wavelength_limits_nm(detector)

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        wl = float(wavelength_nm)
        if not math.isfinite(wl) or wl <= 0.0:
            raise ValueError("wavelength_nm must be > 0")
        lo, hi = self._detector_wavelength_limits_nm()
        clamped = max(lo, min(hi, wl))
        if clamped != wl:
            warnings.warn(
                f"wavelength_nm={wl:g} is outside {self._detector_type} range "
                f"[{lo:g}, {hi:g}] nm; clamped to {clamped:g} nm.",
                RuntimeWarning,
                stacklevel=3,
            )
        self._wavelength_nm = clamped

    def get_wavelength_nm(self) -> float:
        return float(self._wavelength_nm)

    def _interp_responsivity_aw(self, detector: str, wavelength_nm: float) -> float:
        det = self._normalize_detector_type(detector)
        if det not in self._resp_curve_nm:
            raise CoreDAQError("Responsivity curves not loaded.")
        xs = self._resp_curve_nm[det]
        ys = self._resp_curve_aw[det]
        x = float(wavelength_nm)
        if x <= xs[0]:
            return float(ys[0])
        if x >= xs[-1]:
            return float(ys[-1])
        j = bisect.bisect_left(xs, x)
        x0, x1 = xs[j - 1], xs[j]
        y0, y1 = ys[j - 1], ys[j]
        if x1 == x0:
            return float(y0)
        return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0))

    def get_responsivity_A_per_W(
        self,
        detector: Optional[str] = None,
        wavelength_nm: Optional[float] = None,
    ) -> float:
        det = self._detector_type if detector is None else detector
        wl = self._wavelength_nm if wavelength_nm is None else float(wavelength_nm)
        return float(self._interp_responsivity_aw(det, wl))

    def _ingaas_responsivity_correction_factor(self) -> float:
        try:
            r_ref = self._interp_responsivity_aw(self.DETECTOR_INGAAS, self._responsivity_ref_nm)
            r_now = self._interp_responsivity_aw(self.DETECTOR_INGAAS, self._wavelength_nm)
        except Exception:
            return 1.0
        if r_now <= 0.0 or not math.isfinite(r_now):
            return 1.0
        return max(0.0, float(r_ref) / float(r_now))

    def _load_responsivity_curves_doc(self, doc: dict) -> None:
        det_data = doc.get("detectors", {})
        parsed_nm: Dict[str, List[float]] = {}
        parsed_aw: Dict[str, List[float]] = {}
        for key in (self.DETECTOR_INGAAS, self.DETECTOR_SILICON):
            points = det_data.get(key, {}).get("points", [])
            clean = []
            for row in points:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                try:
                    wl, aw = float(row[0]), float(row[1])
                except Exception:
                    continue
                if wl > 0 and aw > 0 and math.isfinite(wl) and math.isfinite(aw):
                    clean.append((wl, aw))
            if not clean:
                continue
            clean.sort(key=lambda p: p[0])
            by_wl: dict[float, float] = {}
            for wl, aw in clean:
                by_wl[wl] = aw
            uniq = sorted(by_wl.items())
            parsed_nm[key] = [p[0] for p in uniq]
            parsed_aw[key] = [p[1] for p in uniq]
        if self.DETECTOR_INGAAS not in parsed_nm or self.DETECTOR_SILICON not in parsed_nm:
            raise CoreDAQError("Responsivity data missing INGAAS or SILICON curve")
        self._resp_curve_nm = parsed_nm
        self._resp_curve_aw = parsed_aw

    def _load_builtin_responsivity_curves(self) -> None:
        self._load_responsivity_curves_doc(_BUILTIN_RESPONSIVITY_CURVES)

    def _bootstrap_silicon_tia_from_linear_cal(self) -> None:
        try:
            r_ref = self._interp_responsivity_aw(self.DETECTOR_INGAAS, self._responsivity_ref_nm)
        except Exception:
            r_ref = 1.0
        if not math.isfinite(r_ref) or r_ref <= 0.0:
            r_ref = 1.0
        for h in range(self.NUM_HEADS):
            for g in range(self.NUM_GAINS):
                slope = float(self._cal_slope[h][g])
                if not math.isfinite(slope) or slope == 0.0:
                    continue
                tia = abs(slope) / (1000.0 * r_ref)
                if math.isfinite(tia) and tia > 0.0:
                    self._silicon_linear_tia_ohm[h][g] = float(tia)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def idn(self, refresh: bool = False) -> str:
        if self._idn_cache and not refresh:
            return self._idn_cache
        st, p = self._ask("IDN?")
        if st != "OK":
            raise CoreDAQError(p)
        self._idn_cache = p
        self._gain_profile = self.gain_profile_from_idn(p, self._frontend_type)
        return p

    def gain_profile(self, refresh: bool = False) -> str:
        self.idn(refresh=refresh)
        return self._gain_profile or self.GAIN_PROFILE_STANDARD

    # ------------------------------------------------------------------
    # ADC math helpers
    # ------------------------------------------------------------------

    @classmethod
    def adc_code_to_volts(cls, code: Number) -> float:
        return float(code) * cls.ADC_LSB_VOLTS

    @classmethod
    def adc_code_to_mV(cls, code: Number) -> float:
        return cls.adc_code_to_volts(code) * 1e3

    @classmethod
    def _power_decimals_from_step(cls, step_w: float) -> int:
        if not math.isfinite(step_w) or step_w <= 0.0:
            return 0
        return max(0, min(cls.POWER_OUTPUT_DECIMALS_MAX, round(-math.log10(step_w))))

    @staticmethod
    def _quantize_to_step(value: float, step: float) -> float:
        if not math.isfinite(value):
            return 0.0
        if not math.isfinite(step) or step <= 0.0:
            return value
        return round(value / step) * step

    # ------------------------------------------------------------------
    # Calibration loading
    # ------------------------------------------------------------------

    def _load_calibration_for_frontend(self) -> None:
        if self._detector_type == self.DETECTOR_SILICON:
            return
        if self._frontend_type == self.FRONTEND_LINEAR:
            self._load_linear_calibration()
        elif self._frontend_type == self.FRONTEND_LOG:
            self._load_log_calibration()
        else:
            raise CoreDAQError(f"Unknown frontend type: {self._frontend_type}")

    def _load_linear_calibration(self) -> None:
        for head in range(1, self.NUM_HEADS + 1):
            for gain in range(self.NUM_GAINS):
                st, payload = self._ask(f"CAL {head} {gain}")
                if st != "OK":
                    raise CoreDAQError(f"CAL {head} {gain} failed: {payload}")
                parts = payload.split()
                slope_hex = intercept_hex = None
                for token in parts:
                    if token.startswith("S="):
                        slope_hex = token.split("=", 1)[1]
                    elif token.startswith("I="):
                        intercept_hex = token.split("=", 1)[1]
                if slope_hex is None or intercept_hex is None:
                    raise CoreDAQError(f"Missing S= or I= in CAL reply: {payload!r}")
                try:
                    slope = struct.unpack("<f", int(slope_hex, 16).to_bytes(4, "little"))[0]
                    intercept = struct.unpack("<f", int(intercept_hex, 16).to_bytes(4, "little"))[0]
                except Exception as exc:
                    raise CoreDAQError(f"Failed parsing CAL payload {payload!r}: {exc}") from exc
                self._cal_slope[head - 1][gain] = float(slope)
                self._cal_intercept[head - 1][gain] = float(intercept)

    def _load_log_calibration(self) -> None:
        loaded: list[tuple[list[int], list[int]]] = []
        for head in range(1, self.NUM_HEADS + 1):
            v_mv_list, log10p_q16_list = self._transport.logcal(head)
            if not v_mv_list:
                raise coreDAQCalibrationError(f"LOG LUT empty for head {head}")
            loaded.append((v_mv_list, log10p_q16_list))

        self._loglut_V_mV_by_head = [vals for vals, _ in loaded]
        self._loglut_log10P_Q16_by_head = [vals for _, vals in loaded]
        self._loglut_V_V_by_head = [[v / 1000.0 for v in vals] for vals, _ in loaded]
        self._loglut_log10P_by_head = [[q / 65536.0 for q in vals] for _, vals in loaded]

        self._loglut_V_V = self._loglut_V_V_by_head[0]
        self._loglut_log10P = self._loglut_log10P_by_head[0]

    def _get_log_lut_for_head_index(self, head_idx: int) -> Tuple[List[float], List[float]]:
        idx = int(head_idx)
        if not (0 <= idx < self.NUM_HEADS):
            raise ValueError("head_idx must be 0..3")
        xs = self._loglut_V_V_by_head[idx] or (self._loglut_V_V if idx == 0 else None)
        ys = self._loglut_log10P_by_head[idx] or (self._loglut_log10P if idx == 0 else None)
        if xs is None or ys is None or len(xs) == 0 or len(xs) != len(ys):
            raise CoreDAQError(f"LOG LUT not loaded for head {idx + 1}")
        return xs, ys

    def _interp_extrap_log10(self, xs: List[float], ys: List[float], x: float) -> float:
        if not xs or not ys or len(xs) != len(ys):
            raise CoreDAQError("Invalid LOG LUT")
        if len(xs) == 1:
            return float(ys[0])
        if x <= xs[0]:
            x0, x1 = xs[0], xs[1]
            y0, y1 = ys[0], ys[1]
            return float(y0 + ((x - x0) / (x1 - x0)) * (y1 - y0)) if x1 != x0 else float(y0)
        if x >= xs[-1]:
            x0, x1 = xs[-2], xs[-1]
            y0, y1 = ys[-2], ys[-1]
            return float(y0 + ((x - x0) / (x1 - x0)) * (y1 - y0)) if x1 != x0 else float(y1)
        j = bisect.bisect_left(xs, x)
        x0, x1 = xs[j - 1], xs[j]
        y0, y1 = ys[j - 1], ys[j]
        if x1 == x0:
            return float(y0)
        return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0))

    # ------------------------------------------------------------------
    # Unit conversion
    # ------------------------------------------------------------------

    def _clamp_ingaas_log_power_w(self, power_w: float) -> float:
        return float(min(max(float(power_w), self.MIN_INGAAS_LOG_POWER_W), self.MAX_INGAAS_LOG_POWER_W))

    def _convert_log_voltage_to_power_w(self, v_volts: float, head_idx: int = 0) -> float:
        if self._detector_type == self.DETECTOR_SILICON:
            resp = self._interp_responsivity_aw(self.DETECTOR_SILICON, self._wavelength_nm)
            if resp <= 0.0:
                raise CoreDAQError("Invalid silicon responsivity")
            return float(
                (self._silicon_log_iz_a / resp)
                * (10.0 ** (float(v_volts) / self._silicon_log_vy_v_per_decade))
            )
        xs, ys = self._get_log_lut_for_head_index(head_idx)
        p_w = 10.0 ** self._interp_extrap_log10(xs, ys, float(v_volts))
        if self._detector_type == self.DETECTOR_INGAAS:
            p_w *= self._ingaas_responsivity_correction_factor()
            p_w = self._clamp_ingaas_log_power_w(p_w)
        return float(p_w)

    def _convert_linear_mv_to_power_w(self, head_idx: int, gain: int, mv_corr: float) -> float:
        if self._detector_type == self.DETECTOR_SILICON:
            resp = self._interp_responsivity_aw(self.DETECTOR_SILICON, self._wavelength_nm)
            tia = float(self._silicon_linear_tia_ohm[head_idx][gain])
            if resp <= 0.0 or tia <= 0.0:
                raise CoreDAQError(f"Invalid silicon model at head {head_idx+1}, gain {gain}")
            power_lsb = self.ADC_LSB_VOLTS / abs(tia * resp)
            decimals = self._power_decimals_from_step(power_lsb)
            p_w = (float(mv_corr) / 1000.0) / (tia * resp)
            p_w = self._quantize_to_step(p_w, power_lsb)
            return round(p_w, decimals)

        slope_mV_per_W = float(self._cal_slope[head_idx][gain])
        if slope_mV_per_W == 0.0:
            raise CoreDAQError(f"Invalid slope for head {head_idx+1}, gain {gain}")
        power_lsb = self.ADC_LSB_MV / abs(slope_mV_per_W)
        p_w = float(mv_corr) / slope_mV_per_W
        if self._detector_type == self.DETECTOR_INGAAS:
            corr = self._ingaas_responsivity_correction_factor()
            p_w *= corr
            power_lsb *= max(0.0, corr)
        decimals = self._power_decimals_from_step(power_lsb)
        p_w = self._quantize_to_step(p_w, power_lsb)
        return round(p_w, decimals)

    # ------------------------------------------------------------------
    # Zeroing (LINEAR only)
    # ------------------------------------------------------------------

    def _load_factory_zeros(self) -> List[int]:
        self._require_frontend(self.FRONTEND_LINEAR, "_load_factory_zeros")
        st, payload = self._ask("FACTORY_ZEROS?")
        if st != "OK":
            raise CoreDAQError(f"FACTORY_ZEROS? failed: {payload}")
        parts = payload.split()
        if len(parts) < 4:
            raise CoreDAQError(f"FACTORY_ZEROS? payload too short: {payload!r}")
        if any("=" in t for t in parts):
            kv = {}
            for t in parts:
                if "=" in t:
                    k, v = t.split("=", 1)
                    kv[k.strip().lower()] = v.strip()
            try:
                z = [int(kv["h1"], 0), int(kv["h2"], 0), int(kv["h3"], 0), int(kv["h4"], 0)]
            except Exception as exc:
                raise CoreDAQError(f"FACTORY_ZEROS? parse error: {payload!r}") from exc
        else:
            try:
                z = [int(parts[i], 0) for i in range(4)]
            except Exception as exc:
                raise CoreDAQError(f"FACTORY_ZEROS? parse error: {payload!r}") from exc
        self._factory_zero_adc = list(z)
        self._linear_zero_adc = list(z)
        return list(z)

    def get_linear_zero_adc(self) -> Tuple[int, int, int, int]:
        if self._frontend_type != self.FRONTEND_LINEAR:
            return (0, 0, 0, 0)
        return tuple(int(x) for x in self._linear_zero_adc)  # type: ignore[return-value]

    def get_factory_zero_adc(self) -> Tuple[int, int, int, int]:
        if self._frontend_type != self.FRONTEND_LINEAR:
            return (0, 0, 0, 0)
        return tuple(int(x) for x in self._factory_zero_adc)  # type: ignore[return-value]

    def restore_factory_zero(self) -> None:
        if self._frontend_type != self.FRONTEND_LINEAR:
            return
        if self._factory_zero_adc == [0, 0, 0, 0]:
            try:
                self._load_factory_zeros()
                return
            except Exception:
                pass
        self._linear_zero_adc = list(self._factory_zero_adc)

    def soft_zero_from_snapshot(self, n_frames: int = 32, settle_s: float = 0.2) -> Tuple[List[int], List[int]]:
        self._require_frontend(self.FRONTEND_LINEAR, "soft_zero_from_snapshot")
        if n_frames <= 0:
            raise ValueError("n_frames must be > 0")
        time.sleep(max(0.0, float(settle_s)))
        codes, gains = self.snapshot_adc(n_frames=n_frames)
        self._linear_zero_adc = [int(codes[i]) for i in range(4)]
        return codes, gains

    def _apply_linear_zero_ch(self, codes: List[int]) -> List[int]:
        if self._frontend_type != self.FRONTEND_LINEAR:
            return codes
        return [int(codes[i]) - int(self._linear_zero_adc[i]) for i in range(4)]

    def snapshot_adc_zeroed(
        self,
        n_frames: int = 1,
        timeout_s: float = 1.0,
        poll_hz: float = 200.0,
    ) -> Tuple[List[int], List[int]]:
        codes, gains = self.snapshot_adc(n_frames=n_frames, timeout_s=timeout_s, poll_hz=poll_hz)
        return self._apply_linear_zero_ch(codes), gains

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot_adc(
        self,
        n_frames: int = 1,
        timeout_s: float = 1.0,
        poll_hz: float = 200.0,
    ) -> Tuple[List[int], List[int]]:
        st, payload = self._ask(f"SNAP {n_frames}")
        if st != "OK":
            raise CoreDAQError(f"SNAP arm failed: {payload}")

        t0 = time.time()
        sleep_s = 1.0 / poll_hz

        while True:
            st, payload = self._ask("SNAP?")
            if st == "BUSY":
                if (time.time() - t0) > timeout_s:
                    raise coreDAQTimeoutError("Snapshot timeout")
                time.sleep(sleep_s)
                continue
            if st != "OK":
                raise CoreDAQError(f"SNAP? failed: {payload}")

            parts = payload.split()
            if len(parts) < 4:
                raise CoreDAQError(f"SNAP? payload too short: {payload}")
            try:
                codes = [int(parts[i]) for i in range(4)]
            except ValueError as exc:
                raise CoreDAQError(f"Failed to parse ADC codes from SNAP?: {payload}") from exc

            gains = [0, 0, 0, 0]
            for i, part in enumerate(parts):
                if "G=" in part:
                    try:
                        gains[0] = int(part.split("=")[1])
                        gains[1] = int(parts[i + 1])
                        gains[2] = int(parts[i + 2])
                        gains[3] = int(parts[i + 3])
                    except (ValueError, IndexError) as exc:
                        raise CoreDAQError(f"Failed to parse gains from SNAP?: {payload}") from exc
                    break
            return codes, gains

    # ------------------------------------------------------------------
    # Gains (LINEAR only)
    # ------------------------------------------------------------------

    def set_gain(self, head: int, value: int) -> None:
        self._require_frontend(self.FRONTEND_LINEAR, "set_gain")
        if head not in (1, 2, 3, 4):
            raise ValueError("head must be 1..4")
        if not (0 <= value <= 7):
            raise ValueError("gain value must be 0..7")
        st, payload = self._ask(f"GAIN {head} {value}")
        if st != "OK":
            raise CoreDAQError(f"GAIN {head} failed: {payload}")
        time.sleep(0.05)

    def get_gains(self) -> Tuple[int, int, int, int]:
        self._require_frontend(self.FRONTEND_LINEAR, "get_gains")
        st, payload = self._ask("GAINS?")
        if st != "OK":
            raise CoreDAQError(f"GAINS? failed: {payload}")
        parts = payload.replace("HEAD", "").replace("=", " ").split()
        try:
            nums = [int(parts[i]) for i in range(1, len(parts), 2)]
            if len(nums) != 4:
                raise ValueError
            return tuple(nums)  # type: ignore[return-value]
        except Exception:
            raise CoreDAQError(f"Unexpected GAINS? payload: '{payload}'")

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    def state_enum(self) -> int:
        st, p = self._ask("STATE?")
        if st != "OK":
            raise CoreDAQError(p)
        return int(p, 0)

    def arm_acquisition(
        self,
        frames: int,
        use_trigger: bool = False,
        trigger_rising: bool = True,
    ) -> None:
        if frames <= 0:
            raise ValueError("frames must be > 0")
        max_frames = self.max_acquisition_frames()
        if frames > max_frames:
            raise CoreDAQError(f"frames={frames} exceeds max={max_frames}")
        if use_trigger:
            pol = "R" if trigger_rising else "F"
            st, p = self._ask(f"TRIGARM {frames} {pol}")
            if st != "OK":
                raise CoreDAQError(f"TRIGARM failed: {p}")
            return
        st, p = self._ask(f"ACQ ARM {frames}")
        if st != "OK":
            raise CoreDAQError(f"ACQ ARM failed: {p}")

    def start_acquisition(self) -> None:
        st, p = self._ask("ACQ START")
        if st != "OK":
            raise CoreDAQError(f"ACQ START failed: {p}")

    def stop_acquisition(self) -> None:
        st, p = self._ask("ACQ STOP")
        if st != "OK":
            raise CoreDAQError(f"ACQ STOP failed: {p}")

    def acquisition_status(self) -> str:
        st, p = self._ask("STREAM?")
        if st != "OK":
            raise CoreDAQError(p)
        return p

    def frames_remaining(self) -> int:
        st, p = self._ask("LEFT?")
        if st != "OK":
            raise CoreDAQError(p)
        return int(p, 0)

    def wait_for_completion(
        self,
        poll_s: float = 0.25,
        timeout_s: Optional[float] = None,
    ) -> None:
        READY_STATE = 4
        t0 = time.time()
        while True:
            if self.state_enum() == READY_STATE:
                return
            if timeout_s is not None and (time.time() - t0) > timeout_s:
                raise coreDAQTimeoutError("Acquisition timeout")
            time.sleep(poll_s)

    # ------------------------------------------------------------------
    # Channel mask
    # ------------------------------------------------------------------

    @staticmethod
    def _active_channel_indices(mask: int) -> List[int]:
        return [i for i in range(4) if (mask >> i) & 1]

    def get_channel_mask_info(self) -> Tuple[int, int, int]:
        st, p = self._ask("CHMASK?")
        if st != "OK":
            raise CoreDAQError(f"CHMASK? failed: {p}")
        m = re.search(r"0x([0-9A-Fa-f]+)", p)
        ch = re.search(r"CH\s*=\s*(\d+)", p, re.IGNORECASE)
        fb = re.search(r"FB\s*=\s*(\d+)", p, re.IGNORECASE)
        if not m:
            raise CoreDAQError(f"Unexpected CHMASK? payload: '{p}'")
        mask = int(m.group(1), 16) & 0x0F
        active = int(ch.group(1)) if ch else len(self._active_channel_indices(mask))
        frame_bytes = int(fb.group(1)) if fb else active * 2
        return mask, active, frame_bytes

    def set_channel_mask(self, mask: int) -> None:
        mask = int(mask) & 0x0F
        if mask == 0:
            raise ValueError("mask must enable at least one channel")
        st, p = self._ask(f"CHMASK 0x{mask:X}")
        if st != "OK":
            raise CoreDAQError(f"CHMASK set failed: {p}")

    def max_acquisition_frames(self, mask: Optional[int] = None) -> int:
        if mask is None:
            try:
                _m, _ch, frame_bytes = self.get_channel_mask_info()
            except Exception:
                frame_bytes = 8
        else:
            active = len(self._active_channel_indices(int(mask) & 0x0F))
            frame_bytes = max(2, active * 2)
        return self.SDRAM_BYTES // frame_bytes

    # ------------------------------------------------------------------
    # Frame transfer (ADC codes only)
    # ------------------------------------------------------------------

    def transfer_frames_adc(self, frames: int) -> List[List[int]]:
        if frames <= 0:
            raise ValueError("frames must be > 0")
        try:
            mask, _active_ch, _fb = self.get_channel_mask_info()
        except Exception:
            mask = 0x0F
        return self._transport.read_frames(frames, mask)

    # ------------------------------------------------------------------
    # Device settings
    # ------------------------------------------------------------------

    def i2c_refresh(self) -> None:
        st, payload = self._ask("I2C REFRESH")
        if st != "OK":
            raise CoreDAQError(f"I2C REFRESH failed: {payload}")

    def get_oversampling(self) -> int:
        st, p = self._ask_with_busy_retry("OS?")
        if st != "OK":
            raise CoreDAQError(p)
        return int(p, 0)

    def get_freq_hz(self) -> int:
        st, p = self._ask_with_busy_retry("FREQ?")
        if st != "OK":
            raise CoreDAQError(p)
        return int(p, 0)

    def _max_freq_for_os(self, os_idx: int) -> int:
        if not (0 <= os_idx <= 7):
            raise ValueError("os_idx must be 0..7")
        base = 100_000
        if os_idx <= 1:
            return base
        return base // (2 ** (os_idx - 1))

    def _best_os_for_freq(self, hz: int) -> int:
        if hz <= 0:
            raise ValueError("hz must be > 0")
        best = 0
        for os_idx in range(8):
            if hz <= self._max_freq_for_os(os_idx):
                best = os_idx
            else:
                break
        return best

    def set_freq(self, hz: int) -> None:
        if hz <= 0 or hz > 100_000:
            raise CoreDAQError("FREQ must be 1..100000 Hz")
        st, p = self._ask_with_busy_retry(f"FREQ {hz}")
        if st != "OK":
            raise CoreDAQError(p)
        cur_os = self.get_oversampling()
        if hz > self._max_freq_for_os(cur_os):
            new_os = self._best_os_for_freq(hz)
            st, p = self._ask_with_busy_retry(f"OS {new_os}")
            if st != "OK":
                raise CoreDAQError(p)
            warnings.warn(
                f"OS {cur_os} is not valid at {hz} Hz. Auto-adjusted OS to {new_os}.",
                RuntimeWarning,
                stacklevel=3,
            )

    def set_oversampling(self, os_idx: int) -> None:
        if not (0 <= os_idx <= 7):
            raise CoreDAQError("OS must be 0..7")
        hz = self.get_freq_hz()
        if hz > self._max_freq_for_os(os_idx):
            new_os = self._best_os_for_freq(hz)
            st, p = self._ask_with_busy_retry(f"OS {new_os}")
            if st != "OK":
                raise CoreDAQError(p)
            warnings.warn(
                f"Requested OS {os_idx} not valid at {hz} Hz. Set OS={new_os}.",
                RuntimeWarning,
                stacklevel=3,
            )
            return
        st, p = self._ask_with_busy_retry(f"OS {os_idx}")
        if st != "OK":
            raise CoreDAQError(p)

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------

    def get_head_temperature_C(self) -> float:
        st, val = self._ask("TEMP?")
        if st != "OK":
            raise CoreDAQError(f"TEMP? failed: {val}")
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad TEMP format: '{val}'")

    def get_head_humidity(self) -> float:
        st, val = self._ask("HUM?")
        if st != "OK":
            raise CoreDAQError(f"HUM? failed: {val}")
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad HUM format: '{val}'")

    def get_die_temperature_C(self) -> float:
        st, val = self._ask("DIE_TEMP?")
        if st != "OK":
            raise CoreDAQError(f"DIE_TEMP? failed: {val}")
        try:
            return float(val)
        except ValueError:
            raise CoreDAQError(f"Bad DIE_TEMP format: '{val}'")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def stream_write_address(self) -> int:
        st, p = self._ask("ADDR?")
        if st != "OK":
            raise CoreDAQError(f"ADDR? failed: {p}")
        return int(p, 0)

    def soft_reset(self) -> None:
        st, p = self._ask("SOFTRESET")
        if st != "OK":
            raise CoreDAQError(f"SOFTRESET failed: {p}")

    def enter_dfu(self) -> None:
        self._transport.drain()
        self._ask("DFU")
