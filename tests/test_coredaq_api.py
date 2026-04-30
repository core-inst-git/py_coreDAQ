"""py_coreDAQ API tests.

Unit tests use MockTransport to inject controlled ADC codes without hardware.
Simulator smoke tests use coreDAQ.connect(simulator=True) for full-stack tests.
Hardware tests require COREDAQ_HARDWARE_PORT=/dev/tty... pytest -m hardware.
"""
import math
import warnings

import pytest

from py_coreDAQ import (
    CaptureResult,
    ChannelReading,
    MeasurementSet,
    coreDAQ,
    coreDAQUnsupportedError,
)
from py_coreDAQ._coredaq import (
    _ADC_LSB_MV,
    _ADC_LSB_V,
    _GAIN_MAX_W,
    _OVER_RANGE_V,
    _UNDER_RANGE_MV,
)


# ---------------------------------------------------------------------------
# MockTransport — returns controlled ADC codes for unit tests
# ---------------------------------------------------------------------------


class MockTransport:
    """Fake transport that responds to runtime firmware commands with fixed data.

    Call _build_meter_linear() or _build_meter_log() to get a coreDAQ
    instance backed by this transport, with calibration state injected directly.
    """

    def __init__(
        self,
        snapshot_codes: list[int],    # raw codes (with zero offset baked in)
        gains: list[int],
        mask: int = 0x0F,
        trace_codes: list[list[int]] | None = None,
    ) -> None:
        self.snapshot_codes = list(snapshot_codes)
        self._gains = list(gains)
        self._mask = mask
        self._trace_codes = trace_codes or [list(snapshot_codes)] * 4
        self.last_snap_n: int | None = None
        self.armed = False
        self.armed_trigger = False
        self.trigger_rising = True
        self.started = False
        self._state = 4  # DATA_READY by default

    def ask(self, cmd: str) -> tuple[str, str]:
        parts = cmd.strip().split()
        verb = parts[0].upper() if parts else ""

        if verb == "SNAP" and len(parts) == 2:
            self.last_snap_n = int(parts[1])
            return "OK", ""
        if verb == "SNAP?":
            c = self.snapshot_codes
            g = self._gains
            payload = f"{c[0]} {c[1]} {c[2]} {c[3]} G={g[0]} {g[1]} {g[2]} {g[3]}"
            return "OK", payload
        if verb == "GAIN" and len(parts) == 3:
            head, val = int(parts[1]), int(parts[2])
            self._gains[head - 1] = val
            return "OK", ""
        if verb == "GAINS?":
            g = self._gains
            return "OK", f"HEAD1={g[0]} HEAD2={g[1]} HEAD3={g[2]} HEAD4={g[3]}"
        if verb == "CHMASK?":
            active = bin(self._mask).count("1")
            return "OK", f"0x{self._mask:X} CH={active} FB={active * 2}"
        if verb == "CHMASK" and len(parts) == 2:
            self._mask = int(parts[1], 16) & 0x0F
            return "OK", ""
        if verb == "ACQ":
            sub = parts[1].upper() if len(parts) > 1 else ""
            if sub == "ARM":
                self.armed = True
                self.armed_trigger = False
                return "OK", ""
            if sub == "START":
                self.started = True
                return "OK", ""
            if sub == "STOP":
                return "OK", ""
        if verb == "TRIGARM":
            self.armed = True
            self.armed_trigger = True
            self.trigger_rising = len(parts) > 2 and parts[2].upper() == "R"
            return "OK", ""
        if verb == "STATE?":
            return "OK", str(self._state)
        if verb == "STREAM?":
            return "OK", "DONE"
        if verb == "LEFT?":
            return "OK", "0"
        if verb in ("FREQ?", "OS?"):
            return "OK", "500" if verb == "FREQ?" else "1"
        if verb in ("FREQ", "OS", "I2C"):
            return "OK", ""
        if verb in ("TEMP?", "HUM?", "DIE_TEMP?"):
            return "OK", "25.0"
        if verb == "ADDR?":
            return "OK", "0xC0000000"
        # unknown — silently accept
        return "OK", ""

    def ask_with_busy_retry(self, cmd: str, retries: int = 20, delay_s: float = 0.05) -> tuple[str, str]:
        return self.ask(cmd)

    def read_frames(self, frames: int, mask: int) -> dict[int, list[int]]:
        channels = [i for i in range(4) if mask & (1 << i)]
        return {ch: self._trace_codes[ch][:frames] for ch in channels}

    def logcal(self, head: int) -> tuple[list[int], list[int]]:
        return [], []

    def close(self) -> None:
        pass

    def drain(self) -> None:
        pass

    def port_name(self) -> str:
        return "mock"


