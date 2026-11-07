"""Application setup."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from phabrillust.gui.fonts import load_fonts


def _resolve_style_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "styles" / "dark.qss"


def _resolve_icon_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "icons" / "logo.png"


def _set_windows_app_id() -> None:
    # Without its own AppUserModelID the taskbar groups us under python.exe
    # and shows Python's icon instead of the window icon.
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("anti-matrix.phabrillust")
    except Exception:
        pass


def create_app() -> QApplication:
    """Create and configure the QApplication."""
    app = QApplication.instance()
    if app is None:
        _set_windows_app_id()
        app = QApplication([])

    icon_path = _resolve_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    app.setStyle("Fusion")
    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    except Exception:
        pass
    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    load_fonts()

    # Default font.
    font = QFont("Space Grotesk", 11)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    # Load and apply stylesheet.
    style_path = _resolve_style_path()
    if style_path.exists():
        try:
            # QSS url() resolves against the CWD, so icons are referenced as
            # @STYLES@/name.svg and pointed at the styles folder here.
            qss = style_path.read_text(encoding="utf-8")
            app.setStyleSheet(qss.replace("@STYLES@", style_path.parent.as_posix()))
        except Exception:
            pass

    return app
