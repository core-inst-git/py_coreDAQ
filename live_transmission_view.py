#!/usr/bin/env python3
"""Live transmission viewer for coreDAQ with HDF5 recording.

Polls one channel with read_channel() (the driver's single-shot "snap")
at a software-paced 500 Hz, shows a scrolling live plot, and has a REC
button that records for X seconds (max 100 s) into a .h5 file.

Note on timing: this is software-paced polling over USB-CDC, so the
actual rate depends on the round-trip time — the achieved rate is
measured and stored in the .h5 attrs, and every sample carries its real
timestamp. If you need hardware-exact 500 Hz sampling, use the block
capture path instead (dev.set_sample_rate_hz(500); dev.capture(...)).

Plot the resulting file with plot_transmission_h5.py.

Run with --sim to use the built-in simulator (no hardware needed).
"""

import datetime
import sys
import threading
import time
from collections import deque

import h5py
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import Button, TextBox

from py_coreDAQ import coreDAQ

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
CHANNEL = 1            # 0..3
RATE_HZ = 500.0        # polling rate target
UNIT = "w"             # "w" | "dbm" | "v" | "mv" | "adc"
LIVE_WINDOW_S = 5.0    # width of the scrolling live view
DEFAULT_RECORD_S = 10.0
MAX_RECORD_S = 100.0
AUTORANGE = False      # off = deterministic read timing (LINEAR only anyway)

_YLABELS = {
    "w": "Power (W)",
    "dbm": "Power (dBm)",
    "v": "Signal (V)",
    "mv": "Signal (mV)",
    "adc": "ADC code",
}


class Acquisition(threading.Thread):
    """Paced polling loop; feeds the live deque and the record buffer."""

    def __init__(self, dev: coreDAQ) -> None:
        super().__init__(daemon=True)
        self.dev = dev
        self.lock = threading.Lock()
        n_live = int(LIVE_WINDOW_S * RATE_HZ)
        self.live_t: deque = deque(maxlen=n_live)
        self.live_v: deque = deque(maxlen=n_live)
        self.t0 = time.perf_counter()
        self.running = True
        self.missed_deadlines = 0
        self.n_samples = 0
        # recording state
        self.recording = False
        self.rec_t: list = []
        self.rec_v: list = []
        self.rec_stop_at = 0.0
        self.rec_started_wall: datetime.datetime | None = None
        self.finished_record: tuple | None = None  # (t_array, v_array, wall_start)

    def start_record(self, seconds: float) -> None:
        with self.lock:
            if self.recording:
                print("already recording — ignored")
                return
            self.rec_t, self.rec_v = [], []
            self.rec_stop_at = time.perf_counter() + seconds
            self.rec_started_wall = datetime.datetime.now()
            self.recording = True
        print(f"recording {seconds:.1f} s ...")

    def rec_remaining_s(self) -> float:
        return max(0.0, self.rec_stop_at - time.perf_counter())

    def run(self) -> None:
        period = 1.0 / RATE_HZ
        next_t = time.perf_counter()
        while self.running:
            val = self.dev.read_channel(CHANNEL, unit=UNIT, autoRange=AUTORANGE)
            now = time.perf_counter()
            with self.lock:
                self.live_t.append(now - self.t0)
                self.live_v.append(val)
                self.n_samples += 1
                if self.recording:
                    self.rec_t.append(now)
                    self.rec_v.append(val)
                    if now >= self.rec_stop_at:
                        self.recording = False
                        t = np.asarray(self.rec_t) - self.rec_t[0]
                        v = np.asarray(self.rec_v, dtype=np.float64)
                        self.finished_record = (t, v, self.rec_started_wall)
            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                self.missed_deadlines += 1
                next_t = time.perf_counter()  # resync instead of spiraling


