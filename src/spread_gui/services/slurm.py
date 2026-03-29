from __future__ import annotations

import json
import os
import stat
import shlex
import subprocess
from typing import List, Tuple

try:
    import requests as _requests  # type: ignore
except Exception:
    _requests = None

_SLURM_REST_URL = "https://slurm-rest.diamond.ac.uk:8443/slurm/v0.0.40/job/submit"
_SLURM_GATEWAY = "wilson"


def chmod_x(path: str) -> None:
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        raise RuntimeError(f"Could not make {path} executable: {e}") from e


def run_sbatch(cmd: List[str], cwd: str, dry_run: bool) -> Tuple[int, str, str]:
    if dry_run:
        return 0, "[DRY] " + " ".join(shlex.quote(x) for x in cmd), ""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return p.returncode, out, err


def check_ssh_key_auth(gateway: str = _SLURM_GATEWAY) -> bool:
    """Return True if passwordless SSH to the gateway node works."""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", gateway, "exit"],
        capture_output=True,
    )
    return result.returncode == 0


def setup_ssh_key(password: str, gateway: str = _SLURM_GATEWAY) -> None:
    """Copy the user's SSH public key to the gateway using a Qt-supplied password.

    The password is passed to ssh-copy-id via SSH_ASKPASS so it never appears
    on the terminal.  The temporary askpass script is deleted immediately after.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as fh:
        fh.write(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(password)}\n")
        askpass_path = fh.name

    try:
        os.chmod(askpass_path, 0o700)
        env = os.environ.copy()
        env["SSH_ASKPASS"] = askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", ":0")

        result = subprocess.run(
            ["ssh-copy-id", gateway],
            capture_output=True,
            text=True,
            env=env,
            start_new_session=True,
            timeout=30,
        )
    finally:
        try:
            os.unlink(askpass_path)
        except Exception:
            pass

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def get_slurm_jwt(lifespan: int = 300) -> str:
    """SSH to the gateway node and retrieve a short-lived SLURM JWT token."""
    result = subprocess.run(
        ["ssh", _SLURM_GATEWAY, f"scontrol token lifespan={lifespan}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"SSH to {_SLURM_GATEWAY} failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    for line in result.stdout.splitlines():
        if "SLURM_JWT" in line:
            return line.split("=", 1)[1].strip()
    raise RuntimeError(
        f"SLURM_JWT not found in scontrol output:\n{result.stdout.strip()}"
    )


def _prepare_script(content: str) -> str:
    """Ensure the script has a bash shebang and sources the modules environment."""
    if not content.startswith("#!/bin/bash"):
        content = "#!/bin/bash\n" + content
    lines = content.splitlines()
    if not any(". /etc/profile.d/modules.sh" in line for line in lines):
        lines.insert(1, ". /etc/profile.d/modules.sh")
    return "\n".join(lines)


def submit_job_via_rest_api(
    script_content: str,
    cwd: str,
    token: str,
    job_name: str = "i23_proc",
    dry_run: bool = False,
) -> Tuple[int, str, str]:
    """Submit a job script to SLURM via the Diamond Light Source REST API."""
    if dry_run:
        preview = "\n".join(script_content.splitlines()[:3])
        return 0, f"[DRY] Would POST to SLURM REST API:\n{preview}\n…", ""

    script = _prepare_script(script_content)
    user = os.environ.get("USER", "")

    payload: dict = {
        "job": {
            "partition": "cs04r",
            "tasks": 1,
            "name": job_name,
            "nodes": 1,
            "cpus_per_task": "20",
            "memory_per_cpu": "10G",
            "current_working_directory": cwd,
            "environment": {
                "USER": user,
                "PATH": (
                    "/usr/share/Modules/bin:/usr/local/bin:/usr/local/sbin"
                    f":/usr/bin:/usr/sbin:/var/cfengine/bin:/home/{user}/bin"
                ),
            },
        },
        "script": script,
    }

    headers = {
        "X-SLURM-USER-NAME": user,
        "X-SLURM-USER-TOKEN": token,
        "Content-Type": "application/json",
    }

    if _requests is not None:
        try:
            r = _requests.post(
                _SLURM_REST_URL, headers=headers, json=payload, timeout=30
            )
            return r.status_code, r.text, ""
        except Exception as exc:
            return 1, "", str(exc)

    # Fallback: urllib (no requests library)
    import urllib.request
    import urllib.error

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _SLURM_REST_URL, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), ""
    except urllib.error.HTTPError as exc:
        return exc.code, "", exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 1, "", str(exc)
