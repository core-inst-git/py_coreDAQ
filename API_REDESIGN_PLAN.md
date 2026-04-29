# API_REDESIGN_PLAN.md — coreDAQ Python API Redesign

## 1. Codebase Audit

### 1.1 File-by-file summary

| File | Lines | What it is |
|---|---|---|
| `py_coreDAQ.py` | 2819 | The entire library in one file: USB-serial transport, low-level firmware protocol driver (`_CoreDAQDriver`, lines 31–1735), built-in responsivity curve data (lines 1737–1790), public dataclasses (lines 1793–1904), and the high-level `coreDAQ` class (lines 1907–2819) |
| `tests/test_coredaq_api.py` | 349 | 11 unit tests using `unittest.mock.patch`; all tests run against hand-rolled fake driver classes (`_BaseFakeDriver`, `_LinearFakeDriver`, `_LogFakeDriver`); no hardware-in-loop tests |
| `pyproject.toml` | 18 | `setuptools` build config; `requires-python = ">=3.9"`; runtime deps: `pyserial>=3.5`, `numpy>=1.24` |
| `requirements.txt` | 3 | Mirror of pyproject runtime deps |
| `docs/*.md` | ~11 files | MkDocs Material site; covers every public method with examples; all examples require physical hardware |

### 1.2 Current public surface

**Connection and identity**
- `coreDAQ(port, timeout=0.15, inter_command_gap_s=0.0)` — constructor; caller must supply the serial port string
- `coreDAQ.discover(baudrate, timeout)` → `list[str]` — static probe; separate from construction
- `identify(refresh=False)` → `str`
- `device_info(refresh=False)` → `DeviceInfo`
- `frontend()` → `"LINEAR"` or `"LOG"`
- `detector()` → `"INGAAS"` or `"SILICON"`

**Wavelength and responsivity**
- `wavelength_nm()` → `float`
- `set_wavelength_nm(nm)` → `None`
- `wavelength_limits_nm(detector=None)` → `(float, float)`
- `responsivity_a_per_w(wavelength_nm, detector=None)` → `float`

**Reading (live power)**
- `read_all(unit, autoRange, n_samples)` → `list[float|int]`
- `read_channel(channel, unit, autoRange, n_samples)` → `float|int`
- `read_all_full(unit, autoRange, n_samples)` → `MeasurementSet`
- `read_channel_full(channel, unit, autoRange, n_samples)` → `ChannelReading`
- `read_all_details(...)` / `read_channel_details(...)` — undocumented aliases for the `_full` variants

**Ranges (LINEAR frontends only)**
- `get_range(channel)` → `int | None`
- `get_ranges()` / `get_range_all()` → `list[int|None]`
- `set_range(channel, range_index)` / `set_power_range(channel, range_index)` — alias pair
- `set_ranges(range_indices)` → `list[int|None]`
- `set_range_power(channel, power_w)` → `int`
- `set_range_powers(power_w_values)` → `list[int|None]`
- `current_ranges()` → alias for `get_ranges()`
- `supported_ranges()` → `list[dict]`

**Zeroing (LINEAR frontends only)**
- `zero_dark(frames=32, settle_s=0.2)` → `tuple[int,int,int,int]`
- `restore_factory_zero()` → `tuple[int,int,int,int]`
- `zero_offsets_adc()` / `factory_zero_offsets_adc()` → `tuple[int,int,int,int]`

**Signal health**
- `signal_status(channel=None)` → `SignalStatus | list[SignalStatus]`
- `is_clipped(channel=None)` → `bool | list[bool]`

**Capture**
- `capture(frames, unit, channels, trigger, trigger_rising)` / `get_data(...)` — alias pair
- `capture_channel(channel, frames, unit, trigger, trigger_rising)` / `get_data_channel(...)` — alias pair
- `arm_capture(frames, trigger, trigger_rising)` → `None`
- `start_capture()` / `stop_capture()` → `None`
- `capture_status()` → `str`
- `remaining_frames()` → `int`
- `wait_until_complete(poll_s, timeout_s)` → `None`

**Capture mask**
- `capture_layout()` → `CaptureLayout`
- `capture_channel_mask()` / `set_capture_channel_mask(mask)` → `int`
- `capture_channels()` / `set_capture_channels(channels)` → `tuple[int,...]`
- `enabled_channels()` / `set_enabled_channels(channels)` — aliases for the `capture_channels` pair
- `max_capture_frames(channels=None)` → `int`

**Settings**
- `set_reading_unit(unit)` / `reading_unit()` → `None | str`
- `set_sample_rate_hz(hz)` / `sample_rate_hz()` → `None | int`
- `set_oversampling(os_idx)` / `oversampling()` → `None | int`

**Environment**
- `head_temperature_c()` / `head_humidity_percent()` / `die_temperature_c()` → `float`
- `refresh_device_state()` → `None`

**Advanced**
- `reset()` / `enter_dfu_mode()` → `None`
- `capture_buffer_address()` → `int`

### 1.3 USB transport layer

Transport: **pyserial** (`serial.Serial`) at `py_coreDAQ.py:156`.

```python
self._ser = serial.Serial(port=port, baudrate=115200, timeout=timeout, write_timeout=0.5)
```

The ASCII request-response protocol lives in `_ask()` (lines 264–280): send a single `\n`-terminated command string, read one response line. The device replies with one of three prefixes: `OK <payload>`, `ERR <reason>`, or `BUSY`. `_ask_with_busy_retry()` (lines 282–291) polls up to 20 times with 50 ms delay on BUSY responses.

A `threading.Lock` at line 162 serializes all serial I/O. The public `coreDAQ` class never touches `serial.Serial` directly — it delegates through `_call(self._driver.method, ...)` (line 1943), which translates the private `CoreDAQError` into the public `coreDAQError`.

### 1.4 Per-variant logic divergence

The four variants diverge in three places.

**Frontend detection** (line 318): a single `HEAD_TYPE?` firmware query sets `_frontend_type` to `"LINEAR"` or `"LOG"` at construction time. All downstream behavior is gated on this string.

**Snapshot reads:**
- LINEAR: `_read_linear_codes_and_ranges()` (lines 2286–2329) subtracts per-channel zero offsets and may iterate up to 4 times for autoranging; calls `_convert_linear_mv_to_power_w(channel, gain, mv)` using an empirical slope/intercept table.
- LOG: `snapshot_adc()` returns raw codes; calls `_convert_log_voltage_to_power_w(v_volts, head_idx)` which dispatches on `_detector_type` to either InGaAs LUT interpolation or a silicon analytical model.

**Calibration load** (`_load_calibration_for_frontend`, called at line 212):
- InGaAs LINEAR: loads `_cal_slope[head][gain]` and `_cal_intercept[head][gain]` from the device via `CAL_SLOPE?` / `CAL_INTERCEPT?` firmware commands.
- InGaAs LOG: loads a LUT from `LOGLUT?`; stored as parallel lists `_loglut_V_V` / `_loglut_log10P`; per-head variants in `_loglut_*_by_head`.
- Si LOG: derives from an analytical log-amp transfer function using `_silicon_log_vy_v_per_decade` and `_silicon_log_iz_a` plus wavelength-dependent responsivity.
- Si LINEAR: derives from TIA resistance tables `_silicon_linear_tia_ohm[channel][gain]`, bootstrapped from loaded calibration when available.

All four paths are interleaved inside a single 1700-line `_CoreDAQDriver` class with no strategy pattern or subclassing. Variant-specific branches are scattered `if self._frontend_type == "LINEAR"` / `if detector == "INGAAS"` blocks.

### 1.5 Calibration details

