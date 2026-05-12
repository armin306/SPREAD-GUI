from __future__ import annotations

import glob
import os
import shlex
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
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
        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout(in_box)
        g.setColumnStretch(1, 1)

        g.addWidget(QLabel("Processing path:"), 0, 0)
        self.proc_path_label = QLabel("\u2014")
        self.proc_path_label.setStyleSheet("color:#444;")
        g.addWidget(self.proc_path_label, 0, 1, 1, 2)

        g.addWidget(QLabel("PDB model:"), 1, 0)
        self.pdb_label = QLabel("\u2014")
        self.pdb_label.setStyleSheet("color:#444;")
        g.addWidget(self.pdb_label, 1, 1)
        self.btn_browse_pdb = QPushButton("Browse\u2026")
        self.btn_browse_pdb.clicked.connect(self._browse_pdb)
        g.addWidget(self.btn_browse_pdb, 1, 2)

        g.addWidget(QLabel("Anomalous groups:"), 2, 0)
        self.anom_label = QLabel("\u2014")
        self.anom_label.setStyleSheet("color:#444;")
        g.addWidget(self.anom_label, 2, 1)
        self.btn_browse_anom = QPushButton("Browse\u2026")
        self.btn_browse_anom.clicked.connect(self._browse_anom)
        g.addWidget(self.btn_browse_anom, 2, 2)

        g.addWidget(QLabel("Macro cycles:"), 3, 0)
        self.spin_cycles = QSpinBox()
        self.spin_cycles.setRange(1, 100)
        self.spin_cycles.setValue(6)
        g.addWidget(self.spin_cycles, 3, 1)

        root.addWidget(in_box)

        # Status group
        st_box = QGroupBox("Job status")
        from PyQt6.QtWidgets import QGridLayout as _GL
        sg = _GL(st_box)
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

    # ---- File browsing ----

    def _browse_pdb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDB model", "", "PDB files (*.pdb *.cif);;All files (*)"
        )
        if path:
            self._pdb_path = path
            self.pdb_label.setText(os.path.basename(path))

    def _browse_anom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select anomalous groups file", "", "DEF files (*.def);;All files (*)"
        )
        if path:
            self._anom_path = path
            self.anom_label.setText(os.path.basename(path))

    # ---- Status ----

    def _detect_run_number(self) -> int:
        """Return the next available phenix_N run number."""
        if not self._proc_path:
            return 1
        pattern = os.path.join(self._proc_path, "*eV", "*img", "phenix_*")
        max_n = 0
        for d in glob.glob(pattern):
            basename = os.path.basename(d)
            if basename.startswith("phenix_"):
                try:
                    max_n = max(max_n, int(basename[len("phenix_"):]))
                except ValueError:
                    pass
        return max_n + 1

    def _find_available_jobs(self) -> List[Tuple[int, int]]:
        """Return (energy_eV, images) pairs where an autoPROC MTZ exists."""
        if not self._proc_path:
            return []
        pattern = os.path.join(
            self._proc_path, "*eV", "*img",
            "autoPROC", "staraniso_alldata-unique.mtz",
        )
        jobs: List[Tuple[int, int]] = []
        for mtz in sorted(glob.glob(pattern)):
            p = Path(mtz).parts
            try:
                energy = int(p[-4][:-2])   # strip "eV"
                images = int(p[-3][:-3])   # strip "img"
                jobs.append((energy, images))
            except (ValueError, IndexError):
                pass
        return jobs

    def _refresh_status(self) -> None:
        run = self._detect_run_number()
        self.lbl_run.setText(str(run))
        jobs = self._find_available_jobs()
        if jobs:
            self.lbl_jobs.setText(f"{len(jobs)} (MTZ found for each)")
        else:
            self.lbl_jobs.setText("0 \u2014 no autoPROC MTZ files found")

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

    def _make_phenix_script(
        self, pdb_name: str, anom_name: str, run: int, macro_cycles: int
    ) -> str:
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
ln -s ${{images_dir}}/autoPROC/staraniso_alldata-unique.mtz ${{energy}}eV_${{images}}img.mtz

phenix.refine ${{energy}}eV_${{images}}img.pdb ${{energy}}eV_${{images}}img.mtz \\
  refinement.main.number_of_macro_cycles={macro_cycles} \\
  miller_array.labels.name="I(+),SIGI(+),I(-),SIGI(-),merged" \\
  miller_array.labels.name="FreeR_flag" \\
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

        jobs = self._find_available_jobs()
        if not jobs:
            QMessageBox.warning(
                self, "No jobs ready",
                "No autoPROC staraniso_alldata-unique.mtz files found.\n"
                "Run autoPROC processing first.",
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
            shutil.copy2(self._pdb_path,  os.path.join(files_dir, pdb_name))
            shutil.copy2(self._anom_path, os.path.join(files_dir, anom_name))
        except Exception as e:
            QMessageBox.warning(self, "File copy failed", str(e))
            return

        # Write phenix job script
        script_path = os.path.join(scripts_dir, _PHENIX_SCRIPT_NAME)
        try:
            with open(script_path, "wt") as fh:
                fh.write(self._make_phenix_script(pdb_name, anom_name, run, macro_cycles))
            chmod_x(script_path)
        except Exception as e:
            QMessageBox.warning(self, "Script write failed", str(e))
            return

        self.log_message.emit(
            f"Phenix run {run}, {macro_cycles} macro cycle(s), {len(jobs)} job(s)"
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
