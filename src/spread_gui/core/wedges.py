from __future__ import annotations
from typing import List

def compute_wedges(wedge_size: int, total_images: int) -> List[int]:
    if wedge_size <= 0 or total_images <= 0:
        return []
    return list(range(wedge_size, total_images + 1, wedge_size))
