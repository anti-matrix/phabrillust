"""PIL-only helpers for injecting the phabrillust signature into exported images."""

import os
import tempfile
from datetime import datetime

from PIL import Image
from PIL.ExifTags import Base, IFD
from PIL.PngImagePlugin import PngInfo

SIGNATURE = "this image was created using phabrillust by @anti-matrix"
_AUTHOR = "phabrillust"
_SOFTWARE = "phabrillust"
_USER_COMMENT_PREFIX = b"ASCII\0\0\0"


def pnginfo_with_signature(created_at: datetime | None = None) -> PngInfo:
    """Build a PngInfo container with readable tEXt metadata before IDAT."""
    if created_at is None:
        created_at = datetime.now()
    pnginfo = PngInfo()
    pnginfo.add_text("Title", SIGNATURE, zip=False)
    pnginfo.add_text("Author", _AUTHOR, zip=False)
    pnginfo.add_text("Copyright", SIGNATURE, zip=False)
    pnginfo.add_text("Comment", SIGNATURE, zip=False)
    pnginfo.add_text("Software", _SOFTWARE, zip=False)
    pnginfo.add_text(
        "Creation Time", created_at.strftime("%Y-%m-%dT%H:%M:%S"), zip=False
    )
    return pnginfo


def _build_exif(created_at: datetime) -> Image.Exif:
    """Build a shared EXIF object (no GPS) for JPEG and PNG exports."""
    timestamp = created_at.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()

    exif[Base.ImageDescription] = SIGNATURE
    exif[Base.Artist] = _AUTHOR
    exif[Base.Copyright] = SIGNATURE
    exif[Base.Software] = _SOFTWARE
    exif[Base.DateTime] = timestamp

    exif[Base.XPTitle] = SIGNATURE.encode("utf-16-le") + b"\0\0"
    exif[Base.XPComment] = SIGNATURE.encode("utf-16-le") + b"\0\0"
    exif[Base.XPAuthor] = _AUTHOR.encode("utf-16-le") + b"\0\0"

    exif[IFD.Exif] = {
        Base.UserComment: _USER_COMMENT_PREFIX + SIGNATURE.encode("ascii"),
        Base.DateTimeOriginal: timestamp,
        Base.DateTimeDigitized: timestamp,
    }

    return exif


def jpeg_exif_bytes(created_at: datetime | None = None) -> bytes:
    """Return EXIF bytes (no GPS) for image.save(..., exif=...)."""
    if created_at is None:
        created_at = datetime.now()
    return _build_exif(created_at).tobytes()


def inject_jpeg_comment(path: str) -> None:
    """Insert a COM segment (FF FE + 2-byte BE length + ASCII SIGNATURE) right after JPEG SOI."""
    with open(path, "rb") as fp:
        data = bytearray(fp.read())

    if data[:2] != b"\xff\xd8":
        raise ValueError(f"Missing JPEG SOI marker at start of {path}")

    payload = SIGNATURE.encode("ascii")
    length = 2 + len(payload)
    com_segment = b"\xff\xfe" + length.to_bytes(2, "big") + payload

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "wb") as fp:
            fp.write(data[:2])
            fp.write(com_segment)
            fp.write(data[2:])
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
