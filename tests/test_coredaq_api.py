"""py_coreDAQ API tests.

All tests run against fake drivers injected at the _driver level, so no
hardware is required.  Use COREDAQ_HARDWARE_PORT=/dev/tty... pytest -m hardware
for hardware-in-loop tests (not included here).

Simulator smoke tests use coreDAQ.connect(simulator=True) to exercise the
full stack end-to-end against SimTransport.
"""
import math
import warnings

import pytest

from py_coreDAQ import CaptureResult, ChannelReading, MeasurementSet, coreDAQ


# ---------------------------------------------------------------------------
# Shared fake driver infrastructure
# ---------------------------------------------------------------------------


class _BaseFakeDriver:
    ADC_BITS = 16
    ADC_VFS_VOLTS = 5.0
    ADC_LSB_VOLTS = (2.0 * ADC_VFS_VOLTS) / (2**ADC_BITS)
    ADC_LSB_MV = ADC_LSB_VOLTS * 1e3
    MV_OUTPUT_DECIMALS = 3
    POWER_OUTPUT_DECIMALS_MAX = 12
    NUM_GAINS = 8
    FRONTEND_LINEAR = "LINEAR"
    FRONTEND_LOG = "LOG"
    DETECTOR_INGAAS = "INGAAS"
    DETECTOR_SILICON = "SILICON"

    def __init__(self, frontend, detector="INGAAS"):
        self._frontend = frontend
        self._detector = detector
        self._wavelength_nm = 1550.0
        self._mask = 0x0F
        self._gains = [1, 2, 3, 4]
        self._linear_zero_adc = [10, 20, 30, 40]
        self._factory_zero_adc = [10, 20, 30, 40]
        self._last_snapshot_n_frames = None

    def close(self): return None
    def frontend_type(self): return self._frontend
    def detector_type(self): return self._detector
    def port_name(self): return "fake"
    def gain_profile(self, refresh=False): return "standard"
    def gain_label(self, gain_index, gain_profile="standard"):
        return ["5 mW", "1 mW", "500 uW", "100 uW", "50 uW", "10 uW", "5 uW", "500 nW"][int(gain_index)]
    def gain_labels(self, gain_profile="standard"):
        return ["5 mW", "1 mW", "500 uW", "100 uW", "50 uW", "10 uW", "5 uW", "500 nW"]
    def gain_max_power_table(self, gain_profile="standard"):
        return [5e-3, 1e-3, 500e-6, 100e-6, 50e-6, 10e-6, 5e-6, 500e-9]
    def get_wavelength_nm(self): return self._wavelength_nm
    def get_freq_hz(self): return 1000
    def get_gains(self): return tuple(self._gains)
    def set_gain(self, head, value):
        self._gains[int(head) - 1] = int(value)
    def get_linear_zero_adc(self): return tuple(self._linear_zero_adc)
    def get_factory_zero_adc(self): return tuple(self._factory_zero_adc)
    def soft_zero_from_snapshot(self, n_frames=32, settle_s=0.2):
        self._linear_zero_adc = [1, 2, 3, 4]
        return [1, 2, 3, 4], list(self._gains)
    def restore_factory_zero(self):
        self._linear_zero_adc = list(self._factory_zero_adc)
    def get_channel_mask_info(self):
        active = sum(1 for i in range(4) if self._mask & (1 << i))
        return self._mask, active, 2 * active
    def set_channel_mask(self, mask): self._mask = int(mask) & 0x0F
    def max_acquisition_frames(self, mask=None): return 1024
    def arm_acquisition(self, frames, use_trigger=False, trigger_rising=True):
        self._armed_frames = int(frames)
        self._armed_with_trigger = bool(use_trigger)
        self._trigger_rising = bool(trigger_rising)
    def start_acquisition(self): self._started = True
    def wait_for_completion(self, poll_s=0.25, timeout_s=None): self._completed = True
    def idn(self, refresh=False): return "coreDAQ FAKE v1.0"
    def get_wavelength_limits_nm(self, detector=None): return (910.0, 1700.0)
    def set_wavelength_nm(self, wl): self._wavelength_nm = float(wl)
    def get_responsivity_A_per_W(self, detector=None, wavelength_nm=None): return 0.99
    def get_oversampling(self): return 1
    def set_oversampling(self, os): pass
    def set_freq(self, hz): pass


