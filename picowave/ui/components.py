from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from picowave.config import *
from picowave.helpers import *
from picowave.models import *
from picowave.processing import *
class ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# Frontend: compact status and summary cards

class StatusCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(40)
        self.setObjectName("statusCard")
        self._title = QLabel("Stopped")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setObjectName("statusTitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self._title)
        self.set_running(False)

    def set_running(self, running: bool) -> None:
        self._title.setText("Running" if running else "Stopped")
        self.setProperty("running", running)
        self.style().unpolish(self)
        self.style().polish(self)


class ScopeCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("scopeCard")
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)

        title = QLabel("Scope")
        title.setObjectName("cardTitle")
        self.value_label = QLabel()
        self.value_label.setObjectName("scopeValue")
        self.samples_value = QLabel()
        self.samples_value.setObjectName("metricValue")
        self.sample_rate_value = QLabel()
        self.sample_rate_value.setObjectName("metricValue")
        samples_title = QLabel("Samples")
        rate_title = QLabel("Sample rate")
        samples_title.setObjectName("metricTitle")
        rate_title.setObjectName("metricTitle")

        layout.addWidget(title, 0, 0)
        layout.addWidget(self.value_label, 1, 0, 2, 1)
        layout.addWidget(samples_title, 0, 1)
        layout.addWidget(self.samples_value, 1, 1)
        layout.addWidget(rate_title, 0, 2)
        layout.addWidget(self.sample_rate_value, 1, 2)

    def update_content(self, time_per_div: float, sample_count: int, sample_rate_hz: float) -> None:
        self.value_label.setText(format_time_per_div(time_per_div))
        self.samples_value.setText(f"{sample_count}")
        self.sample_rate_value.setText(format_sample_rate(sample_rate_hz))


