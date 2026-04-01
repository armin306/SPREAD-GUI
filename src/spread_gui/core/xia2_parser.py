from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# (overall, low-shell, high-shell)
Stats3 = Tuple[Optional[float], Optional[float], Optional[float]]


def _none3() -> Stats3:
    return (None, None, None)


def _strip_stddev(s: str) -> str:
    """Remove parenthetical std-dev suffixes like (4) in 89.2764(4)."""
    return re.sub(r'\(\d+\)', '', s)


def _extract_floats(s: str) -> list[float]:
    return [float(m) for m in re.findall(r'-?\d+\.?\d*(?:[eE][+-]?\d+)?', _strip_stddev(s))]


def _to3(nums: list[float]) -> Stats3:
    return (
        nums[0] if len(nums) > 0 else None,
        nums[1] if len(nums) > 1 else None,
        nums[2] if len(nums) > 2 else None,
    )


@dataclass
class Xia2Stats:
    high_res:          Stats3          = field(default_factory=_none3)
    completeness:      Stats3          = field(default_factory=_none3)
    multiplicity:      Stats3          = field(default_factory=_none3)
    i_over_sigma:      Stats3          = field(default_factory=_none3)
    rmerge_ipm:        Stats3          = field(default_factory=_none3)
    cc_half:           Stats3          = field(default_factory=_none3)
    wilson_b:          Optional[float] = None
    anom_completeness: Stats3          = field(default_factory=_none3)
    anom_multiplicity: Stats3          = field(default_factory=_none3)
    anom_slope:        Optional[float] = None
    cell_a:            Optional[float] = None
    cell_b:            Optional[float] = None
    cell_c:            Optional[float] = None
    cell_alpha:        Optional[float] = None
    cell_beta:         Optional[float] = None
    cell_gamma:        Optional[float] = None
    spacegroup:        Optional[str]   = None


# (label, attribute, is_scalar)
# Longer/more-specific labels must appear before any prefix they share.
_ROW_SPECS: list[tuple[str, str, bool]] = [
    ("High resolution limit",  "high_res",          False),
    ("Anomalous completeness", "anom_completeness",  False),
    ("Anomalous multiplicity", "anom_multiplicity",  False),
    ("Anomalous slope",        "anom_slope",         True),
    ("Completeness",           "completeness",       False),
    ("Multiplicity",           "multiplicity",       False),
    ("I/sigma",                "i_over_sigma",       False),
    ("Rmerge(I+/-)",           "rmerge_ipm",         False),
    ("CC half",                "cc_half",            False),
    ("Wilson B factor",        "wilson_b",           True),
]


def parse_xia2_txt(path: Path) -> Optional[Xia2Stats]:
    """
    Parse a xia2.txt file and return Xia2Stats.
    Returns None if the statistics table is not present (failed / incomplete run).
    """
    try:
        text = path.read_text(errors='replace')
    except OSError:
        return None

    # The table always starts with a "For <dataset>" header line.
    if not re.search(r'^\s*For\s+\S', text, re.MULTILINE):
        return None

    stats = Xia2Stats()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        matched = False
        for label, attr, is_scalar in _ROW_SPECS:
            # Match only if the label is followed by whitespace (not a longer label).
            if re.match(r'^' + re.escape(label) + r'\s', stripped):
                rest = stripped[len(label):]
                nums = _extract_floats(rest)
                if is_scalar:
                    setattr(stats, attr, nums[0] if nums else None)
                else:
                    setattr(stats, attr, _to3(nums))
                matched = True
                break

        if not matched:
            if stripped.startswith("Assuming spacegroup:"):
                stats.spacegroup = stripped.split(":", 1)[1].strip()
            elif re.match(r'^Unit cell\b', stripped):
                # The two lines after the header hold lengths and angles.
                if i + 2 < len(lines):
                    lengths = _extract_floats(lines[i + 1])
                    angles  = _extract_floats(lines[i + 2])
                    if len(lengths) >= 3:
                        stats.cell_a, stats.cell_b, stats.cell_c = lengths[:3]
                    if len(angles) >= 3:
                        stats.cell_alpha, stats.cell_beta, stats.cell_gamma = angles[:3]
                    i += 2

        i += 1

    return stats
