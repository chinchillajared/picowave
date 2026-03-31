from __future__ import annotations

import copy
import math
from typing import Optional

from PySide6.QtCore import QEvent, QSize, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from picowave.config import *
from picowave.controller import Pico2204AController
from picowave.helpers import *
from picowave.logging_config import UI_LOGGER
from picowave.models import *
from picowave.processing import *
from picowave.ui.canvas import WaveformCanvas
from picowave.ui.components import (
    ChannelControl,
    CustomChannelControl,
    ModeControl,
    ScopeFrontStatusWidget,
    SelectionPanel,
    TimingControl,
    TriggerControl,
    WaveformHistoryControl,
    WaveformPreviewStrip,
)
from picowave.ui.dialogs import AboutDialog, ScopeConnectDialog
from picowave.worker import AcquisitionThread
class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: Optional[Pico2204AController] = None,
        autostart_worker: bool = True,
        show_connect_dialog_on_start: bool = False,
    ) -> None:
        super().__init__()
        self.state = ScopeState()
        self.selected_panel: tuple[str, str | None] | None = None
        self._outside_close_armed = False
        self._invalid_flash_token = 0
        self._mode_invalid = False
        self._timing_invalid = False
        self._run_button_hint_text: str | None = None
        self._default_hint_text = "Channel tiles toggle on and off. Trigger tile cycles modes."
        self._hint_text = self._default_hint_text
        self._hint_error = False
        self._manual_all_channels_off = False
        self.annotation_settings = AnnotationSettings()
        self.waveform_annotations: dict[int, list[AnnotationStroke | AnnotationText]] = {}
        self.global_annotations: list[AnnotationStroke | AnnotationText] = []
        self.connection_text = "Hardware: Connect a PicoScope 2204A to begin capture."
        self.history: list[CaptureFrame] = [
            build_empty_frame(self.state, "Hardware", "Connect a PicoScope 2204A to begin capture.")
        ]
        self.history_index = 0
        self.controller = controller or Pico2204AController()
        self.worker = AcquisitionThread(self.controller, self.state)
        self.worker.frame_ready.connect(self.on_frame_ready)
        self.worker.capture_failed.connect(self.on_capture_failed)
        self.about_dialog = AboutDialog(self)
        self.connect_dialog = ScopeConnectDialog(self)
        self.connect_dialog.refresh_button.clicked.connect(self.refresh_connect_dialog_devices)

        self.setWindowTitle("PicoWave")
        self.resize(1800, 900)
        self._build_ui()
        self._apply_styles()
        self._sync_ui()
        QApplication.instance().installEventFilter(self)

        self._worker_started = False
        if autostart_worker:
            self.worker.start()
            self._worker_started = True
        if show_connect_dialog_on_start:
            QTimer.singleShot(0, self.show_connect_dialog)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker_started:
            self.worker.shutdown()
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.MouseButtonPress
            and self.selected_panel is not None
            and self._outside_close_armed
            and hasattr(event, "button")
            and event.button() == Qt.LeftButton
        ):
            target = watched if isinstance(watched, QWidget) else None
            if (
                target is not None
                and self.selected_panel == ("annotations", None)
                and self._is_widget_in_branch(target, self.waveform_canvas)
            ):
                return super().eventFilter(watched, event)
            if target is not None and not self._is_panel_related_click(target):
                self.selected_panel = None
                self._outside_close_armed = False
                self._sync_ui()
        return super().eventFilter(watched, event)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralShell")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(8)

        content = QHBoxLayout()
        content.setSpacing(8)

        left_column = QHBoxLayout()
        left_column.setSpacing(8)
        left_column.setAlignment(Qt.AlignTop)

        controls_strip = QVBoxLayout()
        controls_strip.setSpacing(8)
        controls_strip.setAlignment(Qt.AlignTop)

        # Frontend: top-left primary action buttons.
        top_controls = QHBoxLayout()
        top_controls.setSpacing(8)

        self.run_button = QPushButton("stopped")
        self.run_button.setObjectName("runButton")
        self.run_button.setCursor(Qt.PointingHandCursor)
        self.run_button.setIconSize(QSize(14, 14))
        self.run_button.clicked.connect(self.toggle_running)
        top_controls.addWidget(self.run_button)

        controls_strip.addLayout(top_controls)

        # Frontend: left corner buttons that open the contextual editor column.
        self.mode_control = ModeControl(self.state.acquisition_mode)
        self.mode_control.body.clicked.connect(self.select_mode_panel)
        controls_strip.addWidget(self.mode_control)

        self.trigger_control = TriggerControl(self.state.trigger)
        self.trigger_control.body.clicked.connect(self.select_trigger_panel)
        controls_strip.addWidget(self.trigger_control)

        self.timing_control = TimingControl(self.state.time_per_div, self.state.sample_rate_hz)
        self.timing_control.body.clicked.connect(self.select_timing_panel)
        self.timing_control.minus_button.clicked.connect(lambda: self.adjust_time_per_div(-1))
        self.timing_control.plus_button.clicked.connect(lambda: self.adjust_time_per_div(1))
        controls_strip.addWidget(self.timing_control)

        self.channel_a_control = ChannelControl(self.state.channel_a)
        self.channel_b_control = ChannelControl(self.state.channel_b)
        self.custom_channel_control = CustomChannelControl(self.state.custom_channel)
        self.channel_a_control.body.clicked.connect(
            lambda: self.select_channel("A")
        )
        self.channel_b_control.body.clicked.connect(
            lambda: self.select_channel("B")
        )
        self.custom_channel_control.body.clicked.connect(
            lambda: self.select_channel("Custom")
        )
        controls_strip.addWidget(self.channel_a_control)
        controls_strip.addWidget(self.channel_b_control)
        controls_strip.addWidget(self.custom_channel_control)
        controls_strip.addStretch(1)
        left_column.addLayout(controls_strip)

        # Frontend: contextual side panel used by channels, timing, mode, and trigger.
        self.selection_panel = SelectionPanel()
        self.selection_panel.setFixedWidth(250)
        self.selection_panel.hide_requested.connect(self.hide_selection_panel)
        self.selection_panel.hide()
        left_column.addWidget(self.selection_panel)
        left_column_widget = QWidget()
        left_column_widget.setLayout(left_column)
        content.addWidget(left_column_widget)

        right_column = QVBoxLayout()
        right_column.setSpacing(8)

        # Frontend: top row above the waveform.
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        self.waveform_history_control = WaveformHistoryControl()
        self.waveform_history_control.minus_button.clicked.connect(lambda: self.adjust_history(-1))
        self.waveform_history_control.plus_button.clicked.connect(lambda: self.adjust_history(1))
        self.waveform_history_control.body.clicked.connect(self.select_waveform_panel)
        top_bar.addWidget(self.waveform_history_control)
        self.scope_front_status = ScopeFrontStatusWidget()
        top_bar.addWidget(self.scope_front_status)
        top_bar.addStretch(1)
        logo = QLabel("PicoWave")
        logo.setObjectName("logoText")
        top_bar.addWidget(logo)
        header_panel = QWidget()
        header_panel.setFixedHeight(72)
        header_panel.setLayout(top_bar)
        right_column.addWidget(header_panel)

        waveform_preview_row = QHBoxLayout()
        waveform_preview_row.setContentsMargins(0, 0, 0, 0)
        waveform_preview_row.setSpacing(0)
        self.waveform_preview_prev_button = QPushButton("<")
        self.waveform_preview_prev_button.setObjectName("waveformPreviewNavButton")
        self.waveform_preview_prev_button.clicked.connect(self.show_previous_waveform_preview_page)
        waveform_preview_row.addWidget(self.waveform_preview_prev_button)
        self.waveform_preview_body = QFrame()
        self.waveform_preview_body.setObjectName("waveformPreviewBody")
        waveform_preview_body_layout = QVBoxLayout(self.waveform_preview_body)
        waveform_preview_body_layout.setContentsMargins(0, 0, 0, 0)
        waveform_preview_body_layout.setSpacing(0)
        self.waveform_preview_strip = WaveformPreviewStrip()
        self.waveform_preview_strip.waveform_selected.connect(self.select_history_frame)
        self.waveform_preview_strip.page_requested.connect(self.page_waveform_previews)
        waveform_preview_body_layout.addWidget(self.waveform_preview_strip)
        waveform_preview_row.addWidget(self.waveform_preview_body, 1)
        self.waveform_preview_next_button = QPushButton(">")
        self.waveform_preview_next_button.setObjectName("waveformPreviewNavButton")
        self.waveform_preview_next_button.clicked.connect(self.show_next_waveform_preview_page)
        waveform_preview_row.addWidget(self.waveform_preview_next_button)
        self.waveform_preview_container = QWidget()
        self.waveform_preview_container.setLayout(waveform_preview_row)
        right_column.addWidget(self.waveform_preview_container)

        # Frontend: central waveform viewer.
        self.waveform_canvas = WaveformCanvas()
        self.waveform_canvas.annotation_button_clicked.connect(self.select_annotation_panel)
        self.waveform_canvas.annotation_interaction_started.connect(self.start_annotation_interaction)
        self.waveform_canvas.zoom_box_mode_changed.connect(self.handle_zoom_box_mode_changed)
        self.waveform_canvas.vertical_offset_changed.connect(self.set_channel_vertical_offset)
        self.waveform_canvas.channel_display_zoom_changed.connect(self.set_channel_display_zoom)
        self.waveform_canvas.trigger_level_changed.connect(self.set_trigger_level_value)
        self.waveform_canvas.trigger_pre_trigger_changed.connect(self.set_pre_trigger_percent_value)
        right_column.addWidget(self.waveform_canvas, 1)
        content.addLayout(right_column, 1)
        root.addLayout(content, 1)

        # Frontend: footer status and help text.
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.about_button = QPushButton("About")
        self.about_button.setObjectName("aboutButton")
        self.about_button.setCursor(Qt.PointingHandCursor)
        self.about_button.setIcon(load_icon("about"))
        self.about_button.setIconSize(QSize(14, 14))
        self.about_button.clicked.connect(self.show_about_dialog)
        self.connection_label = QLabel()
        self.connection_label.setObjectName("footerText")
        self.hint_label = QLabel(self._hint_text)
        self.hint_label.setObjectName("footerText")
        footer.addWidget(self.about_button)
        footer.addWidget(self.connection_label, 1)
        footer.addWidget(self.hint_label)
        root.addLayout(footer)

    # Frontend: application-wide stylesheet for all custom widgets.

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, #centralShell {
                background: #f7f9fc;
                color: #18334d;
                font-family: "Segoe UI";
            }
            QLabel {
                background: transparent;
            }
            #runButton {
                background: white;
                border: 2px solid #c1292e;
                border-radius: 6px;
                color: #c1292e;
                min-width: 96px;
                min-height: 44px;
                padding: 6px 16px;
                font-size: 16px;
                font-weight: 600;
            }
            #runButton[hint="true"] {
                background: #fff1f3;
                border-color: #c1292e;
                color: #c1292e;
                min-width: 172px;
                font-size: 12px;
                font-weight: 700;
            }
            #runButton[running="true"] {
                background: white;
                border-color: #119822;
                color: #119822;
            }
            #runButton:hover {
                background: #f8fbff;
                border-color: #a61f24;
            }
            #runButton[running="true"]:hover {
                background: #f8fbff;
                border-color: #0e7f1c;
            }
            #runButton:disabled {
                background: #e5e7eb;
                border-color: #c7ced6;
                color: #9aa3ad;
            }
            #connectButton {
                background: white;
                border: 2px solid #9ca3af;
                border-radius: 6px;
                color: #4b5563;
                min-width: 142px;
                max-width: 142px;
                min-height: 44px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            #connectButton:hover {
                background: #f8fbff;
                border-color: #7b8794;
            }
            #connectButton[connected="true"] {
                background: white;
                border-color: #119822;
                color: #119822;
            }
            #connectButton[connected="true"]:hover {
                background: #f8fbff;
                border-color: #0e7f1c;
            }
            #waveformHistoryStrip {
                background: transparent;
            }
            #waveformHistoryNavButton {
                background: #1e73be;
                border: 1px solid #1e73be;
                color: white;
                min-width: 28px;
                max-width: 28px;
                min-height: 56px;
                max-height: 56px;
                font-size: 20px;
                font-weight: 700;
            }
            #waveformHistoryNavButton:hover {
                background: #185f9d;
                border-color: #185f9d;
            }
            #waveformHistoryNavButton:disabled {
                background: #c9d0d8;
                border-color: #c9d0d8;
                color: #eef2f6;
            }
            #waveformHistoryBody {
                background: white;
                border: 1px solid #d7dfe7;
                min-width: 86px;
                max-width: 86px;
                min-height: 56px;
                max-height: 56px;
            }
            #waveformHistoryTitle {
                color: #51657a;
                font-size: 9px;
            }
            #waveformHistoryValue {
                color: #1a4770;
                font-size: 12px;
                font-weight: 700;
            }
            #waveformPreviewBody {
                background: white;
                border-top: 1px solid #d7dfe7;
                border-bottom: 1px solid #d7dfe7;
            }
            #waveformPreviewNavButton {
                background: #1e73be;
                border: 1px solid #1e73be;
                color: white;
                min-width: 28px;
                max-width: 28px;
                min-height: 82px;
                max-height: 82px;
                font-size: 14px;
                font-weight: 700;
                padding: 0px;
            }
            #waveformPreviewNavButton:hover {
                background: #185f9d;
                border-color: #185f9d;
            }
            #waveformPreviewNavButton:disabled {
                background: #c9d0d8;
                border-color: #c9d0d8;
                color: #eef2f6;
            }
            #annotationCanvasButton {
                background: white;
                border: 1px solid #b7c7d8;
                border-radius: 13px;
                padding: 0px;
            }
            #zoomCanvasButton {
                background: white;
                border: 1px solid #b7c7d8;
                border-radius: 13px;
                padding: 0px;
            }
            #annotationCanvasButton:hover {
                background: #f8fbff;
                border-color: #0e66b2;
            }
            #zoomCanvasButton:hover {
                background: #f8fbff;
                border-color: #0e66b2;
            }
            #annotationCanvasButton[active="true"] {
                background: #e8f3ff;
                border-color: #0e66b2;
            }
            #zoomCanvasButton[active="true"] {
                background: #e8f3ff;
                border-color: #0e66b2;
            }
            #zoomStatusPanel {
                background: #ffffff;
                border: 1px solid #7db0e0;
                border-radius: 0px;
            }
            #zoomPanelTitle {
                background: #1e73be;
                color: white;
                padding: 0px 6px;
                font-size: 12px;
                font-weight: 700;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
            }
            #zoomStatusContent {
                background: white;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
            }
            #zoomOverviewWidget {
                background: white;
                border: 1px solid #b7c7d8;
            }
            #zoomMiniRoundButton {
                background: #1e73be;
                border: 1px solid #1e73be;
                border-radius: 12px;
                color: white;
                min-width: 24px;
                max-width: 24px;
                min-height: 24px;
                max-height: 24px;
                font-size: 18px;
                font-weight: 700;
                padding: 0px;
            }
            #zoomMiniRoundButton:hover {
                background: #185f9d;
                border-color: #185f9d;
            }
            #zoomResetButton {
                background: #1e73be;
                border: 1px solid #1e73be;
                border-radius: 6px;
                color: white;
                font-size: 12px;
                font-weight: 700;
                min-width: 58px;
                min-height: 28px;
                padding: 1px 8px 3px 8px;
            }
            #zoomResetButton:hover {
                background: #185f9d;
                border-color: #185f9d;
            }
            #modeStrip {
                background: white;
                border-radius: 4px;
            }
            #modeBody {
                background: white;
                border: 1px solid #d7dfe7;
                border-radius: 4px;
                min-width: 196px;
            }
            #modeBody[invalid="true"] {
                background: #fff1f3;
                border: 2px solid #c1292e;
            }
            #modeBody:hover {
                background: #f8fbff;
                border-color: #aac7e4;
            }
            #modeTitle {
                color: #51657a;
                font-size: 11px;
            }
            #modeValue {
                color: #1a4770;
                font-size: 16px;
                font-weight: 700;
            }
            #triggerStrip {
                background: white;
                border-radius: 4px;
            }
            #triggerBody {
                background: white;
                border: 1px solid #d7dfe7;
                border-radius: 4px;
                min-width: 228px;
            }
            #triggerBody:hover {
                background: #f8fbff;
                border-color: #aac7e4;
            }
            #triggerTitle {
                color: #51657a;
                font-size: 11px;
            }
            #triggerValueSummary {
                color: #1a4770;
                font-size: 16px;
                font-weight: 700;
            }
            #timingStrip {
                background: white;
                border-radius: 4px;
            }
            #timingBody {
                background: white;
                border: 1px solid #d7dfe7;
                border-radius: 0px;
                min-width: 196px;
                min-height: 56px;
            }
            #timingBody[invalid="true"] {
                background: #fff1f3;
                border: 2px solid #c1292e;
            }
            #timingBody:hover {
                background: #f8fbff;
                border-color: #aac7e4;
            }
            #annotationStrip {
                background: white;
                border-radius: 4px;
            }
            #annotationBody {
                background: white;
                border: 1px solid #d7dfe7;
                border-radius: 4px;
                min-width: 196px;
            }
            #annotationBody:hover {
                background: #f8fbff;
                border-color: #aac7e4;
            }
            #annotationTitle {
                color: #51657a;
                font-size: 11px;
            }
            #annotationValue {
                color: #1a4770;
                font-size: 16px;
                font-weight: 700;
            }
            #annotationMeta {
                color: #1a4770;
                font-size: 12px;
                font-weight: 600;
            }
            #timingTitle {
                color: #51657a;
                font-size: 11px;
            }
            #timingValue {
                color: #1a4770;
                font-size: 16px;
                font-weight: 700;
            }
            #timingMeta {
                color: #1a4770;
                font-size: 12px;
                font-weight: 600;
            }
            #timingNavButton {
                background: #1e73be;
                border: 1px solid #1e73be;
                color: white;
                min-width: 28px;
                max-width: 28px;
                min-height: 56px;
                max-height: 56px;
                font-size: 20px;
                font-weight: 700;
            }
            #timingNavButton:hover {
                background: #185f9d;
                border-color: #185f9d;
            }
            #timingNavButton:disabled {
                background: #c9d0d8;
                border-color: #c9d0d8;
                color: #eef2f6;
            }
            #controlIcon {
                background: transparent;
                min-width: 16px;
                max-width: 16px;
            }
            #statusCard {
                background: white;
                border: 2px solid #ef3340;
                border-radius: 5px;
                min-width: 112px;
            }
            #statusCard[running="true"] {
                border-color: #2a9d5b;
            }
            #statusTitle {
                color: #ef3340;
                font-size: 15px;
                font-weight: 500;
            }
            #scopeCard, #triggerCard, #waveformCard {
                background: #c8e0f7;
                border-radius: 5px;
            }
            #cardTitle, #metricTitle, #detailText {
                color: #51657a;
                font-size: 11px;
            }
            #scopeValue, #triggerValue, #waveformValue {
                color: #1a4770;
                font-size: 18px;
                font-weight: 700;
            }
            #metricValue {
                color: #1a4770;
                font-size: 12px;
                font-weight: 700;
            }
            #logoText {
                color: #92a0ad;
                font-size: 38px;
                font-family: Georgia;
                font-weight: 600;
                margin-right: 8px;
            }
            #channelStrip {
                background: white;
                border-radius: 4px;
            }
            #channelBody {
                background: white;
                border-radius: 4px;
                min-width: 86px;
                border: 1px solid #d7dfe7;
            }
            #channelBody:hover {
                background: #f8fbff;
                border-color: #aac7e4;
            }
            #channelBody[channelName="A"] {
                border-left: 3px solid #1e73be;
            }
            #channelBody[channelName="B"] {
                border-left: 3px solid #ef3340;
            }
            #channelBody[channelName="Custom"] {
                border-left: 3px solid #d97706;
            }
            #selectionPanel {
                background: #ffffff;
                border: 1px solid #d7dfe7;
                border-radius: 8px;
            }
            #selectionPanelCollapseButton {
                background: #ffffff;
                border: 1px solid #c7d2df;
                border-radius: 11px;
                color: #1a4770;
                font-size: 10px;
                font-weight: 700;
                padding: 0px;
            }
            #selectionPanelCollapseButton:hover {
                background: #f8fbff;
                border-color: #9eb7d2;
            }
            #selectionPanelTitle {
                color: #18334d;
                font-size: 13px;
                font-weight: 700;
            }
            #selectionPanelLabel {
                color: #51657a;
                font-size: 11px;
                font-weight: 700;
            }
            #selectionPanelLabel[tone="available"] {
                color: #119822;
            }
            #selectionPanelLabel[tone="timebase"] {
                color: #3b82f6;
            }
            #selectionPanelLabel[tone="alternate"] {
                color: #b77905;
            }
            #selectionPanelLabel[tone="unavailable"] {
                color: #94a3b8;
            }
            #selectorOptionButton {
                background: #ffffff;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                min-height: 30px;
                padding: 4px 8px;
                color: #1a4770;
                font-size: 12px;
                font-weight: 600;
            }
            #selectorOptionButton[iconOnly="true"] {
                min-width: 44px;
                max-width: 44px;
                min-height: 38px;
                padding: 6px;
            }
            #selectorOptionButton:checked {
                background: #1e73be;
                border-color: #1e73be;
                color: white;
            }
            #selectorOptionButton[tone="available"] {
                background: #ffffff;
                border-color: #7ec98a;
                color: #119822;
            }
            #selectorOptionButton[tone="timebase"] {
                background: #ffffff;
                border-color: #93c5fd;
                color: #2563eb;
            }
            #selectorOptionButton[tone="timebase"][selectedState="true"] {
                background: #93c5fd;
                border-color: #60a5fa;
                color: #1e3a8a;
            }
            #selectorOptionButton[tone="available"][selectedState="true"] {
                background: #119822;
                border-color: #119822;
                color: white;
            }
            #selectorOptionButton[tone="channelA"] {
                background: #ffffff;
                border-color: #8bbbe8;
                color: #1e73be;
            }
            #selectorOptionButton[tone="channelA"][selectedState="true"] {
                background: #1e73be;
                border-color: #1e73be;
                color: white;
            }
            #selectorOptionButton[tone="channelB"] {
                background: #ffffff;
                border-color: #f3aab1;
                color: #ef3340;
            }
            #selectorOptionButton[tone="channelB"][selectedState="true"] {
                background: #ef3340;
                border-color: #ef3340;
                color: white;
            }
            #selectorOptionButton[tone="custom"] {
                background: #ffffff;
                border-color: #e9b86b;
                color: #b45309;
            }
            #selectorOptionButton[tone="custom"][selectedState="true"] {
                background: #d97706;
                border-color: #d97706;
                color: white;
            }
            #selectorOptionButton[tone="alternate"] {
                background: #ffffff;
                border-color: #e6c861;
                color: #a16207;
            }
            #selectorOptionButton[tone="alternate"][selectedState="true"] {
                background: #f3d978;
                border-color: #d4a017;
                color: #7a5200;
            }
            #selectorOptionButton[tone="unavailable"] {
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorOptionButton[tone="unavailable"][selectedState="true"] {
                background: #ffffff;
                border-color: #9ca3af;
                color: #6b7280;
            }
            #selectorOptionButton:hover {
                background: #f8fbff;
                border-color: #9eb7d2;
            }
            #selectorOptionButton:checked:hover {
                background: #1b67ab;
                border-color: #1b67ab;
            }
            #selectorOptionButton:disabled {
                opacity: 1.0;
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorOptionButton:disabled:checked {
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorSegmentButton {
                background: white;
                border: 1px solid #c7d2df;
                min-height: 30px;
                color: #1a4770;
                font-size: 12px;
                font-weight: 600;
            }
            #selectorSegmentButton:checked {
                background: #1e73be;
                border-color: #1e73be;
                color: white;
            }
            #selectorSegmentButton[tone="channelA"]:checked {
                background: #1e73be;
                border-color: #1e73be;
                color: white;
            }
            #selectorSegmentButton[tone="channelB"]:checked {
                background: #ef3340;
                border-color: #ef3340;
                color: white;
            }
            #selectorSegmentButton[tone="custom"]:checked {
                background: #d97706;
                border-color: #d97706;
                color: white;
            }
            #selectorSegmentButton:hover {
                background: #f8fbff;
                border-color: #9eb7d2;
            }
            #selectorSegmentButton:checked:hover {
                background: #1b67ab;
                border-color: #1b67ab;
            }
            #selectorSegmentButton[tone="channelB"]:checked:hover {
                background: #d92d39;
                border-color: #d92d39;
            }
            #selectorSegmentButton[tone="custom"]:checked:hover {
                background: #c46805;
                border-color: #c46805;
            }
            #selectorSegmentButton:first-child {
                border-top-left-radius: 4px;
                border-bottom-left-radius: 4px;
            }
            #selectorSegmentButton:last-child {
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }
            #selectorSegmentButton:disabled {
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorSegmentButton:disabled:checked {
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorAdjustButton {
                background: #9ec4ec;
                border: 1px solid #93b8df;
                border-radius: 4px;
                color: white;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                font-size: 16px;
                font-weight: 700;
            }
            #selectorAdjustButton:hover {
                background: #7fb0e2;
                border-color: #6f9fce;
            }
            #selectorAdjustButton:disabled {
                background: #e5e7eb;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorAdjustValue {
                background: white;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                color: #1a4770;
                min-height: 28px;
                font-size: 12px;
                font-weight: 600;
                padding: 2px 8px;
            }
            #selectorAdjustValue[disabledState="true"] {
                background: #ffffff;
                border-color: #d1d5db;
                color: #9ca3af;
            }
            #selectorTextEntry {
                background: white;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                color: #1a4770;
                min-height: 28px;
                font-size: 12px;
                font-weight: 600;
                padding: 2px 8px;
            }
            #selectorTextEntry:focus {
                border-color: #1e73be;
            }
            #selectorOffButton {
                background: #fde1e4;
                border: 1px solid #ef3340;
                border-radius: 4px;
                min-height: 30px;
                font-weight: 600;
                color: #ef3340;
            }
            #selectorOffButton:hover {
                background: #f9cfd4;
                border-color: #d92d39;
            }
            #selectorActionButton {
                background: white;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                min-height: 30px;
                color: #1a4770;
                font-size: 12px;
                font-weight: 600;
            }
            #selectorActionButton[tone="danger"] {
                color: #c1292e;
                border-color: #ef3340;
            }
            #selectorActionButton:hover {
                background: #f8fbff;
                border-color: #9eb7d2;
            }
            #selectorActionButton[tone="danger"]:hover {
                background: #fde1e4;
                border-color: #ef3340;
            }
            #selectorInfoButton {
                background: white;
                border: 1px solid #93c5fd;
                border-radius: 10px;
                min-width: 20px;
                max-width: 20px;
                min-height: 20px;
                max-height: 20px;
                padding: 0px;
                color: #1e73be;
                font-size: 11px;
                font-weight: 700;
            }
            #selectorInfoButton:hover {
                background: #f0f8ff;
                border-color: #60a5fa;
            }
            #selectorColorButton {
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
            }
            #channelTitle {
                font-size: 14px;
                font-weight: 700;
            }
            #channelValue {
                font-size: 22px;
                font-weight: 700;
            }
            #channelMeta {
                color: #68798c;
                font-size: 11px;
            }
            #footerText {
                color: #51657a;
                font-size: 12px;
            }
            #footerText[error="true"] {
                color: #c1292e;
            }
            #aboutButton {
                background: white;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                color: #1a4770;
                min-width: 72px;
                min-height: 24px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            #aboutButton:hover {
                background: #f8fbff;
                border-color: #9eb7d2;
            }
            #aboutDialog {
                background: #ffffff;
            }
            #aboutTitle {
                color: #18334d;
                font-size: 16px;
                font-weight: 700;
            }
            #aboutTabButton {
                background: #ffffff;
                border: 1px solid #c7d2df;
                border-radius: 4px;
                color: #1a4770;
                min-height: 28px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            #aboutTabButton[selected="true"] {
                background: #1e73be;
                border-color: #1e73be;
                color: white;
            }
            #aboutContent {
                background: #f8fbff;
                border: 1px solid #d7dfe7;
                border-radius: 6px;
                color: #18334d;
                padding: 10px;
                font-size: 12px;
            }
            """
        )

    def _current_frame(self) -> CaptureFrame:
        return self.history[self.history_index]

    def _history_display_state(self) -> tuple[int, int]:
        captured_indices = [index for index, frame in enumerate(self.history) if frame.sample_count > 0]
        if not captured_indices:
            return 0, 0
        total_count = len(captured_indices)
        if self.history_index not in captured_indices:
            return 1, total_count
        current_index = captured_indices.index(self.history_index) + 1
        return current_index, total_count

    def _push_frame(self, frame: CaptureFrame) -> None:
        if len(self.history) == 1 and self.history[0].sample_count == 0 and frame.sample_count > 0:
            self.history = [frame]
            self.history_index = 0
            return
        self.history.append(frame)
        self._enforce_history_limit()
        self.history_index = len(self.history) - 1

    def _reset_waveform_history(self) -> None:
        self.history = [
            build_empty_frame(self.state, "Hardware", "Capture history cleared after timebase change.")
        ]
        self.history_index = 0

    def _stored_waveform_count(self) -> int:
        return sum(1 for frame in self.history if frame.sample_count > 0)

    def _enforce_history_limit(self) -> None:
        if self.state.max_waveforms <= 0:
            return
        max_waveforms = max(1, int(self.state.max_waveforms))
        if len(self.history) <= max_waveforms:
            return
        removed_count = len(self.history) - max_waveforms
        self.history = self.history[-max_waveforms:]
        self.history_index = int(clamp(self.history_index - removed_count, 0, len(self.history) - 1))

    def _connection_text_is_error(self) -> bool:
        text = self.connection_text.lower()
        error_markers = (
            "capture failed",
            "timed out",
            "overrun",
            "hardware unavailable",
            "no picoscope detected",
            "dll not found",
        )
        return any(marker in text for marker in error_markers)

    def _app_metadata(self) -> dict[str, str]:
        return {
            "App name": "PicoWave",
            "App version": "1.0.0",
        }

    # Frontend: shared status text and invalid-setting feedback.

    def _set_hint(self, text: str, *, error: bool = False) -> None:
        self._hint_text = text
        self._hint_error = error

    def _reset_hint(self) -> None:
        self._set_hint(self._default_hint_text, error=False)

    def _set_run_button_hint(self, text: str | None) -> None:
        self._run_button_hint_text = text

    def _current_timing_compatibility(self) -> tuple[bool, list[str]]:
        active_channel_count = planning_active_channel_count(self.state)
        current_available = is_sample_rate_available_for_mode(
            self.state.acquisition_mode,
            self.state.time_per_div,
            self.state.sample_rate_hz,
            active_channel_count,
        )
        compatible_modes = [
            mode
            for mode in ACQUISITION_MODES
            if mode != self.state.acquisition_mode
            and is_sample_rate_available_for_mode(
                mode,
                self.state.time_per_div,
                self.state.sample_rate_hz,
                active_channel_count,
            )
        ]
        return current_available, compatible_modes

    def _flash_invalid_controls(self, *, mode: bool = False, timing: bool = False) -> None:
        self._invalid_flash_token += 1
        flash_token = self._invalid_flash_token
        self._mode_invalid = mode
        self._timing_invalid = timing
        self._sync_ui()

        def clear_flash() -> None:
            if flash_token != self._invalid_flash_token:
                return
            self._mode_invalid = False
            self._timing_invalid = False
            self._set_run_button_hint(None)
            self._sync_ui()

        QTimer.singleShot(1200, clear_flash)

    # Frontend: sync all widget states from the current application model.

    def _sync_ui(self) -> None:
        frame = self._current_frame()
        self.run_button.setEnabled(self.controller.is_connected)
        run_button_text = self._run_button_hint_text or ("running" if self.state.running else "stopped")
        self.run_button.setText(run_button_text)
        self.run_button.setIcon(load_icon("run" if self.state.running else "stop"))
        self.run_button.setProperty("running", self.state.running)
        self.run_button.setProperty("hint", self._run_button_hint_text is not None)
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)
        history_current, history_total = self._history_display_state()
        self.waveform_history_control.set_state(history_current, history_total)
        self.waveform_preview_strip.set_history(self.history, self.history_index)
        preview_visible = self.selected_panel == ("waveform", None) and self._stored_waveform_count() > 0
        self.waveform_preview_container.setVisible(preview_visible)
        self.waveform_preview_prev_button.setEnabled(self.waveform_preview_strip.has_previous_page())
        self.waveform_preview_next_button.setEnabled(self.waveform_preview_strip.has_next_page())
        self.scope_front_status.set_channel_state(
            self.state.channel_a.enabled,
            self.state.channel_b.enabled,
        )
        self.mode_control.set_mode(self.state.acquisition_mode)
        self.mode_control.body.setProperty("invalid", self._mode_invalid)
        self.mode_control.body.style().unpolish(self.mode_control.body)
        self.mode_control.body.style().polish(self.mode_control.body)
        self.trigger_control.set_trigger(self.state.trigger)
        self.timing_control.set_values(self.state.time_per_div, self.state.sample_rate_hz)
        current_time_index = TIME_PER_DIV_OPTIONS.index(self.state.time_per_div)
        self.timing_control.set_step_state(
            current_time_index > 0,
            current_time_index < len(TIME_PER_DIV_OPTIONS) - 1,
        )
        self.timing_control.body.setProperty("invalid", self._timing_invalid)
        self.timing_control.body.style().unpolish(self.timing_control.body)
        self.timing_control.body.style().polish(self.timing_control.body)
        self.channel_a_control.set_state(self.state.channel_a)
        self.channel_b_control.set_state(self.state.channel_b)
        self.custom_channel_control.set_state(self.state.custom_channel)
        if self.selected_panel is None:
            self.selection_panel.hide()
        else:
            panel_kind, panel_name = self.selected_panel
            if panel_kind == "channel" and panel_name is not None:
                if panel_name == "Custom":
                    self.selection_panel.set_custom_channel(
                        self.state.custom_channel,
                        self.set_custom_channel_source,
                        self.set_custom_channel_visibility,
                        self.set_custom_channel_color,
                        self.set_custom_channel_operation,
                        self.set_custom_channel_method,
                        self.set_custom_channel_strength,
                        self.turn_custom_channel_off,
                    )
                else:
                    self.selection_panel.set_channel(
                        self._channel_ref(panel_name),
                        lambda value, channel_name=panel_name: self.set_channel_panel_tab(channel_name, value),
                        lambda value, channel_name=panel_name: self.set_channel_voltage(channel_name, value),
                        lambda value, channel_name=panel_name: self.set_channel_coupling(channel_name, value),
                        lambda value, channel_name=panel_name: self.set_channel_invert(channel_name, value == "On"),
                        lambda value, channel_name=panel_name: self.set_channel_probe_scale(channel_name, value),
                        lambda _checked=False, channel_name=panel_name: self.turn_channel_off(channel_name),
                    )
            elif panel_kind == "timing":
                available_rates, compatible_rates, unavailable_rates = classify_sample_rates(
                    self.state.time_per_div,
                    self.state.acquisition_mode,
                    planning_active_channel_count(self.state),
                )
                self.selection_panel.set_timing(
                    self.state.time_per_div,
                    self.state.sample_rate_hz,
                    available_rates,
                    compatible_rates,
                    unavailable_rates,
                    self.set_timebase_value,
                    self.set_sample_rate_value,
                    self.set_sample_rate_for_mode,
                )
            elif panel_kind == "mode":
                self.selection_panel.set_mode(
                    self.state.acquisition_mode,
                    self.set_acquisition_mode,
                )
            elif panel_kind == "waveform":
                self.selection_panel.set_waveform(
                    self.state.max_waveforms,
                    self._stored_waveform_count(),
                    self.set_waveform_limit_mode,
                    self.set_max_waveforms_value,
                )
            elif panel_kind == "annotations":
                self.selection_panel.set_annotations(
                    self.annotation_settings,
                    self.set_annotation_scope,
                    self.set_annotation_tool,
                    self.set_annotation_color,
                    self.clear_current_annotations,
                )
            elif panel_kind == "trigger":
                self.selection_panel.set_trigger(
                    self.state.trigger,
                    self.set_trigger_mode,
                    self.set_trigger_type,
                    self.set_trigger_source,
                    self.set_trigger_direction,
                    self.adjust_trigger_level,
                    self.adjust_trigger_lower_level,
                    self.adjust_trigger_upper_level,
                    self.set_pulse_width_type,
                    self.adjust_pulse_width_lower,
                    self.adjust_pulse_width_upper,
                    self.set_logic_state,
                    self.adjust_pre_trigger_percent,
                )
            self.selection_panel.show()
        self.waveform_canvas.set_annotation_settings(self.annotation_settings)
        self.waveform_canvas.set_annotations(
            self.waveform_annotations.setdefault(self.history_index, []),
            self.global_annotations,
        )
        self.waveform_canvas.set_annotation_panel_open(self.selected_panel == ("annotations", None))
        self.waveform_canvas.set_annotation_button_active(
            self.selected_panel == ("annotations", None) or self.waveform_canvas.has_visible_annotations()
        )
        self.waveform_canvas.set_state(self.state)
        self.waveform_canvas.set_frame(frame)
        self.connection_label.setText(self.connection_text)
        self.connection_label.setProperty("error", self._connection_text_is_error())
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)
        self.hint_label.setText(self._hint_text)
        self.hint_label.setProperty("error", self._hint_error)
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)
        self.worker.update_state(self.state)

    # Frontend: top buttons

    def show_about_dialog(self) -> None:
        self.about_dialog.set_app_metadata(self._app_metadata())
        self.about_dialog.set_device_metadata(self.controller.get_device_metadata())
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def refresh_connect_dialog_devices(self) -> None:
        devices = self.controller.list_available_devices()
        status_text = self.controller.status_text if not devices else "Choose an available oscilloscope and press Connect."
        self.connect_dialog.set_devices(devices, status_text)

    def show_connect_dialog(self) -> None:
        self.refresh_connect_dialog_devices()
        if self.connect_dialog.exec() == QDialog.Accepted:
            self.connect_scope(self.connect_dialog.selected_serial())

    def connect_scope(self, serial: str | None = None) -> None:
        if self.controller.connect_device(serial):
            self.connection_text = (
                f"Hardware: {self.controller.status_text} | Mode: {self.state.acquisition_mode}"
            )
        else:
            self.connection_text = f"Hardware: {self.controller.status_text}"
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def toggle_running(self) -> None:
        if not self.controller.is_connected:
            return
        if not self.state.running and not self._has_enabled_channel():
            if self._manual_all_channels_off:
                self.state.running = False
                self.connection_text = "Hardware: Enable Channel A or B before starting acquisition."
                self._set_run_button_hint(None)
                self._reset_hint()
                self._sync_ui()
                return
            self.state.channel_a.enabled = True
            self.connection_text = "Hardware: Channel A enabled automatically for acquisition."
        if not self.state.running:
            current_available, compatible_modes = self._current_timing_compatibility()
            if not current_available:
                self.state.running = False
                if compatible_modes:
                    modes_text = " or ".join(compatible_modes)
                    self.connection_text = (
                        f"Hardware: Selected timebase and sample rate are not available in "
                        f"{self.state.acquisition_mode}."
                    )
                    self._set_run_button_hint(
                        f"Use {compatible_modes[0]}" if len(compatible_modes) == 1 else "Change Mode"
                    )
                    self._set_hint(f"Suggested mode: {modes_text}.", error=True)
                else:
                    self.connection_text = (
                        "Hardware: Selected timebase and sample rate are not available on this device."
                    )
                    self._set_run_button_hint("Invalid Timing")
                    self._set_hint("Selected mode and timing are not supported on this device.", error=True)
                self._flash_invalid_controls(mode=True, timing=True)
                return
        self._set_run_button_hint(None)
        self._reset_hint()
        self.state.running = not self.state.running
        self._sync_ui()

    # Frontend: left corner buttons and side-panel editors

    def adjust_time_per_div(self, step: int) -> None:
        current_index = TIME_PER_DIV_OPTIONS.index(self.state.time_per_div)
        current_index = int(clamp(current_index + step, 0, len(TIME_PER_DIV_OPTIONS) - 1))
        new_timebase = TIME_PER_DIV_OPTIONS[current_index]
        if math.isclose(new_timebase, self.state.time_per_div, rel_tol=1e-12, abs_tol=1e-12):
            return
        self.state.time_per_div = new_timebase
        self._reset_waveform_history()
        self._normalize_sample_rate_for_current_timing(force_max=True)
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def set_timebase_value(self, time_per_div: float) -> None:
        if math.isclose(time_per_div, self.state.time_per_div, rel_tol=1e-12, abs_tol=1e-12):
            return
        self.state.time_per_div = time_per_div
        self._reset_waveform_history()
        self._normalize_sample_rate_for_current_timing(force_max=False)
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def adjust_sample_rate(self, step: int) -> None:
        current_index = SAMPLE_RATE_OPTIONS.index(self.state.sample_rate_hz)
        current_index = int(clamp(current_index + step, 0, len(SAMPLE_RATE_OPTIONS) - 1))
        self.state.sample_rate_hz = SAMPLE_RATE_OPTIONS[current_index]
        self._sync_ui()

    def set_sample_rate_value(self, sample_rate_hz: float) -> None:
        self.state.sample_rate_hz = sample_rate_hz
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def set_sample_rate_for_mode(self, mode: str, sample_rate_hz: float) -> None:
        self.set_acquisition_mode(mode)
        self.set_sample_rate_value(sample_rate_hz)

    def adjust_max_waveforms(self, step: int) -> None:
        if self.state.max_waveforms <= 0:
            self.state.max_waveforms = DEFAULT_MAX_WAVEFORMS
        else:
            self.state.max_waveforms = max(1, int(self.state.max_waveforms + step))
        self._enforce_history_limit()
        self._sync_ui()

    def set_waveform_limit_mode(self, mode: str) -> None:
        if mode == "Unlimited":
            self.state.max_waveforms = 0
        else:
            self.state.max_waveforms = max(1, self.state.max_waveforms or DEFAULT_MAX_WAVEFORMS)
        self._enforce_history_limit()
        self._sync_ui()

    def set_max_waveforms_value(self, text: str) -> None:
        try:
            value = int(text.strip())
        except (TypeError, ValueError):
            self._sync_ui()
            return
        if value <= 0:
            self._sync_ui()
            return
        self.state.max_waveforms = value
        self._enforce_history_limit()
        self._sync_ui()

    def _available_sample_rates_for_current_mode(self, time_per_div: float) -> list[float]:
        available_rates, _compatible_rates, _ = classify_sample_rates(
            time_per_div,
            self.state.acquisition_mode,
            planning_active_channel_count(self.state),
        )
        return available_rates

    def _max_sample_rate_for_timebase(self, time_per_div: float) -> float:
        available_rates = self._available_sample_rates_for_current_mode(time_per_div)
        if available_rates:
            return max(available_rates)
        return self.state.sample_rate_hz

    def _normalize_sample_rate_for_current_timing(self, *, force_max: bool) -> None:
        # Single normalization point for timing changes. Whenever timebase, mode,
        # or active-channel count changes, this keeps the sample-rate pair honest
        # for the current mode instead of silently drifting into another mode.
        available_rates = self._available_sample_rates_for_current_mode(self.state.time_per_div)
        if not available_rates:
            return
        if force_max or self.state.sample_rate_hz not in available_rates:
            self.state.sample_rate_hz = max(available_rates)

    def set_trigger_mode(self, mode: str) -> None:
        if mode not in TRIGGER_MODES:
            return
        if mode == "None":
            self.state.trigger = TriggerState()
        else:
            self.state.trigger.mode = mode
        self._sync_ui()

    def set_trigger_type(self, trigger_type: str) -> None:
        if trigger_type not in TRIGGER_TYPES:
            return
        self.state.trigger.trigger_type = trigger_type
        direction_options = trigger_direction_options(trigger_type)
        if direction_options and self.state.trigger.direction not in direction_options:
            self.state.trigger.direction = direction_options[0]
        self._sync_ui()

    def set_trigger_source(self, source: str) -> None:
        if source not in TRIGGER_SOURCES:
            return
        self.state.trigger.source = source
        self._sync_ui()

    def set_trigger_direction(self, direction: str) -> None:
        if direction not in trigger_direction_options(self.state.trigger.trigger_type):
            return
        self.state.trigger.direction = direction
        self._sync_ui()

    def adjust_trigger_level(self, step: int) -> None:
        channel = self._channel_ref(self.state.trigger.source)
        max_level = channel_display_range(channel) if channel.enabled else 20.0
        self.state.trigger.level_volts = clamp(
            self.state.trigger.level_volts + (0.1 * max_level * step),
            -max_level,
            max_level,
        )
        self._sync_ui()

    def set_trigger_level_value(self, level_volts: float) -> None:
        channel = self._channel_ref(self.state.trigger.source)
        max_level = channel_display_range(channel) if channel.enabled else 20.0
        clamped_level = clamp(level_volts, -max_level, max_level)
        if self.state.trigger.trigger_type == "Window":
            current_span = self.state.trigger.upper_level_volts - self.state.trigger.lower_level_volts
            half_span = max(abs(current_span) / 2.0, 0.01)
            self.state.trigger.lower_level_volts = clamp(clamped_level - half_span, -max_level, max_level)
            self.state.trigger.upper_level_volts = clamp(clamped_level + half_span, -max_level, max_level)
            if self.state.trigger.lower_level_volts > self.state.trigger.upper_level_volts:
                self.state.trigger.lower_level_volts, self.state.trigger.upper_level_volts = (
                    self.state.trigger.upper_level_volts,
                    self.state.trigger.lower_level_volts,
                )
        else:
            self.state.trigger.level_volts = clamped_level
        self._sync_ui()

    def adjust_trigger_lower_level(self, step: int) -> None:
        channel = self._channel_ref(self.state.trigger.source)
        max_level = channel_display_range(channel) if channel.enabled else 20.0
        self.state.trigger.lower_level_volts = clamp(
            self.state.trigger.lower_level_volts + (0.1 * max_level * step),
            -max_level,
            max_level,
        )
        self._sync_ui()

    def adjust_trigger_upper_level(self, step: int) -> None:
        channel = self._channel_ref(self.state.trigger.source)
        max_level = channel_display_range(channel) if channel.enabled else 20.0
        self.state.trigger.upper_level_volts = clamp(
            self.state.trigger.upper_level_volts + (0.1 * max_level * step),
            -max_level,
            max_level,
        )
        self._sync_ui()

    def set_pulse_width_type(self, pulse_width_type: str) -> None:
        if pulse_width_type not in PULSE_WIDTH_TYPES:
            return
        self.state.trigger.pulse_width_type = pulse_width_type
        self._sync_ui()

    def adjust_pulse_width_lower(self, step: int) -> None:
        self.state.trigger.pulse_width_lower = max(1, self.state.trigger.pulse_width_lower + (10 * step))
        if self.state.trigger.pulse_width_upper < self.state.trigger.pulse_width_lower:
            self.state.trigger.pulse_width_upper = self.state.trigger.pulse_width_lower
        self._sync_ui()

    def adjust_pulse_width_upper(self, step: int) -> None:
        self.state.trigger.pulse_width_upper = max(1, self.state.trigger.pulse_width_upper + (10 * step))
        if self.state.trigger.pulse_width_upper < self.state.trigger.pulse_width_lower:
            self.state.trigger.pulse_width_lower = self.state.trigger.pulse_width_upper
        self._sync_ui()

    def set_logic_state(self, channel_name: str, state_name: str) -> None:
        if channel_name not in ("A", "B") or state_name not in TRIGGER_LOGIC_STATES:
            return
        if channel_name == "A":
            self.state.trigger.logic_a_state = state_name
        else:
            self.state.trigger.logic_b_state = state_name
        self._sync_ui()

    def adjust_pre_trigger_percent(self, step: int) -> None:
        self.state.trigger.pre_trigger_percent = int(
            clamp(self.state.trigger.pre_trigger_percent + (10 * step), 0, 100)
        )
        self._sync_ui()

    def set_pre_trigger_percent_value(self, value: int) -> None:
        self.state.trigger.pre_trigger_percent = int(clamp(value, 0, 100))
        self._sync_ui()

    def adjust_history(self, step: int) -> None:
        self.history_index = int(clamp(self.history_index + step, 0, len(self.history) - 1))
        self._sync_ui()

    def select_history_frame(self, history_index: int) -> None:
        if 0 <= history_index < len(self.history):
            self.history_index = history_index
            if self.selected_panel == ("waveform", None):
                self._set_selected_panel(("waveform", None))
            self._sync_ui()

    def show_previous_waveform_preview_page(self) -> None:
        self.waveform_preview_strip.previous_page()
        self.waveform_preview_prev_button.setEnabled(self.waveform_preview_strip.has_previous_page())
        self.waveform_preview_next_button.setEnabled(self.waveform_preview_strip.has_next_page())

    def show_next_waveform_preview_page(self) -> None:
        self.waveform_preview_strip.next_page()
        self.waveform_preview_prev_button.setEnabled(self.waveform_preview_strip.has_previous_page())
        self.waveform_preview_next_button.setEnabled(self.waveform_preview_strip.has_next_page())

    def page_waveform_previews(self, step: int) -> None:
        if step < 0:
            self.show_previous_waveform_preview_page()
        elif step > 0:
            self.show_next_waveform_preview_page()

    def _channel_ref(self, name: str) -> ChannelState:
        return self.state.channel_a if name == "A" else self.state.channel_b

    def _has_enabled_channel(self) -> bool:
        return self.state.channel_a.enabled or self.state.channel_b.enabled

    @staticmethod
    def _is_widget_in_branch(widget: QWidget, root: QWidget) -> bool:
        current = widget
        while current is not None:
            if current is root:
                return True
            current = current.parentWidget()
        return False

    def _is_panel_related_click(self, widget: QWidget) -> bool:
        panel_roots = [
            self.selection_panel,
            self.run_button,
            self.mode_control.body,
            self.trigger_control.body,
            self.timing_control,
            self.waveform_history_control,
            self.waveform_preview_container,
            self.waveform_preview_body,
            self.waveform_preview_strip,
            self.waveform_preview_prev_button,
            self.waveform_preview_next_button,
            self.waveform_canvas.annotation_button,
            self.channel_a_control.body,
            self.channel_b_control.body,
            self.custom_channel_control.body,
        ]
        return any(self._is_widget_in_branch(widget, root) for root in panel_roots)

    def _set_selected_panel(self, panel: tuple[str, str | None] | None) -> None:
        self.selected_panel = panel
        if panel is None:
            self._outside_close_armed = False
        else:
            self._outside_close_armed = False
            QTimer.singleShot(0, self._arm_outside_close)

    def _arm_outside_close(self) -> None:
        if self.selected_panel is not None:
            self._outside_close_armed = True

    def hide_selection_panel(self) -> None:
        self._set_selected_panel(None)
        self._sync_ui()

    def select_channel(self, name: str) -> None:
        if self.selected_panel == ("channel", name) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("channel", name))
        self._sync_ui()

    def select_timing_panel(self) -> None:
        if self.selected_panel == ("timing", None) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("timing", None))
        self._sync_ui()

    def select_waveform_panel(self) -> None:
        if self.selected_panel == ("waveform", None) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("waveform", None))
        self._sync_ui()

    def select_annotation_panel(self) -> None:
        if self.selected_panel != ("annotations", None):
            self.waveform_canvas.set_zoom_box_mode(False)
        if self.selected_panel == ("annotations", None) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("annotations", None))
        self._sync_ui()

    def start_annotation_interaction(self) -> None:
        if self.selected_panel == ("annotations", None):
            self._set_selected_panel(None)
            self._sync_ui()

    def handle_zoom_box_mode_changed(self, active: bool) -> None:
        if active and self.selected_panel == ("annotations", None):
            self._set_selected_panel(None)
            self._sync_ui()

    def select_trigger_panel(self) -> None:
        if self.selected_panel == ("trigger", None) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("trigger", None))
        self._sync_ui()

    def select_mode_panel(self) -> None:
        if self.selected_panel == ("mode", None) and self.selection_panel.isVisible():
            self._set_selected_panel(None)
        else:
            self._set_selected_panel(("mode", None))
        self._sync_ui()

    # Backend: UI actions that change acquisition settings

    def set_acquisition_mode(self, mode: str) -> None:
        if mode not in ACQUISITION_MODES:
            return
        if mode == self.state.acquisition_mode:
            return
        self.state.acquisition_mode = mode
        self._reset_waveform_history()
        self._normalize_sample_rate_for_current_timing(force_max=False)
        if self.controller.is_connected:
            self.connection_text = (
                f"Hardware: {self.controller.status_text} | Mode: {self.state.acquisition_mode}"
            )
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def set_annotation_scope(self, scope: str) -> None:
        if scope not in ANNOTATION_SCOPES:
            return
        self.annotation_settings.scope = scope
        self._sync_ui()

    def set_annotation_tool(self, tool: str) -> None:
        if tool not in ANNOTATION_TOOLS:
            return
        self.annotation_settings.tool = tool
        self._sync_ui()

    def set_annotation_color(self, color_hex: str) -> None:
        if color_hex not in [value for _label, value in ANNOTATION_COLORS]:
            return
        self.annotation_settings.color_hex = color_hex
        self._sync_ui()

    def clear_current_annotations(self) -> None:
        if self.annotation_settings.scope == "This capture":
            self.waveform_annotations.setdefault(self.history_index, []).clear()
        else:
            self.global_annotations.clear()
        self._sync_ui()

    def set_channel_voltage(self, name: str | None, volts: float) -> None:
        if name is None:
            return
        channel = self._channel_ref(name)
        if math.isclose(channel.range_volts, volts, rel_tol=1e-12, abs_tol=1e-12) and channel.enabled:
            return
        channel.range_volts = volts
        channel.enabled = True
        self._reset_waveform_history()
        self._manual_all_channels_off = False
        self._normalize_sample_rate_for_current_timing(force_max=False)
        self._sync_ui()

    def set_channel_coupling(self, name: str | None, coupling: str) -> None:
        if name is None:
            return
        channel = self._channel_ref(name)
        channel.coupling = coupling
        channel.enabled = True
        self._manual_all_channels_off = False
        self._normalize_sample_rate_for_current_timing(force_max=False)
        self._sync_ui()

    def set_channel_panel_tab(self, name: str | None, tab: str) -> None:
        if name is None or tab not in CHANNEL_PANEL_TABS:
            return
        channel = self._channel_ref(name)
        channel.panel_tab = tab
        self._sync_ui()

    def set_channel_invert(self, name: str | None, invert: bool) -> None:
        if name is None:
            return
        channel = self._channel_ref(name)
        channel.invert = invert
        self._sync_ui()

    def set_channel_vertical_offset(self, name: str | None, offset_divs: float) -> None:
        if name is None:
            return
        if name == "Custom":
            self.state.custom_channel.vertical_offset_divs = float(clamp(offset_divs, -5.0, 5.0))
            self._sync_ui()
            return
        channel = self._channel_ref(name)
        channel.vertical_offset_divs = float(clamp(offset_divs, -5.0, 5.0))
        self._sync_ui()

    def set_channel_display_zoom(self, name: str | None, display_zoom: float) -> None:
        if name is None:
            return
        channel = self._channel_ref(name)
        channel.display_zoom = float(clamp(display_zoom, 0.25, 20.0))
        self._sync_ui()

    def set_channel_probe_scale(self, name: str | None, probe_scale: int) -> None:
        if name is None or probe_scale not in PROBE_SCALE_OPTIONS:
            return
        channel = self._channel_ref(name)
        channel.probe_scale = probe_scale
        channel.probe = format_probe_scale(probe_scale)
        available_ranges = channel_voltage_options(channel)
        if not any(math.isclose(channel.range_volts, option, rel_tol=1e-12, abs_tol=1e-12) for option in available_ranges):
            channel.range_volts = min(available_ranges, key=lambda option: abs(option - channel.range_volts))
        self._sync_ui()

    def set_custom_channel_source(self, source: str) -> None:
        if source not in CUSTOM_CHANNEL_SOURCE_OPTIONS:
            return
        self.state.custom_channel.source_channel = source
        self._sync_ui()

    def set_custom_channel_visibility(self, visibility: str) -> None:
        if visibility not in CUSTOM_CHANNEL_VISIBILITY_OPTIONS:
            return
        self.state.custom_channel.show_source_channel = visibility == "Show"
        self._sync_ui()

    def set_custom_channel_color(self, color_hex: str) -> None:
        if color_hex not in [value for _label, value in CUSTOM_CHANNEL_COLORS]:
            return
        self.state.custom_channel.color_hex = color_hex
        self._sync_ui()

    def set_custom_channel_operation(self, operation: str) -> None:
        if operation not in CUSTOM_CHANNEL_MATH_OPTIONS:
            return
        self.state.custom_channel.operation = operation
        self.state.custom_channel.enabled = True
        self._sync_ui()

    def set_custom_channel_method(self, method: str) -> None:
        if method not in SMOOTHING_METHOD_LABELS:
            return
        self.state.custom_channel.smoothing_method = method
        self._sync_ui()

    def set_custom_channel_strength(self, span: int) -> None:
        if span not in SMOOTHING_STRENGTH_LABELS:
            return
        self.state.custom_channel.smoothing_span = int(span)
        self._sync_ui()

    def turn_custom_channel_off(self) -> None:
        self.state.custom_channel.enabled = False
        self.state.custom_channel.vertical_offset_divs = 0.0
        self._sync_ui()

    def turn_channel_off(self, name: str | None) -> None:
        if name is None:
            return
        channel = self._channel_ref(name)
        channel.enabled = False
        if not self._has_enabled_channel():
            self._manual_all_channels_off = True
            self.state.running = False
        else:
            self._manual_all_channels_off = False
        self._normalize_sample_rate_for_current_timing(force_max=False)
        self._sync_ui()

    def adjust_channel_range(self, name: str, step: int) -> None:
        channel = self._channel_ref(name)
        options = channel_voltage_options(channel)
        current_index = min(range(len(options)), key=lambda index: abs(options[index] - channel.range_volts))
        current_index = int(clamp(current_index + step, 0, len(options) - 1))
        channel.range_volts = options[current_index]
        channel.enabled = True
        self._manual_all_channels_off = False
        self._sync_ui()

    def toggle_channel(self, name: str) -> None:
        if name == "A":
            self.state.channel_a.enabled = True
            self.state.channel_a.coupling = "DC" if self.state.channel_a.coupling == "AC" else "AC"
        else:
            self.state.channel_b.enabled = not self.state.channel_b.enabled
        self._sync_ui()

    # Frontend + backend: acquisition results and error handling

    def on_frame_ready(self, frame: CaptureFrame) -> None:
        # Fresh frame delivery is the canonical "new data arrived" event. The rest
        # of the UI, including waveform history and the front-panel blink LED,
        # fans out from here.
        self._push_frame(frame)
        if frame.sample_count > 0:
            self.scope_front_status.blink_activity()
        if self.state.trigger.mode == "Single":
            self.state.running = False
        self.connection_text = (
            f"{frame.source_label}: {frame.connection_label} | Mode: {self.state.acquisition_mode}"
        )
        self._set_run_button_hint(None)
        self._reset_hint()
        self._sync_ui()

    def on_capture_failed(self, message: str) -> None:
        self.state.running = False
        self.connection_text = f"Hardware: {message}"
        self._set_run_button_hint("Check Settings")
        self._set_hint("Review Mode and Timebase / Sample rate settings.", error=True)
        self._sync_ui()



