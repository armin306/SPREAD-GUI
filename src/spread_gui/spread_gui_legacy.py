#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SPREAD processing pipeline GUI (PyQt6)

Key additions in this version:
- Auto-detect energies from filenames in selected Data path directory
- Dynamic update when Data path changes (debounced) and when directory contents change (QFileSystemWatcher)
- Energies treated as INTEGERS throughout (no 12600.0)
- Wedge defaults: wedge size=30, total images=360
- Processing tab in scroll area for small screens
"""

from __future__ import annotations

import os
import re
import sys
import stat
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------
# Qt/X11 robustness for DLS / remote X11 sessions
# ---------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")

try:
    import gemmi  # type: ignore
except Exception:
    gemmi = None

try:
    import requests  # type: ignore
except Exception:
    requests = None

from PyQt6.QtCore import Qt, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTabWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QGroupBox,
    QRadioButton,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QTextEdit,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QScrollArea,
    QCheckBox,
)

# Visit format: mx39247-20, nr12345-1 etc.
VISIT_RE = re.compile(r"^[a-z]{2}[0-9]{5}-[0-9]{1,3}$")


@dataclass
class Cell:
    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float

    def as_autoproc_string(self) -> str:
        return f"{self.a:.4f} {self.b:.4f} {self.c:.4f} {self.alpha:.2f} {self.beta:.2f} {self.gamma:.2f}"


def chmod_x(path: str) -> None:
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def detect_visit_from_path(path: str) -> Optional[str]:
    parts = os.path.abspath(path).split(os.sep)
    for p in reversed(parts):
        if VISIT_RE.match(p):
            return p
    return None


def infer_visit_root(cwd: str, visit: str) -> Optional[str]:
    parts = os.path.abspath(cwd).split(os.sep)
    if visit not in parts:
        return None
    idx = parts.index(visit)
    if parts and parts[0] == "":
        return os.sep + os.path.join(*parts[1:idx + 1])
    return os.path.join(*parts[:idx + 1])


def normalize_sg_name(s: str) -> str:
    s = s.strip().replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    return s


# -------------------------
# Energies as INTEGERS
# -------------------------
def compute_energy_list(range_mode: bool, start: float, end: float, inc: float, list_str: str) -> List[int]:
    """
    Return integer energies. Any floats entered are rounded to nearest int.
    This ensures filenames and directory names use integer energies consistently.
    """
    energies: List[int] = []

    if range_mode:
        if inc <= 0:
            return []
        x = start
        steps = 0
        # generate values then round to int
        while x <= end + 1e-9 and steps < 100000:
            energies.append(int(round(x)))
            x += inc
            steps += 1
    else:
        for tok in re.split(r"[,\s]+", list_str.strip()):
            if not tok:
                continue
            try:
                energies.append(int(round(float(tok))))
            except ValueError:
                pass

    # unique + sorted
    return sorted(set(energies))


def compute_wedges(wedge_size: int, total_images: int) -> List[int]:
    if wedge_size <= 0 or total_images <= 0:
        return []
    return list(range(wedge_size, total_images + 1, wedge_size))


def parse_pdb_cryst1(pdb_path: str) -> Tuple[Optional[Cell], Optional[str]]:
    cell = None
    sg = None
    try:
        with open(pdb_path, "rt", errors="replace") as fh:
            for line in fh:
                if line.startswith("CRYST1"):
                    a = float(line[6:15].strip())
                    b = float(line[15:24].strip())
                    c = float(line[24:33].strip())
                    alpha = float(line[33:40].strip())
                    beta = float(line[40:47].strip())
                    gamma = float(line[47:54].strip())
                    sg_field = line[55:66].strip()
                    cell = Cell(a, b, c, alpha, beta, gamma)
                    sg = sg_field if sg_field else None
                    break
    except Exception:
        return None, None
    return cell, sg


def all_spacegroups_230() -> List[str]:
    if gemmi is not None:
        out: List[str] = []
        for i in range(1, 231):
            try:
                if hasattr(gemmi, "find_spacegroup_by_number"):
                    sg = gemmi.find_spacegroup_by_number(i)
                else:
                    sg = gemmi.SpaceGroup(i)
                out.append(sg.hm)
            except Exception:
                out.append(str(i))
        # de-duplicate
        seen = set()
        dedup = []
        for s in out:
            if s not in seen:
                dedup.append(s)
                seen.add(s)
        return dedup[:230]
    return ["P 1", "P -1", "P 21 21 21", "C 2", "P 2 2 2", "P 4", "I 4", "R 3", "P 6", "P 23", "F 4 3 2"]


def cell_is_compatible_with_sg(cell: Cell, sg_name: str) -> Tuple[bool, str]:
    if gemmi is None:
        return True, "gemmi not available – compatibility check disabled."
    try:
        if hasattr(gemmi, "find_spacegroup_by_name"):
            sg = gemmi.find_spacegroup_by_name(sg_name)
        else:
            sg = gemmi.SpaceGroup(sg_name)
        uc = gemmi.UnitCell(cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma)
        if hasattr(uc, "is_compatible_with_spacegroup"):
            ok = bool(uc.is_compatible_with_spacegroup(sg))
            return ok, ("Unit cell is compatible with selected space group." if ok
                        else "Unit cell NOT compatible with selected space group.")
        return True, "Compatibility API not available in this gemmi build."
    except Exception as e:
        return False, f"Compatibility check failed: {e}"


class ProcessingTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        # Auto-energy mechanisms
        self._energy_scan_timer = QTimer(self)
        self._energy_scan_timer.setSingleShot(True)
        self._energy_scan_timer.timeout.connect(self._auto_update_energies_from_data_dir)

        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(lambda _: self._schedule_energy_scan())
        self._watched_data_dir: Optional[str] = None
        self._last_auto_energies: Optional[List[int]] = None

        self._build_ui()
        self._wire_signals()
        self._apply_defaults_from_cwd()
        self._refresh_previews_and_validation()
        self._schedule_energy_scan()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("SPREAD – Processing")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Visit / Project
        visit_box = QGroupBox("Visit / Project")
        g = QGridLayout(visit_box)
        g.addWidget(QLabel("Visit:"), 0, 0)
        self.visit_edit = QLineEdit()
        self.visit_edit.setPlaceholderText("e.g. mx39247-20")
        g.addWidget(self.visit_edit, 0, 1)
        self.visit_hint = QLabel("")
        self.visit_hint.setStyleSheet("color:#666;")
        g.addWidget(self.visit_hint, 0, 2)

        g.addWidget(QLabel("Project name:"), 1, 0)
        self.project_edit = QLineEdit()
        g.addWidget(self.project_edit, 1, 1, 1, 2)

        g.addWidget(QLabel("Crystal name:"), 2, 0)
        self.crystal_edit = QLineEdit()
        g.addWidget(self.crystal_edit, 2, 1, 1, 2)
        root.addWidget(visit_box)

        # PDB input
        pdb_box = QGroupBox("PDB input")
        v = QVBoxLayout(pdb_box)
        rb_row = QHBoxLayout()
        self.rb_pdb_file = QRadioButton("Upload local PDB")
        self.rb_pdb_code = QRadioButton("Enter PDB code and fetch")
        self.rb_pdb_file.setChecked(True)
        rb_row.addWidget(self.rb_pdb_file)
        rb_row.addWidget(self.rb_pdb_code)
        rb_row.addStretch(1)
        v.addLayout(rb_row)

        file_row = QHBoxLayout()
        self.pdb_path_edit = QLineEdit()
        self.pdb_path_edit.setPlaceholderText("Select a .pdb file")
        self.btn_browse_pdb = QPushButton("Browse…")
        file_row.addWidget(self.pdb_path_edit, 1)
        file_row.addWidget(self.btn_browse_pdb)
        v.addLayout(file_row)

        code_row = QHBoxLayout()
        self.pdb_code_edit = QLineEdit()
        self.pdb_code_edit.setPlaceholderText("e.g. 1ABC")
        self.btn_fetch_pdb = QPushButton("Fetch")
        self.pdb_fetch_status = QLabel("")
        self.pdb_fetch_status.setStyleSheet("color:#666;")
        code_row.addWidget(self.pdb_code_edit)
        code_row.addWidget(self.btn_fetch_pdb)
        code_row.addWidget(self.pdb_fetch_status)
        code_row.addStretch(1)
        v.addLayout(code_row)
        root.addWidget(pdb_box)

        # SG + cell
        sg_box = QGroupBox("Space group and unit cell (editable)")
        sg = QGridLayout(sg_box)
        sg.addWidget(QLabel("Space group:"), 0, 0)
        self.sg_combo = QComboBox()
        self.sg_combo.addItems(all_spacegroups_230())
        sg.addWidget(self.sg_combo, 0, 1, 1, 4)
        self.sg_source = QLabel("")
        self.sg_source.setStyleSheet("color:#666;")
        sg.addWidget(self.sg_source, 0, 5)

        labels = ["a", "b", "c", "α", "β", "γ"]
        self.cell_spins: List[QDoubleSpinBox] = []
        for i, lab in enumerate(labels):
            sg.addWidget(QLabel(f"{lab}:"), 1 + i // 3, (i % 3) * 2)
            sp = QDoubleSpinBox()
            sp.setDecimals(4 if i < 3 else 3)
            sp.setRange(0.0, 10000.0 if i < 3 else 180.0)
            sp.setSingleStep(0.1)
            self.cell_spins.append(sp)
            sg.addWidget(sp, 1 + i // 3, (i % 3) * 2 + 1)

        self.compat_label = QLabel("")
        self.compat_label.setStyleSheet("color:#666;")
        sg.addWidget(self.compat_label, 3, 0, 1, 6)
        root.addWidget(sg_box)

        # Paths
        path_box = QGroupBox("Paths")
        pg = QGridLayout(path_box)
        pg.addWidget(QLabel("Data path:"), 0, 0)
        self.data_path_edit = QLineEdit()
        self.btn_browse_data = QPushButton("Browse…")
        pg.addWidget(self.data_path_edit, 0, 1)
        pg.addWidget(self.btn_browse_data, 0, 2)

        pg.addWidget(QLabel("Processing path:"), 1, 0)
        self.proc_path_edit = QLineEdit()
        self.btn_browse_proc = QPushButton("Browse…")
        pg.addWidget(self.proc_path_edit, 1, 1)
        pg.addWidget(self.btn_browse_proc, 1, 2)
        root.addWidget(path_box)

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
        self.energy_start = QDoubleSpinBox(); self.energy_start.setRange(0, 200000); self.energy_start.setDecimals(3)
        self.energy_end = QDoubleSpinBox(); self.energy_end.setRange(0, 200000); self.energy_end.setDecimals(3)
        self.energy_inc = QDoubleSpinBox(); self.energy_inc.setRange(0.001, 200000); self.energy_inc.setDecimals(3)
        self.energy_start.setValue(12600.0)
        self.energy_end.setValue(12610.0)
        self.energy_inc.setValue(1.0)
        eg.addWidget(QLabel("Start:"), 0, 0); eg.addWidget(self.energy_start, 0, 1)
        eg.addWidget(QLabel("End:"), 0, 2); eg.addWidget(self.energy_end, 0, 3)
        eg.addWidget(QLabel("Increment:"), 0, 4); eg.addWidget(self.energy_inc, 0, 5)
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
        wg.addWidget(QLabel("Wedge size:"), 0, 0)
        self.wedge_size = QSpinBox(); self.wedge_size.setRange(1, 1000000); self.wedge_size.setValue(30)
        wg.addWidget(self.wedge_size, 0, 1)

        wg.addWidget(QLabel("Total number of images:"), 0, 2)
        self.total_images = QSpinBox(); self.total_images.setRange(1, 100000000); self.total_images.setValue(360)
        wg.addWidget(self.total_images, 0, 3)

        self.wedge_preview = QLabel("")
        self.wedge_preview.setStyleSheet("color:#666;")
        wg.addWidget(self.wedge_preview, 1, 0, 1, 4)
        root.addWidget(w_box)

        # Pipeline selection
        p_box = QGroupBox("Processing pipeline")
        pr = QHBoxLayout(p_box)
        self.rb_autoproc = QRadioButton("AutoProc")
        self.rb_xia2_dials = QRadioButton("Xia2 DIALS")
        self.rb_xia2_3dii = QRadioButton("Xia2 3dii")
        self.rb_autoproc.setChecked(True)
        pr.addWidget(self.rb_xia2_dials)
        pr.addWidget(self.rb_xia2_3dii)
        pr.addWidget(self.rb_autoproc)
        pr.addStretch(1)
        root.addWidget(p_box)

        # Actions
        a_row = QHBoxLayout()
        self.btn_generate = QPushButton("Generate scripts")
        self.btn_submit = QPushButton("Submit jobs (sbatch)")
        self.chk_dry_run = QCheckBox("Dry run (don’t call sbatch)")
        self.chk_dry_run.setChecked(True)
        a_row.addWidget(self.btn_generate)
        a_row.addWidget(self.btn_submit)
        a_row.addWidget(self.chk_dry_run)
        a_row.addStretch(1)
        root.addLayout(a_row)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log…")
        root.addWidget(self.log, 1)

        self._update_pdb_mode()
        self._update_energy_mode()

    def _wire_signals(self):
        self.visit_edit.textChanged.connect(self._validate_visit)

        self.rb_pdb_file.toggled.connect(self._update_pdb_mode)
        self.rb_pdb_code.toggled.connect(self._update_pdb_mode)
        self.btn_browse_pdb.clicked.connect(self._browse_pdb)
        self.btn_fetch_pdb.clicked.connect(self._fetch_pdb)
        self.pdb_path_edit.editingFinished.connect(self._load_pdb_if_possible)

        self.sg_combo.currentTextChanged.connect(self._validate_cell_sg)
        for sp in self.cell_spins:
            sp.valueChanged.connect(self._validate_cell_sg)

        self.btn_browse_data.clicked.connect(lambda: self._browse_dir(self.data_path_edit))
        self.btn_browse_proc.clicked.connect(lambda: self._browse_dir(self.proc_path_edit))
        self.data_path_edit.textChanged.connect(self._schedule_energy_scan)
        self.data_path_edit.editingFinished.connect(self._schedule_energy_scan)

        self.rb_energy_range.toggled.connect(self._update_energy_mode)
        self.rb_energy_list.toggled.connect(self._update_energy_mode)
        self.chk_auto_energies.toggled.connect(self._auto_energies_toggled)
        self.energy_start.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_end.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_inc.valueChanged.connect(self._refresh_previews_and_validation)
        self.energy_list_edit.textChanged.connect(self._refresh_previews_and_validation)

        self.wedge_size.valueChanged.connect(self._refresh_previews_and_validation)
        self.total_images.valueChanged.connect(self._refresh_previews_and_validation)

        self.btn_generate.clicked.connect(self.generate_scripts)
        self.btn_submit.clicked.connect(self.submit_jobs)

    # ---------------- Defaults ----------------
    def _apply_defaults_from_cwd(self):
        cwd = os.getcwd()
        visit = detect_visit_from_path(cwd)
        if visit:
            self.visit_edit.setText(visit)
            self.visit_hint.setText(f"Default from current path: {visit}")
            root = infer_visit_root(cwd, visit)
            if root:
                self.data_path_edit.setText(root)
                self.proc_path_edit.setText(os.path.join(root, "processing", "SPREAD"))
        else:
            self.visit_hint.setText("No visit detected in current path (you can enter manually).")

    # ---------------- UI helpers ----------------
    def _log(self, msg: str):
        self.log.append(msg)
        self.log.ensureCursorVisible()

    def _warn(self, title: str, msg: str):
        QMessageBox.warning(self, title, msg)

    def _info(self, title: str, msg: str):
        QMessageBox.information(self, title, msg)

    def _update_pdb_mode(self):
        file_mode = self.rb_pdb_file.isChecked()
        self.pdb_path_edit.setEnabled(file_mode)
        self.btn_browse_pdb.setEnabled(file_mode)
        self.pdb_code_edit.setEnabled(not file_mode)
        self.btn_fetch_pdb.setEnabled(not file_mode)

    def _update_energy_mode(self):
        range_mode = self.rb_energy_range.isChecked()
        auto = self.chk_auto_energies.isChecked()

        self.energy_start.setEnabled(range_mode and not auto)
        self.energy_end.setEnabled(range_mode and not auto)
        self.energy_inc.setEnabled(range_mode and not auto)

        # list editable only if list mode and auto off
        self.energy_list_edit.setEnabled((not range_mode) and (not auto))

        self._refresh_previews_and_validation()

    def _browse_dir(self, target: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Select directory", target.text().strip() or os.getcwd())
        if d:
            target.setText(d)

    # ---------------- Auto-energy detection ----------------
    def _auto_energies_toggled(self, enabled: bool):
        if enabled:
            self.rb_energy_list.setChecked(True)
            self._update_energy_mode()
            self._schedule_energy_scan()
        else:
            self._update_energy_mode()
            self._refresh_previews_and_validation()

    def _schedule_energy_scan(self):
        if not self.chk_auto_energies.isChecked():
            return
        self._energy_scan_timer.start(300)

    def _set_watched_directory(self, path: str):
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

    def _detect_energies_in_dir(self, data_dir: str) -> List[int]:
        if not data_dir or not os.path.isdir(data_dir):
            return []

        # Pattern: <energy>_E<counter>_1_#####.cbf ; energy may appear as 12600 or 12600.0 -> round to int
        pat = re.compile(r"(?P<energy>\d+(?:\.\d+)?)_E\d+_1_", re.IGNORECASE)
        energies = set()

        try:
            with os.scandir(data_dir) as it:
                for entry in it:
                    if not entry.is_file():
                        continue
                    name = entry.name
                    if "_E" not in name:
                        continue
                    m = pat.search(name)
                    if not m:
                        continue
                    try:
                        energies.add(int(round(float(m.group("energy")))))
                    except ValueError:
                        pass
        except Exception:
            return []

        return sorted(energies)

    def _auto_update_energies_from_data_dir(self):
        data_dir = self.data_path_edit.text().strip()
        self._set_watched_directory(data_dir)

        energies = self._detect_energies_in_dir(data_dir)
        if not energies:
            if self._last_auto_energies != []:
                self._last_auto_energies = []
                self.energy_preview.setText("Energies (auto): no matching files found in Data path.")
                self._log(f"Auto-energy scan: no energies found in {data_dir}")
            return

        self.rb_energy_list.setChecked(True)
        self._update_energy_mode()

        # energies are ints -> show ints
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

    # ---------------- Validation & previews ----------------
    def _validate_visit(self):
        txt = self.visit_edit.text().strip()
        if txt == "":
            self.visit_edit.setStyleSheet("")
            return
        self.visit_edit.setStyleSheet("" if VISIT_RE.match(txt) else "border: 2px solid #c00;")

    def _current_cell(self) -> Cell:
        return Cell(
            a=float(self.cell_spins[0].value()),
            b=float(self.cell_spins[1].value()),
            c=float(self.cell_spins[2].value()),
            alpha=float(self.cell_spins[3].value()),
            beta=float(self.cell_spins[4].value()),
            gamma=float(self.cell_spins[5].value()),
        )

    def _validate_cell_sg(self):
        cell = self._current_cell()
        sg_name = normalize_sg_name(self.sg_combo.currentText())
        ok, msg = cell_is_compatible_with_sg(cell, sg_name)
        if ok:
            for sp in self.cell_spins:
                sp.setStyleSheet("")
            self.sg_combo.setStyleSheet("")
            self.compat_label.setStyleSheet("color:#2a7;")
        else:
            for sp in self.cell_spins:
                sp.setStyleSheet("border: 2px solid #c00;")
            self.sg_combo.setStyleSheet("border: 2px solid #c00;")
            self.compat_label.setStyleSheet("color:#c00;")
        self.compat_label.setText(msg)

    def _refresh_previews_and_validation(self):
        self._validate_visit()
        self._validate_cell_sg()

        energies = compute_energy_list(
            self.rb_energy_range.isChecked(),
            float(self.energy_start.value()),
            float(self.energy_end.value()),
            float(self.energy_inc.value()),
            self.energy_list_edit.text(),
        )
        wedges = compute_wedges(int(self.wedge_size.value()), int(self.total_images.value()))

        # If auto is enabled, the preview is maintained by the auto updater (keep it integer)
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
            self.wedge_preview.setText(
                f"list_of_wedges=$(seq {wedges[0]} {wedges[0]} {wedges[-1]}) "
                f"(preview: {', '.join(map(str, wedges[:12]))}{' …' if len(wedges)>12 else ''})"
            )
        else:
            self.wedge_preview.setText("list_of_wedges: (none / invalid)")

    # ---------------- PDB load / fetch ----------------
    def _browse_pdb(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Select PDB file", "", "PDB files (*.pdb *.ent);;All files (*)")
        if fn:
            self.pdb_path_edit.setText(fn)
            self._load_pdb_if_possible()

    def _load_pdb_if_possible(self):
        path = self.pdb_path_edit.text().strip()
        if not path:
            return
        if not os.path.isfile(path):
            self._warn("PDB", f"File not found:\n{path}")
            return
        self._apply_pdb_header(path)

    def _fetch_pdb(self):
        code = self.pdb_code_edit.text().strip().upper()
        if not re.match(r"^[0-9][A-Z0-9]{3}$", code):
            self._warn("PDB code", "Please enter a valid 4-character PDB code (e.g. 1ABC).")
            return

        url = f"https://files.rcsb.org/download/{code}.pdb"
        self.pdb_fetch_status.setText(f"Fetching {code}…")
        self.pdb_fetch_status.repaint()

        try:
            if requests is not None:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                text = r.text
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=20) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            self.pdb_fetch_status.setText("Fetch failed")
            self._warn("Fetch failed", f"Could not download PDB:\n{url}\n\n{e}")
            return

        out_dir = self.proc_path_edit.text().strip() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{code}.pdb")
        try:
            with open(out_path, "wt") as fh:
                fh.write(text)
        except Exception as e:
            self.pdb_fetch_status.setText("Save failed")
            self._warn("Save failed", f"Could not save PDB to:\n{out_path}\n\n{e}")
            return

        self.pdb_fetch_status.setText(f"Saved {code}.pdb")
        self._log(f"Fetched PDB {code} -> {out_path}")
        self.rb_pdb_file.setChecked(True)
        self.pdb_path_edit.setText(out_path)
        self._apply_pdb_header(out_path)

    def _apply_pdb_header(self, pdb_path: str):
        cell, sg = parse_pdb_cryst1(pdb_path)
        if cell:
            self.cell_spins[0].setValue(cell.a)
            self.cell_spins[1].setValue(cell.b)
            self.cell_spins[2].setValue(cell.c)
            self.cell_spins[3].setValue(cell.alpha)
            self.cell_spins[4].setValue(cell.beta)
            self.cell_spins[5].setValue(cell.gamma)
        else:
            self._warn("PDB", "Could not find CRYST1 record for unit cell in PDB header.")

        if sg:
            sg_norm = normalize_sg_name(sg)
            idx = self.sg_combo.findText(sg_norm)
            if idx >= 0:
                self.sg_combo.setCurrentIndex(idx)
                self.sg_source.setText("From PDB")
            else:
                self.sg_source.setText(f"From PDB (not matched): {sg_norm}")
        else:
            self.sg_source.setText("No SG in PDB header (select manually)")

        self._refresh_previews_and_validation()

    # ---------------- Pipeline selection ----------------
    def _pipeline_key(self) -> str:
        if self.rb_xia2_dials.isChecked():
            return "xia2_dials"
        if self.rb_xia2_3dii.isChecked():
            return "xia2_3dii"
        return "autoproc"

    # ---------------- Script generation ----------------
    def _script_paths(self) -> Tuple[str, str, str, str]:
        proc_dir = self.proc_path_edit.text().strip() or os.getcwd()
        os.makedirs(proc_dir, exist_ok=True)
        submit_script = os.path.join(proc_dir, "run_spread_submit.sh")
        ap = os.path.join(proc_dir, "autoproc_jobs.sh")
        dials = os.path.join(proc_dir, "xia2_dials_jobs.sh")
        d3 = os.path.join(proc_dir, "xia2_3dii_jobs.sh")
        return submit_script, ap, dials, d3

    def _validate_before_generate(self) -> Tuple[List[int], List[int]]:
        visit = self.visit_edit.text().strip()
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

        if not self.data_path_edit.text().strip():
            raise ValueError("Data path is empty.")
        if not self.proc_path_edit.text().strip():
            raise ValueError("Processing path is empty.")

        return energies, wedges

    def _make_driver_script(self, energies: List[int], wedges: List[int], pipeline_script: str) -> str:
        energy_list = " ".join(str(e) for e in energies)
        wedge_list = " ".join(str(w) for w in wedges)
        return f"""#!/bin/bash
