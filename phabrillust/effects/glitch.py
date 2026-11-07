"""Algorithmic glitch effects."""

from typing import Any

import numpy as np
from PIL import Image

from phabrillust.effects.base import Effect, ParamDef


class ChannelShift(Effect):
    name = "Channel Shift"
    description = "Offset the red, green and blue channels against each other."
    id = "channel_shift"
    category = "glitch"
    params = [
        ParamDef("red_offset", "Red offset", "int", 0, -20, 20, 1),
        ParamDef("green_offset", "Green offset", "int", 0, -20, 20, 1),
        ParamDef("blue_offset", "Blue offset", "int", 0, -20, 20, 1),
        ParamDef("vertical", "Vertical", "bool", False),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image)
        ro, go, bo = int(p["red_offset"]), int(p["green_offset"]), int(p["blue_offset"])
        vertical = bool(p["vertical"])
        if vertical:
            if ro:
                arr[:, :, 0] = np.roll(arr[:, :, 0], ro, axis=0)
            if go:
                arr[:, :, 1] = np.roll(arr[:, :, 1], go, axis=0)
            if bo:
                arr[:, :, 2] = np.roll(arr[:, :, 2], bo, axis=0)
        else:
            if ro:
                arr[:, :, 0] = np.roll(arr[:, :, 0], ro, axis=1)
            if go:
                arr[:, :, 1] = np.roll(arr[:, :, 1], go, axis=1)
            if bo:
                arr[:, :, 2] = np.roll(arr[:, :, 2], bo, axis=1)
        return Image.fromarray(arr, "RGB")


class ScanlineTear(Effect):
    name = "Scanline Tear"
    description = "Shift random thin horizontal bands sideways."
    id = "scanline_tear"
    category = "glitch"
    params = [
        ParamDef("tears", "Tears", "int", 10, 0, 50, 1),
        ParamDef("max_shift", "Max shift", "int", 20, 0, 100, 1),
        ParamDef("thickness", "Thickness", "int", 2, 1, 20, 1),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image)
        h, w = arr.shape[:2]
        tears = max(0, int(p["tears"]))
        max_shift = int(p["max_shift"])
        thickness = max(1, int(p["thickness"]))

        for _ in range(tears):
            y = rng.integers(0, max(1, h - thickness + 1))
            shift = rng.integers(-max_shift, max_shift + 1)
            arr[y : y + thickness, :, :] = np.roll(arr[y : y + thickness, :, :], shift, axis=1)
        return Image.fromarray(arr, "RGB")


class PixelSort(Effect):
    name = "Pixel Sort"
    description = "Sort runs of pixels brighter than the threshold."
    id = "pixel_sort"
    category = "glitch"
    params = [
        ParamDef("threshold", "Threshold", "int", 128, 0, 255, 1),
        ParamDef("angle", "Direction", "choice", "horizontal", choices=["horizontal", "vertical"]),
        ParamDef("sort_by", "Sort by", "choice", "lightness", choices=["lightness", "hue", "saturation"]),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image).astype(np.float32)
        threshold = int(p["threshold"])
        angle = str(p["angle"])
        sort_by = str(p["sort_by"])

        if sort_by == "lightness":
            key = arr.max(axis=2)
        elif sort_by == "hue":
            import cv2

            hsv = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
            key = hsv[:, :, 0]
        else:  # saturation
            import cv2

            hsv = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
            key = hsv[:, :, 1]

        mask = key >= threshold

        def sort_line(line: np.ndarray, mask_line: np.ndarray) -> np.ndarray:
            out = line.copy()
            labels = np.diff(mask_line.astype(np.int8), prepend=0, append=0)
            starts = np.where(labels == 1)[0]
            ends = np.where(labels == -1)[0]
            for s, e in zip(starts, ends):
                if e - s < 2:
                    continue
                segment = out[s:e]
                order = np.argsort(segment.max(axis=1))
                out[s:e] = segment[order]
            return out

        if angle == "horizontal":
            for y in range(arr.shape[0]):
                arr[y] = sort_line(arr[y], mask[y])
        else:
            for x in range(arr.shape[1]):
                arr[:, x] = sort_line(arr[:, x], mask[:, x])

        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class BlockDisplace(Effect):
    name = "Block Displace"
    description = "Copy or move square blocks to random nearby spots."
    id = "block_displace"
    category = "glitch"
    params = [
        ParamDef("block_size", "Block size", "int", 32, 8, 128, 1),
        ParamDef("count", "Count", "int", 10, 0, 50, 1),
        ParamDef("max_shift", "Max shift", "int", 30, 0, 100, 1),
        ParamDef("mode", "Mode", "choice", "copy", choices=["copy", "move"]),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image).copy()
        h, w = arr.shape[:2]
        block_size = max(8, int(p["block_size"]))
        count = max(0, int(p["count"]))
        max_shift = int(p["max_shift"])
        mode = str(p["mode"])

        for _ in range(count):
            x = rng.integers(0, max(1, w - block_size + 1))
            y = rng.integers(0, max(1, h - block_size + 1))
            dx = rng.integers(-max_shift, max_shift + 1)
            dy = rng.integers(-max_shift, max_shift + 1)

            block = arr[y : y + block_size, x : x + block_size].copy()
            y2 = (y + dy) % h
            x2 = (x + dx) % w
            # Wrap destination region carefully.
            y2_end = min(y2 + block_size, h)
            x2_end = min(x2 + block_size, w)
            bh = y2_end - y2
            bw = x2_end - x2
            arr[y2:y2_end, x2:x2_end] = block[:bh, :bw]
            if mode == "move":
                arr[y : y + block_size, x : x + block_size] = 0

        return Image.fromarray(arr, "RGB")


class GlitchSlice(Effect):
    name = "Glitch Slice"
    description = "Cut the image into strips and shift each one."
    id = "glitch_slice"
    category = "glitch"
    params = [
        ParamDef("slices", "Slices", "int", 12, 2, 100, 1),
        ParamDef("max_shift", "Max shift", "int", 20, 0, 80, 1),
        ParamDef("direction", "Direction", "choice", "horizontal", choices=["horizontal", "vertical"]),
    ]

    def apply(self, image: Image.Image, params: dict[str, Any], rng: np.random.Generator) -> Image.Image:
        p = self.param_values(params)
        arr = np.array(image)
        h, w = arr.shape[:2]
        slices = max(2, int(p["slices"]))
        max_shift = int(p["max_shift"])
        direction = str(p["direction"])

        if direction == "horizontal":
            bounds = np.linspace(0, h, slices + 1, dtype=np.int32)
            for i in range(slices):
                y1, y2 = bounds[i], bounds[i + 1]
                shift = rng.integers(-max_shift, max_shift + 1)
                arr[y1:y2, :, :] = np.roll(arr[y1:y2, :, :], shift, axis=1)
        else:
            bounds = np.linspace(0, w, slices + 1, dtype=np.int32)
            for i in range(slices):
                x1, x2 = bounds[i], bounds[i + 1]
                shift = rng.integers(-max_shift, max_shift + 1)
                arr[:, x1:x2, :] = np.roll(arr[:, x1:x2, :], shift, axis=0)

        return Image.fromarray(arr, "RGB")
