import pandas as pd
import matplotlib.pyplot as plt
import os
import re

# 设置目标扫描文件夹路径
scan_folder = "data/scan_008"

# 获取所有符合 row_x_col_y.csv 命名的文件
file_list = [f for f in os.listdir(scan_folder) if re.match(r"row_\d+_col_\d+\.csv", f)]

# 提取最大行列数用于子图排布
positions = [re.findall(r"\d+", f) for f in file_list]
rows = max(int(p[0]) for p in positions)
cols = max(int(p[1]) for p in positions)

fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3 * rows), sharex=True, sharey=True)

for f in file_list:
    match = re.findall(r"\d+", f)
    row_idx = int(match[0]) - 1
    col_idx = int(match[1]) - 1
    file_path = os.path.join(scan_folder, f)

    df = pd.read_csv(file_path)
    ax = axes[row_idx, col_idx] if rows > 1 and cols > 1 else (
        axes[col_idx] if rows == 1 else axes[row_idx]
    )
    ax.plot(df["Time (s)"], df["Amplitude (V)"], lw=1)
    ax.set_title(f"Row {row_idx+1}, Col {col_idx+1}")
    ax.grid(True)

# 添加全局 x 和 y 标签
fig.text(0.5, 0.04, 'Time (s)', ha='center', fontsize=12)
fig.text(0.04, 0.5, 'Amplitude (V)', va='center', rotation='vertical', fontsize=12)


plt.suptitle(f"Scan Result: {scan_folder}", fontsize=16)
# plt.tight_layout()
plt.tight_layout(rect=[0.05, 0.05, 1, 0.95])  # 留出标签和标题空间
plt.show()
