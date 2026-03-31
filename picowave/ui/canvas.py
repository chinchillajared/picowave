from __future__ import annotations

import copy
import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QLineF, QMargins, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from picowave.config import *
from picowave.helpers import *
from picowave.models import *
from picowave.processing import *
class ZoomOverviewWidget(QWidget):
    def __init__(self, canvas: "WaveformCanvas") -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.setObjectName("zoomOverviewWidget")
        self.setMinimumSize(120, 70)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#b9dcff"), 1.0, Qt.DashLine))

        for index in range(9):
            x = rect.left() + (rect.width() * index / 8.0)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for index in range(7):
            y = rect.top() + (rect.height() * index / 6.0)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        frame = self.canvas.frame
        if frame is not None and frame.times.size > 1:
            self._draw_preview_trace(painter, rect, frame.times, frame.channel_a, self.canvas.state.channel_a)
            self._draw_preview_trace(painter, rect, frame.times, frame.channel_b, self.canvas.state.channel_b)

        left = rect.left() + (rect.width() * self.canvas._view_start_ratio)
        right = rect.left() + (rect.width() * self.canvas._view_end_ratio)
        view_rect = QRectF(left, rect.top() + 20, max(right - left, 6), max(rect.height() - 40, 16))
        painter.setPen(QPen(QColor("#8a949e"), 1.0))
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRect(view_rect)

    def _draw_preview_trace(
        self,
        painter: QPainter,
        rect: QRectF,
        times: np.ndarray,
        volts: np.ndarray,
        channel_state: ChannelState,
    ) -> None:
        if not channel_state.enabled or volts.size == 0 or times.size <= 1:
            return
        sample_step = max(1, int(math.ceil(times.size / 180)))
        times_view = times[::sample_step]
        volts_view = volts[::sample_step]
        time_start = float(times[0])
        time_span = max(float(times[-1] - times[0]), 1e-9)
        visible_range = max(channel_visible_range(channel_state), 1e-6)

        path = QPainterPath()
        for index, (time_value, voltage) in enumerate(zip(times_view, volts_view)):
            x_ratio = float((time_value - time_start) / time_span)
            y_ratio = 0.5 - float(voltage / (4.0 * visible_range)) - (channel_state.vertical_offset_divs / 20.0)
            x = rect.left() + x_ratio * rect.width()
            y = rect.top() + clamp(y_ratio, 0.0, 1.0) * rect.height()
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor(channel_state.color_hex), 1.0))
        painter.drawPath(path)


# ============================================================================
# Frontend: waveform rendering
# ============================================================================


