# Zeroing and Signal Health

Examples below use `coreDAQ.connect(simulator=True)`.

## Zeroing model

On **LINEAR frontends**, all public readings and captures apply the active zero offset host-side before returning values. The offset is an ADC code subtracted from each channel before unit conversion.

- the factory zero is active by default at power-on
- `zero_dark()` replaces the active zero with a new dark measurement
- `restore_factory_zero()` reverts to the factory zero
- `zero_offsets_adc()` and `factory_zero_offsets_adc()` return the raw ADC counts for inspection

On **LOG frontends**, no host-side zero is applied. Calling `zero_dark()` on a LOG frontend raises `coreDAQUnsupportedError`.

## Zeroing methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `zero_dark(frames=32, settle_s=0.2)` | `tuple[int, int, int, int]` | Capture a dark baseline and set it as the active zero |
| `restore_factory_zero()` | `tuple[int, int, int, int]` | Return to the factory zero stored in the instrument |
| `zero_offsets_adc()` | `tuple[int, int, int, int]` | Inspect the currently active zero offsets |
| `factory_zero_offsets_adc()` | `tuple[int, int, int, int]` | Inspect the factory zero offsets |

## Dark zero procedure (LINEAR only)

1. Block the optical input or cap the fiber
2. Allow a moment for the detector to settle
3. Call `zero_dark()`

```python
from py_coreDAQ import coreDAQ

# LINEAR simulator
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    # block input first — then:
    offsets = meter.zero_dark(frames=32, settle_s=0.2)
    print("Active zero offsets (ADC counts):", offsets)
    print("Reading after zero:", meter.read_channel(0))
```

The `frames` parameter controls how many ADC snapshots are averaged to form the zero. Larger values reduce noise in the zero estimate.

## Restore factory zero (LINEAR only)

```python
with coreDAQ.connect(simulator=True, frontend="LINEAR", detector="INGAAS") as meter:
    meter.restore_factory_zero()
    print("Factory zero offsets:", meter.factory_zero_offsets_adc())
    print("Active zero offsets:", meter.zero_offsets_adc())
```

## LOG frontend behavior

Calling `zero_dark()` on a LOG frontend raises `coreDAQUnsupportedError`:

```python
with coreDAQ.connect(simulator=True) as meter:  # default: InGaAs LOG
    try:
        meter.zero_dark()
    except Exception as e:
        print(type(e).__name__, e)
        # coreDAQUnsupportedError: zero_dark() is not supported on LOG frontends
```

Use `meter.frontend()` to check before calling if your code handles both variants.

## Signal health methods

| Method | Returns | Typical use |
| --- | --- | --- |
| `signal_status(channel=None)` | `SignalStatus` or `list[SignalStatus]` | Inspect voltage levels and threshold flags |
| `is_clipped(channel=None)` | `bool` or `list[bool]` | Fast clipping check |

## Clipping thresholds

- `over_range` when `abs(signal_v) > 4.2`
- `under_range` when `abs(signal_mv) < 5.0`
- `is_clipped` is `True` when either threshold is violated

```python
with coreDAQ.connect(simulator=True) as meter:
    status = meter.signal_status(channel=0)
    print(status.signal_v)
    print(status.over_range)
    print(status.under_range)
    print(status.is_clipped)

    # check all channels at once
    all_status = meter.signal_status()
    all_clipped = meter.is_clipped()    # list[bool]
    print(all_clipped)
```

## Related pages

- [Read Power](readings.md) — `ChannelReading.is_clipped` and `ChannelReading.zero_source`
- [Ranges and AutoRange](ranges.md) — range selection on LINEAR frontends