- **InGaAs LINEAR** (empirical sweep + fit): `_cal_slope[ch][gain]` and `_cal_intercept[ch][gain]` loaded from the device at init. Power conversion: `power_w = slope * signal_mv + intercept` (per channel, per gain).
- **InGaAs LOG** (LUT): voltage → log10(power) lookup; `bisect` for linear interpolation between LUT points. Per-head LUT variants exist.
- **Si LOG** (analytical): `V_out = Vy * log10(I_pd / Iz)` where `Vy = _silicon_log_vy_v_per_decade`, `Iz = _silicon_log_iz_a`, and `I_pd = P * R(λ)` from the embedded responsivity curve.
- **Si LINEAR** (analytical): `P = V_signal / (R_tia * R(λ))` where `R_tia = _silicon_linear_tia_ohm[ch][gain]` and `R(λ)` is interpolated from `_BUILTIN_RESPONSIVITY_CURVES`.
- **Wavelength handling**: `_resp_curve_nm` and `_resp_curve_aw` are populated at init from the built-in curves at lines 1737–1790. `get_responsivity_A_per_W(wavelength_nm, detector)` uses `bisect` linear interpolation. Wavelength affects power output for all variants: directly in Si (via `R(λ)` in the equations), indirectly in InGaAs LOG (the LUT encodes the response at a reference wavelength, so `set_wavelength_nm` shifts the output via a responsivity ratio).

**Wavelength limits by detector** (enforced at `py_coreDAQ.py:64–65`):
- InGaAs: 910–1700 nm
- Silicon: 400–1100 nm

### 1.6 Tests

11 tests in `tests/test_coredaq_api.py`, all using mock driver injection. Good coverage of reading, autoranging, capture, zeroing, and signal health on `_LinearFakeDriver`. `_LogFakeDriver` is used in one test only.

**Missing coverage**: wavelength compensation math for any variant, Si log/linear calibration paths, all four variant paths in a single parameterized suite, simulator (no simulator exists yet), environment sensors (`head_temperature_c` etc.), `discover()`, `arm_capture` / `start_capture` / `wait_until_complete` sequence, `DeviceInfo` field integrity, `CaptureLayout`, `supported_ranges()`, `max_capture_frames()`, oversampling pacing.

### 1.7 Top 5 friction points

**1. Two exception classes with confusingly similar names.**
`CoreDAQError` (line 23) is private, raised by `_CoreDAQDriver`. `coreDAQError` (line 1793) is public, re-raised by `coreDAQ`. Both names are present in the module, and only one should be caught by users. The relationship is not documented.

**2. No first-class channel object.**
Every channel-specific call requires passing a channel-index argument: `meter.read_channel(0)`, `meter.set_range(0, idx)`, `meter.signal_status(0)`. In a REPL session working on one channel, the repeated `0` is noise. `meter.channels[0].power_w` is one attribute access.

**3. No simulator.**
Every code example in the docs requires a physical device on a known serial port. The existing tests use `_BaseFakeDriver` subclasses defined in the test file — not a first-class package export. An engineer running a notebook demo without hardware is stuck.

**4. `discover()` and construction are separate.**
An engineer's first encounter: copy the quickstart example, realize they need to know their serial port string, google it, find the right `/dev/tty...` or `COM` entry. `coreDAQ.connect()` with auto-discovery is the one-liner they want.

**5. Alias proliferation with no canonical name declared.**
`capture()` and `get_data()` both exist; `read_all_full()` and `read_all_details()` both exist; `get_ranges()` and `get_range_all()` both exist. Neither the README nor the API reference states which is canonical. Engineers reading source code cannot tell which form to use.

---

## 2. Proposed Public API

### 2.1 Class diagram (prose)

```
coreDAQ                              # main device handle; context manager
  .channels: list[ChannelProxy]      # indexed 0..3; thin views into coreDAQ
  .connect(port, simulator, …)       # classmethod; auto-discovers if port is None

ChannelProxy                         # thin view; no independent state
  .power_w                           # property: live read in watts
  .read(unit, autoRange, n_samples)  # → float | int
  .read_full(unit, autoRange, n_samples)  # → ChannelReading
  .range                             # property: current range index (None on LOG)
  .set_range(range_index)            # LINEAR only
  .set_range_power(power_w)          # LINEAR only; → int
  .signal_status()                   # → SignalStatus
  .is_clipped()                      # → bool

coreDAQError(Exception)              # unchanged public exception
  coreDAQTimeoutError(coreDAQError)
  coreDAQConnectionError(coreDAQError)
  coreDAQCalibrationError(coreDAQError)
  coreDAQUnsupportedError(coreDAQError)

# Frozen dataclasses — all unchanged from current
DeviceInfo, SignalStatus, ChannelReading, MeasurementSet
CaptureLayout, CaptureChannelStatus, CaptureResult
```

### 2.2 Public method signatures

#### `coreDAQ.connect` (new classmethod)

```python
@classmethod
def connect(
    cls,
    port: Optional[str] = None,
    *,
    simulator: bool = False,
    sim_frontend: str = "LOG",
    sim_detector: str = "INGAAS",
    sim_incident_power_w: float = 1e-4,
    sim_wavelength_nm: float = 1550.0,
    sim_noise_sigma_adc: float = 2.0,
    sim_seed: Optional[int] = 42,
    baudrate: int = 115200,
    timeout: float = 0.15,
) -> "coreDAQ":
    """Connect to a coreDAQ power meter, or open a simulated instance.

    Parameters
    ----------
    port : str or None
        Serial port path, e.g. ``"/dev/tty.usbmodemXXXX"`` or ``"COM3"``.
        If ``None``, calls :meth:`discover` and connects to the single found
        device. Raises ``coreDAQConnectionError`` if zero or more than one
        device is detected.
    simulator : bool
        If ``True``, return a simulated coreDAQ instance. No physical device
        is required. All ``sim_*`` parameters configure the simulation. Use
        this in doc examples so every example is runnable without hardware.
    sim_frontend : str
        Simulated frontend type: ``"LOG"`` (default) or ``"LINEAR"``.
    sim_detector : str
        Simulated detector type: ``"INGAAS"`` (default) or ``"SILICON"``.
        Combined with ``sim_frontend``, selects one of the four variants:
        InGaAs LOG (default), InGaAs LINEAR, Si LOG, Si LINEAR.
    sim_incident_power_w : float
        Baseline optical power delivered to each simulated channel, in watts.
    sim_wavelength_nm : float
        Initial wavelength setting for the simulation.
    sim_noise_sigma_adc : float
        Standard deviation of Gaussian noise added to simulated ADC codes.
    sim_seed : int or None
        Random seed for reproducible simulation output. ``None`` for stochastic.
    baudrate : int
        Serial baud rate for real-device connections. Default 115200.
    timeout : float
        Serial read timeout in seconds for real-device connections.

    Returns
    -------
    coreDAQ
        A connected (or simulated) device handle. Supports the context
        manager protocol.

    Raises
    ------
    coreDAQConnectionError
        ``port`` is ``None`` and zero or >1 coreDAQ devices are found, or
        the device does not respond to ``IDN?`` within ``timeout``.

    Examples
    --------
    Auto-discover physical hardware:

    >>> with coreDAQ.connect() as pm:
    ...     print(pm.read_channel(0))

    Simulator — default InGaAs LOG:

    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     print(pm.read_channel(0))

    Simulator — InGaAs LINEAR variant:

    >>> with coreDAQ.connect(
    ...     simulator=True,
    ...     sim_frontend="LINEAR",
    ...     sim_detector="INGAAS",
    ... ) as pm:
    ...     pm.set_range(0, 3)
    ...     print(pm.read_channel(0))

    Simulator — Si LINEAR at 850 nm:

    >>> with coreDAQ.connect(
    ...     simulator=True,
    ...     sim_frontend="LINEAR",
    ...     sim_detector="SILICON",
    ...     sim_wavelength_nm=850.0,
    ... ) as pm:
    ...     pm.set_wavelength_nm(850.0)
    ...     print(pm.read_channel(0))
    """
```

