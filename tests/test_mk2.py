"""py_coreDAQ mk2 (F746, 5-channel, USB+Ethernet) tests.

Covers mk2 detection, 5-channel straight-binary capture, the mk2 command
surface (tier/sensors/UID/sysstat/IP config/Ethernet status), and the
EthernetTransport wired through connect(transport="ethernet") against an
in-process mock socket. No hardware is touched.

mk1 behaviour is verified in test_coredaq_api.py and must remain unchanged.
"""
import socket
import struct

import numpy as np
import pytest

from py_coreDAQ import (
    CaptureResult,
    coreDAQ,
    coreDAQConnectionError,
    coreDAQError,
    coreDAQUnsupportedError,
)
from py_coreDAQ._ethernet import EthernetTransport
from py_coreDAQ._simulator import SimTransport


# ---------------------------------------------------------------------------
# Mock sockets
# ---------------------------------------------------------------------------


class _CannedSocket:
    """Minimal socket that replies to XFER with pre-baked bytes.

    Used to test EthernetTransport's line reassembly + binary deinterleave in
    isolation, with byte-exact control over the wire content.
    """

    def __init__(self, payload: bytes, header: bytes = b"OK START XFER\r\n") -> None:
        self._out = bytearray()
        self._payload = payload
        self._header = header
        self._blocking = True

    def setsockopt(self, *a) -> None:
        pass

    def settimeout(self, t) -> None:
        self._timeout = t

    def gettimeout(self):
        return getattr(self, "_timeout", None)

    def setblocking(self, b) -> None:
        self._blocking = bool(b)

    def sendall(self, data: bytes) -> None:
        # Any command -> serve the header line then the canned binary payload.
        self._out.extend(self._header)
        self._out.extend(self._payload)

    def recv(self, bufsize: int) -> bytes:
        if not self._out:
            if not self._blocking:
                raise BlockingIOError()
            raise socket.timeout()
        n = min(bufsize, len(self._out))
        chunk = bytes(self._out[:n])
        del self._out[:n]
        return chunk

    def close(self) -> None:
        pass


class _SimSocket:
    """Socket that proxies the line protocol to a mk2 ``SimTransport``.

    Line commands go through ``sim.ask``; the two binary streams (XFER,
    LOGCAL) are synthesised on the wire exactly as the firmware would frame
    them, so a full connect()->capture() round-trip runs over EthernetTransport.
    """

    def __init__(self, sim: SimTransport) -> None:
        self.sim = sim
        self._out = bytearray()
        self._in = bytearray()
        self._blocking = True

    # -- socket surface ------------------------------------------------
    def setsockopt(self, *a) -> None:
        pass

    def settimeout(self, t) -> None:
        self._timeout = t

    def gettimeout(self):
        return getattr(self, "_timeout", None)

    def setblocking(self, b) -> None:
        self._blocking = bool(b)

    def sendall(self, data: bytes) -> None:
        self._in.extend(data)
        while b"\n" in self._in:
            line, _, rest = self._in.partition(b"\n")
            self._in = bytearray(rest)
            self._handle(line.decode("ascii", "ignore").strip())

    def recv(self, bufsize: int) -> bytes:
        if not self._out:
            if not self._blocking:
                raise BlockingIOError()
            raise socket.timeout()
        n = min(bufsize, len(self._out))
        chunk = bytes(self._out[:n])
        del self._out[:n]
        return chunk

    def close(self) -> None:
        pass

    # -- protocol ------------------------------------------------------
    def _emit_reply(self, st: str, p: str) -> None:
        if st == "OK":
            line = f"OK {p}" if p else "OK"
        elif st == "ERR":
            line = f"ERR {p}" if p else "ERR"
        elif st == "BUSY":
            line = "BUSY"
        else:
            line = p
        self._out.extend((line + "\r\n").encode("ascii", "ignore"))

    def _handle(self, cmd: str) -> None:
        up = cmd.upper()
        if up.startswith("XFER "):
            self._handle_xfer(cmd)
        elif up.startswith("LOGCAL"):
            self._handle_logcal(cmd)
        else:
            st, p = self.sim.ask(cmd)
            self._emit_reply(st, p)

    def _handle_xfer(self, cmd: str) -> None:
        nbytes = int(cmd.split()[1])
        mask = self.sim._mask
        active = [i for i in range(self.sim._n_channels) if (mask >> i) & 1]
        frame_bytes = len(active) * 2
        frames = nbytes // frame_bytes if frame_bytes else 0
        codes = [self.sim._power_to_adc(self.sim._incident_power_w, ch) for ch in active]
        fmt = "<" + ("H" if self.sim._unsigned else "h") * len(active)
        frame = struct.pack(fmt, *codes)
        self._out.extend(b"OK START XFER\r\n")
        self._out.extend(frame * frames)

    def _handle_logcal(self, cmd: str) -> None:
        parts = cmd.split()
        head = int(parts[1]) if len(parts) > 1 else 1
        v, q = self.sim.logcal(head)
        n = len(v)
        self._out.extend(f"OK H{head} N={n} RB=6\r\n".encode("ascii"))
        self._out.extend(b"".join(struct.pack("<Hi", v[i], q[i]) for i in range(n)))
        self._out.extend(b"OK DONE\r\n")


