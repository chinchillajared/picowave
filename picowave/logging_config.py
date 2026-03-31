from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from picowave.config import LOG_DIR, LOG_FILE


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("picowave")
    if logger.handlers:
        return logger

    import os

    os.makedirs(LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


APP_LOGGER = configure_logging()
CONTROLLER_LOGGER = APP_LOGGER.getChild("controller")
WORKER_LOGGER = APP_LOGGER.getChild("worker")
UI_LOGGER = APP_LOGGER.getChild("ui")


def install_exception_hooks() -> None:
    def _sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc_value, exc_traceback)
        APP_LOGGER.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = _sys_excepthook

    if hasattr(threading, "excepthook"):
        def _threading_excepthook(args):
            if issubclass(args.exc_type, KeyboardInterrupt):
                return
            APP_LOGGER.critical(
                "Unhandled thread exception",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _threading_excepthook
