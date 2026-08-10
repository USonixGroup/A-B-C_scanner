# Signal Generator Configuration
import re
import time
import numpy as np

# Number of ARB waveform samples generated per cycle per pulse for hardware upload.
# A higher density reduces interpolation artifacts that can look like phase delay.
ARB_POINTS_PER_CYCLE = 40


def _flat_top_window(n):
    """Return a Flat-Top window (5-term, amplitude-normalized) with numpy-compatible shape.

    Coefficients match scipy/Matlab standard flat-top: coefficients sum to 1.0 at the
    window peak so the maximum window value is exactly 1.0 and amplitude is preserved.
    """
    if n <= 1:
        return np.ones(n)
    # Normalized 5-term flat-top (Heinzel 2002 / scipy.signal.windows.flattop)
    a0 = 0.21557895
    a1 = 0.41663158
    a2 = 0.277263158
    a3 = 0.083578947
    a4 = 0.006947368
    x = 2 * np.pi * np.arange(n) / (n - 1)
    return (
        a0
        - a1 * np.cos(x)
        + a2 * np.cos(2 * x)
        - a3 * np.cos(3 * x)
        + a4 * np.cos(4 * x)
    )


def _window_array(window_type, n):
    wtype = (window_type or "Hanning").strip().lower()
    if wtype in {
        "none",
        "no window",
        "no windowing",
        "none (rectangular)",
        "rectangular",
    }:
        return np.ones(n)
    if wtype == "hamming":
        return np.hamming(n)
    if wtype in {"blackman-harris", "blackman harris", "blackmanharris"}:
        # Fallback to numpy.blackman when explicit Blackman-Harris isn't available.
        return np.blackman(n)
    if wtype in {"flat-top", "flattop", "flat top"}:
        return _flat_top_window(n)
    # Default: Hanning
    return np.hanning(n)


def _arb_sample_count(no_of_cycles_per_pulse):
    cycles = max(1.0, float(no_of_cycles_per_pulse or 1.0))
    return max(256, int(np.ceil(ARB_POINTS_PER_CYCLE * cycles)))


def _build_windowed_sine_waveform(no_of_cycles_per_pulse, window_type, sample_count=None):
    cycles = max(1.0, float(no_of_cycles_per_pulse or 1.0))
    n = int(sample_count) if sample_count is not None else _arb_sample_count(cycles)
    phase = np.linspace(0.0, 2.0 * np.pi * cycles, n, endpoint=False)
    carrier = np.sin(phase)
    window = _window_array(window_type, n)
    waveform = carrier * window
    max_abs = np.max(np.abs(waveform))
    if max_abs > 0:
        waveform = waveform / max_abs
    return waveform


def _get_writer(sg):
    """Return a low-level SCPI writer callable if available."""
    if hasattr(sg, "write") and callable(getattr(sg, "write")):
        return sg.write
    adapter = getattr(sg, "adapter", None)
    if adapter is not None and hasattr(adapter, "write"):
        return adapter.write
    return None


def _get_query(sg):
    if hasattr(sg, "query") and callable(getattr(sg, "query")):
        return sg.query
    if hasattr(sg, "ask") and callable(getattr(sg, "ask")):
        return sg.ask
    adapter = getattr(sg, "adapter", None)
    if adapter is not None and hasattr(adapter, "ask"):
        return adapter.ask
    if adapter is not None and hasattr(adapter, "query"):
        return adapter.query
    return None


def _parse_scpi_error_code(response):
    if response is None:
        return None
    match = re.match(r"\s*([+-]?\d+)", str(response))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _drain_error_queue(sg, max_reads=8):
    query = _get_query(sg)
    if query is None:
        return []

    errors = []
    for _ in range(max_reads):
        try:
            response = query("SYST:ERR?")
        except Exception:
            break
        code = _parse_scpi_error_code(response)
        errors.append((code, str(response).strip()))
        if code in {None, 0}:
            break
    return errors


def _clear_error_queue(sg):
    writer = _get_writer(sg)
    if writer is None:
        return
    try:
        writer("*CLS")
    except Exception:
        pass
    _drain_error_queue(sg)


