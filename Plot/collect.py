import os
import re
import pandas as pd

# ⚙️ 信号参数定义
SIGNAL_PARAMS = {
    "shape": "SIN",
    "frequency": 10_000_000,  # 10 MHz
    "amplitude": 1.0,  # 1 V (peak to peak)
    "no_of_cycles_per_pulse": 10,
}


def standardize_waveform_data(
    input_folder="data/scan_013/filtered", output_folder="data/scan_013/filtered1"
):
    os.makedirs(output_folder, exist_ok=True)

    file_list = [
        f for f in os.listdir(input_folder) if re.match(r"row_\d+_col_\d+\.csv", f)
    ]

    for f in file_list:
        file_path = os.path.join(input_folder, f)
        try:
            df = pd.read_csv(file_path)

            if df.shape[1] != 2:
                print(f"⚠️ 跳过非法文件（列数不符）: {f}")
                continue

            # 原始时间、振幅
            time_s = df.iloc[:, 0].astype(float)
            raw_voltage = df.iloc[:, 1].astype(float)

            # 🎯 标准化时间：以 0 起点，单位 µs
            time_us = (time_s - time_s.iloc[0]) * 1e6

            # 🎯 标准化电压：根据 peak-to-peak = 1 V，中心 0V，范围 [-0.5V, +0.5V]
            v_min = raw_voltage.min()
            v_max = raw_voltage.max()
            scale = SIGNAL_PARAMS["amplitude"] / (v_max - v_min)
            voltage = (raw_voltage - (v_max + v_min) / 2) * scale

            # ⚡ 组织输出 DataFrame
            df_out = pd.DataFrame({"Time (µs)": time_us, "Voltage (V)": voltage})

            # 💾 保存
            output_path = os.path.join(output_folder, f)
            df_out.to_csv(output_path, index=False)
            print(f"✅ 标准化完成: {f}")

        except Exception as e:
            print(f"❌ 错误处理文件 {f}: {e}")


if __name__ == "__main__":
    standardize_waveform_data()
