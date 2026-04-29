# Migration Guide

The refreshed API is a clean break from the older `py_coreDAQ` surface.

## Import Change

Old:

```python
from py_coreDAQ import CoreDAQ
```

New:

```python
from py_coreDAQ import coreDAQ
```

## Main Rename Map

- `CoreDAQ` -> `coreDAQ`
- `idn()` -> `identify()`
- `frontend_type()` -> `frontend()`
- `detector_type()` -> `detector()`
- detector type is now auto-detected and no public setter is exposed
- `get_wavelength_nm()` -> `wavelength_nm()`
- `get_wavelength_limits_nm()` -> `wavelength_limits_nm()`
- `get_responsivity_A_per_W(...)` -> `responsivity_a_per_w(...)`
- `snapshot_W()` -> `read_all()` or `read_channel(...)`
- `snapshot_mV()` / `snapshot_volts()` -> `read_*` with `unit="mv"` or `unit="v"`
- detailed metadata now lives in `read_all_full()` and `read_channel_full(...)`
- channels are now public `0..3` instead of `1..4`
- `set_gain(...)` -> `set_range(...)` or `set_range_power(...)`
- `get_gains()` -> `get_ranges()`, `get_range_all()`, or `get_range(...)`
- `soft_zero_from_snapshot(...)` -> `zero_dark(...)`
- `get_linear_zero_adc()` -> `zero_offsets_adc()`
- `get_factory_zero_adc()` -> `factory_zero_offsets_adc()`
- `arm_acquisition(...)` -> `arm_capture(...)`
- `start_acquisition()` -> `start_capture()`
- `stop_acquisition()` -> `stop_capture()`
- `transfer_frames_W(...)` -> `get_data(..., unit="w")`
- `transfer_frames_mV(...)` -> `get_data(..., unit="mv")`
- `transfer_frames_volts(...)` -> `get_data(..., unit="v")`
- `transfer_frames_adc(...)` -> `get_data(..., unit="adc")`
- capture channel masking is now exposed as `capture_channel_mask()` and `set_capture_channel_mask(...)`
- `get_freq_hz()` -> `sample_rate_hz()`
- `set_freq(...)` -> `set_sample_rate_hz(...)`
- `get_oversampling()` -> `oversampling()`
- `find()` -> `discover()`
