from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QThread, Signal

from picowave.controller import Pico2204AController
from picowave.logging_config import WORKER_LOGGER
from picowave.models import ScopeState
class AcquisitionThread(QThread):
    frame_ready = Signal(object)
    capture_failed = Signal(str)

    def __init__(self, controller: Pico2204AController, initial_state: ScopeState) -> None:
        super().__init__()
        self._controller = controller
        self._state = copy.deepcopy(initial_state)
        self._state_lock = threading.Lock()
        self._alive = True
        self._should_capture = bool(initial_state.running)

    def update_state(self, state: ScopeState) -> None:
        with self._state_lock:
            self._state = copy.deepcopy(state)
            self._should_capture = bool(state.running)
        WORKER_LOGGER.debug(
            "Worker state updated. running=%s mode=%s timebase=%s sample_rate=%s",
            state.running,
            state.acquisition_mode,
            state.time_per_div,
            state.sample_rate_hz,
        )

    def shutdown(self) -> None:
        self._alive = False
        WORKER_LOGGER.info("Shutting down acquisition thread.")
        self.wait(2000)

    def run(self) -> None:
        WORKER_LOGGER.info("Acquisition thread started.")
        while self._alive:
            if not self._should_capture:
                self.msleep(40)
                continue
            with self._state_lock:
                state = copy.deepcopy(self._state)
            try:
                frame = self._controller.capture(state)
            except Exception as exc:
                WORKER_LOGGER.exception("Acquisition loop failed.")
                self.capture_failed.emit(str(exc))
                self.msleep(250)
                continue
            self.frame_ready.emit(frame)
            self.msleep(65)
        WORKER_LOGGER.info("Acquisition thread stopped.")


