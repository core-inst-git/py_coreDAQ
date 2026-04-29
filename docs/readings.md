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

- `read_all(unit=None, autoRange=True, n_samples=1)`
- `read_channel(channel, unit=None, autoRange=True, n_samples=1)`

These return plain values in the requested unit:

- `read_all()` returns a list for all four channels
- `read_channel(channel)` returns a single value for channel `0..3`
- `n_samples` controls the `SNAP n` count used for the read
- `n_samples` must be between `1` and `32`
- `autoRange=True` retunes only the channels being read
- pass `autoRange=False` to keep the current manual range selection

If you want the full measurement object, use:

- `read_all_full(unit=None, autoRange=True, n_samples=1)`
- `read_channel_full(channel, unit=None, autoRange=True, n_samples=1)`

For `LINEAR` frontends, `autoRange` uses the raw ADC code from the device, subtracts the active zero offset, and aims for a working window of about `50 mV` to `4 V`.

Detailed reads expose named fields such as:

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

## Channel Indexing

Public channel numbering is `0..3`.

- Channel `0` is the first channel
- Channel `3` is the fourth channel

## Range Control

- `get_ranges()` returns the range indices for all four channels
- `get_range_all()` is an explicit alias for all four channels
- `get_range(channel)` returns the range index for one channel
- `set_range(channel, range_index)` sets one channel explicitly
- `set_ranges(range_indices)` sets all four channels in order
- `set_range_power(channel, power_w)` chooses the smallest suitable range for the requested watt level
- `set_range_powers(power_w_values)` does the same for all four channels

If the requested power exceeds the instrument maximum, the API falls back to the largest range.

Read and range methods do not follow the DAQ capture mask. The capture mask is only used by capture methods.

## Clipping and Low-Signal Status

The API reports:

- `over_range` when `abs(signal_v) > 4.2`
- `under_range` when `abs(signal_mv) < 5.0`
- `is_clipped` when either condition is true

You can check this directly:

```python
status = meter.signal_status(channel=0)
print(status.is_clipped)

all_flags = meter.is_clipped()
print(all_flags)
```
