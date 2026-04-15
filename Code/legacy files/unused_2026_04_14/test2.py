import os
import time
import numpy as np
import pandas as pd
import pyvisa
from pathlib import Path

# === 配置区 ===
OSC_ADDRESS = "USB0::0x05FF::0x1023::3557N06479::INSTR"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# === 获取数据函数 ===
def read_oscilloscope_display():
    rm = pyvisa.ResourceManager()
    osc = rm.open_resource(OSC_ADDRESS)
    osc.timeout = 5000
    print(f"📡 Connected to: {osc.query('*IDN?').strip()}")

    # 设置采集模式
    osc.write("TRIG_MODE NORM")
    time.sleep(1)

    # 读取波形
    raw_data = osc.query_binary_values("C1:WF? DAT1", datatype="B", container=np.array)
    scale = float(osc.query("C1:VDIV?").strip().split(" ")[1])
    v_offset = float(osc.query("C1:OFST?").strip().split(" ")[1])
    scale1 = 1 / 30
    voltages = ((raw_data - 128) * scale + v_offset - 128) * scale1

    time_div = float(osc.query("TDIV?").strip().split(" ")[1])
    num_points = len(raw_data)
    time_values = np.linspace(0, num_points * time_div, num_points)

    # 保存数据
    filename = DATA_DIR / "osc_data.csv"
    df = pd.DataFrame({"Time (s)": time_values, "Amplitude (V)": voltages})
    df.to_csv(filename, index=False)
    print(f"✅ Data saved to: {filename}")

    osc.close()

if __name__ == "__main__":
    read_oscilloscope_display()
