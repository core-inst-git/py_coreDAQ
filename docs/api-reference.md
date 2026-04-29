# API Reference

All public names are importable from `py_coreDAQ`:

```python
from py_coreDAQ import (
    coreDAQ,
    ChannelProxy,
    CaptureResult, ChannelReading, MeasurementSet,
    DeviceInfo, SignalStatus, CaptureLayout, CaptureChannelStatus,
    coreDAQError, coreDAQConnectionError, coreDAQTimeoutError,
    coreDAQCalibrationError, coreDAQUnsupportedError,
)
```

---

## `coreDAQ`

### Connection

| Signature | Returns | Notes |
| --- | --- | --- |
| `coreDAQ.connect(port=None, *, simulator=False, baudrate=115200, timeout=0.15, **sim_kwargs)` | `coreDAQ` | **Preferred entry point.** Auto-discovers if `port=None`; returns a simulator when `simulator=True` |
| `coreDAQ(port, timeout=0.15, inter_command_gap_s=0.0)` | `coreDAQ` | Direct constructor; caller must know the serial port |
| `coreDAQ.discover(baudrate=115200, timeout=0.15)` | `list[str]` | Return port paths of all connected coreDAQ devices |

`coreDAQ` is a context manager — use it with `with` to ensure the serial port is closed on exit.

### Identity

| Signature | Returns |
| --- | --- |
| `identify(refresh=False)` | `str` — raw IDN string from the instrument |
| `device_info(refresh=False)` | `DeviceInfo` |
| `frontend()` | `"LINEAR"` or `"LOG"` |
| `detector()` | `"INGAAS"` or `"SILICON"` |
| `wavelength_nm()` | `float` |
| `set_wavelength_nm(nm)` | `None` |
| `wavelength_limits_nm(detector=None)` | `(float, float)` |
| `responsivity_a_per_w(wavelength_nm, detector=None)` | `float` |

### Live reads

| Signature | Returns |
| --- | --- |
| `read_channel(channel, unit=None, autoRange=True, n_samples=1)` | `float \| int` |
| `read_all(unit=None, autoRange=True, n_samples=1)` | `list[float \| int]` (4 values) |
| `read_channel_full(channel, unit=None, autoRange=True, n_samples=1)` | `ChannelReading` |
| `read_all_full(unit=None, autoRange=True, n_samples=1)` | `MeasurementSet` |

### ChannelProxy access

| Signature | Returns |
| --- | --- |
| `channels` *(property)* | `list[ChannelProxy]` — four proxies, indexed 0..3 |

### Capture

| Signature | Returns |
| --- | --- |
| `capture(frames, unit=None, channels=None, trigger=False, trigger_rising=True)` | `CaptureResult` |
| `capture_channel(channel, frames, unit=None, trigger=False, trigger_rising=True)` | `CaptureResult` |
| `arm_capture(frames, trigger=False, trigger_rising=True)` | `None` |
| `start_capture()` | `None` |
| `stop_capture()` | `None` |
| `wait_until_complete(poll_s=0.05, timeout_s=60.0)` | `None` |
| `capture_status()` | `str` |
| `remaining_frames()` | `int` |

### Capture mask

| Signature | Returns |
| --- | --- |
| `capture_channel_mask()` | `int` |
| `set_capture_channel_mask(mask)` | `int` |
| `capture_channels()` | `tuple[int, ...]` |
| `set_capture_channels(channels)` | `tuple[int, ...]` |
| `capture_layout()` | `CaptureLayout` |
| `max_capture_frames(channels=None)` | `int` |

### Ranges (LINEAR frontends only)

Raises `coreDAQUnsupportedError` when called on a LOG frontend.

| Signature | Returns |
| --- | --- |
| `get_range(channel)` | `int \| None` |
| `get_ranges()` | `list[int \| None]` |
| `set_range(channel, range_index)` | `None` |
| `set_ranges(range_indices)` | `list[int \| None]` |
| `set_range_power(channel, power_w)` | `int` |
| `set_range_powers(power_w_values)` | `list[int \| None]` |
| `supported_ranges()` | `list[dict]` |

### Zeroing (LINEAR frontends only)

`zero_dark()` raises `coreDAQUnsupportedError` on LOG frontends.

| Signature | Returns |
| --- | --- |
| `zero_dark(frames=32, settle_s=0.2)` | `tuple[int, int, int, int]` |
| `restore_factory_zero()` | `tuple[int, int, int, int]` |
| `zero_offsets_adc()` | `tuple[int, int, int, int]` |
| `factory_zero_offsets_adc()` | `tuple[int, int, int, int]` |

### Signal health

| Signature | Returns |
| --- | --- |
| `signal_status(channel=None)` | `SignalStatus \| list[SignalStatus]` |
| `is_clipped(channel=None)` | `bool \| list[bool]` |

### Settings

| Signature | Returns |
| --- | --- |
| `set_reading_unit(unit)` | `None` |
| `reading_unit()` | `str` |
| `set_sample_rate_hz(hz)` | `None` |
| `sample_rate_hz()` | `int` |
| `set_oversampling(os_idx)` | `None` |
| `oversampling()` | `int` |