#### `coreDAQ.__init__`

```python
def __init__(
    self,
    port: str,
    timeout: float = 0.15,
    inter_command_gap_s: float = 0.0,
) -> None:
    """Open a coreDAQ device on the given serial port.

    Parameters
    ----------
    port : str
        Serial port path. Use :meth:`connect` or :meth:`discover` to
        enumerate available devices.
    timeout : float
        Serial read timeout in seconds.
    inter_command_gap_s : float
        Optional minimum delay between consecutive commands. Set > 0 only
        if the host USB stack drops packets under rapid back-to-back writes.

    Raises
    ------
    coreDAQConnectionError
        Device does not respond to ``IDN?`` within ``timeout``.
    coreDAQCalibrationError
        Calibration data returned by the device is malformed or missing.

    See Also
    --------
    connect : Preferred entry point; supports auto-discovery and simulator.
    """
```

#### `coreDAQ.discover`

```python
@staticmethod
def discover(baudrate: int = 115200, timeout: float = 0.15) -> List[str]:
    """Return serial port paths of all connected coreDAQ devices.

    Parameters
    ----------
    baudrate : int
        Baud rate used for the ``IDN?`` probe on each candidate port.
    timeout : float
        Per-port probe timeout in seconds.

    Returns
    -------
    list of str
        Port path strings in discovery order. Empty list if none found.

    Examples
    --------
    >>> ports = coreDAQ.discover()
    >>> print(ports)
    ['/dev/tty.usbmodem12401']
    """
```

#### `coreDAQ.read_channel`

```python
def read_channel(
    self,
    channel: int,
    unit: Optional[str] = None,
    autoRange: bool = True,
    n_samples: int = 1,
) -> Union[int, float]:
    """Read optical power on one channel.

    Parameters
    ----------
    channel : int
        Channel index, 0..3.
    unit : str or None
        Output unit token: ``"w"`` (default), ``"dbm"``, ``"v"``,
        ``"mv"``, or ``"adc"``. ``None`` uses the global reading unit
        set by :meth:`set_reading_unit`.
    autoRange : bool
        If ``True`` (default), automatically select the TIA gain that places
        the signal in the optimal ADC window (~50 mV to 4 V) before reading.
        Applies to LINEAR frontends only; ignored on LOG (no gain control).
    n_samples : int
        Number of ADC snapshots to average together, 1..32.

    Returns
    -------
    float or int
        Optical power in the requested unit. Returns ``int`` only when
        ``unit="adc"``.

    Raises
    ------
    ValueError
        ``channel`` is not in 0..3, or ``n_samples`` is outside 1..32.
    coreDAQError
        Device communication failure.

    See Also
    --------
    read_all : Read all four channels at once.
    read_channel_full : Read with full measurement metadata.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     print(pm.read_channel(0))            # watts (default)
    ...     print(pm.read_channel(0, unit="dbm"))
    ...     print(pm.read_channel(0, n_samples=8))  # average 8 snapshots
    """
```

#### `coreDAQ.read_all`

```python
def read_all(
    self,
    unit: Optional[str] = None,
    autoRange: bool = True,
    n_samples: int = 1,
) -> List[Union[int, float]]:
    """Read optical power on all four channels simultaneously.

    Parameters
    ----------
    unit : str or None
        Output unit token. See :meth:`read_channel` for valid values.
    autoRange : bool
        Autorange all four channels before reading. On LINEAR frontends,
        all channels are adjusted together in one pass to minimize settling
        time.
    n_samples : int
        Snapshots to average, 1..32.

    Returns
    -------
    list of float or int
        Four values ordered by channel index 0..3. The capture channel mask
        is ignored — this method always reads all four channels.

    Notes
    -----
    The active zero offset (set by :meth:`zero_dark`) is applied per channel
    on LINEAR frontends before any unit conversion. LOG frontends do not
    apply host-side zero subtraction.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     powers = pm.read_all()
    ...     print(powers)  # [0.000123, 0.000456, 0.000789, 0.001012]
    """
```

#### `coreDAQ.read_channel_full`

```python
def read_channel_full(
    self,
    channel: int,
    unit: Optional[str] = None,
    autoRange: bool = True,
    n_samples: int = 1,
) -> ChannelReading:
    """Read one channel and return a rich measurement object.

    Returns
    -------
    ChannelReading
        Frozen dataclass with fields: ``channel``, ``value``, ``unit``,
        ``power_w``, ``power_dbm``, ``signal_v``, ``signal_mv``,
        ``adc_code``, ``range_index``, ``range_label``, ``wavelength_nm``,
        ``detector``, ``frontend``, ``zero_source``, ``over_range``,
        ``under_range``, ``is_clipped``.
        ``range_index`` and ``range_label`` are ``None`` on LOG frontends.
        ``zero_source`` is ``"not_applicable"`` on LOG frontends.

    See Also
    --------
    read_channel : Plain scalar read.
    read_all_full : Rich read on all four channels.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     r = pm.read_channel_full(0)
    ...     print(r.power_w, r.range_label, r.is_clipped)
    """
```

#### `coreDAQ.read_all_full`

```python
def read_all_full(
    self,
    unit: Optional[str] = None,
    autoRange: bool = True,
    n_samples: int = 1,
) -> MeasurementSet:
    """Read all four channels and return a rich measurement set.

    Returns
    -------
    MeasurementSet
        Iterable container of four ``ChannelReading`` objects. Access by
        index (``ms[0]``), by channel number (``ms.channel(2)``), or
        iterate. Call ``.values()`` for a plain list of scalars in the
        requested unit.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     ms = pm.read_all_full(unit="dbm")
    ...     for reading in ms:
    ...         print(reading.channel, reading.value, reading.is_clipped)
    """
```

#### `coreDAQ.capture`

```python
def capture(
    self,
    frames: int,
    unit: Optional[str] = None,
    channels: Optional[Union[int, Sequence[int]]] = None,
    trigger: bool = False,
    trigger_rising: bool = True,
) -> CaptureResult:
    """Arm the ADC and capture a block of time-domain samples.

    Parameters
    ----------
    frames : int
        Number of samples per active capture channel. Must be > 0 and <=
        :meth:`max_capture_frames` for the selected channels.
    unit : str or None
        Output unit token. ``None`` uses the global reading unit.
    channels : int, sequence of int, or None
        Channels to include in this capture. ``None`` uses the current
        capture channel mask. If specified, the mask is temporarily
        overridden during the capture and restored on return, even if an
        exception occurs.
    trigger : bool
        If ``True``, arm the capture and wait for an external BNC trigger
        edge before recording. If ``False`` (default), recording starts
        immediately on :meth:`arm_capture`.
    trigger_rising : bool
        Edge polarity when ``trigger=True``. ``True`` = rising (default),
        ``False`` = falling.

    Returns
    -------
    CaptureResult
        Frozen dataclass. Key access patterns:

        - ``result.trace(channel)`` → list of values in the requested unit
        - ``result.status(channel)`` → ``CaptureChannelStatus`` with
          ``any_clipped``, ``over_range_samples``, ``under_range_samples``,
          ``clipped_samples``, ``peak_signal_v``
        - ``result.enabled_channels`` → tuple of captured channel indices
        - ``result.unit``, ``result.sample_rate_hz``, ``result.wavelength_nm``

    Raises
    ------
    ValueError
        ``frames <= 0`` or ``channels`` contains an invalid index.
    coreDAQTimeoutError
        :meth:`wait_until_complete` exceeded ``timeout_s``.

    Notes
    -----
    For LINEAR frontends, the active zero offset is subtracted from every
    ADC code before unit conversion. For LOG frontends, raw codes are used.
    Autoranging is not applied during captures; the range set before calling
    ``capture()`` is held for the entire acquisition.

    Examples
    --------
    Immediate capture, all channels, watts:

    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     result = pm.capture(frames=1000)
    ...     trace = result.trace(0)
    ...     print(f"Peak: {result.status(0).peak_signal_v:.3f} V")

    External trigger, channels 0 and 2, falling edge:

    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     result = pm.capture(
    ...         frames=2048,
    ...         unit="mv",
    ...         channels=[0, 2],
    ...         trigger=True,
    ...         trigger_rising=False,
    ...     )
    ...     print(result.enabled_channels)  # (0, 2)
    """
```

