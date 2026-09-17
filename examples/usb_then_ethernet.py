import time
from py_coreDAQ import coreDAQ

# --- 1. connect over USB (auto-finds the device on the USB cable) ---
daq = coreDAQ.connect()
print("USB:", daq.identify())

# --- 2. give it a fixed IP address (saved in the device's flash) ---
daq.set_ip_static("192.168.0.222", "255.255.255.0", "192.168.0.1")
print("IP config:", daq.ip_config())

# --- 3. done with USB ---
daq.close()

# --- 4. reconnect to the same device over Ethernet ---
time.sleep(2)                       # let the link come up at the new IP
net = coreDAQ.connect(transport="ethernet", host="192.168.0.222")
print("Ethernet:", net.identify())
print("temperature:", net.temperature(), "C")
net.close()