# Slope giving power = mv / (1e6 * (gain+1)) for a simple fake InGaAs LINEAR cal
def _fake_linear_slope(gain: int) -> float:
    return 1e6 * (gain + 1)


def _build_meter_linear(
    snapshot_codes: list[int] | None = None,
    zero_offsets: list[int] | None = None,
    gains: list[int] | None = None,
    trace_codes: list[list[int]] | None = None,
) -> coreDAQ:
    """Build a LINEAR InGaAs coreDAQ with injected state and MockTransport.

    snapshot_codes: zeroed ADC codes (transport will add zero_offsets before returning)
    zero_offsets:   active zero offsets per channel
    gains:          initial gain indices per channel
    """
    zo = zero_offsets or [10, 20, 30, 40]
    zc = snapshot_codes or [3, 27526, 66, 45875]
    gs = gains or [1, 2, 3, 4]

    # Transport sees raw codes (zeroed + offset)
    raw_codes = [zc[i] + zo[i] for i in range(4)]
    raw_trace = [[c + zo[i] for c in (trace_codes[i] if trace_codes else [zc[i]])] for i in range(4)]

    transport = MockTransport(snapshot_codes=raw_codes, gains=gs, trace_codes=raw_trace)

    meter = object.__new__(coreDAQ)
    meter._transport = transport
    meter._frontend = "LINEAR"
    meter._detector = "INGAAS"
    meter._gain_profile = "standard"
    meter._idn_cache = "coreDAQ FAKE-LINEAR v1.0"
    meter._reading_unit = "w"
    meter._wavelength_nm = 1550.0
    meter._zero_source = "factory"
    meter._zero = list(zo)
    meter._factory_zero = list(zo)
    # slope[ch][gain] = 1e6*(gain+1) mV/W  → power = mv / (1e6*(gain+1))
    meter._cal_slope = [[_fake_linear_slope(g) for g in range(8)] for _ in range(4)]
    meter._cal_intercept = [[0.0] * 8 for _ in range(4)]
    meter._lut_v_v = None
    meter._lut_log10p = None
    meter._silicon_tia = [[5e3] * 8 for _ in range(4)]
    return meter


def _build_meter_log(
    snapshot_codes: list[int] | None = None,
    trace_codes: list[list[int]] | None = None,
) -> coreDAQ:
    """Build a LOG Silicon coreDAQ with injected state and MockTransport."""
    sc = snapshot_codes or [100, 200, 300, 400]
    tc = trace_codes or [[c] * 10 for c in sc]

    transport = MockTransport(snapshot_codes=sc, gains=[0, 0, 0, 0], trace_codes=tc)

    meter = object.__new__(coreDAQ)
    meter._transport = transport
    meter._frontend = "LOG"
    meter._detector = "SILICON"
    meter._gain_profile = "standard"
    meter._idn_cache = "coreDAQ FAKE-LOG v1.0"
    meter._reading_unit = "w"
    meter._wavelength_nm = 850.0
    meter._zero_source = "not_applicable"
    meter._zero = [0, 0, 0, 0]
    meter._factory_zero = [0, 0, 0, 0]
    meter._cal_slope = [[0.0] * 8 for _ in range(4)]
    meter._cal_intercept = [[0.0] * 8 for _ in range(4)]
    meter._lut_v_v = None
    meter._lut_log10p = None
    meter._silicon_tia = [[5e3] * 8 for _ in range(4)]
    return meter