def _patch_socket(monkeypatch, sock) -> None:
    """Make socket.create_connection return *sock* for EthernetTransport."""
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: sock)


# ---------------------------------------------------------------------------
# mk2 detection (simulator)
# ---------------------------------------------------------------------------


def test_mk2_detection_five_channels():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LINEAR", detector="INGAAS") as pm:
        assert pm.generation() == "mk2"
        assert pm.channel_count() == 5
        assert pm.frontend() == "LINEAR"
        assert pm.detector() == "INGAAS"
        assert len(pm.channels) == 5


def test_mk1_default_generation_unchanged():
    with coreDAQ.connect(simulator=True) as pm:
        assert pm.generation() == "mk1"
        assert pm.channel_count() == 4
        assert len(pm.channels) == 4


def test_mk2_read_all_returns_five_values():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LOG", detector="INGAAS") as pm:
        vals = pm.read_all(unit="mv")
        assert len(vals) == 5


def test_mk2_channel_index_four_valid():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.read_channel(4, unit="mv")           # aux channel is addressable
        with pytest.raises(ValueError):
            pm.read_channel(5)                   # but 5 is out of range


# ---------------------------------------------------------------------------
# Straight-binary uint16 data format
# ---------------------------------------------------------------------------


def test_mk2_straight_binary_uint16_deinterleave(monkeypatch):
    # 3 frames, 2 active channels (mask 0x03). Channel 1 carries a code above
    # the int16 range to prove it is read as unsigned, not two's-complement.
    ch0 = [100, 200, 300]
    ch1 = [60000, 61000, 62000]         # > 32767: negative if misread as int16
    payload = b"".join(struct.pack("<HH", ch0[i], ch1[i]) for i in range(3))
    _patch_socket(monkeypatch, _CannedSocket(payload))

    t = EthernetTransport("10.0.0.9")
    out = t.read_frames(3, 0x03, n_channels=5, unsigned=True)
    assert len(out) == 5
    assert out[0].dtype == np.uint16
    assert list(out[0]) == ch0
    assert list(out[1]) == ch1                  # 60000 stays positive
    assert out[1][0] == 60000


def test_mk1_signed_int16_still_negative(monkeypatch):
    # Same bytes read with unsigned=False: 60000 wraps to a negative int16.
    payload = struct.pack("<HH", 100, 60000)
    _patch_socket(monkeypatch, _CannedSocket(payload))
    t = EthernetTransport("10.0.0.9")
    out = t.read_frames(1, 0x03, n_channels=4, unsigned=False)
    assert out[0].dtype == np.int16
    assert int(out[1][0]) == 60000 - 65536      # two's-complement wrap: -5536
    assert int(out[1][0]) < 0


