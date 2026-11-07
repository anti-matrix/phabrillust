"""Random-number utilities."""

from typing import Any
import zlib

import numpy as np


def get_rng(seed: int | None, salt: Any = None) -> np.random.Generator:
    """Return a deterministic NumPy Generator from a seed + salt.

    If seed is None, a random 32-bit seed is chosen first. The effective seed
    is derived via a stable CRC32 digest so the same ``(seed, salt)`` pair
    produces identical output across separate process launches.
    """
    if seed is None:
        seed = int(np.random.randint(0, 2**32, dtype=np.uint64))
    digest = zlib.crc32(f"{seed}:{salt}".encode("utf-8"))
    effective = digest & 0xFFFFFFFF
    return np.random.default_rng(effective)
