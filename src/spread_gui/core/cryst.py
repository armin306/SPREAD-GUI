from __future__ import annotations
import re
from typing import Optional, Tuple
from .model import Cell

def normalize_sg_name(s: str) -> str:
    s = s.strip().replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    return s

def parse_pdb_cryst1(pdb_path: str) -> Tuple[Optional[Cell], Optional[str]]:
    cell = None
    sg = None
    try:
        with open(pdb_path, "rt", errors="replace") as fh:
            for line in fh:
                if line.startswith("CRYST1"):
                    a = float(line[6:15].strip())
                    b = float(line[15:24].strip())
                    c = float(line[24:33].strip())
                    alpha = float(line[33:40].strip())
                    beta = float(line[40:47].strip())
                    gamma = float(line[47:54].strip())
                    sg_field = line[55:66].strip()
                    cell = Cell(a, b, c, alpha, beta, gamma)
                    sg = sg_field if sg_field else None
                    break
    except Exception:
        return None, None
    return cell, sg
