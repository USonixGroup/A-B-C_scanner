```json
{
  "cells": [
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "# Software Documentation: Ultrasound Scanning System",
        "",
        "This document provides a comprehensive technical overview of the Ultrasound Scanning GUI, including tab functions, parameter tables, and the mathematical foundations of the system."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 1: Config Tab",
        "",
        "**Purpose:**",
        "",
        "Configure hardware and system-wide settings before scanning. This includes device selection, communication parameters, and global calibration values.",
        "",
        "| Parameter Name        | Description                                                         | Default Value       | Acceptable Range                                                    |",
        "|----------------------|---------------------------------------------------------------------|---------------------|---------------------------------------------------------------------|",
        "| Rig Host / Port       | TCP/IP communication parameters for motion controller               | 192.168.1.250:5001  | Valid IPv4 address and port                                         |",
        "| Signal Generator      | PyMeasure driver model and VISA resource address                    | Agilent33500B       | 33500B, 33521A, 33220A (USB/TCPIP/GPIB)                             |",
        "| Oscilloscope          | Instrument name and VISA resource address                           | Lecroy              | Valid VISA resource address                                         |",
        "| Sampling Rate         | Discrete rates accepted by LeCroy oscilloscope                      | 100 MS/s            | 1 MS/s to 2.5 GS/s (dropdown)                                       |",
        "| Acquisition Mode     | Architecture: Software paced mode vs LeCroy sequence mode          | Software paced mode | Software paced mode, LeCroy sequence mode                           |",
        "| Acquisition Window    | Full horizontal duration across 10 LeCroy divisions (ms)            | 1.0 ms              | > 0 ms (Active only in Software paced mode)                         |",
        "| Delay (μs)            | Desired post-trigger delay before left edge of window (μs)          | 0.0 μs              | ≥ 0 μs (Active only in Software paced mode)                         |",
        "",
        "The Sampling Rate dropdown offers discrete rates accepted by LeCroy scopes (1, 2.5, 5, 10, 25, 50, 100, 250, 500 MS/s, 1 GS/s, 2.5 GS/s) and is stored in Hz in gui_settings.json. In Software paced mode, the scope captures per-pulse echoes and transmits them to the PC immediately (~100 ms LAN/USB latency), which overruns PRFs > 10 Hz. In LeCroy sequence mode, the scope captures all N pulses in hardware-segmented memory at exact PRF intervals (window = 1/PRF) and transfers the block after completion."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 2: Move Tab",
        "",
        "**Purpose:**",
        "",
        "Manually control the scanning rig's position. Useful for setup, calibration, and moving to the desired starting point.",
        "",
        "| Parameter Name   | Description                        | Default Value | Acceptable Range      |",
        "|------------------|------------------------------------|---------------|----------------------|",
        "| X Position       | Current X-axis position (mm)        | 0.000         | -5000 to 5000         |",
        "| Y Position       | Current Y-axis position (mm)        | 0.000         | -5000 to 5000         |",
        "| Z Position       | Current Z-axis position (mm)        | 0.000         | -5000 to 5000         |",
        "| Step Size        | Increment for manual moves (mm)     | 0.001         | 0.001                 |",
        "| Home All         | Return all axes to home position    | -             | -                    |",
        "",
        "Movement fields accept three decimal places with a 0.001 mm increment. The rig uses 5000 pulses/mm, so one controller pulse is 0.0002 mm = 0.2 μm = 200 nm. The backend rounds movement requests to whole pulses because fractional controller pulses are not supported."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 3: Pulses Tab",
        "",
        "**Purpose:**",
        "",
        "Define and configure the ultrasound pulse sequence, including timing, number of pulses, and preview/transmit options.",
        "",
        "| Parameter Name   | Description                                 | Default Value | Acceptable Range      |",
        "|------------------|---------------------------------------------|---------------|----------------------|",
        "| Pulse Count      | Number of pulses per scan point              | 1             | 1 – 1000             |",
        "| PRF (Hz)         | Pulse Repetition Frequency                   | 100           | 1 – 10000            |",
        "| Pulse Width (μs) | Duration of each pulse                       | 1.0           | 0.1 – 100.0          |",
        "| Amplitude (Vpp)  | Output voltage amplitude                     | 1.000         | 0.001 – 100.000      |",
        "| Preview          | Visualize pulse before transmission          | -             | -                    |",
        "",
        "Amplitude accepts three decimal places. The arrow controls use a 1 Vpp increment; values such as 1.123 Vpp can be entered directly."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 4: A-Mode Tab",
        "",
        "**Purpose:**",
        "",
        "Configure signal processing for A-Scan (single-point) measurements. These settings directly affect B-Mode and 3D-Mode calculations, as A-Mode processing is used as a basis for further imaging.",
        "",
        "| Parameter Name      | Description                                 | Default Value | Acceptable Range      |",
        "|---------------------|---------------------------------------------|---------------|----------------------|",
        "| Averaging           | Number of A-Scans to average                | 4             | 1 – 128              |",
        "| Detrend             | Remove DC offset from signal                | Enabled       | Enabled/Disabled     |",
        "| Filtering           | Apply bandpass filter                       | Enabled       | Enabled/Disabled     |",
        "| Filter Low (MHz)    | Low cutoff frequency                        | 1.0           | 0.1 – 10.0           |",
        "| Filter High (MHz)   | High cutoff frequency                       | 5.0           | 0.1 – 20.0           |",
        "| Tukey Window        | Windowing parameter for edge smoothing      | 0.25          | 0.0 – 1.0            |",
        "| Hilbert Transform   | Envelope extraction for amplitude analysis  | Enabled       | Enabled/Disabled     |",
        "",
        "**Note:**",
        "",
        "A-Mode settings (Averaging, Filtering, Tukey Window, Hilbert Transform) are used in B-Mode and 3D-Mode for envelope extraction, noise reduction, and feature enhancement. Adjust these parameters to optimize image quality in subsequent modes."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 5: B-Mode Tab",
        "",
        "**Purpose:**",
        "",
        "Configure and visualize 2D cross-sectional images (B-Scans) using A-Mode data at each scan point.",
        "",
        "| Parameter Name      | Description                                 | Default Value | Acceptable Range      |",
        "|---------------------|---------------------------------------------|---------------|----------------------|",
        "| Scan Area (mm²)     | Area to scan (X × Y)                        | 10 × 10       | Hardware limits      |",
        "| Step Size (mm)      | Distance between scan points                | 0.5           | 0.01 – 10.0          |",
        "| Colormap            | Color mapping for image display              | Gray          | Gray, Viridis, etc.  |",
        "| Metric              | Feature to display (e.g., peak, mean)        | Peak          | Peak, Mean, RMS, etc.|",
        "| Store Automatically | Save all acquired readings during the scan   | Selected      | Selected/Cleared     |",
        "",
        "<p style=\"color: #b42318;\"><strong>⚠ Autosave warning:</strong> Store Automatically is selected by default. Keep it selected to save every acquired reading and measurement. Clear it before starting only when you intentionally want in-memory preview and manual export without automatic files.</p>"
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 6: 3D-Mode Tab",
        "",
        "**Purpose:**",
        "",
        "Configure and visualize volumetric (3D) scans by stacking B-Mode slices or using A-Mode data at each 3D grid point.",
        "",
        "| Parameter Name      | Description                                 | Default Value | Acceptable Range      |",
        "|---------------------|---------------------------------------------|---------------|----------------------|",
        "| Scan Axis 1 Length (mm) | Travel along the first selected axis    | 20.000       | -10000 to 10000      |",
        "| Scan Axis 2 Length (mm) | Travel along the second selected axis   | 20.000       | -10000 to 10000      |",
        "| Scan Points             | Acquisition points per selected axis     | 2            | 1 – 10000            |",
        "| Post-Processing     | Apply A-Mode/B-Mode processing to 3D data   | Enabled       | Enabled/Disabled     |",
        "| Store Automatically | Save readings, manifests, matrices, metadata | Selected     | Selected/Cleared     |",
        "",
        "Negative 3D scan-axis lengths move in the negative direction of their selected axes. <p style=\"color: #b42318;\"><strong>⚠ Autosave warning:</strong> Store Automatically is selected by default. Keep it selected to save every reading and measurement. Clear it before starting only when you intentionally want in-memory preview and manual export without automatic files. When it is clear, the GUI shows a warning, restricts the scan type to A-Mode, and disables C-Mode and Pressure Field Mode because they require stored readings to construct their images.</p>"
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "## Step 7: Mathematics & Formulations",
        "",
        "### Dry Run Mode",
        "",
        "**Purpose:** Simulate signals and movement without hardware for testing and validation.",
        "",
        "The simulated A-Scan signal $s(t)$ consists of two inverted copies of the excitation burst plus noise, generated at the Config-tab sampling rate $f_s$:",
        "",
        "$$",
        "s(t) = -0.7\\,b(t - t_1) - 0.35\\,b(t - t_2) + \\epsilon(t)",
        "$$",
        "",
        "where:",
        "- $b(t)$ = windowed sine burst of the configured frequency $f$, cycles per pulse $N$ and window, with peak half the entered amplitude and duration $N/f$",
        "- $t_1$, $t_2$ = delays of the first and second echo",
        "- $\\epsilon(t)$ = Gaussian noise, 0.1 V standard deviation",
        "",
        "Movement simulation updates position as:",
        "",
        "$$",
        "\\vec{r}_{n+1} = \\vec{r}_n + \\Delta \\vec{r}",
        "$$"
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "### Actual Measurement Model",
        "",
        "**Signal Processing Chain:**",
        "",
        "1. **Time-of-Flight (ToF) Estimation:**",
        "",
        "$$",
        "ToF = \\arg\\max_t |s(t)|",
        "$$",
        "",
        "2. **Envelope Extraction (Hilbert Transform):**",
        "",
        "$$",
        "e(t) = |\\mathcal{H}[s(t)]|",
        "$$",
        "",
        "where $\\mathcal{H}[s(t)]$ is the analytic signal (Hilbert transform of $s(t)$).",
        "",
        "3. **Filtering:**",
        "",
        "$$",
        "s_{filtered}(t) = s(t) * h_{BP}(t)",
        "$$",
        "",
        "where $h_{BP}(t)$ is the bandpass filter kernel."
      ]
    },
    {
      "cell_type": "markdown",
      "metadata": {
        "language": "markdown"
      },
      "source": [
        "### Metrics & Field Characterization",
        "",
        "**Peak-to-Peak Pressure:**",
        "",
        "$$",
        "P_{pp} = \\max_t e(t) - \\min_t e(t)",
        "$$",
        "",
        "**Pulse Intensity Integral (PII):**",
        "",
        "$$",
        "PII = \\int_{t_0}^{t_1} [e(t)]^2 dt",
        "$$",
        "",
        "**Root Mean Square (RMS):**",
        "",
        "$$",
        "RMS = \\sqrt{\\frac{1}{T} \\int_{t_0}^{t_1} [e(t)]^2 dt}",
        "$$",
        "",
        "**Mean Value:**",
        "",
        "$$",
        "Mean = \\frac{1}{T} \\int_{t_0}^{t_1} e(t) dt",
        "$$",
        "",
        "**Variance:**",
        "",
        "$$",
        "Var = \\frac{1}{T} \\int_{t_0}^{t_1} (e(t) - Mean)^2 dt",
        "$$",
        "",
        "where $T = t_1 - t_0$ is the integration window.",
        "",
        "### Oscilloscope Horizontal Timebase & Delay Compensation",
        "",
        "On Teledyne LeCroy oscilloscopes, the acquisition screen comprises 10 horizontal divisions. The horizontal delay parameter defines the timestamp at the **center of the grid** (Division 5). When Delay = 0 s, the trigger event ($t = 0$) sits at the center:",
        "",
        "$$",
        "\\begin{aligned}",
        "\\text{Left edge (Div 0):} & \\quad -5 \\times \\text{Time/div} \\quad (\\text{pre-trigger}) \\\\",
        "\\text{Center (Div 5):} & \\quad 0\\,\\text{s} \\quad (\\text{trigger event}) \\\\",
        "\\text{Right edge (Div 10):} & \\quad +5 \\times \\text{Time/div} \\quad (\\text{post-trigger}) \\\\",
        "\\text{Total window length } W: & \\quad 10 \\times \\text{Time/div}",
        "\\end{aligned}",
        "$$",
        "",
        "To make the visible acquisition window start at the left edge with a user-specified delay $\\Delta t_{\\text{desired}}$ and display $[\\Delta t_{\\text{desired}},\\, \\Delta t_{\\text{desired}} + W]$, the 5-division center offset is compensated:",
        "",
        "$$",
        "\\Delta t_{\\text{center}} = \\Delta t_{\\text{desired}} + \\frac{W}{2} = \\Delta t_{\\text{desired}} + (5 \\times \\text{Time/div})",
        "$$",
        "",
        "In LeCroy SCPI (`TRIG_DELAY` / `TRDL`) and Automation VBScript (`app.Acquisition.Horizontal.HorOffset`), the parameter defines the position of the trigger point ($t = 0$) relative to the grid center. Because the trigger is to the left of the center for post-trigger acquisitions, a **negative sign** is programmed:",
        "",
        "$$",
        "\\text{HorOffset} = -\\Delta t_{\\text{center}} = -[\\Delta t_{\\text{desired}} + (5 \\times \\text{Time/div})]",
        "$$",
        "",
        "$$",
        "\\text{TRIG\\_DELAY} = -\\Delta t_{\\text{center}}",
        "$$",
        "",
        "### Multi-Pulse PRF Generation & Hardware Timer",
        "",
        "When executing multiple pulses ($N_{\\text{pulses}} > 1$) at a pulse repetition frequency $PRF$:",
        "",
        "1. **Timing Relations:**",
        "",
        "$$",
        "T_{\\text{pulse}} = \\frac{N_{\\text{cycles}}}{f_{\\text{carrier}}}, \\qquad T_{\\text{inter-pulse}} = \\frac{1}{PRF}",
        "$$",
        "",
        "2. **Hardware Timer Burst (Option A):**",
        "The signal generator's internal hardware timer fires at interval $T_{\\text{inter-pulse}} = 1/PRF$ for exactly $N_{\\text{pulses}}$ triggers with TTL sync pulses:",
        "",
        "$$",
        "\\text{TRIG1:TIM} = \\frac{1}{PRF}, \\qquad \\text{TRIG1:COUN} = N_{\\text{pulses}}",
        "$$",
        "",
        "3. **Native Sine vs ARB Windowing:**",
        "- Native Sine: $\\text{BURS:NCYC} = N_{\\text{cycles}}$, continuous sine carrier gated for $N_{\\text{cycles}}$.",
        "- ARB Windowed Sine: The uploaded arbitrary waveform spans the complete windowed pulse ($N_{\\text{cycles}}$ shaped by window). From the hardware perspective, 1 iteration of the arbitrary waveform equals 1 pulse, so $\\text{BURS:NCYC} = 1$ and arbitrary repetition rate is $f_{\\text{arb}} = f_{\\text{carrier}} / N_{\\text{cycles}}$."
      ]
    }
  ]
}
```