def test_mk2_mv_conversion_uses_half_lsb():
    # mk2 LSB = 5000/65536 mV. A code of 13107 -> ~1000 mV (1 V).
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LINEAR", detector="INGAAS",
                         noise_sigma_adc=0.0) as pm:
        code = 13107
        expected_mv = round(code * 5000.0 / 65536, 3)
        got = pm._adc_to_unit(0, code, 0, "mv")
        assert got == expected_mv
        # exactly half the mk1 value for the same code
        assert abs(pm._adc_lsb_v - (5.0 / 65536)) < 1e-15


# ---------------------------------------------------------------------------
# 5-channel capture (straight binary) over the simulator
# ---------------------------------------------------------------------------


def test_mk2_capture_five_channels():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LINEAR", detector="INGAAS",
                         noise_sigma_adc=0.0) as pm:
        pm.set_capture_channel_mask(0x1F)
        result = pm.capture(frames=8, unit="mv", channels=[0, 1, 2, 3, 4])
        assert isinstance(result, CaptureResult)
        assert result.enabled_channels == (0, 1, 2, 3, 4)
        for ch in range(5):
            assert len(result.trace(ch)) == 8


def test_mk2_capture_mask_five_bits():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        applied = pm.set_capture_channel_mask(0x1F)
        assert applied == 0x1F
        assert pm.capture_channels() == (0, 1, 2, 3, 4)


# ---------------------------------------------------------------------------
# tier() — read-only, no unlock
# ---------------------------------------------------------------------------


def test_mk2_tier_low():
    with coreDAQ.connect(simulator=True, generation="mk2", tier="LOW") as pm:
        info = pm.tier()
        assert info["tier"] == "LOW"
        assert info["fw"] == "LOWBW"
        assert info["fmax"] == 100_000
        assert info["high_bandwidth"] is False


def test_mk2_tier_high():
    with coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH") as pm:
        info = pm.tier()
        assert info["tier"] == "HIGH"
        assert info["fw"] == "HIGHBW"
        assert info["fmax"] == 1_000_000
        assert info["high_bandwidth"] is True


def test_tier_raises_on_mk1():
    with coreDAQ.connect(simulator=True) as pm:
        with pytest.raises(coreDAQUnsupportedError):
            pm.tier()


def test_no_unlock_method_exists():
    # SECURITY: the driver must expose no unlock/license entry point.
    for name in dir(coreDAQ):
        low = name.lower()
        assert "unlock" not in low
        assert "license" not in low


# ---------------------------------------------------------------------------
# Sensors (with ERR -> None handling)
# ---------------------------------------------------------------------------


def test_mk2_sensors_return_floats():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         temperature_c=23.4, humidity_pct=38.2, die_temp_c=45.6) as pm:
        assert pm.temperature() == 23.4
        assert pm.humidity() == 38.2
        assert pm.die_temperature() == 45.6


def test_mk2_sensor_err_returns_none():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         temperature_c=None, humidity_pct=None, die_temp_c=None) as pm:
        assert pm.temperature() is None       # ERR NO_SENSOR
        assert pm.humidity() is None          # ERR NO_SENSOR
        assert pm.die_temperature() is None   # ERR ADC


def test_sensors_raise_on_mk1():
    with coreDAQ.connect(simulator=True) as pm:
        with pytest.raises(coreDAQUnsupportedError):
            pm.temperature()
        with pytest.raises(coreDAQUnsupportedError):
            pm.die_temperature()


# ---------------------------------------------------------------------------
# UID / sysstat
# ---------------------------------------------------------------------------


def test_mk2_uid():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        uid = pm.uid()
        assert isinstance(uid, str) and len(uid) == 24


def test_mk2_sysstat_parsed():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        stat = pm.sysstat()
        assert stat["uptime"] == 123
        assert stat["heap_free"] == 40000
        assert stat["i2c_err"] == 0
        assert stat["sht"] == "OK"
        assert "raw" in stat


# ---------------------------------------------------------------------------
# IP config / Ethernet status
# ---------------------------------------------------------------------------


def test_mk2_ip_config_default_dhcp():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        cfg = pm.ip_config()
        assert cfg["mode"] == "DHCP"
        assert cfg["ip"] == "169.254.10.20"
        assert cfg["mask"] == "255.255.0.0"


