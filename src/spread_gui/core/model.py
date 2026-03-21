from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Cell:
    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float

    def as_autoproc_string(self) -> str:
        return f"{self.a:.4f} {self.b:.4f} {self.c:.4f} {self.alpha:.2f} {self.beta:.2f} {self.gamma:.2f}"
