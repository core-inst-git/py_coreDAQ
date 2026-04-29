"""Public coreDAQ API — the coreDAQ class and all public dataclasses.

Typical usage::

    with coreDAQ.connect() as pm:           # auto-discovers device
        print(pm.read_channel(0))           # watts

    with coreDAQ.connect(simulator=True) as pm:
        result = pm.capture(frames=1000)
        print(result.trace(0))
"""
from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from ._driver import _CoreDAQDriver
from ._exceptions import (
    CoreDAQError,
    coreDAQConnectionError,
    coreDAQError,
    coreDAQTimeoutError,
    coreDAQUnsupportedError,
)
from ._transport import SerialTransport

# ---------------------------------------------------------------------------
# Public dataclasses
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
        """Return the reading for channel *channel* (0..3)."""
        key = int(channel)
        for reading in self.readings:
            if reading.channel == key:
                return reading
        raise ValueError(f"channel {channel} not present in this MeasurementSet")

    def values(self) -> List[Union[int, float]]:
        """Return a plain list of scalar values in channel order."""
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
    """Block-capture result from capture() / get_data()."""
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
        """Return the list of values for *channel*."""
        key = int(channel)
        if key not in self.traces:
            raise ValueError(f"channel {channel} not present in this capture")
        return self.traces[key]

    def status(self, channel: int) -> CaptureChannelStatus:
        """Return clip/range status for *channel*."""
        key = int(channel)
        if key not in self.statuses:
            raise ValueError(f"channel {channel} not present in this capture")
        return self.statuses[key]


# ---------------------------------------------------------------------------
# ChannelProxy
# ---------------------------------------------------------------------------


