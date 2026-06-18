# Capture with External Trigger

The instrument can synchronise a capture to an external instrument (typically a
tunable laser) through an edge on the **BNC trigger input**. There are two
modes, both selected through `arm_capture()`:

- **Start trigger (continuous)** — one edge starts recording, then the device
  samples on its own internal timer at the configured rate until `frames` frames
  are collected. For a *continuously sweeping* laser: one edge marks the start of
  the sweep, and because the sweep speed and sample rate are both fixed, each
  sample maps to a known wavelength.
- **Stepped trigger** — the device reacts to **every** edge, taking a short burst
  of samples a tunable delay after each one. For a *step-and-dwell* laser that
  emits one pulse per wavelength step. *(Requires firmware v4.3+.)*

Both are armed with `arm_capture()` — the `stepped` flag picks the variant:

```python
# Start trigger (continuous): one edge → free-run `frames` samples at the rate
coredaq.arm_capture(frames, trigger=True, trigger_rising=False)

# Stepped trigger: every edge → wait step_delay_us → burst of step_burst samples
coredaq.arm_capture(frames, trigger=True, trigger_rising=False,
                    stepped=True, step_delay_us=50, step_burst=1)
```

Choose the active edge with `trigger_rising=True` (rising) or `False` (falling).

---

## Start trigger (continuous)

`arm_capture()` arms the buffer and returns immediately, so the instrument sits
waiting at the trigger input while your script keeps running. You fire your
trigger source, wait out the acquisition window, then pull the data with
`collect_capture()`:

```python
import time
from py_coreDAQ import coreDAQ

frames = 4096
with coreDAQ.connect() as coredaq:
    sample_rate = coredaq.sample_rate_hz()

    # Arm — returns immediately; the instrument is now waiting at the trigger input.
    coredaq.arm_capture(frames, trigger=True, trigger_rising=True)

    # Fire your trigger source.
    my_signal_generator.trigger()

    # Wait the acquisition window. Do NOT send commands to the device during it.
    time.sleep(frames / sample_rate + 0.5)

    # Transfer and convert.
    result = coredaq.collect_capture(frames, unit="w")
    print(result.trace(0)[:10])
```

---

## Stepped trigger

A step-tuned laser jumps to a wavelength, dwells briefly, then jumps again,
emitting one trigger pulse per step. In stepped mode, on **every** edge the
device:

> **waits `step_delay_us` → takes a burst of `step_burst` samples → re-arms for the next edge.**

```python
import time
from py_coreDAQ import coreDAQ

with coreDAQ.connect() as coredaq:
    coredaq.set_sample_rate_hz(100_000)        # intra-burst sample rate
    coredaq.arm_capture(
        200_000,                # generous frame budget (see note below)
        trigger=True,
        trigger_rising=False,   # COMET start/stop output: falling edge = step start
        stepped=True,
        step_delay_us=50,       # land sampling mid-dwell
        step_burst=1,           # 1 sample per step (use >1 to average per step)
    )

    # ... the laser sweeps, emitting one pulse per step ...

    coredaq.stop_capture()                      # or it stops at the frame budget
    print(coredaq.captured_frames(), "frames,",
          coredaq.step_missed_edges(), "missed edges")
    result = coredaq.collect_capture(unit="w")  # no frame arg → take what was stored
    print(result.trace(0)[:10])
```

**Parameters**

| Parameter | Range | Meaning |
|---|---|---|
| `step_delay_us` | 1 – 65535 µs | Delay from the edge to the first sample. Set it to land in the middle of the dwell, after the laser has settled — not during the jump. ≤1 µs fires immediately. |
| `step_burst` | 1 – 255 | Samples taken per edge, back-to-back at the sample rate. `1` = one sample per step; `>1` averages down noise at each wavelength. Total samples = steps × burst. |

**Reading it back**

- `collect_capture()` with **no frame argument** collects exactly what the device
  stored (handy because you usually don't know the step count up front).
- `captured_frames()` returns the number of frames stored so far.
- `step_missed_edges()` returns how many edges were skipped because a step was
  still in flight — **`0` means every pulse was captured.**

**Don't outrun the hardware.** Keep `step_delay_us` + the burst duration shorter
than the time between trigger pulses. If pulses arrive faster than a step can
finish, the extra edges are **counted (`step_missed_edges()`) and skipped — never
silently corrupted**. In practice the device keeps up to roughly 50,000 pulses/s
at minimum delay and `step_burst=1`; every microsecond of delay and every extra
burst sample lowers that. Real step-tuned lasers dwell for tens of microseconds
to milliseconds per step, so you'll normally be far below the limit.

**Firmware requirement.** Stepped mode needs **firmware v4.3 or newer**. On older
firmware `arm_capture(..., stepped=True)` raises `coreDAQUnsupportedError` asking
you to update — continuous trigger still works. Check with
`coredaq.firmware_version()`.

---

## Edge polarity

```python
coredaq.arm_capture(frames, trigger=True, trigger_rising=True)   # rising (default)
coredaq.arm_capture(frames, trigger=True, trigger_rising=False)  # falling
```

For the Chilas COMET start/stop output, trigger on the **falling edge** — it
marks the start of the wavelength ramp.

## Selecting channels

Pass `channels=` to `collect_capture()` to override the capture mask for this
transfer (applies to both modes):

```python
result = coredaq.collect_capture(unit="mv", channels=[0, 2])
```

## Acquisition timing constraint

The MCU's DMA and SPI run at full speed during acquisition. Any command sent
during the acquisition window corrupts samples. For the continuous mode the sleep
between `arm_capture` and `collect_capture` *is* that window — don't send
commands during it. In stepped mode, querying `captured_frames()` /
`step_missed_edges()` between bursts is safe at modest step rates, but avoid it
during a high-rate burst.

## Simulator

In the simulator the trigger fires immediately, so you can exercise both
workflows without hardware:

```python
with coreDAQ.connect(simulator=True) as coredaq:
    frames = 1024
    coredaq.arm_capture(frames, trigger=True)
    time.sleep(frames / coredaq.sample_rate_hz() + 0.5)
    result = coredaq.collect_capture(frames, unit="w")
    print(result.trace(0)[:5])
```

## Troubleshooting

- **`collect_capture` returns garbage / raises (continuous)**: the sleep was too
  short — increase the margin or verify `sample_rate_hz()` matches the device.
- **Capture never fills / `captured_frames()` stays 0**: verify the trigger source
  is wired to the BNC input and producing the expected edge polarity.
- **`step_missed_edges()` is non-zero**: pulses are arriving faster than a step
  finishes — slow the trigger rate, shorten `step_delay_us`, or reduce `step_burst`.
- **`coreDAQUnsupportedError` on `stepped=True`**: the device firmware is older
  than v4.3 — update it.
- **Polarity wrong**: swap `trigger_rising=True` ↔ `trigger_rising=False`.

## Related pages

- [Capture Data](capture.md) — non-triggered capture and the manual arm/collect pattern
- [Frames, Masking, and Memory Limits](frames.md) — channel masks and max frame counts
- [Units, Sample Rate, and Oversampling](settings.md) — sample rate configuration
