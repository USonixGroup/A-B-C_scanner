# A/B/C Ultrasound Scanner

A Python GUI and hardware-control library for automated ultrasound scanning and acoustic pressure-field characterisation. The application coordinates a three-axis positioning rig, a signal generator, and an oscilloscope to acquire and visualise pulse-echo measurements.

The software supports:

- **A-mode** — acquire one or more depth waveforms at a selected rig position.
- **B-mode** — move along one axis and stack A-scans into a two-dimensional cross-section.
- **3D mode** — scan a plane using raster or zigzag motion and retain the A-scan at every point.
- **C-mode** — reduce a time-gated region of each waveform to a metric and display a planar image.
- **Pressure-field mode** — map waveform statistics across a plane, with optional high-pass or band-pass filtering.

## Release and version

| Item | Value |
| --- | --- |
| GitHub release | **Release 1** |
| Main-branch version | **v1.0.0** |
| Main-branch release date | August 2026 |

These values describe the current published `main` branch on GitHub. Development branches or local working copies may contain newer changes. Once a tagged release is published, the release tag should replace the commit-based version throughout the application and documentation.

> [!CAUTION]
> This application can command motorised hardware and enable a signal-generator output. Confirm the rig limits, axis directions, clearances, VISA addresses, waveform amplitude, and pulse timing before starting a hardware scan. Use **Dry Run** first when validating a setup.

## Features

- PySide6 desktop interface with live Matplotlib previews.
- Configuration and connection tests for the rig, signal generator, and oscilloscope.
- Manual three-axis motion, saved positions, and return-to-position control.
- Continuous and burst excitation with configurable frequency, amplitude, cycles, pulse count, PRF, delay, and waveform window.
- Synthetic echo generation in dry-run mode, allowing workflows to be tested without instruments.
- Configurable high-pass filtering and depth conversion using the speed of sound.
- C-mode gate selection and Max, Mean, RMS, Kurtosis, or Energy maps.
- Pressure-field statistics including extrema, mean, median, RMS, variance, skewness, kurtosis, entropy, and energy.
- CSV, NumPy matrix, plot, and log export.
- Automatic persistence of GUI settings in `api/settings/gui_settings.json`.

## Requirements

- Python 3.10 or newer is recommended.
- A desktop environment capable of displaying a Qt application.
- For hardware operation:
  - a compatible Agilent/Keysight signal generator supported by PyMeasure;
  - a VISA-accessible oscilloscope (the acquisition code currently contains LeCroy commands); and
  - a PMX-4ET-SA-compatible three-axis controller reachable over TCP/IP.

The main Python dependencies are PySide6, NumPy, pandas, Matplotlib, SciPy, scikit-image, PyVISA, pyvisa-py, and PyMeasure.

## Installation

Clone the repository and enter its root directory, then use either Conda or `pip`.

### Conda

```bash
conda env create -f environment.yml
conda activate abc_scanner
```

