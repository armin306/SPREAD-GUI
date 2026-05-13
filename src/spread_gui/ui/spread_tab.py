from __future__ import annotations

import glob
import os
import shlex
import shutil
import webbrowser
from pathlib import Path
from typing import Dict, List, Tuple

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from spread_gui.services.slurm import (
    check_ssh_key_auth,
    chmod_x,
    get_slurm_jwt,
    setup_ssh_key,
    submit_job_via_rest_api,
)

# Per-pipeline output directory prefix and job script name.
_PHENIX_DIR_PREFIX = {
    "xia2-dials": "phenix_dials",
    "xia2-3dii":  "phenix_3dii",
    "autoPROC":   "phenix_autoproc",
}
_PHENIX_SCRIPT_NAME = {
    "xia2-dials": "phenix_dials_jobs.sh",
    "xia2-3dii":  "phenix_3dii_jobs.sh",
    "autoPROC":   "phenix_autoproc_jobs.sh",
}

# Glob pattern for the MTZ file, relative to the images directory.
# xia2 may create a project/crystal subdirectory hierarchy, so use ** for recursion.
_MTZ_GLOB = {
    "xia2-dials": os.path.join("xia2-dials", "**", "*_free.mtz"),
    "xia2-3dii":  os.path.join("xia2-3dii",  "**", "*_free.mtz"),
    "autoPROC":   os.path.join("autoPROC", "staraniso_alldata-unique.mtz"),
}

# phenix.refine miller_array label arguments per pipeline.
# xia2 *_free.mtz uses plain anomalous labels; autoPROC adds ",merged".
_MILLER_LABELS = {
    "xia2-dials": (
        'miller_array.labels.name="I(+),SIGI(+),I(-),SIGI(-)"',
        'miller_array.labels.name="FreeR_flag"',
    ),
    "xia2-3dii": (
        'miller_array.labels.name="I(+),SIGI(+),I(-),SIGI(-)"',
        'miller_array.labels.name="FreeR_flag"',
    ),
    "autoPROC": (
        'miller_array.labels.name="I(+),SIGI(+),I(-),SIGI(-),merged"',
        'miller_array.labels.name="FreeR_flag"',
    ),
}


class _PhenixAnalysisWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)   # HTML path
    error    = pyqtSignal(str)   # error message

    def __init__(self, proc_path: str, pipeline: str, run: int, out_dir: str) -> None:
        super().__init__()
        self._proc_path = proc_path
        self._pipeline  = pipeline
        self._run       = run
        self._out_dir   = out_dir

    def run(self) -> None:
        try:
            from spread_gui.core.phenix_parser import collect_results
            from spread_gui.core.phenix_analysis import generate_report
            results = collect_results(Path(self._proc_path), self._pipeline, self._run)
            html_path = generate_report(
                results,
                Path(self._out_dir),
                self._pipeline,
                self._proc_path,
                self._run,
                self.progress.emit,
            )
            self.finished.emit(html_path)
        except Exception as exc:
            self.error.emit(str(exc))


