"""PIL-only helpers for injecting the phabrillust signature into exported images."""

import os
import re
import tempfile
from datetime import datetime
from xml.sax.saxutils import escape

from PIL import Image
from PIL.ExifTags import Base, IFD
from PIL.PngImagePlugin import PngInfo

SIGNATURE = "this image was created using phabrillust by @anti-matrix"
_AUTHOR = "phabrillust"
_SOFTWARE = "phabrillust"
_USER_COMMENT_PREFIX = b"ASCII\0\0\0"
_SEED_RE = re.compile(re.escape(SIGNATURE.encode("ascii")) + rb" \(seed (\d+)\)")


def signature_text(seed: int | None = None) -> str:
    """The inscription, plus the export seed when known: '<SIGNATURE> (seed 42)'.

    SIGNATURE stays a verbatim prefix, so byte searches for it keep working.
    """
    return SIGNATURE if seed is None else f"{SIGNATURE} (seed {seed})"


def find_seed(data: bytes) -> int | None:
    """Return the seed recorded next to the inscription in raw file bytes, if any."""
    match = _SEED_RE.search(data)
    return int(match.group(1)) if match else None


def pnginfo_with_signature(
    created_at: datetime | None = None, seed: int | None = None
) -> PngInfo:
    """Build a PngInfo container with readable tEXt metadata before IDAT."""
    if created_at is None:
        created_at = datetime.now()
    pnginfo = PngInfo()
    pnginfo.add_text("Title", SIGNATURE, zip=False)
    pnginfo.add_text("Author", _AUTHOR, zip=False)
    pnginfo.add_text("Copyright", SIGNATURE, zip=False)
    pnginfo.add_text("Comment", signature_text(seed), zip=False)
    pnginfo.add_text("Software", _SOFTWARE, zip=False)
    if seed is not None:
        pnginfo.add_text("Seed", str(seed), zip=False)
    pnginfo.add_text(
        "Creation Time", created_at.strftime("%Y-%m-%dT%H:%M:%S"), zip=False
    )
    # Windows Explorer ignores PNG tEXt/eXIf for Authors/Title/Date taken but
    # reads XMP, so the XMP packet is what populates the Properties dialog.
    pnginfo.add_itxt("XML:com.adobe.xmp", xmp_packet(created_at, seed), zip=False)
    return pnginfo


def xmp_packet(created_at: datetime, seed: int | None = None) -> str:
    """Build an XMP packet (no GPS) carrying author, title, rights and dates.

    The date carries the local UTC offset so it is unambiguous across machines.
    """
    stamp = created_at.astimezone().isoformat(timespec="seconds")
    sig = escape(SIGNATURE)
    desc = escape(signature_text(seed))
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about=""'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
        ' xmlns:exif="http://ns.adobe.com/exif/1.0/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">'
        f"<dc:creator><rdf:Seq><rdf:li>{escape(_AUTHOR)}</rdf:li></rdf:Seq></dc:creator>"
        f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{sig}</rdf:li></rdf:Alt></dc:title>'
        f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{desc}</rdf:li></rdf:Alt></dc:description>'
        f'<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">{sig}</rdf:li></rdf:Alt></dc:rights>'
        f"<xmp:CreatorTool>{escape(_SOFTWARE)}</xmp:CreatorTool>"
        f"<xmp:CreateDate>{stamp}</xmp:CreateDate>"
        f"<exif:DateTimeOriginal>{stamp}</exif:DateTimeOriginal>"
        f"<photoshop:DateCreated>{stamp}</photoshop:DateCreated>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )


def _build_exif(created_at: datetime, seed: int | None = None) -> Image.Exif:
    """Build a shared EXIF object (no GPS) for JPEG and PNG exports."""
    timestamp = created_at.strftime("%Y:%m:%d %H:%M:%S")
    text = signature_text(seed)
    exif = Image.Exif()

    exif[Base.ImageDescription] = text
    exif[Base.Artist] = _AUTHOR
    exif[Base.Copyright] = SIGNATURE
    exif[Base.Software] = _SOFTWARE
    exif[Base.DateTime] = timestamp

    exif[Base.XPTitle] = SIGNATURE.encode("utf-16-le") + b"\0\0"
    exif[Base.XPComment] = text.encode("utf-16-le") + b"\0\0"
    exif[Base.XPAuthor] = _AUTHOR.encode("utf-16-le") + b"\0\0"

    exif[IFD.Exif] = {
        Base.UserComment: _USER_COMMENT_PREFIX + text.encode("ascii"),
        Base.DateTimeOriginal: timestamp,
        Base.DateTimeDigitized: timestamp,
    }

    return exif


def jpeg_exif_bytes(created_at: datetime | None = None, seed: int | None = None) -> bytes:
    """Return EXIF bytes (no GPS) for image.save(..., exif=...)."""
    if created_at is None:
        created_at = datetime.now()
    return _build_exif(created_at, seed).tobytes()


def inject_jpeg_comment(path: str, seed: int | None = None) -> None:
    """Insert a COM segment (FF FE + 2-byte BE length + ASCII signature text) right after JPEG SOI."""
    with open(path, "rb") as fp:
        data = bytearray(fp.read())

    if data[:2] != b"\xff\xd8":
        raise ValueError(f"Missing JPEG SOI marker at start of {path}")

    payload = signature_text(seed).encode("ascii")
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