### pip

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Linux/macOS
source .venv/bin/activate
```

Then install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Depending on the instrument connection, a vendor VISA runtime may also be required. `pyvisa-py` provides a pure-Python backend for supported interfaces.

## Running the GUI

From the repository root:

```bash
python api/gui_main.py
```

The launcher imports the application from `api/src/pyside_gui.py`. Run it from the repository root so package imports and data paths resolve consistently.

## Quick start

1. Open the **Config** tab and enter the signal-generator VISA address, oscilloscope VISA address, and rig host/port.
2. Use the connection test before enabling hardware acquisition.
3. Set the excitation waveform in **Excitation Mode**.
4. Select **A-Mode**, **B-Mode**, or **3D-Mode** and define the position, axes, scan lengths, and number of points.
5. Enable **Dry Run** to test motion/acquisition logic with generated echoes, or leave it disabled to use connected hardware.
6. Enable live preview if required, then start the scan.
7. Select **Store Automatically** next to **Live Preview** to save acquired readings and measurements during the scan. It is selected by default.
8. Review the plot and log, and use the export controls to save processed results.

<p style="color: #b42318;"><strong>⚠ Autosave warning:</strong> Clear <strong>Store Automatically</strong> before starting a scan only when you intentionally do not want readings and measurements written to disk. When it is selected, every acquired reading and measurement is stored automatically. When it is clear, results remain in memory for preview and manual export only. In 3D Mode, clearing it restricts the scan type to A-Mode because C-Mode and Pressure Field Mode require stored readings to construct their images.</p>

In **Excitation Mode**, amplitude accepts up to three decimal places in Vpp. The amplitude arrow controls use a `1 Vpp` increment; values such as `1.123 Vpp` can be entered directly.

Scan lengths are specified in millimetres. For B-mode, a negative scan length moves in the negative direction of the selected scan axis. In 3D mode, negative lengths move in the negative direction of their respective scan axes. The two scan axes must be different; the remaining axis is used as the waveform depth axis.

### Movement precision

The Move Rig and A-mode X/Y/Z fields, B-mode scan length, and 3D scan-axis lengths accept three decimal places with `0.001 mm` increments. The configured conversion is 5000 pulses/mm, so one controller pulse is $0.0002\,\mathrm{mm} = 0.2\,\mathrm{\mu m} = 200\,\mathrm{nm}$. A distance of $0.0001\,\mathrm{mm}$ is $100\,\mathrm{nm}$, not $0.1\,\mathrm{nm}$, and rounds to zero pulses. The backend rounds movement requests to whole pulses because fractional controller pulses are not supported.

### Sampling rate

The **Sampling Rate** field on the Config tab is entered in **MHz** (for example `100` for 100 MS/s). It is stored in `api/settings/gui_settings.json` in Hz, and all internal calculations use Hz. A blank, non-numeric, zero, or negative value is rejected with a message in the log.

- **Dry Run:** synthetic echoes are generated at exactly this rate, and the same value is used for filtering.
- **Hardware scans:** the field is the rate *requested* from the oscilloscope. The rate the scope actually used is read from each captured waveform and is used for all A-mode, B-mode, and 3D-mode filtering, envelope, gating, and Nyquist calculations. If it differs from the field by more than 2%, the log shows a `WARNING`.
- **Saved scans:** C-mode and pressure-field **Apply**, and the 3D live preview, use the rate recorded in the time axis of each stored waveform rather than the field. A `WARNING` is logged when it differs from the field by more than 2%. The field is only a fallback when no rate can be derived from the data.

Choose a rate of at least 10 times the excitation frequency. If the high-pass cut-off is at or above the Nyquist frequency ($f_s/2$), the filter is disabled and an error is logged.

Dry-run echoes consist of two inverted copies of the excitation burst (the second at half the amplitude of the first) with additive Gaussian noise of 0.1 V standard deviation. The burst lasts `cycles / frequency` and uses the selected window. They are intended for checking the workflow, not for validating hardware.

## Scan modes

### A-mode

The rig moves by the requested X/Y/Z increments and records the configured number of pulse echoes. Acquisitions can be averaged and high-pass filtered. With more than one pulse, Live Preview shows the current echo and the running average, and the final plot shows the first, last, and averaged echoes with the envelope of the average. If the echoes to be averaged have different lengths or misaligned time axes (A-, B-, and 3D-mode), the log shows an `averaging WARNING`. When a non-zero speed of sound is supplied, the horizontal axis is converted from time to distance.

### B-mode

The rig performs a linear scan and captures an A-scan at every position. The waveforms are stacked to form a B-mode matrix. Live preview and matrix export are available from the B-mode tab.

### 3D, C-mode, and pressure-field measurements

The 3D workflow scans two spatial axes in a raster or zigzag pattern and captures a waveform at each grid point. The acquired volume can be viewed as:

- an A-mode waveform collection;
- a C-mode image calculated from a user-defined time gate; or
- a pressure-field map calculated using a selected statistical metric.

Pressure-field processing can optionally apply a high-pass or band-pass Butterworth filter. Cut-off frequencies must remain below the Nyquist frequency implied by the sampling rate recorded in the stored waveforms (see [Sampling rate](#sampling-rate)).

## Data and settings

Runtime data are written under `data/` using numbered folders and filenames so earlier runs are preserved. Depending on the mode, outputs include waveform CSV files, point manifests, metadata, and `.npy` matrices. The GUI also provides explicit export actions for data, figures, and logs.

Every stored waveform CSV starts with `# key: value` header lines that give the scan mode (`A-mode`, `B-mode`, or `3D-mode` with its `scan_type`), the pulse number (`pulse_index` and `pulse_total`, or `average`), and both sampling rates: `sampling_rate_config_hz` (Config field) and `sampling_rate_actual_hz` (`sampling_rate_actual_source` is `scope` when reported by the oscilloscope, otherwise `time_axis`, derived from the recorded time axis). Single-pulse data hold the raw echo as acquired; averaged files hold the detrended average of all pulses at that point. With **Store Automatically** selected, each run folder contains:

