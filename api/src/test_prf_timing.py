"""Hardware timing verification for burst pulse spacing.

This script sends a burst through the configured signal generator and records the
exact trigger timestamps seen by Burst_generate. It then compares measured pulse
spacing against the requested PRF period.
"""

from __future__ import annotations

import argparse
import importlib
import statistics
import time
from dataclasses import dataclass

from api.src.Signal_function import Burst_generate


@dataclass
class TimingResult:
    pulse_count: int
    expected_pulses: int
    expected_period_s: float
    mean_period_s: float
    min_period_s: float
    max_period_s: float
    max_abs_error_s: float


class TriggerRecorder:
    """Proxy that records trigger timestamps while forwarding SG operations."""

    def __init__(self, signal_generator):
        object.__setattr__(self, "_sg", signal_generator)
        object.__setattr__(self, "trigger_times", [])

    def __getattr__(self, name):
        return getattr(self._sg, name)

    def __setattr__(self, name, value):
        if name in {"_sg", "trigger_times"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._sg, name, value)

    def trigger(self):
        self.trigger_times.append(time.perf_counter())
        return self._sg.trigger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate SG pulse timing for PRF bursts"
    )
    parser.add_argument(
        "--sg-address", default=None, help="Signal generator VISA address"
    )
    parser.add_argument("--shape", default="SIN", help="Waveform shape")
    parser.add_argument("--window", default="Hanning", help="Window function")
    parser.add_argument(
        "--frequency", type=float, default=1_000_000.0, help="Carrier frequency (Hz)"
    )
    parser.add_argument("--amplitude", type=float, default=1.0, help="Amplitude (Vpp)")
    parser.add_argument("--cycles", type=float, default=60.0, help="Cycles per pulse")
    parser.add_argument("--pulses", type=int, default=10, help="Number of pulses")
    parser.add_argument(
        "--prf", type=float, default=1000.0, help="Pulse repetition frequency (Hz)"
    )
    parser.add_argument(
        "--tolerance-percent",
        type=float,
        default=10.0,
        help="Allowed max spacing error as a percent of expected period",
    )
    return parser.parse_args()


def connect_signal_generator(address: str | None):
    pm = importlib.import_module("pymeasure.instruments.agilent")
    agilent_cls = getattr(pm, "Agilent33500", None)
    if agilent_cls is None:
        raise RuntimeError("Agilent33500 driver is not available in pymeasure.")
    return agilent_cls(address) if address else agilent_cls()


def evaluate_timing(
    trigger_times: list[float], expected_pulses: int, prf_hz: float
) -> TimingResult:
    expected_period = 1.0 / prf_hz
    measured_periods = [b - a for a, b in zip(trigger_times, trigger_times[1:])]

    if measured_periods:
        mean_period = statistics.mean(measured_periods)
        min_period = min(measured_periods)
        max_period = max(measured_periods)
        max_abs_error = max(
            abs(period - expected_period) for period in measured_periods
        )
    else:
        mean_period = 0.0
        min_period = 0.0
        max_period = 0.0
        max_abs_error = 0.0

    return TimingResult(
        pulse_count=len(trigger_times),
        expected_pulses=expected_pulses,
        expected_period_s=expected_period,
        mean_period_s=mean_period,
        min_period_s=min_period,
        max_period_s=max_period,
        max_abs_error_s=max_abs_error,
    )


def main() -> int:
    args = parse_args()

    if args.prf <= 0:
        raise SystemExit("PRF must be > 0 Hz.")
    if args.frequency <= 0:
        raise SystemExit("Frequency must be > 0 Hz.")
    if args.cycles <= 0 or args.pulses <= 0:
        raise SystemExit("Cycles and pulses must be > 0.")

    pulse_width = args.cycles / args.frequency
    prf_period = 1.0 / args.prf
    if pulse_width >= prf_period:
        raise SystemExit(
            "Invalid PRF/pulse combination: pulse width must be smaller than PRF period."
        )

    sg = None
    try:
        sg = connect_signal_generator(args.sg_address)
        print("Connected to signal generator.")

        recorder = TriggerRecorder(sg)
        Burst_generate(
            recorder,
            shape=args.shape,
            frequency=args.frequency,
            amplitude=args.amplitude,
            no_of_cycles_per_pulse=args.cycles,
            no_of_pulses=args.pulses,
            prf=args.prf,
            window_type=args.window,
        )

        result = evaluate_timing(recorder.trigger_times, args.pulses, args.prf)
        tolerance_s = result.expected_period_s * (args.tolerance_percent / 100.0)

        print(f"Expected pulses: {result.expected_pulses}")
        print(f"Measured pulses: {result.pulse_count}")
        print(f"Expected period: {result.expected_period_s:.9f} s")
        print(f"Mean period: {result.mean_period_s:.9f} s")
        print(f"Min period: {result.min_period_s:.9f} s")
        print(f"Max period: {result.max_period_s:.9f} s")
        print(f"Max abs error: {result.max_abs_error_s:.9f} s")
        print(f"Allowed max error: {tolerance_s:.9f} s ({args.tolerance_percent:.2f}%)")

        pass_count = result.pulse_count == result.expected_pulses
        pass_spacing = result.max_abs_error_s <= tolerance_s
        if pass_count and pass_spacing:
            print("PASS: Pulse count and PRF spacing are within tolerance.")
            return 0

        if not pass_count:
            print("FAIL: Pulse count mismatch.")
        if not pass_spacing:
            print("FAIL: PRF spacing is outside tolerance.")
        return 2
    finally:
        if sg is not None:
            try:
                sg.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
