from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from spread_gui.services.database import ProjectDB
from spread_gui.ui.analysis_tab import AnalysisTab
from spread_gui.ui.processing_tab import ProcessingTab
from spread_gui.ui.projects_dialog import ManageProjectsDialog

_DB_PATH = Path.home() / ".config" / "spread_gui" / "projects.db"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SPREAD Processing Pipeline")
        self.resize(1200, 900)

        self._db = ProjectDB(_DB_PATH)

        # ---- Central widget ----
        central = QWidget()
        from PyQt6.QtWidgets import QVBoxLayout
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Project/Crystal header bar ----
        header = QFrame()
        header.setFrameShape(QFrame.Shape.StyledPanel)
        header.setStyleSheet("QFrame { background:#e8eef8; border-bottom:1px solid #b0bcd0; }")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 4, 10, 4)

        lbl_font = QFont()
        lbl_font.setBold(True)

        hl.addWidget(QLabel("Project:"))
        self._lbl_project = QLabel("\u2014")
        self._lbl_project.setFont(lbl_font)
        self._lbl_project.setMinimumWidth(120)
        hl.addWidget(self._lbl_project)

        sep = QLabel("|")
        sep.setStyleSheet("color:#999; margin:0 4px;")
        hl.addWidget(sep)

        hl.addWidget(QLabel("Crystal:"))
        self._lbl_crystal = QLabel("\u2014")
        self._lbl_crystal.setFont(lbl_font)
        self._lbl_crystal.setMinimumWidth(120)
        hl.addWidget(self._lbl_crystal)

        hl.addStretch(1)

        self._btn_manage = QPushButton("Manage Projects\u2026")
        self._btn_manage.clicked.connect(self._open_projects_dialog)
        hl.addWidget(self._btn_manage)

        root.addWidget(header)

        # ---- Tabs ----
        self.tabs = QTabWidget()

        self.proc_tab = ProcessingTab(self._db)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.proc_tab)
        self.tabs.addTab(scroll, "Processing")

        self.analysis_tab = AnalysisTab(self.tabs)
        self.tabs.addTab(self.analysis_tab, "Analysis")

        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # ---- Status bar ----
        sb = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        sb.addPermanentWidget(self.progress, 1)
        self.status_label = QLabel("Ready.")
        sb.addWidget(self.status_label, 3)
        self.setStatusBar(sb)

        # ---- Signals ----
        self.proc_tab.crystal_context_changed.connect(self._on_crystal_context_changed)

    # ---- Header updates ----

    def _on_crystal_context_changed(self, project: str, crystal: str) -> None:
        self._lbl_project.setText(project if project else "\u2014")
        self._lbl_crystal.setText(crystal if crystal else "\u2014")

    # ---- Manage Projects dialog ----

    def _open_projects_dialog(self) -> None:
        dlg = ManageProjectsDialog(
            self._db,
            self.proc_tab.current_crystal_id,
            self.proc_tab._collect_form_state(),
            self,
        )
        if dlg.exec() and dlg.selected_crystal_id is not None:
            # Dialog already asked for confirmation — save current state first,
            # then load the selected crystal.
            self.proc_tab.save_settings()
            self.proc_tab.load_crystal(dlg.selected_crystal_id)

    # ---- Status bar helper ----

    def set_status(self, text: str, done: int, total: int) -> None:
        self.status_label.setText(text)
        if total > 0:
            self.progress.setValue(max(0, min(100, int(100.0 * done / total))))
        else:
            self.progress.setValue(0)

    # ---- Close with confirmation ----

    def closeEvent(self, event: QCloseEvent) -> None:
        ans = QMessageBox.question(
            self,
            "Quit SPREAD?",
            "Save settings and quit?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if ans == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if ans == QMessageBox.StandardButton.Yes:
            self.proc_tab.save_settings()
        super().closeEvent(event)