| Mode | Folder | Contents |
| --- | --- | --- |
| A-mode | `data/a_mode_scan_NNN/` | `a_scan_pulse_NNN.csv` (one per pulse) and `a_scan_average.csv` (average and envelope) |
| B-mode | `data/b_mode_scan_NNN/` | `point_manifest.csv`, `b_mode_measurements/point_NNNN.csv` (average per point), and the raw single-pulse echoes in `pulses.npy` with `pulses_time_s.npy` and `pulses_metadata.txt` |
| 3D, C-mode, pressure field | `data/scan_NNN/` | `point_manifest.csv`, `a_mode_measurements/` (or `pressure_mode_measurements/`) with `point_*_rRRR_cCCC.csv` (average per point), and the raw single-pulse echoes in `pulses.npy` with `pulses_time_s.npy` and `pulses_metadata.txt`; the averaged matrix `a_mode_matrix_NNN.npy` and its `_metadata.txt` are written to `data/` |

The point manifests list, for every point, its position, the number of pulses averaged, the Config and actual sampling rates, the relative path of the averaged CSV, and the index of its raw echoes in `pulses.npy` (`pulse_array_index`).

**Raw per-pulse array.** B-mode and 3D scans write all single-pulse echoes into one memory-mapped float32 array instead of thousands of CSV files: shape `(points, pulses, samples)` for B-mode and `(rows, columns, pulses, samples)` for 3D, where rows are Axis 2 and columns are Axis 1. The array is filled as the scan runs, unreached entries stay `NaN`, and echoes shorter than the first one are `NaN`-padded. The time axis is stored once in `pulses_time_s.npy`, and `pulses_metadata.txt` records the shape, units, scan mode, axes, and both sampling rates. Load it with `np.load("pulses.npy", mmap_mode="r")`. Tick **Also save per-pulse CSV files** on the Config tab to additionally write one CSV per pulse (`b_mode_measurements/pulses/` or `<measurements folder>/pulses/`); this is off by default because large scans create very many files. A-mode always writes its few per-pulse CSVs.

**Crash safety.** Data are written as the scan progresses: each pulse (A-mode CSV or `pulses.npy` entry), each averaged point CSV, and each manifest row are saved immediately, and `pulses.npy` is flushed after every point. If a scan fails, is stopped, or the program is killed, all completed points remain on disk and the manifest lists them; the point in progress keeps the pulses already acquired (the rest is `NaN`). The 3D averaged matrix `a_mode_matrix_NNN.npy` is written when the scan ends, stops, or fails with an error, but not after a hard process kill; it can be rebuilt from the point CSVs. A-mode writes `a_scan_average.csv` at the end of the run, but the per-pulse CSVs it is computed from are already saved. The metadata text file and the A-mode and B-mode matrix exports also record the scan mode, pulse count, and both sampling rates.