BASE_DIR=$(pwd)
ENERGY_LIST="{energy_list}"
WEDGE_LIST="{wedge_list}"
counter=0
for energy in ${{ENERGY_LIST}}; do
  counter=$((counter+1))
  for angle in ${{WEDGE_LIST}}; do
    sbatch {pipeline_script} "$energy" "$angle" "$counter"
  done
done
"""

    def _make_autoproc_job(self, data_dir: str, cell: Cell, sg: str) -> str:
        return f"""#!/bin/bash
#SBATCH --job-name=autoPROC_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#SBATCH --mem=10G

module load autoPROC

BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
energy=$1
angle=$2
counter=$3

energy_dir="${{BASE_DIR}}/${{energy}}eV"
angle_dir="${{energy_dir}}/${{angle}}deg"
mkdir -p "$angle_dir"
cd "$angle_dir" || exit 1

[ -f "aP.log" ] && rm "aP.log"
rm -rf autoPROC

process -M DiamondI23 \\
  -Id "name,${{DATA_DIR}},${{energy}}_E${{counter}}_1_#####.cbf,1,${{angle}}0" \\
  -d autoPROC \\
  cell="{cell.as_autoproc_string()}" \\
  symm="{sg}" > aP.log
"""

    def _make_xia2_dials_job(self, data_dir: str, cell: Cell, sg: str) -> str:
        return f"""#!/bin/bash
