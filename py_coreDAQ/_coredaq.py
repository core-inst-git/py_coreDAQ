"""py_coreDAQ — coreDAQ class, dataclasses, and ChannelProxy.

All device state and firmware I/O live here; _driver.py and _device.py are
gone.  Two private primitives drive every read path:

    _raw_adc(n)                  → (codes[4], gains[4])   one firmware round-trip
    _adc_to_unit(ch, code, g, u) → float | int            pure math, no I/O
"""
from __future__ import annotations

import bisect
import math
import re
import struct
import time
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from ._exceptions import (
    CoreDAQError,
    coreDAQCalibrationError,
    coreDAQConnectionError,
    coreDAQError,
    coreDAQTimeoutError,
    coreDAQUnsupportedError,
)
from ._transport import SerialTransport, Transport

# ---------------------------------------------------------------------------
# ADC constants (AD7606, ±5 V full scale, 16-bit)
# ---------------------------------------------------------------------------

_ADC_LSB_V: float = (2.0 * 5.0) / 65536          # ≈ 0.0001526 V
_ADC_LSB_MV: float = _ADC_LSB_V * 1000.0
_SDRAM_BYTES: int = 32 * 1024 * 1024

# ---------------------------------------------------------------------------
# Autorange thresholds
# ---------------------------------------------------------------------------

_AR_MIN_MV: float = 50.0
_AR_MAX_V: float = 4.0
_AR_MIN_CODE: int = int(math.ceil(_AR_MIN_MV / _ADC_LSB_MV))   # ≈ 328
_AR_MAX_CODE: int = int(math.floor(_AR_MAX_V / _ADC_LSB_V))    # ≈ 26214
_AR_MAX_ITERS: int = 4
_AR_SETTLE_S: float = 0.005

# ---------------------------------------------------------------------------
# Signal health thresholds
# ---------------------------------------------------------------------------

_OVER_RANGE_V: float = 4.2
_UNDER_RANGE_MV: float = 5.0

# ---------------------------------------------------------------------------
# Gain / range tables
# ---------------------------------------------------------------------------

_GAIN_LABELS: list[str] = [
    "5 mW", "1 mW", "500 uW", "100 uW", "50 uW", "10 uW", "5 uW", "500 nW",
]
_GAIN_MAX_W: list[float] = [5e-3, 1e-3, 500e-6, 100e-6, 50e-6, 10e-6, 5e-6, 500e-9]

_GAIN_LABELS_LEGACY: list[str] = [
    "3.5 mW", "1.5 mW", "750 uW", "350 uW", "75 uW", "35 uW", "3.5 uW", "350 nW",
]
_GAIN_MAX_W_LEGACY: list[float] = [
    3.5e-3, 1.5e-3, 750e-6, 350e-6, 75e-6, 35e-6, 3.5e-6, 350e-9,
]

# ---------------------------------------------------------------------------
# Silicon log-amp model constants
# ---------------------------------------------------------------------------

_SI_LOG_VY: float = 0.5       # V per decade
_SI_LOG_IZ: float = 100e-12   # A

# ---------------------------------------------------------------------------
# InGaAs LOG power clamping
# ---------------------------------------------------------------------------

_INGAAS_LOG_MAX_W: float = 3e-3
_INGAAS_LOG_MIN_W: float = 1e-9

# ---------------------------------------------------------------------------
# Wavelength limits
# ---------------------------------------------------------------------------

_INGAAS_WL_RANGE: tuple[float, float] = (910.0, 1700.0)
_SILICON_WL_RANGE: tuple[float, float] = (400.0, 1100.0)
_RESP_REF_NM: float = 1550.0   # calibration reference wavelength

# ---------------------------------------------------------------------------
# Built-in responsivity curves (module-level, parsed once at import)
# ---------------------------------------------------------------------------

_RESP_POINTS: dict[str, list[tuple[float, float]]] = {
    "INGAAS": [
        (910.0, 0.37), (920.0, 0.423), (930.0, 0.508), (940.0, 0.569), (950.0, 0.602),
        (960.0, 0.623), (970.0, 0.657), (980.0, 0.694), (990.0, 0.712), (1000.0, 0.724),
        (1010.0, 0.732), (1020.0, 0.738), (1030.0, 0.741), (1040.0, 0.742), (1050.0, 0.749),
        (1060.0, 0.755), (1070.0, 0.765), (1080.0, 0.775), (1090.0, 0.795), (1100.0, 0.814),
        (1110.0, 0.845), (1120.0, 0.856), (1130.0, 0.863), (1140.0, 0.868), (1150.0, 0.856),
        (1160.0, 0.855), (1170.0, 0.848), (1180.0, 0.85),  (1190.0, 0.857), (1200.0, 0.866),
        (1210.0, 0.872), (1220.0, 0.879), (1230.0, 0.888), (1240.0, 0.899), (1250.0, 0.91),
        (1260.0, 0.921), (1270.0, 0.929), (1280.0, 0.938), (1290.0, 0.944), (1300.0, 0.95),
        (1310.0, 0.954), (1320.0, 0.954), (1330.0, 0.954), (1340.0, 0.953), (1350.0, 0.95),
        (1360.0, 0.947), (1370.0, 0.946), (1380.0, 0.946), (1390.0, 0.945), (1400.0, 0.946),
        (1410.0, 0.933), (1420.0, 0.937), (1430.0, 0.944), (1440.0, 0.949), (1450.0, 0.958),
        (1460.0, 0.966), (1470.0, 0.972), (1480.0, 0.978), (1490.0, 0.985), (1500.0, 0.992),
        (1510.0, 0.997), (1520.0, 0.997), (1530.0, 0.997), (1540.0, 0.994), (1550.0, 0.99),
        (1560.0, 0.984), (1570.0, 0.98),  (1580.0, 0.978), (1590.0, 0.971), (1600.0, 0.961),
        (1610.0, 0.943), (1620.0, 0.921), (1630.0, 0.898), (1640.0, 0.877), (1650.0, 0.852),
        (1660.0, 0.806), (1670.0, 0.652), (1680.0, 0.41),  (1690.0, 0.239), (1700.0, 0.145),
    ],
    "SILICON": [
        (400.0, 0.0918),  (410.0, 0.103),   (420.0, 0.1226),  (430.0, 0.1418),
        (440.0, 0.1539),  (450.0, 0.1658),  (460.0, 0.1760),  (470.0, 0.1693),
        (480.0, 0.1879),  (490.0, 0.1891),  (500.0, 0.2079),  (510.0, 0.2089),
        (520.0, 0.2093),  (530.0, 0.2175),  (540.0, 0.2277),  (550.0, 0.2432),
        (560.0, 0.2559),  (570.0, 0.2723),  (580.0, 0.2885),  (590.0, 0.3060),
        (600.0, 0.3240),  (610.0, 0.3421),  (620.0, 0.3612),  (630.0, 0.3792),
        (640.0, 0.3969),  (650.0, 0.4097),  (660.0, 0.4313),  (670.0, 0.4417),
        (680.0, 0.4538),  (690.0, 0.4635),  (700.0, 0.4746),  (710.0, 0.4871),
        (720.0, 0.4964),  (730.0, 0.4932),  (740.0, 0.5242),  (750.0, 0.5251),
        (760.0, 0.5275),  (770.0, 0.5288),  (780.0, 0.5326),  (790.0, 0.5508),
        (800.0, 0.5600),  (810.0, 0.5636),  (820.0, 0.5642),  (830.0, 0.5653),
        (840.0, 0.5676),  (850.0, 0.5685),  (860.0, 0.5699),  (870.0, 0.5703),
        (880.0, 0.5804),  (890.0, 0.5853),  (900.0, 0.5906),  (910.0, 0.5959),
        (920.0, 0.5979),  (930.0, 0.5985),  (940.0, 0.6021),  (950.0, 0.5988),
        (960.0, 0.5969),  (970.0, 0.5874),  (980.0, 0.5742),  (990.0, 0.5590),
        (1000.0, 0.5491), (1010.0, 0.5256), (1020.0, 0.4838), (1030.0, 0.4346),
        (1040.0, 0.3743), (1050.0, 0.3049), (1060.0, 0.2410), (1070.0, 0.1870),
        (1080.0, 0.1560), (1090.0, 0.1210), (1100.0, 0.0662),
    ],
}


