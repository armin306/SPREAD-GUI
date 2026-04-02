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


def detect_wedge_size_in_dir(data_dir: str):
    """Detect wedge size by finding the first mtime gap >60 s between consecutive
    frames in the primary sweep of the first available energy.

    Returns (wedge_size, diagnostic_str). wedge_size is 0 on failure.
    """
    from typing import Tuple as _Tuple

    def fail(msg):
        return 0, msg

    if not data_dir or not os.path.isdir(data_dir):
        return fail("data_dir not accessible")

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
    except OSError as exc:
        return fail(f"scandir error: {exc}")

    if not first_frame:
        return fail("no primary sweep first frame found")

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
    except OSError as exc:
        return fail(f"frame scan error: {exc}")

    if len(frames) < 2:
        return fail(f"too few frames ({len(frames)}) for {energy_str}_E{counter_str}")

    # Gather mtimes
    mtimes: List[float] = []
    for path, _ in frames:
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            mtimes.append(mtimes[-1] if mtimes else 0.0)

    # Find first gap >60 s
    wedge_size = 0
    max_gap = 0.0
    for i in range(1, len(mtimes)):
        gap = mtimes[i] - mtimes[i - 1]
        if gap > max_gap:
            max_gap = gap
        if gap > 60:
            wedge_size = frames[i - 1][1]  # last frame number before gap
            break

    if wedge_size == 0:
        return fail(f"no gap >60 s found in {len(frames)} frames (max gap={max_gap:.1f} s)")

    # Consistency check: verify the next expected boundary also has a gap >60 s.
    # The gap is AFTER the last frame of the wedge (frame wedge_size*2), i.e.
    # between index idx and idx+1.
    next_boundary = wedge_size * 2
    frame_nums = [f[1] for f in frames]
    if next_boundary in frame_nums:
        idx = frame_nums.index(next_boundary)
        if idx + 1 < len(mtimes):
            gap2 = mtimes[idx + 1] - mtimes[idx]
            if gap2 <= 60:
                return fail(
                    f"wedge={wedge_size} found but consistency check failed: "
                    f"gap at frame {next_boundary}→{next_boundary+1} is {gap2:.1f} s (expected >60 s)"
                )

    return wedge_size, f"wedge={wedge_size} frames, {len(frames)} total frames scanned"


def detect_num_sweeps_in_dir(data_dir: str) -> int:
    """Return the number of sweeps for the first energy found in data_dir.

    Counts the primary sweep (no suffix) plus any additional sweeps
    ({energy}_N_E{counter}_1_00001.cbf). Returns 1 if no additional sweeps
    are found or if the directory is inaccessible.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return 1

    first_pat = re.compile(r"^(\d{4,})_E(\d+)_1_00001\.cbf$", re.IGNORECASE)
    energy_str = counter_str = ""
    try:
        for entry in sorted(os.scandir(data_dir), key=lambda e: e.name):
            if entry.is_file():
                m = first_pat.match(entry.name)
                if m:
                    energy_str, counter_str = m.group(1), m.group(2)
                    break
    except OSError:
        return 1

    if not energy_str:
        return 1

    extra_pat = re.compile(
        rf"^{re.escape(energy_str)}_\d+_E{re.escape(counter_str)}_1_00001\.cbf$",
        re.IGNORECASE,
    )
    count = 1  # primary sweep
    try:
        for entry in os.scandir(data_dir):
            if entry.is_file() and extra_pat.match(entry.name):
                count += 1
    except OSError:
        pass
    return count


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
