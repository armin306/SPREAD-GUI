from __future__ import annotations
from typing import List, Tuple
from .model import Cell

try:
    import gemmi  # type: ignore
except Exception:
    gemmi = None

def all_spacegroups_230() -> List[str]:
    if gemmi is not None:
        out: List[str] = []
        for i in range(1, 231):
            try:
                if hasattr(gemmi, "find_spacegroup_by_number"):
                    sg = gemmi.find_spacegroup_by_number(i)
                else:
                    sg = gemmi.SpaceGroup(i)
                out.append(sg.hm)
            except Exception:
                out.append(str(i))
        seen = set()
        dedup = []
        for s in out:
            if s not in seen:
                dedup.append(s)
                seen.add(s)
        return dedup[:230]
    return ["P 1", "P -1", "P 21 21 21", "C 2", "P 2 2 2", "P 4", "I 4", "R 3", "P 6", "P 23", "F 4 3 2"]

def cell_is_compatible_with_sg(cell: Cell, sg_name: str) -> Tuple[bool, str]:
    if gemmi is None:
        return True, "gemmi not available – compatibility check disabled."
    try:
        if hasattr(gemmi, "find_spacegroup_by_name"):
            sg = gemmi.find_spacegroup_by_name(sg_name)
        else:
            sg = gemmi.SpaceGroup(sg_name)
        uc = gemmi.UnitCell(cell.a, cell.b, cell.c, cell.alpha, cell.beta, cell.gamma)
        if hasattr(uc, "is_compatible_with_spacegroup"):
            ok = bool(uc.is_compatible_with_spacegroup(sg))
            return ok, ("Unit cell is compatible with selected space group." if ok
                        else "Unit cell NOT compatible with selected space group.")
        return True, "Compatibility API not available in this gemmi build."
    except Exception as e:
        return False, f"Compatibility check failed: {e}"
