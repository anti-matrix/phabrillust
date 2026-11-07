"""Qt conversion helpers for PIL images."""

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap


def pil_to_qimage(image: Image.Image) -> QImage:
    """Convert a PIL RGB/RGBA image to QImage."""
    rgb = image.convert("RGBA")
    data = rgb.tobytes("raw", "RGBA")
    return QImage(data, rgb.width, rgb.height, rgb.width * 4, QImage.Format.Format_RGBA8888).copy()


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    """Convert a PIL image to QPixmap."""
    return QPixmap.fromImage(pil_to_qimage(image))