def save_h5(t: np.ndarray, v: np.ndarray, wall_start: datetime.datetime,
            dev: coreDAQ) -> str:
    fname = wall_start.strftime(f"transmission_ch{CHANNEL}_%Y%m%d_%H%M%S.h5")
    actual_hz = (len(t) - 1) / t[-1] if len(t) > 1 and t[-1] > 0 else 0.0
    with h5py.File(fname, "w") as f:
        f.create_dataset("t", data=t)             # s, relative to record start
        f.create_dataset("signal", data=v)
        f.attrs["unit"] = UNIT
        f.attrs["channel"] = CHANNEL
        f.attrs["rate_hz_requested"] = RATE_HZ
        f.attrs["rate_hz_actual"] = actual_hz
        f.attrs["duration_s"] = float(t[-1]) if len(t) else 0.0
        f.attrs["n_samples"] = len(v)
        f.attrs["start_time_iso"] = wall_start.isoformat()
        try:
            f.attrs["idn"] = dev.identify()
            f.attrs["detector"] = dev.detector()
            f.attrs["wavelength_nm"] = dev.wavelength_nm()
            f.attrs["serial"] = dev.serial_number()
        except Exception:
            pass  # metadata is best-effort; keep the data
    print(f"saved {fname}  ({len(v)} samples, actual rate {actual_hz:.1f} Hz)")
    return fname


def main() -> None:
    dev = coreDAQ.connect(simulator="--sim" in sys.argv)
    print(dev.identify())
    dev.set_autorange(AUTORANGE)

    acq = Acquisition(dev)
    acq.start()

    fig, ax = plt.subplots(figsize=(9, 5))
    fig.subplots_adjust(bottom=0.22)
    fig.canvas.manager.set_window_title(f"coreDAQ live — ch{CHANNEL}")
    (line,) = ax.plot([], [], lw=0.8)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(_YLABELS[UNIT])
    ax.grid(True, alpha=0.3)
    status = ax.set_title(f"live  ch{CHANNEL}  target {RATE_HZ:.0f} Hz")

    # widgets: [ duration textbox ] [ REC button ]
    ax_dur = fig.add_axes([0.15, 0.05, 0.12, 0.07])
    tb_dur = TextBox(ax_dur, f"record s (max {MAX_RECORD_S:.0f}): ",
                     initial=f"{DEFAULT_RECORD_S:.0f}")
    ax_rec = fig.add_axes([0.32, 0.05, 0.14, 0.07])
    btn_rec = Button(ax_rec, "● REC", color="#f4c7c3", hovercolor="#f0a8a2")

    def on_rec(_event) -> None:
        try:
            seconds = float(tb_dur.text)
        except ValueError:
            print(f"bad duration {tb_dur.text!r}")
            return
        seconds = min(max(seconds, 0.1), MAX_RECORD_S)
        tb_dur.set_val(f"{seconds:g}")
        acq.start_record(seconds)

    btn_rec.on_clicked(on_rec)

    def update(_frame):
        with acq.lock:
            t = np.asarray(acq.live_t)
            v = np.asarray(acq.live_v)
            recording = acq.recording
            remaining = acq.rec_remaining_s() if recording else 0.0
            done = acq.finished_record
            acq.finished_record = None
        if done is not None:
            save_h5(*done, dev=dev)
        if len(t) < 2:
            return (line,)
        line.set_data(t, v)
        ax.set_xlim(max(0.0, t[-1] - LIVE_WINDOW_S), max(t[-1], LIVE_WINDOW_S))
        vmin, vmax = float(np.min(v)), float(np.max(v))
        pad = 0.05 * (vmax - vmin) or abs(vmax) * 0.1 or 1e-12
        ax.set_ylim(vmin - pad, vmax + pad)
        if recording:
            status.set_text(f"● RECORDING  {remaining:.1f} s left")
            status.set_color("crimson")
        else:
            elapsed = t[-1] - t[0]
            rate = (len(t) - 1) / elapsed if elapsed > 0 else 0.0
            status.set_text(f"live  ch{CHANNEL}  {rate:.0f} Hz achieved "
                            f"(target {RATE_HZ:.0f})")
            status.set_color("black")
        return (line,)

    timer = fig.canvas.new_timer(interval=40)  # ~25 fps GUI refresh
    timer.add_callback(update, None)
    timer.start()

    try:
        plt.show()
    finally:
        acq.running = False
        acq.join(timeout=2.0)
        if acq.missed_deadlines:
            print(f"note: {acq.missed_deadlines} missed deadlines out of "
                  f"{acq.n_samples} samples — polling could not hold "
                  f"{RATE_HZ:.0f} Hz continuously")
        dev.close()


if __name__ == "__main__":
    main()
