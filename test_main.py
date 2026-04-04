import os
import unittest
from ctypes import CFUNCTYPE, POINTER, c_int16, c_uint32, cast
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from picowave import (
    ACQUISITION_MODES,
    AnnotationSettings,
    AnnotationStroke,
    AnnotationText,
    apply_smoothing_method,
    APP_LOGGER,
    CaptureFrame,
    configure_logging,
    FAST_STREAMING_MAX_SAMPLES,
    LOG_DIR,
    LOG_FILE,
    MainWindow,
    Pico2204AController,
    PS2000_THRESHOLD_MODE,
    PS2000_TRIGGER_STATE,
    SAMPLE_RATE_OPTIONS,
    ScopeState,
    TIME_PER_DIV_OPTIONS,
    WaveformCanvas,
    build_empty_frame,
    channel_display_range,
    channel_visible_range,
    channel_hardware_range,
    channel_voltage_options,
    classify_sample_rates,
    format_live_voltage,
    format_time_per_div,
    smooth_signal,
)


class FakeController:
    def __init__(self, connect_result: bool = True) -> None:
        self.is_connected = False
        self.status_text = "No PicoScope detected."
        self._connect_result = connect_result
        self.last_serial = None

    def list_available_devices(self) -> list[dict[str, str]]:
        return [{"label": "2204A [TEST123]", "serial": "TEST123", "variant": "2204A"}]

    def connect_device(self, serial: str | None = None) -> bool:
        self.last_serial = serial
        if self._connect_result:
            self.is_connected = True
            self.status_text = "2204A connected [TEST123]"
        else:
            self.is_connected = False
            self.status_text = "No PicoScope detected."
        return self.is_connected

    def get_device_metadata(self) -> dict[str, str]:
        return {
            "Model / variant info": "2204A",
            "Serial or batch-and-serial": "TEST123",
            "Driver version": "1.0",
            "USB version": "2.0",
            "Hardware version": "A",
            "Calibration date": "2026-01-01",
            "Kernel driver version": "1.0",
            "Error code / driver-reported status info": "PICO_OK",
        }


class FakeDevice:
    def __init__(self) -> None:
        self.handle = 1
        self.info = SimpleNamespace(variant=b"2204A", serial=b"TEST123")
        self._channel_ranges = {"A": 1.0, "B": 5.0}
        self.channels = ()

    def set_channels(self, *channels) -> None:
        self.channels = channels


