from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# (overall, inner-shell, outer-shell)
Stats3 = Tuple[Optional[float], Optional[float], Optional[float]]

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _none3() -> Stats3:
    return (None, None, None)


def _extract_floats(s: str) -> list[float]:
    return [float(m) for m in re.findall(r'-?\d+\.?\d*(?:[eE][+-]?\d+)?', s)]


def _to3(nums: list[float]) -> Stats3:
    return (
        nums[0] if len(nums) > 0 else None,
        nums[1] if len(nums) > 1 else None,
        nums[2] if len(nums) > 2 else None,
    )


@dataclass
class AutoprocStats:
    # STARANISO final summary block (overall, inner, outer)
    high_res:                Stats3          = field(default_factory=_none3)
    completeness_spher:      Stats3          = field(default_factory=_none3)
    completeness_ellip:      Stats3          = field(default_factory=_none3)
    multiplicity:            Stats3          = field(default_factory=_none3)
    i_over_sigma:            Stats3          = field(default_factory=_none3)
    rmerge:                  Stats3          = field(default_factory=_none3)
    rmeas:                   Stats3          = field(default_factory=_none3)
    rpim:                    Stats3          = field(default_factory=_none3)
    cc_half:                 Stats3          = field(default_factory=_none3)
    anom_completeness_spher: Stats3          = field(default_factory=_none3)
    anom_completeness_ellip: Stats3          = field(default_factory=_none3)
    anom_multiplicity:       Stats3          = field(default_factory=_none3)
    cc_ano:                  Stats3          = field(default_factory=_none3)
    sig_ano:                 Stats3          = field(default_factory=_none3)
    # Diffraction limits along the 3 principal ellipsoid axes (labelled a*, b*, c*)
    diff_limit_astar:        Optional[float] = None
    diff_limit_bstar:        Optional[float] = None
    diff_limit_cstar:        Optional[float] = None
    # Crystal metadata
    cell_a:                  Optional[float] = None
    cell_b:                  Optional[float] = None
    cell_c:                  Optional[float] = None
    cell_alpha:              Optional[float] = None
    cell_beta:               Optional[float] = None
    cell_gamma:              Optional[float] = None
    spacegroup:              Optional[str]   = None
    wavelength:              Optional[float] = None


# Row specs for the summary stats block.
# Longer/more-specific prefixes must come before any prefix they share.
_ROW_SPECS: list[tuple[str, str]] = [
    ("High resolution limit",                "high_res"),
    ("Anomalous completeness (ellipsoidal)", "anom_completeness_ellip"),
    ("Anomalous completeness (spherical)",   "anom_completeness_spher"),
    ("Anomalous multiplicity",               "anom_multiplicity"),
    ("Completeness (ellipsoidal)",           "completeness_ellip"),
    ("Completeness (spherical)",             "completeness_spher"),
    ("Multiplicity",                         "multiplicity"),
    ("Mean(I)/sd(I)",                        "i_over_sigma"),
    ("Rmerge  (all I+ & I-)",                "rmerge"),
    ("Rmeas   (all I+ & I-)",                "rmeas"),
    ("Rpim    (all I+ & I-)",                "rpim"),
    ("CC(1/2)",                              "cc_half"),
    ("CC(ano)",                              "cc_ano"),
    ("|DANO|/sd(DANO)",                      "sig_ano"),
]


def parse_autoproc_log(path: Path) -> Optional[AutoprocStats]:
    """
    Parse an aP.log file and return AutoprocStats using the final STARANISO block.
    Returns None if that block is absent (failed / incomplete run).
    """
    try:
        raw = path.read_text(errors='replace')
    except OSError:
        return None

    text = _ANSI_RE.sub('', raw)
    lines = text.splitlines()
    n = len(lines)

    # ----------------------------------------------------------------
    # Locate the final STARANISO "observations" summary block.
    # There are three stats blocks; the third is introduced by:
    #   "NOTE : the statistics below are for all observations up to"
    # (uses "observations", not "measurements", unlike the other two).
    # ----------------------------------------------------------------
    staraniso_start = -1
    for i, line in enumerate(lines):
        if 'statistics below are for all observations up to' in line:
            staraniso_start = i

    if staraniso_start == -1:
        return None

    # Find "Overall  InnerShell  OuterShell" header within the next ~50 lines.
    table_start = -1
    for i in range(staraniso_start, min(staraniso_start + 50, n)):
        if 'Overall' in lines[i] and 'InnerShell' in lines[i] and 'OuterShell' in lines[i]:
            table_start = i + 2  # skip header + dashed separator
            break

    if table_start == -1:
        return None

    stats = AutoprocStats()

    # Parse summary key-value rows (up to 30 lines; stop at per-shell table).
    for i in range(table_start, min(table_start + 30, n)):
        line = lines[i].strip()
        if not line or line.startswith('-'):
            continue
        # The per-shell detail table starts with "Resolution"
        if line.startswith('Resolution'):
            break
        for label, attr in _ROW_SPECS:
            if line.startswith(label):
                nums = _extract_floats(line[len(label):])
                setattr(stats, attr, _to3(nums))
                break

    # ----------------------------------------------------------------
    # Cell parameters, spacegroup, wavelength — take last occurrence.
    # These appear after each stats block; values are equivalent.
    # ----------------------------------------------------------------
    for line in lines:
        s = line.strip()
        if s.startswith('Spacegroup name'):
            parts = s.split(None, 2)
            if len(parts) >= 3:
                stats.spacegroup = parts[2].strip()
        elif s.startswith('Unit cell parameters'):
            nums = _extract_floats(s)
            if len(nums) >= 6:
                stats.cell_a     = nums[0]
                stats.cell_b     = nums[1]
                stats.cell_c     = nums[2]
                stats.cell_alpha = nums[3]
                stats.cell_beta  = nums[4]
                stats.cell_gamma = nums[5]
        elif s.startswith('Wavelength') and ('A' in s or 'Å' in s):
            nums = _extract_floats(s)
            if nums:
                stats.wavelength = nums[0]

    # ----------------------------------------------------------------
    # Diffraction limits along the three principal ellipsoid axes.
    # Section header: "Diffraction limits & principal axes of ellipsoid..."
    # Followed by exactly 3 data lines; the first number is the limit in Å.
    # We label them a*, b*, c* by position regardless of the axis direction,
    # as requested.
    # ----------------------------------------------------------------
    _diff_attrs = ('diff_limit_astar', 'diff_limit_bstar', 'diff_limit_cstar')
    in_diff = False
    diff_idx = 0
    for line in lines:
        s = line.strip()
        if 'Diffraction limits & principal axes' in s:
            in_diff = True
            diff_idx = 0
            continue
        if in_diff:
            if not s:
                in_diff = False
                continue
            if diff_idx >= 3:
                in_diff = False
                continue
            nums = _extract_floats(s)
            if nums:
                setattr(stats, _diff_attrs[diff_idx], nums[0])
                diff_idx += 1

    return stats
