import pyvisa
import time

# Replace with your actual VISA address from the detection script
VISA_ADDRESS = 'USB0::2391::11015::MY52701391::0::INSTR' #'USB0::2391::1031::MY44055132::0::INSTR'  # e.g., 'USB0::2391::12345::INSTR'

# Sine wave parameters
FREQUENCY_HZ = 1000      # 1 kHz
AMPLITUDE_V = 2.0        # 2 Vpp
OFFSET_V = 0.0           # 0 V offset

rm = pyvisa.ResourceManager('@py')

try:
    print(f"Connecting to {VISA_ADDRESS} ...")
    inst = rm.open_resource(VISA_ADDRESS, timeout=2000)
    print("*IDN? ->", inst.query("*IDN?").strip())

    # Reset and configure for sine wave
    inst.write('*RST')
    inst.write('FUNC SIN')
    inst.write(f'FREQ {FREQUENCY_HZ}')
    inst.write(f'VOLT {AMPLITUDE_V}')
    inst.write(f'VOLT:OFFS {OFFSET_V}')
    inst.write('OUTP ON')
    print(f"Sine wave output: {FREQUENCY_HZ} Hz, {AMPLITUDE_V} Vpp, {OFFSET_V} V offset")
    time.sleep(2)  # Output for 2 seconds
    inst.write('OUTP OFF')
    print("Output turned off.")
    inst.close()
except Exception as e:
    print(f"Error: {e}")
