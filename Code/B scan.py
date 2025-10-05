# ✅ Imports
import time
import socket
from pymeasure.instruments.agilent import Agilent33500
from pyvisa import ResourceManager

# === Custom modules ===
from Signal_function import Burst_generate
from Oscilloscope import create_scan_folder, read_oscilloscope_and_save, configure_oscilloscope_for_burst
from rig_function import send_command, enable_axis, wait_until_stopped

# ✅ Configuration import
from Setup import SIGNAL_PARAMS, SCAN_PARAMS, HOST, PORT, SG_ADDRESS, OSC_ADDRESS

# ✅ Connect to signal generator
sg = Agilent33500(SG_ADDRESS)
print("✅ Signal generator connected:", sg.id)

# ✅ Connect to oscilloscope
rm = ResourceManager()
osc = rm.open_resource(OSC_ADDRESS)
osc.timeout = 50000
print("✅ Oscilloscope connected:", osc.query("*IDN?"))

# ✅ Utility functions ================================================
def mm_to_pulse(mm):
    return int(mm * 700)

# ✅ Main program ====================================================

def trigger_and_acquire(sg, osc, cross, s, folder):
    Burst_generate(sg, **SIGNAL_PARAMS)
    print("Triggering signal generator...")
    time.sleep(1)
    read_oscilloscope_and_save(osc, cross, s, folder)
    time.sleep(0.5)
    print(f"📡 Data acquired: Row {cross+1}, Point {s}")


def main():
    # ✅ Convert dimensions from mm to pulses
    # SCAN_PARAMS["scan_length"] = mm_to_pulse(SCAN_PARAMS["scan_length"])
    # SCAN_PARAMS["scan_step"] = mm_to_pulse(SCAN_PARAMS["scan_step"])
    # SCAN_PARAMS["cross_length"] = mm_to_pulse(SCAN_PARAMS["cross_length"])
    # SCAN_PARAMS["cross_step"] = mm_to_pulse(SCAN_PARAMS["cross_step"])

    scan_length_mm = SCAN_PARAMS["scan_length"]
    cross_length_mm = SCAN_PARAMS["cross_length"]
    scan_points = SCAN_PARAMS["scan_points"]
    cross_points = SCAN_PARAMS["cross_points"]

    

    # Create data directory
    scan_folder = create_scan_folder()

    # Unpack scan axis parameters
    scan_axis = SCAN_PARAMS["scan_axis"]
    cross_axis = SCAN_PARAMS["cross_axis"]

    SCAN_PARAMS["scan_step"] = mm_to_pulse(scan_length_mm / (scan_points - 1))
    SCAN_PARAMS["cross_step"] = mm_to_pulse(cross_length_mm / (cross_points - 1))

    scan_steps = int(SCAN_PARAMS["scan_length"] / SCAN_PARAMS["scan_step"])
    cross_steps = int(SCAN_PARAMS["cross_length"] / SCAN_PARAMS["cross_step"]) + 1
    start_col = SCAN_PARAMS["start_col"]


    # Pre-sample one point
    configure_oscilloscope_for_burst(osc, SIGNAL_PARAMS)
    Burst_generate(sg, **SIGNAL_PARAMS)
    time.sleep(1)
    trigger_and_acquire(sg, osc, cross=0, s=1, folder=scan_folder)

    # Establish TCP control connection
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))
        send_command(sock, "INC")
        enable_axis(sock, scan_axis)
        enable_axis(sock, cross_axis)

        for cross in range(cross_steps):
            print(f"\n📏 Scanning row {cross+1}/{cross_steps}")
            scan_direction = -SCAN_PARAMS["scan_step"] if (cross + 1) % 2 == 0 else SCAN_PARAMS["scan_step"]

            for scan in range(scan_steps):
                s = (scan_steps - scan if (cross + 1) % 2 == 0 else scan + start_col) if cross != 0 else scan + start_col

                send_command(sock, f"{scan_axis}{scan_direction}")
                wait_until_stopped(sock, scan_axis)
                trigger_and_acquire(sg, osc, cross, s, scan_folder)

            # Move to next row
            if cross < cross_steps - 1:
                send_command(sock, f"{cross_axis}{SCAN_PARAMS['cross_step']}")
                wait_until_stopped(sock, cross_axis)
                s = scan_steps + 1 if (cross + 2) % 2 == 0 else 1
                trigger_and_acquire(sg, osc, cross + 1, s, scan_folder)

        print("\n✅ B Scan complete.")

    sg.shutdown()
    print("✅ All tasks finished.")

if __name__ == "__main__":
    main()
