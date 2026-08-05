"""PySide6 implementation of the A/B scanner GUI.

This module replaces the old T& "C:\Miniforge3\Scripts\conda.exe" init powershellkinter surface with a Qt-based desktop UI while
reusing the existing scan, motion, and acquisition backends from this project.
"""

from __future__ import annotations

import contextlib
import csv
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
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from scan_utils import compute_step

MM_TO_PULSE = 5000
MODE_LEFT_PANEL_WIDTH = 430
B_MODE_LEFT_PANEL_WIDTH = 540
PANEL_GAP = 14
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = DATA_DIR / "gui_settings.json"


class PlotCanvas(FigureCanvasQTAgg):
    def __init__(self, title: str) -> None:
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self._title = title
        self.setMinimumHeight(260)
        # Reduce figure margins to prevent overlay on left panel when squeezed
        self.figure.subplots_adjust(left=0.08, right=0.95, top=0.95, bottom=0.12)
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
    saved_rig_position_ready = Signal(object)
    tx_log = Signal(str)
    a_mode_log = Signal(str)
    b_mode_log = Signal(str)
    bc_log = Signal(str)
    a_preview = Signal(object)
    b_preview = Signal(object)
    bc_preview = Signal(object)
    bc_pf_auto_apply = Signal()
    bc_plot_csv = Signal(str)
    test_busy = Signal(bool)
    scan_busy = Signal(bool)
    error = Signal(str, str)


class ScannerMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.stop_event = threading.Event()
        self._sg_state_lock = threading.Lock()
        self._sg_use_lock = threading.Lock()
        self._osc_state_lock = threading.Lock()
        self._stable_sg = None
        self._stable_sg_address = None
        self._stable_osc = None
        self._stable_osc_address = None
        self._scope_capture_config_signature = None
        self._scope_capture_config_handle_id = None
        self.bridge = UiBridge()
        self._a_mode_last_results = None
        self._b_mode_last_results = None
        self._b_mode_image_artist = None
        self._b_mode_colorbar = None
        self._b_mode_show_normalized = False
        self._bc_colorbar = None
        self._bc_pressure_field_payload = None
        self._bc_pressure_field_cache = None
        self._bc_pressure_field_source = None
        self._bc_c_mode_cache = None
        self._bc_c_mode_source = None
        self._bc_c_mode_scanned = False
        self._bc_apply_unlocked = False
        self._last_bc_csv_path = None
        self._saved_rig_position: tuple[int, int, int] | None = None
        self._session_move_delta: tuple[int, int, int] = (0, 0, 0)
        self._saved_position_lock = threading.Lock()
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
        self.bridge.saved_rig_position_ready.connect(self._apply_saved_rig_position)
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
        self.bridge.bc_log.connect(
            lambda text: self._append_log(self.bc_output, text)
        )
        self.bridge.bc_preview.connect(self._render_bc_live_preview)
        self.bridge.bc_pf_auto_apply.connect(self._apply_pressure_field_postprocessing_from_saved_data)
        self.bridge.bc_plot_csv.connect(self._on_bc_plot_csv)
        self.bridge.test_busy.connect(self._set_test_busy)
        self.bridge.scan_busy.connect(self._set_scan_busy)
        self.bridge.error.connect(self._show_error)

    def _build_ui(self) -> None:
        self.setWindowTitle("A/B/C/Field Scanner")
        # Remove About menu, add File menu with Documentation, About, Exit
        file_menu = self.menuBar().addMenu("File")


        # Style File menu dropdown: light purple background, larger font
        modern_menu_style = (
            "QMenu {"
            "  background-color: #f3eaff;"
            "  border-radius: 10px;"
            "  padding: 8px 0px;"
            "  font-size: 15pt;"
            "  min-width: 220px;"
            "  border: 1.5px solid #bba6e6;"
            "}"
            "QMenu::item {"
            "  padding: 10px 28px 10px 24px;"
            "  border-radius: 7px;"
            "  font-size: 15pt;"
            "  color: #2d1b69;"
            "}"
            "QMenu::item:selected {"
            "  background-color: #d1b3f7;"
            "  color: #1a0d3a;"
            "}"
        )
        file_menu.setStyleSheet(modern_menu_style)

        # Documentation submenu
        from PySide6.QtWidgets import QMenu
        doc_menu = QMenu("Documentation", self)
        doc_menu.setStyleSheet(modern_menu_style)
        file_menu.addMenu(doc_menu)

        show_doc_action = QAction("Show Documentation", self)
        show_doc_action.triggered.connect(self.show_documentation)
        doc_menu.addAction(show_doc_action)

        update_doc_action = QAction("Update Documentation", self)
        update_doc_action.triggered.connect(self.update_documentation)
        doc_menu.addAction(update_doc_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        file_menu.addAction(about_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

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
        controls_panel = QWidget()
        controls_panel_layout = QVBoxLayout(controls_panel)
        controls_panel_layout.setContentsMargins(0, 0, 0, 0)
        controls_panel_layout.setSpacing(14)
        top = QGridLayout()
        top.setHorizontalSpacing(14)
        top.setVerticalSpacing(14)
        controls_panel_layout.addLayout(top)

        sg_box = QGroupBox("Signal Generator")
        sg_form = QFormLayout(sg_box)
        self._sg_model_to_visa = {
            "Agilent33220A": "USB0::2391::1031::MY44055132::0::INSTR",
            "Agilent33500B": "USB0::2391::11015::MY52701391::0::INSTR",
            "Agilent33521A": "USB0::2391::5639::MY50004553::0::INSTR",
        }
        self.sg_name_edit = QComboBox()
        self.sg_name_edit.addItems(list(self._sg_model_to_visa.keys()))
        self.sg_name_edit.setCurrentText("Agilent33500B")
        self.sg_name_edit.currentTextChanged.connect(self._on_sg_model_changed)
        self._default_sg_address = self._sg_model_to_visa["Agilent33500B"]
        self.sg_address_edit = QLineEdit()
        self.sg_address_edit.setPlaceholderText("USB0::...::INSTR")
        sg_form.addRow("Name", self.sg_name_edit)
        sg_form.addRow("VISA Address", self.sg_address_edit)
        self._on_sg_model_changed(self.sg_name_edit.currentText())
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

        connection_box = QGroupBox("Connection Test")
        controls = QHBoxLayout()
        self._normalize_control_row(controls)
        self.test_button = QPushButton("Connect to Hardware")
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
        connection_box.setLayout(controls)
        top.addWidget(connection_box, 2, 0, 1, 2)

        log_box = QGroupBox("Config Log")
        log_layout = QVBoxLayout(log_box)
        self.cfg_output = self._make_log()
        log_layout.addWidget(self.cfg_output)
        self.cfg_export_logs_button = QPushButton("Export Logs")
        self.cfg_export_logs_button.clicked.connect(self.export_config_logs)
        self.cfg_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.cfg_export_logs_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        log_layout.addWidget(self.cfg_export_logs_button)

        left_panel = QWidget()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(10)
        left_panel_layout.addWidget(controls_panel, 1)
        left_panel_layout.addWidget(log_box, 2)

        layout.addWidget(self._make_vscroll_panel(left_panel))
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

        move_aux_button_style = (
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )

        self.save_position_button = QPushButton("Save Current Position")
        self.save_position_button.clicked.connect(self.save_current_position)
        self.save_position_button.setStyleSheet(move_aux_button_style)
        self.save_position_button.setFixedHeight(34)
        self.save_position_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )

        self.return_position_button = QPushButton("Return to Saved Position")
        self.return_position_button.clicked.connect(self.return_to_saved_position)
        self.return_position_button.setStyleSheet(move_aux_button_style)
        self.return_position_button.setFixedHeight(34)
        self.return_position_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.return_position_button.setEnabled(False)
        left_layout.addStretch(1)
        left_layout.addWidget(self.save_position_button)
        left_layout.addWidget(self.return_position_button)

        log_box = QGroupBox("Move Log")
        log_layout = QVBoxLayout(log_box)
        self.move_output = self._make_log()
        log_layout.addWidget(self.move_output)

        layout.addWidget(self._make_vscroll_panel(left_col), 1)
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

        # Two-column layout: left side holds parameters + log, right side holds preview.
        content_row = QHBoxLayout()
        content_row.setSpacing(0)

        left_side = QWidget()
        left_side_layout = QVBoxLayout(left_side)
        left_side_layout.setContentsMargins(0, 0, 0, 0)
        left_side_layout.setSpacing(10)

        params_col = QWidget()
        left_layout = QVBoxLayout(params_col)
        left_layout.setContentsMargins(0, 0, 0, 0)

        pg_box = QGroupBox("Pulse Generator")
        pg_form = QFormLayout(pg_box)
        self.tx_windowing_combo = QComboBox()
        self.tx_windowing_combo.addItems(
            ["Hanning", "Hamming", "Blackman-Harris", "Flat-Top", "None (Rectangular)"]
        )
        self.tx_windowing_combo.setStyleSheet("QComboBox { padding-right: 28px; }")
        self.tx_freq = self._make_double_spin(0.001, 100_000.0, 1000.0, decimals=3)
        self.tx_amp = self._make_double_spin(0.01, 100, 1.0)
        self.tx_cycles = self._make_spin(1, 10_000, 60)
        self.tx_pulses = self._make_spin(1, 100000, 1)
        self.tx_prf = self._make_double_spin(0.1, 1_000_000, 1000.0, decimals=2)
        self.tx_start_delay_us = self._make_double_spin(0.0, 10_000_000.0, 0.0)
        self.tx_start_delay_us.setEnabled(False)
        self.tx_start_delay_us.setToolTip(
            "Start Delay is currently disabled and is not used by Excitation signal generation."
        )
        pg_form.addRow("Waveform Shape", QLabel("SIN"))
        pg_form.addRow("Windowing Function", self.tx_windowing_combo)
        pg_form.addRow("Frequency (kHz)", self.tx_freq)
        pg_form.addRow("Amplitude (Vpp)", self.tx_amp)
        pg_form.addRow("No. Of Cycles Per Pulse", self.tx_cycles)
        pg_form.addRow("No. Of Pulses", self.tx_pulses)
        pg_form.addRow("Pulse Repetition Frequency (Hz)", self.tx_prf)
        pg_form.addRow("Start Delay (\u03bcs)", self.tx_start_delay_us)
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

        log_box = QGroupBox("Excitation Log")
        log_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_box.setMinimumHeight(0)
        log_layout = QVBoxLayout(log_box)
        self.transmit_output = self._make_log()
        self.transmit_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.transmit_output, 1)
        self.transmit_export_logs_button = QPushButton("Export Logs")
        self.transmit_export_logs_button.clicked.connect(self.export_transmit_logs)
        self.transmit_export_logs_button.setStyleSheet(
            "QPushButton { min-height: 34px; padding: 6px 12px; "
            "background-color: #c9b1f7; color: #2d1b69; border: none; border-radius: 14px; font-weight: 600; }"
            "QPushButton:hover { background-color: #b89ef0; }"
            "QPushButton:pressed { background-color: #a98ae9; }"
            "QPushButton:disabled { background-color: #e4d9fb; color: #9b8abf; }"
        )
        self.transmit_export_logs_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        log_layout.addWidget(self.transmit_export_logs_button, 0)

        left_layout.addStretch(1)
        left_side_layout.addWidget(params_col, 0)
        left_side_layout.addWidget(log_box, 1)

        preview_col = QWidget()
        preview_layout = QVBoxLayout(preview_col)
        preview_layout.setContentsMargins(12, 0, 12, 0)
        preview_layout.setSpacing(8)
        self.tx_preview_canvas = PlotCanvas("Preview waveform will appear here")
        self.tx_preview_toolbar = NavigationToolbar2QT(self.tx_preview_canvas, page)
        preview_layout.addWidget(self.tx_preview_toolbar)
        tx_preview_controls = QWidget()
        tx_preview_controls_layout = QHBoxLayout(tx_preview_controls)
        tx_preview_controls_layout.setContentsMargins(0, 0, 0, 0)
        tx_preview_controls_layout.setSpacing(8)
        tx_preview_controls_layout.addStretch(1)
        tx_preview_controls_layout.addWidget(self.transmit_export_button)
        preview_layout.addWidget(tx_preview_controls)
        preview_layout.addWidget(self.tx_preview_canvas, 1)

        left_side.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        preview_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_col.setMinimumWidth(0)
        tx_left_scroll = self._make_vscroll_panel(left_side, B_MODE_LEFT_PANEL_WIDTH)
        content_row.addWidget(tx_left_scroll, 0)
        content_row.addSpacing(PANEL_GAP)
        content_row.addWidget(preview_col, 1)
        layout.addLayout(content_row, 1)

        self.tx_windowing_combo.currentIndexChanged.connect(
            self._on_transmit_preview_inputs_changed
        )
        self.tx_freq.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_amp.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_cycles.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_pulses.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_prf.valueChanged.connect(self._on_transmit_preview_inputs_changed)
        self.tx_start_delay_us.valueChanged.connect(
            self._on_transmit_preview_inputs_changed
        )
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
        self.a_mode_highpass_cutoff = self._make_double_spin(
            0.0, 100_000.0, 50.0, decimals=1
        )
        self.a_mode_filter_order = self._make_spin(1, 12, 4)
        self.a_mode_sound_speed = self._make_double_spin(0.0, 20000.0, 1500.0)
        self.a_mode_sound_speed.setSingleStep(1.0)
        self.a_mode_sound_speed.setDecimals(0)
        pos_form.addRow("ΔX (mm)", self.a_mode_x)
        pos_form.addRow("ΔY (mm)", self.a_mode_y)
        pos_form.addRow("ΔZ (mm)", self.a_mode_z)
        a_speed_label = QLabel(
            'Speed of Sound (m/s)<br><span style="color:#c23b3b; font-size:9pt;">0 = use time in \\mu s</span>'
        )
        pos_form.addRow(a_speed_label, self.a_mode_sound_speed)
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
        self.a_dry_run_check = QCheckBox("Dry Run")
        self.a_dry_run_check.setChecked(True)
        self.a_live_preview_check = QCheckBox("Live Preview")
        self.a_live_preview_check.setChecked(True)
        self.a_source_label = QLabel("Signal source: Dummy")
        self.a_source_label.setStyleSheet("color: #415368; font-size: 9pt;")
        self.a_dry_run_check.stateChanged.connect(self._update_a_mode_source_label)
        self.a_mode_highpass_cutoff.valueChanged.connect(
            self._on_a_mode_filter_params_changed
        )
        self.a_mode_filter_order.valueChanged.connect(
            self._on_a_mode_filter_params_changed
        )
        self.a_mode_sound_speed.valueChanged.connect(
            self._on_a_mode_filter_params_changed
        )
        action_row = QHBoxLayout()
        self._normalize_button_row(action_row, [self.a_start_button])
        action_row.addWidget(self.a_start_button)
        action_row.addWidget(self.a_dry_run_check)
        action_row.addWidget(self.a_live_preview_check)
        action_row.addWidget(self.a_source_label)
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
        self.a_export_logs_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        log_layout.addWidget(self.a_export_logs_button)

        top_row.addWidget(self._make_vscroll_panel(left_col), 1)
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
        layout.setSpacing(0)

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
        self.bc_scan_algorithm_combo = QComboBox()
        self.bc_scan_algorithm_combo.addItems(["Zigzag", "Raster"])
        scan_form.addRow("Scanning Algorithm", self.bc_scan_algorithm_combo)
        self.depth_axis.setEnabled(False)
        self._sync_depth_axis_from_scan_axes()
        self.scan_axis.currentIndexChanged.connect(self._sync_depth_axis_from_scan_axes)
        self.cross_axis.currentIndexChanged.connect(
            self._sync_depth_axis_from_scan_axes
        )
        controls_layout.addWidget(scan_box)

        options_row = QHBoxLayout()
        self._normalize_control_row(options_row)
        self.dry_run_check = QCheckBox("Dry Run")
        self.live_update_check = QCheckBox("Live Preview")
        self.bc_source_label = QLabel("Signal source: Hardware")
        self.bc_source_label.setStyleSheet("color: #415368; font-size: 9pt;")
        self.dry_run_check.stateChanged.connect(self._update_bc_mode_source_label)
        options_row.addWidget(self.live_update_check)
        options_row.addWidget(self.dry_run_check)
        options_row.addWidget(self.bc_source_label)
        controls_layout.addLayout(options_row)

        scan_type_box = QGroupBox("Post-processing and Preview")
        scan_type_layout = QVBoxLayout(scan_type_box)
        scan_type_layout.setContentsMargins(10, 10, 10, 10)
        scan_type_layout.setSpacing(8)

        mode_row = QHBoxLayout()
        self._normalize_control_row(mode_row, spacing=8)
        self.bc_scan_type_group = QButtonGroup(self)
        self.bc_scan_type_group.setExclusive(True)

        self.bc_scan_type_segment = QFrame()
        self.bc_scan_type_segment.setObjectName("bcScanTypeSegment")
        self.bc_scan_type_segment.setFixedHeight(52)
        self.bc_scan_type_segment.setStyleSheet(
            "QFrame#bcScanTypeSegment { background: #f4f7fb; border: 1px solid #d7c8f7; border-radius: 26px; }"
        )
        segment_layout = QHBoxLayout(self.bc_scan_type_segment)
        segment_layout.setContentsMargins(6, 6, 6, 6)
        segment_layout.setSpacing(2)

        self.bc_scan_type_indicator = QFrame(self.bc_scan_type_segment)
        self.bc_scan_type_indicator.setObjectName("bcScanTypeIndicator")
        self.bc_scan_type_indicator.setStyleSheet(
            "QFrame#bcScanTypeIndicator { background: #d9c2ff; border: 1px solid #c9b1f7; border-radius: 15px; }"
        )
        self.bc_scan_type_indicator.lower()
        self.bc_scan_type_indicator.hide()

        self.bc_scan_type_standard_btn = QPushButton("A-Mode")
        self.bc_scan_type_standard_btn.setCheckable(True)
        self.bc_scan_type_standard_btn.setChecked(True)
        self.bc_scan_type_c_btn = QPushButton("C-Mode")
        self.bc_scan_type_c_btn.setCheckable(True)
        self.bc_scan_type_pf_btn = QPushButton("Pressure Field Mode")
        self.bc_scan_type_pf_btn.setCheckable(True)

        for btn in [
            self.bc_scan_type_standard_btn,
            self.bc_scan_type_c_btn,
            self.bc_scan_type_pf_btn,
        ]:
            btn.setFixedHeight(30)
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton { min-height: 0px; max-height: 30px; height: 30px; "
                "padding: 0px 10px; border: none; border-radius: 15px; "
                "background: transparent; color: #415368; font-weight: 600; "
                "font-size: 9pt; qproperty-iconSize: 0px 0px; }"
                "QPushButton:checked { color: #2d1b69; font-weight: 700; }"
            )
            segment_layout.addWidget(
                btn, 1
            )  # equal stretch → equal width for all buttons

        self.bc_scan_type_group.addButton(self.bc_scan_type_standard_btn)
        self.bc_scan_type_group.addButton(self.bc_scan_type_c_btn)
        self.bc_scan_type_group.addButton(self.bc_scan_type_pf_btn)
        self.bc_scan_type_group.buttonClicked.connect(self._on_bc_scan_type_changed)
        mode_row.addWidget(self.bc_scan_type_segment, 1)
        scan_type_layout.addLayout(mode_row)

        # Apply buttons — created early so they can be embedded in their respective pages.
        _apply_style = (
            "QPushButton { min-width: 78px; padding: 0px 10px; text-align: center; "
            "border: none; border-radius: 13px; color: #ffffff; font-weight: 700; "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6a1fb5, stop:1 #9b59d0); }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7d2dc8, stop:1 #ae72df); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #521890, stop:1 #7a3fad); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; }"
        )
        self.bc_post_apply_button = QPushButton("Apply")
        self.bc_post_apply_button.setFixedHeight(28)
        self.bc_post_apply_button.setStyleSheet(_apply_style)
        self.bc_post_apply_button.setEnabled(False)
        self.bc_post_apply_button.clicked.connect(self._on_bc_post_apply_clicked)
        self.bc_apply_cmode_button = QPushButton("Apply")
        self.bc_apply_cmode_button.setFixedHeight(28)
        self.bc_apply_cmode_button.setStyleSheet(_apply_style)
        self.bc_apply_cmode_button.setEnabled(False)
        self.bc_apply_cmode_button.clicked.connect(self._on_bc_post_apply_clicked)

        # Fixed-height stacked options area — keeps panel size constant across modes.
        self.bc_options_stack = QStackedWidget()
        self.bc_options_stack.setFixedHeight(42)

        # Page 0: A-Mode — blank placeholder
        self.bc_options_stack.addWidget(QWidget())

        # Page 1: C-Mode — top controls (metric only)
        c_page = QWidget()
        c_row1 = QHBoxLayout(c_page)
        c_row1.setContentsMargins(12, 0, 0, 0)
        c_row1.setSpacing(8)
        self.bc_c_mode_metric_label = QLabel("Metric")
        self.bc_c_mode_metric_combo = QComboBox()
        self.bc_c_mode_metric_combo.addItems(
            [
                "Max",
                "Mean",
                "RMS",
                "Kurtosis",
                "Energy",
            ]
        )
        self.bc_c_mode_cmap_label = QLabel("Colormap")
        self.bc_c_mode_cmap_combo = QComboBox()
        self.bc_c_mode_cmap_combo.addItems(
            [
                "Gray (16-bit)",
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "turbo",
                "jet",
            ]
        )
        # Removed empty setStyleSheet calls for bc_c_mode_metric_combo and bc_c_mode_cmap_combo
        # Removed incomplete setStyleSheet calls for bc_c_mode_metric_combo and bc_c_mode_cmap_combo
        c_row1.addWidget(self.bc_c_mode_metric_label)
        c_row1.addWidget(self.bc_c_mode_metric_combo)
        c_row1.addSpacing(8)
        c_row1.addWidget(self.bc_c_mode_cmap_label)
        c_row1.addWidget(self.bc_c_mode_cmap_combo)
        c_row1.addStretch(1)
        self.bc_c_mode_metric_combo.currentIndexChanged.connect(
            self._on_bc_c_mode_metric_changed
        )
        self.bc_c_mode_cmap_combo.currentIndexChanged.connect(
            self._on_bc_c_mode_colormap_changed
        )
        self.bc_options_stack.addWidget(c_page)

        # Page 2: Pressure Field Mode — metric selector
        pf_page = QWidget()
        pf_row = QHBoxLayout(pf_page)
        pf_row.setContentsMargins(12, 0, 0, 0)
        pf_row.setSpacing(8)
        self.bc_pf_mode_filtering_label = QLabel("Filter")
        self.bc_pf_mode_extra_combo = QComboBox()
        self.bc_pf_mode_extra_combo.addItems(["No", "Yes"])
        self.bc_pf_mode_metric_label = QLabel("Metric")
        self.bc_pf_mode_metric_combo = QComboBox()
        self.bc_pf_mode_metric_combo.addItems(
            [
                "Max",
                "Min",
                "Mode",
                "Median",
                "Mean",
                "RMS",
                "Variance",
                "Kurtosis",
                "Skewness",
                "Entropy",
                "Energy",
            ]
        )
        self.bc_pf_mode_cmap_label = QLabel("Colormap")
        self.bc_pf_mode_cmap_combo = QComboBox()
        self.bc_pf_mode_cmap_combo.addItems(
            [
                "Gray (16-bit)",
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "turbo",
                "jet",
            ]
        )
        self.bc_c_mode_metric_combo.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
        )
        self.bc_c_mode_cmap_combo.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
        )
        self.bc_pf_mode_metric_combo.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
        )
        self.bc_pf_mode_cmap_combo.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
        )
        self.bc_pf_mode_extra_combo.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
        )
        pf_row.addWidget(self.bc_pf_mode_filtering_label)
        pf_row.addWidget(self.bc_pf_mode_extra_combo)
        pf_row.addSpacing(8)
        pf_row.addWidget(self.bc_pf_mode_metric_label)
        pf_row.addWidget(self.bc_pf_mode_metric_combo)
        pf_row.addSpacing(8)
        pf_row.addWidget(self.bc_pf_mode_cmap_label)
        pf_row.addWidget(self.bc_pf_mode_cmap_combo)
        pf_row.addStretch(1)
        self.bc_pf_mode_metric_combo.currentIndexChanged.connect(
            self._on_bc_pf_metric_changed
        )
        self.bc_pf_mode_cmap_combo.currentIndexChanged.connect(
            self._on_bc_pf_colormap_changed
        )
        self.bc_options_stack.addWidget(pf_page)

        scan_type_layout.addWidget(self.bc_options_stack)

        self.bc_filter_options_stack = QStackedWidget()
        self.bc_filter_options_stack.setFixedHeight(96)
        self.bc_filter_options_stack.addWidget(QWidget())

        # Page 1: C-Mode lower panel — gate controls
        c_filter_page = QWidget()
        c_filter_layout = QVBoxLayout(c_filter_page)
        c_filter_layout.setContentsMargins(12, 2, 0, 2)
        c_filter_layout.setSpacing(6)

        c_filter_row1 = QHBoxLayout()
        c_filter_row1.setContentsMargins(0, 0, 0, 0)
        c_filter_row1.setSpacing(8)
        self.bc_c_mode_gate_start_label = QLabel("Gate Start (μs)")
        self.bc_c_mode_gate_start = self._make_double_spin(0.0, 20000.0, 1000.0)
        self.bc_c_mode_gate_start.setSingleStep(0.1)
        self.bc_c_mode_gate_start.setDecimals(1)
        c_filter_row1.addWidget(self.bc_c_mode_gate_start_label)
        c_filter_row1.addWidget(self.bc_c_mode_gate_start)
        c_filter_row1.addStretch(1)

        c_filter_row2 = QHBoxLayout()
        c_filter_row2.setContentsMargins(0, 0, 0, 0)
        c_filter_row2.setSpacing(8)
        self.bc_c_mode_gate_width_label = QLabel("Gate Width (μs)")
        self.bc_c_mode_gate_width = self._make_double_spin(0.0, 20000.0, 0.1)
        self.bc_c_mode_gate_width.setSingleStep(0.1)
        self.bc_c_mode_gate_width.setDecimals(1)
        c_filter_row2.addWidget(self.bc_c_mode_gate_width_label)
        c_filter_row2.addWidget(self.bc_c_mode_gate_width)
        c_filter_row2.addStretch(1)
        c_filter_row2.addWidget(self.bc_apply_cmode_button)

        c_filter_layout.addLayout(c_filter_row1)
        c_filter_layout.addLayout(c_filter_row2)
        self.bc_filter_options_stack.addWidget(c_filter_page)

        pf_filter_page = QWidget()
        pf_filter_layout = QVBoxLayout(pf_filter_page)
        pf_filter_layout.setContentsMargins(12, 2, 0, 2)
        pf_filter_layout.setSpacing(6)

        pf_filter_top_row = QHBoxLayout()
        pf_filter_top_row.setContentsMargins(0, 0, 0, 0)
        pf_filter_top_row.setSpacing(8)
        self.bc_pf_filter_type_label = QLabel("Type")
        self.bc_pf_filter_type_combo = QComboBox()
        self.bc_pf_filter_type_combo.addItems(["High-pass", "Band-pass"])
        self.bc_pf_filter_order_label = QLabel("Order")
        self.bc_pf_filter_order_spin = QLineEdit("4")
        self.bc_pf_filter_order_spin.setFixedWidth(70)
        self.bc_pf_filter_order_spin.setPlaceholderText("n")

        pf_filter_bottom_row = QHBoxLayout()
        pf_filter_bottom_row.setContentsMargins(0, 0, 0, 0)
        pf_filter_bottom_row.setSpacing(6)
        self.bc_pf_filter_cutoff_label = QLabel("Cutoff (kHz)")
        # high_cutoff_spin: single value for high-pass OR low edge for band-pass
        self.bc_pf_filter_high_cutoff_spin = QLineEdit("50.0")
        self.bc_pf_filter_high_cutoff_spin.setFixedWidth(90)
        self.bc_pf_filter_high_cutoff_spin.setPlaceholderText("kHz")
        self.bc_pf_filter_cutoff_dash = QLabel("–")
        # band_cutoff_spin: high edge for band-pass only
        self.bc_pf_filter_band_cutoff_spin = QLineEdit("500.0")
        self.bc_pf_filter_band_cutoff_spin.setFixedWidth(90)
        self.bc_pf_filter_band_cutoff_spin.setPlaceholderText("kHz")
        # keep low_cutoff_spin as alias so backend references stay valid
        self.bc_pf_filter_low_cutoff_spin = self.bc_pf_filter_high_cutoff_spin
        pf_filter_top_row.addWidget(self.bc_pf_filter_type_label)
        pf_filter_top_row.addWidget(self.bc_pf_filter_type_combo)
        pf_filter_top_row.addSpacing(8)
        pf_filter_top_row.addWidget(self.bc_pf_filter_order_label)
        pf_filter_top_row.addWidget(self.bc_pf_filter_order_spin)
        pf_filter_top_row.addStretch(1)
        pf_filter_bottom_row.addWidget(self.bc_pf_filter_cutoff_label)
        pf_filter_bottom_row.addWidget(self.bc_pf_filter_high_cutoff_spin)
        pf_filter_bottom_row.addWidget(self.bc_pf_filter_cutoff_dash)
        pf_filter_bottom_row.addWidget(self.bc_pf_filter_band_cutoff_spin)
        pf_filter_bottom_row.addStretch(1)
        pf_filter_bottom_row.addWidget(self.bc_post_apply_button)
        self.bc_pf_filter_bottom_widget = QWidget()
        self.bc_pf_filter_bottom_widget.setLayout(pf_filter_bottom_row)
        pf_filter_layout.addLayout(pf_filter_top_row)
        pf_filter_layout.addWidget(self.bc_pf_filter_bottom_widget)
        self.bc_filter_options_stack.addWidget(pf_filter_page)
        self.bc_pf_mode_extra_combo.currentIndexChanged.connect(
            self._update_bc_pf_filter_controls
        )
        self.bc_pf_filter_type_combo.currentIndexChanged.connect(
            self._update_bc_pf_filter_controls
        )

        scan_type_layout.addWidget(self.bc_filter_options_stack)

        self._set_bc_scan_type("standard")
        self._update_bc_pf_filter_controls()
        self._bc_indicator_ready = False
        self.bc_scan_type_segment.installEventFilter(self)
        controls_layout.addWidget(scan_type_box)

        button_row = QHBoxLayout()
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
        self.stop_button = QPushButton("Stop Scan")
        self.stop_button.clicked.connect(self.stop_scan)
        self.stop_button.setEnabled(False)
        self.stop_button.setFixedHeight(34)
        self.stop_button.setStyleSheet(
            "QPushButton { min-width: 84px; min-height: 34px; max-width: 96px; padding: 6px 12px; "
            "border: none; border-radius: 14px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #d62839, stop:1 #f05a68); color: #ffffff; font-weight: 700; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e03a49, stop:1 #f3717d); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #b91f2f, stop:1 #de4a58); }"
            "QPushButton:disabled { background: #e8edf4; color: #95a3b5; border-color: #dde5ee; }"
        )
        self._normalize_button_row(button_row, [self.start_button, self.stop_button])
        button_row.setSpacing(8)
        for widget in [
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
        self.bc_export_logs_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        log_layout.addWidget(self.bc_export_logs_button)
        controls_layout.addWidget(log_box, 1)

        controls.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        preview_col = QWidget()
        preview_layout = QVBoxLayout(preview_col)
        preview_layout.setContentsMargins(12, 0, 12, 0)
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
        preview_layout.addWidget(self.bc_preview_toolbar)

        bc_preview_controls = QWidget()
        bc_preview_controls_layout = QHBoxLayout(bc_preview_controls)
        bc_preview_controls_layout.setContentsMargins(0, 0, 0, 0)
        bc_preview_controls_layout.setSpacing(8)
        bc_preview_controls_layout.addStretch(1)
        bc_preview_controls_layout.addWidget(self.bc_export_button)
        preview_layout.addWidget(bc_preview_controls)

        preview_layout.addWidget(self.bc_canvas, 1)
        preview_col.setMinimumWidth(0)
        preview_col.setMaximumWidth(2000)
        bc_left_scroll = self._make_vscroll_panel(controls, B_MODE_LEFT_PANEL_WIDTH)
        layout.addWidget(bc_left_scroll, 0)
        layout.addSpacing(PANEL_GAP)
        layout.addWidget(preview_col, 1)
        return page

    def _build_b_mode_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(0)

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
        self.b_sound_speed.setSingleStep(1.0)
        self.b_sound_speed.setDecimals(0)
        scan_form.addRow("Depth Axis", self.b_depth_axis)
        scan_form.addRow("Scan Axis", self.b_scan_axis)
        scan_form.addRow("Scan Length (mm)", self.b_scan_length)
        scan_form.addRow("Scan Points", self.b_scan_points)
        speed_label = QLabel(
            'Speed of Sound (m/s)<br><span style="color:#c23b3b; font-size:9pt;">0 = use time in \\mu s</span>'
        )
        scan_form.addRow(speed_label, self.b_sound_speed)
        self.b_depth_axis.currentIndexChanged.connect(
            self._sync_b_mode_scan_axis_options
        )
        self._sync_b_mode_scan_axis_options()
        left_layout.addWidget(scan_box)

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
        self.b_dry_run_check = QCheckBox("Dry Run")
        self.b_dry_run_check.setChecked(True)
        self.b_live_preview_check = QCheckBox("Live Preview")
        self.b_live_preview_check.setChecked(True)
        self.b_source_label = QLabel("Signal source: Dummy")
        self.b_source_label.setStyleSheet("color: #415368; font-size: 9pt;")
        self.b_dry_run_check.stateChanged.connect(self._update_b_mode_source_label)
        button_row = QHBoxLayout()
        self._normalize_button_row(
            button_row,
            [
                self.b_start_button,
                self.b_stop_button,
            ],
        )
        button_row.setSpacing(8)
        button_row.addWidget(self.b_start_button)
        button_row.addWidget(self.b_stop_button)
        button_row.addWidget(self.b_dry_run_check)
        button_row.addWidget(self.b_live_preview_check)
        button_row.addWidget(self.b_source_label)
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
        self.b_export_logs_button.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        log_layout.addWidget(self.b_export_logs_button)
        left_layout.addWidget(log_box, 1)

        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(12, 0, 12, 0)
        self.b_preview_canvas = PlotCanvas("B-mode preview will appear here")
        self.b_preview_canvas.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.b_preview_toolbar = NavigationToolbar2QT(self.b_preview_canvas, page)
        self.b_preview_toolbar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.b_preview_toolbar.addSeparator()
        self.b_normalize_check = QCheckBox("Normalize")
        self.b_normalize_check.setEnabled(False)
        self.b_normalize_check.setChecked(False)
        self.b_normalize_check.setFixedHeight(34)
        self.b_normalize_check.stateChanged.connect(self._on_b_mode_normalize_toggled)
        right_layout.addWidget(self.b_preview_toolbar)

        b_preview_controls = QWidget()
        b_preview_controls_layout = QHBoxLayout(b_preview_controls)
        b_preview_controls_layout.setContentsMargins(0, 0, 0, 0)
        b_preview_controls_layout.setSpacing(8)
        b_preview_controls_layout.addStretch(1)
        b_preview_controls_layout.addWidget(self.b_normalize_check)
        b_preview_controls_layout.addWidget(self.b_export_button)
        b_preview_controls.setMinimumWidth(0)
        b_preview_controls.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        right_layout.addWidget(b_preview_controls)

        right_layout.addWidget(self.b_preview_canvas, 1)

        left_col.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        right_col.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        right_col.setMinimumWidth(0)
        right_col.setMaximumWidth(2000)
        b_left_scroll = self._make_vscroll_panel(left_col, B_MODE_LEFT_PANEL_WIDTH)
        layout.addWidget(b_left_scroll, 0)
        layout.addSpacing(PANEL_GAP)
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

            QComboBox::drop-down {
                border: none;
                width: 28px;
                subcontrol-origin: padding;
                subcontrol-position: top right;
            }
            QComboBox::down-arrow {
                image: url('data:image/svg+xml;utf8,<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><polygon points="4,6 8,11 12,6" fill="%237b63b5"/></svg>');
                width: 16px;
                height: 16px;
                margin-right: 6px;
            }
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

    def _line_edit_float(self, widget: QLineEdit, default: float) -> float:
        try:
            return float((widget.text() or "").strip())
        except Exception:
            return float(default)

    def _line_edit_int(self, widget: QLineEdit, default: int) -> int:
        try:
            return int((widget.text() or "").strip())
        except Exception:
            return int(default)

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
        combo.setStyleSheet("QComboBox { padding-right: 28px; }")
        return combo

    def _normalize_button_row(
        self, row: QHBoxLayout, buttons: list[QPushButton]
    ) -> None:
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

    def _make_vscroll_panel(
        self, content: QWidget, width: int | None = None
    ) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        scroll.setWidget(content)
        if width is not None:
            scroll.setFixedWidth(width + 18)
            content.setMinimumWidth(width)
        return scroll

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

    def _sync_b_mode_scan_axis_options(self, *_args, preferred_scan_axis: str | None = None) -> None:
        if not hasattr(self, "b_depth_axis") or not hasattr(self, "b_scan_axis"):
            return

        depth_axis = str(self.b_depth_axis.currentText()).strip().upper()
        current_scan_axis = str(self.b_scan_axis.currentText()).strip().upper()
        requested_scan_axis = (
            str(preferred_scan_axis).strip().upper()
            if preferred_scan_axis is not None
            else current_scan_axis
        )
        valid_scan_axes = [axis for axis in ("X", "Y", "Z") if axis != depth_axis]
        if not valid_scan_axes:
            valid_scan_axes = ["X", "Y", "Z"]

        selected_scan_axis = (
            requested_scan_axis
            if requested_scan_axis in valid_scan_axes
            else valid_scan_axes[0]
        )

        self.b_scan_axis.blockSignals(True)
        self.b_scan_axis.clear()
        self.b_scan_axis.addItems(valid_scan_axes)
        self.b_scan_axis.setCurrentText(selected_scan_axis)
        self.b_scan_axis.blockSignals(False)

    def _build_3d_scan_point_plan(
        self,
        scan_steps: int,
        cross_steps: int,
        axis1_mm,
        axis2_mm,
        axis1_pulses,
        axis2_pulses,
        algorithm: str,
    ) -> list[dict]:
        plan: list[dict] = []
        raster = str(algorithm).strip().lower() == "raster"

        order = 1
        for cross_idx in range(cross_steps):
            forward_scan = raster or cross_idx % 2 == 0
            scan_indices = (
                range(scan_steps) if forward_scan else range(scan_steps - 1, -1, -1)
            )
            for scan_idx in scan_indices:
                plan.append(
                    {
                        "order": order,
                        "cross_idx": cross_idx,
                        "scan_idx": scan_idx,
                        "cross_point": cross_idx + 1,
                        "scan_point": scan_idx + 1,
                        "cross_mm": float(axis2_mm[cross_idx]),
                        "scan_mm": float(axis1_mm[scan_idx]),
                        "cross_pulse": int(axis2_pulses[cross_idx]),
                        "scan_pulse": int(axis1_pulses[scan_idx]),
                        "direction": "forward" if forward_scan else "reverse",
                    }
                )
                order += 1
        return plan

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

    def _compute_pressure_field_metric(
        self, signal: np.ndarray, metric_name: str
    ) -> float:
        import numpy as np

        y = np.asarray(signal, dtype=float)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return float("nan")

        key = str(metric_name).strip().lower()
        if key == "max":
            return float(np.max(y))
        if key == "mean":
            return float(np.mean(y))
        if key == "min":
            return float(np.min(y))
        if key == "median":
            return float(np.median(y))
        if key == "rms":
            return float(np.sqrt(np.mean(y * y)))
        if key == "variance":
            return float(np.var(y))
        if key == "energy":
            return float(np.sum(y * y))
        if key == "mode":
            bins = min(256, max(16, int(np.sqrt(y.size))))
            hist, edges = np.histogram(y, bins=bins)
            idx = int(np.argmax(hist))
            return float(0.5 * (edges[idx] + edges[idx + 1]))
        if key == "skewness":
            mu = float(np.mean(y))
            c = y - mu
            m2 = float(np.mean(c * c))
            if m2 <= 0.0:
                return 0.0
            m3 = float(np.mean(c * c * c))
            return float(m3 / (m2**1.5))
        if key == "kurtosis":
            mu = float(np.mean(y))
            c = y - mu
            m2 = float(np.mean(c * c))
            if m2 <= 0.0:
                return 0.0
            m4 = float(np.mean(c * c * c * c))
            return float(m4 / (m2 * m2))
        if key == "entropy":
            bins = min(256, max(16, int(np.sqrt(y.size))))
            hist, _ = np.histogram(y, bins=bins)
            p = hist.astype(float)
            s = float(np.sum(p))
            if s <= 0.0:
                return 0.0
            p /= s
            p = p[p > 0.0]
            return float(-np.sum(p * np.log2(p)))
        return float(np.max(y))

    def _detrend_signal(self, signal: np.ndarray) -> np.ndarray:
        import numpy as np

        y = np.asarray(signal, dtype=float)
        if y.size < 2:
            return y.copy()
        x = np.arange(y.size, dtype=float)
        try:
            coeff = np.polyfit(x, y, 1)
            trend = np.polyval(coeff, x)
            return y - trend
        except Exception:
            # Fallback to DC detrend if linear fit is numerically unstable.
            return y - float(np.mean(y))

    def _apply_pressure_field_filter(
        self,
        signal: np.ndarray,
        *,
        sampling_rate_hz: float,
        filter_type: str,
        order: int,
        highpass_cutoff_hz: float,
        bandpass_low_cutoff_hz: float,
        bandpass_high_cutoff_hz: float,
    ) -> np.ndarray:
        import numpy as np

        y = np.asarray(signal, dtype=float)
        n = y.size
        if n == 0:
            return y.copy()

        fs = float(sampling_rate_hz)
        if fs <= 0.0:
            return y.copy()

        filt_type = str(filter_type).strip().lower()
        ord_n = max(1, int(order))

        # Frequency-domain Butterworth-like response with zero-phase reconstruction.
        f = np.fft.rfftfreq(n, d=1.0 / fs)
        h = np.ones_like(f, dtype=float)

        if filt_type == "high-pass":
            fc = max(0.0, float(highpass_cutoff_hz))
            if fc <= 0.0:
                return y.copy()
            h = 1.0 / np.sqrt(1.0 + np.power(fc / np.maximum(f, 1e-12), 2 * ord_n))
            h[0] = 0.0
        elif filt_type == "band-pass":
            fl = max(0.0, float(bandpass_low_cutoff_hz))
            fh = max(0.0, float(bandpass_high_cutoff_hz))
            nyq = 0.5 * fs
            if fl <= 0.0 or fh <= 0.0 or fl >= fh or fh >= nyq:
                return y.copy()
            h_hp = 1.0 / np.sqrt(1.0 + np.power(fl / np.maximum(f, 1e-12), 2 * ord_n))
            h_hp[0] = 0.0
            h_lp = 1.0 / np.sqrt(1.0 + np.power(np.maximum(f, 1e-12) / fh, 2 * ord_n))
            h = h_hp * h_lp
        else:
            return y.copy()

        spec = np.fft.rfft(y)
        y_f = np.fft.irfft(spec * h, n=n)
        return np.asarray(y_f, dtype=float)

    def _render_bc_live_preview(self, payload: dict) -> None:
        import importlib.util
        import numpy as np

        mode = str(payload.get("mode", "a_mode_live")).strip().lower()
        if mode == "c_mode_map":
            axis1_mm = np.asarray(payload.get("axis1_mm", []), dtype=float)
            axis2_mm = np.asarray(payload.get("axis2_mm", []), dtype=float)
            metric_map = np.asarray(payload.get("metric_map", []), dtype=float)
            metric_name = str(payload.get("metric_name", "Metric"))
            cmap_label = str(payload.get("colormap", "Gray (16-bit)"))
            cmap_name = "gray"
            if cmap_label.lower() not in {"gray (16-bit)", "gray", "grey"}:
                cmap_name = cmap_label

            if axis1_mm.size == 0 or axis2_mm.size == 0 or metric_map.size == 0:
                self.bc_canvas.draw_placeholder("C-Mode preview unavailable")
                return

            self.bc_canvas.figure.clear()
            self.bc_canvas.axes = self.bc_canvas.figure.add_subplot(111)
            self.bc_canvas._style_axes()
            plot_map = np.array(metric_map, dtype=float)
            plot_map[~np.isfinite(plot_map)] = np.nan
            if axis1_mm.size == 1 or axis2_mm.size == 1:
                xg, yg = np.meshgrid(axis1_mm, axis2_mm)
                x_vals = np.asarray(xg, dtype=float).ravel()
                y_vals = np.asarray(yg, dtype=float).ravel()
                c_vals = np.asarray(plot_map, dtype=float).ravel()
                finite = np.isfinite(c_vals)
                if not np.any(finite):
                    self.bc_canvas.draw_placeholder("C-Mode preview unavailable")
                    return
                im = self.bc_canvas.axes.scatter(
                    x_vals[finite],
                    y_vals[finite],
                    c=c_vals[finite],
                    cmap=cmap_name,
                    s=100,
                    marker="o",
                    linewidths=0.4,
                    edgecolors="#1f2a37",
                )
            else:
                extent = [
                    float(np.min(axis1_mm)),
                    float(np.max(axis1_mm)),
                    float(np.min(axis2_mm)),
                    float(np.max(axis2_mm)),
                ]
                im = self.bc_canvas.axes.imshow(
                    plot_map,
                    cmap=cmap_name,
                    aspect="auto",
                    interpolation="nearest",
                    origin="lower",
                    extent=extent,
                )
            if getattr(self, "_bc_colorbar", None) is not None:
                try:
                    self._bc_colorbar.remove()
                except Exception:
                    pass
                self._bc_colorbar = None
            self._bc_colorbar = self.bc_canvas.figure.colorbar(
                im, ax=self.bc_canvas.axes
            )
            self._bc_colorbar.set_label(metric_name)
            self.bc_canvas.axes.set_title(
                f"C-Mode Map ({metric_name})", color="#1f2a37", fontsize=10
            )
            self.bc_canvas.axes.set_xlabel(
                f"{payload.get('axis1_name', 'Axis 1')} (mm)", color="#415368"
            )
            self.bc_canvas.axes.set_ylabel(
                f"{payload.get('axis2_name', 'Axis 2')} (mm)", color="#415368"
            )
            self.bc_canvas.draw_idle()
            return

        if mode == "pressure_field_map":
            axis1_mm = np.asarray(payload.get("axis1_mm", []), dtype=float)
            axis2_mm = np.asarray(payload.get("axis2_mm", []), dtype=float)
            metric_map = np.asarray(payload.get("metric_map", []), dtype=float)
            metric_name = str(payload.get("metric_name", "Metric"))
            cmap_label = str(payload.get("colormap", "Gray (16-bit)"))
            cmap_name = "gray"
            if cmap_label.lower() not in {"gray (16-bit)", "gray", "grey"}:
                cmap_name = cmap_label

            if axis1_mm.size == 0 or axis2_mm.size == 0 or metric_map.size == 0:
                self.bc_canvas.draw_placeholder("Pressure field preview unavailable")
                return

            self._bc_pressure_field_payload = {
                "mode": "pressure_field_map",
                "axis1_name": str(payload.get("axis1_name", "Axis 1")),
                "axis2_name": str(payload.get("axis2_name", "Axis 2")),
                "axis1_mm": np.array(axis1_mm, copy=True),
                "axis2_mm": np.array(axis2_mm, copy=True),
                "metric_map": np.array(metric_map, copy=True),
                "metric_name": metric_name,
                "colormap": cmap_label,
            }

            self.bc_canvas.figure.clear()
            self.bc_canvas.axes = self.bc_canvas.figure.add_subplot(111)
            self.bc_canvas._style_axes()
            plot_map = np.array(metric_map, dtype=float)
            plot_map[~np.isfinite(plot_map)] = np.nan
            if axis1_mm.size == 1 or axis2_mm.size == 1:
                xg, yg = np.meshgrid(axis1_mm, axis2_mm)
                x_vals = np.asarray(xg, dtype=float).ravel()
                y_vals = np.asarray(yg, dtype=float).ravel()
                c_vals = np.asarray(plot_map, dtype=float).ravel()
                finite = np.isfinite(c_vals)
                if not np.any(finite):
                    self.bc_canvas.draw_placeholder("Pressure field preview unavailable")
                    return
                im = self.bc_canvas.axes.scatter(
                    x_vals[finite],
                    y_vals[finite],
                    c=c_vals[finite],
                    cmap=cmap_name,
                    s=100,
                    marker="o",
                    linewidths=0.4,
                    edgecolors="#1f2a37",
                )
            else:
                extent = [
                    float(np.min(axis1_mm)),
                    float(np.max(axis1_mm)),
                    float(np.min(axis2_mm)),
                    float(np.max(axis2_mm)),
                ]
                im = self.bc_canvas.axes.imshow(
                    plot_map,
                    cmap=cmap_name,
                    aspect="auto",
                    interpolation="nearest",
                    origin="lower",
                    extent=extent,
                )
            if getattr(self, "_bc_colorbar", None) is not None:
                try:
                    self._bc_colorbar.remove()
                except Exception:
                    pass
                self._bc_colorbar = None
            self._bc_colorbar = self.bc_canvas.figure.colorbar(
                im, ax=self.bc_canvas.axes
            )
            self._bc_colorbar.set_label(metric_name)
            self.bc_canvas.axes.set_title(
                f"Pressure Field Map ({metric_name})", color="#1f2a37", fontsize=10
            )
            self.bc_canvas.axes.set_xlabel(
                f"{payload.get('axis1_name', 'Axis 1')} (mm)", color="#415368"
            )
            self.bc_canvas.axes.set_ylabel(
                f"{payload.get('axis2_name', 'Axis 2')} (mm)", color="#415368"
            )
            self.bc_canvas.draw_idle()
            return

        t = np.asarray(payload.get("t", []), dtype=float)
        raw = np.asarray(payload.get("raw", []), dtype=float)
        scan_type = str(payload.get("scan_type", "standard")).strip().lower()
        scan_idx = int(payload.get("scan_idx", 1))
        total_scans = int(payload.get("total_scans", 1))
        axis1_name = str(payload.get("axis1_name", "Axis1"))
        axis2_name = str(payload.get("axis2_name", "Axis2"))
        axis1_idx = int(payload.get("axis1_idx", 1))
        axis2_idx = int(payload.get("axis2_idx", 1))
        axis1_total = int(payload.get("axis1_total", 1))
        axis2_total = int(payload.get("axis2_total", 1))

        # Compute envelope using the exact same routine and parameters as the A-Mode tab
        envelope = np.empty(0)
        if t.size > 0 and raw.size > 0:
            try:
                script_path = BASE_DIR / "A scan.py"
                spec = importlib.util.spec_from_file_location("a_scan", script_path)
                if spec is not None and spec.loader is not None:
                    _mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(_mod)
                    envelope = np.asarray(
                        _mod.estimate_a_mode_signal(
                            t,
                            raw,
                            highpass_cutoff_hz=float(
                                self.a_mode_highpass_cutoff.value()
                            )
                            * 1000.0,
                            filter_order=int(self.a_mode_filter_order.value()),
                            sampling_rate_hz=self._extract_last_float(
                                self.sampling_rate_edit.text().strip(), 0.0
                            )
                            * 1000.0,
                        ),
                        dtype=float,
                    )
            except Exception as _env_exc:
                self.bridge.bc_log.emit(f"[Live preview] Envelope error: {_env_exc}")

        # Convert time axis using speed of sound from A-mode control (same as A-Mode tab)
        sound_speed_mps = float(self.a_mode_sound_speed.value())
        if sound_speed_mps > 0.0:
            x = t * sound_speed_mps * 1000.0
            x_label = "Distance (mm)"
        else:
            x = t * 1_000_000.0
            x_label = r"Time ($\mu$s)"

        # A-Mode in 3D preview: keep the current behavior (raw echo + A-mode envelope).
        # Other modes will be wired to their dedicated visualizations in follow-up steps.
        if scan_type not in {"", "standard", "a_mode"}:
            scan_type = "standard"

        title = (
            f"{axis1_name} pt {axis1_idx}/{axis1_total}  |  "
            f"{axis2_name} row {axis2_idx}/{axis2_total}  —  "
            f"Scan {scan_idx}/{total_scans}"
        )

        self.bc_canvas.figure.clear()
        self.bc_canvas.axes = self.bc_canvas.figure.add_subplot(111)
        self.bc_canvas._style_axes()

        n = min(x.size, raw.size)
        if n > 0:
            self.bc_canvas.axes.plot(
                x[:n],
                raw[:n],
                color="#2f80ed",
                linewidth=1.0,
                alpha=0.55,
                label="Raw echo",
            )
        ne = min(x.size, envelope.size)
        if ne > 0:
            self.bc_canvas.axes.plot(
                x[:ne],
                envelope[:ne],
                color="#8a3ffc",
                linewidth=1.8,
                linestyle="--",
                label="A-mode envelope",
            )
        self.bc_canvas.axes.legend(loc="best", fontsize=8)
        self.bc_canvas.axes.set_title(title, color="#1f2a37", fontsize=9)
        self.bc_canvas.axes.set_xlabel(x_label, color="#415368")
        self.bc_canvas.axes.set_ylabel("Amplitude (V)", color="#415368")
        self.bc_canvas.draw_idle()

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _set_test_busy(self, busy: bool) -> None:
        self.test_button.setEnabled(not busy)
        self.test_progress.setVisible(busy)

    def _set_scan_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        if busy:
            for _btn_name in ("bc_post_apply_button", "bc_apply_cmode_button"):
                if hasattr(self, _btn_name):
                    getattr(self, _btn_name).setEnabled(False)
        else:
            if self._bc_apply_unlocked:
                if hasattr(self, "bc_post_apply_button"):
                    self.bc_post_apply_button.setEnabled(True)
                if hasattr(self, "bc_apply_cmode_button"):
                    self.bc_apply_cmode_button.setEnabled(True)


    def show_about(self) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
        from PySide6.QtCore import Qt
        class AboutDialog(QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setWindowTitle("About")
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
                self.setFixedSize(480, 180)
                layout = QVBoxLayout(self)
                label = QLabel(
                    "Developed by Anqi Yang and Reza Haqshenas at the UCL Ultrasonics Group. This project was created with assistance from GitHub Copilot and is released as open-source software under the MIT License, 2026."
                )
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet("font-size: 13pt; padding: 18px;")
                layout.addWidget(label)
                # Remove Ok button, only X at top right
                self.setWindowFlag(Qt.WindowCloseButtonHint, True)
                self.setWindowFlag(Qt.WindowMinimizeButtonHint, False)
                self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        dlg = AboutDialog(self)
        dlg.exec()

    def show_documentation(self) -> None:
        import webbrowser
        import os
        docs_path = os.path.abspath(os.path.join("docs", "docs.html"))
        if not os.path.exists(docs_path):
            QMessageBox.warning(self, "Documentation Not Found", "docs/docs.html was not found. Please run 'Update Documentation' first.")
            return
        webbrowser.open(f"file://{docs_path}")

    def update_documentation(self) -> None:
        import os
        import shutil
        import subprocess
        docs_dir = os.path.join(os.path.dirname(__file__), "docs")
        ipynb_path = os.path.join(docs_dir, "documentation.ipynb")
        html_path = os.path.join(docs_dir, "docs.html")
        if not os.path.exists(ipynb_path):
            QMessageBox.warning(self, "Missing File", "docs/documentation.ipynb was not found.")
            return
        try:
            result = subprocess.run([
                sys.executable, "-m", "jupyter", "nbconvert", "--to", "html", ipynb_path, "--output", html_path, "--no-input"
            ], capture_output=True, text=True)
            if result.returncode != 0:
                if "No module named nbconvert" in result.stderr:
                    QMessageBox.warning(self, "nbconvert Not Installed", "Jupyter nbconvert is not installed. Please run:\n\npip install nbconvert\n\nin your terminal.")
                else:
                    QMessageBox.warning(self, "Conversion Error", f"nbconvert failed:\n{result.stderr}")
                return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred: {e}")
            return
        self.show_documentation()

    def _load_settings_file(self) -> dict:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _apply_saved_rig_position(
        self, payload: object
    ) -> None:
        position = None
        if isinstance(payload, dict):
            position = payload.get("position")
        elif isinstance(payload, (list, tuple)) and len(payload) >= 1:
            position = payload[0]

        with self._saved_position_lock:
            self._saved_rig_position = position
            self._session_move_delta = (0, 0, 0)

        self.return_position_button.setEnabled(position is not None)

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

            saved_sg_name = self._normalize_sg_model_name(
                str(cfg.get("sg_name", self.sg_name_edit.currentText()))
            )
            self.sg_name_edit.setCurrentText(saved_sg_name)
            self.sg_address_edit.setText(
                str(
                    cfg.get(
                        "sg_address",
                        self._sg_model_to_visa.get(saved_sg_name, self._default_sg_address),
                    )
                )
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
                self._extract_last_float(self.sampling_rate_edit.text(), 1000.0)
                * 1000.0,
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
                max(
                    1,
                    int(
                        round(
                            float(
                                tx.get(
                                    "no_of_cycles_per_pulse",
                                    self.tx_cycles.value(),
                                )
                            )
                        )
                    ),
                )
            )
            self.tx_pulses.setValue(int(tx.get("no_of_pulses", self.tx_pulses.value())))
            self.tx_prf.setValue(float(tx.get("prf", self.tx_prf.value())))
            self.tx_start_delay_us.setValue(
                float(tx.get("start_delay_s", 0.0)) * 1_000_000.0
            )
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
            self.a_mode_sound_speed.setValue(
                float(a_mode.get("sound_speed_mps", self.a_mode_sound_speed.value()))
            )
            self.a_dry_run_check.setChecked(
                bool(a_mode.get("dry_run", self.a_dry_run_check.isChecked()))
            )
            self._update_a_mode_source_label()
            self.a_live_preview_check.setChecked(
                bool(a_mode.get("live_preview", self.a_live_preview_check.isChecked()))
            )

            self.b_depth_axis.setCurrentText(
                str(b_line.get("depth_axis", self.b_depth_axis.currentText()))
            )
            self._sync_b_mode_scan_axis_options(
                preferred_scan_axis=str(
                    b_line.get("scan_axis", self.b_scan_axis.currentText())
                )
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
            self._update_b_mode_source_label()
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
            self.bc_scan_algorithm_combo.setCurrentText(
                str(bc.get("scan_algorithm", self.bc_scan_algorithm_combo.currentText()))
            )
            self.dry_run_check.setChecked(
                bool(bc.get("dry_run", self.dry_run_check.isChecked()))
            )
            self._update_bc_mode_source_label()
            self.live_update_check.setChecked(
                bool(bc.get("live_update", self.live_update_check.isChecked()))
            )
            # Always launch with A-Mode selected in the 3D-mode scan-type group.
            self._set_bc_scan_type("standard")
            self.bc_c_mode_gate_start.setValue(
                float(
                    bc.get(
                        "c_mode_gate_start_us",
                        bc.get(
                            "c_mode_gate_time_us",
                            bc.get(
                                "c_mode_sound_speed_mps",
                                self.bc_c_mode_gate_start.value(),
                            ),
                        ),
                    )
                )
            )
            self.bc_c_mode_gate_width.setValue(
                float(bc.get("c_mode_gate_width_us", self.bc_c_mode_gate_width.value()))
            )
            self.bc_c_mode_metric_combo.setCurrentText(
                str(
                    bc.get(
                        "c_mode_metric",
                        self.bc_c_mode_metric_combo.currentText(),
                    )
                )
            )
            self.bc_c_mode_cmap_combo.setCurrentText(
                str(
                    bc.get(
                        "c_mode_colormap",
                        self.bc_c_mode_cmap_combo.currentText(),
                    )
                )
            )
            self.bc_pf_mode_metric_combo.setCurrentText(
                str(
                    bc.get(
                        "a_mode_metric",
                        self.bc_pf_mode_metric_combo.currentText(),
                    )
                )
            )
            self.bc_pf_mode_cmap_combo.setCurrentText(
                str(
                    bc.get(
                        "pressure_field_colormap",
                        self.bc_pf_mode_cmap_combo.currentText(),
                    )
                )
            )
            pf_extra_value = str(
                bc.get(
                    "pressure_field_extra",
                    self.bc_pf_mode_extra_combo.currentText(),
                )
            ).strip()
            if pf_extra_value.lower() in {"filtering", "yes", "true", "1"}:
                self.bc_pf_mode_extra_combo.setCurrentText("Yes")
            else:
                self.bc_pf_mode_extra_combo.setCurrentText("No")
            self.bc_pf_filter_type_combo.setCurrentText(
                str(
                    bc.get(
                        "pressure_field_filter_type",
                        self.bc_pf_filter_type_combo.currentText(),
                    )
                )
            )
            self.bc_pf_filter_order_spin.setText(
                str(bc.get("pressure_field_filter_order", "4"))
            )
            self.bc_pf_filter_high_cutoff_spin.setText(
                str(bc.get("pressure_field_highpass_cutoff_khz", "50.0"))
            )
            self.bc_pf_filter_band_cutoff_spin.setText(
                str(bc.get("pressure_field_bandpass_high_cutoff_khz", "500.0"))
            )
            self._update_bc_pf_filter_controls()
        except Exception:
            pass
        finally:
            self._is_loading_settings = False

    def _collect_settings_payload(self) -> dict:
        return {
            "config": {
                "sg_name": self.sg_name_edit.currentText().strip(),
                "sg_address": self.sg_address_edit.text().strip() or self._default_sg_address,
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
                "no_of_cycles_per_pulse": int(self.tx_cycles.value()),
                "no_of_pulses": int(self.tx_pulses.value()),
                "prf": float(self.tx_prf.value()),
                "start_delay_s": float(self.tx_start_delay_us.value()) * 1e-6,
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
                "sound_speed_mps": float(self.a_mode_sound_speed.value()),
                "mode": "INC",
                "dry_run": bool(self.a_dry_run_check.isChecked()),
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
                "scan_algorithm": self.bc_scan_algorithm_combo.currentText(),
                "dry_run": bool(self.dry_run_check.isChecked()),
                "live_update": bool(self.live_update_check.isChecked()),
                "scan_type": self._current_bc_scan_type(),
                "c_mode_gate_start_us": float(self.bc_c_mode_gate_start.value()),
                "c_mode_gate_width_us": float(self.bc_c_mode_gate_width.value()),
                "c_mode_metric": self.bc_c_mode_metric_combo.currentText(),
                "c_mode_colormap": self.bc_c_mode_cmap_combo.currentText(),
                "a_mode_metric": self.bc_pf_mode_metric_combo.currentText(),
                "pressure_field_colormap": self.bc_pf_mode_cmap_combo.currentText(),
                "pressure_field_extra": self.bc_pf_mode_extra_combo.currentText(),
                "pressure_field_filter_type": self.bc_pf_filter_type_combo.currentText(),
                "pressure_field_filter_order": self._line_edit_int(self.bc_pf_filter_order_spin, 4),
                "pressure_field_highpass_cutoff_khz": self._line_edit_float(self.bc_pf_filter_high_cutoff_spin, 50.0),
                "pressure_field_bandpass_low_cutoff_khz": self._line_edit_float(self.bc_pf_filter_high_cutoff_spin, 50.0),
                "pressure_field_bandpass_high_cutoff_khz": self._line_edit_float(self.bc_pf_filter_band_cutoff_spin, 500.0),
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
            self.tx_start_delay_us,
            self.a_mode_x,
            self.a_mode_y,
            self.a_mode_z,
            self.a_mode_highpass_cutoff,
            self.a_mode_filter_order,
            self.a_mode_sound_speed,
            self.b_scan_length,
            self.b_scan_points,
            self.b_sound_speed,
            self.bc_c_mode_gate_start,
            self.bc_c_mode_gate_width,
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
        for line_edit in (
            self.bc_pf_filter_order_spin,
            self.bc_pf_filter_high_cutoff_spin,
            self.bc_pf_filter_band_cutoff_spin,
        ):
            line_edit.textChanged.connect(self._schedule_settings_save)

        combos = [
            self.sg_name_edit,
            self.tx_windowing_combo,
            self.b_depth_axis,
            self.b_scan_axis,
            self.scan_axis,
            self.cross_axis,
            self.depth_axis,
            self.bc_c_mode_cmap_combo,
            self.bc_pf_mode_metric_combo,
            self.bc_pf_mode_cmap_combo,
            self.bc_pf_mode_extra_combo,
            self.bc_pf_filter_type_combo,
            self.bc_c_mode_metric_combo,
            self.bc_scan_algorithm_combo,
        ]
        for widget in combos:
            widget.currentIndexChanged.connect(self._schedule_settings_save)

        checks = [
            self.tx_auto_preview_check,
            self.a_dry_run_check,
            self.a_live_preview_check,
            self.b_live_preview_check,
            self.b_dry_run_check,
            self.dry_run_check,
            self.live_update_check,
        ]
        for widget in checks:
            widget.stateChanged.connect(self._schedule_settings_save)
        self.bc_scan_type_standard_btn.toggled.connect(self._schedule_settings_save)
        self.bc_scan_type_c_btn.toggled.connect(self._schedule_settings_save)
        self.bc_scan_type_pf_btn.toggled.connect(self._schedule_settings_save)

    def _extract_last_float(self, text: str, fallback: float) -> float:
        for token in reversed(str(text).replace(",", " ").split()):
            try:
                return float(token)
            except ValueError:
                continue
        return fallback

    def _normalize_sg_model_name(self, model_name: str) -> str:
        name = str(model_name).strip()
        if not name:
            return self.sg_name_edit.currentText().strip() or "Agilent33500B"
        if name == "Agilent33500":
            return "Agilent33500B"
        if name in self._sg_model_to_visa:
            return name
        return self.sg_name_edit.currentText().strip() or "Agilent33500B"

    def _on_sg_model_changed(self, model_name: str) -> None:
        normalized = self._normalize_sg_model_name(model_name)
        if normalized != model_name and self.sg_name_edit.currentText() != normalized:
            self.sg_name_edit.blockSignals(True)
            self.sg_name_edit.setCurrentText(normalized)
            self.sg_name_edit.blockSignals(False)
        if getattr(self, "_is_loading_settings", False):
            return
        target_visa = self._sg_model_to_visa.get(normalized, self._default_sg_address)
        self.sg_address_edit.setText(target_visa)

    def _format_hz_for_log(self, value_hz: float | None) -> str:
        if value_hz is None:
            return "generator-internal"
        hz = float(value_hz)
        if hz >= 1_000_000.0:
            return f"{hz / 1_000_000.0:.3f} MHz"
        if hz >= 1_000.0:
            return f"{hz / 1_000.0:.3f} kHz"
        return f"{hz:.3f} Hz"

    def _close_sg_handle(self, sg) -> None:
        if sg is None:
            return
        try:
            sg.output = False
        except Exception:
            pass
        try:
            sg.shutdown()
        except Exception:
            pass
        try:
            adapter = getattr(sg, "adapter", None)
            if adapter is not None and hasattr(adapter, "close"):
                adapter.close()
        except Exception:
            pass

    def _get_stable_sg(self, sg_address: str):
        addr = str(sg_address).strip()
        with self._sg_state_lock:
            if self._stable_sg is None:
                return None
            if str(self._stable_sg_address or "").strip() != addr:
                return None
            return self._stable_sg

    def _set_stable_sg(self, sg, sg_address: str) -> None:
        old = None
        with self._sg_state_lock:
            old = self._stable_sg
            self._stable_sg = sg
            self._stable_sg_address = str(sg_address).strip()
        if old is not None and old is not sg:
            self._close_sg_handle(old)

    def _clear_stable_sg(self) -> None:
        old = None
        with self._sg_state_lock:
            old = self._stable_sg
            self._stable_sg = None
            self._stable_sg_address = None
        self._close_sg_handle(old)

    def _acquire_cached_sg(self, sg_address: str, driver, retries: int, retry_delay: float):
        addr = str(sg_address).strip()
        stable_sg = self._get_stable_sg(addr)
        if stable_sg is not None:
            return stable_sg, False

        last_err = None
        for _ in range(max(1, int(retries))):
            sg = None
            try:
                sg = driver(addr)
                self._set_stable_sg(sg, addr)
                return sg, True
            except Exception as exc:
                last_err = exc
                if sg is not None:
                    self._close_sg_handle(sg)
                time.sleep(max(float(retry_delay), 0.0))

        raise last_err

    def _close_osc_handle(self, osc) -> None:
        if osc is None:
            return
        if id(osc) == self._scope_capture_config_handle_id:
            self._scope_capture_config_handle_id = None
            self._scope_capture_config_signature = None
        try:
            osc.close()
        except Exception:
            pass
        try:
            import Oscilloscope as oscmod

            oscmod.close_oscilloscope()
        except Exception:
            pass

    def _get_stable_osc(self, osc_address: str):
        addr = str(osc_address).strip()
        with self._osc_state_lock:
            if self._stable_osc is None:
                return None
            if str(self._stable_osc_address or "").strip() != addr:
                return None
            return self._stable_osc

    def _set_stable_osc(self, osc, osc_address: str) -> None:
        old = None
        with self._osc_state_lock:
            old = self._stable_osc
            self._stable_osc = osc
            self._stable_osc_address = str(osc_address).strip()
            self._scope_capture_config_signature = None
            self._scope_capture_config_handle_id = None
        if old is not None and old is not osc:
            self._close_osc_handle(old)

    def _clear_stable_osc(self) -> None:
        old = None
        with self._osc_state_lock:
            old = self._stable_osc
            self._stable_osc = None
            self._stable_osc_address = None
            self._scope_capture_config_signature = None
            self._scope_capture_config_handle_id = None
        self._close_osc_handle(old)

    def _acquire_cached_osc(self, osc_address: str, retries: int, retry_delay: float):
        import Oscilloscope as oscmod

        addr = str(osc_address).strip()
        stable_osc = self._get_stable_osc(addr)
        if stable_osc is not None:
            return stable_osc, False

        last_err = None
        for _ in range(max(1, int(retries))):
            try:
                osc = oscmod.open_oscilloscope(
                    addr,
                    max_attempts=1,
                    retry_delay=retry_delay,
                    force_reopen=False,
                )
                self._set_stable_osc(osc, addr)
                return osc, True
            except Exception as exc:
                last_err = exc
                time.sleep(max(float(retry_delay), 0.0))

        raise last_err

    def _update_a_mode_source_label(self, *_args) -> None:
        if self.a_dry_run_check.isChecked():
            self.a_source_label.setText("Signal source: Dummy")
        else:
            self.a_source_label.setText("Signal source: Hardware")

    def _update_b_mode_source_label(self, *_args) -> None:
        if self.b_dry_run_check.isChecked():
            self.b_source_label.setText("Signal source: Dummy")
        else:
            self.b_source_label.setText("Signal source: Hardware")

    def _update_bc_mode_source_label(self, *_args) -> None:
        if self.dry_run_check.isChecked():
            self.bc_source_label.setText("Signal source: Dummy")
        else:
            self.bc_source_label.setText("Signal source: Hardware")

    def _current_bc_scan_type(self) -> str:
        if self.bc_scan_type_c_btn.isChecked():
            return "c_mode"
        if self.bc_scan_type_pf_btn.isChecked():
            return "pressure_field"
        return "standard"

    def _validate_pressure_field_filter_settings(self, pg: dict) -> tuple[bool, str]:
        mode = self._current_bc_scan_type()
        if mode != "pressure_field":
            return True, ""

        filtering_enabled = (
            self.bc_pf_mode_extra_combo.currentText().strip().lower() == "yes"
        )
        if not filtering_enabled:
            return True, ""

        sampling_rate_hz = (
            self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(float(pg.get("frequency", 1.0)) * 100.0, 1e6) / 1000.0,
            )
            * 1000.0
        )
        if sampling_rate_hz <= 0.0:
            return False, "Sampling rate must be > 0 Hz for pressure field filtering."

        nyquist_hz = 0.5 * sampling_rate_hz
        filt_type = self.bc_pf_filter_type_combo.currentText().strip().lower()
        order = self._line_edit_int(self.bc_pf_filter_order_spin, 0)
        if order < 1:
            return False, "Filter order must be >= 1."

        if filt_type == "high-pass":
            cutoff_hz = float(self.bc_pf_filter_high_cutoff_spin.text() or 0.0) * 1000.0
            if cutoff_hz <= 0.0:
                return False, "High-pass cutoff must be > 0 kHz."
            if cutoff_hz >= nyquist_hz:
                return (
                    False,
                    "High-pass cutoff must be below Nyquist frequency "
                    f"({nyquist_hz / 1000.0:.3f} kHz).",
                )
            return True, ""

        if filt_type == "band-pass":
            low_hz = float(self.bc_pf_filter_high_cutoff_spin.text() or 0.0) * 1000.0
            high_hz = float(self.bc_pf_filter_band_cutoff_spin.text() or 0.0) * 1000.0
            if low_hz <= 0.0 or high_hz <= 0.0:
                return False, "Band-pass low/high cutoffs must both be > 0 kHz."
            if low_hz >= high_hz:
                return False, "Band-pass low cutoff must be lower than high cutoff."
            if high_hz >= nyquist_hz:
                return (
                    False,
                    "Band-pass high cutoff must be below Nyquist frequency "
                    f"({nyquist_hz / 1000.0:.3f} kHz).",
                )
            return True, ""

        return False, f"Unsupported filter type: {self.bc_pf_filter_type_combo.currentText()}"

    def _set_bc_scan_type(self, scan_type: str) -> None:
        key = str(scan_type).strip().lower()
        if key == "c_mode":
            self.bc_scan_type_c_btn.setChecked(True)
        elif key == "pressure_field":
            self.bc_scan_type_pf_btn.setChecked(True)
        else:
            self.bc_scan_type_standard_btn.setChecked(True)
        self._on_bc_scan_type_changed()

    def _on_bc_scan_type_changed(self, *_args) -> None:
        mode = self._current_bc_scan_type()
        if mode == "c_mode":
            self.bc_options_stack.setCurrentIndex(1)
        elif mode == "pressure_field":
            self.bc_options_stack.setCurrentIndex(2)
        else:
            self.bc_options_stack.setCurrentIndex(0)
        if hasattr(self, "bc_post_apply_button"):
            self.bc_post_apply_button.setEnabled(self._bc_apply_unlocked)
        if hasattr(self, "bc_apply_cmode_button"):
            self.bc_apply_cmode_button.setEnabled(self._bc_apply_unlocked)
        self._update_bc_pf_filter_controls()
        self._animate_bc_scan_type_indicator(animate=True)

    def _update_bc_pf_filter_controls(self, *_args) -> None:
        if not hasattr(self, "bc_filter_options_stack"):
            return

        mode = self._current_bc_scan_type()
        if mode == "c_mode":
            # C-Mode: show depth/apply controls in lower panel
            self.bc_filter_options_stack.setCurrentIndex(1)
            return
        if mode != "pressure_field":
            # A-Mode: lower panel remains blank
            self.bc_filter_options_stack.setCurrentIndex(0)
            return

        # PF: show the pressure-field filter/apply page
        self.bc_filter_options_stack.setCurrentIndex(2)

        show_filter = (
            self.bc_pf_mode_extra_combo.currentText().strip().lower()
            in {"yes", "filtering"}
        )
        for _w in [
            self.bc_pf_filter_type_label,
            self.bc_pf_filter_type_combo,
            self.bc_pf_filter_order_label,
            self.bc_pf_filter_order_spin,
        ]:
            _w.setVisible(show_filter)
        if hasattr(self, "bc_pf_filter_bottom_widget"):
            self.bc_pf_filter_bottom_widget.setVisible(show_filter)
        if show_filter:
            is_bandpass = (
                self.bc_pf_filter_type_combo.currentText().strip().lower() == "band-pass"
            )
            self.bc_pf_filter_cutoff_dash.setVisible(is_bandpass)
            self.bc_pf_filter_band_cutoff_spin.setVisible(is_bandpass)

    def _on_bc_post_apply_clicked(self) -> None:
        mode = self._current_bc_scan_type()
        if mode == "pressure_field":
            self._apply_pressure_field_postprocessing_from_saved_data()
            return
        if mode == "c_mode":
            self._apply_c_mode_postprocessing_from_saved_data()
            return

    def _apply_pressure_field_postprocessing_from_saved_data(self) -> None:
        import numpy as np

        source = getattr(self, "_bc_pressure_field_source", None)
        if not source:
            self.bridge.bc_log.emit(
                "Apply ignored: no pressure-field scan data is available yet. Run one pressure-field scan first."
            )
            return

        ok, msg = self._validate_pressure_field_filter_settings(
            {"frequency": float(self.freq_spin.value())}
        )
        if not ok:
            self._show_error("Invalid Pressure Field Filter Settings", msg)
            self.bridge.bc_log.emit(f"Pressure field filter settings invalid: {msg}")
            return

        scan_folder = str(source.get("scan_folder", "")).strip()
        scan_steps = int(source.get("scan_steps", 0))
        cross_steps = int(source.get("cross_steps", 0))
        axis1_mm = np.asarray(source.get("axis1_mm", []), dtype=float)
        axis2_mm = np.asarray(source.get("axis2_mm", []), dtype=float)
        axis1_name = str(source.get("axis1_name", "Axis 1"))
        axis2_name = str(source.get("axis2_name", "Axis 2"))

        if not scan_folder or scan_steps <= 0 or cross_steps <= 0:
            self.bridge.bc_log.emit(
                "Apply ignored: saved pressure-field metadata is incomplete. Run a pressure-field scan again."
            )
            return

        sampling_rate_hz = (
            self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(float(self.freq_spin.value()) * 100.0, 1e6) / 1000.0,
            )
            * 1000.0
        )
        filtering_enabled = (
            self.bc_pf_mode_extra_combo.currentText().strip().lower() == "yes"
        )
        filter_type = self.bc_pf_filter_type_combo.currentText().strip()
        filter_order = self._line_edit_int(self.bc_pf_filter_order_spin, 4)
        hp_cutoff_hz = self._line_edit_float(self.bc_pf_filter_high_cutoff_spin, 50.0) * 1000.0
        bp_low_hz = self._line_edit_float(self.bc_pf_filter_high_cutoff_spin, 50.0) * 1000.0
        bp_high_hz = self._line_edit_float(self.bc_pf_filter_band_cutoff_spin, 500.0) * 1000.0
        metric_name = self.bc_pf_mode_metric_combo.currentText().strip()
        colormap = self.bc_pf_mode_cmap_combo.currentText().strip()

        signal_map = np.empty((cross_steps, scan_steps), dtype=object)
        signal_map[:, :] = None
        metric_map = np.full((cross_steps, scan_steps), np.nan, dtype=float)
        loaded_points = 0
        missing_points = 0

        manifest_index: dict[tuple[int, int], str] = {}
        manifest_path = os.path.join(scan_folder, "point_manifest.csv")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8", newline="") as mf:
                    reader = csv.DictReader(mf)
                    for row in reader:
                        row_idx = int(str(row.get("row_index", "")).strip())
                        col_idx = int(str(row.get("col_index", "")).strip())
                        filename = str(row.get("measurement_csv", "")).strip()
                        if row_idx > 0 and col_idx > 0 and filename:
                            manifest_index[(row_idx, col_idx)] = filename
            except Exception as manifest_exc:
                self.bridge.bc_log.emit(
                    f"Pressure-field manifest parse warning: {manifest_exc}"
                )

        for axis2_idx in range(1, cross_steps + 1):
            for axis1_idx in range(1, scan_steps + 1):
                csv_name = manifest_index.get((axis2_idx, axis1_idx), "")
                csv_path = os.path.join(scan_folder, csv_name) if csv_name else ""
                if not csv_path or not os.path.exists(csv_path):
                    csv_path = os.path.join(
                        scan_folder, f"point_*_r{axis2_idx:03d}_c{axis1_idx:03d}.csv"
                    )
                    matches = sorted(Path(scan_folder).glob(os.path.basename(csv_path)))
                    if matches:
                        csv_path = str(matches[-1])
                    else:
                        csv_path = os.path.join(
                            scan_folder, f"row_{axis1_idx}_col_{axis2_idx}.csv"
                        )
                if not os.path.exists(csv_path):
                    missing_points += 1
                    continue
                try:
                    y = np.loadtxt(csv_path, delimiter=",", skiprows=1, usecols=1)
                except Exception:
                    missing_points += 1
                    continue

                arr = np.asarray(np.atleast_1d(y), dtype=float)
                if arr.size == 0:
                    missing_points += 1
                    continue

                processed = self._detrend_signal(arr)
                if filtering_enabled:
                    processed = self._apply_pressure_field_filter(
                        processed,
                        sampling_rate_hz=sampling_rate_hz,
                        filter_type=filter_type,
                        order=filter_order,
                        highpass_cutoff_hz=hp_cutoff_hz,
                        bandpass_low_cutoff_hz=bp_low_hz,
                        bandpass_high_cutoff_hz=bp_high_hz,
                    )

                r = axis2_idx - 1
                c = axis1_idx - 1
                signal_map[r, c] = np.array(processed, copy=True)
                metric_map[r, c] = self._compute_pressure_field_metric(processed, metric_name)
                loaded_points += 1

        if loaded_points == 0:
            self.bridge.bc_log.emit(
                "Apply ignored: no saved pressure-field A-scan CSV files were found for post-processing."
            )
            return

        self._bc_pressure_field_cache = {
            "axis1_name": axis1_name,
            "axis2_name": axis2_name,
            "axis1_mm": np.array(axis1_mm, copy=True),
            "axis2_mm": np.array(axis2_mm, copy=True),
            "signal_map": np.array(signal_map, copy=True),
        }

        self._render_bc_live_preview(
            {
                "mode": "pressure_field_map",
                "axis1_name": axis1_name,
                "axis2_name": axis2_name,
                "axis1_mm": axis1_mm,
                "axis2_mm": axis2_mm,
                "metric_map": metric_map,
                "metric_name": metric_name,
                "colormap": colormap,
            }
        )

        self.bridge.bc_log.emit(
            f"Applied pressure-field post-processing from saved A-scans: loaded={loaded_points}, missing={missing_points}, "
            f"filter={'on' if filtering_enabled else 'off'}, metric={metric_name}."
        )
        if hasattr(self, "bc_post_apply_button"):
            self.bc_post_apply_button.setEnabled(self._bc_apply_unlocked)

    def _apply_c_mode_postprocessing_from_saved_data(self) -> None:
        import importlib.util
        import numpy as np

        source = getattr(self, "_bc_c_mode_source", None)
        if not source:
            self.bridge.bc_log.emit(
                "Apply ignored: no C-Mode scan data is available yet. Run one C-Mode scan first."
            )
            return

        scan_folder = str(source.get("scan_folder", "")).strip()
        scan_steps = int(source.get("scan_steps", 0))
        cross_steps = int(source.get("cross_steps", 0))
        axis1_mm = np.asarray(source.get("axis1_mm", []), dtype=float)
        axis2_mm = np.asarray(source.get("axis2_mm", []), dtype=float)
        axis1_name = str(source.get("axis1_name", "Axis 1"))
        axis2_name = str(source.get("axis2_name", "Axis 2"))

        if not scan_folder or scan_steps <= 0 or cross_steps <= 0:
            self.bridge.bc_log.emit(
                "Apply ignored: saved C-Mode metadata is incomplete. Run a C-Mode scan again."
            )
            return

        script_path = BASE_DIR / "A scan.py"
        spec = importlib.util.spec_from_file_location("a_scan", script_path)
        if spec is None or spec.loader is None:
            self.bridge.bc_log.emit(
                "Apply ignored: could not load A scan.py for envelope estimation."
            )
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sampling_rate_hz = (
            self._extract_last_float(
                self.sampling_rate_edit.text().strip(),
                max(float(self.freq_spin.value()) * 100.0, 1e6) / 1000.0,
            )
            * 1000.0
        )
        cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
        filter_order = int(self.a_mode_filter_order.value())
        gate_start_s = float(self.bc_c_mode_gate_start.value()) * 1e-6
        gate_width_s = float(self.bc_c_mode_gate_width.value()) * 1e-6
        gate_end_s = gate_start_s + gate_width_s
        metric_name = self.bc_c_mode_metric_combo.currentText().strip()
        colormap = self.bc_c_mode_cmap_combo.currentText().strip()

        gated_signal_map = np.empty((cross_steps, scan_steps), dtype=object)
        gated_signal_map[:, :] = None
        metric_map = np.full((cross_steps, scan_steps), np.nan, dtype=float)
        loaded_points = 0
        missing_points = 0

        manifest_index: dict[tuple[int, int], str] = {}
        manifest_path = os.path.join(scan_folder, "point_manifest.csv")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8", newline="") as mf:
                    reader = csv.DictReader(mf)
                    for row in reader:
                        row_idx = int(str(row.get("row_index", "")).strip())
                        col_idx = int(str(row.get("col_index", "")).strip())
                        filename = str(row.get("measurement_csv", "")).strip()
                        if row_idx > 0 and col_idx > 0 and filename:
                            manifest_index[(row_idx, col_idx)] = filename
            except Exception as manifest_exc:
                self.bridge.bc_log.emit(
                    f"C-Mode manifest parse warning: {manifest_exc}"
                )

        for axis2_idx in range(1, cross_steps + 1):
            for axis1_idx in range(1, scan_steps + 1):
                csv_name = manifest_index.get((axis2_idx, axis1_idx), "")
                csv_path = os.path.join(scan_folder, csv_name) if csv_name else ""
                if not csv_path or not os.path.exists(csv_path):
                    csv_path = os.path.join(
                        scan_folder, f"point_*_r{axis2_idx:03d}_c{axis1_idx:03d}.csv"
                    )
                    matches = sorted(Path(scan_folder).glob(os.path.basename(csv_path)))
                    if matches:
                        csv_path = str(matches[-1])
                    else:
                        csv_path = os.path.join(
                            scan_folder, f"row_{axis1_idx}_col_{axis2_idx}.csv"
                        )
                if not os.path.exists(csv_path):
                    missing_points += 1
                    continue
                try:
                    data = np.loadtxt(csv_path, delimiter=",", skiprows=1)
                except Exception:
                    missing_points += 1
                    continue

                arr = np.asarray(data, dtype=float)
                if arr.size == 0:
                    missing_points += 1
                    continue
                if arr.ndim == 1:
                    if arr.size < 2:
                        missing_points += 1
                        continue
                    arr = arr.reshape(1, -1)
                if arr.shape[1] < 2:
                    missing_points += 1
                    continue

                t = np.asarray(arr[:, 0], dtype=float)
                y = np.asarray(arr[:, 1], dtype=float)
                if t.size == 0 or y.size == 0:
                    missing_points += 1
                    continue

                envelope = np.asarray(
                    module.estimate_a_mode_signal(
                        t,
                        y,
                        highpass_cutoff_hz=cutoff_hz,
                        filter_order=filter_order,
                        sampling_rate_hz=sampling_rate_hz,
                    ),
                    dtype=float,
                )
                n = min(t.size, envelope.size)
                t = t[:n]
                envelope = envelope[:n]
                gated = envelope[(t >= gate_start_s) & (t <= gate_end_s)]

                r = axis2_idx - 1
                c = axis1_idx - 1
                gated_signal_map[r, c] = np.array(gated, copy=True)
                metric_map[r, c] = self._compute_pressure_field_metric(
                    gated, metric_name
                )
                loaded_points += 1

        if loaded_points == 0:
            self.bridge.bc_log.emit(
                "Apply ignored: no saved C-Mode A-scan CSV files were found for post-processing."
            )
            return

        self._bc_c_mode_cache = {
            "axis1_name": axis1_name,
            "axis2_name": axis2_name,
            "axis1_mm": np.array(axis1_mm, copy=True),
            "axis2_mm": np.array(axis2_mm, copy=True),
            "signal_map": np.array(gated_signal_map, copy=True),
        }

        self._render_bc_live_preview(
            {
                "mode": "c_mode_map",
                "axis1_name": axis1_name,
                "axis2_name": axis2_name,
                "axis1_mm": axis1_mm,
                "axis2_mm": axis2_mm,
                "metric_map": metric_map,
                "metric_name": metric_name,
                "colormap": colormap,
            }
        )
        self.bridge.bc_log.emit(
            f"Applied C-Mode post-processing from saved A-scans: loaded={loaded_points}, missing={missing_points}, "
            f"gate_start_us={self.bc_c_mode_gate_start.value():.1f}, gate_width_us={self.bc_c_mode_gate_width.value():.1f}, metric={metric_name}."
        )

    def _refresh_pressure_field_map_from_cache(self) -> bool:
        import numpy as np

        cache = getattr(self, "_bc_pressure_field_cache", None)
        if not cache:
            return False

        signal_map = np.asarray(cache.get("signal_map", []), dtype=object)
        if signal_map.size == 0:
            return False

        metric_name = self.bc_pf_mode_metric_combo.currentText().strip()
        metric_map = np.full(signal_map.shape, np.nan, dtype=float)
        for r in range(signal_map.shape[0]):
            for c in range(signal_map.shape[1]):
                sig = signal_map[r, c]
                if sig is None:
                    continue
                arr = np.asarray(sig, dtype=float)
                if arr.size == 0:
                    continue
                metric_map[r, c] = self._compute_pressure_field_metric(arr, metric_name)

        payload = {
            "mode": "pressure_field_map",
            "axis1_name": cache.get("axis1_name", "Axis 1"),
            "axis2_name": cache.get("axis2_name", "Axis 2"),
            "axis1_mm": np.asarray(cache.get("axis1_mm", []), dtype=float),
            "axis2_mm": np.asarray(cache.get("axis2_mm", []), dtype=float),
            "metric_map": metric_map,
            "metric_name": metric_name,
            "colormap": self.bc_pf_mode_cmap_combo.currentText().strip(),
        }
        self._render_bc_live_preview(payload)
        self.bridge.bc_log.emit(
            f"Pressure-field map updated: metric={metric_name}, colormap={self.bc_pf_mode_cmap_combo.currentText().strip()}."
        )
        return True

    def _refresh_c_mode_map_from_cache(self) -> bool:
        import numpy as np

        cache = getattr(self, "_bc_c_mode_cache", None)
        if not cache:
            return False

        signal_map = np.asarray(cache.get("signal_map", []), dtype=object)
        if signal_map.size == 0:
            return False

        metric_name = self.bc_c_mode_metric_combo.currentText().strip()
        metric_map = np.full(signal_map.shape, np.nan, dtype=float)
        for r in range(signal_map.shape[0]):
            for c in range(signal_map.shape[1]):
                sig = signal_map[r, c]
                if sig is None:
                    continue
                arr = np.asarray(sig, dtype=float)
                if arr.size == 0:
                    continue
                metric_map[r, c] = self._compute_pressure_field_metric(arr, metric_name)

        payload = {
            "mode": "c_mode_map",
            "axis1_name": cache.get("axis1_name", "Axis 1"),
            "axis2_name": cache.get("axis2_name", "Axis 2"),
            "axis1_mm": np.asarray(cache.get("axis1_mm", []), dtype=float),
            "axis2_mm": np.asarray(cache.get("axis2_mm", []), dtype=float),
            "metric_map": metric_map,
            "metric_name": metric_name,
            "colormap": self.bc_c_mode_cmap_combo.currentText().strip(),
        }
        self._render_bc_live_preview(payload)
        return True

    def _on_bc_pf_metric_changed(self, *_args) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        if self._current_bc_scan_type() != "pressure_field":
            return
        self._refresh_pressure_field_map_from_cache()

    def _on_bc_pf_colormap_changed(self, *_args) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        if self._current_bc_scan_type() != "pressure_field":
            return
        if self._refresh_pressure_field_map_from_cache():
            return
        cached = getattr(self, "_bc_pressure_field_payload", None)
        if not cached:
            return
        payload = dict(cached)
        payload["mode"] = "pressure_field_map"
        payload["colormap"] = self.bc_pf_mode_cmap_combo.currentText().strip()
        self._render_bc_live_preview(payload)

    def _on_bc_c_mode_metric_changed(self, *_args) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        if self._current_bc_scan_type() != "c_mode":
            return
        self._refresh_c_mode_map_from_cache()

    def _on_bc_c_mode_colormap_changed(self, *_args) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        if self._current_bc_scan_type() != "c_mode":
            return
        self._refresh_c_mode_map_from_cache()

    def _animate_bc_scan_type_indicator(self, animate: bool = True) -> None:
        if not hasattr(self, "bc_scan_type_group") or not hasattr(
            self, "bc_scan_type_indicator"
        ):
            return
        btn = self.bc_scan_type_group.checkedButton()
        if btn is None:
            return

        end_rect = QRect(btn.x(), btn.y(), btn.width(), btn.height())
        # Button hasn't been laid out yet (tab still hidden) — skip.
        if end_rect.width() == 0:
            return

        # Clamp to the segment frame's interior so the indicator never overflows.
        frame_w = self.bc_scan_type_segment.width()
        frame_h = self.bc_scan_type_segment.height()
        x = max(0, end_rect.x())
        y = max(0, end_rect.y())
        w = min(end_rect.width(), frame_w - x)
        h = min(end_rect.height(), frame_h - y)
        end_rect = QRect(x, y, w, h)
        radius = max(2, h // 2)
        self.bc_scan_type_indicator.setStyleSheet(
            f"QFrame#bcScanTypeIndicator {{ background: #d9c2ff; border: 1px solid #c9b1f7; border-radius: {radius}px; }}"
        )

        self._bc_indicator_ready = True
        if not animate:
            self.bc_scan_type_indicator.setGeometry(end_rect)
            self.bc_scan_type_indicator.show()
            return

        self.bc_scan_type_indicator.show()
        self._bc_scan_type_indicator_anim = QPropertyAnimation(
            self.bc_scan_type_indicator, b"geometry", self
        )
        self._bc_scan_type_indicator_anim.setDuration(180)
        self._bc_scan_type_indicator_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._bc_scan_type_indicator_anim.setStartValue(
            self.bc_scan_type_indicator.geometry()
        )
        self._bc_scan_type_indicator_anim.setEndValue(end_rect)
        self._bc_scan_type_indicator_anim.start()

    def eventFilter(self, obj, event) -> bool:
        if (
            hasattr(self, "bc_scan_type_segment")
            and obj is self.bc_scan_type_segment
            and event.type() == QEvent.Type.Resize
        ):
            QTimer.singleShot(
                0, lambda: self._animate_bc_scan_type_indicator(animate=False)
            )
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "bc_scan_type_indicator") and getattr(
            self, "_bc_indicator_ready", False
        ):
            QTimer.singleShot(
                0, lambda: self._animate_bc_scan_type_indicator(animate=False)
            )

    def _capture_a_mode_waveform(
        self,
        osc,
        frequency: float,
        amplitude: float,
        cycles: float,
        log_fn=None,
    ):
        import numpy as np

        burst_duration = max(float(cycles) / max(float(frequency), 1e-12), 1e-9)
        sampling_rate = max(
            (
                self._extract_last_float(
                    self.sampling_rate_edit.text().strip(),
                    max(float(frequency) * 100.0, 1e6) / 1000.0,
                )
                * 1000.0
            ),
            1.0,
        )
        capture_signature = (
            id(osc),
            str(self.osc_address_edit.text().strip()),
            round(float(frequency), 6),
            round(float(amplitude), 6),
            round(float(cycles), 6),
            round(float(sampling_rate), 3),
        )
        if (
            self._scope_capture_config_handle_id != id(osc)
            or self._scope_capture_config_signature != capture_signature
        ):
            def vbs(cmd: str) -> None:
                osc.write(f"VBS '{cmd}'")
                time.sleep(0.1)

            hor_scale = max(burst_duration, 1e-9)
            ver_scale = max(float(amplitude), 0.01)
            osc.write("COMM_HEADER OFF")
            osc.write("COMM_FORMAT DEF9,WORD,BIN")
            vbs(f"app.Acquisition.Horizontal.HorScale = {hor_scale}")
            vbs(f"app.Acquisition.C1.VerScale = {ver_scale}")
            vbs(f"app.Acquisition.Horizontal.SampleRate = {sampling_rate}")
            vbs("app.Acquisition.C1.View = true")
            vbs('app.Acquisition.Trigger.Source = "C1"')
            osc.write("TRIG_MODE NORM")
            self._scope_capture_config_handle_id = id(osc)
            self._scope_capture_config_signature = capture_signature
            if callable(log_fn):
                log_fn(
                    "A-mode: Applied scope capture configuration for current cached hardware/config signature."
                )

        import struct

        osc.write("C1:WF? ALL")
        raw = b""
        while True:
            try:
                chunk = osc.read_raw()
            except Exception as read_exc:
                if raw:
                    break
                raise RuntimeError(
                    f"Unable to read scope waveform for exact axis reconstruction: {read_exc}"
                ) from read_exc
            if not chunk:
                break
            raw += chunk

        wd_start = raw.find(b"WAVEDESC")
        if wd_start < 0:
            raise RuntimeError("LeCroy waveform descriptor was not found in the scope response.")

        desc_len = struct.unpack("<i", raw[wd_start + 36 : wd_start + 40])[0]
        text_len = struct.unpack("<i", raw[wd_start + 40 : wd_start + 44])[0]
        num_points = struct.unpack("<i", raw[wd_start + 60 : wd_start + 64])[0]
        v_gain = struct.unpack("<f", raw[wd_start + 156 : wd_start + 160])[0]
        v_off = struct.unpack("<f", raw[wd_start + 160 : wd_start + 164])[0]
        h_int = struct.unpack("<f", raw[wd_start + 176 : wd_start + 180])[0]
        h_off = struct.unpack("<d", raw[wd_start + 180 : wd_start + 188])[0]

        raw_data = osc.query_binary_values(
            "C1:WF? DAT1", datatype="h", container=np.array
        )
        volts = (raw_data * v_gain) - v_off
        num_points = len(raw_data)
        t = np.arange(num_points, dtype=float) * h_int + h_off
        actual_sampling_rate = 1.0 / h_int if h_int > 0.0 else float("nan")

        if callable(log_fn):
            log_fn(
                f"Scope waveform axis: points={num_points}, xzero={h_off:.9e} s, xincrement={h_int:.9e} s"
            )

        if callable(log_fn):
            req_msps = sampling_rate / 1e6
            act_msps = actual_sampling_rate / 1e6
            delta_pct = (
                abs(actual_sampling_rate - sampling_rate) / sampling_rate * 100.0
                if sampling_rate > 0.0
                else 0.0
            )
            log_fn(
                f"Scope sampling rate: requested={req_msps:.6f} MS/s, actual={act_msps:.6f} MS/s, points={num_points}, xzero={h_off:.9e} s, xincrement={h_int:.9e} s"
            )
            if delta_pct > 2.0:
                log_fn(
                    f"Scope sampling-rate deviation: {delta_pct:.2f}% (instrument constrained by timebase/memory settings)."
                )

        return t, volts, actual_sampling_rate

    def _render_a_mode_preview(self, payload: dict) -> None:
        import numpy as np

        mode = payload.get("mode", "live")
        t = np.asarray(payload.get("t", []))
        y = np.asarray(payload.get("y", []))
        y_first = np.asarray(payload.get("y_first", []))
        y_avg = np.asarray(payload.get("y_avg", []))
        y_mode = np.asarray(payload.get("y_mode", []))
        sound_speed_mps = float(self.a_mode_sound_speed.value())
        if sound_speed_mps > 0.0:
            x = t * sound_speed_mps * 1000.0
            x_label = "Distance (mm)"
        else:
            x = t * 1_000_000.0
            x_label = r"Time ($\mu$s)"

        self.a_preview_canvas.figure.clear()
        self.a_preview_canvas.axes = self.a_preview_canvas.figure.add_subplot(111)
        self.a_preview_canvas._style_axes()

        if mode == "live":
            pulse_idx = int(payload.get("pulse_idx", 1))
            pulse_total = int(payload.get("pulse_total", 1))
            self.a_preview_canvas.axes.plot(
                x, y, color="#2f80ed", linewidth=1.1, alpha=0.8, zorder=2
            )
            self.a_preview_canvas.axes.set_title(
                f"A-Mode Live Preview ({pulse_idx}/{pulse_total})", color="#1f2a37"
            )
        else:
            live_enabled = bool(payload.get("live_enabled", False))
            pulse_total = int(payload.get("pulse_total", 1))
            if live_enabled and y.size == y_avg.size and y.size > 0:
                if pulse_total > 1 and y_first.size == y.size:
                    self.a_preview_canvas.axes.plot(
                        x,
                        y_first,
                        color="#3a8d5c",
                        linewidth=1.0,
                        alpha=0.5,
                        zorder=2,
                        label="First echo",
                    )
                self.a_preview_canvas.axes.plot(
                    x,
                    y,
                    color="#2f80ed",
                    linewidth=1.0,
                    alpha=0.45,
                    zorder=2,
                    label="Last echo",
                )
                self.a_preview_canvas.axes.plot(
                    x,
                    y_avg,
                    color="#e04b3f",
                    linewidth=1.6,
                    alpha=0.9,
                    zorder=3,
                    label="Average",
                )
                if y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.fill_between(
                        x,
                        0.0,
                        y_mode,
                        color="#f7b801",
                        alpha=0.18,
                        zorder=1,
                    )
                    self.a_preview_canvas.axes.plot(
                        x,
                        y_mode,
                        color="#f59e0b",
                        linewidth=2.2,
                        linestyle="-",
                        zorder=4,
                        label="A-Mode envelope",
                    )
                self.a_preview_canvas.axes.legend(loc="best")
                self.a_preview_canvas.axes.set_title(
                    "A-Mode First/Last Echoes + Average", color="#1f2a37"
                )
            else:
                if pulse_total > 1 and y_first.size == y_avg.size and y_avg.size > 0:
                    self.a_preview_canvas.axes.plot(
                        x,
                        y_first,
                        color="#3a8d5c",
                        linewidth=1.0,
                        alpha=0.5,
                        zorder=2,
                        label="First echo",
                    )
                    self.a_preview_canvas.axes.plot(
                        x,
                        y,
                        color="#2f80ed",
                        linewidth=1.0,
                        alpha=0.45,
                        zorder=2,
                        label="Last echo",
                    )
                self.a_preview_canvas.axes.plot(
                    x,
                    y_avg,
                    color="#e04b3f",
                    linewidth=1.6,
                    alpha=0.9,
                    zorder=3,
                    label="Average",
                )
                if y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.fill_between(
                        x,
                        0.0,
                        y_mode,
                        color="#f7b801",
                        alpha=0.18,
                        zorder=1,
                    )
                    self.a_preview_canvas.axes.plot(
                        x,
                        y_mode,
                        color="#f59e0b",
                        linewidth=2.2,
                        linestyle="-",
                        zorder=4,
                        label="A-Mode envelope",
                    )
                if pulse_total > 1:
                    self.a_preview_canvas.axes.legend(loc="best")
                elif y_mode.size == y_avg.size and y_mode.size > 0:
                    self.a_preview_canvas.axes.legend(loc="best")
                self.a_preview_canvas.axes.set_title(
                    "A-Mode Average Echo", color="#1f2a37"
                )

            self.a_preview_canvas.axes.set_xlabel(x_label, color="#415368")
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

            detrended_traces = np.vstack(
                [self._detrend_signal(trace) for trace in traces]
            )
            pulse_total = int(detrended_traces.shape[0])
            if pulse_total > 1:
                avg = np.mean(detrended_traces, axis=0)
            else:
                avg = detrended_traces[0].copy()
            cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            filter_order = int(self.a_mode_filter_order.value())
            sampling_rate_hz = float(
                self._a_mode_last_results.get(
                    "filter_sampling_rate_hz",
                    self._extract_last_float(self.sampling_rate_edit.text().strip(), 0.0)
                    * 1000.0,
                )
            )
            effective_cutoff_hz = cutoff_hz
            if sampling_rate_hz > 0.0:
                nyquist_hz = 0.5 * sampling_rate_hz
                if cutoff_hz >= nyquist_hz:
                    self.bridge.a_mode_log.emit(
                        f"A-mode filtering ERROR: cutoff={cutoff_hz/1000.0:.3f} kHz is >= Nyquist={nyquist_hz/1000.0:.3f} kHz. High-pass filter is disabled."
                    )
                    self.bridge.a_mode_log.emit(
                        "A-mode filtering note: filtering is only meaningful when cutoff is much smaller than Nyquist frequency."
                    )
                    effective_cutoff_hz = 0.0
                elif cutoff_hz >= 0.8 * nyquist_hz:
                    self.bridge.a_mode_log.emit(
                        f"A-mode filtering WARNING: cutoff={cutoff_hz/1000.0:.3f} kHz is close to Nyquist={nyquist_hz/1000.0:.3f} kHz; filtering is meaningful when cutoff is much smaller than Nyquist."
                    )
            a_mode_signal = module.estimate_a_mode_signal(
                t_ref,
                avg,
                highpass_cutoff_hz=effective_cutoff_hz,
                filter_order=filter_order,
                sampling_rate_hz=sampling_rate_hz,
            )

            first = detrended_traces[0]
            last = detrended_traces[-1]
            live_enabled = bool(self._a_mode_last_results.get("live_enabled", True))

            self._a_mode_last_results["traces"] = detrended_traces
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
            str(DATA_DIR / "a_mode_matrix.txt"),
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
                f.write(
                    f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
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
            str(DATA_DIR / default_filename),
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

    def export_config_logs(self) -> None:
        self._export_log_widget_text(
            self.cfg_output,
            "Export Config Logs",
            "config_log.txt",
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
            str(DATA_DIR / suggested_name),
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
            "scan_algorithm": self.bc_scan_algorithm_combo.currentText(),
        }
        scan_step_mm = compute_step(scan["scan_length"], scan["scan_points"])
        cross_step_mm = compute_step(scan["cross_length"], scan["cross_points"])
        scan["scan_step"] = max(1, int(round(scan_step_mm * MM_TO_PULSE)))
        scan["cross_step"] = max(1, int(round(cross_step_mm * MM_TO_PULSE)))
        return pg, scan

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

    def _on_b_mode_normalize_toggled(self, value: int) -> None:
        if not self._b_mode_last_results:
            return
        show_normalized = bool(value)
        original = self._b_mode_last_results.get("image_original")
        normalized = self._b_mode_last_results.get("image_normalized")
        if show_normalized and normalized is None:
            self.bridge.b_mode_log.emit(
                "Normalization unavailable: cannot toggle because normalized image is not available."
            )
            self.b_normalize_check.blockSignals(True)
            self.b_normalize_check.setChecked(False)
            self.b_normalize_check.blockSignals(False)
            return

        image = normalized if show_normalized else original
        if image is None:
            return

        self._b_mode_show_normalized = show_normalized
        self._b_mode_last_results["image"] = image
        self._b_mode_last_results["normalized_display"] = show_normalized
        self.bridge.b_preview.emit(
            {
                "mode": "b_image_final",
                "x_axis": self._b_mode_last_results.get("x_axis"),
                "x_label": self._b_mode_last_results.get("x_label", "Depth (mm)"),
                "scan_mm": self._b_mode_last_results.get("scan_mm"),
                "image": image,
                "normalized_display": show_normalized,
                "can_toggle_normalize": normalized is not None,
                "pulse_total": int(self._b_mode_last_results.get("pulse_total", 1)),
                "live_enabled": bool(
                    self._b_mode_last_results.get("live_enabled", False)
                ),
            }
        )

    def _render_b_mode_preview(self, payload: dict) -> None:
        import numpy as np

        mode = payload.get("mode", "b_image")
        x_axis = np.asarray(payload.get("x_axis", []), dtype=float)
        scan_mm = np.asarray(payload.get("scan_mm", []), dtype=float)
        image = np.asarray(payload.get("image", []), dtype=float)
        x_label = str(payload.get("x_label", "Depth (mm)"))
        normalized_display = bool(payload.get("normalized_display", False))
        can_toggle_normalize = bool(payload.get("can_toggle_normalize", False))
        if x_axis.size == 0 or scan_mm.size == 0 or image.size == 0:
            self.b_preview_canvas.draw_placeholder("B-mode preview will appear here")
            return

        draw_image = np.array(image, copy=True)
        draw_image[~np.isfinite(draw_image)] = np.nan
        extent = [
            float(x_axis.min()),
            float(x_axis.max()),
            float(scan_mm.min()),
            float(scan_mm.max()),
        ]

        needs_reset = (
            self._b_mode_image_artist is None
            or self.b_preview_canvas.axes is None
            or self._b_mode_image_artist.get_array().shape != draw_image.shape
        )
        if needs_reset:
            self.b_preview_canvas.figure.clear()
            self.b_preview_canvas.axes = self.b_preview_canvas.figure.add_subplot(111)
            self.b_preview_canvas._style_axes()
            self._b_mode_image_artist = self.b_preview_canvas.axes.imshow(
                draw_image,
                cmap="gray",
                aspect="auto",
                interpolation="nearest",
                origin="lower",
                extent=extent,
                vmin=0.0 if normalized_display else None,
                vmax=1.0 if normalized_display else None,
            )
            self._b_mode_colorbar = self.b_preview_canvas.figure.colorbar(
                self._b_mode_image_artist,
                ax=self.b_preview_canvas.axes,
            )
            self._b_mode_colorbar.set_label(
                "Normalized intensity (16-bit)" if normalized_display else "Amplitude"
            )
        else:
            self._b_mode_image_artist.set_data(draw_image)
            self._b_mode_image_artist.set_extent(extent)
            if normalized_display:
                self._b_mode_image_artist.set_clim(0.0, 1.0)
            else:
                self._b_mode_image_artist.autoscale()
            if self._b_mode_colorbar is not None:
                self._b_mode_colorbar.update_normal(self._b_mode_image_artist)
                self._b_mode_colorbar.set_label(
                    "Normalized intensity (16-bit)"
                    if normalized_display
                    else "Amplitude"
                )

        if mode == "b_image_live":
            pulse_idx = int(payload.get("pulse_idx", 1))
            pulse_total = int(payload.get("pulse_total", image.shape[0]))
            self.b_preview_canvas.axes.set_title(
                f"B-Mode Live Preview ({pulse_idx}/{pulse_total})", color="#1f2a37"
            )
            self.b_normalize_check.blockSignals(True)
            self.b_normalize_check.setChecked(False)
            self.b_normalize_check.setEnabled(False)
            self.b_normalize_check.blockSignals(False)
        else:
            if normalized_display:
                self.b_preview_canvas.axes.set_title(
                    "B-Mode Image (Normalized 16-bit)", color="#1f2a37"
                )
            else:
                self.b_preview_canvas.axes.set_title("B-Mode Image", color="#1f2a37")
            self.b_normalize_check.blockSignals(True)
            self.b_normalize_check.setEnabled(can_toggle_normalize)
            self.b_normalize_check.setChecked(
                bool(normalized_display and can_toggle_normalize)
            )
            self.b_normalize_check.blockSignals(False)

        self.b_preview_canvas.axes.set_xlabel(x_label, color="#415368")
        self.b_preview_canvas.axes.set_ylabel("Scanning Axis (mm)", color="#415368")
        self.b_preview_canvas.draw_idle()

    def start_b_mode(self) -> None:
        self._save_settings_now()
        self._b_mode_last_results = None
        self._b_mode_show_normalized = False
        self.b_normalize_check.blockSignals(True)
        self.b_normalize_check.setChecked(False)
        self.b_normalize_check.setEnabled(False)
        self.b_normalize_check.blockSignals(False)
        self.b_output.clear()
        self.stop_event.clear()
        self.b_start_button.setEnabled(False)
        self.b_stop_button.setEnabled(True)
        threading.Thread(target=self._run_b_mode_worker, daemon=True).start()

    def stop_b_mode(self) -> None:
        self.stop_event.set()
        self.bridge.b_mode_log.emit(
            "Stop requested. Current capture/move will finish first."
        )

    def _run_b_mode_worker(self) -> None:
        script_path = BASE_DIR / "A scan.py"
        sg = None
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
            amplitude_vpp = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            prf_hz = float(self.tx_prf.value())
            window_fn = self.tx_windowing_combo.currentText()
            pulses_per_point = max(1, int(self.tx_pulses.value()))
            sampling_rate = (
                self._extract_last_float(
                    self.sampling_rate_edit.text().strip(),
                    max(frequency * 100.0, 1e6) / 1000.0,
                )
                * 1000.0
            )
            period_sec = 1.0 / max(prf_hz, 1e-12)
            cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            filter_order = int(self.a_mode_filter_order.value())

            if not dry_run:
                try:
                    pm = importlib.import_module("pymeasure.instruments.agilent")
                    sg_model = self.sg_name_edit.currentText().strip()
                    sg_lookup = {
                        "Agilent33500B": "Agilent33500",
                        "Agilent33521A": "Agilent33500",
                        "Agilent33220A": "Agilent33220A",
                    }.get(sg_model, sg_model)
                    sg_class = getattr(pm, sg_lookup, None)
                    if sg_class is None:
                        raise ImportError(f"Signal generator model '{sg_model}' not available in pymeasure.instruments.agilent")
                    from Signal_function import Burst_generate
                    import rig_function

                    sg_address = self.sg_address_edit.text().strip() or self._default_sg_address
                    osc_address = self.osc_address_edit.text().strip()
                    if not osc_address:
                        self.bridge.b_mode_log.emit("B-Mode: No oscilloscope VISA address configured. Please set it in the Config tab.")
                        return
                    retries = max(1, int(self.test_retries.value()))
                    retry_delay = min(0.3, float(self.test_timeout.value()))

                    was_sg_cached = self._get_stable_sg(sg_address) is not None
                    try:
                        sg, _opened_sg_now = self._acquire_cached_sg(
                            sg_address,
                            driver=sg_class,
                            retries=retries,
                            retry_delay=retry_delay,
                        )
                    except Exception as sg_exc:
                        self.bridge.b_mode_log.emit(
                            "B-Mode hardware not detected and Dry Run is off. "
                            "Enable Dry Run or connect hardware."
                        )
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Failed to connect to signal generator at {sg_address}: {sg_exc}"
                        )
                        return

                    if was_sg_cached:
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Using cached signal generator connection at {sg_address}."
                        )
                    else:
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Opened and cached signal generator connection at {sg_address}."
                        )

                    try:
                        was_osc_cached = self._get_stable_osc(osc_address) is not None
                        osc, _opened_osc_now = self._acquire_cached_osc(
                            osc_address,
                            retries=retries,
                            retry_delay=retry_delay,
                        )
                        if was_osc_cached:
                            self.bridge.b_mode_log.emit(
                                f"B-Mode: Using cached oscilloscope connection at {osc_address}."
                            )
                        else:
                            self.bridge.b_mode_log.emit(
                                f"B-Mode: Opened and cached oscilloscope connection at {osc_address}."
                            )
                    except Exception as osc_exc:
                        self.bridge.b_mode_log.emit(
                            "B-Mode hardware not detected and Dry Run is off. "
                            "Enable Dry Run or connect hardware."
                        )
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Failed to connect to oscilloscope at {osc_address}: {osc_exc}"
                        )
                        return

                    try:
                        idn = osc.query("*IDN?").strip()
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Oscilloscope connected ({idn})"
                        )
                    except Exception as idn_exc:
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Oscilloscope connected, but *IDN? failed: {idn_exc}"
                        )

                    rig_host = params["host"]
                    rig_port = params["port"]
                    self.bridge.b_mode_log.emit(
                        f"B-Mode: Connecting to rig at {rig_host}:{rig_port} for scanning."
                    )
                    try:
                        motion_sock = socket.create_connection(
                            (rig_host, rig_port),
                            timeout=float(self.test_timeout.value()),
                        )
                        rig_function.send_command(motion_sock, "INC")
                        rig_function.enable_axis(motion_sock, params["scan_axis"])
                    except Exception as rig_exc:
                        self.bridge.b_mode_log.emit(
                            f"B-Mode: Failed to connect to rig at {rig_host}:{rig_port}: {rig_exc}"
                        )
                        return
                except Exception as hw_exc:
                    self.bridge.b_mode_log.emit(
                        "B-Mode hardware not detected and Dry Run is off. "
                        "Enable Dry Run or connect hardware."
                    )
                    self.bridge.b_mode_log.emit(
                        f"B-Mode: Hardware detection error: {hw_exc}"
                    )
                    return

                b_data_dir = DATA_DIR
                b_data_dir.mkdir(exist_ok=True)
                existing = [
                    d
                    for d in os.listdir(b_data_dir)
                    if d.startswith("b_mode_scan_")
                    and os.path.isdir(b_data_dir / d)
                    and d.split("_")[-1].isdigit()
                ]
                next_idx = max((int(d.split("_")[-1]) for d in existing), default=0) + 1
                b_scan_folder = b_data_dir / f"b_mode_scan_{next_idx:03d}"
                b_scan_folder.mkdir(parents=True, exist_ok=True)
            else:
                Burst_generate = None
                b_data_dir = DATA_DIR
                b_data_dir.mkdir(exist_ok=True)
                existing = [
                    d
                    for d in os.listdir(b_data_dir)
                    if d.startswith("b_mode_scan_")
                    and os.path.isdir(b_data_dir / d)
                    and d.split("_")[-1].isdigit()
                ]
                next_idx = max((int(d.split("_")[-1]) for d in existing), default=0) + 1
                b_scan_folder = b_data_dir / f"b_mode_scan_{next_idx:03d}"
                b_scan_folder.mkdir(parents=True, exist_ok=True)

            self.bridge.b_mode_log.emit(
                f"B-Mode settings: scan_axis={params['scan_axis']}, depth_axis={params['depth_axis']}, "
                f"scan_points={points}, scan_step={scan_step}, sound_speed={sound_speed_mps} m/s, "
                f"dry_run={dry_run}, live_preview={live_enabled}, "
                f"echo_averages={pulses_per_point}, "
                f"line_period={period_sec:.6f} s"
            )

            scan_mm = np.linspace(0.0, float(params["scan_length"]), points)
            x_axis = None
            x_label = "Depth (mm)" if sound_speed_mps > 0 else r"Time ($\mu$s)"
            b_image = None
            for idx in range(1, points + 1):
                cycle_start = time.perf_counter()
                if self.stop_event.is_set():
                    self.bridge.b_mode_log.emit("B-Mode scan stopped by user.")
                    break
                t_echoes = []
                y_echoes = []
                scope_sampling_rates = []
                for pulse_idx in range(1, pulses_per_point + 1):
                    if dry_run:
                        t_one, y_one = module.generate_test_echo(
                            frequency_hz=frequency,
                            amplitude_v=amplitude_vpp,
                            no_of_cycles_per_pulse=cycles,
                            window_type=window_fn,
                            sampling_rate_hz=sampling_rate,
                        )
                    else:
                        Burst_generate(
                            sg,
                            shape="SIN",
                            frequency=frequency,
                            amplitude=amplitude_vpp,
                            no_of_cycles_per_pulse=cycles,
                            no_of_pulses=1,
                            prf=prf_hz,
                            window_type=window_fn,
                        )
                        t_one, y_one, _scope_fs_hz = self._capture_a_mode_waveform(
                            osc,
                            frequency,
                            amplitude_vpp,
                            cycles,
                            log_fn=self.bridge.b_mode_log.emit,
                        )
                        if _scope_fs_hz is not None and np.isfinite(_scope_fs_hz) and _scope_fs_hz > 0.0:
                            scope_sampling_rates.append(float(_scope_fs_hz))
                    t_echoes.append(np.asarray(t_one, dtype=float))
                    y_echoes.append(np.asarray(y_one, dtype=float))
                    self.bridge.b_mode_log.emit(
                        f"Captured echo {pulse_idx}/{pulses_per_point} at point {idx}/{points}"
                    )

                if not y_echoes:
                    raise RuntimeError(f"No echoes captured for B-Mode point {idx}.")

                min_len = min(e.size for e in y_echoes)
                t = t_echoes[0][:min_len]
                stack = np.vstack(
                    [self._detrend_signal(e[:min_len]) for e in y_echoes]
                )
                if pulses_per_point > 1:
                    y = np.mean(stack, axis=0)
                else:
                    y = stack[0].copy()

                config_sampling_rate_hz = float(sampling_rate)
                if scope_sampling_rates:
                    filter_sampling_rate_hz = float(
                        np.median(np.asarray(scope_sampling_rates, dtype=float))
                    )
                    pct = (
                        abs(filter_sampling_rate_hz - config_sampling_rate_hz)
                        / max(config_sampling_rate_hz, 1e-12)
                        * 100.0
                    )
                    self.bridge.b_mode_log.emit(
                        f"B-mode filtering: Config sampling rate={config_sampling_rate_hz/1e6:.6f} MS/s, scope measured sampling rate={filter_sampling_rate_hz/1e6:.6f} MS/s (delta={pct:.2f}%)."
                    )
                    self.bridge.b_mode_log.emit(
                        f"B-mode filtering: fs={filter_sampling_rate_hz/1e6:.6f} MS/s (scope actual) is used for Nyquist/cutoff calculations."
                    )
                else:
                    filter_sampling_rate_hz = config_sampling_rate_hz
                    self.bridge.b_mode_log.emit(
                        f"B-mode filtering: scope sampling rate unavailable; fs={filter_sampling_rate_hz/1e6:.6f} MS/s (Config) is used for Nyquist/cutoff calculations."
                    )

                effective_cutoff_hz = cutoff_hz
                if filter_sampling_rate_hz > 0.0:
                    nyquist_hz = 0.5 * filter_sampling_rate_hz
                    if cutoff_hz >= nyquist_hz:
                        self.bridge.b_mode_log.emit(
                            f"B-mode filtering ERROR: cutoff={cutoff_hz/1000.0:.3f} kHz is >= Nyquist={nyquist_hz/1000.0:.3f} kHz. High-pass filter is disabled."
                        )
                        self.bridge.b_mode_log.emit(
                            "B-mode filtering note: filtering is only meaningful when cutoff is much smaller than Nyquist frequency."
                        )
                        effective_cutoff_hz = 0.0
                    elif cutoff_hz >= 0.8 * nyquist_hz:
                        self.bridge.b_mode_log.emit(
                            f"B-mode filtering WARNING: cutoff={cutoff_hz/1000.0:.3f} kHz is close to Nyquist={nyquist_hz/1000.0:.3f} kHz; filtering is meaningful when cutoff is much smaller than Nyquist."
                        )

                point_csv_path = b_scan_folder / f"point_{idx:04d}.csv"
                with open(point_csv_path, "w", encoding="utf-8") as _f:
                    _f.write("Time (s),Amplitude (V)\n")
                    for _t, _v in zip(t, y):
                        _f.write(f"{_t:.10e},{_v:.10e}\n")

                envelope = module.estimate_a_mode_signal(
                    t,
                    y,
                    highpass_cutoff_hz=effective_cutoff_hz,
                    filter_order=filter_order,
                    sampling_rate_hz=filter_sampling_rate_hz,
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
                            "line_idx": idx - 1,
                            "pulse_idx": idx,
                            "pulse_total": points,
                        }
                    )

                if idx < points and not dry_run and rig_function and motion_sock:
                    rig_function.send_command(
                        motion_sock, f"{params['scan_axis']}{scan_step}"
                    )
                    rig_function.wait_until_stopped(motion_sock, params["scan_axis"])

                if idx < points and period_sec > 0:
                    remaining = period_sec - (time.perf_counter() - cycle_start)
                    if remaining > 0:
                        time.sleep(remaining)

            if x_axis is None or b_image is None:
                raise RuntimeError("No B-mode echoes captured.")

            original_image = np.array(b_image, copy=True)
            normalized_image = None
            finite = np.isfinite(original_image)
            if np.any(finite):
                max_val = float(np.nanmax(original_image[finite]))
                if max_val > 0.0:
                    scaled = np.zeros_like(original_image, dtype=float)
                    scaled[finite] = np.clip(original_image[finite] / max_val, 0.0, 1.0)
                    # Quantize to 16-bit grayscale levels, then map back to [0, 1] for imshow.
                    quantized = np.round(scaled * 65535.0).astype(np.uint16)
                    normalized_image = quantized.astype(float) / 65535.0
                    normalized_image[~finite] = np.nan
                    self.bridge.b_mode_log.emit(
                        f"Normalized image prepared using max={max_val:.6e} (16-bit grayscale)."
                    )
                else:
                    self.bridge.b_mode_log.emit(
                        "Normalized image unavailable: maximum pixel value is not positive."
                    )
            else:
                self.bridge.b_mode_log.emit(
                    "Normalized image unavailable: image contains no finite pixels."
                )

            self._b_mode_last_results = {
                "x_axis": x_axis,
                "x_label": x_label,
                "scan_mm": scan_mm,
                "image": original_image,
                "image_original": original_image,
                "image_normalized": normalized_image,
                "live_enabled": live_enabled,
                "normalized_display": False,
                "pulse_total": points,
            }
            self.bridge.b_preview.emit(
                {
                    "mode": "b_image_final",
                    "x_axis": x_axis,
                    "x_label": x_label,
                    "scan_mm": scan_mm,
                    "image": original_image,
                    "normalized_display": False,
                    "can_toggle_normalize": normalized_image is not None,
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
            str(DATA_DIR / "b_mode_matrix.txt"),
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
                f.write(
                    f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                header = ["ScanAxis_mm"]
                prefix = "Depth_mm" if x_label == "Depth (mm)" else "Time_us"
                header.extend(f"{prefix}_{value:.6f}" for value in x_axis)
                f.write(",".join(header) + "\n")

                for row_idx in range(len(scan_mm)):
                    row = [f"{scan_mm[row_idx]:.9e}"]
                    row.extend(
                        f"{image[row_idx, col_idx]:.9e}"
                        for col_idx in range(image.shape[1])
                    )
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
            return False, "Pulse Repetition Frequency must be greater than 0 Hz."

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
            from Signal_function import _build_windowed_sine_waveform

            shape = "SIN"
            window_fn = self.tx_windowing_combo.currentText()
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude_vpp = float(self.tx_amp.value())
            amplitude_peak = 0.5 * amplitude_vpp
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())
            frequency_khz = frequency / 1000.0

            pulse_width_sec = cycles / frequency
            period_sec = 1.0 / prf_hz
            total_duration_sec = pulses * period_sec
            preview_sampling_rate_hz = max(40.0 * frequency, 1.0)

            # Build the pulse on the same kind of sample grid used for ARB upload so
            # preview timing matches the actual generated waveform.
            n_points = max(2, int(np.ceil(total_duration_sec * preview_sampling_rate_hz)))
            t_sec = np.linspace(0.0, total_duration_sec, n_points, endpoint=False)
            y = np.zeros_like(t_sec)
            pulse_samples = max(64, int(np.ceil(pulse_width_sec * preview_sampling_rate_hz)))
            pulse_waveform = amplitude_peak * _build_windowed_sine_waveform(
                no_of_cycles_per_pulse=cycles,
                window_type=window_fn,
                sample_count=pulse_samples,
            )
            pulse_offsets = np.arange(pulses, dtype=float) * period_sec
            for pulse_offset_sec in pulse_offsets:
                start_idx = int(round(pulse_offset_sec * preview_sampling_rate_hz))
                end_idx = min(start_idx + pulse_samples, y.size)
                if end_idx > start_idx:
                    y[start_idx:end_idx] = pulse_waveform[: end_idx - start_idx]
            t_us = t_sec * 1e6

            self.tx_preview_canvas.figure.clear()
            self.tx_preview_canvas.axes = self.tx_preview_canvas.figure.add_subplot(111)
            self.tx_preview_canvas._style_axes()
            self.tx_preview_canvas.axes.plot(t_us, y, color="#2f80ed", linewidth=1.4)
            self.tx_preview_canvas.axes.set_title("Excitation Preview", color="#1f2a37")
            self.tx_preview_canvas.axes.set_xlabel(r"Time ($\mu$s)", color="#415368")
            self.tx_preview_canvas.axes.set_ylabel("Voltage (V)", color="#415368")
            self.tx_preview_canvas.draw_idle()
            if log_update:
                self.bridge.tx_log.emit(
                    f"Preview updated: shape={shape}, window={window_fn}, frequency={frequency_khz} kHz, amplitude={amplitude_vpp} Vpp, cycles/pulse={cycles}, pulses={pulses}, PRF={prf_hz} Hz, preview_sampling_rate={self._format_hz_for_log(preview_sampling_rate_hz)}"
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
            from Signal_function import _build_windowed_sine_waveform

            window_fn = self.tx_windowing_combo.currentText()
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude_vpp = float(self.tx_amp.value())
            amplitude_peak = 0.5 * amplitude_vpp
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())

            pulse_width_sec = cycles / frequency
            period_sec = 1.0 / prf_hz
            total_duration_sec = pulses * period_sec
            preview_sampling_rate_hz = max(40.0 * frequency, 1.0)
            n_points = max(2, int(np.ceil(total_duration_sec * preview_sampling_rate_hz)))
            t_sec = np.linspace(0.0, total_duration_sec, n_points, endpoint=False)
            y = np.zeros_like(t_sec)
            pulse_samples = max(64, int(np.ceil(pulse_width_sec * preview_sampling_rate_hz)))
            pulse_waveform = amplitude_peak * _build_windowed_sine_waveform(
                no_of_cycles_per_pulse=cycles,
                window_type=window_fn,
                sample_count=pulse_samples,
            )
            pulse_offsets = np.arange(pulses, dtype=float) * period_sec
            for pulse_offset_sec in pulse_offsets:
                start_idx = int(round(pulse_offset_sec * preview_sampling_rate_hz))
                end_idx = min(start_idx + pulse_samples, y.size)
                if end_idx > start_idx:
                    y[start_idx:end_idx] = pulse_waveform[: end_idx - start_idx]
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
                f.write(
                    f"# Exported at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write("Time_us,Voltage_V\n")
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
                rig_function.move_to_position(sock, x=x_p, y=y_p, z=z_p, log_func=self.bridge.move_log.emit)
                with self._saved_position_lock:
                    self._session_move_delta = tuple(
                        int(current + delta)
                        for current, delta in zip(self._session_move_delta, (x_p, y_p, z_p))
                    )
                self.bridge.move_log.emit("Move complete.")
        except Exception as exc:
            self.bridge.move_log.emit(f"Move failed: {exc}")

    def save_current_position(self) -> None:
        threading.Thread(target=self._save_current_position_worker, daemon=True).start()

    def _save_current_position_worker(self) -> None:
        try:
            import rig_function

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host_edit.text().strip(), self._port_value()))
                self.bridge.move_log.emit("Connected to rig.")
                saved_samples = [rig_function.get_position(sock, axis) for axis in ("X", "Y", "Z")]
                saved_position = tuple(int(sample[0]) for sample in saved_samples)
                self.bridge.saved_rig_position_ready.emit(
                    {
                        "position": saved_position,
                    }
                )
                self.bridge.move_log.emit(
                    f"Saved current position: encoder={saved_position}"
                )
        except Exception as exc:
            self.bridge.move_log.emit(f"Save position failed: {exc}")

    def return_to_saved_position(self) -> None:
        if self._saved_rig_position is None:
            self.bridge.move_log.emit("No saved position available.")
            return

        threading.Thread(target=self._return_to_saved_position_worker, daemon=True).start()

    def _return_to_saved_position_worker(self) -> None:
        try:
            import rig_function

            with self._saved_position_lock:
                saved_position = self._saved_rig_position
                session_move_delta = self._session_move_delta

            if saved_position is None:
                self.bridge.move_log.emit("No saved position available.")
                return

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host_edit.text().strip(), self._port_value()))
                self.bridge.move_log.emit("Connected to rig.")

                current_samples = [
                    rig_function.get_position(sock, axis) for axis in ("X", "Y", "Z")
                ]
                current_position = tuple(int(sample[0]) for sample in current_samples)
                current_pulse_position = tuple(int(sample[1]) for sample in current_samples)

                self.bridge.move_log.emit(
                    f"Current position before return: encoder={current_position}, pulse={current_pulse_position}"
                )

                if session_move_delta == (0, 0, 0):
                    self.bridge.move_log.emit("Already at the saved position for this session.")
                    return

                correction = tuple(-delta for delta in session_move_delta)
                self.bridge.move_log.emit(
                    f"Returning by undoing session delta: ΔX={correction[0]}, ΔY={correction[1]}, ΔZ={correction[2]}"
                )
                rig_function.move_to_position(
                    sock,
                    x=correction[0],
                    y=correction[1],
                    z=correction[2],
                    log_func=self.bridge.move_log.emit,
                )

                with self._saved_position_lock:
                    self._session_move_delta = (0, 0, 0)

                after_samples = [
                    rig_function.get_position(sock, axis) for axis in ("X", "Y", "Z")
                ]
                after_position = tuple(int(sample[0]) for sample in after_samples)
                after_pulse_position = tuple(int(sample[1]) for sample in after_samples)
                self.bridge.move_log.emit(
                    f"Current position after return: encoder={after_position}, pulse={after_pulse_position}"
                )
                self.bridge.move_log.emit("Returned to saved position.")
        except Exception as exc:
            self.bridge.move_log.emit(f"Return to saved position failed: {exc}")

    def test_connections(self) -> None:
        self.cfg_output.clear()
        self.bridge.test_busy.emit(True)
        threading.Thread(target=self._test_connections_worker, daemon=True).start()

    def _test_connections_worker(self) -> None:
        sg_addr = self.sg_address_edit.text().strip() or self._default_sg_address
        retries = self.test_retries.value()
        timeout = float(self.test_timeout.value())
        if not sg_addr:
            self.bridge.cfg_log.emit("Signal generator: No address configured")
        else:
            try:
                import importlib
                pm = importlib.import_module("pymeasure.instruments.agilent")
                sg_model = self.sg_name_edit.currentText().strip()
                sg_lookup = {
                    "Agilent33500B": "Agilent33500",
                    "Agilent33521A": "Agilent33500",
                    "Agilent33220A": "Agilent33220A",
                }.get(sg_model, sg_model)
                driver = getattr(pm, sg_lookup, None)
                if driver is None:
                    raise ImportError(f"Signal generator model '{sg_model}' not available in pymeasure.instruments.agilent")
            except Exception as exc:
                self.bridge.cfg_log.emit(
                    f"Signal generator: driver unavailable ({exc})"
                )
                driver = None
            if driver is not None:
                with self._sg_use_lock:
                    stable = self._get_stable_sg(sg_addr)
                    if stable is not None:
                        self.bridge.cfg_log.emit(
                            f"Signal generator: Connected at {sg_addr} (stable cached session)"
                        )
                    else:
                        try:
                            self._acquire_cached_sg(
                                sg_addr,
                                driver=driver,
                                retries=retries,
                                retry_delay=min(0.3, timeout),
                            )
                            self.bridge.cfg_log.emit(
                                f"Signal generator: Connected at {sg_addr}"
                            )
                            self.bridge.cfg_log.emit(
                                "Signal generator: Stable session cached for subsequent operations."
                            )
                        except Exception as exc:
                            self._clear_stable_sg()
                            self.bridge.cfg_log.emit(
                                f"Signal generator: Failed to open {sg_addr}: {exc}"
                            )
        osc_addr = self.osc_address_edit.text().strip()
        try:
            if not osc_addr:
                self.bridge.cfg_log.emit("Oscilloscope: No VISA address configured")
            else:
                stable_osc = self._get_stable_osc(osc_addr)
                if stable_osc is not None:
                    try:
                        idn = stable_osc.query("*IDN?").strip()
                        self.bridge.cfg_log.emit(
                            f"Oscilloscope: Connected at {osc_addr} (stable cached session, {idn})"
                        )
                    except Exception:
                        self._clear_stable_osc()
                        stable_osc = None

                if stable_osc is None:
                    try:
                        osc, opened_now = self._acquire_cached_osc(
                            osc_addr,
                            retries=retries,
                            retry_delay=min(0.3, timeout),
                        )
                        idn = osc.query("*IDN?").strip()
                        self.bridge.cfg_log.emit(f"Oscilloscope: Connected at {osc_addr} ({idn})")
                        if opened_now:
                            self.bridge.cfg_log.emit(
                                "Oscilloscope: Stable session cached for subsequent operations."
                            )
                    except Exception as exc:
                        self._clear_stable_osc()
                        self.bridge.cfg_log.emit(f"Oscilloscope: Failed to open {osc_addr}: {exc}")
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
        sg = None
        close_after_use = True
        try:
            test_script = BASE_DIR / "test_prf_timing.py"
            spec = importlib.util.spec_from_file_location(
                "test_prf_timing", test_script
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load test_prf_timing.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            from Signal_function import Burst_generate, describe_burst_generation

            # Always use the config page value, fallback only if empty
            sg_address = self.sg_address_edit.text().strip() or self._default_sg_address
            retries = max(1, int(self.test_retries.value()))
            timeout = float(self.test_timeout.value())

            with self._sg_use_lock:
                stable = self._get_stable_sg(sg_address)
                if stable is not None:
                    sg = stable
                    close_after_use = False
                    self.bridge.tx_log.emit("Connected to signal generator (stable cached session).")
                else:
                    last_err = None
                    for _ in range(retries):
                        try:
                            sg = module.connect_signal_generator(sg_address)
                            last_err = None
                            break
                        except Exception as conn_exc:
                            last_err = conn_exc
                            time.sleep(min(0.3, timeout))
                    if sg is None:
                        raise RuntimeError(
                            f"Unable to connect to signal generator at {sg_address}: {last_err}"
                        )
                    self.bridge.tx_log.emit("Connected to signal generator.")

            shape = "SIN"
            frequency = float(self.tx_freq.value()) * 1000.0
            amplitude = float(self.tx_amp.value())
            cycles = float(self.tx_cycles.value())
            pulses = int(self.tx_pulses.value())
            prf_hz = float(self.tx_prf.value())
            window_fn = self.tx_windowing_combo.currentText()
            frequency_khz = frequency / 1000.0
            live_preview = bool(self.tx_auto_preview_check.isChecked())

            self.bridge.tx_log.emit(
                f"Excitation settings: shape={shape}, window={window_fn}, "
                f"frequency={frequency_khz} kHz, amplitude={amplitude} Vpp, "
                f"cycles/pulse={cycles}, pulses={pulses}, PRF={prf_hz} Hz, "
                f"live_preview={live_preview}"
            )
            generation = describe_burst_generation(shape, frequency, cycles, window_fn)
            hw_sampling_rate = self._format_hz_for_log(generation["sampling_rate_hz"])
            self.bridge.tx_log.emit(
                f"Hardware generation: mode={generation['mode']}, sampling_rate={hw_sampling_rate}"
            )
            with self._sg_use_lock:
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
        except Exception as exc:
            self.bridge.tx_log.emit(f"Transmit failed: {exc}")
            if not close_after_use:
                self._clear_stable_sg()
        finally:
            if close_after_use:
                self._close_sg_handle(sg)
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
        close_after_use = True
        try:
            from Signal_function import describe_burst_generation

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
            live_preview = bool(self.tx_auto_preview_check.isChecked())
            generation = describe_burst_generation(
                shape, frequency, cycles, window_fn
            )
            hw_sampling_rate = self._format_hz_for_log(generation["sampling_rate_hz"])

            sg_address = self.sg_address_edit.text().strip() or self._default_sg_address
            with self._sg_use_lock:
                stable = self._get_stable_sg(sg_address)
                if stable is not None:
                    sg = stable
                    close_after_use = False
                    self.bridge.tx_log.emit("Timing test: connected to signal generator (stable cached session).")
                else:
                    sg = module.connect_signal_generator(sg_address)
                    self.bridge.tx_log.emit("Timing test: connected to signal generator.")
            self.bridge.tx_log.emit(
                f"Timing test settings: shape={shape}, window={window_fn}, frequency={frequency_khz} kHz, "
                f"cycles/pulse={cycles}, pulses={pulses}, PRF={prf_hz} Hz, amplitude={amplitude} Vpp, tolerance={tolerance_percent}%, "
                f"live_preview={live_preview}"
            )
            self.bridge.tx_log.emit(
                f"Hardware generation: mode={generation['mode']}, sampling_rate={hw_sampling_rate}"
            )

            with self._sg_use_lock:
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
                f"Timing deviation: max_abs={result.max_abs_error_s:.9f} s, max_allowed={tolerance_s:.9f} s"
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
            if not close_after_use:
                self._clear_stable_sg()
        finally:
            if close_after_use:
                self._close_sg_handle(sg)
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
        osc = None
        motion_sock = None
        try:
            import numpy as np
            import importlib

            spec = importlib.util.spec_from_file_location("a_scan", script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            use_dummy = bool(self.a_dry_run_check.isChecked())
            if use_dummy:
                self.bridge.a_mode_log.emit(
                    "A-mode test mode: using dummy signal generator echoes."
                )
                Burst_generate = None
            else:
                try:
                    pm = importlib.import_module("pymeasure.instruments.agilent")
                    sg_model = self.sg_name_edit.currentText().strip()
                    sg_lookup = {
                        "Agilent33500B": "Agilent33500",
                        "Agilent33521A": "Agilent33500",
                        "Agilent33220A": "Agilent33220A",
                    }.get(sg_model, sg_model)
                    sg_class = getattr(pm, sg_lookup, None)
                    if sg_class is None:
                        raise ImportError(f"Signal generator model '{sg_model}' not available in pymeasure.instruments.agilent")
                    from Signal_function import Burst_generate

                    sg_address = self.sg_address_edit.text().strip() or self._default_sg_address
                    osc_address = self.osc_address_edit.text().strip()
                    if not osc_address:
                        self.bridge.a_mode_log.emit("A-mode: No oscilloscope VISA address configured. Please set it in the Config tab.")
                        return
                    retries = max(1, int(self.test_retries.value()))
                    retry_delay = min(0.3, float(self.test_timeout.value()))

                    was_sg_cached = self._get_stable_sg(sg_address) is not None
                    try:
                        sg, _opened_sg_now = self._acquire_cached_sg(
                            sg_address,
                            driver=sg_class,
                            retries=retries,
                            retry_delay=retry_delay,
                        )
                    except Exception as sg_exc:
                        self.bridge.a_mode_log.emit(
                            "A-mode hardware not detected and Dry Run is off. "
                            "Enable Dry Run or connect hardware."
                        )
                        self.bridge.a_mode_log.emit(
                            f"A-mode: Failed to connect to signal generator at {sg_address}: {sg_exc}"
                        )
                        return

                    if was_sg_cached:
                        self.bridge.a_mode_log.emit(
                            f"A-mode: Using cached signal generator connection at {sg_address}."
                        )
                    else:
                        self.bridge.a_mode_log.emit(
                            f"A-mode: Opened and cached signal generator connection at {sg_address}."
                        )

                    try:
                        was_cached = self._get_stable_osc(osc_address) is not None
                        osc, _opened_now = self._acquire_cached_osc(
                            osc_address,
                            retries=retries,
                            retry_delay=retry_delay,
                        )
                        if was_cached:
                            self.bridge.a_mode_log.emit(
                                f"A-mode: Using cached oscilloscope connection at {osc_address}."
                            )
                        else:
                            self.bridge.a_mode_log.emit(
                                f"A-mode: Opened and cached oscilloscope connection at {osc_address}."
                            )
                    except Exception as hw_exc:
                        self.bridge.a_mode_log.emit(
                            "A-mode hardware not detected and Dry Run is off. "
                            "Enable Dry Run or connect hardware."
                        )
                        self.bridge.a_mode_log.emit(f"A-mode: Failed to connect to oscilloscope at {osc_address}: {hw_exc}")
                        return
                    # Optionally, try a simple *IDN? query to verify communication
                    try:
                        idn = osc.query("*IDN?").strip()
                        self.bridge.a_mode_log.emit(f"A-mode: Oscilloscope connected ({idn})")
                    except Exception as idn_exc:
                        self.bridge.a_mode_log.emit(f"A-mode: Oscilloscope connected, but *IDN? failed: {idn_exc}")
                except Exception as hw_exc:
                    self.bridge.a_mode_log.emit(
                        "A-mode hardware not detected and Dry Run is off. "
                        "Enable Dry Run or connect hardware."
                    )
                    self.bridge.a_mode_log.emit(f"A-mode: Hardware detection error: {hw_exc}")
                    return

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
            sampling_rate = (
                self._extract_last_float(
                    self.sampling_rate_edit.text().strip(),
                    max(frequency * 100.0, 1e6) / 1000.0,
                )
                * 1000.0
            )
            live_enabled = bool(self.a_live_preview_check.isChecked())
            cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            cutoff_khz = cutoff_hz / 1000.0
            filter_order = int(self.a_mode_filter_order.value())
            frequency_khz = frequency / 1000.0
            sampling_rate_khz = sampling_rate / 1000.0

            self.bridge.a_mode_log.emit(
                f"A-mode settings: mode={params['mode']}, X={params['X']} mm, Y={params['Y']} mm, Z={params['Z']} mm, "
                f"frequency={frequency_khz} kHz, amplitude={amplitude} V, cycles/pulse={cycles}, pulses={pulses}, "
                f"PRF={prf_hz} Hz, window={window_fn}, sampling_rate={sampling_rate_khz} kHz, "
                f"highpass_cutoff={cutoff_khz} kHz, filter_order={filter_order}, live_preview={live_enabled}, "
                f"signal_source={'dummy' if use_dummy else 'hardware'}"
            )

            a_data_dir = DATA_DIR
            a_data_dir.mkdir(exist_ok=True)
            existing_runs = [
                d
                for d in os.listdir(a_data_dir)
                if d.startswith("a_mode_scan_")
                and os.path.isdir(a_data_dir / d)
                and d.split("_")[-1].isdigit()
            ]
            next_run_id = max((int(d.split("_")[-1]) for d in existing_runs), default=0) + 1
            a_scan_folder = a_data_dir / f"a_mode_scan_{next_run_id:03d}"
            a_scan_folder.mkdir(parents=True, exist_ok=True)
            self.bridge.a_mode_log.emit(f"A-mode run folder: {a_scan_folder}")

            run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            a_scan_metadata = {
                "run_timestamp": run_timestamp,
                "run_id": next_run_id,
                "mode": params["mode"],
                "x_mm": params["X"],
                "y_mm": params["Y"],
                "z_mm": params["Z"],
                "frequency_hz": frequency,
                "amplitude_v": amplitude,
                "cycles_per_pulse": cycles,
                "pulse_count": pulses,
                "prf_hz": prf_hz,
                "window": window_fn,
                "sampling_rate_hz": sampling_rate,
                "highpass_cutoff_hz": cutoff_hz,
                "filter_order": filter_order,
                "live_preview": live_enabled,
                "signal_source": "dummy" if use_dummy else "hardware",
            }

            def _write_wave_csv(
                output_path: Path,
                time_axis: object,
                amplitude_axis: object,
                extra_metadata: dict | None = None,
            ) -> None:
                metadata = dict(a_scan_metadata)
                if extra_metadata:
                    metadata.update(extra_metadata)
                with open(output_path, "w", encoding="utf-8", newline="") as csv_file:
                    for key, value in metadata.items():
                        csv_file.write(f"# {key}: {value}\n")
                    writer = csv.writer(csv_file)
                    writer.writerow(["Time_s", "Amplitude_V"])
                    writer.writerows(zip(time_axis, amplitude_axis))

            # Move rig once per A-mode run in hardware mode.
            if not use_dummy:
                x_p = module.mm_to_pulse(params["X"])
                y_p = module.mm_to_pulse(params["Y"])
                z_p = module.mm_to_pulse(params["Z"])
                if any(value != 0 for value in (x_p, y_p, z_p)):
                    rig_host = self.host_edit.text().strip()
                    rig_port = self._port_value()
                    self.bridge.a_mode_log.emit(
                        f"A-mode: Connecting to rig at {rig_host}:{rig_port} for positioning."
                    )
                    try:
                        motion_sock = socket.create_connection(
                            (rig_host, rig_port),
                            timeout=float(self.test_timeout.value()),
                        )
                        module.move(motion_sock, x=x_p, y=y_p, z=z_p)
                    except Exception as rig_exc:
                        self.bridge.a_mode_log.emit(
                            f"A-mode: Failed to connect to rig at {rig_host}:{rig_port}: {rig_exc}"
                        )
                        return
                else:
                    self.bridge.a_mode_log.emit(
                        "A-mode: Requested position is zero-offset; skipping rig connection and movement."
                    )

            traces = []
            scope_sampling_rates = []
            self.bridge.a_mode_log.emit(f"A-mode running for {pulses} pulse(s).")
            for pulse_idx in range(1, pulses + 1):
                scope_fs_hz = None
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
                    t, y, scope_fs_hz = self._capture_a_mode_waveform(
                        osc,
                        frequency,
                        amplitude,
                        cycles,
                        log_fn=self.bridge.a_mode_log.emit,
                    )
                    if scope_fs_hz is not None and scope_fs_hz > 0.0:
                        scope_sampling_rates.append(float(scope_fs_hz))
                traces.append((t, y))
                pulse_csv = a_scan_folder / f"a_scan_pulse_{pulse_idx:03d}.csv"
                _write_wave_csv(
                    pulse_csv,
                    t,
                    y,
                    {
                        "pulse_index": pulse_idx,
                        "pulse_total": pulses,
                        "scope_sampling_rate_hz": (
                            float(scope_fs_hz) if scope_fs_hz is not None else "n/a"
                        ),
                    },
                )
                self.bridge.a_mode_log.emit(
                    f"Captured A-mode echo {pulse_idx}/{pulses}"
                )

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
            stack = np.vstack(
                [self._detrend_signal(item[1][:min_len]) for item in traces]
            )
            if pulses > 1:
                avg = np.mean(stack, axis=0)
            else:
                avg = stack[0].copy()
            first = stack[0]
            last = stack[-1]
            config_sampling_rate_hz = float(sampling_rate)
            if scope_sampling_rates:
                filter_sampling_rate_hz = float(np.median(np.asarray(scope_sampling_rates, dtype=float)))
                pct = abs(filter_sampling_rate_hz - config_sampling_rate_hz) / max(config_sampling_rate_hz, 1e-12) * 100.0
                self.bridge.a_mode_log.emit(
                    f"A-mode filtering: Config sampling rate={config_sampling_rate_hz/1e6:.6f} MS/s, scope measured sampling rate={filter_sampling_rate_hz/1e6:.6f} MS/s (delta={pct:.2f}%)."
                )
                self.bridge.a_mode_log.emit(
                    f"A-mode filtering: fs={filter_sampling_rate_hz/1e6:.6f} MS/s (scope actual) is used for Nyquist/cutoff calculations."
                )
            else:
                filter_sampling_rate_hz = config_sampling_rate_hz
                self.bridge.a_mode_log.emit(
                    f"A-mode filtering: scope sampling rate unavailable; fs={filter_sampling_rate_hz/1e6:.6f} MS/s (Config) is used for Nyquist/cutoff calculations."
                )
            effective_cutoff_hz = cutoff_hz
            if filter_sampling_rate_hz > 0.0:
                nyquist_hz = 0.5 * filter_sampling_rate_hz
                if cutoff_hz >= nyquist_hz:
                    self.bridge.a_mode_log.emit(
                        f"A-mode filtering ERROR: cutoff={cutoff_hz/1000.0:.3f} kHz is >= Nyquist={nyquist_hz/1000.0:.3f} kHz. High-pass filter is disabled."
                    )
                    self.bridge.a_mode_log.emit(
                        "A-mode filtering note: filtering is only meaningful when cutoff is much smaller than Nyquist frequency."
                    )
                    effective_cutoff_hz = 0.0
                elif cutoff_hz >= 0.8 * nyquist_hz:
                    self.bridge.a_mode_log.emit(
                        f"A-mode filtering WARNING: cutoff={cutoff_hz/1000.0:.3f} kHz is close to Nyquist={nyquist_hz/1000.0:.3f} kHz; filtering is meaningful when cutoff is much smaller than Nyquist."
                    )
            a_mode_signal = module.estimate_a_mode_signal(
                t_ref,
                avg,
                highpass_cutoff_hz=effective_cutoff_hz,
                filter_order=filter_order,
                sampling_rate_hz=filter_sampling_rate_hz,
            )
            self._a_mode_last_results = {
                "t": t_ref,
                "traces": stack,
                "avg": avg,
                "a_mode": a_mode_signal,
                "filter_sampling_rate_hz": float(filter_sampling_rate_hz),
                "live_enabled": live_enabled,
                "use_dummy": use_dummy,
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

            avg_csv = a_scan_folder / "a_scan_average.csv"
            with open(avg_csv, "w", encoding="utf-8", newline="") as csv_file:
                final_meta = dict(a_scan_metadata)
                final_meta["effective_filter_sampling_rate_hz"] = float(
                    filter_sampling_rate_hz
                )
                final_meta["effective_highpass_cutoff_hz"] = float(effective_cutoff_hz)
                for key, value in final_meta.items():
                    csv_file.write(f"# {key}: {value}\n")
                writer = csv.writer(csv_file)
                writer.writerow(
                    ["Time_s", "Average_Detrended_Amplitude_V", "A_Mode_Envelope_V"]
                )
                writer.writerows(zip(t_ref, avg, a_mode_signal))

            self.bridge.a_mode_log.emit(
                f"A-mode CSV files saved in run folder: {a_scan_folder}"
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

        active_scan_type = self._current_bc_scan_type()
        self._bc_apply_unlocked = True
        self._bc_pressure_field_cache = None
        self._bc_pressure_field_source = None
        self._bc_c_mode_cache = None
        self._bc_c_mode_source = None
        self._bc_c_mode_scanned = False
        self.stop_event.clear()
        self.bridge.scan_busy.emit(True)
        self.bc_output.clear()
        threading.Thread(
            target=self._scan_worker,
            args=(pg, scan, active_scan_type),
            daemon=True,
        ).start()

    def _scan_worker(self, pg: dict, scan: dict, scan_type: str = "standard") -> None:
        oscmod = None
        rig_function = None
        Agilent33500 = None
        sg = None
        sock = None
        osc = None
        dry_run = self.dry_run_check.isChecked()
        import importlib

        try:
            a_scan_script = BASE_DIR / "A scan.py"
            a_scan_spec = importlib.util.spec_from_file_location(
                "a_scan", a_scan_script
            )
            if a_scan_spec is None or a_scan_spec.loader is None:
                raise RuntimeError(
                    "Unable to load A scan.py for dummy signal generation"
                )
            a_scan_module = importlib.util.module_from_spec(a_scan_spec)
            a_scan_spec.loader.exec_module(a_scan_module)
            window_fn = self.tx_windowing_combo.currentText()
            sampling_rate = (
                self._extract_last_float(
                    self.sampling_rate_edit.text().strip(),
                    max(float(pg["frequency"]) * 100.0, 1e6) / 1000.0,
                )
                * 1000.0
            )

            # Align 3D dry-run excitation with A-mode/Excitation tab values.
            dry_frequency_hz = float(self.tx_freq.value()) * 1000.0
            dry_amplitude_v = float(self.tx_amp.value())
            dry_cycles = float(self.tx_cycles.value())
            dry_window_fn = self.tx_windowing_combo.currentText()
            pulses_per_point = max(1, int(self.tx_pulses.value()))
            dry_prf_hz = float(self.tx_prf.value())

            if not dry_run:
                pm = importlib.import_module("pymeasure.instruments.agilent")
                sg_model = self.sg_name_edit.currentText().strip()
                sg_lookup = {
                    "Agilent33500B": "Agilent33500",
                    "Agilent33521A": "Agilent33500",
                    "Agilent33220A": "Agilent33220A",
                }.get(sg_model, sg_model)
                sg_class = getattr(pm, sg_lookup, None)
                from Signal_function import Burst_generate
                import Oscilloscope as oscmod
                import rig_function

                if sg_class is None:
                    raise ImportError(f"Signal generator model '{sg_model}' not available in pymeasure.instruments.agilent")
                sg_address = pg.get("sg_address") or self._default_sg_address
                retries = max(1, int(self.test_retries.value()))
                retry_delay = min(0.3, float(self.test_timeout.value()))
                was_sg_cached = self._get_stable_sg(sg_address) is not None
                try:
                    sg, _opened_sg_now = self._acquire_cached_sg(
                        sg_address,
                        driver=sg_class,
                        retries=retries,
                        retry_delay=retry_delay,
                    )
                except Exception as sg_exc:
                    self.bridge.bc_log.emit(
                        "3D-Mode hardware not detected and Dry Run is off. "
                        "Enable Dry Run or connect hardware."
                    )
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Failed to connect to signal generator at {sg_address}: {sg_exc}"
                    )
                    return
                if was_sg_cached:
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Using cached signal generator connection at {sg_address}."
                    )
                else:
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Opened and cached signal generator connection at {sg_address}."
                    )
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((scan["host"], self._port_value()))
                osc_address = self.osc_address_edit.text().strip()
                if not osc_address:
                    self.bridge.bc_log.emit(
                        "3D-Mode: No oscilloscope VISA address configured. Please set it in the Config tab."
                    )
                    return
                was_osc_cached = self._get_stable_osc(osc_address) is not None
                try:
                    osc, _opened_osc_now = self._acquire_cached_osc(
                        osc_address,
                        retries=retries,
                        retry_delay=retry_delay,
                    )
                except Exception as osc_exc:
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Failed to connect to oscilloscope at {osc_address}: {osc_exc}"
                    )
                    return
                if was_osc_cached:
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Using cached oscilloscope connection at {osc_address}."
                    )
                else:
                    self.bridge.bc_log.emit(
                        f"3D-Mode: Opened and cached oscilloscope connection at {osc_address}."
                    )
                scan_folder = oscmod.create_scan_folder()
                self.bridge.bc_log.emit(f"Scan folder: {scan_folder}")
                self.bridge.bc_log.emit("Hardware connected.")
            else:
                # Dry-run: create scan folder directly without importing Oscilloscope
                _data_dir = DATA_DIR
                _data_dir.mkdir(exist_ok=True)
                _existing = [
                    d
                    for d in os.listdir(_data_dir)
                    if d.startswith("scan_") and os.path.isdir(_data_dir / d)
                ]
                _nums = [
                    int(d.split("_")[1]) for d in _existing if d.split("_")[1].isdigit()
                ]
                _next = max(_nums, default=0) + 1
                scan_folder = str(_data_dir / f"scan_{_next:03d}")
                os.makedirs(scan_folder, exist_ok=True)
                self.bridge.bc_log.emit(f"[Dry-run] Scan folder: {scan_folder}")
                self.bridge.bc_log.emit(
                    "[Dry-run] Using dummy_signal_generator.py for acquisition."
                )

            if rig_function and sock:
                rig_function.send_command(sock, "INC")
                rig_function.enable_axis(sock, scan["scan_axis"])
                rig_function.enable_axis(sock, scan["cross_axis"])

            import numpy as np

            scan_steps = int(scan["scan_points"])
            cross_steps = int(scan["cross_points"])
            axis1_mm = np.linspace(0.0, float(scan["scan_length"]), scan_steps)
            axis2_mm = np.linspace(0.0, float(scan["cross_length"]), cross_steps)
            axis1_pulses = np.rint(axis1_mm * MM_TO_PULSE).astype(int)
            axis2_pulses = np.rint(axis2_mm * MM_TO_PULSE).astype(int)
            scan_algorithm_label = str(scan.get("scan_algorithm", "Zigzag")).strip() or "Zigzag"
            scan_algorithm = scan_algorithm_label.lower()
            point_plan = self._build_3d_scan_point_plan(
                scan_steps,
                cross_steps,
                axis1_mm,
                axis2_mm,
                axis1_pulses,
                axis2_pulses,
                scan_algorithm,
            )
            nominal_scan_step = (
                int(axis1_pulses[1] - axis1_pulses[0]) if scan_steps > 1 else 0
            )
            nominal_cross_step = (
                int(axis2_pulses[1] - axis2_pulses[0]) if cross_steps > 1 else 0
            )
            self.bridge.bc_log.emit(
                f"B-Mode settings: dry_run={dry_run}, live_preview={self.live_update_check.isChecked()}, "
                f"shape={pg['shape']}, frequency={pg['frequency']} Hz, amplitude={pg['amplitude']} V, "
                f"cycles/pulse={pg['no_of_cycles_per_pulse']}, pulses={pg['no_of_pulses']}, "
                f"scan_axis={scan['scan_axis']}, cross_axis={scan['cross_axis']}, depth_axis={scan['depth_axis']}, "
                f"scan_points={scan_steps}, cross_points={cross_steps}, "
                f"scan_algorithm={scan_algorithm_label}, "
                f"scan_step={nominal_scan_step}, cross_step={nominal_cross_step}"
            )
            self.bridge.bc_log.emit(
                f"Averaging echoes per 3D point using Excitation No. Of Pulses: {pulses_per_point}"
            )
            self.bridge.bc_log.emit(
                f"Post-processing mode: {str(scan_type).strip().lower() or 'standard'}"
            )
            if dry_run:
                self.bridge.bc_log.emit(
                    f"[Dry-run] Using Excitation tab params for dummy echoes: "
                    f"frequency={dry_frequency_hz} Hz, amplitude={dry_amplitude_v} V, "
                    f"cycles/pulse={dry_cycles}, window={dry_window_fn}"
                )
            self.bridge.bc_log.emit(
                f"Starting 3D grid scan: {scan_steps} x {cross_steps} points "
                f"(endpoints included on both axes)."
            )

            # Initialize list to accumulate A-mode signals for matrix

            a_mode_signals = []  # Will store (amplitude_array) for each acquisition
            acquisition_count = 0
            live_preview = bool(self.live_update_check.isChecked())
            total_scans = len(point_plan)
            c_mode_enabled = str(scan_type).strip().lower() == "c_mode"
            pressure_field_mode = str(scan_type).strip().lower() == "pressure_field"
            measurements_subdir = (
                "pressure_mode_measurements"
                if pressure_field_mode
                else "a_mode_measurements"
            )
            measurements_dir = Path(scan_folder) / measurements_subdir
            measurements_dir.mkdir(parents=True, exist_ok=True)
            self.bridge.bc_log.emit(
                f"3D-Mode measurement folder: {measurements_dir}"
            )
            c_mode_signal_map = np.empty((cross_steps, scan_steps), dtype=object)
            c_mode_signal_map[:, :] = None
            c_mode_metric_map = np.full((cross_steps, scan_steps), np.nan, dtype=float)
            c_mode_gate_start_s = float(self.bc_c_mode_gate_start.value()) * 1e-6
            c_mode_gate_width_s = float(self.bc_c_mode_gate_width.value()) * 1e-6
            c_mode_metric_name = self.bc_c_mode_metric_combo.currentText().strip()
            c_mode_colormap = self.bc_c_mode_cmap_combo.currentText().strip()
            a_mode_cutoff_hz = float(self.a_mode_highpass_cutoff.value()) * 1000.0
            a_mode_filter_order = int(self.a_mode_filter_order.value())
            current_axis1_pulse = 0
            current_axis2_pulse = 0
            point_records: list[dict] = []

            current_cross_idx = None
            for point in point_plan:
                if self.stop_event.is_set():
                    self.bridge.bc_log.emit("Scan stopped by user.")
                    break

                cross_idx = int(point["cross_idx"])
                scan_idx = int(point["scan_idx"])
                axis1_idx = int(point["scan_point"])
                axis2_idx = int(point["cross_point"])
                target_axis1_pulse = int(point["scan_pulse"])
                target_axis2_pulse = int(point["cross_pulse"])
                point_order = int(point["order"])

                if cross_idx != current_cross_idx:
                    current_cross_idx = cross_idx
                    self.bridge.bc_log.emit(
                        f"Scanning row {axis2_idx}/{cross_steps} using {scan_algorithm_label}"
                    )

                if rig_function and sock:
                    delta_axis2 = target_axis2_pulse - current_axis2_pulse
                    if delta_axis2 != 0:
                        rig_function.send_command(
                            sock, f"{scan['cross_axis']}{delta_axis2}"
                        )
                        rig_function.wait_until_stopped(sock, scan["cross_axis"])
                        current_axis2_pulse = target_axis2_pulse

                    delta_axis1 = target_axis1_pulse - current_axis1_pulse
                    if delta_axis1 != 0:
                        rig_function.send_command(
                            sock, f"{scan['scan_axis']}{delta_axis1}"
                        )
                        rig_function.wait_until_stopped(sock, scan["scan_axis"])
                        current_axis1_pulse = target_axis1_pulse
                else:
                    delta_axis2 = target_axis2_pulse - current_axis2_pulse
                    delta_axis1 = target_axis1_pulse - current_axis1_pulse
                    if delta_axis2 != 0:
                        self.bridge.bc_log.emit(
                            f"[Dry-run] Move {scan['cross_axis']} {delta_axis2}"
                        )
                        current_axis2_pulse = target_axis2_pulse
                    if delta_axis1 != 0:
                        self.bridge.bc_log.emit(
                            f"[Dry-run] Move {scan['scan_axis']} {delta_axis1}"
                        )
                        current_axis1_pulse = target_axis1_pulse

                # --- acquire and average echoes for this point ---
                t_acq = None
                echo_acq = None
                t_echoes = []
                y_echoes = []
                scope_sampling_rates = []
                for pulse_idx in range(1, pulses_per_point + 1):
                    if oscmod and osc and sg and not dry_run:
                        Burst_generate(
                            sg,
                            shape="SIN",
                            frequency=dry_frequency_hz,
                            amplitude=dry_amplitude_v,
                            no_of_cycles_per_pulse=dry_cycles,
                            no_of_pulses=1,
                            prf=dry_prf_hz,
                            window_type=dry_window_fn,
                        )
                        t_one, y_one, _scope_fs_hz = self._capture_a_mode_waveform(
                            osc,
                            dry_frequency_hz,
                            dry_amplitude_v,
                            dry_cycles,
                            log_fn=self.bridge.bc_log.emit,
                        )
                        if _scope_fs_hz is not None and np.isfinite(_scope_fs_hz) and _scope_fs_hz > 0.0:
                            scope_sampling_rates.append(float(_scope_fs_hz))
                    else:
                        t_one, y_one = a_scan_module.generate_test_echo(
                            frequency_hz=dry_frequency_hz,
                            amplitude_v=dry_amplitude_v,
                            no_of_cycles_per_pulse=dry_cycles,
                            window_type=dry_window_fn,
                            sampling_rate_hz=sampling_rate,
                        )
                    t_echoes.append(np.asarray(t_one, dtype=float))
                    y_echoes.append(np.asarray(y_one, dtype=float))
                    self.bridge.bc_log.emit(
                        f"Captured echo {pulse_idx}/{pulses_per_point} at row {axis2_idx}, col {axis1_idx}"
                    )

                if y_echoes:
                    min_len = min(e.size for e in y_echoes)
                    t_acq = t_echoes[0][:min_len]
                    stack = np.vstack(
                        [self._detrend_signal(e[:min_len]) for e in y_echoes]
                    )
                    if pulses_per_point > 1:
                        echo_acq = np.mean(stack, axis=0)
                    else:
                        echo_acq = stack[0].copy()

                    config_sampling_rate_hz = float(sampling_rate)
                    if scope_sampling_rates:
                        filter_sampling_rate_hz = float(
                            np.median(np.asarray(scope_sampling_rates, dtype=float))
                        )
                        pct = (
                            abs(filter_sampling_rate_hz - config_sampling_rate_hz)
                            / max(config_sampling_rate_hz, 1e-12)
                            * 100.0
                        )
                        self.bridge.bc_log.emit(
                            f"3D-mode filtering: Config fs={config_sampling_rate_hz/1e6:.6f} MS/s, "
                            f"scope measured fs={filter_sampling_rate_hz/1e6:.6f} MS/s (delta={pct:.2f}%)."
                        )
                    else:
                        filter_sampling_rate_hz = config_sampling_rate_hz
                    effective_cutoff_hz = a_mode_cutoff_hz
                    if filter_sampling_rate_hz > 0.0:
                        nyquist_hz = 0.5 * filter_sampling_rate_hz
                        if a_mode_cutoff_hz >= nyquist_hz:
                            self.bridge.bc_log.emit(
                                f"3D-mode filtering ERROR: cutoff={a_mode_cutoff_hz/1000.0:.3f} kHz is >= Nyquist={nyquist_hz/1000.0:.3f} kHz. High-pass filter is disabled."
                            )
                            self.bridge.bc_log.emit(
                                "3D-mode filtering note: filtering is only meaningful when cutoff is much smaller than Nyquist frequency."
                            )
                            effective_cutoff_hz = 0.0
                        elif a_mode_cutoff_hz >= 0.8 * nyquist_hz:
                            self.bridge.bc_log.emit(
                                f"3D-mode filtering WARNING: cutoff={a_mode_cutoff_hz/1000.0:.3f} kHz is close to Nyquist={nyquist_hz/1000.0:.3f} kHz; filtering is meaningful when cutoff is much smaller than Nyquist."
                            )

                    csv_path = measurements_dir / (
                        f"point_{point_order:04d}_r{axis2_idx:03d}_c{axis1_idx:03d}.csv"
                    )
                    with open(csv_path, "w", encoding="utf-8") as _f:
                        _f.write(f"# algorithm: {scan_algorithm_label}\n")
                        _f.write(f"# point_order: {point_order}\n")
                        _f.write(f"# row_axis (Axis 2): {scan['cross_axis']}\n")
                        _f.write(f"# column_axis (Axis 1): {scan['scan_axis']}\n")
                        _f.write(f"# cross_point: {axis2_idx}\n")
                        _f.write(f"# scan_point: {axis1_idx}\n")
                        _f.write(f"# cross_mm: {point['cross_mm']:.6f}\n")
                        _f.write(f"# scan_mm: {point['scan_mm']:.6f}\n")
                        _f.write(f"# cross_pulse: {target_axis2_pulse}\n")
                        _f.write(f"# scan_pulse: {target_axis1_pulse}\n")
                        _f.write("Time (s),Amplitude (V)\n")
                        for _t, _v in zip(t_acq, echo_acq):
                            _f.write(f"{_t:.10e},{_v:.10e}\n")
                    self.bridge.bc_plot_csv.emit(str(csv_path))
                    self.bridge.bc_log.emit(
                        f"Saved averaged echo: point {point_order}/{total_scans} (row {axis2_idx}, col {axis1_idx}, {scan_algorithm_label})"
                    )

                    point_records.append(
                        {
                            "point_order": point_order,
                            "row_index": axis2_idx,
                            "col_index": axis1_idx,
                            "row_axis_role": "axis_2",
                            "row_axis_name": scan["cross_axis"],
                            "col_axis_role": "axis_1",
                            "col_axis_name": scan["scan_axis"],
                            "direction": point["direction"],
                            "algorithm": scan_algorithm_label,
                            "scan_axis": scan["scan_axis"],
                            "cross_axis": scan["cross_axis"],
                            "scan_mm": f"{point['scan_mm']:.9f}",
                            "cross_mm": f"{point['cross_mm']:.9f}",
                            "scan_pulse": target_axis1_pulse,
                            "cross_pulse": target_axis2_pulse,
                            "measurement_csv": str(
                                Path(csv_path).relative_to(Path(scan_folder)).as_posix()
                            ),
                        }
                    )

                    if c_mode_enabled:
                        try:
                            envelope = np.asarray(
                                a_scan_module.estimate_a_mode_signal(
                                    t_acq,
                                    echo_acq,
                                    highpass_cutoff_hz=effective_cutoff_hz,
                                    filter_order=a_mode_filter_order,
                                    sampling_rate_hz=filter_sampling_rate_hz,
                                ),
                                dtype=float,
                            )
                            n_env = min(t_acq.size, envelope.size)
                            t_env = np.asarray(t_acq[:n_env], dtype=float)
                            env = np.asarray(envelope[:n_env], dtype=float)
                            gate_end_s = c_mode_gate_start_s + c_mode_gate_width_s
                            gate_mask = (t_env >= c_mode_gate_start_s) & (t_env <= gate_end_s)
                            gated_env = env[gate_mask]
                            r = axis2_idx - 1
                            c = axis1_idx - 1
                            c_mode_signal_map[r, c] = np.array(gated_env, copy=True)
                            c_mode_metric_map[r, c] = self._compute_pressure_field_metric(
                                gated_env, c_mode_metric_name
                            )
                        except Exception as c_exc:
                            self.bridge.bc_log.emit(
                                f"C-Mode metric failed at row {axis2_idx}, col {axis1_idx}: {c_exc}"
                            )

                    a_mode_signals.append(echo_acq)
                    acquisition_count += 1
                else:
                    self.bridge.bc_log.emit(
                        f"Warning: No echoes captured at row {axis2_idx}, col {axis1_idx}"
                    )

                # --- live preview: emit raw signal only; renderer computes envelope ---
                if live_preview and t_acq is not None and echo_acq is not None:
                    self.bridge.bc_preview.emit(
                        {
                            "t": t_acq,
                            "raw": echo_acq,
                            "scan_idx": point_order,
                            "total_scans": total_scans,
                            "axis1_name": scan["scan_axis"],
                            "axis2_name": scan["cross_axis"],
                            "axis1_idx": axis1_idx,
                            "axis2_idx": axis2_idx,
                            "axis1_total": scan_steps,
                            "axis2_total": cross_steps,
                            "scan_type": scan_type,
                            "scan_algorithm": scan_algorithm_label,
                        }
                    )
            self.bridge.bc_log.emit("B Scan finished.")

            if point_records:
                manifest_path = os.path.join(scan_folder, "point_manifest.csv")
                with open(manifest_path, "w", newline="", encoding="utf-8") as mf:
                    fieldnames = [
                        "point_order",
                        "row_index",
                        "col_index",
                        "row_axis_role",
                        "row_axis_name",
                        "col_axis_role",
                        "col_axis_name",
                        "direction",
                        "algorithm",
                        "scan_axis",
                        "cross_axis",
                        "scan_mm",
                        "cross_mm",
                        "scan_pulse",
                        "cross_pulse",
                        "measurement_csv",
                    ]
                    writer = csv.DictWriter(mf, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(point_records)
                self.bridge.bc_log.emit(
                    f"Point manifest saved: {os.path.basename(manifest_path)}"
                )

            if pressure_field_mode and not self.stop_event.is_set():
                try:
                    axis1_mm = np.linspace(0.0, float(scan["scan_length"]), scan_steps)
                    axis2_mm = np.linspace(
                        0.0, float(scan["cross_length"]), cross_steps
                    )
                    self._bc_pressure_field_source = {
                        "scan_folder": str(scan_folder),
                        "axis1_name": scan["scan_axis"],
                        "axis2_name": scan["cross_axis"],
                        "scan_steps": int(scan_steps),
                        "cross_steps": int(cross_steps),
                        "axis1_mm": np.array(axis1_mm, copy=True),
                        "axis2_mm": np.array(axis2_mm, copy=True),
                    }
                    self.bridge.bc_log.emit(
                        "Pressure-field scan complete. Applying post-processing automatically..."
                    )
                    self.bridge.bc_pf_auto_apply.emit()
                except Exception as pf_exc:
                    self.bridge.bc_log.emit(
                        f"Pressure-field source metadata update failed: {pf_exc}"
                    )

            if c_mode_enabled and not self.stop_event.is_set():
                try:
                    axis1_mm = np.linspace(0.0, float(scan["scan_length"]), scan_steps)
                    axis2_mm = np.linspace(
                        0.0, float(scan["cross_length"]), cross_steps
                    )
                    self._bc_c_mode_source = {
                        "scan_folder": str(scan_folder),
                        "axis1_name": scan["scan_axis"],
                        "axis2_name": scan["cross_axis"],
                        "scan_steps": int(scan_steps),
                        "cross_steps": int(cross_steps),
                        "axis1_mm": np.array(axis1_mm, copy=True),
                        "axis2_mm": np.array(axis2_mm, copy=True),
                    }
                    self._bc_c_mode_cache = {
                        "axis1_name": scan["scan_axis"],
                        "axis2_name": scan["cross_axis"],
                        "axis1_mm": np.array(axis1_mm, copy=True),
                        "axis2_mm": np.array(axis2_mm, copy=True),
                        "signal_map": np.array(c_mode_signal_map, copy=True),
                    }
                    self.bridge.bc_preview.emit(
                        {
                            "mode": "c_mode_map",
                            "axis1_name": scan["scan_axis"],
                            "axis2_name": scan["cross_axis"],
                            "axis1_mm": axis1_mm,
                            "axis2_mm": axis2_mm,
                            "metric_map": c_mode_metric_map,
                            "metric_name": c_mode_metric_name,
                            "colormap": c_mode_colormap,
                        }
                    )
                    self.bridge.bc_log.emit(
                        f"C-Mode image rendered automatically using gated envelope metric: {c_mode_metric_name}."
                    )
                except Exception as c_map_exc:
                    self.bridge.bc_log.emit(
                        f"C-Mode map render failed: {c_map_exc}"
                    )

            # Save A-mode matrix with auto-incrementing ID
            if a_mode_signals and acquisition_count > 0:
                try:
                    # Find the maximum signal length
                    max_length = max(len(signal) for signal in a_mode_signals)

                    # Create matrix with padding (fill missing values with NaN)
                    a_mode_matrix = np.full(
                        (len(a_mode_signals), max_length), np.nan, dtype=float
                    )
                    for idx, signal in enumerate(a_mode_signals):
                        a_mode_matrix[idx, : len(signal)] = signal

                    # Generate unique matrix filename with auto-incrementing ID
                    data_dir = BASE_DIR / "data"
                    data_dir.mkdir(exist_ok=True)

                    # Find next available matrix ID
                    existing_matrices = [
                        f
                        for f in os.listdir(data_dir)
                        if f.startswith("a_mode_matrix_") and f.endswith(".npy")
                    ]
                    matrix_numbers = []
                    for f in existing_matrices:
                        try:
                            num = int(
                                f.replace("a_mode_matrix_", "").replace(".npy", "")
                            )
                            matrix_numbers.append(num)
                        except ValueError:
                            pass
                    next_matrix_id = max(matrix_numbers, default=0) + 1

                    # Save matrix
                    matrix_path = data_dir / f"a_mode_matrix_{next_matrix_id:03d}.npy"
                    np.save(str(matrix_path), a_mode_matrix)

                    # Also save metadata
                    metadata_path = (
                        data_dir / f"a_mode_matrix_{next_matrix_id:03d}_metadata.txt"
                    )
                    with open(str(metadata_path), "w", encoding="utf-8") as mf:
                        mf.write(f"A-Mode Matrix Data\n")
                        mf.write(
                            f"Shape: {a_mode_matrix.shape} (acquisitions × samples)\n"
                        )
                        mf.write(f"Total Acquisitions: {acquisition_count}\n")
                        mf.write(f"Scan Points (Axis 1): {scan_steps}\n")
                        mf.write(f"Cross Points (Axis 2): {cross_steps}\n")
                        mf.write(f"Frequency: {pg.get('frequency')} Hz\n")
                        mf.write(f"Amplitude: {pg.get('amplitude')} V\n")
                        mf.write(f"Dry Run: {dry_run}\n")

                    self.bridge.bc_log.emit(
                        f"A-mode matrix saved: {matrix_path.name} "
                        f"(shape: {a_mode_matrix.shape[0]} acquisitions × {a_mode_matrix.shape[1]} samples)"
                    )
                except Exception as e:
                    self.bridge.bc_log.emit(
                        f"Warning: Failed to save A-mode matrix: {e}"
                    )
        except Exception as exc:
            self.bridge.bc_log.emit(f"Error during scan: {exc}")
        finally:
            if sock is not None:
                try:
                    sock.close()
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
        self._clear_stable_sg()
        event.accept()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = ScannerMainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