#### `coreDAQ.zero_dark`

```python
def zero_dark(
    self,
    frames: int = 32,
    settle_s: float = 0.2,
) -> Tuple[int, int, int, int]:
    """Capture a dark baseline and apply it as the active zero offset.

    Block all input light before calling (e.g. cap the fiber ends), then
    call this method. The captured per-channel average ADC code is stored
    as the new zero. All subsequent ``read_*`` and ``capture`` calls
    subtract this offset from every ADC code before unit conversion.

    LINEAR frontends only. Calling on a LOG frontend raises
    ``coreDAQUnsupportedError``.

    Parameters
    ----------
    frames : int
        Number of ADC samples to average for the baseline measurement.
        More frames reduce noise in the stored offset.
    settle_s : float
        Wait time in seconds before sampling, to allow any transient
        settling after input is blocked.

    Returns
    -------
    tuple of int
        Active zero offsets in ADC counts for channels 0, 1, 2, 3.

    Raises
    ------
    coreDAQUnsupportedError
        Called on a LOG frontend.

    See Also
    --------
    restore_factory_zero : Revert to the factory-stored zero.
    zero_offsets_adc : Inspect the current active zero without changing it.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True, sim_frontend="LINEAR") as pm:
    ...     pm.zero_dark()        # block input first
    ...     print(pm.read_channel(0))   # zero-corrected watts
    """
```

#### `coreDAQ.set_range`

```python
def set_range(self, channel: int, range_index: int) -> None:
    """Set the TIA gain range for one channel.

    LINEAR frontends only. The ADC7606 TIA has 8 switchable resistor ranges
    covering 500 nW to 5 mW full scale. Lower index = lower gain = higher
    power full scale.

    Parameters
    ----------
    channel : int
        Channel index 0..3.
    range_index : int
        Range index 0..7. See :meth:`supported_ranges` for full-scale power
        per index.

    Raises
    ------
    coreDAQUnsupportedError
        Called on a LOG frontend (LOG variants have no gain control).
    ValueError
        ``range_index`` outside 0..7, or ``channel`` outside 0..3.

    See Also
    --------
    set_range_power : Select range by target optical power level.
    supported_ranges : List all ranges with labels and full-scale powers.
    autoRange parameter on read_channel : Let the driver pick the range.

    Examples
    --------
    >>> with coreDAQ.connect(
    ...     simulator=True, sim_frontend="LINEAR"
    ... ) as pm:
    ...     pm.set_range(0, 3)   # 100 uW full scale on channel 0
    ...     print(pm.read_channel(0, autoRange=False))
    """
```

#### `coreDAQ.signal_status`

```python
def signal_status(
    self,
    channel: Optional[int] = None,
) -> Union[SignalStatus, List[SignalStatus]]:
    """Return signal health for one or all channels.

    A channel is *over-range* when ``abs(signal_v) > 4.2 V``.
    A channel is *under-range* when ``abs(signal_mv) < 5.0 mV``.
    A channel is *clipped* when either threshold is violated.

    Parameters
    ----------
    channel : int or None
        Channel index 0..3, or ``None`` for all four channels.

    Returns
    -------
    SignalStatus or list of SignalStatus
        A single ``SignalStatus`` if ``channel`` is specified, or a list of
        four in channel order 0..3 if ``channel`` is ``None``.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     for s in pm.signal_status():
    ...         print(s.channel, "clipped:", s.is_clipped)
    """
```

#### `coreDAQ.set_wavelength_nm`

```python
def set_wavelength_nm(self, wavelength_nm: float) -> None:
    """Set the operating wavelength for power conversion.

    Wavelength affects power-to-watt conversion for all variants:
    - Si variants: responsivity ``R(λ)`` enters the TIA or log-amp equation
      directly, so power readings change with wavelength.
    - InGaAs LINEAR: empirical calibration is at a reference wavelength;
      ``set_wavelength_nm`` applies a responsivity-ratio correction.
    - InGaAs LOG: the LUT is reference-wavelength-encoded; wavelength shifts
      the output via a responsivity ratio.

    Parameters
    ----------
    wavelength_nm : float
        Wavelength in nanometers. Must be within the range supported by the
        connected detector:

        - InGaAs: 910–1700 nm
        - Silicon: 400–1100 nm

        Call :meth:`wavelength_limits_nm` to retrieve the exact limits.

    Raises
    ------
    ValueError
        Wavelength is outside the detector's valid range.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     pm.set_wavelength_nm(1310.0)
    ...     print(pm.read_channel(0))

    Si variant (wavelength must stay within 400–1100 nm):

    >>> with coreDAQ.connect(
    ...     simulator=True, sim_detector="SILICON", sim_frontend="LOG"
    ... ) as pm:
    ...     pm.set_wavelength_nm(850.0)
    ...     print(pm.read_channel(0))
    """
```

#### `coreDAQ.set_capture_channel_mask`

```python
def set_capture_channel_mask(self, mask: Union[int, str]) -> int:
    """Set the active capture channel mask.

    Parameters
    ----------
    mask : int or str
        Integer mask (bits 0..3 select channels 0..3), or a string in
        any of these formats: ``5``, ``"0x5"``, ``"0b0101"``,
        ``"0000 0101"``. At least one bit must be set.

    Returns
    -------
    int
        The applied mask as an integer.

    Notes
    -----
    The capture mask only affects :meth:`capture`, :meth:`capture_channel`,
    and the manual acquisition methods. :meth:`read_all` and all range
    methods always operate on all four channels regardless of this mask.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     pm.set_capture_channel_mask("0000 0101")  # channels 0 and 2
    ...     result = pm.capture(frames=512)
    ...     print(result.enabled_channels)  # (0, 2)
    """
```

### 2.3 `ChannelProxy` (new class)

```python
class ChannelProxy:
    """A channel-scoped view into a coreDAQ device.

    Do not instantiate directly. Access via ``meter.channels[n]``.

    All methods delegate to the parent ``coreDAQ`` instance with the
    channel index pre-filled. There is no independent state.

    Examples
    --------
    >>> with coreDAQ.connect(simulator=True) as pm:
    ...     ch = pm.channels[0]
    ...     print(ch.power_w)         # live read in watts
    ...     print(ch.read(unit="dbm"))
    """

    @property
    def power_w(self) -> float:
        """Live optical power reading in watts.

        Triggers one ADC read with ``autoRange=True``. Equivalent to
        ``meter.read_channel(n, unit="w")``.
        """

    def read(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> Union[float, int]:
        """Read optical power in the requested unit.

        Parameters
        ----------
        unit : str or None
            ``"w"``, ``"dbm"``, ``"v"``, ``"mv"``, or ``"adc"``.
        autoRange : bool
            Autorange this channel before reading (LINEAR only).
        n_samples : int
            Snapshots to average, 1..32.

        Returns
        -------
        float or int
            Power in the requested unit.
        """

    def read_full(
        self,
        unit: Optional[str] = None,
        autoRange: bool = True,
        n_samples: int = 1,
    ) -> ChannelReading:
        """Read with full measurement metadata.

        Returns
        -------
        ChannelReading
            Frozen dataclass with all fields populated.
        """

    @property
    def range(self) -> Optional[int]:
        """Current TIA gain range index (0..7).

        Returns ``None`` on LOG frontends, which have no gain control.
        """

    def set_range(self, range_index: int) -> None:
        """Set the TIA gain range.

        LINEAR frontends only. Raises ``coreDAQUnsupportedError`` on LOG.
        See :meth:`coreDAQ.set_range`.
        """

    def set_range_power(self, power_w: float) -> int:
        """Select the best range for a target optical power level.

        LINEAR frontends only. Raises ``coreDAQUnsupportedError`` on LOG.

        Returns
        -------
        int
            The selected range index.
        """

    def signal_status(self) -> SignalStatus:
        """Return signal health for this channel."""

    def is_clipped(self) -> bool:
        """Return ``True`` if this channel is over-range or under-range."""
```

