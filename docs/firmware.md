# Firmware Updates <span class="mk2">Mk2</span>

coreDAQ Mk2 updates its firmware **from your web browser** — no driver installs, no
command-line tools, no risk of bricking the instrument.

<span class="mk2-legend">This page describes a feature available on coreDAQ Mk2 only.</span>

## Update from your browser

1. Open the **coreDAQ firmware updater** at
   **[core-instrumentation.com/update](https://core-instrumentation.com/update)** in
   **Google Chrome** or **Microsoft Edge** (desktop — Windows, macOS, or Linux).
2. Connect the instrument with a USB-C cable and click **Connect**, then pick your
   coreDAQ from the list.
3. The page shows your unit — firmware version, licence tier, and serial. Click
   **Check for updates**.
4. If an update is available, click **Install** and wait. The instrument verifies the
   update, installs it, and reconnects on its own — don't unplug until it reports done.

That's it. Nothing to install on your computer, on any operating system.

## Safe by design

- **Signed updates.** Every firmware image is cryptographically signed by Core
  Instrumentation and verified **on the instrument itself** before it is applied. A
  corrupted or unofficial file is refused, and your coreDAQ keeps running its current
  firmware.
- **Never bricks.** If power is lost during an update, the instrument either keeps its
  previous firmware or finishes the installation on the next power-up. It always boots.
- **Your settings survive.** Calibration, licence tier, and the unique device ID are
  preserved across an update.

## Checking your firmware version

The driver reports the running version at any time:

```python
from py_coreDAQ import coreDAQ

with coreDAQ.connect() as coredaq:
    print(coredaq.firmware_version())   # e.g. (1, 6, 0)
    print(coredaq.identify())           # full identity string
```

## Licence tier and firmware

Updating firmware does **not** change your performance tier — a High-performance unit
stays High-performance, a Base unit stays Base. Tier upgrades are a separate step handled
by Core Instrumentation; see [Performance Tiers & Licensing](tiers.md).

!!! note "Service updates"
    A traditional USB firmware-recovery mode also exists for service use. You should not
    need it — the browser updater above is the supported path for field updates. If you
    ever do, contact support.

## Related pages

- [Performance Tiers & Licensing](tiers.md)
- [Mk2 & Ethernet](mk2.md)
