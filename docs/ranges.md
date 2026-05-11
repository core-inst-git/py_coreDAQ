# Ranges and AutoRange

Range control applies to **LINEAR frontends only**. On LOG frontends, `get_range()` returns `None` and `set_range()` raises `coreDAQUnsupportedError`.

Examples below use a LINEAR simulator: `coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS")`.

## Range methods

| Method | Returns | Description |
| --- | --- | --- |
| `get_range(channel)` | `int \| None` | Inspect one channel's active range index |
| `get_ranges()` | `list[int \| None]` | Inspect all four channels |
| `set_range(channel, range_index)` | `None` | Force one channel to a specific range index |
| `set_ranges(range_indices)` | `list[int \| None]` | Force all four channels at once |
| `set_range_power(channel, power_w)` | `int` | Pick the best range for a target optical power level |
| `set_range_powers(power_w_values)` | `list[int \| None]` | Apply power-based range choice to all four channels |
| `supported_ranges()` | `list[dict]` | Full range table with labels and full-scale power values |
| `set_autorange(enabled)` | `None` | Enable or disable the global autoRange setting |
| `autorange()` | `bool` | Return the current global autoRange setting |

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

## AutoRange — global setting

AutoRange is **on by default**. When enabled, the driver automatically hunts for the best TIA gain range before every `read_*()` call on LINEAR frontends. The driver iterates the gain until the zero-corrected ADC code falls inside the target signal window (approximately 50 mV to 4 V).

Use `set_autorange()` to control this globally:

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    print(coredaq.autorange())       # True  — on by default

    coredaq.set_autorange(False)     # disable globally
    print(coredaq.autorange())       # False

    coredaq.set_autorange(True)      # re-enable
```

### AutoRange is automatically disabled by manual range calls

Calling any of `set_range()`, `set_ranges()`, `set_range_power()`, or `set_range_powers()` **immediately turns off global autoRange**. This ensures the range you explicitly chose is respected on all subsequent reads — not overwritten by the autoRange algorithm on the very next call.

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    print(coredaq.autorange())       # True

    coredaq.set_range(0, 3)          # fix channel 0 to range 3 (100 uW)
    print(coredaq.autorange())       # False — automatically disabled

    # All subsequent reads respect the fixed range
    print(coredaq.read_channel(0))   # uses range 3, no retuning
    print(coredaq.read_all())        # same

    # Re-enable whenever you want automatic selection again
    coredaq.set_autorange(True)
    print(coredaq.read_channel(0))   # autoRange on again
```

This applies to all four manual range setters and their ChannelProxy equivalents (`ch.set_range()`, `ch.set_range_power()`).

### Per-call override

Pass `autoRange=True` or `autoRange=False` to any individual `read_*()` call to override the global setting **for that measurement only**. The global setting is not affected.

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    coredaq.set_autorange(False)

    # Override just this one read
    print(coredaq.read_channel(0, autoRange=True))    # autoranges once, global stays False
    print(coredaq.autorange())                        # still False

    coredaq.set_autorange(True)
    print(coredaq.read_channel(0, autoRange=False))   # fixed range for this read only
    print(coredaq.autorange())                        # still True
```

- `read_channel(...)` retunes the selected channel only
- `read_all(...)` retunes all four channels together

## Manual range selection

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    # Both calls fix the range and disable global autoRange
    coredaq.set_range(0, 1)                # channel 0 → range 1 (1 mW full scale)
    coredaq.set_ranges([1, 2, 3, 4])       # all four channels at once

    print(coredaq.get_range(0))
    print(coredaq.get_ranges())
    print(coredaq.autorange())             # False
```

## Power-targeted range selection

`set_range_power()` picks the smallest range whose full-scale power is ≥ the requested power and disables global autoRange. If the requested power exceeds range 0's full-scale, range 0 is used.

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    coredaq.set_range_power(0, 1e-3)                         # 1 mW → picks range 1
    coredaq.set_range_powers([1e-3, 5e-4, 5e-5, 5e-6])      # all four channels

    print(coredaq.get_ranges())
    print(coredaq.autorange())             # False
```

## ChannelProxy — per-channel range access

`ch.set_range()` and `ch.set_range_power()` on a `ChannelProxy` follow the same rule: they fix the range on the selected channel and disable global autoRange.

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as coredaq:
    ch = coredaq.channels[0]

    ch.set_range(3)               # 100 uW full scale — autoRange disabled
    ch.set_range_power(50e-6)     # pick range for 50 uW — autoRange disabled
    print(ch.range)               # current range index

    coredaq.set_autorange(True)   # re-enable for all channels
```

## Separation from capture masking

Range methods and live `read_*()` methods always operate on all four channels regardless of the current capture channel mask. The capture mask only affects `capture()` and related DAQ methods.

## Related pages

- [Frames, Masking, and Memory Limits](frames.md) — capture channel mask
- [Read Power](readings.md) — single-shot reads and ChannelProxy
- [Capture Data](capture.md) — block acquisition
