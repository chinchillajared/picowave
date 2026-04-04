from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap

from picowave.config import (
    ICON_DIR,
    ICON_FILES,
    TRIGGER_EDGE_DIRECTIONS,
)

if TYPE_CHECKING:
    from picowave.models import TriggerState


_VERTICAL_OFFSET_CURSOR: QCursor | None = None


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def format_with_unit(value: float, units: tuple[tuple[float, str], ...]) -> str:
    for scale, suffix in units:
        if abs(value) >= scale or scale == units[-1][0]:
            return f"{value / scale:.3g} {suffix}"
    return f"{value:.3g}"


def format_time_value(seconds: float) -> str:
    for scale, suffix in ((1.0, "s"), (1e-3, "ms"), (1e-6, "us"), (1e-9, "ns")):
        if abs(seconds) >= scale or scale == 1e-9:
            scaled = seconds / scale
            rounded = round(scaled)
            if math.isclose(scaled, rounded, rel_tol=1e-12, abs_tol=1e-12):
                return f"{int(rounded)} {suffix}"
            if abs(scaled) >= 100:
                formatted = f"{scaled:.1f}".rstrip("0").rstrip(".")
                return f"{formatted} {suffix}"
            return f"{scaled:.3g} {suffix}"
    return f"{seconds:.3g} s"


def format_time_per_div(seconds: float) -> str:
    return f"{format_time_value(seconds)} /div"


def timebase_unit_group(seconds: float) -> str:
    if seconds < 1e-6:
        return "ns /div"
    if seconds < 1e-3:
        return "us /div"
    if seconds < 1.0:
        return "ms /div"
    return "s /div"


def format_sample_rate(hz: float) -> str:
    return format_with_unit(hz, ((1e6, "MS/s"), (1e3, "kS/s"), (1.0, "S/s")))


def format_voltage(volts: float) -> str:
    if volts >= 1.0:
        text = f"{volts:g} V"
    else:
        text = f"{volts * 1000:g} mV"
    return f"+{text}"


def format_live_voltage(volts: float) -> str:
    abs_volts = abs(volts)
    if abs_volts >= 1.0:
        text = f"{abs_volts:.3g}V"
    else:
        text = f"{abs_volts * 1000:.3g}mV"
    return f"-{text}" if volts < 0 else text


def format_probe_scale(scale: int) -> str:
    return f"x{scale}"


def trigger_direction_options(trigger_type: str) -> list[str]:
    return TRIGGER_EDGE_DIRECTIONS


def format_trigger_summary(trigger: TriggerState) -> str:
    if trigger.mode == "None":
        return "None"
    return f"{trigger.mode} {trigger.trigger_type}"


def display_trigger_level(trigger: TriggerState) -> float:
    return trigger.level_volts


def icon_path(name: str) -> str:
    filename = ICON_FILES.get(name)
    if not filename:
        return ""
    path = os.path.join(ICON_DIR, filename)
    return path if os.path.exists(path) else ""


def load_icon(name: str) -> QIcon:
    path = icon_path(name)
    return QIcon(path) if path else QIcon()


def icon_pixmap(name: str, size: int = 14) -> QPixmap:
    icon = load_icon(name)
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(QSize(size, size))


def disabled_icon(name: str, size: int = 16) -> QIcon:
    pixmap = icon_pixmap(name, size)
    if pixmap.isNull():
        return QIcon()
    muted = QPixmap(pixmap.size())
    muted.fill(Qt.transparent)
    painter = QPainter(muted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(muted.rect(), QColor("#9ca3af"))
    painter.end()
    return QIcon(muted)


def decode_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 ")
    return str(value)


def vertical_offset_cursor() -> QCursor:
    global _VERTICAL_OFFSET_CURSOR
    if _VERTICAL_OFFSET_CURSOR is not None:
        return _VERTICAL_OFFSET_CURSOR

    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor("#111111"), 1.6, Qt.DashLine)
    painter.setPen(pen)
    painter.drawLine(QPointF(10, 3), QPointF(10, 17))
    painter.drawLine(QPointF(10, 3), QPointF(6, 7))
    painter.drawLine(QPointF(10, 3), QPointF(14, 7))
    painter.drawLine(QPointF(10, 17), QPointF(6, 13))
    painter.drawLine(QPointF(10, 17), QPointF(14, 13))
    painter.end()
    _VERTICAL_OFFSET_CURSOR = QCursor(pixmap, 10, 10)
    return _VERTICAL_OFFSET_CURSOR
