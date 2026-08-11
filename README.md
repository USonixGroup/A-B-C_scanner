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
| GitHub release | **Unreleased** — no GitHub release or version tag has been published yet |
| Main-branch version | **main@8b089f2** |
| Main-branch commit | [`8b089f28e747a760e27b85275c479c162de33d80`](https://github.com/USonixGroup/A-B-C_scanner/commit/8b089f28e747a760e27b85275c479c162de33d80) |
| Main-branch commit date | 5 October 2025 |

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
7. Review the plot and log, and use the export controls to save processed results.

Scan lengths are specified in millimetres. For B-mode, a negative scan length moves in the negative direction of the selected scan axis. In 3D mode, the two scan axes must be different; the remaining axis is used as the waveform depth axis.

## Scan modes

### A-mode

The rig moves by the requested X/Y/Z increments and records the configured number of pulse echoes. Acquisitions can be averaged and high-pass filtered. When a non-zero speed of sound is supplied, the horizontal axis is converted from time to distance.

### B-mode

The rig performs a linear scan and captures an A-scan at every position. The waveforms are stacked to form a B-mode matrix. Live preview and matrix export are available from the B-mode tab.

### 3D, C-mode, and pressure-field measurements

The 3D workflow scans two spatial axes in a raster or zigzag pattern and captures a waveform at each grid point. The acquired volume can be viewed as:

- an A-mode waveform collection;
- a C-mode image calculated from a user-defined time gate; or
- a pressure-field map calculated using a selected statistical metric.

Pressure-field processing can optionally apply a high-pass or band-pass Butterworth filter. Cut-off frequencies must remain below the Nyquist frequency implied by the oscilloscope sampling rate.

## Data and settings

Runtime data are written under `data/` using numbered folders and filenames so earlier runs are preserved. Depending on the mode, outputs include waveform CSV files, point manifests, metadata, and `.npy` matrices. The GUI also provides explicit export actions for data, figures, and logs.

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
Code/                      Legacy code and example acquisition data
command/                   Hardware command notes
environment.yml            Conda environment definition
requirements.txt           pip dependencies
```

## Troubleshooting

- **The GUI does not start:** verify that the active environment contains `PySide6`, `matplotlib`, and `pandas`, and launch from the repository root.
- **A VISA device is not found:** confirm its address in the Config tab, check the cable/network route, and inspect `pyvisa.ResourceManager().list_resources()` in the same environment.
- **The rig is unreachable:** verify that the host and port are on the same reachable network and that no other process owns the controller connection.
- **A filter is rejected:** reduce its cut-off frequency or increase the acquisition sampling rate so the cut-off is below Nyquist.
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
