import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 设置目标文件路径
file_path = "data/scan_013/row_2_col_2.csv"

# 读取数据（跳过标题行）
df = pd.read_csv(file_path, skiprows=1)
df.columns = ["Time (s)", "Amplitude (V)"]

# 转换为数组
time = df["Time (s)"].astype(float).values
amplitude = df["Amplitude (V)"].astype(float).values

# 差分检测非平坦区域
diff_amp = np.abs(np.diff(amplitude))
flat_threshold = 1.0  # 可调整
non_flat_indices = np.where(diff_amp > flat_threshold)[0]

# 若有有效波形就截取
if len(non_flat_indices) > 0:
    buffer = 2
    start = max(0, non_flat_indices[0] - buffer)
    end = min(len(amplitude), non_flat_indices[-1] + buffer)
    selected_time = time[start:end]
    selected_amplitude = amplitude[start:end]

    # 只画筛选过后的波形
    plt.figure(figsize=(10, 4))
    plt.plot(selected_time, selected_amplitude, linewidth=2)
    plt.title("Filtered Waveform Only")
    plt.xlabel("Time (s)")
    plt.ylabel("Amplitude (V)")
    plt.grid(True)
    plt.tight_layout()
    plt.show()
else:
    print("⚠️ 没有检测到非平坦波形。")
