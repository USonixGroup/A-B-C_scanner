from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import hilbert
import os
import re

def extract_envelope_max_matrix(folder_path: str) -> np.ndarray:
    """
    从指定文件夹读取所有 row_X_col_Y.csv 文件，提取 Hilbert envelope 最大值并组织成矩阵。

    参数:
        folder_path: 包含形如 row_X_col_Y.csv 文件的目录路径

    返回:
        envelope_matrix: 以 (row, col) 组织的 envelope 最大值矩阵
    """
    file_list = [f for f in os.listdir(folder_path) if re.match(r"row_\d+_col_\d+\.csv", f)]

    if not file_list:
        raise ValueError("No matching CSV files found in the specified directory.")

    # 提取最大行列数
    positions = [re.findall(r"\d+", f) for f in file_list]
    rows = max(int(p[0]) for p in positions)
    cols = max(int(p[1]) for p in positions)

    # 初始化矩阵
    envelope_matrix = np.zeros((rows, cols))

    for f in file_list:
        match = re.findall(r"\d+", f)
        row_idx = int(match[0]) - 1
        col_idx = int(match[1]) - 1
        file_path = os.path.join(folder_path, f)

        try:
            df = pd.read_csv(file_path, skiprows=2, header=None)
            signal = df[1].astype(float).values
            envelope = np.abs(hilbert(signal))
            max_env = envelope.max()
            envelope_matrix[row_idx, col_idx] = max_env
        except Exception as e:
            print(f"Error processing {f}: {e}")

    return envelope_matrix

def plot_envelope_matrix(matrix: np.ndarray, title: str = "Envelope Max Value Heatmap (Normalized)") -> None:
    """
    绘制 envelope 最大值的热力图（灰度图，自动归一化处理）。

    参数:
        matrix: 2D numpy 数组，包含 envelope 最大值
        title: 图像标题
    """
    # 自动归一化
    norm_matrix = (matrix - np.min(matrix)) / (np.max(matrix) - np.min(matrix) + 1e-12)

    plt.imshow(norm_matrix, cmap='gray', interpolation='nearest')
    plt.title(title)
    plt.xlabel("Column Index")
    plt.ylabel("Row Index")
    plt.colorbar(label="Normalized Envelope Max")
    plt.show()

# 示例调用
matrix = extract_envelope_max_matrix("data/scan_013/filtered1")
plot_envelope_matrix(matrix)