def _run_scpi_command_group(sg, commands):
    writer = _get_writer(sg)
    if writer is None:
        return False

    query = _get_query(sg)
    _clear_error_queue(sg)
    try:
        for command in commands:
            writer(command)
    except Exception:
        _drain_error_queue(sg)
        return False

    if query is None:
        return True

    errors = _drain_error_queue(sg)
    for code, _message in errors:
        if code not in {None, 0}:
            return False
    return True


def _try_scpi_command_groups(sg, command_groups):
    for commands in command_groups:
        if _run_scpi_command_group(sg, commands):
            return True
    return False


def _set_first_supported_attr(sg, candidates, value):
    """Set the first supported SG attribute from a list; returns the attribute name or None."""
    for attr in candidates:
        try:
            setattr(sg, attr, value)
            return attr
        except Exception:
            continue
    return None


def _enforce_amplitude_vpp(sg, amplitude):
    """Best-effort amplitude programming in Vpp across different drivers/SCPI paths."""
    if amplitude is None:
        return
    amp = float(amplitude)

    # Driver-level attributes first.
    _set_first_supported_attr(sg, ["amplitude", "voltage"], amp)

    # SCPI fallback: some instruments reset ARB amplitude to 100 mVpp on FUNC change.
    _try_scpi_command_groups(
        sg,
        [
            ["SOUR1:VOLT:UNIT VPP", f"SOUR1:VOLT {amp}"],
            ["SOUR:VOLT:UNIT VPP", f"SOUR:VOLT {amp}"],
            ["VOLT:UNIT VPP", f"VOLT {amp}"],
            [f"VOLT {amp}"],
        ],
    )


def _apply_phase_if_requested(sg, phase):
    """Best-effort phase programming only when an explicit phase is requested."""
    if phase is None:
        return

    phase_deg = float(phase)
    _set_first_supported_attr(sg, ["phase"], phase_deg)

    _try_scpi_command_groups(
        sg,
        [
            [f"SOUR1:PHAS {phase_deg}"],
            [f"SOUR:PHAS {phase_deg}"],
            [f"PHAS {phase_deg}"],
        ],
    )


def describe_burst_generation(shape, frequency, no_of_cycles_per_pulse, window_type):
    """Describe the SG generation mode and effective hardware sampling rate."""
    shape_key = (shape or "").strip().upper()
    wtype = (window_type or "Hanning").strip().lower()
    native_window_types = {
        "none",
        "no window",
        "no windowing",
        "none (rectangular)",
        "rectangular",
    }

    if shape_key != "SIN" or wtype in native_window_types:
        return {
            "mode": "Native Burst",
            "sampling_rate_hz": None,
            "sample_count": None,
        }

    cycles = max(1.0, float(no_of_cycles_per_pulse or 1.0))
    sample_count = _arb_sample_count(cycles)
    sampling_rate_hz = 0.0
    if frequency and float(frequency) > 0.0:
        sampling_rate_hz = sample_count * float(frequency) / cycles

    return {
        "mode": "ARB",
        "sampling_rate_hz": sampling_rate_hz,
        "sample_count": sample_count,
    }


def _configure_arb_timing(sg, frequency_hz, no_of_cycles_per_pulse):
    """Best-effort ARB playback timing configuration.

    The uploaded ARB waveform spans one pulse containing `no_of_cycles_per_pulse`
    carrier cycles. To preserve the requested carrier frequency, ARB waveform
    repetition frequency must be `frequency_hz / no_of_cycles_per_pulse`.
    """
    if frequency_hz is None or float(frequency_hz) <= 0.0:
        return

    cycles = max(1.0, float(no_of_cycles_per_pulse or 1.0))
    sample_count = _arb_sample_count(cycles)
    arb_waveform_freq_hz = float(frequency_hz) / cycles
    arb_sample_rate_hz = arb_waveform_freq_hz * sample_count

    # Driver-level best effort.
    _set_first_supported_attr(sg, ["arb_frequency", "arb_freq"], arb_waveform_freq_hz)
    _set_first_supported_attr(
        sg,
        ["arb_sample_rate", "arb_srate", "sample_rate"],
        arb_sample_rate_hz,
    )

    _try_scpi_command_groups(
        sg,
        [
            [
                f"SOUR1:FUNC:ARB:SRAT {arb_sample_rate_hz}",
                f"SOUR1:FUNC:ARB:FREQ {arb_waveform_freq_hz}",
            ],
            [
                f"SOUR:FUNC:ARB:SRAT {arb_sample_rate_hz}",
                f"SOUR:FUNC:ARB:FREQ {arb_waveform_freq_hz}",
            ],
            [f"SOUR1:FREQ {arb_waveform_freq_hz}"],
            [f"SOUR:FREQ {arb_waveform_freq_hz}"],
            [f"FREQ {arb_waveform_freq_hz}"],
        ],
    )