### 2.4 Property vs method decisions

**Properties** (cheap, no parameters, semantically noun-like):
- `ChannelProxy.power_w` — the "give me the number" one-liner for REPL use; the I/O is obvious from the name
- `ChannelProxy.range` — reads the last-known gain state (same I/O as `get_range(n)`)

**Methods** (everything else on `coreDAQ`): Any call that takes parameters or touches the device stays a method. `pm.wavelength_nm()` as a method is correct — properties on the device object would hide I/O.

### 2.5 Unit handling

Unit tokens (`w`, `dbm`, `v`, `mv`, `adc`) are kept as-is. `_normalize_unit()` (line 1959) already handles aliases (`"watt"`, `"volts"`, `"raw"`, etc.). No enum is required; plain strings continue to work.

Global `reading_unit` default is `"w"`. Per-call `unit=` overrides it for that call only, without changing the global.

### 2.6 Context-manager lifecycle

```python
with coreDAQ.connect() as pm:
    ...
# serial port closed on __exit__
```

`connect()` is the canonical entry point. `coreDAQ(port)` remains valid (useful for frameworks that manage lifecycle externally). Both support `with`. No `__del__` — close must be explicit.

### 2.7 Variant exposure

All four variants use the same `coreDAQ` class. Variant identity is surfaced via read-only methods:

```python
pm.frontend()  # "LINEAR" or "LOG"
pm.detector()  # "INGAAS" or "SILICON"
pm.device_info().gain_profile  # "standard" or "linear_legacy"
pm.wavelength_limits_nm()  # (910.0, 1700.0) for InGaAs, (400.0, 1100.0) for Si
```

Methods that are variant-specific raise `coreDAQUnsupportedError` when called on the wrong variant:

| Method | Constraint |
|---|---|
| `set_range`, `get_range`, `set_ranges`, `set_range_power`, `set_range_powers` | LINEAR only |
| `zero_dark`, `restore_factory_zero` | LINEAR only |
| `zero_offsets_adc`, `factory_zero_offsets_adc` | LINEAR only |
| `set_wavelength_nm` | Validates against detector's wavelength range |

Docstrings for every constrained method call out which variants are supported.

### 2.8 Exception hierarchy

```
coreDAQError(Exception)            # unchanged public base exception
    coreDAQConnectionError         # port not found, IDN? failure, serial open failure
    coreDAQTimeoutError            # wait_until_complete exceeded, busy-retry exhausted
    coreDAQCalibrationError        # malformed calibration data from device
    coreDAQUnsupportedError        # feature not available on this variant
```

The private `CoreDAQError` (line 23) is translated into `coreDAQError` at the `coreDAQ._call()` boundary (line 1943). Subclass selection in `_call()` is based on the exception message pattern or a dedicated `kind` attribute on `CoreDAQError`.

User-actionable guidance belongs in the exception message. Example: `coreDAQConnectionError("No coreDAQ device found on any serial port. Check the USB-C cable and, on Linux, verify that the udev rule is installed.")`.

### 2.9 Naming change log

| Old name | Status | Notes |
|---|---|---|
| `coreDAQError` | **Unchanged** | Established in codebase and docs; no rename |
| `autoRange` parameter | **Unchanged** | Established API surface; no rename |
| `get_data(...)` | Deprecated → use `capture(...)` | `capture()` already exists as an alias; declare it canonical |
| `get_data_channel(...)` | Deprecated → use `capture_channel(...)` | Same rationale |
| `read_all_details(...)` | Deprecated → use `read_all_full(...)` | `_full` is more descriptive |
| `read_channel_details(...)` | Deprecated → use `read_channel_full(...)` | Same |
| `get_range_all()` | Deprecated → use `get_ranges()` | Redundant alias |
| `set_power_range(ch, idx)` | Deprecated → use `set_range(ch, idx)` | Redundant alias |
| `current_ranges()` | Deprecated → use `get_ranges()` | Redundant alias |
| `enabled_channels()` | Deprecated → use `capture_channels()` | `capture_` prefix makes scope clear |
| `set_enabled_channels(chs)` | Deprecated → use `set_capture_channels(chs)` | Same |

---

## 3. Internal Architecture

### 3.1 Layered design

```
┌──────────────────────────────────────────────────────────┐
│  Public API  (coreDAQ, ChannelProxy)                     │
│  py_coreDAQ.py lines 1907–2819 (today)                  │
├──────────────────────────────────────────────────────────┤
│  Calibration Layer                                       │
│  CalibrationStrategy — one implementation per variant    │
│  InGaAsLinearCal, InGaAsLogCal, SiLogCal, SiLinearCal   │
├──────────────────────────────────────────────────────────┤
│  Device Protocol  (_DeviceProtocol)                      │
│  maps firmware command strings to Python return values   │
│  reads raw ADC codes; applies zeroing; knows nothing     │
│  about watts or wavelength                               │
├──────────────────────────────────────────────────────────┤
│  Transport  (abstract)                                   │
│  ask(cmd) → (status, payload)                            │
│  SerialTransport  (real device, pyserial)                │
│  SimTransport     (simulator, no hardware)               │
└──────────────────────────────────────────────────────────┘
```

- **Transport** knows about bytes. Holds `serial.Serial` or simulator state. Nothing above this layer imports `serial`.
- **Device Protocol** knows firmware command strings (`IDN?`, `SNAP_N?`, `GAIN_SET`, `ACQ_ARM`, etc.). Converts raw bytes to Python scalars. Does not know about watts.
- **Calibration** converts zeroed ADC codes to watts for each variant. Does not know about firmware commands.
- **Public API** orchestrates Protocol + Calibration calls; exposes units, autoranging, zeroing, and channel masks.

### 3.2 Calibration strategy pattern

```python
class CalibrationStrategy(Protocol):
    frontend: str   # "LINEAR" or "LOG"
    detector: str   # "INGAAS" or "SILICON"
    def load(self, protocol: "_DeviceProtocol") -> None: ...
    def adc_to_power_w(
        self,
        channel: int,
        gain: Optional[int],
        adc_code_zeroed: int,
        wavelength_nm: float,
    ) -> float: ...
```

Four concrete implementations:
- `InGaAsLinearCal` — slope/intercept table per `(channel, gain)`
- `InGaAsLogCal` — LUT interpolation; per-head LUT variant supported
- `SiLogCal` — analytical log-amp equation + responsivity curve
- `SiLinearCal` — analytical TIA equation + responsivity curve

Registry at construction time:

```python
_STRATEGY_REGISTRY: Dict[Tuple[str, str], Type[CalibrationStrategy]] = {
    ("LINEAR", "INGAAS"): InGaAsLinearCal,
    ("LOG",    "INGAAS"): InGaAsLogCal,
    ("LOG",    "SILICON"): SiLogCal,
    ("LINEAR", "SILICON"): SiLinearCal,
}
```

