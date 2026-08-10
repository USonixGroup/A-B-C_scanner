import os
import pyvisa
import time
import pandas as pd
import numpy as np
from pathlib import Path
from api.src.const import data_dir
from api.src.Signal_function import Burst_generate
try:
    from api.src.Setup import OSC_ADDRESS, SIGNAL_PARAMS
except Exception:
    # Fallback defaults when Setup.py is intentionally archived.
    OSC_ADDRESS = "USB0::0x05FF::0x1023::3502N04522::INSTR"
    SIGNAL_PARAMS = {
        "shape": "SIN",
        "frequency": 1_000_000,
        "amplitude": 1,
        "no_of_cycles_per_pulse": 1,
        "no_of_pulses": 1,
        "prf": 1000,
        "window_type": "Hanning",
    }

# Module-level oscilloscope handle — opened once per scan session.
_osc = None
_osc_address = None


def _osc_resource_targets(address):
    target = str(address or OSC_ADDRESS).strip()
    candidates = [target]
    upper_target = target.upper()

    if upper_target.startswith("TCPIP") and upper_target.endswith("::INSTR"):
        parts = target.split("::")
        if len(parts) == 3:
            host = parts[1]
            candidates.append(f"TCPIP0::{host}::inst0::INSTR")
            candidates.append(f"TCPIP::{host}::inst0::INSTR")
            candidates.append(f"TCPIP::{host}::INSTR")

    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate.strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _osc_resource_managers(address):
    target = str(address or OSC_ADDRESS).strip().upper()
    backends = [None, "@py"] if target.startswith("TCPIP") else ["@py", None]

    managers = []
    for backend in backends:
        try:
            rm = pyvisa.ResourceManager() if backend is None else pyvisa.ResourceManager(backend)
            managers.append((backend or "default", rm))
        except Exception:
            continue
    return managers


def open_oscilloscope(address=None, max_attempts=1, retry_delay=0.3, force_reopen=False):
    """Open a VISA connection to the oscilloscope and cache it on this module."""
    global _osc, _osc_address

    target = address or OSC_ADDRESS
    normalized_target = str(target).strip()
    if _osc is not None and not force_reopen and str(_osc_address or "").strip() == normalized_target:
        return _osc

    if _osc is not None:
        close_oscilloscope()

    attempts = max(1, int(max_attempts))
    last_error = None
    error_messages = []
    target_candidates = _osc_resource_targets(normalized_target)
    managers = _osc_resource_managers(normalized_target)
    if not managers:
        raise RuntimeError("No VISA backend available for oscilloscope connection.")

    for attempt in range(attempts):
        for backend_name, rm in managers:
            for candidate in target_candidates:
                try:
                    _osc = rm.open_resource(candidate, timeout=5000)
                    _osc_address = normalized_target
                    return _osc
                except Exception as exc:
                    last_error = exc
                    _osc = None
                    _osc_address = None
                    error_messages.append(f"backend={backend_name}, target={candidate}, error={exc}")
        if attempt < attempts - 1:
            time.sleep(max(float(retry_delay), 0.0))

    details = "; ".join(error_messages[-6:]) if error_messages else str(last_error)
    raise RuntimeError(f"Unable to open oscilloscope at {normalized_target}. Attempts: {details}") from last_error


def close_oscilloscope():
    """Close the cached oscilloscope handle."""
    global _osc, _osc_address
    if _osc is not None:
        try:
            _osc.close()
        except Exception:
            pass
        _osc = None
    _osc_address = None


def create_scan_folder():
    """Create and return a new numbered scan folder inside data_dir."""
    os.makedirs(data_dir, exist_ok=True)
    existing_scans = [
        d
        for d in os.listdir(data_dir)
        if d.startswith("scan_") and os.path.isdir(os.path.join(data_dir, d))
    ]
    scan_numbers = [
        int(d.split("_")[1]) for d in existing_scans if d.split("_")[1].isdigit()
    ]
    next_scan_num = max(scan_numbers, default=0) + 1
    scan_folder = os.path.join(data_dir, f"scan_{next_scan_num:03d}")
    os.makedirs(scan_folder)
    return scan_folder


