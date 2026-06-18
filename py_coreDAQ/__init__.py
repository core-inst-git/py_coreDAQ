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

External trigger modes (capture synchronized to a tunable laser via the
trigger BNC). Select rising/falling edge with ``trigger_rising``:

* **Start trigger (continuous)** — one edge starts sampling on the internal
  timer at the configured rate, for the requested number of frames. For a
  continuously sweeping laser, where frame index maps to wavelength::

      coredaq.arm_capture(N, trigger=True, trigger_rising=False)
      # laser sweep fires the edge ...
      result = coredaq.collect_capture(N)

* **Stepped trigger** — every edge fires a burst of conversions after a
  tunable delay; for a step-and-dwell laser (one pulse per step). Requires
  firmware v4.3+ (older firmware raises ``coreDAQUnsupportedError``)::

      coredaq.arm_capture(N, trigger=True, trigger_rising=False,
                          stepped=True, step_delay_us=50, step_burst=1)
      # laser steps, one pulse per step ...
      result = coredaq.collect_capture()       # collects what was stored
      print(coredaq.step_missed_edges())       # 0 == every edge captured

  ``step_delay_us`` (1..65535) places sampling inside the dwell; ``step_burst``
  (1..255) takes that many samples per step. Keep ``delay + burst`` shorter
  than the trigger period or edges are counted as missed and skipped.
"""
__version__ = "1.2.1"

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
    "__version__",
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
