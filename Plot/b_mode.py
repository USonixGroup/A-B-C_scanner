import os
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import hilbert

# 读取 CSV 文件中的数值信号（自动跳过非数值列）
def load_numeric_signal(filepath):
    df = pd.read_csv(filepath)
    numeric_cols = df.select_dtypes(include=[np.number])  # 选择数值列
    if numeric_cols.shape[1] == 0:
        # 如果没有明确的数值列，尝试强制转换
        numeric_cols = df.apply(pd.to_numeric, errors='coerce').dropna(axis=1, how='all')
    return numeric_cols.iloc[:, 1].dropna().values  # 返回第一列有效数值数据

# 从文件名中解析 row 和 col 的编号（如 row_3_col_4 → (3, 4)）
def parse_row_col(filename):
    match = re.search(r'row_(\d+)_col_(\d+)', filename)
    return int(match.group(1)), int(match.group(2)) if match else (None, None)

# 构建能量矩阵，每个元素是对应位置的波形包络积分值
def build_energy_matrix(folder_path):
    energy_values = {}  # 保存位置对应的能量值
    max_row, max_col = 0, 0  # 用于确定矩阵大小

    for filename in os.listdir(folder_path):
        if filename.endswith('.csv') and 'row_' in filename and 'col_' in filename:
            row, col = parse_row_col(filename)
            signal = load_numeric_signal(os.path.join(folder_path, filename))

            # 需要的所有统计量
            envelope = np.abs(hilbert(signal))  # Hilbert 变换计算包络
            rms = np.sqrt(np.mean(signal**2))
            envelope_sum = np.sum(envelope)
            envelope_max = np.max(envelope)
            signal_variance = np.var(signal)
            energy = np.sum(envelope)  # 计算包络积分（总能量）


            energy_values[(row, col)] = rms
            max_row = max(max_row, row)
            max_col = max(max_col, col)

    # 初始化能量矩阵，大小由最大 row/col 决定
    matrix = np.zeros((max_row, max_col))
    for (row, col), energy in energy_values.items():
        matrix[row - 1, col - 1] = energy  # 将 row/col 转为从 0 开始的索引

    return matrix

def build_max_envelope_matrix(folder_path):
    values = {}
    max_row, max_col = 0, 0

    for filename in os.listdir(folder_path):
        if filename.endswith('.csv') and 'row_' in filename and 'col_' in filename:
            row, col = parse_row_col(filename)
            signal = load_numeric_signal(os.path.join(folder_path, filename))
            envelope = np.abs(hilbert(signal))
            envelope_max = np.max(envelope)  # ✅ 使用最大包络值

            values[(row, col)] = envelope_max
            max_row = max(max_row, row)
            max_col = max(max_col, col)

    matrix = np.zeros((max_row, max_col))
    for (row, col), val in values.items():
        matrix[row - 1, col - 1] = val
    return matrix

# 自动将最小值/最大值取整到最近的整十数，用于统一灰度映射
def auto_round_limits(matrix):
    raw_min = matrix.min()
    raw_max = matrix.max()
    return np.floor(raw_min / 10) * 10, np.ceil(raw_max / 10) * 10

# 将能量值矩阵归一化并映射为 0~255 的灰度整数
def normalize_to_grayscale(matrix, min_val, max_val):
    norm = (matrix - min_val) / (max_val - min_val)  # 线性归一化
    norm = np.clip(norm, 0, 1)  # 限制在 [0, 1]
    return np.round(norm * 255).astype(int)  # 映射为整数灰度值

# 绘制 B-mode 图像（灰度图），并可选保存到文件
def plot_bmode(grayscale_matrix, min_val, max_val, save_path=None, show_labels=True, title="B-mode Image"):
    fig, ax = plt.subplots(figsize=(6, 6))
    cax = ax.imshow(grayscale_matrix, cmap='gray', origin='upper')  # 显示灰度图
    fig.colorbar(cax, label=f'Grayscale (Mapped {int(min_val)}–{int(max_val)})')  # 添加颜色条
    ax.set_title(title)  # ✅ 使用传入的标题
    ax.set_xlabel("Scan Column")
    ax.set_ylabel("Scan Row")
    ax.set_xticks(np.arange(grayscale_matrix.shape[1]))
    ax.set_yticks(np.arange(grayscale_matrix.shape[0]))

    # 在每个像素格中添加灰度值标签（可选）
    if show_labels:
        for i in range(grayscale_matrix.shape[0]):
            for j in range(grayscale_matrix.shape[1]):
                ax.text(j, i, f"{grayscale_matrix[i, j]}", va='center', ha='center', color='red', fontsize=10)

    plt.tight_layout()
    # 如果启用保存图像，请取消注释下行代码
    # if save_path:
    #     plt.savefig(save_path, dpi=300)
    plt.show()  # 显示图像窗口

def generate_bmode_from_folder(folder_path, scan_name="unknown", save_path=None):
    energy_matrix = build_energy_matrix(folder_path)
    min_val, max_val = auto_round_limits(energy_matrix)
    grayscale_matrix = normalize_to_grayscale(energy_matrix, min_val, max_val)
    plot_bmode(grayscale_matrix, min_val, max_val, save_path=save_path,
           title=f"B-mode Image (RMS Energy) - {scan_name}")

    # ✅ 添加 envelope_max 的图像
    max_env_matrix = build_max_envelope_matrix(folder_path)
    min_env, max_env = auto_round_limits(max_env_matrix)
    grayscale_env = normalize_to_grayscale(max_env_matrix, min_env, max_env)
    plot_bmode(grayscale_env, min_env, max_env, save_path=None, show_labels=False,
           title=f"B-mode Image (Envelope Max) - {scan_name}")


# # 从单个文件夹生成并显示 B-mode 图像
# def generate_bmode_from_folder(folder_path, save_path=None):
#     energy_matrix = build_energy_matrix(folder_path)
#     min_val, max_val = auto_round_limits(energy_matrix)
#     grayscale_matrix = normalize_to_grayscale(energy_matrix, min_val, max_val)
#     plot_bmode(grayscale_matrix, min_val, max_val, save_path=save_path)

# 自动处理 base_path 下所有 scan_xxx/filtered 文件夹，逐个生成 B-mode 图像
def process_all_scans(base_path="data", show_labels=True):
    scan_folders = [f for f in os.listdir(base_path) if f.startswith("scan_")]  # 找出所有 scan_xxx 文件夹

    for scan_name in scan_folders:
        scan_dir = os.path.join(base_path, scan_name)
        filtered_path = os.path.join(scan_dir, "filtered")

        # 检查 filtered/ 是否存在，并包含 .csv 文件
        if not os.path.isdir(filtered_path):
            print(f"[跳过] {scan_name}: 没有 filtered 文件夹")
            continue

        csv_files = [f for f in os.listdir(filtered_path) if f.endswith(".csv")]
        if not csv_files:
            print(f"[跳过] {scan_name}: filtered 目录中无 .csv 文件")
            continue

        print(f"[处理中] {scan_name}/filtered/")
        save_path = os.path.join(scan_dir, f"bmode_{scan_name}.png")  # 保存图像路径（未启用）
        generate_bmode_from_folder(filtered_path, scan_name=scan_name, save_path=save_path)  # 生成并显示图像


# example 用法（运行时取消注释）
# from b_mode import process_all_scans
process_all_scans(base_path="data")  # 会自动处理 data/scan_xxx/filtered 中的所有扫描
