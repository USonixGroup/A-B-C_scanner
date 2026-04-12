# Signal Generator Configuration
import time
import numpy as np

# Number of ARB waveform samples generated per cycle per pulse for hardware upload.
# Increase for smoother waveforms; decrease to reduce SCPI transfer time.
ARB_POINTS_PER_CYCLE = 10


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
    if wtype in {"none", "no window", "no windowing", "none (rectangular)", "rectangular"}:
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


def _get_writer(sg):
    """Return a low-level SCPI writer callable if available."""
    if hasattr(sg, "write") and callable(getattr(sg, "write")):
        return sg.write
    adapter = getattr(sg, "adapter", None)
    if adapter is not None and hasattr(adapter, "write"):
        return adapter.write
    return None


def _set_first_supported_attr(sg, candidates, value):
    """Set the first supported SG attribute from a list; returns the attribute name or None."""
    for attr in candidates:
        try:
            setattr(sg, attr, value)
            return attr
        except Exception:
            continue
    return None


def _apply_windowed_sine_if_possible(
    sg,
    shape,
    window_type,
    no_of_cycles_per_pulse,
):
    """Best-effort SG programming for a windowed sine pulse via ARB data.

    Point count = 10 points per cycle per pulse (minimum 64).
    Returns True if ARB waveform programming appears successful; otherwise False.
    """
    if (shape or "").strip().upper() != "SIN":
        return False

    writer = _get_writer(sg)
    if writer is None:
        return False

    try:
        cycles = max(1.0, float(no_of_cycles_per_pulse))
        n = max(64, int(ARB_POINTS_PER_CYCLE * cycles))
        t = np.linspace(0.0, 1.0, n, endpoint=False)
        carrier = np.sin(2 * np.pi * cycles * t)
        window = _window_array(window_type, n)
        waveform = carrier * window
        max_abs = np.max(np.abs(waveform))
        if max_abs > 0:
            waveform = waveform / max_abs
        csv = ",".join(f"{v:.6f}" for v in waveform)

        # Generic Keysight/Agilent 33500 SCPI sequence for volatile ARB data.
        writer(f"SOUR1:DATA VOLATILE,{csv}")
        writer("SOUR1:FUNC ARB")
        writer("SOUR1:FUNC:ARB VOLATILE")
        return True
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
    if phase:
        sg.phase = phase

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
    if phase:
        sg.phase = phase

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
    if no_of_cycles_per_pulse:
        sg.burst_ncycles = no_of_cycles_per_pulse
    if offset:
        sg.offset = offset
    if phase:
        sg.phase = phase

    # 4. Apply selected window type when possible (best effort).
    # If not supported by the SG backend, function continues with normal SIN burst.
    window_applied = _apply_windowed_sine_if_possible(
        sg,
        shape=shape,
        window_type=window_type,
        no_of_cycles_per_pulse=no_of_cycles_per_pulse,
    )

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

    # 5. Program pulse count/period on hardware when the driver exposes these knobs.
    programmed_count_attr = _set_first_supported_attr(
        sg,
        ["burst_count", "burst_ncycles_count", "burst_ncycles_num", "burst_cycles"],
        int(no_of_pulses),
    )
    programmed_period_attr = _set_first_supported_attr(
        sg,
        ["burst_internal_period", "burst_period", "burst_trigger_period"],
        float(inter_pulse_period),
    )

    sg.output = True

    if programmed_count_attr is not None and programmed_period_attr is not None:
        # Preferred path: one bus trigger emits a full burst train at programmed PRF.
        sg.trigger()
        sg.wait_for_trigger(timeout=max(total_burst_length, inter_pulse_period))
    else:
        # Fallback path for drivers without explicit pulse-count/period controls.
        for _ in range(int(no_of_pulses)):
            t0 = time.perf_counter()
            sg.trigger()
            sg.wait_for_trigger(timeout=max(pulse_width, inter_pulse_period))
            remain = inter_pulse_period - (time.perf_counter() - t0)
            if remain > 0:
                time.sleep(remain)

    print("Signal generator set to burst mode.")
    print(
        f"""Waveform: {sg.shape}, 
Frequency: {sg.frequency} Hz, 
Amplitude: {sg.amplitude} V,
Window Type: {window_type},
Window Applied: {window_applied},
No. Of Cycles Per Pulse: {sg.burst_ncycles},
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
