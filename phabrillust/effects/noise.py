"""Noise effects."""

from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from phabrillust.effects.base import Effect, ParamDef


class GaussianNoise(Effect):
    name = "Gaussian Noise"
    description = "Soft random noise, per channel or monochrome."
    id = "gaussian_noise"
    category = "noise"
    params = [
        ParamDef("sigma", "Sigma", "float", 10.0, 0.0, 100.0, 1.0),
        ParamDef("per_channel", "Per channel", "bool", True),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image, dtype=np.float32)
        sigma = float(p["sigma"])
        per_channel = bool(p["per_channel"])
        if per_channel:
            noise = rng.normal(0, sigma, arr.shape)
        else:
            noise = rng.normal(0, sigma, arr.shape[:2] + (1,))
        return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB")


class SaltPepper(Effect):
    name = "Salt & Pepper"
    description = "Scatter pure white and black pixels."
    id = "salt_pepper"
    category = "noise"
    params = [
        ParamDef("amount", "Amount", "float", 0.05, 0.0, 0.5, 0.01),
        ParamDef("salt_ratio", "Salt ratio", "float", 0.5, 0.0, 1.0, 0.05),
        ParamDef("per_channel", "Per channel", "bool", True),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image, dtype=np.float32)
        amount = float(p["amount"])
        salt_ratio = float(p["salt_ratio"])
        per_channel = bool(p["per_channel"])
        h, w = arr.shape[:2]
        total = h * w
        n = int(total * amount)
        if n <= 0:
            return Image.fromarray(arr.astype(np.uint8), "RGB")

        coords = rng.choice(total, size=n, replace=False)
        ys, xs = np.unravel_index(coords, (h, w))
        n_salt = int(n * salt_ratio)

        if per_channel:
            for i in range(3):
                salt_mask = np.zeros(n, dtype=bool)
                salt_mask[:n_salt] = True
                rng.shuffle(salt_mask)
                arr[ys[salt_mask], xs[salt_mask], i] = 255
                arr[ys[~salt_mask], xs[~salt_mask], i] = 0
        else:
            salt_mask = np.zeros(n, dtype=bool)
            salt_mask[:n_salt] = True
            rng.shuffle(salt_mask)
            arr[ys[salt_mask], xs[salt_mask]] = 255
            arr[ys[~salt_mask], xs[~salt_mask]] = 0

        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class ValueNoise(Effect):
    name = "Value Noise"
    description = "Blotchy multi-scale cloud noise."
    id = "value_noise"
    category = "noise"
    params = [
        ParamDef("scale", "Scale", "int", 20, 1, 100, 1),
        ParamDef("octaves", "Octaves", "int", 3, 1, 8, 1),
        ParamDef("persistence", "Persistence", "float", 0.5, 0.0, 1.0, 0.05),
        ParamDef("per_channel", "Per channel", "bool", True),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image, dtype=np.float32)
        scale = max(1, int(p["scale"]))
        octaves = max(1, min(8, int(p["octaves"])))
        persistence = float(p["persistence"])
        per_channel = bool(p["per_channel"])
        h, w = arr.shape[:2]

        base_h = max(1, h // scale)
        base_w = max(1, w // scale)

        noise = np.zeros(arr.shape if per_channel else (h, w), dtype=np.float32)
        amp = 1.0
        total_amp = 0.0
        for _ in range(octaves):
            if per_channel:
                layer = rng.random((base_h, base_w, 3)).astype(np.float32)
                zoomed = ndimage.zoom(layer, (h / base_h, w / base_w, 1), order=1)
                # Crop to exact size if zoom produces rounding differences.
                zoomed = zoomed[:h, :w, :]
            else:
                layer = rng.random((base_h, base_w)).astype(np.float32)
                zoomed = ndimage.zoom(layer, (h / base_h, w / base_w), order=1)
                zoomed = zoomed[:h, :w]
            noise += zoomed * amp
            total_amp += amp
            amp *= persistence
            base_h = min(base_h * 2, h)
            base_w = min(base_w * 2, w)

        noise = (noise / total_amp) * 255
        if not per_channel:
            noise = noise[:, :, None]
        return Image.fromarray(np.clip(arr + noise - 128, 0, 255).astype(np.uint8), "RGB")


class FilmGrain(Effect):
    name = "Film Grain"
    description = "Fine photographic grain with a slight blur."
    id = "film_grain"
    category = "noise"
    params = [
        ParamDef("intensity", "Intensity", "float", 15.0, 0.0, 100.0, 1.0),
        ParamDef("grain_size", "Grain size", "int", 2, 1, 10, 1),
        ParamDef("monochrome", "Monochrome", "bool", False),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image, dtype=np.float32)
        intensity = float(p["intensity"])
        grain_size = max(1, int(p["grain_size"]))
        monochrome = bool(p["monochrome"])
        h, w = arr.shape[:2]

        small_h = max(1, h // grain_size)
        small_w = max(1, w // grain_size)
        if monochrome:
            grain = rng.normal(0, intensity, (small_h, small_w))
            grain = ndimage.zoom(grain, (h / small_h, w / small_w), order=1)[:h, :w]
            grain = grain[:, :, None]
        else:
            grain = rng.normal(0, intensity, (small_h, small_w, 3))
            grain = ndimage.zoom(grain, (h / small_h, w / small_w, 1), order=1)[:h, :w, :]

        # Subtle blur to simulate film grain clumping.
        grain = ndimage.gaussian_filter(grain, sigma=0.5)
        return Image.fromarray(np.clip(arr + grain, 0, 255).astype(np.uint8), "RGB")