def test_mk2_set_ip_static_then_read():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.set_ip_static("192.168.1.50", "255.255.255.0", "192.168.1.1")
        cfg = pm.ip_config()
        assert cfg["mode"] == "STATIC"
        assert cfg["ip"] == "192.168.1.50"
        assert cfg["gateway"] == "192.168.1.1"


def test_mk2_set_ip_dhcp():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.set_ip_static("10.0.0.5", "255.0.0.0", "10.0.0.1")
        pm.set_ip_dhcp()
        assert pm.ip_config()["mode"] == "DHCP"


def test_mk2_set_ip_static_validates_ipv4():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        with pytest.raises(ValueError):
            pm.set_ip_static("not.an.ip", "255.255.255.0", "192.168.1.1")
        with pytest.raises(ValueError):
            pm.set_ip_static("192.168.1.999", "255.255.255.0", "192.168.1.1")


def test_mk2_eth_status():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        eth = pm.eth_status()
        assert eth["link_up"] is True
        assert eth["port"] == 5025
        assert eth["mac"] == "02:00:00:00:00:01"


def test_ip_config_raises_on_mk1():
    with coreDAQ.connect(simulator=True) as pm:
        with pytest.raises(coreDAQUnsupportedError):
            pm.ip_config()
        with pytest.raises(coreDAQUnsupportedError):
            pm.eth_status()


# ---------------------------------------------------------------------------
# gain get/set on mk2 LINEAR (same protocol as mk1)
# ---------------------------------------------------------------------------


def test_mk2_linear_gain_get_set():
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LINEAR", detector="INGAAS") as pm:
        pm.set_range(0, 5)
        assert pm.get_range(0) == 5
        ranges = pm.get_ranges()
        assert len(ranges) == 5
        assert ranges[0] == 5
        assert ranges[4] is None        # aux channel has no gain range


# ---------------------------------------------------------------------------
# Sample rate / oversampling caps are generation-aware
# ---------------------------------------------------------------------------


def test_mk2_sample_rate_up_to_1mhz():
    with coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH") as pm:
        pm.set_sample_rate_hz(1_000_000)        # allowed on mk2 High Performance
        with pytest.raises(coreDAQError):
            pm.set_sample_rate_hz(2_000_000)


def test_mk1_sample_rate_capped_at_100k():
    with coreDAQ.connect(simulator=True) as pm:
        with pytest.raises(coreDAQError):
            pm.set_sample_rate_hz(1_000_000)    # rejected on mk1


def test_mk2_oversampling_up_to_8():
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.set_oversampling(8)                  # allowed on mk2
        with pytest.raises(coreDAQError):
            pm.set_oversampling(9)


# ---------------------------------------------------------------------------
# connect(transport="ethernet") wiring (against a mock socket)
# ---------------------------------------------------------------------------


def test_connect_ethernet_requires_host():
    with pytest.raises(coreDAQConnectionError):
        coreDAQ.connect(transport="ethernet")   # no host


def test_connect_ethernet_full_stack(monkeypatch):
    # Full connect()->_detect_variant->_load_calibration (incl. LOGCAL binary)
    # runs over EthernetTransport, driven by a mk2 InGaAs LOG simulator.
    sim = SimTransport(generation="mk2", frontend="LOG", detector="INGAAS")
    _patch_socket(monkeypatch, _SimSocket(sim))

    pm = coreDAQ.connect(transport="ethernet", host="192.168.7.7")
    try:
        assert pm.generation() == "mk2"
        assert pm.channel_count() == 5
        assert pm.frontend() == "LOG"
        assert pm.detector() == "INGAAS"
        assert pm._port_name() == "192.168.7.7:5025"
        # A live query and a mk2-only query both work over TCP.
        assert pm.tier()["tier"] == "LOW"
        assert pm.uid() == "0123456789ABCDEF01234567"
    finally:
        pm.close()


