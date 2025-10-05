# Signal Generator Configuration
import time

def Continuous_generate(
    sg, 
    shape=None, 
    frequency=None, 
    amplitude=None, 
    offset=None,
    phase=None,
):   
    # 1. Restore continuous mode
    sg.burst_state = False
    sg.arb_advance = "SRAT"  # Set to sample rate advance mode (continuous playback)
    sg.trigger_source = "IMM"  # Set to immediate trigger mode (no external trigger)
    
    # 2. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if offset:
        sg.offset = offset
    if phase:
        sg.phase = phase  

    sg.output = True

    print("✅ Signal generator set to continuous mode.")
    print(f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V, 
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees""")


def Trigger_generate(
    sg, 
    shape="None",
    frequency=None, 
    amplitude=None, 
    number_of_trigger=None,
    offset=None,
    phase=None
):
    # 1. Set mode to triggered output
    sg.arb_advance = "TRIG"
    sg.burst_state = False
    sg.trigger_source = "BUS"
    
    # 2. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if offset:
        sg.offset = offset
    if phase:
        sg.phase = phase 

    sg.output = True

    for i in range(number_of_trigger):
        sg.trigger()
        sg.wait_for_trigger(timeout=10)
        sg.beep()  

    print("✅ Signal generator executed triggered mode.")
    print(f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V, 
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees""")


def Burst_generate(
    sg,
    shape="None",
    frequency=None, 
    amplitude=None, 
    burst_ncycles=None,
    number_of_burst=None,
    offset=None,
    phase=None
):
    # 1. Set burst mode
    sg.burst_mode = "TRIG"
    sg.burst_state = True
    sg.trigger_source = "BUS"
    
    # 2. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if burst_ncycles:
        sg.burst_ncycles = burst_ncycles
    if offset:
        sg.offset = offset
    if phase:
        sg.phase = phase 

    # Estimate burst period based on cycles and frequency
    burst_period = burst_ncycles / frequency if frequency and burst_ncycles else 1

    sg.output = True
    # time.sleep(1)

    if not number_of_burst:
        number_of_burst = 1  # Default to one trigger if not specified

    for i in range(number_of_burst):
        sg.trigger()
        sg.wait_for_trigger(timeout=burst_period)
        sg.beep()

    print("✅ Signal generator set to burst mode.")
    print(f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V,
Burst cycles: {sg.burst_ncycles},
Number of bursts: {number_of_burst}, 
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees""")


# Prompt to stop signal generation
def stop_condition():
    input("Press Enter to stop signal generation...")
    return True

# Stop output
def stop_output(sg):
    stop_condition()
    sg.output = False
    print("⚠️ Signal output stopped.")

# Wait for trigger (blocking)
def stop_trigger(sg):
    sg.wait_for_trigger()

# Wait for burst to complete or manual stop
def stop_burst(sg, burst_period):
    sg.wait_for_trigger(timeout=burst_period, should_stop=stop_condition)
