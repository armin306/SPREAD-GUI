from __future__ import annotations

import configparser
import os
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

_CONFIG_PATH = Path.home() / ".config" / "spread_gui" / "settings.ini"


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _AnalysisWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)   # HTML path on success
    error    = pyqtSignal(str)   # error message on failure

    def __init__(
        self,
        proc_path: str,
        pipeline: str,
        out_dir: str,
        project_name: str = "",
        crystal_name: str = "",
        results_path: str = "",
    ) -> None:
        super().__init__()
        self._proc_path    = proc_path
        self._pipeline     = pipeline
        self._out_dir      = out_dir
        self._project_name = project_name
        self._crystal_name = crystal_name
        self._results_path = results_path

    def run(self) -> None:
        try:
            if self._pipeline == "autoPROC":
                from spread_gui.core.autoproc_analysis import collect_results, generate_report
            else:
                from spread_gui.core.xia2_analysis import collect_results, generate_report
            results = collect_results(Path(self._proc_path), self._pipeline)
            html_path = generate_report(
                results,
                Path(self._out_dir),
                self._pipeline,
                self._proc_path,
                self.progress.emit,
                project_name=self._project_name,
                crystal_name=self._crystal_name,
                results_path=self._results_path,
            )
            # Regenerate crystal and project index pages
            if self._results_path and self._crystal_name:
                try:
                    from spread_gui.core.report_index import (
                        generate_crystal_index, generate_project_index,
                    )
                    crystal_dir = Path(self._results_path) / self._crystal_name
                    generate_crystal_index(crystal_dir, self._project_name, self._crystal_name)
                    generate_project_index(Path(self._results_path), self._project_name)
                except Exception as e:
                    self.progress.emit(f"Warning: could not update index pages: {e}")
            self.finished.emit(html_path)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Analysis tab widget
# ---------------------------------------------------------------------------

