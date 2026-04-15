import os
import re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import hilbert


def parse_row_col(filename_stem):
    """从文件名中提取 row 和 col 编号。"""
    match = re.match(r"row_(\d+)_col_(\d+)", filename_stem)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def load_scan_folder(scan_id, base_folder="data"):
    """
    读取某个 scan 文件夹下的所有 CSV 文件，构建三维波形数组。
    返回 shape = (rows, cols, min_length)
    """
    folder = Path(base_folder) / f"scan_{scan_id:03d}"
    files = sorted(folder.glob("row_*_col_*.csv"))

    scan_map = {}
    max_row, max_col = 0, 0
    min_length = np.inf

    for file in files:
        row, col = parse_row_col(file.stem)
        if row is None or col is None:
            continue

        max_row = max(max_row, row)
        max_col = max(max_col, col)

        df = pd.read_csv(file)
        if "Amplitude (V)" in df.columns:
            series = df["Amplitude (V)"]
        else:
            series = pd.to_numeric(df.iloc[:, 0], errors='coerce').dropna()

        values = series.values
        min_length = min(min_length, len(values))
        scan_map[(row - 1, col - 1)] = values  # 从 0 开始计数

    data_array = np.zeros((max_row, max_col, min_length))
    for (r, c), waveform in scan_map.items():
        data_array[r, c, :] = waveform[:min_length]

    return data_array


def build_b_mode_image(data_array):
    """
    使用 Hilbert 变换提取 envelope 并返回最大值图像（标准 B-mode）。
    返回 shape = (rows, cols)
    """
    envelope = np.abs(hilbert(data_array, axis=2))
    b_mode = np.max(envelope, axis=2)
    b_mode /= np.max(b_mode)
    return b_mode


def build_stacked_envelope_image(data_array):
    """
    返回堆叠式 B-mode 图像，保留每条 envelope 波形。
    返回 shape = (rows * depth, cols)
    """
    envelope = np.abs(hilbert(data_array, axis=2))
    rows, cols, depth = envelope.shape
    stacked = np.zeros((rows * depth, cols))

    for r in range(rows):
        for c in range(cols):
            stacked[r * depth:(r + 1) * depth, c] = envelope[r, c, :]

    stacked -= stacked.min()
    stacked /= stacked.max()
    return stacked, depth


def show_b_mode_image(b_mode, save_path=None):
    """显示标准 B-mode 最大值图像。"""
    plt.imshow(b_mode.T, cmap='gray', aspect='auto', origin='lower')
    plt.title('B-mode Image (Max Envelope)')
    plt.xlabel('X Scan Index (Column)')
    plt.ylabel('Y Scan Index (Row)')
    plt.colorbar(label='Normalized Echo Amplitude')
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=300)
        print(f"[Saved] {save_path}")
    plt.show()


def show_stacked_image(stacked, depth_per_row, save_path=None):
    """显示完整 envelope 堆叠图像。"""
    plt.imshow(stacked, cmap='gray', aspect='auto', origin='lower')
    plt.title('Stacked Envelope Image')
    plt.xlabel('Scan Column Index')
    plt.ylabel('Depth (Row-wise)')
    yticks = [i * depth_per_row for i in range(stacked.shape[0] // depth_per_row)]
    plt.yticks(yticks, [f"Row {i + 1}" for i in range(len(yticks))])
    plt.colorbar(label='Envelope Amplitude')
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=300)
        print(f"[Saved] {save_path}")
    plt.show()


# ==========================
# 示例入口（运行本模块）
# ==========================
if __name__ == "__main__":
    scan_id = 12  # TODO: 你可以在这里改 scan ID
    base_folder = "data"  # TODO: 修改为你的路径（如有必要）

    data = load_scan_folder(scan_id=scan_id, base_folder=base_folder)

    # 输出 1：标准 B-mode 最大值图
    b_mode = build_b_mode_image(data)
    show_b_mode_image(b_mode, save_path=f"bmode_scan_{scan_id}.png")

    # 输出 2：堆叠 envelope 图像（完整结构）
    stacked_img, depth = build_stacked_envelope_image(data)
    show_stacked_image(stacked_img, depth_per_row=depth, save_path=f"stacked_scan_{scan_id}.png")
