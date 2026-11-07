"""Image state with undo history."""

from collections import deque
from pathlib import Path

from PIL import Image

from phabrillust.config import HISTORY_LIMIT


class ImageState:
    """Holds the original source and the current working image.

    Maintains a bounded undo history of current images, each with a short
    label describing the step that produced it (shown in the History list).
    """

    def __init__(self) -> None:
        self.source: Image.Image | None = None
        self.current: Image.Image | None = None
        self.history: deque[Image.Image] = deque(maxlen=HISTORY_LIMIT)
        self._history_labels: deque[str] = deque(maxlen=HISTORY_LIMIT)
        self.current_label: str = ""
        self.source_name: str | None = None

    def load(self, path: str | Path) -> Image.Image:
        """Load an image from disk, drop alpha, set as source/current."""
        img = Image.open(path).convert("RGB")
        self.source = img.copy()
        self.current = img
        self.source_name = Path(path).stem
        self.current_label = f"Opened {Path(path).name}"
        self._clear_history()
        return img

    def set_source(self, image: Image.Image, label: str = "New image") -> None:
        """Set a new source and reset current/history."""
        rgb = image.convert("RGB")
        self.source = rgb.copy()
        self.current = rgb
        self.source_name = None
        self.current_label = label
        self._clear_history()

    def push(self, image: Image.Image, label: str = "Edit") -> None:
        """Save current to history and set a new current image."""
        if self.current is not None:
            self.history.append(self.current.copy())
            self._history_labels.append(self.current_label)
        self.current = image.convert("RGB")
        self.current_label = label

    def reset(self) -> Image.Image | None:
        """Restore current from source (undoable)."""
        if self.source is None:
            return None
        if self.current is not None:
            self.history.append(self.current.copy())
            self._history_labels.append(self.current_label)
        self.current = self.source.copy()
        self.current_label = "Reset to original"
        return self.current

    def undo(self) -> Image.Image | None:
        """Restore current from history, if any."""
        if not self.history:
            return self.current.copy() if self.current is not None else None
        self.current = self.history.pop()
        self.current_label = self._history_labels.pop()
        return self.current

    def _clear_history(self) -> None:
        self.history.clear()
        self._history_labels.clear()

    @property
    def steps(self) -> list[str]:
        """Labels of the undoable chain, oldest first, current last."""
        return [*self._history_labels, self.current_label] if self.current is not None else []

    @property
    def can_undo(self) -> bool:
        return bool(self.history)

    @property
    def has_image(self) -> bool:
        return self.current is not None
