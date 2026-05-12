from __future__ import annotations

import glob
import os
import shlex
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
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

_PHENIX_SCRIPT_NAME = "phenix_jobs.sh"

# Glob pattern for the MTZ file, relative to the images directory.
_MTZ_GLOB = {
    "xia2-dials": os.path.join("xia2-dials", "DataFiles", "*_free.mtz"),
    "xia2-3dii":  os.path.join("xia2-3dii",  "DataFiles", "*_free.mtz"),
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


class SpreadTab(QWidget):
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._proc_path: str = ""
        self._pdb_path:  str = ""
        self._anom_path: str = ""
        self._build_ui()
        QTimer.singleShot(0, self._check_ssh_key_status)

    # ---- Public API ----

    def set_proc_path(self, path: str) -> None:
        self._proc_path = path
        self.proc_path_label.setText(path if path else "\u2014")
        self._refresh_status()

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

        root.addStretch(1)

    # ---- Pipeline helpers ----

    def _pipeline(self) -> str:
        if self.rb_dials.isChecked():
            return "xia2-dials"
        if self.rb_3dii.isChecked():
            return "xia2-3dii"
        return "autoPROC"

    def _on_pipeline_changed(self) -> None:
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

    def _detect_run_number(self) -> int:
        """Return the next available phenix_N run number."""
        if not self._proc_path:
            return 1
        pattern = os.path.join(self._proc_path, "*eV", "*img", "phenix_*")
        max_n = 0
        for d in glob.glob(pattern):
            name = os.path.basename(d)
            if name.startswith("phenix_"):
                try:
                    max_n = max(max_n, int(name[len("phenix_"):]))
                except ValueError:
                    pass
        return max_n + 1

    def _jobs_for_pipeline(self, pipeline: str) -> List[Tuple[int, int]]:
        """Return (energy_eV, images) pairs where an MTZ exists for the given pipeline."""
        if not self._proc_path:
            return []
        mtz_glob = _MTZ_GLOB[pipeline]
        pattern = os.path.join(self._proc_path, "*eV", "*img", mtz_glob)
        jobs: List[Tuple[int, int]] = []
        for mtz in sorted(glob.glob(pattern)):
            p = Path(mtz).parts
            # parts[-1] = mtz filename
            # autoPROC:  parts[-2]="autoPROC",  parts[-3]="{N}img", parts[-4]="{E}eV"
            # xia2:      parts[-2]="DataFiles",  parts[-3]=pipeline, parts[-4]="{N}img", parts[-5]="{E}eV"
            try:
                if pipeline == "autoPROC":
                    energy = int(p[-4][:-2])
                    images = int(p[-3][:-3])
                else:
                    energy = int(p[-5][:-2])
                    images = int(p[-4][:-3])
                jobs.append((energy, images))
            except (ValueError, IndexError):
                pass
        return jobs

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

        self.lbl_run.setText(str(self._detect_run_number()))
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
        # xia2: glob for *_free.mtz since the name encodes project/crystal
        subdir = pipeline  # "xia2-dials" or "xia2-3dii"
        return (
            f'mtz_file=$(ls "${{images_dir}}/{subdir}/DataFiles/"*_free.mtz'
            ' 2>/dev/null | head -1)\n'
            '[ -z "$mtz_file" ] && { echo "No MTZ found for '
            f'{subdir}" >&2; exit 1; }}\n'
            "ln -s \"$mtz_file\" ${energy}eV_${images}img.mtz"
        )

    def _make_phenix_script(
        self, pdb_name: str, anom_name: str, run: int, macro_cycles: int, pipeline: str
    ) -> str:
        lbl1, lbl2 = _MILLER_LABELS[pipeline]
        mtz_snippet = self._mtz_symlink_snippet(pipeline)
        return f"""#!/bin/bash
. /etc/profile.d/modules.sh
#SBATCH --job-name=phenix_job
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#SBATCH --partition=cs04r
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20
#SBATCH --mem=10G
module load phenix

BASE_DIR=$(pwd)
RUN={run}

energy=$1
images=$2

energy_dir="${{BASE_DIR}}/${{energy}}eV"
images_dir="${{energy_dir}}/${{images}}img"
phenix_dir="${{images_dir}}/phenix_${{RUN}}"

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

        run          = self._detect_run_number()
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
        script_path = os.path.join(scripts_dir, _PHENIX_SCRIPT_NAME)
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
                    f"  sbatch scripts/{_PHENIX_SCRIPT_NAME} {energy} {images}"
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
