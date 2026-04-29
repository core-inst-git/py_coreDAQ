# Get Data

Use `get_data(...)` when you want a DAQ trace instead of a single powermeter reading.

## Capture Current Mask Channels

```python
capture = meter.get_data(frames=4096, unit="w")

print(capture.unit)
print(capture.enabled_channels)
print(capture.traces[0][:10])
print(capture.status(0).any_clipped)
```

## Set the Capture Channel Mask

```python
meter.set_capture_channel_mask("0000 0101")
print(hex(meter.capture_channel_mask()))
print(meter.capture_channels())
```

## Capture One Channel

```python
capture = meter.get_data_channel(0, frames=2048, unit="dbm")
trace = capture.trace(0)
```

## Temporarily Select Channels

```python
capture = meter.get_data(frames=1024, channels=[0, 2], unit="mv")
```

The API accepts capture masks as integers, hex strings, or binary-style strings such as `0000 0101` or `0b0101`.

When `channels=[...]` is passed to `get_data(...)`, the API temporarily applies that capture channel mask, restores the previous mask after transfer, and returns channel-keyed trace data.
