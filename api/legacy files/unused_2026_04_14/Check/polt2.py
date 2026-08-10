import os
import re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import hilbert
from skimage import filters, exposure


def parse_row_col(filename_stem):
    match = re.match(r"row_(\d+)_col_(\d+)", filename_stem)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def load_scan_folder(scan_id, base_folder="data"):
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
        series = df.get("Amplitude (V)", pd.to_numeric(df.iloc[:, 0], errors='coerce').dropna())
        values = series.values
        min_length = min(min_length, len(values))
        scan_map[(row - 1, col - 1)] = values

    data_array = np.zeros((max_row, max_col, min_length))
    for (r, c), waveform in scan_map.items():
        data_array[r, c, :] = waveform[:min_length]

    return data_array


def build_b_mode_image(data_array, crop_ratio=0.1, smooth_window=5, log_compress=True, dynamic_range=60):
    data_array = np.nan_to_num(data_array)
    envelope = np.abs(hilbert(data_array, axis=2))

    if smooth_window > 1:
        from scipy.ndimage import uniform_filter1d
        envelope = uniform_filter1d(envelope, size=smooth_window, axis=2)

    start = int(crop_ratio * envelope.shape[2])
    end = int((1 - crop_ratio) * envelope.shape[2])
    envelope = envelope[:, :, start:end]
    b_mode = np.max(envelope, axis=2)

    if log_compress:
        b_mode /= b_mode.max() + 1e-10
        b_mode = 20 * np.log10(np.clip(b_mode, 1e-10, None))
        b_mode = np.clip(b_mode, -dynamic_range, 0)
        b_mode = (b_mode + dynamic_range) / dynamic_range

    return b_mode


def show_b_mode_image(b_mode, save_path=None):
    plt.imshow(b_mode.T, cmap='gray', aspect='auto', origin='lower')
    plt.title('Enhanced B-mode Image')
    plt.xlabel('X Scan Index (Column)')
    plt.ylabel('Y Scan Index (Row)')
    plt.colorbar(label='Compressed Envelope')
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=300)
        print(f"[Saved] {save_path}")
    plt.show()


if __name__ == "__main__":
    scan_id = 12
    base_folder = "data"
    data = load_scan_folder(scan_id=scan_id, base_folder=base_folder)

    b_mode = build_b_mode_image(data, crop_ratio=0.1, smooth_window=5, log_compress=True, dynamic_range=60)
    show_b_mode_image(b_mode, save_path=f"bmode_enhanced_scan_{scan_id}.png")
