# Capture with External Trigger

Use `trigger=True` when another instrument or timing source should decide when the capture begins. The coreDAQ arms the acquisition and holds until a BNC trigger edge arrives, then records the requested number of frames.

Examples below use `coreDAQ.connect(simulator=True)`. In the simulator, the trigger fires immediately.

## Triggered capture behavior

With `capture(..., trigger=True)` the instrument:

1. Arms the capture buffer for the requested number of frames
2. Waits for the external trigger edge on the BNC input
3. Records frames continuously from that edge
4. Transfers all samples to the host after completion

## Single-channel triggered capture

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture_channel(
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
with coreDAQ.connect(simulator=True) as meter:
    result = meter.capture(
        frames=4096,
        unit="mv",
        channels=[0, 2],
        trigger=True,
        trigger_rising=False,   # capture on falling edge
    )

    print(result.enabled_channels)   # (0, 2)
    print(result.trace(0)[:5])
    print(result.trace(2)[:5])
```

## Edge polarity

- `trigger_rising=True` — capture starts on the rising edge (default)
- `trigger_rising=False` — capture starts on the falling edge

## Notes

- triggered capture returns the same `CaptureResult` type as non-triggered capture
- passing `channels=[...]` to `capture()` temporarily overrides the capture mask and restores it after transfer
- `capture()` blocks until the trigger fires and the transfer completes; set `timeout_s` inside `wait_until_complete()` if you need a bounded wait (advanced use only)

## Troubleshooting

- **Capture never returns**: verify the external trigger source is connected to the instrument trigger input and is producing the expected edge
- **Wrong samples**: switch between `trigger_rising=True` and `trigger_rising=False`
- **Timeout**: reduce `frames`, confirm the trigger is present, and verify the capture channel mask is not larger than intended (see [Frames, Masking, and Memory Limits](frames.md))

## Related pages

- [Capture Data](capture.md) — non-triggered capture
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and SDRAM limits