def configure_oscilloscope_for_burst(osc, params):
    """Configure oscilloscope acquisition settings for burst waveform capture."""

    def vbs(cmd):
        osc.write(f"VBS '{cmd}'")
        time.sleep(0.1)

    freq = params["frequency"]
    amp = params["amplitude"]
    n_cycles = params.get("no_of_cycles_per_pulse", 1)

    burst_duration = n_cycles / freq
    hor_scale = burst_duration / 5
    ver_scale = amp
    sampling_rate = freq * 100

    vbs(f"app.Acquisition.Horizontal.HorScale = {hor_scale}")
    vbs(f"app.Acquisition.C1.VerScale = {ver_scale}")
    vbs(f"app.Acquisition.Horizontal.SampleRate = {sampling_rate}")
    vbs("app.Acquisition.C1.Offset = 0")
    vbs("app.Acquisition.C1.View = true")
    vbs('app.Acquisition.Trigger.Source = "EXT"')
    osc.write("TRIG_MODE NORM")


def read_oscilloscope_and_save(osc, cross, s, scan_folder):
    """Read one waveform from the oscilloscope and save it as a CSV.

    Parameters
    ----------
    osc : pyvisa Resource | None
        Open oscilloscope handle.  Falls back to the module-level ``_osc``
        if *None* is passed.
    cross, s : int
        Row and column indices used to name the output file.
    scan_folder : str
        Directory where the CSV will be written.
    """
    if osc is None:
        osc = _osc
    if osc is None:
        print("❌ Oscilloscope not open. Call open_oscilloscope() first.")
        return

    try:
        configure_oscilloscope_for_burst(osc, SIGNAL_PARAMS)

        osc.write("C1:WF? DAT1")
        raw_data = osc.query_binary_values(
            "C1:WF? DAT1", datatype="B", container=np.array
        )

        scale = float(osc.query("C1:VDIV?").strip().split(" ")[1])
        v_offset = float(osc.query("C1:OFST?").strip().split(" ")[1])
        scale1 = 1 / 30
        voltages = ((raw_data - 128) * scale + v_offset - 128) * scale1

        time_div = float(osc.query("TDIV?").strip().split(" ")[1])
        num_points = len(raw_data)
        time_span = 10 * time_div  # total span across 10 divisions
        time_values = np.linspace(0, time_span, num_points, endpoint=False)

        filename = f"row_{cross + 1}_col_{s}.csv"
        file_path = os.path.join(scan_folder, filename)
        df = pd.DataFrame({"Time (s)": time_values, "Amplitude (V)": voltages})
        df.to_csv(file_path, index=False)
        print(f"✅ Data saved to: {file_path}")

        # Save signal parameters alongside waveform data
        param_path = os.path.join(scan_folder, "wave_parameter.csv")
        param_df = pd.DataFrame(
            list(SIGNAL_PARAMS.items()), columns=["Parameter", "Value"]
        )
        param_df.to_csv(param_path, index=False)
        print(f"📝 Signal parameters saved to: {param_path}")

    except Exception as e:
        print(f"❌ Failed to read oscilloscope data: {e}")


def send_burst(osc, sg, cross, s, scan_folder):
    """Trigger one burst on the signal generator then capture from the oscilloscope.

    Parameters
    ----------
    osc : pyvisa Resource
        Open oscilloscope handle (from open_oscilloscope()).
    sg : Agilent33500
        Open signal generator handle.
    cross, s : int
        Row and column indices for the saved CSV file name.
    scan_folder : str
        Destination directory for CSV files.
    """
    Burst_generate(
        sg,
        shape=SIGNAL_PARAMS.get("shape", "SIN"),
        frequency=SIGNAL_PARAMS.get("frequency", 1000000),
        amplitude=SIGNAL_PARAMS.get("amplitude", 1),
        no_of_cycles_per_pulse=SIGNAL_PARAMS.get("no_of_cycles_per_pulse", 1),
        no_of_pulses=SIGNAL_PARAMS.get("no_of_pulses", 1),
        prf=SIGNAL_PARAMS.get("prf", 1000),
        window_type=SIGNAL_PARAMS.get("window_type", "Hanning"),
    )
    print("⚡ Burst triggered")
    time.sleep(1)
    read_oscilloscope_and_save(osc, cross, s, scan_folder)
    time.sleep(0.5)
    print(f"📡 Captured triggered data for row {cross + 1}, column {s}...")
