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

import numpy as np

from ._exceptions import (
    CoreDAQError,
    coreDAQCalibrationError,
    coreDAQConnectionError,
    coreDAQError,
    coreDAQTimeoutError,
    coreDAQUnsupportedError,
    coreDAQStateError,
    coreDAQLicenseError,
    error_for_payload,
)
from ._transport import SerialTransport, Transport

# ---------------------------------------------------------------------------
# ADC constants
# ---------------------------------------------------------------------------
# mk1 (F730, AD7606): ±5 V bipolar full scale, 16-bit two's-complement.
#   LSB = 10 V / 65536 ≈ 152.6 µV.
# mk2 (F746, AD7606C-16): 0-5 V unipolar full scale, 16-bit straight binary.
#   LSB = 5 V / 65536 ≈ 76.294 µV = HALF the mk1 LSB.  mV = code * 5000 / 65536.
# The per-device value lives in ``self._adc_lsb_v`` (set in _detect_variant);
# these module constants are the mk1 defaults and back the mk1 autorange
# thresholds and the public ``_ADC_LSB_V`` import.

_ADC_LSB_V: float = (2.0 * 5.0) / 65536          # mk1 ≈ 0.0001526 V
_ADC_LSB_MV: float = _ADC_LSB_V * 1000.0
_ADC_LSB_V_MK2: float = 5.0 / 65536              # mk2 ≈ 0.00007629 V (half of mk1)
_ADC_LSB_MV_MK2: float = _ADC_LSB_V_MK2 * 1000.0
_SDRAM_BYTES: int = 32 * 1024 * 1024

# TIA photodiode heads are always channels 0..3 (both generations). mk2 adds a
# 5th channel (index 4 = Analog_IN) that has no TIA / responsivity, so optical
# power is undefined there.
_TIA_HEADS: int = 4

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
_OVER_RANGE_V_MK2: float = 4.9   # unipolar 0-5 V rail, 2% headroom
_UNDER_RANGE_MV: float = 5.0

# Hard dBm floor applied to every dBm output.
# -75 dBm ≈ 32 pW, just above the ~20 pW dark floor of the instrument.
# Prevents -inf / zero-power artefacts from zeroing or sub-floor noise.
_DBM_FLOOR: float = -75.0

# dBm display precision. The LOG detector resolves 200 mV/decade (10 dB), and
# the ADC LSB is 0.15 mV, so one count ≈ (10 dB / 200 mV) × 0.15 mV ≈ 0.0075 dB.
# Reporting more than 2 decimals (0.01 dB) is below the hardware resolution.
_DBM_DECIMALS: int = 2

# ---------------------------------------------------------------------------
# Capture timing
# ---------------------------------------------------------------------------

# Added to frames/rate to give the firmware a margin to enter DONE state.
# Set to 0 in SimTransport/MockTransport so tests don't sleep.
_CAPTURE_OVERHEAD_S: float = 0.5

# Firmware acquisition state integers (STATE? command)
_ACQ_STATE_IDLE: int = 0
_ACQ_STATE_ARMED: int = 1     # armed, waiting for trigger — DMA not running
_ACQ_STATE_ACQUIRING: int = 2  # DMA active — never poll during this state
_ACQ_STATE_DONE: int = 4      # data ready in SDRAM

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
# Silicon log-amp model constants (legacy units, SN < 0020)
# ---------------------------------------------------------------------------

_SI_LOG_VY: float = 0.5       # V per decade
_SI_LOG_IZ: float = 100e-12   # A

# ---------------------------------------------------------------------------
# Nominal log-amp model — SN 0020 and up, both detectors
# ---------------------------------------------------------------------------
# From SN 0020 the log frontend follows V = 0.2 V/decade * log10(I / 10 pA).
# Used to compute power when no calibration LUT is available; a loaded LUT
# always takes precedence.

_LOG_NOMINAL_VY: float = 0.2      # V per decade
_LOG_NOMINAL_IZ: float = 10e-12   # A
_LOG_NOMINAL_MIN_SN: int = 20

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
# Display rounding — one consistent scheme for every unit.
#   W   → significant figures (spans pW…mW, so fixed decimals can't work)
#   V   → 6 decimals (1 µV)   mV → 3 decimals (1 µV)
#   dBm → _DBM_DECIMALS (set above)        adc → exact integer
# Values are physically quantized by the 16-bit ADC; rounding only trims the
# float noise that otherwise prints as "infinite decimals".
# ---------------------------------------------------------------------------
_W_SIGFIGS: int = 6
_V_DECIMALS: int = 6
_MV_DECIMALS: int = 3


def _round_w(value: float) -> float:
    """Round a power in watts to _W_SIGFIGS significant figures."""
    if not math.isfinite(value) or value == 0.0:
        return float(value)
    d = _W_SIGFIGS - 1 - int(math.floor(math.log10(abs(value))))
    return round(float(value), d)


def _round_w_array(a: np.ndarray) -> np.ndarray:
    """Vectorized _round_w over a numpy array (W → _W_SIGFIGS sig figs)."""
    a = np.asarray(a, dtype=np.float64)
    out = a.copy()
    m = np.isfinite(a) & (a != 0.0)
    if np.any(m):
        mag = np.floor(np.log10(np.abs(a[m])))
        factor = np.power(10.0, (_W_SIGFIGS - 1) - mag)
        out[m] = np.round(a[m] * factor) / factor
    return out


# ---------------------------------------------------------------------------
# Serial number parsing
# ---------------------------------------------------------------------------

def _serial_numeric(serial: str) -> Optional[int]:
    """Extract the numeric part of an instrument serial number.

    Serials appear in the wild with prefix variants — ``SN0020``, ``SNX0020``,
    and double-prefixed ``SNSN0020`` — case-insensitive. Returns ``None`` when
    no plain digit run can be extracted (e.g. simulator ``SIM0000``), in which
    case callers must not assume a hardware generation.
    """
    m = re.fullmatch(r"(?:SN)*[A-Z]?(\d+)", serial.strip().upper())
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# CALINFO? payload parser
# ---------------------------------------------------------------------------

