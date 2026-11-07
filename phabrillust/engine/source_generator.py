"""Built-in source image generators."""

import math

import numpy as np
from PIL import Image


def blank(w: int, h: int, color: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Solid-color RGB canvas."""
    return Image.new("RGB", (max(1, w), max(1, h)), color)


def solid(w: int, h: int, color: tuple[int, int, int]) -> Image.Image:
    """Solid color fill."""
    return blank(w, h, color)


def linear_gradient(
    w: int,
    h: int,
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
    angle: float = 0.0,
) -> Image.Image:
    """Two-color linear gradient at a given angle in degrees."""
    width = max(1, w)
    height = max(1, h)
    rad = math.radians(angle)

    # Build normalized coordinate grid.
    x = np.linspace(-0.5, 0.5, width)
    y = np.linspace(-0.5, 0.5, height)
    xx, yy = np.meshgrid(x, y)

    # Project onto the gradient direction.
    gx = math.cos(rad)
    gy = math.sin(rad)
    t = (xx * gx + yy * gy + 0.5).clip(0, 1)

    arr1 = np.array(c1, dtype=np.float32)
    arr2 = np.array(c2, dtype=np.float32)
    grad = (arr1[None, None, :] * (1 - t[:, :, None]) + arr2[None, None, :] * t[:, :, None]).astype(np.uint8)
    return Image.fromarray(grad, "RGB")


def radial_gradient(
    w: int,
    h: int,
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
) -> Image.Image:
    """Two-color radial gradient from the center outwards."""
    width = max(1, w)
    height = max(1, h)
    x = np.linspace(-1, 1, width)
    y = np.linspace(-1, 1, height)
    xx, yy = np.meshgrid(x, y)
    # Normalize by aspect ratio so the gradient stays circular.
    aspect = width / height if height else 1.0
    d = np.sqrt((xx * aspect) ** 2 + yy**2)
    t = (d / d.max()).clip(0, 1) if d.max() > 0 else np.zeros_like(d)

    arr1 = np.array(c1, dtype=np.float32)
    arr2 = np.array(c2, dtype=np.float32)
    grad = (arr1[None, None, :] * (1 - t[:, :, None]) + arr2[None, None, :] * t[:, :, None]).astype(np.uint8)
    return Image.fromarray(grad, "RGB")
