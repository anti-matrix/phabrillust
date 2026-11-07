"""Byte-level databending effects.

All databend effects encode the current image to bytes, corrupt those bytes,
and attempt to decode the result. If decoding fails they return the original
image and append a warning string.
"""

from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

from phabrillust.effects.base import Effect, ParamDef

PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
JPEG_SOI = bytes([0xFF, 0xD8])
JPEG_EOI = bytes([0xFF, 0xD9])


def _png_header_end(data: bytes) -> int:
    """Return the byte index just after the IHDR chunk (signature + IHDR)."""
    # PNG signature 8 + IHDR length(4)+type(4)+data(13)+crc(4) = 33
    return 33


def _is_png(data: bytes) -> bool:
    return data[:8] == PNG_SIG


def _is_jpeg(data: bytes) -> bool:
    return len(data) >= 2 and data[:2] == JPEG_SOI


def _safe_indices(data: bytes, protect_headers: bool) -> set[int]:
    """Return indices that must not be modified for header protection."""
    protected: set[int] = set()
    if not protect_headers:
        return protected

    if _is_png(data):
        protected.update(range(_png_header_end(data)))
    elif _is_jpeg(data):
        protected.update({0, 1, len(data) - 2, len(data) - 1})
        # Avoid marker bytes: 0xFF followed by non-0x00.
        for i in range(2, len(data) - 2):
            if data[i] == 0xFF and data[i + 1] != 0x00:
                protected.add(i)
                protected.add(i + 1)
    else:
        protected.update(range(min(32, len(data))))
        protected.update(range(max(0, len(data) - 4), len(data)))
    return protected


def _mutable_mask(data: bytes, protected: set[int]) -> list[int]:
    return [i for i in range(len(data)) if i not in protected]


def corrupt_bytes_bitflip(data: bytes, intensity: float, protect_headers: bool, rng: np.random.Generator) -> bytes:
    """Randomly flip bits in byte data."""
    protected = _safe_indices(data, protect_headers)
    mutable = _mutable_mask(data, protected)
    if not mutable:
        return data
    count = max(0, int(len(data) * intensity))
    if count == 0:
        return data

    bytearray_data = bytearray(data)
    chosen = rng.choice(mutable, size=min(count, len(mutable)), replace=False)
    for idx in chosen:
        bit = rng.integers(0, 8)
        bytearray_data[idx] ^= 1 << bit
    return bytes(bytearray_data)


def corrupt_bytes_drop(data: bytes, intensity: float, protect_headers: bool, rng: np.random.Generator) -> bytes:
    """Randomly drop bytes from data."""
    protected = _safe_indices(data, protect_headers)
    mutable = _mutable_mask(data, protected)
    count = max(0, int(len(data) * intensity))
    if count == 0 or not mutable:
        return data
    chosen = set(rng.choice(mutable, size=min(count, len(mutable)), replace=False))
    return bytes(b for i, b in enumerate(data) if i not in chosen)


def corrupt_bytes_duplicate(data: bytes, intensity: float, protect_headers: bool, rng: np.random.Generator) -> bytes:
    """Randomly duplicate bytes in data."""
    protected = _safe_indices(data, protect_headers)
    mutable = _mutable_mask(data, protected)
    if not mutable:
        return data
    count = max(0, int(len(data) * intensity))
    if count == 0:
        return data

    chosen = sorted(rng.choice(mutable, size=min(count, len(mutable)), replace=False))
    result = bytearray(data)
    for idx in reversed(chosen):
        result.insert(idx, result[idx])
    return bytes(result)


