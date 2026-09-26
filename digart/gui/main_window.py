"""Main application window."""

import random
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from digart.config import DEFAULT_HEIGHT, DEFAULT_WIDTH, PREVIEW_MAX_SIDE
from digart.engine.image_state import ImageState
from digart.utils.export import default_export_name
from digart.engine.pipeline import (
    DATABEND_EFFECTS,
    GLITCH_EFFECTS,
    NOISE_EFFECTS,
    apply_pipeline,
)
from digart.gui.renderer import pil_to_qpixmap
from digart.engine.source_generator import (
    blank,
    linear_gradient,
    radial_gradient,
    solid,
)
from digart.gui.widgets import EffectPanel
from digart.gui.worker import ExportWorker, PreviewWorker


class Canvas(QLabel):
    """Preview canvas that scales and centers a pixmap."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("preview-canvas")
        self._pixmap: QPixmap | None = None
        self._base_size: tuple[int, int] | None = None
        self._displayed_size: tuple[int, int] | None = None

    def set_pil_image(self, image: Image.Image | None) -> None:
        if image is None:
            self._pixmap = None
            self._base_size = None
            self.setText("no image")
            return
        self._pixmap = pil_to_qpixmap(image)
        self._base_size = image.size
        self._scale()

    def _scale(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        rect = self.contentsRect()
        scaled = self._pixmap.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._displayed_size = (scaled.width(), scaled.height())
        super().setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        self._scale()
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        state: ImageState,
        initial_seed: int = 0,
        default_width: int = DEFAULT_WIDTH,
        default_height: int = DEFAULT_HEIGHT,
    ) -> None:
        super().__init__()
        self.state = state
        self.default_width = default_width
        self.default_height = default_height
        self.global_seed = initial_seed
        self._live_preview = True
        self._preview_counter = 0
        self._latest_finished_counter = 0
        self._active_workers: set[PreviewWorker] = set()
        self._closing = False
        self._export_worker: ExportWorker | None = None
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)
        self._warning_timer = QTimer(self)
        self._warning_timer.setSingleShot(True)
        self._warning_timer.timeout.connect(self._clear_warning)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._run_preview)

        self.setWindowTitle("phabrillust")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        self._build_ui()
        self._connect_signals()

        # Initial preview.
        self._refresh_preview()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # Left panel.
        left = QWidget()
        left.setFixedWidth(260)
        left_layout = QVBoxLayout(left)
        left_layout.setSpacing(12)
        left_layout.setContentsMargins(12, 12, 12, 12)

        source_group = QGroupBox("SOURCE")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(8)

        load_btn = QPushButton("Load Image")
        load_btn.clicked.connect(self._load_image)
        source_layout.addWidget(load_btn)

        generate_btn = QToolButton()
        generate_btn.setText("Generate ▾")
        generate_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        generate_menu = QMenu(generate_btn)
        generate_menu.addAction("Blank", self._generate_blank)
        generate_menu.addAction("Solid", self._generate_solid)
        generate_menu.addAction("Linear Gradient", self._generate_linear)
        generate_menu.addAction("Radial Gradient", self._generate_radial)
        generate_btn.setMenu(generate_menu)
        source_layout.addWidget(generate_btn)

        undo_btn = QPushButton("Undo")
        undo_btn.clicked.connect(self._undo)
        source_layout.addWidget(undo_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(self._reset)
        source_layout.addWidget(reset_btn)

        left_layout.addWidget(source_group)

        seed_group = QGroupBox("SEED")
        seed_layout = QHBoxLayout(seed_group)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2**31 - 1)
        self.seed_spin.setValue(self.global_seed)
        self.seed_spin.setWrapping(False)
        seed_layout.addWidget(self.seed_spin)
        random_seed_btn = QPushButton("Randomize")
        random_seed_btn.clicked.connect(self._randomize_seed)
        seed_layout.addWidget(random_seed_btn)
        left_layout.addWidget(seed_group)

        preview_group = QGroupBox("PREVIEW")
        preview_layout = QVBoxLayout(preview_group)
        self.live_checkbox = QCheckBox("Live preview")
        self.live_checkbox.setChecked(True)
        preview_layout.addWidget(self.live_checkbox)
        self.processing_label = QLabel("")
        self.processing_label.setObjectName("processing-label")
        preview_layout.addWidget(self.processing_label)
        left_layout.addWidget(preview_group)

        export_btn = QToolButton()
        export_btn.setText("Export ▾")
        export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        export_menu = QMenu(export_btn)
        export_menu.addAction("PNG", self._export)
        export_menu.addAction("JPEG", self._export_jpeg)
        export_btn.setMenu(export_menu)
        export_btn.clicked.connect(self._export)
        left_layout.addWidget(export_btn)

        left_layout.addStretch(1)
        splitter.addWidget(left)

        # Center canvas.
        self.canvas = Canvas()
        splitter.addWidget(self.canvas)

        # Right panel.
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFixedWidth(320)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(12, 12, 12, 12)

        self._panels: list[EffectPanel] = []
        self._right_sections: dict[str, QGroupBox] = {}

        for title, effects in [
            ("Glitch", GLITCH_EFFECTS),
            ("Noise", NOISE_EFFECTS),
            ("Databend", DATABEND_EFFECTS),
        ]:
            section = QGroupBox(title.upper())
            section_layout = QVBoxLayout(section)
            section_layout.setSpacing(8)
            section_layout.setContentsMargins(8, 16, 8, 8)
            for effect in effects:
                panel = EffectPanel(effect)
                panel.configuration_changed.connect(self._schedule_preview)
                self._panels.append(panel)
                section_layout.addWidget(panel)
            right_layout.addWidget(section)
            self._right_sections[title.lower()] = section

        right_layout.addStretch(1)
        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)

        splitter.setSizes([260, 820, 320])

        # Status bar.
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_dim_label = QLabel("—")
        self.status_fit_label = QLabel("—")
        self.status_warn_label = QLabel("")
        self.status_warn_label.setObjectName("status-warning")
        self.status.addWidget(self.status_dim_label)
        self.status.addWidget(self.status_fit_label)
        self.status.addWidget(self.status_warn_label, 1)

    def _connect_signals(self) -> None:
        self.seed_spin.valueChanged.connect(self._seed_changed)
        self.live_checkbox.stateChanged.connect(self._live_toggled)

    def _live_toggled(self, state: int) -> None:
        self._live_preview = bool(state)
        if self._live_preview:
            self._schedule_preview()

    def _seed_changed(self, value: int) -> None:
        self.global_seed = value
        if self._live_preview:
            self._schedule_preview()

    def _randomize_seed(self) -> None:
        self.seed_spin.setValue(random.randint(0, 2**31 - 1))

    def _enabled_effects(self) -> list[tuple[Any, dict[str, Any]]]:
        result: list[tuple[Any, dict[str, Any]]] = []
        for panel in self._panels:
            if panel.is_enabled():
                result.append((panel.effect, panel.get_params()))
        return result

    def _schedule_preview(self) -> None:
        if not self._live_preview or not self.state.has_image or self._closing:
            return
        # Restartable debounce: rapid changes coalesce into one preview.
        self._preview_timer.stop()
        self._preview_timer.start(120)

    def _run_preview(self) -> None:
        if not self.state.has_image or self._closing:
            return
        self._preview_counter += 1
        counter = self._preview_counter
        self.processing_label.setText("Processing…")
        source = self.state.current
        enabled = self._enabled_effects()
        worker = PreviewWorker(
            counter,
            source,
            enabled,
            self.global_seed,
            PREVIEW_MAX_SIDE,
        )
        worker.setAutoDelete(False)
        self._active_workers.add(worker)

        def on_done(c: int, img: Image.Image | None, warns: list[str]) -> None:
            self._active_workers.discard(worker)
            self._on_preview_finished(c, img, warns)
            # Break the closure<->signal reference cycle (the C++ connection
            # holds a strong ref to this closure that Python's GC cannot see),
            # so the worker and its full-res image reference can be freed.
            try:
                worker.signals.finished.disconnect(on_done)
            except Exception:
                pass
            # PySide6 with setAutoDelete(False) may retain the QRunnable wrapper
            # itself; drop the full-res image reference so memory is released.
            worker.source = None

        worker.signals.finished.connect(on_done, Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    def _on_preview_finished(self, counter: int, image: Image.Image | None, warnings: list[str]) -> None:
        if self._closing:
            return
        if counter < self._latest_finished_counter:
            return
        self._latest_finished_counter = counter
        if self._export_worker is None:
            self.processing_label.setText("")
        if image is None:
            self._show_warning("Preview failed")
            return
        self.canvas.set_pil_image(image)
        self._update_status_dim(self.state.current.size)
        if warnings:
            self._show_warning("; ".join(warnings))

    def _update_status_dim(self, size: tuple[int, int]) -> None:
        w, h = size
        self.status_dim_label.setText(f"{w}×{h}")
        if w and h and self.canvas._displayed_size:
            dw, dh = self.canvas._displayed_size
            fit_w = dw / w * 100
            fit_h = dh / h * 100
            self.status_fit_label.setText(f"fit {min(fit_w, fit_h):.0f}%")

    def _show_warning(self, text: str) -> None:
        self.status_warn_label.setText(text)
        self._warning_timer.start(5000)

    def _clear_warning(self) -> None:
        self.status_warn_label.setText("")

    def _refresh_preview(self) -> None:
        if self.state.has_image:
            self.canvas.set_pil_image(self.state.current)
            self._update_status_dim(self.state.current.size)
        else:
            self.canvas.set_pil_image(None)

    def _load_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.webp *.gif)",
        )
        if path:
            try:
                self.state.load(path)
                self._refresh_preview()
                self._schedule_preview()
            except Exception as exc:
                QMessageBox.critical(self, "Load failed", str(exc))

    def _generator_dimensions(self) -> tuple[int, int] | None:
        w, ok = QInputDialog.getInt(self, "Width", "Width:", self.default_width, 1, 16384, 1)
        if not ok:
            return None
        h, ok = QInputDialog.getInt(self, "Height", "Height:", self.default_height, 1, 16384, 1)
        if not ok:
            return None
        return w, h

    def _generate_blank(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        w, h = dims
        img = blank(w, h)
        self.state.set_source(img)
        self._refresh_preview()
        self._schedule_preview()

    def _generate_solid(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        color = QColorDialog.getColor()
        if not color.isValid():
            return
        rgb = (color.red(), color.green(), color.blue())
        img = solid(dims[0], dims[1], rgb)
        self.state.set_source(img)
        self._refresh_preview()
        self._schedule_preview()

    def _generate_linear(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        c1 = QColorDialog.getColor()
        if not c1.isValid():
            return
        c2 = QColorDialog.getColor()
        if not c2.isValid():
            return
        angle, ok = QInputDialog.getDouble(self, "Angle", "Angle (degrees):", 0.0, -360.0, 360.0, 1)
        if not ok:
            return
        rgb1 = (c1.red(), c1.green(), c1.blue())
        rgb2 = (c2.red(), c2.green(), c2.blue())
        img = linear_gradient(dims[0], dims[1], rgb1, rgb2, angle)
        self.state.set_source(img)
        self._refresh_preview()
        self._schedule_preview()

    def _generate_radial(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        c1 = QColorDialog.getColor()
        if not c1.isValid():
            return
        c2 = QColorDialog.getColor()
        if not c2.isValid():
            return
        rgb1 = (c1.red(), c1.green(), c1.blue())
        rgb2 = (c2.red(), c2.green(), c2.blue())
        img = radial_gradient(dims[0], dims[1], rgb1, rgb2)
        self.state.set_source(img)
        self._refresh_preview()
        self._schedule_preview()

    def _undo(self) -> None:
        self.state.undo()
        self._refresh_preview()
        self._schedule_preview()

    def _reset(self) -> None:
        self.state.reset()
        self._refresh_preview()
        self._schedule_preview()

    def _export(self) -> None:
        self._run_export_dialog(is_jpeg=False)

    def _export_jpeg(self) -> None:
        self._run_export_dialog(is_jpeg=True)

    def _run_export_dialog(self, is_jpeg: bool) -> None:
        if not self.state.has_image:
            QMessageBox.warning(self, "Export", "No image to export.")
            return
        if self._export_worker is not None:
            return

        ext = ".jpg" if is_jpeg else ".png"
        default_name = default_export_name(self.state.source_name, self.global_seed, ext)
        if is_jpeg:
            title = "Export JPEG"
            filter_ = "JPEG (*.jpg *.jpeg)"
        else:
            title = "Export PNG"
            filter_ = "PNG (*.png)"

        path, _ = QFileDialog.getSaveFileName(self, title, default_name, filter_)
        if not path:
            return

        p = Path(path)
        if is_jpeg:
            if p.suffix.lower() not in (".jpg", ".jpeg"):
                p = p.with_suffix(".jpg")
        else:
            if p.suffix.lower() != ".png":
                p = p.with_suffix(".png")
        path = str(p)

        quality = 95
        if is_jpeg:
            quality, ok = QInputDialog.getInt(self, "JPEG Quality", "Quality:", 95, 10, 100, 1)
            if not ok:
                return

        self._start_export(path, is_jpeg, quality)

    def _start_export(self, path: str, is_jpeg: bool, quality: int) -> None:
        self._set_export_active(True)
        worker = ExportWorker(
            self.state.current,
            self._enabled_effects(),
            self.global_seed,
            path,
            is_jpeg,
            quality,
        )
        worker.setAutoDelete(False)
        self._export_worker = worker

        def on_finished(warnings: list[str]) -> None:
            if self._closing:
                return
            self._export_worker = None
            self._set_export_active(False)
            if warnings:
                self._show_warning("Export warnings: " + "; ".join(warnings))

        def on_error(msg: str) -> None:
            if self._closing:
                return
            self._export_worker = None
            self._set_export_active(False)
            QMessageBox.critical(self, "Export failed", msg)

        worker.signals.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(on_error, Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    def _set_export_active(self, active: bool) -> None:
        self.centralWidget().setEnabled(not active)
        self.processing_label.setText("Exporting…" if active else "")

    def closeEvent(self, event) -> None:
        self._closing = True
        self._preview_timer.stop()
        self._thread_pool.clear()
        if self._export_worker is not None:
            try:
                self._export_worker.signals.finished.disconnect()
            except Exception:
                pass
            try:
                self._export_worker.signals.error.disconnect()
            except Exception:
                pass
            self._export_worker = None
        for worker in list(self._active_workers):
            try:
                worker.signals.finished.disconnect()
            except Exception:
                pass
        self._thread_pool.waitForDone(5000)
        event.accept()
