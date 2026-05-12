from __future__ import annotations

import glob
import os
from pathlib import Path

import datetime

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QCloseEvent, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from spread_gui.services.database import ProjectDB
from spread_gui.ui.processing_tab import ProcessingTab
from spread_gui.ui.projects_dialog import ManageProjectsDialog
from spread_gui.ui.spread_tab import SpreadTab

_DB_PATH = Path.home() / ".config" / "spread_gui" / "projects.db"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SPREAD Processing Pipeline")
        self.resize(1200, 900)

        self._db = ProjectDB(_DB_PATH)

        # ---- Central widget ----
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header bar ----
        header = QFrame()
        header.setFrameShape(QFrame.Shape.StyledPanel)
        header.setStyleSheet("QFrame { background:#e8eef8; border-bottom:1px solid #b0bcd0; }")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(10, 4, 10, 4)
        hv.setSpacing(2)

        bold_font = QFont()
        bold_font.setBold(True)

        detail_font = QFont()
        detail_font.setPointSize(max(7, bold_font.pointSize() - 1))

        key_style  = "color:#555;"
        val_style  = "color:#111;"
        dash_style = "color:#999; margin:0 4px;"

        def _sep() -> QLabel:
            s = QLabel("|")
            s.setStyleSheet(dash_style)
            return s

        def _key(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(key_style)
            lbl.setFont(detail_font)
            return lbl

        def _val(min_width: int = 0) -> QLabel:
            lbl = QLabel("\u2014")
            lbl.setStyleSheet(val_style)
            lbl.setFont(detail_font)
            if min_width:
                lbl.setMinimumWidth(min_width)
            return lbl

        # -- Row 1: Project / Crystal / Manage button --
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        row1.addWidget(QLabel("Project:"))
        self._lbl_project = QLabel("\u2014")
        self._lbl_project.setFont(bold_font)
        self._lbl_project.setMinimumWidth(120)
        row1.addWidget(self._lbl_project)

        row1.addWidget(_sep())

        row1.addWidget(QLabel("Crystal:"))
        self._lbl_crystal = QLabel("\u2014")
        self._lbl_crystal.setFont(bold_font)
        self._lbl_crystal.setMinimumWidth(120)
        row1.addWidget(self._lbl_crystal)

        row1.addStretch(1)

        self._btn_manage = QPushButton("Manage Projects\u2026")
        self._btn_manage.clicked.connect(self._open_projects_dialog)
        row1.addWidget(self._btn_manage)

        hv.addLayout(row1)

        # -- Row 2: Data path / Processing path --
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        row2.addWidget(_key("Data path:"))
        self._lbl_data_path = _val()
        self._lbl_data_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row2.addWidget(self._lbl_data_path, 2)

        row2.addWidget(_sep())

        row2.addWidget(_key("Proc path:"))
        self._lbl_proc_path = _val()
        self._lbl_proc_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row2.addWidget(self._lbl_proc_path, 2)

        hv.addLayout(row2)

        # -- Row 3: Space group / Unit cell --
        row3 = QHBoxLayout()
        row3.setSpacing(4)

        row3.addWidget(_key("Space group:"))
        self._lbl_sg = _val(80)
        row3.addWidget(self._lbl_sg)

        row3.addWidget(_sep())

        row3.addWidget(_key("Unit cell:"))
        self._lbl_cell = _val()
        row3.addWidget(self._lbl_cell, 1)

        row3.addStretch(1)

        hv.addLayout(row3)

        root.addWidget(header)

        # ---- Tabs ----
        self.tabs = QTabWidget()

        self.proc_tab = ProcessingTab(self._db)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.proc_tab)
        self.tabs.addTab(scroll, "Processing")

        self.spread_tab = SpreadTab(self.tabs)
        spread_scroll = QScrollArea()
        spread_scroll.setWidgetResizable(True)
        spread_scroll.setWidget(self.spread_tab)
        self.tabs.addTab(spread_scroll, "SPREAD")

        self.tabs.setIconSize(QSize(14, 14))

        # ---- Shared log panel ----
        log_panel = QFrame()
        log_panel.setFrameShape(QFrame.Shape.StyledPanel)
        lv = QVBoxLayout(log_panel)
        lv.setContentsMargins(4, 2, 4, 4)
        lv.setSpacing(2)

        log_hdr = QHBoxLayout()
        log_hdr.addWidget(QLabel("Log"))
        log_hdr.addStretch(1)
        self._btn_clear_log = QPushButton("Clear")
        self._btn_save_log  = QPushButton("Save log\u2026")
        log_hdr.addWidget(self._btn_clear_log)
        log_hdr.addWidget(self._btn_save_log)
        lv.addLayout(log_hdr)

        self.shared_log = QTextEdit()
        self.shared_log.setReadOnly(True)
        self.shared_log.setPlaceholderText("Log\u2026")
        lv.addWidget(self.shared_log)

        # ---- Splitter: tabs on top, log at bottom ----
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(log_panel)
        splitter.setSizes([650, 200])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

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
        self.proc_tab.crystal_context_changed.connect(
            lambda _p, _c: self._update_processing_tab_indicator()
        )
        self.proc_tab.processing_info_changed.connect(self._on_processing_info_changed)
        self.proc_tab.jobs_status_changed.connect(self._update_processing_tab_indicator)
        self.proc_tab.log_message.connect(self._append_log)
        self.proc_tab.processing_info_changed.connect(
            lambda _d, proc, _sg, _c: self.spread_tab.set_proc_path(proc)
        )
        self.spread_tab.log_message.connect(self._append_log)
        self._btn_clear_log.clicked.connect(self.shared_log.clear)
        self._btn_save_log.clicked.connect(self._save_log)

        # Restore header from the crystal that was active in the previous session.
        cid = self.proc_tab.current_crystal_id
        if cid is not None:
            project, crystal = self._db.get_crystal_info(cid)
            if project:
                self._on_crystal_context_changed(project, crystal)
            else:
                self.proc_tab._current_crystal_id = None

        # Populate the details rows and SPREAD tab with whatever was loaded from settings.
        self.proc_tab._emit_processing_info()
        self.spread_tab.set_proc_path(self.proc_tab._proc_path)
        self._update_processing_tab_indicator()

        # Poll the filesystem every 60 s to detect completed jobs.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(60_000)
        self._poll_timer.timeout.connect(self._poll_jobs)
        self._poll_timer.start()

    # ---- Header updates ----

    def _on_crystal_context_changed(self, project: str, crystal: str) -> None:
        self._lbl_project.setText(project if project else "\u2014")
        self._lbl_crystal.setText(crystal if crystal else "\u2014")

    def _on_processing_info_changed(
        self, data_path: str, proc_path: str, space_group: str, cell_str: str
    ) -> None:
        self._lbl_data_path.setText(data_path if data_path else "\u2014")
        self._lbl_proc_path.setText(proc_path if proc_path else "\u2014")
        self._lbl_sg.setText(space_group if space_group else "\u2014")
        self._lbl_cell.setText(cell_str if cell_str else "\u2014")

    # ---- Processing tab indicator ----

    @staticmethod
    def _dot_icon(color: str) -> QIcon:
        px = QPixmap(14, 14)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 12, 12)
        p.end()
        return QIcon(px)

    def _update_processing_tab_indicator(self) -> None:
        """Set a yellow or green dot on the Processing tab based on job state."""
        cid = self.proc_tab.current_crystal_id
        if cid is None:
            self.tabs.setTabIcon(0, QIcon())
            return

        jobs = self._db.get_jobs(cid)
        real_jobs = [j for j in jobs if not j["dry_run"]]
        if not real_jobs:
            self.tabs.setTabIcon(0, QIcon())
            return

        # Green if every real job's output directory exists on the filesystem.
        all_done = all(
            os.path.isdir(os.path.join(j["proc_dir"], j["output_dir"]))
            for j in real_jobs
        )
        color = "#44bb44" if all_done else "#e6b800"
        self.tabs.setTabIcon(0, self._dot_icon(color))

    # ---- Job polling (filesystem) ----

    def _poll_jobs(self) -> None:
        """Check each submitted job's output directory; mark completed if found."""
        cid = self.proc_tab.current_crystal_id
        if cid is None:
            return
        jobs = self._db.get_jobs(cid)
        changed = False
        for j in jobs:
            if j["status"] != "submitted":
                continue
            output_path = os.path.join(j["proc_dir"], j["output_dir"])
            if os.path.isdir(output_path):
                self._db.update_job_status(j["id"], "completed")
                changed = True
        if changed:
            self._update_processing_tab_indicator()

    # ---- Manage Projects dialog ----

    def _open_projects_dialog(self) -> None:
        dlg = ManageProjectsDialog(
            self._db,
            self.proc_tab.current_crystal_id,
            self.proc_tab._collect_form_state(),
            self,
        )
        result = dlg.exec()
        if result and dlg.selected_crystal_id is not None:
            # Dialog already asked for confirmation — save current state first,
            # then load the selected crystal.
            self.proc_tab.save_settings()
            self.proc_tab.load_crystal(dlg.selected_crystal_id)
        elif dlg._needs_reload and self.proc_tab.current_crystal_id is not None:
            # SG/cell was updated for the currently loaded crystal — reload it.
            self.proc_tab.load_crystal(self.proc_tab.current_crystal_id)

    # ---- Shared log ----

    def _append_log(self, msg: str) -> None:
        self.shared_log.append(msg)
        self.shared_log.ensureCursorVisible()

    def _save_log(self) -> None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        proc_dir = self.proc_tab._proc_path or os.getcwd()
        default_name = os.path.join(proc_dir, f"spread_gui_{ts}.log")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", default_name, "Log files (*.log);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "wt") as fh:
                fh.write(self.shared_log.toPlainText())
            self._append_log(f"Log saved to {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Save log failed", str(exc))

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