# ---------------------------------------------------------------------------
# Unit tests — read methods
# ---------------------------------------------------------------------------


def test_read_all_returns_plain_values():
    meter = _build_meter_linear()
    readings = meter.read_all(autoRange=False)
    assert isinstance(readings, list)
    assert len(readings) == 4
    assert all(isinstance(v, float) for v in readings)


def test_read_details_returns_measurement_objects():
    meter = _build_meter_linear()
    readings = meter.read_all_full(autoRange=False)
    reading = meter.read_channel_full(0, unit="dbm", autoRange=False)
    assert isinstance(readings, MeasurementSet)
    assert isinstance(reading, ChannelReading)
    assert readings.channel(3).range_label == "50 uW"   # gain=4 → index 4 → "50 uW"
    assert reading.channel == 0
    assert reading.unit == "dbm"
    assert math.isfinite(reading.value) or math.isinf(reading.value)


def test_read_channel_returns_single_value():
    meter = _build_meter_linear()
    first = meter.read_channel(0, unit="adc", autoRange=False)
    second = meter.read_channel(0, unit="dbm", autoRange=False)
    assert isinstance(first, int)
    assert first == 3  # zeroed code = 13 - 10 = 3
    assert math.isfinite(second) or math.isinf(second)


def test_read_channel_auto_range_picks_best_gain():
    meter = _build_meter_linear()
    meter.read_channel(0)  # code 3 is tiny → autorange should move to highest gain
    transport = meter._transport
    assert transport._gains[0] == 7   # autorange picked gain 7 (500 nW)
    assert transport._gains[1:] == [2, 3, 4]  # other channels untouched


def test_read_all_autoranges_all_channels():
    meter = _build_meter_linear()
    meter._transport._mask = 0x05    # capture mask only affects capture, not read_all
    readings = meter.read_all()      # read_all still reads all 4 channels
    assert len(readings) == 4
    # code 3 → gain 7, code 27526 → gain 0 (over-range clamp), code 66 → 7, code 45875 → 0
    assert meter._transport._gains[0] == 7
    assert meter._transport._gains[2] == 7


def test_n_samples_forwarded_to_snap():
    meter = _build_meter_linear()
    meter.read_all(n_samples=8, autoRange=False)
    assert meter._transport.last_snap_n == 8


def test_n_samples_validates_range():
    meter = _build_meter_linear()
    with pytest.raises(ValueError):
        meter.read_all(n_samples=0)
    with pytest.raises(ValueError):
        meter.read_channel(0, n_samples=33)


def test_channel_validation():
    meter = _build_meter_linear()
    with pytest.raises(ValueError):
        meter.read_channel(4)
    with pytest.raises(ValueError):
        meter.read_channel(-1)


# ---------------------------------------------------------------------------
# Unit tests — ranges (LINEAR)
# ---------------------------------------------------------------------------


def test_get_range_returns_current_gain():
    meter = _build_meter_linear(gains=[1, 2, 3, 4])
    assert meter.get_range(0) == 1
    assert meter.get_ranges() == [1, 2, 3, 4]


def test_set_range_updates_firmware():
    meter = _build_meter_linear(gains=[1, 2, 3, 4])
    meter.set_range(0, 5)
    assert meter._transport._gains[0] == 5


def test_set_ranges_updates_all_channels():
    meter = _build_meter_linear(gains=[0, 0, 0, 0])
    meter.set_ranges([1, 2, 3, 4])
    assert meter._transport._gains == [1, 2, 3, 4]


def test_set_range_power_selects_correct_index():
    meter = _build_meter_linear()
    chosen = meter.set_range_power(2, 1e-3)    # 1 mW → index 1 (1 mW full scale)
    assert chosen == 1
    chosen2 = meter.set_range_power(3, 2e-2)   # 20 mW → clamps to index 0 (5 mW, largest)
    assert chosen2 == 0


