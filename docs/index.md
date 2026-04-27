# py_coreDAQ

`py_coreDAQ` is the Python API for the coreDAQ instrument.

## What This Module Covers

- Device discovery and connection
- Frontend detection for `LINEAR` and `LOG`
- Detector handling for `INGAAS` and `SILICON`
- Gain control, snapshots, streamed frame transfer, and acquisition state
- Voltage and optical-power conversion
- Embedded detector responsivity curves for wavelength-aware power conversion

## Install

```bash
pip install -r requirements.txt
```

## Basic Example

```python
from py_coreDAQ import CoreDAQ

with CoreDAQ("/dev/tty.usbmodemXXXX") as daq:
    print(daq.idn())
    daq.set_wavelength_nm(1550.0)
    print(daq.snapshot_W(n_frames=4))
```

## Key API Areas

### Connection and Discovery

- `CoreDAQ(port, timeout=0.15, inter_command_gap_s=0.0)`
- `CoreDAQ.find()`
- `idn()`
- `frontend_type()`
- `detector_type()`

### Responsivity and Wavelength

- `set_detector_type(detector)`
- `set_wavelength_nm(wavelength_nm)`
- `get_wavelength_nm()`
- `set_responsivity_reference_nm(wavelength_nm)`
- `get_responsivity_A_per_W(detector=None, wavelength_nm=None)`
- `load_responsivity_curves_json(path)` for custom overrides

### Data Capture

- `snapshot_adc()`
- `snapshot_mV()`
- `snapshot_volts()`
- `snapshot_W()`
- `transfer_frames_adc()`
- `transfer_frames_mV()`
- `transfer_frames_volts()`
- `transfer_frames_W()`

### Gain and Acquisition

- `set_gain(head, value)`
- `get_gains()`
- `arm_acquisition(frames, use_trigger=False, trigger_rising=True)`
- `start_acquisition()`
- `stop_acquisition()`
- `wait_for_completion()`

## Notes

- The runtime driver now embeds the default responsivity curves directly in the Python module.
- [`responsivity_curves.json`](../responsivity_curves.json) is kept as a reference/source artifact.
- Existing command methods were preserved; only the responsivity data-loading path was internalized.
