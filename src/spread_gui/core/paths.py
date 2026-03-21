from __future__ import annotations
import os
import re
from typing import Optional

VISIT_RE = re.compile(r"^[a-z]{2}[0-9]{5}-[0-9]{1,3}$")

def detect_visit_from_path(path: str) -> Optional[str]:
    parts = os.path.abspath(path).split(os.sep)
    for p in reversed(parts):
        if VISIT_RE.match(p):
            return p
    return None

def infer_visit_root(cwd: str, visit: str) -> Optional[str]:
    parts = os.path.abspath(cwd).split(os.sep)
    if visit not in parts:
        return None
    idx = parts.index(visit)
    if parts and parts[0] == "":
        return os.sep + os.path.join(*parts[1:idx + 1])
    return os.path.join(*parts[:idx + 1])
