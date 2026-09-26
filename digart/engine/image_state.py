"""Image state with undo history."""

from collections import deque
from pathlib import Path

from PIL import Image

from digart.config import HISTORY_LIMIT


class ImageState:
    """Holds the original source and the current working image.

    Maintains a bounded undo history of current images.
    """

    def __init__(self) -> None:
        self.source: Image.Image | None = None
        self.current: Image.Image | None = None
        self.history: deque[Image.Image] = deque(maxlen=HISTORY_LIMIT)
        self.source_name: str | None = None

    def load(self, path: str | Path) -> Image.Image:
        """Load an image from disk, drop alpha, set as source/current."""
        img = Image.open(path).convert("RGB")
        self.source = img.copy()
        self.current = img
        self.source_name = Path(path).stem
        self.history.clear()
        return img

    def set_source(self, image: Image.Image) -> None:
        """Set a new source and reset current/history."""
        rgb = image.convert("RGB")
        self.source = rgb.copy()
        self.current = rgb
        self.source_name = None
        self.history.clear()

    def push(self, image: Image.Image) -> None:
        """Save current to history and set a new current image."""
        if self.current is not None:
            self.history.append(self.current.copy())
        self.current = image.convert("RGB")

    def reset(self) -> Image.Image | None:
        """Restore current from source."""
        if self.source is None:
            return None
        if self.current is not None:
            self.history.append(self.current.copy())
        self.current = self.source.copy()
        return self.current

    def undo(self) -> Image.Image | None:
        """Restore current from history, if any."""
        if not self.history:
            return self.current.copy() if self.current is not None else None
        self.current = self.history.pop()
        return self.current

    @property
    def has_image(self) -> bool:
        return self.current is not None