#SBATCH --job-name=xia2_dials_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#SBATCH --mem=10G

module load xia2

BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
energy=$1
angle=$2
counter=$3

energy_dir="${{BASE_DIR}}/${{energy}}eV"
angle_dir="${{energy_dir}}/${{angle}}deg"
mkdir -p "$angle_dir"
cd "$angle_dir" || exit 1

xia2 pipeline=dials \\
  image="${{DATA_DIR}}/${{energy}}_E${{counter}}_1_#####.cbf" \\
  space_group="{sg}" \\
  unit_cell="{cell.as_autoproc_string()}"
"""

    def _make_xia2_3dii_job(self, data_dir: str, cell: Cell, sg: str) -> str:
        return f"""#!/bin/bash
#SBATCH --job-name=xia2_3dii_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#SBATCH --mem=10G

module load xia2

BASE_DIR=$(pwd)
DATA_DIR="{data_dir}"
energy=$1
angle=$2
counter=$3

energy_dir="${{BASE_DIR}}/${{energy}}eV"
angle_dir="${{energy_dir}}/${{angle}}deg"
mkdir -p "$angle_dir"
cd "$angle_dir" || exit 1

xia2 pipeline=3dii \\
  image="${{DATA_DIR}}/${{energy}}_E${{counter}}_1_#####.cbf" \\
  space_group="{sg}" \\
  unit_cell="{cell.as_autoproc_string()}"
