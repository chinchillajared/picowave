from __future__ import annotations

import numpy as np

from picowave.config import (
    ACQUISITION_MODES,
    BLOCK_BUFFER_SAMPLES_2204A,
    FAST_STREAMING_MAX_SAMPLES,
    SAMPLE_RATE_OPTIONS,
    SIGNAL_SMOOTHER_DEFAULT_SPAN,
    SMOOTHING_PREVIEW_POINT_LIMITS,
)
from picowave.helpers import clamp
from picowave.models import ScopeState
def requested_sample_count(time_per_div: float, sample_rate_hz: float) -> int:
    return max(1, int(round(time_per_div * 10.0 * sample_rate_hz)))


def planning_active_channel_count(settings: ScopeState) -> int:
    enabled_count = int(settings.channel_a.enabled) + int(settings.channel_b.enabled)
    return enabled_count or 1


def block_max_sample_count(active_channel_count: int) -> int:
    return max(200, BLOCK_BUFFER_SAMPLES_2204A // max(active_channel_count, 1))


def is_sample_rate_available_for_mode(
    mode: str,
    time_per_div: float,
    sample_rate_hz: float,
    active_channel_count: int,
) -> bool:
    total_samples = requested_sample_count(time_per_div, sample_rate_hz)
    if mode == "Block":
        return 200 <= total_samples <= block_max_sample_count(active_channel_count)
    if mode == "Compatible streaming":
        if sample_rate_hz > 1_000:
            return False
        interval_ms = 1000.0 / sample_rate_hz
        return abs(interval_ms - round(interval_ms)) < 1e-9 and total_samples <= 60_000
    if mode == "Fast streaming":
        return sample_rate_hz <= 1_000_000 and total_samples <= FAST_STREAMING_MAX_SAMPLES
    return False


def classify_sample_rates(
    time_per_div: float,
    current_mode: str,
    active_channel_count: int,
) -> tuple[list[float], dict[str, list[float]], list[float]]:
    # Central timing classifier used by both the manual selector and the compact
    # +/- timing control. It answers three UX questions for a given timebase:
    # available right now, available only in another mode, or not supported.
    available: list[float] = []
    compatible_elsewhere = {
        mode: [] for mode in ACQUISITION_MODES if mode != current_mode
    }
    unavailable: list[float] = []
    for sample_rate in SAMPLE_RATE_OPTIONS:
        supported_modes = [
            mode
            for mode in ACQUISITION_MODES
            if is_sample_rate_available_for_mode(mode, time_per_div, sample_rate, active_channel_count)
        ]
        if current_mode in supported_modes:
            available.append(sample_rate)
        elif supported_modes:
            for mode in supported_modes:
                compatible_elsewhere.setdefault(mode, []).append(sample_rate)
        else:
            unavailable.append(sample_rate)
    compatible_elsewhere = {mode: values for mode, values in compatible_elsewhere.items() if values}
    return available, compatible_elsewhere, unavailable


def smooth_signal(values: np.ndarray, span: int = SIGNAL_SMOOTHER_DEFAULT_SPAN) -> np.ndarray:
    # MATLAB's "smooth" moving-average behavior keeps the center point aligned
    # with an odd span and shrinks the usable neighborhood at the edges. That
    # gives a predictable low-pass smoother without pulling endpoints outward.
    if values.size <= 2:
        return values.astype(np.float32, copy=True)

    span = max(1, int(span))
    if span % 2 == 0:
        span += 1
    half_window = span // 2
    indexes = np.arange(values.size, dtype=np.int32)
    local_half = np.minimum(np.minimum(indexes, values.size - indexes - 1), half_window)
    starts = indexes - local_half
    ends = indexes + local_half + 1
    prefix = np.concatenate(([0.0], np.cumsum(values.astype(np.float64, copy=False), dtype=np.float64)))
    sums = prefix[ends] - prefix[starts]
    counts = (ends - starts).astype(np.float64)
    return (sums / counts).astype(np.float32)


def savitzky_golay_smooth(values: np.ndarray, span: int = SIGNAL_SMOOTHER_DEFAULT_SPAN, degree: int = 3) -> np.ndarray:
    if values.size <= 2:
        return values.astype(np.float32, copy=True)
    span = max(3, int(span))
    if span % 2 == 0:
        span += 1
    degree = max(1, min(int(degree), span - 2))
    half_window = span // 2
    smoothed = np.empty_like(values, dtype=np.float32)
    for index in range(values.size):
        local_half = min(half_window, index, values.size - index - 1)
        start = index - local_half
        end = index + local_half + 1
        window = values[start:end].astype(np.float64, copy=False)
        if window.size <= degree:
            smoothed[index] = float(np.mean(window, dtype=np.float64))
            continue
        x = np.arange(start, end, dtype=np.float64) - float(index)
        coefficients = np.polyfit(x, window, degree)
        smoothed[index] = float(np.polyval(coefficients, 0.0))
    return smoothed


def _lowess_core(values: np.ndarray, span: int, *, robust: bool) -> np.ndarray:
    if values.size <= 2:
        return values.astype(np.float32, copy=True)
    span = max(3, int(span))
    x = np.arange(values.size, dtype=np.float64)
    y = values.astype(np.float64, copy=False)
    fitted = np.empty(values.size, dtype=np.float64)
    robust_weights = np.ones(values.size, dtype=np.float64)
    iterations = 4 if robust else 1

    for _ in range(iterations):
        for index in range(values.size):
            left = max(0, index - span // 2)
            right = min(values.size, left + span)
            left = max(0, right - span)

            x_window = x[left:right]
            y_window = y[left:right]
            distances = np.abs(x_window - x[index])
            max_distance = np.max(distances) if distances.size else 0.0
            if max_distance <= 0.0:
                fitted[index] = y[index]
                continue

            normalized = distances / max_distance
            weights = (1.0 - normalized**3) ** 3
            weights *= robust_weights[left:right]
            if not np.any(weights > 0):
                fitted[index] = y[index]
                continue

            design = np.column_stack((np.ones_like(x_window), x_window - x[index]))
            weighted_design = design * weights[:, None]
            beta, *_ = np.linalg.lstsq(weighted_design, y_window * weights, rcond=None)
            fitted[index] = float(beta[0])

        if not robust:
            break

        residuals = y - fitted
        mad = np.median(np.abs(residuals))
        if mad <= 1e-12:
            break
        scaled = residuals / (6.0 * mad)
        robust_weights = np.where(np.abs(scaled) < 1.0, (1.0 - scaled**2) ** 2, 0.0)

    return fitted.astype(np.float32)


def lowess_smooth(values: np.ndarray, span: int = SIGNAL_SMOOTHER_DEFAULT_SPAN) -> np.ndarray:
    return _lowess_core(values, span, robust=False)


def robust_lowess_smooth(values: np.ndarray, span: int = SIGNAL_SMOOTHER_DEFAULT_SPAN) -> np.ndarray:
    return _lowess_core(values, span, robust=True)


def _resample_for_smoothing(values: np.ndarray, max_points: int | None) -> tuple[np.ndarray, np.ndarray | None]:
    if max_points is None or values.size <= max_points:
        return values.astype(np.float32, copy=False), None
    full_x = np.arange(values.size, dtype=np.float64)
    sample_x = np.linspace(0.0, float(values.size - 1), num=max_points, dtype=np.float64)
    sampled = np.interp(sample_x, full_x, values.astype(np.float64, copy=False)).astype(np.float32)
    return sampled, sample_x


def apply_smoothing_method(values: np.ndarray, method: str, span: int) -> np.ndarray:
    working_values, sample_x = _resample_for_smoothing(values, SMOOTHING_PREVIEW_POINT_LIMITS.get(method))
    if method == "savitzky_golay":
        smoothed = savitzky_golay_smooth(working_values, span=span)
    elif method == "lowess":
        smoothed = lowess_smooth(working_values, span=span)
    elif method == "robust_lowess":
        smoothed = robust_lowess_smooth(working_values, span=span)
    else:
        smoothed = smooth_signal(working_values, span=span)

    if sample_x is None:
        return smoothed

    full_x = np.arange(values.size, dtype=np.float64)
    return np.interp(full_x, sample_x, smoothed.astype(np.float64, copy=False)).astype(np.float32)


def sample_count_for_span(total_span_s: float, interval_s: float, *, minimum: int = 1) -> int:
    if total_span_s <= 0.0 or interval_s <= 0.0:
        return int(max(1, minimum))
    return max(int(minimum), int(round(total_span_s / interval_s)) + 1)