def test_range_raises_on_log_frontend():
    meter = _build_meter_log()
    with pytest.raises(coreDAQUnsupportedError):
        meter.set_range(0, 3)


def test_get_range_returns_none_on_log_frontend():
    meter = _build_meter_log()
    assert meter.get_range(0) is None
    assert meter.get_ranges() == [None, None, None, None]


# ---------------------------------------------------------------------------
# Unit tests — capture channel mask
# ---------------------------------------------------------------------------


def test_set_capture_channel_mask_binary_string():
    meter = _build_meter_linear()
    applied = meter.set_capture_channel_mask("0000 0100")
    assert applied == 0x04
    assert meter.capture_channel_mask() == 0x04
    assert meter.capture_channels() == (2,)


def test_set_capture_channels():
    meter = _build_meter_linear()
    meter.set_capture_channels([1, 3])
    assert meter.capture_channels() == (1, 3)


# ---------------------------------------------------------------------------
# Unit tests — signal health
# ---------------------------------------------------------------------------


def test_signal_status_thresholds():
    # zeroed codes: [3, 27526, 66, 45875]
    # code 3   → ~0.46 mV → under_range
    # code 27526 → ~4.20 V → over_range
    # code 66  → ~10.1 mV → in range
    # code 45875 → ~7.0 V → over_range
    meter = _build_meter_linear()
    status = meter.signal_status()
    clipped = meter.is_clipped()
    assert len(status) == 4
    assert status[0].under_range and not status[0].over_range
    assert status[1].over_range and not status[1].under_range
    assert not status[2].is_clipped
    assert status[3].over_range
    assert clipped == [True, True, False, True]


def test_signal_status_single_channel():
    meter = _build_meter_linear()
    s = meter.signal_status(0)
    assert s.channel == 0
    assert s.under_range


# ---------------------------------------------------------------------------
# Unit tests — capture
# ---------------------------------------------------------------------------


def test_capture_uses_requested_channels_and_restores_mask():
    meter = _build_meter_linear(
        snapshot_codes=[3, 27526, 66, 45875],
        zero_offsets=[10, 20, 30, 40],
        gains=[1, 2, 3, 4],
        trace_codes=[
            [13, 14, 15],        # ch0 raw
            [27546, 27556, 27566],  # ch1 raw
            [96, 106, 116],      # ch2 raw
            [45915, 45925, 45935],  # ch3 raw
        ],
    )
    original_mask = meter._transport._mask  # 0x0F
    result = meter.capture(frames=3, unit="mv", channels=[0, 2])
    assert isinstance(result, CaptureResult)
    assert result.unit == "mv"
    assert result.enabled_channels == (0, 2)
    assert sorted(result.traces.keys()) == [0, 2]
    assert meter._transport._mask == original_mask   # restored after capture


def test_triggered_capture_uses_trigger_path():
    meter = _build_meter_linear(
        trace_codes=[
            [13, 14, 15],
            [27546, 27556, 27566],
            [96, 106, 116],
            [45915, 45925, 45935],
        ],
    )
    result = meter.capture(frames=3, unit="adc", trigger=True, trigger_rising=False)
    assert isinstance(result, CaptureResult)
    assert result.enabled_channels == (0, 1, 2, 3)
    assert meter._transport.armed_trigger
    assert not meter._transport.trigger_rising
    assert not meter._transport.started    # start_capture not called for triggered


def test_capture_result_has_status():
    meter = _build_meter_linear(
        trace_codes=[
            [13, 14, 15],
            [27546, 27556, 27566],
            [96, 106, 116],
            [45915, 45925, 45935],
        ],
    )
    result = meter.capture(frames=3, unit="w", channels=[0])
    # ch0: zeroed codes [3,4,5] → all under range (< 328 min_code → < 5mV)
    assert result.status(0).any_clipped


# ---------------------------------------------------------------------------
# Unit tests — zeroing
# ---------------------------------------------------------------------------


