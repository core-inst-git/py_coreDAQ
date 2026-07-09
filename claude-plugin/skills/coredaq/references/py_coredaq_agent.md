# py_coreDAQ — AI agent guide

This file teaches an AI agent (Claude, GPT, etc.) how to drive a **coreDAQ** optical power
meter / DAQ correctly with the `py_coreDAQ` Python package. Keep it next to your scripts, or
paste it into the agent's context. Every rule here reflects real hardware behavior — follow
them even when a request seems to allow otherwise.

Full human documentation: https://py-coredaq.readthedocs.io/

```bash
pip install py_coreDAQ            # driver (brings numpy, pyserial)
pip install matplotlib h5py       # for the plotting / saving recipes below
```

---

## 1. The device in one paragraph

coreDAQ is a USB-powered, low-noise opto-electronic DAQ with **4 measurement channels**
(numbered `0..3`), sample rates up to **100 kHz**, **32 MiB** of on-board capture memory, and
a **BNC trigger input**. It exists in four variants — detector **InGaAs** (910–1700 nm) or
**Silicon** (400–1100 nm), amplifier **LINEAR** (transimpedance, 8 gain ranges, ~300 pW–3 mW)
or **LOG** (single logarithmic range, ~1 nW–3 mW). The driver detects the variant at connect
time and picks the right calibration automatically — call `frontend()` / `detector()` when
behavior depends on it. A built-in simulator (`simulator=True`) runs every example without
hardware.

Device side: an STM32 microcontroller exposing a **USB-CDC virtual COM port** over
**USB High-Speed (480 Mbit/s — HS, not Full-Speed)**. That HS link is what makes bulk
capture transfers fast; the CDC `baudrate` parameter is nominal and can be left at its
default.

---

## 2. Golden rules (never violate)

1. **Leave oversampling at OS 1.** The device initializes to OS 1 and should stay there.
   Never call `set_oversampling()` unless the user explicitly insists after being told it is
   not recommended.