def _apply_windowed_sine_if_possible(
    sg,
    shape,
    window_type,
    no_of_cycles_per_pulse,
):
    """Best-effort SG programming for a windowed sine pulse via ARB data.

    Point count is derived from the shared ARB sample-density rule.
    Returns True if ARB waveform programming appears successful; otherwise False.
    """
    if (shape or "").strip().upper() != "SIN":
        return False

    # Rectangular/no-window pulses are better represented by native SIN burst mode
    # (frequency + burst_ncycles) than ARB upload.
    wtype = (window_type or "Hanning").strip().lower()
    if wtype in {
        "none",
        "no window",
        "no windowing",
        "none (rectangular)",
        "rectangular",
    }:
        return False

    writer = _get_writer(sg)
    if writer is None:
        return False

    try:
        waveform = _build_windowed_sine_waveform(
            no_of_cycles_per_pulse=no_of_cycles_per_pulse,
            window_type=window_type,
        )
        csv = ",".join(f"{v:.6f}" for v in waveform)

        # Use model-tolerant command groups. 33500-family supports FUNC ARB,
        # while 33220A-class instruments typically use FUNC USER / FUNC:USER.
        return _try_scpi_command_groups(
            sg,
            [
                [
                    f"SOUR1:DATA VOLATILE,{csv}",
                    "SOUR1:FUNC ARB",
                    "SOUR1:FUNC:ARB VOLATILE",
                ],
                [
                    f"SOUR:DATA VOLATILE,{csv}",
                    "SOUR:FUNC ARB",
                    "SOUR:FUNC:ARB VOLATILE",
                ],
                [
                    f"DATA VOLATILE,{csv}",
                    "FUNC:USER VOLATILE",
                    "FUNC USER",
                ],
                [
                    f"DATA VOLATILE,{csv}",
                    "FUNC USER",
                ],
            ],
        )
    except Exception:
        return False


def Continuous_generate(
    sg,
    shape=None,
    frequency=None,
    amplitude=None,
    offset=None,
    phase=None,
):
    # 1. Restore continuous mode
    sg.burst_state = False
    sg.arb_advance = "SRAT"  # Set to sample rate advance mode (continuous playback)
    sg.trigger_source = "IMM"  # Set to immediate trigger mode (no external trigger)

    # 2. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if offset:
        sg.offset = offset
    _apply_phase_if_requested(sg, phase)

    sg.output = True

    print("Signal generator set to continuous mode.")
    print(
        f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V, 
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees"""
    )


def Trigger_generate(
    sg,
    shape="None",
    frequency=None,
    amplitude=None,
    number_of_trigger=None,
    offset=None,
    phase=None,
):
    # 1. Set mode to triggered output
    sg.arb_advance = "TRIG"
    sg.burst_state = False
    sg.trigger_source = "BUS"

    # 2. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if offset:
        sg.offset = offset
    _apply_phase_if_requested(sg, phase)

    sg.output = True

    for i in range(number_of_trigger):
        sg.trigger()
        sg.wait_for_trigger(timeout=10)
        sg.beep()

    print("Signal generator executed triggered mode.")
    print(
        f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V, 
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees"""
    )