A registry is preferred over subclassing `_CoreDAQDriver` because:
1. Variant selection happens at runtime (after `HEAD_TYPE?` and `IDN?`).
2. All four variants share the same transport and protocol; only the math differs.
3. Adding a future variant is one new class + one registry entry.

### 3.3 Acquisition modes

`capture()` is the single user-facing entry point. Internally:

1. Temporarily override capture mask if `channels` is specified.
2. `protocol.arm_acquisition(frames, trigger, trigger_rising)`.
3. If `trigger=False`: `protocol.start_acquisition()`.
4. `protocol.wait_for_completion(poll_s, timeout_s)` → raises `coreDAQTimeoutError` on expiry.
5. `protocol.transfer_frames_adc(frames)` → list of per-channel ADC code lists.
6. Apply zeroing (LINEAR) or raw pass-through (LOG), then `calibration.adc_to_power_w()` per sample.
7. Restore original mask in a `finally` block.

Oversampling and sample rate are global device settings configured separately. They are not per-capture arguments.

### 3.4 Threading and async story

`capture()` is **blocking**. The lock in `Transport.ask()` serializes all firmware I/O. This is the correct model for a bench instrument used one operation at a time.

For Jupyter: blocking calls release the GIL during serial I/O, so the notebook UI remains responsive. No `await` needed.

Streaming/async is out of scope for this redesign. The `Transport` ABC is designed so an async subclass is possible later (making `ask()` a coroutine does not require rewriting the protocol layer).

### 3.5 Configuration state model

| State | Where it lives | Re-read on reconnect? |
|---|---|---|
| `_reading_unit` | Python object | No — user preference |
| `_zero_source` | Python object | No — user action |
| `_linear_zero_adc` | Python object (loaded at init) | Yes — re-read from device |
| `_wavelength_nm` | Python object | No — user preference |
| `_frontend_type`, `_detector_type` | Python object (cached at init) | Yes — re-detect |
| TIA gain indices | Device SRAM (firmware holds) | Yes — re-read |
| Capture channel mask | Device SRAM | Yes — re-read |
| Sample rate, oversampling | Device SRAM | Yes — re-read |
| Calibration tables, LUTs | Python object (loaded at init) | Yes — reload |

On disconnect/reconnect, create a new `coreDAQ` instance. `refresh_device_state()` re-reads I2C registers (temperature, humidity) only — it does not reload calibration tables.

---

## 4. Simulator Design

### 4.1 Entry point

```python
# Default: InGaAs LOG (most popular SKU)
with coreDAQ.connect(simulator=True) as pm:
    print(pm.read_channel(0))   # works; no hardware needed

# Other variants via keyword arguments
with coreDAQ.connect(simulator=True, sim_frontend="LINEAR", sim_detector="INGAAS") as pm:
    pm.set_range(0, 3)
    print(pm.read_channel(0, autoRange=False))

with coreDAQ.connect(simulator=True, sim_frontend="LOG", sim_detector="SILICON") as pm:
    pm.set_wavelength_nm(850.0)
    print(pm.read_channel(0))
```

`connect(simulator=True)` constructs a `coreDAQ` backed by `SimTransport` instead of `SerialTransport`. The returned type is identical (`coreDAQ`) — no separate class, no special-case branches in user code.

**Rationale for `connect(simulator=True)` over a separate class**: one entry point means one type in type annotations, no "am I using the simulator?" questions at runtime, and every doc example changes from `coreDAQ("/dev/tty...")` to `coreDAQ.connect(simulator=True)` with no other code changes.

### 4.2 Per-variant simulation

`SimTransport` is constructed with `frontend` and `detector` parameters; `connect()` passes its `sim_*` arguments through:

```python
class SimTransport(Transport):
    def __init__(
        self,
        frontend: str = "LOG",
        detector: str = "INGAAS",
        incident_power_w: float = 1e-4,
        wavelength_nm: float = 1550.0,
        dark_current_a: float = 5e-9,
        noise_sigma_adc: float = 2.0,
        seed: Optional[int] = 42,
    ) -> None: ...
```

| Feature | Simulation approach |
|---|---|
| Dark current + shot noise | ADC code = `signal_code + N(0, noise_sigma_adc)` |
| TIA gain ranging (LINEAR) | 8 internal resistor values; `set_gain` updates `_gains[]`; next `snapshot_adc` returns codes consistent with the configured `incident_power_w` at that gain |
| Autoranging (LINEAR) | Simulated gain set/get tracks `_gains[]`; the public `autoRange=True` logic in `coreDAQ` drives it exactly as with real hardware |
| Log compression (LOG) | Apply log-amp equation forward: `V = Vy * log10(P * R(λ) / Iz)`; then add noise |
| Wavelength responsivity (Si) | Interpolate `_BUILTIN_RESPONSIVITY_CURVES` (same data as real device) |
| Wavelength limits | `set_wavelength_nm` raises `ValueError` outside detector range (910–1700 nm for InGaAs, 400–1100 nm for Si) |
| Clipping | ADC code clamped to ±32767; `over_range` and `under_range` flags computed from the same thresholds as the real driver |
| External trigger | `SimTransport.inject_trigger()` fires the software trigger; `arm_acquisition(trigger=True)` blocks until `inject_trigger()` is called or timeout |
| Oversampling effect | Noise σ divided by `sqrt(oversampling_factor)` |
| Variant-specific guard | `set_gain` on a LOG simulator raises `coreDAQUnsupportedError`; `zero_dark` on a LOG simulator raises `coreDAQUnsupportedError` — same behavior as real LOG device |

### 4.3 Deterministic examples

The default `sim_seed=42` makes all doc examples produce the same output on every run. Recipe examples can override:

```python
with coreDAQ.connect(
    simulator=True,
    sim_incident_power_w=5e-4,
    sim_noise_sigma_adc=5.0,
    sim_seed=None,   # stochastic
) as pm:
    ...
```

### 4.4 Test strategy

The same `pytest` parameterization runs every test against both the simulator and optionally real hardware:

```python
@pytest.fixture(params=["simulator", "hardware"])
def meter(request):
    if request.param == "simulator":
        return coreDAQ.connect(simulator=True)
    port = os.environ.get("COREDAQ_HARDWARE_PORT")
    if port is None:
        pytest.skip("COREDAQ_HARDWARE_PORT not set")
    return coreDAQ.connect(port)
```

All four variant paths are covered by separate parameterized fixtures:

```python
@pytest.fixture(params=[
    {"sim_frontend": "LOG",    "sim_detector": "INGAAS"},
    {"sim_frontend": "LINEAR", "sim_detector": "INGAAS"},
    {"sim_frontend": "LOG",    "sim_detector": "SILICON"},
    {"sim_frontend": "LINEAR", "sim_detector": "SILICON"},
])
def meter_all_variants(request):
    return coreDAQ.connect(simulator=True, **request.param)
```

Tests that are variant-agnostic (e.g., `read_channel` returns a finite float) run against all four. Tests that are variant-specific (e.g., `set_range` raises on LOG) are marked with `@pytest.mark.linear_only` or similar.

### 4.5 Simulator location

`SimTransport` lives in `py_coredaq/sim.py`. Exported at package level only via `coreDAQ.connect(simulator=True)`. Direct import for advanced use:

```python
from py_coredaq.sim import SimTransport   # configure noise parameters manually
from py_coredaq.testing import SimTransport   # alias for test authors
```

---

## 5. Migration Strategy

### 5.1 Complete name map

