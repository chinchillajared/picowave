from __future__ import annotations

import os

TIME_PER_DIV_OPTIONS = [
    200e-9,
    500e-9,
    1e-6,
    2e-6,
    5e-6,
    10e-6,
    20e-6,
    50e-6,
    100e-6,
    200e-6,
    500e-6,
    1e-3,
    2e-3,
    5e-3,
    10e-3,
    20e-3,
    50e-3,
    100e-3,
    200e-3,
    500e-3,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
    1000.0,
    2000.0,
    5000.0,
]
TIMEBASE_UNIT_GROUPS = {
    "ns /div": [value for value in TIME_PER_DIV_OPTIONS if value < 1e-6],
    "us /div": [value for value in TIME_PER_DIV_OPTIONS if 1e-6 <= value < 1e-3],
    "ms /div": [value for value in TIME_PER_DIV_OPTIONS if 1e-3 <= value < 1.0],
    "s /div": [value for value in TIME_PER_DIV_OPTIONS if value >= 1.0],
}
SAMPLE_RATE_OPTIONS = [
    1_000,
    2_000,
    5_000,
    10_000,
    20_000,
    50_000,
    100_000,
    200_000,
    500_000,
    1_000_000,
]
BLOCK_BUFFER_SAMPLES_2204A = 8_000
FAST_STREAMING_MAX_SAMPLES = 8_000_000
ACQUISITION_MODES = [
    "Block",
    "Fast streaming",
]
RANGE_OPTIONS_2204A = [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
CHANNEL_PANEL_TABS = ["Vertical", "Probes"]
PROBE_SCALE_OPTIONS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
CUSTOM_CHANNEL_SOURCE_OPTIONS = ["A", "B"]
CUSTOM_CHANNEL_VISIBILITY_OPTIONS = ["Hide", "Show"]
CUSTOM_CHANNEL_MATH_OPTIONS = ["Signal smoother"]
SIGNAL_SMOOTHER_DEFAULT_SPAN = 11
SMOOTHING_PREVIEW_POINT_LIMITS = {
    "moving_average": None,
    "savitzky_golay": 8_000,
    "lowess": 2_500,
    "robust_lowess": 1_500,
}
SIGNAL_SMOOTHER_HELP_TEXT = (
    "Signal smoother applies a centered moving-average filter to the selected "
    "source waveform. It reduces fast noise by averaging neighboring samples, "
    "while keeping the same time axis and source voltage scale."
)
SMOOTHING_METHOD_OPTIONS = [
    ("Gentle average", "moving_average"),
    ("Shape-preserving", "savitzky_golay"),
    ("Adaptive trend", "lowess"),
    ("Outlier-resistant", "robust_lowess"),
]
SMOOTHING_METHOD_LABELS = {code: label for label, code in SMOOTHING_METHOD_OPTIONS}
SMOOTHING_METHOD_DESCRIPTIONS = {
    "moving_average": "Simple averaging of nearby samples. Best for straightforward noise reduction.",
    "savitzky_golay": "Keeps peaks and waveform shape better while still smoothing noise.",
    "lowess": "Fits local trends so the smoothing adapts to changing waveform sections.",
    "robust_lowess": "Like adaptive smoothing, but reduces the influence of spikes and outliers.",
}
SMOOTHING_STRENGTH_OPTIONS = [
    ("Light", 5),
    ("Balanced", 11),
    ("Strong", 21),
    ("Extra smooth", 41),
]
SMOOTHING_STRENGTH_LABELS = {span: label for label, span in SMOOTHING_STRENGTH_OPTIONS}
TRIGGER_MODES = ["None", "Auto", "Repeat", "Single"]
TRIGGER_SOURCES = ["A", "B"]
TRIGGER_TYPES = ["Simple edge"]
TRIGGER_EDGE_DIRECTIONS = ["Rising", "Falling"]
ANNOTATION_SCOPES = ["All captures", "This capture"]
ANNOTATION_TOOLS = ["Off", "Pen", "Text", "Eraser"]
ANNOTATION_COLORS = [
    ("Blue", "#1e73be"),
    ("Red", "#ef3340"),
    ("Green", "#119822"),
    ("Amber", "#d97706"),
    ("Black", "#111827"),
]
CUSTOM_CHANNEL_COLORS = [
    ("Green", "#119822"),
    ("Amber", "#d97706"),
    ("Black", "#111827"),
    ("Teal", "#0f766e"),
    ("Purple", "#7c3aed"),
]
DEFAULT_MAX_WAVEFORMS = 60
WAVEFORM_PREVIEW_PAGE_SIZE = 10
# Fast streaming trigger alignment is less reliable at short timebases
# This threshold (in seconds per division) determines when to fall back to Block mode
FAST_STREAMING_TRIGGER_FALLBACK_THRESHOLD_S = 5e-6
PACKAGE_ROOT = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(PACKAGE_ROOT)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "picowave.log")
ICON_DIR = os.path.join(PACKAGE_ROOT, "icons")
ICON_FILES = {
    "run": "run.svg",
    "stop": "stop.svg",
    "connect": "connect.svg",
    "about": "about.svg",
    "annotate": "annotate.svg",
    "zoom": "zoom.svg",
    "Off": "annotation_off.svg",
    "Pen": "annotate.svg",
    "Text": "annotation_text.svg",
    "Eraser": "annotation_eraser.svg",
    "mode": "mode.svg",
    "trigger": "trigger.svg",
    "timing": "timing.svg",
    "Simple edge": "trigger_simple_edge.svg",
    "Advanced edge": "trigger_advanced_edge.svg",
    "Window": "trigger_window.svg",
    "Pulse width": "trigger_pulse_width.svg",
    "Logic": "trigger_logic.svg",
}
PS2000_TRIGGER_STATE = {
    "Don't care": 0,
    "True": 1,
    "False": 2,
}
PS2000_THRESHOLD_DIRECTION = {
    "Above": 0,
    "Below": 1,
    "Inside": 0,
    "Outside": 1,
    "Rising": 2,
    "Falling": 3,
    "Rising or Falling": 4,
    "Enter": 2,
    "Exit": 3,
    "Enter or Exit": 4,
    "None": 2,
}
PS2000_THRESHOLD_MODE = {
    "Level": 0,
    "Window": 1,
}
PS2000_PULSE_WIDTH_TYPE = {
    "Less than": 1,
    "Greater than": 2,
    "In range": 3,
    "Out of range": 4,
}