class FakePS:
    def __init__(self) -> None:
        self.trigger_configured = False
        self.stopped = False
        self.trigger_args = None
        self.advanced_properties = []
        self.advanced_conditions = []
        self.advanced_directions = None
        self.advanced_delay = None
        self.pulse_width_args = None
        self.PICO_CHANNEL = {"A": 0, "B": 1}

    def set_null_trigger(self, device) -> None:
        self.trigger_configured = True

    def _set_trigger2(
        self, handle, source, threshold, direction, delay, auto_trigger_ms
    ):
        self.trigger_args = {
            "kind": "simple",
            "source": source.value,
            "threshold": threshold.value,
            "direction": direction.value,
            "delay": float(delay),
            "auto_trigger_ms": auto_trigger_ms.value,
        }
        self.trigger_configured = True
        return 1

    def _SetAdvTriggerChannelProperties(
        self, handle, channel_properties, count, auto_trigger_ms
    ):
        count_value = count.value if hasattr(count, "value") else int(count)
        self.advanced_properties = [
            channel_properties[index] for index in range(count_value)
        ]
        self.trigger_configured = True
        return 1

    def _SetAdvTriggerChannelConditions(self, handle, conditions, count):
        count_value = count.value if hasattr(count, "value") else int(count)
        self.advanced_conditions = (
            []
            if conditions is None
            else [conditions[index] for index in range(count_value)]
        )
        self.trigger_configured = True
        return 1

    def _SetAdvTriggerChannelDirections(
        self, handle, channel_a, channel_b, channel_c, channel_d, ext
    ):
        self.advanced_directions = {
            "A": channel_a.value if hasattr(channel_a, "value") else int(channel_a),
            "B": channel_b.value if hasattr(channel_b, "value") else int(channel_b),
            "C": channel_c.value if hasattr(channel_c, "value") else int(channel_c),
            "D": channel_d.value if hasattr(channel_d, "value") else int(channel_d),
            "Ext": ext.value if hasattr(ext, "value") else int(ext),
        }
        self.trigger_configured = True
        return 1

    def _SetAdvTriggerDelay(self, handle, delay, pre_trigger_delay):
        self.advanced_delay = {
            "delay": delay.value if hasattr(delay, "value") else int(delay),
            "pre_trigger_delay": float(pre_trigger_delay),
        }
        return 1

    def _SetPulseWidthQualifier(
        self, handle, conditions, count, direction, lower, upper, trigger_type
    ):
        count_value = count.value if hasattr(count, "value") else int(count)
        direction_value = (
            direction.value if hasattr(direction, "value") else int(direction)
        )
        lower_value = lower.value if hasattr(lower, "value") else int(lower)
        upper_value = upper.value if hasattr(upper, "value") else int(upper)
        type_value = (
            trigger_type.value if hasattr(trigger_type, "value") else int(trigger_type)
        )
        self.pulse_width_args = {
            "conditions": []
            if conditions is None
            else [conditions[index] for index in range(count_value)],
            "direction": direction_value,
            "lower": lower_value,
            "upper": upper_value,
            "type": type_value,
        }
        return 1

    def run_block(
        self, device, pretrig, sample_count, timebase_id, oversample, segment_index
    ):
        return 0.01

    def is_ready(self, device) -> bool:
        return True

    def get_values(self, device, active_channels, sample_count, start_index):
        data = {}
        if "A" in active_channels:
            data["A"] = np.array([0, 50, -50], dtype=np.int16)
        if "B" in active_channels:
            data["B"] = np.array([100, 0, -100], dtype=np.int16)
        return data, False

    def stop(self, device) -> None:
        self.stopped = True

    def maximum_value(self, device) -> int:
        return 100


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        if hasattr(self, "window"):
            self.window.close()

    def test_default_state_matches_requested_defaults(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.assertFalse(self.window.state.channel_a.enabled)
        self.assertFalse(self.window.state.channel_b.enabled)
        self.assertEqual(self.window.state.channel_a.coupling, "DC")
        self.assertEqual(self.window.state.channel_b.coupling, "DC")
        self.assertEqual(self.window.state.acquisition_mode, "Block")
        self.assertEqual(self.window.state.sample_rate_hz, 100_000)
        self.assertEqual(self.window.state.time_per_div, 5e-3)
        self.assertEqual(self.window.state.max_waveforms, 60)
        self.assertFalse(self.window.state.custom_channel.enabled)
        self.assertEqual(self.window.state.custom_channel.source_channel, "A")
        self.assertTrue(self.window.state.custom_channel.show_source_channel)
        self.assertEqual(self.window.state.custom_channel.operation, "Signal smoother")
        self.assertEqual(
            self.window.state.custom_channel.smoothing_method, "moving_average"
        )
        self.assertEqual(self.window.state.custom_channel.smoothing_span, 11)
        self.assertEqual(
            self.window.waveform_history_control.value_label.text(), "0 of 0"
        )
        self.assertFalse(self.window.waveform_history_control.minus_button.isEnabled())
        self.assertFalse(self.window.waveform_history_control.plus_button.isEnabled())

    def test_signal_smoother_matches_centered_moving_average_edges(self) -> None:
        values = np.array([0.0, 0.0, 10.0, 0.0, 0.0], dtype=np.float32)

        smoothed = smooth_signal(values, span=5)

        np.testing.assert_allclose(
            smoothed,
            np.array([0.0, 10.0 / 3.0, 2.0, 10.0 / 3.0, 0.0], dtype=np.float32),
        )

    def test_all_smoothing_methods_return_valid_output(self) -> None:
        values = np.array([0.0, 0.0, 10.0, 0.0, 0.0, 5.0, 0.0], dtype=np.float32)

        for method in ("moving_average", "savitzky_golay", "lowess", "robust_lowess"):
            smoothed = apply_smoothing_method(values, method, 5)
            self.assertEqual(smoothed.shape, values.shape)
            self.assertFalse(np.isnan(smoothed).any())

    def test_custom_channel_panel_updates_visibility_source_and_color(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_channel("Custom")
        self.window.set_custom_channel_source("B")
        self.window.set_custom_channel_visibility("Hide")
        self.window.set_custom_channel_color("#119822")

        self.assertEqual(self.window.selected_panel, ("channel", "Custom"))
        self.assertEqual(self.window.state.custom_channel.source_channel, "B")
        self.assertFalse(self.window.state.custom_channel.show_source_channel)
        self.assertEqual(self.window.state.custom_channel.color_hex, "#119822")
        self.assertFalse(self.window.state.custom_channel.enabled)

    def test_selecting_custom_math_operation_enables_custom_channel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_custom_channel_operation("Signal smoother")

        self.assertTrue(self.window.state.custom_channel.enabled)

    def test_custom_channel_method_and_strength_update(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_custom_channel_method("savitzky_golay")
        self.window.set_custom_channel_strength(21)

        self.assertEqual(
            self.window.state.custom_channel.smoothing_method, "savitzky_golay"
        )
        self.assertEqual(self.window.state.custom_channel.smoothing_span, 21)

    def test_turn_custom_channel_off_disables_it_and_resets_offset(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.state.custom_channel.enabled = True
        self.window.state.custom_channel.vertical_offset_divs = 2.0

        self.window.turn_custom_channel_off()

        self.assertFalse(self.window.state.custom_channel.enabled)
        self.assertEqual(self.window.state.custom_channel.vertical_offset_divs, 0.0)

    def test_custom_channel_rejects_channel_a_and_b_colors(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_custom_channel_color("#1e73be")

        self.assertEqual(self.window.state.custom_channel.color_hex, "#d97706")

    def test_waveform_canvas_builds_custom_smoothed_trace_from_selected_source(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        state = ScopeState()
        state.channel_b.enabled = True
        state.custom_channel.enabled = True
        state.custom_channel.source_channel = "B"
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            channel_a=np.array([1, 1, 1, 1, 1], dtype=np.float32),
            channel_b=np.array([0, 0, 10, 0, 0], dtype=np.float32),
            sample_rate_hz=1_000.0,
            sample_count=5,
            y_range_volts=1.0,
            source_label="Hardware",
            connection_label="Connected",
            trigger_label="None",
        )
        canvas.set_frame(frame)

        payload = canvas._custom_channel_volts()

        self.assertIsNotNone(payload)
        smoothed, source_state = payload
        np.testing.assert_allclose(
            smoothed,
            np.array([0.0, 10.0 / 3.0, 2.0, 10.0 / 3.0, 0.0], dtype=np.float32),
        )
        self.assertEqual(source_state.name, "Custom")

    def test_waveform_canvas_caches_custom_smoothed_trace_until_frame_or_state_changes(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        state = ScopeState()
        state.channel_a.enabled = True
        state.custom_channel.enabled = True
        state.custom_channel.source_channel = "A"
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.array([0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            channel_a=np.array([0, 1, 0, 1, 0], dtype=np.float32),
            channel_b=np.array([0, 0, 0, 0, 0], dtype=np.float32),
            sample_rate_hz=1_000.0,
            sample_count=5,
            y_range_volts=1.0,
            source_label="Hardware",
            connection_label="Connected",
            trigger_label="None",
        )
        canvas.set_frame(frame)

        with patch(
            "picowave.ui.canvas.apply_smoothing_method", wraps=apply_smoothing_method
        ) as wrapped:
            first = canvas._custom_channel_volts()
            second = canvas._custom_channel_volts()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(wrapped.call_count, 1)

    def test_waveform_canvas_hides_selected_source_trace_when_requested(self) -> None:
        canvas = WaveformCanvas()
        state = ScopeState()
        state.channel_a.enabled = True
        state.custom_channel.enabled = True
        state.custom_channel.source_channel = "A"
        state.custom_channel.show_source_channel = False
        canvas.set_state(state)

        self.assertFalse(canvas._should_draw_source_trace("A"))
        self.assertTrue(canvas._should_draw_source_trace("B"))

    def test_custom_channel_uses_source_axis_with_custom_color(self) -> None:
        canvas = WaveformCanvas()
        state = ScopeState()
        state.channel_b.enabled = True
        state.custom_channel.enabled = True
        state.custom_channel.source_channel = "B"
        state.custom_channel.color_hex = "#119822"
        canvas.set_state(state)

        descriptor = canvas._custom_axis_descriptor()

        self.assertIsNotNone(descriptor)
        side, axis_channel, color_hex = descriptor
        self.assertEqual(side, "right")
        self.assertEqual(axis_channel.name, "Custom")
        self.assertEqual(color_hex, "#119822")

    def test_connect_scope_updates_button_and_enables_run(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.assertFalse(self.window.run_button.isEnabled())
        self.window.connect_scope()

        self.assertTrue(self.window.controller.is_connected)
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertIn("2204A connected", self.window.connection_label.text())

    def test_refresh_connect_dialog_populates_detected_devices(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.refresh_connect_dialog_devices()

        self.assertEqual(self.window.connect_dialog.device_list.count(), 1)
        self.assertEqual(
            self.window.connect_dialog.device_list.item(0).text(), "2204A [TEST123]"
        )

    def test_scope_front_status_tracks_enabled_channels(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.assertFalse(self.window.scope_front_status._channel_a_enabled)
        self.assertFalse(self.window.scope_front_status._channel_b_enabled)

        self.window.set_channel_voltage("A", 1.0)
        self.window.set_channel_voltage("B", 1.0)

        self.assertTrue(self.window.scope_front_status._channel_a_enabled)
        self.assertTrue(self.window.scope_front_status._channel_b_enabled)

    def test_scope_front_status_blinks_when_new_frame_arrives(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        frame = build_empty_frame(self.window.state, "Hardware", "Connected")
        frame.sample_count = 10

        self.window.on_frame_ready(frame)

        self.assertTrue(self.window.scope_front_status._blink_on)

    def test_scope_front_status_switches_to_heartbeat_for_fast_activity(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        with patch("picowave.ui.components.time.monotonic", side_effect=[1.0, 1.05]):
            self.window.scope_front_status.blink_activity()
            self.window.scope_front_status.blink_activity()

        self.assertTrue(self.window.scope_front_status._heartbeat_mode)
        self.assertTrue(self.window.scope_front_status._heartbeat_timer.isActive())

    def test_annotation_button_shows_active_state_when_annotations_exist(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.global_annotations.append(
            AnnotationStroke(points=[(0.1, 0.1), (0.4, 0.1)], color_hex="#ef3340")
        )
        self.window._sync_ui()

        self.assertTrue(
            self.window.waveform_canvas.annotation_button.property("active")
        )

    def test_toggle_running_auto_enables_channel_a_when_needed(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.connect_scope()
        self.window.set_acquisition_mode("Fast streaming")

        self.window.toggle_running()

        self.assertTrue(self.window.state.running)
        self.assertTrue(self.window.state.channel_a.enabled)
        self.assertIn(
            "Channel A enabled automatically", self.window.connection_label.text()
        )

    def test_toggle_running_shows_visual_feedback_for_invalid_mode_timing_combo(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.connect_scope()
        self.window.set_channel_voltage("A", 1.0)
        self.window.set_acquisition_mode("Block")
        self.window.set_timebase_value(0.1)
        self.window.set_sample_rate_value(1_000_000)

        self.window.toggle_running()

        self.assertFalse(self.window.state.running)
        self.assertIn("not available in Block", self.window.connection_label.text())
        self.assertIn("Suggested mode: Fast streaming.", self.window.hint_label.text())
        self.assertEqual(self.window.run_button.text(), "Use Fast streaming")
        self.assertTrue(self.window.mode_control.body.property("invalid"))
        self.assertTrue(self.window.timing_control.body.property("invalid"))

    def test_manual_channel_a_off_is_respected_when_starting(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.connect_scope()
        self.window.set_channel_voltage("A", 5.0)
        self.window.turn_channel_off("A")

        self.window.toggle_running()

        self.assertFalse(self.window.state.running)
        self.assertFalse(self.window.state.channel_a.enabled)
        self.assertIn("Enable Channel A or B", self.window.connection_label.text())

    def test_channel_controls_update_backend_state(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_channel_voltage("A", 5.0)
        self.window.set_channel_coupling("A", "AC")
        self.window.set_channel_invert("A", True)
        self.window.set_channel_panel_tab("A", "Probes")
        self.window.set_channel_probe_scale("A", 10)

        self.assertTrue(self.window.state.channel_a.enabled)
        self.assertEqual(self.window.state.channel_a.range_volts, 5.0)
        self.assertEqual(self.window.state.channel_a.coupling, "AC")
        self.assertTrue(self.window.state.channel_a.invert)
        self.assertEqual(self.window.state.channel_a.panel_tab, "Probes")
        self.assertEqual(self.window.state.channel_a.probe_scale, 10)

        self.window.state.running = True
        self.window.turn_channel_off("A")

        self.assertFalse(self.window.state.channel_a.enabled)
        self.assertFalse(self.window.state.running)

    def test_channel_strip_keeps_selected_range_text_when_probe_scale_changes(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_channel_voltage("A", 10.0)
        self.window.set_channel_probe_scale("A", 20)

        self.assertEqual(self.window.channel_a_control.range_label.text(), "+10 V")
        self.assertEqual(self.window.channel_a_control.probe_label.text(), "x20")
        self.assertEqual(channel_hardware_range(self.window.state.channel_a), 0.5)

    def test_probe_scale_expands_available_voltage_options(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_channel_probe_scale("A", 20)

        self.assertEqual(
            channel_voltage_options(self.window.state.channel_a),
            [1.0, 2.0, 4.0, 10.0, 20.0, 40.0, 100.0, 200.0, 400.0],
        )

    def test_timing_controls_update_state(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.adjust_time_per_div(1)
        self.window.adjust_sample_rate(-1)

        self.assertEqual(self.window.state.time_per_div, TIME_PER_DIV_OPTIONS[14])
        self.assertEqual(self.window.state.sample_rate_hz, 20_000)

    def test_timebase_formatting_uses_plain_seconds_for_large_values(self) -> None:
        self.assertEqual(format_time_per_div(1000.0), "1000 s /div")
        self.assertEqual(format_time_per_div(2000.0), "2000 s /div")
        self.assertEqual(format_time_per_div(5000.0), "5000 s /div")

    def test_timing_step_buttons_pick_max_available_sample_rate_for_new_timebase(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_timebase_value(0.1)
        self.window.set_sample_rate_value(1_000)
        self.window.adjust_time_per_div(-1)

        self.assertEqual(self.window.state.time_per_div, 0.05)
        self.assertEqual(self.window.state.sample_rate_hz, 10_000)

    def test_timing_step_buttons_do_not_use_sample_rates_from_other_modes(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_acquisition_mode("Block")
        self.window.set_sample_rate_value(100_000)

        self.assertEqual(self.window._max_sample_rate_for_timebase(5000.0), 100_000)

    def test_set_timebase_value_clamps_invalid_sample_rate_to_current_mode_max(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_acquisition_mode("Block")
        self.window.set_sample_rate_value(1_000_000)
        self.window.set_timebase_value(0.1)

        self.assertEqual(self.window.state.sample_rate_hz, 5_000)

    def test_switching_mode_clamps_invalid_sample_rate_to_current_mode_max(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_acquisition_mode("Fast streaming")
        self.window.set_timebase_value(0.1)
        self.window.set_sample_rate_value(1_000_000)
        self.window.set_acquisition_mode("Block")

        self.assertEqual(self.window.state.sample_rate_hz, 5_000)

    def test_enabling_second_channel_clamps_invalid_block_sample_rate(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_acquisition_mode("Block")
        self.window.set_timebase_value(0.002)
        self.window.set_channel_voltage("A", 1.0)
        self.window.set_sample_rate_value(1_000_000)
        self.window.set_channel_voltage("B", 1.0)

        self.assertEqual(self.window.state.sample_rate_hz, 200_000)

    def test_timing_step_buttons_disable_at_timebase_limits(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_timebase_value(TIME_PER_DIV_OPTIONS[0])
        self.assertFalse(self.window.timing_control.minus_button.isEnabled())
        self.assertTrue(self.window.timing_control.plus_button.isEnabled())

        self.window.set_timebase_value(TIME_PER_DIV_OPTIONS[-1])
        self.assertTrue(self.window.timing_control.minus_button.isEnabled())
        self.assertFalse(self.window.timing_control.plus_button.isEnabled())

    def test_annotation_settings_update_from_actions(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_annotation_scope("All captures")
        self.window.set_annotation_tool("Pen")
        self.window.set_annotation_color("#ef3340")

        self.assertEqual(self.window.annotation_settings.scope, "All captures")
        self.assertEqual(self.window.annotation_settings.tool, "Pen")
        self.assertEqual(self.window.annotation_settings.color_hex, "#ef3340")

    def test_annotation_scope_defaults_to_all_captures(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.assertEqual(self.window.annotation_settings.scope, "All captures")

    def test_clear_current_annotations_respects_scope(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.waveform_annotations[0] = [
            AnnotationStroke(points=[(0.1, 0.1), (0.2, 0.2)], color_hex="#1e73be")
        ]
        self.window.global_annotations = [
            AnnotationStroke(points=[(0.3, 0.3), (0.4, 0.4)], color_hex="#ef3340")
        ]

        self.window.set_annotation_scope("This capture")
        self.window.clear_current_annotations()
        self.assertEqual(self.window.waveform_annotations[0], [])
        self.assertEqual(len(self.window.global_annotations), 1)

        self.window.waveform_annotations[0] = [
            AnnotationStroke(points=[(0.1, 0.1), (0.2, 0.2)], color_hex="#1e73be")
        ]
        self.window.set_annotation_scope("All captures")
        self.window.clear_current_annotations()
        self.assertEqual(len(self.window.global_annotations), 0)
        self.assertEqual(len(self.window.waveform_annotations[0]), 1)

    def test_start_annotation_interaction_hides_annotation_panel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window._set_selected_panel(("annotations", None))
        self.window._sync_ui()

        self.window.start_annotation_interaction()

        self.assertIsNone(self.window.selected_panel)

    def test_channel_a_panel_off_turns_off_channel_a(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.set_channel_voltage("A", 10.0)
        self.window.set_channel_voltage("B", 5.0)
        self.window._set_selected_panel(("channel", "A"))
        self.window._sync_ui()

        off_button = None
        for button in self.window.selection_panel.findChildren(
            type(self.window.about_button)
        ):
            if button.objectName() == "selectorOffButton":
                off_button = button
                break

        self.assertIsNotNone(off_button)
        off_button.click()

        self.assertFalse(self.window.state.channel_a.enabled)
        self.assertTrue(self.window.state.channel_b.enabled)

        self.window.set_timebase_value(TIME_PER_DIV_OPTIONS[0])
        self.window.set_sample_rate_value(SAMPLE_RATE_OPTIONS[0])

        self.assertEqual(self.window.state.time_per_div, TIME_PER_DIV_OPTIONS[0])
        self.assertEqual(self.window.state.sample_rate_hz, SAMPLE_RATE_OPTIONS[0])

    def test_timing_selector_updates_panel_and_combined_control(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_timing_panel()

        self.assertEqual(self.window.selected_panel, ("timing", None))
        self.assertFalse(self.window.selection_panel.isHidden())

        self.window.set_timebase_value(TIME_PER_DIV_OPTIONS[15])
        self.window.set_sample_rate_value(SAMPLE_RATE_OPTIONS[2])

        self.assertEqual(
            self.window.timing_control.timebase_value_label.text(), "20 ms /div"
        )
        self.assertEqual(
            self.window.timing_control.sample_rate_value_label.text(), "5 kS/s"
        )

    def test_timing_selector_uses_timebase_and_sample_rate_tabs(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_timing_panel()

        def current_panel_button_texts() -> list[str]:
            texts: list[str] = []

            def walk(layout) -> None:
                for index in range(layout.count()):
                    item = layout.itemAt(index)
                    widget = item.widget()
                    child_layout = item.layout()
                    if widget is not None:
                        for child in widget.findChildren(
                            type(self.window.about_button)
                        ):
                            texts.append(child.text())
                    elif child_layout is not None:
                        walk(child_layout)

            walk(self.window.selection_panel.content_layout)
            return texts

        segment_buttons = [
            button
            for button in self.window.selection_panel.findChildren(
                type(self.window.about_button)
            )
            if button.objectName() == "selectorSegmentButton"
        ]
        segment_texts = [button.text() for button in segment_buttons]
        self.assertIn("Timebase", segment_texts)
        self.assertIn("Sample rate", segment_texts)
        self.assertIn("ns /div", segment_texts)
        self.assertIn("us /div", segment_texts)
        self.assertIn("ms /div", segment_texts)
        self.assertIn("s /div", segment_texts)

        initial_texts = current_panel_button_texts()
        self.assertIn("5 ms /div", initial_texts)
        self.assertNotIn("200 ns /div", initial_texts)
        self.assertNotIn("100 kS/s", initial_texts)

        for button in segment_buttons:
            if button.text() == "ns /div":
                button.click()
                break

        self.app.processEvents()
        ns_texts = current_panel_button_texts()
        self.assertIn("200 ns /div", ns_texts)
        self.assertNotIn("5 ms /div", ns_texts)

        for button in segment_buttons:
            if button.text() == "Sample rate":
                button.click()
                break

        self.app.processEvents()
        all_button_texts = current_panel_button_texts()
        self.assertIn("100 kS/s", all_button_texts)
        self.assertNotIn("200 ns /div", all_button_texts)

    def test_compatible_sample_rate_button_switches_mode(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_timebase_value(0.1)
        self.window.set_acquisition_mode("Block")
        self.window.select_timing_panel()

        segment_buttons = [
            button
            for button in self.window.selection_panel.findChildren(
                type(self.window.about_button)
            )
            if button.objectName() == "selectorSegmentButton"
        ]
        for button in segment_buttons:
            if button.text() == "Sample rate":
                button.click()
                break

        self.app.processEvents()

        compatible_button = None
        for button in self.window.selection_panel.findChildren(
            type(self.window.about_button)
        ):
            if (
                button.objectName() == "selectorOptionButton"
                and button.text() == "1 MS/s"
                and button.isEnabled()
            ):
                compatible_button = button
                break

        self.assertIsNotNone(compatible_button)

        compatible_button.click()

        self.assertEqual(self.window.state.acquisition_mode, "Fast streaming")
        self.assertEqual(self.window.state.sample_rate_hz, 1_000_000)

    def test_mode_selector_updates_state_and_panel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_mode_panel()
        self.assertEqual(self.window.selected_panel, ("mode", None))
        self.assertFalse(self.window.selection_panel.isHidden())

        self.window.set_acquisition_mode("Fast streaming")

        self.assertEqual(self.window.state.acquisition_mode, "Fast streaming")
        self.assertEqual(self.window.mode_control.value_label.text(), "Fast streaming")

    def test_panel_closes_on_outside_click(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_mode_panel()
        self.window._arm_outside_close()
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(5, 5),
            QPointF(5, 5),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        self.window.eventFilter(self.window.waveform_canvas, event)

        self.assertIsNone(self.window.selected_panel)
        self.assertTrue(self.window.selection_panel.isHidden())

    def test_panel_closes_from_collapse_button(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_mode_panel()
        self.window.selection_panel.collapse_button.click()

        self.assertIsNone(self.window.selected_panel)
        self.assertTrue(self.window.selection_panel.isHidden())

    def test_run_button_does_not_close_open_side_panel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_mode_panel()
        self.window._arm_outside_close()

        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(4.0, 4.0),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        self.window.eventFilter(self.window.run_button, event)

        self.assertEqual(self.window.selected_panel, ("mode", None))
        self.assertFalse(self.window.selection_panel.isHidden())

    def test_trigger_selector_updates_state_and_panel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.select_trigger_panel()
        self.window.set_trigger_mode("Auto")
        self.window.set_trigger_type("Simple edge")
        self.window.set_trigger_source("B")
        self.window.set_trigger_direction("Rising")
        self.window.adjust_trigger_level(1)
        self.window.adjust_pre_trigger_percent(-1)

        self.assertEqual(self.window.selected_panel, ("trigger", None))
        self.assertEqual(self.window.state.trigger.mode, "Auto")
        self.assertEqual(self.window.state.trigger.trigger_type, "Simple edge")
        self.assertEqual(self.window.state.trigger.source, "B")
        self.assertEqual(self.window.state.trigger.direction, "Rising")
        self.assertEqual(self.window.state.trigger.pre_trigger_percent, 40)
        self.assertIn(
            "Auto Simple edge", self.window.trigger_control.value_label.text()
        )

    def test_trigger_none_resets_trigger_state(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_trigger_mode("Auto")
        self.window.set_trigger_type("Simple edge")
        self.window.set_trigger_source("B")
        self.window.set_trigger_direction("Rising")
        self.window.adjust_pre_trigger_percent(-1)
        self.window.set_trigger_mode("None")

        self.assertEqual(self.window.state.trigger.mode, "None")
        self.assertEqual(self.window.state.trigger.trigger_type, "Simple edge")
        self.assertEqual(self.window.state.trigger.source, "A")
        self.assertEqual(self.window.state.trigger.direction, "Rising")
        self.assertEqual(self.window.state.trigger.pre_trigger_percent, 50)
        self.assertEqual(self.window.trigger_control.value_label.text(), "None")

    def test_invalid_mode_is_ignored(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.set_acquisition_mode("Invalid mode")

        self.assertEqual(self.window.state.acquisition_mode, ACQUISITION_MODES[0])

    def test_on_frame_ready_updates_history_and_canvas(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        frame = CaptureFrame(
            times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            channel_a=np.array([0.0, 0.5, -0.5], dtype=np.float32),
            channel_b=np.array([], dtype=np.float32),
            sample_rate_hz=1_000_000,
            sample_count=3,
            y_range_volts=1.0,
            source_label="Live",
            connection_label="2204A connected [TEST123]",
            trigger_label="None",
        )

        self.window.on_frame_ready(frame)

        self.assertIs(self.window.waveform_canvas.frame, frame)
        self.assertEqual(self.window.history[-1], frame)
        self.assertIn("2204A connected", self.window.connection_label.text())
        self.assertEqual(
            self.window.waveform_history_control.value_label.text(), "1 of 1"
        )
        self.assertTrue(self.window.waveform_history_control.minus_button.isEnabled())
        self.assertTrue(self.window.waveform_history_control.plus_button.isEnabled())

    def test_waveform_button_opens_waveform_panel(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.waveform_history_control.body.clicked.emit()

        self.assertEqual(self.window.selected_panel, ("waveform", None))
        self.assertFalse(self.window.selection_panel.isHidden())

    def test_waveform_preview_strip_only_shows_with_panel_and_saved_waveforms(
        self,
    ) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        frame = CaptureFrame(
            times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            channel_a=np.array([0.0, 0.5, -0.5], dtype=np.float32),
            channel_b=np.array([], dtype=np.float32),
            sample_rate_hz=1_000_000,
            sample_count=3,
            y_range_volts=1.0,
            source_label="Live",
            connection_label="2204A connected [TEST123]",
            trigger_label="None",
        )

        self.window.on_frame_ready(frame)
        self.assertTrue(self.window.waveform_preview_container.isHidden())

        self.window.select_waveform_panel()

        self.assertFalse(self.window.waveform_preview_container.isHidden())

    def test_adjust_max_waveforms_trims_history(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.state.max_waveforms = 3

        for index in range(5):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, index + 0.1, index + 0.2], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.assertEqual(len(self.window.history), 3)
        self.assertEqual(self.window.history_index, 2)

    def test_timebase_change_resets_waveform_history(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(3):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, index + 0.1, index + 0.2], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window.set_timebase_value(10e-3)

        self.assertEqual(
            self.window.waveform_history_control.value_label.text(), "0 of 0"
        )
        self.assertEqual(len(self.window.history), 1)
        self.assertEqual(self.window.history_index, 0)
        self.assertEqual(self.window.history[0].sample_count, 0)

    def test_mode_change_resets_waveform_history(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(3):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, index + 0.1, index + 0.2], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window.set_acquisition_mode("Fast streaming")

        self.assertEqual(
            self.window.waveform_history_control.value_label.text(), "0 of 0"
        )
        self.assertEqual(len(self.window.history), 1)
        self.assertEqual(self.window.history[0].sample_count, 0)

    def test_channel_voltage_change_resets_waveform_history(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(3):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, index + 0.1, index + 0.2], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window.set_channel_voltage("A", 2.0)

        self.assertEqual(
            self.window.waveform_history_control.value_label.text(), "0 of 0"
        )
        self.assertEqual(len(self.window.history), 1)
        self.assertEqual(self.window.history[0].sample_count, 0)

    def test_unlimited_waveform_history_does_not_trim(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        self.window.set_waveform_limit_mode("Unlimited")

        for index in range(12):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, index + 0.1, index + 0.2], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.assertEqual(self.window.state.max_waveforms, 0)
        self.assertEqual(len(self.window.history), 12)

    def test_waveform_preview_click_selects_saved_frame(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(3):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, 0.2 * (index + 1), 0.0], dtype=np.float32),
                channel_b=np.array([0.0, -0.1 * (index + 1), 0.0], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window._sync_ui()
        target_rect = self.window.waveform_preview_strip._thumbnail_rects()[0]
        press = QMouseEvent(
            QEvent.MouseButtonPress,
            target_rect.center(),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        self.window.waveform_preview_strip.mousePressEvent(press)

        self.assertEqual(self.window.history_index, 0)

    def test_waveform_preview_click_keeps_waveform_panel_open(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(3):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, 0.2 * (index + 1), 0.0], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window.select_waveform_panel()
        target_rect = self.window.waveform_preview_strip._thumbnail_rects()[0]
        press = QMouseEvent(
            QEvent.MouseButtonPress,
            target_rect.center(),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        self.window.waveform_preview_strip.mousePressEvent(press)

        self.assertEqual(self.window.selected_panel, ("waveform", None))
        self.assertFalse(self.window.selection_panel.isHidden())

    def test_waveform_preview_always_draws_10_slots(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)
        frame = CaptureFrame(
            times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            channel_a=np.array([0.0, 0.5, -0.5], dtype=np.float32),
            channel_b=np.array([], dtype=np.float32),
            sample_rate_hz=1_000_000,
            sample_count=3,
            y_range_volts=1.0,
            source_label="Live",
            connection_label="2204A connected [TEST123]",
            trigger_label="None",
        )
        self.window._push_frame(frame)
        self.window.waveform_preview_strip.resize(900, 82)

        self.assertEqual(len(self.window.waveform_preview_strip._thumbnail_rects()), 10)

    def test_waveform_preview_uses_10_item_pages(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        for index in range(20):
            frame = CaptureFrame(
                times=np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
                channel_a=np.array([0.0, 0.2 * (index + 1), 0.0], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000_000,
                sample_count=3,
                y_range_volts=1.0,
                source_label=f"Live {index}",
                connection_label="2204A connected [TEST123]",
                trigger_label="None",
            )
            self.window._push_frame(frame)

        self.window.select_waveform_panel()

        self.assertEqual(len(self.window.waveform_preview_strip.visible_items()), 10)
        self.assertTrue(self.window.waveform_preview_prev_button.isEnabled())
        self.assertFalse(self.window.waveform_preview_next_button.isEnabled())

        self.window.show_previous_waveform_preview_page()

        self.assertEqual(len(self.window.waveform_preview_strip.visible_items()), 10)
        self.assertFalse(self.window.waveform_preview_prev_button.isEnabled())
        self.assertTrue(self.window.waveform_preview_next_button.isEnabled())

    def test_about_dialog_loads_app_and_device_data(self) -> None:
        self.window = MainWindow(controller=FakeController(), autostart_worker=False)

        self.window.show_about_dialog()

        self.assertIn("PicoWave", self.window.about_dialog.content_label.text())
        self.window.about_dialog.set_section("device")
        self.assertIn("2204A", self.window.about_dialog.content_label.text())
        self.assertIn("TEST123", self.window.about_dialog.content_label.text())


class ControllerTests(unittest.TestCase):
    def test_logging_configuration_creates_log_directory_and_handler(self) -> None:
        logger = configure_logging()

        self.assertIs(logger, APP_LOGGER)
        self.assertTrue(os.path.isdir(LOG_DIR))
        self.assertTrue(
            any(
                getattr(handler, "baseFilename", "") == LOG_FILE
                for handler in logger.handlers
            )
        )

    def test_classify_sample_rates_groups_modes(self) -> None:
        available, compatible, unavailable = classify_sample_rates(1.0, "Block", 1)

        self.assertEqual(available, [])
        self.assertIn(5_000, compatible["Fast streaming"])
        self.assertIn(1_000_000, unavailable)

    def test_1ms_is_available_in_fast_streaming_for_100ms_div(self) -> None:
        available, compatible, unavailable = classify_sample_rates(0.1, "Block", 1)

        self.assertNotIn(1_000_000, unavailable)
        self.assertIn(1_000_000, compatible["Fast streaming"])

    def test_target_sample_count_respects_active_channel_limit(self) -> None:
        controller = Pico2204AController()
        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.channel_b.enabled = False
        settings.time_per_div = 5e-3
        settings.sample_rate_hz = 1_000_000

        self.assertEqual(controller._target_sample_count(settings), 8_000)

        settings.channel_b.enabled = True
        self.assertEqual(controller._target_sample_count(settings), 4_000)

    def test_capture_live_requires_enabled_channel(self) -> None:
        controller = Pico2204AController()

        with self.assertRaisesRegex(RuntimeError, "Enable Channel A or B"):
            controller._capture_block_mode(ScopeState())

    def test_candidate_sdk_dirs_uses_path_entries_with_ps2000_dll(self) -> None:
        controller = Pico2204AController()
        path_dir = r"C:\Drivers\Pico"

        with patch.dict(
            os.environ,
            {
                "PATH": path_dir,
            },
            clear=False,
        ):
            with (
                patch("picowave.controller.os.path.isdir") as mock_isdir,
                patch("picowave.controller.os.path.isfile") as mock_isfile,
            ):
                mock_isdir.side_effect = lambda path: path == path_dir
                mock_isfile.side_effect = lambda path: (
                    path == os.path.join(path_dir, "ps2000.dll")
                )

                candidates = controller._candidate_sdk_dirs()

        self.assertEqual(candidates, [path_dir])

    def test_choose_block_capture_plan_reduces_sample_count_until_timebase_is_valid(
        self,
    ) -> None:
        controller = Pico2204AController()
        attempted_counts = []

        def choose_timebase(sample_count, target_span):
            attempted_counts.append(sample_count)
            if sample_count > 4_000:
                return None
            return SimpleNamespace(timebase_id=6, time_interval=12.5e-6)

        controller._choose_timebase = choose_timebase

        sample_count, timebase = controller._choose_block_capture_plan(5_000, 50e-3)

        self.assertEqual(attempted_counts[0], 5_000)
        self.assertLess(sample_count, 5_000)
        self.assertIsNotNone(timebase)
        self.assertEqual(timebase.timebase_id, 6)

    def test_capture_block_mode_returns_samples_for_both_channels(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()
        controller._channel_config = lambda name, enabled, coupling, range_volts: (
            SimpleNamespace(
                name=name,
                enabled=enabled,
                coupling=coupling,
                range_volts=range_volts,
            )
        )
        controller._choose_timebase = lambda sample_count, target_span: SimpleNamespace(
            timebase_id=7,
            time_interval=1e-6,
        )

        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.channel_b.enabled = True
        settings.channel_a.range_volts = 1.0
        settings.channel_b.range_volts = 5.0

        frame = controller._capture_block_mode(settings)

        np.testing.assert_allclose(
            frame.channel_a, np.array([0.0, 0.5, -0.5], dtype=np.float32)
        )
        np.testing.assert_allclose(
            frame.channel_b, np.array([5.0, 0.0, -5.0], dtype=np.float32)
        )
        self.assertEqual(frame.sample_rate_hz, 1_000_000)
        self.assertEqual(frame.connection_label, "2204A [TEST123]")
        self.assertTrue(controller._ps.trigger_configured)
        self.assertTrue(controller._ps.stopped)
        self.assertEqual(frame.source_label, "Block")

    def test_build_frame_applies_probe_scale_and_invert(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()

        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.channel_a.range_volts = 10.0
        settings.channel_a.probe_scale = 10
        settings.channel_a.invert = True

        frame = controller._build_frame(
            settings,
            np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            {"A": np.array([0, 50, -50], dtype=np.int16)},
            {"A": 1.0, "B": 5.0},
            "Block",
            1_000_000,
        )

        np.testing.assert_allclose(
            frame.channel_a, np.array([0.0, -5.0, 5.0], dtype=np.float32)
        )
        self.assertEqual(frame.y_range_volts, 10.0)

    def test_apply_trigger_configures_simple_edge_trigger(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()
        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.trigger.mode = "Auto"
        settings.trigger.source = "A"
        settings.trigger.direction = "Falling"
        settings.trigger.level_volts = 0.5
        settings.trigger.pre_trigger_percent = 40
        active_ranges = {"A": 1.0, "B": 5.0}

        controller._apply_trigger(settings, active_ranges)

        self.assertTrue(controller._ps.trigger_configured)
        self.assertEqual(controller._ps.trigger_args["source"], 0)
        self.assertEqual(controller._ps.trigger_args["direction"], 1)
        self.assertEqual(controller._ps.trigger_args["auto_trigger_ms"], 100)

    def test_apply_trigger_uses_probe_scale_and_invert_for_simple_edge(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()
        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.channel_a.invert = True
        settings.channel_a.probe_scale = 10
        settings.trigger.mode = "Auto"
        settings.trigger.source = "A"
        settings.trigger.direction = "Rising"
        settings.trigger.level_volts = 5.0

        controller._apply_trigger(settings, {"A": 1.0, "B": 5.0})

        self.assertEqual(controller._ps.trigger_args["threshold"], -50)
        self.assertEqual(controller._ps.trigger_args["direction"], 1)

    def test_build_frame_includes_trigger_marker_metadata(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()

        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.trigger.mode = "Auto"
        settings.trigger.source = "A"
        settings.trigger.level_volts = 0.5
        settings.trigger.pre_trigger_percent = 40

        frame = controller._build_frame(
            settings,
            np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            {"A": np.array([0, 50, -50], dtype=np.int16)},
            {"A": 1.0, "B": 5.0},
            "Block",
            1_000_000,
        )

        self.assertTrue(frame.trigger_enabled)
        self.assertEqual(frame.trigger_source, "A")
        self.assertAlmostEqual(frame.trigger_level_volts, 0.5)
        self.assertAlmostEqual(frame.trigger_time_ratio, 0.4)
        self.assertTrue(frame.trigger_confirmed)

    def test_build_frame_marks_channel_overrange_samples(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()

        settings = ScopeState()
        settings.channel_a.enabled = True

        frame = controller._build_frame(
            settings,
            np.array([0.0, 1e-6, 2e-6], dtype=np.float32),
            {"A": np.array([0, 100, -100], dtype=np.int16)},
            {"A": 1.0, "B": 5.0},
            "Block",
            1_000_000,
        )

        np.testing.assert_array_equal(
            frame.channel_a_overrange, np.array([0, 1, -1], dtype=np.int8)
        )

    def test_build_frame_preserves_captured_time_axis(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()

        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.time_per_div = 5e-3

        frame = controller._build_frame(
            settings,
            np.array([0.0, 10.24e-6, 20.48e-6, 30.72e-6], dtype=np.float32),
            {"A": np.array([0, 50, -50, 0], dtype=np.int16)},
            {"A": 1.0, "B": 5.0},
            "Block",
            97_656.25,
        )

        self.assertAlmostEqual(frame.times[0], 0.0)
        self.assertAlmostEqual(frame.times[-1], 30.72e-6)
        self.assertAlmostEqual(frame.sample_rate_hz, 97_656.25)

    def test_capture_dispatches_by_mode(self) -> None:
        controller = Pico2204AController()
        controller._device = object()
        controller._connect_if_needed = lambda force=False: None
        block_frame = build_empty_frame(ScopeState(), "Block", "Block backend")
        fast_frame = build_empty_frame(ScopeState(), "Fast streaming", "Fast backend")
        calls = []

        def block(settings):
            calls.append("block")
            return block_frame

        def fast(settings):
            calls.append("fast")
            return fast_frame

        controller._capture_block_mode = block
        controller._capture_fast_streaming = fast

        settings = ScopeState()
        self.assertIs(controller.capture(settings), block_frame)

        settings.acquisition_mode = "Fast streaming"
        self.assertIs(controller.capture(settings), fast_frame)
        self.assertEqual(calls, ["block", "fast"])

    def test_capture_error_does_not_disconnect_device(self) -> None:
        controller = Pico2204AController()
        controller._device = object()
        controller._connect_if_needed = lambda force=False: None
        controller._capture_fast_streaming = lambda settings: (_ for _ in ()).throw(
            RuntimeError("Fast streaming overview buffer overrun.")
        )

        settings = ScopeState()
        settings.acquisition_mode = "Fast streaming"

        with self.assertRaisesRegex(RuntimeError, "overview buffer overrun"):
            controller.capture(settings)

        self.assertTrue(controller.is_connected)

    def test_fast_streaming_settings_keep_raw_aggregation_for_extended_record_lengths(
        self,
    ) -> None:
        controller = Pico2204AController()

        aggregate, overview_size = controller._fast_streaming_settings(
            FAST_STREAMING_MAX_SAMPLES
        )

        self.assertEqual(aggregate, 1)
        self.assertEqual(overview_size, FAST_STREAMING_MAX_SAMPLES)

    def test_fast_streaming_capture_window_expands_for_triggered_capture(self) -> None:
        controller = Pico2204AController()
        settings = ScopeState()
        settings.trigger.mode = "Auto"

        desired, capture = controller._fast_streaming_capture_window(settings, 10e-6)

        self.assertGreater(capture, desired)
        self.assertGreaterEqual(capture - desired, desired)

    def test_find_simple_edge_trigger_index_returns_real_crossing(self) -> None:
        controller = Pico2204AController()
        raw = np.array([0, 0, 0, 50, 50, 50], dtype=np.int16)

        crossing = controller._find_simple_edge_trigger_index(
            raw, 25, "Rising", hint_index=1
        )

        self.assertEqual(crossing, 3)

    def test_fast_streaming_can_realign_trigger_from_raw_signal(self) -> None:
        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakePS()
        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.channel_a.range_volts = 10.0
        settings.trigger.mode = "Auto"
        settings.trigger.trigger_type = "Simple edge"
        settings.trigger.source = "A"
        settings.trigger.direction = "Rising"
        settings.trigger.level_volts = 2.0
        raw_data = {
            "A": np.array([0, 0, 0, 0, 60, 60, 60], dtype=np.int16),
        }

        index, source = controller._software_realign_fast_streaming_trigger(
            settings,
            {"A": 10.0},
            raw_data,
            captured=7,
            driver_trigger_index=1,
        )

        self.assertEqual(index, 4)
        self.assertEqual(source, "software")

    def test_fast_streaming_trigger_requests_pre_trigger_window(self) -> None:
        controller = Pico2204AController()
        settings = ScopeState()
        settings.trigger.mode = "Auto"
        settings.trigger.pre_trigger_percent = 20

        start_time_ns = controller._fast_streaming_start_time_ns(
            settings,
            sample_count=5_000,
            actual_interval_s=10e-6,
            triggered=True,
        )

        self.assertAlmostEqual(start_time_ns, -9_998_000.0)

    def test_fast_streaming_without_trigger_requests_start_of_capture(self) -> None:
        controller = Pico2204AController()
        settings = ScopeState()
        settings.trigger.mode = "Auto"
        settings.trigger.pre_trigger_percent = 20

        start_time_ns = controller._fast_streaming_start_time_ns(
            settings,
            sample_count=5_000,
            actual_interval_s=10e-6,
            triggered=False,
        )

        self.assertEqual(start_time_ns, 0.0)

    def test_fast_streaming_uses_visible_window_for_trigger_start_time(self) -> None:
        class FakeFastStreamingPS:
            def _run_streaming_ns(self, *args):
                return 1

            def maximum_value(self, _device):
                return 100

            def _get_streaming_last_values(self, _handle, callback):
                overview_callback = cast(
                    callback,
                    CFUNCTYPE(
                        None,
                        POINTER(POINTER(c_int16)),
                        c_int16,
                        c_uint32,
                        c_int16,
                        c_int16,
                        c_uint32,
                    ),
                )
                overview_callback(None, 0, 0, 1, 1, 0)

            def _overview_buffer_status(self, _handle, _overrun):
                return 0

            def stop(self, _device):
                return None

            def _get_streaming_values_no_aggregation(
                self,
                _handle,
                _start_time,
                buffer_a,
                _buffer_b,
                _buffer_a_min,
                _buffer_b_min,
                overflow,
                trigger_at,
                triggered,
                sample_count,
            ):
                overflow._obj.value = 0
                trigger_at._obj.value = 200
                triggered._obj.value = 1
                return sample_count.value

        controller = Pico2204AController()
        controller._device = FakeDevice()
        controller._ps = FakeFastStreamingPS()
        controller._apply_channels = lambda settings: {"A": 1.0}
        controller._apply_trigger = lambda settings, active_ranges: None
        controller._interval_to_ps2000_units = lambda interval_s: (1, 0, 10e-6)
        controller._fast_streaming_capture_window = lambda settings, actual_interval_s: (
            1001,
            1251,
        )
        recorded_sample_counts = []
        build_frame_calls = {}
        controller._fast_streaming_start_time_ns = (
            lambda settings, sample_count, actual_interval_s, triggered: (
                recorded_sample_counts.append(sample_count) or 0.0
            )
        )
        controller._software_realign_fast_streaming_trigger = (
            lambda settings, active_ranges, raw_buffers, captured, driver_trigger_index: (
                driver_trigger_index,
                "driver",
            )
        )
        controller._build_frame = (
            lambda settings, times, raw_data, active_ranges, source_label, sample_rate_hz, **kwargs: (
                build_frame_calls.update(
                    {
                        "times": times.copy(),
                        "trigger_sample_index": kwargs.get("trigger_sample_index"),
                    }
                )
                or SimpleNamespace(source_label="Fast streaming")
            )
        )

        settings = ScopeState()
        settings.channel_a.enabled = True
        settings.acquisition_mode = "Fast streaming"
        settings.trigger.mode = "Auto"

        controller._capture_fast_streaming(settings)

        self.assertEqual(recorded_sample_counts, [1001])
        self.assertAlmostEqual(float(build_frame_calls["times"][0]), 0.0)
        self.assertEqual(build_frame_calls["trigger_sample_index"], 200)

    def test_empty_frame_contains_both_channel_buffers(self) -> None:
        settings = ScopeState()

        frame = build_empty_frame(
            settings, "Hardware", "Connect a PicoScope 2204A to begin capture."
        )

        self.assertEqual(frame.channel_a.size, 0)
        self.assertEqual(frame.channel_b.size, 0)


class WaveformCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_pen_annotation_can_draw_without_type_error(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        canvas.set_frame(build_empty_frame(ScopeState(), "Hardware", "Idle"))
        waveform_annotations: list[AnnotationStroke] = []
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Pen", color_hex="#1e73be")
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(200.0, 200.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(260.0, 240.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(280.0, 260.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertEqual(len(waveform_annotations), 1)
        self.assertGreaterEqual(len(waveform_annotations[0].points), 2)

    def test_pen_click_without_visible_movement_does_not_create_ghost_line(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        canvas.set_frame(build_empty_frame(ScopeState(), "Hardware", "Idle"))
        waveform_annotations: list[AnnotationStroke] = []
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Pen", color_hex="#1e73be")
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(220.0, 220.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(220.0, 220.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.resize(980, 600)
        canvas.mouseReleaseEvent(release)

        self.assertEqual(len(waveform_annotations), 0)

    def test_annotation_tool_updates_canvas_cursor(self) -> None:
        canvas = WaveformCanvas()

        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Text", color_hex="#1e73be")
        )
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.IBeamCursor)

        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Off", color_hex="#1e73be")
        )
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_eraser_can_remove_waveform_annotations_while_global_scope_is_selected(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        waveform_annotations = [
            AnnotationStroke(
                points=[(0.2, 0.2), (0.3, 0.2), (0.4, 0.2)], color_hex="#1e73be"
            )
        ]
        global_annotations = [
            AnnotationStroke(points=[(0.6, 0.2), (0.7, 0.2)], color_hex="#ef3340")
        ]
        canvas.set_annotations(waveform_annotations, global_annotations)
        canvas.set_annotation_settings(
            AnnotationSettings(scope="All captures", tool="Eraser", color_hex="#1e73be")
        )

        erase_point = canvas._normalized_to_plot((0.3, 0.2), canvas._plot_rect())
        canvas._erase_at(erase_point, canvas._plot_rect())

        self.assertEqual(len(waveform_annotations), 0)
        self.assertEqual(len(global_annotations), 1)

    def test_point_eraser_splits_stroke_instead_of_deleting_whole_trace(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        waveform_annotations = [
            AnnotationStroke(
                points=[
                    (0.20, 0.20),
                    (0.24, 0.20),
                    (0.28, 0.20),
                    (0.32, 0.20),
                    (0.36, 0.20),
                ],
                color_hex="#1e73be",
            )
        ]
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Eraser", color_hex="#1e73be")
        )

        erase_point = canvas._normalized_to_plot((0.28, 0.20), canvas._plot_rect())
        canvas._erase_at(erase_point, canvas._plot_rect())

        self.assertEqual(len(waveform_annotations), 2)
        self.assertTrue(all(len(item.points) >= 2 for item in waveform_annotations))

    def test_eraser_can_remove_text_annotation_by_clicking_inside_text_box(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        waveform_annotations = [
            AnnotationText(position=(0.25, 0.25), text="Test", color_hex="#1e73be")
        ]
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="All captures", tool="Eraser", color_hex="#1e73be")
        )

        text_rect = canvas._annotation_text_rect(
            waveform_annotations[0], canvas._plot_rect()
        )
        canvas._erase_at(text_rect.center(), canvas._plot_rect())

        self.assertEqual(len(waveform_annotations), 0)

    def test_text_annotation_uses_inline_box_and_commits_on_enter(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        canvas.set_frame(build_empty_frame(ScopeState(), "Hardware", "Idle"))
        waveform_annotations = []
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Text", color_hex="#1e73be")
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(240.0, 220.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mousePressEvent(press)

        self.assertIsNotNone(canvas._active_text_box)
        self.assertEqual(len(waveform_annotations), 0)

        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key_H, Qt.KeyboardModifier.NoModifier, "H"
            )
        )
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key_I, Qt.KeyboardModifier.NoModifier, "i"
            )
        )
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key_Return, Qt.KeyboardModifier.NoModifier
            )
        )

        self.assertIsNone(canvas._active_text_box)
        self.assertEqual(len(waveform_annotations), 1)
        self.assertEqual(waveform_annotations[0].text, "Hi")

    def test_text_annotation_escape_cancels_inline_box(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        canvas.set_frame(build_empty_frame(ScopeState(), "Hardware", "Idle"))
        waveform_annotations = []
        canvas.set_annotations(waveform_annotations, [])
        canvas.set_annotation_settings(
            AnnotationSettings(scope="This capture", tool="Text", color_hex="#1e73be")
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(260.0, 230.0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mousePressEvent(press)
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key_A, Qt.KeyboardModifier.NoModifier, "A"
            )
        )
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key_Escape, Qt.KeyboardModifier.NoModifier
            )
        )

        self.assertIsNone(canvas._active_text_box)
        self.assertEqual(len(waveform_annotations), 0)

    def test_vertical_offset_drag_updates_channel_display_offset(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)
        canvas.set_frame(build_empty_frame(state, "Hardware", "Idle"))
        changes = []
        canvas.vertical_offset_changed.connect(
            lambda name, offset: changes.append((name, offset))
        )

        axis_rect = canvas._channel_axis_drag_rect("A", canvas._plot_rect())
        self.assertIsNotNone(axis_rect)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            axis_rect.center(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(
                axis_rect.center().x(),
                axis_rect.center().y() - (canvas._plot_rect().height() / 5.0),
            ),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            move.position(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertTrue(changes)
        self.assertEqual(changes[-1][0], "A")
        self.assertGreater(changes[-1][1], 0.0)
        self.assertGreater(canvas.state.channel_a.vertical_offset_divs, 0.0)

    def test_vertical_offset_changes_display_position_without_changing_voltage_value(
        self,
    ) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)

        plot_rect = canvas._plot_rect()
        base_y = (
            plot_rect.top()
            + canvas._channel_y_ratio(
                0.0,
                canvas.state.channel_a,
                channel_display_range(canvas.state.channel_a),
            )
            * plot_rect.height()
        )

        canvas.state.channel_a.vertical_offset_divs = 2.0
        shifted_y = (
            plot_rect.top()
            + canvas._channel_y_ratio(
                0.0,
                canvas.state.channel_a,
                channel_display_range(canvas.state.channel_a),
            )
            * plot_rect.height()
        )

        self.assertLess(shifted_y, base_y)

    def test_channel_axis_value_reflects_vertical_offset(self) -> None:
        canvas = WaveformCanvas()
        channel = ScopeState().channel_a

        self.assertAlmostEqual(canvas._channel_axis_value(5, channel), 0.0)
        channel.vertical_offset_divs = 2.0
        self.assertAlmostEqual(canvas._channel_axis_value(5, channel), -0.4)

    def test_channel_b_vertical_offset_handle_is_on_right_side(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_b.enabled = True
        canvas.set_state(state)

        handle_rect = canvas._channel_axis_drag_rect("B", canvas._plot_rect())

        self.assertIsNotNone(handle_rect)
        self.assertGreaterEqual(handle_rect.right(), canvas._plot_rect().right())

    def test_default_channel_draw_order_keeps_a_on_top(self) -> None:
        canvas = WaveformCanvas()
        canvas.set_state(ScopeState())

        self.assertEqual(canvas._channel_draw_order, ["B", "A"])

    def test_dragging_channel_handle_brings_that_channel_to_front(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.channel_b.enabled = True
        canvas.set_state(state)

        handle_rect = canvas._channel_axis_drag_rect("B", canvas._plot_rect())
        self.assertIsNotNone(handle_rect)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            handle_rect.center(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            handle_rect.center(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.mouseReleaseEvent(release)

        self.assertEqual(canvas._channel_draw_order[-1], "B")

    def test_hovering_axis_changes_cursor_to_vertical_offset_cursor(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)

        axis_rect = canvas._channel_axis_drag_rect("A", canvas._plot_rect())
        canvas._update_hover_cursor(axis_rect.center())

        self.assertNotEqual(canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_leaving_axis_area_resets_cursor_to_default(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)

        axis_rect = canvas._channel_axis_drag_rect("A", canvas._plot_rect())
        canvas._update_hover_cursor(axis_rect.center())
        canvas._update_hover_cursor(
            QPointF(canvas._plot_rect().center().x(), canvas._plot_rect().center().y())
        )

        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_horizontal_zoom_changes_visible_time_window(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="None",
        )
        canvas.set_frame(frame)

        full_start, full_end = canvas._visible_time_window()
        canvas._zoom_horizontal(0.5, True)
        zoom_start, zoom_end = canvas._visible_time_window()

        self.assertLess(zoom_end - zoom_start, full_end - full_start)

    def test_zoom_button_toggles_zoom_box_mode(self) -> None:
        canvas = WaveformCanvas()

        self.assertFalse(canvas._zoom_box_mode)
        canvas.toggle_zoom_box_mode()
        self.assertTrue(canvas._zoom_box_mode)
        canvas.toggle_zoom_box_mode()
        self.assertFalse(canvas._zoom_box_mode)

    def test_channel_vertical_zoom_changes_visible_range_only(self) -> None:
        channel = ScopeState().channel_a

        self.assertEqual(channel_display_range(channel), 1.0)
        self.assertEqual(channel_visible_range(channel), 1.0)

        channel.display_zoom = 2.0

        self.assertEqual(channel_display_range(channel), 1.0)
        self.assertEqual(channel_visible_range(channel), 0.5)

    def test_double_click_plot_resets_horizontal_zoom(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="None",
        )
        canvas.set_frame(frame)
        canvas._zoom_horizontal(0.5, True)

        double_click = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            canvas._plot_rect().center(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mouseDoubleClickEvent(double_click)

        self.assertAlmostEqual(canvas._view_start_ratio, 0.0)
        self.assertAlmostEqual(canvas._view_end_ratio, 1.0)

    def test_left_drag_zoom_box_reduces_horizontal_view_range(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="None",
        )
        canvas.set_frame(frame)
        canvas.toggle_zoom_box_mode()
        plot_rect = canvas._plot_rect()
        start = QPointF(
            plot_rect.left() + (plot_rect.width() * 0.2),
            plot_rect.top() + (plot_rect.height() * 0.2),
        )
        end = QPointF(
            plot_rect.left() + (plot_rect.width() * 0.7),
            plot_rect.top() + (plot_rect.height() * 0.8),
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            end,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            end,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertLess(canvas._view_end_ratio - canvas._view_start_ratio, 1.0)

    def test_trigger_marker_point_uses_current_trigger_state(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.trigger.mode = "Auto"
        state.trigger.pre_trigger_percent = 60
        state.trigger.level_volts = 0.25
        state.channel_a.range_volts = 1.0
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="Auto Simple edge",
            trigger_enabled=True,
            trigger_source="A",
            trigger_level_volts=0.0,
            trigger_time_ratio=0.4,
            trigger_confirmed=True,
        )
        canvas.set_frame(frame)

        marker = canvas._trigger_marker_point(canvas._plot_rect())

        self.assertIsNotNone(marker)
        self.assertAlmostEqual(
            marker.x(),
            canvas._plot_rect().left() + (canvas._plot_rect().width() * 0.6),
            delta=1.0,
        )
        self.assertLess(marker.y(), canvas._plot_rect().center().y())

    def test_dragging_trigger_marker_emits_level_and_pre_trigger_changes(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.trigger.mode = "Auto"
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="Auto Simple edge",
            trigger_enabled=True,
            trigger_source="A",
            trigger_level_volts=0.0,
            trigger_time_ratio=0.4,
            trigger_confirmed=True,
        )
        canvas.set_frame(frame)

        levels = []
        pre_triggers = []
        canvas.trigger_level_changed.connect(levels.append)
        canvas.trigger_pre_trigger_changed.connect(pre_triggers.append)

        marker = canvas._trigger_marker_point(canvas._plot_rect())
        self.assertIsNotNone(marker)
        target = QPointF(marker.x() + 40.0, marker.y() - 30.0)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            marker,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            target,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            target,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertTrue(levels)
        self.assertTrue(pre_triggers)
        self.assertGreater(pre_triggers[-1], 40)

    def test_trigger_marker_hides_when_trigger_mode_is_none(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.trigger.mode = "None"
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Block",
            connection_label="Test",
            trigger_label="Auto Simple edge",
            trigger_enabled=True,
            trigger_source="A",
            trigger_level_volts=0.0,
            trigger_time_ratio=0.4,
            trigger_confirmed=True,
        )
        canvas.set_frame(frame)

        self.assertIsNone(canvas._trigger_marker_point(canvas._plot_rect()))

    def test_visible_overrange_channels_reports_active_channel(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)
        canvas.set_frame(
            CaptureFrame(
                times=np.array([0.0, 0.5, 1.0], dtype=np.float32),
                channel_a=np.array([0.0, 1.0, 1.0], dtype=np.float32),
                channel_b=np.array([], dtype=np.float32),
                sample_rate_hz=1_000.0,
                sample_count=3,
                y_range_volts=1.0,
                source_label="Block",
                connection_label="Connected",
                trigger_label="None",
                channel_a_overrange=np.array([0, 1, 1], dtype=np.int8),
            )
        )

        self.assertEqual(canvas._visible_overrange_channels(), ["A"])

    def test_trigger_marker_hit_area_extends_around_circle(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.trigger.mode = "Auto"
        canvas.set_state(state)
        frame = CaptureFrame(
            times=np.linspace(0.0, 0.05, 1000, dtype=np.float32),
            channel_a=np.zeros(1000, dtype=np.float32),
            channel_b=np.zeros(1000, dtype=np.float32),
            sample_rate_hz=100_000,
            sample_count=1000,
            y_range_volts=1.0,
            source_label="Fast streaming",
            connection_label="Test",
            trigger_label="Auto Simple edge",
            trigger_enabled=True,
            trigger_source="A",
            trigger_level_volts=0.0,
            trigger_time_ratio=0.4,
            trigger_confirmed=True,
        )
        canvas.set_frame(frame)

        marker = canvas._trigger_marker_point(canvas._plot_rect())
        self.assertIsNotNone(marker)

        nearby_point = QPointF(marker.x() + 12.0, marker.y() + 3.0)
        self.assertTrue(
            canvas._trigger_marker_contains(nearby_point, canvas._plot_rect())
        )

    def test_zoom_out_hits_full_horizontal_before_full_reset(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)
        canvas._set_view_range(0.06, 0.96)
        canvas.state.channel_a.display_zoom = 2.0
        canvas.state.channel_a.vertical_offset_divs = 1.5

        canvas._zoom_horizontal(0.5, False)

        self.assertAlmostEqual(canvas._view_start_ratio, 0.0)
        self.assertAlmostEqual(canvas._view_end_ratio, 1.0)
        self.assertAlmostEqual(canvas.state.channel_a.display_zoom, 2.0)
        self.assertAlmostEqual(canvas.state.channel_a.vertical_offset_divs, 1.5)

        canvas._zoom_horizontal(0.5, False)

        self.assertAlmostEqual(canvas._view_start_ratio, 0.0)
        self.assertAlmostEqual(canvas._view_end_ratio, 1.0)
        self.assertAlmostEqual(canvas.state.channel_a.display_zoom, 1.0)
        self.assertAlmostEqual(canvas.state.channel_a.vertical_offset_divs, 0.0)

    def test_zoom_status_panel_appears_when_zoom_is_active(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())

        self.assertTrue(canvas.zoom_status_panel.isHidden())

        canvas._zoom_horizontal(0.5, True)

        self.assertFalse(canvas.zoom_status_panel.isHidden())
        self.assertEqual(canvas.zoom_reset_button.text(), "Reset")

    def test_reset_all_zoom_restores_default_view_and_hides_panel(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        canvas.set_state(state)
        canvas._zoom_horizontal(0.5, True)
        canvas.state.channel_a.display_zoom = 2.5
        canvas.state.channel_a.vertical_offset_divs = 1.75
        canvas._refresh_zoom_status_panel()

        canvas._reset_all_zoom()

        self.assertAlmostEqual(canvas._view_start_ratio, 0.0)
        self.assertAlmostEqual(canvas._view_end_ratio, 1.0)
        self.assertAlmostEqual(canvas.state.channel_a.display_zoom, 1.0)
        self.assertAlmostEqual(canvas.state.channel_a.vertical_offset_divs, 0.0)
        self.assertTrue(canvas.zoom_status_panel.isHidden())

    def test_middle_button_drag_pans_zoomed_view(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        canvas.set_state(ScopeState())
        canvas._set_view_range(0.2, 0.6)
        plot_rect = canvas._plot_rect()
        start = QPointF(
            plot_rect.left() + (plot_rect.width() * 0.6), plot_rect.center().y()
        )
        end = QPointF(
            plot_rect.left() + (plot_rect.width() * 0.3), plot_rect.center().y()
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            end,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            end,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        initial_start = canvas._view_start_ratio
        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertGreater(canvas._view_start_ratio, initial_start)

    def test_middle_button_drag_pans_vertical_zoomed_view(self) -> None:
        canvas = WaveformCanvas()
        canvas.resize(800, 600)
        state = ScopeState()
        state.channel_a.enabled = True
        state.channel_a.display_zoom = 2.0
        canvas.set_state(state)
        plot_rect = canvas._plot_rect()
        start = QPointF(plot_rect.center().x(), plot_rect.center().y())
        end = QPointF(
            plot_rect.center().x(), plot_rect.center().y() - (plot_rect.height() * 0.2)
        )

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            end,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            end,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        initial_offset = canvas.state.channel_a.vertical_offset_divs
        canvas.mousePressEvent(press)
        canvas.mouseMoveEvent(move)
        canvas.mouseReleaseEvent(release)

        self.assertGreater(canvas.state.channel_a.vertical_offset_divs, initial_offset)

    def test_zoom_panel_only_keeps_reset_button(self) -> None:
        canvas = WaveformCanvas()

        self.assertTrue(hasattr(canvas, "zoom_reset_button"))
        self.assertFalse(hasattr(canvas, "zoom_horizontal_in_button"))
        self.assertFalse(hasattr(canvas, "zoom_horizontal_out_button"))
        self.assertFalse(hasattr(canvas, "zoom_vertical_in_button"))
        self.assertFalse(hasattr(canvas, "zoom_vertical_out_button"))


if __name__ == "__main__":
    unittest.main()
