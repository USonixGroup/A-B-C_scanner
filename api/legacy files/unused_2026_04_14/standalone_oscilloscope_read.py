import pyvisa
import time

# Set your VISA address here (copy from your working get_hardware.py output)
VISA_ADDRESS = "TCPIP0::192.168.1.200::INSTR"

# Connect to oscilloscope and print *IDN?
rm = pyvisa.ResourceManager('@py')
print(f"Connecting to: {VISA_ADDRESS}")
try:
    scope = rm.open_resource(VISA_ADDRESS, timeout=5000)
    print("Connected!")
    print("*IDN?:", scope.query("*IDN?").strip())

    # Try to read waveform from all active channels (C1, C2, C3, C4)
    for ch in range(1, 5):
        try:
            # Check if channel is on (for LeCroy/Keysight, adapt as needed)
            state = scope.query(f"C{ch}:TRACE? STATE").strip()
            if state in ("ON", "1", "True", "TRUE"):
                print(f"Reading waveform from C{ch}...")
                # Standard SCPI waveform readout (adapt for your scope)
                scope.write(f"DATA:SOURCE C{ch}")
                scope.write("DATA:ENCdg ASCii")
                scope.write("DATA:WIDTH 1")
                scope.write("DATA:START 1")
                scope.write("DATA:STOP 1000")
                data = scope.query("CURVE?")
                print(f"C{ch} data (first 100 chars):", data[:100])
            else:
                print(f"C{ch} is OFF or not available.")
        except Exception as e:
            print(f"Could not read C{ch}: {e}")
    scope.close()
except Exception as e:
    print(f"Failed to connect or read: {e}")
