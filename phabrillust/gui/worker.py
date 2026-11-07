"""Background worker for live preview rendering."""

from datetime import datetime
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, Signal

from phabrillust.engine.pipeline import apply_pipeline
from phabrillust.engine.selection import Selection
from phabrillust.effects.base import Effect


class PreviewWorkerSignals(QObject):
    finished = Signal(int, object, list)


class PreviewWorker(QRunnable):
    """Run apply_pipeline in a background thread.

    Emits ``finished(counter, image, warnings)`` when done.
    """

    def __init__(
        self,
        counter: int,
        source: Image.Image,
        enabled_effects: list[tuple[Effect, dict[str, Any]]],
        global_seed: int,
        max_preview_side: int,
        selection: Selection | None = None,
    ) -> None:
        super().__init__()
        self.counter = counter
        self.source = source
        self.enabled_effects = enabled_effects
        self.global_seed = global_seed
        self.max_preview_side = max_preview_side
        self.selection = selection
        self.signals = PreviewWorkerSignals()

    def run(self) -> None:
        try:
            image, warnings = apply_pipeline(
                self.source,
                self.enabled_effects,
                self.global_seed,
                preview=True,
                max_preview_side=self.max_preview_side,
                selection=self.selection,
            )
            self.signals.finished.emit(self.counter, image, warnings)
        except Exception as exc:
            self.signals.finished.emit(self.counter, None, [str(exc)])


class RenderWorkerSignals(QObject):
    finished = Signal(object, list)  # full-res image, warnings
    error = Signal(str)


class RenderWorker(QRunnable):
    """Run the full-resolution pipeline on a background thread ("Apply to image")."""

    def __init__(
        self,
        source: Image.Image,
        enabled_effects: list[tuple[Effect, dict[str, Any]]],
        global_seed: int,
        selection: Selection | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.enabled_effects = enabled_effects
        self.global_seed = global_seed
        self.selection = selection
        self.signals = RenderWorkerSignals()

    def run(self) -> None:
        try:
            image, warnings = apply_pipeline(
                self.source,
                self.enabled_effects,
                self.global_seed,
                preview=False,
                selection=self.selection,
            )
            self.signals.finished.emit(image, warnings)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class VerifyWorkerSignals(QObject):
    finished = Signal(object, object)  # VerifyReport, RGB thumbnail
    error = Signal(str, str)  # path, message


class VerifyWorker(QRunnable):
    """Read the watermark + metadata signature of an image file (Verify tab)."""

    def __init__(self, path: str, thumb_side: int) -> None:
        super().__init__()
        self.path = path
        self.thumb_side = thumb_side
        self.signals = VerifyWorkerSignals()

    def run(self) -> None:
        try:
            # Local import: phabrillust.verify pulls in engine.signature, which
            # must not be imported at module level here (see ExportWorker.run).
            from phabrillust.verify import check

            report = check(self.path)
            with Image.open(self.path) as img:
                img.draft("RGB", (self.thumb_side, self.thumb_side))
                thumb = img.convert("RGB")
            thumb.thumbnail((self.thumb_side, self.thumb_side), Image.Resampling.LANCZOS)
            self.signals.finished.emit(report, thumb)
        except Exception as exc:
            self.signals.error.emit(self.path, str(exc))


class ExportWorkerSignals(QObject):
    finished = Signal(list)  # warnings
    error = Signal(str)


class ExportWorker(QRunnable):
    """Run apply_pipeline + save on a background thread."""

    def __init__(
        self,
        source: Image.Image,
        enabled_effects: list[tuple[Effect, dict[str, Any]]],
        global_seed: int,
        path: str,
        is_jpeg: bool,
        quality: int,
        selection: Selection | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.enabled_effects = enabled_effects
        self.global_seed = global_seed
        self.path = path
        self.is_jpeg = is_jpeg
        self.quality = quality
        self.selection = selection
        self.signals = ExportWorkerSignals()

    def run(self) -> None:
        try:
            image, warnings = apply_pipeline(
                self.source,
                self.enabled_effects,
                self.global_seed,
                preview=False,
                selection=self.selection,
            )
            # Import signature helpers locally: a module-level import triggers a
            # native heap-corruption crash (0xC0000374) in test_preview_worker_no_leak
            # under Python 3.14.6 / PySide6 6.11.2. Keeping them here keeps the leak
            # test stable.
            from phabrillust.engine.signature import (
                inject_jpeg_comment,
                jpeg_exif_bytes,
                pnginfo_with_signature,
            )
            from phabrillust.engine.watermark import embed_watermark

            # Watermark last: any effect after it (databending) would destroy it.
            image = embed_watermark(image)
            created_at = datetime.now()
            if self.is_jpeg:
                image.convert("RGB").save(
                    self.path,
                    "JPEG",
                    quality=self.quality,
                    optimize=True,
                    exif=jpeg_exif_bytes(created_at, self.global_seed),
                )
                inject_jpeg_comment(self.path, self.global_seed)
            else:
                image.save(
                    self.path,
                    "PNG",
                    pnginfo=pnginfo_with_signature(created_at, self.global_seed),
                    exif=jpeg_exif_bytes(created_at, self.global_seed),
                )
            self.signals.finished.emit(warnings)
        except Exception as exc:
            self.signals.error.emit(str(exc))
