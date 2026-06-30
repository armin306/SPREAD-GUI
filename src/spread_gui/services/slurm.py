from __future__ import annotations

import json
import os
import re
import stat
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

# Only cache successful discoveries — fallback is never cached so each
# submission retries the openapi endpoint in case it becomes reachable.
_slurm_api_version: str | None = None


def discover_slurm_api_version(token: str = "", user: str = "") -> tuple[str, str]:
    """Return (api_version, log_message) for the slurmrestd instance.

    First tries an unauthenticated GET /openapi/v3.  If that fails or returns
    no usable paths, retries with JWT auth headers (some DLS configurations
    require them).  Falls back to _SLURM_REST_FALLBACK_VERSION on complete
    failure and does NOT cache that fallback so the next call retries.
    """
    global _slurm_api_version
    if _slurm_api_version is not None:
        return _slurm_api_version, f"SLURM REST API version (cached): {_slurm_api_version}"

    url = f"{_SLURM_REST_BASE}/openapi/v3"
    auth_headers = {}
    if token and user:
        auth_headers = {"X-SLURM-USER-NAME": user, "X-SLURM-USER-TOKEN": token}

    last_error = ""
    for attempt, headers in enumerate([{}, auth_headers]):
        if attempt == 1 and not auth_headers:
            break
        try:
            if _requests is not None:
                r = _requests.get(url, headers=headers, timeout=10, verify=True)
                if r.status_code != 200:
                    last_error = f"GET {url} returned HTTP {r.status_code}"
                    continue
                data = r.json()
            else:
                import urllib.request
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())

            for path in data.get("paths", {}):
                m = re.match(r"^/slurm/(v\d+\.\d+\.\d+)/job/submit$", path)
                if m:
                    _slurm_api_version = m.group(1)
                    label = "authenticated" if headers else "unauthenticated"
                    return _slurm_api_version, (
                        f"Discovered SLURM REST API version via {label} "
                        f"openapi query: {_slurm_api_version}"
                    )
            last_error = f"GET {url} succeeded but no /slurm/vX.Y.Z/job/submit path found in response"
        except Exception as exc:
            last_error = f"GET {url}: {exc}"

    return _SLURM_REST_FALLBACK_VERSION, (
        f"WARNING: SLURM REST API version discovery failed ({last_error}); "
        f"using fallback {_SLURM_REST_FALLBACK_VERSION}"
    )


def chmod_x(path: str) -> None:
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        raise RuntimeError(f"Could not make {path} executable: {e}") from e



def check_ssh_key_auth(gateway: str = _SLURM_GATEWAY) -> tuple[bool, str]:
    """Return (success, verbose_log) for passwordless SSH to the gateway node.

    Always uses -v so the caller can log the full negotiation output and
    see exactly why publickey auth succeeded or failed.
    """
    result = subprocess.run(
        [
            "ssh",
            "-v",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            gateway, "exit",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    log = result.stderr.strip() or result.stdout.strip() or "no output"
    return result.returncode == 0, log


def setup_ssh_key(gateway: str = _SLURM_GATEWAY) -> str:
    """Copy the local SSH public key to the gateway.

    Runs ssh-copy-id without detaching from the controlling terminal, so ssh
    can prompt for the password on the console — the same mechanism used by
    get_slurm_jwt().  stdout/stderr are captured for logging and error
    detection; the password prompt goes through /dev/tty directly and is
    unaffected by the capture.

    Returns the combined stdout+stderr for display in the GUI log.
    Raises RuntimeError on failure or if no keys were installed.
    """
    result = subprocess.run(
        [
            "ssh-copy-id",
            "-o", "StrictHostKeyChecking=accept-new",
            gateway,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    combined = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    )

    if result.returncode != 0:
        raise RuntimeError(combined or "ssh-copy-id exited non-zero but produced no output.")

    if "Number of key(s) added: 0" in combined:
        raise RuntimeError(
            f"ssh-copy-id connected to {gateway} but installed 0 keys "
            f"(wrong password, or key already present but auth still fails):\n{combined}"
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
    """Submit a job script to SLURM via the Diamond Light Source REST API.

    Returns (status_code, stdout_or_body, stderr_or_discovery_log).
    The third element carries the API version discovery log so callers
    can emit it as a diagnostic message on the first job submission.
    """
    user = os.environ.get("USER", "")
    api_version, discovery_log = discover_slurm_api_version(token=token, user=user)
    rest_url = f"{_SLURM_REST_BASE}/slurm/{api_version}/job/submit"

    if dry_run:
        preview = "\n".join(script_content.splitlines()[:3])
        return 0, f"[DRY] Would POST to {rest_url}:\n{preview}\n…", discovery_log

    script = _prepare_script(script_content)

    payload: dict = {
        "job": {
            "partition": "cs04r,cs05r",
            "tasks": 1,
            "name": job_name,
            "nodes": "1",
            "cpus_per_task": cpus_per_task,
            "memory_per_node": memory_per_node,
            "current_working_directory": cwd,
            "environment": [
                f"USER={user}",
                (
                    "PATH=/usr/share/Modules/bin:/usr/local/bin:/usr/local/sbin"
                    f":/usr/bin:/usr/sbin:/var/cfengine/bin:/home/{user}/bin"
                ),
            ],
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
