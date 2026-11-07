"""Parameter and effect-panel widgets."""

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from phabrillust.effects.base import Effect, ParamDef

_SPIN_WIDTH = 72


class _WheelGuard(QObject):
    """Ignore wheel events on unfocused controls so scrolling the panel
    doesn't silently change slider/spin/combo values."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QWidget) and not obj.hasFocus():
            event.ignore()
            return True
        return False


_wheel_guard = _WheelGuard()


def guard_wheel(*widgets: QWidget) -> None:
    for w in widgets:
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        w.installEventFilter(_wheel_guard)


def _decimals_for(step: float) -> int:
    text = f"{step:.6f}".rstrip("0")
    return max(1, len(text.split(".")[1])) if "." in text else 1


class ParamWidget(QWidget):
    """Base class for a single parameter control."""

    value_changed = Signal(object)

    def __init__(self, param: ParamDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.param = param
        self._build_ui()
        self.set_value(param.default)

    def _build_ui(self) -> None:
        raise NotImplementedError

    def get_value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError

    def _emit(self) -> None:
        self.value_changed.emit(self.get_value())


class IntParamWidget(ParamWidget):
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(int(self.param.min))
        self.slider.setMaximum(int(self.param.max))
        self.slider.setSingleStep(int(self.param.step or 1))
        self.spin = QSpinBox()
        self.spin.setMinimum(int(self.param.min))
        self.spin.setMaximum(int(self.param.max))
        self.spin.setSingleStep(int(self.param.step or 1))
        self.spin.setFixedWidth(_SPIN_WIDTH)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        guard_wheel(self.slider, self.spin)

        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.spin.valueChanged.connect(self._emit)

        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin, 0)

    def get_value(self) -> int:
        return self.spin.value()

    def set_value(self, value: Any) -> None:
        self.spin.setValue(int(value))


class FloatParamWidget(ParamWidget):
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(10000)
        self.spin = QDoubleSpinBox()
        self.spin.setMinimum(float(self.param.min))
        self.spin.setMaximum(float(self.param.max))
        step = float(self.param.step or 0.1)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(_decimals_for(step))
        self.spin.setFixedWidth(_SPIN_WIDTH)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        guard_wheel(self.slider, self.spin)

        self.slider.valueChanged.connect(self._slider_to_spin)
        self.spin.valueChanged.connect(self._spin_to_slider)
        self.spin.valueChanged.connect(self._emit)

        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin, 0)

    def _slider_to_spin(self, value: int) -> None:
        rng = float(self.param.max) - float(self.param.min)
        fraction = value / 10000.0
        self.spin.setValue(float(self.param.min) + fraction * rng)

    def _spin_to_slider(self, value: float) -> None:
        rng = float(self.param.max) - float(self.param.min)
        if rng == 0:
            fraction = 0
        else:
            fraction = (value - float(self.param.min)) / rng
        self.slider.blockSignals(True)
        self.slider.setValue(int(fraction * 10000))
        self.slider.blockSignals(False)

    def get_value(self) -> float:
        return self.spin.value()

    def set_value(self, value: Any) -> None:
        self.spin.setValue(float(value))


class BoolParamWidget(ParamWidget):
    # The row label comes from EffectPanel's grid; the checkbox itself is unlabeled.
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox()
        self.checkbox.stateChanged.connect(self._emit)
        layout.addWidget(self.checkbox)
        layout.addStretch(1)

    def get_value(self) -> bool:
        return self.checkbox.isChecked()

    def set_value(self, value: Any) -> None:
        self.checkbox.setChecked(bool(value))


class ChoiceParamWidget(ParamWidget):
    # The row label comes from EffectPanel's grid; the combo itself is unlabeled.
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.addItems(list(self.param.choices))
        self.combo.currentTextChanged.connect(lambda _: self._emit())
        guard_wheel(self.combo)
        layout.addWidget(self.combo, 1)

    def get_value(self) -> str:
        return self.combo.currentText()

    def set_value(self, value: Any) -> None:
        idx = self.combo.findText(str(value))
        if idx >= 0:
            self.combo.setCurrentIndex(idx)


def _build_param_widget(param: ParamDef) -> ParamWidget:
    if param.type == "int":
        return IntParamWidget(param)
    if param.type == "float":
        return FloatParamWidget(param)
    if param.type == "bool":
        return BoolParamWidget(param)
    if param.type == "choice":
        return ChoiceParamWidget(param)
    raise ValueError(f"Unknown param type: {param.type}")


class EffectPanel(QGroupBox):
    """Panel for one effect: the title checkbox enables it; parameters are
    shown only while it is enabled, keeping the effect list short."""

    configuration_changed = Signal()

    def __init__(self, effect: Effect, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.effect = effect
        self._param_widgets: dict[str, ParamWidget] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        self.setTitle(self.effect.name.upper().replace("&", "&&"))  # "&" is a mnemonic marker
        self.setObjectName("effect-panel")
        self.setCheckable(True)
        self.setChecked(False)
        if self.effect.description:
            self.setToolTip(self.effect.description)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(10, 14, 10, 6)

        self._body = QWidget()
        grid = QGridLayout(self._body)
        grid.setContentsMargins(0, 6, 0, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        for row, param in enumerate(self.effect.params):
            label = QLabel(param.label)
            label.setObjectName("param-label")
            widget = _build_param_widget(param)
            widget.value_changed.connect(self.configuration_changed)
            self._param_widgets[param.name] = widget
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        layout.addWidget(self._body)
        self._body.setVisible(False)

        self.toggled.connect(self._body.setVisible)
        self.toggled.connect(self.configuration_changed)

    def is_enabled(self) -> bool:
        return self.isChecked()

    def get_params(self) -> dict[str, Any]:
        return {name: widget.get_value() for name, widget in self._param_widgets.items()}

    def set_params(self, params: dict[str, Any]) -> None:
        for name, value in params.items():
            if name in self._param_widgets:
                self._param_widgets[name].set_value(value)

    def reset_params(self) -> None:
        for param in self.effect.params:
            self._param_widgets[param.name].set_value(param.default)
