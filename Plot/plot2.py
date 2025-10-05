import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# === 设置路径 ===
file_path = "data/scan_014/row_1_col_2.csv"  # 替换为你想分析的文件路径

# === 加载数据 ===
df = pd.read_csv(file_path, skiprows=1)
df.columns = ["Time (s)", "Amplitude (V)"]
time = df["Time (s)"].astype(float).values
amplitude = df["Amplitude (V)"].astype(float).values

# === 差分 + 自适应阈值 ===
diff_amp = np.abs(np.diff(amplitude))
adaptive_threshold = np.percentile(diff_amp, 90) * 0.5  # 取第90百分位的一半作为阈值
non_flat_indices = np.where(diff_amp > adaptive_threshold)[0]

# === 提取波形部分 ===
if len(non_flat_indices) > 0:
    buffer = 2  # 可根据需要调大调小
    start = max(0, non_flat_indices[0] - buffer)
    end = min(len(amplitude), non_flat_indices[-1] + buffer)
    selected_time = time[start:end]
    selected_amplitude = amplitude[start:end]

    # === 画图 ===
    plt.figure(figsize=(10, 4))
    plt.plot(selected_time, selected_amplitude, linewidth=2)
    plt.title(f"Filtered Waveform ({file_path.split('/')[-1]})")
    plt.xlabel("Time (s)")
    plt.ylabel("Amplitude (V)")
    plt.grid(True)
    plt.tight_layout()
    plt.show()

    print(f"✅ 波形成功提取：共 {end - start} 个点，阈值 ≈ {adaptive_threshold:.3f}")
else:
    print("⚠️ 无法识别非平坦区，可能数据过于平缓或完全一致。")
