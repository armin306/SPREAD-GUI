from __future__ import annotations
import os
import re
from typing import List

_PAT = re.compile(r"^(?P<energy>\d{4,})(?:_\d+)?_E\d+_1_", re.IGNORECASE)

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

def detect_sweeps_for_energy(data_dir: str, energy: int, counter: int) -> List[int]:
    """Return sorted sweep numbers for one energy/counter pair.
    0 = primary (no sweep suffix), N = _N_ sweep (e.g. 2 for _2_).
    Falls back to [0] when data_dir is inaccessible so callers always
    get at least one sweep to submit.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return [0]
    sweeps: List[int] = []
    if os.path.isfile(os.path.join(data_dir, f"{energy}_E{counter}_1_00001.cbf")):
        sweeps.append(0)
    pat = re.compile(
        rf"^{re.escape(str(energy))}_(\d+)_E{counter}_1_00001\.cbf$",
        re.IGNORECASE,
    )
    try:
        for entry in os.scandir(data_dir):
            if entry.is_file():
                m = pat.match(entry.name)
                if m:
                    sweeps.append(int(m.group(1)))
    except OSError:
        pass
    return sorted(sweeps) if sweeps else [0]


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
