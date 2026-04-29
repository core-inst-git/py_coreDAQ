# Quickstart

## Connect and Read Power

```python
from coredaq import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    info = meter.device_info()
    print(info.raw_idn)
    print(info.frontend, info.detector)

    meter.set_wavelength_nm(1550.0)
    reading = meter.read_channel1(autorange=True)

    print(reading.value, reading.unit)
    print(reading.power_w, "W")
    print(reading.power_dbm, "dBm")
```

## Read All Four Channels

```python
from coredaq import coreDAQ

with coreDAQ("/dev/tty.usbmodemXXXX") as meter:
    meter.set_reading_unit("dbm")
    readings = meter.read_all()
    for reading in readings:
        print(reading.channel, reading.value, reading.unit)
```

## Change Units Temporarily

```python
reading_w = meter.read_channel1()
reading_v = meter.read_channel1(unit="v")
reading_adc = meter.read_channel1(unit="adc")
```

The per-call `unit=` override does not change the default unit stored by `set_reading_unit(...)`.
