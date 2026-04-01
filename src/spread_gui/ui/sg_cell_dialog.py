"""
SgCellDialog — define space group and unit cell for a crystal.

Three methods:
  1. Upload PDB file   — browse to a local .pdb, click Load, confirm
  2. PDB code          — enter a 4-char code, click Fetch, confirm
  3. Manual            — type SG and cell values directly

Each method shows a result preview before the user clicks Apply.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from spread_gui.core.model import Cell
from spread_gui.core.cryst import normalize_sg_name, parse_pdb_cryst1
from spread_gui.core.spacegroups import all_spacegroups_230, cell_is_compatible_with_sg


class SgCellDialog(QDialog):
    """
    Dialog to define space group and unit cell.

    On accept, read back `.space_group` (str) and `.cell` (Cell).
    Both are None if not yet defined (should not happen if Apply was clicked).
    """

    def __init__(
        self,
        current_sg: str = "",
        current_cell: Optional[Cell] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Space Group & Unit Cell")
        self.setMinimumWidth(520)

        self.space_group: Optional[str] = None
        self.cell: Optional[Cell] = None

        self._build_ui(current_sg, current_cell)

    # ---- UI ----

    def _build_ui(self, current_sg: str, current_cell: Optional[Cell]) -> None:
        root = QVBoxLayout(self)

        # Method selection
        method_box = QGroupBox("Method")
        mr = QHBoxLayout(method_box)
        self.rb_file   = QRadioButton("Upload PDB file")
        self.rb_code   = QRadioButton("PDB code")
        self.rb_manual = QRadioButton("Manual")
        self.rb_file.setChecked(True)
        mr.addWidget(self.rb_file)
        mr.addWidget(self.rb_code)
        mr.addWidget(self.rb_manual)
        mr.addStretch(1)
        root.addWidget(method_box)

        # Stacked panels
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_file_panel())    # index 0
        self._stack.addWidget(self._build_code_panel())    # index 1
        self._stack.addWidget(self._build_manual_panel(current_sg, current_cell))  # index 2
        root.addWidget(self._stack)

        # Preview area (file + code methods)
        self._preview_box = QGroupBox("Result preview")
        pv = QVBoxLayout(self._preview_box)
        self._preview_label = QLabel("—")
        self._preview_label.setWordWrap(True)
        pv.addWidget(self._preview_label)
        root.addWidget(self._preview_box)

        # Buttons
        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setDefault(True)
        self._btn_apply.setEnabled(False)
        btn_cancel = QPushButton("Cancel")
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_apply)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

        # Signals
        self.rb_file.toggled.connect(self._on_method_changed)
        self.rb_code.toggled.connect(self._on_method_changed)
        self.rb_manual.toggled.connect(self._on_method_changed)
        self._btn_apply.clicked.connect(self._apply)
        btn_cancel.clicked.connect(self.reject)

        self._on_method_changed()

    def _build_file_panel(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("PDB file:"), 0, 0)
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Select a .pdb file")
        g.addWidget(self._file_edit, 0, 1)
        self._btn_browse = QPushButton("Browse…")
        g.addWidget(self._btn_browse, 0, 2)
        self._btn_load_file = QPushButton("Load")
        g.addWidget(self._btn_load_file, 1, 2)
        g.setColumnStretch(1, 1)

        self._btn_browse.clicked.connect(self._browse_pdb)
        self._btn_load_file.clicked.connect(self._load_from_file)
        return w

    def _build_code_panel(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("PDB code:"), 0, 0)
        self._code_edit = QLineEdit()
        self._code_edit.setPlaceholderText("e.g. 1ABC")
        g.addWidget(self._code_edit, 0, 1)
        self._btn_fetch = QPushButton("Fetch")
        g.addWidget(self._btn_fetch, 0, 2)
        self._fetch_status = QLabel("")
        self._fetch_status.setStyleSheet("color:#666;")
        g.addWidget(self._fetch_status, 1, 0, 1, 3)
        g.setColumnStretch(1, 1)

        self._btn_fetch.clicked.connect(self._fetch_pdb)
        return w

    def _build_manual_panel(self, current_sg: str, current_cell: Optional[Cell]) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)

        g.addWidget(QLabel("Space group:"), 0, 0)
        self._sg_combo = QComboBox()
        self._sg_combo.addItems(all_spacegroups_230())
        g.addWidget(self._sg_combo, 0, 1, 1, 4)

        labels = ["a", "b", "c", "\u03b1", "\u03b2", "\u03b3"]
        self._cell_spins: list[QDoubleSpinBox] = []
        for i, lab in enumerate(labels):
            g.addWidget(QLabel(f"{lab}:"), 1 + i // 3, (i % 3) * 2)
            sp = QDoubleSpinBox()
            sp.setDecimals(4 if i < 3 else 3)
            sp.setRange(0.0, 10000.0 if i < 3 else 180.0)
            sp.setSingleStep(0.1)
            self._cell_spins.append(sp)
            g.addWidget(sp, 1 + i // 3, (i % 3) * 2 + 1)

        self._compat_label = QLabel("")
        self._compat_label.setStyleSheet("color:#666;")
        g.addWidget(self._compat_label, 3, 0, 1, 6)

        # Pre-fill with current values if available
        if current_sg:
            idx = self._sg_combo.findText(normalize_sg_name(current_sg))
            if idx >= 0:
                self._sg_combo.setCurrentIndex(idx)
        if current_cell is not None:
            vals = [current_cell.a, current_cell.b, current_cell.c,
                    current_cell.alpha, current_cell.beta, current_cell.gamma]
            for sp, v in zip(self._cell_spins, vals):
                sp.setValue(v)

        # Live validation
        self._sg_combo.currentTextChanged.connect(self._validate_manual)
        for sp in self._cell_spins:
            sp.valueChanged.connect(self._validate_manual)

        self._validate_manual()
        return w

    # ---- Method switching ----

    def _on_method_changed(self) -> None:
        if self.rb_file.isChecked():
            self._stack.setCurrentIndex(0)
            self._preview_box.setVisible(True)
            self._preview_label.setText("—")
            self._btn_apply.setEnabled(False)
        elif self.rb_code.isChecked():
            self._stack.setCurrentIndex(1)
            self._preview_box.setVisible(True)
            self._preview_label.setText("—")
            self._btn_apply.setEnabled(False)
        else:  # manual
            self._stack.setCurrentIndex(2)
            self._preview_box.setVisible(False)
            self._btn_apply.setEnabled(True)

        # Reset pending result when switching methods
        self._pending_sg: Optional[str] = None
        self._pending_cell: Optional[Cell] = None

    # ---- PDB file ----

    def _browse_pdb(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(
            self, "Select PDB file", "", "PDB files (*.pdb *.ent);;All files (*)"
        )
        if fn:
            self._file_edit.setText(fn)

    def _load_from_file(self) -> None:
        path = self._file_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "No file", "Please select a PDB file first.")
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "File not found", f"File not found:\n{path}")
            return
        cell, sg = parse_pdb_cryst1(path)
        self._show_pdb_result(cell, sg, source=Path(path).name)

    # ---- PDB code ----

    def _fetch_pdb(self) -> None:
        from spread_gui.services.rcsb import fetch_pdb_text

        code = self._code_edit.text().strip().upper()
        if not re.match(r"^[0-9][A-Z0-9]{3}$", code):
            QMessageBox.warning(self, "Invalid code",
                                "Please enter a valid 4-character PDB code (e.g. 1ABC).")
            return

        self._fetch_status.setText(f"Fetching {code}…")
        self._fetch_status.repaint()
        self._btn_fetch.setEnabled(False)
        try:
            text = fetch_pdb_text(code, timeout=20)
        except Exception as exc:
            self._fetch_status.setText("Fetch failed")
            self._btn_fetch.setEnabled(True)
            QMessageBox.warning(self, "Fetch failed", f"Could not download PDB {code}:\n{exc}")
            return

        self._btn_fetch.setEnabled(True)
        self._fetch_status.setText(f"Fetched {code}")

        # Parse CRYST1 directly from the text without saving a file
        import tempfile
        with tempfile.NamedTemporaryFile(mode="wt", suffix=".pdb", delete=False) as tf:
            tf.write(text)
            tmp_path = tf.name
        try:
            cell, sg = parse_pdb_cryst1(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        self._show_pdb_result(cell, sg, source=f"PDB:{code}")

    # ---- Shared PDB result display ----

    def _show_pdb_result(
        self,
        cell: Optional[Cell],
        sg: Optional[str],
        source: str,
    ) -> None:
        if cell is None and sg is None:
            self._preview_label.setText("No CRYST1 record found in PDB file.")
            self._btn_apply.setEnabled(False)
            self._pending_sg = None
            self._pending_cell = None
            return

        sg_norm = normalize_sg_name(sg) if sg else ""
        parts = []
        if sg_norm:
            parts.append(f"Space group: {sg_norm}")
        else:
            parts.append("Space group: (not found)")
        if cell:
            parts.append(
                f"Unit cell: a={cell.a}  b={cell.b}  c={cell.c}  "
                f"\u03b1={cell.alpha}  \u03b2={cell.beta}  \u03b3={cell.gamma}"
            )
        else:
            parts.append("Unit cell: (not found)")
        parts.append(f"Source: {source}")

        self._preview_label.setText("\n".join(parts))
        self._pending_sg   = sg_norm if sg_norm else None
        self._pending_cell = cell
        # Enable Apply only if we have at least one useful value
        self._btn_apply.setEnabled(bool(sg_norm or cell))

    # ---- Manual validation ----

    def _validate_manual(self) -> None:
        cell = Cell(
            a=self._cell_spins[0].value(),
            b=self._cell_spins[1].value(),
            c=self._cell_spins[2].value(),
            alpha=self._cell_spins[3].value(),
            beta=self._cell_spins[4].value(),
            gamma=self._cell_spins[5].value(),
        )
        sg = normalize_sg_name(self._sg_combo.currentText())
        ok, msg = cell_is_compatible_with_sg(cell, sg)
        if ok:
            self._compat_label.setStyleSheet("color:#2a7;")
        else:
            self._compat_label.setStyleSheet("color:#c00;")
        self._compat_label.setText(msg)

    # ---- Apply ----

    def _apply(self) -> None:
        if self.rb_manual.isChecked():
            self.space_group = normalize_sg_name(self._sg_combo.currentText())
            self.cell = Cell(
                a=self._cell_spins[0].value(),
                b=self._cell_spins[1].value(),
                c=self._cell_spins[2].value(),
                alpha=self._cell_spins[3].value(),
                beta=self._cell_spins[4].value(),
                gamma=self._cell_spins[5].value(),
            )
        else:
            self.space_group = self._pending_sg
            self.cell        = self._pending_cell
        self.accept()
