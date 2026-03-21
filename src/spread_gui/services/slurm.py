from __future__ import annotations
import os
import stat
import shlex
import subprocess
from typing import List, Tuple

def chmod_x(path: str) -> None:
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

def run_sbatch(cmd: List[str], cwd: str, dry_run: bool) -> Tuple[int, str, str]:
    if dry_run:
        return 0, "[DRY] " + " ".join(shlex.quote(x) for x in cmd), ""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return p.returncode, out, err