2. **Live streaming runs at 500 Hz — no more, no less.** Poll `read_channel()` at exactly
   500 Hz for live plots; the device and USB link hold that rate dependably. Don't try to
   go faster (the USB round-trip won't keep up) and don't bother measuring or benchmarking
   the achieved throughput — it isn't needed. Faster acquisition → block capture.
3. **Block capture may use up to 100 kHz** (`set_sample_rate_hz(1..100_000)`; typical
   values: 500, 1000, 2000, 5000, 10_000, 100_000).
4. **Never send commands to the device while it is acquiring a capture.** On firmware
   < v4.2 this corrupts samples. Sleep out the acquisition window
   (`frames / rate + 0.5` s); on firmware ≥ v4.2 you may instead poll
   `capture_is_data_ready()` every 0.1 s.
5. **Never call `stop_capture()` to interrupt a read.** Read commands (`read_channel()`,
   `read_all()`, averaged reads) always execute and finish by themselves — let them.
   Calling `stop_capture()` while a read is in flight puts the device into a bad state
   (recover with `reset()`). `stop_capture()` is only for aborting an armed/running
   *capture*.
6. **Polling loops are software-paced** — timestamps jitter with the OS and USB. Record
   real timestamps alongside values when timing matters. When the user needs an
   exact/uniform time base, use capture (hardware-timed) instead of a polling loop.
7. **`frames` means samples per active channel**, not total. Check
   `max_capture_frames()` before large captures (32 MiB SDRAM, 2 bytes × active channels
   per frame).
8. **On LINEAR frontends autoRange does not run during capture.** Set the range before
   arming: do one `read_all()` (lets autoRange settle) or call `set_range_power()` first.
9. **Never call `enter_dfu_mode()`** unless the user explicitly asks to update firmware —
   it reboots the instrument into the bootloader and it leaves the USB bus.
10. **Close the device** — use `with coreDAQ.connect() as coredaq:` wherever possible. The
    serial port is exclusive; a second script cannot open the device while one holds it.
11. **Ask the wavelength if unknown** and call `set_wavelength_nm()` before power
    measurements — power conversion uses it for the responsivity correction.

---

## 3. Connecting

```python
from py_coreDAQ import coreDAQ

coredaq = coreDAQ.connect()                      # auto-discover the only device on the bus
coredaq = coreDAQ.connect("/dev/tty.usbmodem12401")  # explicit port
ports   = coreDAQ.discover()                     # list all coreDAQ port paths
coredaq.close()                                  # or use a context manager
```

- `connect()` with several devices attached raises `coreDAQConnectionError` listing the
  ports — pass `port=` explicitly.
- **Windows: port auto-discovery is currently not working.** Always pass the COM port
  explicitly on Windows — `coreDAQ.connect("COM5")` — the user can read it from Device
  Manager (it appears as a USB Serial Device / virtual COM port). Auto-discovery works on
  macOS and Linux.
- **Simulator** (no hardware, deterministic, seed=42): all four variants supported.

```python
coredaq = coreDAQ.connect(simulator=True)                              # InGaAs LOG, 1550 nm
coredaq = coreDAQ.connect(simulator=True, frontend="LINEAR",
                          detector="SILICON", wavelength_nm=850.0)
# extra sim kwargs: incident_power_w, noise_sigma_adc, seed
```

- Good practice at the top of any script: print `coredaq.identify()` so the run is traceable
  to a serial number and firmware version. `coredaq.firmware_version()` returns
  `(major, minor, patch)` for feature gating (see §8, §9).

Identity / metadata methods: `identify()`, `device_info()`, `serial_number()`,
`firmware_version()`, `frontend()` → `"LINEAR" | "LOG"`, `detector()` → `"INGAAS" | "SILICON"`,
`wavelength_nm()`, `set_wavelength_nm(nm)`, `wavelength_limits_nm()`,
`calibration_info()` (dict: `serial`, `variant`, `valid`, `status`, …).

Environment sensors (readable any time the device is idle): `head_temperature_c()`,
`head_humidity_percent()`, `die_temperature_c()`, `refresh_device_state()`.

---

## 4. Choosing the acquisition mode — decision table

| User asks for | Use | Why |
| --- | --- | --- |
| "current power", one number | `read_channel()` / `read_all()` | single shot |
| "live plot", "monitor", "watch it" | polling loop ≤ 500 Hz (§7) | human-speed display |
| "capture/record N s at X Hz", X > 500 | block capture (§8) | hardware-timed, up to 100 kHz |
| exact/uniform sample spacing at any rate | block capture (§8) | polling jitters |
| "sync to my laser / sweep / source" | triggered capture (§9) | BNC edge starts it |
| step-and-dwell laser, one pulse per step | stepped trigger (§9, fw ≥ v4.3) | burst per edge |
| live view *and* exact-rate recording | live view + separate capture, or chunked captures | can't poll during capture |

For long recordings at ≤ 500 Hz where sample-to-sample timing matters less than duration
(e.g. drift logs), a polling loop with real timestamps is fine and can run for hours.

---

## 5. Units and global settings

Every read/capture accepts `unit=`; `set_reading_unit()` changes the default (initially `"w"`).

| Token | Meaning |
| --- | --- |
| `"w"` | optical power, watts (default) |
| `"dbm"` | optical power, dBm |
| `"mv"` / `"v"` | detector signal voltage |
| `"adc"` | ADC code (zero-corrected on LINEAR, raw on LOG) |

```python
coredaq.set_wavelength_nm(1310.0)   # BEFORE power measurements
coredaq.set_sample_rate_hz(10_000)  # 1..100_000; init 500
coredaq.sample_rate_hz()            # read back
# Oversampling: init OS 1 — leave it there (Golden rule 1).
```

**Unit guidance:** `"w"` for general power work (`"dbm"` for LOG-frontend dynamic-range
work); `"mv"` for electrical/noise diagnostics; `"adc"` for raw transfers at maximum speed.

---

## 6. Single-shot reads

```python
coredaq.read_channel(0)                    # float, watts
coredaq.read_channel(0, unit="dbm")
coredaq.read_all()                         # [W, W, W, W] — always all 4 channels
coredaq.read_channel(0, n_samples=8)       # average of 8 (max 32)
```

- At 500 Hz one measurement takes ~2 ms; `n_samples=8` ≈ 16 ms. Don't send other commands
  while an averaged read is in flight (device answers busy → `coreDAQTimeoutError`).
- `read_channel(..., autoRange=True)` is the default (LINEAR only; harmless on LOG). Note
  the argument spelling: **`autoRange=`** on `coreDAQ` methods, **`auto_range=`** on
  `ChannelProxy.read()`.
- Full-metadata reads: `read_channel_full()` → `ChannelReading` (fields: `value`, `unit`,
  `power_w`, `power_dbm`, `signal_v`, `signal_mv`, `adc_code`, `range_index`, `range_label`,
  `wavelength_nm`, `detector`, `frontend`, `zero_source`, `over_range`, `under_range`,
  `is_clipped`). `read_all_full()` → `MeasurementSet` (iterable, `ms[0]`, `.values()`).
- Per-channel proxy: `ch = coredaq.channels[0]`; `ch.power_w`, `ch.read(unit="dbm")`,
  `ch.read_full()`, `ch.is_clipped()`.

---

## 7. Live streaming and live plots (≤ 500 Hz)

Pattern: a background thread polls `read_channel()` at a software-paced rate into a deque;
matplotlib redraws ~25 fps on a GUI timer. **Run it at 500 Hz — no more, no less** (Golden
rule 2). That rate makes a nice live stream plot and the link holds it dependably; there is
no need to measure or report throughput.

Complete runnable recipe — adapt the constants at the top:

```python
#!/usr/bin/env python3
"""Live scrolling view of one coreDAQ channel. Run with --sim for the simulator."""
import sys, threading, time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
from py_coreDAQ import coreDAQ

CHANNEL       = 0        # 0..3
RATE_HZ       = 500.0    # THE live streaming rate — no more, no less
UNIT          = "w"      # "w" | "dbm" | "mv" | "v" | "adc"
WINDOW_S      = 5.0      # scrolling window width
AUTORANGE     = True     # LINEAR: False gives steadier read timing if power is stable
YLABEL = {"w": "Power (W)", "dbm": "Power (dBm)", "v": "Signal (V)",
          "mv": "Signal (mV)", "adc": "ADC code"}[UNIT]

class Poller(threading.Thread):
    def __init__(self, dev):
        super().__init__(daemon=True)
        self.dev, self.lock, self.running = dev, threading.Lock(), True
        n = int(WINDOW_S * RATE_HZ)
        self.t, self.v = deque(maxlen=n), deque(maxlen=n)
        self.t0 = time.perf_counter()

    def run(self):
        period, next_t = 1.0 / RATE_HZ, time.perf_counter()
        while self.running:
            val = self.dev.read_channel(CHANNEL, unit=UNIT, autoRange=AUTORANGE)
            now = time.perf_counter()
            with self.lock:
                self.t.append(now - self.t0); self.v.append(val)
            next_t += period
            dt = next_t - time.perf_counter()
            if dt > 0: time.sleep(dt)
            else: next_t = time.perf_counter()   # resync, don't spiral

def main():
    with coreDAQ.connect(simulator="--sim" in sys.argv) as dev:
        print(dev.identify())
        poller = Poller(dev); poller.start()
        fig, ax = plt.subplots(figsize=(9, 5))
        (line,) = ax.plot([], [], lw=0.8)
        ax.set_xlabel("Time (s)"); ax.set_ylabel(YLABEL); ax.grid(True, alpha=0.3)
        ax.set_title(f"coreDAQ live — ch{CHANNEL} @ {RATE_HZ:.0f} Hz")

        def update(_):
            with poller.lock:
                t, v = np.asarray(poller.t), np.asarray(poller.v)
            if len(t) < 2: return
            line.set_data(t, v)
            ax.set_xlim(max(0.0, t[-1] - WINDOW_S), max(t[-1], WINDOW_S))
            vmin, vmax = float(v.min()), float(v.max())
            pad = 0.05 * (vmax - vmin) or abs(vmax) * 0.1 or 1e-12
            ax.set_ylim(vmin - pad, vmax + pad)

        timer = fig.canvas.new_timer(interval=40)   # ~25 fps
        timer.add_callback(update, None); timer.start()
        try:
            plt.show()
        finally:
            poller.running = False; poller.join(timeout=2.0)

if __name__ == "__main__":
    main()
```

Adaptations:
- **All four channels:** poll `dev.read_all(unit=UNIT)` once per period (one USB
  transaction) and keep four deques / four lines.
- **Record to file while viewing:** append `(timestamp, value)` to a list during the loop
  and save the timestamps with the data (see the HDF5 attrs pattern in §8) — polling is
  software-paced, so the timestamps are the time base.
- **"Live" above 500 Hz:** run repeated short block captures (e.g. 0.2 s each) and redraw
  between them — note to the user there are gaps between blocks (transfer time).

---

## 8. Block capture — "capture at X sample rate"

Hardware-timed acquisition into the instrument's SDRAM, then one bulk USB transfer.
This is the correct tool whenever the user names a sample rate above 500 Hz or needs a
uniform time base.

**Memory limit** (2 bytes per frame per active channel, 32 MiB total):

| Active channels | Max frames |
| --- | --- |
| 1 | 16,777,216 |
| 2 | 8,388,608 |
| 4 | 4,194,304 |

Check programmatically: `coredaq.max_capture_frames(channels=[0, 1])`.

Simplest form — `capture()` blocks through arm → acquire → transfer:

```python
result = coredaq.capture(frames=4096, unit="w", channels=[0])   # channels optional
```

Complete runnable recipe (capture at X Hz → PNG + HDF5):

```python
#!/usr/bin/env python3
"""Capture a coreDAQ trace at a fixed sample rate, save .h5 + .png. --sim for simulator."""
import datetime, sys

import h5py
import matplotlib.pyplot as plt
import numpy as np
from py_coreDAQ import coreDAQ

CHANNELS   = [0]        # subset of 0..3
RATE_HZ    = 10_000     # 1..100_000 (hardware-timed; >500 Hz is fine HERE, not in live loops)
DURATION_S = 2.0
UNIT       = "w"

def main():
    frames = int(RATE_HZ * DURATION_S)
    with coreDAQ.connect(simulator="--sim" in sys.argv) as dev:
        print(dev.identify())
        assert frames <= dev.max_capture_frames(channels=CHANNELS), "exceeds SDRAM"
        dev.set_sample_rate_hz(RATE_HZ)
        if dev.frontend() == "LINEAR":
            dev.read_all()                       # let autoRange settle a range before capture
        result = dev.capture(frames=frames, unit=UNIT, channels=CHANNELS)

        t = np.arange(frames) / result.sample_rate_hz
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"capture_{stamp}"

        with h5py.File(base + ".h5", "w") as f:
            f.create_dataset("t", data=t)
            for ch in result.enabled_channels:
                f.create_dataset(f"ch{ch}", data=result.trace(ch))
            f.attrs.update({"unit": result.unit, "rate_hz": result.sample_rate_hz,
                            "wavelength_nm": result.wavelength_nm, "idn": dev.identify()})

        fig, ax = plt.subplots(figsize=(6, 4))
        for ch in result.enabled_channels:
            ax.plot(t, result.trace(ch), lw=0.8, label=f"ch{ch}")
            s = result.status(ch)
            if s.any_clipped:
                print(f"WARNING ch{ch}: {s.clipped_samples} clipped samples "
                      f"(peak {s.peak_signal_v:.2f} V)")
        ax.set_xlabel("Time (s)"); ax.set_ylabel(UNIT); ax.grid(True, alpha=0.3)
        ax.legend(); ax.set_title(f"{base}  {RATE_HZ} Hz")
        fig.tight_layout(); fig.savefig(base + ".png", dpi=300)
        print(f"saved {base}.h5 / {base}.png  ({frames} frames per channel)")

if __name__ == "__main__":
    main()
```

Manual workflow (needed when you must do something between arm and collect, e.g. fire an
external source):

```python
frames = 5000
coredaq.set_sample_rate_hz(10_000)
coredaq.arm_capture(frames)
coredaq.start_capture()

import time
if coredaq.firmware_version() >= (4, 2, 0):
    while not coredaq.capture_is_data_ready():   # polling is safe on fw >= 4.2
        time.sleep(0.1)
else:
    time.sleep(frames / 10_000 + 0.5)            # fw < 4.2: NO commands during acquisition

result = coredaq.collect_capture(frames, unit="w")
```

- `result.trace(ch)` → `numpy.ndarray`; `result.status(ch)` → clip stats;
  `result.sample_rate_hz`, `.enabled_channels`, `.unit`, `.wavelength_nm`,
  `.ranges`, `.range_labels`.
- Always check `result.status(ch).any_clipped` and warn the user if set.
- Persistent channel selection: `set_capture_channels([0, 2])` /
  `set_capture_channel_mask(0x5)`; the `channels=` argument on `capture()` /
  `collect_capture()` overrides for that call and restores afterwards. The mask affects
  captures only — `read_all()` always reads all four.

---

## 9. Triggered capture (BNC input)

Two modes, both armed via `arm_capture()`; choose edge with `trigger_rising=True/False`.

**Start trigger (continuous)** — one edge starts free-running sampling at the configured
rate. For continuously-swept sources (sample index ↔ wavelength):

```python
frames = 4096
coredaq.set_sample_rate_hz(10_000)
coredaq.arm_capture(frames, trigger=True, trigger_rising=True)   # returns immediately
# fire/start your external source HERE (same script, non-blocking)
time.sleep(frames / 10_000 + 0.5)          # acquisition window — no device commands
result = coredaq.collect_capture(frames, unit="w")
```

Do **not** use blocking `capture()` for triggered work — you couldn't fire the source.

**Stepped trigger** (firmware ≥ v4.3; older firmware raises `coreDAQUnsupportedError`) —
EVERY edge fires a burst: wait `step_delay_us` → take `step_burst` samples → re-arm. For
step-and-dwell lasers emitting one pulse per wavelength step:

```python
coredaq.set_sample_rate_hz(100_000)              # intra-burst sample rate
coredaq.arm_capture(200_000,                     # generous frame budget
                    trigger=True, trigger_rising=False,
                    stepped=True, step_delay_us=50, step_burst=1)
# ... source sweeps, one pulse per step ...
coredaq.stop_capture()                           # or it stops at the frame budget
print(coredaq.captured_frames(), "frames;", coredaq.step_missed_edges(), "missed edges")
result = coredaq.collect_capture(unit="w")       # no frames arg → take what was stored
```

- `step_delay_us` 0..65535 (≤1 fires immediately) — land mid-dwell, after settling.
- `step_burst` 1..255 — >1 averages noise per step; total samples = steps × burst.
- Keep `step_delay_us` + burst duration shorter than the trigger period. Edges arriving
  too fast are **counted and skipped** (`step_missed_edges()`; 0 = all captured), never
  corrupted. Hardware keeps up to roughly 50,000 pulses/s at minimum delay and burst 1.
- Troubleshooting: capture never fills → check BNC wiring and edge polarity; missed edges
  > 0 → slow the source, shorten delay, or reduce burst.

---

## 10. LINEAR-frontend specifics (ranges, autoRange, zeroing)

Only on `frontend() == "LINEAR"`; these raise `coreDAQUnsupportedError` on LOG.

Range table (index 0 = lowest gain / highest power):

| Index | Full scale | Index | Full scale |
| --- | --- | --- | --- |
| 0 | 5 mW | 4 | 50 µW |
| 1 | 1 mW | 5 | 10 µW |
| 2 | 500 µW | 6 | 5 µW |
| 3 | 100 µW | 7 | 500 nW |

(Query the real table with `supported_ranges()` — legacy units differ.)

- **autoRange** is on by default; before each read it retunes so the signal sits in
  ~50 mV–4 V. Calling any of `set_range()`, `set_ranges()`, `set_range_power()`,
  `set_range_powers()` (or the ChannelProxy variants) **disables global autoRange** so
  your choice sticks; re-enable with `set_autorange(True)`. Per-call override:
  `read_channel(0, autoRange=False)`.
- `set_range_power(ch, power_w)` picks the smallest range that fits the given power —
  preferred over raw indices when the user states an expected power.
- **AutoRange does not operate during captures** — set a suitable fixed range (or do one
  autoranged read) before arming.
- **Dark zeroing:** with the input blocked, `zero_dark(frames=32, settle_s=0.2)` replaces
  the active zero; `restore_factory_zero()` reverts. Zeros are applied host-side to all
  readings automatically (`zero_source` on a full read: `"factory"` / `"user"`).

## 11. LOG-frontend specifics (floors)

- No ranges (`get_range()` → `None`), no zeroing — the full ~1 nW–3 mW span is one
  logarithmic range. Great for signals sweeping many decades without range switching.
- **Power floor:** LOG power readings (`"w"` and `"dbm"`) are clamped at the instrument
  floor — **1 nW (−60 dBm)** standard, automatically **100 pW (−70 dBm)** on
  high-sensitivity units whose calibration reaches ≤ −73 dBm. A trace flat-lining exactly
  at the floor means "at or below the floor", not a real level — tell the user. `"v"`,
  `"mv"`, `"adc"` units are never clamped if you need to look underneath.
- All dBm outputs (both frontends) additionally hard-floor at −75 dBm; dBm values are
  rounded to 2 decimals by design (LSB ≈ 0.15 mV at 200 mV/decade).

## 12. Signal health / clipping

- Thresholds: over-range `|signal| > 4.2 V`, under-range `|signal| < 5 mV`; either sets
  `is_clipped`.
- Live: `coredaq.signal_status(0)` → `SignalStatus(channel, signal_v, signal_mv,
  over_range, under_range, is_clipped)`; `coredaq.is_clipped()` → `[bool]*4`.
- Captures: `result.status(ch)` → `CaptureChannelStatus(any_over_range, any_under_range,
  any_clipped, over_range_samples, under_range_samples, clipped_samples, peak_signal_v)`.
- Agent duty: check clipping after every measurement you make for a user and surface it.

## 13. Device state machine and error recovery

States (`capture_status()`): `IDLE` → `ARMED` → `RUNNING` (acquiring) → `DONE`.
Commands sent while acquiring/busy raise **`coreDAQTimeoutError`**.

Recovery playbook:
1. If a read (`read_channel()` / `read_all()` / averaged read) is in flight, just wait —
   read commands always execute and finish by themselves. **Never `stop_capture()` a
   read** — that puts the device in a bad state (Golden rule 5); if it happens, `reset()`.
2. `coredaq.capture_status()` — see what state it's in (safe when not acquiring).
3. A *capture* may still be running — wait it out, or `stop_capture()` to abort (captures
   only).
4. `coredaq.reset()` — soft reset back to idle; settings return to power-on defaults
   (500 Hz, OS 1, watts).
5. Nothing on the bus → check cable, close other programs holding the port, replug.

Exceptions (all inherit `coreDAQError`): `coreDAQConnectionError` (no device / IDN failed),
`coreDAQTimeoutError` (busy/timeout), `coreDAQCalibrationError` (cal missing/corrupt),
`coreDAQUnsupportedError` (feature not on this variant/firmware).

```python
from py_coreDAQ import coreDAQ, coreDAQError, coreDAQConnectionError
try:
    with coreDAQ.connect() as coredaq:
        print(coredaq.read_all())
except coreDAQConnectionError as e:
    print("No device found:", e)
except coreDAQError as e:
    print("Driver error:", e)
```

## 14. Agent gotchas checklist

- `autoRange=` (coreDAQ methods) vs `auto_range=` (ChannelProxy.read) — spelling differs.
- `frames` is per active channel (Golden rule 6); duration = `frames / sample_rate_hz`.
- Sleep margin after arming: `frames / rate + 0.5` seconds, and verify with
  `capture_is_data_ready()` before collecting.
- `collect_capture()` **without** a frames argument collects exactly what is stored — the
  normal way to read back stepped captures.
- Averaging (`n_samples`) makes reads slow; don't interleave other commands meanwhile.
- Wavelength out of detector limits raises — check `wavelength_limits_nm()` when unsure.
- The simulator is deterministic (seed=42): identical runs → identical data. Perfect for
  testing scripts before touching hardware; state `--sim` support in scripts you write.
- Don't loop `read_channel()` for four channels — one `read_all()` is one USB transaction.
- Long polling sessions: expect occasional missed deadlines; resync (recipe §7) instead of
  accumulating sleep debt.
- Windows: auto-discovery currently broken — always `connect("COMx")` explicitly (§3).
- Reads finish by themselves; `stop_capture()` is for captures only — using it on a read
  puts the device in a bad state (Golden rule 5).
- Firmware gates: ≥ v4.2 poll-during-capture safe; ≥ v4.3 stepped trigger +
  `captured_frames()` / `step_missed_edges()`. Check `firmware_version()` before using.
