# Capture with External Trigger

Use `trigger=True` to synchronize the start of a capture with an external signal. The instrument arms the acquisition and waits for the trigger edge before beginning to record. Once the edge arrives, internal timing takes over — the sample rate and frame count you configured determine what gets recorded.

The trigger controls **when recording starts**, not the timing between samples. Sample timing is always governed by the configured sample rate.

Examples below use `coreDAQ.connect(simulator=True)`. In the simulator, the trigger fires immediately.

## Triggered capture behavior

With `capture(..., trigger=True)` the instrument:

1. Arms the capture buffer for the requested number of frames
2. Waits at the trigger input for the specified edge (rising or falling)
3. Begins recording at the configured sample rate as soon as the edge arrives
4. Transfers all samples to the host after the buffer is full

## Single-channel triggered capture

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture_channel(
        0,
        frames=2048,
        unit="adc",
        trigger=True,
        trigger_rising=True,
    )
    print(result.trace(0)[:5])
```

## Multi-channel triggered capture

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(
        frames=4096,
        unit="mv",
        channels=[0, 2],
        trigger=True,
        trigger_rising=False,   # start on falling edge
    )

    print(result.enabled_channels)   # (0, 2)
    print(result.trace(0)[:5])
    print(result.trace(2)[:5])
```

## Edge polarity

- `trigger_rising=True` — recording starts on the rising edge (default)
- `trigger_rising=False` — recording starts on the falling edge

## Notes

- triggered capture returns the same `CaptureResult` type as non-triggered capture
- passing `channels=[...]` to `capture()` temporarily overrides the capture mask and restores it after transfer
- `capture()` blocks until the trigger fires and all frames are transferred; make sure the trigger source is producing the expected edge before calling

## Troubleshooting

- **Capture never returns**: verify the external trigger source is connected to the BNC trigger input and is producing the expected edge
- **Polarity wrong**: switch between `trigger_rising=True` and `trigger_rising=False`
- **Too few samples**: check that `frames` and your configured sample rate give you the time window you need — see [Frames, Masking, and Memory Limits](frames.md)

## Related pages

- [Capture Data](capture.md) — non-triggered capture
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and SDRAM limits
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate configuration