The application saves its current configuration to:

```text
api/settings/gui_settings.json
```

This file may contain local network addresses and instrument identifiers. Review it before sharing logs or configuration files outside the lab.

## Library modules

The hardware and processing functions can also be imported independently:

| Module | Purpose |
| --- | --- |
| `api.src.rig_function` | TCP connection, position queries, homing, and three-axis motion |
| `api.src.Signal_function` | Continuous, triggered, and burst signal generation |
| `api.src.Oscilloscope` | VISA connection, LeCroy configuration, waveform capture, and CSV output |
| `api.src.scan_utils` | Scan-step calculations |
| `api.src.dummy_signal_generator` | Synthetic echo generation for offline testing |

For example, a non-moving rig connection check can be performed with:

```python
from api.src.rig_function import check_connection

reachable = check_connection(host="192.168.1.250", port=5001)
print(reachable)
```

These modules are currently a source-level API rather than a packaged, versioned public interface, so review their docstrings and function signatures before integrating them into another application.

## Timing verification

The repository includes a command-line utility for checking burst count and PRF spacing on a connected signal generator:

```bash
python -m api.src.test_prf_timing --sg-address "USB0::...::INSTR" --frequency 1000000 --cycles 60 --pulses 10 --prf 1000
```

Use `python -m api.src.test_prf_timing --help` for all options.

## Repository layout

```text
api/
  gui_main.py              GUI launcher
  src/                     GUI, hardware control, acquisition, and processing
  settings/                Persisted application settings
  docs/                    Additional generated/notebook documentation
data/                      Scan output created at runtime
command/                   Hardware command notes
environment.yml            Conda environment definition
requirements.txt           pip dependencies
```

## Troubleshooting

- **The GUI does not start:** verify that the active environment contains `PySide6`, `matplotlib`, and `pandas`, and launch from the repository root.
- **A VISA device is not found:** confirm its address in the Config tab, check the cable/network route, and inspect `pyvisa.ResourceManager().list_resources()` in the same environment.
- **The rig is unreachable:** verify that the host and port are on the same reachable network and that no other process owns the controller connection.
- **A filter is rejected:** reduce its cut-off frequency or increase the acquisition sampling rate (Config tab, in MHz) so the cut-off is below Nyquist.
- **A sampling-rate WARNING appears:** the oscilloscope used a different rate from the Config field. The actual scope rate is used for the calculations; update the field to match it, or adjust the scope timebase/memory settings.
- **No hardware is available:** enable **Dry Run** to exercise acquisition, plotting, processing, and export with synthetic signals.

## License

This project is licensed under the [Apache License 2.0](LICENSE). You may use, modify, and distribute it, including for commercial purposes, subject to the licence conditions. Redistributions must preserve the licence and copyright notices, and modified files must state that they were changed.

The software is provided **as is**, without warranties or conditions of any kind. In particular, the licence does not guarantee that the software is suitable or safe for a specific instrument, scanner, clinical application, or hardware configuration. See the `LICENSE` file for the complete terms, including the patent grant and limitation of liability.

Copyright © 2026 **UCL Ultrasonics Group, UCL Mechanical Engineering**. Redistributed copies must retain the attribution information in [NOTICE](NOTICE) as required by the Apache License 2.0.

## Citation and acknowledgement

If this software, its source code, or this repository contributes to a publication, presentation, thesis, report, dataset, or other research output, cite the repository and acknowledge **UCL Ultrasonics Group, UCL Mechanical Engineering**.

Suggested citation:

> UCL Ultrasonics Group, UCL Mechanical Engineering (2026). *A/B/C Ultrasound Scanner* [Computer software]. https://github.com/USonixGroup/A-B-C_scanner

GitHub and compatible reference managers can generate a citation from the repository's [CITATION.cff](CITATION.cff). For reproducible work, include the software version or Git commit hash used. If a DOI is assigned through Zenodo or another archive, use the archived release citation in preference to the repository URL.