def test_zero_dark_updates_zero_source_and_offsets():
    meter = _build_meter_linear()
    # _raw_adc will return snapshot_codes (raw=13,27546,96,45915) as the new zero
    meter.zero_dark(frames=1, settle_s=0.0)
    assert meter._zero_source == "user"
    raw_codes = meter._transport.snapshot_codes
    assert list(meter._zero) == raw_codes


def test_restore_factory_zero_reverts():
    meter = _build_meter_linear(zero_offsets=[10, 20, 30, 40])
    original_factory = list(meter._factory_zero)
    meter.zero_dark(frames=1, settle_s=0.0)   # changes _zero
    assert meter._zero != original_factory
    meter.restore_factory_zero()
    assert meter._zero == original_factory
    assert meter._zero_source == "factory"


def test_zero_dark_raises_on_log_frontend():
    meter = _build_meter_log()
    with pytest.raises(coreDAQUnsupportedError):
        meter.zero_dark()


# ---------------------------------------------------------------------------
# Unit tests — LOG frontend zero source
# ---------------------------------------------------------------------------


def test_log_frontend_zero_source_not_applicable():
    meter = _build_meter_log()
    reading = meter.read_channel_full(0)
    assert reading.zero_source == "not_applicable"
    assert reading.range_index is None
    assert reading.range_label is None


# ---------------------------------------------------------------------------
# Unit tests — unit normalization
# ---------------------------------------------------------------------------


def test_unit_aliases_accepted():
    meter = _build_meter_log()
    for alias in ("w", "watt", "watts", "W"):
        assert meter._unit(alias) == "w"
    for alias in ("dbm", "dBm", "DBM"):
        assert meter._unit(alias.lower()) == "dbm"
    for alias in ("adc", "raw", "ADC"):
        assert meter._unit(alias.lower()) == "adc"


def test_invalid_unit_raises():
    meter = _build_meter_log()
    with pytest.raises(ValueError):
        meter._unit("joules")


# ---------------------------------------------------------------------------
# Simulator smoke tests (InGaAs LOG default)
# ---------------------------------------------------------------------------


def test_simulator_connect_returns_coredaq():
    with coreDAQ.connect(simulator=True) as pm:
        assert isinstance(pm, coreDAQ)
        assert pm.frontend() == "LOG"
        assert pm.detector() == "INGAAS"


def test_simulator_read_channel_returns_finite_float():
    with coreDAQ.connect(simulator=True) as pm:
        value = pm.read_channel(0)
        assert isinstance(value, float)
        assert math.isfinite(value)
        assert value > 0


def test_simulator_read_all_returns_four_values():
    with coreDAQ.connect(simulator=True) as pm:
        values = pm.read_all()
    assert len(values) == 4
    assert all(math.isfinite(v) for v in values)


def test_simulator_read_channel_adc_returns_int():
    with coreDAQ.connect(simulator=True) as pm:
        v = pm.read_channel(0, unit="adc")
    assert isinstance(v, int)


def test_simulator_read_channel_dbm_is_finite_negative():
    with coreDAQ.connect(simulator=True) as pm:
        dbm = pm.read_channel(0, unit="dbm")
    assert isinstance(dbm, float)
    assert math.isfinite(dbm)
    assert dbm < 0


def test_simulator_capture_returns_correct_structure():
    with coreDAQ.connect(simulator=True) as pm:
        result = pm.capture(frames=10)
    assert isinstance(result, CaptureResult)
    assert result.enabled_channels == (0, 1, 2, 3)
    for ch in range(4):
        assert len(result.trace(ch)) == 10


def test_simulator_device_info_fields():
    with coreDAQ.connect(simulator=True) as pm:
        info = pm.device_info()
    assert info.frontend == "LOG"
    assert info.detector == "INGAAS"
    assert info.port == "simulator"
    assert "coredaq" in info.raw_idn.lower()


def test_simulator_linear_variant_allows_set_range():
    with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as pm:
        assert pm.frontend() == "LINEAR"
        pm.set_range(0, 3)
        assert pm.get_range(0) == 3


