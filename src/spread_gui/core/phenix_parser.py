"""
Parse phenix.refine log files and collect results across an energy/wedge grid.

Expected directory layout:
    proc_path/
        {energy}eV/
            {images}img/
                {PIPELINE_DIR_PREFIX[pipeline]}_{run}/
                    {energy}eV_{images}img_refine_001.log
"""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Canonical mapping: pipeline name → output directory prefix.
# Used by both the submission (spread_tab.py) and analysis sides.
PIPELINE_DIR_PREFIX: dict[str, str] = {
    "xia2-dials": "phenix_dials",
    "xia2-3dii":  "phenix_3dii",
    "autoPROC":   "phenix_autoproc",
}


@dataclass
class PhenixGroup:
    selection: str
    f_prime: float
    f_double_prime: float


@dataclass
class PhenixResult:
    energy_ev: int
    images: int
    r_work_final: Optional[float]
    r_free_final: Optional[float]
    groups: list[PhenixGroup] = field(default_factory=list)


def group_short_label(selection: str) -> str:
    """Turn 'chain A and resid 401 and name CA' → 'A-401-CA'."""
    chain = re.search(r'chain\s+(\S+)', selection)
    resid = re.search(r'resid\s+(\d+)', selection)
    atom  = re.search(r'name\s+(\S+)', selection)
    parts = []
    if chain: parts.append(chain.group(1))
    if resid: parts.append(resid.group(1))
    if atom:  parts.append(atom.group(1))
    return '-'.join(parts) if parts else selection[:20]


def parse_phenix_log(path: Path) -> Optional[PhenixResult]:
    """
    Parse one phenix.refine log file.

    Returns a PhenixResult with the *final* f'/f'' values (last macro-cycle),
    or None if the file cannot be parsed or contains no anomalous groups.
    """
    try:
        text = path.read_text(errors='replace')
    except Exception:
        return None

    # Energy and images come from the directory structure:
    # .../{energy}eV/{images}img/{dir_prefix}_{run}/{logfile}
    p = path.parts
    try:
        energy = int(p[-4][:-2])   # "9661eV" → 9661
        images = int(p[-3][:-3])   # "2400img" → 2400
    except (ValueError, IndexError):
        energy = images = 0

    # Final R-values (only present when refinement completed successfully)
    r_work = r_free = None
    m = re.search(r'Final R-work\s*=\s*([\d.]+),\s*R-free\s*=\s*([\d.]+)', text)
    if m:
        r_work = float(m.group(1))
        r_free = float(m.group(2))

    # Parse every "Anomalous scatterer group:" block in the file.
    # The initial setup block has f_prime=0; each macro-cycle appends one set.
    # We want the last complete set (= last macro-cycle).
    all_groups: list[PhenixGroup] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == "Anomalous scatterer group:":
            selection = f_prime = f_double_prime = None
            for j in range(i + 1, min(i + 8, len(lines))):
                l = lines[j].strip()
                if l.startswith('Selection:'):
                    sm = re.match(r'Selection:\s*"([^"]+)"', l)
                    if sm:
                        selection = sm.group(1)
                elif l.startswith('f_prime:'):
                    try:
                        f_prime = float(l.split(':', 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif l.startswith('f_double_prime:'):
                    try:
                        f_double_prime = float(l.split(':', 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
            if selection is not None and f_prime is not None and f_double_prime is not None:
                all_groups.append(PhenixGroup(selection, f_prime, f_double_prime))
        i += 1

    if not all_groups:
        return None

    # Determine the number of unique groups from first-occurrence order.
    seen: dict[str, int] = {}
    for g in all_groups:
        if g.selection not in seen:
            seen[g.selection] = len(seen)
    n_groups = len(seen)

    # The last n_groups blocks are the final macro-cycle values.
    final_groups = all_groups[-n_groups:]

    return PhenixResult(
        energy_ev=energy,
        images=images,
        r_work_final=r_work,
        r_free_final=r_free,
        groups=final_groups,
    )


def collect_results(
    proc_path: Path,
    pipeline: str,
    run: int,
) -> dict[tuple[int, int], PhenixResult]:
    """
    Scan *proc_path* for completed phenix.refine logs.

    Returns a dict keyed by (energy_eV, images) for the given pipeline and
    run number.  Only entries with at least one anomalous group are included.
    """
    dir_prefix = PIPELINE_DIR_PREFIX[pipeline]
    pattern = str(
        proc_path / "*eV" / "*img" / f"{dir_prefix}_{run}" / "*_refine_001.log"
    )
    results: dict[tuple[int, int], PhenixResult] = {}
    for log_path in sorted(glob.glob(pattern)):
        result = parse_phenix_log(Path(log_path))
        if result is not None and result.groups:
            results[(result.energy_ev, result.images)] = result
    return results