| Old name | New name | Change type |
|---|---|---|
| `coreDAQError` | **Unchanged** | — |
| `autoRange` parameter | **Unchanged** | — |
| `coreDAQ(port)` constructor | Still works; `coreDAQ.connect(port)` is additive | Additive |
| `get_data(...)` | Deprecated → `capture(...)` | Deprecation shim, 1 minor version |
| `get_data_channel(...)` | Deprecated → `capture_channel(...)` | Deprecation shim |
| `read_all_details(...)` | Deprecated → `read_all_full(...)` | Deprecation shim |
| `read_channel_details(...)` | Deprecated → `read_channel_full(...)` | Deprecation shim |
| `get_range_all()` | Deprecated → `get_ranges()` | Deprecation shim |
| `set_power_range(ch, idx)` | Deprecated → `set_range(ch, idx)` | Deprecation shim |
| `current_ranges()` | Deprecated → `get_ranges()` | Deprecation shim |
| `enabled_channels()` | Deprecated → `capture_channels()` | Deprecation shim |
| `set_enabled_channels(chs)` | Deprecated → `set_capture_channels(chs)` | Deprecation shim |
| Channel numbering 0..3 | Unchanged | — |
| Unit tokens w/dbm/v/mv/adc | Unchanged | — |
| All dataclass names and fields | Unchanged | — |
| `DeviceInfo.raw_idn` | Unchanged (docs had a typo: called it `identity`) | Doc fix only |

### 5.2 Deprecation policy

Deprecated aliases survive for **one minor version** after the version that declares them deprecated. They emit `DeprecationWarning` with `stacklevel=2` so the warning points at the caller's code, not the library internals.

Shim form:

```python
def get_data(self, *args, **kwargs) -> CaptureResult:
    warnings.warn(
        "get_data() is deprecated; use capture() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return self.capture(*args, **kwargs)
```

### 5.3 What will and will not break

**Will break:** Nothing in the existing name surface. `coreDAQError` and `autoRange` are kept exactly as-is.

**Will not break:**
- Any existing `read_*`, `capture`, `get_data`, `zero_dark`, `set_range*`, or `set_wavelength_nm` calls
- Any `CaptureResult.trace()`, `MeasurementSet.channel()`, or dataclass field accesses
- `coreDAQ(port)` constructor
- `discover()` static method
- Unit tokens, channel numbering, `frames` semantics, `trigger` / `trigger_rising` parameters
- `n_samples` ranges

### 5.4 MIGRATION.md outline

```markdown
# Migrating to py_coreDAQ v0.2

## No breaking changes

coreDAQError and autoRange are unchanged. Existing code that works with
v0.1 will work with v0.2 without modification.

## Deprecated aliases (removed in v0.3)

These names continue to work in v0.2 but emit DeprecationWarning:

| Old name                  | Use instead              |
|---------------------------|--------------------------|
| get_data(...)             | capture(...)             |
| get_data_channel(...)     | capture_channel(...)     |
| read_all_details(...)     | read_all_full(...)       |
| read_channel_details(...) | read_channel_full(...)   |
| get_range_all()           | get_ranges()             |
| set_power_range(ch, idx)  | set_range(ch, idx)       |
| current_ranges()          | get_ranges()             |
| enabled_channels()        | capture_channels()       |
| set_enabled_channels(chs) | set_capture_channels(chs)|

## New features

- coreDAQ.connect() — auto-discovers the device if no port is given
- coreDAQ.connect(simulator=True) — all four variants, no hardware needed
- meter.channels[0].power_w — channel proxy for one-liner reads
- coreDAQConnectionError, coreDAQTimeoutError, coreDAQUnsupportedError,
  coreDAQCalibrationError — catch specific failure modes
```

---

## 6. Implementation Plan

### Phase 0 — Housekeeping (S, ~1 day)

**What changes**: `pyproject.toml` bumps `requires-python` to `>=3.10`; adds `py.typed` marker file; configures `ruff` and `mypy` in `pyproject.toml`.

**Files**: `pyproject.toml`, new `py_coreDAQ/py.typed`.

**Test**: `mypy py_coreDAQ.py` passes at strict; `ruff check .` passes clean.

**Risk**: Existing annotations may use constructs only valid in 3.10+ (`X | Y` union syntax, `match`); audit the existing 2819 lines before bumping. The library's pyproject says `>=3.9`; confirm no user base is pinned to 3.9.

---

### Phase 1 — Transport abstraction (M, ~2 days)

**What changes**: Extract `Transport` ABC with `ask(cmd) -> tuple[str, str]` and a `close()` method. Move `serial.Serial` construction, `_drain`, `_writeln`, `_readline`, `_ask`, `_ask_with_busy_retry`, and the `threading.Lock` into `SerialTransport`. `_CoreDAQDriver.__init__` accepts a `Transport` instance instead of a port string.

**Files**: New `py_coredaq/transport.py`; modify `py_coreDAQ.py` lines 155–165 (serial construction) and lines 264–291 (`_ask` / `_ask_with_busy_retry`).

**Test**: All 11 existing tests pass unchanged. `_BaseFakeDriver` in `test_coredaq_api.py` already implements the `ask()` pattern; it becomes a valid `Transport` implementation.

**Risk**: `_lock` and `_inter_command_gap_s` must move with the transport. The `_drain()` call during init (line 165) must happen inside `SerialTransport`, not after construction.

---

### Phase 2 — Simulator (M, ~2 days)

**What changes**: Implement `SimTransport(Transport)` in `py_coredaq/sim.py` with `frontend`, `detector`, and noise parameters. Wire `coreDAQ.connect(simulator=True, sim_frontend, sim_detector, ...)` to construct `_CoreDAQDriver(SimTransport(...))`. Add `inject_trigger()` method on `SimTransport` for external-trigger test scenarios.

**Files**: New `py_coredaq/sim.py`; modify `coreDAQ.connect()` classmethod.

**Test**: Add smoke test: `connect(simulator=True)` → `read_channel(0)` returns a finite float. Add variant smoke test for all four `(frontend, detector)` combinations. Verify `zero_dark()` raises `coreDAQUnsupportedError` on LOG simulator. Verify `set_wavelength_nm(1600)` raises `ValueError` on Si simulator.

**Risk**: `SimTransport` must respond correctly to every firmware command queried at `_CoreDAQDriver.__init__`: `HEAD_TYPE?`, `IDN?`, `CAL_SLOPE?`, `CAL_INTERCEPT?`, `LOGLUT?`, `FACTORY_ZEROS?`, `I2C_STATE?`. Map all init-time commands before implementing.

---

### Phase 3 — Exception hierarchy (S, ~0.5 day)

**What changes**: Add `coreDAQConnectionError`, `coreDAQTimeoutError`, `coreDAQCalibrationError`, `coreDAQUnsupportedError` as subclasses of `coreDAQError` in the module. Update `_call()` to select the appropriate subclass based on the nature of the failure. Add `coreDAQUnsupportedError` raises in `set_range`, `zero_dark`, `restore_factory_zero` when called on a LOG frontend.

**Files**: `py_coreDAQ.py` (exception definitions near line 1793; `_call()` at line 1943; all LOG-guarded methods).

**Test**: `except coreDAQError` still catches all subclasses. `set_range()` on a LOG `coreDAQ` raises `coreDAQUnsupportedError`. `wait_until_complete(timeout_s=0)` raises `coreDAQTimeoutError`.

**Risk**: The `_call()` wrapper at line 1943 currently translates all `CoreDAQError` to `coreDAQError`. Subclass routing needs to be done here without breaking the catch-all case.

---

### Phase 4 — Calibration strategy extraction (L, ~3 days)

**What changes**: Extract `InGaAsLinearCal`, `InGaAsLogCal`, `SiLogCal`, `SiLinearCal` from `_CoreDAQDriver` into `py_coredaq/calibration.py`. Each strategy implements `CalibrationStrategy`. `_CoreDAQDriver` selects and loads the correct strategy at init via `_STRATEGY_REGISTRY`. All `_convert_*` methods (line ~1400–1600) move into strategies. The `_loglut_*`, `_cal_slope`, `_cal_intercept`, `_silicon_*` state moves into strategy instances.

