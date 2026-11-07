"""Image selections (rect / ellipse / lasso) as resolution-independent masks.

Points are stored normalized to [0, 1] so the same selection applies to the
downscaled preview and the full-resolution export.
"""

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

SHAPES = ("rect", "ellipse", "lasso")
MAX_FEATHER = 0.1  # feather is a fraction of the image's long side


@dataclass(frozen=True)
class Selection:
    shape: str  # one of SHAPES
    points: tuple[tuple[float, float], ...]  # rect/ellipse: two corners; lasso: polygon
    inverted: bool = False
    feather: float = 0.0  # 0..MAX_FEATHER

    def __post_init__(self) -> None:
        if self.shape not in SHAPES:
            raise ValueError(f"unknown selection shape: {self.shape!r}")
        needed = 3 if self.shape == "lasso" else 2
        if len(self.points) < needed:
            raise ValueError(f"{self.shape} selection needs at least {needed} points")

    def _pixel_points(self, size: tuple[int, int]) -> list[tuple[float, float]]:
        w, h = size
        return [(x * w, y * h) for x, y in self.points]

    def _feather_px(self, size: tuple[int, int]) -> float:
        return max(0.0, min(self.feather, MAX_FEATHER)) * max(size)

    def mask(self, size: tuple[int, int]) -> Image.Image:
        """Return an 'L' mask: 255 where effects apply, 0 where they don't."""
        m = Image.new("L", size, 0)
        draw = ImageDraw.Draw(m)
        pts = self._pixel_points(size)
        if self.shape == "lasso":
            draw.polygon(pts, fill=255)
        else:
            (x0, y0), (x1, y1) = pts[0], pts[1]
            box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if self.shape == "rect":
                draw.rectangle(box, fill=255)
            else:
                draw.ellipse(box, fill=255)
        if self.inverted:
            m = m.point(lambda v: 255 - v)
        radius = self._feather_px(size)
        if radius > 0:
            m = m.filter(ImageFilter.GaussianBlur(radius))
        return m

    def bbox(self, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
        """Pixel box (left, top, right, bottom) covering the affected area, or None if empty."""
        w, h = size
        if self.inverted:
            return (0, 0, w, h)
        pts = self._pixel_points(size)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        pad = 2 * self._feather_px(size)
        left = max(0, int(min(xs) - pad))
        top = max(0, int(min(ys) - pad))
        right = min(w, int(max(xs) + pad + 1))
        bottom = min(h, int(max(ys) + pad + 1))
        if right - left < 2 or bottom - top < 2:
            return None
        return (left, top, right, bottom)

    def describe(self, size: tuple[int, int]) -> str:
        """Short human description, e.g. 'ellipse 340×220' (bbox in pixels of ``size``)."""
        pts = self._pixel_points(size)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        text = f"{self.shape} {round(max(xs) - min(xs))}×{round(max(ys) - min(ys))}"
        if self.inverted:
            text += " inverted"
        if self.feather > 0:
            text += " feathered"
        return text
