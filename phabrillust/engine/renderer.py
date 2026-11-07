"""PIL image rendering helpers (no Qt)."""

from PIL import Image


def make_preview(image: Image.Image, max_side: int = 1200) -> Image.Image:
    """Return a down-scaled RGB copy for preview, preserving aspect ratio."""
    w, h = image.size
    if max(w, h) <= max_side:
        return image
    scale = max_side / max(w, h)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)
