"""Preview canvas: shows the image and lets the user draw a selection."""

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from phabrillust.engine.selection import Selection
from phabrillust.gui.renderer import pil_to_qpixmap

_DIM = QColor(0, 0, 0, 110)  # over the area effects will NOT touch
_MIN_DRAG_PX = 4


class Canvas(QLabel):
    """Scales and centers a pixmap; draws/edits a selection on top of it.

    Emits ``selection_drawn(Selection | None)`` when the user finishes a drag
    (None = a click without a drag, which clears the selection). Inversion and
    feathering are decided by the owner, which hands the final selection back
    through ``set_selection`` for display.
    """

    selection_drawn = Signal(object)
    resized = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("preview-canvas")
        self.setMouseTracking(False)
        self._pixmap: QPixmap | None = None
        self._base_size: tuple[int, int] | None = None
        self._displayed_size: tuple[int, int] | None = None

        self._tool: str | None = None
        self._selection: Selection | None = None
        self._drag: list[QPointF] = []  # widget coords while drawing
        self._drag_shift = False
        self._ants_offset = 0
        self._ants = QTimer(self)
        self._ants.timeout.connect(self._tick_ants)

    # -- image ---------------------------------------------------------
    def set_pil_image(self, image: Image.Image | None) -> None:
        if image is None:
            self._pixmap = None
            self._base_size = None
            self._displayed_size = None
            self.clear()
            self.setText("no image — open one (Ctrl+O) or drop a file here")
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
        self.resized.emit()

    def image_rect(self) -> QRectF | None:
        """Where the displayed image sits, in widget coordinates."""
        if self._displayed_size is None:
            return None
        dw, dh = self._displayed_size
        c = self.contentsRect()
        return QRectF(c.x() + (c.width() - dw) / 2, c.y() + (c.height() - dh) / 2, dw, dh)

    # -- selection API -------------------------------------------------
    def set_tool(self, tool: str | None) -> None:
        self._tool = tool
        self._drag = []
        self.setCursor(Qt.CursorShape.CrossCursor if tool else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_selection(self, selection: Selection | None) -> None:
        self._selection = selection
        if selection is None:
            self._ants.stop()
        elif not self._ants.isActive():
            self._ants.start(150)
        self.update()

    def cancel_drawing(self) -> bool:
        if not self._drag:
            return False
        self._drag = []
        self.update()
        return True

    def _tick_ants(self) -> None:
        self._ants_offset = (self._ants_offset + 1) % 8
        self.update()

    # -- mouse ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        r = self.image_rect()
        if self._tool and r is not None and event.button() == Qt.MouseButton.LeftButton:
            p = self._clamp(event.position(), r)
            self._drag = [p, p]
            self._drag_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        r = self.image_rect()
        if not self._drag or r is None:
            return super().mouseMoveEvent(event)
        p = self._clamp(event.position(), r)
        self._drag_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._tool == "lasso":
            last = self._drag[-1]
            if abs(p.x() - last.x()) + abs(p.y() - last.y()) >= 2:
                self._drag.append(p)
        else:
            self._drag[1] = p
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        r = self.image_rect()
        if not self._drag or r is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        points = self._drag_points()
        self._drag = []
        self.update()
        self.selection_drawn.emit(self._to_selection(points, r))

    def _drag_points(self) -> list[QPointF]:
        if self._tool == "lasso" or len(self._drag) < 2:
            return list(self._drag)
        a, b = self._drag[0], self._drag[1]
        if self._drag_shift:  # constrain to square / circle
            side = max(abs(b.x() - a.x()), abs(b.y() - a.y()))
            b = QPointF(a.x() + side * (1 if b.x() >= a.x() else -1), a.y() + side * (1 if b.y() >= a.y() else -1))
            r = self.image_rect()
            if r is not None:
                b = self._clamp(b, r)
        return [a, b]

    def _to_selection(self, points: list[QPointF], r: QRectF) -> Selection | None:
        xs = [p.x() for p in points]
        ys = [p.y() for p in points]
        if max(xs) - min(xs) < _MIN_DRAG_PX or max(ys) - min(ys) < _MIN_DRAG_PX:
            return None
        if self._tool == "lasso" and len(points) < 3:
            return None
        norm = tuple(((p.x() - r.x()) / r.width(), (p.y() - r.y()) / r.height()) for p in points)
        return Selection(self._tool, norm)

    @staticmethod
    def _clamp(p: QPointF, r: QRectF) -> QPointF:
        return QPointF(min(max(p.x(), r.left()), r.right()), min(max(p.y(), r.top()), r.bottom()))

    # -- painting ------------------------------------------------------
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        r = self.image_rect()
        if r is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drag:
            path = self._path(self._tool, self._drag_points(), closed=self._tool != "lasso")
            self._draw_outline(painter, path)
        elif self._selection is not None:
            sel = self._selection
            pts = [QPointF(r.x() + x * r.width(), r.y() + y * r.height()) for x, y in sel.points]
            shape = self._path(sel.shape, pts, closed=True)
            dim = QPainterPath()
            if sel.inverted:
                dim = shape
            else:
                dim.addRect(r)
                dim.addPath(shape)
                dim.setFillRule(Qt.FillRule.OddEvenFill)
            painter.fillPath(dim, _DIM)
            self._draw_outline(painter, shape)
        painter.end()

    @staticmethod
    def _path(shape: str | None, pts: list[QPointF], closed: bool) -> QPainterPath:
        path = QPainterPath()
        if len(pts) < 2:
            return path
        if shape == "lasso":
            poly = QPolygonF(pts)
            if closed:
                poly.append(pts[0])
            path.addPolygon(poly)
        else:
            rect = QRectF(pts[0], pts[1]).normalized()
            if shape == "ellipse":
                path.addEllipse(rect)
            else:
                path.addRect(rect)
        return path

    def _draw_outline(self, painter: QPainter, path: QPainterPath) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawPath(path)
        ants = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.CustomDashLine)
        ants.setDashPattern([4, 4])
        ants.setDashOffset(self._ants_offset)
        painter.setPen(ants)
        painter.drawPath(path)
