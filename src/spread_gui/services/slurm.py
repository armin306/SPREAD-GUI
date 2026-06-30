from __future__ import annotations

import json
import os
import re
import stat
import shlex
import subprocess
from pathlib import Path
from typing import Tuple

try:
    import requests as _requests  # type: ignore
except Exception:
    _requests = None

_SLURM_REST_BASE    = "https://slurm-rest.diamond.ac.uk:8443"
_SLURM_REST_FALLBACK_VERSION = "v0.0.40"
_SLURM_GATEWAY      = "wilson"

# Cached after the first successful discovery so every job in a session
# doesn't re-query the openapi endpoint.
_slurm_api_version: str | None = None


def discover_slurm_api_version() -> str:
    """Return the slurmrestd API version string (e.g. 'v0.0.41').

    Queries the /openapi/v3 endpoint, which lists every valid path including
    the current version prefix.  Falls back to _SLURM_REST_FALLBACK_VERSION
    if the endpoint is unreachable or the response is unparseable.
    """
    global _slurm_api_version
    if _slurm_api_version is not None:
        return _slurm_api_version

    url = f"{_SLURM_REST_BASE}/openapi/v3"
    try:
        if _requests is not None:
            data = _requests.get(url, timeout=10).json()
        else:
            import urllib.request
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())

        for path in data.get("paths", {}):
            m = re.match(r"^/slurm/(v\d+\.\d+\.\d+)/job/submit$", path)
            if m:
                _slurm_api_version = m.group(1)
                return _slurm_api_version
    except Exception:
        pass

    _slurm_api_version = _SLURM_REST_FALLBACK_VERSION
    return _slurm_api_version


def chmod_x(path: str) -> None:
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        raise RuntimeError(f"Could not make {path} executable: {e}") from e



def check_ssh_key_auth(gateway: str = _SLURM_GATEWAY) -> tuple[bool, str]:
    """Return (success, message) for passwordless SSH to the gateway node."""
    result = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            gateway, "exit",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    msg = (result.stderr.strip() or result.stdout.strip() or "unknown error")
    return False, msg


def setup_ssh_key(password: str, gateway: str = _SLURM_GATEWAY) -> str:
    """Copy the user's SSH public key to the gateway using a Qt-supplied password.

    The password is passed to ssh-copy-id via SSH_ASKPASS so it never appears
    on the terminal.  The temporary askpass script is deleted immediately after.

    Returns the combined stdout+stderr from ssh-copy-id for display in the log.
    Raises RuntimeError on failure or if no keys were installed.
    """
    config_dir = Path.home() / ".config" / "spread_gui"
    config_dir.mkdir(parents=True, exist_ok=True)
    askpass_path = str(config_dir / "_askpass.sh")

    # Write with 0o700 atomically so the file is never world-readable,
    # even for the brief moment between creation and chmod.
    script = f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(password)}\n"
    fd = os.open(askpass_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
    with os.fdopen(fd, "w") as fh:
        fh.write(script)

    try:
        env = os.environ.copy()
        env["SSH_ASKPASS"] = askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", ":0")

        result = subprocess.run(
            [
                "ssh-copy-id",
                "-o", "StrictHostKeyChecking=accept-new",
                gateway,
            ],
            capture_output=True,
            text=True,
            env=env,
            start_new_session=True,
            timeout=30,
        )
    finally:
        try:
            # Overwrite with zeros before deleting so the password is gone
            # even if deletion fails (e.g. after SIGKILL on a subsequent run).
            with open(askpass_path, "w") as fh:
                fh.write("\x00" * len(script))
            os.unlink(askpass_path)
        except Exception:
            pass

    combined = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )

    if result.returncode != 0:
        raise RuntimeError(combined or "ssh-copy-id exited with a non-zero status but produced no output.")

    # ssh-copy-id exits 0 even when authentication succeeded but it had nothing
    # to install (wrong password causes it to copy 0 keys).
    if "Number of key(s) added: 0" in combined:
        raise RuntimeError(
            f"ssh-copy-id connected to {gateway} but installed 0 keys "
            f"(wrong password, or all keys already present):\n{combined}"
        )

    return combined


def get_slurm_jwt(lifespan: int = 300) -> str:
    """SSH to the gateway node and retrieve a short-lived SLURM JWT token."""
    result = subprocess.run(
        [
            "ssh",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            _SLURM_GATEWAY,
            f"scontrol token lifespan={lifespan}",
        ],
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
    """Ensure the script has a bash shebang and sources the modules environment.

    The modules.sh source line must be inserted after the trailing block of
    #SBATCH directives, not immediately after the shebang — SLURM stops
    scanning for #SBATCH lines at the first non-comment line, so inserting
    it earlier would cause all #SBATCH directives to be silently ignored.
    """
    if not content.startswith("#!/bin/bash"):
        content = "#!/bin/bash\n" + content
    lines = content.splitlines()
    if not any(". /etc/profile.d/modules.sh" in line for line in lines):
        insert_at = 1
        while insert_at < len(lines) and lines[insert_at].startswith("#SBATCH"):
            insert_at += 1
        lines.insert(insert_at, ". /etc/profile.d/modules.sh")
    return "\n".join(lines)


def submit_job_via_rest_api(
    script_content: str,
    cwd: str,
    token: str,
    job_name: str = "i23_proc",
    dry_run: bool = False,
    cpus_per_task: int = 16,
    memory_per_node: str = "4G",
) -> Tuple[int, str, str]:
    """Submit a job script to SLURM via the Diamond Light Source REST API."""
    api_version = discover_slurm_api_version()
    rest_url = f"{_SLURM_REST_BASE}/slurm/{api_version}/job/submit"

    if dry_run:
        preview = "\n".join(script_content.splitlines()[:3])
        return 0, f"[DRY] Would POST to {rest_url}:\n{preview}\n…", ""

    script = _prepare_script(script_content)
    user = os.environ.get("USER", "")

    payload: dict = {
        "job": {
            "partition": "cs04r,cs05r",
            "tasks": 1,
            "name": job_name,
            "nodes": 1,
            "cpus_per_task": str(cpus_per_task),
            "memory_per_node": memory_per_node,
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
                rest_url, headers=headers, json=payload, timeout=30
            )
            return r.status_code, r.text, ""
        except Exception as exc:
            return 1, "", str(exc)

    # Fallback: urllib (no requests library)
    import urllib.request
    import urllib.error

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rest_url, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), ""
    except urllib.error.HTTPError as exc:
        return exc.code, "", exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 1, "", str(exc)