class WaveformCanvas(QWidget):
    annotation_button_clicked = Signal()
    annotation_interaction_started = Signal()
    vertical_offset_changed = Signal(str, float)
    channel_display_zoom_changed = Signal(str, float)
    zoom_box_mode_changed = Signal(bool)
    trigger_level_changed = Signal(float)
    trigger_pre_trigger_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("waveformCanvas")
        self.setMouseTracking(True)
        self.frame: Optional[CaptureFrame] = None
        self.state = ScopeState()
        self.annotation_settings = AnnotationSettings()
        self.waveform_annotations: list[AnnotationStroke | AnnotationText] = []
        self.global_annotations: list[AnnotationStroke | AnnotationText] = []
        self._active_stroke: list[tuple[float, float]] | None = None
        self._active_stroke_plot_rect: QRectF | None = None
        self._active_stroke_press_global: QPointF | None = None
        self._active_stroke_press_local: QPointF | None = None
        self._active_stroke_has_moved = False
        self._pending_pen_start_after_hide = False
        self._annotation_panel_open = False
        self._active_text_box: AnnotationText | None = None
        self._active_text_scope: str | None = None
        self._dragging_vertical_offset: str | None = None
        self._dragging_vertical_plot_rect: QRectF | None = None
        self._dragging_trigger_marker = False
        self._channel_draw_order: list[str] = ["B", "A"]
        self._view_start_ratio = 0.0
        self._view_end_ratio = 1.0
        self._panning_view = False
        self._pan_button = Qt.MouseButton.LeftButton
        self._pan_press_x = 0.0
        self._pan_press_y = 0.0
        self._pan_start_range = (0.0, 1.0)
        self._pan_start_offsets: dict[str, float] = {"A": 0.0, "B": 0.0}
        self._zoom_box_mode = False
        self._zoom_box_start: QPointF | None = None
        self._zoom_box_end: QPointF | None = None
        self._custom_channel_cache: tuple[np.ndarray, ChannelState] | None = None
        self.annotation_button = QPushButton(self)
        self.annotation_button.setObjectName("annotationCanvasButton")
        self.annotation_button.setCursor(Qt.PointingHandCursor)
        self.annotation_button.setIcon(load_icon("annotate"))
        self.annotation_button.setIconSize(QSize(14, 14))
        self.annotation_button.setToolTip("Annotations")
        self.annotation_button.clicked.connect(self.annotation_button_clicked.emit)
        self.zoom_button = QPushButton(self)
        self.zoom_button.setObjectName("zoomCanvasButton")
        self.zoom_button.setCursor(Qt.PointingHandCursor)
        self.zoom_button.setIcon(load_icon("zoom"))
        self.zoom_button.setIconSize(QSize(14, 14))
        self.zoom_button.setToolTip("Zoom window")
        self.zoom_button.clicked.connect(self.toggle_zoom_box_mode)
        self.zoom_status_panel = QFrame(self)
        self.zoom_status_panel.setObjectName("zoomStatusPanel")
        zoom_status_layout = QVBoxLayout(self.zoom_status_panel)
        zoom_status_layout.setContentsMargins(0, 0, 0, 0)
        zoom_status_layout.setSpacing(0)
        self.zoom_title_label = QLabel("Zoom")
        self.zoom_title_label.setObjectName("zoomPanelTitle")
        self.zoom_title_label.setFixedHeight(16)
        zoom_status_layout.addWidget(self.zoom_title_label)

        zoom_content = QFrame()
        zoom_content.setObjectName("zoomStatusContent")
        zoom_content_layout = QVBoxLayout(zoom_content)
        zoom_content_layout.setContentsMargins(8, 8, 8, 8)
        zoom_content_layout.setSpacing(6)

        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(6)
        self.zoom_overview = ZoomOverviewWidget(self)
        preview_row.addWidget(self.zoom_overview, 1)
        zoom_content_layout.addLayout(preview_row)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)
        self.zoom_reset_button = QPushButton("Reset")
        self.zoom_reset_button.setObjectName("zoomResetButton")
        self.zoom_reset_button.setCursor(Qt.PointingHandCursor)
        self.zoom_reset_button.clicked.connect(self._reset_all_zoom)
        controls_row.addStretch(1)
        controls_row.addWidget(self.zoom_reset_button)
        controls_row.addStretch(1)
        zoom_content_layout.addLayout(controls_row)
        zoom_status_layout.addWidget(zoom_content)
        self.zoom_status_panel.hide()
        self.setMinimumHeight(620)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self._position_overlay_buttons()
        self._refresh_zoom_status_panel()

    def _invalidate_custom_channel_cache(self) -> None:
        self._custom_channel_cache = None

    def set_frame(self, frame: CaptureFrame) -> None:
        self.frame = frame
        self._invalidate_custom_channel_cache()
        self.zoom_overview.update()
        self.update()

    def set_state(self, state: ScopeState) -> None:
        self.state = copy.deepcopy(state)
        self._invalidate_custom_channel_cache()
        self._normalize_channel_draw_order()
        self._refresh_zoom_status_panel()
        self.zoom_overview.update()
        self.update()

    def set_annotation_settings(self, settings: AnnotationSettings) -> None:
        self.annotation_settings = copy.deepcopy(settings)
        self._apply_annotation_cursor()
        self.update()

    def set_annotations(
        self,
        waveform_annotations: list[AnnotationStroke | AnnotationText],
        global_annotations: list[AnnotationStroke | AnnotationText],
    ) -> None:
        self.waveform_annotations = waveform_annotations
        self.global_annotations = global_annotations
        self.update()

    def set_annotation_button_active(self, active: bool) -> None:
        self.annotation_button.setProperty("active", active)
        self.annotation_button.style().unpolish(self.annotation_button)
        self.annotation_button.style().polish(self.annotation_button)

    def set_annotation_panel_open(self, is_open: bool) -> None:
        self._annotation_panel_open = is_open

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_overlay_buttons()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._reset_canvas_cursor()
        super().leaveEvent(event)

    def _position_overlay_buttons(self) -> None:
        plot_rect = self._plot_rect()
        self.annotation_button.setGeometry(int(plot_rect.left() + 34), int(plot_rect.top() + 6), 26, 26)
        self.zoom_button.setGeometry(int(plot_rect.left() + 66), int(plot_rect.top() + 6), 26, 26)
        self.zoom_status_panel.setGeometry(int(plot_rect.left() + 6), int(plot_rect.bottom() - 148), 222, 142)

    def toggle_zoom_box_mode(self) -> None:
        self._zoom_box_mode = not self._zoom_box_mode
        if not self._zoom_box_mode:
            self._zoom_box_start = None
            self._zoom_box_end = None
        self.zoom_button.setProperty("active", self._zoom_box_mode)
        self.zoom_button.style().unpolish(self.zoom_button)
        self.zoom_button.style().polish(self.zoom_button)
        self._reset_canvas_cursor()
        self.zoom_box_mode_changed.emit(self._zoom_box_mode)
        self.update()

    def set_zoom_box_mode(self, active: bool) -> None:
        if self._zoom_box_mode == active:
            return
        self._zoom_box_mode = active
        if not self._zoom_box_mode:
            self._zoom_box_start = None
            self._zoom_box_end = None
        self.zoom_button.setProperty("active", self._zoom_box_mode)
        self.zoom_button.style().unpolish(self.zoom_button)
        self.zoom_button.style().polish(self.zoom_button)
        self._reset_canvas_cursor()
        self.zoom_box_mode_changed.emit(self._zoom_box_mode)
        self.update()

    def _apply_annotation_cursor(self) -> None:
        tool = self.annotation_settings.tool
        if tool == "Off":
            self.unsetCursor()
            return
        if tool == "Text":
            self.setCursor(Qt.IBeamCursor)
            return
        if tool in ("Pen", "Eraser"):
            icon = load_icon(tool)
            if not icon.isNull():
                pixmap = icon.pixmap(QSize(20, 20))
                self.setCursor(QCursor(pixmap, 2, 18))
                return
            self.setCursor(Qt.CrossCursor)
            return
        self.unsetCursor()

    def _has_active_zoom(self) -> bool:
        if (self._view_end_ratio - self._view_start_ratio) < 0.999:
            return True
        for channel in (self.state.channel_a, self.state.channel_b):
            if channel.enabled and not math.isclose(channel.display_zoom, 1.0, rel_tol=1e-9, abs_tol=1e-9):
                return True
        if self.state.custom_channel.enabled and not math.isclose(
            self.state.custom_channel.vertical_offset_divs, 0.0, rel_tol=1e-9, abs_tol=1e-9
        ):
            return True
        return False

    def _horizontal_zoom_factor(self) -> float:
        span_ratio = max(self._view_end_ratio - self._view_start_ratio, 1e-9)
        return 1.0 / span_ratio

    def _refresh_zoom_status_panel(self) -> None:
        self.zoom_status_panel.setVisible(self._has_active_zoom())
        self.zoom_overview.update()

    def _reset_all_zoom(self) -> None:
        self._view_start_ratio = 0.0
        self._view_end_ratio = 1.0
        for name, channel in (("A", self.state.channel_a), ("B", self.state.channel_b)):
            if not math.isclose(channel.display_zoom, 1.0, rel_tol=1e-9, abs_tol=1e-9):
                channel.display_zoom = 1.0
                self.channel_display_zoom_changed.emit(name, 1.0)
            if not math.isclose(channel.vertical_offset_divs, 0.0, rel_tol=1e-9, abs_tol=1e-9):
                channel.vertical_offset_divs = 0.0
                self.vertical_offset_changed.emit(name, 0.0)
        if not math.isclose(self.state.custom_channel.vertical_offset_divs, 0.0, rel_tol=1e-9, abs_tol=1e-9):
            self.state.custom_channel.vertical_offset_divs = 0.0
            self.vertical_offset_changed.emit("Custom", 0.0)
        self._refresh_zoom_status_panel()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        plot_rect = self._plot_rect()
        if plot_rect.width() <= 0 or plot_rect.height() <= 0:
            return

        self._draw_grid(painter, plot_rect)
        self._draw_axes_labels(painter, plot_rect)
        self._draw_annotation_strokes(painter, plot_rect)
        self._draw_waveform(painter, plot_rect)
        self._draw_overrange_badges(painter, plot_rect)
        self._draw_trigger_marker(painter, plot_rect)
        self._draw_annotation_overlays(painter, plot_rect)
        self._draw_zoom_box_overlay(painter, plot_rect)

    def _plot_rect(self) -> QRectF:
        margin = QMargins(42, 18, 42, 30)
        return QRectF(self.rect().marginsRemoved(margin))

    def _full_time_window(self) -> tuple[float, float]:
        if self.frame is None or self.frame.times.size == 0:
            total_span = self.state.time_per_div * 10.0
            return 0.0, total_span
        return float(self.frame.times[0]), float(self.frame.times[-1])

    def _visible_time_window(self) -> tuple[float, float]:
        full_start, full_end = self._full_time_window()
        full_span = max(full_end - full_start, 1e-9)
        start = full_start + (full_span * self._view_start_ratio)
        end = full_start + (full_span * self._view_end_ratio)
        return start, max(end, start + 1e-9)

    def _set_view_range(self, start_ratio: float, end_ratio: float) -> None:
        start = clamp(start_ratio, 0.0, 0.999)
        end = clamp(end_ratio, start + 0.001, 1.0)
        if math.isclose(self._view_start_ratio, start, rel_tol=1e-9, abs_tol=1e-9) and math.isclose(
            self._view_end_ratio, end, rel_tol=1e-9, abs_tol=1e-9
        ):
            return
        self._view_start_ratio = start
        self._view_end_ratio = end
        self._refresh_zoom_status_panel()
        self.update()

    def _zoom_horizontal(self, cursor_ratio: float, zoom_in: bool) -> None:
        current_span = self._view_end_ratio - self._view_start_ratio
        if not zoom_in and current_span >= 0.999:
            self._reset_all_zoom()
            return
        factor = 0.8 if zoom_in else 1.6
        new_span = clamp(current_span * factor, 0.02, 1.0)
        full_cursor_ratio = self._view_start_ratio + (current_span * clamp(cursor_ratio, 0.0, 1.0))
        new_start = full_cursor_ratio - (new_span * clamp(cursor_ratio, 0.0, 1.0))
        new_end = new_start + new_span
        if new_start < 0.0:
            new_end -= new_start
            new_start = 0.0
        if new_end > 1.0:
            new_start -= (new_end - 1.0)
            new_end = 1.0
        if not zoom_in and new_span >= 0.999:
            new_start = 0.0
            new_end = 1.0
        self._set_view_range(new_start, new_end)

    def _adjust_channel_display_zoom(self, name: str, step: int) -> None:
        if name == "Custom":
            source_name = self.state.custom_channel.source_channel
            if self._channel_state(source_name) is None:
                return
            self._adjust_channel_display_zoom(source_name, step)
            return
        channel = self._channel_state(name)
        if channel is None:
            return
        factor = 1.25 if step > 0 else 0.8
        new_zoom = float(clamp(channel.display_zoom * factor, 0.25, 20.0))
        if math.isclose(new_zoom, channel.display_zoom, rel_tol=1e-9, abs_tol=1e-9):
            return
        channel.display_zoom = new_zoom
        self.channel_display_zoom_changed.emit(name, new_zoom)
        self._refresh_zoom_status_panel()
        self.update()

    def _channel_voltage_from_y_ratio(self, y_ratio: float, channel_state: ChannelState) -> float:
        y_range = max(channel_visible_range(channel_state), 1e-6)
        return (0.5 - (channel_state.vertical_offset_divs / 10.0) - y_ratio) * 2.0 * y_range

    def _apply_zoom_box(self, zoom_rect: QRectF, plot_rect: QRectF) -> None:
        normalized = zoom_rect.normalized().intersected(plot_rect)
        if normalized.width() < 12 or normalized.height() < 12:
            return
        start_ratio = clamp((normalized.left() - plot_rect.left()) / max(plot_rect.width(), 1.0), 0.0, 1.0)
        end_ratio = clamp((normalized.right() - plot_rect.left()) / max(plot_rect.width(), 1.0), 0.0, 1.0)
        current_span = self._view_end_ratio - self._view_start_ratio
        full_start = self._view_start_ratio + (current_span * start_ratio)
        full_end = self._view_start_ratio + (current_span * end_ratio)
        self._set_view_range(full_start, full_end)

        top_ratio = clamp((normalized.top() - plot_rect.top()) / max(plot_rect.height(), 1.0), 0.0, 1.0)
        bottom_ratio = clamp((normalized.bottom() - plot_rect.top()) / max(plot_rect.height(), 1.0), 0.0, 1.0)
        for name in ("A", "B"):
            channel = self._channel_state(name)
            if channel is None:
                continue
            top_voltage = self._channel_voltage_from_y_ratio(top_ratio, channel)
            bottom_voltage = self._channel_voltage_from_y_ratio(bottom_ratio, channel)
            new_visible_range = max(abs(top_voltage - bottom_voltage) / 2.0, 1e-6)
            new_zoom = float(clamp(channel.range_volts / new_visible_range, 0.25, 20.0))
            actual_visible_range = max(channel.range_volts / new_zoom, 1e-6)
            center_voltage = (top_voltage + bottom_voltage) / 2.0
            new_offset = float(clamp(-(center_voltage / (2.0 * actual_visible_range)) * 10.0, -5.0, 5.0))
            channel.display_zoom = new_zoom
            channel.vertical_offset_divs = new_offset
            self.channel_display_zoom_changed.emit(name, new_zoom)
            self.vertical_offset_changed.emit(name, new_offset)
        self._refresh_zoom_status_panel()

    def _reset_zoom(self, *, axis_name: str | None = None) -> None:
        if axis_name is None:
            self._view_start_ratio = 0.0
            self._view_end_ratio = 1.0
        else:
            if axis_name == "Custom":
                self.state.custom_channel.vertical_offset_divs = 0.0
                self.vertical_offset_changed.emit("Custom", 0.0)
                self._refresh_zoom_status_panel()
                self.update()
                return
            channel = self._channel_state(axis_name)
            if channel is not None:
                channel.display_zoom = 1.0
                self.channel_display_zoom_changed.emit(axis_name, 1.0)
        self._refresh_zoom_status_panel()
        self.update()

    def _draw_grid(self, painter: QPainter, plot_rect: QRectF) -> None:
        grid_pen = QPen(QColor("#b9dcff"), 1.0, Qt.DashLine)
        painter.setPen(grid_pen)

        vertical_divisions = 10
        horizontal_divisions = 10
        for index in range(vertical_divisions + 1):
            x = plot_rect.left() + (plot_rect.width() * index / vertical_divisions)
            painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))
        for index in range(horizontal_divisions + 1):
            y = plot_rect.top() + (plot_rect.height() * index / horizontal_divisions)
            painter.drawLine(QPointF(plot_rect.left(), y), QPointF(plot_rect.right(), y))

    def _draw_axes_labels(self, painter: QPainter, plot_rect: QRectF) -> None:
        font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(font)
        custom_axis = self._custom_axis_descriptor()

        if self.state.channel_a.enabled and self._should_draw_source_trace("A"):
            self._draw_channel_axis_labels(painter, plot_rect, self.state.channel_a, side="left")
        if self.state.channel_b.enabled and self._should_draw_source_trace("B"):
            self._draw_channel_axis_labels(painter, plot_rect, self.state.channel_b, side="right")
        if (
            not (self.state.channel_a.enabled and self._should_draw_source_trace("A"))
            and not (self.state.channel_b.enabled and self._should_draw_source_trace("B"))
            and custom_axis is None
        ):
            self._draw_channel_axis_labels(painter, plot_rect, self.state.channel_a, side="left")
        if custom_axis is not None:
            side, axis_channel, color_hex = custom_axis
            self._draw_channel_axis_labels(
                painter,
                plot_rect,
                axis_channel,
                side=side,
                override_color_hex=color_hex,
                secondary=True,
            )

        visible_start, visible_end = self._visible_time_window()
        total_span = max(visible_end - visible_start, 1e-9)
        display_scale = 1e3 if total_span < 1 else 1.0
        suffix = "ms" if total_span < 1 else "s"
        painter.setPen(QPen(QColor("#111827")))
        for index in range(11):
            value = (visible_start + (total_span * index / 10.0)) * display_scale
            x = plot_rect.left() + (plot_rect.width() * index / 10.0)
            text = f"{value:.1f}"
            if index == 0:
                text += f" {suffix}"
            if index == 0:
                painter.drawText(QRectF(x + 4, plot_rect.bottom() + 2, 48, 18), Qt.AlignLeft | Qt.AlignTop, text)
            elif index == 10:
                painter.drawText(QRectF(x - 52, plot_rect.bottom() + 2, 48, 18), Qt.AlignRight | Qt.AlignTop, text)
            else:
                painter.drawText(QRectF(x - 24, plot_rect.bottom() + 2, 48, 18), Qt.AlignHCenter | Qt.AlignTop, text)

    def _draw_channel_axis_labels(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        channel_state: ChannelState,
        *,
        side: str,
        override_color_hex: str | None = None,
        secondary: bool = False,
    ) -> None:
        color = QColor(override_color_hex or channel_state.color_hex)
        painter.setPen(QPen(color))

        for index in range(11):
            value = self._channel_axis_value(index, channel_state)
            y = plot_rect.top() + (plot_rect.height() * index / 10.0)
            label = f"{value:.1f} V" if index == 0 else f"{value:.1f}"
            if side == "left":
                if secondary:
                    secondary_y = y - (3.0 if index == 0 else 15.0 if index == 10 else 9.0)
                    rect = QRectF(plot_rect.left() + 2.0, secondary_y, 50.0, 18.0)
                    align = Qt.AlignLeft | Qt.AlignVCenter
                else:
                    rect = QRectF(0, y - 9, plot_rect.left() - 2, 18)
                    align = Qt.AlignRight | Qt.AlignVCenter
                painter.drawText(rect, align, label)
            else:
                if secondary:
                    secondary_y = y - (3.0 if index == 0 else 15.0 if index == 10 else 9.0)
                    rect = QRectF(plot_rect.right() - 52.0, secondary_y, 50.0, 18.0)
                    align = Qt.AlignRight | Qt.AlignVCenter
                else:
                    rect = QRectF(plot_rect.right() + 4, y - 9, self.width() - plot_rect.right() - 4, 18)
                    align = Qt.AlignLeft | Qt.AlignVCenter
                painter.drawText(
                    rect,
                    align,
                    label,
                )

    def _custom_display_channel(self) -> tuple[ChannelState, str] | None:
        custom = self.state.custom_channel
        if not custom.enabled or custom.operation != "Signal smoother":
            return None
        source_name = custom.source_channel
        source_channel = self._channel_state(source_name)
        if source_channel is None:
            return None
        display_channel = copy.deepcopy(source_channel)
        display_channel.name = "Custom"
        display_channel.color_hex = custom.color_hex
        display_channel.vertical_offset_divs = custom.vertical_offset_divs
        return display_channel, source_name

    def _custom_axis_descriptor(self) -> tuple[str, ChannelState, str] | None:
        custom_payload = self._custom_display_channel()
        if custom_payload is None:
            return None
        display_channel, source_name = custom_payload
        side = "left" if source_name == "A" else "right"
        return side, display_channel, self.state.custom_channel.color_hex

    def _channel_axis_value(self, index: int, channel_state: ChannelState) -> float:
        y_range = max(channel_visible_range(channel_state), 1e-6)
        y_ratio = index / 10.0
        return (0.5 - (channel_state.vertical_offset_divs / 10.0) - y_ratio) * 2.0 * y_range

    def _draw_waveform(self, painter: QPainter, plot_rect: QRectF) -> None:
        if self.frame is None or self.frame.times.size == 0:
            return
        frame_map = {
            "A": self.frame.channel_a,
            "B": self.frame.channel_b,
        }
        overrange_map = {
            "A": self.frame.channel_a_overrange,
            "B": self.frame.channel_b_overrange,
        }
        channel_map = {
            "A": self.state.channel_a,
            "B": self.state.channel_b,
        }
        for name in self._channel_draw_order:
            if not self._should_draw_source_trace(name):
                continue
            self._draw_channel_trace(
                painter,
                plot_rect,
                frame_map[name],
                overrange_map[name],
                channel_map[name],
            )
        self._draw_custom_channel_trace(painter, plot_rect)

    def _visible_overrange_channels(self) -> list[str]:
        if self.frame is None or self.frame.times.size == 0:
            return []

        view_start, view_end = self._visible_time_window()
        start_index = max(int(np.searchsorted(self.frame.times, view_start, side="left")) - 1, 0)
        end_index = min(int(np.searchsorted(self.frame.times, view_end, side="right")) + 1, self.frame.times.size)
        visible_channels: list[str] = []
        for name, channel, overrange in (
            ("A", self.state.channel_a, self.frame.channel_a_overrange),
            ("B", self.state.channel_b, self.frame.channel_b_overrange),
        ):
            if not channel.enabled or not self._should_draw_source_trace(name):
                continue
            if overrange.size == 0:
                continue
            if np.any(overrange[start_index:end_index] != 0):
                visible_channels.append(name)
        return visible_channels

    def _draw_overrange_badges(self, painter: QPainter, plot_rect: QRectF) -> None:
        visible_channels = self._visible_overrange_channels()
        if not visible_channels:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        badge_width = 170.0
        badge_height = 24.0
        total_height = len(visible_channels) * (badge_height + 6.0) - 6.0
        top = plot_rect.top() + 10.0
        left = plot_rect.center().x() - (badge_width / 2.0)
        for index, name in enumerate(visible_channels):
            badge_top = top + index * (badge_height + 6.0)
            icon_rect = QRectF(left, badge_top, 24.0, badge_height)
            text_rect = QRectF(left + 32.0, badge_top, badge_width - 32.0, badge_height)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ff0000"))
            painter.drawRect(icon_rect)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.drawText(icon_rect, Qt.AlignCenter, "!")
            painter.setPen(QPen(QColor("#333333")))
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, f"Channel {name} overrange")
        painter.restore()

    def _trigger_marker_point(self, plot_rect: QRectF) -> QPointF | None:
        if self.frame is None or self.state.trigger.mode == "None":
            return None
        source_channel = self._channel_state(self.state.trigger.source)
        if source_channel is None or not source_channel.enabled:
            return None

        visible_span = max(self._view_end_ratio - self._view_start_ratio, 1e-9)
        trigger_time_ratio = clamp(self.state.trigger.pre_trigger_percent / 100.0, 0.0, 1.0)
        x_ratio = (trigger_time_ratio - self._view_start_ratio) / visible_span
        if x_ratio < 0.0 or x_ratio > 1.0:
            return None

        y_range = max(channel_visible_range(source_channel), 1e-6)
        y_ratio = 0.5 - (source_channel.vertical_offset_divs / 10.0) - (
            display_trigger_level(self.state.trigger) / (2.0 * y_range)
        )
        if y_ratio < 0.0 or y_ratio > 1.0:
            return None

        return QPointF(
            plot_rect.left() + (plot_rect.width() * x_ratio),
            plot_rect.top() + (plot_rect.height() * y_ratio),
        )

    def _draw_trigger_marker(self, painter: QPainter, plot_rect: QRectF) -> None:
        marker_point = self._trigger_marker_point(plot_rect)
        if marker_point is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#1e73be"), 1.0))
        painter.setBrush(QColor("#facc15"))
        painter.drawEllipse(marker_point, 4.0, 4.0)
        painter.restore()

    def _trigger_marker_contains(self, point: QPointF, plot_rect: QRectF) -> bool:
        marker_point = self._trigger_marker_point(plot_rect)
        if marker_point is None:
            return False
        return QLineF(point, marker_point).length() <= 16.0

    def _set_trigger_marker_from_point(self, point: QPointF, plot_rect: QRectF) -> None:
        if self.frame is None or self.state.trigger.mode == "None":
            return
        source_channel = self._channel_state(self.state.trigger.source)
        if source_channel is None or not source_channel.enabled:
            return

        x_ratio = clamp((point.x() - plot_rect.left()) / max(plot_rect.width(), 1.0), 0.0, 1.0)
        visible_span = max(self._view_end_ratio - self._view_start_ratio, 1e-9)
        full_ratio = self._view_start_ratio + (visible_span * x_ratio)
        new_pre_trigger = int(round(clamp(full_ratio, 0.0, 1.0) * 100.0))

        y_ratio = clamp((point.y() - plot_rect.top()) / max(plot_rect.height(), 1.0), 0.0, 1.0)
        y_range = max(channel_visible_range(source_channel), 1e-6)
        new_level = (0.5 - (source_channel.vertical_offset_divs / 10.0) - y_ratio) * 2.0 * y_range

        self.trigger_pre_trigger_changed.emit(new_pre_trigger)
        self.trigger_level_changed.emit(new_level)

    def _draw_channel_trace(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        volts: np.ndarray,
        overrange: np.ndarray,
        channel_state: ChannelState,
    ) -> None:
        if not channel_state.enabled or volts.size == 0 or self.frame is None:
            return

        times = self.frame.times
        view_start, view_end = self._visible_time_window()
        start_index = max(int(np.searchsorted(times, view_start, side="left")) - 1, 0)
        end_index = min(int(np.searchsorted(times, view_end, side="right")) + 1, times.size)
        times = times[start_index:end_index]
        volts = volts[start_index:end_index]
        overrange = overrange[start_index:end_index] if overrange.size else np.zeros(volts.shape, dtype=np.int8)
        if times.size == 0 or volts.size == 0:
            return
        span = max(view_end - view_start, 1e-9)
        y_range = max(channel_visible_range(channel_state), 1e-6)

        path = QPainterPath()
        has_segment = False
        previous_state = 0
        for time_value, voltage, overrange_state in zip(times, volts, overrange):
            x_ratio = float((time_value - view_start) / span)
            x = plot_rect.left() + x_ratio * plot_rect.width()
            overrange_state = int(overrange_state)
            if overrange_state > 0:
                boundary_point = QPointF(x, plot_rect.top())
                if has_segment:
                    path.lineTo(boundary_point)
                    has_segment = False
                previous_state = 1
                continue
            if overrange_state < 0:
                boundary_point = QPointF(x, plot_rect.bottom())
                if has_segment:
                    path.lineTo(boundary_point)
                    has_segment = False
                previous_state = -1
                continue

            y_ratio = self._channel_y_ratio(float(voltage), channel_state, y_range)
            y = plot_rect.top() + clamp(y_ratio, 0.0, 1.0) * plot_rect.height()
            point = QPointF(x, y)
            if not has_segment:
                if previous_state > 0:
                    path.moveTo(QPointF(x, plot_rect.top()))
                    path.lineTo(point)
                elif previous_state < 0:
                    path.moveTo(QPointF(x, plot_rect.bottom()))
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                has_segment = True
            else:
                path.lineTo(point)
            previous_state = 0

        waveform_pen = QPen(QColor(channel_state.color_hex), 1.2)
        painter.setPen(waveform_pen)
        painter.drawPath(path)

    def _custom_channel_volts(self) -> tuple[np.ndarray, ChannelState] | None:
        if self.frame is None or self.frame.times.size == 0:
            return None
        if self._custom_channel_cache is not None:
            return self._custom_channel_cache
        custom_payload = self._custom_display_channel()
        if custom_payload is None:
            return None
        display_channel, source_name = custom_payload
        source_volts = self.frame.channel_a if source_name == "A" else self.frame.channel_b
        self._custom_channel_cache = (
            apply_smoothing_method(
                source_volts,
                self.state.custom_channel.smoothing_method,
                self.state.custom_channel.smoothing_span,
            ),
            display_channel,
        )
        return self._custom_channel_cache

    def _draw_custom_channel_trace(self, painter: QPainter, plot_rect: QRectF) -> None:
        custom_payload = self._custom_channel_volts()
        if custom_payload is None:
            return
        volts, source_channel = custom_payload
        if self.frame is None:
            return

        times = self.frame.times
        view_start, view_end = self._visible_time_window()
        start_index = max(int(np.searchsorted(times, view_start, side="left")) - 1, 0)
        end_index = min(int(np.searchsorted(times, view_end, side="right")) + 1, times.size)
        times = times[start_index:end_index]
        volts = volts[start_index:end_index]
        if times.size == 0 or volts.size == 0:
            return
        span = max(view_end - view_start, 1e-9)
        y_range = max(channel_visible_range(source_channel), 1e-6)

        path = QPainterPath()
        for index, (time_value, voltage) in enumerate(zip(times, volts)):
            x_ratio = float((time_value - view_start) / span)
            y_ratio = self._channel_y_ratio(float(voltage), source_channel, y_range)
            x = plot_rect.left() + x_ratio * plot_rect.width()
            y = plot_rect.top() + clamp(y_ratio, 0.0, 1.0) * plot_rect.height()
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        waveform_pen = QPen(QColor(self.state.custom_channel.color_hex), 1.6)
        painter.setPen(waveform_pen)
        painter.drawPath(path)

    def _active_annotation_items(self) -> list[AnnotationStroke | AnnotationText]:
        return self.waveform_annotations if self.annotation_settings.scope == "This capture" else self.global_annotations

    def _draw_annotation_strokes(self, painter: QPainter, plot_rect: QRectF) -> None:
        for item in [*self.global_annotations, *self.waveform_annotations]:
            if not isinstance(item, AnnotationStroke) or len(item.points) < 2:
                continue
            path = QPainterPath()
            for index, point in enumerate(item.points):
                widget_point = self._normalized_to_plot(point, plot_rect)
                if index == 0:
                    path.moveTo(widget_point)
                else:
                    path.lineTo(widget_point)
            color = QColor(item.color_hex)
            color.setAlpha(170)
            pen = QPen(color, item.width)
            painter.setPen(pen)
            painter.drawPath(path)

    def _draw_annotation_overlays(self, painter: QPainter, plot_rect: QRectF) -> None:
        for item in [*self.global_annotations, *self.waveform_annotations]:
            if not isinstance(item, AnnotationText):
                continue
            painter.setPen(QPen(QColor(item.color_hex)))
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            point = self._normalized_to_plot(item.position, plot_rect)
            painter.drawText(QRectF(point.x(), point.y(), 180, 22), Qt.AlignLeft | Qt.AlignTop, item.text)
        if self._active_stroke and len(self._active_stroke) >= 2:
            path = QPainterPath()
            for index, point in enumerate(self._active_stroke):
                widget_point = self._normalized_to_plot(point, plot_rect)
                if index == 0:
                    path.moveTo(widget_point)
                else:
                    path.lineTo(widget_point)
            pen = QPen(QColor(self.annotation_settings.color_hex), 2.0)
            painter.setPen(pen)
            painter.drawPath(path)

        if self._active_text_box is not None:
            box_rect = self._annotation_text_rect(self._active_text_box, plot_rect)
            box_pen = QPen(QColor(self._active_text_box.color_hex), 1.4, Qt.DashLine)
            painter.setPen(box_pen)
            painter.drawRect(box_rect)
            painter.setPen(QPen(QColor(self._active_text_box.color_hex)))
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            text_rect = box_rect.adjusted(6, 4, -6, -4)
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self._active_text_box.text)
            if self.hasFocus():
                font_metrics = painter.fontMetrics()
                caret_x = min(
                    text_rect.left() + font_metrics.horizontalAdvance(self._active_text_box.text),
                    text_rect.right() - 2,
                )
            painter.drawLine(
                QPointF(caret_x + 1, text_rect.top() + 2),
                QPointF(caret_x + 1, text_rect.bottom() - 2),
            )

    def _draw_zoom_box_overlay(self, painter: QPainter, plot_rect: QRectF) -> None:
        if self._zoom_box_start is None or self._zoom_box_end is None:
            return
        zoom_rect = QRectF(self._zoom_box_start, self._zoom_box_end).normalized().intersected(plot_rect)
        if zoom_rect.width() < 2 or zoom_rect.height() < 2:
            return
        painter.save()
        pen = QPen(QColor("#1e40ff"), 1.2, Qt.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(zoom_rect)
        painter.restore()

    def has_visible_annotations(self) -> bool:
        return bool(self.global_annotations or self.waveform_annotations)

    @staticmethod
    def _normalized_to_plot(point: tuple[float, float], plot_rect: QRectF) -> QPointF:
        return QPointF(
            plot_rect.left() + (point[0] * plot_rect.width()),
            plot_rect.top() + (point[1] * plot_rect.height()),
        )

    @staticmethod
    def _plot_to_normalized(point: QPointF, plot_rect: QRectF) -> tuple[float, float]:
        return (
            clamp((point.x() - plot_rect.left()) / max(plot_rect.width(), 1.0), 0.0, 1.0),
            clamp((point.y() - plot_rect.top()) / max(plot_rect.height(), 1.0), 0.0, 1.0),
        )

    def _annotation_text_rect(self, text_box: AnnotationText, plot_rect: QRectF) -> QRectF:
        point = self._normalized_to_plot(text_box.position, plot_rect)
        width = 168.0
        height = 28.0
        left = clamp(point.x(), plot_rect.left(), max(plot_rect.right() - width, plot_rect.left()))
        top = clamp(point.y(), plot_rect.top(), max(plot_rect.bottom() - height, plot_rect.top()))
        return QRectF(left, top, width, height)

    def _channel_state(self, name: str) -> ChannelState | None:
        if name == "A":
            return self.state.channel_a if self.state.channel_a.enabled else None
        if name == "B":
            return self.state.channel_b if self.state.channel_b.enabled else None
        if name == "Custom":
            custom_payload = self._custom_display_channel()
            return None if custom_payload is None else custom_payload[0]
        return None

    def _should_draw_source_trace(self, name: str) -> bool:
        if name not in ("A", "B"):
            return False
        custom = self.state.custom_channel
        if custom.enabled and custom.source_channel == name and not custom.show_source_channel:
            return False
        return True

    def _channel_axis_drag_rect(self, name: str, plot_rect: QRectF) -> QRectF | None:
        channel = self._channel_state(name)
        if channel is None:
            return None
        if name == "Custom":
            descriptor = self._custom_axis_descriptor()
            if descriptor is None:
                return None
            side = descriptor[0]
            if side == "left":
                return QRectF(plot_rect.left() + 2.0, plot_rect.top(), 34.0, plot_rect.height())
            return QRectF(plot_rect.right() - 36.0, plot_rect.top(), 34.0, plot_rect.height())
        if name == "A":
            return QRectF(0.0, plot_rect.top(), plot_rect.left() + 2.0, plot_rect.height())
        return QRectF(plot_rect.right() - 2.0, plot_rect.top(), self.width() - plot_rect.right() + 2.0, plot_rect.height())

    def _channel_axis_name_at(self, point: QPointF, plot_rect: QRectF) -> str | None:
        for name in ("Custom", "A", "B"):
            axis_rect = self._channel_axis_drag_rect(name, plot_rect)
            if axis_rect is not None and axis_rect.contains(point):
                return name
        return None

    def _normalize_channel_draw_order(self) -> None:
        normalized = [name for name in self._channel_draw_order if name in ("A", "B")]
        for name in ("A", "B"):
            if name not in normalized:
                normalized.append(name)
        self._channel_draw_order = normalized[-2:]

    def _bring_channel_to_front(self, name: str) -> None:
        if name not in ("A", "B"):
            return
        self._normalize_channel_draw_order()
        if name in self._channel_draw_order:
            self._channel_draw_order.remove(name)
        self._channel_draw_order.append(name)

    def _channel_y_ratio(self, voltage: float, channel_state: ChannelState, y_range: float) -> float:
        return 0.5 - float(voltage / (2.0 * y_range)) - (channel_state.vertical_offset_divs / 10.0)

    def _channel_zero_y(self, plot_rect: QRectF, channel_state: ChannelState) -> float:
        y_ratio = self._channel_y_ratio(0.0, channel_state, max(channel_visible_range(channel_state), 1e-6))
        return plot_rect.top() + clamp(y_ratio, 0.0, 1.0) * plot_rect.height()

    def _vertical_offset_from_point(self, point: QPointF, plot_rect: QRectF) -> float:
        division_height = max(plot_rect.height() / 10.0, 1.0)
        return float(clamp((plot_rect.center().y() - point.y()) / division_height, -5.0, 5.0))

    def _apply_vertical_offset_drag(self, point: QPointF) -> None:
        if self._dragging_vertical_offset is None:
            return
        plot_rect = self._dragging_vertical_plot_rect or self._plot_rect()
        channel = self._channel_state(self._dragging_vertical_offset)
        if channel is None:
            return
        new_offset = self._vertical_offset_from_point(point, plot_rect)
        if math.isclose(channel.vertical_offset_divs, new_offset, rel_tol=1e-9, abs_tol=1e-9):
            return
        channel.vertical_offset_divs = new_offset
        self.vertical_offset_changed.emit(self._dragging_vertical_offset, new_offset)
        self.update()

    def _reset_canvas_cursor(self) -> None:
        if self._dragging_vertical_offset is not None:
            self.setCursor(vertical_offset_cursor())
            return
        if self._zoom_box_mode:
            self.setCursor(Qt.CrossCursor)
            return
        self._apply_annotation_cursor()

    def _update_hover_cursor(self, point: QPointF) -> None:
        plot_rect = self._plot_rect()
        if self._trigger_marker_contains(point, plot_rect):
            self.setCursor(Qt.OpenHandCursor)
            return
        if self._channel_axis_name_at(point, plot_rect) is not None:
            self.setCursor(vertical_offset_cursor())
            return
        self._reset_canvas_cursor()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        plot_rect = self._plot_rect()
        if (
            event.button() == Qt.LeftButton
            and self.annotation_settings.tool == "Off"
            and not self._zoom_box_mode
            and self._trigger_marker_contains(event.position(), plot_rect)
        ):
            self._dragging_trigger_marker = True
            self._set_trigger_marker_from_point(event.position(), plot_rect)
            self.setFocus(Qt.MouseFocusReason)
            self.setCursor(Qt.ClosedHandCursor)
            return
        handle_name = self._channel_axis_name_at(event.position(), plot_rect)
        if event.button() == Qt.LeftButton and handle_name is not None:
            self._bring_channel_to_front(handle_name)
            self._dragging_vertical_offset = handle_name
            self._dragging_vertical_plot_rect = QRectF(plot_rect)
            self._apply_vertical_offset_drag(event.position())
            self.setFocus(Qt.MouseFocusReason)
            self.setCursor(vertical_offset_cursor())
            return
        if event.button() == Qt.LeftButton and self._zoom_box_mode and plot_rect.contains(event.position()):
            self._zoom_box_start = QPointF(event.position())
            self._zoom_box_end = QPointF(event.position())
            self.setFocus(Qt.MouseFocusReason)
            self.update()
            return
        if (
            event.button() in (Qt.LeftButton, Qt.MiddleButton)
            and (self.annotation_settings.tool == "Off" or event.button() == Qt.MiddleButton)
            and plot_rect.contains(event.position())
            and self._has_active_zoom()
        ):
            self._panning_view = True
            self._pan_button = event.button()
            self._pan_press_x = float(event.position().x())
            self._pan_press_y = float(event.position().y())
            self._pan_start_range = (self._view_start_ratio, self._view_end_ratio)
            self._pan_start_offsets = {
                "A": self.state.channel_a.vertical_offset_divs,
                "B": self.state.channel_b.vertical_offset_divs,
            }
            self.setFocus(Qt.MouseFocusReason)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() != Qt.LeftButton or not plot_rect.contains(event.position()):
            return super().mousePressEvent(event)
        if self.annotation_settings.tool == "Pen":
            self._active_stroke_plot_rect = None
            self._active_stroke = []
            self._active_stroke_press_global = self._event_global_position(event)
            self._active_stroke_press_local = QPointF(event.position())
            self._active_stroke_has_moved = False
            return
        if self.annotation_settings.tool == "Text":
            self._active_text_box = AnnotationText(
                position=self._plot_to_normalized(event.position(), plot_rect),
                text="",
                color_hex=self.annotation_settings.color_hex,
            )
            self._active_text_scope = self.annotation_settings.scope
            self.annotation_interaction_started.emit()
            self.setFocus(Qt.MouseFocusReason)
            self.update()
            return
        if self.annotation_settings.tool == "Eraser":
            self.annotation_interaction_started.emit()
            self._erase_at(event.position(), plot_rect)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging_trigger_marker:
            self._set_trigger_marker_from_point(event.position(), self._plot_rect())
            return
        if self._dragging_vertical_offset is not None:
            self._apply_vertical_offset_drag(event.position())
            return
        if self._zoom_box_start is not None and (event.buttons() & Qt.LeftButton):
            self._zoom_box_end = QPointF(event.position())
            self.update()
            return
        if self._panning_view and (event.buttons() & self._pan_button):
            plot_rect = self._plot_rect()
            width = max(plot_rect.width(), 1.0)
            delta_ratio = (self._pan_press_x - float(event.position().x())) / width
            span = self._pan_start_range[1] - self._pan_start_range[0]
            new_start = clamp(self._pan_start_range[0] + delta_ratio, 0.0, 1.0 - span)
            self._set_view_range(new_start, new_start + span)
            division_height = max(plot_rect.height() / 10.0, 1.0)
            delta_divs = (self._pan_press_y - float(event.position().y())) / division_height
            for name, channel in (("A", self.state.channel_a), ("B", self.state.channel_b)):
                if not channel.enabled:
                    continue
                new_offset = float(clamp(self._pan_start_offsets.get(name, channel.vertical_offset_divs) + delta_divs, -5.0, 5.0))
                if math.isclose(new_offset, channel.vertical_offset_divs, rel_tol=1e-9, abs_tol=1e-9):
                    continue
                channel.vertical_offset_divs = new_offset
                self.vertical_offset_changed.emit(name, new_offset)
            self.update()
            return
        if self.annotation_settings.tool == "Pen" and self._active_stroke is not None:
            if event.buttons() & Qt.LeftButton:
                if self._stroke_motion_distance(event, prefer_global=True) < 2.0:
                    return
                if not self._active_stroke_has_moved and self._annotation_panel_open:
                    self.annotation_interaction_started.emit()
                    self._pending_pen_start_after_hide = True
                    return
                if self._pending_pen_start_after_hide and not self._annotation_panel_open:
                    self._active_stroke_plot_rect = QRectF(self._plot_rect())
                    self._pending_pen_start_after_hide = False
                if not self._active_stroke_has_moved:
                    self._active_stroke_plot_rect = QRectF(self._plot_rect())
                    self._active_stroke_has_moved = True
                plot_rect = self._active_stroke_plot_rect or self._plot_rect()
                if not plot_rect.contains(event.position()):
                    return
                point = self._plot_to_normalized(event.position(), plot_rect)
                if not self._active_stroke or point != self._active_stroke[-1]:
                    self._active_stroke.append(point)
                    self._active_stroke_has_moved = True
                self.update()
                return
        if self.annotation_settings.tool == "Eraser" and event.buttons() & Qt.LeftButton:
            plot_rect = self._plot_rect()
            if plot_rect.contains(event.position()):
                self._erase_at(event.position(), plot_rect)
                return
        if not (event.buttons() & Qt.LeftButton):
            self._update_hover_cursor(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging_trigger_marker and event.button() == Qt.LeftButton:
            self._set_trigger_marker_from_point(event.position(), self._plot_rect())
            self._dragging_trigger_marker = False
            self._update_hover_cursor(event.position())
            return
        if self._dragging_vertical_offset is not None:
            self._apply_vertical_offset_drag(event.position())
            self._dragging_vertical_offset = None
            self._dragging_vertical_plot_rect = None
            self._update_hover_cursor(event.position())
            return
        if self._zoom_box_start is not None and event.button() == Qt.LeftButton:
            self._zoom_box_end = QPointF(event.position())
            self._apply_zoom_box(QRectF(self._zoom_box_start, self._zoom_box_end), self._plot_rect())
            self._zoom_box_start = None
            self._zoom_box_end = None
            self._reset_canvas_cursor()
            self.update()
            return
        if self._panning_view and event.button() == self._pan_button:
            self._panning_view = False
            self._pan_button = Qt.MouseButton.LeftButton
            self._update_hover_cursor(event.position())
            return
        if self.annotation_settings.tool == "Pen" and self._active_stroke is not None:
            plot_rect = self._active_stroke_plot_rect or self._plot_rect()
            moved_distance = self._stroke_motion_distance(event, prefer_global=True)
            if plot_rect.contains(event.position()) and moved_distance >= 2.0:
                point = self._plot_to_normalized(event.position(), plot_rect)
                if not self._active_stroke or point != self._active_stroke[-1]:
                    self._active_stroke.append(point)
                    self._active_stroke_has_moved = True
            if (
                self._active_stroke_has_moved
                and len(self._active_stroke) >= 2
                and self._stroke_has_visible_length(self._active_stroke, plot_rect)
            ):
                self._active_annotation_items().append(
                    AnnotationStroke(points=self._active_stroke[:], color_hex=self.annotation_settings.color_hex)
                )
            self._active_stroke = None
            self._active_stroke_plot_rect = None
            self._active_stroke_press_global = None
            self._active_stroke_press_local = None
            self._active_stroke_has_moved = False
            self._pending_pen_start_after_hide = False
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        plot_rect = self._plot_rect()
        axis_name = self._channel_axis_name_at(event.position(), plot_rect)
        if axis_name is not None:
            self._reset_zoom(axis_name=axis_name)
            self._update_hover_cursor(event.position())
            return
        if plot_rect.contains(event.position()):
            self._reset_zoom()
            self._update_hover_cursor(event.position())
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        plot_rect = self._plot_rect()
        axis_name = self._channel_axis_name_at(event.position(), plot_rect)
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        if axis_name is not None:
            self._adjust_channel_display_zoom(axis_name, 1 if delta > 0 else -1)
            return
        if plot_rect.contains(event.position()):
            cursor_ratio = clamp((event.position().x() - plot_rect.left()) / max(plot_rect.width(), 1.0), 0.0, 1.0)
            self._zoom_horizontal(cursor_ratio, delta > 0)
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._active_text_box is None:
            return super().keyPressEvent(event)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._active_text_box.text.strip():
                target_items = (
                    self.waveform_annotations
                    if self._active_text_scope == "This capture"
                    else self.global_annotations
                )
                target_items.append(
                    AnnotationText(
                        position=self._active_text_box.position,
                        text=self._active_text_box.text.strip(),
                        color_hex=self._active_text_box.color_hex,
                    )
                )
            self._active_text_box = None
            self._active_text_scope = None
            self.update()
            return
        if event.key() == Qt.Key_Escape:
            self._active_text_box = None
            self._active_text_scope = None
            self.update()
            return
        if event.key() == Qt.Key_Backspace:
            self._active_text_box.text = self._active_text_box.text[:-1]
            self.update()
            return
        if event.text() and event.text().isprintable():
            self._active_text_box.text += event.text()
            self.update()
            return
        super().keyPressEvent(event)

    def _stroke_has_visible_length(self, points: list[tuple[float, float]], plot_rect: QRectF) -> bool:
        if len(points) < 2:
            return False
        start_point = self._normalized_to_plot(points[0], plot_rect)
        end_point = self._normalized_to_plot(points[-1], plot_rect)
        return QLineF(start_point, end_point).length() >= 2.0

    def _stroke_motion_distance(self, event, prefer_global: bool = False) -> float:
        global_distance = 0.0
        local_distance = 0.0
        if self._active_stroke_press_global is not None:
            global_distance = QLineF(self._active_stroke_press_global, self._event_global_position(event)).length()
        if self._active_stroke_press_local is not None:
            local_distance = QLineF(self._active_stroke_press_local, QPointF(event.position())).length()
        if prefer_global and global_distance > 0.0:
            return global_distance
        return max(global_distance, local_distance)

    @staticmethod
    def _event_global_position(event) -> QPointF:
        if hasattr(event, "globalPosition"):
            return event.globalPosition()
        return event.position()

    def _erase_at(self, point: QPointF, plot_rect: QRectF) -> None:
        removed = False
        if self._active_text_box is not None and self._annotation_text_rect(self._active_text_box, plot_rect).adjusted(-4, -4, 4, 4).contains(point):
            self._active_text_box = None
            self._active_text_scope = None
            removed = True
        removed = self._erase_from_collection(self.waveform_annotations, point, plot_rect) or removed
        removed = self._erase_from_collection(self.global_annotations, point, plot_rect) or removed
        if removed:
            self.update()

    def _erase_from_collection(
        self,
        items: list[AnnotationStroke | AnnotationText],
        point: QPointF,
        plot_rect: QRectF,
    ) -> bool:
        for index in range(len(items) - 1, -1, -1):
            item = items[index]
            if isinstance(item, AnnotationText):
                if self._annotation_text_rect(item, plot_rect).adjusted(-4, -4, 4, 4).contains(point):
                    items.pop(index)
                    return True
                continue

            split_segments: list[list[tuple[float, float]]] = []
            current_segment: list[tuple[float, float]] = []
            removed_point = False
            for stored in item.points:
                stored_point = self._normalized_to_plot(stored, plot_rect)
                if QLineF(stored_point, point).length() <= 12:
                    removed_point = True
                    if len(current_segment) >= 2:
                        split_segments.append(current_segment[:])
                    current_segment = []
                    continue
                current_segment.append(stored)
            if len(current_segment) >= 2:
                split_segments.append(current_segment[:])
            if not removed_point:
                continue

            items.pop(index)
            for segment in reversed(split_segments):
                items.insert(index, AnnotationStroke(points=segment, color_hex=item.color_hex, width=item.width))
            return True
        return False


# ============================================================================
# Frontend + backend coordinator: main desktop window
# ============================================================================


