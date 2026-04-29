import math
import unittest

from coredaq import CaptureResult, MeasurementSet, coreDAQ


class _BaseFakeDriver:
    ADC_BITS = 16
    ADC_VFS_VOLTS = 5.0
    ADC_LSB_VOLTS = (2.0 * ADC_VFS_VOLTS) / (2 ** ADC_BITS)
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

    def close(self):
        return None

    def frontend_type(self):
        return self._frontend

    def detector_type(self):
        return self._detector

    def gain_profile(self, refresh=False):
        return "standard"

    def gain_label(self, gain_index, gain_profile="standard"):
        return f"R{int(gain_index)}"

    def gain_labels(self, gain_profile="standard"):
        return [f"R{i}" for i in range(8)]

    def gain_max_power_table(self, gain_profile="standard"):
        return [1e-3 * (i + 1) for i in range(8)]

    def get_wavelength_nm(self):
        return self._wavelength_nm

    def get_freq_hz(self):
        return 1000

    def get_gains(self):
        return tuple(self._gains)

    def set_gain(self, head, value):
        self._gains[int(head) - 1] = int(value)

    def get_linear_zero_adc(self):
        return tuple(self._linear_zero_adc)

    def get_factory_zero_adc(self):
        return tuple(self._factory_zero_adc)

    def soft_zero_from_snapshot(self, n_frames=32, settle_s=0.2):
        self._linear_zero_adc = [1, 2, 3, 4]
        return [1, 2, 3, 4], list(self._gains)

    def restore_factory_zero(self):
        self._linear_zero_adc = list(self._factory_zero_adc)

    def get_channel_mask_info(self):
        active = sum(1 for idx in range(4) if self._mask & (1 << idx))
        return self._mask, active, 2 * active

    def set_channel_mask(self, mask):
        self._mask = int(mask) & 0x0F

    def max_acquisition_frames(self, mask=None):
        return 1024

    def arm_acquisition(self, frames, use_trigger=False, trigger_rising=True):
        self._armed_frames = int(frames)

    def start_acquisition(self):
        self._started = True

    def wait_for_completion(self, poll_s=0.25, timeout_s=None):
        self._completed = True


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
        return list(self._snapshot_codes), list(self._gains)

    def snapshot_adc(self, n_frames=1, timeout_s=1.0, poll_hz=200.0):
        raw = [self._snapshot_codes[i] + self._linear_zero_adc[i] for i in range(4)]
        return raw, list(self._gains)

    def _convert_linear_mv_to_power_w(self, head_idx, gain, mv_corr):
        return round((float(mv_corr) / 1000.0) / (1000.0 * (int(gain) + 1)), 12)

    def transfer_frames_adc(self, frames):
        return [trace[: int(frames)] for trace in self._trace_codes]


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
        return list(self._snapshot_codes), [0, 0, 0, 0]

    def _convert_log_voltage_to_power_w(self, v_volts, head_idx=0):
        return round(max(1e-12, abs(float(v_volts)) * 1e-3), 12)

    def transfer_frames_adc(self, frames):
        return [trace[: int(frames)] for trace in self._trace_codes]


def _build_meter(fake_driver):
    meter = object.__new__(coreDAQ)
    meter._driver = fake_driver
    meter._reading_unit = "w"
    meter._zero_source = "factory" if fake_driver.frontend_type() == fake_driver.FRONTEND_LINEAR else "not_applicable"
    return meter


class CoreDAQApiTests(unittest.TestCase):
    def test_read_all_returns_measurement_set(self):
        meter = _build_meter(_LinearFakeDriver())

        readings = meter.read_all()

        self.assertIsInstance(readings, MeasurementSet)
        self.assertEqual(len(readings), 4)
        self.assertEqual(readings.unit, "w")
        self.assertEqual(readings.channel(1).channel, 1)
        self.assertEqual(readings.channel(1).unit, "w")
        self.assertEqual(readings.channel(4).range_label, "R4")

    def test_unit_override_and_channel_alias_match(self):
        meter = _build_meter(_LinearFakeDriver())

        first = meter.read_channel(1, unit="adc")
        alias = meter.read_channel1(unit="adc")
        dbm = meter.read_channel1(unit="dbm")

        self.assertEqual(first.adc_code, alias.adc_code)
        self.assertEqual(first.value, alias.value)
        self.assertEqual(first.unit, "adc")
        self.assertEqual(dbm.unit, "dbm")
        self.assertTrue(math.isfinite(dbm.value) or math.isinf(dbm.value))

    def test_signal_status_and_is_clipped_follow_thresholds(self):
        meter = _build_meter(_LinearFakeDriver())

        status = meter.signal_status()
        clipped = meter.is_clipped()

        self.assertEqual(len(status), 4)
        self.assertEqual(clipped, [True, True, False, True])
        self.assertTrue(status[0].under_range)
        self.assertTrue(status[1].over_range)
        self.assertTrue(status[3].over_range)

    def test_capture_uses_requested_channels_and_restores_mask(self):
        driver = _LinearFakeDriver()
        meter = _build_meter(driver)

        capture = meter.get_data(frames=3, unit="mv", channels=[1, 3])

        self.assertIsInstance(capture, CaptureResult)
        self.assertEqual(capture.unit, "mv")
        self.assertEqual(capture.enabled_channels, (1, 3))
        self.assertEqual(sorted(capture.traces.keys()), [1, 3])
        self.assertEqual(driver._mask, 0x0F)
        self.assertTrue(capture.status(1).any_clipped)

    def test_zero_dark_and_restore_factory_zero_update_source(self):
        driver = _LinearFakeDriver()
        meter = _build_meter(driver)

        meter.zero_dark()
        self.assertEqual(meter._zero_source, "user")
        self.assertEqual(meter.zero_offsets_adc(), (1, 2, 3, 4))

        meter.restore_factory_zero()
        self.assertEqual(meter._zero_source, "factory")
        self.assertEqual(meter.zero_offsets_adc(), (10, 20, 30, 40))

    def test_log_frontend_reports_not_applicable_zero_source(self):
        meter = _build_meter(_LogFakeDriver())

        reading = meter.read_channel2(unit="v")

        self.assertEqual(reading.unit, "v")
        self.assertEqual(reading.zero_source, "not_applicable")
        self.assertEqual(reading.range_index, None)


if __name__ == "__main__":
    unittest.main()
