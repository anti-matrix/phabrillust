"""Font loading helpers."""

from pathlib import Path

from PySide6.QtGui import QFontDatabase


def load_fonts() -> list[str]:
    """Load every .ttf under assets/fonts and return the loaded family names."""
    families: list[str] = []
    fonts_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"
    if not fonts_dir.exists():
        return families

    for ttf in fonts_dir.glob("*.ttf"):
        try:
            fid = QFontDatabase.addApplicationFont(str(ttf))
            if fid != -1:
                families.extend(QFontDatabase.applicationFontFamilies(fid))
        except Exception:
            continue

    return families
