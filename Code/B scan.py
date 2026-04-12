# Imports
import json
import time
import socket
from pathlib import Path
from pymeasure.instruments.agilent import Agilent33500
from pyvisa import ResourceManager

# === Custom modules ===
from Signal_function import Burst_generate
from Oscilloscope import (
    create_scan_folder,
    read_oscilloscope_and_save,
    configure_oscilloscope_for_burst,
)
from rig_function import send_command, enable_axis, wait_until_stopped

# Column index for the first loop-acquired scan point (pre-sample is always saved as col 1)
START_COL = 2

# === Load settings from GUI JSON config ===
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "gui_settings.json"


def load_settings():
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(f"Settings file not found: {SETTINGS_PATH}")
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()
_cfg = settings.get("config", {})
_ex = settings.get("excitation", {})
_bc = settings.get("b_mode", settings.get("bc_mode", {}))

SG_ADDRESS = _cfg.get("sg_address") or None
HOST = _cfg.get("host", "192.168.1.250")
PORT = int(_cfg.get("port", 5001))

SIGNAL_PARAMS = {
    "shape": _ex.get("shape", "SIN"),
    "frequency": float(_ex.get("frequency", 1_000_000)),
    "amplitude": float(_ex.get("amplitude", 1.0)),
    "no_of_cycles_per_pulse": float(_ex.get("no_of_cycles_per_pulse", 60)),
    "no_of_pulses": int(_ex.get("no_of_pulses", 1)),
    "prf": float(_ex.get("prf", 1000)),
    "window_type": _ex.get("window_type", "Hanning"),
}

SCAN_PARAMS = {
    "axis1": _bc.get("axis1", _bc.get("scan_axis", "X")),
    "axis2": _bc.get("axis2", _bc.get("cross_axis", "Z")),
    "axis1_length": float(_bc.get("axis1_length", _bc.get("scan_length", 20.0))),
    "axis1_points": int(_bc.get("axis1_points", _bc.get("scan_points", 2))),
    "axis2_length": float(_bc.get("axis2_length", _bc.get("cross_length", 20.0))),
    "axis2_points": int(_bc.get("axis2_points", _bc.get("cross_points", 2))),
}

# Connect to signal generator
sg = Agilent33500(SG_ADDRESS) if SG_ADDRESS else Agilent33500()
print("Signal generator connected:", sg.id)

# Connect to oscilloscope
rm = ResourceManager()
osc = rm.open_resource(_cfg.get("osc_address", ""))
osc.timeout = 50000
print("Oscilloscope connected:", osc.query("*IDN?"))


# Utility functions ================================================
def mm_to_pulse(mm):
    return int(mm * 700)


# Main program ====================================================


def trigger_and_acquire(sg, osc, cross, s, folder):
    Burst_generate(sg, **SIGNAL_PARAMS)
    print("Triggering signal generator...")
    time.sleep(1)
    read_oscilloscope_and_save(osc, cross, s, folder)
    time.sleep(0.5)
    print(f"Data acquired: Row {cross+1}, Point {s}")


def main():
    axis1_length_mm = SCAN_PARAMS["axis1_length"]
    axis2_length_mm = SCAN_PARAMS["axis2_length"]
    axis1_points = SCAN_PARAMS["axis1_points"]
    axis2_points = SCAN_PARAMS["axis2_points"]

    # Create data directory
    scan_folder = create_scan_folder()

    # Unpack scan axis parameters
    axis1 = SCAN_PARAMS["axis1"]
    axis2 = SCAN_PARAMS["axis2"]

    scan_step = mm_to_pulse(axis1_length_mm / (axis1_points - 1))
    axis2_step = mm_to_pulse(axis2_length_mm / (axis2_points - 1))

    axis1_steps = axis1_points - 1
    axis2_steps = axis2_points
    start_col = START_COL

    # Pre-sample one point
    configure_oscilloscope_for_burst(osc, SIGNAL_PARAMS)
    Burst_generate(sg, **SIGNAL_PARAMS)
    time.sleep(1)
    trigger_and_acquire(sg, osc, cross=0, s=1, folder=scan_folder)

    # Establish TCP control connection
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((HOST, PORT))
        send_command(sock, "INC")
        enable_axis(sock, axis1)
        enable_axis(sock, axis2)

        for cross in range(axis2_steps):
            print(f"\nScanning row {cross+1}/{axis2_steps}")
            scan_direction = -scan_step if (cross + 1) % 2 == 0 else scan_step

            for scan in range(axis1_steps):
                s = (
                    (axis1_steps - scan if (cross + 1) % 2 == 0 else scan + start_col)
                    if cross != 0
                    else scan + start_col
                )

                send_command(sock, f"{axis1}{scan_direction}")
                wait_until_stopped(sock, axis1)
                trigger_and_acquire(sg, osc, cross, s, scan_folder)

            # Move to next row
            if cross < axis2_steps - 1:
                send_command(sock, f"{axis2}{axis2_step}")
                wait_until_stopped(sock, axis2)
                s = axis1_steps + 1 if (cross + 2) % 2 == 0 else 1
                trigger_and_acquire(sg, osc, cross + 1, s, scan_folder)

        print("\nB Scan complete.")

    sg.shutdown()
    print("All tasks finished.")


if __name__ == "__main__":
    main()