def test_connect_ethernet_capture_over_socket(monkeypatch):
    # 5-channel capture end-to-end over EthernetTransport (XFER binary path).
    sim = SimTransport(generation="mk2", frontend="LINEAR", detector="INGAAS",
                       noise_sigma_adc=0.0)
    _patch_socket(monkeypatch, _SimSocket(sim))

    pm = coreDAQ.connect(transport="ethernet", host="192.168.7.8")
    try:
        pm._transport.acq_overhead_s = 0.0      # keep the wait short
        pm.set_capture_channel_mask(0x1F)
        result = pm.capture(frames=6, unit="mv", channels=[0, 1, 2, 3, 4])
        assert result.enabled_channels == (0, 1, 2, 3, 4)
        for ch in range(5):
            assert len(result.trace(ch)) == 6
    finally:
        pm.close()


def test_ethernet_transport_ask_and_port_name(monkeypatch):
    sim = SimTransport(generation="mk2", frontend="LOG", detector="INGAAS")
    _patch_socket(monkeypatch, _SimSocket(sim))
    t = EthernetTransport("1.2.3.4", 5025)
    st, p = t.ask("IDN?")
    assert st == "OK"
    assert "mk2" in p.lower()
    assert t.port_name() == "1.2.3.4:5025"
    # ERR replies parse cleanly.
    st, p = t.ask("BW 0x01")                     # LOW tier -> NOT_SUPPORTED
    assert st == "ERR"
    t.close()


def test_mk2_sync_mode_roundtrip():
    """coreLINK master/slave role reads and switches (High Performance tier)."""
    from py_coreDAQ import coreDAQ
    daq = coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH")
    try:
        assert daq.sync_mode() == "MASTER"
        assert daq.set_sync_mode("slave") == "SLAVE"
        assert daq.sync_mode() == "SLAVE"
        assert daq.set_sync_mode("standalone") == "MASTER"
        assert daq.sync_mode() == "MASTER"
        import pytest
        with pytest.raises(ValueError):
            daq.set_sync_mode("bogus")
    finally:
        daq.close()


def test_mk1_rejects_sync_mode():
    from py_coreDAQ import coreDAQ
    from py_coreDAQ._exceptions import coreDAQError
    daq = coreDAQ.connect(simulator=True, generation="mk1")
    try:
        import pytest
        with pytest.raises(coreDAQError):
            daq.sync_mode()
    finally:
        daq.close()


def test_mk2_frames_ovfl_parsed_and_not_crash():
    """mk2 FRAMES? OVFL= field parses; the frame count is never corrupted by it."""
    from py_coreDAQ import coreDAQ
    daq = coreDAQ.connect(simulator=True, generation="mk2")
    try:
        daq.set_capture_channel_mask(0x1F)
        daq.arm_capture(1000)
        daq.start_capture()
        assert daq.captured_frames() == 1000     # OVFL token must not break the count
        assert daq.capture_overflowed() is False
    finally:
        daq.close()


def test_mk1_frames_no_ovfl_and_overflow_false():
    from py_coreDAQ import coreDAQ
    daq = coreDAQ.connect(simulator=True, generation="mk1")
    try:
        daq.arm_capture(1000)
        daq.start_capture()
        assert daq.captured_frames() == 1000
        assert daq.capture_overflowed() is False
    finally:
        daq.close()


# ---------------------------------------------------------------------------
# error taxonomy (v2.0.0)
# ---------------------------------------------------------------------------

def test_error_taxonomy_subclassing():
    from py_coreDAQ import (coreDAQError, coreDAQUnsupportedError,
                            coreDAQLicenseError, coreDAQStateError)
    assert issubclass(coreDAQLicenseError, coreDAQUnsupportedError)
    assert issubclass(coreDAQLicenseError, coreDAQError)
    assert issubclass(coreDAQStateError, coreDAQError)