class TriggerCard(ClickableFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("triggerCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        title = QLabel("Trigger")
        title.setObjectName("cardTitle")
        self.value_label = QLabel("None")
        self.value_label.setObjectName("triggerValue")
        self.detail_label = QLabel("Click to cycle mode")
        self.detail_label.setObjectName("detailText")
        layout.addWidget(title)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

    def update_content(self, trigger: TriggerState) -> None:
        self.value_label.setText(format_trigger_summary(trigger))


class WaveformCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("waveformCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        title = QLabel("Waveform")
        title.setObjectName("cardTitle")
        self.value_label = QLabel("1\nof 1")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setObjectName("waveformValue")
        layout.addWidget(title)
        layout.addWidget(self.value_label)

    def update_content(self, current_index: int, total_count: int) -> None:
        self.value_label.setText(f"{current_index}\nof {total_count}")


class WaveformHistoryControl(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("waveformHistoryStrip")
        self.setFixedWidth(142)
        self.setFixedHeight(60)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.minus_button = QPushButton("-")
        self.minus_button.setObjectName("waveformHistoryNavButton")
        layout.addWidget(self.minus_button)

        self.body = ClickableFrame()
        self.body.setObjectName("waveformHistoryBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 4)
        body_layout.setSpacing(1)

        self.title_label = QLabel("Waveform")
        self.title_label.setObjectName("waveformHistoryTitle")
        self.value_label = QLabel("0 of 0")
        self.value_label.setObjectName("waveformHistoryValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setWordWrap(False)

        body_layout.addWidget(self.title_label)
        body_layout.addWidget(self.value_label)
        layout.addWidget(self.body)

        self.plus_button = QPushButton("+")
        self.plus_button.setObjectName("waveformHistoryNavButton")
        layout.addWidget(self.plus_button)

    def set_state(self, current_index: int, total_count: int) -> None:
        self.value_label.setText(f"{current_index} of {total_count}")
        has_waveforms = total_count > 0
        self.minus_button.setEnabled(has_waveforms)
        self.plus_button.setEnabled(has_waveforms)


class WaveformPreviewStrip(QWidget):
    waveform_selected = Signal(int)
    page_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("waveformPreviewStrip")
        self.setFixedHeight(82)
        self._items: list[tuple[int, CaptureFrame]] = []
        self._current_history_index = 0
        self._page_start = 0

    def set_history(self, history: list[CaptureFrame], current_index: int) -> None:
        self._items = [(index, frame) for index, frame in enumerate(history) if frame.sample_count > 0]
        self._current_history_index = current_index
        if not self._items:
            self._page_start = 0
            self.update()
            return
        self._page_start = int(clamp(self._page_start, 0, self._last_page_start()))
        current_position = next(
            (index for index, (history_index, _frame) in enumerate(self._items) if history_index == current_index),
            len(self._items) - 1,
        )
        if current_position < self._page_start or current_position >= self._page_start + WAVEFORM_PREVIEW_PAGE_SIZE:
            page_start = (current_position // WAVEFORM_PREVIEW_PAGE_SIZE) * WAVEFORM_PREVIEW_PAGE_SIZE
            self._page_start = int(clamp(page_start, 0, self._last_page_start()))
        self.update()

    def visible_items(self) -> list[tuple[int, CaptureFrame]]:
        return self._items[self._page_start : self._page_start + WAVEFORM_PREVIEW_PAGE_SIZE]

    def _last_page_start(self) -> int:
        if not self._items:
            return 0
        return ((len(self._items) - 1) // WAVEFORM_PREVIEW_PAGE_SIZE) * WAVEFORM_PREVIEW_PAGE_SIZE

    def has_previous_page(self) -> bool:
        return self._page_start > 0

    def has_next_page(self) -> bool:
        return self._page_start < self._last_page_start()

    def previous_page(self) -> None:
        self._page_start = max(0, self._page_start - WAVEFORM_PREVIEW_PAGE_SIZE)
        self.update()

    def next_page(self) -> None:
        self._page_start = min(self._last_page_start(), self._page_start + WAVEFORM_PREVIEW_PAGE_SIZE)
        self.update()

    def _thumbnail_rects(self) -> list[QRectF]:
        outer = self.rect().adjusted(6, 6, -6, -6)
        spacing = 6.0
        count = WAVEFORM_PREVIEW_PAGE_SIZE
        available_width = max(outer.width() - spacing * (count - 1), 1.0)
        thumb_width = available_width / count
        rects: list[QRectF] = []
        x = float(outer.left())
        for _index in range(count):
            rects.append(QRectF(x, float(outer.top()), thumb_width, float(outer.height())))
            x += thumb_width + spacing
        return rects

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            for rect, (history_index, _frame) in zip(self._thumbnail_rects(), self.visible_items()):
                if rect.contains(event.position()):
                    self.waveform_selected.emit(history_index)
                    break
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        angle_delta = event.angleDelta().y()
        if angle_delta < 0 and self.has_next_page():
            self.page_requested.emit(1)
            event.accept()
            return
        if angle_delta > 0 and self.has_previous_page():
            self.page_requested.emit(-1)
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            outer = self.rect().adjusted(0, 0, -1, -1)
            painter.fillRect(outer, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#d7dfe7"), 1.0))
            painter.drawRect(outer)

            visible_items = self.visible_items()
            for visible_offset, rect in enumerate(self._thumbnail_rects()):
                item = visible_items[visible_offset] if visible_offset < len(visible_items) else None
                history_index = item[0] if item is not None else None
                frame = item[1] if item is not None else None
                display_index = self._page_start + visible_offset + 1
                is_selected = history_index == self._current_history_index if history_index is not None else False
                border_color = QColor("#1e73be" if is_selected else "#d7dfe7")
                fill_color = QColor("#f8fbff" if is_selected else "#ffffff")
                painter.setPen(QPen(border_color, 2.0 if is_selected else 1.0))
                painter.setBrush(fill_color)
                painter.drawRect(rect)

                preview_rect = rect.adjusted(4, 16, -4, -6)
                painter.setPen(QPen(QColor("#d6ebff"), 1.0, Qt.DashLine))
                if frame is not None:
                    for column in range(5):
                        x = preview_rect.left() + (preview_rect.width() * column / 4.0)
                        painter.drawLine(QPointF(x, preview_rect.top()), QPointF(x, preview_rect.bottom()))
                    for row in range(4):
                        y = preview_rect.top() + (preview_rect.height() * row / 3.0)
                        painter.drawLine(QPointF(preview_rect.left(), y), QPointF(preview_rect.right(), y))
                    self._draw_preview_trace(
                        painter, preview_rect, frame.times, frame.channel_a, QColor("#1e73be"), frame.y_range_volts
                    )
                    self._draw_preview_trace(
                        painter, preview_rect, frame.times, frame.channel_b, QColor("#ef3340"), frame.y_range_volts
                    )

                painter.setPen(
                    QPen(QColor("#1e73be" if is_selected else "#51657a" if frame is not None else "#9aa3ad"))
                )
                painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
                if frame is not None:
                    painter.drawText(
                        rect.adjusted(6, 3, -6, -rect.height() + 15), Qt.AlignLeft | Qt.AlignVCenter, str(display_index)
                    )
        finally:
            painter.end()

    def _draw_preview_trace(
        self,
        painter: QPainter,
        rect: QRectF,
        times: np.ndarray,
        volts: np.ndarray,
        color: QColor,
        y_range_volts: float,
    ) -> None:
        if times.size <= 1 or volts.size == 0:
            return
        sample_step = max(1, int(math.ceil(times.size / max(rect.width(), 1.0))))
        times_view = times[::sample_step]
        volts_view = volts[::sample_step]
        time_start = float(times[0])
        time_span = max(float(times[-1] - times[0]), 1e-9)
        visible_range = max(float(y_range_volts), 1e-3)

        path = QPainterPath()
        for index, (time_value, volt_value) in enumerate(zip(times_view, volts_view)):
            x = rect.left() + ((float(time_value) - time_start) / time_span) * rect.width()
            normalized = 0.5 - (float(volt_value) / (visible_range * 2.4))
            y = rect.top() + clamp(normalized, 0.0, 1.0) * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(color, 1.0))
        painter.drawPath(path)


class ScopeFrontStatusWidget(QFrame):
    # Lightweight front-panel sketch used as a live status widget. It is drawn
    # with QPainter instead of a static asset so it can reflect enabled channels
    # and blink whenever a fresh capture frame arrives.
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("scopeFrontStatus")
        self.setFixedSize(156, 44)
        self._channel_a_enabled = False
        self._channel_b_enabled = False
        self._blink_on = False
        self._last_activity_at = 0.0
        self._heartbeat_mode = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._clear_blink)
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(90)
        self._heartbeat_timer.timeout.connect(self._toggle_heartbeat_blink)
        self._heartbeat_decay_timer = QTimer(self)
        self._heartbeat_decay_timer.setSingleShot(True)
        self._heartbeat_decay_timer.timeout.connect(self._stop_heartbeat_mode)

    def set_channel_state(self, a_enabled: bool, b_enabled: bool) -> None:
        if self._channel_a_enabled == a_enabled and self._channel_b_enabled == b_enabled:
            return
        self._channel_a_enabled = a_enabled
        self._channel_b_enabled = b_enabled
        self.update()

    def blink_activity(self) -> None:
        now = time.monotonic()
        interval = now - self._last_activity_at if self._last_activity_at > 0.0 else float("inf")
        self._last_activity_at = now
        if interval < 0.12:
            self._start_heartbeat_mode()
            return
        self._stop_heartbeat_mode()
        self._blink_on = True
        self._blink_timer.start(120)
        self.update()

    def _clear_blink(self) -> None:
        if self._heartbeat_mode:
            return
        self._blink_on = False
        self.update()

    def _start_heartbeat_mode(self) -> None:
        self._heartbeat_mode = True
        self._blink_timer.stop()
        self._blink_on = True
        if not self._heartbeat_timer.isActive():
            self._heartbeat_timer.start()
        self._heartbeat_decay_timer.start(260)
        self.update()

    def _toggle_heartbeat_blink(self) -> None:
        if not self._heartbeat_mode:
            self._heartbeat_timer.stop()
            return
        self._blink_on = not self._blink_on
        self.update()

    def _stop_heartbeat_mode(self) -> None:
        self._heartbeat_mode = False
        self._heartbeat_timer.stop()
        self._heartbeat_decay_timer.stop()
        self._blink_on = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)

            # The geometry is intentionally stylized around a simple 2-channel front
            # view rather than being a literal mechanical drawing of the enclosure.
            body_rect = self.rect().adjusted(1, 6, -1, -6)
            painter.setPen(QPen(QColor("#334b68"), 1.1))
            painter.setBrush(QColor("#183b67"))
            painter.drawRoundedRect(body_rect, 12, 12)

            inner_rect = body_rect.adjusted(8, 8, -8, -8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#214a79"))
            painter.drawRoundedRect(inner_rect, 8, 8)

            self._draw_port(
                painter,
                QPointF(inner_rect.left() + 30, inner_rect.center().y()),
                "#1e73be",
                "A",
                self._channel_a_enabled,
            )
            self._draw_port(
                painter,
                QPointF(inner_rect.left() + 76, inner_rect.center().y()),
                "#ef3340",
                "B",
                self._channel_b_enabled,
            )

            led_center = QPointF(inner_rect.right() - 16, inner_rect.center().y())
            led_color = QColor("#ef3340" if self._blink_on else "#6d3a3f")
            painter.setPen(QPen(QColor("#ffd7db" if self._blink_on else "#8f6a6f"), 1.0))
            painter.setBrush(led_color)
            painter.drawEllipse(led_center, 5.0, 5.0)
        finally:
            painter.end()

    def _draw_port(
        self,
        painter: QPainter,
        center: QPointF,
        color_hex: str,
        label: str,
        enabled: bool,
    ) -> None:
        ring_color = QColor(color_hex if enabled else "#9fb0c1")
        core_color = QColor("#f4f7fb")
        shell_color = QColor("#8f98a5")

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawEllipse(center, 12.0, 12.0)

        painter.setPen(QPen(ring_color, 2.0))
        painter.setBrush(QColor("#f7f9fc"))
        painter.drawEllipse(center, 9.6, 9.6)

        painter.setPen(QPen(shell_color, 1.0))
        painter.setBrush(QColor("#c9d0d8"))
        painter.drawEllipse(center, 5.1, 5.1)

        painter.setPen(QPen(QColor("#7b8491"), 1.0))
        painter.setBrush(core_color)
        painter.drawEllipse(center, 2.5, 2.5)

        label_rect = QRectF(center.x() - 24, center.y() - 8, 12, 16)
        painter.setPen(QPen(ring_color))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(label_rect, Qt.AlignCenter, label)


# Frontend: left corner buttons and strips

class ChannelControl(QFrame):
    changed = Signal()

    def __init__(self, channel_state: ChannelState) -> None:
        super().__init__()
        self.channel_name = channel_state.name
        self.color_hex = channel_state.color_hex
        self.setObjectName("channelStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.body = ClickableFrame()
        self.body.setObjectName("channelBody")
        self.body.setProperty("channelName", channel_state.name)
        self.body_layout = QGridLayout(self.body)
        self.body_layout.setContentsMargins(8, 4, 8, 4)
        self.body_layout.setHorizontalSpacing(8)
        self.body_layout.setVerticalSpacing(0)
        self.title_label = QLabel(channel_state.name)
        self.title_label.setObjectName("channelTitle")
        self.title_label.setStyleSheet(f"color: {channel_state.color_hex};")
        self.coupling_label = QLabel()
        self.coupling_label.setObjectName("channelMeta")
        self.range_label = QLabel()
        self.range_label.setObjectName("channelValue")
        self.probe_label = QLabel()
        self.probe_label.setObjectName("channelMeta")

        self.body_layout.addWidget(self.title_label, 0, 0)
        self.body_layout.addWidget(self.coupling_label, 0, 1, alignment=Qt.AlignRight)
        self.body_layout.addWidget(self.range_label, 1, 0)
        self.body_layout.addWidget(self.probe_label, 2, 1, alignment=Qt.AlignRight | Qt.AlignBottom)

        layout.addWidget(self.body, 1)

    def set_state(self, channel_state: ChannelState) -> None:
        if not channel_state.enabled:
            self.range_label.setText("Off")
            self.range_label.setStyleSheet(f"color: {channel_state.color_hex};")
        else:
            # Keep the strip tied to the selected scope range. The attenuator
            # scales the measured signal, not the chosen input-range setting.
            self.range_label.setText(format_voltage(channel_state.range_volts))
            self.range_label.setStyleSheet(f"color: {channel_state.color_hex};")
        self.coupling_label.setText(channel_state.coupling)
        self.probe_label.setText(channel_probe_label(channel_state))


class CustomChannelControl(QFrame):
    def __init__(self, custom_state: CustomChannelState) -> None:
        super().__init__()
        self.setObjectName("channelStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.body = ClickableFrame()
        self.body.setObjectName("channelBody")
        self.body.setProperty("channelName", "Custom")
        self.body_layout = QGridLayout(self.body)
        self.body_layout.setContentsMargins(8, 4, 8, 4)
        self.body_layout.setHorizontalSpacing(8)
        self.body_layout.setVerticalSpacing(0)

        self.title_label = QLabel("Custom Math Channel")
        self.title_label.setObjectName("channelTitle")
        self.source_label = QLabel()
        self.source_label.setObjectName("channelMeta")
        self.operation_label = QLabel()
        self.operation_label.setObjectName("channelValue")
        self.visibility_label = QLabel()
        self.visibility_label.setObjectName("channelMeta")

        self.body_layout.addWidget(self.title_label, 0, 0)
        self.body_layout.addWidget(self.source_label, 0, 1, alignment=Qt.AlignRight)
        self.body_layout.addWidget(self.operation_label, 1, 0)
        self.body_layout.addWidget(self.visibility_label, 2, 1, alignment=Qt.AlignRight | Qt.AlignBottom)
        layout.addWidget(self.body, 1)

        self.set_state(custom_state)

    def set_state(self, custom_state: CustomChannelState) -> None:
        self.title_label.setStyleSheet(f"color: {custom_state.color_hex};")
        self.operation_label.setStyleSheet(f"color: {custom_state.color_hex};")
        self.source_label.setText(f"Src {custom_state.source_channel}")
        self.visibility_label.setText("Show" if custom_state.show_source_channel else "Hide")
        self.operation_label.setText("Off" if not custom_state.enabled else f"From {custom_state.source_channel}")
        self.body.setStyleSheet(
            f"""
            QFrame#channelBody {{
                background: white;
                border-radius: 4px;
                min-width: 86px;
                border: 1px solid #d7dfe7;
                border-left: 3px solid {custom_state.color_hex};
            }}
            QFrame#channelBody:hover {{
                background: #f8fbff;
                border-color: #aac7e4;
                border-left: 3px solid {custom_state.color_hex};
            }}
            """
        )


class ModeControl(QFrame):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.setObjectName("modeStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.body = ClickableFrame()
        self.body.setObjectName("modeBody")
        body_layout = QGridLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 4)
        body_layout.setHorizontalSpacing(8)
        body_layout.setVerticalSpacing(0)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icon_pixmap("mode", 14))
        self.icon_label.setObjectName("controlIcon")
        self.title_label = QLabel("Mode")
        self.title_label.setObjectName("modeTitle")
        self.value_label = QLabel()
        self.value_label.setObjectName("modeValue")

        body_layout.addWidget(self.icon_label, 0, 0, 2, 1, alignment=Qt.AlignTop)
        body_layout.addWidget(self.title_label, 0, 1)
        body_layout.addWidget(self.value_label, 1, 1)

        layout.addWidget(self.body, 1)

        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        self.value_label.setText(mode)


class TriggerControl(QFrame):
    def __init__(self, trigger: TriggerState) -> None:
        super().__init__()
        self.setObjectName("triggerStrip")

        self.body = ClickableFrame()
        self.body.setObjectName("triggerBody")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body)

        body_layout = QGridLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 4)
        body_layout.setHorizontalSpacing(8)
        body_layout.setVerticalSpacing(0)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icon_pixmap("trigger", 14))
        self.icon_label.setObjectName("controlIcon")
        self.title_label = QLabel("Trigger")
        self.title_label.setObjectName("triggerTitle")
        self.value_label = QLabel()
        self.value_label.setObjectName("triggerValueSummary")

        body_layout.addWidget(self.icon_label, 0, 0, 2, 1, alignment=Qt.AlignTop)
        body_layout.addWidget(self.title_label, 0, 1)
        body_layout.addWidget(self.value_label, 1, 1)

        self.set_trigger(trigger)

    def set_trigger(self, trigger: TriggerState) -> None:
        self.value_label.setText(format_trigger_summary(trigger))


class TimingControl(QFrame):
    def __init__(self, time_per_div: float, sample_rate_hz: float) -> None:
        super().__init__()
        self.setObjectName("timingStrip")

        self.minus_button = QPushButton("-")
        self.minus_button.setObjectName("timingNavButton")
        self.body = ClickableFrame()
        self.body.setObjectName("timingBody")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.minus_button)
        layout.addWidget(self.body)
        self.plus_button = QPushButton("+")
        self.plus_button.setObjectName("timingNavButton")
        layout.addWidget(self.plus_button)

        body_layout = QGridLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 4)
        body_layout.setHorizontalSpacing(8)
        body_layout.setVerticalSpacing(0)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icon_pixmap("timing", 14))
        self.icon_label.setObjectName("controlIcon")
        self.title_label = QLabel("Timebase / Sample rate")
        self.title_label.setObjectName("timingTitle")
        self.timebase_value_label = QLabel()
        self.timebase_value_label.setObjectName("timingValue")
        self.sample_rate_value_label = QLabel()
        self.sample_rate_value_label.setObjectName("timingMeta")

        body_layout.addWidget(self.icon_label, 0, 0, 3, 1, alignment=Qt.AlignTop)
        body_layout.addWidget(self.title_label, 0, 1)
        body_layout.addWidget(self.timebase_value_label, 1, 1)
        body_layout.addWidget(self.sample_rate_value_label, 2, 1)

        self.set_values(time_per_div, sample_rate_hz)

    def set_values(self, time_per_div: float, sample_rate_hz: float) -> None:
        self.timebase_value_label.setText(format_time_per_div(time_per_div))
        self.sample_rate_value_label.setText(format_sample_rate(sample_rate_hz))

    def set_step_state(self, can_decrease: bool, can_increase: bool) -> None:
        self.minus_button.setEnabled(can_decrease)
        self.plus_button.setEnabled(can_increase)


class AnnotationControl(QFrame):
    def __init__(self, settings: AnnotationSettings) -> None:
        super().__init__()
        self.setObjectName("annotationStrip")

        self.body = ClickableFrame()
        self.body.setObjectName("annotationBody")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body)

        body_layout = QGridLayout(self.body)
        body_layout.setContentsMargins(8, 4, 8, 4)
        body_layout.setHorizontalSpacing(8)
        body_layout.setVerticalSpacing(0)

        self.title_label = QLabel("Annotations")
        self.title_label.setObjectName("annotationTitle")
        self.scope_label = QLabel()
        self.scope_label.setObjectName("annotationValue")
        self.tool_label = QLabel()
        self.tool_label.setObjectName("annotationMeta")

        body_layout.addWidget(self.title_label, 0, 0)
        body_layout.addWidget(self.scope_label, 1, 0)
        body_layout.addWidget(self.tool_label, 2, 0)

        self.set_annotation_settings(settings)

    def set_annotation_settings(self, settings: AnnotationSettings) -> None:
        self.scope_label.setText(settings.scope)
        self.tool_label.setText(settings.tool)


# Frontend: right-side contextual editor panels

class SelectionPanel(QFrame):
    hide_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("selectionPanel")
        self._timing_tab = "Timebase"
        self._timebase_unit_tab = "ms /div"
        self._timing_payload = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.title = QLabel("Selection")
        self.title.setObjectName("selectionPanelTitle")
        layout.addWidget(self.title)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        layout.addWidget(self.content)
        layout.addStretch(1)

        self.collapse_button = QPushButton("â—€", self)
        self.collapse_button.setObjectName("selectionPanelCollapseButton")
        self.collapse_button.setCursor(Qt.PointingHandCursor)
        self.collapse_button.clicked.connect(self.hide_requested)
        self.collapse_button.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        button_size = 22
        x = self.width() - (button_size // 2)
        y = max(8, (self.height() - button_size) // 2)
        self.collapse_button.setGeometry(x, y, button_size, button_size)

    def _clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._delete_layout(child_layout)

    def _delete_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._delete_layout(child_layout)

    @staticmethod
    def _matches(current: object, option: object) -> bool:
        if current is None or option is None:
            return current == option
        if isinstance(current, float) or isinstance(option, float):
            return math.isclose(float(current), float(option), rel_tol=1e-12, abs_tol=1e-12)
        return current == option

    def _add_section_label(self, text: str, tone: str = "default") -> None:
        label = QLabel(text)
        label.setObjectName("selectionPanelLabel")
        label.setProperty("tone", tone)
        self.content_layout.addWidget(label)

    def _add_option_grid(
        self,
        options: list[object],
        selected_value: object,
        formatter,
        callback,
        columns: int = 3,
        tone: str = "available",
        enabled: bool = True,
    ) -> None:
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, option in enumerate(options):
            button_text = formatter(option)
            button = QPushButton(button_text)
            button.setObjectName("selectorOptionButton")
            if isinstance(option, str):
                icon = load_icon(option) if enabled else disabled_icon(option)
                if not icon.isNull():
                    button.setIcon(icon)
                    button.setIconSize(QSize(18, 18))
                if not button_text:
                    button.setProperty("iconOnly", True)
                button.setToolTip(option)
            is_selected = self._matches(selected_value, option)
            button.setProperty("tone", tone)
            button.setProperty("selectedState", is_selected)
            button.setCheckable(enabled)
            button.setChecked(enabled and is_selected)
            if enabled:
                button.clicked.connect(lambda _checked=False, value=option: callback(value))
                button.setCursor(Qt.PointingHandCursor)
            else:
                button.setEnabled(False)
            grid.addWidget(button, index // columns, index % columns)
        self.content_layout.addWidget(grid_widget)

    def _add_segmented_options(
        self,
        options: list[str],
        selected_value: str,
        callback,
        enabled: bool = True,
        tone: str = "default",
    ) -> None:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for option in options:
            button = QPushButton(option)
            button.setObjectName("selectorSegmentButton")
            button.setProperty("tone", tone)
            button.setCheckable(enabled)
            button.setChecked(enabled and option == selected_value)
            button.setEnabled(enabled)
            if enabled:
                button.clicked.connect(lambda _checked=False, value=option: callback(value))
            row.addWidget(button)
        self.content_layout.addWidget(row_widget)

    def _add_off_button(self, callback, color_hex: str) -> None:
        button = QPushButton("Off")
        button.setObjectName("selectorOffButton")
        button.clicked.connect(callback)
        self.content_layout.addWidget(button)

    def _add_action_button(self, text: str, callback, tone: str = "default") -> None:
        button = QPushButton(text)
        button.setObjectName("selectorActionButton")
        button.setProperty("tone", tone)
        button.clicked.connect(callback)
        self.content_layout.addWidget(button)

    def _add_math_option_with_info(
        self,
        text: str,
        *,
        selected: bool,
        callback,
        help_text: str,
        tone: str = "custom",
    ) -> None:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        button = QPushButton(text)
        button.setObjectName("selectorOptionButton")
        button.setProperty("tone", tone)
        button.setProperty("selectedState", selected)
        button.setCheckable(True)
        button.setChecked(selected)
        button.clicked.connect(lambda _checked=False: callback(text))
        row.addWidget(button, 1)

        info_button = QPushButton("i")
        info_button.setObjectName("selectorInfoButton")
        info_button.setCursor(Qt.PointingHandCursor)
        info_button.clicked.connect(
            lambda _checked=False, btn=info_button: QToolTip.showText(
                btn.mapToGlobal(btn.rect().bottomLeft()),
                help_text,
                btn,
            )
        )
        row.addWidget(info_button)
        self.content_layout.addWidget(row_widget)

    def _add_color_grid(self, selected_color: str, callback, palette: list[tuple[str, str]] | None = None) -> None:
        palette = palette or ANNOTATION_COLORS
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, (label, color_hex) in enumerate(palette):
            is_selected = color_hex == selected_color
            button = QPushButton()
            button.setObjectName("selectorColorButton")
            button.setToolTip(label)
            border_color = "#111827" if is_selected else "#d7dfe7"
            border_width = 2 if is_selected else 1
            button.setStyleSheet(
                f"background: {color_hex}; border: {border_width}px solid {border_color}; border-radius: 9px;"
            )
            button.clicked.connect(lambda _checked=False, value=color_hex: callback(value))
            grid.addWidget(button, index // 5, index % 5)
        self.content_layout.addWidget(grid_widget)

    def _add_adjust_row(
        self,
        label_text: str,
        value_text: str,
        on_minus,
        on_plus,
        enabled: bool = True,
    ) -> None:
        self._add_section_label(label_text, tone="default" if enabled else "unavailable")
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        minus_button = QPushButton("-")
        minus_button.setObjectName("selectorAdjustButton")
        minus_button.setEnabled(enabled)
        if enabled:
            minus_button.clicked.connect(on_minus)

        value_label = QLabel(value_text)
        value_label.setObjectName("selectorAdjustValue")
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setProperty("disabledState", not enabled)

        plus_button = QPushButton("+")
        plus_button.setObjectName("selectorAdjustButton")
        plus_button.setEnabled(enabled)
        if enabled:
            plus_button.clicked.connect(on_plus)

        row.addWidget(minus_button)
        row.addWidget(value_label, 1)
        row.addWidget(plus_button)
        self.content_layout.addWidget(row_widget)

    def _add_text_entry_row(
        self,
        label_text: str,
        value_text: str,
        on_submit,
    ) -> None:
        self._add_section_label(label_text)
        entry = QLineEdit()
        entry.setObjectName("selectorTextEntry")
        entry.setText(value_text)
        entry.setPlaceholderText("Enter a number")
        entry.editingFinished.connect(lambda: on_submit(entry.text()))
        self.content_layout.addWidget(entry)

    def _set_timing_tab(self, tab: str) -> None:
        self._timing_tab = tab
        if self._timing_payload is not None:
            self.set_timing(*self._timing_payload)

    def _set_timebase_unit_tab(self, tab: str) -> None:
        self._timebase_unit_tab = tab
        if self._timing_payload is not None:
            self.set_timing(*self._timing_payload)

    def set_channel(
        self,
        channel_state: ChannelState,
        on_tab_select,
        on_voltage,
        on_coupling,
        on_invert,
        on_probe_scale,
        on_off,
    ) -> None:
        self.title.setText(f"Channel {channel_state.name}")
        self.title.setStyleSheet(f"color: {channel_state.color_hex};")
        self._clear_content()
        tone = "channelA" if channel_state.name == "A" else "channelB"
        self._add_segmented_options(CHANNEL_PANEL_TABS, channel_state.panel_tab, on_tab_select, tone=tone)
        selected_voltage = channel_state.range_volts if channel_state.enabled else None
        if channel_state.panel_tab == "Probes":
            self._add_section_label("Attenuators")
            self._add_option_grid(
                PROBE_SCALE_OPTIONS,
                channel_state.probe_scale,
                format_probe_scale,
                on_probe_scale,
                columns=3,
                tone=tone,
            )
            return

        self._add_section_label("Voltage")
        self._add_option_grid(
            channel_voltage_options(channel_state),
            selected_voltage,
            format_voltage,
            on_voltage,
            columns=3,
            tone=tone,
        )
        self._add_section_label("Coupling mode")
        self._add_segmented_options(["AC", "DC"], channel_state.coupling, on_coupling, tone=tone)
        self._add_section_label("Invert")
        self._add_segmented_options(
            ["Off", "On"],
            "On" if channel_state.invert else "Off",
            on_invert,
            tone=tone,
        )
        self._add_off_button(on_off, channel_state.color_hex)

    def set_custom_channel(
        self,
        custom_state: CustomChannelState,
        on_source_select,
        on_visibility_select,
        on_color_select,
        on_operation_select,
        on_method_select,
        on_strength_select,
        on_off,
    ) -> None:
        self.title.setText("Custom Math Channel")
        self.title.setStyleSheet(f"color: {custom_state.color_hex};")
        self._clear_content()
        self._add_section_label("Source channel")
        self._add_segmented_options(
            CUSTOM_CHANNEL_SOURCE_OPTIONS,
            custom_state.source_channel,
            on_source_select,
            tone="custom",
        )
        self._add_section_label("Source signal")
        self._add_segmented_options(
            CUSTOM_CHANNEL_VISIBILITY_OPTIONS,
            "Show" if custom_state.show_source_channel else "Hide",
            on_visibility_select,
            tone="custom",
        )
        self._add_section_label("Math")
        self._add_math_option_with_info(
            "Signal smoother",
            selected=custom_state.enabled and custom_state.operation == "Signal smoother",
            callback=on_operation_select,
            help_text=SIGNAL_SMOOTHER_HELP_TEXT,
            tone="custom",
        )
        if custom_state.enabled and custom_state.operation == "Signal smoother":
            self._add_section_label("Smoothing style")
            self._add_option_grid(
                [code for _label, code in SMOOTHING_METHOD_OPTIONS],
                custom_state.smoothing_method,
                lambda value: SMOOTHING_METHOD_LABELS[value],
                on_method_select,
                columns=2,
                tone="custom",
            )
            method_description = QLabel(SMOOTHING_METHOD_DESCRIPTIONS[custom_state.smoothing_method])
            method_description.setObjectName("footerText")
            method_description.setWordWrap(True)
            self.content_layout.addWidget(method_description)
            self._add_section_label("Smoothing strength")
            self._add_option_grid(
                [span for _label, span in SMOOTHING_STRENGTH_OPTIONS],
                custom_state.smoothing_span,
                lambda value: SMOOTHING_STRENGTH_LABELS[value],
                on_strength_select,
                columns=2,
                tone="custom",
            )
        self._add_section_label("Color")
        self._add_color_grid(custom_state.color_hex, on_color_select, palette=CUSTOM_CHANNEL_COLORS)
        self._add_off_button(on_off, custom_state.color_hex)

    def set_timing(
        self,
        time_per_div: float,
        sample_rate_hz: float,
        available_rates: list[float],
        compatible_rates: dict[str, list[float]],
        unavailable_rates: list[float],
        on_timebase_select,
        on_sample_rate_select,
        on_compatible_sample_rate_select,
    ) -> None:
        # Timebase and sample rate are edited in separate tabs to keep the panel
        # compact, but they still resolve through the same mode-aware pairing rules.
        self._timing_payload = (
            time_per_div,
            sample_rate_hz,
            available_rates,
            compatible_rates,
            unavailable_rates,
            on_timebase_select,
            on_sample_rate_select,
            on_compatible_sample_rate_select,
        )
        self.title.setText("Timebase / Sample rate")
        self.title.setStyleSheet("")
        self._clear_content()
        if self._timing_tab not in {"Timebase", "Sample rate"}:
            self._timing_tab = "Timebase"
        self._add_segmented_options(
            ["Timebase", "Sample rate"],
            self._timing_tab,
            self._set_timing_tab,
            tone="timebase",
        )
        if self._timing_tab == "Timebase":
            if self._timebase_unit_tab not in TIMEBASE_UNIT_GROUPS:
                self._timebase_unit_tab = timebase_unit_group(time_per_div)
            self._add_segmented_options(
                list(TIMEBASE_UNIT_GROUPS.keys()),
                self._timebase_unit_tab,
                self._set_timebase_unit_tab,
                tone="timebase",
            )
            self._add_option_grid(
                TIMEBASE_UNIT_GROUPS[self._timebase_unit_tab],
                time_per_div,
                format_time_per_div,
                on_timebase_select,
                columns=2,
                tone="timebase",
            )
            return

        self._add_section_label("Available", tone="available")
        self._add_option_grid(
            available_rates,
            sample_rate_hz,
            format_sample_rate,
            on_sample_rate_select,
            columns=2,
            tone="available",
            enabled=True,
        )
        for mode_name, rates in compatible_rates.items():
            self._add_section_label(f"Available in {mode_name}", tone="alternate")
            self._add_option_grid(
                rates,
                sample_rate_hz,
                format_sample_rate,
                lambda value, target_mode=mode_name: on_compatible_sample_rate_select(target_mode, value),
                columns=2,
                tone="alternate",
                enabled=True,
            )
        self._add_section_label("Not available", tone="unavailable")
        self._add_option_grid(
            unavailable_rates,
            sample_rate_hz,
            format_sample_rate,
            on_sample_rate_select,
            columns=2,
            tone="unavailable",
            enabled=False,
        )

    def set_waveform(
        self,
        max_waveforms: int,
        stored_waveforms: int,
        on_limit_mode_select,
        on_limit_submit,
    ) -> None:
        self.title.setText("Waveform")
        self.title.setStyleSheet("")
        self._clear_content()
        limit_mode = "Unlimited" if max_waveforms <= 0 else "Limited"
        self._add_section_label("Stored waveform limit")
        self._add_segmented_options(["Limited", "Unlimited"], limit_mode, on_limit_mode_select)
        if max_waveforms > 0:
            self._add_text_entry_row("Maximum stored waveforms", str(max_waveforms), on_limit_submit)
        info_label = QLabel(f"Stored waveforms: {stored_waveforms}")
        info_label.setObjectName("selectorAdjustValue")
        info_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(info_label)
        helper = QLabel("Use the preview row above the waveform to review and select saved captures.")
        helper.setObjectName("footerText")
        helper.setWordWrap(True)
        self.content_layout.addWidget(helper)

    def set_annotations(
        self,
        settings: AnnotationSettings,
        on_scope_select,
        on_tool_select,
        on_color_select,
        on_clear,
    ) -> None:
        self.title.setText("Annotations")
        self.title.setStyleSheet("")
        self._clear_content()
        self._add_section_label("Scope")
        self._add_segmented_options(ANNOTATION_SCOPES, settings.scope, on_scope_select)
        self._add_section_label("Tool")
        self._add_option_grid(
            ANNOTATION_TOOLS,
            settings.tool,
            lambda _value: "",
            on_tool_select,
            columns=2,
            tone="default",
        )
        self._add_section_label("Color")
        self._add_color_grid(settings.color_hex, on_color_select)
        self._add_action_button("Clear current scope", on_clear, tone="danger")

    def set_mode(self, mode: str, on_select) -> None:
        self.title.setText("Mode")
        self.title.setStyleSheet("")
        self._clear_content()
        self._add_section_label("Acquisition mode")
        self._add_option_grid(
            ACQUISITION_MODES,
            mode,
            lambda value: value,
            on_select,
            columns=1,
        )

    def set_trigger(
        self,
        trigger: TriggerState,
        on_mode_select,
        on_type_select,
        on_source_select,
        on_direction_select,
        on_level_step,
        on_lower_level_step,
        on_upper_level_step,
        on_pulse_width_type_select,
        on_pulse_lower_step,
        on_pulse_upper_step,
        on_logic_state_select,
        on_pre_trigger_step,
    ) -> None:
        self.title.setText("Trigger")
        self.title.setStyleSheet("")
        self._clear_content()
        trigger_enabled = trigger.mode != "None"
        self._add_section_label("Mode")
        self._add_option_grid(
            TRIGGER_MODES,
            trigger.mode,
            lambda value: value,
            on_mode_select,
            columns=2,
        )
        self._add_section_label("Type", tone="default" if trigger_enabled else "unavailable")
        self._add_option_grid(
            TRIGGER_TYPES,
            trigger.trigger_type,
            lambda value: value,
            on_type_select,
            columns=2,
            tone="available" if trigger_enabled else "unavailable",
            enabled=trigger_enabled,
        )
        if trigger.trigger_type in ("Simple edge", "Advanced edge", "Window", "Pulse width"):
            self._add_section_label("Source", tone="default" if trigger_enabled else "unavailable")
            self._add_segmented_options(TRIGGER_SOURCES, trigger.source, on_source_select, enabled=trigger_enabled)
        if trigger.trigger_type in ("Simple edge", "Advanced edge", "Pulse width"):
            self._add_adjust_row(
                "Threshold",
                f"{trigger.level_volts:+.1f} V".replace("+0.0", "0.0"),
                lambda: on_level_step(-1),
                lambda: on_level_step(1),
                enabled=trigger_enabled,
            )
        if trigger.trigger_type == "Window":
            self._add_adjust_row(
                "Lower threshold",
                f"{trigger.lower_level_volts:+.1f} V".replace("+0.0", "0.0"),
                lambda: on_lower_level_step(-1),
                lambda: on_lower_level_step(1),
                enabled=trigger_enabled,
            )
            self._add_adjust_row(
                "Upper threshold",
                f"{trigger.upper_level_volts:+.1f} V".replace("+0.0", "0.0"),
                lambda: on_upper_level_step(-1),
                lambda: on_upper_level_step(1),
                enabled=trigger_enabled,
            )
        direction_options = trigger_direction_options(trigger.trigger_type)
        if direction_options:
            self._add_section_label("Direction", tone="default" if trigger_enabled else "unavailable")
            self._add_segmented_options(
                direction_options,
                trigger.direction,
                on_direction_select,
                enabled=trigger_enabled,
            )
        if trigger.trigger_type == "Pulse width":
            self._add_section_label("Pulse width type", tone="default" if trigger_enabled else "unavailable")
            self._add_option_grid(
                PULSE_WIDTH_TYPES,
                trigger.pulse_width_type,
                lambda value: value,
                on_pulse_width_type_select,
                columns=2,
                tone="available" if trigger_enabled else "unavailable",
                enabled=trigger_enabled,
            )
            self._add_adjust_row(
                "Lower count",
                str(trigger.pulse_width_lower),
                lambda: on_pulse_lower_step(-1),
                lambda: on_pulse_lower_step(1),
                enabled=trigger_enabled,
            )
            self._add_adjust_row(
                "Upper count",
                str(trigger.pulse_width_upper),
                lambda: on_pulse_upper_step(-1),
                lambda: on_pulse_upper_step(1),
                enabled=trigger_enabled,
            )
        if trigger.trigger_type == "Logic":
            self._add_section_label("Channel A", tone="default" if trigger_enabled else "unavailable")
            self._add_segmented_options(
                TRIGGER_LOGIC_STATES,
                trigger.logic_a_state,
                lambda value: on_logic_state_select("A", value),
                enabled=trigger_enabled,
            )
            self._add_section_label("Channel B", tone="default" if trigger_enabled else "unavailable")
            self._add_segmented_options(
                TRIGGER_LOGIC_STATES,
                trigger.logic_b_state,
                lambda value: on_logic_state_select("B", value),
                enabled=trigger_enabled,
            )
        self._add_adjust_row(
            "Pre-trigger",
            f"{trigger.pre_trigger_percent} %",
            lambda: on_pre_trigger_step(-1),
            lambda: on_pre_trigger_step(1),
            enabled=trigger_enabled,
        )