**Files**: New `py_coredaq/calibration.py`; major refactor of `_CoreDAQDriver` lines 177–227 (init), ~400–1600 (calibration internals), and all `_convert_*` calls in `coreDAQ`.

**Test**: Existing 11 tests pass (they use mock drivers that bypass calibration). Add per-strategy unit tests: `InGaAsLogCal.adc_to_power_w(ch=0, gain=None, code=X, wavelength_nm=1550)` matches a known LUT lookup result. `SiLogCal` with a known V → P mapping. `InGaAsLinearCal` with known slope/intercept.

**Risk**: This is the most complex phase. The LUT, slope/intercept tables, silicon model, and responsivity curves are all tightly coupled to `_CoreDAQDriver` internal state. The strategy `load(protocol)` method must populate its own tables by querying the protocol layer — it cannot share mutable state with the driver.

---

### Phase 5 — `coreDAQ.connect()` and `ChannelProxy` (M, ~2 days)

**What changes**: Add `coreDAQ.connect(port, simulator, sim_*, ...)` classmethod. Add `ChannelProxy` class. Add `meter.channels` property returning `[ChannelProxy(self, ch) for ch in range(4)]`.

**Files**: `py_coreDAQ.py` (or split into `py_coredaq/device.py` if package refactor is underway); new `py_coredaq/channel.py`.

**Test**: `connect()` with no args and no discoverable device raises `coreDAQConnectionError`. `connect(simulator=True)` returns a `coreDAQ`. `pm.channels[0].power_w` returns a finite float. `pm.channels[0].set_range(3)` on a LINEAR simulator updates the range. `pm.channels[0].set_range(3)` on a LOG simulator raises `coreDAQUnsupportedError`.

**Risk**: Auto-discovery behavior when multiple devices are present must be defined precisely: raise `coreDAQConnectionError` with a message listing all found ports. The user must then pass a specific `port=`.

---

### Phase 6 — Deprecation shims (S, ~0.5 day)

**What changes**: Add `DeprecationWarning` wrappers for all nine deprecated aliases (`get_data`, `get_data_channel`, `read_all_details`, `read_channel_details`, `get_range_all`, `set_power_range`, `current_ranges`, `enabled_channels`, `set_enabled_channels`).

**Files**: `py_coreDAQ.py` (or `py_coredaq/device.py`).

**Test**: Each deprecated alias triggers `DeprecationWarning` when called inside `warnings.catch_warnings(record=True)`. Each still returns the correct value.

**Risk**: None — additive only.

---

### Phase 7 — Test suite expansion (M, ~2 days)

**What changes**: Migrate 11 existing `unittest` tests to `pytest`. Add parameterized fixture for simulator (all 4 variants) and optional hardware. Add tests for: Si log/linear calibration paths, wavelength compensation (all variants), all deprecated aliases emit `DeprecationWarning`, `connect()` classmethod, `ChannelProxy` all methods, all four exception subclasses, `SimTransport.inject_trigger()`, `max_capture_frames()`, `device_info()` field integrity, `supported_ranges()` shape.

**Files**: `tests/test_*.py` (split by concern: transport, calibration, api, simulator, integration).

**Test**: `pytest tests/ -x` passes with 0 warnings (errors). `COREDAQ_HARDWARE_PORT=/dev/... pytest tests/ -m hardware` runs hardware-gated tests.

**Risk**: Hardware tests are fragile. Gate them strictly with `pytest.mark.hardware` and the env var fixture; CI should run simulator-only by default.

---

### Phase 8 — Docstrings, examples, and Sphinx (M, ~2 days)

**What changes**: Numpydoc docstrings on every public symbol using the templates in §2.2. Add `examples/` folder with runnable scripts (one per major feature, all use `connect(simulator=True)`). Configure Sphinx with `sphinx.ext.napoleon`, `myst_parser`, and `sphinx.ext.doctest`. Update MkDocs nav to include the new API sections.

**Files**: All public class and method docstrings; `docs/conf.py`; `examples/*.py`; `mkdocs.yml`.

**Test**: `sphinx-build docs/ docs/_build/html -W` passes (warnings as errors). `python -m doctest examples/quickstart.py` passes. `python examples/quickstart.py` produces output without hardware.

**Risk**: Doctest in Sphinx requires the simulator to produce consistent output — rely on `sim_seed=42` default. Any example that prints a floating-point value must use `round()` or `# doctest: +ELLIPSIS` to avoid brittle float formatting.

---

## 7. Open Questions

These require product-owner input before implementation begins.

**1. Should `capture()` return numpy arrays by default?**
NumPy is already a required dependency. `CaptureResult.traces` is currently `dict[int, list]`. Returning `np.ndarray` would be more efficient for downstream analysis but would be a breaking change for code doing `result.trace(0)[0]` (both work, but `np.ndarray` is a different type). Recommendation: add a `numpy=True` parameter to `capture()` that wraps each trace in `np.asarray()`; keep the default as `list`. Flip the default in v0.3.

**2. Should `set_sample_rate_hz()` validate against a list of supported rates?**
The firmware accepts an integer Hz value but quantizes it to a supported subset (e.g., 500, 1000, 2000, 5000, 10000, 100000). Passing 999 may silently produce 1000 Hz. Recommendation: add `supported_sample_rates()` returning the validated list; raise `ValueError` if the passed rate is not in the list. This is a tightening but not a breaking change.

**3. Should `zero_dark()` raise on LOG frontends, or silently no-op?**
Raising `coreDAQUnsupportedError` is more honest and catches user mistakes early. A no-op is convenient for scripts that call `zero_dark()` unconditionally and check `frontend()` separately. Recommendation: raise; the error message should say "zero_dark() is not supported on LOG frontends. LOG variants do not apply host-side zero subtraction." Confirm with the team that no existing scripts rely on the no-op behavior.

**4. Synchronous-only, or also expose an async API for high-rate streaming?**
The blocking model is correct for bench use. High-rate streaming (100 kHz live plot in a Jupyter widget) would benefit from `AsyncCoreDAQ`. Recommendation: out of scope for this redesign; design `Transport.ask()` as a plain method now, but document that an `AsyncTransport` subclass is possible later.

**5. Multi-device support?**
A rack of 4 × 4-channel coreDAQ units is 16 channels. The current API has no concept of device grouping or synchronized capture across units. Recommendation: out of scope; `[coreDAQ.connect(p) for p in discover()]` is the multi-device pattern today. Do not add a grouping class in this redesign.

**6. `DeviceInfo.raw_idn` vs `identity`?**
The existing docs (`docs/api-reference.md`) refer to this field as `identity`, but the actual dataclass at line 1799 has `raw_idn`. Recommendation: fix the docs to say `raw_idn`; add a `@property identity` that returns `raw_idn` with a deprecation note. Confirm whether any user scripts access `device_info().identity`.

**7. Python version floor: 3.9 or 3.10?**
`pyproject.toml` currently says `>=3.9`. The `X | Y` union syntax and some `match` patterns require 3.10. Python 3.9 reaches end-of-life October 2025. Recommendation: bump to `>=3.10`. Confirm no users are pinned to 3.9 before proceeding.

**8. Should `capture()` support per-channel range locking?**
Currently, the range in effect when `capture()` is called is used for the entire acquisition — there is no per-sample autoranging during a capture. An engineer running a long trace at an unknown power level might want the device to autorange before arming. Recommendation: add an `autorange_before_capture: bool = True` parameter that, if `True`, does one `read_all()` with `autoRange=True` before `arm_capture()`. Confirm whether the settling time this adds is acceptable.