def test_error_for_payload_mapping():
    from py_coreDAQ._exceptions import (error_for_payload, coreDAQError,
                                        coreDAQLicenseError, coreDAQStateError,
                                        coreDAQUnsupportedError)
    assert isinstance(error_for_payload("BW 0x10", "LICENSE"), coreDAQLicenseError)
    assert isinstance(error_for_payload("XFER", "EMPTY"), coreDAQStateError)
    assert isinstance(error_for_payload("SNAP", "SLAVE_MODE"), coreDAQStateError)
    assert isinstance(error_for_payload("DFU", "USB_ONLY"), coreDAQUnsupportedError)
    e = error_for_payload("FREQ 500000", "FREQ_FAIL")
    assert type(e) is coreDAQError                      # unknown token -> base
    assert str(e) == "FREQ 500000 failed: FREQ_FAIL"    # historical format kept
    assert str(error_for_payload("X", "")) == "X failed: "


# ---------------------------------------------------------------------------
# v2.0.0 defect-fix pins (D1-D7)
# ---------------------------------------------------------------------------

def test_collect_capture_uses_frames_query_on_mk2():
    # D1: mk2 fw v1.0 must take the FRAMES?-validated path, not the pre-v4.3
    # session-bookkeeping fallback (raw (1,0,0) < (4,3) misroute).
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.set_capture_channels([0])
        pm.arm_capture(64)
        pm.start_capture()
        import time as _t
        _t.sleep(0.05)
        pm._armed_frames = 0            # simulate a fresh host session
        res = pm.collect_capture(64)    # would raise "no capture was armed" pre-fix
        assert len(res.trace(0)) == 64


def test_collect_capture_over_request_mentions_stored_mk2():
    import pytest
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        pm.set_capture_channels([0])
        pm.arm_capture(64)
        pm.start_capture()
        import time as _t
        _t.sleep(0.05)
        with pytest.raises(ValueError):
            pm.collect_capture(65)


def test_frames_query_parse_robustness():
    # D3: OVFL numeric parse + unknown-token immunity
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        real_ask = pm._transport.ask
        for payload, want in (
            ("100 MISSED=2 OVFL=0x0", (100, 2, False)),
            ("100 MISSED=0 OVFL=1", (100, 0, True)),
            ("100 OVFL=00", (100, 0, False)),
            ("100 READY MISSED=zz", (100, 0, False)),   # junk tokens skipped
        ):
            pm._transport.ask = lambda c, _p=payload, _r=real_ask: (
                ("OK", _p) if c.startswith("FRAMES") else _r(c))
            assert pm._frames_query() == want
        pm._transport.ask = real_ask


def test_capture_overflowed_false_on_old_mk1():
    # D3a: pre-v4.3 mk1 returns False (docstring contract), never raises
    with coreDAQ.connect(simulator=True) as pm:            # mk1 sim
        pm._firmware_version = (4, 2, 0)
        assert pm.capture_overflowed() is False


def test_set_ranges_roundtrip_mk2_linear():
    # D4: get_ranges() -> set_ranges() roundtrip with the None aux entry
    with coreDAQ.connect(simulator=True, generation="mk2",
                         frontend="LINEAR") as pm:
        r = pm.get_ranges()
        assert len(r) == 5 and r[4] is None
        out = pm.set_ranges(r)
        assert len(out) == 5
        import pytest
        with pytest.raises(ValueError, match="5"):
            pm.set_ranges([0, 0, 0, 0])


def test_signal_flags_unipolar_mk2():
    # D5: 4.5 V is healthy on the 0-5 V rail; 4.95 V is over; over is SIGNED
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        over, under, clip = pm._signal_flags(4.5, 4500.0)
        assert not over and not under
        over, _, _ = pm._signal_flags(4.95, 4950.0)
        assert over
        over, under, _ = pm._signal_flags(-0.004, -4.0)   # small dark negative
        assert not over and under


def test_signal_flags_mk1_regression_lock():
    # D5: mk1 semantics byte-identical to <=1.2.1 (abs() both checks, 4.2 V)
    with coreDAQ.connect(simulator=True) as pm:            # mk1 sim
        assert pm._signal_flags(4.5, 4500.0)[0] is True    # |4.5| > 4.2
        assert pm._signal_flags(-4.5, -4500.0)[0] is True  # abs() semantics
        assert pm._signal_flags(4.1, 4100.0) == (False, False, False)
        assert pm._signal_flags(0.004, 4.0)[1] is True     # |4 mV| < 5 mV


