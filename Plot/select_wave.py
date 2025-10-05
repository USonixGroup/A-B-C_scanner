import pandas as pd
import numpy as np
import os
import re

# 设置目标扫描文件夹路径
scan_folder = "data/scan_009"
output_folder = os.path.join(scan_folder, "filtered")
os.makedirs(output_folder, exist_ok=True)

# 获取所有符合 row_x_col_y.csv 命名的文件
file_list = [f for f in os.listdir(scan_folder) if re.match(r"row_\d+_col_\d+\.csv", f)]

# def extract_non_flat_waveform(df, std_window=10, std_thresh=0.03, buffer=10):
#     time = df["Time (s)"].astype(float).values
#     amp = df["Amplitude (V)"].astype(float).values

#     # 计算移动标准差
#     rolling_std = pd.Series(amp).rolling(std_window, center=True).std().fillna(0).values

#     # 找到活动区域索引
#     active_indices = np.where(rolling_std > std_thresh)[0]
#     if len(active_indices) == 0:
#         return pd.DataFrame(columns=["Time (s)", "Amplitude (V)"])  # 无有效波形

#     # 获取范围
#     start = max(0, active_indices[0] - buffer)
#     end = min(len(amp), active_indices[-1] + buffer)

#     return pd.DataFrame({
#         "Time (s)": time[start:end],
#         "Amplitude (V)": amp[start:end]
#     })

# def extract_non_flat_waveform(df, std_window=10, std_thresh=0.03, buffer=10):
#     time = df["Time (s)"].astype(float).values
#     amp = df["Amplitude (V)"].astype(float).values

#     # 计算移动标准差
#     rolling_std = pd.Series(amp).rolling(std_window, center=True).std().fillna(0).values

#     # 找到活动区域索引
#     active_indices = np.where(rolling_std > std_thresh)[0]
#     if len(active_indices) == 0:
#         return pd.DataFrame(columns=["Time (s)", "Amplitude (V)"])  # 无有效波形

#     # 获取范围
#     start = max(0, active_indices[0] - buffer)
#     end = min(len(amp), active_indices[-1] + buffer)

#     time_cut = time[start:end]
#     amp_cut = amp[start:end]

#     # 尝试找到第一个上升沿的时间点（简单用一阶差分）
#     diff_amp = np.diff(amp_cut)
#     rise_idx = np.argmax(diff_amp > np.max(diff_amp) * 0.5)  # 找最大上升斜率位置的一半为阈值
#     time_zero = time_cut[rise_idx] if rise_idx > 0 else time_cut[0]

#     time_aligned = time_cut - time_zero  # 使时间以上升点为零点

#     return pd.DataFrame({
#         "Time (s)": time_aligned,
#         "Amplitude (V)": amp_cut
#     })

def extract_non_flat_waveform(df, std_window=10, std_thresh=0.03, buffer=10):
    time = df["Time (s)"].astype(float).values
    amp = df["Amplitude (V)"].astype(float).values

    # 计算移动标准差
    rolling_std = pd.Series(amp).rolling(std_window, center=True).std().fillna(0).values

    # 找到活动区域索引
    active_indices = np.where(rolling_std > std_thresh)[0]
    if len(active_indices) == 0:
        return pd.DataFrame(columns=["Time (s)", "Amplitude (V)"])  # 无有效波形

    # 获取范围
    start = max(0, active_indices[0] - buffer)
    end = min(len(amp), active_indices[-1] + buffer)

    time_cut = time[start:end]
    amp_cut = amp[start:end]

    # 用第11个点（索引10）的时间作为新的零点
    if len(time_cut) > 10:
        time_zero = time_cut[10]
    else:
        time_zero = time_cut[0]  # 防止样本太短报错

    time_aligned = time_cut - time_zero

    return pd.DataFrame({
        "Time (s)": time_aligned,
        "Amplitude (V)": amp_cut
    })




# 处理并保存
for f in file_list:
    file_path = os.path.join(scan_folder, f)
    df = pd.read_csv(file_path, skiprows=1)
    df.columns = ["Time (s)", "Amplitude (V)"]
    filtered_df = extract_non_flat_waveform(df)
    filtered_df.to_csv(os.path.join(output_folder, f), index=False)