def test_simulator_si_log_variant():
    with coreDAQ.connect(simulator=True, frontend="LOG", detector="SILICON", wavelength_nm=850.0) as pm:
        assert pm.detector() == "SILICON"
        value = pm.read_channel(0)
        assert math.isfinite(value) and value > 0


def test_simulator_si_linear_variant():
    with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="SILICON", wavelength_nm=850.0) as pm:
        assert pm.frontend() == "LINEAR"
        assert pm.detector() == "SILICON"
        value = pm.read_channel(0)
        assert math.isfinite(value) and value > 0


def test_simulator_oversampling_and_sample_rate_at_init():
    with coreDAQ.connect(simulator=True) as pm:
        assert pm.oversampling() == 1
        assert pm.sample_rate_hz() == 500


def test_simulator_channel_proxy():
    with coreDAQ.connect(simulator=True) as pm:
        ch = pm.channels[0]
        assert isinstance(ch.power_w, float)
        assert math.isfinite(ch.power_w)
        reading = ch.read_full()
        assert isinstance(reading, ChannelReading)


def test_simulator_triggered_capture():
    with coreDAQ.connect(simulator=True) as pm:
        result = pm.capture(frames=5, trigger=True, trigger_rising=False)
    assert isinstance(result, CaptureResult)
    assert len(result.trace(0)) == 5


def test_simulator_deterministic_with_seed():
    with coreDAQ.connect(simulator=True, seed=99) as pm:
        v1 = pm.read_channel(0)
    with coreDAQ.connect(simulator=True, seed=99) as pm:
        v2 = pm.read_channel(0)
    assert v1 == v2


def test_simulator_sensors_return_floats():
    with coreDAQ.connect(simulator=True) as pm:
        assert isinstance(pm.head_temperature_c(), float)
        assert isinstance(pm.head_humidity_percent(), float)
        assert isinstance(pm.die_temperature_c(), float)


def test_simulator_n_samples_validation():
    with coreDAQ.connect(simulator=True) as pm:
        with pytest.raises(ValueError):
            pm.read_all(n_samples=0)
        with pytest.raises(ValueError):
            pm.read_channel(0, n_samples=33)


def test_simulator_capture_mask_restored_after_capture():
    with coreDAQ.connect(simulator=True) as pm:
        original = pm.capture_channels()
        pm.capture(frames=5, channels=[0, 1])
        assert pm.capture_channels() == original


def test_simulator_zero_dark_raises_on_log():
    with coreDAQ.connect(simulator=True) as pm:
        assert pm.frontend() == "LOG"
        with pytest.raises(coreDAQUnsupportedError):
            pm.zero_dark()


def test_simulator_linear_zero_dark_and_restore():
    with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as pm:
        pm.zero_dark(frames=4, settle_s=0.0)
        assert pm._zero_source == "user"
        pm.restore_factory_zero()
        assert pm._zero_source == "factory"


def test_simulator_log_zero_source_not_applicable():
    with coreDAQ.connect(simulator=True) as pm:
        r = pm.read_channel_full(0)
        assert r.zero_source == "not_applicable"
        assert r.range_index is None


def test_simulator_wavelength_change_affects_power():
    with coreDAQ.connect(simulator=True) as pm:
        pm.set_wavelength_nm(1310.0)
        v1310 = pm.read_channel(0, unit="w")
        pm.set_wavelength_nm(1550.0)
        v1550 = pm.read_channel(0, unit="w")
    # different wavelengths → different responsivity → different power readings
    assert v1310 != v1550


def test_simulator_capture_channel_method():
    with coreDAQ.connect(simulator=True) as pm:
        result = pm.capture_channel(2, frames=8)
    assert isinstance(result, CaptureResult)
    assert result.enabled_channels == (2,)
    assert len(result.trace(2)) == 8


def test_simulator_read_all_full_returns_measurement_set():
    with coreDAQ.connect(simulator=True) as pm:
        ms = pm.read_all_full()
    assert isinstance(ms, MeasurementSet)
    assert len(ms) == 4
    for r in ms:
        assert isinstance(r, ChannelReading)
        assert math.isfinite(r.power_w)
