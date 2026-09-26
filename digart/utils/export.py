"""Export helpers (Qt-free)."""

from __future__ import annotations

_PNG_EXT = ".png"
_JPEG_EXT = ".jpg"
_ALLOWED_EXTS = frozenset({_PNG_EXT, _JPEG_EXT})


def default_export_name(source_name: str | None, seed: int, ext: str) -> str:
    """Return '<stem>_phabrillust_<seed><ext>'."""
    if ext not in _ALLOWED_EXTS:
        raise ValueError(f"unsupported export extension {ext!r}")
    stem = source_name or "image"
    return f"{stem}_phabrillust_{seed}{ext}"
