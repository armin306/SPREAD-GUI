from __future__ import annotations
import os
import re
from typing import List

_PAT = re.compile(r"(?P<energy>\d+(?:\.\d+)?)_E\d+_1_", re.IGNORECASE)

def compute_energy_list(range_mode: bool, start: float, end: float, inc: float, list_str: str) -> List[int]:
    energies: List[int] = []
    if range_mode:
        if inc <= 0:
            return []
        x = start
        steps = 0
        while x <= end + 1e-9 and steps < 100000:
            energies.append(int(round(x)))
            x += inc
            steps += 1
    else:
        for tok in re.split(r"[,\\s]+", list_str.strip()):
            if not tok:
                continue
            try:
                energies.append(int(round(float(tok))))
            except ValueError:
                pass
    return sorted(set(energies))

def detect_energies_in_dir(data_dir: str) -> List[int]:
    if not data_dir or not os.path.isdir(data_dir):
        return []
    energies = set()
    try:
        with os.scandir(data_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name
                if "_E" not in name:
                    continue
                m = _PAT.search(name)
                if not m:
                    continue
                try:
                    energies.add(int(round(float(m.group("energy")))))
                except ValueError:
                    pass
    except Exception:
        return []
    return sorted(energies)
