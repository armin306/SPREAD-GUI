from __future__ import annotations

import configparser
import glob
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QThread, QTimer, QFileSystemWatcher, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFrame,
    QGroupBox,
    QRadioButton,
    QDoubleSpinBox,
    QSpinBox,
    QMessageBox,
    QCheckBox,
    QInputDialog,
)

from spread_gui.core.model import Cell
from spread_gui.core.paths import VISIT_RE, detect_visit_from_path, infer_visit_root
from spread_gui.core.cryst import normalize_sg_name
from spread_gui.core.energies import compute_energy_list, detect_energies_in_dir, detect_num_sweeps_in_dir, detect_sweeps_for_energy, detect_wedge_size_in_dir
from spread_gui.core.wedges import compute_wedges

from spread_gui.services.database import ProjectDB
from spread_gui.services.slurm import (
    chmod_x,
    get_slurm_jwt,
    submit_job_via_rest_api,
    check_ssh_key_auth,
    setup_ssh_key,
)

_CONFIG_PATH = Path.home() / ".config" / "spread_gui" / "settings.ini"


class _DataScanWorker(QThread):
    """Scans the data directory for energies and wedge size off the main thread."""
    energies_found = pyqtSignal(list)          # List[int]
    wedge_found    = pyqtSignal(int, int, int, str)  # wedge_size, total_images_per_sweep, num_sweeps, diag

    def __init__(self, data_dir: str, scan_energies: bool, scan_wedges: bool) -> None:
        super().__init__()
        self._data_dir      = data_dir
        self._scan_energies = scan_energies
        self._scan_wedges   = scan_wedges

    def run(self) -> None:
        if self._scan_energies:
            energies = detect_energies_in_dir(self._data_dir)
            self.energies_found.emit(energies)

        if self._scan_wedges:
            import glob as _glob
            wedge, diag = detect_wedge_size_in_dir(self._data_dir)
            total = 0
            num_sweeps = 1
            if wedge > 0:
                energies_found = detect_energies_in_dir(self._data_dir)
                if energies_found:
                    e0 = energies_found[0]
                    pat = os.path.join(self._data_dir, f"{e0}_E1_1_?????.cbf")
                    total = len(_glob.glob(pat))
                num_sweeps = detect_num_sweeps_in_dir(self._data_dir)
            self.wedge_found.emit(wedge, total, num_sweeps, diag)