class ChannelProxy:
    """Channel-scoped view into a coreDAQ device.

    Do not instantiate directly — use ``meter.channels[n]``::

        ch = pm.channels[0]
        print(ch.power_w)
        ch.set_range_power(1e-3)
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
        """Read optical power in the requested unit."""
        return self._meter.read_channel(self._channel, unit=unit, autoRange=autoRange, n_samples=n_samples)

    def read_full(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> ChannelReading:
        """Read with full measurement metadata."""
        return self._meter.read_channel_full(self._channel, unit=unit, autoRange=autoRange, n_samples=n_samples)

    @property
    def range(self) -> Optional[int]:
        """Current TIA gain range index (0..7), or None on LOG frontends."""
        return self._meter.get_range(self._channel)

    def set_range(self, range_index: int) -> None:
        """Set the TIA gain range. See coreDAQ.set_range()."""
        self._meter.set_range(self._channel, range_index)

    def set_range_power(self, power_w: float) -> int:
        """Select the best range for a target optical power level.

        Returns the selected range index.
        """
        return self._meter.set_range_power(self._channel, power_w)

    def signal_status(self) -> SignalStatus:
        """Return signal health for this channel."""
        return self._meter.signal_status(self._channel)

    def is_clipped(self) -> bool:
        """Return True if the channel is over-range or under-range."""
        return bool(self._meter.is_clipped(self._channel))

    def __repr__(self) -> str:
        return f"<ChannelProxy ch={self._channel}>"


# ---------------------------------------------------------------------------
# coreDAQ — main public class
# ---------------------------------------------------------------------------


class coreDAQ:
    """Python driver for the coreDAQ 4-channel optical power meter.

    Preferred entry point::

        with coreDAQ.connect() as pm:       # auto-discovers device
            print(pm.read_all())

        with coreDAQ.connect(simulator=True) as pm:
            result = pm.capture(frames=500)

    Direct construction (when you already know the port)::

        with coreDAQ("/dev/tty.usbmodem1") as pm:
            print(pm.read_channel(0))
    """

    VALID_UNITS = ("w", "dbm", "v", "mv", "adc")
    DEFAULT_READING_UNIT = "w"
    DEFAULT_SAMPLE_RATE_HZ = 500
    DEFAULT_OVERSAMPLING = 1
    MAX_READ_SAMPLES = 32
    AUTO_RANGE_MIN_MV = 50.0
    AUTO_RANGE_MAX_VOLTS = 4.0
    AUTO_RANGE_MAX_ITERS = 4
    AUTO_RANGE_SETTLE_S = 0.005
    OVER_RANGE_VOLTS = 4.2
    UNDER_RANGE_MV = 5.0

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        port: str,
        timeout: float = 0.15,
        inter_command_gap_s: float = 0.0,
    ) -> None:
        transport = SerialTransport(port, timeout=timeout, inter_command_gap_s=inter_command_gap_s)
        self._init_from_transport(transport)

    def _init_from_transport(self, transport: Any) -> None:
        try:
            self._driver = _CoreDAQDriver(transport)
        except CoreDAQError as exc:
            raise coreDAQConnectionError(str(exc)) from exc
        self._reading_unit = self.DEFAULT_READING_UNIT
        self._zero_source = (
            "factory"
            if self._driver.frontend_type() == self._driver.FRONTEND_LINEAR
            else "not_applicable"
        )
        self.set_oversampling(self.DEFAULT_OVERSAMPLING)
        self.set_sample_rate_hz(self.DEFAULT_SAMPLE_RATE_HZ)

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
            Serial port path (e.g. ``"/dev/tty.usbmodemXXXX"`` or ``"COM3"``).
            If ``None``, auto-discovers via ``discover()`` and connects to the
            first found device.
        simulator : bool
            If ``True``, return a fully functional simulated device.
            Accepts keyword arguments forwarded to ``SimTransport``:
            ``frontend``, ``detector``, ``incident_power_w``,
            ``wavelength_nm``, ``noise_sigma_adc``, ``seed``.
        baudrate, timeout, inter_command_gap_s
            Passed to ``SerialTransport`` (real hardware only).

        Returns
        -------
        coreDAQ
            A connected (or simulated) device handle.

        Raises
        ------
        coreDAQConnectionError
            Zero or more than one device found during auto-discovery, or the
            device did not respond to IDN? within *timeout*.

        Examples
        --------
        Auto-discover real hardware::

            with coreDAQ.connect() as pm:
                print(pm.read_channel(0))

        Simulator::

            with coreDAQ.connect(simulator=True) as pm:
                print(pm.read_channel(0))

        Si LINEAR simulator::

            with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="SILICON") as pm:
                print(pm.frontend())
        """
        instance = object.__new__(cls)
        if simulator:
            from ._simulator import SimTransport
            transport = SimTransport(**sim_kwargs)
        elif port is not None:
            transport = SerialTransport(
                port,
                baudrate=baudrate,
                timeout=timeout,
                inter_command_gap_s=inter_command_gap_s,
            )
        else:
            ports = SerialTransport.find_ports(baudrate=baudrate, timeout=timeout)
            if len(ports) == 0:
                raise coreDAQConnectionError(
                    "No coreDAQ device found. Check the USB-C cable and serial permissions."
                )
            if len(ports) > 1:
                raise coreDAQConnectionError(
                    f"Multiple coreDAQ devices found: {ports}. "
                    "Pass port= explicitly to select one."
                )
            transport = SerialTransport(
                ports[0],
                baudrate=baudrate,
                timeout=timeout,
                inter_command_gap_s=inter_command_gap_s,
            )
        instance._init_from_transport(transport)
        return instance

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "coreDAQ":
        return self

    def __exit__(self, et: Any, ev: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Release the serial port (or simulator)."""
        self._driver.close()

    # ------------------------------------------------------------------
    # ChannelProxy access
    # ------------------------------------------------------------------

    @property
    def channels(self) -> List[ChannelProxy]:
        """Four ChannelProxy objects indexed 0..3."""
        return [ChannelProxy(self, ch) for ch in range(4)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except CoreDAQError as exc:
            raise coreDAQError(str(exc)) from exc

    @staticmethod
    def _resolve_auto_range(autoRange: bool, kwargs: Dict[str, Any]) -> bool:
        if "autorange" in kwargs:
            autoRange = kwargs.pop("autorange")
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"unexpected keyword argument(s): {unexpected}")
        return bool(autoRange)

    @classmethod
    def _normalize_unit(cls, unit: Optional[str]) -> str:
        if unit is None:
            return cls.DEFAULT_READING_UNIT
        token = str(unit).strip().lower()
        aliases = {
            "w": "w", "watt": "w", "watts": "w",
            "dbm": "dbm",
            "v": "v", "volt": "v", "volts": "v",
            "mv": "mv", "millivolt": "mv", "millivolts": "mv",
            "adc": "adc", "raw": "adc", "raw_adc": "adc",
            "adccode": "adc", "adc_code": "adc",
        }
        normalized = aliases.get(token)
        if normalized not in cls.VALID_UNITS:
            raise ValueError(f"unit must be one of {', '.join(cls.VALID_UNITS)}")
        return normalized

    @staticmethod
    def _normalize_channel(channel: int) -> int:
        ch = int(channel)
        if ch not in (0, 1, 2, 3):
            raise ValueError("channel must be 0..3")
        return ch

    @classmethod
    def _normalize_n_samples(cls, n_samples: int) -> int:
        value = int(n_samples)
        if not (1 <= value <= cls.MAX_READ_SAMPLES):
            raise ValueError(f"n_samples must be between 1 and {cls.MAX_READ_SAMPLES}")
        return value

    @classmethod
    def _normalize_channels(
        cls, channels: Optional[Union[int, Sequence[int]]]
    ) -> Optional[Tuple[int, ...]]:
        if channels is None:
            return None
        if isinstance(channels, int):
            return (cls._normalize_channel(channels),)
        normalized = [cls._normalize_channel(c) for c in channels]
        if not normalized:
            raise ValueError("channels must not be empty")
        return tuple(sorted(set(normalized)))

    @staticmethod
    def _all_channels() -> Tuple[int, ...]:
        return (0, 1, 2, 3)

    @staticmethod
    def _channels_to_mask(channels: Sequence[int]) -> int:
        mask = 0
        for ch in channels:
            mask |= 1 << int(ch)
        return mask

    @staticmethod
    def _mask_to_channels(mask: int) -> Tuple[int, ...]:
        return tuple(i for i in range(4) if mask & (1 << i))

    @classmethod
    def _normalize_capture_channel_mask(cls, mask: Union[int, str]) -> int:
        if isinstance(mask, str):
            token = str(mask).strip().replace(" ", "").replace("_", "")
            if not token:
                raise ValueError("capture_channel_mask must not be empty")
            tl = token.lower()
            if tl.startswith("0b"):
                value = int(tl[2:], 2)
            elif tl.startswith("0x"):
                value = int(tl, 16)
            elif set(token) <= {"0", "1"}:
                value = int(token, 2)
            else:
                value = int(token, 10)
        else:
            value = int(mask)
        if not (0 <= value <= 0x0F):
            raise ValueError("capture_channel_mask must only use bits 0..3")
        return value

    @classmethod
    def _power_dbm(cls, power_w: float) -> float:
        if not math.isfinite(power_w) or power_w <= 0.0:
            return float("-inf")
        return float(10.0 * math.log10(power_w / 1e-3))

    @classmethod
    def _signal_flags(cls, signal_v: float, signal_mv: float) -> Tuple[bool, bool, bool]:
        over_range = abs(float(signal_v)) > cls.OVER_RANGE_VOLTS
        under_range = abs(float(signal_mv)) < cls.UNDER_RANGE_MV
        return over_range, under_range, bool(over_range or under_range)

    @staticmethod
    def _value_for_unit(
        unit: str,
        power_w: float,
        power_dbm: float,
        signal_v: float,
        signal_mv: float,
        adc_code: int,
    ) -> Union[int, float]:
        if unit == "w":
            return power_w
        if unit == "dbm":
            return power_dbm
        if unit == "v":
            return signal_v
        if unit == "mv":
            return signal_mv
        return int(adc_code)

    def _range_label(self, range_index: Optional[int]) -> Optional[str]:
        if range_index is None:
            return None
        return self._driver.gain_label(int(range_index), self._call(self._driver.gain_profile))

    # ------------------------------------------------------------------
    # Reading unit
    # ------------------------------------------------------------------

    def set_reading_unit(self, unit: str) -> None:
        """Set the default output unit for all read_* calls."""
        self._reading_unit = self._normalize_unit(unit)

    def reading_unit(self) -> str:
        """Return the current default output unit."""
        return self._reading_unit

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def identify(self, refresh: bool = False) -> str:
        """Return the raw IDN string from the device."""
        return self._call(self._driver.idn, refresh=refresh)

    def device_info(self, refresh: bool = False) -> DeviceInfo:
        """Return a snapshot of device identity."""
        return DeviceInfo(
            raw_idn=self.identify(refresh=refresh),
            frontend=self._driver.frontend_type(),
            detector=self._driver.detector_type(),
            gain_profile=self._call(self._driver.gain_profile, refresh=refresh),
            port=self._driver.port_name(),
        )

    def frontend(self) -> str:
        """Return ``"LINEAR"`` or ``"LOG"``."""
        return self._driver.frontend_type()

    def detector(self) -> str:
        """Return ``"INGAAS"`` or ``"SILICON"``."""
        return self._driver.detector_type()

    # ------------------------------------------------------------------
    # Wavelength
    # ------------------------------------------------------------------

    def wavelength_limits_nm(self, detector: Optional[str] = None) -> Tuple[float, float]:
        """Return (min_nm, max_nm) for the detector's valid wavelength range."""
        return self._call(self._driver.get_wavelength_limits_nm, detector)

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        """Set the operating wavelength in nm."""
        self._call(self._driver.set_wavelength_nm, wavelength_nm)

    def wavelength_nm(self) -> float:
        """Return the current operating wavelength in nm."""
        return self._driver.get_wavelength_nm()

    def responsivity_a_per_w(
        self, wavelength_nm: float, detector: Optional[str] = None
    ) -> float:
        """Return the detector responsivity (A/W) at *wavelength_nm*."""
        return self._call(
            self._driver.get_responsivity_A_per_W,
            detector=detector,
            wavelength_nm=float(wavelength_nm),
        )

    # ------------------------------------------------------------------
    # Ranges (LINEAR only)
    # ------------------------------------------------------------------

    def supported_ranges(self) -> List[Dict[str, Union[int, float, str]]]:
        """Return all valid range indices with labels and full-scale powers."""
        profile = self._call(self._driver.gain_profile)
        labels = self._driver.gain_labels(profile)
        limits = self._driver.gain_max_power_table(profile)
        return [
            {"range_index": idx, "label": labels[idx], "max_power_w": limits[idx]}
            for idx in range(len(labels))
        ]

    def _choose_range_index(self, power_w: float) -> int:
        requested = abs(float(power_w))
        if not math.isfinite(requested):
            raise ValueError("power_w must be finite")
        limits = self._driver.gain_max_power_table(self._call(self._driver.gain_profile))
        fitting = [idx for idx, limit in enumerate(limits) if requested <= float(limit)]
        return int(fitting[-1]) if fitting else 0

    def get_range(self, channel: int) -> Optional[int]:
        """Return the current gain range index for *channel*, or None on LOG."""
        ch = self._normalize_channel(channel)
        if self.frontend() != self._driver.FRONTEND_LINEAR:
            return None
        gains = self._call(self._driver.get_gains)
        return int(gains[ch])

    def get_ranges(self) -> List[Optional[int]]:
        """Return current range indices for all four channels."""
        return [self.get_range(ch) for ch in self._all_channels()]

    def set_range(self, channel: int, range_index: int) -> None:
        """Set the TIA gain range for one channel (LINEAR only)."""
        ch = self._normalize_channel(channel)
        idx = int(range_index)
        if not (0 <= idx < self._driver.NUM_GAINS):
            raise ValueError("range_index must be 0..7")
        self._call(self._driver.set_gain, ch + 1, idx)

    def set_ranges(self, range_indices: Sequence[int]) -> List[Optional[int]]:
        """Set range indices for all four channels."""
        values = [int(v) for v in range_indices]
        if len(values) != 4:
            raise ValueError("range_indices must have exactly 4 elements")
        for ch, idx in zip(self._all_channels(), values):
            self.set_range(ch, idx)
        return self.get_ranges()

    def set_range_power(self, channel: int, power_w: float) -> int:
        """Select the best range for a target optical power; return chosen index."""
        range_index = self._choose_range_index(power_w)
        self.set_range(channel, range_index)
        return range_index

    def set_range_powers(self, power_w_values: Sequence[float]) -> List[Optional[int]]:
        """Call set_range_power for all four channels."""
        values = [float(v) for v in power_w_values]
        if len(values) != 4:
            raise ValueError("power_w_values must have exactly 4 elements")
        for ch, pw in zip(self._all_channels(), values):
            self.set_range_power(ch, pw)
        return self.get_ranges()

    # Deprecated aliases
    def set_power_range(self, channel: int, range_index: int) -> None:
        warnings.warn("set_power_range() is deprecated; use set_range().", DeprecationWarning, stacklevel=2)
        self.set_range(channel, range_index)

    def get_range_all(self) -> List[Optional[int]]:
        warnings.warn("get_range_all() is deprecated; use get_ranges().", DeprecationWarning, stacklevel=2)
        return self.get_ranges()

    def current_ranges(self) -> List[Optional[int]]:
        warnings.warn("current_ranges() is deprecated; use get_ranges().", DeprecationWarning, stacklevel=2)
        return self.get_ranges()

    # ------------------------------------------------------------------
    # Zeroing (LINEAR only)
    # ------------------------------------------------------------------

    def zero_offsets_adc(self) -> Tuple[int, int, int, int]:
        """Return the active zero offsets in ADC counts (CH0..CH3)."""
        return self._call(self._driver.get_linear_zero_adc)

    def factory_zero_offsets_adc(self) -> Tuple[int, int, int, int]:
        """Return the factory-stored zero offsets in ADC counts."""
        return self._call(self._driver.get_factory_zero_adc)

    def zero_dark(
        self, frames: int = 32, settle_s: float = 0.2
    ) -> Tuple[int, int, int, int]:
        """Capture a dark baseline and set it as the active zero offset.

        Block the input (or cover the fiber end) before calling this.
        Raises ``coreDAQUnsupportedError`` on LOG frontends.
        """
        if self.frontend() != self._driver.FRONTEND_LINEAR:
            raise coreDAQUnsupportedError(
                "zero_dark() is not supported on LOG frontends."
            )
        self._call(self._driver.soft_zero_from_snapshot, n_frames=frames, settle_s=settle_s)
        self._zero_source = "user"
        return self.zero_offsets_adc()

    def restore_factory_zero(self) -> Tuple[int, int, int, int]:
        """Restore the factory-stored zero offsets."""
        self._call(self._driver.restore_factory_zero)
        if self.frontend() == self._driver.FRONTEND_LINEAR:
            self._zero_source = "factory"
        return self.zero_offsets_adc()

    # ------------------------------------------------------------------
    # Live reading internals
    # ------------------------------------------------------------------

    def _zero_linear_codes(self, raw_codes: Sequence[int]) -> List[int]:
        zero_offsets = getattr(self._driver, "_linear_zero_adc", (0, 0, 0, 0))
        return [int(raw_codes[i]) - int(zero_offsets[i]) for i in range(4)]

    def _auto_range_code_limits(self) -> Tuple[int, int]:
        min_code = int(math.ceil(self.AUTO_RANGE_MIN_MV / self._driver.ADC_LSB_MV))
        max_code = int(math.floor(self.AUTO_RANGE_MAX_VOLTS / self._driver.ADC_LSB_VOLTS))
        return max(0, min_code), max(max_code, min_code)

    def _choose_auto_range_index(
        self,
        code_abs: int,
        current_range: int,
        limits: Sequence[float],
        min_code: int,
        max_code: int,
    ) -> int:
        if not limits:
            return int(current_range)
        current_limit = float(limits[current_range])
        predictions: List[Tuple[int, float]] = []
        fitting: List[int] = []

        for candidate, candidate_limit in enumerate(limits):
            if float(candidate_limit) <= 0.0:
                continue
            predicted = abs(float(code_abs)) * (current_limit / float(candidate_limit))
            predictions.append((candidate, predicted))
            if min_code <= predicted <= max_code:
                fitting.append(candidate)

        if fitting:
            return int(max(fitting))
        if predictions:
            _, high_code = predictions[-1]
            if high_code < min_code:
                return predictions[-1][0]
            _, low_code = predictions[0]
            if low_code > max_code:
                return predictions[0][0]

            def dist(item: Tuple[int, float]) -> Tuple[float, int]:
                c, pc = item
                d = float(min_code) - pc if pc < min_code else (pc - float(max_code) if pc > max_code else 0.0)
                return d, -c

            return int(min(predictions, key=dist)[0])
        return int(current_range)

    def _read_linear_codes_and_ranges(
        self,
        channels: Sequence[int],
        auto_range: bool,
        n_samples: int,
    ) -> Tuple[List[int], List[int]]:
        if not auto_range:
            raw_codes, gains = self._call(self._driver.snapshot_adc, n_frames=n_samples)
            return self._zero_linear_codes(raw_codes), gains

        target_channels = tuple(self._normalize_channel(ch) for ch in channels)
        if not target_channels:
            raw_codes, gains = self._call(self._driver.snapshot_adc, n_frames=n_samples)
            return self._zero_linear_codes(raw_codes), gains

        min_code, max_code = self._auto_range_code_limits()
        limits = self._driver.gain_max_power_table(self._call(self._driver.gain_profile))

        for _ in range(self.AUTO_RANGE_MAX_ITERS):
            raw_codes, gains = self._call(self._driver.snapshot_adc, n_frames=n_samples)
            zeroed_codes = self._zero_linear_codes(raw_codes)
            pending: Dict[int, int] = {}
            for ch in target_channels:
                desired = self._choose_auto_range_index(
                    abs(int(zeroed_codes[ch])), int(gains[ch]), limits, min_code, max_code
                )
                if desired != int(gains[ch]):
                    pending[ch] = desired
            if not pending:
                return zeroed_codes, [int(g) for g in gains]
            for ch, idx in pending.items():
                self._call(self._driver.set_gain, ch + 1, idx)
            time.sleep(self.AUTO_RANGE_SETTLE_S)

        raw_codes, gains = self._call(self._driver.snapshot_adc, n_frames=n_samples)
        return self._zero_linear_codes(raw_codes), [int(g) for g in gains]

    def _build_channel_reading(
        self,
        channel: int,
        adc_code: int,
        range_index: Optional[int],
        unit: str,
        frontend: str,
        detector: str,
        wavelength_nm: float,
    ) -> ChannelReading:
        signal_v_raw = float(adc_code) * self._driver.ADC_LSB_VOLTS
        signal_mv = round(signal_v_raw * 1e3, self._driver.MV_OUTPUT_DECIMALS)
        signal_v = signal_mv / 1000.0

        if frontend == self._driver.FRONTEND_LINEAR:
            if range_index is None:
                raise coreDAQError(f"Missing range index for channel {channel}")
            power_w = self._call(
                self._driver._convert_linear_mv_to_power_w,
                channel, int(range_index), float(signal_mv),
            )
        else:
            power_w = round(
                self._call(self._driver._convert_log_voltage_to_power_w, float(signal_v_raw), channel),
                self._driver.POWER_OUTPUT_DECIMALS_MAX,
            )

        power_dbm = self._power_dbm(power_w)
        over_range, under_range, clipped = self._signal_flags(signal_v, signal_mv)
        zero_source = self._zero_source if frontend == self._driver.FRONTEND_LINEAR else "not_applicable"

        return ChannelReading(
            channel=channel,
            value=self._value_for_unit(unit, power_w, power_dbm, signal_v, signal_mv, int(adc_code)),
            unit=unit,
            power_w=power_w,
            power_dbm=power_dbm,
            signal_v=signal_v,
            signal_mv=signal_mv,
            adc_code=int(adc_code),
            range_index=int(range_index) if range_index is not None else None,
            range_label=self._range_label(range_index),
            wavelength_nm=float(wavelength_nm),
            detector=detector,
            frontend=frontend,
            zero_source=zero_source,
            over_range=over_range,
            under_range=under_range,
            is_clipped=clipped,
        )

    def _collect_live_measurements(
        self,
        unit: Optional[str] = None,
        auto_range: bool = True,
        n_samples: int = 1,
        channels: Optional[Sequence[int]] = None,
    ) -> MeasurementSet:
        output_unit = self._normalize_unit(self._reading_unit if unit is None else unit)
        n_samples = self._normalize_n_samples(n_samples)
        frontend = self.frontend()
        detector = self.detector()
        wavelength_nm = self.wavelength_nm()
        requested_channels = self._all_channels() if channels is None else self._normalize_channels(channels)
        if not requested_channels:
            return MeasurementSet(readings=tuple(), unit=output_unit)

        if frontend == self._driver.FRONTEND_LINEAR:
            codes, gains = self._read_linear_codes_and_ranges(
                channels=requested_channels, auto_range=auto_range, n_samples=n_samples
            )
            range_indices: List[Optional[int]] = [int(g) for g in gains]
        else:
            codes, _gains = self._call(self._driver.snapshot_adc, n_frames=n_samples)
            range_indices = [None, None, None, None]

        readings = tuple(
            self._build_channel_reading(
                channel=ch,
                adc_code=int(codes[ch]),
                range_index=range_indices[ch],
                unit=output_unit,
                frontend=frontend,
                detector=detector,
                wavelength_nm=wavelength_nm,
            )
            for ch in requested_channels
        )
        return MeasurementSet(readings=readings, unit=output_unit)

    # ------------------------------------------------------------------
    # Public read methods
    # ------------------------------------------------------------------

    def read_all_full(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
        **kwargs: Any,
    ) -> MeasurementSet:
        """Read all four channels and return rich measurement objects."""
        auto_range = self._resolve_auto_range(autoRange, kwargs)
        return self._collect_live_measurements(unit=unit, auto_range=auto_range, n_samples=n_samples)

    def read_channel_full(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
        **kwargs: Any,
    ) -> ChannelReading:
        """Read one channel and return a rich measurement object."""
        ch = self._normalize_channel(channel)
        auto_range = self._resolve_auto_range(autoRange, kwargs)
        return self._collect_live_measurements(
            unit=unit, auto_range=auto_range, n_samples=n_samples, channels=(ch,)
        ).channel(ch)

    def read_all(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
        **kwargs: Any,
    ) -> List[Union[int, float]]:
        """Read all four channels; return a plain list of scalar values."""
        auto_range = self._resolve_auto_range(autoRange, kwargs)
        return self.read_all_full(unit=unit, autoRange=auto_range, n_samples=n_samples).values()

    def read_channel(
        self,
        channel: int,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
        **kwargs: Any,
    ) -> Union[int, float]:
        """Read one channel; return a plain scalar value."""
        auto_range = self._resolve_auto_range(autoRange, kwargs)
        return self.read_channel_full(
            channel=channel, unit=unit, autoRange=auto_range, n_samples=n_samples
        ).value

    # Deprecated aliases
    def read_all_details(self, unit: Optional[str] = None, autoRange: bool = True, n_samples: int = 1, **kw: Any) -> MeasurementSet:
        warnings.warn("read_all_details() is deprecated; use read_all_full().", DeprecationWarning, stacklevel=2)
        return self.read_all_full(unit=unit, autoRange=autoRange, n_samples=n_samples, **kw)

    def read_channel_details(self, channel: int, unit: Optional[str] = None, autoRange: bool = True, n_samples: int = 1, **kw: Any) -> ChannelReading:
        warnings.warn("read_channel_details() is deprecated; use read_channel_full().", DeprecationWarning, stacklevel=2)
        return self.read_channel_full(channel=channel, unit=unit, autoRange=autoRange, n_samples=n_samples, **kw)

    # ------------------------------------------------------------------
    # Signal health
    # ------------------------------------------------------------------

    def signal_status(
        self, channel: Optional[int] = None
    ) -> Union[SignalStatus, List[SignalStatus]]:
        """Return signal health for one channel (int) or all channels (None)."""
        if channel is not None:
            reading = self.read_channel_full(channel=channel, unit="mv", autoRange=False)
            return SignalStatus(
                channel=reading.channel,
                signal_v=reading.signal_v,
                signal_mv=reading.signal_mv,
                over_range=reading.over_range,
                under_range=reading.under_range,
                is_clipped=reading.is_clipped,
            )
        readings = self.read_all_full(unit="mv", autoRange=False)
        return [
            SignalStatus(
                channel=r.channel,
                signal_v=r.signal_v,
                signal_mv=r.signal_mv,
                over_range=r.over_range,
                under_range=r.under_range,
                is_clipped=r.is_clipped,
            )
            for r in readings
        ]

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

    def capture_layout(self) -> CaptureLayout:
        """Return the current channel mask and frame geometry."""
        mask, _active, frame_bytes = self._call(self._driver.get_channel_mask_info)
        return CaptureLayout(
            mask=mask,
            enabled_channels=self._mask_to_channels(mask),
            frame_bytes=frame_bytes,
        )

    def capture_channel_mask(self) -> int:
        """Return the current channel mask as an integer."""
        return self.capture_layout().mask

    def capture_channels(self) -> Tuple[int, ...]:
        """Return a tuple of enabled channel indices."""
        return self.capture_layout().enabled_channels

    def set_capture_channel_mask(self, mask: Union[int, str]) -> int:
        """Set the active capture channel mask.

        Accepts int, hex string (``"0x5"``), binary string (``"0b0101"``),
        or spaced binary (``"0000 0101"``).
        """
        normalized = self._normalize_capture_channel_mask(mask)
        if normalized == 0:
            raise ValueError("capture_channel_mask must enable at least one channel")
        self._call(self._driver.set_channel_mask, normalized)
        return self.capture_channel_mask()

    def set_capture_channels(self, channels: Sequence[int]) -> Tuple[int, ...]:
        """Enable exactly the listed channel indices for capture."""
        normalized = self._normalize_channels(tuple(channels))
        if normalized is None:
            raise ValueError("channels must not be empty")
        self._call(self._driver.set_channel_mask, self._channels_to_mask(normalized))
        return self.capture_channels()

    def max_capture_frames(self, channels: Optional[Sequence[int]] = None) -> int:
        """Return the maximum number of frames that fit in device SDRAM."""
        normalized = self._normalize_channels(channels)
        if normalized is None:
            return self._call(self._driver.max_acquisition_frames)
        return self._call(self._driver.max_acquisition_frames, self._channels_to_mask(normalized))

    # Deprecated aliases
    def enabled_channels(self) -> Tuple[int, ...]:
        warnings.warn("enabled_channels() is deprecated; use capture_channels().", DeprecationWarning, stacklevel=2)
        return self.capture_channels()

    def set_enabled_channels(self, channels: Sequence[int]) -> Tuple[int, ...]:
        warnings.warn("set_enabled_channels() is deprecated; use set_capture_channels().", DeprecationWarning, stacklevel=2)
        return self.set_capture_channels(channels)

    # ------------------------------------------------------------------
    # Capture control
    # ------------------------------------------------------------------

    def arm_capture(
        self, frames: int, trigger: bool = False, trigger_rising: bool = True
    ) -> None:
        """Arm the ADC for a block acquisition (does not start yet)."""
        self._call(self._driver.arm_acquisition, frames, trigger, trigger_rising)

    def start_capture(self) -> None:
        """Start a previously armed (non-triggered) acquisition."""
        self._call(self._driver.start_acquisition)

    def stop_capture(self) -> None:
        """Abort an active acquisition."""
        self._call(self._driver.stop_acquisition)

    def capture_status(self) -> str:
        """Return the current acquisition state string from the device."""
        return self._call(self._driver.acquisition_status)

    def remaining_frames(self) -> int:
        """Return the number of frames still to be collected."""
        return self._call(self._driver.frames_remaining)

    def wait_until_complete(
        self, poll_s: float = 0.25, timeout_s: Optional[float] = None
    ) -> None:
        """Block until the current acquisition completes.

        Raises ``coreDAQTimeoutError`` if *timeout_s* elapses first.
        """
        self._call(self._driver.wait_for_completion, poll_s=poll_s, timeout_s=timeout_s)

    # ------------------------------------------------------------------
    # Block capture (conversion from ADC codes happens here)
    # ------------------------------------------------------------------

    def _convert_trace_values(
        self,
        channel: int,
        zeroed_codes: List[int],
        range_index: Optional[int],
        unit: str,
        frontend: str,
    ) -> Tuple[List[Union[int, float]], CaptureChannelStatus]:
        lsb_mv = self._driver.ADC_LSB_MV
        mv_dec = self._driver.MV_OUTPUT_DECIMALS

        signal_mv = [round(float(c) * lsb_mv, mv_dec) for c in zeroed_codes]
        signal_v = [mv / 1000.0 for mv in signal_mv]

        if frontend == self._driver.FRONTEND_LINEAR:
            if range_index is None:
                raise coreDAQError(f"Missing range index for channel {channel}")
            power_w = [
                self._call(self._driver._convert_linear_mv_to_power_w, channel, int(range_index), float(mv))
                for mv in signal_mv
            ]
        else:
            power_w = [
                round(
                    self._call(self._driver._convert_log_voltage_to_power_w, float(v), channel),
                    self._driver.POWER_OUTPUT_DECIMALS_MAX,
                )
                for v in signal_v
            ]

        power_dbm = [self._power_dbm(p) for p in power_w]

        if unit == "w":
            values: List[Union[int, float]] = list(power_w)
        elif unit == "dbm":
            values = list(power_dbm)
        elif unit == "v":
            values = list(signal_v)
        elif unit == "mv":
            values = list(signal_mv)
        else:
            values = [int(c) for c in zeroed_codes]

        over_s = under_s = clip_s = 0
        peak_v = 0.0
        for v, mv in zip(signal_v, signal_mv):
            ov, un, cl = self._signal_flags(v, mv)
            over_s += int(ov)
            under_s += int(un)
            clip_s += int(cl)
            peak_v = max(peak_v, abs(float(v)))

        return values, CaptureChannelStatus(
            channel=channel,
            any_over_range=over_s > 0,
            any_under_range=under_s > 0,
            any_clipped=clip_s > 0,
            over_range_samples=over_s,
            under_range_samples=under_s,
            clipped_samples=clip_s,
            peak_signal_v=peak_v,
        )

    def get_data(
        self,
        frames: int,
        unit: Optional[str] = None,
        channels: Optional[Union[int, Sequence[int]]] = None,
        trigger: bool = False,
        trigger_rising: bool = True,
    ) -> CaptureResult:
        """Arm and run a block capture; return converted traces.

        This is the canonical name.  ``capture()`` is an alias.
        """
        if int(frames) <= 0:
            raise ValueError("frames must be > 0")

        output_unit = self._normalize_unit(self._reading_unit if unit is None else unit)
        requested_channels = self._normalize_channels(channels)
        original_layout = self.capture_layout()
        target_channels = (
            original_layout.enabled_channels if requested_channels is None else requested_channels
        )
        target_mask = (
            original_layout.mask
            if requested_channels is None
            else self._channels_to_mask(target_channels)
        )

        if requested_channels is not None and target_mask != original_layout.mask:
            self._call(self._driver.set_channel_mask, target_mask)

        try:
            self.arm_capture(int(frames), trigger=trigger, trigger_rising=trigger_rising)
            if not trigger:
                self.start_capture()
            self.wait_until_complete()
            raw_traces = self._call(self._driver.transfer_frames_adc, int(frames))
        finally:
            if requested_channels is not None and target_mask != original_layout.mask:
                try:
                    self._call(self._driver.set_channel_mask, original_layout.mask)
                except coreDAQError:
                    pass

        frontend = self.frontend()
        detector = self.detector()
        wavelength_nm = self.wavelength_nm()
        sample_rate_hz = self.sample_rate_hz()
        gains = (
            self._call(self._driver.get_gains)
            if frontend == self._driver.FRONTEND_LINEAR
            else (None, None, None, None)
        )

        traces: Dict[int, List[Union[int, float]]] = {}
        statuses: Dict[int, CaptureChannelStatus] = {}
        ranges: Dict[int, Optional[int]] = {}
        range_labels: Dict[int, Optional[str]] = {}

        for ch in target_channels:
            raw_codes = [int(v) for v in raw_traces[ch]]
            if frontend == self._driver.FRONTEND_LINEAR:
                zero = int(self._driver._linear_zero_adc[ch])
                zeroed_codes = [c - zero for c in raw_codes]
                range_index: Optional[int] = int(gains[ch])
            else:
                zeroed_codes = raw_codes
                range_index = None

            values, status = self._convert_trace_values(
                channel=ch,
                zeroed_codes=zeroed_codes,
                range_index=range_index,
                unit=output_unit,
                frontend=frontend,
            )
            traces[ch] = values
            statuses[ch] = status
            ranges[ch] = range_index
            range_labels[ch] = self._range_label(range_index)

        return CaptureResult(
            traces=traces,
            statuses=statuses,
            unit=output_unit,
            sample_rate_hz=sample_rate_hz,
            enabled_channels=tuple(target_channels),
            ranges=ranges,
            range_labels=range_labels,
            wavelength_nm=wavelength_nm,
            detector=detector,
            frontend=frontend,
        )

    def capture(
        self,
        frames: int,
        unit: Optional[str] = None,
        channels: Optional[Union[int, Sequence[int]]] = None,
        trigger: bool = False,
        trigger_rising: bool = True,
    ) -> CaptureResult:
        """Arm and run a block capture; return converted traces.

        Alias for ``get_data()``.
        """
        return self.get_data(
            frames=frames, unit=unit, channels=channels,
            trigger=trigger, trigger_rising=trigger_rising,
        )

    def capture_channel(
        self,
        channel: int,
        frames: int,
        unit: Optional[str] = None,
        trigger: bool = False,
        trigger_rising: bool = True,
    ) -> CaptureResult:
        """Capture a single channel.  Alias for get_data(channels=[channel])."""
        return self.get_data(
            frames=frames, unit=unit,
            channels=[self._normalize_channel(channel)],
            trigger=trigger, trigger_rising=trigger_rising,
        )

    # Deprecated aliases
    def get_data_channel(self, channel: int, frames: int, unit: Optional[str] = None, trigger: bool = False, trigger_rising: bool = True) -> CaptureResult:
        warnings.warn("get_data_channel() is deprecated; use capture_channel().", DeprecationWarning, stacklevel=2)
        return self.capture_channel(channel, frames=frames, unit=unit, trigger=trigger, trigger_rising=trigger_rising)

    # ------------------------------------------------------------------
    # Sample rate and oversampling
    # ------------------------------------------------------------------

    def set_sample_rate_hz(self, hz: int) -> None:
        """Set the ADC sample rate in Hz (1..100 000)."""
        self._call(self._driver.set_freq, hz)

    def sample_rate_hz(self) -> int:
        """Return the current ADC sample rate in Hz."""
        return self._call(self._driver.get_freq_hz)

    def set_oversampling(self, os_idx: int) -> None:
        """Set the oversampling index (0..7)."""
        self._call(self._driver.set_oversampling, os_idx)

    def oversampling(self) -> int:
        """Return the current oversampling index."""
        return self._call(self._driver.get_oversampling)

    # ------------------------------------------------------------------
    # Environmental sensors
    # ------------------------------------------------------------------

    def head_temperature_c(self) -> float:
        """Return the optical head temperature in °C."""
        return self._call(self._driver.get_head_temperature_C)

    def head_humidity_percent(self) -> float:
        """Return the optical head relative humidity in %."""
        return self._call(self._driver.get_head_humidity)

    def die_temperature_c(self) -> float:
        """Return the MCU die temperature in °C."""
        return self._call(self._driver.get_die_temperature_C)

    def refresh_device_state(self) -> None:
        """Re-read I2C sensor registers (temperature, humidity)."""
        self._call(self._driver.i2c_refresh)

    # ------------------------------------------------------------------
    # Advanced / low-level
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Soft-reset the device firmware."""
        self._call(self._driver.soft_reset)

    def enter_dfu_mode(self) -> None:
        """Enter DFU (firmware update) mode.  The device will reset."""
        self._call(self._driver.enter_dfu)

    def capture_buffer_address(self) -> int:
        """Return the current SDRAM write address (for diagnostics)."""
        return self._call(self._driver.stream_write_address)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def discover(baudrate: int = 115200, timeout: float = 0.15) -> List[str]:
        """Return serial port paths of all connected coreDAQ devices.

        Uses USB descriptor matching plus an IDN? probe.
        Returns an empty list if no devices are found.
        """
        try:
            return SerialTransport.find_ports(baudrate=baudrate, timeout=timeout)
        except Exception as exc:
            raise coreDAQError(str(exc)) from exc