def corrupt_bytes_shuffle_chunks(data: bytes, chunk_size: int, shuffle_amount: float, protect_headers: bool, rng: np.random.Generator) -> bytes:
    """Shuffle chunks of byte data."""
    if chunk_size < 2 or len(data) <= chunk_size:
        return data

    protected = _safe_indices(data, protect_headers)
    # Convert to list of indices, shuffle in chunks, but skip protected.
    indices = list(range(len(data)))
    chunks = [indices[i : i + chunk_size] for i in range(0, len(indices), chunk_size)]
    num_swaps = int(len(chunks) * shuffle_amount)
    if num_swaps <= 0:
        return data

    for _ in range(num_swaps):
        a = rng.integers(0, len(chunks))
        b = rng.integers(0, len(chunks))
        # Only swap if neither chunk contains protected bytes.
        chunk_a = set(chunks[a])
        chunk_b = set(chunks[b])
        if chunk_a.isdisjoint(protected) and chunk_b.isdisjoint(protected):
            chunks[a], chunks[b] = chunks[b], chunks[a]

    new_indices = [i for chunk in chunks for i in chunk]
    return bytes(data[i] for i in new_indices)


def _find_idat_bounds(data: bytes) -> tuple[int, int] | None:
    """Return start/end offsets of the first IDAT chunk payload, or None."""
    pos = _png_header_end(data)
    while pos + 8 < len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"IDAT":
            start = pos + 8
            return start, start + length
        pos += 8 + length + 4
    return None


def corrupt_png_idat(data: bytes, intensity: float, rng: np.random.Generator) -> bytes:
    """Corrupt bytes inside the first PNG IDAT chunk payload."""
    if not _is_png(data):
        return data
    bounds = _find_idat_bounds(data)
    if bounds is None:
        return data
    start, end = bounds
    payload = data[start:end]
    if not payload:
        return data
    corrupted_payload = corrupt_bytes_bitflip(payload, intensity, protect_headers=False, rng=rng)
    return data[:start] + corrupted_payload + data[end:]


def corrupt_jpeg_body(data: bytes, intensity: float, rng: np.random.Generator) -> bytes:
    """Corrupt bytes in the body of a JPEG, skipping SOI/EOI and markers."""
    if not _is_jpeg(data) or len(data) < 4:
        return data

    # Protect SOI and EOI.
    protected = {0, 1, len(data) - 2, len(data) - 1}
    # Protect marker bytes in the body.
    i = 2
    while i < len(data) - 2:
        if data[i] == 0xFF and data[i + 1] != 0x00:
            protected.add(i)
            protected.add(i + 1)
            # Skip over segment length if applicable.
            marker = data[i + 1]
            if 0xD0 <= marker <= 0xD7 or marker in (0xD8, 0xD9, 0x01):
                i += 2
            else:
                seg_len = int.from_bytes(data[i + 2 : i + 4], "big") if i + 4 <= len(data) else 2
                for j in range(i, min(i + 2 + seg_len, len(data))):
                    protected.add(j)
                i += 2 + seg_len
        else:
            i += 1

    mutable = _mutable_mask(data, protected)
    if not mutable:
        return data
    count = max(0, int(len(data) * intensity))
    if count == 0:
        return data

    bytearray_data = bytearray(data)
    chosen = rng.choice(mutable, size=min(count, len(mutable)), replace=False)
    for idx in chosen:
        bytearray_data[idx] = rng.integers(0, 256)
    return bytes(bytearray_data)


