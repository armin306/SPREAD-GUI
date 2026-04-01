from __future__ import annotations

import configparser
import os
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from spread_gui.core.xia2_analysis import collect_results, generate_report

_CONFIG_PATH = Path.home() / ".config" / "spread_gui" / "settings.ini"


# ---------------------------------------------------------------------------
# Existing-results dialog
# ---------------------------------------------------------------------------

class _ExistingResultsDialog(QDialog):
    """
    Shown when the default output directory already exists.
    Three choices:
      SHOW      — open the existing HTML in the browser
      OVERWRITE — re-run and overwrite the existing directory
      NEW_DIR   — re-run into a user-chosen directory
    """

    SHOW      = "show"
    OVERWRITE = "overwrite"
    NEW_DIR   = "new_dir"

    def __init__(self, out_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Analysis results already exist")
        self.choice: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        msg = QLabel(
            f"Analysis results already exist in:\n<b>{out_dir}</b>"
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        def _btn(label: str, description: str, choice: str) -> None:
            b = QPushButton(label)
            b.setToolTip(description)
            b.clicked.connect(lambda: self._pick(choice))
            layout.addWidget(b)

        _btn("Show existing results",
             "Open the existing HTML report in the browser without re-running.",
             self.SHOW)
        _btn("Re-run and overwrite",
             "Delete the existing results and run the analysis again in the same directory.",
             self.OVERWRITE)
        _btn("Re-run in a new directory",
             "Keep the existing results and write the new analysis to a different directory.",
             self.NEW_DIR)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _AnalysisWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)   # HTML path on success
    error    = pyqtSignal(str)   # error message on failure

    def __init__(self, proc_path: str, pipeline: str, out_dir: str) -> None:
        super().__init__()
        self._proc_path = proc_path
        self._pipeline  = pipeline
        self._out_dir   = out_dir

    def run(self) -> None:
        try:
            results = collect_results(Path(self._proc_path), self._pipeline)
            html_path = generate_report(
                results,
                Path(self._out_dir),
                self._pipeline,
                self._proc_path,
                self.progress.emit,
            )
            self.finished.emit(html_path)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Analysis tab widget
# ---------------------------------------------------------------------------

class AnalysisTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: _AnalysisWorker | None = None
        self._build_ui()
        self._load_settings()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        title = QLabel("SPREAD \u2013 Analysis")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Input group
        in_box = QGroupBox("Input")
        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout(in_box)

        g.addWidget(QLabel("Processing path:"), 0, 0)
        self.proc_path_edit = QLineEdit()
        self.proc_path_edit.setPlaceholderText("Directory containing {energy}eV sub-directories")
        self.btn_browse = QPushButton("Browse\u2026")
        g.addWidget(self.proc_path_edit, 0, 1)
        g.addWidget(self.btn_browse, 0, 2)

        g.addWidget(QLabel("Pipeline:"), 1, 0)
        pl_row = QHBoxLayout()
        self.rb_dials = QRadioButton("xia2-dials")
        self.rb_3dii  = QRadioButton("xia2-3dii")
        self.rb_dials.setChecked(True)
        pl_row.addWidget(self.rb_dials)
        pl_row.addWidget(self.rb_3dii)
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

        # Log
        root.addWidget(QLabel("Log"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Analysis output will appear here\u2026")
        root.addWidget(self.log, 1)

        # Signals
        self.btn_browse.clicked.connect(self._browse_proc)
        self.btn_run.clicked.connect(self._run_analysis)
        self.proc_path_edit.textChanged.connect(self._update_out_label)
        self.rb_dials.toggled.connect(self._update_out_label)

    # ---- Helpers ----

    def _pipeline(self) -> str:
        return "xia2-dials" if self.rb_dials.isChecked() else "xia2-3dii"

    def _default_out_dir(self) -> str:
        proc = self.proc_path_edit.text().strip()
        if not proc:
            return ""
        return str(Path(proc) / "results" / self._pipeline())

    def _update_out_label(self) -> None:
        self.out_dir_label.setText(self._default_out_dir())

    def _browse_proc(self) -> None:
        start = self.proc_path_edit.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select processing directory", start)
        if d:
            self.proc_path_edit.setText(d)

    def _log(self, msg: str) -> None:
        self.log.append(msg)
        self.log.ensureCursorVisible()

    # ---- Settings ----

    def _load_settings(self) -> None:
        """Pre-populate proc path and pipeline from shared settings.ini."""
        cfg = configparser.ConfigParser()
        if not _CONFIG_PATH.exists():
            return
        cfg.read(_CONFIG_PATH)
        if "spread_gui" not in cfg:
            return
        s = cfg["spread_gui"]
        if "proc_path" in s:
            self.proc_path_edit.setText(s["proc_path"])
        pipeline = s.get("pipeline", "")
        if pipeline == "xia2_dials":
            self.rb_dials.setChecked(True)
        elif pipeline == "xia2_3dii":
            self.rb_3dii.setChecked(True)
        self._update_out_label()

    # ---- Run analysis ----

    def _run_analysis(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        proc_path = self.proc_path_edit.text().strip()
        if not proc_path:
            QMessageBox.warning(self, "Missing input", "Please set the processing path.")
            return
        if not os.path.isdir(proc_path):
            QMessageBox.warning(self, "Invalid path", f"Directory not found:\n{proc_path}")
            return

        out_dir = self._resolve_out_dir(proc_path)
        if out_dir is None:
            return  # user cancelled

        self.log.clear()
        self.btn_run.setEnabled(False)
        self._log(f"Pipeline        : {self._pipeline()}")
        self._log(f"Processing path : {proc_path}")
        self._log(f"Output directory: {out_dir}")
        self._log("")

        self._worker = _AnalysisWorker(proc_path, self._pipeline(), str(out_dir))
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _resolve_out_dir(self, proc_path: str) -> Path | None:
        """
        Determine the output directory.

        If the default doesn't exist yet, return it directly.
        If it already exists, ask the user via a three-option dialog:
          • Show results  → open existing HTML, return None (no re-run)
          • Overwrite     → confirm, return the same directory
          • New directory → prompt for a name, return that directory
        Returns None to abort (user cancelled, or "show results" was chosen).
        """
        out_dir = Path(self._default_out_dir())

        if not out_dir.exists():
            return out_dir

        dlg = _ExistingResultsDialog(out_dir, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.choice is None:
            return None

        if dlg.choice == _ExistingResultsDialog.SHOW:
            html = out_dir / "index.html"
            if html.exists():
                webbrowser.open(f"file://{html}")
            else:
                QMessageBox.warning(
                    self, "No report found",
                    f"index.html not found in:\n{out_dir}\n\n"
                    "The previous run may have failed. Use 'Re-run' to generate a new report.",
                )
            return None

        if dlg.choice == _ExistingResultsDialog.OVERWRITE:
            ans = QMessageBox.question(
                self, "Overwrite existing results?",
                f"All files in:\n{out_dir}\nwill be replaced. Continue?",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return None
            return out_dir

        # NEW_DIR — ask for a name, loop until it's free
        suggestion = self._pipeline() + "-2"
        while True:
            name, ok = QInputDialog.getText(
                self,
                "Choose a new output directory",
                "Enter a sub-directory name under results/:",
                text=suggestion,
            )
            if not ok:
                return None
            name = name.strip()
            if not name:
                continue
            candidate = Path(proc_path) / "results" / name
            if not candidate.exists():
                return candidate
            QMessageBox.warning(
                self, "Already exists",
                f"\u2018{candidate}\u2019 also exists. Please choose a different name.",
            )
            suggestion = name + "-2"

    # ---- Worker callbacks ----

    def _on_finished(self, html_path: str) -> None:
        self._log("")
        self._log(f"Done. Opening report in browser: {html_path}")
        self.btn_run.setEnabled(True)
        webbrowser.open(f"file://{html_path}")

    def _on_error(self, msg: str) -> None:
        self._log(f"\nERROR: {msg}")
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Analysis failed", msg)
