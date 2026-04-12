import socket
import json
from pathlib import Path
import numpy as np
from rig_function import send_command, enable_axis, wait_until_stopped
from scipy.signal import butter, detrend, filtfilt, hilbert, windows

try:
    from dummy_signal_generator import generate_dummy_echo as _generate_dummy_echo
except Exception:
    _generate_dummy_echo = None

# Local defaults; callers (e.g., GUI) can override these before calling main().
HOST = "192.168.1.250"
PORT = 5001
A_SCAN_PARAMS = {"X": 0, "Y": 0, "Z": 0, "mode": "INC"}
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "gui_settings.json"

# Test-mode switch for GUI development.
USE_DUMMY_SIGNAL_GENERATOR = True
DUMMY_NOISE_STD = 0.1

# Note: we avoid creating instrument connections at import time so this
# module is safe to import. The signal generator is not required by
# the a_scan() function below and should be constructed by callers if
# needed.


#  mm → pulse conversion function (1 mm = 700 pulses, matching move.py)
def mm_to_pulse(mm):
    return int(mm * 700)


def a_scan(test=False):
    if test:
        print(f"Simulated A-mode scan with dummy echo (noise std: {DUMMY_NOISE_STD})")
        # In test mode, use excitation parameters from gui_settings.json.
        return generate_test_echo()
    else:
        print("A-mode scan triggered.")
        return None


def move(sock, x, y, z):
    send_command(sock, "INC")
    enable_axis(sock, "X")
    enable_axis(sock, "Y")
    enable_axis(sock, "Z")

    print(f"A-mode delta move request: Delta_X={x}, Delta_Y={y}, Delta_Z={z}")
    if x != 0:
        send_command(sock, f"X{x}")
        wait_until_stopped(sock, "X")
    if y != 0:
        send_command(sock, f"Y{y}")
        wait_until_stopped(sock, "Y")
    if z != 0:
        send_command(sock, f"Z{z}")
        wait_until_stopped(sock, "Z")
    return print("Rig is moved by the specified deltas.")


def generate_test_echo(
    frequency_hz=None,
    amplitude_v=None,
    no_of_cycles_per_pulse=None,
    window_type="Hanning",
    sampling_rate_hz=None,
):
    if _generate_dummy_echo is None:
        raise RuntimeError("dummy_signal_generator.py is unavailable")
    return _generate_dummy_echo(
        frequency_hz=frequency_hz,
        amplitude_v=amplitude_v,
        no_of_cycles_per_pulse=no_of_cycles_per_pulse,
        window_type=window_type,
        sampling_rate_hz=sampling_rate_hz,
        noise_std=DUMMY_NOISE_STD,
    )


def estimate_a_mode_signal(
    time_axis,
    signal,
    highpass_cutoff_hz=50_000.0,
    filter_order=4,
    sampling_rate_hz=None,
):
    """Estimate the A-mode envelope from an A-scan trace.

    Processing flow:
    1. detrend the averaged A-scan
    2. apply a high-pass filter
    3. apply a light Tukey taper to reduce edge artefacts
    4. detect the envelope with the Hilbert transform
    """
    time_arr = np.asarray(time_axis, dtype=float)
    trace = np.asarray(signal, dtype=float)
    if trace.size == 0:
        return trace

    processed = detrend(trace, type="linear")

    sample_rate_hz = None
    if sampling_rate_hz is not None:
        try:
            sample_rate_hz = float(sampling_rate_hz)
        except Exception:
            sample_rate_hz = None
    if sample_rate_hz is None or sample_rate_hz <= 0.0:
        if time_arr.size > 1:
            dt = float(np.median(np.diff(time_arr)))
        else:
            dt = 0.0
        if dt > 0.0:
            sample_rate_hz = 1.0 / dt

    if sample_rate_hz is not None and sample_rate_hz > 0.0:
        nyquist_hz = 0.5 * sample_rate_hz
        cutoff_hz = float(highpass_cutoff_hz)
        order = max(1, int(filter_order))
        if cutoff_hz <= 0.0:
            # Explicitly skip high-pass filtering when cutoff is set to 0.
            pass
        elif cutoff_hz < nyquist_hz and trace.size > (3 * order + 3):
            normalized_cutoff = min(cutoff_hz / nyquist_hz, 0.95)
            b, a = butter(order, normalized_cutoff, btype="highpass")
            processed = filtfilt(b, a, processed)

    processed = processed * windows.tukey(trace.size, alpha=0.1)
    envelope = np.abs(hilbert(processed))
    return envelope


def _load_runtime_defaults_from_settings():
    if not SETTINGS_PATH.exists():
        return HOST, PORT, dict(A_SCAN_PARAMS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
        cfg = settings.get("config", {}) if isinstance(settings, dict) else {}
        a_mode = settings.get("a_mode", {}) if isinstance(settings, dict) else {}

        host = cfg.get("host", HOST)
        port = int(cfg.get("port", PORT))
        params = {
            "X": float(a_mode.get("X", A_SCAN_PARAMS.get("X", 0))),
            "Y": float(a_mode.get("Y", A_SCAN_PARAMS.get("Y", 0))),
            "Z": float(a_mode.get("Z", A_SCAN_PARAMS.get("Z", 0))),
            "mode": "INC",
        }
        return host, port, params
    except Exception:
        return HOST, PORT, dict(A_SCAN_PARAMS)


def main(a_scan_params=None, host=None, port=None):
    default_host, default_port, default_params = _load_runtime_defaults_from_settings()
    params = dict(default_params if a_scan_params is None else a_scan_params)
    host = default_host if host is None else host
    port = default_port if port is None else port

    # Convert mm units to pulse
    x_pulse = mm_to_pulse(params.get("X", 0))
    y_pulse = mm_to_pulse(params.get("Y", 0))
    z_pulse = mm_to_pulse(params.get("Z", 0))

    if USE_DUMMY_SIGNAL_GENERATOR:
        return a_scan(test=True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, port))
        move(sock, x=x_pulse, y=y_pulse, z=z_pulse)
        return a_scan(test=False)


if __name__ == "__main__":
    main()
