"""
Simple Tkinter GUI to collect pulse generator arguments and scanning parameters
and run a B-scan using the existing modules in this project. This file does
not modify any existing files and only creates hardware connections when the
user clicks Start.

Usage: python3 -m Code.gui_main

"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
from tkinter import BooleanVar
from tkinter import filedialog
from scan_utils import compute_step
import os
import time
import json

# Lazy imports for hardware are done inside the worker thread so importing this
# module does not attempt to open instruments or sockets.


class ScanGUI:
    def __init__(self, root):
        import subprocess
        import tkinter.font as tkfont
        import sys

        self.root = root
        # Global font and modern theme
        root.option_add("*Font", ("Arial", 12))
        # Prefer ttkthemes.ThemedStyle so we can set 'arc' explicitly when present.
        # Ensure `style` exists so subsequent styling code can safely reference it.
        style = ttk.Style()
        # Try to apply the ARC theme if available. Prefer ttkthemes.ThemedStyle,
        # otherwise attempt to use the 'arc' theme via ttk.Style().theme_use.
        try:
            from ttkthemes import ThemedStyle
            # create a themed style linked to the root; if it works, replace style
            try:
                themed = ThemedStyle(self.root)
                style = themed
                # Try to set arc explicitly when supported
                try:
                    themed.set_theme('arc')
                except Exception:
                    try:
                        themed.theme_use('arc')
                    except Exception:
                        pass
            except Exception:
                # If ThemedStyle exists but initialization failed, keep base style
                pass
        except Exception:
            # Fallback: use the builtin ttk.Style we already created and try to use 'arc'
            try:
                if 'arc' in style.theme_names():
                    style.theme_use('arc')
            except Exception:
                # silently continue with default theme
                pass

        # No micromamba: prefer pip-only workflows and the current
        # interpreter for subprocesses. Subprocesses will use sys.executable.

        # Color palette
        accent = '#0437F2'   # requested blue
        bg = '#f4f7ff'       # light background
        panel = '#ffffff'    # card/panel background
        text = '#1f2937'

        # Notebook/tab styling (sleek, bold tabs)
        style.configure('TNotebook', background=bg)
        style.configure('TNotebook.Tab', font=("Arial", 12, 'bold'), padding=(12, 6), foreground=text)
        # Highlight the active tab with accent underline where supported
        try:
            style.map('TNotebook.Tab', background=[('selected', panel)])
        except Exception:
            pass

        # Frame/Label styling
        style.configure('TFrame', background=bg)
        style.configure('TLabelFrame', background=panel, padding=10)
        style.configure('TLabel', background=panel, foreground=text)

        # Base button tweaks
        style.configure('TButton', padding=6, font=("Arial", 11))

        # Accent (blue) button style for Start/Move actions: filled blue with white text
        try:
            style.configure('Accent.TButton', foreground='white', background=accent,
                            font=("Arial", 12, 'bold'), padding=(10, 6), relief='flat')
            style.map('Accent.TButton',
                      background=[('active', '#0329e6'), ('pressed', '#021bd1')],
                      foreground=[('disabled', "#1d1c1c")])
        except Exception:
            # Fallback: at least set font if styling not supported
            style.configure('Accent.TButton', font=("Arial", 12, 'bold'))
            # Action buttons: neutral background when idle; light green when active; navy text
            try:
                style.configure('Action.TButton', font=("Arial", 11), foreground='#001f5b')
                style.map('Action.TButton',
                          background=[('active', '#dff5d9'), ('pressed', '#cfeecf')],
                          foreground=[('!disabled', '#001f5b')])
            except Exception:
                style.configure('Action.TButton', foreground='#001f5b')

            # Active action style (explicit green background while running)
            try:
                style.configure('ActionActive.TButton', font=("Arial", 11), foreground='#001f5b', background='#9fe29f')
                style.map('ActionActive.TButton',
                          background=[('active', '#9fe29f'), ('pressed', '#85d685')],
                          foreground=[('!disabled', '#001f5b')])
            except Exception:
                style.configure('ActionActive.TButton', foreground='#001f5b')

        root.title("A/B/C-Scanner")

        # Replace the top-right About button with an About menu -> About A/B/C-Scanner
        menubar = tk.Menu(root)
        about_menu = tk.Menu(menubar, tearoff=0)
        about_menu.add_command(label='About A/B/C-Scanner', command=self.show_about)
        menubar.add_cascade(label='About', menu=about_menu)
        root.config(menu=menubar)

        # Notebook with three tabs: Config | Move Rig | A/Transmit | B/C
        # Top bar containing the main notebook and a right-aligned About button
        topbar = ttk.Frame(root)
        topbar.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)
        topbar.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(topbar)
        self.notebook = notebook
        notebook.grid(row=0, column=0, sticky='nsew')

        # Allow the notebook to expand when the root window is resized
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)

        # Tabs
        self.config_tab = ttk.Frame(notebook)
        self.bc_tab = ttk.Frame(notebook)
        self.move_tab = ttk.Frame(notebook)
        self.a_tab = ttk.Frame(notebook)
        # About tab has been moved to a separate About dialog (button on topbar)
        self.about_tab = None

        notebook.add(self.config_tab, text='Config')
        notebook.add(self.move_tab, text='Move Rig')
        notebook.add(self.a_tab, text='A/Transmit Mode')
        notebook.add(self.bc_tab, text='B/C Mode')

        # -- Config tab widgets --
        cfg = self.config_tab

        # create a few distinct labelled frames to separate SG / Osc / Rig
        # create lightweight styles for visual separation
        try:
            style.configure('SG.TLabelframe', background='#eaf4ff')
            style.configure('OSC.TLabelframe', background='#fff8e6')
            style.configure('RIG.TLabelframe', background='#e9fff2')
            style.configure('SG.TLabelframe.Label', background='#eaf4ff')
            style.configure('OSC.TLabelframe.Label', background='#fff8e6')
            style.configure('RIG.TLabelframe.Label', background='#e9fff2')
        except Exception:
            pass

        # Signal Generator section
        sg_frame = ttk.LabelFrame(cfg, text='Signal Generator', style='SG.TLabelframe')
        sg_frame.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=6, pady=(6, 3))
        ttk.Label(sg_frame, text='VISA address:').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.sg_address_var = tk.StringVar(value='')
        ttk.Entry(sg_frame, textvariable=self.sg_address_var, width=50).grid(row=0, column=1, sticky='w', padx=6)

        # Rig section (host + port stacked)
        rig_frame = ttk.LabelFrame(cfg, text='Rig', style='RIG.TLabelframe')
        rig_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=6, pady=3)
        ttk.Label(rig_frame, text='HOST:').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.host_var = tk.StringVar(value='192.168.1.250')
        ttk.Entry(rig_frame, textvariable=self.host_var, width=24).grid(row=0, column=1, sticky='w', padx=6)

        ttk.Label(rig_frame, text='PORT:').grid(row=1, column=0, sticky='w', padx=6, pady=6)
        self.port_var = tk.StringVar(value='5001')
        ttk.Entry(rig_frame, textvariable=self.port_var, width=12).grid(row=1, column=1, sticky='w', padx=6)

        # Oscilloscope section
        osc_frame = ttk.LabelFrame(cfg, text='Oscilloscope', style='OSC.TLabelframe')
        osc_frame.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=6, pady=(3,6))
        ttk.Label(osc_frame, text='Sampling rate (Hz):').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.sampling_rate_var = tk.StringVar(value='1000000')
        ttk.Entry(osc_frame, textvariable=self.sampling_rate_var, width=20).grid(row=0, column=1, sticky='w', padx=6)

        # make config tab stretch
        cfg.columnconfigure(1, weight=1)

        # Test connections button + terminal for this tab
        test_frame = ttk.Frame(cfg)
        test_frame.grid(row=3, column=0, columnspan=2, sticky='ew', padx=6, pady=6)
        self.test_btn = ttk.Button(test_frame, text='Test Connections', command=self.test_connections)
        self.test_btn.grid(row=0, column=0, sticky='w')

        # macOS look toggle: prefer native on macOS, apply mac-like styling on other OSes
        self.macos_style_var = tk.BooleanVar(value=(sys.platform == 'darwin'))
        try:
            ttk.Checkbutton(test_frame, text='macOS look', variable=self.macos_style_var,
                            command=self._apply_macos_style).grid(row=0, column=5, sticky='e', padx=(12,0))
        except Exception:
            pass

    # (Theme install button removed per user request)

        # Theme chooser removed per user request (no chooser in UI)

        ttk.Label(test_frame, text='Retries:').grid(row=0, column=1, sticky='e', padx=(12,2))
        self.test_retries_var = tk.IntVar(value=2)
        ttk.Spinbox(test_frame, from_=1, to=10, textvariable=self.test_retries_var, width=4).grid(row=0, column=2, sticky='w')

        ttk.Label(test_frame, text='Timeout (s):').grid(row=0, column=3, sticky='e', padx=(12,2))
        self.test_timeout_var = tk.DoubleVar(value=2.0)
        ttk.Entry(test_frame, textvariable=self.test_timeout_var, width=6).grid(row=0, column=4, sticky='w')

        # progress indicator
        self.test_progress = ttk.Progressbar(test_frame, mode='indeterminate', length=120)
        self.test_progress.grid(row=0, column=5, sticky='e', padx=8)

        self.cfg_output = ScrolledText(cfg, height=8)
        self.cfg_output.grid(row=4, column=0, columnspan=2, sticky='nsew', padx=6, pady=(0,6))
        # configure simple color tags for pass/fail/info
        try:
            self.cfg_output.tag_config('ok', foreground='green')
            self.cfg_output.tag_config('fail', foreground='red')
            self.cfg_output.tag_config('info', foreground='#333333')
        except Exception:
            pass
        cfg.rowconfigure(4, weight=1)

        # -- B/C Mode tab: move existing GUI into bc_tab --
        parent = self.bc_tab

        # Pulse generator frame
        pg_frame = ttk.LabelFrame(parent, text="Pulse generator (signal) parameters")
        pg_frame.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        # (SG address is configured on Config tab; keep global var from there)
        self.shape_var = tk.StringVar(value="SIN")
        ttk.Label(pg_frame, text="Waveform shape:").grid(row=0, column=0, sticky="w")
        ttk.Entry(pg_frame, textvariable=self.shape_var).grid(row=0, column=1, sticky="w")

        self.freq_var = tk.StringVar(value="1000000")
        ttk.Label(pg_frame, text="Frequency (Hz):").grid(row=1, column=0, sticky="w")
        ttk.Entry(pg_frame, textvariable=self.freq_var).grid(row=1, column=1, sticky="w")

        self.amp_var = tk.StringVar(value="1")
        ttk.Label(pg_frame, text="Amplitude (V):").grid(row=2, column=0, sticky="w")
        ttk.Entry(pg_frame, textvariable=self.amp_var).grid(row=2, column=1, sticky="w")

        self.burst_cycles_var = tk.StringVar(value="60")
        ttk.Label(pg_frame, text="Burst cycles: ").grid(row=3, column=0, sticky="w")
        ttk.Entry(pg_frame, textvariable=self.burst_cycles_var).grid(row=3, column=1, sticky="w")

        self.num_bursts_var = tk.StringVar(value="1")
        ttk.Label(pg_frame, text="Number of bursts:").grid(row=4, column=0, sticky="w")
        ttk.Entry(pg_frame, textvariable=self.num_bursts_var).grid(row=4, column=1, sticky="w")

        # Scanning frame
        scan_frame = ttk.LabelFrame(parent, text="Scanning parameters")
        scan_frame.grid(row=1, column=0, padx=8, pady=8, sticky="nsew")

        # host/port now in Config tab; we still read the variables from there
        ttk.Label(scan_frame, text="(Host/Port configured in Config tab)").grid(row=0, column=0, columnspan=4, sticky="w")

        self.scan_axis_var = tk.StringVar(value="X")
        self.cross_axis_var = tk.StringVar(value="Z")
        ttk.Label(scan_frame, text="Axis 1:").grid(row=1, column=0, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.scan_axis_var, width=6).grid(row=1, column=1, sticky="w")
        ttk.Label(scan_frame, text="Axis 2:").grid(row=1, column=2, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.cross_axis_var, width=6).grid(row=1, column=3, sticky="w")

        self.scan_length_var = tk.StringVar(value="20")
        self.scan_points_var = tk.StringVar(value="2")
        ttk.Label(scan_frame, text="Scan length (mm):").grid(row=2, column=0, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.scan_length_var).grid(row=2, column=1, sticky="w")
        ttk.Label(scan_frame, text="Scan points:").grid(row=2, column=2, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.scan_points_var, width=6).grid(row=2, column=3, sticky="w")

        self.cross_length_var = tk.StringVar(value="20")
        self.cross_points_var = tk.StringVar(value="2")
        ttk.Label(scan_frame, text="Cross length (mm):").grid(row=3, column=0, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.cross_length_var).grid(row=3, column=1, sticky="w")
        ttk.Label(scan_frame, text="Cross points:").grid(row=3, column=2, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.cross_points_var, width=6).grid(row=3, column=3, sticky="w")

        self.start_col_var = tk.StringVar(value="2")
        ttk.Label(scan_frame, text="Start column index:").grid(row=4, column=0, sticky="w")
        ttk.Entry(scan_frame, textvariable=self.start_col_var, width=6).grid(row=4, column=1, sticky="w")

        # Controls
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=8)
        self.start_btn = ttk.Button(btn_frame, text="Start Scan", command=self.start_scan, style='Action.TButton')
        self.start_btn.grid(row=0, column=0, padx=4)

        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self.stop_scan, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=4)
        # keep dry_run variable for internal logic but remove the UI checkbox
        self.dry_run_var = BooleanVar(value=False)
        ttk.Button(btn_frame, text="Preview", command=self.preview_scan).grid(row=0, column=3, padx=6)
        ttk.Button(btn_frame, text="Load CSV", command=self.load_csv).grid(row=0, column=4, padx=6)

        # keep live_update variable for internal logic but remove the UI checkbox
        self.live_update_var = BooleanVar(value=False)

        ttk.Button(btn_frame, text="Save preview", command=self.save_preview_to_json).grid(row=0, column=6, padx=6)

        # Internal status box kept for logging but not shown (user requested removal)
        self.status_box = ScrolledText(parent, height=12)
        # Do not grid the status_box to remove the large top terminal from the UI

        # Create a content area: plotting (full width) and terminal (below)
        self.bc_content_frame = ttk.Frame(parent)
        # place B/C content at row 3 (moved up since top terminal removed)
        self.bc_content_frame.grid(row=3, column=0, columnspan=2, padx=8, pady=8, sticky='nsew')
        # Top: large plotting area spanning full width
        self.bc_plot_frame = ttk.LabelFrame(self.bc_content_frame, text="Scanned Image")
        # make plot large by default
        self.bc_plot_frame.grid(row=0, column=0, columnspan=2, sticky='nsew')
        self.bc_plot_placeholder = ttk.Label(self.bc_plot_frame, text="No waveform yet")
        self.bc_plot_placeholder.pack(expand=True, fill='both', padx=8, pady=8)
        # Bottom: terminal / status output for B/C
        self.bc_output = ScrolledText(self.bc_content_frame, height=8)
        self.bc_output.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(8,0))
        # Make waveform area dominant
        self.bc_content_frame.rowconfigure(0, weight=10, minsize=360)
        self.bc_content_frame.rowconfigure(1, weight=1, minsize=80)
        self._canvas = None

        # Logging
        repo_root = os.path.dirname(os.path.dirname(__file__))
        default_log = os.path.join(repo_root, 'data', 'gui_log.txt')
        self.log_file = tk.StringVar(value=default_log)

        log_frame = ttk.Frame(parent)
        # move log row up since bc_content_frame now uses row 3
        log_frame.grid(row=4, column=0, columnspan=2, sticky='ew', padx=8)
        ttk.Label(log_frame, text='Log file:').grid(row=0, column=0, sticky='w')
        ttk.Entry(log_frame, textvariable=self.log_file, width=60).grid(row=0, column=1, sticky='w')
        ttk.Button(log_frame, text='Browse', command=self.browse_log_file).grid(row=0, column=2, padx=6)

        # live update internal vars
        self._live_after_id = None
        self._live_last_mtime = None
        self._live_start_time = None

        # make layout stretchable
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        self.bc_tab.columnconfigure(0, weight=1)
        self.bc_tab.columnconfigure(1, weight=1)
        self.config_tab.columnconfigure(1, weight=1)
        # ensure the B/C content row expands when the tab is resized
        try:
            self.bc_tab.rowconfigure(3, weight=1)
        except Exception:
            pass

        # Ensure A/Transmit content area expands
        try:
            self.a_tab.columnconfigure(0, weight=1)
            self.a_tab.columnconfigure(1, weight=1)
            self.a_tab.rowconfigure(3, weight=1)
        except Exception:
            pass

        # Ensure Move Rig tab expands its output area
        try:
            self.move_tab.columnconfigure(0, weight=1)
            self.move_tab.columnconfigure(1, weight=1)
            self.move_tab.rowconfigure(5, weight=1)
        except Exception:
            pass

        # --- Aspect ratio handling: initialize aspect ratio and bind resize ---
        # Capture an initial aspect ratio so the window preserves it when
        # resized or maximized. Use a guard to avoid recursive geometry changes.
        self.root.update_idletasks()
        init_w = self.root.winfo_width() or 1000
        init_h = self.root.winfo_height() or 700
        try:
            self._aspect_ratio = float(init_w) / float(init_h)
        except Exception:
            self._aspect_ratio = 16 / 9
        self._resizing = False
        self.root.bind('<Configure>', self._on_resize)

        # -- A/Transmit Mode tab --
        a_parent = self.a_tab
        ttk.Label(a_parent, text='A/Transmit controls').grid(row=0, column=0, columnspan=2, sticky='w', padx=6, pady=6)
        # keep a_mode_var for compatibility (always A-mode here)
        self.a_mode_var = tk.StringVar(value='A-mode')

        # Mode radio buttons (A-mode vs Transmit)
        try:
            mode_frame = ttk.Frame(a_parent)
            mode_frame.grid(row=0, column=2, sticky='e', padx=6)
            ttk.Radiobutton(mode_frame, text='A-mode', variable=self.a_mode_var, value='A-mode').pack(side='left', padx=4)
            ttk.Radiobutton(mode_frame, text='Transmit', variable=self.a_mode_var, value='Transmit').pack(side='left', padx=4)
        except Exception:
            pass

        # Pulse generator frame (duplicate of B/C tab controls)
        pg_frame_a = ttk.LabelFrame(a_parent, text="Pulse generator (signal) parameters")
        pg_frame_a.grid(row=1, column=0, columnspan=2, padx=8, pady=4, sticky="nsew")

        # reuse variables from the B/C tab so settings stay in sync
        try:
            ttk.Label(pg_frame_a, text="Waveform shape:").grid(row=0, column=0, sticky="w")
            ttk.Entry(pg_frame_a, textvariable=self.shape_var).grid(row=0, column=1, sticky="w")

            ttk.Label(pg_frame_a, text="Frequency (Hz):").grid(row=1, column=0, sticky="w")
            ttk.Entry(pg_frame_a, textvariable=self.freq_var).grid(row=1, column=1, sticky="w")

            ttk.Label(pg_frame_a, text="Amplitude (V):").grid(row=2, column=0, sticky="w")
            ttk.Entry(pg_frame_a, textvariable=self.amp_var).grid(row=2, column=1, sticky="w")

            ttk.Label(pg_frame_a, text="Burst cycles: ").grid(row=3, column=0, sticky="w")
            ttk.Entry(pg_frame_a, textvariable=self.burst_cycles_var).grid(row=3, column=1, sticky="w")

            ttk.Label(pg_frame_a, text="Number of bursts:").grid(row=4, column=0, sticky="w")
            ttk.Entry(pg_frame_a, textvariable=self.num_bursts_var).grid(row=4, column=1, sticky="w")
        except Exception:
            # in case variables are not defined yet (shouldn't happen), ignore
            pass

    # Single Start Scan button: behavior depends on Mode
    # A/Transmit Start Scan button (styled)
        self.a_start_btn = ttk.Button(a_parent, text='Start Scan', command=self.start_a_transmit_scan, style='Action.TButton')
        self.a_start_btn.grid(row=2, column=0, columnspan=2, padx=6, pady=8)

        # Create content area for A/Transmit: full-width plotting and terminal below
        self.a_content_frame = ttk.Frame(a_parent)
        self.a_content_frame.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=6, pady=6)
        # A plot frame (full width)
        self.a_plot_frame = ttk.LabelFrame(self.a_content_frame, text='Waveform')
        self.a_plot_frame.grid(row=0, column=0, columnspan=2, sticky='nsew')
        self.a_plot_placeholder = ttk.Label(self.a_plot_frame, text='No waveform yet')
        self.a_plot_placeholder.pack(expand=True, fill='both', padx=8, pady=8)
        # A terminal output (below)
        self.a_output = ScrolledText(self.a_content_frame, height=8)
        self.a_output.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(8,0))
        self.a_content_frame.rowconfigure(0, weight=10, minsize=360)
        self.a_content_frame.rowconfigure(1, weight=1, minsize=80)
        a_parent.columnconfigure(1, weight=1)

        # -- Move Rig tab --
        m_parent = self.move_tab
        ttk.Label(m_parent, text='Manual rig movement (mm)').grid(row=0, column=0, columnspan=2, sticky='w', padx=6, pady=6)
        ttk.Label(m_parent, text='X (mm):').grid(row=1, column=0, sticky='w', padx=6)
        self.move_x_var = tk.StringVar(value='0')
        ttk.Entry(m_parent, textvariable=self.move_x_var, width=12).grid(row=1, column=1, sticky='w')
        ttk.Label(m_parent, text='Y (mm):').grid(row=2, column=0, sticky='w', padx=6)
        self.move_y_var = tk.StringVar(value='0')
        ttk.Entry(m_parent, textvariable=self.move_y_var, width=12).grid(row=2, column=1, sticky='w')
        ttk.Label(m_parent, text='Z (mm):').grid(row=3, column=0, sticky='w', padx=6)
        self.move_z_var = tk.StringVar(value='0')
        ttk.Entry(m_parent, textvariable=self.move_z_var, width=12).grid(row=3, column=1, sticky='w')

        self.move_btn = ttk.Button(m_parent, text='Move', command=self.move_rig_now, style='Action.TButton')
        self.move_btn.grid(row=4, column=0, columnspan=2, pady=8)

        self.move_output = ScrolledText(m_parent, height=8)
        self.move_output.grid(row=5, column=0, columnspan=2, sticky='nsew', padx=6, pady=6)
        m_parent.columnconfigure(1, weight=1)

        # About tab removed; About dialog is available via the About button

    def move_rig_now(self):
        """Collect X/Y/Z from the Move Rig tab and run the move in background."""
        try:
            x = float(self.move_x_var.get())
            y = float(self.move_y_var.get())
            z = float(self.move_z_var.get())
        except Exception as e:
            messagebox.showerror('Invalid input', f'Enter numeric X/Y/Z: {e}')
            return

        self.move_output.insert(tk.END, f"Moving to X={x} mm Y={y} mm Z={z} mm\n")
        self.move_output.see(tk.END)

        t = threading.Thread(target=self._move_worker, args=(x, y, z), daemon=True)
        t.start()

    def _move_worker(self, x_mm, y_mm, z_mm):
        """Background worker that performs socket commands to move the rig.

        Uses `rig_function.move_to_position` which accepts pulse units, so we
        convert mm -> pulse with a conservative conversion factor.
        """
        try:
            import socket
            import rig_function
        except Exception as e:
            self.move_output.insert(tk.END, f"Failed to import rig utilities: {e}\n")
            return

        # Simple mm -> pulse conversion; prefer move.mm_to_pulse if available
        try:
            import importlib.util
            move_path = os.path.join(os.path.dirname(__file__), 'move.py')
            spec = importlib.util.spec_from_file_location('move', move_path)
            move_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(move_mod)
            conv = getattr(move_mod, 'mm_to_pulse', lambda m: int(m * 700))
        except Exception:
            conv = lambda m: int(m * 700)

        x_p = conv(x_mm) if x_mm is not None else None
        y_p = conv(y_mm) if y_mm is not None else None
        z_p = conv(z_mm) if z_mm is not None else None

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host_var.get().strip(), int(self.port_var.get())))
                self.move_output.insert(tk.END, f"Connected to rig at {self.host_var.get()}:{self.port_var.get()}\n")
                self.move_output.see(tk.END)

                rig_function.move_to_position(sock, x=x_p, y=y_p, z=z_p)
                self.move_output.insert(tk.END, "Move complete.\n")
                self.move_output.see(tk.END)
        except Exception as e:
            self.move_output.insert(tk.END, f"Move failed: {e}\n")
            self.move_output.see(tk.END)
        finally:
            try:
                self._set_button_active(self.move_btn, False)
            except Exception:
                pass

    def _on_resize(self, event):
        """Maintain the initial aspect ratio when the window is resized.

        This handler attempts to adjust the window geometry so the aspect
        ratio remains approximately the same. A guard (_resizing) prevents
        recursion where geometry changes cause new Configure events.
        """
        try:
            if getattr(self, '_resizing', False):
                return
            self._resizing = True

            # current size
            cur_w = self.root.winfo_width()
            cur_h = self.root.winfo_height()
            if cur_h == 0 or cur_w == 0:
                return

            target_h = int(cur_w / self._aspect_ratio)
            target_w = int(cur_h * self._aspect_ratio)

            # choose the smaller adjustment to avoid overshooting
            if abs(target_h - cur_h) < abs(target_w - cur_w):
                # adjust height
                geom = f"{cur_w}x{target_h}"
            else:
                # adjust width
                geom = f"{target_w}x{cur_h}"

            # apply geometry change; wrap in try to ignore platform issues
            try:
                self.root.geometry(geom)
            except Exception:
                pass

        finally:
            # short delay before allowing another resize handling
            self.root.after(50, lambda: setattr(self, '_resizing', False))

    def log(self, *parts):
        text = " ".join(str(p) for p in parts)
        self.status_box.insert(tk.END, text + "\n")
        self.status_box.see(tk.END)

    def validate_and_collect(self):
        try:
            pg = {
                "sg_address": self.sg_address_var.get().strip() or None,
                "shape": self.shape_var.get().strip(),
                "frequency": float(self.freq_var.get()),
                "amplitude": float(self.amp_var.get()),
                "burst_ncycles": float(self.burst_cycles_var.get()),
                "number_of_burst": int(self.num_bursts_var.get()),
            }

            scan = {
                "host": self.host_var.get().strip(),
                "port": int(self.port_var.get()),
                "scan_axis": self.scan_axis_var.get().strip() or "X",
                "cross_axis": self.cross_axis_var.get().strip() or "Z",
                "scan_length": float(self.scan_length_var.get()),
                "scan_points": int(self.scan_points_var.get()),
                "cross_length": float(self.cross_length_var.get()),
                "cross_points": int(self.cross_points_var.get()),
                "start_col": int(self.start_col_var.get()),
            }
        except Exception as e:
            messagebox.showerror("Invalid input", f"Please check input values: {e}")
            return None, None

        # Compute steps using utility
        scan_step = compute_step(scan["scan_length"], scan["scan_points"]) 
        cross_step = compute_step(scan["cross_length"], scan["cross_points"]) 
        scan["scan_step"] = scan_step
        scan["cross_step"] = cross_step

        return pg, scan

    def preview_scan(self):
        vals = self.validate_and_collect()
        if vals[0] is None:
            return
        pg, scan = vals
        scan_steps = int(scan["scan_length"] / scan["scan_step"]) if scan["scan_step"]>0 else 1
        cross_steps = int(scan["cross_length"] / scan["cross_step"]) + 1 if scan["cross_step"]>0 else 1
        msg = (
            f"Preview:\nScan axis: {scan['scan_axis']} ({scan_steps} steps, step={scan['scan_step']})\n"
            f"Cross axis: {scan['cross_axis']} ({cross_steps} rows, step={scan['cross_step']})\n"
            f"Pulse: shape={pg['shape']}, freq={pg['frequency']} Hz, amp={pg['amplitude']} V, cycles={pg['burst_ncycles']}\n"
            f"Dry-run: {'yes' if self.dry_run_var.get() else 'no'}"
        )
        messagebox.showinfo("Scan preview", msg)

    def _show_plot_from_csv(self, csv_path):
        """Read a CSV file and display an interactive Plotly waveform.

        Uses pandas to read the CSV and plotly to build an interactive plot.
        If `tkinterweb.HtmlFrame` is available it will embed the HTML in the GUI;
        otherwise the generated HTML file is opened in the default browser.
        """
        try:
            import importlib
            pd = importlib.import_module('pandas')
            px = importlib.import_module('plotly.express')
            pio = importlib.import_module('plotly.io')
            import tempfile
            import webbrowser
        except Exception as e:
            # If optional plotting libraries are missing, log and return.
            self.log("Plotly/pandas dependencies missing:", e)
            return

        if not os.path.exists(csv_path):
            self.log(f"CSV not found for plotting: {csv_path}")
            return

        try:
            df = pd.read_csv(csv_path)

            # choose x and y
            if 'Time (s)' in df.columns and 'Amplitude (V)' in df.columns:
                xcol = 'Time (s)'
                ycol = 'Amplitude (V)'
            else:
                xcol = df.columns[0]
                ycol = df.columns[1]

            fig = px.line(df, x=xcol, y=ycol, title=os.path.basename(csv_path))

            # produce standalone HTML fragment
            html = pio.to_html(fig, full_html=False, include_plotlyjs='cdn')

            # Choose target plot frame based on active tab
            try:
                cur = self.notebook.select()
                tab_text = self.notebook.tab(cur, 'text')
            except Exception:
                tab_text = ''

            if 'A/Transmit' in tab_text:
                plot_target = getattr(self, 'a_plot_frame', None)
            else:
                plot_target = getattr(self, 'bc_plot_frame', None)

            if plot_target is None:
                plot_target = getattr(self, 'bc_plot_frame', None)

            # Clear previous content
            for w in plot_target.winfo_children():
                w.destroy()

            # Try to embed HTML in Tk using tkinterweb if available
            try:
                # Try to import tkinterweb dynamically to avoid static analyzer errors
                import importlib
                tw = importlib.import_module('tkinterweb')
                HtmlFrame = getattr(tw, 'HtmlFrame', None)
                if HtmlFrame is not None:
                    htmlf = HtmlFrame(plot_target, horizontal_scrollbar='auto')
                    htmlf.pack(expand=True, fill='both')
                    htmlf.load_html(html)
                    self._canvas = htmlf
                    return
            except Exception:
                # embedding failed or tkinterweb not installed -> fallback to browser
                pass

            # Fallback: write a temporary HTML file and open in browser
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.html')
            with open(tmp.name, 'w', encoding='utf-8') as f:
                f.write(f"<html><head><meta charset='utf-8'></head><body>{html}</body></html>")
            webbrowser.open('file://' + tmp.name)
            self.log('Opened interactive plot in default browser (no tkinterweb).')

        except Exception as e:
            self.log('Failed to plot CSV:', e)

    # --- Logging helpers ---
    def browse_log_file(self):
        fn = filedialog.asksaveasfilename(title='Select log file', defaultextension='.txt', filetypes=[('Text', '*.txt'), ('All', '*.*')])
        if fn:
            self.log_file.set(fn)

    def _write_log(self, text):
        # append to status box and file
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{timestamp}] {text}\n"
        try:
            with open(self.log_file.get(), 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception:
            pass
        self.log(text)

    # --- Preview save ---
    def save_preview_to_json(self):
        vals = self.validate_and_collect()
        if vals[0] is None:
            return
        pg, scan = vals
        # merge a compact dict
        data = {'pulse': pg, 'scan': scan, 'dry_run': bool(self.dry_run_var.get()), 'live_update': bool(self.live_update_var.get())}
        fn = filedialog.asksaveasfilename(title='Save preview config', defaultextension='.json', filetypes=[('JSON', '*.json')])
        if fn:
            try:
                with open(fn, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                self._write_log(f"Saved preview to {fn}")
            except Exception as e:
                messagebox.showerror('Save error', f'Failed to save preview: {e}')

    # --- Live update helpers ---
    def _start_live_watch(self, csv_path, interval=500):
        # initialize
        self._live_last_mtime = None
        self._live_start_time = time.time()

        def _watch():
            try:
                if not os.path.exists(csv_path):
                    # reschedule
                    self._live_after_id = self.root.after(interval, _watch)
                    return

                mtime = os.path.getmtime(csv_path)
                if self._live_last_mtime is None or mtime > self._live_last_mtime:
                    self._live_last_mtime = mtime
                    # update plot
                    self._show_plot_from_csv(csv_path)

                # continue polling
                self._live_after_id = self.root.after(interval, _watch)
            except Exception as e:
                self.log('Live watch error:', e)

        _watch()

    def _stop_live_watch(self):
        if self._live_after_id:
            try:
                self.root.after_cancel(self._live_after_id)
            except Exception:
                pass
            self._live_after_id = None


    def load_csv(self):
        fn = filedialog.askopenfilename(
            title='Select waveform CSV',
            filetypes=[('CSV files', '*.csv'), ('All files', '*.*')]
        )
        if fn:
            self._show_plot_from_csv(fn)

    def _stream_subprocess(self, cmd, display_widget, env=None):
        import subprocess
        # Accept optional on_exit callback passed via env['_ON_EXIT_CB'] (callable)
        on_exit = None
        if env and isinstance(env, dict):
            on_exit = env.pop('_ON_EXIT_CB', None)

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        except Exception as e:
            display_widget.insert(tk.END, f"Failed to start: {e}\n")
            return

        def _reader():
            for line in proc.stdout:
                try:
                    display_widget.insert(tk.END, line)
                    display_widget.see(tk.END)
                except Exception:
                    pass
            proc.wait()
            display_widget.insert(tk.END, f"Process exited with {proc.returncode}\n")
            # call on_exit if provided
            try:
                if callable(on_exit):
                    on_exit(proc.returncode)
            except Exception:
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

    def _run_callable_in_thread(self, target_callable, args=(), display_widget=None):
        """Run a callable in a background thread while capturing stdout/stderr
        and streaming printed output into the provided display_widget (a
        ScrolledText)."""
        import sys
        import io

        def _worker():
            # capture stdout/stderr
            old_out, old_err = sys.stdout, sys.stderr
            buf = io.StringIO()
            sys.stdout = buf
            sys.stderr = buf
            try:
                try:
                    target_callable(*args)
                except Exception as e:
                    print(f"Error in callable: {e}")
            finally:
                # flush buffer to widget
                try:
                    contents = buf.getvalue()
                    if display_widget is not None:
                        display_widget.insert(tk.END, contents)
                        display_widget.see(tk.END)
                except Exception:
                    pass
                finally:
                    sys.stdout = old_out
                    sys.stderr = old_err

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _set_button_active(self, button, active=True):
        """Toggle an action button's visual state."""
        try:
            if active:
                button.config(style='ActionActive.TButton')
            else:
                button.config(style='Action.TButton')
        except Exception:
            pass

    def test_connections(self):
        """Start a background check of configured devices and report to cfg_output."""
        # clear previous output
        try:
            self.cfg_output.delete('1.0', tk.END)
        except Exception:
            pass

        # disable button and start progress
        try:
            self.test_btn.config(state='disabled')
            self.test_progress.start(10)
        except Exception:
            pass

        t = threading.Thread(target=self._test_connections_worker, daemon=True)
        t.start()

    def _append_cfg_output(self, text):
        try:
            self.cfg_output.insert(tk.END, text + "\n")
            self.cfg_output.see(tk.END)
        except Exception:
            pass

    def _test_connections_worker(self):
        """Worker that attempts lightweight checks for SG, Oscilloscope utilities, and Rig socket."""
        # Signal generator
        sg_addr = self.sg_address_var.get().strip()
        retries = max(1, int(self.test_retries_var.get())) if hasattr(self, 'test_retries_var') else 1
        timeout = float(self.test_timeout_var.get()) if hasattr(self, 'test_timeout_var') else 2.0

        if not sg_addr:
            self.root.after(0, lambda: self._append_cfg_output('Signal generator: No address configured'))
        else:
            try:
                import importlib
                pm = importlib.import_module('pymeasure.instruments.agilent')
                Agilent33500 = getattr(pm, 'Agilent33500', None)
                if Agilent33500 is None:
                    raise ImportError('Agilent33500 not found in pymeasure')
            except Exception:
                self.root.after(0, lambda: self._append_cfg_output('Signal generator: pymeasure driver not available'))
                sg_driver = None
            else:
                sg_driver = Agilent33500

            if sg_driver is None:
                pass
            else:
                ok = False
                last_err = None
                for attempt in range(retries):
                    try:
                        sg = sg_driver(sg_addr)
                        # try to query id if available
                        idn = None
                        try:
                            idn = getattr(sg, 'id', None) or (sg.ask('*IDN?') if hasattr(sg, 'ask') else None)
                        except Exception:
                            idn = None
                        if idn:
                            self.root.after(0, lambda i=idn: self._append_cfg_output(f'Signal generator: {i}'))
                        else:
                            self.root.after(0, lambda: self._append_cfg_output(f'Signal generator: Connected at {sg_addr}'))
                        try:
                            sg.shutdown()
                        except Exception:
                            pass
                        ok = True
                        break
                    except Exception as e:
                        last_err = e
                        time.sleep(0.2)
                if not ok:
                    self.root.after(0, lambda e=last_err: self._append_cfg_output(f'Signal generator: Failed to open {sg_addr}: {e}'))

        # Oscilloscope helper
        try:
            import Oscilloscope as oscmod
            self.root.after(0, lambda: self._append_cfg_output('Oscilloscope utilities: available'))
        except Exception as e:
            self.root.after(0, lambda e=e: self._append_cfg_output(f'Oscilloscope utilities: not available ({e})'))

        # Install ARC theme functionality removed per user request

    def _apply_macos_style(self):
        """Apply a macOS-like look-and-feel.

        On real macOS this tries to use the native Aqua theme. On other
        platforms we fall back to a macOS-inspired palette and tighter
        paddings/fonts so the UI feels closer to macOS.
        """
        try:
            import sys
            s = self.style
            # On macOS prefer the native theme
            if sys.platform == 'darwin':
                try:
                    s.theme_use('aqua')
                    self.cfg_output.insert(tk.END, "Applied native macOS (Aqua) theme.\n")
                except Exception:
                    self.cfg_output.insert(tk.END, "Native Aqua theme not available; leaving current theme.\n")
                return

            # macOS-like palette for other OSes
            bg = '#f6f6f7'
            panel = '#ffffff'
            accent = '#0437F2'
            text = '#1f2937'

            try:
                s.theme_use('clam')
            except Exception:
                pass

            try:
                s.configure('TFrame', background=bg)
                s.configure('TLabelFrame', background=panel, padding=10)
                s.configure('TLabel', background=panel, foreground=text, font=('Helvetica', 12))
                s.configure('TButton', background=panel, relief='flat', padding=6, font=('Helvetica', 11))
                s.configure('Accent.TButton', foreground='white', background=accent,
                            font=('Helvetica', 12, 'bold'), padding=(10, 6), relief='flat')
                try:
                    s.map('Accent.TButton', background=[('active', '#0329e6'), ('pressed', '#021bd1')])
                except Exception:
                    pass
                try:
                    self.root.configure(bg=bg)
                except Exception:
                    pass
                self.cfg_output.insert(tk.END, "Applied macOS-like style (non-macOS).\n")
            except Exception as e:
                try:
                    self.cfg_output.insert(tk.END, f"Failed to apply macOS style: {e}\n")
                except Exception:
                    pass
        except Exception:
            pass

        # Theme selection functions removed; theme chooser was removed per user request

    def run_move_script(self):
        # Try in-process call first (safe because move.py is import-safe now)
        try:
            import importlib
            move_mod = importlib.import_module('Code.move') if False else None
        except Exception:
            move_mod = None

        # attempt to import directly from file path if regular import fails
        if move_mod is None:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location('move', os.path.join(os.path.dirname(__file__), 'move.py'))
                move_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(move_mod)
            except Exception as e:
                move_mod = None

        if move_mod and hasattr(move_mod, 'main'):
            self.a_output.insert(tk.END, 'Running move.main() in-process...\n')
            self._run_callable_in_thread(move_mod.main, display_widget=self.a_output)
            return

    # Fallback: run as subprocess using the current Python interpreter.
        script = os.path.join(os.path.dirname(__file__), 'move.py')
        if not os.path.exists(script):
            self.a_output.insert(tk.END, 'move.py not found\n')
            return
        self.a_output.insert(tk.END, f'Running move.py as subprocess...\n')
        import sys
        cmd = [sys.executable, script]
        self._stream_subprocess(cmd, self.a_output)

    def run_a_scan_script(self):
        # Try in-process call to A scan module (file name has a space)
        try:
            import importlib.util
            script_path = os.path.join(os.path.dirname(__file__), 'A scan.py')
            spec = importlib.util.spec_from_file_location('a_scan', script_path)
            a_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(a_mod)
        except Exception as e:
            a_mod = None

        if a_mod and hasattr(a_mod, 'main'):
            self.a_output.insert(tk.END, 'Running A scan.main() in-process...\n')
            # set temporary A_SCAN_PARAMS: only provide the selected mode
            try:
                from Setup import A_SCAN_PARAMS
                A_SCAN_PARAMS['mode'] = self.a_mode_var.get()
            except Exception:
                pass
            self._run_callable_in_thread(a_mod.main, display_widget=self.a_output)
            return

    # Fallback: run as subprocess using the current Python interpreter.
        script = os.path.join(os.path.dirname(__file__), 'A scan.py')
        if not os.path.exists(script):
            self.a_output.insert(tk.END, 'A scan.py not found\n')
            return
        self.a_output.insert(tk.END, f'Running A scan.py as subprocess...\n')
        import sys, os
        cmd = [sys.executable, script]
        # propagate selected mode to subprocess via environment variable
        env = os.environ.copy()
        env['A_SCAN_MODE'] = self.a_mode_var.get()
        self._stream_subprocess(cmd, self.a_output, env=env)

    def start_a_transmit_scan(self):
        """Dispatcher called by the A/Transmit Start Scan button.

        If Mode == 'A-mode' -> run the A scan procedure.
        If Mode == 'Transmit' -> run the move/transmit procedure.
        """
        self.a_output.insert(tk.END, "Start requested for A-mode\n")
        # Only A-mode is supported in this tab; run A scan flow
        self.run_a_scan_script()


    def start_scan(self):
        pg, scan = self.validate_and_collect()
        if pg is None:
            return

        # disable start button and enable stop
        self.start_btn.config(state="disabled")
        try:
            self._set_button_active(self.start_btn, True)
        except Exception:
            pass
        self.stop_btn.config(state="normal")
        self.stop_event.clear()

        t = threading.Thread(target=self._scan_worker, args=(pg, scan), daemon=True)
        t.start()

    def stop_scan(self):
        self.log("Stop requested — will stop after current operation finishes.")
        self.stop_event.set()
        try:
            self._set_button_active(self.start_btn, False)
        except Exception:
            pass

    def on_close(self):
        if self.start_btn.instate(["disabled"]):
            if not messagebox.askyesno("Quit", "A scan is running. Quit anyway?"):
                return
        self.stop_event.set()
        self.root.destroy()

    def show_about(self):
        """Open a centered About dialog showing logo, developer info, release and license."""
        # Build a robust layout: top row contains logo (left) and info (right).
        try:
            # Compute sizes relative to the main window so the About dialog
            # is never larger than half of the GUI window.
            try:
                self.root.update_idletasks()
                root_w = max(200, self.root.winfo_width())
                root_h = max(200, self.root.winfo_height())
            except Exception:
                root_w, root_h = 800, 600

            # Determine a provisional maximum logo size relative to the main window
            max_logo_w = min(600, int(root_w * 0.6))
            max_logo_h = min(800, int(root_h * 0.6))

            about_win = tk.Toplevel(self.root)
            about_win.title('About A/B/C-Scanner')
            about_win.transient(self.root)
            # allow the about dialog to be resizable; contents will reflow
            about_win.resizable(True, True)

            content = ttk.Frame(about_win, padding=12)
            content.grid(row=0, column=0, sticky='nsew')
            try:
                # allow the about_win and content to grow
                about_win.columnconfigure(0, weight=1)
                about_win.rowconfigure(0, weight=1)
            except Exception:
                pass
            # allow content to expand and the license box to stretch
            try:
                content.columnconfigure(0, weight=1)
                content.rowconfigure(1, weight=1)
            except Exception:
                pass

            top = ttk.Frame(content)
            top.grid(row=0, column=0, sticky='nsew')
            try:
                top.columnconfigure(1, weight=1)
            except Exception:
                pass

            # Left: logo
            logo_path = os.path.join(os.path.dirname(__file__), 'UltrasonicsGroupLogo_original.png')
            img_label = None
            try:
                import importlib
                pil = importlib.import_module('PIL')
                Image = getattr(pil, 'Image', None)
                ImageTk = getattr(importlib.import_module('PIL.ImageTk'), 'ImageTk', None)
                if Image is not None and ImageTk is not None and os.path.exists(logo_path):
                    img = Image.open(logo_path)
                    # Scale image to fit within the provisional max logo size
                    img.thumbnail((max_logo_w, max_logo_h))
                    self._about_logo = ImageTk.PhotoImage(img)
                    img_label = ttk.Label(top, image=self._about_logo)
                    img_label.grid(row=0, column=0, rowspan=3, sticky='nw', padx=(0,12))
                else:
                    raise ImportError('Pillow not available or logo missing')
            except Exception:
                try:
                    if os.path.exists(logo_path):
                        # Use tk.PhotoImage but subsample if very large to avoid huge dialog
                        try:
                            tmp = tk.PhotoImage(file=logo_path)
                            w = tmp.width()
                            h = tmp.height()
                            # subsample so image fits within the provisional max logo size
                            fx = max(1, int(w / max_logo_w)) if w > max_logo_w else 1
                            fy = max(1, int(h / max_logo_h)) if h > max_logo_h else 1
                            f = max(fx, fy)
                            if f > 1:
                                self._about_logo = tmp.subsample(f, f)
                            else:
                                self._about_logo = tmp
                        except Exception:
                            self._about_logo = tk.PhotoImage(file=logo_path)
                        img_label = ttk.Label(top, image=self._about_logo)
                        img_label.grid(row=0, column=0, rowspan=3, sticky='nw', padx=(0,12))
                except Exception:
                    img_label = ttk.Label(top, text='[Logo not available]')
                    img_label.grid(row=0, column=0, rowspan=3, sticky='nw', padx=(0,12))

            # Right: textual info
            info_frame = ttk.Frame(top)
            info_frame.grid(row=0, column=1, sticky='nsew')
            try:
                info_frame.columnconfigure(0, weight=1)
            except Exception:
                pass
            # After image is created, compute displayed image size and set dialog size
            try:
                # get the displayed image size from the PhotoImage (works for PIL ImageTk too)
                img_w = getattr(self._about_logo, 'width', None)
                img_h = getattr(self._about_logo, 'height', None)
                if callable(img_w):
                    img_w = self._about_logo.width()
                    img_h = self._about_logo.height()
                else:
                    # fallback: use PIL image size if available
                    try:
                        img_w, img_h = img.size
                    except Exception:
                        img_w, img_h = max_logo_w, max_logo_h
            except Exception:
                img_w, img_h = max_logo_w, max_logo_h

            # dialog dimensions: height == image height, width == 2.5 * image width
            about_w = max(360, int(img_w * 2.5))
            about_h = max(120, int(img_h))

            try:
                info_wrap = max(120, int(about_w * 0.45))
            except Exception:
                info_wrap = 320
            lbl_title = ttk.Label(info_frame, text='A/B/C-Scanner', font=('Arial', 13, 'bold'), wraplength=info_wrap)
            lbl_title.grid(row=0, column=0, sticky='w')
            lbl_version = ttk.Label(info_frame, text='Version: v0.1 (2025-10-15)', font=('Arial', 11), wraplength=info_wrap)
            lbl_version.grid(row=1, column=0, sticky='w', pady=(4,6))
            lbl_developed = ttk.Label(info_frame, text='Developed by: Dr Reza Haqshenas, Anqi Yang in Ultrasonics Group', font=('Arial', 10), wraplength=info_wrap)
            lbl_developed.grid(row=2, column=0, sticky='w')
            # remove contact email as requested
            lbl_open_source = ttk.Label(info_frame, text='This code is available as open-source under the MIT License.', foreground='#444444', font=('Arial', 10), wraplength=info_wrap)
            lbl_open_source.grid(row=3, column=0, sticky='w', pady=(6,0))

            # License / longer text below
            # Note: the detailed license text box was removed per user request.
            # The short open-source statement is shown above.
            try:
                content.rowconfigure(1, weight=0)
            except Exception:
                pass

            # Make sure the dialog geometry is sensible and center it (use about_w/about_h)
            about_win.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - about_w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - about_h) // 2
            about_win.geometry(f"{about_w}x{about_h}+{x}+{y}")
            # update wraplengths on resize so text reflows
            # Debounced resize handler: schedule a single update after rapid
            # Configure events to avoid recursive geometry changes and flicker.
            def _apply_wrap():
                try:
                    w = about_win.winfo_width()
                    wrap = max(120, int(w * 0.45))
                    if getattr(about_win, '_last_wrap', None) != wrap:
                        lbl_title.config(wraplength=wrap)
                        lbl_version.config(wraplength=wrap)
                        lbl_developed.config(wraplength=wrap)
                        lbl_open_source.config(wraplength=wrap)
                        about_win._last_wrap = wrap
                except Exception:
                    pass

            def _schedule_wrap_update(event=None):
                try:
                    # cancel previous scheduled update
                    aid = getattr(about_win, '_wrap_after_id', None)
                    if aid:
                        about_win.after_cancel(aid)
                except Exception:
                    pass
                try:
                    about_win._wrap_after_id = about_win.after(120, _apply_wrap)
                except Exception:
                    pass

            about_win.bind('<Configure>', _schedule_wrap_update)
            about_win.grab_set()
        except Exception as e:
            messagebox.showinfo('About', f'A/B/C-Scanner\n\n{e}')

    def _scan_worker(self, pg, scan):
        # Run the scanning procedure using existing modules. Hardware imports
        # and connections happen here so importing this module remains side-
        # effect free.
        # Import hardware modules conditionally. If dry run is enabled, we
        # skip instrument imports and any hardware access.
        oscmod = None
        rig_function = None
        socket = None
        Burst_generate = None
        Agilent33500 = None

        if not self.dry_run_var.get():
            try:
                import importlib
                pm = importlib.import_module('pymeasure.instruments.agilent')
                Agilent33500 = getattr(pm, 'Agilent33500', None)
                try:
                    from Signal_function import Burst_generate
                except Exception:
                    Burst_generate = None
                # Oscilloscope utilities and rig functions
                import Oscilloscope as oscmod
                import rig_function
                import socket
                if Agilent33500 is None:
                    raise ImportError('Agilent33500 not available')
            except Exception as e:
                self.log("Failed to import hardware modules:", e)
                messagebox.showerror("Import error", f"Failed to import hardware modules: {e}")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                return

        # Connect to signal generator
        # Connect to signal generator (or stub in dry-run)
        sg = None
        if not self.dry_run_var.get():
            try:
                sg_address = pg.get("sg_address")
                if sg_address:
                    sg = Agilent33500(sg_address)
                    self.log("Connected to SG:", getattr(sg, 'id', sg_address))
                else:
                    sg = Agilent33500()  # default constructor if supported
                    self.log("Connected to SG (default constructor)")
            except Exception as e:
                self.log("Failed to connect to signal generator:", e)
                messagebox.showerror("SG error", f"Failed to connect to signal generator: {e}")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                return

        # Create scan folder
        # Create scan folder (oscmod may not be imported in dry-run)
        scan_folder = None
        try:
            if oscmod:
                scan_folder = oscmod.create_scan_folder()
                self.log("Scan folder:", scan_folder)
            else:
                self.log("Dry-run mode: scan folder not created")
        except Exception as e:
            self.log("Failed to create scan folder:", e)
            scan_folder = None

        # Establish socket to rig
        sock = None
        if not self.dry_run_var.get():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((scan["host"], scan["port"]))
                self.log("Connected to rig at", scan["host"], scan["port"])
            except Exception as e:
                self.log("Failed to connect to rig:", e)
                messagebox.showerror("Rig error", f"Failed to connect to rig: {e}")
                try:
                    if sg:
                        sg.shutdown()
                except:
                    pass
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                return

        try:
            # Setup incremental mode and enable axes
            if rig_function:
                rig_function.send_command(sock, "INC")
                rig_function.enable_axis(sock, scan["scan_axis"])
                rig_function.enable_axis(sock, scan["cross_axis"])

            scan_steps = int(scan["scan_length"] / scan["scan_step"]) if scan["scan_step"]>0 else 1
            cross_steps = int(scan["cross_length"] / scan["cross_step"]) + 1 if scan["cross_step"]>0 else 1

            self.log("Starting B Scan: {}-axis {} steps, step {}, {}-axis {} rows, step {}".format(
                scan["scan_axis"], scan_steps, scan["scan_step"], scan["cross_axis"], cross_steps, scan["cross_step"]
            ))

            cross = 0
            start_col = scan.get("start_col", 2)

            for cross in range(cross_steps):
                if self.stop_event.is_set():
                    self.log("Scan stopped by user.")
                    break

                self.log(f"Scanning row {cross+1}/{cross_steps}")
                # zig-zag direction
                scan_direction = -scan["scan_step"] if (cross + 1) % 2 == 0 else scan["scan_step"]

                for scan_idx in range(scan_steps):
                    if self.stop_event.is_set():
                        break

                    if cross == 0:
                        s = scan_idx + start_col
                    else:
                        s = scan_steps - scan_idx if (cross + 1) % 2 == 0 else scan_idx + start_col

                    # Move scan axis
                    rig_function.send_command(sock, f"{scan['scan_axis']}{scan_direction}")
                    rig_function.wait_until_stopped(sock, scan['scan_axis'])

                    # Trigger burst according to PG params (or simulate in dry-run)
                    if Burst_generate and sg:
                        Burst_generate(
                            sg,
                            shape=pg['shape'],
                            frequency=pg['frequency'],
                            amplitude=pg['amplitude'],
                            burst_ncycles=pg['burst_ncycles'],
                            number_of_burst=pg['number_of_burst'],
                        )
                        self.log(f"Triggered burst — row {cross+1}, col {s}")
                    else:
                        self.log(f"[Dry-run] Would trigger burst — row {cross+1}, col {s}")

                    # Read & save waveform using oscilloscope helper (or simulate)
                    if oscmod:
                        try:
                            oscmod.send_burst(sg, cross, s, scan_folder)
                            # After saving, display the CSV if available
                            if scan_folder:
                                csv_path = os.path.join(scan_folder, f"row_{cross+1}_col_{s}.csv")
                                # schedule plotting on the main thread
                                try:
                                    self.root.after(100, lambda p=csv_path: self._show_plot_from_csv(p))
                                except Exception:
                                    # fallback: call directly
                                    self._show_plot_from_csv(csv_path)

                                # If live update enabled, start watcher for this CSV
                                if self.live_update_var.get():
                                    # stop any previous live watcher
                                    self._stop_live_watch()
                                    self._start_live_watch(csv_path, interval=500)

                        except Exception as e:
                            self.log("Oscilloscope save error:", e)
                    else:
                        self.log(f"[Dry-run] Would read oscilloscope and save row {cross+1} col {s}")

                # Move to next row
                if cross < cross_steps - 1 and not self.stop_event.is_set():
                    if rig_function and sock:
                        rig_function.send_command(sock, f"{scan['cross_axis']}{scan['cross_step']}")
                        rig_function.wait_until_stopped(sock, scan['cross_axis'])
                    else:
                        self.log(f"[Dry-run] Would move {scan['cross_axis']} by {scan['cross_step']}")

                # If live update enabled, stop live watch after finishing scans for this row
                if self.live_update_var.get():
                    self._stop_live_watch()

            self.log("B Scan finished")

        except Exception as e:
            self.log("Error during scan:", e)
        finally:
            try:
                sock.close()
            except:
                pass
            try:
                sg.shutdown()
            except:
                pass
            try:
                self._set_button_active(self.start_btn, False)
            except Exception:
                pass
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")


def main():
    root = tk.Tk()
    app = ScanGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