def test_read_frames_derives_channel_count():
    # D6: no n_channels kwarg + 5-bit mask must not truncate to 4 channels
    from py_coreDAQ._simulator import SimTransport
    t = SimTransport(generation="mk2")
    t.ask("CHMASK 0x1F")
    t.ask("ACQ ARM 16"); t.ask("ACQ START")
    import time as _t
    _t.sleep(0.02)
    out = t.read_frames(16, 0x1F)
    assert len(out) == 5 and all(len(a) == 16 for a in out)


def test_read_frames_zero_raises():
    # D2
    import pytest
    from py_coreDAQ._simulator import SimTransport
    t = SimTransport(generation="mk2")
    with pytest.raises(ValueError, match="> 0"):
        t.read_frames(0, 0x0F)


def test_start_capture_refuses_device_armed_fresh_session():
    # D7: trigger-armed on the DEVICE (fresh host session) is still refused
    import pytest
    from py_coreDAQ import coreDAQStateError
    with coreDAQ.connect(simulator=True, generation="mk2") as pm:
        real_ask = pm._transport.ask
        pm._transport.ask = lambda c: ("OK", "1") if c == "STATE?" else real_ask(c)
        pm._armed_trigger = False        # fresh-session amnesia
        pm._armed_frames = 0
        with pytest.raises(coreDAQStateError):
            pm.start_capture()           # STATE?=ARMED on the device wins
        pm._transport.ask = real_ask


# ---------------------------------------------------------------------------
# v2.0.0 tier UX
# ---------------------------------------------------------------------------

def test_tier_name_and_sync_fields():
    with coreDAQ.connect(simulator=True, generation="mk2", tier="LOW") as pm:
        t = pm.tier()
        assert t["name"] == "base" and t["sync"] is False
    with coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH") as pm:
        t = pm.tier()
        assert t["name"] == "high-performance" and t["sync"] is True


def test_tier_sync_inferred_when_token_absent():
    # Old firmware without the SYNC= token: infer from tier
    with coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH") as pm:
        real_ask = pm._transport.ask
        pm._transport.ask = lambda c: (
            ("OK", "TIER=HIGH FW=HIGHBW VARIANT=LOG LOCK=MATCH FMAX=1000000")
            if c == "TIER?" else real_ask(c))
        assert pm.tier()["sync"] is True
        pm._transport.ask = lambda c: (
            ("OK", "TIER=HIGH FW=HIGHBW VARIANT=LOG LOCK=MATCH FMAX=1000000 SYNC=0")
            if c == "TIER?" else real_ask(c))
        assert pm.tier()["sync"] is False      # explicit token beats inference
        pm._transport.ask = real_ask


def test_base_tier_sync_slave_refused_typed():
    import pytest
    from py_coreDAQ import coreDAQLicenseError, coreDAQError
    with coreDAQ.connect(simulator=True, generation="mk2", tier="LOW") as pm:
        with pytest.raises(coreDAQLicenseError) as ei:
            pm.set_sync_mode("slave")
        assert "High Performance" in str(ei.value)
        assert isinstance(ei.value, coreDAQError)
        assert pm.set_sync_mode("master") == "MASTER"   # scrub path stays open


def test_base_tier_rate_clamp_warns_and_stores_truth():
    import warnings as _w
    with coreDAQ.connect(simulator=True, generation="mk2", tier="LOW") as pm:
        with _w.catch_warnings(record=True) as rec:
            _w.simplefilter("always")
            pm.set_sample_rate_hz(500_000)
        assert any("clamped" in str(r.message) for r in rec)
        assert pm.sample_rate_hz() == 100_000
    with coreDAQ.connect(simulator=True, generation="mk2", tier="HIGH") as pm:
        with _w.catch_warnings(record=True) as rec:
            _w.simplefilter("always")
            pm.set_sample_rate_hz(500_000)
        assert not rec
        assert pm.sample_rate_hz() == 500_000
