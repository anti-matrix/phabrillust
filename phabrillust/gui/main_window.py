"""Main application window."""

import random
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
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
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from phabrillust.config import DEFAULT_HEIGHT, DEFAULT_WIDTH, PREVIEW_MAX_SIDE
from phabrillust.engine.image_state import ImageState
from phabrillust.engine.pipeline import (
    DATABEND_EFFECTS,
    GLITCH_EFFECTS,
    NOISE_EFFECTS,
)
from phabrillust.engine.selection import MAX_FEATHER, Selection
from phabrillust.engine.source_generator import (
    blank,
    linear_gradient,
    radial_gradient,
    solid,
)
from phabrillust.gui.canvas import Canvas
from phabrillust.gui.verify_panel import IMAGE_FILTER, VerifyPanel
from phabrillust.gui.widgets import EffectPanel, guard_wheel
from phabrillust.gui.worker import ExportWorker, PreviewWorker, RenderWorker
from phabrillust.utils.export import default_export_name

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif")
_TOOLS = [("Off", None), ("Rect", "rect"), ("Ellipse", "ellipse"), ("Lasso", "lasso")]


def _full_width(button: QWidget) -> QWidget:
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return button


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
        self._suspend_preview = False
        self._preview_counter = 0
        self._latest_finished_counter = 0
        self._active_workers: set[PreviewWorker] = set()
        self._closing = False
        self._export_worker: ExportWorker | None = None
        self._render_worker: RenderWorker | None = None
        self._selection_shape: Selection | None = None  # as drawn; invert/feather applied on top
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)
        self._warning_timer = QTimer(self)
        self._warning_timer.setSingleShot(True)
        self._warning_timer.timeout.connect(self._clear_warning)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._run_preview)

        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._build_shortcuts()

        # Initial preview.
        self._refresh_preview()
        self._on_image_replaced()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("main-tabs")
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._edit_page = splitter
        self.tabs.addTab(splitter, "EDIT")
        self.verify_panel = VerifyPanel()
        self.tabs.addTab(self.verify_panel, "VERIFY")
        self.tabs.setTabToolTip(0, "Glitch an image")
        self.tabs.setTabToolTip(1, "Check whether an image was made with Phabrillust")

        splitter.addWidget(self._build_left_panel())

        self.canvas = Canvas()
        splitter.addWidget(self.canvas)

        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 780, 340])

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_dim_label = QLabel("—")
        self.status_fit_label = QLabel("—")
        self.status_sel_label = QLabel("no selection")
        self.status_warn_label = QLabel("")
        self.status_warn_label.setObjectName("status-warning")
        self.status.addWidget(self.status_dim_label)
        self.status.addWidget(self.status_fit_label)
        self.status.addWidget(self.status_sel_label)
        self.status.addWidget(self.status_warn_label, 1)
        self.verify_panel.status.connect(self._show_info)
        self.tabs.currentChanged.connect(self._tab_changed)

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(280)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # FILE
        file_group = QGroupBox("IMAGE")
        file_layout = QHBoxLayout(file_group)
        open_btn = _full_width(QPushButton("Open…"))
        open_btn.setToolTip("Open an image (Ctrl+O). You can also drop a file on the window.")
        open_btn.clicked.connect(self._load_image)
        new_btn = _full_width(QToolButton())
        new_btn.setText("New ▾")
        new_btn.setToolTip("Start from a generated image")
        new_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        new_menu = QMenu(new_btn)
        new_menu.addAction("Blank", self._generate_blank)
        new_menu.addAction("Solid color", self._generate_solid)
        new_menu.addAction("Linear gradient", self._generate_linear)
        new_menu.addAction("Radial gradient", self._generate_radial)
        new_btn.setMenu(new_menu)
        file_layout.addWidget(open_btn)
        file_layout.addWidget(new_btn)
        layout.addWidget(file_group)

        # SELECTION
        sel_group = QGroupBox("SELECTION")
        sel_layout = QVBoxLayout(sel_group)
        sel_layout.setSpacing(8)
        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self._tool_buttons: dict[str | None, QToolButton] = {}
        for text, tool in _TOOLS:
            b = _full_width(QToolButton())
            b.setText(text)
            b.setCheckable(True)
            b.setObjectName("tool-button")
            b.setToolTip(
                "Stop drawing (keeps the current selection)" if tool is None
                else f"Draw a {tool} selection on the image (hold Shift for a square/circle)" if tool != "lasso"
                else "Draw a freehand selection on the image"
            )
            self.tool_group.addButton(b)
            self._tool_buttons[tool] = b
            tools_row.addWidget(b)
            b.clicked.connect(lambda _=False, t=tool: self._set_tool(t))
        self._tool_buttons[None].setChecked(True)
        sel_layout.addLayout(tools_row)

        opts_row = QHBoxLayout()
        self.invert_checkbox = QCheckBox("Invert")
        self.invert_checkbox.setToolTip("Affect everything except the selection (Ctrl+Shift+I)")
        self.clear_sel_btn = QPushButton("Clear")
        self.clear_sel_btn.setObjectName("small-button")
        self.clear_sel_btn.setToolTip("Remove the selection; effects apply to the whole image (Ctrl+D)")
        self.clear_sel_btn.clicked.connect(self._clear_selection)
        opts_row.addWidget(self.invert_checkbox)
        opts_row.addStretch(1)
        opts_row.addWidget(self.clear_sel_btn)
        sel_layout.addLayout(opts_row)

        feather_row = QHBoxLayout()
        feather_label = QLabel("Feather")
        feather_label.setObjectName("param-label")
        self.feather_slider = QSlider(Qt.Orientation.Horizontal)
        self.feather_slider.setRange(0, 100)
        self.feather_slider.setToolTip("Soften the selection edge")
        guard_wheel(self.feather_slider)
        self.feather_value = QLabel("0")
        self.feather_value.setObjectName("value-label")
        self.feather_value.setFixedWidth(28)
        feather_row.addWidget(feather_label)
        feather_row.addWidget(self.feather_slider, 1)
        feather_row.addWidget(self.feather_value)
        sel_layout.addLayout(feather_row)

        self.sel_hint = QLabel("No selection: effects apply to the whole image.")
        self.sel_hint.setObjectName("hint-label")
        self.sel_hint.setWordWrap(True)
        sel_layout.addWidget(self.sel_hint)
        layout.addWidget(sel_group)

        # SEED
        seed_group = QGroupBox("SEED")
        seed_layout = QHBoxLayout(seed_group)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2**31 - 1)
        self.seed_spin.setValue(self.global_seed)
        self.seed_spin.setToolTip("Same seed + same settings = same result")
        self.seed_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        guard_wheel(self.seed_spin)
        seed_layout.addWidget(self.seed_spin, 1)
        random_seed_btn = QPushButton("Random")
        random_seed_btn.setObjectName("small-button")
        random_seed_btn.setToolTip("Pick a random seed")
        random_seed_btn.clicked.connect(self._randomize_seed)
        seed_layout.addWidget(random_seed_btn)
        layout.addWidget(seed_group)

        # PREVIEW
        preview_group = QGroupBox("PREVIEW")
        preview_layout = QVBoxLayout(preview_group)
        live_row = QHBoxLayout()
        self.live_checkbox = QCheckBox("Live preview")
        self.live_checkbox.setChecked(True)
        self.live_checkbox.setToolTip("Re-render automatically on every change")
        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("small-button")
        self.update_btn.setToolTip("Render the preview now (F5)")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self._run_preview)
        live_row.addWidget(self.live_checkbox)
        live_row.addStretch(1)
        live_row.addWidget(self.update_btn)
        preview_layout.addLayout(live_row)
        self.processing_label = QLabel("")
        self.processing_label.setObjectName("processing-label")
        preview_layout.addWidget(self.processing_label)
        layout.addWidget(preview_group)

        # HISTORY
        history_group = QGroupBox("HISTORY")
        history_layout = QVBoxLayout(history_group)
        self.history_list = QListWidget()
        self.history_list.setObjectName("history-list")
        self.history_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.history_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history_list.setFixedHeight(130)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history_list.setToolTip("Steps applied to the image. Undo goes back one step.")
        history_layout.addWidget(self.history_list)
        hist_row = QHBoxLayout()
        self.undo_btn = _full_width(QPushButton("Undo"))
        self.undo_btn.setToolTip("Undo the last applied step (Ctrl+Z)")
        self.undo_btn.clicked.connect(self._undo)
        self.reset_btn = _full_width(QPushButton("Reset"))
        self.reset_btn.setToolTip("Go back to the original image (undoable)")
        self.reset_btn.clicked.connect(self._reset)
        hist_row.addWidget(self.undo_btn)
        hist_row.addWidget(self.reset_btn)
        history_layout.addLayout(hist_row)
        layout.addWidget(history_group)

        layout.addStretch(1)

        export_btn = _full_width(QToolButton())
        export_btn.setObjectName("primary")
        export_btn.setText("Export PNG")
        export_btn.setToolTip("Export at full resolution (Ctrl+S PNG, Ctrl+Shift+S JPEG)")
        export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        export_menu = QMenu(export_btn)
        export_menu.addAction("Export PNG", self._export)
        export_menu.addAction("Export JPEG…", self._export_jpeg)
        export_btn.setMenu(export_menu)
        export_btn.clicked.connect(self._export)
        layout.addWidget(export_btn)

        scroll.setWidget(left)
        return scroll

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        container.setFixedWidth(340)
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        header.setObjectName("panel-header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 10)
        header_layout.setSpacing(6)
        self.apply_btn = _full_width(QPushButton("Apply to image"))
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setToolTip(
            "Bake the enabled effects into the image (Ctrl+Enter), then turn them off.\n"
            "Use it to stack passes: e.g. sort one selection, apply, select another area, databend it."
        )
        self.apply_btn.clicked.connect(self._apply_effects)
        header_layout.addWidget(self.apply_btn)
        self.effects_hint = QLabel("Tick an effect's title to turn it on.")
        self.effects_hint.setObjectName("hint-label")
        header_layout.addWidget(self.effects_hint)
        col.addWidget(header)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(12)
        right_layout.setContentsMargins(12, 4, 12, 12)

        self._panels: list[EffectPanel] = []
        self._right_sections: dict[str, QGroupBox] = {}

        for title, effects in [
            ("Glitch", GLITCH_EFFECTS),
            ("Noise", NOISE_EFFECTS),
            ("Databend", DATABEND_EFFECTS),
        ]:
            section = QGroupBox(title.upper())
            section_layout = QVBoxLayout(section)
            section_layout.setSpacing(6)
            section_layout.setContentsMargins(8, 16, 8, 8)
            for effect in effects:
                panel = EffectPanel(effect)
                panel.configuration_changed.connect(self._schedule_preview)
                panel.toggled.connect(self._update_effect_counts)
                self._panels.append(panel)
                section_layout.addWidget(panel)
            right_layout.addWidget(section)
            self._right_sections[title.lower()] = section

        right_layout.addStretch(1)
        right_scroll.setWidget(right_widget)
        col.addWidget(right_scroll, 1)
        return container

    def _connect_signals(self) -> None:
        self.seed_spin.valueChanged.connect(self._seed_changed)
        self.live_checkbox.stateChanged.connect(self._live_toggled)
        self.canvas.selection_drawn.connect(self._on_selection_drawn)
        self.canvas.resized.connect(self._update_status_dim_current)
        self.invert_checkbox.toggled.connect(self._selection_options_changed)
        self.feather_slider.valueChanged.connect(self._selection_options_changed)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self._open_shortcut)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.tabs.setCurrentIndex(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.tabs.setCurrentIndex(1))
        # Editor shortcuts do nothing while the Verify tab is showing.
        bindings = [
            (QKeySequence("Ctrl+S"), self._export),
            (QKeySequence("Ctrl+Shift+S"), self._export_jpeg),
            (QKeySequence.StandardKey.Undo, self._undo),
            (QKeySequence("Ctrl+Return"), self._apply_effects),
            (QKeySequence("Ctrl+Enter"), self._apply_effects),
            (QKeySequence("F5"), self._run_preview),
            (QKeySequence("Ctrl+D"), self._clear_selection),
            (QKeySequence("Ctrl+Shift+I"), self.invert_checkbox.toggle),
            (QKeySequence("Esc"), self._escape),
        ]
        for seq, slot in bindings:
            QShortcut(seq, self, activated=lambda s=slot: self._editing and s())

    @property
    def _editing(self) -> bool:
        return self.tabs.currentWidget() is self._edit_page

    def _open_shortcut(self) -> None:
        if self._editing:
            self._load_image()
        else:
            self.verify_panel.choose_files()

    def _tab_changed(self, _index: int) -> None:
        # Image size / fit / selection describe the editor canvas only.
        for label in (self.status_dim_label, self.status_fit_label, self.status_sel_label):
            label.setVisible(self._editing)
        self._clear_warning()

    # ------------------------------------------------------------- preview
    def _live_toggled(self, state: int) -> None:
        self._live_preview = bool(state)
        self.update_btn.setEnabled(not self._live_preview)
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
        if not self._live_preview or not self.state.has_image or self._closing or self._suspend_preview:
            return
        # Restartable debounce: rapid changes coalesce into one preview.
        self._preview_timer.stop()
        self._preview_timer.start(120)

    def _run_preview(self) -> None:
        if not self.state.has_image or self._closing:
            return
        self._preview_counter += 1
        counter = self._preview_counter
        if not self._busy:
            self.processing_label.setText("Rendering preview…")
        source = self.state.current
        enabled = self._enabled_effects()
        worker = PreviewWorker(
            counter,
            source,
            enabled,
            self.global_seed,
            PREVIEW_MAX_SIDE,
            selection=self.selection,
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
        if not self._busy:
            self.processing_label.setText("")
        if image is None:
            self._show_warning("Preview failed")
            return
        self.canvas.set_pil_image(image)
        self._update_status_dim(self.state.current.size)
        if warnings:
            self._show_warning("; ".join(warnings))

    def _refresh_preview(self) -> None:
        # Invalidate any in-flight preview so a stale result can't overwrite this.
        self._preview_counter += 1
        self._latest_finished_counter = self._preview_counter
        if self.state.has_image:
            self.canvas.set_pil_image(self.state.current)
            self._update_status_dim(self.state.current.size)
        else:
            self.canvas.set_pil_image(None)

    # -------------------------------------------------------------- status
    def _update_status_dim(self, size: tuple[int, int]) -> None:
        w, h = size
        self.status_dim_label.setText(f"{w}×{h}")
        if w and h and self.canvas._displayed_size:
            dw, dh = self.canvas._displayed_size
            fit_w = dw / w * 100
            fit_h = dh / h * 100
            self.status_fit_label.setText(f"fit {min(fit_w, fit_h):.0f}%")

    def _update_status_dim_current(self) -> None:
        if self.state.has_image:
            self._update_status_dim(self.state.current.size)

    def _show_warning(self, text: str) -> None:
        self._show_status(text, "status-warning")

    def _show_info(self, text: str) -> None:
        self._show_status(text, "status-info")

    def _show_status(self, text: str, kind: str) -> None:
        if self.status_warn_label.objectName() != kind:
            self.status_warn_label.setObjectName(kind)
            self.status_warn_label.style().unpolish(self.status_warn_label)
            self.status_warn_label.style().polish(self.status_warn_label)
        self.status_warn_label.setText(text)
        self._warning_timer.start(5000)

    def _clear_warning(self) -> None:
        self.status_warn_label.setText("")

    def _update_effect_counts(self) -> None:
        total = 0
        for key, section in self._right_sections.items():
            n = sum(1 for p in self._panels if p.effect.category == key and p.is_enabled())
            total += n
            section.setTitle(f"{key.upper()} · {n} on" if n else key.upper())
        self.apply_btn.setEnabled(total > 0 and not self._busy)
        if total == 0:
            self.effects_hint.setText("Tick an effect's title to turn it on.")
        else:
            where = "the selection" if self.selection is not None else "the whole image"
            self.effects_hint.setText(f"{total} effect{'s' if total > 1 else ''} on · affecting {where}")

    def _update_history(self) -> None:
        self.history_list.clear()
        steps = self.state.steps
        for i, label in enumerate(steps):
            item = QListWidgetItem(("● " if i == len(steps) - 1 else "   ") + label)
            item.setToolTip(label)
            if i == len(steps) - 1:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            else:
                item.setForeground(QColor("#888888"))
            self.history_list.addItem(item)
        self.history_list.scrollToBottom()
        self.undo_btn.setEnabled(self.state.can_undo and not self._busy)
        self.reset_btn.setEnabled(self.state.has_image and not self._busy)

    def _update_title(self) -> None:
        name = self.state.source_name
        self.setWindowTitle(f"{name} — phabrillust" if name else "phabrillust")

    # ----------------------------------------------------------- selection
    @property
    def selection(self) -> Selection | None:
        if self._selection_shape is None:
            return None
        feather = self.feather_slider.value() / 100 * MAX_FEATHER
        return replace(self._selection_shape, inverted=self.invert_checkbox.isChecked(), feather=feather)

    def _set_tool(self, tool: str | None) -> None:
        self._tool_buttons[tool].setChecked(True)
        self.canvas.set_tool(tool)

    def _on_selection_drawn(self, selection: Selection | None) -> None:
        self._selection_shape = selection
        self._selection_changed()

    def _selection_options_changed(self) -> None:
        self.feather_value.setText(str(self.feather_slider.value()))
        if self._selection_shape is not None:
            self._selection_changed()

    def _clear_selection(self) -> None:
        if self._selection_shape is None:
            return
        self._selection_shape = None
        self._selection_changed()

    def _selection_changed(self) -> None:
        sel = self.selection
        self.canvas.set_selection(sel)
        has = sel is not None
        self.invert_checkbox.setEnabled(has)
        self.feather_slider.setEnabled(has)
        self.clear_sel_btn.setEnabled(has)
        if has and self.state.has_image:
            self.status_sel_label.setText(f"sel {sel.describe(self.state.current.size)}")
            self.sel_hint.setText("Effects apply only inside the selection (the dimmed area is untouched).")
        else:
            self.status_sel_label.setText("no selection")
            self.sel_hint.setText("No selection: effects apply to the whole image.")
        self._update_effect_counts()
        self._schedule_preview()

    def _escape(self) -> None:
        if not self.canvas.cancel_drawing():
            self._set_tool(None)

    # --------------------------------------------------------------- image
    def _on_image_replaced(self) -> None:
        """A new image was opened/generated: drop the selection, refresh chrome."""
        self._selection_shape = None
        self._selection_changed()
        self._update_history()
        self._update_title()

    def _open_path(self, path: str) -> None:
        try:
            self.state.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Couldn't open image", f"{Path(path).name}\n\n{exc}")
            return
        self._refresh_preview()
        self._on_image_replaced()
        self._schedule_preview()

    def _load_image(self) -> None:
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open image", "", IMAGE_FILTER)
        if path:
            self._open_path(path)

    @staticmethod
    def _dropped_images(event) -> list[str]:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        return [p for p in (u.toLocalFile() for u in urls) if p.lower().endswith(_IMAGE_EXTS)]

    def dragEnterEvent(self, event) -> None:
        # Verify takes any number of images; the editor opens one (and not mid-job).
        if self._dropped_images(event) and (not self._editing or not self._busy):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._dropped_images(event)
        if not paths:
            return
        if self._editing:
            self._open_path(paths[0])
        else:
            self.verify_panel.check_paths(paths)
        event.acceptProposedAction()

    def _generator_dimensions(self) -> tuple[int, int] | None:
        w, ok = QInputDialog.getInt(self, "New image", "Width (px):", self.default_width, 1, 16384, 1)
        if not ok:
            return None
        h, ok = QInputDialog.getInt(self, "New image", "Height (px):", self.default_height, 1, 16384, 1)
        if not ok:
            return None
        return w, h

    def _pick_color(self, title: str, initial: str) -> tuple[int, int, int] | None:
        color = QColorDialog.getColor(QColor(initial), self, title)
        if not color.isValid():
            return None
        return (color.red(), color.green(), color.blue())

    def _set_generated(self, img: Image.Image, label: str) -> None:
        self.state.set_source(img, label=f"{label} {img.width}×{img.height}")
        self._refresh_preview()
        self._on_image_replaced()
        self._schedule_preview()

    def _generate_blank(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        self._set_generated(blank(*dims), "New blank")

    def _generate_solid(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        rgb = self._pick_color("Fill color", "#808080")
        if rgb is None:
            return
        self._set_generated(solid(dims[0], dims[1], rgb), "New solid")

    def _generate_linear(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        rgb1 = self._pick_color("Gradient start color", "#000000")
        if rgb1 is None:
            return
        rgb2 = self._pick_color("Gradient end color", "#ffffff")
        if rgb2 is None:
            return
        angle, ok = QInputDialog.getDouble(self, "Linear gradient", "Angle (degrees, 0 = left→right):", 0.0, -360.0, 360.0, 1)
        if not ok:
            return
        self._set_generated(linear_gradient(dims[0], dims[1], rgb1, rgb2, angle), "New linear gradient")

    def _generate_radial(self) -> None:
        dims = self._generator_dimensions()
        if dims is None:
            return
        rgb1 = self._pick_color("Gradient center color", "#ffffff")
        if rgb1 is None:
            return
        rgb2 = self._pick_color("Gradient edge color", "#000000")
        if rgb2 is None:
            return
        self._set_generated(radial_gradient(dims[0], dims[1], rgb1, rgb2), "New radial gradient")

    # ------------------------------------------------------ apply / history
    def _apply_effects(self) -> None:
        if self._busy or not self.state.has_image:
            return
        enabled = self._enabled_effects()
        if not enabled:
            self._show_warning("Turn on at least one effect to apply.")
            return
        names = [effect.name for effect, _ in enabled]
        label = ", ".join(names) if len(names) <= 2 else f"{names[0]} +{len(names) - 1}"
        if self.selection is not None:
            label += " · selection"

        worker = RenderWorker(self.state.current, enabled, self.global_seed, self.selection)
        worker.setAutoDelete(False)
        self._render_worker = worker
        self._set_busy("Applying…")

        def on_finished(image: Image.Image, warnings: list[str]) -> None:
            if self._closing:
                return
            self._render_worker = None
            self._set_busy(None)
            self.state.push(image, label)
            self._turn_off_effects()
            self._refresh_preview()
            self._update_history()
            if warnings:
                self._show_warning("; ".join(warnings))

        def on_error(msg: str) -> None:
            if self._closing:
                return
            self._render_worker = None
            self._set_busy(None)
            QMessageBox.critical(self, "Apply failed", msg)

        worker.signals.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(on_error, Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    def _turn_off_effects(self) -> None:
        self._suspend_preview = True
        try:
            for panel in self._panels:
                panel.setChecked(False)
        finally:
            self._suspend_preview = False
        self._update_effect_counts()

    def _undo(self) -> None:
        if self._busy or not self.state.can_undo:
            return
        self.state.undo()
        self._refresh_preview()
        self._update_history()
        self._schedule_preview()

    def _reset(self) -> None:
        if self._busy or not self.state.has_image:
            return
        self.state.reset()
        self._refresh_preview()
        self._update_history()
        self._schedule_preview()

    # --------------------------------------------------------------- export
    def _export(self) -> None:
        self._run_export_dialog(is_jpeg=False)

    def _export_jpeg(self) -> None:
        self._run_export_dialog(is_jpeg=True)

    def _run_export_dialog(self, is_jpeg: bool) -> None:
        if not self.state.has_image:
            QMessageBox.warning(self, "Export", "No image to export.")
            return
        if self._busy:
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
            quality, ok = QInputDialog.getInt(self, "JPEG quality", "Quality (10–100):", 95, 10, 100, 1)
            if not ok:
                return

        self._start_export(path, is_jpeg, quality)

    def _start_export(self, path: str, is_jpeg: bool, quality: int) -> None:
        worker = ExportWorker(
            self.state.current,
            self._enabled_effects(),
            self.global_seed,
            path,
            is_jpeg,
            quality,
            selection=self.selection,
        )
        worker.setAutoDelete(False)
        self._export_worker = worker
        self._set_busy("Exporting…")

        def on_finished(warnings: list[str]) -> None:
            if self._closing:
                return
            self._export_worker = None
            self._set_busy(None)
            if warnings:
                self._show_warning("Export warnings: " + "; ".join(warnings))
            else:
                self._show_info(f"Exported {Path(path).name}")

        def on_error(msg: str) -> None:
            if self._closing:
                return
            self._export_worker = None
            self._set_busy(None)
            QMessageBox.critical(self, "Export failed", msg)

        worker.signals.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(on_error, Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    # ----------------------------------------------------------------- busy
    @property
    def _busy(self) -> bool:
        return self._export_worker is not None or self._render_worker is not None

    def _set_busy(self, text: str | None) -> None:
        """Disable the editor while a full-res job runs; text=None re-enables.

        The Verify tab stays usable."""
        self._edit_page.setEnabled(text is None)
        self.processing_label.setText(text or "")
        if text is None:
            self._update_effect_counts()
            self._update_history()

    def closeEvent(self, event) -> None:
        self._closing = True
        self._preview_timer.stop()
        self.verify_panel.shutdown()
        self._thread_pool.clear()
        for job in (self._export_worker, self._render_worker):
            if job is None:
                continue
            for sig in (job.signals.finished, job.signals.error):
                try:
                    sig.disconnect()
                except Exception:
                    pass
        self._export_worker = None
        self._render_worker = None
        for worker in list(self._active_workers):
            try:
                worker.signals.finished.disconnect()
            except Exception:
                pass
        self._thread_pool.waitForDone(5000)
        event.accept()
