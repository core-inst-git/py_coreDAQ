---
name: coredaq
description: Drive a coreDAQ 4-channel optical power meter / DAQ with the py_coreDAQ Python driver — live power plots, block captures up to 100 kHz, externally triggered and stepped acquisition, W/dBm/mV readings, LINEAR range control and LOG floors. Use whenever the user mentions coreDAQ or py_coreDAQ, asks for a live plot or capture from their optical power meter, wants a measurement/monitoring script for this instrument, or debugs coreDAQ behavior (clipping, busy errors, trigger issues).
---

# coreDAQ instrument control

You are writing code for a **coreDAQ** — a 4-channel opto-electronic power meter / DAQ
driven by the `py_coreDAQ` Python package (`pip install py_coreDAQ`).

**Before writing any coreDAQ code, read `references/py_coredaq_agent.md` in this skill's
directory.** It is the complete device + driver guide: golden rules, an acquisition-mode
decision table, verified copy-paste recipes (live plot, capture at X Hz, triggered and
stepped capture), variant-specific behavior (LINEAR vs LOG), and error recovery.

## Non-negotiable rules (details in the reference)

1. Oversampling stays at **OS 1** — never call `set_oversampling()`.
2. Live streaming (polling reads) runs at **exactly 500 Hz** — no more, no less, and no
   throughput benchmarking needed. Anything faster → block capture (up to 100 kHz).
3. Never send commands to the device while a capture is acquiring.
4. Never call `stop_capture()` to interrupt a read — reads finish by themselves;
   interrupting them puts the device in a bad state (`reset()` recovers).
5. On **Windows**, port auto-discovery is currently broken — always
   `coreDAQ.connect("COMx")` explicitly.
6. Never call `enter_dfu_mode()` unless the user explicitly asks for a firmware update.

## Request → recipe map

| User asks | Do |
| --- | --- |
| "make a live plot" | §7 live recipe (500 Hz polling, scrolling matplotlib) |
| "capture/record at X Hz" | §8 capture recipe (hardware-timed, saves .h5 + .png) |
| "sync to my laser/source" | §9 triggered capture (continuous or stepped) |
| "what power is it reading" | `read_channel()` / `read_all()` one-shots |
| device seems stuck / busy errors | §13 recovery playbook (`reset()`) |

Scripts you write should accept `--sim` (`coreDAQ.connect(simulator="--sim" in sys.argv)`)
so users can test without hardware, and should print `identify()` at startup. After any
measurement, check clipping (`result.status(ch).any_clipped` / `is_clipped()`) and tell
the user if set.
