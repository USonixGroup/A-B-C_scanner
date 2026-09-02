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
        "| Parameter Name      | Description                                 | Default Value | Acceptable Range         |",
        "|--------------------|---------------------------------------------|---------------|-------------------------|",
        "| Device Port        | Serial port for hardware connection          | COM3          | Any valid serial port    |",
        "| Baud Rate          | Communication speed (baud)                   | 115200        | 9600 – 921600            |",
        "| Calibration Factor | System gain calibration                      | 1.0           | 0.1 – 10.0               |",
        "| Save Directory     | Folder for saving scan data                  | ./data        | Any valid path           |"
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
        "The simulated A-Scan signal $s(t)$ is generated as a sum of Gaussian-modulated sinusoids with added noise:",
        "",
        "$$",
        "s(t) = \\sum_{i=1}^N A_i \\exp\\left(-\\frac{(t - t_i)^2}{2\\sigma^2}\\right) \\sin(2\\pi f_i t) + \\epsilon(t)",
        "$$",
        "",
        "where:",
        "- $A_i$ = amplitude of the $i$-th echo",
        "- $t_i$ = time-of-flight for the $i$-th echo",
        "- $\\sigma$ = width of the echo",
        "- $f_i$ = center frequency",
        "- $\\epsilon(t)$ = random noise",
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
        "where $T = t_1 - t_0$ is the integration window."
      ]
    }
  ]
}
```