def decode_bent_bytes(data: bytes) -> Image.Image | None:
    """Try to decode corrupted bytes; progressively repair if needed.

    Returns None if decoding fails after up to three attempts.
    """
    attempts = [data]

    # Attempt 1: raw.
    # Attempt 2: truncate trailing noise for PNG/JPEG.
    # Attempt 3: more aggressive truncate.
    if _is_png(data):
        # Try truncating to the IEND chunk if we can find it.
        end_pos = data.find(b"IEND")
        if end_pos != -1:
            attempts.append(data[: end_pos + 8])  # IEND type + CRC
        attempts.append(data[: max(33, len(data) * 9 // 10)])
    elif _is_jpeg(data):
        # Truncate after last EOI marker.
        eoi_pos = data.rfind(JPEG_EOI)
        if eoi_pos != -1:
            attempts.append(data[: eoi_pos + 2])
        attempts.append(data[: len(data) * 9 // 10])
    else:
        attempts.append(data[: len(data) * 9 // 10])
        attempts.append(data[: len(data) * 4 // 5])

    for payload in attempts:
        try:
            img = Image.open(BytesIO(payload))
            img.load()
            return img.convert("RGB")
        except Exception:
            continue
    return None


def _to_png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _to_jpeg_bytes(image: Image.Image, quality: int = 95) -> bytes:
    buf = BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


class _BaseDatabend(Effect):
    category = "databend"

    def _encode(self, image: Image.Image) -> bytes:
        raise NotImplementedError

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        raise NotImplementedError

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        try:
            data = self._encode(image)
            bent = self._corrupt(data, params, rng)
            decoded = decode_bent_bytes(bent)
            if decoded is None:
                return image
            return decoded.convert("RGB")
        except Exception:
            return image


class DatabendBitflip(_BaseDatabend):
    name = "Databend Bitflip"
    description = "Flip random bits in the image's PNG bytes."
    id = "databend_bitflip"
    params = [
        ParamDef("intensity", "Intensity", "float", 0.005, 0.0, 0.1, 0.001),
        ParamDef("protect_headers", "Protect headers", "bool", True),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_png_bytes(image)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_bytes_bitflip(data, float(params.get("intensity", 0.005)), bool(params.get("protect_headers", True)), rng)


class DatabendDrop(_BaseDatabend):
    name = "Databend Drop"
    description = "Delete random bytes from the PNG data (smears and shifts)."
    id = "databend_drop"
    params = [
        ParamDef("intensity", "Intensity", "float", 0.002, 0.0, 0.05, 0.001),
        ParamDef("protect_headers", "Protect headers", "bool", True),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_png_bytes(image)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_bytes_drop(data, float(params.get("intensity", 0.002)), bool(params.get("protect_headers", True)), rng)


class DatabendDuplicate(_BaseDatabend):
    name = "Databend Duplicate"
    description = "Repeat random bytes in the PNG data."
    id = "databend_duplicate"
    params = [
        ParamDef("intensity", "Intensity", "float", 0.002, 0.0, 0.05, 0.001),
        ParamDef("protect_headers", "Protect headers", "bool", True),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_png_bytes(image)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_bytes_duplicate(data, float(params.get("intensity", 0.002)), bool(params.get("protect_headers", True)), rng)


class DatabendShuffle(_BaseDatabend):
    name = "Databend Shuffle"
    description = "Swap chunks of the PNG data around."
    id = "databend_shuffle"
    params = [
        ParamDef("chunk_size", "Chunk size", "int", 16, 2, 256, 1),
        ParamDef("shuffle_amount", "Shuffle amount", "float", 0.1, 0.0, 1.0, 0.05),
        ParamDef("protect_headers", "Protect headers", "bool", True),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_png_bytes(image)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_bytes_shuffle_chunks(
            data,
            int(params.get("chunk_size", 16)),
            float(params.get("shuffle_amount", 0.1)),
            bool(params.get("protect_headers", True)),
            rng,
        )


class DatabendPngIdat(_BaseDatabend):
    name = "Databend PNG IDAT"
    description = "Corrupt only the compressed pixel data of the PNG."
    id = "databend_png_idat"
    params = [
        ParamDef("intensity", "Intensity", "float", 0.005, 0.0, 0.05, 0.001),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_png_bytes(image)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_png_idat(data, float(params.get("intensity", 0.005)), rng)


class DatabendJpegBody(_BaseDatabend):
    name = "Databend JPEG Body"
    description = "Encode as JPEG, then corrupt the compressed body (blocky color bursts)."
    id = "databend_jpeg_body"
    params = [
        ParamDef("intensity", "Intensity", "float", 0.005, 0.0, 0.05, 0.001),
    ]

    def _encode(self, image: Image.Image) -> bytes:
        return _to_jpeg_bytes(image, quality=95)

    def _corrupt(self, data: bytes, params: dict[str, Any], rng: np.random.Generator) -> bytes:
        return corrupt_jpeg_body(data, float(params.get("intensity", 0.005)), rng)


DATABEND_EFFECTS: list[Effect] = [
    DatabendBitflip(),
    DatabendDrop(),
    DatabendDuplicate(),
    DatabendShuffle(),
    DatabendPngIdat(),
    DatabendJpegBody(),
]