"""

    def generate_scripts(self):
        try:
            energies, wedges = self._validate_before_generate()
        except Exception as e:
            self._warn("Invalid inputs", str(e))
            return

        data_dir = self.data_path_edit.text().strip()
        proc_dir = self.proc_path_edit.text().strip()
        cell = self._current_cell()
        sg = normalize_sg_name(self.sg_combo.currentText())

        submit_script, ap, dials, d3 = self._script_paths()
        pipeline = self._pipeline_key()
        pipeline_script = {"autoproc": "autoproc_jobs.sh",
                           "xia2_dials": "xia2_dials_jobs.sh",
                           "xia2_3dii": "xia2_3dii_jobs.sh"}[pipeline]

        driver = self._make_driver_script(energies, wedges, pipeline_script)
        ap_txt = self._make_autoproc_job(data_dir, cell, sg)
        dials_txt = self._make_xia2_dials_job(data_dir, cell, sg)
        d3_txt = self._make_xia2_3dii_job(data_dir, cell, sg)

        try:
            with open(submit_script, "wt") as f:
                f.write(driver)
            # write all job scripts
            with open(ap, "wt") as f:
                f.write(ap_txt)
            with open(dials, "wt") as f:
                f.write(dials_txt)
            with open(d3, "wt") as f:
                f.write(d3_txt)
            chmod_x(submit_script); chmod_x(ap); chmod_x(dials); chmod_x(d3)
        except Exception as e:
            self._warn("Write failed", f"Could not write scripts to:\n{proc_dir}\n\n{e}")
            return

        self._log(f"Scripts generated in {proc_dir}")
        self._log(f" - {submit_script}")
        self._log(f" - {ap}")
        self._log(f" - {dials}")
        self._log(f" - {d3}")
        self._info("Scripts generated", f"Generated scripts in:\n{proc_dir}")

    def submit_jobs(self):
        self.generate_scripts()
        try:
            energies, wedges = self._validate_before_generate()
        except Exception as e:
            self._warn("Invalid inputs", str(e))
            return

        proc_dir = self.proc_path_edit.text().strip()
        pipeline = self._pipeline_key()
        pipeline_script = {"autoproc": "autoproc_jobs.sh",
                           "xia2_dials": "xia2_dials_jobs.sh",
                           "xia2_3dii": "xia2_3dii_jobs.sh"}[pipeline]

        script_path = os.path.join(proc_dir, pipeline_script)
        if not os.path.isfile(script_path):
            self._warn("Missing script", f"Job script not found:\n{script_path}")
            return

        total = len(energies) * len(wedges)
        if total <= 0:
            self._warn("Nothing to submit", "No jobs to submit.")
            return

        dry = self.chk_dry_run.isChecked()
        self.parent().parent().set_status(f"{'Dry-run' if dry else 'Submitting'} {total} jobs…", 0, total)

        submitted = 0
        counter = 0
        for e in energies:
            counter += 1
            for a in wedges:
                submitted += 1
                self.parent().parent().set_status("Submitting jobs…", submitted, total)

                cmd = ["sbatch", pipeline_script, str(e), str(a), str(counter)]
                if dry:
                    self._log("[DRY] " + " ".join(shlex.quote(x) for x in cmd))
                    continue
                try:
                    p = subprocess.run(cmd, cwd=proc_dir, capture_output=True, text=True)
                    out = (p.stdout or "").strip()
                    err = (p.stderr or "").strip()
                    if p.returncode == 0:
                        self._log(f"Submitted: {out}")
                    else:
                        self._log(f"sbatch failed rc={p.returncode}: {err or out}")
                except Exception as ex:
                    self._log(f"sbatch exception: {ex}")

        self.parent().parent().set_status("Done.", total, total)
        self._info("Submission complete", f"{'Dry run complete' if dry else 'Submission complete'}.\nJobs: {total}")


class AnalysisTab(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        lab = QLabel("Analysis tab will be populated in a later step.")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet("color:#666; font-size: 14px;")
        v.addWidget(lab, 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SPREAD Processing Pipeline")
        self.resize(1200, 850)

        self.tabs = QTabWidget()

        # Processing tab with scroll area
        self.proc_tab = ProcessingTab()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.proc_tab)
        self.tabs.addTab(scroll, "Processing")

        self.analysis_tab = AnalysisTab(self.tabs)
        self.tabs.addTab(self.analysis_tab, "Analysis")

        self.setCentralWidget(self.tabs)

        # Status bar
        sb = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        sb.addPermanentWidget(self.progress, 1)
        self.status_label = QLabel("Ready.")
        sb.addWidget(self.status_label, 3)
        self.setStatusBar(sb)

    def set_status(self, text: str, done: int, total: int):
        self.status_label.setText(text)
        if total > 0:
            pct = int(100.0 * done / total)
            self.progress.setValue(max(0, min(100, pct)))
        else:
            self.progress.setValue(0)


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