class SpreadTab(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._proc_path: str = ""
        self._pdb_path:  str = ""
        self._anom_path: str = ""
        self._analysis_worker: _PhenixAnalysisWorker | None = None
        self._build_ui()
        QTimer.singleShot(0, self._check_ssh_key_status)

    # ---- Public API ----

    def set_proc_path(self, path: str) -> None:
        self._proc_path = path
        self.proc_path_label.setText(path if path else "\u2014")
        self._refresh_status()
        self._refresh_analysis_run_numbers()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        title = QLabel("SPREAD \u2013 Phenix Anomalous Refinement")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Input group
        in_box = QGroupBox("Input")
        g = QGridLayout(in_box)
        g.setColumnStretch(1, 1)

        g.addWidget(QLabel("Processing path:"), 0, 0)
        self.proc_path_label = QLabel("\u2014")
        self.proc_path_label.setStyleSheet("color:#444;")
        g.addWidget(self.proc_path_label, 0, 1, 1, 2)

        g.addWidget(QLabel("Pipeline:"), 1, 0)
        pl_row = QHBoxLayout()
        self.rb_dials    = QRadioButton("xia2-dials")
        self.rb_3dii     = QRadioButton("xia2-3dii")
        self.rb_autoproc = QRadioButton("autoPROC")
        self.rb_autoproc.setChecked(True)
        for rb in (self.rb_dials, self.rb_3dii, self.rb_autoproc):
            rb.toggled.connect(self._on_pipeline_changed)
            pl_row.addWidget(rb)
        pl_row.addStretch(1)
        g.addLayout(pl_row, 1, 1, 1, 2)

        g.addWidget(QLabel("PDB model:"), 2, 0)
        self.pdb_label = QLabel("\u2014")
        self.pdb_label.setStyleSheet("color:#444;")
        g.addWidget(self.pdb_label, 2, 1)
        self.btn_browse_pdb = QPushButton("Browse\u2026")
        self.btn_browse_pdb.clicked.connect(self._browse_pdb)
        g.addWidget(self.btn_browse_pdb, 2, 2)

        g.addWidget(QLabel("Anomalous groups:"), 3, 0)
        self.anom_label = QLabel("\u2014")
        self.anom_label.setStyleSheet("color:#444;")
        g.addWidget(self.anom_label, 3, 1)
        self.btn_browse_anom = QPushButton("Browse\u2026")
        self.btn_browse_anom.clicked.connect(self._browse_anom)
        g.addWidget(self.btn_browse_anom, 3, 2)

        g.addWidget(QLabel("Macro cycles:"), 4, 0)
        self.spin_cycles = QSpinBox()
        self.spin_cycles.setRange(1, 100)
        self.spin_cycles.setValue(6)
        g.addWidget(self.spin_cycles, 4, 1)

        root.addWidget(in_box)

        # Status group
        st_box = QGroupBox("Job status")
        sg = QGridLayout(st_box)
        sg.setColumnStretch(1, 1)

        sg.addWidget(QLabel("Run number:"), 0, 0)
        self.lbl_run = QLabel("\u2014")
        sg.addWidget(self.lbl_run, 0, 1)

        sg.addWidget(QLabel("Jobs ready:"), 1, 0)
        self.lbl_jobs = QLabel("\u2014")
        sg.addWidget(self.lbl_jobs, 1, 1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._refresh_status)
        sg.addWidget(self.btn_refresh, 0, 2, 2, 1)

        root.addWidget(st_box)

        # Submission method
        sub_box = QGroupBox("Submission method")
        sub_vbox = QVBoxLayout(sub_box)

        sub_row = QHBoxLayout()
        self.rb_rest = QRadioButton("REST API (recommended)")
        self.rb_dry  = QRadioButton("Dry run \u2014 generate script only")
        self.rb_rest.setChecked(True)
        sub_row.addWidget(self.rb_rest)
        sub_row.addWidget(self.rb_dry)
        sub_row.addStretch(1)
        sub_vbox.addLayout(sub_row)

        key_row = QHBoxLayout()
        self.ssh_key_status = QLabel("SSH key: unknown")
        self.ssh_key_status.setStyleSheet("color:#666;")
        self.btn_setup_ssh = QPushButton("Setup SSH key\u2026")
        self.btn_setup_ssh.clicked.connect(self._setup_ssh_key)
        key_row.addWidget(self.ssh_key_status)
        key_row.addWidget(self.btn_setup_ssh)
        key_row.addStretch(1)
        sub_vbox.addLayout(key_row)

        root.addWidget(sub_box)

        # Action row
        a_row = QHBoxLayout()
        self.btn_submit = QPushButton("Submit jobs")
        self.btn_submit.clicked.connect(self._submit)
        a_row.addWidget(self.btn_submit)
        a_row.addStretch(1)
        root.addLayout(a_row)

        # ---- Analyse SPREAD section ----
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        analyse_title = QLabel("Analyse SPREAD")
        af = QFont()
        af.setPointSize(11)
        af.setBold(True)
        analyse_title.setFont(af)
        root.addWidget(analyse_title)

        an_box = QGroupBox("Analysis input")
        ag = QGridLayout(an_box)
        ag.setColumnStretch(3, 1)

        ag.addWidget(QLabel("Pipeline / Run:"), 0, 0)

        self.an_rb_dials    = QRadioButton("xia2-dials")
        self.an_spin_dials  = QSpinBox()
        self.an_spin_dials.setRange(1, 999)
        self.an_spin_dials.setValue(1)

        self.an_rb_3dii     = QRadioButton("xia2-3dii")
        self.an_spin_3dii   = QSpinBox()
        self.an_spin_3dii.setRange(1, 999)
        self.an_spin_3dii.setValue(1)

        self.an_rb_autoproc = QRadioButton("autoPROC")
        self.an_spin_autoproc = QSpinBox()
        self.an_spin_autoproc.setRange(1, 999)
        self.an_spin_autoproc.setValue(1)
        self.an_rb_autoproc.setChecked(True)

        for row, (rb, spin) in enumerate([
            (self.an_rb_dials,    self.an_spin_dials),
            (self.an_rb_3dii,     self.an_spin_3dii),
            (self.an_rb_autoproc, self.an_spin_autoproc),
        ]):
            ag.addWidget(rb,   row, 1)
            ag.addWidget(QLabel("Run:"), row, 2)
            ag.addWidget(spin, row, 3)

        root.addWidget(an_box)

        an_out_row = QHBoxLayout()
        an_out_row.addWidget(QLabel("Output:"))
        self.an_out_label = QLabel("\u2014")
        self.an_out_label.setStyleSheet("color:#444;")
        an_out_row.addWidget(self.an_out_label, 1)
        root.addLayout(an_out_row)

        an_btn_row = QHBoxLayout()
        self.btn_run_analysis = QPushButton("Run Analysis")
        self.btn_run_analysis.clicked.connect(self._run_phenix_analysis)
        an_btn_row.addWidget(self.btn_run_analysis)
        an_btn_row.addStretch(1)
        root.addLayout(an_btn_row)

        # Wire analysis pipeline radio buttons
        for rb in (self.an_rb_dials, self.an_rb_3dii, self.an_rb_autoproc):
            rb.toggled.connect(self._update_analysis_out_label)
        for spin in (self.an_spin_dials, self.an_spin_3dii, self.an_spin_autoproc):
            spin.valueChanged.connect(self._update_analysis_out_label)

        root.addStretch(1)

    # ---- Pipeline helpers ----

    def _pipeline(self) -> str:
        if self.rb_dials.isChecked():
            return "xia2-dials"
        if self.rb_3dii.isChecked():
            return "xia2-3dii"
        return "autoPROC"

    def _on_pipeline_changed(self) -> None:
        pl = self._pipeline()
        self.lbl_run.setText(str(self._detect_run_number(pl)))
        self._update_job_count()

    # ---- File browsing ----

    def _files_dir(self) -> str:
        return os.path.join(self._proc_path, "files") if self._proc_path else ""

    def _copy_to_files(self, src: str) -> str | None:
        """Copy *src* into proc_dir/files/ and return the destination path.

        Skips the copy when src already lives there.  Returns None on error.
        """
        files_dir = self._files_dir()
        if not files_dir:
            return None
        os.makedirs(files_dir, exist_ok=True)
        dst = os.path.join(files_dir, os.path.basename(src))
        try:
            if not (os.path.exists(dst) and os.path.samefile(src, dst)):
                shutil.copy2(src, dst)
        except Exception as e:
            QMessageBox.warning(self, "File copy failed", str(e))
            return None
        return dst

    def _browse_pdb(self) -> None:
        start = self._files_dir() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDB model", start, "PDB files (*.pdb *.cif);;All files (*)"
        )
        if path:
            dest = self._copy_to_files(path)
            self._pdb_path = dest or path
            self.pdb_label.setText(os.path.basename(self._pdb_path))

    def _browse_anom(self) -> None:
        start = self._files_dir() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select anomalous groups file", start, "DEF files (*.def);;All files (*)"
        )
        if path:
            dest = self._copy_to_files(path)
            self._anom_path = dest or path
            self.anom_label.setText(os.path.basename(self._anom_path))

    # ---- Status ----

    def _detect_run_number(self, pipeline: str) -> int:
        """Return the next available run number for the given pipeline prefix."""
        if not self._proc_path:
            return 1
        prefix = _PHENIX_DIR_PREFIX[pipeline]
        pattern = os.path.join(self._proc_path, "*eV", "*img", f"{prefix}_*")
        max_n = 0
        for d in glob.glob(pattern):
            name = os.path.basename(d)
            if name.startswith(f"{prefix}_"):
                try:
                    max_n = max(max_n, int(name[len(prefix) + 1:]))
                except ValueError:
                    pass
        return max_n + 1

    def _jobs_for_pipeline(self, pipeline: str) -> List[Tuple[int, int]]:
        """Return (energy_eV, images) pairs where an MTZ exists for the given pipeline."""
        if not self._proc_path:
            return []
        mtz_glob = _MTZ_GLOB[pipeline]
        recursive = pipeline != "autoPROC"
        pattern = os.path.join(self._proc_path, "*eV", "*img", mtz_glob)
        seen: set[Tuple[int, int]] = set()
        for mtz in glob.glob(pattern, recursive=recursive):
            parts = Path(mtz).parts
            # Extract energy and images by finding the *eV and *img path components,
            # which are always present regardless of xia2's project/crystal subdir depth.
            try:
                energy = next(int(p[:-2]) for p in parts if p.endswith("eV") and p[:-2].isdigit())
                images = next(int(p[:-3]) for p in parts if p.endswith("img") and p[:-3].isdigit())
                seen.add((energy, images))
            except StopIteration:
                pass
        return sorted(seen)

    def _pipeline_counts(self) -> Dict[str, int]:
        return {pl: len(self._jobs_for_pipeline(pl)) for pl in _MTZ_GLOB}

    def _refresh_status(self) -> None:
        """Update pipeline radio button availability, run number, and job count."""
        counts = self._pipeline_counts()

        # Enable / disable radio buttons based on MTZ availability.
        radio_map = {
            "xia2-dials": self.rb_dials,
            "xia2-3dii":  self.rb_3dii,
            "autoPROC":   self.rb_autoproc,
        }
        for pl, rb in radio_map.items():
            rb.setEnabled(counts[pl] > 0)

        # If the currently selected pipeline has no jobs, switch to the first available.
        if not radio_map[self._pipeline()].isEnabled():
            for pl in ("autoPROC", "xia2-dials", "xia2-3dii"):
                if counts[pl] > 0:
                    radio_map[pl].setChecked(True)
                    break

        self.lbl_run.setText(str(self._detect_run_number(self._pipeline())))
        self._update_job_count()

    def _update_job_count(self) -> None:
        n = len(self._jobs_for_pipeline(self._pipeline()))
        if n:
            self.lbl_jobs.setText(f"{n} (MTZ found for each)")
        else:
            self.lbl_jobs.setText("0 \u2014 no MTZ files found for this pipeline")

    # ---- SSH key ----

    def _check_ssh_key_status(self) -> None:
        ok, err = check_ssh_key_auth()
        if ok:
            self.ssh_key_status.setText("SSH key: OK (passwordless)")
            self.ssh_key_status.setStyleSheet("color:green;")
            self.ssh_key_status.setToolTip("")
            self.btn_setup_ssh.setText("Re-setup SSH key\u2026")
        else:
            self.ssh_key_status.setText("SSH key: auth failed \u2014 see tooltip")
            self.ssh_key_status.setStyleSheet("color:#b05000;")
            self.ssh_key_status.setToolTip(err or "SSH returned a non-zero exit code")

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
            QMessageBox.warning(self, "SSH key setup failed", str(e))
            return
        self._check_ssh_key_status()
        self.log_message.emit("SSH key copied to Wilson.")

    # ---- Script generation ----

    def _mtz_symlink_snippet(self, pipeline: str) -> str:
        """Return the bash lines that create the MTZ symlink inside the phenix dir."""
        if pipeline == "autoPROC":
            return (
                "ln -s ${images_dir}/autoPROC/staraniso_alldata-unique.mtz"
                " ${energy}eV_${images}img.mtz"
            )
        # xia2: search recursively — xia2 may place DataFiles under a
        # project/crystal subdirectory depending on how it was invoked.
        subdir = pipeline  # "xia2-dials" or "xia2-3dii"
        return (
            f'mtz_file=$(find "${{images_dir}}/{subdir}" -name "*_free.mtz"'
            ' 2>/dev/null | head -1)\n'
            '[ -z "$mtz_file" ] && { echo "No MTZ found under '
            f'{subdir}" >&2; exit 1; }}\n'
            'ln -s "$mtz_file" ${energy}eV_${images}img.mtz'
        )

    def _make_phenix_script(
        self, pdb_name: str, anom_name: str, run: int, macro_cycles: int, pipeline: str
    ) -> str:
        lbl1, lbl2 = _MILLER_LABELS[pipeline]
        mtz_snippet = self._mtz_symlink_snippet(pipeline)
        dir_prefix = _PHENIX_DIR_PREFIX[pipeline]
        return f"""#!/bin/bash
. /etc/profile.d/modules.sh
#SBATCH --job-name=phenix_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=10G
module load phenix

BASE_DIR=$(pwd)
RUN={run}

energy=$1
images=$2

energy_dir="${{BASE_DIR}}/${{energy}}eV"
images_dir="${{energy_dir}}/${{images}}img"
phenix_dir="${{images_dir}}/{dir_prefix}_${{RUN}}"

rm -rf "$phenix_dir"
mkdir -p "$phenix_dir"
cd "$phenix_dir" || exit

ln -s ${{BASE_DIR}}/files/{pdb_name} ${{energy}}eV_${{images}}img.pdb
ln -s ${{BASE_DIR}}/files/{anom_name} .
{mtz_snippet}

phenix.refine ${{energy}}eV_${{images}}img.pdb ${{energy}}eV_${{images}}img.mtz \\
  refinement.main.number_of_macro_cycles={macro_cycles} \\
  {lbl1} \\
  {lbl2} \\
  strategy=group_anomalous \\
  {anom_name}
"""

    # ---- Submission ----

    def _submit(self) -> None:
        if not self._proc_path:
            QMessageBox.warning(self, "No crystal loaded", "Load a crystal first.")
            return
        if not self._pdb_path:
            QMessageBox.warning(self, "Missing PDB", "Select a PDB model file.")
            return
        if not self._anom_path:
            QMessageBox.warning(self, "Missing anomalous groups file",
                                "Select an anomalous groups (.def) file.")
            return

        pipeline = self._pipeline()
        jobs = self._jobs_for_pipeline(pipeline)
        if not jobs:
            QMessageBox.warning(
                self, "No jobs ready",
                f"No MTZ files found for {pipeline}.\n"
                "Run the corresponding processing pipeline first.",
            )
            return

        run          = self._detect_run_number(pipeline)
        macro_cycles = self.spin_cycles.value()
        dry          = self.rb_dry.isChecked()
        proc_dir     = self._proc_path
        pdb_name     = os.path.basename(self._pdb_path)
        anom_name    = os.path.basename(self._anom_path)

        # Copy input files into proc_dir/files/
        files_dir   = os.path.join(proc_dir, "files")
        scripts_dir = os.path.join(proc_dir, "scripts")
        os.makedirs(files_dir,   exist_ok=True)
        os.makedirs(scripts_dir, exist_ok=True)

        try:
            for src, name in ((self._pdb_path, pdb_name), (self._anom_path, anom_name)):
                dst = os.path.join(files_dir, name)
                if not (os.path.exists(dst) and os.path.samefile(src, dst)):
                    shutil.copy2(src, dst)
        except Exception as e:
            QMessageBox.warning(self, "File copy failed", str(e))
            return

        # Write phenix job script
        script_path = os.path.join(scripts_dir, _PHENIX_SCRIPT_NAME[pipeline])
        try:
            with open(script_path, "wt") as fh:
                fh.write(self._make_phenix_script(
                    pdb_name, anom_name, run, macro_cycles, pipeline
                ))
            chmod_x(script_path)
        except Exception as e:
            QMessageBox.warning(self, "Script write failed", str(e))
            return

        self.log_message.emit(
            f"Phenix run {run} | pipeline: {pipeline} | "
            f"{macro_cycles} macro cycles | {len(jobs)} job(s)"
        )
        self.log_message.emit(f"PDB: {pdb_name}   Anom groups: {anom_name}")

        if dry:
            self.log_message.emit("")
            self.log_message.emit("Dry run \u2014 script generated, nothing submitted.")
            self.log_message.emit(f"Script: {script_path}")
            self.log_message.emit("")
            self.log_message.emit("To submit manually, open a terminal on Wilson and run:")
            self.log_message.emit(f"  cd {shlex.quote(proc_dir)}")
            for energy, images in jobs:
                self.log_message.emit(
                    f"  sbatch scripts/{_PHENIX_SCRIPT_NAME[pipeline]} {energy} {images}"
                )
            return

        # REST API submission
        mw = self.window()
        total = len(jobs)
        if hasattr(mw, "set_status"):
            mw.set_status("Fetching SLURM token\u2026", 0, total)
        try:
            token = get_slurm_jwt()
            self.log_message.emit("SLURM JWT token acquired.")
        except Exception as e:
            QMessageBox.warning(
                self, "Token error",
                f"Could not obtain SLURM JWT via SSH to wilson:\n\n{e}",
            )
            return

        submitted = 0
        for energy, images in jobs:
            submitted += 1
            if hasattr(mw, "set_status"):
                mw.set_status("Submitting Phenix jobs\u2026", submitted, total)
            wrapper = (
                f"#!/bin/bash\n"
                f"bash {shlex.quote(script_path)} {energy} {images}\n"
            )
            rc, out, err = submit_job_via_rest_api(
                wrapper, proc_dir, token, job_name=f"phenix_r{run}"
            )
            if rc in (0, 200):
                self.log_message.emit(
                    f"  Submitted ({energy} eV, {images} img): {out.strip()}"
                )
            else:
                self.log_message.emit(
                    f"  Failed ({energy} eV, {images} img) rc={rc}: {err or out}"
                )

        if hasattr(mw, "set_status"):
            mw.set_status("Done.", total, total)
        QMessageBox.information(
            self, "Submission complete",
            f"Submitted {total} Phenix job(s) as run {run}.",
        )
        self._refresh_status()

    # ---- Analyse SPREAD ----

    def _analysis_pipeline(self) -> str:
        if self.an_rb_dials.isChecked():
            return "xia2-dials"
        if self.an_rb_3dii.isChecked():
            return "xia2-3dii"
        return "autoPROC"

    def _analysis_run(self) -> int:
        if self.an_rb_dials.isChecked():
            return self.an_spin_dials.value()
        if self.an_rb_3dii.isChecked():
            return self.an_spin_3dii.value()
        return self.an_spin_autoproc.value()

    def _detect_last_run(self, pipeline: str) -> int:
        """Return the highest existing run number for a pipeline, or 0 if none."""
        if not self._proc_path:
            return 0
        prefix = _PHENIX_DIR_PREFIX[pipeline]
        pattern = os.path.join(self._proc_path, "*eV", "*img", f"{prefix}_*")
        max_n = 0
        for d in glob.glob(pattern):
            name = os.path.basename(d)
            if name.startswith(f"{prefix}_"):
                try:
                    max_n = max(max_n, int(name[len(prefix) + 1:]))
                except ValueError:
                    pass
        return max_n

    def _refresh_analysis_run_numbers(self) -> None:
        """Enable/disable analysis radio buttons and populate spinboxes."""
        rb_spin_map = {
            "xia2-dials": (self.an_rb_dials,    self.an_spin_dials),
            "xia2-3dii":  (self.an_rb_3dii,     self.an_spin_3dii),
            "autoPROC":   (self.an_rb_autoproc,  self.an_spin_autoproc),
        }
        for pipeline, (rb, spin) in rb_spin_map.items():
            last = self._detect_last_run(pipeline)
            has_runs = last > 0
            rb.setEnabled(has_runs)
            spin.setEnabled(has_runs)
            spin.setValue(last if has_runs else 1)

        # If the selected pipeline has no runs, switch to the first available.
        if not rb_spin_map[self._analysis_pipeline()][0].isEnabled():
            for pipeline in ("autoPROC", "xia2-dials", "xia2-3dii"):
                if rb_spin_map[pipeline][0].isEnabled():
                    rb_spin_map[pipeline][0].setChecked(True)
                    break

        self._update_analysis_out_label()

    def _analysis_out_dir(self) -> str:
        if not self._proc_path:
            return ""
        pipeline = self._analysis_pipeline()
        run      = self._analysis_run()
        prefix   = _PHENIX_DIR_PREFIX[pipeline]
        return str(Path(self._proc_path) / "results" / f"{prefix}_{run}")

    def _update_analysis_out_label(self) -> None:
        self.an_out_label.setText(self._analysis_out_dir() or "\u2014")

    def _run_phenix_analysis(self) -> None:
        if self._analysis_worker and self._analysis_worker.isRunning():
            return
        if not self._proc_path:
            QMessageBox.warning(self, "No crystal loaded", "Load a crystal first.")
            return
        if not os.path.isdir(self._proc_path):
            QMessageBox.warning(self, "Invalid path",
                                f"Directory not found:\n{self._proc_path}")
            return

        pipeline = self._analysis_pipeline()
        run      = self._analysis_run()
        out_dir  = self._analysis_out_dir()

        self.btn_run_analysis.setEnabled(False)
        self.log_message.emit("--- Analyse SPREAD ---")
        self.log_message.emit(f"Pipeline : {pipeline}  Run: {run}")
        self.log_message.emit(f"Output   : {out_dir}")

        self._analysis_worker = _PhenixAnalysisWorker(
            self._proc_path, pipeline, run, out_dir
        )
        self._analysis_worker.progress.connect(self.log_message)
        self._analysis_worker.finished.connect(self._on_phenix_finished)
        self._analysis_worker.error.connect(self._on_phenix_error)
        self._analysis_worker.start()

    def _on_phenix_finished(self, html_path: str) -> None:
        self.log_message.emit(f"Done. Opening report: {html_path}")
        self.btn_run_analysis.setEnabled(True)
        webbrowser.open(f"file://{html_path}")

    def _on_phenix_error(self, msg: str) -> None:
        self.log_message.emit(f"\nERROR: {msg}")
        self.btn_run_analysis.setEnabled(True)
        QMessageBox.critical(self, "Analysis failed", msg)
