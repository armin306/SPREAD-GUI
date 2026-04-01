"""
Manage Projects dialog.

Layout
------
  ┌─ Projects ──────┐  ┌─ Crystals in "<project>" ──────────┐
  │  ● TDO          │  │  xtal1                              │
  │    Lysozyme     │  │  ● xtal4   ← currently loaded      │
  │                 │  │  xtal5                              │
  │  [+New] [✕Del]  │  │  [+New] [✕Del]                     │
  └─────────────────┘  └────────────────────────────────────┘
  ──────────────────────────────────────────────────────────
  Selected: TDO / xtal4          [Load Crystal]    [Close]

"●" marks the currently-loaded crystal (and its project).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spread_gui.services.database import Crystal, Project, ProjectDB


# ---------------------------------------------------------------------------
# New-Crystal sub-dialog
# ---------------------------------------------------------------------------

class _NewCrystalDialog(QDialog):
    """Ask for crystal name, visit, data path and processing path."""

    def __init__(
        self,
        prefill: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Crystal")
        self.setMinimumWidth(500)

        g = QGridLayout(self)
        g.setColumnStretch(1, 1)

        def _row(label: str, row: int) -> QLineEdit:
            g.addWidget(QLabel(label), row, 0)
            w = QLineEdit()
            g.addWidget(w, row, 1)
            return w

        self.name_edit      = _row("Crystal name:",      0)
        self.visit_edit     = _row("Visit:",              1)
        self.data_path_edit = _row("Data path:",          2)
        self.proc_path_edit = _row("Processing path:",   3)

        # Browse buttons
        for row, edit in ((2, self.data_path_edit), (3, self.proc_path_edit)):
            btn = QPushButton("Browse\u2026")
            btn.clicked.connect(lambda _, e=edit: self._browse(e))
            g.addWidget(btn, row, 2)

        # Pre-fill from current form state
        self.visit_edit.setText(prefill.get("visit", ""))
        self.data_path_edit.setText(prefill.get("data_path", ""))
        self.proc_path_edit.setText(prefill.get("proc_path", ""))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        g.addWidget(buttons, 4, 0, 1, 3)

    def _browse(self, edit: QLineEdit) -> None:
        start = edit.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select directory", start)
        if d:
            edit.setText(d)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a crystal name.")
            return
        self.accept()

    # ---- results ----
    @property
    def crystal_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def visit(self) -> str:
        return self.visit_edit.text().strip()

    @property
    def data_path(self) -> str:
        return self.data_path_edit.text().strip()

    @property
    def proc_path(self) -> str:
        return self.proc_path_edit.text().strip()


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

_BULLET = "\u25cf "   # ● followed by space — marks the active crystal


class ManageProjectsDialog(QDialog):
    """
    Two-pane project/crystal browser.

    After the dialog closes with Accepted, check *selected_crystal_id*;
    if it is not None the caller should call proc_tab.load_crystal().
    """

    def __init__(
        self,
        db: ProjectDB,
        current_crystal_id: Optional[int],
        current_form_state: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Projects")
        self.setMinimumSize(700, 420)

        self._db                 = db
        self._current_crystal_id = current_crystal_id
        self._current_form_state = current_form_state   # for pre-filling New Crystal
        self.selected_crystal_id: Optional[int] = None  # set on Load
        self._needs_reload: bool = False  # set when SG/cell updated for active crystal

        self._build_ui()
        self._refresh_projects()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        panes = QHBoxLayout()

        # -- Projects pane --
        proj_box = QGroupBox("Projects")
        pv = QVBoxLayout(proj_box)
        self.proj_list = QListWidget()
        self.proj_list.currentItemChanged.connect(self._on_project_selected)
        pv.addWidget(self.proj_list)
        pb = QHBoxLayout()
        self.btn_new_project = QPushButton("+ New")
        self.btn_del_project = QPushButton("\u2715 Delete")
        self.btn_del_project.setEnabled(False)
        pb.addWidget(self.btn_new_project)
        pb.addWidget(self.btn_del_project)
        pb.addStretch(1)
        pv.addLayout(pb)
        panes.addWidget(proj_box, 1)

        # -- Crystals pane --
        self.cryst_box = QGroupBox("Crystals")
        cv = QVBoxLayout(self.cryst_box)
        self.cryst_list = QListWidget()
        self.cryst_list.currentItemChanged.connect(self._on_crystal_selected)
        self.cryst_list.itemDoubleClicked.connect(self._load_crystal)
        cv.addWidget(self.cryst_list)
        cb = QHBoxLayout()
        self.btn_new_crystal = QPushButton("+ New")
        self.btn_del_crystal = QPushButton("\u2715 Delete")
        self.btn_sg_cell     = QPushButton("Set SG \u0026 Cell\u2026")
        self.btn_new_crystal.setEnabled(False)
        self.btn_del_crystal.setEnabled(False)
        self.btn_sg_cell.setEnabled(False)
        cb.addWidget(self.btn_new_crystal)
        cb.addWidget(self.btn_del_crystal)
        cb.addWidget(self.btn_sg_cell)
        cb.addStretch(1)
        cv.addLayout(cb)
        panes.addWidget(self.cryst_box, 2)

        root.addLayout(panes)

        # -- Footer --
        self.selection_label = QLabel("No crystal selected.")
        self.selection_label.setStyleSheet("color:#555;")
        root.addWidget(self.selection_label)

        footer = QHBoxLayout()
        footer.addWidget(self.selection_label, 1)
        self.btn_load = QPushButton("Load Crystal")
        self.btn_load.setEnabled(False)
        self.btn_load.setDefault(True)
        btn_close = QPushButton("Close")
        footer.addWidget(self.btn_load)
        footer.addWidget(btn_close)
        root.addLayout(footer)

        # Signals
        self.btn_new_project.clicked.connect(self._new_project)
        self.btn_del_project.clicked.connect(self._delete_project)
        self.btn_new_crystal.clicked.connect(self._new_crystal)
        self.btn_del_crystal.clicked.connect(self._delete_crystal)
        self.btn_sg_cell.clicked.connect(self._set_sg_cell)
        self.btn_load.clicked.connect(self._load_crystal)
        btn_close.clicked.connect(self.reject)

    # ---- Data helpers ----

    def _selected_project(self) -> Optional[Project]:
        item = self.proj_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_crystal(self) -> Optional[Crystal]:
        item = self.cryst_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _current_project_id(self) -> Optional[int]:
        p = self._selected_project()
        # Walk up from the active crystal to find its project too
        if p is None and self._current_crystal_id is not None:
            info = self._db.get_crystal_info(self._current_crystal_id)
            _ = info  # not used here; project pane drives this
        return p.id if p else None

    # ---- Refresh ----

    def _refresh_projects(self, select_id: Optional[int] = None) -> None:
        self.proj_list.clear()
        active_project_id: Optional[int] = None
        if self._current_crystal_id is not None:
            info = self._db.get_crystal_info(self._current_crystal_id)
            # find project whose name matches
            for p in self._db.get_projects():
                if p.name == info[0]:
                    active_project_id = p.id
                    break

        projects = self._db.get_projects()
        restore_idx = 0
        for i, p in enumerate(projects):
            label = (_BULLET if p.id == active_project_id else "  ") + p.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.proj_list.addItem(item)
            if select_id is not None and p.id == select_id:
                restore_idx = i
            elif select_id is None and p.id == active_project_id:
                restore_idx = i

        if self.proj_list.count() > 0:
            self.proj_list.setCurrentRow(restore_idx)

    def _refresh_crystals(self, project_id: int, select_id: Optional[int] = None) -> None:
        self.cryst_list.clear()
        crystals = self._db.get_crystals(project_id)
        restore_idx = 0
        for i, c in enumerate(crystals):
            is_active = c.id == self._current_crystal_id
            label = (_BULLET if is_active else "  ") + c.name
            if c.visit:
                label += f"  [{c.visit}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.cryst_list.addItem(item)
            if select_id is not None and c.id == select_id:
                restore_idx = i
            elif select_id is None and is_active:
                restore_idx = i

        if self.cryst_list.count() > 0:
            self.cryst_list.setCurrentRow(restore_idx)

    # ---- Selection callbacks ----

    def _on_project_selected(self) -> None:
        p = self._selected_project()
        has_p = p is not None
        self.btn_del_project.setEnabled(has_p)
        self.btn_new_crystal.setEnabled(has_p)
        self.cryst_list.clear()
        self.btn_del_crystal.setEnabled(False)
        self.btn_load.setEnabled(False)
        self.selection_label.setText("No crystal selected.")
        if has_p:
            self.cryst_box.setTitle(f'Crystals in "{p.name}"')
            self._refresh_crystals(p.id)
        else:
            self.cryst_box.setTitle("Crystals")

    def _on_crystal_selected(self) -> None:
        c = self._selected_crystal()
        p = self._selected_project()
        has_c = c is not None
        self.btn_del_crystal.setEnabled(has_c)
        self.btn_sg_cell.setEnabled(has_c)
        self.btn_load.setEnabled(has_c)
        if has_c and p:
            self.selection_label.setText(f"Selected:  {p.name} / {c.name}")
        else:
            self.selection_label.setText("No crystal selected.")

    # ---- Actions ----

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            p = self._db.create_project(name)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self._refresh_projects(select_id=p.id)

    def _delete_project(self) -> None:
        p = self._selected_project()
        if p is None:
            return
        crystals = self._db.get_crystals(p.id)
        n = len(crystals)
        detail = f" and its {n} crystal{'s' if n != 1 else ''}" if n else ""
        ans = QMessageBox.question(
            self, "Delete project",
            f'Delete project \u201c{p.name}\u201d{detail}?\nThis cannot be undone.',
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        if self._current_crystal_id in {c.id for c in crystals}:
            self._current_crystal_id = None
        self._db.delete_project(p.id)
        self._refresh_projects()

    def _new_crystal(self) -> None:
        p = self._selected_project()
        if p is None:
            return
        dlg = _NewCrystalDialog(self._current_form_state, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            c = self._db.create_crystal(
                p.id,
                dlg.crystal_name,
                visit=dlg.visit,
                data_path=dlg.data_path,
                proc_path=dlg.proc_path,
                initial_settings=self._current_form_state,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        self._refresh_crystals(p.id, select_id=c.id)

    def _delete_crystal(self) -> None:
        c = self._selected_crystal()
        if c is None:
            return
        ans = QMessageBox.question(
            self, "Delete crystal",
            f'Delete crystal \u201c{c.name}\u201d?\nThis cannot be undone.',
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        if c.id == self._current_crystal_id:
            self._current_crystal_id = None
        self._db.delete_crystal(c.id)
        p = self._selected_project()
        if p:
            self._refresh_crystals(p.id)

    def _set_sg_cell(self) -> None:
        from spread_gui.ui.sg_cell_dialog import SgCellDialog
        from spread_gui.core.model import Cell

        c = self._selected_crystal()
        if c is None:
            return

        # Read existing SG/cell from stored settings so the dialog can pre-fill.
        settings = self._db.get_crystal_settings(c.id)
        current_sg   = settings.get("space_group", "")
        current_cell = None
        try:
            current_cell = Cell(
                a=float(settings.get("cell_a", 0)),
                b=float(settings.get("cell_b", 0)),
                c=float(settings.get("cell_c", 0)),
                alpha=float(settings.get("cell_alpha", 0)),
                beta=float(settings.get("cell_beta", 0)),
                gamma=float(settings.get("cell_gamma", 0)),
            )
        except (TypeError, ValueError):
            current_cell = None

        dlg = SgCellDialog(current_sg, current_cell, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        patch: dict = {}
        if dlg.space_group is not None:
            patch["space_group"] = dlg.space_group
        if dlg.cell is not None:
            patch["cell_a"]     = str(dlg.cell.a)
            patch["cell_b"]     = str(dlg.cell.b)
            patch["cell_c"]     = str(dlg.cell.c)
            patch["cell_alpha"] = str(dlg.cell.alpha)
            patch["cell_beta"]  = str(dlg.cell.beta)
            patch["cell_gamma"] = str(dlg.cell.gamma)

        if patch:
            self._db.patch_crystal_settings(c.id, patch)
            # If this crystal is currently loaded, tell the caller to reload it.
            if c.id == self._current_crystal_id:
                self._needs_reload = True

    def _load_crystal(self) -> None:
        c = self._selected_crystal()
        p = self._selected_project()
        if c is None or p is None:
            return
        ans = QMessageBox.question(
            self,
            "Load crystal",
            f"Load \u201c{p.name} / {c.name}\u201d?\n"
            "Current settings will be saved first.",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.selected_crystal_id = c.id
        self.accept()
