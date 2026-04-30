"""py_coreDAQ — Python driver for the coreDAQ 4-channel optical power meter.

Quick start::

    from py_coreDAQ import coreDAQ

    with coreDAQ.connect() as coredaq:       # auto-discovers real hardware
        print(coredaq.read_all())            # [W, W, W, W]

    with coreDAQ.connect(simulator=True) as coredaq:
        result = coredaq.capture(frames=500)
        print(result.trace(0))

All public names are importable from this top-level package::

    from py_coreDAQ import (
        coreDAQ, CaptureResult, ChannelReading, MeasurementSet,
        coreDAQError, coreDAQConnectionError, coreDAQTimeoutError,
    )
"""
from ._coredaq import (
    CaptureChannelStatus,
    CaptureLayout,
    CaptureResult,
    ChannelProxy,
    ChannelReading,
    DeviceInfo,
    MeasurementSet,
    SignalStatus,
    coreDAQ,
)
from ._exceptions import (
    coreDAQCalibrationError,
    coreDAQConnectionError,
    coreDAQError,
    coreDAQTimeoutError,
    coreDAQUnsupportedError,
)

__all__ = [
    # Main class
    "coreDAQ",
    # Channel proxy
    "ChannelProxy",
    # Dataclasses
    "DeviceInfo",
    "SignalStatus",
    "ChannelReading",
    "MeasurementSet",
    "CaptureLayout",
    "CaptureChannelStatus",
    "CaptureResult",
    # Exceptions
    "coreDAQError",
    "coreDAQConnectionError",
    "coreDAQTimeoutError",
    "coreDAQCalibrationError",
    "coreDAQUnsupportedError",
]