def _parse_calinfo_payload(payload: str) -> dict:
    """Parse the key=value tokens from a CALINFO? response payload."""
    kv: dict[str, str] = {}
    for tok in payload.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            kv[k.strip().upper()] = v.strip()

    def _hex_or_int(key: str, default: int = 0) -> int:
        val = kv.get(key, str(default))
        try:
            return int(val, 16) if val.lower().startswith("0x") else int(val)
        except ValueError:
            return default

    _VARIANT_STR_TO_ID: dict[str, int] = {
        "INGAAS_LINEAR": 1, "INGAAS_LINEAR_LEGACY": 2,
        "INGAAS_LOG": 3, "SILICON_LINEAR": 4, "SILICON_LOG": 5,
    }
    variant_str = kv.get("VARIANT", "").upper()
    variant_id  = _VARIANT_STR_TO_ID.get(variant_str, 0)
    schema_str  = kv.get("SCHEMA", "").upper()

    # cal_date_unix → datetime.date (None if 0 / missing)
    _date_unix = int(kv.get("DATE", "0") or "0")
    import datetime as _dt
    _cal_date: Optional[_dt.date] = None
    if _date_unix > 0:
        try:
            _cal_date = _dt.date.fromtimestamp(_date_unix)
        except (OSError, OverflowError, ValueError):
            _cal_date = None

    return {
        "valid":                     kv.get("VALID", "0") != "0",
        "status":                    kv.get("STATUS", ""),
        "variant":                   variant_str,
        "variant_id":                variant_id,
        "schema":                    schema_str,
        "serial":                    kv.get("SN", ""),
        "calibration_wavelength_nm": float(kv.get("WL_NM", "0")),
        "num_wavelengths":           int(kv.get("NUM_WL", "1") or "1"),
        "cal_date_unix":             _date_unix,
        "cal_date":                  _cal_date,
        "slot_address":              _hex_or_int("ADDR"),
        "payload_size":              _hex_or_int("SIZE"),
        "crc32":                     _hex_or_int("CRC"),
        "raw":                       payload,
        "has_linear_table": schema_str == "LINEAR_TABLE",
        "has_log_lut":      schema_str == "LOG_LUT",
        "is_placeholder":   schema_str == "PLACEHOLDER",
        "is_silicon":       variant_id in (4, 5),
        "is_ingaas":        variant_id in (1, 2, 3),
    }


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
    traces: Dict[int, np.ndarray]
    statuses: Dict[int, CaptureChannelStatus]
    unit: str
    sample_rate_hz: int
    enabled_channels: Tuple[int, ...]
    ranges: Dict[int, Optional[int]]
    range_labels: Dict[int, Optional[str]]
    wavelength_nm: float
    detector: str
    frontend: str

    def trace(self, channel: int) -> np.ndarray:
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
        autoRange: Optional[bool] = None,
        n_samples: int = 1,
    ) -> Union[int, float]:
        return self._meter.read_channel(self._channel, unit=unit, autoRange=autoRange, n_samples=n_samples)

    def read_full(
        self,
        unit: Optional[str] = None,
        autoRange: Optional[bool] = None,
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
    """Python driver for the coreDAQ optical power meter / DAQ.

    Supports both device generations transparently: mk1 (F730, 4-channel,
    USB-only, ±5 V two's-complement) and mk2 (F746, 5-channel, USB+Ethernet,
    0-5 V straight-binary). The generation is discovered at connect time; the
    public API is identical across generations and transports.

    Preferred entry point::

        with coreDAQ.connect() as coredaq:            # USB auto-discover
            print(coredaq.read_all())

        with coreDAQ.connect(transport="ethernet",    # mk2 over TCP
                             host="192.168.1.50") as coredaq:
            print(coredaq.tier())

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

    # SN 0020 and up: LOG conversion may fall back to the nominal
    # 200 mV/decade / 10 pA model when no LUT is available. Class-level
    # defaults keep directly-constructed instances (tests) conservative.
    _log_nominal_eligible: bool = False
    _log_min_w: float = _INGAAS_LOG_MIN_W

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
            # Use the calibration image wavelength as the default operating wavelength.
            # Falls back to 850 nm for silicon and 1550 nm for InGaAs if cal is absent.
            _cal_wl = self._cal_wavelength_nm()
            _fallback = 850.0 if self._detector == "SILICON" else 1550.0
            self._wavelength_nm: float = _cal_wl if _cal_wl > 0.0 else _fallback
            self._zero_source: str = (
                "not_applicable" if self._detector == "INGAAS" and self._frontend == "LOG"
                else "factory"
            )
            self._transport.ask("I2C REFRESH")  # warm I2C sensors; ignore failure
            self._apply_defaults()
        except CoreDAQError as exc:
            raise coreDAQConnectionError(str(exc)) from exc

    def _apply_defaults(self) -> None:
        self._ask(f"OS {self.DEFAULT_OVERSAMPLING}")
        self._ask(f"FREQ {self.DEFAULT_SAMPLE_RATE_HZ}")
        self._sample_rate_hz: int = self.DEFAULT_SAMPLE_RATE_HZ
        self._armed_frames: int = 0
        self._armed_trigger: bool = False
        self._calinfo_cache: Optional[dict] = None
        self._autorange: bool = True

    @classmethod
    def connect(
        cls,
        port: Optional[str] = None,
        *,
        simulator: bool = False,
        transport: str = "usb",
        host: Optional[str] = None,
        tcp_port: int = 5025,
        baudrate: int = 115200,
        timeout: float = 0.15,
        inter_command_gap_s: float = 0.0,
        **sim_kwargs: Any,
    ) -> "coreDAQ":
        """Connect to a coreDAQ power meter.

        Everything above the transport layer — variant detection, calibration,
        unit conversion, capture/trigger — is identical across USB, Ethernet,
        mk1 and mk2. The device generation (mk1/mk2) is discovered at runtime.

        Parameters
        ----------
        port : str or None
            Serial port path (USB transport). ``None`` auto-discovers.
        simulator : bool
            Return a fully functional simulated device (no hardware needed).
            Extra keyword arguments are forwarded to SimTransport, e.g.
            ``frontend``, ``detector``, ``generation`` (``"mk1"``/``"mk2"``),
            ``incident_power_w``, ``wavelength_nm``, ``noise_sigma_adc``,
            ``seed``.
        transport : str
            ``"usb"`` (default) or ``"ethernet"`` (alias ``"tcp"``). Ethernet
            connects over TCP and requires ``host``. mk2 devices support both;
            mk1 is USB-only.
        host : str or None
            Device IP address or hostname (required for ``transport="ethernet"``).
        tcp_port : int
            TCP port for the Ethernet transport (firmware serves 5025).
        """
        instance = object.__new__(cls)
        if simulator:
            from ._simulator import SimTransport
            t: Any = SimTransport(**sim_kwargs)
        else:
            mode = str(transport).strip().lower()
            if mode in ("ethernet", "tcp", "eth"):
                from ._ethernet import EthernetTransport
                if not host:
                    raise coreDAQConnectionError(
                        "transport='ethernet' requires host=<ip or hostname>."
                    )
                try:
                    t = EthernetTransport(host, int(tcp_port), timeout=max(0.5, timeout))
                except CoreDAQError as exc:
                    raise coreDAQConnectionError(str(exc)) from exc
            elif mode == "usb":
                if port is not None:
                    t = SerialTransport(
                        port, baudrate=baudrate, timeout=timeout,
                        inter_command_gap_s=inter_command_gap_s,
                    )
                else:
                    ports = SerialTransport.find_ports(baudrate=baudrate)
                    if not ports:
                        raise coreDAQConnectionError(
                            "No coreDAQ device found. "
                            "Check the USB-C cable and serial permissions."
                        )
                    if len(ports) > 1:
                        raise coreDAQConnectionError(
                            f"Multiple coreDAQ devices found: {ports}. "
                            "Pass port= explicitly to select one."
                        )
                    t = SerialTransport(
                        ports[0], baudrate=baudrate, timeout=timeout,
                        inter_command_gap_s=inter_command_gap_s,
                    )
            else:
                raise ValueError(
                    f"transport must be 'usb' or 'ethernet', got {transport!r}"
                )
        instance._init_from_transport(t)
        return instance

    @staticmethod
    def discover(baudrate: int = 115200, timeout: float = 0.15) -> List[str]:
        """Return serial port paths of all connected coreDAQ devices."""
        try:
            return SerialTransport.find_ports(baudrate=baudrate)
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

        st, head_p = self._transport.ask("HEAD_TYPE?")
        if st != "OK":
            raise CoreDAQError(f"HEAD_TYPE? failed: {head_p}")
        head_txt = head_p.strip().upper().replace(" ", "")

        st, p = self._transport.ask("IDN?")
        if st != "OK":
            raise CoreDAQError(f"IDN? failed: {p}")
        self._idn_cache: str = p

        # Device generation from the IDN string ("Mk1" vs "Mk2"). Absence of a
        # Mk2 token means mk1 — so every legacy device (and the mk1 simulator,
        # whose IDN has no generation token) stays exactly on the mk1 path.
        idn_tokens = re.split(r"[^A-Z0-9]+", p.upper())
        is_mk2 = any(t == "MK2" or t.startswith("MK2") for t in idn_tokens)
        self._generation: str = "mk2" if is_mk2 else "mk1"
        if self._generation == "mk2":
            self._n_channels: int = 5           # ch0-3 = TIA heads, ch4 = Analog_IN
            self._chmask_max: int = 0x1F         # 5-bit channel mask
            self._adc_lsb_v: float = _ADC_LSB_V_MK2
            self._adc_unsigned: bool = True      # 0-5 V straight-binary uint16
            self._over_range_v: float = _OVER_RANGE_V_MK2
            self._signed_over: bool = True      # unipolar: over-range is signed, not abs()
        else:
            self._n_channels = 4
            self._chmask_max = 0x0F
            self._adc_lsb_v = _ADC_LSB_V
            self._adc_unsigned = False           # ±5 V two's-complement int16
            self._over_range_v = _OVER_RANGE_V
            self._signed_over = False            # bipolar: over-range is |v|

        # Frontend from HEAD_TYPE?. mk2 SHIP firmware v1.0 reports LINEAR/LOG
        # (like mk1); the pre-ship mk2 build reported TYPE=MK2 — in that case
        # fall back to the frontend token in the IDN string.
        if "TYPE=LOG" in head_txt:
            self._frontend = "LOG"
        elif "TYPE=LINEAR" in head_txt:
            self._frontend = "LINEAR"
        elif self._generation == "mk2" or "MK2" in head_txt:
            self._frontend = "LOG" if "LOG" in p.upper() else "LINEAR"
        else:
            raise CoreDAQError(f"Unexpected HEAD_TYPE? reply: {head_p!r}")

        self._gain_profile: str = self._parse_gain_profile(p)
        self._firmware_version: tuple[int, int, int] = self._parse_firmware_version(p)

        # Detector: CALINFO? variant_id is authoritative (identity lives in the cal image).
        # Fall back to IDN? string parsing if CALINFO? is unavailable or has unknown variant.
        _detector_from_cal = ""
        try:
            st_cal, cal_payload = self._transport.ask("CALINFO?")
            if st_cal == "OK":
                cal = _parse_calinfo_payload(cal_payload)
                vid = cal.get("variant_id", 0)
                if vid in (4, 5):
                    _detector_from_cal = "SILICON"
                elif vid in (1, 2, 3):
                    _detector_from_cal = "INGAAS"
        except Exception:
            pass

        if _detector_from_cal:
            self._detector = _detector_from_cal
        else:
            txt_idn = p.upper()
            if "INGAAS" in txt_idn:
                self._detector = "INGAAS"
            elif "SILICON" in txt_idn:
                self._detector = "SILICON"
            else:
                toks = re.split(r"[^A-Z0-9]+", txt_idn)
                self._detector = "SILICON" if "SI" in toks else "INGAAS"

    @staticmethod
    def _parse_firmware_version(idn: str) -> tuple[int, int, int]:
        """Extract (major, minor, patch) from IDN string, e.g. 'FW=4.1.0' or 'v4.1'."""
        m = re.search(r"(?:FW=|[Vv])(\d+)\.(\d+)(?:\.(\d+))?", idn)
        if m:
            major = int(m.group(1))
            minor = int(m.group(2))
            patch = int(m.group(3)) if m.group(3) else 0
            return (major, minor, patch)
        return (0, 0, 0)

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

    def _cal_wavelength_nm(self) -> float:
        """Return the calibration wavelength from the flash image, or 0.0 if unavailable."""
        try:
            st, payload = self._transport.ask("CALINFO?")
            if st == "OK":
                cal = _parse_calinfo_payload(payload)
                wl = float(cal.get("calibration_wavelength_nm", 0.0))
                lo, hi = (400.0, 1100.0) if self._detector == "SILICON" else (910.0, 1700.0)
                if lo <= wl <= hi:
                    return wl
        except Exception:
            pass
        return 0.0

    def _load_calibration(self) -> None:
        # Per-channel calibration state, sized to the device channel count
        # (4 on mk1, 5 on mk2). Cal only ever populates the 4 TIA heads; a mk2
        # aux channel (index 4) keeps its zero defaults and never carries a
        # slope/LUT (optical power is undefined there — see _is_tia_head).
        n = self._n_channels
        self._cal_slope: list[list[float]] = [[0.0] * 8 for _ in range(n)]
        self._cal_intercept: list[list[float]] = [[0.0] * 8 for _ in range(n)]
        self._zero: list[int] = [0] * n
        self._factory_zero: list[int] = [0] * n
        self._lut_v_v: Optional[list[list[float]]] = None
        self._lut_log10p: Optional[list[list[float]]] = None
        self._log_min_w: float = _INGAAS_LOG_MIN_W

        # Silicon TIA defaults (derived from standard gain table at 1.0 A/W)
        self._silicon_tia: list[list[float]] = [
            [5.0 / pw for pw in _GAIN_MAX_W] for _ in range(n)
        ]

        # Determine what's in the cal flash image
        _cal_schema = ""
        _cal_serial = ""
        try:
            st_cal, cal_pl = self._transport.ask("CALINFO?")
            if st_cal == "OK":
                _ci = _parse_calinfo_payload(cal_pl)
                _cal_schema = _ci.get("schema", "")
                _cal_serial = str(_ci.get("serial", ""))
        except Exception:
            pass
        _has_table = _cal_schema == "LINEAR_TABLE"
        _has_lut   = _cal_schema == "LOG_LUT"

        # SN 0020 and up (serials also seen as "SNX0020"/"SNSN0020"): the log
        # frontend follows the nominal 200 mV/decade / 10 pA model, so power
        # stays computable when no calibration LUT is available.
        _sn_num = _serial_numeric(_cal_serial)
        self._log_nominal_eligible = (
            _sn_num is not None and _sn_num >= _LOG_NOMINAL_MIN_SN
        )

        if self._frontend == "LINEAR":
            # Factory zeros: graceful — NOT_SUPPORTED means PLACEHOLDER/silicon, use [0,0,0,0]
            self._load_factory_zeros()
            if self._detector == "INGAAS":
                # InGaAs always uses measured slopes/intercepts
                self._load_linear_cal()
            elif _has_table:
                # Silicon with a real measured cal image at a specific wavelength
                self._load_linear_cal()
            # else: silicon with PLACEHOLDER — use built-in analytical TIA model
        else:  # LOG
            # InGaAs LOG with a real LUT: load via binary LOGCAL protocol.
            # Silicon LOG: always use the analytical model — silicon's logarithmic
            # response is well-described analytically; LUT support can be added later.
            if _has_lut and self._detector == "INGAAS":
                self._load_log_cal()

        # Bootstrap silicon TIA from InGaAs slope at reference wavelength
        if self._frontend == "LINEAR":
            r_ref = _interp_resp("INGAAS", _RESP_REF_NM)
            if math.isfinite(r_ref) and r_ref > 0:
                for ch in range(self._n_channels):
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
            # NOT_SUPPORTED = PLACEHOLDER cal or silicon without a measured table.
            # Leave factory zeros at [0, 0, 0, 0] — analytically calibrated devices
            # don't need a dark-level offset from flash.
            if "NOT_SUPPORTED" in payload.upper():
                return
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
        # Assign into the (n_channels-sized) arrays so a mk2 aux channel keeps
        # its zero default. Factory zeros only ever cover the 4 TIA heads.
        for i in range(min(len(z), self._n_channels)):
            self._zero[i] = int(z[i])
            self._factory_zero[i] = int(z[i])

    def _load_log_cal(self) -> None:
        lut_v: list[list[float]] = []
        lut_lp: list[list[float]] = []
        for head in range(1, 5):
            v_mv_list, log10p_q16_list = self._transport.logcal(head)
            if not v_mv_list:
                if self._log_nominal_eligible:
                    # SN >= 0020 without a usable LUT: fall back to the
                    # nominal log model rather than refusing to convert.
                    self._lut_v_v = None
                    self._lut_log10p = None
                    return
                raise coreDAQCalibrationError(f"LOG LUT empty for head {head}")
            lut_v.append([v / 1000.0 for v in v_mv_list])
            lut_lp.append([q / 65536.0 for q in log10p_q16_list])
        self._lut_v_v = lut_v
        self._lut_log10p = lut_lp

        # Adapt floor to calibration depth.
        # Threshold: if any channel is calibrated to -73 dBm or lower, this is a
        # high-sensitivity device — lower the floor to 100 pW (-70 dBm) so readings
        # in the calibrated range are returned rather than clipped at 1 nW.
        _threshold_log10p = -73.0 / 10.0 - 3.0   # log10(W) for -73 dBm ≈ -10.3
        min_log10p = min(min(ch_lp) for ch_lp in lut_lp)
        if min_log10p <= _threshold_log10p:
            self._log_min_w = 100e-12  # 100 pW = -70 dBm

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
        """Send SNAP n, poll SNAP?, return (codes, gains) of ``_n_channels``.

        The SNAP? reply is ``<code...> G=<gain...>``. On mk1 there are four of
        each; on mk2 the driver parses ``_n_channels`` codes and gains. Missing
        trailing values are padded with zeros so a shorter reply never raises.
        """
        st, _ = self._transport.ask(f"SNAP {n}")
        if st != "OK":
            raise coreDAQError(f"SNAP {n} failed")

        nch = self._n_channels
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
                self._raise_cmd_error("SNAP?", payload)

            parts = payload.split()
            # Codes are the leading integer tokens up to the "G=" gain marker.
            g_idx: Optional[int] = None
            for i, tok in enumerate(parts):
                if tok.upper().startswith("G="):
                    g_idx = i
                    break
            code_tokens = parts[:g_idx] if g_idx is not None else parts

            codes = [0] * nch
            try:
                for i in range(min(nch, len(code_tokens))):
                    codes[i] = int(code_tokens[i])
            except ValueError as exc:
                raise coreDAQError(f"Cannot parse ADC codes from SNAP?: {payload!r}") from exc
            if len(code_tokens) == 0:
                raise coreDAQError(f"SNAP? payload too short: {payload!r}")

            gains = [0] * nch
            if g_idx is not None:
                gain_tokens = [parts[g_idx].split("=", 1)[1]] + parts[g_idx + 1:]
                for i in range(min(nch, len(gain_tokens))):
                    try:
                        gains[i] = int(gain_tokens[i])
                    except ValueError:
                        gains[i] = 0
            return codes, gains

    def _raw_adc_auto(
        self, n: int, autorange_channels: tuple[int, ...]
    ) -> tuple[list[int], list[int]]:
        """Like _raw_adc, but first autoranges the listed channels (LINEAR only).

        Autorange is mk1-only: its code thresholds are derived from the mk1
        ±5 V two's-complement scale. mk2 gain is set explicitly (set_range).
        """
        if (
            not autorange_channels
            or self._frontend != "LINEAR"
            or self._generation != "mk1"
        ):
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
            self._raise_cmd_error(f"GAIN {channel + 1} {gain}", p)
        time.sleep(0.05)

    def _get_firmware_gains(self) -> tuple[int, ...]:
        """Return gain indices for every channel (length ``_n_channels``).

        GAINS? reports the four TIA heads; a mk2 aux channel (index 4) has no
        programmable gain and is padded with 0.
        """
        nch = self._n_channels
        if self._frontend != "LINEAR":
            return tuple([0] * nch)
        st, payload = self._transport.ask("GAINS?")
        if st != "OK":
            self._raise_cmd_error("GAINS?", payload)
        parts = payload.replace("HEAD", "").replace("=", " ").split()
        try:
            nums = [int(parts[i]) for i in range(1, len(parts), 2)]
            if len(nums) != _TIA_HEADS:
                raise ValueError
        except Exception:
            raise coreDAQError(f"Unexpected GAINS? payload: {payload!r}")
        nums += [0] * (nch - len(nums))
        return tuple(nums[:nch])

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

        signal_v = float(zeroed_code) * self._adc_lsb_v
        signal_mv = round(signal_v * 1000.0, _MV_DECIMALS)

        if unit == "v":
            return round(signal_v, _V_DECIMALS)
        if unit == "mv":
            return signal_mv

        p_w = self._to_power_w(ch, gain, zeroed_code, signal_v, signal_mv)

        if unit == "w":
            return p_w   # already rounded to sig-figs by _to_power_w
        if unit == "dbm":
            dbm = max(_DBM_FLOOR, 10.0 * math.log10(max(p_w, 1e-15) * 1000.0))
            return round(dbm, _DBM_DECIMALS)
        raise ValueError(f"Unknown unit {unit!r}")

    @staticmethod
    def _is_tia_head(ch: int) -> bool:
        """True for the 4 photodiode/TIA channels (0..3), both generations.

        A mk2 aux input (index 4 = Analog_IN) has no responsivity/TIA, so
        optical power is undefined and returns 0.0 W rather than raising.
        """
        return int(ch) < _TIA_HEADS

    def _to_power_w(
        self, ch: int, gain: int, zeroed_code: int, signal_v: float, signal_mv: float
    ) -> float:
        if not self._is_tia_head(ch):
            return 0.0
        if self._frontend == "LINEAR":
            return self._linear_to_power_w(ch, gain, signal_mv)
        return self._log_to_power_w(ch, signal_v)

    def _linear_to_power_w(self, ch: int, gain: int, signal_mv: float) -> float:
        if not self._is_tia_head(ch):
            return 0.0
        if self._detector == "SILICON":
            resp = _interp_resp("SILICON", self._wavelength_nm)
            tia = self._silicon_tia[ch][gain]
            if resp <= 0.0 or tia <= 0.0:
                raise coreDAQError(f"Invalid silicon model for ch {ch} gain {gain}")
            p_w = (signal_mv / 1000.0) / (tia * resp)
            return _round_w(p_w)

        slope = self._cal_slope[ch][gain]
        if slope == 0.0:
            raise coreDAQError(f"Zero calibration slope for ch {ch} gain {gain}")
        p_w = (signal_mv / slope) * self._resp_correction()
        return _round_w(p_w)

    def _log_to_power_w(self, ch: int, signal_v: float) -> float:
        if not self._is_tia_head(ch):
            return 0.0
        # A loaded calibration LUT always takes precedence.
        if self._lut_v_v is not None and self._lut_log10p is not None:
            xs = self._lut_v_v[ch]
            ys = self._lut_log10p[ch]
            if not xs:
                raise coreDAQError(f"LOG LUT empty for ch {ch}")
            p_w = 10.0 ** _interp_lut(xs, ys, signal_v)
            p_w *= self._resp_correction()
            return _round_w(min(max(p_w, self._log_min_w), _INGAAS_LOG_MAX_W))

        # No LUT — analytic log-amp model: I = IZ * 10^(V/VY), P = I / resp.
        iz, vy = self._log_model_iz_vy()
        resp = _interp_resp(self._detector, self._wavelength_nm)
        if resp <= 0.0:
            raise coreDAQError(f"Invalid {self._detector} responsivity")
        p_w = (iz / resp) * (10.0 ** (signal_v / vy))
        return _round_w(min(max(p_w, self._log_min_w), _INGAAS_LOG_MAX_W))

    def _log_model_iz_vy(self) -> tuple[float, float]:
        """Intercept/slope of the analytic log model when no LUT is loaded.

        SN 0020 and up (both detectors): 10 pA intercept, 200 mV/decade.
        Older silicon units: legacy 100 pA / 0.5 V-per-decade model.
        Older InGaAs units have no analytic model — they require the LUT.
        """
        if self._log_nominal_eligible:
            return _LOG_NOMINAL_IZ, _LOG_NOMINAL_VY
        if self._detector == "SILICON":
            return _SI_LOG_IZ, _SI_LOG_VY
        raise coreDAQError("LOG LUT not loaded")

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

    def _ch(self, channel: int) -> int:
        ch = int(channel)
        if not (0 <= ch < self._n_channels):
            raise ValueError(f"channel must be 0..{self._n_channels - 1}")
        return ch

    @classmethod
    def _n(cls, n_samples: int) -> int:
        v = int(n_samples)
        if not (1 <= v <= cls.MAX_READ_SAMPLES):
            raise ValueError(f"n_samples must be 1..{cls.MAX_READ_SAMPLES}")
        return v

    def _channels_arg(
        self, channels: Optional[Union[int, Sequence[int]]]
    ) -> Optional[tuple[int, ...]]:
        if channels is None:
            return None
        if isinstance(channels, int):
            return (self._ch(channels),)
        result = [self._ch(c) for c in channels]
        if not result:
            raise ValueError("channels must not be empty")
        return tuple(sorted(set(result)))

    def _mask_to_channels(self, mask: int) -> tuple[int, ...]:
        return tuple(i for i in range(self._n_channels) if mask & (1 << i))

    @staticmethod
    def _channels_to_mask(channels: Sequence[int]) -> int:
        mask = 0
        for ch in channels:
            mask |= 1 << int(ch)
        return mask

    def _parse_mask(self, mask: int) -> int:
        value = int(mask)
        if not (0 <= value <= self._chmask_max):
            raise ValueError(
                f"capture_channel_mask must be an integer 0..{self._chmask_max} "
                f"(bits 0..{self._n_channels - 1})"
            )
        return value

    @staticmethod
    def _power_dbm(power_w: float) -> float:
        if not math.isfinite(power_w) or power_w <= 0.0:
            return _DBM_FLOOR
        dbm = max(_DBM_FLOOR, 10.0 * math.log10(power_w / 1e-3))
        return round(dbm, _DBM_DECIMALS)

    def _signal_flags(self, signal_v: float, signal_mv: float) -> tuple[bool, bool, bool]:
        # mk1 (bipolar +/-5 V): both checks on |v| — byte-identical to <=1.2.1.
        # mk2 (unipolar 0-5 V): over-range is a SIGNED compare against the 4.9 V
        # rail headroom (a post-zero negative excursion is small, not "over");
        # under-range keeps |v| (zero subtraction can yield small negatives).
        ov = float(signal_v) if self._signed_over else abs(float(signal_v))
        over = ov > self._over_range_v
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
        """One ChannelProxy per device channel (4 on mk1, 5 on mk2)."""
        return [ChannelProxy(self, ch) for ch in range(self._n_channels)]

    # ------------------------------------------------------------------
    # Reading unit
    # ------------------------------------------------------------------

    def set_reading_unit(self, unit: str) -> None:
        """Set the default output unit for all read_* calls."""
        self._reading_unit = self._unit(unit)

    def reading_unit(self) -> str:
        """Return the current default output unit."""
        return self._reading_unit

    def set_autorange(self, enabled: bool) -> None:
        """Enable or disable autoRange globally for all read_* calls.

        When enabled (the default), the driver automatically selects the best
        TIA gain range before each read on LINEAR frontends.  Pass
        ``autoRange=True/False`` to any individual read call to override for
        that measurement only — the global setting is not affected.

        Calling any of :meth:`set_range`, :meth:`set_ranges`,
        :meth:`set_range_power`, or :meth:`set_range_powers` implicitly calls
        ``set_autorange(False)`` so that the manually selected range is
        preserved on subsequent reads.  Call ``set_autorange(True)`` to
        re-enable automatic selection at any time.
        """
        self._autorange = bool(enabled)

    def autorange(self) -> bool:
        """Return the current global autoRange setting."""
        return self._autorange

    def _resolve_autorange(self, autoRange: Optional[bool]) -> bool:
        """Return the effective autoRange for one call.

        ``None`` means use the global setting; an explicit bool overrides it
        for this call only.
        """
        return self._autorange if autoRange is None else bool(autoRange)

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    def read_channel(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: Optional[bool] = None,
        n_samples: int = 1,
    ) -> Union[int, float]:
        """Read one channel; return a plain scalar value.

        ``autoRange=None`` uses the global setting (see :meth:`set_autorange`).
        Pass ``True`` or ``False`` to override for this call only.
        """
        ch = self._ch(channel)
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (ch,) if self._resolve_autorange(autoRange) else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return self._adc_to_unit(ch, codes[ch] - self._zero[ch], gains[ch], u)

    def read_all(
        self,
        unit: Optional[str] = None,
        autoRange: Optional[bool] = None,
        n_samples: int = 1,
    ) -> List[Union[int, float]]:
        """Read every channel; return a plain list of scalar values.

        Returns 4 values on mk1, 5 on mk2 (index 4 = Analog_IN).
        ``autoRange=None`` uses the global setting (see :meth:`set_autorange`).
        """
        u = self._unit(unit)
        n = self._n(n_samples)
        all_ch = tuple(range(self._n_channels))
        ar_chs: tuple[int, ...] = all_ch if self._resolve_autorange(autoRange) else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return [
            self._adc_to_unit(ch, codes[ch] - self._zero[ch], gains[ch], u)
            for ch in all_ch
        ]

    def read_channel_full(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: Optional[bool] = None,
        n_samples: int = 1,
    ) -> ChannelReading:
        """Read one channel and return a rich measurement object.

        ``autoRange=None`` uses the global setting (see :meth:`set_autorange`).
        """
        ch = self._ch(channel)
        u = self._unit(unit)
        n = self._n(n_samples)
        ar_chs: tuple[int, ...] = (ch,) if self._resolve_autorange(autoRange) else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        return self._make_reading(ch, codes[ch], gains[ch], u)

    def read_all_full(
        self,
        unit: Optional[str] = None,
        autoRange: Optional[bool] = None,
        n_samples: int = 1,
    ) -> MeasurementSet:
        """Read every channel and return a rich measurement set.

        Returns 4 readings on mk1, 5 on mk2 (index 4 = Analog_IN).
        ``autoRange=None`` uses the global setting (see :meth:`set_autorange`).
        """
        u = self._unit(unit)
        n = self._n(n_samples)
        all_ch = tuple(range(self._n_channels))
        ar_chs: tuple[int, ...] = all_ch if self._resolve_autorange(autoRange) else ()
        codes, gains = self._raw_adc_auto(n, ar_chs)
        readings = tuple(self._make_reading(ch, codes[ch], gains[ch], u) for ch in all_ch)
        return MeasurementSet(readings=readings, unit=u)

    def _make_reading(self, ch: int, raw_code: int, gain: int, unit: str) -> ChannelReading:
        zeroed = raw_code - self._zero[ch]
        signal_v = float(zeroed) * self._adc_lsb_v     # raw, used for power math
        signal_mv = round(signal_v * 1000.0, _MV_DECIMALS)
        signal_v_disp = round(signal_v, _V_DECIMALS)  # rounded for display/storage
        over, under, clipped = self._signal_flags(signal_v, signal_mv)

        if self._frontend == "LINEAR":
            p_w = self._linear_to_power_w(ch, gain, signal_mv)
            # Aux (non-TIA) channels carry no gain range.
            range_index: Optional[int] = gain if self._is_tia_head(ch) else None
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
            value = signal_v_disp
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
            signal_v=signal_v_disp,
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
        chs = range(self._n_channels) if channel is None else (self._ch(channel),)
        statuses = []
        for ch in chs:
            zeroed = codes[ch] - self._zero[ch]
            sv = float(zeroed) * self._adc_lsb_v
            smv = round(sv * 1000.0, _MV_DECIMALS)
            over, under, clipped = self._signal_flags(sv, smv)
            statuses.append(SignalStatus(
                channel=ch,
                signal_v=round(sv, _V_DECIMALS),
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
            self._raise_cmd_error("CHMASK?", p)
        m = re.search(r"0x([0-9A-Fa-f]+)", p)
        ch_m = re.search(r"CH\s*=\s*(\d+)", p, re.IGNORECASE)
        fb_m = re.search(r"FB\s*=\s*(\d+)", p, re.IGNORECASE)
        if not m:
            raise coreDAQError(f"Unexpected CHMASK? payload: {p!r}")
        mask = int(m.group(1), 16) & self._chmask_max
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

    def set_capture_channel_mask(self, mask: int) -> int:
        value = self._parse_mask(mask)
        if value == 0:
            raise ValueError("capture_channel_mask must enable at least one channel")
        st, p = self._transport.ask(f"CHMASK 0x{value:X}")
        if st != "OK":
            self._raise_cmd_error("CHMASK set", p)
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

    def arm_capture(
        self,
        frames: int,
        trigger: bool = False,
        trigger_rising: bool = True,
        stepped: bool = False,
        step_delay_us: int = 0,
        step_burst: int = 1,
        gate: bool = False,
    ) -> None:
        """Arm the ADC for a block acquisition (does not start yet).

        Continuous trigger mode (``trigger=True``): the first BNC edge starts
        free-running sampling at the configured sample rate until ``frames``
        frames are stored.

        Step-tuned mode (``trigger=True, stepped=True``): EVERY BNC edge fires
        a burst of ``step_burst`` conversions, ``step_delay_us`` microseconds
        after the edge (use it to land mid-dwell of a step-tuned laser).
        Bursts longer than 1 run at the configured sample rate. Edges arriving
        while the previous step is still in flight are counted as missed (see
        :meth:`step_missed_edges`) and ignored — nothing breaks. The capture
        completes when ``frames`` total frames are stored; stop early with
        :meth:`stop_capture` and collect what's there via
        ``collect_capture()`` with no frame argument.

        Requires firmware v4.3+ for stepped mode.
        """
        if frames <= 0:
            raise ValueError("frames must be > 0")
        if stepped:
            if not trigger:
                raise ValueError("stepped=True requires trigger=True")
            if not (0 <= int(step_delay_us) <= 65535):
                raise ValueError("step_delay_us must be 0..65535")
            if not (1 <= int(step_burst) <= 255):
                raise ValueError("step_burst must be 1..255")
            # Older firmware silently ignores the trailing 'S ...' tokens and
            # would arm a continuous capture instead — refuse up front.
            self._require_firmware(4, 3, "stepped trigger mode")
        if gate:
            # FSYN-gated stepped arm ('G' suffix): per-step edges are ignored
            # until a gate edge on CH4 opens the acquisition (e.g. ATLS FSYN =
            # scan start, PSYN = per-step). mk2 firmware only.
            if not stepped:
                raise ValueError("gate=True requires stepped=True")
            self._require_mk2("gated stepped arm (gate=True)")
        if trigger:
            pol = "R" if trigger_rising else "F"
            if stepped:
                cmd = f"TRIGARM {frames} {pol} S {int(step_delay_us)} {int(step_burst)}"
                if gate:
                    cmd += " G"
                st, p = self._transport.ask(cmd)
            else:
                st, p = self._transport.ask(f"TRIGARM {frames} {pol}")
        else:
            st, p = self._transport.ask(f"ACQ ARM {frames}")
        if st != "OK":
            self._raise_cmd_error("arm_capture", p)
        self._armed_frames = int(frames)
        self._armed_trigger = bool(trigger)

    def arm_window_capture(self, max_frames: Optional[int] = None) -> None:
        """Arm a WINDOWED run-till-stop capture (mk2, swept-laser style).

        Acquisition runs between two edges of the window/sweep-gate input
        (CH3 BNC): the FALLING edge starts it, the RISING edge stops it.
        While the window is open, the mask input (CH4 BNC) gates sampling —
        HIGH = sample, LOW = masked (e.g. a laser mode hop). Frames are paced
        by the free-running sample rate.

        *max_frames* optionally caps the capture; ``None`` runs until the stop
        edge, bounded only by device memory — if memory fills first,
        :meth:`capture_overflowed` returns True. Finish with
        ``stop_capture()`` (safety) + ``collect_capture()`` (frames=None).
        """
        self._require_mk2("arm_window_capture()")
        n = 0 if max_frames is None else int(max_frames)
        if n < 0:
            raise ValueError("max_frames must be >= 0")
        st, p = self._transport.ask(f"TRIGARM_COMET {n}" if n else "TRIGARM_COMET")
        if st != "OK":
            self._raise_cmd_error("arm_window_capture", p)
        self._armed_frames = 0            # run-till-stop: collect via FRAMES?
        self._armed_trigger = True

    def hop_count(self) -> int:
        """Return the number of mask (mode-hop) edges seen since arming (mk2).

        For windowed captures this counts CH4 mask events; for gated stepped
        captures it counts gate-open edges.
        """
        self._require_mk2("hop_count()")
        st, p = self._transport.ask("HOPS?")
        if st != "OK":
            self._raise_cmd_error("HOPS?", p)
        return int(p.split()[0], 0)

    def start_capture(self) -> None:
        """Start a previously armed (non-triggered) acquisition.

        Only valid after ``arm_capture(trigger=False)``. A capture armed with
        ``trigger=True`` (edge-started or stepped) starts on the BNC edge —
        calling this then is a usage error and is refused locally with a clear
        message instead of confusing the device.
        """
        armed_trigger = self._armed_trigger
        if not armed_trigger and self._armed_frames == 0 and self._fw_at_least(4, 3):
            # Fresh session (or another process armed the device): consult the
            # device state so a trigger-armed capture is still refused cleanly.
            st, p = self._transport.ask("STATE?")
            if st == "OK":
                try:
                    armed_trigger = int(p, 0) == _ACQ_STATE_ARMED
                except ValueError:
                    pass
        if armed_trigger:
            raise coreDAQStateError(
                "start_capture() is not used with a trigger-armed capture: the "
                "acquisition starts on the BNC trigger edge itself (stepped mode "
                "captures one burst per edge). Fire your trigger source, then "
                "poll captured_frames() and finish with stop_capture() + "
                "collect_capture()."
            )
        st, p = self._transport.ask("ACQ START")
        if st != "OK":
            self._raise_cmd_error("ACQ START", p)

    def stop_capture(self) -> None:
        """Abort an active acquisition."""
        self._transport.ask("ACQ STOP")
        self._armed_frames = 0
        self._armed_trigger = False

    def capture_status(self) -> str:
        """Return the current acquisition state string from the device."""
        st, p = self._transport.ask("STREAM?")
        if st != "OK":
            self._raise_cmd_error("STREAM?", p)
        return p

    def remaining_frames(self) -> int:
        """Return the number of frames still to be collected."""
        st, p = self._transport.ask("LEFT?")
        if st != "OK":
            self._raise_cmd_error("LEFT?", p)
        return int(p, 0)

    def _frames_query(self) -> tuple[int, int, bool]:
        """FRAMES? -> (frames_stored, missed_edges, overflow). Requires firmware v4.3.

        mk2 firmware appends ``OVFL=<0|1>`` (set when a run-till-stop capture,
        e.g. COMET, filled the 32 MB buffer before the stop edge — the data is
        the first 3,342,336 frames, the rest was dropped). mk1 omits it.
        Only a bare integer token is the stored count; ``KEY=VALUE`` tokens are
        parsed by key, so unknown future fields never corrupt the frame count.
        """
        self._require_firmware(4, 3, "FRAMES? query")
        st, p = self._transport.ask("FRAMES?")
        if st != "OK":
            self._raise_cmd_error("FRAMES?", p)
        stored = 0
        missed = 0
        overflow = False
        for tok in p.split():
            up = tok.upper()
            if up.startswith("MISSED="):
                try:
                    missed = int(tok.split("=", 1)[1], 0)
                except ValueError:
                    pass
            elif up.startswith("OVFL="):
                val = tok.split("=", 1)[1]
                try:
                    overflow = int(val, 0) != 0
                except ValueError:
                    overflow = False
            elif "=" not in tok:
                try:
                    stored = int(tok, 0)
                except ValueError:
                    pass            # unknown future bare token: never crash
        return stored, missed, overflow

    def capture_overflowed(self) -> bool:
        """True if the last/current capture overflowed the 32 MB buffer.

        Relevant to run-till-stop captures (COMET): if the sweep window runs
        longer than SDRAM can hold, the capture stops at the buffer limit and
        this returns True. The stored data (see :meth:`captured_frames`) is
        intact up to the limit; samples past it were dropped. Always False on
        mk1 (which has no run-till-stop mode).
        """
        if not self._fw_at_least(4, 3):
            return False        # pre-v4.3 mk1: no run-till-stop, can never overflow
        return self._frames_query()[2]

    def captured_frames(self) -> int:
        """Return the number of frames stored in device memory so far.

        Works in any state (after completion, after :meth:`stop_capture`,
        or while armed). Counts whole frames respecting the channel mask.
        Requires firmware v4.3.
        """
        return self._frames_query()[0]

    def step_missed_edges(self) -> int:
        """Return the count of trigger edges ignored in stepped mode.

        An edge is missed when it arrives while the previous step's delay or
        burst is still in flight (e.g. delay set longer than the trigger
        period). Reset on every arm. Requires firmware v4.3.
        """
        return self._frames_query()[1]

    def _wait_for_completion(
        self,
        frames: int,
        trigger: bool = False,
        trigger_timeout_s: float = 60.0,
    ) -> None:
        """Wait for an acquisition to finish without polling during DMA.

        Polling the device while the STM32 DMA+SPI are running at full speed
        corrupts samples. Instead:
        - For triggered captures: poll STATE? only while in ARMED state (DMA
          is not yet running). Stop the instant the trigger fires, then sleep.
        - For all captures: sleep for frames/sample_rate + overhead — no I/O
          during the acquisition window.
        """
        overhead = getattr(self._transport, "acq_overhead_s", _CAPTURE_OVERHEAD_S)
        acq_s = frames / max(1, self._sample_rate_hz) + overhead

        if trigger:
            # Poll STATE? until the trigger fires (ARMED → anything else).
            # During ARMED the DMA is idle, so this is safe.
            t0 = time.time()
            while True:
                st, p = self._transport.ask("STATE?")
                if st == "OK":
                    try:
                        state = int(p, 0)
                    except ValueError:
                        state = -1
                    if state == _ACQ_STATE_DONE:
                        return  # trigger fired and acquisition already finished
                    if state != _ACQ_STATE_ARMED and state != _ACQ_STATE_IDLE:
                        break  # ACQUIRING started — fall through to sleep
                if (time.time() - t0) > trigger_timeout_s:
                    raise coreDAQTimeoutError(
                        f"Triggered capture timeout: no trigger edge received "
                        f"within {trigger_timeout_s:.1f} s."
                    )
                time.sleep(0.025)

        # Sleep for the full acquisition duration — no device I/O during DMA.
        time.sleep(acq_s)

    def capture_is_data_ready(self) -> bool:
        """Return True if the acquisition is complete and data is in SDRAM.

        Queries the firmware state register (``STATE?``).

        .. warning::
            Do **not** call this while the device is actively acquiring.
            The MCU's DMA and SPI run at full speed during acquisition and
            any USB command sent in that window will corrupt samples.
            Use this only after sleeping for the expected acquisition duration,
            or to confirm readiness after ``arm_capture()`` returns (before
            ``start_capture()`` is called). Reliable non-blocking status will
            be addressed in a future firmware release.
        """
        st, p = self._transport.ask("STATE?")
        if st != "OK":
            return False
        try:
            return int(p, 0) == _ACQ_STATE_DONE
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Block capture
    # ------------------------------------------------------------------

    def _resolve_capture_channels(
        self, channels: Optional[Union[int, Sequence[int]]]
    ) -> tuple[tuple[int, ...], int, int, bool]:
        """Return (target_channels, target_mask, original_mask, mask_changed)."""
        requested = self._channels_arg(channels)
        original_mask, _, _ = self._get_mask_info()
        original_channels = self._mask_to_channels(original_mask)
        target_channels = original_channels if requested is None else requested
        target_mask = (
            original_mask if requested is None else self._channels_to_mask(target_channels)
        )
        mask_changed = requested is not None and target_mask != original_mask
        return target_channels, target_mask, original_mask, mask_changed

    def _build_capture_result(
        self,
        frames: int,
        target_channels: tuple[int, ...],
        target_mask: int,
        unit: str,
    ) -> CaptureResult:
        """XFER from device and convert to CaptureResult. No timing, no arm."""
        raw_traces = self._transport.read_frames(
            int(frames), target_mask,
            n_channels=self._n_channels, unsigned=self._adc_unsigned,
        )
        gains = self._get_firmware_gains()

        traces: dict[int, np.ndarray] = {}
        statuses: dict[int, CaptureChannelStatus] = {}
        ranges: dict[int, Optional[int]] = {}
        range_labels: dict[int, Optional[str]] = {}

        for ch in target_channels:
            raw_arr = raw_traces[ch].astype(np.int32)
            gain = gains[ch]
            if self._frontend == "LINEAR":
                zeroed = raw_arr - self._zero[ch]
                # Aux (non-TIA) channels carry no gain range.
                range_index: Optional[int] = int(gain) if self._is_tia_head(ch) else None
            else:
                zeroed = raw_arr
                range_index = None

            values, status = self._convert_capture_trace(ch, zeroed, gain, range_index, unit)
            traces[ch] = values
            statuses[ch] = status
            ranges[ch] = range_index
            range_labels[ch] = self._gain_label(range_index)

        return CaptureResult(
            traces=traces,
            statuses=statuses,
            unit=unit,
            sample_rate_hz=self._sample_rate_hz,
            enabled_channels=tuple(target_channels),
            ranges=ranges,
            range_labels=range_labels,
            wavelength_nm=self._wavelength_nm,
            detector=self._detector,
            frontend=self._frontend,
        )

    def capture(
        self,
        frames: int,
        unit: Optional[str] = None,
        channels: Optional[Union[int, Sequence[int]]] = None,
    ) -> CaptureResult:
        """Arm, start, wait, and return a block capture in one blocking call.

        For triggered captures (where the trigger source must be started from
        the same script), use the manual workflow instead::

            coredaq.arm_capture(N, trigger=True)
            my_instrument.fire()
            time.sleep(N / coredaq.sample_rate_hz() + 0.5)
            result = coredaq.collect_capture(N, unit="w")
        """
        if int(frames) <= 0:
            raise ValueError("frames must be > 0")
        u = self._unit(unit)
        target_channels, target_mask, original_mask, mask_changed = (
            self._resolve_capture_channels(channels)
        )
        if mask_changed:
            self._transport.ask(f"CHMASK 0x{target_mask:X}")
        try:
            self.arm_capture(int(frames))
            self.start_capture()
            self._wait_for_completion(int(frames), trigger=False)
            result = self._build_capture_result(int(frames), target_channels, target_mask, u)
        finally:
            if mask_changed:
                try:
                    self._transport.ask(f"CHMASK 0x{original_mask:X}")
                except Exception:
                    pass
        return result

    def collect_capture(
        self,
        frames: Optional[int] = None,
        unit: Optional[str] = None,
        channels: Optional[Union[int, Sequence[int]]] = None,
    ) -> CaptureResult:
        """Transfer and convert a capture that has already completed.

        Use this after you have armed the device, started or triggered the
        acquisition, and slept for the acquisition duration yourself::

            # Software start:
            coredaq.arm_capture(N)
            coredaq.start_capture()
            time.sleep(N / coredaq.sample_rate_hz() + 0.5)
            result = coredaq.collect_capture(N, unit="w")

            # External trigger — fire from the same script, non-blocking:
            coredaq.arm_capture(N, trigger=True)
            my_instrument.fire()
            time.sleep(N / coredaq.sample_rate_hz() + 0.5)
            result = coredaq.collect_capture(N, unit="w")

        With ``frames=None`` (firmware v4.3+) the device is asked how many
        frames it actually stored (``FRAMES?``) and that many are collected.
        This is the natural way to read back a stepped-trigger capture or a
        capture aborted early with :meth:`stop_capture` — and it also works
        from a fresh session, since the frame count comes from the device,
        not from this object's arm bookkeeping.

        Does not arm, does not sleep, sends XFER immediately.
        """
        if frames is None:
            # Device-reported count: safe even if arm_capture() was called in
            # a different session/process — the data lives in device SDRAM.
            n = self.captured_frames()
            if n <= 0:
                raise coreDAQError(
                    "collect_capture(): device reports 0 frames stored — "
                    "nothing to collect."
                )
        else:
            n = int(frames)
            if n <= 0:
                raise ValueError("frames must be > 0")
            # Validate before any USB traffic: asking to XFER more frames than
            # the device holds makes the firmware send what it has and then
            # stall, with no clean recovery.
            if self._fw_at_least(4, 3):
                # Device is the source of truth (works across sessions, and
                # catches aborted/short captures the client count would miss).
                stored = self.captured_frames()
                if stored <= 0:
                    raise coreDAQError(
                        "collect_capture(): device reports 0 frames stored — "
                        "nothing to collect."
                    )
                if n > stored:
                    raise ValueError(
                        f"collect_capture({n:,}) exceeds the {stored:,} frames the "
                        f"device has stored. Pass no frames to collect exactly what "
                        f"is stored, or request {stored:,} or fewer."
                    )
            else:
                # Pre-v4.3 firmware has no FRAMES? — fall back to this session's
                # arm bookkeeping.
                if self._armed_frames == 0:
                    raise coreDAQError(
                        "collect_capture(frames) called but no capture was armed "
                        "by this session, so the frame count cannot be validated "
                        "(firmware < v4.3 has no device-side frame count). "
                        "arm_capture() first."
                    )
                if n != self._armed_frames:
                    raise ValueError(
                        f"collect_capture({n:,}) does not match the armed frame count "
                        f"({self._armed_frames:,}). Pass the same number of frames you "
                        f"passed to arm_capture(), or pass no frames at all."
                    )

        u = self._unit(unit)
        target_channels, target_mask, original_mask, mask_changed = (
            self._resolve_capture_channels(channels)
        )
        if mask_changed:
            self._transport.ask(f"CHMASK 0x{target_mask:X}")
        try:
            result = self._build_capture_result(n, target_channels, target_mask, u)
            self._armed_frames = 0   # consumed — prevent a second collect
        except Exception:
            # XFER failed mid-transfer — firmware is in an inconsistent state.
            # Soft-reset so the next call starts clean.
            try:
                self._transport.drain()
                self._transport.ask("SOFTRESET")
            except Exception:
                pass
            self._armed_frames = 0
            raise
        finally:
            if mask_changed:
                try:
                    self._transport.ask(f"CHMASK 0x{original_mask:X}")
                except Exception:
                    pass
        return result

    def _convert_capture_trace(
        self,
        ch: int,
        zeroed: np.ndarray,
        gain: int,
        range_index: Optional[int],
        unit: str,
    ) -> tuple[np.ndarray, CaptureChannelStatus]:
        sv = zeroed.astype(np.float64) * self._adc_lsb_v
        sv_abs = np.abs(sv)
        ov = sv if self._signed_over else sv_abs
        over_mask  = ov > self._over_range_v
        under_mask = (sv_abs * 1000.0) < _UNDER_RANGE_MV
        clip_mask  = over_mask | under_mask

        status = CaptureChannelStatus(
            channel=ch,
            any_over_range=bool(np.any(over_mask)),
            any_under_range=bool(np.any(under_mask)),
            any_clipped=bool(np.any(clip_mask)),
            over_range_samples=int(np.sum(over_mask)),
            under_range_samples=int(np.sum(under_mask)),
            clipped_samples=int(np.sum(clip_mask)),
            peak_signal_v=round(float(np.max(sv_abs)), _V_DECIMALS) if len(sv_abs) else 0.0,
        )

        if unit == "adc":
            return zeroed, status
        if unit == "v":
            return np.round(sv, _V_DECIMALS), status
        if unit == "mv":
            return np.round(sv * 1000.0, _MV_DECIMALS), status
        if unit in ("w", "dbm"):
            return self._power_array(ch, gain, sv, unit), status
        raise ValueError(f"Unknown unit {unit!r}")

    def _power_array(
        self,
        ch: int,
        gain: int,
        sv: np.ndarray,
        unit: str,
    ) -> np.ndarray:
        """Vectorized power conversion for capture traces (w or dbm)."""
        if not self._is_tia_head(ch):
            # Aux (non-TIA) channel: optical power is undefined.
            if unit == "w":
                return np.zeros_like(sv)
            return np.full_like(sv, _DBM_FLOOR)
        if self._frontend == "LINEAR":
            if self._detector == "SILICON":
                resp = _interp_resp("SILICON", self._wavelength_nm)
                tia  = self._silicon_tia[ch][gain]
                if resp <= 0.0 or tia <= 0.0:
                    raise coreDAQError(f"Invalid silicon model for ch {ch} gain {gain}")
                p_w = sv / (tia * resp)
            else:
                slope = self._cal_slope[ch][gain]
                if slope == 0.0:
                    raise coreDAQError(f"Zero calibration slope for ch {ch} gain {gain}")
                p_w = (sv * 1000.0) / slope * self._resp_correction()
        else:  # LOG
            if self._lut_v_v is not None and self._lut_log10p is not None:
                xs = self._lut_v_v[ch]
                ys = self._lut_log10p[ch]
                if not xs:
                    raise coreDAQError(f"LOG LUT empty for ch {ch}")
                log10p = np.interp(sv, xs, ys)
                p_w = np.power(10.0, log10p) * self._resp_correction()
                p_w = np.clip(p_w, self._log_min_w, _INGAAS_LOG_MAX_W)
            else:
                iz, vy = self._log_model_iz_vy()
                resp = _interp_resp(self._detector, self._wavelength_nm)
                if resp <= 0.0:
                    raise coreDAQError(f"Invalid {self._detector} responsivity")
                p_w = (iz / resp) * np.power(10.0, sv / vy)
                p_w = np.clip(p_w, self._log_min_w, _INGAAS_LOG_MAX_W)

        if unit == "w":
            return _round_w_array(p_w)
        dbm = 10.0 * np.log10(np.maximum(p_w, 1e-15) * 1000.0)
        return np.round(np.maximum(dbm, _DBM_FLOOR), _DBM_DECIMALS)

    def capture_channel(
        self,
        channel: int,
        frames: int,
        unit: Optional[str] = None,
    ) -> CaptureResult:
        """Arm, start, wait, and collect a single-channel capture (blocking)."""
        return self.capture(frames=frames, unit=unit, channels=[self._ch(channel)])

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
        """Return current range indices for every channel.

        4 entries on mk1, 5 on mk2; a non-TIA (aux) channel reports ``None``.
        """
        if self._frontend != "LINEAR":
            return [None] * self._n_channels
        gains = self._get_firmware_gains()
        return [int(gains[ch]) if self._is_tia_head(ch) else None
                for ch in range(self._n_channels)]

    def set_range(self, channel: int, range_index: int) -> None:
        """Set the TIA gain range for one channel (LINEAR only).

        Implicitly disables global autoRange so the chosen range is preserved
        on subsequent reads.  Call :meth:`set_autorange` ``(True)`` to
        re-enable automatic range selection.
        """
        self._require_linear("set_range")
        ch = self._ch(channel)
        idx = int(range_index)
        if not (0 <= idx <= 7):
            raise ValueError("range_index must be 0..7")
        self._autorange = False
        self._set_gain_hw(ch, idx)

    def set_ranges(self, range_indices: Sequence[int]) -> List[Optional[int]]:
        """Set range indices for all channels (LINEAR only; 4 on mk1, 5 on mk2 with None for the aux channel).

        Implicitly disables global autoRange so the chosen ranges are preserved
        on subsequent reads.  Call :meth:`set_autorange` ``(True)`` to
        re-enable automatic range selection.
        """
        values = [None if v is None else int(v) for v in range_indices]
        n = self._n_channels
        if len(values) != n:
            raise ValueError(f"range_indices must have exactly {n} elements "
                             f"(one per channel; None for non-TIA channels)")
        self._autorange = False
        for ch, idx in enumerate(values):
            if idx is None:
                continue                      # aux channel (mk2 ch4): no TIA range
            self._require_linear("set_ranges")
            if not (0 <= idx <= 7):
                raise ValueError(f"range_index[{ch}] must be 0..7")
            self._set_gain_hw(ch, idx)
        return self.get_ranges()

    def set_range_power(self, channel: int, power_w: float) -> int:
        """Select the best range for a target optical power; return chosen index.

        Implicitly disables global autoRange so the chosen range is preserved
        on subsequent reads.  Call :meth:`set_autorange` ``(True)`` to
        re-enable automatic range selection.
        """
        self._require_linear("set_range_power")
        requested = abs(float(power_w))
        if not math.isfinite(requested):
            raise ValueError("power_w must be finite")
        limits = _GAIN_MAX_W_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_MAX_W
        fitting = [idx for idx, lim in enumerate(limits) if requested <= float(lim)]
        idx = int(fitting[-1]) if fitting else 0
        self._autorange = False
        self._set_gain_hw(channel, idx)
        return idx

    def set_range_powers(self, power_w_values: Sequence[float]) -> List[Optional[int]]:
        """Call set_range_power for all TIA channels (LINEAR only; pass None for the mk2 aux channel).

        Implicitly disables global autoRange so the chosen ranges are preserved
        on subsequent reads.  Call :meth:`set_autorange` ``(True)`` to
        re-enable automatic range selection.
        """
        values = [None if v is None else float(v) for v in power_w_values]
        n = self._n_channels
        if len(values) != n:
            raise ValueError(f"power_w_values must have exactly {n} elements "
                             f"(one per channel; None for non-TIA channels)")
        self._autorange = False
        for ch, pw in enumerate(values):
            if pw is None:
                continue                      # aux channel (mk2 ch4): no TIA range
            self._require_linear("set_range_powers")
            requested = abs(pw)
            if not math.isfinite(requested):
                raise ValueError(f"power_w_values[{ch}] must be finite")
            limits = _GAIN_MAX_W_LEGACY if self._gain_profile == "linear_legacy" else _GAIN_MAX_W
            fitting = [idx for idx, lim in enumerate(limits) if requested <= float(lim)]
            idx = int(fitting[-1]) if fitting else 0
            self._set_gain_hw(ch, idx)
        return self.get_ranges()

    # ------------------------------------------------------------------
    # Zeroing (LINEAR only)
    # ------------------------------------------------------------------

    def zero_offsets_adc(self) -> tuple[int, ...]:
        """Return the active zero offsets in ADC counts (CH0..CH3)."""
        return tuple(int(x) for x in self._zero)  # type: ignore[return-value]

    def factory_zero_offsets_adc(self) -> tuple[int, ...]:
        """Return the factory-stored zero offsets in ADC counts."""
        return tuple(int(x) for x in self._factory_zero)  # type: ignore[return-value]

    def zero_dark(
        self, frames: int = 32, settle_s: float = 0.2
    ) -> tuple[int, ...]:
        """Capture a dark baseline and set it as the active zero offset.

        Block the input (or cover the fiber end) before calling this.
        Raises ``coreDAQUnsupportedError`` on InGaAs LOG frontends (which use
        a LUT calibration with no zero offset path). Silicon devices support
        zeroing regardless of frontend topology.
        """
        if self._frontend != "LINEAR" and self._detector != "SILICON":
            raise coreDAQUnsupportedError(
                "zero_dark() is not supported on InGaAs LOG frontends."
            )
        if frames <= 0:
            raise ValueError("frames must be > 0")
        time.sleep(max(0.0, float(settle_s)))
        codes, _ = self._raw_adc(frames)
        self._zero = [int(codes[ch]) for ch in range(self._n_channels)]
        self._zero_source = "user"
        return self.zero_offsets_adc()

    def restore_factory_zero(self) -> tuple[int, ...]:
        """Restore the factory-stored zero offsets."""
        if self._frontend == "LINEAR":
            self._zero = list(self._factory_zero)
            self._zero_source = "factory"
        return self.zero_offsets_adc()

    # ------------------------------------------------------------------
    # Sample rate and oversampling
    # ------------------------------------------------------------------

    def set_sample_rate_hz(self, hz: int) -> None:
        """Set the ADC sample rate in Hz.

        mk1 accepts 1..100 000 Hz; mk2 accepts 1..1 000 000 Hz. On a mk2 in the
        low-bandwidth tier the firmware clamps the effective rate — the driver
        never gates the tier locally; any tier-related refusal surfaces from
        firmware as a clean error.
        """
        fmax = 1_000_000 if self._generation == "mk2" else 100_000
        if hz <= 0 or hz > fmax:
            raise coreDAQError(f"FREQ must be 1..{fmax} Hz")
        st, p = self._ask_busy(f"FREQ {hz}")
        if st != "OK":
            self._raise_cmd_error(f"FREQ {hz}", p)
        # The firmware clamps to the tier/oversampling ceiling and replies with
        # the APPLIED rate. Trust the device: capture timing math must never
        # believe a rate the hardware is not running.
        applied = int(hz)
        try:
            applied = int(p.split()[0], 0)
        except (ValueError, IndexError):
            stq, pq = self._ask_busy("FREQ?")
            if stq == "OK":
                try:
                    applied = int(pq.split()[0], 0)
                except (ValueError, IndexError):
                    pass
        if applied != int(hz):
            warnings.warn(
                f"sample rate clamped to {applied} Hz by the device "
                f"(requested {hz} Hz; see tier() for this unit's ceiling)",
                RuntimeWarning, stacklevel=2)
        self._sample_rate_hz = applied

    def sample_rate_hz(self) -> int:
        """Return the current ADC sample rate in Hz."""
        st, p = self._ask_busy("FREQ?")
        if st != "OK":
            self._raise_cmd_error("FREQ?", p)
        rate = int(p, 0)
        self._sample_rate_hz = rate
        return rate

    def set_oversampling(self, os_idx: int) -> None:
        """Set the oversampling index (mk1: 0..7; mk2: 0..8)."""
        os_max = 8 if self._generation == "mk2" else 7
        if not (0 <= os_idx <= os_max):
            raise coreDAQError(f"OS must be 0..{os_max}")
        st, p = self._ask_busy(f"OS {os_idx}")
        if st != "OK":
            self._raise_cmd_error(f"OS {os_idx}", p)

    def oversampling(self) -> int:
        """Return the current oversampling index."""
        st, p = self._ask_busy("OS?")
        if st != "OK":
            self._raise_cmd_error("OS?", p)
        return int(p, 0)

    # ------------------------------------------------------------------
    # Environmental sensors
    # ------------------------------------------------------------------

    def head_temperature_c(self) -> float:
        """Return the optical head temperature in °C."""
        st, val = self._transport.ask("TEMP?")
        if st != "OK":
            self._raise_cmd_error("TEMP?", val)
        return float(val)

    def head_humidity_percent(self) -> float:
        """Return the optical head relative humidity in %."""
        st, val = self._transport.ask("HUM?")
        if st != "OK":
            self._raise_cmd_error("HUM?", val)
        return float(val)

    def die_temperature_c(self) -> float:
        """Return the MCU die temperature in °C."""
        st, val = self._transport.ask("DIE_TEMP?")
        if st != "OK":
            self._raise_cmd_error("DIE_TEMP?", val)
        return float(val)

    def refresh_device_state(self) -> None:
        """Re-read I2C sensor registers (temperature, humidity)."""
        self._transport.ask("I2C REFRESH")

    # ------------------------------------------------------------------
    # Identity and wavelength
    # ------------------------------------------------------------------

    def calibration_info(self, refresh: bool = False) -> dict:
        """Return parsed calibration metadata from the firmware CALINFO? command.

        The result is cached after the first successful query. Pass
        ``refresh=True`` to force a new query.

        Returns a dict with fields:

        - ``valid`` — bool, whether calibration data passed CRC check
        - ``status`` — str, firmware status token (e.g. ``"CAL_OK"``)
        - ``variant`` — str, detector/topology identifier
        - ``schema`` — str, calibration storage schema
        - ``serial`` — str, instrument serial number
        - ``calibration_wavelength_nm`` — float, reference wavelength used at calibration
        - ``slot_address`` — int, flash slot base address (hex-parsed)
        - ``payload_size`` — int, calibration payload size in bytes
        - ``crc32`` — int, CRC-32 of the stored payload (hex-parsed)
        - ``raw`` — str, the original payload string from the firmware

        Raises ``coreDAQCalibrationError`` if the firmware returns a non-OK
        response. Older firmware that does not implement ``CALINFO?`` will
        return an error — this method is safe to call speculatively and will
        raise rather than affecting any other API behaviour.
        """
        if self._calinfo_cache is not None and not refresh:
            return self._calinfo_cache
        st, payload = self._transport.ask("CALINFO?")
        if st != "OK":
            raise coreDAQCalibrationError(
                f"CALINFO? not supported or failed: {payload!r}. "
                "This firmware may not implement CALINFO?."
            )
        result = _parse_calinfo_payload(payload)
        self._calinfo_cache = result
        return result

    def get_calibration_info(self, refresh: bool = False) -> dict:
        """Alias for :meth:`calibration_info`."""
        return self.calibration_info(refresh=refresh)

    def firmware_version(self) -> tuple[int, int, int]:
        """Return the firmware version as (major, minor, patch).

        Parsed from the IDN string at connection time. Returns ``(0, 0, 0)``
        if the IDN string does not contain a recognisable version token.
        """
        return self._firmware_version

    def _fw_at_least(self, major: int, minor: int) -> bool:
        """Boolean twin of _require_firmware: mk2 satisfies every mk1 gate.

        mk1 firmware versions are v4.x while mk2 restarted at v1.0, so a raw
        tuple compare misroutes mk2 — always gate through this helper.
        """
        if getattr(self, "_generation", "mk1") == "mk2":
            return True
        return self._firmware_version >= (major, minor, 0)

    def _require_firmware(self, major: int, minor: int, feature: str) -> None:
        """Raise coreDAQUnsupportedError if firmware is older than required.

        The version gate applies only to the mk1 firmware line. mk2 ships as a
        separate v1.x family that implements the full feature set (FRAMES?,
        stepped trigger, cal metadata, …) regardless of its version number.
        """
        if getattr(self, "_generation", "mk1") == "mk2":
            return
        if self._firmware_version < (major, minor, 0):
            fw = ".".join(str(x) for x in self._firmware_version)
            raise coreDAQUnsupportedError(
                f"{feature} requires firmware v{major}.{minor} or newer "
                f"(this device reports v{fw or 'unknown'}). "
                f"Please update the device firmware."
            )

    def serial_number(self) -> str:
        """Return the instrument serial number from calibration metadata.

        Requires firmware >= 4.1. Raises ``coreDAQUnsupportedError`` on
        older firmware.
        """
        self._require_firmware(4, 1, "serial_number()")
        return self.calibration_info()["serial"]

    def calibration_status(self) -> str:
        """Return the calibration status token (e.g. ``"CAL_OK"``).

        Requires firmware >= 4.1.
        """
        self._require_firmware(4, 1, "calibration_status()")
        return self.calibration_info()["status"]

    def is_calibration_valid(self) -> bool:
        """Return ``True`` if the stored calibration passed its CRC check.

        Requires firmware >= 4.1.
        """
        self._require_firmware(4, 1, "is_calibration_valid()")
        return self.calibration_info()["valid"]

    def calibration_serial(self) -> str:
        """Alias for :meth:`serial_number`."""
        return self.serial_number()

    def calibration_date(self) -> "Optional[__import__('datetime').date]":
        """Return the calibration date as a ``datetime.date``, or ``None`` if not recorded.

        Requires firmware >= 4.2 and a v2 calibration image.
        """
        try:
            return self.calibration_info().get("cal_date")
        except Exception:
            return None

    def calibration_wavelength_nm(self) -> float:
        """Return the primary calibration wavelength in nm (from the cal image).

        This is the wavelength at which the device was calibrated and is also
        set as the default operating wavelength on connect.
        """
        try:
            return float(self.calibration_info().get("calibration_wavelength_nm", 0.0))
        except Exception:
            return self._wavelength_nm

    def num_calibration_wavelengths(self) -> int:
        """Return the number of wavelength calibrations stored on the device.

        Currently always 1. >1 will be supported in a future firmware/cal image
        revision when multi-wavelength calibration is available.
        """
        try:
            return int(self.calibration_info().get("num_wavelengths", 1))
        except Exception:
            return 1

    def calibration_variant(self) -> str:
        """Return the calibration variant string, e.g. ``"INGAAS_LINEAR"``."""
        try:
            return self.calibration_info().get("variant", "")
        except Exception:
            return ""

    def identify(self, refresh: bool = False) -> str:
        """Return the raw IDN string from the device."""
        if refresh or not self._idn_cache:
            st, p = self._transport.ask("IDN?")
            if st != "OK":
                self._raise_cmd_error("IDN?", p)
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

    def generation(self) -> str:
        """Return the device generation: ``"mk1"`` (F730) or ``"mk2"`` (F746)."""
        return self._generation

    def channel_count(self) -> int:
        """Return the number of channels (4 on mk1, 5 on mk2)."""
        return self._n_channels

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
    # mk2-only API surface (identity/tier, sensors, networking)
    #
    # These read or configure mk2 features. They raise coreDAQUnsupportedError
    # on mk1 (where they are inapplicable). SECURITY: tier() only *reads*
    # TIER?; there is no unlock/license path in this driver — high-bandwidth
    # tier limits are enforced in firmware and must never be worked around
    # locally. If a tier-gated operation is refused, the firmware ERR surfaces
    # as a clean exception.
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_kv(payload: str) -> Dict[str, str]:
        """Parse space-separated ``KEY=VALUE`` tokens into an upper-key dict."""
        kv: Dict[str, str] = {}
        for tok in payload.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                kv[k.strip().upper()] = v.strip()
        return kv

    def _raise_cmd_error(self, context: str, payload: str) -> None:
        """Raise the typed exception for a firmware ERR reply (see _exceptions)."""
        raise error_for_payload(context, payload)

    def _require_mk2(self, method: str) -> None:
        if getattr(self, "_generation", "mk1") != "mk2":
            raise coreDAQUnsupportedError(
                f"{method} is only available on coreDAQ mk2 devices "
                f"(this device is a coreDAQ mk1)."
            )

    def tier(self) -> Dict[str, Any]:
        """Return the mk2 licensing tier (read-only; parses ``TIER?``).

        Reports the firmware-enforced bandwidth tier so callers can inform the
        user. Keys: ``tier`` (``"LOW"``/``"HIGH"``), ``fw``
        (``"LOWBW"``/``"HIGHBW"``), ``variant`` (``"LINEAR"``/``"LOG"``),
        ``lock`` (``"MATCH"``/``"LOCKED"``/``"UNPROVISIONED"``/``"N/A"``),
        ``fmax`` (max sample rate in Hz), plus ``raw``.

        This method never attempts to change or bypass the tier — high-rate
        limits are enforced in firmware.
        """
        self._require_mk2("tier()")
        st, p = self._transport.ask("TIER?")
        if st != "OK":
            self._raise_cmd_error("TIER?", p)
        kv = self._parse_kv(p)
        try:
            fmax = int(kv.get("FMAX", "0"), 0)
        except ValueError:
            fmax = 0
        return {
            "tier": kv.get("TIER", ""),
            "fw": kv.get("FW", ""),
            "variant": kv.get("VARIANT", ""),
            "lock": kv.get("LOCK", ""),
            "fmax": fmax,
            "high_bandwidth": kv.get("TIER", "").upper() == "HIGH",
            # Customer-facing tier name (wire tokens stay LOW/HIGH):
            "name": {"LOW": "base", "HIGH": "high-performance"}.get(
                kv.get("TIER", "").upper(), kv.get("TIER", "").lower()),
            # Multi-unit sync availability. New firmware reports SYNC=<0|1>;
            # older firmware omits it -> infer from the tier (sync is a
            # High Performance feature).
            "sync": (kv["SYNC"] == "1") if "SYNC" in kv
                    else kv.get("TIER", "").upper() == "HIGH",
            "raw": p,
        }

    def _read_sensor(self, cmd: str) -> Optional[float]:
        """Query a sensor command; return float, or None on ERR (no sensor)."""
        st, p = self._transport.ask(cmd)
        if st == "OK":
            try:
                return float(p.split()[0])
            except (ValueError, IndexError):
                raise coreDAQError(f"{cmd} returned unparseable payload: {p!r}")
        # ERR NO_SENSOR / ERR ADC — the sensor is absent or unavailable.
        return None

    def temperature(self) -> Optional[float]:
        """Return the mk2 board temperature in °C, or ``None`` if no sensor."""
        self._require_mk2("temperature()")
        return self._read_sensor("TEMP?")

    def humidity(self) -> Optional[float]:
        """Return the mk2 relative humidity in %, or ``None`` if no sensor."""
        self._require_mk2("humidity()")
        return self._read_sensor("HUM?")

    def die_temperature(self) -> Optional[float]:
        """Return the mk2 MCU die temperature in °C, or ``None`` if unavailable."""
        self._require_mk2("die_temperature()")
        return self._read_sensor("DIE_TEMP?")

    def uid(self) -> str:
        """Return the mk2 device unique ID (hex string from ``UID?``)."""
        self._require_mk2("uid()")
        st, p = self._transport.ask("UID?")
        if st != "OK":
            self._raise_cmd_error("UID?", p)
        toks = p.split()
        return toks[0] if toks else p.strip()

    def sysstat(self) -> Dict[str, Any]:
        """Return mk2 system diagnostics (parsed ``SYSSTAT?`` key/values).

        Numeric fields (uptime, heap, stack, I2C error count) are ints; text
        fields (SHT/TCA presence) stay strings. ``raw`` holds the payload.
        """
        self._require_mk2("sysstat()")
        st, p = self._transport.ask("SYSSTAT?")
        if st != "OK":
            self._raise_cmd_error("SYSSTAT?", p)
        out: Dict[str, Any] = {}
        for k, v in self._parse_kv(p).items():
            try:
                out[k.lower()] = int(v, 0)
            except ValueError:
                out[k.lower()] = v
        out["raw"] = p
        return out

    def ip_config(self) -> Dict[str, str]:
        """Return the mk2 network configuration (parsed ``IPCFG?``).

        Keys: ``mode`` (``"DHCP"``/``"STATIC"``), ``ip``, ``mask``,
        ``gateway``, ``raw``.
        """
        self._require_mk2("ip_config()")
        st, p = self._transport.ask("IPCFG?")
        if st != "OK":
            self._raise_cmd_error("IPCFG?", p)
        kv = self._parse_kv(p)
        return {
            "mode": kv.get("MODE", ""),
            "ip": kv.get("IP", ""),
            "mask": kv.get("MASK", ""),
            "gateway": kv.get("GW", ""),
            "raw": p,
        }

    def set_ip_dhcp(self) -> None:
        """Switch the mk2 to DHCP addressing (``IPCFG DHCP``). Flash-persisted."""
        self._require_mk2("set_ip_dhcp()")
        st, p = self._transport.ask("IPCFG DHCP")
        if st != "OK":
            self._raise_cmd_error("IPCFG DHCP", p)

    def set_ip_static(self, ip: str, mask: str, gateway: str) -> None:
        """Set a static mk2 IP configuration (``IPCFG STATIC``). Flash-persisted.

        *ip*, *mask* and *gateway* are dotted IPv4 strings.
        """
        self._require_mk2("set_ip_static()")
        for label, val in (("ip", ip), ("mask", mask), ("gateway", gateway)):
            if not self._is_ipv4(val):
                raise ValueError(f"{label} must be a dotted IPv4 address, got {val!r}")
        st, p = self._transport.ask(f"IPCFG STATIC {ip} {mask} {gateway}")
        if st != "OK":
            self._raise_cmd_error("IPCFG STATIC", p)

    @staticmethod
    def _is_ipv4(s: Any) -> bool:
        parts = str(s).split(".")
        if len(parts) != 4:
            return False
        for octet in parts:
            if not octet.isdigit() or not (0 <= int(octet) <= 255):
                return False
        return True

    # ------------------------------------------------------------------
    # coreLINK master/slave synchronisation (mk2)
    # ------------------------------------------------------------------
    def sync_mode(self) -> str:
        """Return the mk2 coreLINK sync role: ``"MASTER"`` or ``"SLAVE"``.

        MASTER/standalone uses the unit's own timebase to drive CONVST (and
        exports it over LVDS to any slaves). SLAVE takes CONVST from the
        master's LVDS SAMPLE, so chained units convert in lockstep. Parses
        ``SYNC?``.
        """
        self._require_mk2("sync_mode()")
        st, p = self._transport.ask("SYNC?")
        if st != "OK":
            self._raise_cmd_error("SYNC?", p)
        return self._parse_kv(p).get("MODE", "")

    def set_sync_mode(self, mode: str) -> str:
        """Set the mk2 coreLINK sync role (``SYNC MASTER`` / ``SYNC SLAVE``).

        *mode* is ``"master"`` (or ``"standalone"``) or ``"slave"``,
        case-insensitive. Flash-persisted, so a unit provisioned as a slave
        boots as a slave. This only selects the CONVST source mux; actually
        acquiring in SLAVE mode requires a master unit driving the LVDS SAMPLE
        line (otherwise a capture will stall and abort). Returns the applied
        mode.
        """
        self._require_mk2("set_sync_mode()")
        m = str(mode).strip().upper()
        if m == "STANDALONE":
            m = "MASTER"
        if m not in ("MASTER", "SLAVE"):
            raise ValueError(f"mode must be 'master'/'standalone' or 'slave', got {mode!r}")
        st, p = self._transport.ask(f"SYNC {m}")
        if st != "OK":
            if p.split() and p.split()[0].upper() == "LICENSE":
                raise coreDAQLicenseError(
                    "multi-unit sync (coreLOOM) requires the High Performance "
                    "tier; this unit reports the Base tier (see tier()). "
                    "There is no software unlock — contact Core Instrumentation "
                    "to upgrade the unit.")
            self._raise_cmd_error(f"SYNC {m}", p)
        return m

    def eth_status(self) -> Dict[str, Any]:
        """Return the mk2 Ethernet link status (parsed ``ETH?``).

        Keys: ``link_up`` (bool), ``link`` (``"UP"``/``"DOWN"``), ``ip``,
        ``mask``, ``gateway``, ``mac``, ``port`` (int), ``raw``.
        """
        self._require_mk2("eth_status()")
        st, p = self._transport.ask("ETH?")
        if st != "OK":
            self._raise_cmd_error("ETH?", p)
        kv = self._parse_kv(p)
        try:
            port = int(kv.get("PORT", "0"), 0)
        except ValueError:
            port = 0
        return {
            "link_up": kv.get("LINK", "").upper() == "UP",
            "link": kv.get("LINK", ""),
            "ip": kv.get("IP", ""),
            "mask": kv.get("MASK", ""),
            "gateway": kv.get("GW", ""),
            "mac": kv.get("MAC", ""),
            "port": port,
            "raw": p,
        }

    # ------------------------------------------------------------------
    # Advanced / low-level
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Soft-reset the device firmware."""
        st, p = self._transport.ask("SOFTRESET")
        if st != "OK":
            self._raise_cmd_error("SOFTRESET", p)

    def enter_dfu_mode(self) -> None:
        """Enter DFU (firmware update) mode."""
        self._transport.drain()
        self._transport.ask("DFU")

    def capture_buffer_address(self) -> int:
        """Return the current SDRAM write address (for diagnostics)."""
        st, p = self._transport.ask("ADDR?")
        if st != "OK":
            self._raise_cmd_error("ADDR?", p)
        return int(p, 0)
