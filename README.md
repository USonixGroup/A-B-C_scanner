A/B/C-Scanner GUI (api/gui_main.py)
===================================

This file implements a user-friendly Tkinter GUI to collect pulse
generator arguments and scanning parameters and then run an A/B/C scan.

How to run
----------

From the repository root run:

```bash
python3 api/gui_main.py
```

Notes and caveats
-----------------
- The GUI imports and uses hardware-related modules (pymeasure, pyvisa and
  the project's `Oscilloscope.py` and `rig_function.py`) when you click
  Start. Importing the module itself is side-effect free.
- Make sure you have a working X11/Wayland DISPLAY when launching the GUI
  (for headless systems use an X server or run with X forwarding).
- Dependencies are listed in `requirements.txt`. Tkinter is required and is
  usually provided by your system Python (package name varies by distro,
  e.g. `python3-tk` on Debian/Ubuntu).

Optional runtime dependencies
-----------------------------
- The GUI supports richer features when optional packages are installed:
  - `pandas` + `plotly` — interactive CSV plotting embedded in the GUI (fallback opens your browser)
  - `Pillow` (PIL) — better image handling for the About dialog and previews
  - `tkinterweb` — embed HTML/Plotly output directly inside the Tk window
  - `pymeasure`, `pyvisa` — instrument drivers used when running real hardware scans

If these are missing the GUI will still run; some features will fallback or be disabled with a warning/error message.

Open-source / License
---------------------
This code is available as open-source under the MIT License. See the
project `LICENSE` file for the full text.
- The GUI does not change any existing code. It will attempt to connect to
  devices when the scan starts — please ensure hardware addresses in the
  GUI fields match your devices.