class _LinearFakeDriver(_BaseFakeDriver):
    def __init__(self):
        super().__init__(frontend=self.FRONTEND_LINEAR, detector="INGAAS")
        self._snapshot_codes = [3, 27526, 66, 45875]
        self._trace_codes = [
            [13, 14, 15],
            [27546, 27556, 27566],
            [96, 106, 116],
            [45915, 45925, 45935],
        ]

    def snapshot_adc_zeroed(self, n_frames=1, timeout_s=1.0, poll_hz=200.0):
        self._last_snapshot_n_frames = int(n_frames)
        return list(self._snapshot_codes), list(self._gains)

    def snapshot_adc(self, n_frames=1, timeout_s=1.0, poll_hz=200.0):
        self._last_snapshot_n_frames = int(n_frames)
        raw = [self._snapshot_codes[i] + self._linear_zero_adc[i] for i in range(4)]
        return raw, list(self._gains)

    def _convert_linear_mv_to_power_w(self, head_idx, gain, mv_corr):
        return round((float(mv_corr) / 1000.0) / (1000.0 * (int(gain) + 1)), 12)

    def transfer_frames_adc(self, frames):
        return [trace[:int(frames)] for trace in self._trace_codes]


class _LogFakeDriver(_BaseFakeDriver):
    def __init__(self):
        super().__init__(frontend=self.FRONTEND_LOG, detector="SILICON")
        self._snapshot_codes = [100, 200, 300, 400]
        self._trace_codes = [
            [100, 110, 120],
            [200, 210, 220],
            [300, 310, 320],
            [400, 410, 420],
        ]

    def snapshot_adc(self, n_frames=1, timeout_s=1.0, poll_hz=200.0):
        self._last_snapshot_n_frames = int(n_frames)
        return list(self._snapshot_codes), [0, 0, 0, 0]

    def _convert_log_voltage_to_power_w(self, v_volts, head_idx=0):
        return round(max(1e-12, abs(float(v_volts)) * 1e-3), 12)

    def transfer_frames_adc(self, frames):
        return [trace[:int(frames)] for trace in self._trace_codes]


def _build_meter(fake_driver):
    meter = object.__new__(coreDAQ)
    meter._driver = fake_driver
    meter._reading_unit = "w"
    meter._zero_source = (
        "factory"
        if fake_driver.frontend_type() == fake_driver.FRONTEND_LINEAR
        else "not_applicable"
    )
    return meter


# ---------------------------------------------------------------------------
# Unit tests using fake drivers
# ---------------------------------------------------------------------------


def test_read_all_returns_plain_values():
    meter = _build_meter(_LinearFakeDriver())
    readings = meter.read_all()
    assert isinstance(readings, list)
    assert len(readings) == 4
    assert all(isinstance(v, float) for v in readings)


def test_read_details_returns_measurement_objects():
    meter = _build_meter(_LinearFakeDriver())
    readings = meter.read_all_full(autoRange=False)
    reading = meter.read_channel_full(0, unit="dbm", autoRange=False)
    assert isinstance(readings, MeasurementSet)
    assert isinstance(reading, ChannelReading)
    assert readings.channel(3).range_label == "50 uW"
    assert reading.channel == 0
    assert reading.unit == "dbm"
    assert math.isfinite(reading.value) or math.isinf(reading.value)


def test_read_channel_auto_range_only_adjusts_requested_channel():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    meter.read_channel(0)
    assert driver._gains[0] == 7
    assert driver._gains[1:] == [2, 3, 4]


def test_read_all_ignores_capture_mask_and_adjusts_all_channels():
    driver = _LinearFakeDriver()
    driver._mask = 0x05
    meter = _build_meter(driver)
    readings = meter.read_all()
    assert len(readings) == 4
    assert driver._gains == [7, 0, 7, 0]


def test_read_channel_returns_single_value():
    meter = _build_meter(_LinearFakeDriver())
    first = meter.read_channel(0, unit="adc")
    second = meter.read_channel(0, unit="dbm")
    assert isinstance(first, int)
    assert first == 3
    assert math.isfinite(second) or math.isinf(second)


