
# --- Robust LeCroy HDO6054 LAN/LXI Waveform Readout for ALL Channels ---
import pyvisa
import time



import time
from pyvisa import ResourceManager
import numpy as np
import os
import pandas as pd

# You may need to adjust these imports/paths as needed
try:
    from Setup import SIGNAL_PARAMS
    from const import data_dir
except ImportError:
    SIGNAL_PARAMS = {"frequency": 1e6, "amplitude": 2.0, "no_of_cycles_per_pulse": 5}
    data_dir = os.getcwd()

OSC_ADDRESS = "TCPIP0::192.168.1.200::INSTR"

def configure_oscilloscope_for_burst(osc):
    def vbs(osc, cmd):
        osc.write(f"VBS '{cmd}'")
        time.sleep(0.1)

    freq = SIGNAL_PARAMS["frequency"]
    amp = SIGNAL_PARAMS["amplitude"]
    n_cycles = SIGNAL_PARAMS["no_of_cycles_per_pulse"]

    burst_duration = n_cycles / freq
    hor_scale = burst_duration
    ver_scale = amp / 4
    Sampling_Rate = freq * 100

    vbs(osc, f"app.Acquisition.Horizontal.HorScale = {hor_scale}")
    vbs(osc, f"app.Acquisition.C1.VerScale = {ver_scale}")
    vbs(osc, f"app.Acquisition.Horizontal.SampleRate = {Sampling_Rate}")
    vbs(osc, "app.Acquisition.C1.Offset = 0")
    vbs(osc, "app.Acquisition.C1.View = true")
    vbs(osc, 'app.Acquisition.Trigger.Source = "C1"')
    osc.write("TRIG_MODE NORM")

if __name__ == "__main__":
    rm = ResourceManager()
    try:
        osc = rm.open_resource(OSC_ADDRESS)
        osc.timeout = 50000
        print("✅ Connected to:", osc.query("*IDN?"))
        configure_oscilloscope_for_burst(osc)
        time.sleep(1)
        print("Triggering signal generator...")
        time.sleep(1)

        for ch in range(1, 5):
            chan = f"C{ch}"
            try:
                print(f"\nRequesting {chan} waveform ({chan}:WF? DAT1)...")
                osc.write(f"{chan}:WF? DAT1")
                raw_data = osc.query_binary_values(f"{chan}:WF? DAT1", datatype="B", container=np.array)
                print(f"{chan} raw data: ", raw_data)

                scale = float(osc.query(f"{chan}:VDIV?").strip().split(" ")[1])
                v_offset = float(osc.query(f"{chan}:OFST?").strip().split(" ")[1])
                scale1 = 1/30
                voltages = ((raw_data - 128) * scale + v_offset - 128) * scale1

                time_div = float(osc.query("TDIV?").strip().split(" ")[1])
                num_points = len(raw_data)
                time_span = 10 * time_div
                time_values = np.linspace(0, time_span, num_points, endpoint=False)

                filename = f"test_{chan}.csv"
                scan_folder = os.path.join(data_dir, 'data')
                os.makedirs(scan_folder, exist_ok=True)
                file_path = os.path.join(scan_folder, filename)
                df = pd.DataFrame({"Time (s)": time_values, "Amplitude (V)": voltages})
                df.to_csv(file_path, index=False)
                print(f"✅ {chan} Data saved to: {file_path}")
            except Exception as e:
                print(f"❌ {chan} Error: {e}")

    except Exception as e:
        print("❌ Error:", e)
    finally:
        if "osc" in locals():
            osc.close()
            print("✅ Connection closed.")
