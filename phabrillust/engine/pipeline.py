"""Pipeline that orders and applies effects."""

from typing import Any

from PIL import Image

from phabrillust.effects.base import Effect
from phabrillust.effects.glitch import (
    BlockDisplace,
    ChannelShift,
    GlitchSlice,
    PixelSort,
    ScanlineTear,
)
from phabrillust.effects.noise import (
    FilmGrain,
    GaussianNoise,
    SaltPepper,
    ValueNoise,
)
from phabrillust.engine.databend import DATABEND_EFFECTS
from phabrillust.engine.selection import Selection
from phabrillust.utils.rng import get_rng

GLITCH_EFFECTS: list[Effect] = [
    ChannelShift(),
    ScanlineTear(),
    PixelSort(),
    BlockDisplace(),
    GlitchSlice(),
]

NOISE_EFFECTS: list[Effect] = [
    GaussianNoise(),
    SaltPepper(),
    ValueNoise(),
    FilmGrain(),
]


def apply_pipeline(
    source: Image.Image,
    enabled_effects: list[tuple[Effect, dict[str, Any]]],
    global_seed: int,
    preview: bool = True,
    max_preview_side: int = 1200,
    selection: Selection | None = None,
) -> tuple[Image.Image, list[str]]:
    """Apply enabled effects in fixed order: glitch -> noise -> databend.

    Each effect receives a per-effect RNG derived from ``global_seed`` and
    ``effect.id``. Exceptions are caught, a warning is recorded, and the
    previous image is carried forward.

    If ``preview`` is True the returned image is down-scaled to
    ``max_preview_side`` using LANCZOS.

    With a ``selection``, effects run on the selection's bounding-box crop
    only (so e.g. databending corrupts that region's own bytes) and the
    result is pasted back through the selection mask.
    """
    warnings: list[str] = []

    # Reorder by category.
    by_category: dict[str, list[tuple[Effect, dict[str, Any]]]] = {
        "glitch": [],
        "noise": [],
        "databend": [],
    }
    for effect, params in enabled_effects:
        by_category.setdefault(effect.category, []).append((effect, params))

    ordered = by_category["glitch"] + by_category["noise"] + by_category["databend"]
    image = source.convert("RGB")
    if preview:
        from phabrillust.engine.renderer import make_preview

        image = make_preview(image, max_preview_side)

    if selection is None or not ordered:
        return _run_effects(image, ordered, global_seed, warnings), warnings

    box = selection.bbox(image.size)
    if box is None:
        return image, warnings
    region = image.crop(box)
    worked = _run_effects(region, ordered, global_seed, warnings)
    if worked.size != region.size:
        # A databend that corrupted dimensions; keep the region's footprint.
        worked = worked.resize(region.size, Image.NEAREST)
    result = image.copy()
    result.paste(worked, box[:2], selection.mask(image.size).crop(box))
    return result, warnings


def _run_effects(
    image: Image.Image,
    ordered: list[tuple[Effect, dict[str, Any]]],
    global_seed: int,
    warnings: list[str],
) -> Image.Image:
    for effect, params in ordered:
        try:
            rng = get_rng(global_seed, salt=effect.id)
            image = effect.apply(image, params, rng)
        except Exception as exc:
            warnings.append(f"{effect.name} failed: {exc}")
    return image
