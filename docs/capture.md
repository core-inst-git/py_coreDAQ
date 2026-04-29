# Get Data

Use `get_data(...)` when you want a DAQ trace instead of a single powermeter reading.

## Capture All Enabled Channels

```python
capture = meter.get_data(frames=4096, unit="w")

print(capture.unit)
print(capture.enabled_channels)
print(capture.traces[1][:10])
print(capture.status(1).any_clipped)
```

## Capture One Channel

```python
capture = meter.get_data_channel1(frames=2048, unit="dbm")
trace = capture.trace(1)
```

## Temporarily Select Channels

```python
capture = meter.get_data(frames=1024, channels=[1, 3], unit="mv")
```

The API temporarily applies the requested capture channel mask, restores the previous mask after transfer, and returns channel-keyed trace data.