def _build_resp_curves() -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    nm: dict[str, list[float]] = {}
    aw: dict[str, list[float]] = {}
    for det, pts in _RESP_POINTS.items():
        clean = sorted({p[0]: p[1] for p in pts if p[0] > 0 and p[1] > 0}.items())
        nm[det] = [p[0] for p in clean]
        aw[det] = [p[1] for p in clean]
    return nm, aw


_RESP_NM, _RESP_AW = _build_resp_curves()


# ---------------------------------------------------------------------------
# Module-level helpers (pure math, no device state)
# ---------------------------------------------------------------------------

def _interp_resp(detector: str, wavelength_nm: float) -> float:
    """Linear interpolation into the built-in responsivity curve."""
    xs = _RESP_NM[detector]
    ys = _RESP_AW[detector]
    x = float(wavelength_nm)
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    j = bisect.bisect_left(xs, x)
    x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
    return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0)) if x1 != x0 else float(y0)


def _interp_lut(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation (with linear extrapolation) for LOG LUT."""
    if len(xs) == 1:
        return float(ys[0])
    if x <= xs[0]:
        x0, x1, y0, y1 = xs[0], xs[1], ys[0], ys[1]
        return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0)) if x1 != x0 else float(y0)
    if x >= xs[-1]:
        x0, x1, y0, y1 = xs[-2], xs[-1], ys[-2], ys[-1]
        return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0)) if x1 != x0 else float(y1)
    j = bisect.bisect_left(xs, x)
    x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
    return float(y0 + (x - x0) / (x1 - x0) * (y1 - y0)) if x1 != x0 else float(y0)


def _power_decimals(step_w: float) -> int:
    if not math.isfinite(step_w) or step_w <= 0.0:
        return 0
    return max(0, min(12, round(-math.log10(step_w))))


def _quantize(value: float, step: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if not math.isfinite(step) or step <= 0.0:
        return value
    return round(value / step) * step


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceInfo:
    """Snapshot of device identity."""
    raw_idn: str
    frontend: str
    detector: str
    gain_profile: str
    port: str


@dataclass(frozen=True)
class SignalStatus:
    """Signal health for one channel."""
    channel: int
    signal_v: float
    signal_mv: float
    over_range: bool
    under_range: bool
    is_clipped: bool


@dataclass(frozen=True)
class ChannelReading:
    """Full measurement result for one channel."""
    channel: int
    value: Union[int, float]
    unit: str
    power_w: float
    power_dbm: float
    signal_v: float
    signal_mv: float
    adc_code: int
    range_index: Optional[int]
    range_label: Optional[str]
    wavelength_nm: float
    detector: str
    frontend: str
    zero_source: str
    over_range: bool
    under_range: bool
    is_clipped: bool


@dataclass(frozen=True)
class MeasurementSet:
    """All four channel readings from a single read_all_full() call."""
    readings: Tuple[ChannelReading, ...]
    unit: str

    def __iter__(self) -> Iterator[ChannelReading]:
        return iter(self.readings)

    def __len__(self) -> int:
        return len(self.readings)

    def __getitem__(self, item: int) -> ChannelReading:
        return self.readings[item]

    def channel(self, channel: int) -> ChannelReading:
        key = int(channel)
        for r in self.readings:
            if r.channel == key:
                return r
        raise ValueError(f"channel {channel} not present in this MeasurementSet")

    def values(self) -> List[Union[int, float]]:
        return [r.value for r in self.readings]


@dataclass(frozen=True)
class CaptureLayout:
    """Active channel mask and frame geometry."""
    mask: int
    enabled_channels: Tuple[int, ...]
    frame_bytes: int


@dataclass(frozen=True)
class CaptureChannelStatus:
    """Clip/range statistics for one channel in a CaptureResult."""
    channel: int
    any_over_range: bool
    any_under_range: bool
    any_clipped: bool
    over_range_samples: int
    under_range_samples: int
    clipped_samples: int
    peak_signal_v: float


@dataclass(frozen=True)
class CaptureResult:
    """Block-capture result from capture()."""
    traces: Dict[int, List[Union[int, float]]]
    statuses: Dict[int, CaptureChannelStatus]
    unit: str
    sample_rate_hz: int
    enabled_channels: Tuple[int, ...]
    ranges: Dict[int, Optional[int]]
    range_labels: Dict[int, Optional[str]]
    wavelength_nm: float
    detector: str
    frontend: str

    def trace(self, channel: int) -> List[Union[int, float]]:
        key = int(channel)
        if key not in self.traces:
            raise ValueError(f"channel {channel} not present in this capture")
        return self.traces[key]

    def status(self, channel: int) -> CaptureChannelStatus:
        key = int(channel)
        if key not in self.statuses:
            raise ValueError(f"channel {channel} not present in this capture")
        return self.statuses[key]


# ---------------------------------------------------------------------------
# ChannelProxy
# ---------------------------------------------------------------------------


class ChannelProxy:
    """Channel-scoped view into a coreDAQ device.

    Do not instantiate directly — use ``coredaq.channels[n]``::

        ch = coredaq.channels[0]
        print(ch.power_w)
    """

    def __init__(self, meter: "coreDAQ", channel: int) -> None:
        self._meter = meter
        self._channel = int(channel)

    @property
    def power_w(self) -> float:
        """Live optical power in watts (triggers a single read)."""
        return float(self._meter.read_channel(self._channel, unit="w"))

    def read(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> Union[int, float]:
        return self._meter.read_channel(self._channel, unit=unit, autoRange=autoRange, n_samples=n_samples)

    def read_full(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> ChannelReading:
        return self._meter.read_channel_full(self._channel, unit=unit, autoRange=autoRange, n_samples=n_samples)

    @property
    def range(self) -> Optional[int]:
        """Current TIA gain range index (0..7), or None on LOG frontends."""
        return self._meter.get_range(self._channel)

    def set_range(self, range_index: int) -> None:
        self._meter.set_range(self._channel, range_index)

    def set_range_power(self, power_w: float) -> int:
        return self._meter.set_range_power(self._channel, power_w)

    def signal_status(self) -> SignalStatus:
        return self._meter.signal_status(self._channel)  # type: ignore[return-value]

    def is_clipped(self) -> bool:
        return bool(self._meter.is_clipped(self._channel))

    def __repr__(self) -> str:
        return f"<ChannelProxy ch={self._channel}>"


# ---------------------------------------------------------------------------
# coreDAQ
# ---------------------------------------------------------------------------

_VALID_UNITS = ("w", "dbm", "v", "mv", "adc")
_UNIT_ALIASES: dict[str, str] = {
    "w": "w", "watt": "w", "watts": "w",
    "dbm": "dbm",
    "v": "v", "volt": "v", "volts": "v",
    "mv": "mv", "millivolt": "mv", "millivolts": "mv",
    "adc": "adc", "raw": "adc", "raw_adc": "adc", "adccode": "adc", "adc_code": "adc",
}


class coreDAQ:
    """Python driver for the coreDAQ 4-channel optical power meter.

    Preferred entry point::

        with coreDAQ.connect() as coredaq:
            print(coredaq.read_all())

        with coreDAQ.connect(simulator=True) as coredaq:
            result = coredaq.capture(frames=500)

    Direct construction (when you already know the port)::

        coredaq = coreDAQ("/dev/tty.usbmodem1")
        print(coredaq.read_channel(0))
        coredaq.close()
    """

    MAX_READ_SAMPLES: int = 32
    DEFAULT_SAMPLE_RATE_HZ: int = 500
    DEFAULT_OVERSAMPLING: int = 1

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        port: str,
        timeout: float = 0.15,
        inter_command_gap_s: float = 0.0,
    ) -> None:
        transport = SerialTransport(
            port, baudrate=115200, timeout=timeout,
            inter_command_gap_s=inter_command_gap_s,
        )
        self._init_from_transport(transport)

    def _init_from_transport(self, transport: Any) -> None:
        self._transport = transport
        try:
            self._detect_variant()
            self._load_calibration()
            self._reading_unit: str = "w"
            self._wavelength_nm: float = 1550.0
            self._zero_source: str = (
                "factory" if self._frontend == "LINEAR" else "not_applicable"
            )
            self._transport.ask("I2C REFRESH")  # warm I2C sensors; ignore failure
            self._apply_defaults()
        except CoreDAQError as exc:
            raise coreDAQConnectionError(str(exc)) from exc

    def _apply_defaults(self) -> None:
        self._ask(f"OS {self.DEFAULT_OVERSAMPLING}")
        self._ask(f"FREQ {self.DEFAULT_SAMPLE_RATE_HZ}")

    @classmethod
    def connect(
        cls,
        port: Optional[str] = None,
        *,
        simulator: bool = False,
        baudrate: int = 115200,
        timeout: float = 0.15,
        inter_command_gap_s: float = 0.0,
        **sim_kwargs: Any,
    ) -> "coreDAQ":
        """Connect to a coreDAQ power meter.

        Parameters
        ----------
        port : str or None
            Serial port path. ``None`` auto-discovers via ``discover()``.
        simulator : bool
            Return a fully functional simulated device (no hardware needed).
            Extra keyword arguments are forwarded to SimTransport:
            ``frontend``, ``detector``, ``incident_power_w``,
            ``wavelength_nm``, ``noise_sigma_adc``, ``seed``.
        """
        instance = object.__new__(cls)
        if simulator:
            from ._simulator import SimTransport
            transport: Any = SimTransport(**sim_kwargs)
        elif port is not None:
            transport = SerialTransport(
                port, baudrate=baudrate, timeout=timeout,
                inter_command_gap_s=inter_command_gap_s,
            )
        else:
            ports = SerialTransport.find_ports(baudrate=baudrate, timeout=timeout)
            if not ports:
                raise coreDAQConnectionError(
                    "No coreDAQ device found. Check the USB-C cable and serial permissions."
                )
            if len(ports) > 1:
                raise coreDAQConnectionError(
                    f"Multiple coreDAQ devices found: {ports}. "
                    "Pass port= explicitly to select one."
                )
            transport = SerialTransport(
                ports[0], baudrate=baudrate, timeout=timeout,
                inter_command_gap_s=inter_command_gap_s,
            )
        instance._init_from_transport(transport)
        return instance

    @staticmethod
    def discover(baudrate: int = 115200, timeout: float = 0.15) -> List[str]:
        """Return serial port paths of all connected coreDAQ devices."""
        try:
            return SerialTransport.find_ports(baudrate=baudrate, timeout=timeout)
        except Exception as exc:
            raise coreDAQError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "coreDAQ":
        return self

    def __exit__(self, et: Any, ev: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Release the serial port (or simulator)."""
        self._transport.close()

    # ------------------------------------------------------------------
    # Device variant detection
    # ------------------------------------------------------------------

    def _detect_variant(self) -> None:
        time.sleep(0.05)
        self._transport.drain()

        st, p = self._transport.ask("HEAD_TYPE?")
        if st != "OK":
            raise CoreDAQError(f"HEAD_TYPE? failed: {p}")
        txt = p.strip().upper().replace(" ", "")
        if "TYPE=LOG" in txt:
            self._frontend = "LOG"
        elif "TYPE=LINEAR" in txt:
            self._frontend = "LINEAR"
        else:
            raise CoreDAQError(f"Unexpected HEAD_TYPE? reply: {p!r}")

        st, p = self._transport.ask("IDN?")
        if st != "OK":
            raise CoreDAQError(f"IDN? failed: {p}")
        self._idn_cache: str = p
        self._gain_profile: str = self._parse_gain_profile(p)

        txt_idn = p.upper()
        if "INGAAS" in txt_idn:
            self._detector = "INGAAS"
        elif "SILICON" in txt_idn:
            self._detector = "SILICON"
        else:
            toks = re.split(r"[^A-Z0-9]+", txt_idn)
            self._detector = "SILICON" if "SI" in toks else "INGAAS"

    def _parse_gain_profile(self, idn: str) -> str:
        txt = idn.upper()
        if self._frontend == "LINEAR" and (
            "LINEAR_LEGACY" in txt or ("LINEAR" in txt and "LEGACY" in txt)
        ):
            return "linear_legacy"
        return "standard"

    # ------------------------------------------------------------------
    # Calibration loading
    # ------------------------------------------------------------------

    def _load_calibration(self) -> None:
        # Calibration state (defaults suitable for LOG / Silicon)
        self._cal_slope: list[list[float]] = [[0.0] * 8 for _ in range(4)]
        self._cal_intercept: list[list[float]] = [[0.0] * 8 for _ in range(4)]
        self._zero: list[int] = [0, 0, 0, 0]
        self._factory_zero: list[int] = [0, 0, 0, 0]
        self._lut_v_v: Optional[list[list[float]]] = None
        self._lut_log10p: Optional[list[list[float]]] = None

        # Silicon TIA defaults (derived from standard gain table at 1.0 A/W)
        self._silicon_tia: list[list[float]] = [
            [5.0 / pw for pw in _GAIN_MAX_W] for _ in range(4)
        ]

        if self._detector != "SILICON":
            if self._frontend == "LINEAR":
                self._load_linear_cal()
                self._load_factory_zeros()
            else:
                self._load_log_cal()

        # Bootstrap silicon TIA from InGaAs slope at reference wavelength
        if self._frontend == "LINEAR":
            r_ref = _interp_resp("INGAAS", _RESP_REF_NM)
            if math.isfinite(r_ref) and r_ref > 0:
                for ch in range(4):
                    for g in range(8):
                        s = self._cal_slope[ch][g]
                        if math.isfinite(s) and s != 0.0:
                            tia = abs(s) / (1000.0 * r_ref)
                            if math.isfinite(tia) and tia > 0:
                                self._silicon_tia[ch][g] = tia

    def _load_linear_cal(self) -> None:
        for head in range(1, 5):
            for gain in range(8):
                st, payload = self._transport.ask(f"CAL {head} {gain}")
                if st != "OK":
                    raise coreDAQCalibrationError(f"CAL {head} {gain} failed: {payload}")
                slope_hex = intercept_hex = None
                for tok in payload.split():
                    if tok.startswith("S="):
                        slope_hex = tok[2:]
                    elif tok.startswith("I="):
                        intercept_hex = tok[2:]
                if slope_hex is None or intercept_hex is None:
                    raise coreDAQCalibrationError(f"Missing S= or I= in CAL reply: {payload!r}")
                try:
                    s = struct.unpack("<f", int(slope_hex, 16).to_bytes(4, "little"))[0]
                    i = struct.unpack("<f", int(intercept_hex, 16).to_bytes(4, "little"))[0]
                except Exception as exc:
                    raise coreDAQCalibrationError(f"Bad CAL payload {payload!r}") from exc
                self._cal_slope[head - 1][gain] = float(s)
                self._cal_intercept[head - 1][gain] = float(i)

    def _load_factory_zeros(self) -> None:
        st, payload = self._transport.ask("FACTORY_ZEROS?")
        if st != "OK":
            raise coreDAQCalibrationError(f"FACTORY_ZEROS? failed: {payload}")
        parts = payload.split()
        if len(parts) < 4:
            raise coreDAQCalibrationError(f"FACTORY_ZEROS? payload too short: {payload!r}")
        if any("=" in t for t in parts):
            kv = {t.split("=")[0].strip().lower(): t.split("=")[1].strip() for t in parts if "=" in t}
            try:
                z = [int(kv["h1"], 0), int(kv["h2"], 0), int(kv["h3"], 0), int(kv["h4"], 0)]
            except Exception as exc:
                raise coreDAQCalibrationError(f"FACTORY_ZEROS? parse error: {payload!r}") from exc
        else:
            try:
                z = [int(parts[i], 0) for i in range(4)]
            except Exception as exc:
                raise coreDAQCalibrationError(f"FACTORY_ZEROS? parse error: {payload!r}") from exc
        self._zero = list(z)
        self._factory_zero = list(z)

    def _load_log_cal(self) -> None:
        lut_v: list[list[float]] = []
        lut_lp: list[list[float]] = []
        for head in range(1, 5):
            v_mv_list, log10p_q16_list = self._transport.logcal(head)
            if not v_mv_list:
                raise coreDAQCalibrationError(f"LOG LUT empty for head {head}")
            lut_v.append([v / 1000.0 for v in v_mv_list])
            lut_lp.append([q / 65536.0 for q in log10p_q16_list])
        self._lut_v_v = lut_v
        self._lut_log10p = lut_lp

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------

    def _ask(self, cmd: str) -> tuple[str, str]:
        return self._transport.ask(cmd)

    def _ask_busy(self, cmd: str) -> tuple[str, str]:
        return self._transport.ask_with_busy_retry(cmd, retries=20, delay_s=0.05)

    def _port_name(self) -> str:
        fn = getattr(self._transport, "port_name", None)
        if callable(fn):
            return fn()
        ser = getattr(self._transport, "_ser", None)
        return str(getattr(ser, "port", "")) if ser is not None else ""

    # ------------------------------------------------------------------
    # Core primitive 1: _raw_adc
    # ------------------------------------------------------------------

    def _raw_adc(self, n: int = 1) -> tuple[list[int], list[int]]:
        """Send SNAP n, poll SNAP?, return (codes[4], gains[4])."""
        st, _ = self._transport.ask(f"SNAP {n}")
        if st != "OK":
            raise coreDAQError(f"SNAP {n} failed")

        timeout_s = max(1.0, n * 0.1)
        t0 = time.time()
        while True:
            st, payload = self._transport.ask("SNAP?")
            if st == "BUSY":
                if (time.time() - t0) > timeout_s:
                    raise coreDAQTimeoutError(
                        "Device busy — averaging in progress. "
                        "Reduce n_samples or wait for the current read to finish."
                    )
                time.sleep(0.005)
                continue
            if st != "OK":
                raise coreDAQError(f"SNAP? failed: {payload}")

            parts = payload.split()
            if len(parts) < 4:
                raise coreDAQError(f"SNAP? payload too short: {payload!r}")
            try:
                codes = [int(parts[i]) for i in range(4)]
            except ValueError as exc:
                raise coreDAQError(f"Cannot parse ADC codes from SNAP?: {payload!r}") from exc

            gains = [0, 0, 0, 0]
            for i, part in enumerate(parts):
                if "G=" in part:
                    try:
                        gains[0] = int(part.split("=")[1])
                        gains[1] = int(parts[i + 1])
                        gains[2] = int(parts[i + 2])
                        gains[3] = int(parts[i + 3])
                    except (ValueError, IndexError) as exc:
                        raise coreDAQError(f"Cannot parse gains from SNAP?: {payload!r}") from exc
                    break
            return codes, gains

    def _raw_adc_auto(
        self, n: int, autorange_channels: tuple[int, ...]
    ) -> tuple[list[int], list[int]]:
        """Like _raw_adc, but first autoranges the listed channels (LINEAR only)."""
        if not autorange_channels or self._frontend != "LINEAR":
            return self._raw_adc(n)

        limits = _GAIN_MAX_W_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_MAX_W

        for _ in range(_AR_MAX_ITERS):
            codes, gains = self._raw_adc(n)
            pending: dict[int, int] = {}
            for ch in autorange_channels:
                zeroed_abs = abs(codes[ch] - self._zero[ch])
                desired = self._choose_gain(zeroed_abs, gains[ch], limits)
                if desired != gains[ch]:
                    pending[ch] = desired
            if not pending:
                return codes, gains
            for ch, g in pending.items():
                self._set_gain_hw(ch, g)
            time.sleep(_AR_SETTLE_S)

        codes, gains = self._raw_adc(n)
        return codes, gains

    def _choose_gain(self, code_abs: int, current_gain: int, limits: list[float]) -> int:
        current_limit = limits[current_gain]
        fitting = [
            idx for idx, lim in enumerate(limits)
            if lim > 0 and _AR_MIN_CODE <= code_abs * (current_limit / lim) <= _AR_MAX_CODE
        ]
        if fitting:
            return max(fitting)

        predictions = [
            (idx, code_abs * (current_limit / lim)) for idx, lim in enumerate(limits) if lim > 0
        ]
        if not predictions:
            return current_gain

        # signal too weak even at max gain → stay at max gain
        if predictions[-1][1] < _AR_MIN_CODE:
            return predictions[-1][0]
        # signal too strong even at min gain → stay at min gain
        if predictions[0][1] > _AR_MAX_CODE:
            return predictions[0][0]

        # mixed: minimize distance to in-range, prefer higher gain for ties
        def dist(item: tuple[int, float]) -> tuple[float, int]:
            idx, pc = item
            d = (_AR_MIN_CODE - pc) if pc < _AR_MIN_CODE else (pc - _AR_MAX_CODE)
            return (d, -idx)

        return min(predictions, key=dist)[0]

    def _set_gain_hw(self, channel: int, gain: int) -> None:
        """Send GAIN command for one channel (head = channel + 1)."""
        st, p = self._transport.ask(f"GAIN {channel + 1} {gain}")
        if st != "OK":
            raise coreDAQError(f"GAIN {channel + 1} {gain} failed: {p}")
        time.sleep(0.05)

    def _get_firmware_gains(self) -> tuple[int, int, int, int]:
        if self._frontend != "LINEAR":
            return (0, 0, 0, 0)
        st, payload = self._transport.ask("GAINS?")
        if st != "OK":
            raise coreDAQError(f"GAINS? failed: {payload}")
        parts = payload.replace("HEAD", "").replace("=", " ").split()
        try:
            nums = [int(parts[i]) for i in range(1, len(parts), 2)]
            if len(nums) != 4:
                raise ValueError
            return tuple(nums)  # type: ignore[return-value]
        except Exception:
            raise coreDAQError(f"Unexpected GAINS? payload: {payload!r}")

    # ------------------------------------------------------------------
    # Core primitive 2: _adc_to_unit
    # ------------------------------------------------------------------

    def _adc_to_unit(
        self,
        ch: int,
        zeroed_code: int,
        gain: int,
        unit: str,
    ) -> Union[int, float]:
        """Convert one zero-corrected ADC code to the requested unit. No I/O."""
        if unit == "adc":
            return int(zeroed_code)

        signal_v = float(zeroed_code) * _ADC_LSB_V
        signal_mv = round(signal_v * 1000.0, 3)

        if unit == "v":
            return signal_v
        if unit == "mv":
            return signal_mv

        p_w = self._to_power_w(ch, gain, zeroed_code, signal_v, signal_mv)

        if unit == "w":
            return p_w
        if unit == "dbm":
            return 10.0 * math.log10(max(p_w, 1e-15) * 1000.0)
        raise ValueError(f"Unknown unit {unit!r}")

    def _to_power_w(
        self, ch: int, gain: int, zeroed_code: int, signal_v: float, signal_mv: float
    ) -> float:
        if self._frontend == "LINEAR":
            return self._linear_to_power_w(ch, gain, signal_mv)
        return self._log_to_power_w(ch, signal_v)

    def _linear_to_power_w(self, ch: int, gain: int, signal_mv: float) -> float:
        if self._detector == "SILICON":
            resp = _interp_resp("SILICON", self._wavelength_nm)
            tia = self._silicon_tia[ch][gain]
            if resp <= 0.0 or tia <= 0.0:
                raise coreDAQError(f"Invalid silicon model for ch {ch} gain {gain}")
            power_lsb = _ADC_LSB_V / abs(tia * resp)
            p_w = (signal_mv / 1000.0) / (tia * resp)
            return round(_quantize(p_w, power_lsb), _power_decimals(power_lsb))

        slope = self._cal_slope[ch][gain]
        if slope == 0.0:
            raise coreDAQError(f"Zero calibration slope for ch {ch} gain {gain}")
        power_lsb = _ADC_LSB_MV / abs(slope)
        p_w = signal_mv / slope
        corr = self._resp_correction()
        p_w *= corr
        power_lsb *= max(0.0, corr)
        return round(_quantize(p_w, power_lsb), _power_decimals(power_lsb))

    def _log_to_power_w(self, ch: int, signal_v: float) -> float:
        if self._detector == "SILICON":
            resp = _interp_resp("SILICON", self._wavelength_nm)
            if resp <= 0.0:
                raise coreDAQError("Invalid silicon responsivity")
            return float((_SI_LOG_IZ / resp) * (10.0 ** (signal_v / _SI_LOG_VY)))

        if self._lut_v_v is None or self._lut_log10p is None:
            raise coreDAQError("LOG LUT not loaded")
        xs = self._lut_v_v[ch]
        ys = self._lut_log10p[ch]
        if not xs:
            raise coreDAQError(f"LOG LUT empty for ch {ch}")
        p_w = 10.0 ** _interp_lut(xs, ys, signal_v)
        p_w *= self._resp_correction()
        return float(min(max(p_w, _INGAAS_LOG_MIN_W), _INGAAS_LOG_MAX_W))

    def _resp_correction(self) -> float:
        """Responsivity correction factor: resp(ref) / resp(current wavelength)."""
        try:
            r_ref = _interp_resp("INGAAS", _RESP_REF_NM)
            r_now = _interp_resp("INGAAS", self._wavelength_nm)
        except Exception:
            return 1.0
        if r_now <= 0.0 or not math.isfinite(r_now):
            return 1.0
        return max(0.0, r_ref / r_now)

    # ------------------------------------------------------------------
    # Input validation helpers
    # ------------------------------------------------------------------

    def _unit(self, unit: Optional[str]) -> str:
        if unit is None:
            return self._reading_unit
        tok = str(unit).strip().lower()
        normalized = _UNIT_ALIASES.get(tok)
        if normalized is None:
            raise ValueError(f"unit must be one of {', '.join(_VALID_UNITS)}")
        return normalized

    @staticmethod
    def _ch(channel: int) -> int:
        ch = int(channel)
        if ch not in (0, 1, 2, 3):
            raise ValueError("channel must be 0..3")
        return ch

    @classmethod
    def _n(cls, n_samples: int) -> int:
        v = int(n_samples)
        if not (1 <= v <= cls.MAX_READ_SAMPLES):
            raise ValueError(f"n_samples must be 1..{cls.MAX_READ_SAMPLES}")
        return v

    @staticmethod
    def _channels_arg(channels: Optional[Union[int, Sequence[int]]]) -> Optional[tuple[int, ...]]:
        if channels is None:
            return None
        if isinstance(channels, int):
            return (coreDAQ._ch(channels),)
        result = [coreDAQ._ch(c) for c in channels]
        if not result:
            raise ValueError("channels must not be empty")
        return tuple(sorted(set(result)))

    @staticmethod
    def _mask_to_channels(mask: int) -> tuple[int, ...]:
        return tuple(i for i in range(4) if mask & (1 << i))

    @staticmethod
    def _channels_to_mask(channels: Sequence[int]) -> int:
        mask = 0
        for ch in channels:
            mask |= 1 << int(ch)
        return mask

    @staticmethod
    def _parse_mask(mask: Union[int, str]) -> int:
        if isinstance(mask, str):
            tok = str(mask).strip().replace(" ", "").replace("_", "")
            if not tok:
                raise ValueError("mask must not be empty")
            tl = tok.lower()
            if tl.startswith("0b"):
                value = int(tl[2:], 2)
            elif tl.startswith("0x"):
                value = int(tl, 16)
            elif set(tok) <= {"0", "1"}:
                value = int(tok, 2)
            else:
                value = int(tok, 10)
        else:
            value = int(mask)
        if not (0 <= value <= 0x0F):
            raise ValueError("capture_channel_mask must only use bits 0..3")
        return value

    @staticmethod
    def _power_dbm(power_w: float) -> float:
        if not math.isfinite(power_w) or power_w <= 0.0:
            return float("-inf")
        return 10.0 * math.log10(power_w / 1e-3)

    @staticmethod
    def _signal_flags(signal_v: float, signal_mv: float) -> tuple[bool, bool, bool]:
        over = abs(float(signal_v)) > _OVER_RANGE_V
        under = abs(float(signal_mv)) < _UNDER_RANGE_MV
        return over, under, bool(over or under)

    def _gain_label(self, gain_index: Optional[int]) -> Optional[str]:
        if gain_index is None:
            return None
        labels = _GAIN_LABELS_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_LABELS
        idx = max(0, min(len(labels) - 1, int(gain_index)))
        return labels[idx]

    # ------------------------------------------------------------------
    # ChannelProxy access
    # ------------------------------------------------------------------

    @property
    def channels(self) -> List[ChannelProxy]:
        """Four ChannelProxy objects indexed 0..3."""
        return [ChannelProxy(self, ch) for ch in range(4)]

    # ------------------------------------------------------------------
    # Reading unit
    # ------------------------------------------------------------------

    def set_reading_unit(self, unit: str) -> None:
        """Set the default output unit for all read_* calls."""
        self._reading_unit = self._unit(unit)

    def reading_unit(self) -> str:
        """Return the current default output unit."""
        return self._reading_unit

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    def read_channel(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> Union[int, float]:
        """Read one channel; return a plain scalar value."""
        ch = self._ch(channel)
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (ch,) if autoRange else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return self._adc_to_unit(ch, codes[ch] - self._zero[ch], gains[ch], u)

    def read_all(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> List[Union[int, float]]:
        """Read all four channels; return a plain list of scalar values."""
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (0, 1, 2, 3) if autoRange else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return [
            self._adc_to_unit(ch, codes[ch] - self._zero[ch], gains[ch], u)
            for ch in range(4)
        ]

    def read_channel_full(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> ChannelReading:
        """Read one channel and return a rich measurement object."""
        ch = self._ch(channel)
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (ch,) if autoRange else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return self._make_reading(ch, codes[ch], gains[ch], u)

    def read_all_full(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> MeasurementSet:
        """Read all four channels and return a rich measurement set."""
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (0, 1, 2, 3) if autoRange else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        readings = tuple(self._make_reading(ch, codes[ch], gains[ch], u) for ch in range(4))
        return MeasurementSet(readings=readings, unit=u)

    def _make_reading(self, ch: int, raw_code: int, gain: int, unit: str) -> ChannelReading:
        zeroed = raw_code - self._zero[ch]
        signal_v = float(zeroed) * _ADC_LSB_V
        signal_mv = round(signal_v * 1000.0, 3)
        over, under, clipped = self._signal_flags(signal_v, signal_mv)

        if self._frontend == "LINEAR":
            p_w = self._linear_to_power_w(ch, gain, signal_mv)
            range_index: Optional[int] = gain
        else:
            p_w = self._log_to_power_w(ch, signal_v)
            range_index = None

        power_dbm = self._power_dbm(p_w)
        zero_source = self._zero_source if self._frontend == "LINEAR" else "not_applicable"

        if unit == "w":
            value: Union[int, float] = p_w
        elif unit == "dbm":
            value = power_dbm
        elif unit == "v":
            value = signal_v
        elif unit == "mv":
            value = signal_mv
        else:
            value = int(zeroed)

        return ChannelReading(
            channel=ch,
            value=value,
            unit=unit,
            power_w=p_w,
            power_dbm=power_dbm,
            signal_v=signal_v,
            signal_mv=signal_mv,
            adc_code=int(zeroed),
            range_index=range_index,
            range_label=self._gain_label(range_index),
            wavelength_nm=self._wavelength_nm,
            detector=self._detector,
            frontend=self._frontend,
            zero_source=zero_source,
            over_range=over,
            under_range=under,
            is_clipped=clipped,
        )

    # ------------------------------------------------------------------
    # Signal health
    # ------------------------------------------------------------------

    def signal_status(
        self, channel: Optional[int] = None
    ) -> Union[SignalStatus, List[SignalStatus]]:
        """Return signal health for one channel (int) or all channels (None)."""
        codes, _ = self._raw_adc(1)
        chs = range(4) if channel is None else (self._ch(channel),)
        statuses = []
        for ch in chs:
            zeroed = codes[ch] - self._zero[ch]
            sv = float(zeroed) * _ADC_LSB_V
            smv = round(sv * 1000.0, 3)
            over, under, clipped = self._signal_flags(sv, smv)
            statuses.append(SignalStatus(
                channel=ch,
                signal_v=sv,
                signal_mv=smv,
                over_range=over,
                under_range=under,
                is_clipped=clipped,
            ))
        if channel is not None:
            return statuses[0]
        return statuses

    def is_clipped(
        self, channel: Optional[int] = None
    ) -> Union[bool, List[bool]]:
        """Return True if the channel is over-range or under-range."""
        status = self.signal_status(channel=channel)
        if channel is None:
            return [s.is_clipped for s in status]  # type: ignore[union-attr]
        return status.is_clipped  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Capture channel mask
    # ------------------------------------------------------------------

    def _get_mask_info(self) -> tuple[int, int, int]:
        st, p = self._transport.ask("CHMASK?")
        if st != "OK":
            raise coreDAQError(f"CHMASK? failed: {p}")
        m = re.search(r"0x([0-9A-Fa-f]+)", p)
        ch_m = re.search(r"CH\s*=\s*(\d+)", p, re.IGNORECASE)
        fb_m = re.search(r"FB\s*=\s*(\d+)", p, re.IGNORECASE)
        if not m:
            raise coreDAQError(f"Unexpected CHMASK? payload: {p!r}")
        mask = int(m.group(1), 16) & 0x0F
        active = int(ch_m.group(1)) if ch_m else bin(mask).count("1")
        frame_bytes = int(fb_m.group(1)) if fb_m else active * 2
        return mask, active, frame_bytes

    def capture_layout(self) -> CaptureLayout:
        mask, _, fb = self._get_mask_info()
        return CaptureLayout(mask=mask, enabled_channels=self._mask_to_channels(mask), frame_bytes=fb)

    def capture_channel_mask(self) -> int:
        mask, _, _ = self._get_mask_info()
        return mask

    def capture_channels(self) -> tuple[int, ...]:
        return self._mask_to_channels(self.capture_channel_mask())

    def set_capture_channel_mask(self, mask: Union[int, str]) -> int:
        value = self._parse_mask(mask)
        if value == 0:
            raise ValueError("capture_channel_mask must enable at least one channel")
        st, p = self._transport.ask(f"CHMASK 0x{value:X}")
        if st != "OK":
            raise coreDAQError(f"CHMASK set failed: {p}")
        return self.capture_channel_mask()

    def set_capture_channels(self, channels: Sequence[int]) -> tuple[int, ...]:
        normalized = self._channels_arg(tuple(channels))
        if normalized is None:
            raise ValueError("channels must not be empty")
        self.set_capture_channel_mask(self._channels_to_mask(normalized))
        return self.capture_channels()

    def max_capture_frames(self, channels: Optional[Sequence[int]] = None) -> int:
        if channels is None:
            _, _, fb = self._get_mask_info()
        else:
            norm = self._channels_arg(tuple(channels))
            if not norm:
                raise ValueError("channels must not be empty")
            fb = max(2, len(norm) * 2)
        return _SDRAM_BYTES // fb

    # ------------------------------------------------------------------
    # Capture control
    # ------------------------------------------------------------------

    def arm_capture(self, frames: int, trigger: bool = False, trigger_rising: bool = True) -> None:
        """Arm the ADC for a block acquisition (does not start yet)."""
        if frames <= 0:
            raise ValueError("frames must be > 0")
        if trigger:
            pol = "R" if trigger_rising else "F"
            st, p = self._transport.ask(f"TRIGARM {frames} {pol}")
        else:
            st, p = self._transport.ask(f"ACQ ARM {frames}")
        if st != "OK":
            raise coreDAQError(f"arm_capture failed: {p}")

    def start_capture(self) -> None:
        """Start a previously armed (non-triggered) acquisition."""
        st, p = self._transport.ask("ACQ START")
        if st != "OK":
            raise coreDAQError(f"ACQ START failed: {p}")

    def stop_capture(self) -> None:
        """Abort an active acquisition."""
        self._transport.ask("ACQ STOP")

    def capture_status(self) -> str:
        """Return the current acquisition state string from the device."""
        st, p = self._transport.ask("STREAM?")
        if st != "OK":
            raise coreDAQError(f"STREAM? failed: {p}")
        return p

    def remaining_frames(self) -> int:
        """Return the number of frames still to be collected."""
        st, p = self._transport.ask("LEFT?")
        if st != "OK":
            raise coreDAQError(f"LEFT? failed: {p}")
        return int(p, 0)

    def _wait_for_completion(self, poll_s: float = 0.25, timeout_s: Optional[float] = None) -> None:
        DATA_READY = 4
        t0 = time.time()
        while True:
            st, p = self._transport.ask("STATE?")
            if st == "OK" and int(p, 0) == DATA_READY:
                return
            if timeout_s is not None and (time.time() - t0) > timeout_s:
                raise coreDAQTimeoutError("Acquisition timeout")
            time.sleep(poll_s)

    # ------------------------------------------------------------------
    # Block capture
    # ------------------------------------------------------------------

    def capture(
        self,
        frames: int,
        unit: Optional[str] = None,
        channels: Optional[Union[int, Sequence[int]]] = None,
        trigger: bool = False,
        trigger_rising: bool = True,
    ) -> CaptureResult:
        """Arm and run a block capture; return converted traces."""
        if int(frames) <= 0:
            raise ValueError("frames must be > 0")

        u = self._unit(unit)
        requested = self._channels_arg(channels)
        original_mask, _, _ = self._get_mask_info()
        original_channels = self._mask_to_channels(original_mask)
        target_channels = original_channels if requested is None else requested
        target_mask = (
            original_mask if requested is None else self._channels_to_mask(target_channels)
        )

        if requested is not None and target_mask != original_mask:
            self._transport.ask(f"CHMASK 0x{target_mask:X}")

        try:
            self.arm_capture(int(frames), trigger=trigger, trigger_rising=trigger_rising)
            if not trigger:
                self.start_capture()
            self._wait_for_completion()
            raw_traces = self._transport.read_frames(int(frames), target_mask)
        finally:
            if requested is not None and target_mask != original_mask:
                try:
                    self._transport.ask(f"CHMASK 0x{original_mask:X}")
                except Exception:
                    pass

        sample_rate = self.sample_rate_hz()
        gains = self._get_firmware_gains()

        traces: dict[int, list[Union[int, float]]] = {}
        statuses: dict[int, CaptureChannelStatus] = {}
        ranges: dict[int, Optional[int]] = {}
        range_labels: dict[int, Optional[str]] = {}

        for ch in target_channels:
            raw_codes = [int(v) for v in raw_traces[ch]]
            zero = self._zero[ch]
            gain = gains[ch]
            if self._frontend == "LINEAR":
                zeroed_codes = [c - zero for c in raw_codes]
                range_index: Optional[int] = int(gain)
            else:
                zeroed_codes = raw_codes
                range_index = None

            values, status = self._convert_capture_trace(ch, zeroed_codes, gain, range_index, u)
            traces[ch] = values
            statuses[ch] = status
            ranges[ch] = range_index
            range_labels[ch] = self._gain_label(range_index)

        return CaptureResult(
            traces=traces,
            statuses=statuses,
            unit=u,
            sample_rate_hz=sample_rate,
            enabled_channels=tuple(target_channels),
            ranges=ranges,
            range_labels=range_labels,
            wavelength_nm=self._wavelength_nm,
            detector=self._detector,
            frontend=self._frontend,
        )

    def _convert_capture_trace(
        self,
        ch: int,
        zeroed_codes: List[int],
        gain: int,
        range_index: Optional[int],
        unit: str,
    ) -> tuple[List[Union[int, float]], CaptureChannelStatus]:
        values: list[Union[int, float]] = [
            self._adc_to_unit(ch, code, gain, unit) for code in zeroed_codes
        ]

        over_s = under_s = clip_s = 0
        peak_v = 0.0
        for code in zeroed_codes:
            sv = float(code) * _ADC_LSB_V
            smv = round(sv * 1000.0, 3)
            ov, un, cl = self._signal_flags(sv, smv)
            over_s += int(ov)
            under_s += int(un)
            clip_s += int(cl)
            peak_v = max(peak_v, abs(sv))

        return values, CaptureChannelStatus(
            channel=ch,
            any_over_range=over_s > 0,
            any_under_range=under_s > 0,
            any_clipped=clip_s > 0,
            over_range_samples=over_s,
            under_range_samples=under_s,
            clipped_samples=clip_s,
            peak_signal_v=peak_v,
        )

    def capture_channel(
        self,
        channel: int,
        frames: int,
        unit: Optional[str] = None,
        trigger: bool = False,
        trigger_rising: bool = True,
    ) -> CaptureResult:
        """Capture a single channel."""
        return self.capture(
            frames=frames, unit=unit,
            channels=[self._ch(channel)],
            trigger=trigger, trigger_rising=trigger_rising,
        )

    # ------------------------------------------------------------------
    # Ranges (LINEAR only)
    # ------------------------------------------------------------------

    def _require_linear(self, method: str) -> None:
        if self._frontend != "LINEAR":
            raise coreDAQUnsupportedError(
                f"{method} is not supported on LOG frontends."
            )

    def supported_ranges(self) -> List[Dict[str, Any]]:
        """Return all range indices with labels and full-scale powers."""
        labels = _GAIN_LABELS_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_LABELS
        limits = _GAIN_MAX_W_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_MAX_W
        return [
            {"range_index": idx, "label": labels[idx], "max_power_w": limits[idx]}
            for idx in range(len(labels))
        ]

    def get_range(self, channel: int) -> Optional[int]:
        """Return the current gain range index for *channel*, or None on LOG."""
        self._ch(channel)
        if self._frontend != "LINEAR":
            return None
        return int(self._get_firmware_gains()[self._ch(channel)])

    def get_ranges(self) -> List[Optional[int]]:
        """Return current range indices for all four channels."""
        if self._frontend != "LINEAR":
            return [None, None, None, None]
        gains = self._get_firmware_gains()
        return [int(g) for g in gains]

    def set_range(self, channel: int, range_index: int) -> None:
        """Set the TIA gain range for one channel (LINEAR only)."""
        self._require_linear("set_range")
        ch = self._ch(channel)
        idx = int(range_index)
        if not (0 <= idx <= 7):
            raise ValueError("range_index must be 0..7")
        self._set_gain_hw(ch, idx)

    def set_ranges(self, range_indices: Sequence[int]) -> List[Optional[int]]:
        """Set range indices for all four channels."""
        values = [int(v) for v in range_indices]
        if len(values) != 4:
            raise ValueError("range_indices must have exactly 4 elements")
        for ch, idx in enumerate(values):
            self.set_range(ch, idx)
        return self.get_ranges()

    def set_range_power(self, channel: int, power_w: float) -> int:
        """Select the best range for a target optical power; return chosen index."""
        self._require_linear("set_range_power")
        requested = abs(float(power_w))
        if not math.isfinite(requested):
            raise ValueError("power_w must be finite")
        limits = _GAIN_MAX_W_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_MAX_W
        fitting = [idx for idx, lim in enumerate(limits) if requested <= float(lim)]
        idx = int(fitting[-1]) if fitting else 0
        self.set_range(channel, idx)
        return idx

    def set_range_powers(self, power_w_values: Sequence[float]) -> List[Optional[int]]:
        """Call set_range_power for all four channels."""
        values = [float(v) for v in power_w_values]
        if len(values) != 4:
            raise ValueError("power_w_values must have exactly 4 elements")
        for ch, pw in enumerate(values):
            self.set_range_power(ch, pw)
        return self.get_ranges()

    # ------------------------------------------------------------------
    # Zeroing (LINEAR only)
    # ------------------------------------------------------------------

    def zero_offsets_adc(self) -> tuple[int, int, int, int]:
        """Return the active zero offsets in ADC counts (CH0..CH3)."""
        return tuple(int(x) for x in self._zero)  # type: ignore[return-value]

    def factory_zero_offsets_adc(self) -> tuple[int, int, int, int]:
        """Return the factory-stored zero offsets in ADC counts."""
        return tuple(int(x) for x in self._factory_zero)  # type: ignore[return-value]

    def zero_dark(
        self, frames: int = 32, settle_s: float = 0.2
    ) -> tuple[int, int, int, int]:
        """Capture a dark baseline and set it as the active zero offset.

        Block the input (or cover the fiber end) before calling this.
        Raises ``coreDAQUnsupportedError`` on LOG frontends.
        """
        if self._frontend != "LINEAR":
            raise coreDAQUnsupportedError(
                "zero_dark() is not supported on LOG frontends."
            )
        if frames <= 0:
            raise ValueError("frames must be > 0")
        time.sleep(max(0.0, float(settle_s)))
        codes, _ = self._raw_adc(frames)
        self._zero = [int(codes[ch]) for ch in range(4)]
        self._zero_source = "user"
        return self.zero_offsets_adc()

    def restore_factory_zero(self) -> tuple[int, int, int, int]:
        """Restore the factory-stored zero offsets."""
        if self._frontend == "LINEAR":
            self._zero = list(self._factory_zero)
            self._zero_source = "factory"
        return self.zero_offsets_adc()

    # ------------------------------------------------------------------
    # Sample rate and oversampling
    # ------------------------------------------------------------------

    def set_sample_rate_hz(self, hz: int) -> None:
        """Set the ADC sample rate in Hz (1..100 000)."""
        if hz <= 0 or hz > 100_000:
            raise coreDAQError("FREQ must be 1..100000 Hz")
        st, p = self._ask_busy(f"FREQ {hz}")
        if st != "OK":
            raise coreDAQError(f"FREQ {hz} failed: {p}")

    def sample_rate_hz(self) -> int:
        """Return the current ADC sample rate in Hz."""
        st, p = self._ask_busy("FREQ?")
        if st != "OK":
            raise coreDAQError(f"FREQ? failed: {p}")
        return int(p, 0)

    def set_oversampling(self, os_idx: int) -> None:
        """Set the oversampling index (0..7)."""
        if not (0 <= os_idx <= 7):
            raise coreDAQError("OS must be 0..7")
        st, p = self._ask_busy(f"OS {os_idx}")
        if st != "OK":
            raise coreDAQError(f"OS {os_idx} failed: {p}")

    def oversampling(self) -> int:
        """Return the current oversampling index."""
        st, p = self._ask_busy("OS?")
        if st != "OK":
            raise coreDAQError(f"OS? failed: {p}")
        return int(p, 0)

    # ------------------------------------------------------------------
    # Environmental sensors
    # ------------------------------------------------------------------

    def head_temperature_c(self) -> float:
        """Return the optical head temperature in °C."""
        st, val = self._transport.ask("TEMP?")
        if st != "OK":
            raise coreDAQError(f"TEMP? failed: {val}")
        return float(val)

    def head_humidity_percent(self) -> float:
        """Return the optical head relative humidity in %."""
        st, val = self._transport.ask("HUM?")
        if st != "OK":
            raise coreDAQError(f"HUM? failed: {val}")
        return float(val)

    def die_temperature_c(self) -> float:
        """Return the MCU die temperature in °C."""
        st, val = self._transport.ask("DIE_TEMP?")
        if st != "OK":
            raise coreDAQError(f"DIE_TEMP? failed: {val}")
        return float(val)

    def refresh_device_state(self) -> None:
        """Re-read I2C sensor registers (temperature, humidity)."""
        self._transport.ask("I2C REFRESH")

    # ------------------------------------------------------------------
    # Identity and wavelength
    # ------------------------------------------------------------------

    def identify(self, refresh: bool = False) -> str:
        """Return the raw IDN string from the device."""
        if refresh or not self._idn_cache:
            st, p = self._transport.ask("IDN?")
            if st != "OK":
                raise coreDAQError(f"IDN? failed: {p}")
            self._idn_cache = p
        return self._idn_cache

    def device_info(self, refresh: bool = False) -> DeviceInfo:
        """Return a snapshot of device identity."""
        return DeviceInfo(
            raw_idn=self.identify(refresh=refresh),
            frontend=self._frontend,
            detector=self._detector,
            gain_profile=self._gain_profile,
            port=self._port_name(),
        )

    def frontend(self) -> str:
        """Return ``"LINEAR"`` or ``"LOG"``."""
        return self._frontend

    def detector(self) -> str:
        """Return ``"INGAAS"`` or ``"SILICON"``."""
        return self._detector

    def wavelength_nm(self) -> float:
        """Return the current operating wavelength in nm."""
        return self._wavelength_nm

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        """Set the operating wavelength in nm."""
        wl = float(wavelength_nm)
        if not math.isfinite(wl) or wl <= 0.0:
            raise ValueError("wavelength_nm must be > 0")
        lo, hi = _INGAAS_WL_RANGE if self._detector == "INGAAS" else _SILICON_WL_RANGE
        clamped = max(lo, min(hi, wl))
        if clamped != wl:
            warnings.warn(
                f"wavelength_nm={wl:g} is outside {self._detector} range "
                f"[{lo:g}, {hi:g}] nm; clamped to {clamped:g} nm.",
                RuntimeWarning,
                stacklevel=2,
            )
        self._wavelength_nm = clamped

    def wavelength_limits_nm(self, detector: Optional[str] = None) -> tuple[float, float]:
        """Return (min_nm, max_nm) for the detector's valid wavelength range."""
        det = (self._detector if detector is None else detector).upper()
        return _SILICON_WL_RANGE if det == "SILICON" else _INGAAS_WL_RANGE

    def responsivity_a_per_w(
        self, wavelength_nm: float, detector: Optional[str] = None
    ) -> float:
        """Return the detector responsivity (A/W) at *wavelength_nm*."""
        det = (self._detector if detector is None else detector).upper()
        if det not in _RESP_NM:
            raise coreDAQError(f"Unknown detector: {det!r}")
        return _interp_resp(det, float(wavelength_nm))

    # ------------------------------------------------------------------
    # Advanced / low-level
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Soft-reset the device firmware."""
        st, p = self._transport.ask("SOFTRESET")
        if st != "OK":
            raise coreDAQError(f"SOFTRESET failed: {p}")

    def enter_dfu_mode(self) -> None:
        """Enter DFU (firmware update) mode."""
        self._transport.drain()
        self._transport.ask("DFU")

    def capture_buffer_address(self) -> int:
        """Return the current SDRAM write address (for diagnostics)."""
        st, p = self._transport.ask("ADDR?")
        if st != "OK":
            raise coreDAQError(f"ADDR? failed: {p}")
        return int(p, 0)