class AnalysisTab(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker:       _AnalysisWorker | None = None
        self._proc_path:    str = ""
        self._results_path: str = ""
        self._project_name: str = ""
        self._crystal_name: str = ""
        self._build_ui()
        self._load_settings()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Input group
        in_box = QGroupBox("Input")
        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout(in_box)

        g.addWidget(QLabel("Processing path:"), 0, 0)
        self.proc_path_label = QLabel("\u2014")
        self.proc_path_label.setStyleSheet("color:#444;")
        g.addWidget(self.proc_path_label, 0, 1, 1, 2)

        g.addWidget(QLabel("Pipeline:"), 1, 0)
        pl_row = QHBoxLayout()
        self.rb_dials    = QRadioButton("xia2-dials")
        self.rb_3dii     = QRadioButton("xia2-3dii")
        self.rb_autoproc = QRadioButton("autoPROC")
        self.rb_dials.setChecked(True)
        pl_row.addWidget(self.rb_dials)
        pl_row.addWidget(self.rb_3dii)
        pl_row.addWidget(self.rb_autoproc)
        pl_row.addStretch(1)
        g.addLayout(pl_row, 1, 1, 1, 2)

        root.addWidget(in_box)

        # Output group
        out_box = QGroupBox("Output")
        ol = QHBoxLayout(out_box)
        ol.addWidget(QLabel("Results directory:"))
        self.out_dir_label = QLabel("")
        self.out_dir_label.setStyleSheet("color:#444;")
        ol.addWidget(self.out_dir_label, 1)
        root.addWidget(out_box)

        # Action row
        a_row = QHBoxLayout()
        self.btn_run = QPushButton("Run Analysis")
        a_row.addWidget(self.btn_run)
        a_row.addStretch(1)
        root.addLayout(a_row)

        # Signals
        self.btn_run.clicked.connect(self._run_analysis)
        self.rb_dials.toggled.connect(self._update_out_label)
        self.rb_3dii.toggled.connect(self._update_out_label)
        self.rb_autoproc.toggled.connect(self._update_out_label)

    # ---- Helpers ----

    def _pipeline(self) -> str:
        if self.rb_autoproc.isChecked():
            return "autoPROC"
        return "xia2-dials" if self.rb_dials.isChecked() else "xia2-3dii"

    def set_proc_path(self, path: str) -> None:
        self._proc_path = path
        self.proc_path_label.setText(path if path else "\u2014")
        self._update_out_label()

    def set_context(self, project_name: str, crystal_name: str, results_path: str) -> None:
        """Called when a crystal is loaded to provide naming and output path context."""
        self._project_name = project_name
        self._crystal_name = crystal_name
        self._results_path = results_path
        self._update_out_label()

    def _next_run_dir(self) -> Path | None:
        """Return the next run_N directory under the results tree, or None if unconfigured."""
        if not (self._results_path and self._crystal_name):
            return None
        base = (
            Path(self._results_path)
            / self._crystal_name
            / "processing"
            / self._pipeline()
        )
        existing = [
            d for d in base.iterdir()
            if d.is_dir() and d.name.startswith("run_") and d.name[4:].isdigit()
        ] if base.is_dir() else []
        run_n = max((int(d.name[4:]) for d in existing), default=0) + 1
        return base / f"run_{run_n}"

    def _default_out_dir(self) -> str:
        nd = self._next_run_dir()
        if nd is not None:
            return str(nd)
        # Fall back to proc_path-relative location if results_path not configured
        if not self._proc_path:
            return ""
        return str(Path(self._proc_path) / "results" / self._pipeline())

    def _update_out_label(self) -> None:
        self.out_dir_label.setText(self._default_out_dir())

    def _log(self, msg: str) -> None:
        self.log_message.emit(msg)

    # ---- Settings ----

    def _load_settings(self) -> None:
        cfg = configparser.ConfigParser()
        if not _CONFIG_PATH.exists():
            return
        cfg.read(_CONFIG_PATH)
        if "spread_gui" not in cfg:
            return
        s = cfg["spread_gui"]
        pipeline = s.get("pipeline", "")
        if pipeline == "xia2_dials":
            self.rb_dials.setChecked(True)
        elif pipeline == "xia2_3dii":
            self.rb_3dii.setChecked(True)
        elif pipeline == "autoproc":
            self.rb_autoproc.setChecked(True)
        self._update_out_label()

    # ---- Run analysis ----

    def _run_analysis(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        proc_path = self._proc_path
        if not proc_path:
            QMessageBox.warning(self, "Missing input", "No processing path set — load a crystal first.")
            return
        if not os.path.isdir(proc_path):
            QMessageBox.warning(self, "Invalid path", f"Directory not found:\n{proc_path}")
            return
        if not self._results_path:
            QMessageBox.warning(
                self, "No results path",
                "No results path configured for this project.\n\n"
                "Open Manage Projects, select the project, click Edit, and set a Results path.",
            )
            return

        out_dir = Path(self._default_out_dir())

        self.btn_run.setEnabled(False)
        self._log("--- Analyse Processing ---")
        self._log(f"Pipeline        : {self._pipeline()}")
        self._log(f"Processing path : {proc_path}")
        self._log(f"Output directory: {out_dir}")
        self._log("")

        self._worker = _AnalysisWorker(
            proc_path, self._pipeline(), str(out_dir),
            project_name=self._project_name,
            crystal_name=self._crystal_name,
            results_path=self._results_path,
        )
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ---- Worker callbacks ----

    def _on_finished(self, html_path: str) -> None:
        self._log("")
        self._log(f"Done. Opening report in browser: {html_path}")
        self._update_out_label()   # advance to next run number for display
        self.btn_run.setEnabled(True)
        webbrowser.open(f"file://{html_path}")

    def _on_error(self, msg: str) -> None:
        self._log(f"\nERROR: {msg}")
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Analysis failed", msg)
