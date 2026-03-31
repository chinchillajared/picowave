from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from picowave.logging_config import install_exception_hooks
from picowave.ui.main_window import MainWindow


def main() -> int:
    install_exception_hooks()
    app = QApplication(sys.argv)
    window = MainWindow(show_connect_dialog_on_start=True)
    window.show()
    return app.exec()
