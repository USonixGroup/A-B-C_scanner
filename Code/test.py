import time
from pymeasure.instruments.agilent import Agilent33500
from pyvisa import ResourceManager
from Setup import SIGNAL_PARAMS, SCAN_PARAMS, HOST, PORT, SG_ADDRESS, OSC_ADDRESS
from Signal_function import Burst_generate
import numpy as np
import os
from const import data_dir
import pandas as pd

# ✅ Connect to signal generator
# sg = Agilent33500(SG_ADDRESS)
# print("✅ Signal generator connected:", sg.id)

OSC_ADDRESS =  "TCPIP0::192.168.1.200::INSTR"

def configure_oscilloscope_for_burst(osc):
    def vbs(osc, cmd):
        osc.write(f"VBS '{cmd}'")
        time.sleep(0.1)

    # freq = SIGNAL_PARAMS["frequency"]
    # amp = SIGNAL_PARAMS["amplitude"]
    # n_cycles = SIGNAL_PARAMS["no_of_cycles_per_pulse"]

    # burst_duration = n_cycles / freq
    # y = 4
    # hor_scale = burst_duration/5  #*y
    # ver_scale = 0.01#amp/10000
    # # var = 12500000 / freq #1250000000
    # # Sampling_Rate = freq*var
    # Sampling_Rate = 50e6#freq*1000 # 50e6

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

    # osc.write("SINGLE")


if __name__ == "__main__":
    rm = ResourceManager()
    try:
        osc = rm.open_resource(OSC_ADDRESS)
        osc.timeout = 50000
        print("✅ Connected to:", osc.query("*IDN?"))
        configure_oscilloscope_for_burst(osc)
        time.sleep(1)
        # Burst_generate(sg, **SIGNAL_PARAMS)
        print("Triggering signal generator...")
        time.sleep(1)
        osc.write("C1:WF? DAT1")
        raw_data = osc.query_binary_values("C1:WF? DAT1", datatype="B", container=np.array)
        print(raw_data)

        scale = float(osc.query("C1:VDIV?").strip().split(" ")[1])
        v_offset = float(osc.query("C1:OFST?").strip().split(" ")[1])
        scale1 = 1/30
        voltages = ((raw_data - 128) * scale + v_offset - 128) * scale1

        time_div = float(osc.query("TDIV?").strip().split(" ")[1])
        num_points = len(raw_data)
        time_span = 10 * time_div  # 总时间范围（10格）
        # dt = time_span / num_points  # 每个点的时间间隔
        time_values = np.linspace(0, time_span, num_points, endpoint=False)

        filename = f"test.csv"
        scan_folder = os.path.join(data_dir, 'data')
        file_path = os.path.join(data_dir, filename)
        df = pd.DataFrame({"Time (s)": time_values, "Amplitude (V)": voltages})
        df.to_csv(file_path, index=False)
        print(f"Data saved to: {file_path}")

    except Exception as e:
        print("Error:", e)
    finally:
        if "osc" in locals():
            osc.close()
            print("Connection closed.")
