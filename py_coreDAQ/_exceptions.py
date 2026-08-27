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


class coreDAQLicenseError(coreDAQUnsupportedError):
    """Raised when the firmware refuses an operation with ``ERR LICENSE``.

    The connected unit's license tier (see ``tier()``) does not include the
    requested feature — e.g. the high-bandwidth mode, >100 kHz sample rates,
    or multi-unit sync on a Base-tier unit.  Tier limits are enforced in
    firmware; there is deliberately no software unlock in this driver.
    Subclasses coreDAQUnsupportedError: catching that (or coreDAQError)
    handles license refusals too.
    """


class coreDAQStateError(coreDAQError):
    """Raised when a command is refused because of the device's current state.

    Examples: transferring when nothing is captured (``ERR EMPTY``), sending
    master-only commands to a coreLINK slave (``ERR SLAVE_MODE``), changing
    settings while a capture is active (``ERR BUSY``), or starting a
    trigger-armed capture with start_capture().  Fix the ordering/state and
    retry; the device itself is healthy.
    """


class coreDAQSyncError(coreDAQError):
    """Raised when a multi-unit lockstep run failed and must be discarded.

    Firmware contract: a slave capture ending with fewer frames than armed
    (or zero) means the shared conversion clock was interrupted — reversed
    sync cable (0 frames), master death mid-run, or a tier-pace abort.
    Discard the run and re-run; do not merge partial data.
    """


# Map a firmware "ERR <TOKEN> ..." payload to the most specific exception.
# The message format ("<context> failed: <payload>") is stable API — tests
# and user code match on it.
_ERR_TOKEN_CLASSES = {
    "LICENSE": coreDAQLicenseError,
    "SLAVE_MODE": coreDAQStateError,
    "EMPTY": coreDAQStateError,
    "BUSY": coreDAQStateError,
    "USB_ONLY": coreDAQUnsupportedError,
    "NOT_SUPPORTED": coreDAQUnsupportedError,
}


def error_for_payload(context: str, payload: str) -> coreDAQError:
    """Build the appropriate exception for a firmware ERR reply.

    ``context`` names the operation ("TIER?", "arm_capture", ...);
    ``payload`` is the text after "ERR".  Unrecognized payloads produce the
    plain coreDAQError with the historical message format.
    """
    token = payload.split()[0].upper() if payload.split() else ""
    cls = _ERR_TOKEN_CLASSES.get(token, coreDAQError)
    return cls(f"{context} failed: {payload}")


# Internal alias used by _CoreDAQDriver to raise errors that _call() will
# re-raise as coreDAQError subclasses.  External code should never catch this
# directly.
CoreDAQError = coreDAQError
