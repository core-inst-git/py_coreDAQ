# Device State

The coreDAQ instrument is a state machine. Sending a command in the wrong state — for example, requesting a transfer before a capture has finished — causes the device to return busy, which raises `coreDAQTimeoutError`. Knowing what state the instrument is in before issuing commands prevents these errors.

Examples below use `coreDAQ.connect(simulator=True)`.

## States

| State | Meaning | What you can do |
| --- | --- | --- |
| **Idle** | No acquisition in progress | Single-shot reads, configure settings, arm a capture |
| **Armed** | Capture armed, waiting for start or trigger | Start the capture (`trigger=False`) or wait for trigger edge (`trigger=True`) |
| **Acquiring** | Recording frames into internal memory | Nothing — any command returns busy |
| **Complete** | All frames recorded; data sitting in memory | Transfer the data with `capture()` |

`capture()` moves through Armed → Acquiring → Complete and performs the transfer, all in one call. The manual arm/start API exists for advanced workflows but exposes the intermediate states.

## Inspecting state

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.capture_status())    # e.g. "IDLE", "ARMED", "RUNNING", "DONE"
    print(coredaq.remaining_frames())  # frames left to record (0 when idle or complete)
```

`capture_status()` returns the raw status string from the instrument. Use it to confirm the device is idle before arming, or to check whether a previous capture completed.

## Typical state flow

### Single-shot read

```
Idle → [read] → Idle
```

The instrument takes a measurement and returns immediately. State does not change.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.capture_status())    # IDLE
    power = coredaq.read_channel(0)
    print(coredaq.capture_status())    # IDLE
```

### Normal capture

```
Idle → Armed → Acquiring → Complete → [transfer] → Idle
```

`capture()` handles all transitions internally and returns once the transfer is done.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.capture_status())        # IDLE
    result = coredaq.capture(frames=1024)  # moves through all states and transfers
    print(coredaq.capture_status())        # IDLE
    print(result.trace(0)[:5])
```

### Triggered capture

```
Idle → Armed → [waiting for edge] → Acquiring → Complete → [transfer] → Idle
```

The instrument stays in Armed while waiting for the trigger. `capture()` blocks on the host side until the edge arrives, recording finishes, and the transfer completes.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    result = coredaq.capture(frames=1024, trigger=True)
    # call only returns after the trigger fires and transfer is done
```

## What raises `coreDAQTimeoutError`

The device will reject any command sent while it is Acquiring and return a busy status. The driver converts this into `coreDAQTimeoutError`. Common causes:

- Sending a second `capture()` before the first one returns
- Calling `read_channel()` while a capture is running
- Requesting very high averaging (`n_samples` large) at a low sample rate, then sending another command before the averaging finishes

```python
from py_coreDAQ import coreDAQTimeoutError

try:
    result = coredaq.capture(frames=4096)
except coreDAQTimeoutError as e:
    print("Device busy:", e)
    # check capture_status() to understand the current state
    print(coredaq.capture_status())
```

## Environment state

The instrument also exposes sensor readings from the optical head. These are independent of the acquisition state and can be read at any time the device is idle.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    print(coredaq.head_temperature_c())
    print(coredaq.head_humidity_percent())
    print(coredaq.die_temperature_c())
```

Call `refresh_device_state()` to force a re-read of all cached sensor values from the instrument.

```python
with coreDAQ.connect(simulator=True) as coredaq:
    coredaq.refresh_device_state()
    print(coredaq.head_temperature_c())
```

## Related pages

- [Capture Data](capture.md) — `capture()` and `CaptureResult`
- [Capture with External Trigger](trigger.md) — triggered capture
- [Read Power](readings.md) — single-shot reads and the busy error
