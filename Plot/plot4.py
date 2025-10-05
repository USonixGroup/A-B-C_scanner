import pandas as pd
import matplotlib.pyplot as plt
import os
import re

# 设置你的目标文件夹
filtered_folder = "data/scan_013/filtered1"

# 获取所有 row_x_col_y.csv 文件
file_list = [f for f in os.listdir(filtered_folder) if re.match(r"row_\d+_col_\d+\.csv", f)]

# 提取最大行列数
positions = [re.findall(r"\d+", f) for f in file_list]
rows = max(int(p[0]) for p in positions)
cols = max(int(p[1]) for p in positions)

# 创建子图
fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3 * rows), sharex=True, sharey=True)

# 保证 axes 是二维索引结构
if rows == 1 and cols == 1:
    axes = [[axes]]
elif rows == 1:
    axes = [axes]
elif cols == 1:
    axes = [[ax] for ax in axes]

# 绘图
for f in file_list:
    match = re.findall(r"\d+", f)
    row_idx = int(match[0]) - 1
    col_idx = int(match[1]) - 1
    file_path = os.path.join(filtered_folder, f)

    df = pd.read_csv(file_path)
    ax = axes[row_idx][col_idx]
    if not df.empty:
        ax.plot(df["Time (µs)"], df["Voltage (V)"], lw=1)
    ax.set_title(f"Row {row_idx+1}, Col {col_idx+1}")
    ax.grid(True)

plt.suptitle("Filtered Waveforms from 'filtered1'", fontsize=16)
plt.tight_layout(rect=[0, 0.03, 1, 0.95])
plt.show()
