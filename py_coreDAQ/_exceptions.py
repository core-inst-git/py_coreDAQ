"""Exception hierarchy for coreDAQ.

All exceptions raised by the public API are subclasses of coreDAQError.
Catch coreDAQError to handle any device error; catch a subclass for finer
control.
"""


class coreDAQError(Exception):
    """Base exception for all coreDAQ errors."""


class coreDAQConnectionError(coreDAQError):
    """Raised when the device cannot be opened or does not respond.

    Typical causes: USB cable not connected, port path wrong, device in DFU
    mode, or IDN? did not return a coreDAQ identifier within the timeout.
    """


class coreDAQTimeoutError(coreDAQError):
    """Raised when a device operation exceeds its time limit.

    Covers: snapshot poll timeout, wait_until_complete timeout, XFER idle
    timeout, and busy-retry exhaustion.
    """


class coreDAQCalibrationError(coreDAQError):
    """Raised when calibration data is missing or malformed.

    Typical causes: firmware returned an unexpected CAL or LOGCAL response,
    or the loaded responsivity curve data is incomplete.
    """


class coreDAQUnsupportedError(coreDAQError):
    """Raised when a feature is not available on the connected variant.

    Examples: calling set_range() on a LOG frontend, or calling zero_dark()
    on a LOG frontend.  Check pm.frontend() before calling variant-specific
    methods if you work with both frontend types.
    """


# Internal alias used by _CoreDAQDriver to raise errors that _call() will
# re-raise as coreDAQError subclasses.  External code should never catch this
# directly.
CoreDAQError = coreDAQError