class ProcessingTab(QWidget):
    # Emitted when a crystal is loaded from the database (project_name, crystal_name).
    crystal_context_changed = pyqtSignal(str, str)
    # Emitted after any save: (data_path, proc_path, space_group, cell_str).
    processing_info_changed = pyqtSignal(str, str, str, str)
    # Emitted after jobs are submitted so the tab indicator can be updated.
    jobs_status_changed = pyqtSignal()
    # Log messages forwarded to the shared main-window log.
    log_message = pyqtSignal(str)

    def __init__(self, db: ProjectDB, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._db: ProjectDB = db
        self._current_crystal_id: Optional[int] = None
        self._dirty: bool = False

        # Auto-energy mechanisms
        self._energy_scan_timer = QTimer(self)
        self._energy_scan_timer.setSingleShot(True)
        self._energy_scan_timer.timeout.connect(self._auto_update_energies_from_data_dir)

        # Auto-save timer — fires 1 s after the last field change
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self.save_settings)
        self._loading = False  # suppress autosave during startup

        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(lambda _: self._schedule_energy_scan())
        self._watched_data_dir: Optional[str] = None

        self._last_auto_energies: Optional[List[int]] = None
        self._scan_worker: Optional[_DataScanWorker] = None
        self._num_sweeps: int = 1

        self._build_ui()
        self._wire_signals()
        # Spinboxes disabled by default since auto-detect checkbox starts checked
        self.wedge_size.setEnabled(False)
        self.total_images.setEnabled(False)
        self._apply_defaults_from_cwd()
        self._loading = True
        self._load_settings()
        self._loading = False
        self.save_settings()  # write back immediately to upgrade any stale settings file
        self._refresh_previews_and_validation()
        self._schedule_energy_scan()
        # Defer the SSH check so it doesn't block the window from painting.
        QTimer.singleShot(0, self._check_ssh_key_status)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        title_row = QHBoxLayout()
        title = QLabel("SPREAD – Processing")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        title.setFont(f)
        title_row.addWidget(title)
        title_row.addStretch(1)
        root.addLayout(title_row)

        # All crystal metadata (visit, project, crystal, paths, SG, cell) is
        # managed via the Manage Projects dialog; stored as plain attributes.
        self._visit:        str            = ""
        self._project:      str            = ""
        self._crystal:      str            = ""
        self._data_path:    str            = ""
        self._proc_path:    str            = ""
        self._space_group:  str            = ""
        self._cell:         Optional[Cell] = None

        # Energy definition
        e_box = QGroupBox("Energy definition")
        ev = QVBoxLayout(e_box)

        er = QHBoxLayout()
        self.rb_energy_range = QRadioButton("Range: start / end / increment")
        self.rb_energy_list = QRadioButton("List: comma/space separated")
        self.rb_energy_range.setChecked(True)
        er.addWidget(self.rb_energy_range)
        er.addWidget(self.rb_energy_list)
        er.addStretch(1)
        ev.addLayout(er)

        self.chk_auto_energies = QCheckBox("Auto-detect energies from Data path")
        self.chk_auto_energies.setChecked(True)
        ev.addWidget(self.chk_auto_energies)

        eg = QGridLayout()
        self.energy_start = QDoubleSpinBox()
        self.energy_start.setRange(0, 200000)
        self.energy_start.setDecimals(3)
        self.energy_end = QDoubleSpinBox()
        self.energy_end.setRange(0, 200000)
        self.energy_end.setDecimals(3)
        self.energy_inc = QDoubleSpinBox()
        self.energy_inc.setRange(0.001, 200000)
        self.energy_inc.setDecimals(3)

        self.energy_start.setValue(12600.0)
        self.energy_end.setValue(12610.0)
        self.energy_inc.setValue(1.0)

        eg.addWidget(QLabel("Start:"), 0, 0)
        eg.addWidget(self.energy_start, 0, 1)
        eg.addWidget(QLabel("End:"), 0, 2)
        eg.addWidget(self.energy_end, 0, 3)
        eg.addWidget(QLabel("Increment:"), 0, 4)
        eg.addWidget(self.energy_inc, 0, 5)
        ev.addLayout(eg)

        self.energy_list_edit = QLineEdit()
        self.energy_list_edit.setPlaceholderText("e.g. 12600, 12605, 12610")
        ev.addWidget(self.energy_list_edit)

        self.energy_preview = QLabel("")
        self.energy_preview.setStyleSheet("color:#666;")
        ev.addWidget(self.energy_preview)
        root.addWidget(e_box)

        # Wedges (defaults: 30 / 360)
        w_box = QGroupBox("Wedge definition")
        wg = QGridLayout(w_box)

        self.chk_auto_wedges = QCheckBox("Auto-detect wedge size from Data path")
        self.chk_auto_wedges.setChecked(True)
        wg.addWidget(self.chk_auto_wedges, 0, 0, 1, 4)

        wg.addWidget(QLabel("Wedge size:"), 1, 0)
        self.wedge_size = QSpinBox()
        self.wedge_size.setRange(1, 1000000)
        self.wedge_size.setValue(300)
        wg.addWidget(self.wedge_size, 1, 1)

        wg.addWidget(QLabel("Total number of images:"), 1, 2)
        self.total_images = QSpinBox()
        self.total_images.setRange(1, 100000000)
        self.total_images.setValue(3600)
        wg.addWidget(self.total_images, 1, 3)

        self.wedge_preview = QLabel("")
        self.wedge_preview.setStyleSheet("color:#666;")
        wg.addWidget(self.wedge_preview, 2, 0, 1, 4)
        root.addWidget(w_box)

        # Pipeline selection
        p_box = QGroupBox("Processing pipeline")
        p_vbox = QVBoxLayout(p_box)

        pr = QHBoxLayout()
        self.rb_autoproc = QRadioButton("AutoProc")
        self.rb_xia2_dials = QRadioButton("Xia2 DIALS")
        self.rb_xia2_3dii = QRadioButton("Xia2 3dii")
        self.rb_autoproc.setChecked(True)
        pr.addWidget(self.rb_xia2_dials)
        pr.addWidget(self.rb_xia2_3dii)
        pr.addWidget(self.rb_autoproc)
        pr.addStretch(1)
        p_vbox.addLayout(pr)

        root.addWidget(p_box)

        # Submission method
        sub_box = QGroupBox("Submission method")
        sub_vbox = QVBoxLayout(sub_box)

        sub_row = QHBoxLayout()
        self.rb_submit_rest = QRadioButton("REST API (recommended)")
        self.rb_submit_dry  = QRadioButton("Dry run — generate scripts only")
        self.rb_submit_rest.setChecked(True)
        sub_row.addWidget(self.rb_submit_rest)
        sub_row.addWidget(self.rb_submit_dry)
        sub_row.addStretch(1)
        sub_vbox.addLayout(sub_row)

        key_row = QHBoxLayout()
        self.ssh_key_status = QLabel("SSH key: unknown")
        self.ssh_key_status.setStyleSheet("color:#666;")
        self.btn_setup_ssh_key = QPushButton("Setup SSH key…")
        key_row.addWidget(self.ssh_key_status)
        key_row.addWidget(self.btn_setup_ssh_key)
        key_row.addStretch(1)
        sub_vbox.addLayout(key_row)

        root.addWidget(sub_box)

        # Actions
        a_row = QHBoxLayout()
        self.btn_submit = QPushButton("Submit jobs")
        a_row.addWidget(self.btn_submit)
        a_row.addStretch(1)
        root.addLayout(a_row)

        # ---- Analyse Processing section ----
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #b0bcd0;")
        root.addWidget(sep)

        from spread_gui.ui.analysis_tab import AnalysisTab
        analyse_title = QLabel("Analyse Processing")
        af = QFont()
        af.setPointSize(12)
        af.setBold(True)
        analyse_title.setFont(af)
        root.addWidget(analyse_title)

        self.analysis_section = AnalysisTab(self)
        root.addWidget(self.analysis_section)

        self._update_energy_mode()

    def _wire_signals(self) -> None:
        self.rb_energy_range.toggled.connect(self._update_energy_mode)
        self.rb_energy_list.toggled.connect(self._update_energy_mode)
        self.rb_energy_range.toggled.connect(self._schedule_autosave)
        self.rb_energy_list.toggled.connect(self._schedule_autosave)
        self.chk_auto_energies.toggled.connect(self._auto_energies_toggled)
        self.chk_auto_energies.toggled.connect(self._schedule_autosave)

        self.energy_start.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_end.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_inc.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_list_edit.textChanged.connect(self._refresh_previews_and_validation)
        self.energy_start.valueChanged.connect(self._schedule_autosave)
        self.energy_end.valueChanged.connect(self._schedule_autosave)
        self.energy_inc.valueChanged.connect(self._schedule_autosave)
        self.energy_list_edit.textChanged.connect(self._schedule_autosave)

        self.chk_auto_wedges.toggled.connect(self._auto_wedges_toggled)
        self.chk_auto_wedges.toggled.connect(self._schedule_autosave)
        self.wedge_size.valueChanged.connect(self._refresh_previews_and_validation)
        self.total_images.valueChanged.connect(self._refresh_previews_and_validation)
        self.wedge_size.valueChanged.connect(self._schedule_autosave)
        self.total_images.valueChanged.connect(self._schedule_autosave)

        self.rb_autoproc.toggled.connect(self._schedule_autosave)
        self.rb_xia2_dials.toggled.connect(self._schedule_autosave)
        self.rb_xia2_3dii.toggled.connect(self._schedule_autosave)
        self.rb_submit_rest.toggled.connect(self._schedule_autosave)
        self.rb_submit_dry.toggled.connect(self._schedule_autosave)

        self.btn_submit.clicked.connect(self.submit_jobs)
        self.btn_setup_ssh_key.clicked.connect(self._setup_ssh_key)

        # Keep the embedded analysis section in sync with proc_path changes,
        # and forward its log messages to the shared main-window log.
        self.processing_info_changed.connect(
            lambda _d, proc, _sg, _c: self.analysis_section.set_proc_path(proc)
        )
        self.analysis_section.log_message.connect(self.log_message)

    # ---------------- Defaults ----------------
    def _apply_defaults_from_cwd(self) -> None:
        cwd = os.getcwd()
        visit = detect_visit_from_path(cwd)
        if visit:
            self._visit = visit
            root = infer_visit_root(cwd, visit)
            if root:
                self._data_path = root
                self._proc_path = os.path.join(root, "processing", "SPREAD")

    # ---------------- Persistent settings ----------------
    def _load_settings(self) -> None:
        cfg = configparser.ConfigParser()
        if not _CONFIG_PATH.exists():
            return
        cfg.read(_CONFIG_PATH)
        if "spread_gui" not in cfg:
            return
        self._apply_form_state(dict(cfg["spread_gui"]))

    # ---------------- Form state dict (shared by INI and DB) ----------------

    def _collect_form_state(self) -> dict:
        """Return all form fields as a flat string dict (matches settings.ini keys)."""
        cell = self._cell
        return {
            "visit":         self._visit,
            "project":       self._project,
            "crystal":       self._crystal,
            "crystal_id":    str(self._current_crystal_id) if self._current_crystal_id is not None else "",
            "data_path":     self._data_path,
            "proc_path":     self._proc_path,
            "space_group":   self._space_group,
            "cell_a":        str(cell.a)     if cell else "0",
            "cell_b":        str(cell.b)     if cell else "0",
            "cell_c":        str(cell.c)     if cell else "0",
            "cell_alpha":    str(cell.alpha) if cell else "0",
            "cell_beta":     str(cell.beta)  if cell else "0",
            "cell_gamma":    str(cell.gamma) if cell else "0",
            "pipeline":      self._pipeline_key(),
            "submit_method": "rest" if self.rb_submit_rest.isChecked() else "dry",
            "energy_mode":   "range" if self.rb_energy_range.isChecked() else "list",
            "energy_start":  str(self.energy_start.value()),
            "energy_end":    str(self.energy_end.value()),
            "energy_inc":    str(self.energy_inc.value()),
            "energy_list":   self.energy_list_edit.text(),
            "wedge_size":    str(self.wedge_size.value()),
            "total_images":  str(self.total_images.value()),
            "auto_energies": str(self.chk_auto_energies.isChecked()),
            "auto_wedges":   str(self.chk_auto_wedges.isChecked()),
        }

    def _apply_form_state(self, s: dict) -> None:
        """Populate form fields from a flat string dict."""
        if "visit"   in s: self._visit   = s["visit"]
        if "project" in s: self._project = s["project"]
        if "crystal" in s: self._crystal = s["crystal"]
        if s.get("crystal_id", ""):
            try:
                self._current_crystal_id = int(s["crystal_id"])
            except ValueError:
                self._current_crystal_id = None

        if "data_path" in s:
            if s["data_path"] != self._data_path:
                self._num_sweeps = 1  # reset until next scan
            self._data_path = s["data_path"]
        if "proc_path" in s: self._proc_path = s["proc_path"]
        if "energy_list" in s:
            self.energy_list_edit.setText(s["energy_list"])

        self._space_group = s.get("space_group", "")
        cell_keys = ["cell_a", "cell_b", "cell_c", "cell_alpha", "cell_beta", "cell_gamma"]
        cell_vals = []
        for key in cell_keys:
            try:
                cell_vals.append(float(s.get(key, 0)))
            except (TypeError, ValueError):
                cell_vals.append(0.0)
        if any(v != 0.0 for v in cell_vals):
            self._cell = Cell(*cell_vals)
        else:
            self._cell = None

        pipeline = s.get("pipeline", "")
        if pipeline == "xia2_dials":
            self.rb_xia2_dials.setChecked(True)
        elif pipeline == "xia2_3dii":
            self.rb_xia2_3dii.setChecked(True)
        elif pipeline == "autoproc":
            self.rb_autoproc.setChecked(True)

        submit_method = s.get("submit_method", "")
        if submit_method == "dry":
            self.rb_submit_dry.setChecked(True)
        elif submit_method == "rest":
            self.rb_submit_rest.setChecked(True)

        energy_mode = s.get("energy_mode", "")
        if energy_mode == "list":
            self.rb_energy_list.setChecked(True)
        elif energy_mode == "range":
            self.rb_energy_range.setChecked(True)

        for key, spin in (
            ("energy_start", self.energy_start),
            ("energy_end",   self.energy_end),
            ("energy_inc",   self.energy_inc),
        ):
            if key in s:
                try:
                    spin.setValue(float(s[key]))
                except ValueError:
                    pass

        for key, spin in (
            ("wedge_size",   self.wedge_size),
            ("total_images", self.total_images),
        ):
            if key in s:
                try:
                    spin.setValue(int(s[key]))
                except ValueError:
                    pass

        if "auto_energies" in s:
            self.chk_auto_energies.setChecked(s["auto_energies"].lower() == "true")
        if "auto_wedges" in s:
            checked = s["auto_wedges"].lower() == "true"
            self.chk_auto_wedges.setChecked(checked)
            self.wedge_size.setEnabled(not checked)
            self.total_images.setEnabled(not checked)

    def _cell_str(self) -> str:
        c = self._cell
        if c is None:
            return "\u2014"
        return (
            f"a={c.a:.4g}  b={c.b:.4g}  c={c.c:.4g}"
            f"    \u03b1={c.alpha:.4g}  \u03b2={c.beta:.4g}  \u03b3={c.gamma:.4g}"
        )

    def _emit_processing_info(self) -> None:
        self.processing_info_changed.emit(
            self._data_path,
            self._proc_path,
            self._space_group or "\u2014",
            self._cell_str(),
        )

    def save_settings(self) -> None:
        state = self._collect_form_state()
        cfg = configparser.ConfigParser()
        cfg["spread_gui"] = state
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CONFIG_PATH, "wt") as fh:
                cfg.write(fh)
        except Exception as e:
            self._log(f"Warning: could not save settings to {_CONFIG_PATH}: {e}")
        if self._current_crystal_id is not None:
            try:
                self._db.update_crystal(self._current_crystal_id, state)
            except Exception as e:
                self._log(f"Warning: could not save crystal to database: {e}")
        self._dirty = False
        self._emit_processing_info()

    # ---------------- Crystal context ----------------

    @property
    def current_crystal_id(self) -> Optional[int]:
        return self._current_crystal_id

    def load_crystal(self, crystal_id: int) -> None:
        """Load a crystal from the database into the form."""
        settings = self._db.get_crystal_settings(crystal_id)
        self._loading = True
        self._apply_form_state(settings)
        self._loading = False
        self._current_crystal_id = crystal_id
        self._dirty = False
        self.save_settings()           # sync INI immediately
        self._refresh_previews_and_validation()
        self._schedule_energy_scan()
        project_name, crystal_name = self._db.get_crystal_info(crystal_id)
        self.crystal_context_changed.emit(project_name, crystal_name)
        self._emit_processing_info()

    # ---------------- UI helpers ----------------
    def _schedule_autosave(self) -> None:
        if not self._loading:
            self._dirty = True
            self._autosave_timer.start(1000)

    def _log(self, msg: str) -> None:
        self.log_message.emit(msg)

    def _warn(self, title: str, msg: str) -> None:
        QMessageBox.warning(self, title, msg)

    def _info(self, title: str, msg: str) -> None:
        QMessageBox.information(self, title, msg)

    def _update_energy_mode(self) -> None:
        range_mode = self.rb_energy_range.isChecked()
        auto = self.chk_auto_energies.isChecked()
        self.energy_start.setEnabled(range_mode and not auto)
        self.energy_end.setEnabled(range_mode and not auto)
        self.energy_inc.setEnabled(range_mode and not auto)
        self.energy_list_edit.setEnabled((not range_mode) and (not auto))
        self._refresh_previews_and_validation()

    # ---------------- Auto-energy detection ----------------
    def _auto_energies_toggled(self, enabled: bool) -> None:
        if enabled:
            self.rb_energy_list.setChecked(True)
            self._update_energy_mode()
            self._schedule_energy_scan()
        else:
            self._update_energy_mode()
            self._refresh_previews_and_validation()

    def _auto_wedges_toggled(self, enabled: bool) -> None:
        self.wedge_size.setEnabled(not enabled)
        self.total_images.setEnabled(not enabled)
        if enabled:
            self._schedule_energy_scan()
        else:
            self._refresh_previews_and_validation()

    def _schedule_energy_scan(self) -> None:
        if not self.chk_auto_energies.isChecked() and not self.chk_auto_wedges.isChecked():
            return
        self._energy_scan_timer.start(300)

    def _set_watched_directory(self, path: str) -> None:
        path = path.strip()
        if not path or not os.path.isdir(path):
            if self._watched_data_dir:
                try:
                    self._fs_watcher.removePath(self._watched_data_dir)
                except Exception:
                    pass
                self._watched_data_dir = None
            return

        if self._watched_data_dir == path:
            return

        if self._watched_data_dir:
            try:
                self._fs_watcher.removePath(self._watched_data_dir)
            except Exception:
                pass

        try:
            self._fs_watcher.addPath(path)
            self._watched_data_dir = path
        except Exception:
            self._watched_data_dir = None

    def _auto_update_energies_from_data_dir(self) -> None:
        data_dir = self._data_path
        self._set_watched_directory(data_dir)

        scan_energies = self.chk_auto_energies.isChecked()
        scan_wedges   = self.chk_auto_wedges.isChecked()
        if not scan_energies and not scan_wedges:
            return
        if not data_dir or not os.path.isdir(data_dir):
            return

        # If a previous scan is still running, let it finish — its signals
        # will be ignored if the data_dir has changed by the time they arrive.
        if self._scan_worker and self._scan_worker.isRunning():
            return

        self._scan_worker = _DataScanWorker(data_dir, scan_energies, scan_wedges)
        self._scan_worker.energies_found.connect(
            lambda energies, d=data_dir: self._on_energies_found(energies, d)
        )
        self._scan_worker.wedge_found.connect(
            lambda wedge, total, nsweeps, diag, d=data_dir: self._on_wedge_found(wedge, total, nsweeps, diag, d)
        )
        self._scan_worker.start()

    def _on_energies_found(self, energies: List[int], data_dir: str) -> None:
        if data_dir != self._data_path:
            return  # stale result from a previous data dir
        if not energies:
            if self._last_auto_energies != []:
                self._last_auto_energies = []
                self.energy_preview.setText("Energies (auto): no matching files found in Data path.")
                self._log(f"Auto-energy scan: no energies found in {data_dir}")
        else:
            self.rb_energy_list.setChecked(True)
            self._update_energy_mode()
            self.energy_list_edit.setText(", ".join(str(e) for e in energies))
            self.energy_preview.setText(
                f"Energies (auto, n={len(energies)}): "
                + ", ".join(str(e) for e in energies[:10])
                + (" …" if len(energies) > 10 else "")
            )
            if self._last_auto_energies != energies:
                self._last_auto_energies = energies
                self._log(f"Auto-detected energies from {data_dir}: {', '.join(str(e) for e in energies)}")
        self._refresh_previews_and_validation()

    def _on_wedge_found(self, wedge: int, total: int, num_sweeps: int, diag: str, data_dir: str) -> None:
        if data_dir != self._data_path:
            return  # stale result from a previous data dir
        if wedge > 0:
            self._log(f"Auto-wedge: {diag}, {num_sweeps} sweep(s), {total} frames/sweep")
            if self.wedge_size.value() != wedge:
                self.wedge_size.setValue(wedge)
            if total > 0 and self.total_images.value() != total:
                self.total_images.setValue(total)
            self._num_sweeps = num_sweeps
        else:
            self._log(f"Auto-wedge: {diag}")
        self._refresh_previews_and_validation()

    # ---------------- Validation & previews ----------------

    def _refresh_previews_and_validation(self) -> None:

        energies = compute_energy_list(
            self.rb_energy_range.isChecked(),
            float(self.energy_start.value()),
            float(self.energy_end.value()),
            float(self.energy_inc.value()),
            self.energy_list_edit.text(),
        )
        wedges = compute_wedges(int(self.wedge_size.value()), int(self.total_images.value()))

        if not self.chk_auto_energies.isChecked():
            if energies:
                self.energy_preview.setText(
                    f"Energies (n={len(energies)}): "
                    + ", ".join(str(e) for e in energies[:10])
                    + (" …" if len(energies) > 10 else "")
                )
            else:
                self.energy_preview.setText("Energies: (none / invalid)")

        if wedges:
            total_per_sweep = int(self.total_images.value())
            all_wedges: List[int] = []
            for sw in range(self._num_sweeps):
                all_wedges.extend(sw * total_per_sweep + w for w in wedges)
            preview_vals = all_wedges[:12]
            suffix = " …" if len(all_wedges) > 12 else ""
            sweep_note = f", {self._num_sweeps} sweep(s)" if self._num_sweeps > 1 else ""
            self.wedge_preview.setText(
                f"seq {wedges[0]} {wedges[0]} {wedges[-1]}{sweep_note} → "
                f"{len(all_wedges)} jobs/energy "
                f"(preview: {', '.join(map(str, preview_vals))}{suffix})"
            )
        else:
            self.wedge_preview.setText("list_of_wedges: (none / invalid)")

    # ---------------- Pipeline selection ----------------
    def _pipeline_key(self) -> str:
        if self.rb_xia2_dials.isChecked():
            return "xia2_dials"
        if self.rb_xia2_3dii.isChecked():
            return "xia2_3dii"
        return "autoproc"

    # ---------------- Script generation ----------------
    def _script_paths(self) -> Tuple[str, str]:
        """Return (driver_script_path, pipeline_job_script_path) for the selected pipeline."""
        proc_dir = self._proc_path or os.getcwd()
        scripts_dir = os.path.join(proc_dir, "scripts")
        files_dir = os.path.join(proc_dir, "files")
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        submit_script = os.path.join(scripts_dir, "run_spread_submit.sh")
        pipeline_filename = {
            "autoproc":   "autoproc_jobs.sh",
            "xia2_dials": "xia2_dials_jobs.sh",
            "xia2_3dii":  "xia2_3dii_jobs.sh",
        }[self._pipeline_key()]
        pipeline_script = os.path.join(scripts_dir, pipeline_filename)
        return submit_script, pipeline_script

    def _validate_before_generate(self) -> Tuple[List[int], List[int]]:
        visit = self._visit.strip()
        if visit and not VISIT_RE.match(visit):
            raise ValueError("Visit format is invalid.")

        energies = compute_energy_list(
            self.rb_energy_range.isChecked(),
            float(self.energy_start.value()),
            float(self.energy_end.value()),
            float(self.energy_inc.value()),
            self.energy_list_edit.text(),
        )
        if not energies:
            raise ValueError("No valid energies defined.")

        wedges = compute_wedges(int(self.wedge_size.value()), int(self.total_images.value()))
        if not wedges:
            raise ValueError("No valid wedges defined.")

        if not self._data_path:
            raise ValueError("Data path is empty.")
        if not self._proc_path:
            raise ValueError("Processing path is empty.")

        return energies, wedges

    def _make_driver_script(
        self,
        energies: List[int],
        wedges: List[int],
        pipeline_script: str,
        data_dir: str,
        total_images: int,
    ) -> str:
        energy_list = " ".join(str(e) for e in energies)
        wedge_list = " ".join(str(w) for w in wedges)
        return f"""#!/bin/bash
BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
ENERGY_LIST="{energy_list}"
WEDGE_LIST="{wedge_list}"
TOTAL_IMAGES={total_images}
counter=0
for energy in ${{ENERGY_LIST}}; do
  counter=$((counter+1))
  # Discover all sweeps for this energy/counter
  sweeps=()
  [ -f "${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf" ] && sweeps+=("primary")
  for extra in ${{DATA_DIR}}/${{energy}}_[0-9]*_E${{counter}}_1_00001.cbf; do
    [ -f "$extra" ] && sweeps+=("$extra")
  done
  sweep_index=0
  for sweep in "${{sweeps[@]}}"; do
    for images in ${{WEDGE_LIST}}; do
      cumulative=$((sweep_index * TOTAL_IMAGES + images))
      sbatch scripts/{pipeline_script} "$energy" "$cumulative" "$counter"
    done
    sweep_index=$((sweep_index + 1))
  done
done
"""

    def _make_autoproc_job(self, data_dir: str, cell: Cell, sg: str, total_images: int) -> str:
        return f"""#!/bin/bash
. /etc/profile.d/modules.sh
#SBATCH --job-name=autoPROC_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=10G
module load autoPROC
BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
TOTAL_IMAGES={total_images}
energy=$1
cumulative=$2
counter=$3
sweep_index=$(( (cumulative - 1) / TOTAL_IMAGES ))
images_in_current=$(( (cumulative - 1) % TOTAL_IMAGES + 1 ))
energy_dir="${{BASE_DIR}}/${{energy}}eV"
images_dir="${{energy_dir}}/${{cumulative}}img"
mkdir -p "$images_dir"
cd "$images_dir" || exit 1
[ -f "aP.log" ] && rm "aP.log"
rm -rf autoPROC
sweeps=()
[ -f "${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf" ] && sweeps+=("${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf")
for extra in ${{DATA_DIR}}/${{energy}}_[0-9]*_E${{counter}}_1_00001.cbf; do
    [ -f "$extra" ] && sweeps+=("$extra")
done
if [ ${{#sweeps[@]}} -eq 0 ]; then
    echo "No CBF files found for energy ${{energy}} eV (E${{counter}})" >&2
    exit 1
fi
id_args=()
for i in "${{!sweeps[@]}}"; do
    base=$(basename "${{sweeps[$i]}}")
    template="${{base/_00001.cbf/_#####.cbf}}"
    if [ "$i" -lt "$sweep_index" ]; then
        id_args+=("-Id" "name,${{DATA_DIR}},$template,1,${{TOTAL_IMAGES}}")
    elif [ "$i" -eq "$sweep_index" ]; then
        id_args+=("-Id" "name,${{DATA_DIR}},$template,1,${{images_in_current}}")
    fi
done
process -M DiamondI23 \\
  "${{id_args[@]}}" \\
  -d autoPROC \\
  cell="{cell.as_autoproc_string()}" \\
  symm="{sg}" > aP.log
"""

    def _make_xia2_dials_job(self, data_dir: str, cell: Cell, sg: str, project: str, crystal: str, total_images: int) -> str:
        return f"""#!/bin/bash
. /etc/profile.d/modules.sh
#SBATCH --job-name=xia2_dials_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=10G
module load xia2
BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
TOTAL_IMAGES={total_images}
energy=$1
cumulative=$2
counter=$3
sweep_index=$(( (cumulative - 1) / TOTAL_IMAGES ))
images_in_current=$(( (cumulative - 1) % TOTAL_IMAGES + 1 ))
energy_dir="${{BASE_DIR}}/${{energy}}eV"
images_dir="${{energy_dir}}/${{cumulative}}img"
mkdir -p "$images_dir"
cd "$images_dir" || exit 1
mkdir -p xia2-dials
cd xia2-dials
sweeps=()
[ -f "${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf" ] && sweeps+=("${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf")
for extra in ${{DATA_DIR}}/${{energy}}_[0-9]*_E${{counter}}_1_00001.cbf; do
    [ -f "$extra" ] && sweeps+=("$extra")
done
if [ ${{#sweeps[@]}} -eq 0 ]; then
    echo "No CBF files found for energy ${{energy}} eV (E${{counter}})" >&2
    exit 1
fi
image_args=()
for i in "${{!sweeps[@]}}"; do
    if [ "$i" -lt "$sweep_index" ]; then
        image_args+=("image=${{sweeps[$i]}}:1:${{TOTAL_IMAGES}}")
    elif [ "$i" -eq "$sweep_index" ]; then
        image_args+=("image=${{sweeps[$i]}}:1:${{images_in_current}}")
    fi
done
xia2 pipeline=dials \\
  "${{image_args[@]}}" \\
  read_all_image_headers=False \\
  trust_beam_centre=True \\
  keep_outliers=True \\
  anomalous=True \\
  space_group="{sg}" \\
  unit_cell="{cell.as_autoproc_string()}" \\
  project={project} \\
  crystal={crystal}
"""

    def _make_xia2_3dii_job(self, data_dir: str, cell: Cell, sg: str, project: str, crystal: str, total_images: int) -> str:
        return f"""#!/bin/bash
. /etc/profile.d/modules.sh
#SBATCH --job-name=xia2_3dii_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=10G
module load xia2
BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
TOTAL_IMAGES={total_images}
energy=$1
cumulative=$2
counter=$3
sweep_index=$(( (cumulative - 1) / TOTAL_IMAGES ))
images_in_current=$(( (cumulative - 1) % TOTAL_IMAGES + 1 ))
energy_dir="${{BASE_DIR}}/${{energy}}eV"
images_dir="${{energy_dir}}/${{cumulative}}img"
mkdir -p "$images_dir"
cd "$images_dir" || exit 1
mkdir -p xia2-3dii
cd xia2-3dii
sweeps=()
[ -f "${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf" ] && sweeps+=("${{DATA_DIR}}/${{energy}}_E${{counter}}_1_00001.cbf")
for extra in ${{DATA_DIR}}/${{energy}}_[0-9]*_E${{counter}}_1_00001.cbf; do
    [ -f "$extra" ] && sweeps+=("$extra")
done
if [ ${{#sweeps[@]}} -eq 0 ]; then
    echo "No CBF files found for energy ${{energy}} eV (E${{counter}})" >&2
    exit 1
fi
image_args=()
for i in "${{!sweeps[@]}}"; do
    if [ "$i" -lt "$sweep_index" ]; then
        image_args+=("image=${{sweeps[$i]}}:1:${{TOTAL_IMAGES}}")
    elif [ "$i" -eq "$sweep_index" ]; then
        image_args+=("image=${{sweeps[$i]}}:1:${{images_in_current}}")
    fi
done
xia2 pipeline=3dii \\
  "${{image_args[@]}}" \\
  read_all_image_headers=False \\
  trust_beam_centre=True \\
  keep_outliers=True \\
  anomalous=True \\
  space_group="{sg}" \\
  unit_cell="{cell.as_autoproc_string()}" \\
  project={project} \\
  crystal={crystal}
"""

    # ---------------- New project ----------------
    # ---------------- SSH key helpers ----------------
    def _check_ssh_key_status(self) -> None:
        ok, err = check_ssh_key_auth()
        if ok:
            self.ssh_key_status.setText("SSH key: OK (passwordless)")
            self.ssh_key_status.setStyleSheet("color:green;")
            self.ssh_key_status.setToolTip("")
            self.btn_setup_ssh_key.setText("Re-setup SSH key…")
        else:
            self.ssh_key_status.setText("SSH key: auth failed — see tooltip")
            self.ssh_key_status.setStyleSheet("color:#b05000;")
            self.ssh_key_status.setToolTip(err or "SSH returned a non-zero exit code")
            self.btn_setup_ssh_key.setText("Setup SSH key…")

    def _setup_ssh_key(self) -> None:
        password, ok = QInputDialog.getText(
            self,
            "Wilson SSH password",
            "Enter your Wilson (DLS) password to copy your SSH key:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not password:
            return
        try:
            setup_ssh_key(password)
        except Exception as e:
            self._warn("SSH key setup failed", str(e))
            return
        self._check_ssh_key_status()
        self._log("SSH key copied to Wilson — future submissions will not require a password.")

    def generate_scripts(self) -> None:
        try:
            energies, wedges = self._validate_before_generate()
        except Exception as e:
            self._warn("Invalid inputs", str(e))
            return

        data_dir = self._data_path
        proc_dir = self._proc_path
        cell = self._cell or Cell(0, 0, 0, 0, 0, 0)
        sg = normalize_sg_name(self._space_group) if self._space_group else ""
        project = self._project.strip() or "PROJECT"
        crystal = self._crystal.strip() or "CRYSTAL"

        pipeline = self._pipeline_key()
        total_images = int(self.total_images.value())

        pipeline_script_name = {
            "autoproc":   "autoproc_jobs.sh",
            "xia2_dials": "xia2_dials_jobs.sh",
            "xia2_3dii":  "xia2_3dii_jobs.sh",
        }[pipeline]
        pipeline_content = {
            "autoproc":   self._make_autoproc_job(data_dir, cell, sg, total_images),
            "xia2_dials": self._make_xia2_dials_job(data_dir, cell, sg, project, crystal, total_images),
            "xia2_3dii":  self._make_xia2_3dii_job(data_dir, cell, sg, project, crystal, total_images),
        }[pipeline]

        submit_script, pipeline_script = self._script_paths()
        driver = self._make_driver_script(energies, wedges, pipeline_script_name, data_dir, total_images)

        try:
            with open(submit_script, "wt") as f:
                f.write(driver)
            with open(pipeline_script, "wt") as f:
                f.write(pipeline_content)
            chmod_x(submit_script)
            chmod_x(pipeline_script)
        except Exception as e:
            self._warn("Write failed", f"Could not write scripts to:\n{proc_dir}\n\n{e}")
            return

        self._log(f"Scripts generated in {proc_dir}")
        self._log(f" - {submit_script}")
        self._log(f" - {pipeline_script}")

    @staticmethod
    def _parse_slurm_job_id(rc: int, out: str, use_rest: bool) -> Optional[int]:
        """Extract the SLURM job id from a submission response, or return None."""
        try:
            if use_rest and rc == 200:
                data = json.loads(out)
                return int(data.get("job_id") or data.get("jobId") or 0) or None
            elif not use_rest:
                # sbatch prints "Submitted batch job 12345"
                m = re.search(r"Submitted batch job (\d+)", out)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None

    def submit_jobs(self) -> None:
        try:
            _, _ = self._validate_before_generate()
        except Exception as e:
            self._warn("Invalid inputs", str(e))
            return

        proc_dir = self._proc_path
        pipeline_output_dir = {
            "autoproc":   "autoPROC",
            "xia2_dials": "xia2-dials",
            "xia2_3dii":  "xia2-3dii",
        }[self._pipeline_key()]
        existing = glob.glob(os.path.join(proc_dir, "*eV", "*img", pipeline_output_dir))
        if existing:
            ans = QMessageBox.question(
                self,
                "Processing already exists",
                f"Output from a previous {pipeline_output_dir} run was found in "
                f"{len(existing)} director{'y' if len(existing) == 1 else 'ies'}:\n{proc_dir}\n\n"
                "Delete existing output and re-submit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

            # Remove existing pipeline output directories.
            errors = []
            for d in existing:
                try:
                    shutil.rmtree(d)
                    self._log(f"Deleted: {d}")
                except Exception as exc:
                    errors.append(f"{d}: {exc}")
            if errors:
                self._warn("Deletion failed", "\n".join(errors))
                return

            # Clear old job records for this crystal + pipeline from the DB.
            if self._current_crystal_id is not None:
                self._db.delete_jobs(self._current_crystal_id, self._pipeline_key())
                self.jobs_status_changed.emit()

        self.generate_scripts()

        try:
            energies, wedges = self._validate_before_generate()
        except Exception as e:
            self._warn("Invalid inputs", str(e))
            return

        proc_dir = self._proc_path
        pipeline = self._pipeline_key()
        dry = self.rb_submit_dry.isChecked()

        _, script_path = self._script_paths()
        if not os.path.isfile(script_path):
            self._warn("Missing script", f"Job script not found:\n{script_path}")
            return

        # Build full job list: (energy, cumulative_images, counter)
        total_images_per_sweep = int(self.total_images.value())
        jobs: List[Tuple[int, int, int]] = []
        counter = 0
        for e in energies:
            counter += 1
            sweeps = detect_sweeps_for_energy(self._data_path, e, counter)
            for sweep_idx in range(len(sweeps)):
                offset = sweep_idx * total_images_per_sweep
                for a in wedges:
                    jobs.append((e, offset + a, counter))

        total = len(jobs)
        if total <= 0:
            self._warn("Nothing to submit", "No jobs to submit.")
            return

        mw = self.window()

        if dry:
            # Dry run: scripts are already generated — just tell the user where they are.
            scripts_dir = os.path.join(proc_dir, "scripts")
            self._log("")
            self._log("Dry run — scripts generated, nothing submitted.")
            self._log(f"Scripts are in: {scripts_dir}")
            self._log("")
            self._log("To submit manually, open a terminal on Wilson and run:")
            self._log(f"  cd {shlex.quote(proc_dir)}")
            self._log(f"  bash scripts/run_spread_submit.sh")
            self._log("")
            self._log(f"This will submit {total} job(s) via sbatch.")
            if hasattr(mw, "set_status"):
                mw.set_status("Dry run — scripts ready.", total, total)
            self.jobs_status_changed.emit()
            return

        # REST API submission
        if hasattr(mw, "set_status"):
            mw.set_status("Fetching SLURM token…", 0, total)
        try:
            token = get_slurm_jwt()
            self._log("SLURM JWT token acquired.")
        except Exception as e:
            self._warn(
                "Token error",
                f"Could not obtain SLURM JWT via SSH to wilson:\n\n{e}",
            )
            return

        if hasattr(mw, "set_status"):
            mw.set_status(f"Submitting {total} jobs…", 0, total)

        crystal_id = self._current_crystal_id
        submitted = 0
        for e, cumulative, ctr in jobs:
            submitted += 1
            if hasattr(mw, "set_status"):
                mw.set_status("Submitting jobs…", submitted, total)

            wrapper = (
                f"#!/bin/bash\n"
                f"bash {shlex.quote(script_path)} {e} {cumulative} {ctr}\n"
            )
            rc, out, err = submit_job_via_rest_api(wrapper, proc_dir, token)

            if rc in (0, 200):
                self._log(f"Submitted job {submitted}/{total}: {out.strip()}")
            else:
                self._log(f"Submission failed (rc={rc}): {err or out}")

            if crystal_id is not None and rc in (0, 200):
                slurm_id = self._parse_slurm_job_id(rc, out, use_rest=True)
                self._db.add_job(
                    crystal_id=crystal_id,
                    pipeline=pipeline,
                    proc_dir=proc_dir,
                    energy_ev=e,
                    cumulative=cumulative,
                    dry_run=False,
                    slurm_job_id=slurm_id,
                )

        if hasattr(mw, "set_status"):
            mw.set_status("Done.", total, total)
        self._info("Submission complete", f"Submitted {total} job(s).")
        self.jobs_status_changed.emit()
