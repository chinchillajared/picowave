from __future__ import annotations

import os
import time
from ctypes import CFUNCTYPE, POINTER, byref, c_double, c_int16, c_int32, c_uint32, c_void_p, cast

import numpy as np

from picowave.config import (
    FAST_STREAMING_MAX_SAMPLES,
    PS2000_PULSE_WIDTH_TYPE,
    PS2000_THRESHOLD_DIRECTION,
    PS2000_THRESHOLD_MODE,
    PS2000_TRIGGER_STATE,
)
from picowave.helpers import decode_text, display_trigger_level, format_trigger_summary
from picowave.logging_config import CONTROLLER_LOGGER
from picowave.models import (
    CaptureFrame,
    ScopeState,
    channel_display_range,
    channel_hardware_range,
    channel_scale_factor,
    display_to_hardware_volts,
)
from picowave.picosdk_types import (
    PS2000_PWQ_CONDITIONS,
    PS2000_TRIGGER_CHANNEL_PROPERTIES,
    PS2000_TRIGGER_CONDITIONS,
)
from picowave.processing import (
    block_max_sample_count,
    clamp,
    planning_active_channel_count,
    sample_count_for_span,
)
class Pico2204AController:
    def __init__(self) -> None:
        self._dll_handles: list[object] = []
        self._ps = None
        self._device = None
        self._channel_config = None
        self._errors = {
            "cannot_find": RuntimeError,
            "device_not_found": RuntimeError,
            "ctypes": RuntimeError,
        }
        self._initialized = False
        self._last_connect_attempt = 0.0
        self._last_status = "Connect a PicoScope 2204A to begin capture."
        self._last_source = "Hardware"
        self._connected_serial = ""

    @property
    def is_connected(self) -> bool:
        return self._device is not None

    @property
    def status_text(self) -> str:
        return self._last_status

    # Backend: PicoSDK runtime and device discovery

    def _candidate_sdk_dirs(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add_candidate(path: str | None) -> None:
            if not path:
                return
            normalized = os.path.normcase(os.path.normpath(path))
            if normalized in seen or not os.path.isdir(path):
                return
            seen.add(normalized)
            candidates.append(path)

        for value in os.environ.get("PATH", "").split(os.pathsep):
            if value and os.path.isfile(os.path.join(value, "ps2000.dll")):
                add_candidate(value)
        return candidates

    def _prepare_runtime(self) -> None:
        if self._initialized:
            return

        CONTROLLER_LOGGER.info("Preparing PicoSDK runtime.")
        try:
            from picosdk.device import ChannelConfig
            from picosdk.errors import CannotFindPicoSDKError, DeviceNotFoundError, PicoSDKCtypesError
        except ModuleNotFoundError as exc:
            self._last_status = "Python package 'picosdk' is not installed."
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("Python package 'picosdk' is not installed.")
            raise RuntimeError(self._last_status) from exc

        self._channel_config = ChannelConfig
        self._errors = {
            "cannot_find": CannotFindPicoSDKError,
            "device_not_found": DeviceNotFoundError,
            "ctypes": PicoSDKCtypesError,
        }

        # Keep SDK discovery Python-first and only extend runtime lookup with
        # PATH entries that already expose ps2000.dll.
        for directory in self._candidate_sdk_dirs():
            if directory not in os.environ.get("PATH", ""):
                os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    self._dll_handles.append(os.add_dll_directory(directory))
                except OSError:
                    pass

        from picosdk.ps2000 import ps2000

        self._ps = ps2000
        self._initialized = True
        CONTROLLER_LOGGER.info("PicoSDK runtime ready.")

    def _connect_if_needed(self, force: bool = False, serial: str | None = None) -> None:
        requested_serial = (serial or "").strip()
        if self._device is not None and (not requested_serial or requested_serial == self._connected_serial):
            return
        if self._device is not None and requested_serial and requested_serial != self._connected_serial:
            self._disconnect()
        if not force and time.time() - self._last_connect_attempt < 5.0:
            return

        self._last_connect_attempt = time.time()
        CONTROLLER_LOGGER.info("Connecting to PicoScope 2204A. serial=%s force=%s", requested_serial or "<any>", force)
        try:
            self._prepare_runtime()
            self._device = self._ps.open_unit(serial=requested_serial.encode("utf-8")) if requested_serial else self._ps.open_unit()
            info = self._device.info
            variant = decode_text(info.variant)
            serial = decode_text(info.serial)
            variant = variant or "PicoScope 2204A"
            self._last_status = f"{variant} connected"
            if serial:
                self._last_status += f" [{serial}]"
            self._connected_serial = serial
            self._last_source = "Live"
            CONTROLLER_LOGGER.info("Connected to %s", self._last_status)
        except self._errors["device_not_found"]:
            self._device = None
            self._connected_serial = ""
            self._last_status = "No PicoScope detected."
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.warning("No PicoScope detected during connect.")
        except self._errors["cannot_find"]:
            self._device = None
            self._connected_serial = ""
            self._last_status = "PicoSDK DLL not found."
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("PicoSDK DLL not found during connect.")
        except Exception as exc:
            self._device = None
            self._connected_serial = ""
            self._last_status = f"Hardware unavailable: {exc}"
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("Unexpected hardware error during connect.")

    def _disconnect(self) -> None:
        if self._device is None:
            return
        CONTROLLER_LOGGER.info("Disconnecting PicoScope device.")
        try:
            self._device.close()
        except Exception:
            CONTROLLER_LOGGER.exception("Error while closing PicoScope device.")
            pass
        self._device = None
        self._connected_serial = ""

    def list_available_devices(self) -> list[dict[str, str]]:
        try:
            self._prepare_runtime()
            units = self._ps.list_units()
        except self._errors["cannot_find"]:
            self._last_status = "PicoSDK DLL not found."
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("Could not list devices because PicoSDK DLL was not found.")
            return []
        except Exception as exc:
            self._last_status = f"Hardware unavailable: {exc}"
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("Unexpected error while listing PicoScope devices.")
            return []

        devices: list[dict[str, str]] = []
        for unit in units:
            variant = decode_text(getattr(unit, "variant", "")) or "PicoScope 2204A"
            serial = decode_text(getattr(unit, "serial", "")) or "Unknown serial"
            devices.append(
                {
                    "label": f"{variant} [{serial}]",
                    "serial": serial if serial != "Unknown serial" else "",
                    "variant": variant,
                }
            )
        CONTROLLER_LOGGER.info("Discovered %d PicoScope device(s).", len(devices))
        return devices

    def connect_device(self, serial: str | None = None) -> bool:
        self._connect_if_needed(force=True, serial=serial)
        return self._device is not None

    def get_device_metadata(self) -> dict[str, str]:
        self._connect_if_needed()
        if self._device is None:
            return {
                "Model / variant info": "Not connected",
                "Serial or batch-and-serial": "Not connected",
                "Driver version": "Not connected",
                "USB version": "Not connected",
                "Hardware version": "Not connected",
                "Calibration date": "Not connected",
                "Kernel driver version": "Not connected",
                "Error code / driver-reported status info": self._last_status,
            }

        info_keys = [
            ("Model / variant info", "PICO_VARIANT_INFO"),
            ("Serial or batch-and-serial", "PICO_BATCH_AND_SERIAL"),
            ("Driver version", "PICO_DRIVER_VERSION"),
            ("USB version", "PICO_USB_VERSION"),
            ("Hardware version", "PICO_HARDWARE_VERSION"),
            ("Calibration date", "PICO_CAL_DATE"),
            ("Kernel driver version", "PICO_KERNEL_DRIVER_VERSION"),
            ("Error code / driver-reported status info", "PICO_ERROR_CODE"),
        ]
        values = self._ps.get_unit_info(self._device, *[key for _label, key in info_keys])
        metadata = {}
        for label, key in info_keys:
            metadata[label] = decode_text(getattr(values, key, "")) or "Unavailable"
        return metadata

    # Backend: timing and channel planning

    def _target_sample_count(self, settings: ScopeState) -> int:
        # Block mode must respect the small on-device capture buffer of the 2204A.
        # Streaming modes can go much longer, but block captures need to clamp the
        # requested depth to something the driver can realistically satisfy.
        requested = int(round(settings.time_per_div * 10.0 * settings.sample_rate_hz))
        active_channels = planning_active_channel_count(settings)
        max_samples = block_max_sample_count(active_channels)
        return int(clamp(requested, 200, max_samples))

    def _requested_sample_count(self, settings: ScopeState) -> int:
        return max(1, int(round(settings.time_per_div * 10.0 * settings.sample_rate_hz)))

    def _choose_timebase(self, sample_count: int, target_span: float):
        target_interval = target_span / max(sample_count - 1, 1)
        previous = None
        current = None
        for timebase_id in range(0, 4000):
            try:
                current = self._ps.get_timebase(self._device, timebase_id, sample_count)
            except Exception:
                if previous is not None:
                    break
                continue
            if current.time_interval >= target_interval:
                if previous is None:
                    return current
                prev_error = abs(previous.time_interval - target_interval)
                curr_error = abs(current.time_interval - target_interval)
                return previous if prev_error <= curr_error else current
            previous = current
        if previous is not None:
            return previous
        return current

    def _choose_block_capture_plan(self, sample_count: int, target_span: float):
        candidate_count = max(200, int(sample_count))
        while candidate_count >= 200:
            timebase = self._choose_timebase(candidate_count, target_span)
            if timebase is not None:
                return candidate_count, timebase
            if candidate_count == 200:
                break
            next_count = max(200, candidate_count - max(200, candidate_count // 5))
            if next_count == candidate_count:
                next_count -= 1
            candidate_count = next_count
        return sample_count, None

    def _apply_channels(self, settings: ScopeState) -> dict[str, float]:
        channel_a = self._channel_config(
            "A",
            settings.channel_a.enabled,
            settings.channel_a.coupling,
            channel_hardware_range(settings.channel_a),
        )
        channel_b = self._channel_config(
            "B",
            settings.channel_b.enabled,
            settings.channel_b.coupling,
            channel_hardware_range(settings.channel_b),
        )
        self._device.set_channels(channel_a, channel_b)
        return {
            "A": float(self._device._channel_ranges.get("A", channel_hardware_range(settings.channel_a))),
            "B": float(self._device._channel_ranges.get("B", channel_hardware_range(settings.channel_b))),
        }

    # Backend: trigger configuration

    def _trigger_auto_ms(self, mode: str) -> int:
        return 100 if mode in ("Auto", "Repeat") else 0

    def _threshold_counts(self, volts: float, channel_range: float) -> int:
        max_adc = self._ps.maximum_value(self._device)
        safe_range = max(channel_range, 1e-6)
        return int(clamp(volts / safe_range, -1.0, 1.0) * max_adc)

    @staticmethod
    def _trigger_direction_for_channel(direction: str, channel: ChannelState) -> str:
        if not channel.invert:
            return direction
        if direction == "Rising":
            return "Falling"
        if direction == "Falling":
            return "Rising"
        return direction

    def _clear_pulse_width_qualifier(self) -> None:
        if hasattr(self._ps, "_SetPulseWidthQualifier"):
            self._ps._SetPulseWidthQualifier(
                c_int16(self._device.handle),
                None,
                c_int16(0),
                c_int32(PS2000_THRESHOLD_DIRECTION["None"]),
                c_uint32(0),
                c_uint32(0),
                c_int32(0),
            )

    def _apply_trigger_delay(self, pre_trigger_percent: int) -> None:
        if hasattr(self._ps, "_SetAdvTriggerDelay"):
            self._ps._SetAdvTriggerDelay(
                c_int16(self._device.handle),
                c_uint32(0),
                -float(pre_trigger_percent),
            )

    def _source_condition(self, source: str, state: int, include_pwq: int = 0) -> PS2000_TRIGGER_CONDITIONS:
        return PS2000_TRIGGER_CONDITIONS(
            channelA=state if source == "A" else PS2000_TRIGGER_STATE["Don't care"],
            channelB=state if source == "B" else PS2000_TRIGGER_STATE["Don't care"],
            channelC=PS2000_TRIGGER_STATE["Don't care"],
            channelD=PS2000_TRIGGER_STATE["Don't care"],
            external=PS2000_TRIGGER_STATE["Don't care"],
            pulseWidthQualifier=include_pwq,
        )

    def _source_pwq_condition(self, source: str, state: int) -> PS2000_PWQ_CONDITIONS:
        return PS2000_PWQ_CONDITIONS(
            channelA=state if source == "A" else PS2000_TRIGGER_STATE["Don't care"],
            channelB=state if source == "B" else PS2000_TRIGGER_STATE["Don't care"],
            channelC=PS2000_TRIGGER_STATE["Don't care"],
            channelD=PS2000_TRIGGER_STATE["Don't care"],
            external=PS2000_TRIGGER_STATE["Don't care"],
        )

    def _apply_advanced_trigger_core(
        self,
        properties: list[PS2000_TRIGGER_CHANNEL_PROPERTIES],
        conditions: list[PS2000_TRIGGER_CONDITIONS],
        directions: dict[str, int],
        auto_trigger_ms: int,
        pre_trigger_percent: int,
    ) -> None:
        if not all(
            hasattr(self._ps, attr)
            for attr in (
                "_SetAdvTriggerChannelProperties",
                "_SetAdvTriggerChannelConditions",
                "_SetAdvTriggerChannelDirections",
            )
        ):
            raise RuntimeError("Advanced trigger APIs are not available in this PicoSDK installation.")

        handle = c_int16(self._device.handle)
        self._clear_pulse_width_qualifier()
        properties_buffer = (
            (PS2000_TRIGGER_CHANNEL_PROPERTIES * len(properties))(*properties) if properties else None
        )
        conditions_buffer = (PS2000_TRIGGER_CONDITIONS * len(conditions))(*conditions) if conditions else None
        properties_result = self._ps._SetAdvTriggerChannelProperties(
            handle,
            properties_buffer,
            c_int16(len(properties)),
            c_int32(auto_trigger_ms),
        )
        conditions_result = self._ps._SetAdvTriggerChannelConditions(
            handle,
            conditions_buffer,
            c_int16(len(conditions)),
        )
        directions_result = self._ps._SetAdvTriggerChannelDirections(
            handle,
            c_int32(directions.get("A", PS2000_THRESHOLD_DIRECTION["None"])),
            c_int32(directions.get("B", PS2000_THRESHOLD_DIRECTION["None"])),
            c_int32(directions.get("C", PS2000_THRESHOLD_DIRECTION["None"])),
            c_int32(directions.get("D", PS2000_THRESHOLD_DIRECTION["None"])),
            c_int32(directions.get("Ext", PS2000_THRESHOLD_DIRECTION["None"])),
        )
        self._apply_trigger_delay(pre_trigger_percent)
        if not properties_result or not conditions_result or not directions_result:
            raise RuntimeError("Could not apply advanced trigger settings.")

    def _apply_simple_edge_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        source = settings.trigger.source
        source_channel = settings.channel_a if source == "A" else settings.channel_b
        if not source_channel.enabled:
            raise RuntimeError(f"Trigger source {source} must be enabled.")

        hardware_level = display_to_hardware_volts(source_channel, settings.trigger.level_volts)
        threshold_counts = self._threshold_counts(hardware_level, active_ranges[source])
        direction_name = self._trigger_direction_for_channel(settings.trigger.direction, source_channel)
        direction = 0 if direction_name == "Rising" else 1
        delay_percent = -float(settings.trigger.pre_trigger_percent)
        auto_trigger_ms = self._trigger_auto_ms(settings.trigger.mode)

        if hasattr(self._ps, "_set_trigger2"):
            result = self._ps._set_trigger2(
                c_int16(self._device.handle),
                c_int16(self._ps.PICO_CHANNEL[source]),
                c_int16(threshold_counts),
                c_int16(direction),
                delay_percent,
                c_int16(auto_trigger_ms),
            )
        else:
            result = self._ps._set_trigger(
                c_int16(self._device.handle),
                c_int16(self._ps.PICO_CHANNEL[source]),
                c_int16(threshold_counts),
                c_int16(direction),
                c_int16(int(delay_percent)),
                c_int16(auto_trigger_ms),
            )
        if not result:
            raise RuntimeError("Could not apply trigger settings.")

    def _apply_advanced_edge_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        source = settings.trigger.source
        source_channel = settings.channel_a if source == "A" else settings.channel_b
        if not source_channel.enabled:
            raise RuntimeError(f"Trigger source {source} must be enabled.")

        hardware_level = display_to_hardware_volts(source_channel, settings.trigger.level_volts)
        threshold = self._threshold_counts(hardware_level, active_ranges[source])
        properties = [
            PS2000_TRIGGER_CHANNEL_PROPERTIES(
                thresholdMajor=threshold,
                thresholdMinor=threshold,
                hysteresis=0,
                channel=self._ps.PICO_CHANNEL[source],
                thresholdMode=PS2000_THRESHOLD_MODE["Level"],
            )
        ]
        conditions = [self._source_condition(source, PS2000_TRIGGER_STATE["True"])]
        directions = {
            source: PS2000_THRESHOLD_DIRECTION[
                self._trigger_direction_for_channel(settings.trigger.direction, source_channel)
            ]
        }
        self._apply_advanced_trigger_core(
            properties,
            conditions,
            directions,
            self._trigger_auto_ms(settings.trigger.mode),
            settings.trigger.pre_trigger_percent,
        )

    def _apply_window_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        source = settings.trigger.source
        source_channel = settings.channel_a if source == "A" else settings.channel_b
        if not source_channel.enabled:
            raise RuntimeError(f"Trigger source {source} must be enabled.")

        lower_volts = min(settings.trigger.lower_level_volts, settings.trigger.upper_level_volts)
        upper_volts = max(settings.trigger.lower_level_volts, settings.trigger.upper_level_volts)
        hardware_lower = display_to_hardware_volts(source_channel, lower_volts)
        hardware_upper = display_to_hardware_volts(source_channel, upper_volts)
        actual_lower = min(hardware_lower, hardware_upper)
        actual_upper = max(hardware_lower, hardware_upper)
        properties = [
            PS2000_TRIGGER_CHANNEL_PROPERTIES(
                thresholdMajor=self._threshold_counts(actual_upper, active_ranges[source]),
                thresholdMinor=self._threshold_counts(actual_lower, active_ranges[source]),
                hysteresis=0,
                channel=self._ps.PICO_CHANNEL[source],
                thresholdMode=PS2000_THRESHOLD_MODE["Window"],
            )
        ]
        conditions = [self._source_condition(source, PS2000_TRIGGER_STATE["True"])]
        directions = {source: PS2000_THRESHOLD_DIRECTION[settings.trigger.direction]}
        self._apply_advanced_trigger_core(
            properties,
            conditions,
            directions,
            self._trigger_auto_ms(settings.trigger.mode),
            settings.trigger.pre_trigger_percent,
        )

    def _apply_logic_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        states = {
            "A": settings.trigger.logic_a_state,
            "B": settings.trigger.logic_b_state,
        }
        if all(state == "Don't care" for state in states.values()):
            raise RuntimeError("Logic trigger requires Channel A or B to be set to True or False.")

        effective_states = dict(states)
        for channel_name in ("A", "B"):
            channel_state = settings.channel_a if channel_name == "A" else settings.channel_b
            if not channel_state.invert:
                continue
            if effective_states[channel_name] == "True":
                effective_states[channel_name] = "False"
            elif effective_states[channel_name] == "False":
                effective_states[channel_name] = "True"

        properties: list[PS2000_TRIGGER_CHANNEL_PROPERTIES] = []
        conditions = PS2000_TRIGGER_CONDITIONS(
            channelA=PS2000_TRIGGER_STATE[effective_states["A"]],
            channelB=PS2000_TRIGGER_STATE[effective_states["B"]],
            channelC=PS2000_TRIGGER_STATE["Don't care"],
            channelD=PS2000_TRIGGER_STATE["Don't care"],
            external=PS2000_TRIGGER_STATE["Don't care"],
            pulseWidthQualifier=PS2000_TRIGGER_STATE["Don't care"],
        )
        directions: dict[str, int] = {}
        for channel_name, state_name in effective_states.items():
            if state_name == "Don't care":
                continue
            source_channel = settings.channel_a if channel_name == "A" else settings.channel_b
            if not source_channel.enabled:
                raise RuntimeError(f"Logic trigger channel {channel_name} must be enabled.")
            hardware_level = display_to_hardware_volts(source_channel, settings.trigger.level_volts)
            threshold = self._threshold_counts(hardware_level, active_ranges[channel_name])
            properties.append(
                PS2000_TRIGGER_CHANNEL_PROPERTIES(
                    thresholdMajor=threshold,
                    thresholdMinor=threshold,
                    hysteresis=0,
                    channel=self._ps.PICO_CHANNEL[channel_name],
                    thresholdMode=PS2000_THRESHOLD_MODE["Level"],
                )
            )
            directions[channel_name] = PS2000_THRESHOLD_DIRECTION["Above" if state_name == "True" else "Below"]
        self._apply_advanced_trigger_core(
            properties,
            [conditions],
            directions,
            self._trigger_auto_ms(settings.trigger.mode),
            settings.trigger.pre_trigger_percent,
        )

    def _apply_pulse_width_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        source = settings.trigger.source
        source_channel = settings.channel_a if source == "A" else settings.channel_b
        if not source_channel.enabled:
            raise RuntimeError(f"Trigger source {source} must be enabled.")

        hardware_level = display_to_hardware_volts(source_channel, settings.trigger.level_volts)
        threshold = self._threshold_counts(hardware_level, active_ranges[source])
        properties = [
            PS2000_TRIGGER_CHANNEL_PROPERTIES(
                thresholdMajor=threshold,
                thresholdMinor=threshold,
                hysteresis=0,
                channel=self._ps.PICO_CHANNEL[source],
                thresholdMode=PS2000_THRESHOLD_MODE["Level"],
            )
        ]
        conditions = [
            self._source_condition(
                source,
                PS2000_TRIGGER_STATE["True"],
                include_pwq=PS2000_TRIGGER_STATE["True"],
            )
        ]
        direction_name = self._trigger_direction_for_channel(settings.trigger.direction, source_channel)
        directions = {source: PS2000_THRESHOLD_DIRECTION[direction_name]}
        self._apply_advanced_trigger_core(
            properties,
            conditions,
            directions,
            self._trigger_auto_ms(settings.trigger.mode),
            settings.trigger.pre_trigger_percent,
        )
        pwq_conditions = (PS2000_PWQ_CONDITIONS * 1)(
            self._source_pwq_condition(source, PS2000_TRIGGER_STATE["True"])
        )
        lower = max(1, int(settings.trigger.pulse_width_lower))
        upper = max(lower, int(settings.trigger.pulse_width_upper))
        result = self._ps._SetPulseWidthQualifier(
            c_int16(self._device.handle),
            pwq_conditions,
            c_int16(1),
            c_int32(PS2000_THRESHOLD_DIRECTION[direction_name]),
            c_uint32(lower),
            c_uint32(upper),
            c_int32(PS2000_PULSE_WIDTH_TYPE[settings.trigger.pulse_width_type]),
        )
        if not result:
            raise RuntimeError("Could not apply pulse width trigger settings.")

    def _apply_trigger(self, settings: ScopeState, active_ranges: dict[str, float]) -> None:
        if settings.trigger.mode == "None":
            self._ps.set_null_trigger(self._device)
            return
        if settings.trigger.trigger_type == "Simple edge":
            self._apply_simple_edge_trigger(settings, active_ranges)
        elif settings.trigger.trigger_type == "Advanced edge":
            self._apply_advanced_edge_trigger(settings, active_ranges)
        elif settings.trigger.trigger_type == "Window":
            self._apply_window_trigger(settings, active_ranges)
        elif settings.trigger.trigger_type == "Logic":
            self._apply_logic_trigger(settings, active_ranges)
        elif settings.trigger.trigger_type == "Pulse width":
            self._apply_pulse_width_trigger(settings, active_ranges)
        else:
            raise RuntimeError(f"Unsupported trigger type: {settings.trigger.trigger_type}")

    # Backend: frame conversion shared by all acquisition modes

    def _build_frame(
        self,
        settings: ScopeState,
        times: np.ndarray,
        raw_data: dict[str, np.ndarray],
        active_ranges: dict[str, float],
        source_label: str,
        sample_rate_hz: float,
        *,
        trigger_sample_index: int | None = None,
        trigger_confirmed: bool = False,
    ) -> CaptureFrame:
        max_adc = self._ps.maximum_value(self._device)
        raw_a = raw_data.get("A")
        raw_b = raw_data.get("B")
        volts_a = np.array([], dtype=np.float32)
        volts_b = np.array([], dtype=np.float32)
        overrange_a = np.array([], dtype=np.int8)
        overrange_b = np.array([], dtype=np.int8)
        if raw_a is not None:
            raw_a_i32 = raw_a.astype(np.int32, copy=False)
            overrange_a = np.zeros(raw_a_i32.shape, dtype=np.int8)
            overrange_a[raw_a_i32 >= int(max_adc)] = 1
            overrange_a[raw_a_i32 <= -int(max_adc)] = -1
            volts_a = raw_a.astype(np.float32) * (active_ranges["A"] / max_adc)
            volts_a *= channel_scale_factor(settings.channel_a)
            if settings.channel_a.invert:
                volts_a *= -1.0
                overrange_a *= -1
        if raw_b is not None:
            raw_b_i32 = raw_b.astype(np.int32, copy=False)
            overrange_b = np.zeros(raw_b_i32.shape, dtype=np.int8)
            overrange_b[raw_b_i32 >= int(max_adc)] = 1
            overrange_b[raw_b_i32 <= -int(max_adc)] = -1
            volts_b = raw_b.astype(np.float32) * (active_ranges["B"] / max_adc)
            volts_b *= channel_scale_factor(settings.channel_b)
            if settings.channel_b.invert:
                volts_b *= -1.0
                overrange_b *= -1

        info = self._device.info
        variant = decode_text(info.variant) or "PicoScope 2204A"
        serial = decode_text(info.serial)
        status_text = variant if not serial else f"{variant} [{serial}]"

        trigger_label = (
            settings.trigger.mode
            if settings.trigger.mode == "None"
            else format_trigger_summary(settings.trigger)
        )
        trigger_enabled = (
            settings.trigger.mode != "None"
            and source_label != "Compatible streaming"
            and settings.trigger.source in active_ranges
        )
        trigger_ratio = clamp(settings.trigger.pre_trigger_percent / 100.0, 0.0, 1.0)
        if trigger_sample_index is not None and times.size > 1:
            trigger_ratio = clamp(trigger_sample_index / max(times.size - 1, 1), 0.0, 1.0)
        return CaptureFrame(
            times=times,
            channel_a=volts_a,
            channel_b=volts_b,
            sample_rate_hz=sample_rate_hz,
            sample_count=int(times.size),
            y_range_volts=max(
                channel_display_range(settings.channel_a) if settings.channel_a.enabled else 0.0,
                channel_display_range(settings.channel_b) if settings.channel_b.enabled else 0.0,
                channel_display_range(settings.channel_a),
            ),
            source_label=source_label,
            connection_label=status_text,
            trigger_label=trigger_label,
            trigger_enabled=trigger_enabled,
            trigger_source=settings.trigger.source,
            trigger_level_volts=display_trigger_level(settings.trigger),
            trigger_time_ratio=trigger_ratio,
            trigger_confirmed=trigger_enabled and (trigger_confirmed or source_label == "Block"),
            channel_a_overrange=overrange_a,
            channel_b_overrange=overrange_b,
        )

    # Backend: data acquisition modes

    def _capture_block_mode(self, settings: ScopeState) -> CaptureFrame:
        if not settings.channel_a.enabled and not settings.channel_b.enabled:
            raise RuntimeError("Enable Channel A or B before starting acquisition.")

        active_ranges = self._apply_channels(settings)
        self._apply_trigger(settings, active_ranges)

        requested_span = settings.time_per_div * 10.0
        max_block_samples = block_max_sample_count(planning_active_channel_count(settings))
        requested_sample_count = self._target_sample_count(settings)
        sample_count, timebase = self._choose_block_capture_plan(
            requested_sample_count,
            requested_span,
        )
        if timebase is None:
            raise RuntimeError("No valid timebase found for the selected view.")
        actual_sample_count = sample_count_for_span(requested_span, float(timebase.time_interval), minimum=200)
        if actual_sample_count > max_block_samples:
            timebase = self._choose_timebase(max_block_samples, requested_span)
            if timebase is None:
                raise RuntimeError("No valid timebase found for the selected view.")
            actual_sample_count = sample_count_for_span(requested_span, float(timebase.time_interval), minimum=200)
        actual_sample_count = min(actual_sample_count, max_block_samples)

        capture_time = self._ps.run_block(self._device, 0, actual_sample_count, timebase.timebase_id, 1, 0)
        deadline = time.time() + max(capture_time, 0.05) + 2.0
        while not self._ps.is_ready(self._device):
            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for PicoScope capture data.")
            time.sleep(0.004)

        active_channels = ["A"] + (["B"] if settings.channel_b.enabled else [])
        raw_data, _overflow = self._ps.get_values(self._device, active_channels, actual_sample_count, 0)
        self._ps.stop(self._device)
        times = np.arange(actual_sample_count, dtype=np.float32) * float(timebase.time_interval)
        return self._build_frame(
            settings,
            times,
            raw_data,
            active_ranges,
            "Block",
            (1.0 / timebase.time_interval) if timebase.time_interval else 0.0,
        )

    def _capture_compatible_streaming(self, settings: ScopeState) -> CaptureFrame:
        if not settings.channel_a.enabled and not settings.channel_b.enabled:
            raise RuntimeError("Enable Channel A or B before starting acquisition.")
        if settings.sample_rate_hz > 1_000:
            raise RuntimeError("Compatible streaming supports up to 1 kS/s on this controller.")

        active_ranges = self._apply_channels(settings)
        self._ps.set_null_trigger(self._device)

        interval_ms = max(1, int(round(1000.0 / settings.sample_rate_hz)))
        actual_rate_hz = 1000.0 / interval_ms
        sample_count = int(clamp(sample_count_for_span(settings.time_per_div * 10.0, 1.0 / actual_rate_hz, minimum=200), 200, 60_000))
        handle = c_int16(self._device.handle)

        status = self._ps._run_streaming(
            handle,
            c_int16(interval_ms),
            c_int32(sample_count),
            c_int16(0),
        )
        if not status:
            raise RuntimeError("Compatible streaming could not be started.")

        buffer_a = np.empty(sample_count, dtype=np.int16) if settings.channel_a.enabled else None
        buffer_b = np.empty(sample_count, dtype=np.int16) if settings.channel_b.enabled else None
        overflow = c_int16(0)
        deadline = time.time() + max(2.0, sample_count * interval_ms / 1000.0 + 1.0)
        captured = 0
        while captured == 0:
            captured = int(
                self._ps._get_values(
                    handle,
                    buffer_a.ctypes.data if buffer_a is not None else None,
                    buffer_b.ctypes.data if buffer_b is not None else None,
                    None,
                    None,
                    byref(overflow),
                    c_int32(sample_count),
                )
            )
            if captured > 0:
                break
            if time.time() > deadline:
                self._ps.stop(self._device)
                raise TimeoutError("Timed out waiting for compatible streaming data.")
            time.sleep(max(interval_ms / 1000.0, 0.02))

        self._ps.stop(self._device)
        raw_data = {}
        if buffer_a is not None:
            raw_data["A"] = buffer_a[:captured]
        if buffer_b is not None:
            raw_data["B"] = buffer_b[:captured]
        times = np.arange(captured, dtype=np.float32) / float(actual_rate_hz)
        return self._build_frame(
            settings,
            times,
            raw_data,
            active_ranges,
            "Compatible streaming",
            actual_rate_hz,
        )

    # Backend: fast streaming helpers

    def _interval_to_ps2000_units(self, interval_seconds: float) -> tuple[int, int, float]:
        units = (
            (1.0, 5, 1.0),
            (1e-3, 4, 1e-3),
            (1e-6, 3, 1e-6),
            (1e-9, 2, 1e-9),
        )
        for scale, unit_code, factor in units:
            if interval_seconds >= scale:
                value = max(1, int(round(interval_seconds / factor)))
                return value, unit_code, value * factor
        value = max(1, int(round(interval_seconds / 1e-9)))
        return value, 2, value * 1e-9

    def _fast_streaming_settings(self, sample_count: int) -> tuple[int, int]:
        # We always retrieve the final waveform with
        # ps2000_get_streaming_values_no_aggregation(), so keeping the driver
        # aggregation at 1 preserves the raw capture shape in the UI. Using
        # larger aggregation factors made short timebase captures look
        # distorted even though the underlying signal was stable.
        aggregate = 1
        overview_size = int(clamp(max(60_000, sample_count * 4), 60_000, FAST_STREAMING_MAX_SAMPLES))
        return aggregate, overview_size

    def _fast_streaming_capture_window(self, settings: ScopeState, actual_interval_s: float) -> tuple[int, int]:
        desired_samples = int(
            clamp(
                sample_count_for_span(settings.time_per_div * 10.0, actual_interval_s, minimum=200),
                200,
                FAST_STREAMING_MAX_SAMPLES,
            )
        )
        if settings.trigger.mode != "None":
            # When triggering in fast streaming, we intentionally over-capture
            # so the application can re-slice the raw waveform around the real
            # threshold crossing instead of depending only on the driver's
            # trigger index.
            guard_samples = min(max(500, desired_samples), max(0, FAST_STREAMING_MAX_SAMPLES - desired_samples))
        else:
            guard_samples = min(max(200, desired_samples // 4), max(0, FAST_STREAMING_MAX_SAMPLES - desired_samples))
        return desired_samples, desired_samples + guard_samples

    def _find_simple_edge_trigger_index(
        self,
        raw_samples: np.ndarray,
        threshold_counts: int,
        direction_name: str,
        *,
        hint_index: int | None = None,
    ) -> int | None:
        if raw_samples.size < 2:
            return None
        samples = raw_samples.astype(np.int32, copy=False)
        if direction_name == "Rising":
            crossings = np.flatnonzero((samples[:-1] < threshold_counts) & (samples[1:] >= threshold_counts)) + 1
        elif direction_name == "Falling":
            crossings = np.flatnonzero((samples[:-1] > threshold_counts) & (samples[1:] <= threshold_counts)) + 1
        else:
            crossings = np.flatnonzero(
                ((samples[:-1] < threshold_counts) & (samples[1:] >= threshold_counts))
                | ((samples[:-1] > threshold_counts) & (samples[1:] <= threshold_counts))
            ) + 1
        if crossings.size == 0:
            return None
        if hint_index is None:
            return int(crossings[0])
        return int(crossings[np.argmin(np.abs(crossings - int(hint_index)))])

    def _software_realign_fast_streaming_trigger(
        self,
        settings: ScopeState,
        active_ranges: dict[str, float],
        raw_buffers: dict[str, np.ndarray],
        *,
        captured: int,
        driver_trigger_index: int | None,
    ) -> tuple[int | None, str]:
        if settings.trigger.mode == "None" or settings.trigger.trigger_type != "Simple edge":
            return driver_trigger_index, "driver"
        source = settings.trigger.source
        raw_source = raw_buffers.get(source)
        if raw_source is None or captured <= 1:
            return driver_trigger_index, "driver"
        source_channel = settings.channel_a if source == "A" else settings.channel_b
        if source not in active_ranges:
            return driver_trigger_index, "driver"
        direction_name = self._trigger_direction_for_channel(settings.trigger.direction, source_channel)
        hardware_level = display_to_hardware_volts(source_channel, settings.trigger.level_volts)
        threshold_counts = self._threshold_counts(hardware_level, active_ranges[source])
        aligned_index = self._find_simple_edge_trigger_index(
            raw_source[:captured],
            threshold_counts,
            direction_name,
            hint_index=driver_trigger_index,
        )
        if aligned_index is None:
            return driver_trigger_index, "driver"
        return aligned_index, "software"

    def _fast_streaming_start_time_ns(
        self,
        settings: ScopeState,
        sample_count: int,
        actual_interval_s: float,
        *,
        triggered: bool,
    ) -> float:
        if settings.trigger.mode == "None" or not triggered:
            return 0.0
        total_span_s = max(sample_count - 1, 0) * actual_interval_s
        pre_trigger_s = total_span_s * clamp(settings.trigger.pre_trigger_percent / 100.0, 0.0, 1.0)
        return -pre_trigger_s * 1e9

    def _capture_fast_streaming(self, settings: ScopeState) -> CaptureFrame:
        if not settings.channel_a.enabled and not settings.channel_b.enabled:
            raise RuntimeError("Enable Channel A or B before starting acquisition.")

        active_ranges = self._apply_channels(settings)
        self._apply_trigger(settings, active_ranges)

        interval_value, unit_code, actual_interval_s = self._interval_to_ps2000_units(
            1.0 / settings.sample_rate_hz
        )
        desired_sample_count, capture_sample_count = self._fast_streaming_capture_window(settings, actual_interval_s)
        aggregate, overview_size = self._fast_streaming_settings(capture_sample_count)
        handle = c_int16(self._device.handle)
        status = self._ps._run_streaming_ns(
            handle,
            c_uint32(interval_value),
            c_int32(unit_code),
            c_uint32(capture_sample_count),
            c_int16(1),
            c_uint32(aggregate),
            c_uint32(overview_size),
        )
        if not status:
            raise RuntimeError("Fast streaming could not be started.")

        callback_state = {"auto_stop": False, "triggered": False}
        callback_type = CFUNCTYPE(
            None,
            POINTER(POINTER(c_int16)),
            c_int16,
            c_uint32,
            c_int16,
            c_int16,
            c_uint32,
        )

        def _overview_callback(_overview_buffers, _overflow, _triggered_at, _triggered, auto_stop, _n_values):
            callback_state["auto_stop"] = bool(auto_stop)
            callback_state["triggered"] = callback_state["triggered"] or bool(_triggered)

        callback = callback_type(_overview_callback)
        overrun = c_int16(0)
        deadline = time.time() + max(2.0, capture_sample_count * actual_interval_s + 1.0)
        while not callback_state["auto_stop"]:
            self._ps._get_streaming_last_values(handle, cast(callback, c_void_p))
            self._ps._overview_buffer_status(handle, byref(overrun))
            if overrun.value:
                self._ps.stop(self._device)
                raise RuntimeError(
                    f"Fast streaming overview buffer overrun. "
                    f"Try a lower sample rate or shorter timebase, or switch mode."
                )
            if time.time() > deadline:
                self._ps.stop(self._device)
                raise TimeoutError("Timed out waiting for fast streaming data.")
            time.sleep(0.001)

        self._ps.stop(self._device)

        buffer_a = np.empty(capture_sample_count, dtype=np.int16) if settings.channel_a.enabled else None
        buffer_b = np.empty(capture_sample_count, dtype=np.int16) if settings.channel_b.enabled else None
        start_time = c_double(
            self._fast_streaming_start_time_ns(
                settings,
                desired_sample_count,
                actual_interval_s,
                triggered=bool(callback_state["triggered"]),
            )
        )
        overflow = c_int16(0)
        trigger_at = c_uint32(0)
        triggered = c_int16(0)
        captured = int(
            self._ps._get_streaming_values_no_aggregation(
                handle,
                byref(start_time),
                buffer_a.ctypes.data if buffer_a is not None else None,
                buffer_b.ctypes.data if buffer_b is not None else None,
                None,
                None,
                byref(overflow),
                byref(trigger_at),
                byref(triggered),
                c_uint32(capture_sample_count),
            )
        )
        if captured <= 0:
            raise RuntimeError("Fast streaming capture returned no samples.")

        full_raw_data = {}
        if buffer_a is not None:
            full_raw_data["A"] = buffer_a[:captured]
        if buffer_b is not None:
            full_raw_data["B"] = buffer_b[:captured]

        driver_trigger_index = int(trigger_at.value) if bool(triggered.value) else None
        effective_trigger_index, trigger_index_source = self._software_realign_fast_streaming_trigger(
            settings,
            active_ranges,
            full_raw_data,
            captured=captured,
            driver_trigger_index=driver_trigger_index,
        )
        target_trigger_index = int(
            round(clamp(settings.trigger.pre_trigger_percent / 100.0, 0.0, 1.0) * max(desired_sample_count - 1, 1))
        )
        if effective_trigger_index is not None:
            start_index = int(clamp(int(effective_trigger_index) - target_trigger_index, 0, max(captured - desired_sample_count, 0)))
        else:
            start_index = 0
        end_index = min(captured, start_index + desired_sample_count)

        raw_data = {}
        if buffer_a is not None:
            raw_data["A"] = buffer_a[start_index:end_index]
        if buffer_b is not None:
            raw_data["B"] = buffer_b[start_index:end_index]
        sliced_count = end_index - start_index
        if effective_trigger_index is not None:
            relative_trigger_index = max(0, int(effective_trigger_index) - start_index)
            # Keep the displayed time axis consistent with block mode and with
            # PicoScope's screen-time workflow: the screen starts at 0 and the
            # trigger marker sits inside the visible window at the pre-trigger
            # position, instead of shifting the whole X axis negative.
            times = np.arange(sliced_count, dtype=np.float32) * actual_interval_s
            trigger_index_for_frame = relative_trigger_index
        else:
            times = np.arange(sliced_count, dtype=np.float32) * actual_interval_s
            trigger_index_for_frame = None
        CONTROLLER_LOGGER.info(
            "Fast streaming trigger window. captured=%s desired=%s capture_window=%s start_time_ns=%s driver_trigger_at=%s effective_trigger_at=%s trigger_source=%s relative_trigger_index=%s start_index=%s end_index=%s",
            captured,
            desired_sample_count,
            capture_sample_count,
            float(start_time.value),
            driver_trigger_index,
            effective_trigger_index,
            trigger_index_source,
            relative_trigger_index if bool(triggered.value) else None,
            start_index,
            end_index,
        )
        return self._build_frame(
            settings,
            times,
            raw_data,
            active_ranges,
            "Fast streaming",
            1.0 / actual_interval_s,
            trigger_sample_index=trigger_index_for_frame,
            trigger_confirmed=bool(triggered.value),
        )

    def capture(self, settings: ScopeState) -> CaptureFrame:
        self._connect_if_needed()
        if self._device is None:
            CONTROLLER_LOGGER.warning("Capture requested without an active device connection.")
            raise RuntimeError(self._last_status)

        try:
            if settings.acquisition_mode == "Compatible streaming":
                frame = self._capture_compatible_streaming(settings)
            elif settings.acquisition_mode == "Fast streaming":
                frame = self._capture_fast_streaming(settings)
            else:
                frame = self._capture_block_mode(settings)
            self._last_status = frame.connection_label
            self._last_source = frame.source_label
            CONTROLLER_LOGGER.info(
                "Capture complete. mode=%s source=%s samples=%d sample_rate=%s",
                settings.acquisition_mode,
                frame.source_label,
                frame.sample_count,
                frame.sample_rate_hz,
            )
            return frame
        except Exception as exc:
            self._last_status = f"Capture failed: {exc}"
            self._last_source = "Hardware"
            CONTROLLER_LOGGER.exception("Capture failed. mode=%s", settings.acquisition_mode)
            raise RuntimeError(self._last_status) from exc