def test_read_n_samples_is_forwarded_to_snap():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    readings = meter.read_all(n_samples=32)
    assert len(readings) == 4
    assert driver._last_snapshot_n_frames == 32


def test_read_n_samples_validates_range():
    meter = _build_meter(_LinearFakeDriver())
    with pytest.raises(ValueError):
        meter.read_all(n_samples=0)
    with pytest.raises(ValueError):
        meter.read_channel(0, n_samples=33)


def test_range_getters_and_setters_support_arrays_and_power_targets():
    meter = _build_meter(_LinearFakeDriver())
    assert meter.get_range(0) == 1
    assert meter.get_ranges() == [1, 2, 3, 4]
    assert meter.get_range_all() == [1, 2, 3, 4]  # deprecated alias still works

    meter.set_range(0, 0)
    assert meter.get_range(0) == 0

    meter.set_ranges([1, 1, 1, 1])
    assert meter.get_ranges() == [1, 1, 1, 1]

    chosen = meter.set_range_power(2, 1e-3)
    assert chosen == 1
    assert meter.get_range(2) == 1

    chosen = meter.set_range_power(3, 2e-2)
    assert chosen == 0
    assert meter.get_range(3) == 0


def test_deprecated_range_aliases_emit_warnings():
    meter = _build_meter(_LinearFakeDriver())
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        meter.get_range_all()
        meter.current_ranges()
        meter.set_power_range(0, 1)
    assert any("deprecated" in str(warning.message).lower() for warning in w)


def test_capture_channel_mask_supports_binary_strings():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    applied = meter.set_capture_channel_mask("0000 0100")
    assert applied == 0x04
    assert meter.capture_channel_mask() == 0x04
    assert meter.capture_channels() == (2,)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert meter.enabled_channels() == (2,)


def test_signal_status_and_is_clipped_follow_thresholds():
    meter = _build_meter(_LinearFakeDriver())
    status = meter.signal_status()
    clipped = meter.is_clipped()
    assert len(status) == 4
    assert clipped == [True, True, False, True]
    assert status[0].under_range
    assert status[1].over_range
    assert status[3].over_range


def test_capture_uses_requested_channels_and_restores_mask():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    capture = meter.get_data(frames=3, unit="mv", channels=[0, 2])
    assert isinstance(capture, CaptureResult)
    assert capture.unit == "mv"
    assert capture.enabled_channels == (0, 2)
    assert sorted(capture.traces.keys()) == [0, 2]
    assert driver._mask == 0x0F
    assert capture.status(0).any_clipped
    assert not getattr(driver, "_armed_with_trigger", False)
    assert getattr(driver, "_started", False)


def test_triggered_capture_uses_trigger_path_without_start():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    capture = meter.get_data(frames=3, unit="adc", trigger=True, trigger_rising=False)
    assert isinstance(capture, CaptureResult)
    assert capture.enabled_channels == (0, 1, 2, 3)
    assert driver._armed_with_trigger
    assert not driver._trigger_rising
    assert not getattr(driver, "_started", False)


def test_zero_dark_and_restore_factory_zero_update_source():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    meter.zero_dark()
    assert meter._zero_source == "user"
    assert meter.zero_offsets_adc() == (1, 2, 3, 4)
    meter.restore_factory_zero()
    assert meter._zero_source == "factory"
    assert meter.zero_offsets_adc() == (10, 20, 30, 40)


def test_log_frontend_reports_not_applicable_zero_source():
    meter = _build_meter(_LogFakeDriver())
    reading = meter.read_channel_full(1, unit="v")
    assert reading.unit == "v"
    assert reading.zero_source == "not_applicable"
    assert reading.range_index is None


def test_zero_dark_raises_on_log_frontend():
    meter = _build_meter(_LogFakeDriver())
    from py_coreDAQ import coreDAQUnsupportedError
    with pytest.raises(coreDAQUnsupportedError):
        meter.zero_dark()


def test_deprecated_capture_aliases_emit_warnings():
    driver = _LinearFakeDriver()
    meter = _build_meter(driver)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        meter.get_data_channel(0, frames=3)
    assert any("deprecated" in str(warning.message).lower() for warning in w)


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


def test_simulator_oversampling_and_sample_rate_set_at_init():
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
