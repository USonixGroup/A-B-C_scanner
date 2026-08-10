import json
from pathlib import Path

import numpy as np

from Signal_function import (
    ARB_POINTS_PER_CYCLE,
    _arb_sample_count,
    _build_windowed_sine_waveform,
)

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR.parent / "data" / "gui_settings.json"


def _load_gui_settings(settings_path=None):
    path = Path(settings_path) if settings_path else SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_excitation_settings(settings_path=None):
    settings = _load_gui_settings(settings_path=settings_path)
    excitation = settings.get("excitation", {}) if isinstance(settings, dict) else {}
    config = settings.get("config", {}) if isinstance(settings, dict) else {}
    return {
        "frequency_hz": excitation.get("frequency"),
        "amplitude_v": excitation.get("amplitude"),
        "no_of_cycles_per_pulse": excitation.get("no_of_cycles_per_pulse"),
        "window_type": excitation.get("window_type"),
        "sampling_rate_hz": config.get("sampling_rate"),
    }


def generate_dummy_echo(
    frequency_hz=None,
    amplitude_v=None,
    no_of_cycles_per_pulse=None,
    window_type=None,
    sampling_rate_hz=None,
    noise_std=0.1,
    settings_path=None,
):
    """Generate synthetic A-scan echo from excitation settings.

    Echo model: -0.5 * excitation + N(0, noise_std).
    If parameters are omitted, values are loaded from gui_settings.json.
    """
    cfg = get_excitation_settings(settings_path=settings_path)

    frequency = float(
        frequency_hz if frequency_hz is not None else cfg.get("frequency_hz")
    )
    amplitude = float(
        amplitude_v if amplitude_v is not None else cfg.get("amplitude_v")
    )
    cycles = float(
        no_of_cycles_per_pulse
        if no_of_cycles_per_pulse is not None
        else cfg.get("no_of_cycles_per_pulse")
    )
    wtype = window_type if window_type is not None else cfg.get("window_type")

    sampling_raw = (
        sampling_rate_hz
        if sampling_rate_hz is not None
        else cfg.get("sampling_rate_hz")
    )
    if sampling_raw is None or str(sampling_raw).strip() == "":
        sampling = max(float(frequency) * ARB_POINTS_PER_CYCLE, 1.0)
    else:
        sampling = max(float(sampling_raw), 1.0)

    cycles = max(cycles, 1.0)
    frequency = max(frequency, 1e-9)
    pulse_width_sec = cycles / frequency

    # Build a more realistic A-scan record with quiet pre-trigger space, a delayed
    # main echo, and a weaker later reflection so the envelope is visually distinct.
    pre_trigger_sec = 1.5 * pulse_width_sec
    echo_delay_sec = 2.0 * pulse_width_sec
    second_echo_delay_sec = 4.2 * pulse_width_sec
    total_duration_sec = max(6.0 * pulse_width_sec, second_echo_delay_sec + 1.5 * pulse_width_sec)

    n_total = max(256, int(np.ceil(total_duration_sec * sampling)))
    t = np.linspace(0.0, total_duration_sec, n_total, endpoint=False)
    echo = np.zeros(n_total, dtype=float)

    pulse_samples = min(_arb_sample_count(cycles), n_total)
    burst = 0.5 * amplitude * _build_windowed_sine_waveform(
        no_of_cycles_per_pulse=cycles,
        window_type=wtype,
        sample_count=pulse_samples,
    )

    first_start_idx = int(round((pre_trigger_sec + echo_delay_sec) * sampling))
    second_start_idx = int(round((pre_trigger_sec + second_echo_delay_sec) * sampling))

    first_end_idx = min(first_start_idx + pulse_samples, n_total)
    if first_end_idx > first_start_idx:
        echo[first_start_idx:first_end_idx] += -0.7 * burst[: first_end_idx - first_start_idx]

    second_end_idx = min(second_start_idx + pulse_samples, n_total)
    if second_end_idx > second_start_idx:
        echo[second_start_idx:second_end_idx] += -0.35 * burst[: second_end_idx - second_start_idx]

    noise = np.random.normal(0.0, float(noise_std), size=n_total)
    echo = echo + noise
    return t, echo
