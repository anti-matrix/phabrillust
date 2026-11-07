"""Invisible pixel watermark that survives JPEG re-encoding and resizing.

Scheme: spread-transform dither modulation (STDM) on cell-mean luma.
The image is divided into a G x G grid in *relative* coordinates, so resizing
does not move it. Each payload bit owns a keyed set of cells; its feature is
the projection of those cells' mean luma onto a keyed +/-1 direction. The
embedder nudges that feature onto a quantizer lattice whose parity encodes the
bit. Because the embedder knows the image, the image content itself does not
interfere with reading (unlike additive spread-spectrum), so even very busy
art (value noise, pixel sorting) reads reliably.

Payload: up to ``MAX_CHARS`` characters from a 64-symbol alphabet (6 bits
each) plus a CRC-16. A bad CRC means "no watermark found".

Survives: JPEG/WebP re-encoding down to ~q50, downscaling, repeated re-uploads.
Does not survive: cropping, rotation, mirroring, partial screenshots, or
brightness/contrast edits. The key is public (no private key yet), so anyone
can read or strip it.
"""

import binascii
import zlib
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from PIL import Image

WATERMARK_TEXT = "Done by @Phabrillust"
MAX_CHARS = 20
ALPHABET = " @0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_KEY = "phabrillust-watermark-v1"
_GRID = 64
_CELL_PX = 8  # work-size pixels per grid cell
_TEXT_BITS = MAX_CHARS * 6
_NBITS = _TEXT_BITS + 16
_STEP = 24.0  # quantizer step on the projected feature (luma levels)
_ITERATIONS = 5


@dataclass
class WatermarkResult:
    text: str | None  # None when no valid watermark was found
    confidence: float  # ~1.0 on a clean marked image, ~0.0 on unmarked ones


def _plan() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(zlib.crc32(_KEY.encode()))
    chips = rng.choice([-1.0, 1.0], size=(_GRID, _GRID))
    owner = rng.permutation(np.arange(_GRID * _GRID) % _NBITS).reshape(_GRID, _GRID)
    dither = rng.uniform(0.0, _STEP, _NBITS)
    norms = np.sqrt(np.bincount(owner.ravel(), minlength=_NBITS).astype(np.float64))
    return chips, owner, dither, norms


def _luma(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _cell_means(image: Image.Image) -> np.ndarray:
    side = _GRID * _CELL_PX
    work = image.convert("RGB").resize((side, side), Image.BOX)
    y = _luma(np.asarray(work, dtype=np.float64))
    return y.reshape(_GRID, _CELL_PX, _GRID, _CELL_PX).mean(axis=(1, 3))


def _features(cells, chips, owner, norms) -> np.ndarray:
    return np.bincount(owner.ravel(), weights=(cells * chips).ravel(), minlength=_NBITS) / norms


def _encode_text(text: str) -> np.ndarray:
    if len(text) > MAX_CHARS:
        raise ValueError(f"watermark text longer than {MAX_CHARS} chars: {text!r}")
    bad = sorted(set(text) - set(ALPHABET))
    if bad:
        raise ValueError(f"watermark text has unsupported characters: {bad}")
    padded = text.ljust(MAX_CHARS)
    bits = [(ALPHABET.index(c) >> (5 - k)) & 1 for c in padded for k in range(6)]
    crc = binascii.crc_hqx(padded.encode("ascii"), 0)
    bits += [(crc >> (15 - k)) & 1 for k in range(16)]
    return np.array(bits, dtype=np.int64)


def _decode_bits(bits: np.ndarray) -> str | None:
    chars = []
    for i in range(MAX_CHARS):
        idx = 0
        for b in bits[i * 6 : i * 6 + 6]:
            idx = (idx << 1) | int(b)
        chars.append(ALPHABET[idx])
    padded = "".join(chars)
    crc = 0
    for b in bits[_TEXT_BITS:]:
        crc = (crc << 1) | int(b)
    if binascii.crc_hqx(padded.encode("ascii"), 0) != crc:
        return None
    return padded.rstrip(" ")


def embed_watermark(image: Image.Image, text: str = WATERMARK_TEXT) -> Image.Image:
    """Return an RGB copy of ``image`` carrying ``text`` as an invisible watermark."""
    payload = _encode_text(text)
    chips, owner, dither, norms = _plan()
    rgb = image.convert("RGB")
    w, h = rgb.size
    base = np.asarray(rgb, dtype=np.float32)

    # Target: nearest lattice point (step _STEP) whose parity equals the bit.
    half = _STEP / 2.0
    x0 = _features(_cell_means(rgb), chips, owner, norms)
    k = np.round((x0 - dither - payload * half) / _STEP)
    target = dither + payload * half + k * _STEP

    # Iterate: smooth (bilinear) delta field attenuates and clipping eats some
    # of the change, so re-measure and push the remaining error each pass.
    cell_delta = np.zeros((_GRID, _GRID))
    out = rgb
    for _ in range(_ITERATIONS):
        err = target - _features(_cell_means(out), chips, owner, norms)
        if np.max(np.abs(err)) < 0.5:
            break
        cell_delta += chips * (err / norms)[owner]
        field = np.asarray(
            Image.fromarray(cell_delta.astype(np.float32), "F").resize((w, h), Image.BILINEAR)
        )
        arr = np.clip(base + field[..., None], 0, 255)
        out = Image.fromarray(np.rint(arr).astype(np.uint8), "RGB")
    return out


def read_watermark(image: Image.Image) -> WatermarkResult:
    """Try to recover watermark text from ``image`` (any size, any format)."""
    chips, owner, dither, norms = _plan()
    x = _features(_cell_means(image), chips, owner, norms)
    r = (x - dither) / (_STEP / 2.0)
    nearest = np.round(r)
    bits = nearest.astype(np.int64) % 2
    offset = np.abs(r - nearest)  # 0 = dead on a lattice point, 0.5 = coin flip
    # Unmarked images have offsets uniform on [0, 0.5] (mean 0.25) -> ~0.
    confidence = float(np.clip(1.0 - 4.0 * np.mean(offset), 0.0, 1.0))
    text = _decode_bits(bits)
    if text is None and confidence >= _CHASE_MIN_CONFIDENCE:
        text = _chase_decode(bits, offset)
    return WatermarkResult(text=text, confidence=confidence)


_CHASE_MIN_CONFIDENCE = 0.3
_CHASE_CANDIDATES = 10
_CHASE_MAX_FLIPS = 3


def _chase_decode(bits: np.ndarray, offset: np.ndarray) -> str | None:
    """Retry the CRC after flipping combinations of the least reliable bits.

    Only used on images that are clearly marked (gated by confidence), so the
    extra CRC trials cannot turn an unmarked image into a false positive.
    """
    weakest = np.argsort(-offset)[:_CHASE_CANDIDATES]
    for flips in range(1, _CHASE_MAX_FLIPS + 1):
        for combo in combinations(weakest, flips):
            trial = bits.copy()
            trial[list(combo)] ^= 1
            text = _decode_bits(trial)
            if text is not None:
                return text
    return None
