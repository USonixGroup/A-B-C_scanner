"""PySide6 implementation of the A/B scanner GUI.

This module replaces the old Tkinter surface with a Qt-based desktop UI while
reusing the existing scan, motion, and acquisition backends from this project.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from scan_utils import compute_step

MM_TO_PULSE = 700
MODE_LEFT_PANEL_WIDTH = 430
B_MODE_LEFT_PANEL_WIDTH = 540
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR.parent / "data" / "gui_settings.json"


class PlotCanvas(FigureCanvasQTAgg):
    def __init__(self, title: str) -> None:
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self._title = title
        self.setMinimumHeight(260)
        self.draw_placeholder(title)

    def _style_axes(self) -> None:
        self.figure.patch.set_facecolor("#ffffff")
        self.axes.set_facecolor("#ffffff")
        self.axes.tick_params(colors="#415368")
        for spine in self.axes.spines.values():
            spine.set_color("#d6dfeb")
        self.axes.grid(True, color="#edf2f8", linewidth=0.8)

    def draw_placeholder(self, text: str) -> None:
        self.figure.clear()
        self.axes = self.figure.add_subplot(111)
        self._style_axes()
        self.axes.text(
            0.5,
            0.5,
            text,
            ha="center",
            va="center",
            fontsize=12,
            color="#73859b",
            transform=self.axes.transAxes,
        )
        self.axes.set_xticks([])
        self.axes.set_yticks([])
        self.draw_idle()

    def plot_waveform(self, csv_path: str) -> None:
        try:
            import pandas as pd

            df = pd.read_csv(csv_path)
            if "Time (s)" in df.columns and "Amplitude (V)" in df.columns:
                xcol, ycol = "Time (s)", "Amplitude (V)"
            else:
                xcol, ycol = df.columns[:2]
            self.figure.clear()
            self.axes = self.figure.add_subplot(111)
            self._style_axes()
            self.axes.plot(df[xcol], df[ycol], color="#2f80ed", linewidth=1.7)
            self.axes.set_title(Path(csv_path).name, color="#1f2a37")
            self.axes.set_xlabel(xcol, color="#415368")
            self.axes.set_ylabel(ycol, color="#415368")
            self.draw_idle()
        except Exception as exc:
            self.draw_placeholder(f"Plot failed: {exc}")

class UiBridge(QObject):
    cfg_log = Signal(str)
    move_log = Signal(str)
    tx_log = Signal(str)
    a_mode_log = Signal(str)
    b_mode_log = Signal(str)
    bc_log = Signal(str)
    a_preview = Signal(object)
    b_preview = Signal(object)
    bc_plot_csv = Signal(str)
    test_busy = Signal(bool)
    scan_busy = Signal(bool)
    error = Signal(str, str)


class ScannerMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.stop_event = threading.Event()
        self.bridge = UiBridge()
        self._a_mode_last_results = None
        self._b_mode_last_results = None
        self._last_bc_csv_path = None
        self._is_loading_settings = False
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(300)
        self._settings_save_timer.timeout.connect(self._save_settings_now)
        self._build_ui()
        self._load_settings_into_ui()
        self._wire_settings_autosave()
        self._wire_bridge()
        self._apply_styles()
        self._set_default_size()

    def _wire_bridge(self) -> None:
        self.bridge.cfg_log.connect(
            lambda text: self._append_log(self.cfg_output, text)
        )
        self.bridge.move_log.connect(
            lambda text: self._append_log(self.move_output, text)
        )
        self.bridge.tx_log.connect(
            lambda text: self._append_log(self.transmit_output, text)
        )
        self.bridge.a_mode_log.connect(
            lambda text: self._append_log(self.a_output, text)
        )
        self.bridge.a_preview.connect(self._render_a_mode_preview)
        self.bridge.b_mode_log.connect(
            lambda text: self._append_log(self.b_output, text)
        )
        self.bridge.b_preview.connect(self._render_b_mode_preview)
        self.bridge.bc_log.connect(lambda text: self._append_log(self.bc_output, text))
        self.bridge.bc_plot_csv.connect(self._on_bc_plot_csv)
        self.bridge.test_busy.connect(self._set_test_busy)
        self.bridge.scan_busy.connect(self._set_scan_busy)
        self.bridge.error.connect(self._show_error)

    def _build_ui(self) -> None:
        self.setWindowTitle("A/B Scanner")
        about_action = QAction("About A/B-Scanner", self)
        about_action.triggered.connect(self.show_about)
        self.menuBar().addMenu("About").addAction(about_action)

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.config_tab = self._build_config_tab()
        self.move_tab = self._build_move_tab()
        self.transmit_tab = self._build_transmit_tab()
        self.a_mode_tab = self._build_a_mode_tab()
        self.b_mode_tab = self._build_b_mode_tab()
        self.bc_tab = self._build_bc_tab()

        self.tabs.addTab(self.config_tab, "Config")
        self.tabs.addTab(self.move_tab, "Move Rig")
        self.tabs.addTab(self.transmit_tab, "Excitation Mode")
        self.tabs.addTab(self.a_mode_tab, "A-Mode")
        self.tabs.addTab(self.b_mode_tab, "B-Mode")
        self.tabs.addTab(self.bc_tab, "3D-Mode")

    def _build_config_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QGridLayout()
        top.setHorizontalSpacing(14)
        top.setVerticalSpacing(14)
        layout.addLayout(top)

        sg_box = QGroupBox("Signal Generator")
        sg_form = QFormLayout(sg_box)
        self.sg_name_edit = QLineEdit("Agilent33500")
        self.sg_address_edit = QLineEdit()
        self.sg_address_edit.setPlaceholderText("USB0::...::INSTR")
        sg_form.addRow("Name", self.sg_name_edit)
        sg_form.addRow("VISA Address", self.sg_address_edit)
        top.addWidget(sg_box, 0, 0)

        rig_box = QGroupBox("Rig")
        rig_form = QFormLayout(rig_box)
        self.host_edit = QLineEdit("192.168.1.250")
        self.port_edit = QLineEdit("5001")
        rig_form.addRow("Host", self.host_edit)
        rig_form.addRow("Port", self.port_edit)
        top.addWidget(rig_box, 0, 1)

        osc_box = QGroupBox("Oscilloscope")
        osc_form = QFormLayout(osc_box)
        self.osc_name_edit = QLineEdit("Lecroy")
        self.osc_address_edit = QLineEdit()
        self.osc_address_edit.setPlaceholderText("USB0::...::INSTR")
        self.sampling_rate_edit = QLineEdit("1000")
        osc_form.addRow("Name", self.osc_name_edit)
        osc_form.addRow("VISA Address", self.osc_address_edit)
        osc_form.addRow("Sampling Rate (kHz)", self.sampling_rate_edit)
        top.addWidget(osc_box, 1, 0, 1, 2)

        controls = QHBoxLayout()
        self._normalize_control_row(controls)
        self.test_button = QPushButton("Test Connections")
        self.test_button.clicked.connect(self.test_connections)
        self.test_button.setProperty("role", "primary")
        self.test_retries = QSpinBox()
        self.test_retries.setRange(1, 10)
        self.test_retries.setValue(2)
        self.test_timeout = QDoubleSpinBox()
        self.test_timeout.setRange(0.2, 30.0)
        self.test_timeout.setValue(2.0)
        self.test_timeout.setSingleStep(0.5)
        self.test_progress = QProgressBar()
        self.test_progress.setRange(0, 0)
        self.test_progress.setVisible(False)
        controls.addWidget(self.test_button)
        controls.addWidget(QLabel("Retries"))
        controls.addWidget(self.test_retries)
        controls.addWidget(QLabel("Timeout (s)"))
        controls.addWidget(self.test_timeout)
        controls.addStretch(1)
        controls.addWidget(self.test_progress)
        layout.addLayout(controls)

        self.cfg_output = self._make_log()
        layout.addWidget(self.cfg_output, 1)
        return page

    def _build_move_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(14)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("Manual Rig Movement")
        form = QFormLayout(box)
        self.move_x = self._make_double_spin(-5000, 5000, 0.0)
        self.move_y = self._make_double_spin(-5000, 5000, 0.0)
        self.move_z = self._make_double_spin(-5000, 5000, 0.0)
        form.addRow("ΔX (mm)", self.move_x)
        form.addRow("ΔY (mm)", self.move_y)
        form.addRow("ΔZ (mm)", self.move_z)
        left_layout.addWidget(box)

        row = QHBoxLayout()
        self.move_button = QPushButton("Move")
        self.move_button.setProperty("role", "primary")
        self.move_button.clicked.connect(self.move_rig_now)
        self._normalize_button_row(row, [self.move_button])
        row.addWidget(self.move_button)
        row.addStretch(1)
        left_layout.addLayout(row)
        left_layout.addStretch(1)

        log_box = QGroupBox("Move Log")
        log_layout = QVBoxLayout(log_box)
        self.move_output = self._make_log()
        log_layout.addWidget(self.move_output)

        layout.addWidget(left_col, 1)
        layout.addWidget(log_box, 1)
        return page

    def _build_transmit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        top_row = QHBoxLayout()
        title = QLabel("Excitation Mode")
        title.setObjectName("SectionTitle")
        top_row.addWidget(title)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        main_top = QHBoxLayout()
        main_top.setSpacing(14)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)

        pg_box = QGroupBox("Pulse Generator")
        pg_form = QFormLayout(pg_box)
        self.tx_windowing_combo = QComboBox()
        self.tx_windowing_combo.addItems(
            ["Hanning", "Hamming", "Blackman-Harris", "Flat-Top", "None (Rectangular)"]
        )
        self.tx_freq = self._make_double_spin(0.001, 100_000.0, 1000.0, decimals=3)
        self.tx_amp = self._make_double_spin(0.01, 100, 1.0)
        self.tx_cycles = self._make_double_spin(1, 10_000, 60, decimals=0)
        self.tx_pulses = self._make_spin(1, 100000, 1)
        self.tx_prf = self._make_double_spin(0.1, 1_000_000, 1000.0, decimals=2)
        pg_form.addRow("Waveform Shape", QLabel("SIN"))
        pg_form.addRow("Windowing Function", self.tx_windowing_combo)
        pg_form.addRow("Frequency (kHz)", self.tx_freq)
        pg_form.addRow("Amplitude (V)", self.tx_amp)
        pg_form.addRow("No. Of Cycles Per Pulse", self.tx_cycles)
        pg_form.addRow("No. Of Pulses", self.tx_pulses)
        pg_form.addRow("Pulse Repetition Frequency (Hz)", self.tx_prf)
        left_layout.addWidget(pg_box)

        self.transmit_start_button = QPushButton("Start Transmit")
        self.transmit_start_button.setProperty("role", "primary")
        self.transmit_start_button.clicked.connect(self.start_transmit_mode)
        self.transmit_start_button.setFixedHeight(34)
        self.transmit_start_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "border: none; border-radius: 14px; color: #ffffff; font-weight: 700; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f6fd8, stop:1 #3c92ff); }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2d7de2, stop:1 #56a1ff); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1756ad, stop:1 #2f80ed); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; }"
        )
        self.transmit_preview_button = QPushButton("Preview")
        self.transmit_preview_button.clicked.connect(
            lambda _checked=False: self.preview_transmit_waveform(log_update=True)
        )
        self.transmit_preview_button.setStyleSheet(
            "QPushButton { min-width: 92px; min-height: 34px; max-width: 110px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.transmit_export_button = QPushButton("Export Data")
        self.transmit_export_button.clicked.connect(self.export_transmit_waveform)
        self.transmit_export_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.transmit_timing_button = QPushButton("Run Timing Test")
        self.transmit_timing_button.clicked.connect(self.start_transmit_timing_test)
        self.transmit_timing_button.setStyleSheet(
            "QPushButton { min-width: 118px; min-height: 34px; max-width: 140px; padding: 6px 12px; "
            "border: 1px solid #d0dae7; border-radius: 14px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            "stop:0 #ffffff, stop:1 #eef4fb); color: #214163; font-weight: 600; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e2edfb); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dbe9fb, stop:1 #c6dbfb); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; border-color: #dde5ee; }"
        )
        self.tx_auto_preview_check = QCheckBox("Live Preview")
        self.tx_auto_preview_check.setChecked(False)

        button_row = QHBoxLayout()
        self._normalize_button_row(
            button_row,
            [
                self.transmit_preview_button,
                self.transmit_timing_button,
                self.transmit_start_button,
            ],
        )
        button_row.addWidget(self.transmit_preview_button)
        button_row.addWidget(self.transmit_timing_button)
        button_row.addWidget(self.transmit_start_button)
        button_row.addWidget(self.tx_auto_preview_check)
        button_row.addStretch(1)
        left_layout.addLayout(button_row)
        left_layout.addStretch(1)

        log_box = QGroupBox("Excitation Log")
        log_layout = QVBoxLayout(log_box)
        self.transmit_output = self._make_log()
        log_layout.addWidget(self.transmit_output)
        self.transmit_export_logs_button = QPushButton("Export Logs")
        self.transmit_export_logs_button.clicked.connect(self.export_transmit_logs)
        self.transmit_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.transmit_export_logs_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        log_layout.addWidget(self.transmit_export_logs_button)

        main_top.addWidget(left_col, 1)
        main_top.addWidget(log_box, 1)
        layout.addLayout(main_top)

        self.tx_preview_canvas = PlotCanvas("Preview waveform will appear here")
        self.tx_preview_toolbar = NavigationToolbar2QT(self.tx_preview_canvas, page)
        layout.addWidget(self.tx_preview_toolbar)
        layout.addWidget(self.tx_preview_canvas, 3)
        tx_preview_actions = QHBoxLayout()
        self._normalize_button_row(tx_preview_actions, [self.transmit_export_button])
        tx_preview_actions.addWidget(self.transmit_export_button)
        tx_preview_actions.addStretch(1)
        layout.addLayout(tx_preview_actions)

        self.tx_windowing_combo.currentIndexChanged.connect(
            self._on_transmit_preview_inputs_changed
        )
        self.tx_freq.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_amp.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_cycles.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_pulses.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_prf.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_auto_preview_check.stateChanged.connect(
            self._on_transmit_auto_preview_toggled
        )
        return page

    def _build_a_mode_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("A-Mode")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        info = QLabel(
            "A-Mode is a POINT pulse-echo scan at the defined coordinate.\n\n"
            "Running A-mode scan using configs and excitation signal defined in Config and Excitation Mode tabs. PRF is ignored, and No. of Pulses is used for averaging the echoes. A Butterworth filter is used for high-pass filtering in A-mode envelope estimation."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)

        pos_box = QGroupBox("A-Mode Position")
        pos_form = QFormLayout(pos_box)
        self.a_mode_x = self._make_double_spin(-5000, 5000, 0.0)
        self.a_mode_y = self._make_double_spin(-5000, 5000, 0.0)
        self.a_mode_z = self._make_double_spin(-5000, 5000, 0.0)
        self.a_mode_highpass_cutoff = self._make_double_spin(0.0, 100_000.0, 50.0, decimals=1)
        self.a_mode_filter_order = self._make_spin(1, 12, 4)
        pos_form.addRow("ΔX (mm)", self.a_mode_x)
        pos_form.addRow("ΔY (mm)", self.a_mode_y)
        pos_form.addRow("ΔZ (mm)", self.a_mode_z)
        pos_form.addRow("High-pass Cutoff (kHz)", self.a_mode_highpass_cutoff)
        pos_form.addRow("Filter Order", self.a_mode_filter_order)
        left_layout.addWidget(pos_box)

        self.a_start_button = QPushButton("Start Scan")
        self.a_start_button.setProperty("role", "primary")
        self.a_start_button.clicked.connect(self.start_a_mode)
        self.a_start_button.setFixedHeight(34)
        self.a_start_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "border: none; border-radius: 14px; color: #ffffff; font-weight: 700; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f6fd8, stop:1 #3c92ff); }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2d7de2, stop:1 #56a1ff); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1756ad, stop:1 #2f80ed); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; }"
        )
        self.a_export_button = QPushButton("Export Data")
        self.a_export_button.clicked.connect(self.export_a_mode_matrix)
        self.a_export_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.a_live_preview_check = QCheckBox("Live Preview")
        self.a_live_preview_check.setChecked(True)
        self.a_mode_highpass_cutoff.valueChanged.connect(
            self._on_a_mode_filter_params_changed
        )
        self.a_mode_filter_order.valueChanged.connect(
            self._on_a_mode_filter_params_changed
        )
        action_row = QHBoxLayout()
        self._normalize_button_row(action_row, [self.a_start_button])
        action_row.addWidget(self.a_start_button)
        action_row.addWidget(self.a_live_preview_check)
        action_row.addStretch(1)
        left_layout.addLayout(action_row)
        left_layout.addStretch(1)

        log_box = QGroupBox("A-Mode Log")
        log_layout = QVBoxLayout(log_box)
        self.a_output = self._make_log()
        log_layout.addWidget(self.a_output)
        self.a_export_logs_button = QPushButton("Export Logs")
        self.a_export_logs_button.clicked.connect(self.export_a_mode_logs)
        self.a_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.a_export_logs_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        log_layout.addWidget(self.a_export_logs_button)

        top_row.addWidget(left_col, 1)
        top_row.addWidget(log_box, 1)
        layout.addLayout(top_row)

        self.a_preview_canvas = PlotCanvas("A-mode preview will appear here")
        self.a_preview_toolbar = NavigationToolbar2QT(self.a_preview_canvas, page)
        self.a_preview_toolbar.addSeparator()
        self.a_preview_toolbar.addWidget(self.a_export_button)
        layout.addWidget(self.a_preview_toolbar)
        layout.addWidget(self.a_preview_canvas, 3)
        return page

    def _build_bc_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(14)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setSpacing(12)

        title = QLabel("3D-Mode")
        title.setObjectName("SectionTitle")
        controls_layout.addWidget(title)

        info = QLabel(
            "This is a PLANAR pulse-echo scanns along Axies 1 and Axies 2. At each point A-Mode scans provide Depth information. Output data is a 3D matrix. Data are visualised at a specific depth (C-Scan) or based on specified temporal features (Field Characterisation)."
        )
        info.setWordWrap(True)
        controls_layout.addWidget(info)

        # Pulse Generator widgets kept as hidden instances for settings/logic compatibility
        self.shape_edit = QLineEdit("SIN")
        self.freq_spin = self._make_double_spin(1, 100_000_000, 1_000_000, decimals=0)
        self.amp_spin = self._make_double_spin(0.01, 100, 1.0)
        self.burst_cycles_spin = self._make_double_spin(1, 10_000, 60, decimals=0)
        self.num_bursts_spin = self._make_spin(1, 1000, 1)

        scan_box = QGroupBox("Scanning Parameters")
        scan_form = QFormLayout(scan_box)
        self.scan_axis = self._make_axis_combo("X")
        self.cross_axis = self._make_axis_combo("Z")
        self.depth_axis = self._make_axis_combo("Y")
        self.scan_length = self._make_double_spin(0.1, 10000, 20.0)
        self.scan_points = self._make_spin(1, 10000, 2)
        self.cross_length = self._make_double_spin(0.1, 10000, 20.0)
        self.cross_points = self._make_spin(1, 10000, 2)
        scan_form.addRow("Depth Axis", self.depth_axis)
        scan_form.addRow("Scan Axis 1", self.scan_axis)
        scan_form.addRow("Scan Axis 1 Length (mm)", self.scan_length)
        scan_form.addRow("Scan Axis 1 Points", self.scan_points)
        scan_form.addRow("Scan Axis 2", self.cross_axis)
        scan_form.addRow("Scan Axis 2 Length (mm)", self.cross_length)
        scan_form.addRow("Scan Axis 2 Points", self.cross_points)
        self.depth_axis.setEnabled(False)
        self._sync_depth_axis_from_scan_axes()
        self.scan_axis.currentIndexChanged.connect(self._sync_depth_axis_from_scan_axes)
        self.cross_axis.currentIndexChanged.connect(self._sync_depth_axis_from_scan_axes)
        controls_layout.addWidget(scan_box)

        options_row = QHBoxLayout()
        self._normalize_control_row(options_row)
        self.dry_run_check = QCheckBox("Dry Run")
        self.live_update_check = QCheckBox("Live Preview")
        options_row.addWidget(self.live_update_check)
        options_row.addWidget(self.dry_run_check)
        controls_layout.addLayout(options_row)

        button_row = QHBoxLayout()
        preview_button = QPushButton("Preview")
        preview_button.clicked.connect(self.preview_scan)
        preview_button.setStyleSheet(
            "QPushButton { min-width: 92px; min-height: 34px; max-width: 110px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.start_button = QPushButton("Start Scan")
        self.start_button.setProperty("role", "primary")
        self.start_button.clicked.connect(self.start_scan)
        self.start_button.setFixedHeight(34)
        self.start_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "border: none; border-radius: 14px; color: #ffffff; font-weight: 700; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f6fd8, stop:1 #3c92ff); }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2d7de2, stop:1 #56a1ff); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1756ad, stop:1 #2f80ed); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; }"
        )
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_scan)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet(
            "QPushButton { min-width: 84px; min-height: 34px; max-width: 96px; padding: 6px 12px; "
            "border: none; border-radius: 14px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #d62839, stop:1 #f05a68); color: #ffffff; font-weight: 700; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e03a49, stop:1 #f3717d); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b91f2f, stop:1 #de4a58); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; border-color: #dde5ee; }"
        )
        self._normalize_button_row(button_row, [preview_button, self.start_button, self.stop_button])
        for widget in [
            preview_button,
            self.start_button,
            self.stop_button,
        ]:
            button_row.addWidget(widget)
        controls_layout.addLayout(button_row)

        log_box = QGroupBox("3D-Mode Log")
        log_layout = QVBoxLayout(log_box)
        self.bc_output = self._make_log()
        log_layout.addWidget(self.bc_output)
        self.bc_export_logs_button = QPushButton("Export Logs")
        self.bc_export_logs_button.clicked.connect(self.export_3d_mode_logs)
        self.bc_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.bc_export_logs_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        log_layout.addWidget(self.bc_export_logs_button)
        controls_layout.addWidget(log_box, 1)
        controls_layout.addStretch(1)

        controls.setFixedWidth(B_MODE_LEFT_PANEL_WIDTH)
        layout.addWidget(controls, 0)

        preview_col = QWidget()
        preview_layout = QVBoxLayout(preview_col)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.bc_canvas = PlotCanvas("Waveform preview will appear here")
        self.bc_preview_toolbar = NavigationToolbar2QT(self.bc_canvas, page)
        self.bc_export_button = QPushButton("Export Data")
        self.bc_export_button.clicked.connect(self.export_3d_mode_data)
        self.bc_export_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.bc_preview_toolbar.addSeparator()
        self.bc_preview_toolbar.addWidget(self.bc_export_button)
        preview_layout.addWidget(self.bc_preview_toolbar)
        preview_layout.addWidget(self.bc_canvas, 1)
        layout.addWidget(preview_col, 1)
        return page

    def _build_b_mode_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(14)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        title = QLabel("B-Mode")
        title.setObjectName("SectionTitle")
        left_layout.addWidget(title)

        info = QLabel(
            "B-Mode is a LINEAR pulse-echo scan along Scan Axis. At each point an A-scan is captured and stacked into a B-mode dataset."
        )
        info.setWordWrap(True)
        left_layout.addWidget(info)

        scan_box = QGroupBox("Scanning Parameters")
        scan_form = QFormLayout(scan_box)
        self.b_depth_axis = self._make_axis_combo("X")
        self.b_scan_axis = self._make_axis_combo("Z")
        self.b_scan_length = self._make_double_spin(0.1, 10000, 20.0)
        self.b_scan_points = self._make_spin(1, 10000, 5)
        self.b_sound_speed = self._make_double_spin(0.0, 20000.0, 1500.0)
        scan_form.addRow("Depth Axis", self.b_depth_axis)
        scan_form.addRow("Scan Axis", self.b_scan_axis)
        scan_form.addRow("Scan Length (mm)", self.b_scan_length)
        scan_form.addRow("Scan Points", self.b_scan_points)
        speed_label = QLabel(
            'Speed of Sound (m/s)<br><span style="color:#c23b3b; font-size:9pt;">0 = use time in \\mu s</span>'
        )
        scan_form.addRow(speed_label, self.b_sound_speed)
        left_layout.addWidget(scan_box)

        options_row = QHBoxLayout()
        self._normalize_control_row(options_row)
        self.b_live_preview_check = QCheckBox("Live Preview")
        self.b_live_preview_check.setChecked(True)
        self.b_dry_run_check = QCheckBox("Dry Run")
        options_row.addWidget(self.b_live_preview_check)
        options_row.addWidget(self.b_dry_run_check)
        options_row.addStretch(1)
        left_layout.addLayout(options_row)

        button_row = QHBoxLayout()
        self.b_preview_button = QPushButton("Preview")
        self.b_preview_button.clicked.connect(self.preview_b_mode)
        self.b_preview_button.setStyleSheet(
            "QPushButton { min-width: 92px; min-height: 34px; max-width: 110px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.b_export_button = QPushButton("Export Data")
        self.b_export_button.clicked.connect(self.export_b_mode_matrix)
        self.b_export_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.b_start_button = QPushButton("Start Scan")
        self.b_start_button.setProperty("role", "primary")
        self.b_start_button.clicked.connect(self.start_b_mode)
        self.b_start_button.setFixedHeight(34)
        self.b_start_button.setStyleSheet(
            "QPushButton { min-width: 96px; min-height: 34px; max-width: 120px; padding: 6px 12px; "
            "border: none; border-radius: 14px; color: #ffffff; font-weight: 700; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f6fd8, stop:1 #3c92ff); }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2d7de2, stop:1 #56a1ff); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1756ad, stop:1 #2f80ed); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; }"
        )
        self.b_stop_button = QPushButton("Stop")
        self.b_stop_button.clicked.connect(self.stop_b_mode)
        self.b_stop_button.setEnabled(False)
        self.b_stop_button.setStyleSheet(
            "QPushButton { min-width: 84px; min-height: 34px; max-width: 96px; padding: 6px 12px; "
            "border: none; border-radius: 14px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #d62839, stop:1 #f05a68); color: #ffffff; font-weight: 700; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e03a49, stop:1 #f3717d); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b91f2f, stop:1 #de4a58); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; border-color: #dde5ee; }"
        )
        self._normalize_button_row(
            button_row,
            [
                self.b_preview_button,
                self.b_start_button,
                self.b_stop_button,
            ],
        )
        button_row.setSpacing(14)
        button_row.addWidget(self.b_preview_button)
        button_row.addWidget(self.b_start_button)
        button_row.addWidget(self.b_stop_button)
        button_row.addStretch(1)
        left_layout.addLayout(button_row)

        log_box = QGroupBox("B-Mode Log")
        log_layout = QVBoxLayout(log_box)
        self.b_output = self._make_log()
        log_layout.addWidget(self.b_output)
        self.b_export_logs_button = QPushButton("Export Logs")
        self.b_export_logs_button.clicked.connect(self.export_b_mode_logs)
        self.b_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.b_export_logs_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        log_layout.addWidget(self.b_export_logs_button)
        left_layout.addWidget(log_box, 1)

        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.b_preview_canvas = PlotCanvas("B-mode preview will appear here")
        self.b_preview_toolbar = NavigationToolbar2QT(self.b_preview_canvas, page)
        self.b_preview_toolbar.addSeparator()
        self.b_preview_toolbar.addWidget(self.b_export_button)
        right_layout.addWidget(self.b_preview_toolbar)
        right_layout.addWidget(self.b_preview_canvas, 1)

        left_col.setFixedWidth(B_MODE_LEFT_PANEL_WIDTH)
        left_col.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        right_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(left_col, 0)
        layout.addWidget(right_col, 1)
        return page

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f4f7fb;
                color: #1f2a37;
                font-family: 'Segoe UI', 'Noto Sans', sans-serif;
                font-size: 10pt;
            }
            QMainWindow::separator {
                background: #d5deeb;
                width: 1px;
                height: 1px;
            }
            QMenuBar {
                background: #ffffff;
                border-bottom: 1px solid #d7e0ec;
                padding: 4px;
            }
            QTabWidget::pane {
                border: 1px solid #d7e0ec;
                border-radius: 18px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: #edf3fb;
                color: #5f6b7a;
                border: 1px solid #d7e0ec;
                border-bottom: none;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
                padding: 10px 18px;
                margin-right: 6px;
                font-size: 12pt;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                color: #ffffff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2f80ed, stop:1 #56a1ff);
            }
            QTabBar::tab:hover:!selected {
                background: #e2edfb;
                color: #214163;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d7e0ec;
                border-radius: 18px;
                margin-top: 14px;
                padding: 16px 14px 14px 14px;
                font-weight: 600;
                color: #2c4f77;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
                background: #ffffff;
                color: #1f2a37;
                border: 1px solid #cfd9e6;
                border-radius: 14px;
                padding: 8px 10px;
                selection-background-color: #ffffff;
                selection-color: #1f2a37;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #2f80ed;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 22px;
                border-left: 1px solid #cfd9e6;
                border-bottom: 1px solid #2a2a2a;
                border-top-right-radius: 14px;
                background: #111111;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 22px;
                border-left: 1px solid #cfd9e6;
                border-bottom-right-radius: 14px;
                background: #111111;
            }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background: #202020;
            }
            QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
            QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
                background: #000000;
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                width: 10px;
                height: 10px;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QPushButton {
                border: none;
                border-radius: 16px;
                padding: 10px 22px;
                min-height: 40px;
                min-width: 150px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2f80ed, stop:1 #56a1ff);
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3d8ef7, stop:1 #69acff);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2468c7, stop:1 #2f80ed);
            }
            QPushButton[role='primary'] {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1f6fd8, stop:1 #3c92ff);
            }
            QPushButton[role='primary']:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2d7de2, stop:1 #56a1ff);
            }
            QPushButton[role='primary']:pressed {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1756ad, stop:1 #2f80ed);
            }
            QPushButton:disabled {
                background: #e8edf4;
                color: #95a3b5;
                border-color: #dde5ee;
            }
            QCheckBox, QLabel#SectionTitle {
                font-weight: 600;
            }
            QPlainTextEdit {
                background: #fbfdff;
            }
            QProgressBar {
                border: 1px solid #d7e0ec;
                border-radius: 10px;
                background: #eef3fb;
                min-height: 12px;
            }
            QProgressBar::chunk {
                border-radius: 10px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2f80ed, stop:1 #56a1ff);
            }
            QSplitter::handle {
                background: #e6edf7;
                border-radius: 2px;
            }
            """
        )

    def _set_default_size(self) -> None:
        screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else None
        if geometry is None:
            self.resize(1280, 720)
            return
        width = int(geometry.width() * 0.82)
        height = int(width * 9 / 16)
        if height > int(geometry.height() * 0.88):
            height = int(geometry.height() * 0.88)
            width = int(height * 16 / 9)
        self.resize(width, height)
        self.setMinimumSize(960, 540)

    def _make_log(self) -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setFrameShape(QFrame.NoFrame)
        return edit

    def _port_value(self) -> int:
        return int(self.port_edit.text().strip())

    def _make_spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _make_double_spin(
        self,
        minimum: float,
        maximum: float,
        value: float,
        decimals: int = 2,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setValue(value)
        spin.setSingleStep(0.1)
        return spin

    def _make_axis_combo(self, default: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(["X", "Y", "Z"])
        combo.setCurrentText(default)
        return combo

    def _normalize_button_row(self, row: QHBoxLayout, buttons: list[QPushButton]) -> None:
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for button in buttons:
            button.setFixedHeight(34)
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _normalize_control_row(self, row: QHBoxLayout, spacing: int = 10) -> None:
        row.setSpacing(spacing)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def _unused_axis(self, axis1: str, axis2: str) -> str:
        used = {str(axis1).strip().upper(), str(axis2).strip().upper()}
        for axis in ("X", "Y", "Z"):
            if axis not in used:
                return axis
        return "Y"

    def _sync_depth_axis_from_scan_axes(self, *_args) -> None:
        if not hasattr(self, "scan_axis") or not hasattr(self, "cross_axis"):
            return
        if not hasattr(self, "depth_axis"):
            return
        depth = self._unused_axis(
            self.scan_axis.currentText(), self.cross_axis.currentText()
        )
        if self.depth_axis.currentText() != depth:
            self.depth_axis.setCurrentText(depth)

    def _append_log(self, widget: QPlainTextEdit, text: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not widget.toPlainText().strip():
            widget.appendPlainText(f"[{timestamp}] Log session started")

        lines = str(text).splitlines() or [str(text)]
        for line in lines:
            widget.appendPlainText(f"[{timestamp}] {line}")
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    def _on_bc_plot_csv(self, csv_path: str) -> None:
        self._last_bc_csv_path = csv_path
        self.bc_canvas.plot_waveform(csv_path)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _set_test_busy(self, busy: bool) -> None:
        self.test_button.setEnabled(not busy)
        self.test_progress.setVisible(busy)

    def _set_scan_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About A/B Scanner",
            "A/B Scanner\n\nPySide6 desktop UI for the ultrasonic scanner rig.",
        )

    def _load_settings_file(self) -> dict:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _load_settings_into_ui(self) -> None:
        settings = self._load_settings_file()
        if not settings:
            return

        self._is_loading_settings = True
        try:
            cfg = settings.get("config", {})
            tx = settings.get("excitation", {})
            a_mode = settings.get("a_mode", {})
            b_line = settings.get("line_b_mode", {})
            bc = settings.get("b_mode", settings.get("bc_mode", {}))

            self.sg_address_edit.setText(
                str(cfg.get("sg_address", self.sg_address_edit.text()))
            )
            self.sg_name_edit.setText(
                str(cfg.get("sg_name", self.sg_name_edit.text()))
            )
            self.osc_name_edit.setText(
                str(cfg.get("osc_name", self.osc_name_edit.text()))
            )
            self.osc_address_edit.setText(
                str(cfg.get("osc_address", self.osc_address_edit.text()))
            )
            self.host_edit.setText(str(cfg.get("host", self.host_edit.text())))
            self.port_edit.setText(str(cfg.get("port", self.port_edit.text())))
            sampling_rate_hz = self._extract_last_float(
                str(cfg.get("sampling_rate", self.sampling_rate_edit.text())),
                self._extract_last_float(self.sampling_rate_edit.text(), 1000.0) * 1000.0,
            )
            self.sampling_rate_edit.setText(f"{sampling_rate_hz / 1000.0:g}")

            self.tx_windowing_combo.setCurrentText(
                str(tx.get("window_type", self.tx_windowing_combo.currentText()))
            )
            self.tx_freq.setValue(
                float(tx.get("frequency", self.tx_freq.value() * 1000.0)) / 1000.0
            )
            self.tx_amp.setValue(float(tx.get("amplitude", self.tx_amp.value())))
            self.tx_cycles.setValue(
                float(tx.get("no_of_cycles_per_pulse", self.tx_cycles.value()))
            )
            self.tx_pulses.setValue(int(tx.get("no_of_pulses", self.tx_pulses.value())))
            self.tx_prf.setValue(float(tx.get("prf", self.tx_prf.value())))
            self.tx_auto_preview_check.setChecked(
                bool(tx.get("auto_preview", self.tx_auto_preview_check.isChecked()))
            )

            self.a_mode_x.setValue(float(a_mode.get("X", self.a_mode_x.value())))
            self.a_mode_y.setValue(float(a_mode.get("Y", self.a_mode_y.value())))
            self.a_mode_z.setValue(float(a_mode.get("Z", self.a_mode_z.value())))
            self.a_mode_highpass_cutoff.setValue(
                float(a_mode.get("highpass_cutoff_hz", 50_000.0)) / 1000.0
            )
            self.a_mode_filter_order.setValue(
                int(a_mode.get("filter_order", self.a_mode_filter_order.value()))
            )
            self.a_live_preview_check.setChecked(
                bool(a_mode.get("live_preview", self.a_live_preview_check.isChecked()))
            )

            self.b_depth_axis.setCurrentText(
                str(b_line.get("depth_axis", self.b_depth_axis.currentText()))
            )
            self.b_scan_axis.setCurrentText(
                str(b_line.get("scan_axis", self.b_scan_axis.currentText()))
            )
            self.b_scan_length.setValue(
                float(b_line.get("scan_length", self.b_scan_length.value()))
            )
            self.b_scan_points.setValue(
                int(b_line.get("scan_points", self.b_scan_points.value()))
            )
            self.b_sound_speed.setValue(
                float(b_line.get("sound_speed_mps", self.b_sound_speed.value()))
            )
            self.b_dry_run_check.setChecked(
                bool(b_line.get("dry_run", self.b_dry_run_check.isChecked()))
            )
            self.b_live_preview_check.setChecked(
                bool(b_line.get("live_preview", self.b_live_preview_check.isChecked()))
            )

            self.shape_edit.setText(str(bc.get("shape", self.shape_edit.text())))
            self.freq_spin.setValue(float(bc.get("frequency", self.freq_spin.value())))
            self.amp_spin.setValue(float(bc.get("amplitude", self.amp_spin.value())))
            self.burst_cycles_spin.setValue(
                float(bc.get("no_of_cycles_per_pulse", self.burst_cycles_spin.value()))
            )
            self.num_bursts_spin.setValue(
                int(bc.get("no_of_pulses", self.num_bursts_spin.value()))
            )
            self.scan_axis.setCurrentText(
                str(bc.get("scan_axis", self.scan_axis.currentText()))
            )
            self.cross_axis.setCurrentText(
                str(bc.get("cross_axis", self.cross_axis.currentText()))
            )
            self.depth_axis.setCurrentText(
                str(
                    bc.get(
                        "depth_axis",
                        self._unused_axis(
                            self.scan_axis.currentText(), self.cross_axis.currentText()
                        ),
                    )
                )
            )
            self._sync_depth_axis_from_scan_axes()
            self.scan_length.setValue(
                float(bc.get("scan_length", self.scan_length.value()))
            )
            self.scan_points.setValue(
                int(bc.get("scan_points", self.scan_points.value()))
            )
            self.cross_length.setValue(
                float(bc.get("cross_length", self.cross_length.value()))
            )
            self.cross_points.setValue(
                int(bc.get("cross_points", self.cross_points.value()))
            )
            self.dry_run_check.setChecked(
                bool(bc.get("dry_run", self.dry_run_check.isChecked()))
            )
            self.live_update_check.setChecked(
                bool(bc.get("live_update", self.live_update_check.isChecked()))
            )
        except Exception:
            pass
        finally:
            self._is_loading_settings = False

    def _collect_settings_payload(self) -> dict:
        return {
            "config": {
                "sg_name": self.sg_name_edit.text().strip(),
                "sg_address": self.sg_address_edit.text().strip(),
                "osc_name": self.osc_name_edit.text().strip(),
                "osc_address": self.osc_address_edit.text().strip(),
                "host": self.host_edit.text().strip(),
                "port": self.port_edit.text().strip(),
                "sampling_rate": self._extract_last_float(
                    self.sampling_rate_edit.text().strip(), 1000.0
                )
                * 1000.0,
            },
            "excitation": {
                "shape": "SIN",
                "window_type": self.tx_windowing_combo.currentText(),
                "frequency": float(self.tx_freq.value()) * 1000.0,
                "amplitude": float(self.tx_amp.value()),
                "no_of_cycles_per_pulse": float(self.tx_cycles.value()),
                "no_of_pulses": int(self.tx_pulses.value()),
                "prf": float(self.tx_prf.value()),
                "timing_tolerance_pct": 10.0,
                "auto_preview": bool(self.tx_auto_preview_check.isChecked()),
            },
            "a_mode": {
                "X": float(self.a_mode_x.value()),
                "Y": float(self.a_mode_y.value()),
                "Z": float(self.a_mode_z.value()),
                "highpass_cutoff_hz": float(self.a_mode_highpass_cutoff.value())
                * 1000.0,
                "filter_order": int(self.a_mode_filter_order.value()),
                "mode": "INC",
                "live_preview": bool(self.a_live_preview_check.isChecked()),
                "average_echoes": int(self.tx_pulses.value()),
            },
            "line_b_mode": {
                "depth_axis": self.b_depth_axis.currentText(),
                "scan_axis": self.b_scan_axis.currentText(),
                "scan_length": float(self.b_scan_length.value()),
                "scan_points": int(self.b_scan_points.value()),
                "sound_speed_mps": float(self.b_sound_speed.value()),
                "dry_run": bool(self.b_dry_run_check.isChecked()),
                "live_preview": bool(self.b_live_preview_check.isChecked()),
            },
            "b_mode": {
                "shape": self.shape_edit.text().strip() or "SIN",
                "frequency": float(self.freq_spin.value()),
                "amplitude": float(self.amp_spin.value()),
                "no_of_cycles_per_pulse": float(self.burst_cycles_spin.value()),
                "no_of_pulses": int(self.num_bursts_spin.value()),
                "scan_axis": self.scan_axis.currentText(),
                "cross_axis": self.cross_axis.currentText(),
                "depth_axis": self.depth_axis.currentText(),
                "scan_length": float(self.scan_length.value()),
                "scan_points": int(self.scan_points.value()),
                "cross_length": float(self.cross_length.value()),
                "cross_points": int(self.cross_points.value()),
                "dry_run": bool(self.dry_run_check.isChecked()),
                "live_update": bool(self.live_update_check.isChecked()),
            },
        }

    def _save_settings_now(self) -> None:
        if self._is_loading_settings:
            return
        payload = self._collect_settings_payload()
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp_path = SETTINGS_PATH.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(temp_path, SETTINGS_PATH)
        except Exception as exc:
            self.bridge.cfg_log.emit(f"Settings save failed: {exc}")

    def _schedule_settings_save(self, *_args) -> None:
        if self._is_loading_settings:
            return
        self._settings_save_timer.start()

    def _wire_settings_autosave(self) -> None:
        line_edits = [
            self.sg_name_edit,
            self.sg_address_edit,
            self.osc_name_edit,
            self.osc_address_edit,
            self.host_edit,
            self.port_edit,
            self.sampling_rate_edit,
            self.shape_edit,
        ]
        for widget in line_edits:
            widget.textChanged.connect(self._schedule_settings_save)

        spin_boxes = [
            self.tx_freq,
            self.tx_amp,
            self.tx_cycles,
            self.tx_pulses,
            self.tx_prf,
            self.a_mode_x,
            self.a_mode_y,
            self.a_mode_z,
            self.a_mode_highpass_cutoff,
            self.a_mode_filter_order,
            self.b_scan_length,
            self.b_scan_points,
            self.b_sound_speed,
            self.freq_spin,
            self.amp_spin,
            self.burst_cycles_spin,
            self.num_bursts_spin,
            self.scan_length,
            self.scan_points,
            self.cross_length,
            self.cross_points,
        ]
        for widget in spin_boxes:
            widget.valueChanged.connect(self._schedule_settings_save)

        combos = [
            self.tx_windowing_combo,
            self.b_depth_axis,
            self.b_scan_axis,
            self.scan_axis,
            self.cross_axis,
            self.depth_axis,
        ]
        for widget in combos:
            widget.currentIndexChanged.connect(self._schedule_settings_save)

        checks = [
            self.tx_auto_preview_check,
            self.a_live_preview_check,
            self.b_live_preview_check,
            self.b_dry_run_check,
            self.dry_run_check,
            self.live_update_check,
        ]
        for widget in checks:
            widget.stateChanged.connect(self._schedule_settings_save)

    def _extract_last_float(self, text: str, fallback: float) -> float:
        for token in reversed(str(text).replace(",", " ").split()):
            try:
                return float(token)
            except ValueError:
                continue
        return fallback

    def _capture_a_mode_waveform(self, osc, frequency: float, amplitude: float, cycles: float):
        import numpy as np

        def vbs(cmd: str) -> None:
            osc.write(f"VBS '{cmd}'")
            time.sleep(0.05)

        burst_duration = max(float(cycles) / max(float(frequency), 1e-12), 1e-9)
        hor_scale = max(burst_duration / 5.0, 1e-9)
        ver_scale = max(float(amplitude), 0.01)
        sampling_rate = (
            self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(float(frequency) * 100.0, 1e6) / 1000.0,
            )
            * 1000.0
        )

        vbs(f"app.Acquisition.Horizontal.HorScale = {hor_scale}")
        vbs(f"app.Acquisition.C1.VerScale = {ver_scale}")
        vbs(f"app.Acquisition.Horizontal.SampleRate = {sampling_rate}")
        vbs("app.Acquisition.C1.Offset = 0")
        vbs("app.Acquisition.C1.View = true")
        vbs('app.Acquisition.Trigger.Source = "C1"')
        osc.write("TRIG_MODE NORM")

        raw_data = osc.query_binary_values("C1:WF? DAT1", datatype="B", container=np.array)
        vdiv = self._extract_last_float(osc.query("C1:VDIV?"), 1.0)
        ofst = self._extract_last_float(osc.query("C1:OFST?"), 0.0)
        volts = ((raw_data - 128) * vdiv + ofst - 128) * (1.0 / 30.0)

        tdiv = self._extract_last_float(osc.query("TDIV?"), burst_duration / 10.0)
        n = len(raw_data)
        t = np.linspace(0.0, 10.0 * tdiv, n, endpoint=False)
        return t, volts

    def _render_a_mode_preview(self, payload: dict) -> None:
        import numpy as np

        mode = payload.get("mode", "live")
        t = np.asarray(payload.get("t", []))
        y = np.asarray(payload.get("y", []))
        y_first = np.asarray(payload.get("y_first", []))
        y_avg = np.asarray(payload.get("y_avg", []))
        y_mode = np.asarray(payload.get("y_mode", []))

        self.a_preview_canvas.figure.clear()
        self.a_preview_canvas.axes = self.a_preview_canvas.figure.add_subplot(111)
        self.a_preview_canvas._style_axes()

        if mode == "live":
            pulse_idx = int(payload.get("pulse_idx", 1))
            pulse_total = int(payload.get("pulse_total", 1))
            self.a_preview_canvas.axes.plot(t, y, color="#2f80ed", linewidth=1.2)
            self.a_preview_canvas.axes.set_title(
                f"A-Mode Live Preview ({pulse_idx}/{pulse_total})", color="#1f2a37"
            )
        else:
            live_enabled = bool(payload.get("live_enabled", False))
            pulse_total = int(payload.get("pulse_total", 1))
            if live_enabled and y.size == y_avg.size and y.size > 0:
                if pulse_total > 1 and y_first.size == y.size:
                    self.a_preview_canvas.axes.plot(
                        t,
                        y_first,
                        color="#3a8d5c",
                        linewidth=1.0,
                        alpha=0.7,
                        label="First echo",
                    )
                self.a_preview_canvas.axes.plot(
                    t, y, color="#2f80ed", linewidth=1.0, alpha=0.6, label="Last echo"
                )
                self.a_preview_canvas.axes.plot(
                    t, y_avg, color="#e04b3f", linewidth=1.8, label="Average"
                )
                if y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.plot(
                        t,
                        y_mode,
                        color="#8a3ffc",
                        linewidth=1.8,
                        linestyle="--",
                        label="A-Mode envelope",
                    )
                self.a_preview_canvas.axes.legend(loc="best")
                self.a_preview_canvas.axes.set_title(
                    "A-Mode First/Last Echoes + Average", color="#1f2a37"
                )
            else:
                if pulse_total > 1 and y_first.size == y_avg.size and y_avg.size > 0:
                    self.a_preview_canvas.axes.plot(
                        t,
                        y_first,
                        color="#3a8d5c",
                        linewidth=1.0,
                        alpha=0.7,
                        label="First echo",
                    )
                    self.a_preview_canvas.axes.plot(
                        t,
                        y,
                        color="#2f80ed",
                        linewidth=1.0,
                        alpha=0.6,
                        label="Last echo",
                    )
                self.a_preview_canvas.axes.plot(
                    t, y_avg, color="#e04b3f", linewidth=1.8, label="Average"
                )
                if y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.plot(
                        t,
                        y_mode,
                        color="#8a3ffc",
                        linewidth=1.8,
                        linestyle="--",
                        label="A-Mode envelope",
                    )
                if pulse_total > 1:
                    self.a_preview_canvas.axes.legend(loc="best")
                elif y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.legend(loc="best")
                self.a_preview_canvas.axes.set_title(
                    "A-Mode Average Echo", color="#1f2a37"
                )

        self.a_preview_canvas.axes.set_xlabel("Time (s)", color="#415368")
        self.a_preview_canvas.axes.set_ylabel("Amplitude (V)", color="#415368")
        self.a_preview_canvas.draw_idle()

    def _on_a_mode_filter_params_changed(self, *_args) -> None:
        """Recompute and refresh A-mode envelope overlay from last captured traces."""
        if not self._a_mode_last_results:
            return
        try:
            import importlib.util
            import numpy as np

            script_path = BASE_DIR / "A scan.py"
            spec = importlib.util.spec_from_file_location("a_scan", script_path)
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            t_ref = np.asarray(self._a_mode_last_results.get("t", []))
            traces = np.asarray(self._a_mode_last_results.get("traces", []))
            if t_ref.size == 0 or traces.size == 0:
                return

            avg = np.mean(traces, axis=0)
            a_mode_signal = module.estimate_a_mode_signal(
                t_ref,
                avg,
                highpass_cutoff_hz=float(self.a_mode_highpass_cutoff.value()) * 1000.0,
                filter_order=int(self.a_mode_filter_order.value()),
                sampling_rate_hz=self._extract_last_float(
                    self.sampling_rate_edit.text().strip(), 0.0
                )
                * 1000.0,
            )

            first = traces[0]
            last = traces[-1]
            pulse_total = int(traces.shape[0])
            live_enabled = bool(self._a_mode_last_results.get("live_enabled", True))

            self._a_mode_last_results["avg"] = avg
            self._a_mode_last_results["a_mode"] = a_mode_signal
            self.bridge.a_preview.emit(
                {
                    "mode": "final",
                    "t": t_ref,
                    "y_first": first,
                    "y": last,
                    "y_avg": avg,
                    "y_mode": a_mode_signal,
                    "pulse_total": pulse_total,
                    "live_enabled": live_enabled,
                }
            )
        except Exception as exc:
            self.bridge.a_mode_log.emit(f"A-mode live filter update failed: {exc}")

    def export_a_mode_matrix(self) -> None:
        if not self._a_mode_last_results:
            self._show_error(
                "Export failed",
                "No A-mode data available. Run A-mode first to capture traces.",
            )
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export A-mode Matrix",
            str(BASE_DIR.parent / "data" / "a_mode_matrix.txt"),
            "Text files (*.txt);;CSV files (*.csv);;All files (*.*)",
        )
        if not filename:
            return

        try:
            import numpy as np

            t = np.asarray(self._a_mode_last_results["t"])
            traces = np.asarray(self._a_mode_last_results["traces"])
            avg = np.asarray(self._a_mode_last_results["avg"])

            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                header = ["Time_s"]
                header.extend(f"AScan_{idx + 1}" for idx in range(traces.shape[0]))
                header.append("Average")
                f.write(",".join(header) + "\n")

                for i in range(len(t)):
                    row = [f"{t[i]:.9e}"]
                    row.extend(f"{traces[p, i]:.9e}" for p in range(traces.shape[0]))
                    row.append(f"{avg[i]:.9e}")
                    f.write(",".join(row) + "\n")

            self.bridge.a_mode_log.emit(f"A-mode matrix exported to: {filename}")
        except Exception as exc:
            self._show_error("Export failed", str(exc))

    def _export_log_widget_text(
        self, log_widget: QPlainTextEdit, title: str, default_filename: str
    ) -> None:
        text = log_widget.toPlainText()
        if not text.strip():
            self._show_error("Export failed", "No log content available to export.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            title,
            str(BASE_DIR.parent / "data" / default_filename),
            "Text files (*.txt);;All files (*.*)",
        )
        if not filename:
            return

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            self._show_error("Export failed", str(exc))

    def export_transmit_logs(self) -> None:
        self._export_log_widget_text(
            self.transmit_output,
            "Export Excitation Logs",
            "excitation_log.txt",
        )

    def export_a_mode_logs(self) -> None:
        self._export_log_widget_text(
            self.a_output,
            "Export A-Mode Logs",
            "a_mode_log.txt",
        )

    def export_b_mode_logs(self) -> None:
        self._export_log_widget_text(
            self.b_output,
            "Export B-Mode Logs",
            "b_mode_log.txt",
        )

    def export_3d_mode_logs(self) -> None:
        self._export_log_widget_text(
            self.bc_output,
            "Export 3D-Mode Logs",
            "3d_mode_log.txt",
        )

    def export_3d_mode_data(self) -> None:
        csv_path = self._last_bc_csv_path
        if not csv_path or not os.path.exists(csv_path):
            self._show_error(
                "Export failed",
                "No 3D-Mode preview data available. Run a scan or preview acquisition first.",
            )
            return

        suggested_name = Path(csv_path).name
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export 3D-Mode Data",
            str(BASE_DIR.parent / "data" / suggested_name),
            "CSV files (*.csv);;Text files (*.txt);;All files (*.*)",
        )
        if not filename:
            return

        try:
            shutil.copyfile(csv_path, filename)
            self.bridge.bc_log.emit(f"3D-Mode data exported to: {filename}")
        except OSError as exc:
            self._show_error("Export failed", str(exc))

    def collect_pg(self) -> dict:
        return {
            "sg_address": self.sg_address_edit.text().strip() or None,
            "shape": self.shape_edit.text().strip() or "SIN",
            "frequency": float(self.freq_spin.value()),
            "amplitude": float(self.amp_spin.value()),
            "no_of_cycles_per_pulse": float(self.burst_cycles_spin.value()),
            "no_of_pulses": int(self.num_bursts_spin.value()),
        }

    def validate_scan_inputs(self):
        pg = self.collect_pg()
        scan = {
            "host": self.host_edit.text().strip(),
            "port": self._port_value(),
            "scan_axis": self.scan_axis.currentText(),
            "cross_axis": self.cross_axis.currentText(),
            "depth_axis": self.depth_axis.currentText(),
            "scan_length": float(self.scan_length.value()),
            "scan_points": int(self.scan_points.value()),
            "cross_length": float(self.cross_length.value()),
            "cross_points": int(self.cross_points.value()),
        }
        scan_step_mm = compute_step(scan["scan_length"], scan["scan_points"])
        cross_step_mm = compute_step(scan["cross_length"], scan["cross_points"])
        scan["scan_step"] = max(1, int(round(scan_step_mm * MM_TO_PULSE)))
        scan["cross_step"] = max(1, int(round(cross_step_mm * MM_TO_PULSE)))
        return pg, scan

    def preview_scan(self) -> None:
        pg, scan = self.validate_scan_inputs()
        message = (
            f"Scan axis: {scan['scan_axis']} ({scan['scan_points']} points, step {scan['scan_step']} pulses)\n"
            f"Cross axis: {scan['cross_axis']} ({scan['cross_points']} rows, step {scan['cross_step']} pulses)\n"
            f"Depth axis: {scan['depth_axis']}\n"
            f"Pulse: shape={pg['shape']}, freq={pg['frequency']} Hz, amp={pg['amplitude']} V, cycles/pulse={pg['no_of_cycles_per_pulse']}, pulses={pg['no_of_pulses']}"
        )
        QMessageBox.information(self, "Scan Preview", message)

    def collect_b_mode_inputs(self) -> dict:
        params = {
            "host": self.host_edit.text().strip(),
            "port": self._port_value(),
            "depth_axis": self.b_depth_axis.currentText(),
            "scan_axis": self.b_scan_axis.currentText(),
            "scan_length": float(self.b_scan_length.value()),
            "scan_points": int(self.b_scan_points.value()),
            "sound_speed_mps": float(self.b_sound_speed.value()),
        }
        step_mm = compute_step(params["scan_length"], params["scan_points"])
        params["scan_step"] = max(1, int(round(step_mm * MM_TO_PULSE)))
        return params

    def preview_b_mode(self) -> None:
        params = self.collect_b_mode_inputs()
        message = (
            f"Scan axis: {params['scan_axis']} ({params['scan_points']} points, step {params['scan_step']} pulses)\n"
            f"Depth axis: {params['depth_axis']}\n"
            f"Speed of sound: {params['sound_speed_mps']} m/s\n"
            f"Dry run: {self.b_dry_run_check.isChecked()} | Live preview: {self.b_live_preview_check.isChecked()}"
        )
        QMessageBox.information(self, "B-Mode Preview", message)

    def _render_b_mode_preview(self, payload: dict) -> None:
        import numpy as np

        mode = payload.get("mode", "b_image")
        x_axis = np.asarray(payload.get("x_axis", []), dtype=float)
        scan_mm = np.asarray(payload.get("scan_mm", []), dtype=float)
        image = np.asarray(payload.get("image", []), dtype=float)
        x_label = str(payload.get("x_label", "Depth (mm)"))
        if x_axis.size == 0 or scan_mm.size == 0 or image.size == 0:
            self.b_preview_canvas.draw_placeholder("B-mode preview will appear here")
            return

        self.b_preview_canvas.figure.clear()
        self.b_preview_canvas.axes = self.b_preview_canvas.figure.add_subplot(111)
        self.b_preview_canvas._style_axes()

        draw_image = np.array(image, copy=True)
        draw_image[~np.isfinite(draw_image)] = np.nan
        extent = [float(x_axis.min()), float(x_axis.max()), float(scan_mm.min()), float(scan_mm.max())]
        im = self.b_preview_canvas.axes.imshow(
            draw_image,
            cmap="gray",
            aspect="auto",
            interpolation="nearest",
            origin="lower",
            extent=extent,
        )
        cbar = self.b_preview_canvas.figure.colorbar(im, ax=self.b_preview_canvas.axes)
        cbar.set_label("Amplitude")

        if mode == "b_image_live":
            pulse_idx = int(payload.get("pulse_idx", 1))
            pulse_total = int(payload.get("pulse_total", image.shape[0]))
            self.b_preview_canvas.axes.set_title(
                f"B-Mode Live Preview ({pulse_idx}/{pulse_total})", color="#1f2a37"
            )
        else:
            self.b_preview_canvas.axes.set_title("B-Mode Image", color="#1f2a37")

        self.b_preview_canvas.axes.set_xlabel(x_label, color="#415368")
        self.b_preview_canvas.axes.set_ylabel("Scanning Axis (mm)", color="#415368")
        self.b_preview_canvas.draw_idle()

    def start_b_mode(self) -> None:
        self._save_settings_now()
        self._b_mode_last_results = None
        self.b_output.clear()
        self.stop_event.clear()
        self.b_start_button.setEnabled(False)
        self.b_stop_button.setEnabled(True)
        threading.Thread(target=self._run_b_mode_worker, daemon=True).start()

    def stop_b_mode(self) -> None:
        self.stop_event.set()
        self.bridge.b_mode_log.emit("Stop requested. Current capture/move will finish first.")

    def _run_b_mode_worker(self) -> None:
        script_path = BASE_DIR / "A scan.py"
        sg = None
        oscmod = None
        osc = None
        motion_sock = None
        rig_function = None
        try:
            import numpy as np
            import importlib

            params = self.collect_b_mode_inputs()
            dry_run = bool(self.b_dry_run_check.isChecked())
            live_enabled = bool(self.b_live_preview_check.isChecked())
            points = int(params["scan_points"])
            scan_step = int(params["scan_step"])
            sound_speed_mps = float(params["sound_speed_mps"])

            spec = importlib.util.spec_from_file_location("a_scan", script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            prf_hz = float(self.tx_prf.value())
            window_fn = self.tx_windowing_combo.currentText()
            sampling_rate = self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(frequency * 100.0, 1e6) / 1000.0,
            ) * 1000.0
            cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            filter_order = int(self.a_mode_filter_order.value())

            if not dry_run:
                pm = importlib.import_module("pymeasure.instruments.agilent")
                Agilent33500 = getattr(pm, "Agilent33500", None)
                if Agilent33500 is None:
                    raise ImportError("Agilent33500 not available")
                from Signal_function import Burst_generate
                import Oscilloscope as oscmod
                import rig_function

                sg_address = self.sg_address_edit.text().strip() or None
                sg = Agilent33500(sg_address) if sg_address else Agilent33500()
                osc = oscmod.open_oscilloscope(self.osc_address_edit.text().strip() or None)
                motion_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                motion_sock.connect((params["host"], params["port"]))
                rig_function.send_command(motion_sock, "INC")
                rig_function.enable_axis(motion_sock, params["scan_axis"])
            else:
                Burst_generate = None

            self.bridge.b_mode_log.emit(
                f"B-Mode settings: scan_axis={params['scan_axis']}, depth_axis={params['depth_axis']}, "
                f"scan_points={points}, scan_step={scan_step}, sound_speed={sound_speed_mps} m/s, "
                f"dry_run={dry_run}, live_preview={live_enabled}"
            )

            scan_mm = np.linspace(0.0, float(params["scan_length"]), points)
            x_axis = None
            x_label = "Depth (mm)" if sound_speed_mps > 0 else r"Time ($\mu$s)"
            b_image = None
            for idx in range(1, points + 1):
                if self.stop_event.is_set():
                    self.bridge.b_mode_log.emit("B-Mode scan stopped by user.")
                    break
                if dry_run:
                    t, y = module.generate_test_echo(
                        frequency_hz=frequency,
                        amplitude_v=amplitude,
                        no_of_cycles_per_pulse=cycles,
                        window_type=window_fn,
                        sampling_rate_hz=sampling_rate,
                    )
                else:
                    Burst_generate(
                        sg,
                        shape="SIN",
                        frequency=frequency,
                        amplitude=amplitude,
                        no_of_cycles_per_pulse=cycles,
                        no_of_pulses=1,
                        prf=prf_hz,
                        window_type=window_fn,
                    )
                    t, y = self._capture_a_mode_waveform(osc, frequency, amplitude, cycles)

                envelope = module.estimate_a_mode_signal(
                    t,
                    y,
                    highpass_cutoff_hz=cutoff_hz,
                    filter_order=filter_order,
                    sampling_rate_hz=sampling_rate,
                )
                t_axis = np.asarray(t, dtype=float)
                env_axis = np.asarray(envelope, dtype=float)
                n = min(t_axis.size, env_axis.size)
                t_axis = t_axis[:n]
                env_axis = env_axis[:n]

                if x_axis is None:
                    if sound_speed_mps > 0:
                        x_axis = sound_speed_mps * t_axis * 1000.0
                    else:
                        x_axis = t_axis * 1_000_000.0
                    b_image = np.full((points, x_axis.size), np.nan, dtype=float)

                n_use = min(x_axis.size, env_axis.size)
                b_image[idx - 1, :n_use] = env_axis[:n_use]
                self.bridge.b_mode_log.emit(f"Captured B-mode point {idx}/{points}")

                if live_enabled and x_axis is not None and b_image is not None:
                    self.bridge.b_preview.emit(
                        {
                            "mode": "b_image_live",
                            "x_axis": x_axis,
                            "x_label": x_label,
                            "scan_mm": scan_mm,
                            "image": b_image,
                            "pulse_idx": idx,
                            "pulse_total": points,
                        }
                    )

                if idx < points and not dry_run and rig_function and motion_sock:
                    rig_function.send_command(motion_sock, f"{params['scan_axis']}{scan_step}")
                    rig_function.wait_until_stopped(motion_sock, params["scan_axis"])

            if x_axis is None or b_image is None:
                raise RuntimeError("No B-mode echoes captured.")

            self._b_mode_last_results = {
                "x_axis": x_axis,
                "x_label": x_label,
                "scan_mm": scan_mm,
                "image": b_image,
                "live_enabled": live_enabled,
            }
            self.bridge.b_preview.emit(
                {
                    "mode": "b_image_final",
                    "x_axis": x_axis,
                    "x_label": x_label,
                    "scan_mm": scan_mm,
                    "image": b_image,
                    "pulse_total": points,
                    "live_enabled": live_enabled,
                }
            )
            self.bridge.b_mode_log.emit("B-Mode scan finished.")
        except Exception as exc:
            self.bridge.b_mode_log.emit(f"B-Mode scan failed: {exc}")
        finally:
            if motion_sock is not None:
                try:
                    motion_sock.close()
                except Exception:
                    pass
            if sg is not None:
                try:
                    sg.shutdown()
                except Exception:
                    pass
            if oscmod is not None:
                try:
                    oscmod.close_oscilloscope()
                except Exception:
                    pass
            self.b_start_button.setEnabled(True)
            self.b_stop_button.setEnabled(False)

    def export_b_mode_matrix(self) -> None:
        if not self._b_mode_last_results:
            self._show_error(
                "Export failed",
                "No B-mode data available. Run B-mode first to capture traces.",
            )
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export B-mode Matrix",
            str(BASE_DIR.parent / "data" / "b_mode_matrix.txt"),
            "Text files (*.txt);;CSV files (*.csv);;All files (*.*)",
        )
        if not filename:
            return

        try:
            import numpy as np

            x_axis = np.asarray(self._b_mode_last_results["x_axis"])
            x_label = str(self._b_mode_last_results.get("x_label", "Depth (mm)"))
            scan_mm = np.asarray(self._b_mode_last_results["scan_mm"])
            image = np.asarray(self._b_mode_last_results["image"])

            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                header = ["ScanAxis_mm"]
                prefix = "Depth_mm" if x_label == "Depth (mm)" else "Time_us"
                header.extend(f"{prefix}_{value:.6f}" for value in x_axis)
                f.write(",".join(header) + "\n")

                for row_idx in range(len(scan_mm)):
                    row = [f"{scan_mm[row_idx]:.9e}"]
                    row.extend(f"{image[row_idx, col_idx]:.9e}" for col_idx in range(image.shape[1]))
                    f.write(",".join(row) + "\n")

            self.bridge.b_mode_log.emit(f"B-mode matrix exported to: {filename}")
        except Exception as exc:
            self._show_error("Export failed", str(exc))

    def _validate_transmit_inputs(self) -> tuple[bool, str]:
        frequency = float(self.tx_freq.value()) * 1000.0
        cycles = float(self.tx_cycles.value())
        pulses = int(self.tx_pulses.value())
        prf_hz = float(self.tx_prf.value())

        if frequency <= 0:
            return False, "Frequency must be greater than 0 kHz."
        if cycles <= 0:
            return False, "No. Of Cycles Per Pulse must be greater than 0."
        if pulses <= 0:
            return False, "No. Of Pulses must be greater than 0."
        if prf_hz <= 0:
            return False, "Pulse Repetition Frequency must be greater than 0 kHz."

        pulse_width_sec = cycles / frequency
        prf_period_sec = 1.0 / prf_hz
        if pulse_width_sec >= prf_period_sec:
            return (
                False,
                "Invalid PRF/pulse combination: pulse width must be shorter than the PRF period. "
                "Reduce cycles per pulse, increase frequency, or reduce PRF.",
            )
        return True, ""

    def _window_array(self, window_type: str, n: int):
        import numpy as np

        wtype = (window_type or "Hanning").strip().lower()
        if wtype in {
            "none",
            "none (rectangular)",
            "rectangular",
            "no window",
            "no windowing",
        }:
            return np.ones(n)
        if wtype == "hamming":
            return np.hamming(n)
        if wtype in {"blackman-harris", "blackman harris", "blackmanharris"}:
            return np.blackman(n)
        if wtype in {"flat-top", "flat top", "flattop"}:
            if n <= 1:
                return np.ones(n)
            # Normalized 5-term flat-top (Heinzel 2002 / scipy.signal.windows.flattop)
            a0, a1, a2, a3, a4 = (
                0.21557895,
                0.41663158,
                0.277263158,
                0.083578947,
                0.006947368,
            )
            x = 2 * np.pi * np.arange(n) / (n - 1)
            return (
                a0
                - a1 * np.cos(x)
                + a2 * np.cos(2 * x)
                - a3 * np.cos(3 * x)
                + a4 * np.cos(4 * x)
            )
        # Default: Hanning
        return np.hanning(n)

    def _on_transmit_preview_inputs_changed(self, *_args) -> None:
        if self.tx_auto_preview_check.isChecked():
            self.preview_transmit_waveform(log_update=False)

    def _on_transmit_auto_preview_toggled(self, state: int) -> None:
        if state == Qt.Checked:
            self.preview_transmit_waveform(log_update=False)

    def preview_transmit_waveform(self, log_update: bool = True) -> None:
        valid, message = self._validate_transmit_inputs()
        if not valid:
            if log_update:
                self.bridge.tx_log.emit(f"Validation failed: {message}")
                self._show_error("Invalid excitation settings", message)
            return
        try:
            import numpy as np

            shape = "SIN"
            window_fn = self.tx_windowing_combo.currentText()
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())
            frequency_khz = frequency / 1000.0
            prf_khz = prf_hz / 1000.0

            pulse_width_sec = cycles / frequency
            period_sec = 1.0 / prf_hz
            total_duration_sec = max(period_sec, pulses * period_sec)

            # Render the entire burst train so pulses and PRF are visible in preview.
            n_points = min(24000, max(3000, pulses * 700))
            t_sec = np.linspace(0.0, total_duration_sec, n_points, endpoint=False)
            local_t = np.mod(t_sec, period_sec)
            in_pulse = local_t < pulse_width_sec

            phase = 2.0 * np.pi * frequency * local_t
            if shape == "SIN":
                carrier = np.sin(phase)
            elif shape in {"SQU", "SQUARE"}:
                carrier = np.sign(np.sin(phase))
            elif shape in {"RAMP", "SAW"}:
                carrier = 2.0 * (
                    (frequency * local_t) - np.floor(0.5 + frequency * local_t)
                )
            elif shape in {"TRI", "TRIANGLE"}:
                saw = 2.0 * (
                    (frequency * local_t) - np.floor(0.5 + frequency * local_t)
                )
                carrier = 2.0 * np.abs(saw) - 1.0
            else:
                carrier = np.sin(phase)

            window_lut = self._window_array(window_fn, 2048)
            norm = np.clip(local_t / pulse_width_sec, 0.0, 0.999999)
            lut_idx = (norm * len(window_lut)).astype(int)
            window = window_lut[lut_idx]

            y = amplitude * carrier * window * in_pulse.astype(float)
            t_us = t_sec * 1e6

            self.tx_preview_canvas.figure.clear()
            self.tx_preview_canvas.axes = self.tx_preview_canvas.figure.add_subplot(111)
            self.tx_preview_canvas._style_axes()
            self.tx_preview_canvas.axes.plot(t_us, y, color="#2f80ed", linewidth=1.4)
            self.tx_preview_canvas.axes.set_title("Excitation Preview", color="#1f2a37")
            self.tx_preview_canvas.axes.set_xlabel(r"Time ($\mu$s)", color="#415368")
            self.tx_preview_canvas.axes.set_ylabel("Amplitude (V)", color="#415368")
            self.tx_preview_canvas.draw_idle()
            if log_update:
                self.bridge.tx_log.emit(
                    f"Preview updated: shape={shape}, window={window_fn}, frequency={frequency_khz} kHz, cycles/pulse={cycles}, pulses={pulses}, PRF={prf_khz} kHz"
                )
        except Exception as exc:
            self.tx_preview_canvas.draw_placeholder(f"Preview failed: {exc}")
            if log_update:
                self.bridge.tx_log.emit(f"Preview failed: {exc}")

    def export_transmit_waveform(self) -> None:
        """Export the currently previewed waveform as a comma-delimited text file."""
        valid, message = self._validate_transmit_inputs()
        if not valid:
            self._show_error("Invalid excitation settings", message)
            return
        try:
            import numpy as np

            window_fn = self.tx_windowing_combo.currentText()
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())

            pulse_width_sec = cycles / frequency
            period_sec = 1.0 / prf_hz
            total_duration_sec = max(period_sec, pulses * period_sec)
            n_points = min(24000, max(3000, pulses * 700))
            t_sec = np.linspace(0.0, total_duration_sec, n_points, endpoint=False)
            local_t = np.mod(t_sec, period_sec)
            in_pulse = local_t < pulse_width_sec
            carrier = np.sin(2.0 * np.pi * frequency * local_t)
            window_lut = self._window_array(window_fn, 2048)
            norm = np.clip(local_t / pulse_width_sec, 0.0, 0.999999)
            lut_idx = (norm * len(window_lut)).astype(int)
            window = window_lut[lut_idx]
            y = amplitude * carrier * window * in_pulse.astype(float)
            t_us = t_sec * 1e6
        except Exception as exc:
            self._show_error("Export failed", str(exc))
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Waveform",
            "waveform.txt",
            "Text files (*.txt);;CSV files (*.csv);;All files (*.*)",
        )
        if not filename:
            return
        try:
            with open(filename, "w") as f:
                f.write(f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("Time_us,Amplitude_V\n")
                for t, v in zip(t_us, y):
                    f.write(f"{t:.6f},{v:.6f}\n")
            self.bridge.tx_log.emit(f"Waveform exported to: {filename}")
        except OSError as exc:
            self._show_error("Export failed", str(exc))

    def move_rig_now(self) -> None:
        x_mm = self.move_x.value()
        y_mm = self.move_y.value()
        z_mm = self.move_z.value()
        self.bridge.move_log.emit(
            f"Requested relative move: ΔX={x_mm} mm, ΔY={y_mm} mm, ΔZ={z_mm} mm"
        )
        threading.Thread(
            target=self._move_worker, args=(x_mm, y_mm, z_mm), daemon=True
        ).start()

    def _move_worker(self, x_mm, y_mm, z_mm) -> None:
        try:
            import rig_function

            move_path = BASE_DIR / "move.py"
            spec = importlib.util.spec_from_file_location("move", move_path)
            move_mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(move_mod)
            conv = getattr(
                move_mod, "mm_to_pulse", lambda value: int(value * MM_TO_PULSE)
            )
            x_p, y_p, z_p = conv(x_mm), conv(y_mm), conv(z_mm)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host_edit.text().strip(), self._port_value()))
                self.bridge.move_log.emit("Connected to rig.")
                rig_function.move_to_position(sock, x=x_p, y=y_p, z=z_p)
                self.bridge.move_log.emit("Move complete.")
        except Exception as exc:
            self.bridge.move_log.emit(f"Move failed: {exc}")

    def test_connections(self) -> None:
        self.cfg_output.clear()
        self.bridge.test_busy.emit(True)
        threading.Thread(target=self._test_connections_worker, daemon=True).start()

    def _test_connections_worker(self) -> None:
        sg_addr = self.sg_address_edit.text().strip()
        retries = self.test_retries.value()
        timeout = float(self.test_timeout.value())
        if not sg_addr:
            self.bridge.cfg_log.emit("Signal generator: No address configured")
        else:
            try:
                import importlib

                pm = importlib.import_module("pymeasure.instruments.agilent")
                driver = getattr(pm, "Agilent33500", None)
                if driver is None:
                    raise ImportError("Agilent33500 not available")
            except Exception as exc:
                self.bridge.cfg_log.emit(
                    f"Signal generator: driver unavailable ({exc})"
                )
                driver = None
            if driver is not None:
                last_err = None
                for _ in range(retries):
                    try:
                        sg = driver(sg_addr)
                        self.bridge.cfg_log.emit(
                            f"Signal generator: Connected at {sg_addr}"
                        )
                        try:
                            sg.shutdown()
                        except Exception:
                            pass
                        last_err = None
                        break
                    except Exception as exc:
                        last_err = exc
                        time.sleep(min(0.3, timeout))
                if last_err is not None:
                    self.bridge.cfg_log.emit(
                        f"Signal generator: Failed to open {sg_addr}: {last_err}"
                    )
        try:
            import Oscilloscope  # noqa: F401

            self.bridge.cfg_log.emit("Oscilloscope utilities: available")
        except Exception as exc:
            self.bridge.cfg_log.emit(f"Oscilloscope utilities: not available ({exc})")
        try:
            with socket.create_connection(
                (self.host_edit.text().strip(), self._port_value()),
                timeout=timeout,
            ):
                self.bridge.cfg_log.emit("Rig socket: reachable")
        except Exception as exc:
            self.bridge.cfg_log.emit(f"Rig socket: connection failed ({exc})")
        self.bridge.test_busy.emit(False)

    def start_transmit_mode(self) -> None:
        valid, message = self._validate_transmit_inputs()
        if not valid:
            self.bridge.tx_log.emit(f"Validation failed: {message}")
            self._show_error("Invalid excitation settings", message)
            return
        self.transmit_preview_button.setEnabled(False)
        self.transmit_start_button.setEnabled(False)
        self.transmit_timing_button.setEnabled(False)
        threading.Thread(target=self._run_transmit_worker, daemon=True).start()

    def _run_transmit_worker(self) -> None:
        """Transmit-only path: trigger SG burst(s), no oscilloscope read."""
        try:
            import importlib

            pm = importlib.import_module("pymeasure.instruments.agilent")
            Agilent33500 = getattr(pm, "Agilent33500", None)
            if Agilent33500 is None:
                raise ImportError("Agilent33500 not available")

            from Signal_function import Burst_generate

            sg_address = self.sg_address_edit.text().strip() or None
            sg = Agilent33500(sg_address) if sg_address else Agilent33500()
            self.bridge.tx_log.emit("Connected to signal generator.")

            shape = "SIN"
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())
            window_fn = self.tx_windowing_combo.currentText()
            frequency_khz = frequency / 1000.0
            prf_khz = prf_hz / 1000.0
            live_preview = bool(self.tx_auto_preview_check.isChecked())

            self.bridge.tx_log.emit(
                f"Excitation settings: shape={shape}, window={window_fn}, "
                f"frequency={frequency_khz} kHz, amplitude={amplitude} V, "
                f"cycles/pulse={cycles}, pulses={pulses}, PRF={prf_khz} kHz, "
                f"live_preview={live_preview}"
            )
            Burst_generate(
                sg,
                shape=shape,
                frequency=frequency,
                amplitude=amplitude,
                no_of_cycles_per_pulse=cycles,
                no_of_pulses=pulses,
                prf=prf_hz,
                window_type=window_fn,
            )

            self.bridge.tx_log.emit("Excitation completed (signal generator only).")
            try:
                sg.shutdown()
            except Exception:
                pass
        except Exception as exc:
            self.bridge.tx_log.emit(f"Transmit failed: {exc}")
        finally:
            self.transmit_preview_button.setEnabled(True)
            self.transmit_start_button.setEnabled(True)
            self.transmit_timing_button.setEnabled(True)

    def start_transmit_timing_test(self) -> None:
        valid, message = self._validate_transmit_inputs()
        if not valid:
            self.bridge.tx_log.emit(f"Validation failed: {message}")
            self._show_error("Invalid excitation settings", message)
            return
        self.transmit_preview_button.setEnabled(False)
        self.transmit_start_button.setEnabled(False)
        self.transmit_timing_button.setEnabled(False)
        threading.Thread(
            target=self._run_transmit_timing_test_worker, daemon=True
        ).start()

    def _run_transmit_timing_test_worker(self) -> None:
        sg = None
        try:
            test_script = BASE_DIR / "test_prf_timing.py"
            spec = importlib.util.spec_from_file_location(
                "test_prf_timing", test_script
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load test_prf_timing.py")
            module = importlib.util.module_from_spec(spec)
            # Ensure dataclass and other decorators can resolve module globals.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            shape = "SIN"
            window_fn = self.tx_windowing_combo.currentText()
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())
            tolerance_percent = 10.0
            tolerance_ratio = tolerance_percent / 100.0
            frequency_khz = frequency / 1000.0
            prf_khz = prf_hz / 1000.0
            live_preview = bool(self.tx_auto_preview_check.isChecked())

            sg_address = self.sg_address_edit.text().strip() or None
            sg = module.connect_signal_generator(sg_address)
            self.bridge.tx_log.emit("Timing test: connected to signal generator.")
            self.bridge.tx_log.emit(
                f"Timing test settings: shape={shape}, window={window_fn}, frequency={frequency_khz} kHz, "
                f"cycles/pulse={cycles}, pulses={pulses}, PRF={prf_khz} kHz, tolerance={tolerance_percent}%, "
                f"live_preview={live_preview}"
            )

            recorder = module.TriggerRecorder(sg)
            module.Burst_generate(
                recorder,
                shape=shape,
                frequency=frequency,
                amplitude=amplitude,
                no_of_cycles_per_pulse=cycles,
                no_of_pulses=pulses,
                prf=prf_hz,
                window_type=window_fn,
            )

            result = module.evaluate_timing(recorder.trigger_times, pulses, prf_hz)
            tolerance_s = result.expected_period_s * tolerance_ratio

            self.bridge.tx_log.emit(
                f"Timing results: expected_pulses={result.expected_pulses}, measured_pulses={result.pulse_count}"
            )
            self.bridge.tx_log.emit(
                f"Period(s): expected={result.expected_period_s:.9f}, mean={result.mean_period_s:.9f}, "
                f"min={result.min_period_s:.9f}, max={result.max_period_s:.9f}"
            )
            self.bridge.tx_log.emit(
                f"Error: max_abs={result.max_abs_error_s:.9f} s, allowed={tolerance_s:.9f} s"
            )

            pass_count = result.pulse_count == result.expected_pulses
            pass_spacing = result.max_abs_error_s <= tolerance_s
            if pass_count and pass_spacing:
                self.bridge.tx_log.emit(
                    "PASS: Pulse count and PRF spacing are within tolerance."
                )
            else:
                if not pass_count:
                    self.bridge.tx_log.emit("FAIL: Pulse count mismatch.")
                if not pass_spacing:
                    self.bridge.tx_log.emit("FAIL: PRF spacing is outside tolerance.")
        except Exception as exc:
            self.bridge.tx_log.emit(f"Timing test failed: {exc}")
        finally:
            if sg is not None:
                try:
                    sg.shutdown()
                except Exception:
                    pass
            self.transmit_preview_button.setEnabled(True)
            self.transmit_start_button.setEnabled(True)
            self.transmit_timing_button.setEnabled(True)

    def start_a_mode(self) -> None:
        self._save_settings_now()
        self._a_mode_last_results = None
        self.a_start_button.setEnabled(False)
        threading.Thread(target=self._run_a_mode_worker, daemon=True).start()

    def _run_a_mode_worker(self) -> None:
        script_path = BASE_DIR / "A scan.py"
        sg = None
        oscmod = None
        osc = None
        motion_sock = None
        try:
            import numpy as np
            import importlib

            spec = importlib.util.spec_from_file_location("a_scan", script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            use_dummy = bool(getattr(module, "USE_DUMMY_SIGNAL_GENERATOR", False))
            if use_dummy:
                self.bridge.a_mode_log.emit(
                    "A-mode test mode: using dummy signal generator echoes."
                )
                Burst_generate = None
            else:
                pm = importlib.import_module("pymeasure.instruments.agilent")
                Agilent33500 = getattr(pm, "Agilent33500", None)
                if Agilent33500 is None:
                    raise ImportError("Agilent33500 not available")
                from Signal_function import Burst_generate
                import Oscilloscope as oscmod

                sg_address = self.sg_address_edit.text().strip() or None
                sg = Agilent33500(sg_address) if sg_address else Agilent33500()
                osc = oscmod.open_oscilloscope(self.osc_address_edit.text().strip() or None)

            params = {
                "X": float(self.a_mode_x.value()),
                "Y": float(self.a_mode_y.value()),
                "Z": float(self.a_mode_z.value()),
                "mode": "INC",
            }
            pulses = int(self.tx_pulses.value())
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            prf_hz = float(self.tx_prf.value())
            window_fn = self.tx_windowing_combo.currentText()
            sampling_rate = self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(frequency * 100.0, 1e6) / 1000.0,
            ) * 1000.0
            live_enabled = bool(self.a_live_preview_check.isChecked())
            cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            cutoff_khz = cutoff_hz / 1000.0
            filter_order = int(self.a_mode_filter_order.value())
            frequency_khz = frequency / 1000.0
            prf_khz = prf_hz / 1000.0
            sampling_rate_khz = sampling_rate / 1000.0

            self.bridge.a_mode_log.emit(
                f"A-mode settings: mode={params['mode']}, X={params['X']} mm, Y={params['Y']} mm, Z={params['Z']} mm, "
                f"frequency={frequency_khz} kHz, amplitude={amplitude} V, cycles/pulse={cycles}, pulses={pulses}, "
                f"PRF={prf_khz} kHz, window={window_fn}, sampling_rate={sampling_rate_khz} kHz, "
                f"highpass_cutoff={cutoff_khz} kHz, filter_order={filter_order}, live_preview={live_enabled}, "
                f"signal_source={'dummy' if use_dummy else 'hardware'}"
            )

            # Move rig once per A-mode run in hardware mode.
            if not use_dummy:
                x_p = module.mm_to_pulse(params["X"])
                y_p = module.mm_to_pulse(params["Y"])
                z_p = module.mm_to_pulse(params["Z"])
                motion_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                motion_sock.connect((self.host_edit.text().strip(), self._port_value()))
                module.move(motion_sock, x=x_p, y=y_p, z=z_p)

            traces = []
            self.bridge.a_mode_log.emit(f"A-mode running for {pulses} pulse(s).")
            for pulse_idx in range(1, pulses + 1):
                if use_dummy:
                    t, y = module.generate_test_echo(
                        frequency_hz=frequency,
                        amplitude_v=amplitude,
                        no_of_cycles_per_pulse=cycles,
                        window_type=window_fn,
                        sampling_rate_hz=sampling_rate,
                    )
                else:
                    Burst_generate(
                        sg,
                        shape="SIN",
                        frequency=frequency,
                        amplitude=amplitude,
                        no_of_cycles_per_pulse=cycles,
                        no_of_pulses=1,
                        prf=prf_hz,
                        window_type=window_fn,
                    )
                    t, y = self._capture_a_mode_waveform(
                        osc, frequency, amplitude, cycles
                    )
                traces.append((t, y))
                self.bridge.a_mode_log.emit(f"Captured A-mode echo {pulse_idx}/{pulses}")

                if live_enabled:
                    self.bridge.a_preview.emit(
                        {
                            "mode": "live",
                            "t": t,
                            "y": y,
                            "pulse_idx": pulse_idx,
                            "pulse_total": pulses,
                        }
                    )

            if not traces:
                raise RuntimeError("No A-mode echoes captured.")

            min_len = min(len(item[1]) for item in traces)
            t_ref = traces[0][0][:min_len]
            stack = np.vstack([item[1][:min_len] for item in traces])
            avg = np.mean(stack, axis=0)
            first = traces[0][1][:min_len]
            last = traces[-1][1][:min_len]
            a_mode_signal = module.estimate_a_mode_signal(
                t_ref,
                avg,
                highpass_cutoff_hz=cutoff_hz,
                filter_order=filter_order,
                sampling_rate_hz=sampling_rate,
            )
            self._a_mode_last_results = {
                "t": t_ref,
                "traces": stack,
                "avg": avg,
                "a_mode": a_mode_signal,
                "live_enabled": live_enabled,
            }
            self.bridge.a_preview.emit(
                {
                    "mode": "final",
                    "t": t_ref,
                    "y_first": first,
                    "y": last,
                    "y_avg": avg,
                    "y_mode": a_mode_signal,
                    "pulse_total": pulses,
                    "live_enabled": live_enabled,
                }
            )

            self.bridge.a_mode_log.emit("A-mode scan finished.")
        except Exception as exc:
            self.bridge.a_mode_log.emit(f"A-mode scan failed: {exc}")
        finally:
            if motion_sock is not None:
                try:
                    motion_sock.close()
                except Exception:
                    pass
            if sg is not None:
                try:
                    sg.shutdown()
                except Exception:
                    pass
            if oscmod is not None:
                try:
                    oscmod.close_oscilloscope()
                except Exception:
                    pass
            self.a_start_button.setEnabled(True)

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.bridge.bc_log.emit("Stop requested. Current motion will finish first.")

    def start_scan(self) -> None:
        try:
            pg, scan = self.validate_scan_inputs()
        except Exception as exc:
            self._show_error("Invalid input", str(exc))
            return
        self.stop_event.clear()
        self.bridge.scan_busy.emit(True)
        self.bc_output.clear()
        threading.Thread(target=self._scan_worker, args=(pg, scan), daemon=True).start()

    def _scan_worker(self, pg: dict, scan: dict) -> None:
        oscmod = None
        rig_function = None
        Agilent33500 = None
        sg = None
        sock = None
        osc = None
        dry_run = self.dry_run_check.isChecked()

        try:
            if not dry_run:
                import importlib

                pm = importlib.import_module("pymeasure.instruments.agilent")
                Agilent33500 = getattr(pm, "Agilent33500", None)
                import Oscilloscope as oscmod
                import rig_function

                if Agilent33500 is None:
                    raise ImportError("Agilent33500 not available")
                sg_address = pg.get("sg_address")
                sg = Agilent33500(sg_address) if sg_address else Agilent33500()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((scan["host"], self._port_value()))
                osc = oscmod.open_oscilloscope(self.osc_address_edit.text().strip() or None)
                scan_folder = oscmod.create_scan_folder()
                self.bridge.bc_log.emit(f"Scan folder: {scan_folder}")
                self.bridge.bc_log.emit("Hardware connected.")
            else:
                import Oscilloscope as oscmod
                from dummy_signal_generator import generate_dummy_echo
                import numpy as np
                scan_folder = oscmod.create_scan_folder()
                self.bridge.bc_log.emit(f"[Dry-run] Scan folder: {scan_folder}")
                self.bridge.bc_log.emit("[Dry-run] Using dummy signal generator for acquisition.")

            if rig_function and sock:
                rig_function.send_command(sock, "INC")
                rig_function.enable_axis(sock, scan["scan_axis"])
                rig_function.enable_axis(sock, scan["cross_axis"])

            scan_steps = scan["scan_points"]
            cross_steps = scan["cross_points"]
            scan_step = scan["scan_step"]
            cross_step = scan["cross_step"]
            start_col = 2  # hardcoded: pre-sample is always saved as col 1
            self.bridge.bc_log.emit(
                f"B-Mode settings: dry_run={dry_run}, live_preview={self.live_update_check.isChecked()}, "
                f"shape={pg['shape']}, frequency={pg['frequency']} Hz, amplitude={pg['amplitude']} V, "
                f"cycles/pulse={pg['no_of_cycles_per_pulse']}, pulses={pg['no_of_pulses']}, "
                f"scan_axis={scan['scan_axis']}, cross_axis={scan['cross_axis']}, depth_axis={scan['depth_axis']}, "
                f"scan_points={scan_steps}, cross_points={cross_steps}, "
                f"scan_step={scan_step}, cross_step={cross_step}"
            )
            self.bridge.bc_log.emit(
                f"Starting B Scan: {scan['scan_axis']} {scan_steps} steps ({scan_step} pulses) | "
                f"{scan['cross_axis']} {cross_steps} rows ({cross_step} pulses)"
            )

            for cross in range(cross_steps):
                if self.stop_event.is_set():
                    self.bridge.bc_log.emit("Scan stopped by user.")
                    break
                self.bridge.bc_log.emit(f"Scanning row {cross + 1}/{cross_steps}")
                scan_direction = -scan_step if cross % 2 else scan_step
                for scan_idx in range(scan_steps):
                    if self.stop_event.is_set():
                        break
                    column = (
                        (scan_steps - scan_idx) if cross % 2 else (scan_idx + start_col)
                    )
                    if rig_function and sock:
                        rig_function.send_command(
                            sock, f"{scan['scan_axis']}{scan_direction}"
                        )
                        rig_function.wait_until_stopped(sock, scan["scan_axis"])
                    else:
                        self.bridge.bc_log.emit(
                            f"[Dry-run] Move {scan['scan_axis']} {scan_direction}"
                        )
                    if oscmod and osc and sg:
                        oscmod.send_burst(osc, sg, cross, column, scan_folder)
                        csv_path = os.path.join(
                            scan_folder, f"row_{cross + 1}_col_{column}.csv"
                        )
                        self.bridge.bc_plot_csv.emit(csv_path)
                        self.bridge.bc_log.emit(
                            f"Acquired row {cross + 1}, col {column}"
                        )
                    else:
                        t, echo = generate_dummy_echo(
                            frequency_hz=float(pg["frequency"]),
                            amplitude_v=float(pg["amplitude"]),
                            no_of_cycles_per_pulse=float(pg["no_of_cycles_per_pulse"]),
                        )
                        csv_path = os.path.join(
                            scan_folder, f"row_{cross + 1}_col_{column}.csv"
                        )
                        with open(csv_path, "w", encoding="utf-8") as _f:
                            _f.write("Time (s),Amplitude (V)\n")
                            for _t, _v in zip(t, echo):
                                _f.write(f"{_t:.10e},{_v:.10e}\n")
                        self.bridge.bc_plot_csv.emit(csv_path)
                        self.bridge.bc_log.emit(
                            f"[Dry-run] Generated dummy echo: row {cross + 1}, col {column}"
                        )
                if cross < cross_steps - 1 and not self.stop_event.is_set():
                    if rig_function and sock:
                        rig_function.send_command(
                            sock, f"{scan['cross_axis']}{cross_step}"
                        )
                        rig_function.wait_until_stopped(sock, scan["cross_axis"])
                    else:
                        self.bridge.bc_log.emit(
                            f"[Dry-run] Move {scan['cross_axis']} {cross_step}"
                        )
            self.bridge.bc_log.emit("B Scan finished.")
        except Exception as exc:
            self.bridge.bc_log.emit(f"Error during scan: {exc}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if sg is not None:
                try:
                    sg.shutdown()
                except Exception:
                    pass
            if oscmod is not None:
                try:
                    oscmod.close_oscilloscope()
                except Exception:
                    pass
            self.bridge.scan_busy.emit(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.stop_button.isEnabled():
            result = QMessageBox.question(
                self,
                "Quit",
                "A scan is running. Quit anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                event.ignore()
                return
            self.stop_event.set()
        event.accept()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = ScannerMainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
