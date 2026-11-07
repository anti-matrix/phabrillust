"""Base effect dataclasses and abstract class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image


@dataclass
class ParamDef:
    """Parameter definition for an effect panel."""

    name: str
    label: str
    type: str  # one of: int, float, bool, choice
    default: Any
    min: Any = None
    max: Any = None
    step: Any = None
    choices: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type not in {"int", "float", "bool", "choice"}:
            raise ValueError(f"Invalid param type: {self.type}")


class Effect(ABC):
    """Base class for all image effects."""

    name: str
    id: str
    category: str  # glitch, noise, or databend
    params: list[ParamDef]
    description: str = ""  # one line, shown as a tooltip

    @abstractmethod
    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        """Apply the effect and return a new RGB PIL image."""
        ...

    def param_values(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return params with defaults filled in for missing keys."""
        values: dict[str, Any] = {}
        for p in self.params:
            values[p.name] = params.get(p.name, p.default)
        return values
