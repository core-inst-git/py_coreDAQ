# py_coreDAQ — project context

Python driver + STM32F730 firmware for the coreDAQ low-noise opto-electronic power meter / DAQ.
Current package version: see `pyproject.toml` (`version = ...`). Firmware line is v4.x.

## Variants (this matters everywhere)
Four detector/scale variants, two firmwares:
- **InGaAs LINEAR**, **InGaAs LOG**, **Silicon LINEAR**, **Silicon LOG**.
- LINEAR and LOG are **separate firmware projects** (`LinearFirmware/`, `LogFirmware/`). Never merge.
- Variant, serial number, and wavelength are identified at runtime **from the calibration image**, not compiled in.

## Measurement floor logic (LOG variants)
- Default power floor is **1 nW = −60 dBm** when called with dBm units. Volts and raw ADC code are left unclamped.
- Newer detectors calibrated deeper: if the cal LUT reaches **below −73 dBm**, select the **−70 dBm (100 pW)** floor (conservative). Floor is chosen from cal depth automatically.
- LINEAR silicon: API must subtract factory dark zeros before conversion.
- dBm rounding: LSB is 0.15 mV, 200 mV/decade — round to a sensible number of decimals, don't dump full float precision.

## LUT
- 128 LUT points per channel (current standard). 4 heads/channels.
- Legacy `calib.c` stored one LUT for all 4 heads; new format is per-channel.

## Firmware build
Each firmware has its own `build.sh` (LinearFirmware/build.sh, LogFirmware/build.sh):
- Toolchain: `/opt/ST/STM32CubeCLT_1.19.0/GNU-tools-for-STM32/bin`
- Runs `make -j` in `Release/`, then objcopy ELF→BIN using `BUILD_ARTIFACT_NAME` from the makefile.
- Verify the emitted version in the filename — historically build.sh sometimes emitted v1.0 when it should be v4.x.

## CubeMX regen "protections" (re-apply after every regen)
CubeMX overwrites hand-tuned code. After regenerating, re-apply the manual edits that were protecting:
USB configuration (usbd_conf / descriptors), the fast bare-metal SPI/DMA ADC read path, TCA GPIO-expander
reset blip (LINEAR only — active-low, non-blocking so the USB loop can't crash), and any trigger/capture logic.
Always diff against the pre-regen backup before trusting a fresh build.

## Triggering / capture modes
- Start trigger (external rising edge starts acquisition), internal-timer acquisition, and **stepped/burst** capture.
- Masking mode was removed — do not reintroduce it in firmware or API.
- Known limit: reliable up to ~50 kHz edge rate; above that edges get missed.

## Calibration & flashing
Authoritative guide: `calibration_gen/CALIBRATION_AND_FLASHING_GUIDE.md`. Generators in `calibration_gen/`:
`make_{ingaas,silicon}_{linear,log}_cal.py` and `..._from_calib_c.py`. See the /cal-image and /flash skills.
Flash addresses: firmware `0x08000000`, calibration `0x0800C000` (independent sectors — one never erases the other).

## Release
Bump `pyproject.toml` version → build → `twine upload dist/*` (config in ~/.pypirc). Docs on Read the Docs
are rebuilt manually; PyPI README points there rather than duplicating. See the /coredaq-py skill
(driver + release) and /coredaq-mcu (firmware build/flash/debug).

## Related work
- `F746_Port/` — porting the same firmware/driver to STM32F746 (USB HS→FS, adds RMII 100M Ethernet).
- coreSOM (~/Downloads/coreSOM) is the F746 SOM hardware; coreDAQ_rev5 is its carrier with AD7606C-16 ADC.
