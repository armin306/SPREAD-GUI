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


def detect_wedge_size_in_dir(data_dir: str) -> int:
    """Detect wedge size by finding the first mtime gap >60 s between consecutive
    frames in the primary sweep of the first available energy.

    Returns 0 if detection fails or the data directory is not accessible.

    Algorithm:
    - Find the first primary sweep (no sweep-number suffix) in data_dir.
    - Walk frames in order; record mtime of each.
    - The first consecutive gap >60 s defines the wedge boundary.
    - Verify consistency: at least one further gap must occur at the same
      interval (±1 frame), guarding against a one-off network hiccup.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return 0

    # Find any primary sweep first frame: {energy}_E{counter}_1_00001.cbf
    first_pat = re.compile(r"^(\d{4,})_E(\d+)_1_00001\.cbf$", re.IGNORECASE)
    first_frame: str = ""
    energy_str = counter_str = ""
    try:
        for entry in sorted(os.scandir(data_dir), key=lambda e: e.name):
            if entry.is_file():
                m = first_pat.match(entry.name)
                if m:
                    first_frame = entry.path
                    energy_str, counter_str = m.group(1), m.group(2)
                    break
    except OSError:
        return 0

    if not first_frame:
        return 0

    # Collect all frames for this sweep in order
    frame_pat = re.compile(
        rf"^{re.escape(energy_str)}_E{re.escape(counter_str)}_1_(\d{{5}})\.cbf$",
        re.IGNORECASE,
    )
    try:
        raw = []
        for entry in os.scandir(data_dir):
            if entry.is_file():
                m = frame_pat.match(entry.name)
                if m:
                    raw.append((entry.path, int(m.group(1))))
        frames = sorted(raw, key=lambda x: x[1])
    except OSError:
        return 0

    if len(frames) < 2:
        return 0

    # Gather mtimes
    mtimes: List[float] = []
    for path, _ in frames:
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            mtimes.append(mtimes[-1] if mtimes else 0.0)

    # Find first gap >60 s
    wedge_size = 0
    for i in range(1, len(mtimes)):
        if mtimes[i] - mtimes[i - 1] > 60:
            wedge_size = frames[i - 1][1]  # last frame number before gap
            break

    if wedge_size == 0:
        return 0

    # Consistency check: verify the next expected boundary also has a gap >60 s
    next_boundary = wedge_size * 2
    frame_nums = [f[1] for f in frames]
    if next_boundary in frame_nums:
        idx = frame_nums.index(next_boundary)
        if mtimes[idx] - mtimes[idx - 1] <= 60:
            return 0  # No matching gap at second boundary — unreliable

    return wedge_size


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