### Environment

| Signature | Returns |
| --- | --- |
| `head_temperature_c()` | `float` |
| `head_humidity_percent()` | `float` |
| `die_temperature_c()` | `float` |
| `refresh_device_state()` | `None` |

### Advanced

| Signature | Returns |
| --- | --- |
| `reset()` | `None` |
| `enter_dfu_mode()` | `None` |
| `capture_buffer_address()` | `int` |

---

## `ChannelProxy`

Access via `meter.channels[n]`. Do not instantiate directly.

| Signature | Returns |
| --- | --- |
| `power_w` *(property)* | `float` — live read in watts |
| `read(unit=None, auto_range=True, n_samples=1)` | `float \| int` |
| `read_full(unit=None, auto_range=True, n_samples=1)` | `ChannelReading` |
| `range` *(property)* | `int \| None` — current range index |
| `set_range(range_index)` | `None` |
| `set_range_power(power_w)` | `int` |
| `signal_status()` | `SignalStatus` |
| `is_clipped()` | `bool` |

---

## Dataclasses

All dataclasses are frozen.

### `DeviceInfo`

| Field | Type | Meaning |
| --- | --- | --- |
| `raw_idn` | `str` | Full IDN string from the instrument |
| `frontend` | `str` | `"LINEAR"` or `"LOG"` |
| `detector` | `str` | `"INGAAS"` or `"SILICON"` |
| `gain_profile` | `str` | `"standard"` or `"linear_legacy"` |
| `port` | `str` | Serial port path or `"simulator"` |

### `ChannelReading`

| Field | Type | Meaning |
| --- | --- | --- |
| `value` | `float \| int` | Reading in the requested unit |
| `unit` | `str` | Unit token |
| `power_w` | `float` | Optical power in watts |
| `power_dbm` | `float` | Optical power in dBm |
| `signal_v` | `float` | Signal in volts |
| `signal_mv` | `float` | Signal in millivolts |
| `adc_code` | `int` | Zero-corrected (LINEAR) or raw (LOG) ADC code |
| `range_index` | `int \| None` | Active range index |
| `range_label` | `str` | Human-readable range label |
| `wavelength_nm` | `float` | Wavelength setting |
| `detector` | `str` | Detector family |
| `frontend` | `str` | Frontend type |
| `zero_source` | `str` | `"factory"`, `"user"`, or `"not_applicable"` |
| `over_range` | `bool` | Signal above 4.2 V threshold |
| `under_range` | `bool` | Signal below 5.0 mV threshold |
| `is_clipped` | `bool` | Either threshold violated |

### `MeasurementSet`

Iterable container of four `ChannelReading` objects.

- index access: `ms[0]` → `ChannelReading` for channel 0
- iteration: `for r in ms`
- `.values()` → `list[float | int]` in the requested unit

### `CaptureResult`

| Access | Returns |
| --- | --- |
| `.trace(channel)` | `list[float \| int]` — all samples for that channel |
| `.status(channel)` | `CaptureChannelStatus` |
| `.unit` | `str` |
| `.sample_rate_hz` | `int` |
| `.enabled_channels` | `tuple[int, ...]` |
| `.ranges` | `dict[int, int \| None]` |
| `.range_labels` | `dict[int, str]` |
| `.wavelength_nm` | `float` |
| `.detector` | `str` |
| `.frontend` | `str` |

### `CaptureChannelStatus`

| Field | Type | Meaning |
| --- | --- | --- |
| `any_over_range` | `bool` | At least one sample above high threshold |
| `any_under_range` | `bool` | At least one sample below low threshold |
| `any_clipped` | `bool` | Either threshold violated |
| `over_range_samples` | `int` | Count of over-range samples |
| `under_range_samples` | `int` | Count of under-range samples |
| `clipped_samples` | `int` | Count of clipped samples |
| `peak_signal_v` | `float` | Peak absolute signal in volts |

### `SignalStatus`

| Field | Type | Meaning |
| --- | --- | --- |
| `channel` | `int` | Channel index |
| `signal_v` | `float` | Signal in volts |
| `signal_mv` | `float` | Signal in millivolts |
| `over_range` | `bool` | Exceeds 4.2 V |
| `under_range` | `bool` | Below 5.0 mV |
| `is_clipped` | `bool` | Either threshold |

---

## Exceptions

All exceptions inherit from `coreDAQError`. Catch `coreDAQError` to handle any driver error.

```
coreDAQError(Exception)
    coreDAQConnectionError    — serial port not found, device not responding, IDN? failed
    coreDAQTimeoutError       — wait_until_complete() exceeded timeout
    coreDAQCalibrationError   — calibration data missing or malformed
    coreDAQUnsupportedError   — feature not available on this variant (e.g. zero_dark on LOG)
```

```python
from py_coreDAQ import coreDAQ, coreDAQError, coreDAQConnectionError

try:
    with coreDAQ.connect() as meter:
        print(meter.read_all())
except coreDAQConnectionError as e:
    print("No device found:", e)
except coreDAQError as e:
    print("Driver error:", e)
```
