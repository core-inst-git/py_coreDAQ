#!/usr/bin/env python3
"""Plot a transmission recording made by live_transmission_view.py.

Usage:
    python3 plot_transmission_h5.py transmission_ch1_20260708_143000.h5 [more.h5 ...]

Saves a 300 dpi PNG next to each .h5 and shows the plot.
"""

import sys
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np

# SI auto-prefix for the watt axis so 3.2e-06 reads as 3.2 µW
_PREFIXES = [(1.0, "W"), (1e-3, "mW"), (1e-6, "µW"), (1e-9, "nW"), (1e-12, "pW")]


def _watt_scale(v: np.ndarray) -> tuple[float, str]:
    peak = float(np.max(np.abs(v))) if len(v) else 0.0
    for scale, label in _PREFIXES:
        if peak >= scale:
            return scale, label
    return 1e-12, "pW"


def plot_file(path: Path) -> None:
    with h5py.File(path, "r") as f:
        t = f["t"][:]
        v = f["signal"][:]
        unit = f.attrs.get("unit", "?")
        channel = f.attrs.get("channel", "?")
        rate = f.attrs.get("rate_hz_actual", 0.0)
        wavelength = f.attrs.get("wavelength_nm", None)

    fig, ax = plt.subplots(figsize=(6, 4))
    if unit == "w":
        scale, label = _watt_scale(v)
        ax.plot(t, v / scale, lw=0.8)
        ax.set_ylabel(f"Power ({label})")
    elif unit in ("v", "mv"):
        mv = v * 1000.0 if unit == "v" else v
        ax.plot(t, mv, lw=0.8)
        ax.set_ylabel("Signal (mV)")
    elif unit == "dbm":
        ax.plot(t, v, lw=0.8)
        ax.set_ylabel("Power (dBm)")
    else:
        ax.plot(t, v, lw=0.8)
        ax.set_ylabel("ADC code")
    ax.set_xlabel("Time (s)")
    ax.grid(True, alpha=0.3)

    subtitle = f"ch{channel}, {rate:.0f} Hz"
    if wavelength is not None:
        subtitle += f", {wavelength:.0f} nm"
    ax.set_title(f"{path.stem}\n{subtitle}", fontsize=10)
    fig.tight_layout()

    out = path.with_suffix(".png")
    fig.savefig(out, dpi=300)
    print(f"saved {out}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"skipping {path} — not found")
            continue
        plot_file(path)
    plt.show()


if __name__ == "__main__":
    main()
