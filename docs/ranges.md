# Ranges and AutoRange

Range control applies to **LINEAR frontends only**. On LOG frontends, `get_range()` returns `None` and `set_range()` raises `coreDAQUnsupportedError`.

Examples below use a LINEAR simulator: `coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS")`.

## Range methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `get_range(channel)` | `int \| None` | Inspect one channel's active range index |
| `get_ranges()` | `list[int \| None]` | Inspect all four channels |
| `set_range(channel, range_index)` | `None` | Force one channel to a specific range index |
| `set_ranges(range_indices)` | `list[int \| None]` | Force all four channels at once |
| `set_range_power(channel, power_w)` | `int` | Pick the best range for a target optical power level |
| `set_range_powers(power_w_values)` | `list[int \| None]` | Apply power-based range choice to all four channels |
| `supported_ranges()` | `list[dict]` | Full range table with labels and full-scale power values |

## Standard range table

| Range index | Label | Full-scale optical power |
| --- | --- | --- |
| `0` | `5 mW` | `5e-3 W` |
| `1` | `1 mW` | `1e-3 W` |
| `2` | `500 uW` | `500e-6 W` |
| `3` | `100 uW` | `100e-6 W` |
| `4` | `50 uW` | `50e-6 W` |
| `5` | `10 uW` | `10e-6 W` |
| `6` | `5 uW` | `5e-6 W` |
| `7` | `500 nW` | `500e-9 W` |

Range index 0 is the lowest TIA gain (highest power, lowest sensitivity). Range index 7 is the highest TIA gain (lowest power, highest sensitivity). Older legacy instruments may report a different profile through `supported_ranges()`.

## Manual range selection

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    meter.set_range(0, 1)                # channel 0 → range 1 (1 mW full scale)
    meter.set_ranges([1, 2, 3, 4])       # set all four channels at once

    print(meter.get_range(0))
    print(meter.get_ranges())
```

## Power-targeted range selection

`set_range_power()` picks the smallest range whose full-scale power is >= the requested power. If the requested power exceeds the range 0 full-scale, range 0 is used.

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    meter.set_range_power(0, 1e-3)                         # 1 mW → picks range 1
    meter.set_range_powers([1e-3, 5e-4, 5e-5, 5e-6])      # all four channels

    print(meter.get_ranges())
```

## ChannelProxy — per-channel range access

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    ch = meter.channels[0]

    ch.set_range(3)               # 100 uW full scale
    ch.set_range_power(50e-6)     # pick range for 50 uW
    print(ch.range)               # current range index
```

## AutoRange

`autoRange=True` is the default for all `read*()` methods. On LINEAR frontends, the driver iterates the gain until the zero-corrected ADC code falls in the target signal window (approximately 50 mV to 4 V).

- `read_channel(...)` only retunes the selected channel
- `read_all(...)` retunes all four channels together
- pass `autoRange=False` to keep the current manual range selection

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    print(meter.read_channel(0))               # autorange on
    print(meter.read_channel(0, autoRange=False))  # stay on current range
```

## Separation from capture masking

Range methods and live `read_*()` methods always operate on all four channels, regardless of the current capture channel mask. The capture mask only affects `capture()` and related DAQ methods.

## Related pages

- [Frames, Masking, and Memory Limits](frames.md) — capture channel mask
- [Read Power](readings.md) — live reads and ChannelProxy
- [Capture Data](capture.md) — block acquisition
