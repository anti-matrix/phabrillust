"""Verify tab: check whether an image carries the phabrillust signature."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QMargins, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from phabrillust.gui.renderer import pil_to_qpixmap
from phabrillust.gui.worker import VerifyWorker

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif)"
_THUMB_SIDE = 1200
_MAX_CHECKS = 30

_EMPTY_TEXT = "drop images here to check them\nor use “Check images…” (Ctrl+O)"

# (marker, title, label objectName) per verdict. Markers must exist in the bundled
# fonts: Space Grotesk / JetBrains Mono have no ✓.
_VERDICTS = {
    "yes": ("●", "Made with Phabrillust", "verdict-yes"),
    "other": ("●", "Phabrillust watermark, unexpected text", "verdict-partial"),
    "metadata": ("~", "Signed in metadata only", "verdict-partial"),
    "no": ("✕", "No Phabrillust signature", "verdict-no"),
    "error": ("!", "Couldn't read this file", "verdict-error"),
}


def _verdict(report) -> str:
    if report.watermark_ok:
        return "yes"
    if report.watermark is not None:
        return "other"
    if report.has_metadata:
        return "metadata"
    return "no"


def _explain(report) -> str:
    kind = _verdict(report)
    if kind == "yes":
        tail = (
            "The metadata signature is intact too."
            if report.has_metadata
            else "The metadata was stripped (normal after a social media upload), "
            "but the watermark survived."
        )
        return f"The invisible pixel watermark reads “{report.watermark}”. {tail}"
    if kind == "other":
        return (
            f"A valid watermark was found, but it reads “{report.watermark}”. "
            "It was embedded with different watermark text."
        )
    if kind == "metadata":
        return (
            "The file carries the Phabrillust inscription but no pixel watermark. "
            "Images exported before the watermark existed look like this, and so do signed "
            "images that were later cropped, rotated or edited. Metadata is easy to copy, "
            "so this alone is weak evidence."
        )
    return (
        "Neither signature layer was found. The watermark doesn't survive cropping, "
        "rotation, mirroring, brightness/contrast edits or images under ~256 px, so this "
        "doesn't prove the image wasn't made with Phabrillust."
    )


class _Thumbnail(QLabel):
    """Aspect-fit image preview that doubles as the drop-zone hint."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("preview-canvas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._pixmap = None
        self.set_image(None)

    def set_image(self, image: Image.Image | None, text: str = _EMPTY_TEXT) -> None:
        self._pixmap = pil_to_qpixmap(image) if image is not None else None
        if self._pixmap is None:
            self.clear()
            self.setText(text)
        self._scale()

    def _scale(self) -> None:
        if self._pixmap is None:
            return
        area = self.contentsRect().size().shrunkBy(QMargins(24, 24, 24, 24))
        side = min(self._pixmap.width(), area.width()), min(self._pixmap.height(), area.height())
        scaled = self._pixmap.scaled(
            side[0], side[1], Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        self._scale()
        super().resizeEvent(event)


class VerifyPanel(QWidget):
    """Check images for the watermark + metadata signature (see phabrillust.verify)."""

    status = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._pending: set[VerifyWorker] = set()
        self._closing = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.thumbnail = _Thumbnail()
        row.addWidget(self.thumbnail, 1)

        side = QScrollArea()
        side.setWidgetResizable(True)
        side.setFixedWidth(340)
        side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side.setObjectName("verify-side")
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(12)

        self.check_btn = QPushButton("Check images…")
        self.check_btn.setObjectName("primary")
        self.check_btn.setToolTip("Pick one or more images to check (Ctrl+O). You can also drop files here.")
        self.check_btn.clicked.connect(self.choose_files)
        col.addWidget(self.check_btn)

        # RESULT
        result_group = QGroupBox("RESULT")
        result_layout = QVBoxLayout(result_group)
        result_layout.setSpacing(10)
        self.verdict_label = QLabel()
        self.verdict_label.setWordWrap(True)
        result_layout.addWidget(self.verdict_label)
        self.explain_label = QLabel()
        self.explain_label.setObjectName("hint-label")
        self.explain_label.setWordWrap(True)
        result_layout.addWidget(self.explain_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._details: dict[str, QLabel] = {}
        for r, (key, text) in enumerate(
            [("watermark", "Watermark"), ("confidence", "Confidence"), ("metadata", "Metadata"),
             ("seed", "Seed"), ("size", "Size"), ("file", "File")]
        ):
            name = QLabel(text)
            name.setObjectName("param-label")
            grid.addWidget(name, r, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—")
            value.setObjectName("value-label")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._details[key] = value
            if key == "seed":
                tip = "The seed used for the export, recorded in the metadata (lost if it was stripped)"
                name.setToolTip(tip)
                value.setToolTip(tip)
            if key == "file":
                value.setWordWrap(False)
                value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if key == "confidence":
                conf_row = QHBoxLayout()
                self.confidence_bar = QProgressBar()
                self.confidence_bar.setRange(0, 100)
                self.confidence_bar.setTextVisible(False)
                self.confidence_bar.setFixedHeight(6)
                conf_row.addWidget(self.confidence_bar, 1)
                value.setFixedWidth(40)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                conf_row.addWidget(value)
                grid.addLayout(conf_row, r, 1)
            else:
                grid.addWidget(value, r, 1)
        grid.setColumnStretch(1, 1)
        result_layout.addLayout(grid)
        col.addWidget(result_group)

        # CHECKED
        list_group = QGroupBox("CHECKED")
        list_layout = QVBoxLayout(list_group)
        self.checked_list = QListWidget()
        self.checked_list.setObjectName("history-list")
        self.checked_list.setFixedHeight(150)
        self.checked_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.checked_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.checked_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.checked_list.setToolTip("Images checked in this session. Click one to see its result again.")
        self.checked_list.currentItemChanged.connect(self._on_item_changed)
        list_layout.addWidget(self.checked_list)
        col.addWidget(list_group)

        about = QLabel(
            "Two layers are checked. The <b>pixel watermark</b> is invisible and survives "
            "JPEG/WebP compression, resizing and re-uploads. The <b>metadata</b> inscription "
            "is easy to read in a hex editor but is stripped by most platforms."
        )
        about.setObjectName("hint-label")
        about.setWordWrap(True)
        col.addWidget(about)
        col.addStretch(1)

        side.setWidget(inner)
        row.addWidget(side)

        self._show_empty()

    # ------------------------------------------------------------- checking
    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Check images", "", IMAGE_FILTER)
        self.check_paths(paths)

    def check_paths(self, paths: list[str]) -> None:
        for path in paths:
            item = QListWidgetItem(f"…  {Path(path).name}")
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, None)
            self.checked_list.insertItem(0, item)
            while self.checked_list.count() > _MAX_CHECKS:
                self.checked_list.takeItem(self.checked_list.count() - 1)

            worker = VerifyWorker(path, _THUMB_SIDE)
            worker.signals.finished.connect(
                lambda report, thumb, it=item, w=worker: self._on_checked(it, w, report, thumb),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.signals.error.connect(
                lambda p, msg, it=item, w=worker: self._on_error(it, w, p, msg),
                Qt.ConnectionType.QueuedConnection,
            )
            self._pending.add(worker)
            self._pool.start(worker)
        if paths:
            # Show the first file's result as soon as it is ready.
            self.checked_list.setCurrentRow(len(paths) - 1)
            self.status.emit(f"checking {len(paths)} image{'s' if len(paths) > 1 else ''}…")

    def _release(self, worker: VerifyWorker) -> None:
        self._pending.discard(worker)
        for sig in (worker.signals.finished, worker.signals.error):
            try:
                sig.disconnect()
            except Exception:
                pass

    def _on_checked(self, item: QListWidgetItem, worker: VerifyWorker, report, thumb) -> None:
        self._release(worker)
        if self._closing:
            return
        marker = _VERDICTS[_verdict(report)][0]
        item.setText(f"{marker}  {Path(report.path).name}")
        item.setData(Qt.ItemDataRole.UserRole, (report, thumb))
        if item is self.checked_list.currentItem():
            self._show_entry(item)
        if not self._pending:
            self.status.emit("")

    def _on_error(self, item: QListWidgetItem, worker: VerifyWorker, path: str, message: str) -> None:
        self._release(worker)
        if self._closing:
            return
        item.setText(f"!  {Path(path).name}")
        item.setData(Qt.ItemDataRole.UserRole, ("error", path, message))
        if item is self.checked_list.currentItem():
            self._show_entry(item)
        if not self._pending:
            self.status.emit("")

    # -------------------------------------------------------------- display
    def _on_item_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._show_empty()
        else:
            self._show_entry(current)

    def _show_entry(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            self._set_verdict("…", "Checking…", "verdict-no", "Reading the watermark.")
            self._set_details(file=item.toolTip())
            self.thumbnail.set_image(None, "checking…")
        elif data[0] == "error":
            _, path, message = data
            marker, title, style = _VERDICTS["error"]
            self._set_verdict(marker, title, style, message)
            self._set_details(file=path)
            self.thumbnail.set_image(None, "can't open this file")
        else:
            report, thumb = data
            marker, title, style = _VERDICTS[_verdict(report)]
            self._set_verdict(marker, title, style, _explain(report))
            self._set_details(
                watermark=f"“{report.watermark}”" if report.watermark is not None else "not found",
                confidence=report.confidence,
                metadata="signature present" if report.has_metadata else "stripped or never signed",
                seed=str(report.seed) if report.seed is not None else "not recorded",
                size=f"{report.size[0]}×{report.size[1]}",
                file=report.path,
            )
            self.thumbnail.set_image(thumb)

    def _show_empty(self) -> None:
        self._set_verdict("", "Nothing checked yet", "verdict-no", "Open or drop an image to see whether it was made with Phabrillust.")
        self._set_details()
        self.thumbnail.set_image(None)

    def _set_verdict(self, marker: str, title: str, style: str, text: str) -> None:
        self.verdict_label.setText(f"{marker}  {title}" if marker else title)
        self.verdict_label.setObjectName(style)
        self.verdict_label.style().unpolish(self.verdict_label)
        self.verdict_label.style().polish(self.verdict_label)
        self.explain_label.setText(text)

    def _set_details(self, confidence: float | None = None, **values: str) -> None:
        for key, label in self._details.items():
            if key == "confidence":
                label.setText("—" if confidence is None else f"{confidence:.0%}")
            elif key == "file":
                # Paths have no spaces to wrap at; show the name, full path on hover.
                path = values.get("file")
                label.setText(Path(path).name if path else "—")
                label.setToolTip(path or "")
            else:
                label.setText(values.get(key, "—"))
        self.confidence_bar.setValue(0 if confidence is None else round(confidence * 100))

    def shutdown(self) -> None:
        self._closing = True
        self._pool.clear()
        for worker in list(self._pending):
            self._release(worker)
        self._pool.waitForDone(5000)