def Burst_generate(
    sg,
    shape="None",
    frequency=None,
    amplitude=None,
    no_of_cycles_per_pulse=None,
    no_of_pulses=None,
    prf=None,
    window_type="Hanning",
    offset=None,
    phase=None,
):
    # 1. Set burst mode
    sg.burst_mode = "TRIG"
    sg.burst_state = True
    sg.trigger_source = "BUS"

    # 2. Required defaults for pulse model.
    if no_of_cycles_per_pulse is None:
        no_of_cycles_per_pulse = 1
    if no_of_pulses is None:
        no_of_pulses = 1

    # 3. Optionally update waveform parameters
    if shape:
        sg.shape = shape
    if frequency:
        sg.frequency = frequency
    if amplitude:
        sg.amplitude = amplitude
    if offset:
        sg.offset = offset
    _apply_phase_if_requested(sg, phase)

    # 4. Apply selected window type when possible (best effort).
    # If not supported by the SG backend, function continues with normal SIN burst.
    window_applied = _apply_windowed_sine_if_possible(
        sg,
        shape=shape,
        window_type=window_type,
        no_of_cycles_per_pulse=no_of_cycles_per_pulse,
    )

    # Keep hardware burst semantics aligned with GUI semantics:
    # - Non-ARB path: one carrier cycle at `frequency`, repeated `no_of_cycles_per_pulse` times.
    # - ARB path: uploaded waveform already contains `no_of_cycles_per_pulse` carrier cycles,
    #   so set burst_ncycles=1 and ARB repetition frequency = frequency/cycles.
    burst_cycles_for_hw = float(no_of_cycles_per_pulse)
    if window_applied:
        if frequency and float(frequency) > 0:
            _configure_arb_timing(sg, float(frequency), float(no_of_cycles_per_pulse))
        burst_cycles_for_hw = 1.0

    # Re-apply amplitude after potential FUNC/ARB changes.
    _enforce_amplitude_vpp(sg, amplitude)

    if burst_cycles_for_hw:
        sg.burst_ncycles = burst_cycles_for_hw

    # Pulse width from cycles/frequency; PRF controls trigger spacing.
    pulse_width = (
        float(no_of_cycles_per_pulse) / float(frequency)
        if frequency and no_of_cycles_per_pulse
        else 0.001
    )
    if prf and float(prf) > 0:
        inter_pulse_period = 1.0 / float(prf)
    else:
        # Fallback: no explicit PRF provided -> minimum spacing equals pulse width.
        inter_pulse_period = pulse_width

    total_burst_length = float(no_of_pulses) * inter_pulse_period

    # 5. Program inter-pulse period on hardware when available.
    # Pulse count itself is enforced in software below for deterministic behavior
    # across different SG drivers/models.
    programmed_count_attr = None
    programmed_period_attr = _set_first_supported_attr(
        sg,
        ["burst_internal_period", "burst_period", "burst_trigger_period"],
        float(inter_pulse_period),
    )

    sg.output = True

    # Deterministic path: one trigger per requested pulse on an absolute PRF timeline.
    # Avoid using wait_for_trigger for pacing because backend/device latency can vary and
    # introduce random-looking spacing.
    next_trigger_at = time.perf_counter()
    for _ in range(int(no_of_pulses)):
        now = time.perf_counter()
        wait_s = next_trigger_at - now
        if wait_s > 0:
            time.sleep(wait_s)

        sg.trigger()
        next_trigger_at += inter_pulse_period

    print("Signal generator set to burst mode.")
    print(
        f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V,
Window Type: {window_type},
Window Applied: {window_applied},
No. Of Cycles Per Pulse (programmed): {sg.burst_ncycles},
No. Of Pulses: {no_of_pulses},
PRF: {prf if prf else 'auto'} Hz,
Total Burst Length: {total_burst_length} s,
Pulse Count Attr: {programmed_count_attr if programmed_count_attr else 'software-loop'},
PRF Attr: {programmed_period_attr if programmed_period_attr else 'software-loop'},
Offset: {sg.offset} V, 
Phase: {sg.phase} degrees"""
    )


# Prompt to stop signal generation
def stop_condition():
    input("Press Enter to stop signal generation...")
    return True


# Stop output
def stop_output(sg):
    stop_condition()
    sg.output = False
    print("⚠️ Signal output stopped.")


# Wait for trigger (blocking)
def stop_trigger(sg):
    sg.wait_for_trigger()


# Wait for burst to complete or manual stop
def stop_burst(sg, burst_period):
    sg.wait_for_trigger(timeout=burst_period, should_stop=stop_condition)
