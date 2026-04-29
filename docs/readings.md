# Readings and Units

## Default Unit Behavior

`coreDAQ` reads in watts by default.

```python
meter.set_reading_unit("w")
```

Accepted units are:

- `w`
- `dbm`
- `v`
- `mv`
- `adc`

The default applies to both live reads and captured traces unless a method call passes `unit=...`.

## Live Reading Methods

- `read_all(unit=None, autorange=False)`
- `read_channel(channel, unit=None, autorange=False)`
- `read_channel1()` through `read_channel4()`

Each live read returns named fields instead of tuple positions:

- `value`
- `unit`
- `power_w`
- `power_dbm`
- `signal_v`
- `signal_mv`
- `adc_code`
- `range_index`
- `range_label`
- `zero_source`
- `over_range`
- `under_range`
- `is_clipped`

## Clipping and Low-Signal Status

The API reports:

- `over_range` when `abs(signal_v) > 4.2`
- `under_range` when `abs(signal_mv) < 5.0`
- `is_clipped` when either condition is true

You can check this directly:

```python
status = meter.signal_status(channel=1)
print(status.is_clipped)

all_flags = meter.is_clipped()
print(all_flags)
```
