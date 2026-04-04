from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from picowave.config import (
    DEFAULT_MAX_WAVEFORMS,
    RANGE_OPTIONS_2204A,
    SIGNAL_SMOOTHER_DEFAULT_SPAN,
)
from picowave.helpers import clamp, format_probe_scale


@dataclass
class ChannelState:
    name: str
    enabled: bool
    coupling: str
    range_volts: float
    color_hex: str
    invert: bool = False
    vertical_offset_divs: float = 0.0
    display_zoom: float = 1.0
    panel_tab: str = "Vertical"
    probe_scale: int = 1
    probe: str = "x1"


@dataclass
class CustomChannelState:
    name: str = "Custom"
    enabled: bool = False
    source_channel: str = "A"
    show_source_channel: bool = True
    color_hex: str = "#d97706"
    operation: str = "Signal smoother"
    smoothing_method: str = "moving_average"
    smoothing_span: int = SIGNAL_SMOOTHER_DEFAULT_SPAN
    vertical_offset_divs: float = 0.0


@dataclass
class TriggerState:
    mode: str = "None"
    trigger_type: str = "Simple edge"
    source: str = "A"
    direction: str = "Rising"
    level_volts: float = 0.0
    lower_level_volts: float = -0.2
    upper_level_volts: float = 0.2
    pulse_width_type: str = "Greater than"
    pulse_width_lower: int = 20
    pulse_width_upper: int = 100
    logic_a_state: str = "Don't care"
    logic_b_state: str = "Don't care"
    pre_trigger_percent: int = 50


@dataclass
class ScopeState:
    acquisition_mode: str = "Block"
    time_per_div: float = 5e-3
    sample_rate_hz: float = 100_000
    max_waveforms: int = DEFAULT_MAX_WAVEFORMS
    running: bool = False
    channel_a: ChannelState = field(
        default_factory=lambda: ChannelState("A", False, "DC", 1.0, "#1e73be")
    )
    channel_b: ChannelState = field(
        default_factory=lambda: ChannelState("B", False, "DC", 1.0, "#ef3340")
    )
    custom_channel: CustomChannelState = field(default_factory=CustomChannelState)
    trigger: TriggerState = field(default_factory=TriggerState)


def channel_scale_factor(channel: ChannelState) -> float:
    return float(max(1, channel.probe_scale))


def channel_display_range(channel: ChannelState) -> float:
    return float(channel.range_volts)


def channel_visible_range(channel: ChannelState) -> float:
    zoom = clamp(channel.display_zoom, 0.25, 20.0)
    return float(max(channel.range_volts / zoom, 1e-6))


def channel_hardware_range(channel: ChannelState) -> float:
    return float(channel.range_volts / channel_scale_factor(channel))


def channel_voltage_options(channel: ChannelState) -> list[float]:
    scale = channel_scale_factor(channel)
    return [float(option * scale) for option in RANGE_OPTIONS_2204A]


def channel_probe_label(channel: ChannelState) -> str:
    return format_probe_scale(channel.probe_scale)


def display_to_hardware_volts(channel: ChannelState, display_volts: float) -> float:
    scaled = display_volts / channel_scale_factor(channel)
    return -scaled if channel.invert else scaled


@dataclass
class CaptureFrame:
    times: np.ndarray
    channel_a: np.ndarray
    channel_b: np.ndarray
    sample_rate_hz: float
    sample_count: int
    y_range_volts: float
    source_label: str
    connection_label: str
    trigger_label: str
    trigger_enabled: bool = False
    trigger_source: str = "A"
    trigger_level_volts: float = 0.0
    trigger_time_ratio: float = 0.5
    trigger_confirmed: bool = False
    channel_a_overrange: np.ndarray = field(
        default_factory=lambda: np.array([], dtype=np.int8)
    )
    channel_b_overrange: np.ndarray = field(
        default_factory=lambda: np.array([], dtype=np.int8)
    )

    @property
    def total_span(self) -> float:
        if self.times.size <= 1:
            return 0.0
        return float(self.times[-1] - self.times[0])


@dataclass
class AnnotationStroke:
    points: list[tuple[float, float]]
    color_hex: str
    width: float = 2.0


@dataclass
class AnnotationText:
    position: tuple[float, float]
    text: str
    color_hex: str


@dataclass
class AnnotationSettings:
    scope: str = "All captures"
    tool: str = "Off"
    color_hex: str = "#1e73be"


def build_empty_frame(
    settings: ScopeState, source_label: str, connection_label: str
) -> CaptureFrame:
    return CaptureFrame(
        times=np.array([], dtype=np.float32),
        channel_a=np.array([], dtype=np.float32),
        channel_b=np.array([], dtype=np.float32),
        sample_rate_hz=0.0,
        sample_count=0,
        y_range_volts=channel_display_range(settings.channel_a),
        source_label=source_label,
        connection_label=connection_label,
        trigger_label=settings.trigger.mode
        if settings.trigger.mode != "None"
        else "None",
    )


@dataclass
class MeasurementLozenge:
    """Lozenge display data for waveform overlay."""

    name: str
    value: float
    unit: str
    color_hex: str
    position_x: float  # 0-1 normalized position on waveform
    position_y: float  # 0-1 normalized position on waveform
    threshold_level: str  # "green", "yellow", "red"

    def to_display_text(self) -> str:
        """Format for display."""
        if abs(self.value) >= 1000:
            return f"{self.name}: {self.value:.3g}k {self.unit}"
        elif abs(self.value) < 0.01 and self.value != 0:
            return f"{self.name}: {self.value * 1000:.3g}m {self.unit}"
        else:
            return f"{self.name}: {self.value:.3g} {self.unit}"


@dataclass
class DiagnosticReport:
    """Diagnostic report for export."""

    timestamp: float
    template_name: str
    measurements: list[dict]
    channel_settings: dict
    threshold_alerts: list[dict]
    notes: str = ""

    def to_json(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "timestamp": self.timestamp,
            "template": self.template_name,
            "measurements": self.measurements,
            "channel_settings": self.channel_settings,
            "alerts": self.threshold_alerts,
            "notes": self.notes,
        }
