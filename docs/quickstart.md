# Quickstart

## Connect and Read Power

```python
from py_coreDAQ import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    print(meter.identify())
    print(meter.frontend(), meter.detector())

    meter.set_wavelength_nm(1550.0)
    power_w = meter.read_channel(0, n_samples=8)
    power_dbm = meter.read_channel(0, unit="dbm", n_samples=8)

    print(power_w, "W")
    print(power_dbm, "dBm")
```

## Read All Four Channels

```python
from py_coreDAQ import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    meter.set_reading_unit("dbm")
    readings = meter.read_all()
    print(readings)
```

## Default Global Read Settings

At initialization the API sets:

- sample rate to `500 Hz`
- oversampling to `OS 1`

You can change them globally:

```python
meter.set_sample_rate_hz(1000)
meter.set_oversampling(2)
```

## Set and Get Ranges

```python
meter.set_range(0, 1)
meter.set_range_power(1, 1e-3)
meter.set_ranges([1, 2, 3, 4])
print(meter.get_ranges())
print(meter.get_range_all())
print(meter.get_range(0))
```

## Capture Channel Mask

```python
meter.set_capture_channel_mask("0000 0101")
print(hex(meter.capture_channel_mask()))
print(meter.capture_channels())
```

## Read Full Measurement Data

```python
reading = meter.read_channel_full(0, unit="mv")
print(reading.signal_mv)
print(reading.range_label)
print(reading.is_clipped)
```

## Change Units Temporarily

```python
reading_w = meter.read_channel(0)
reading_v = meter.read_channel(0, unit="v")
reading_adc = meter.read_channel(0, unit="adc")
```

The per-call `unit=` override does not change the default unit stored by `set_reading_unit(...)`.

## Read Averaging

All `read*` methods accept `n_samples`.

- default: `n_samples=1`
- maximum: `n_samples=32`
- `autoRange=True` by default
- pass `autoRange=False` when you want to keep the current manual range

Example:

```python
power = meter.read_channel(0, n_samples=32)
full = meter.read_channel_full(0, unit="mv", n_samples=16)
```
