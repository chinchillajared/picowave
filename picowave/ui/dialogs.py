from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget
class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setModal(False)
        self.setObjectName("aboutDialog")
        self.resize(440, 300)
        self._current_section = "app"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("About")
        title.setObjectName("aboutTitle")
        layout.addWidget(title)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        self.app_data_button = QPushButton("App data")
        self.app_data_button.setObjectName("aboutTabButton")
        self.app_data_button.clicked.connect(lambda: self.set_section("app"))
        buttons_row.addWidget(self.app_data_button)
        self.device_data_button = QPushButton("Device data")
        self.device_data_button.setObjectName("aboutTabButton")
        self.device_data_button.clicked.connect(lambda: self.set_section("device"))
        buttons_row.addWidget(self.device_data_button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        self.content_label = QLabel()
        self.content_label.setObjectName("aboutContent")
        self.content_label.setWordWrap(True)
        self.content_label.setTextFormat(Qt.RichText)
        self.content_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.content_label, 1)

        self._app_html = ""
        self._device_html = ""
        self.set_section("app")

    @staticmethod
    def _render_metadata_html(title: str, metadata: dict[str, str]) -> str:
        rows = "".join(
            f"<tr><td><b>{label}</b></td><td>{value}</td></tr>"
            for label, value in metadata.items()
        )
        return (
            f"<div><b>{title}</b></div>"
            f"<table cellspacing='6' cellpadding='0'>{rows}</table>"
        )

    def set_app_metadata(self, metadata: dict[str, str]) -> None:
        self._app_html = self._render_metadata_html("App data", metadata)
        if self._current_section == "app":
            self.content_label.setText(self._app_html)

    def set_device_metadata(self, metadata: dict[str, str]) -> None:
        self._device_html = self._render_metadata_html("Device data", metadata)
        if self._current_section == "device":
            self.content_label.setText(self._device_html)

    def set_section(self, section: str) -> None:
        self._current_section = section
        self.app_data_button.setProperty("selected", section == "app")
        self.device_data_button.setProperty("selected", section == "device")
        for button in (self.app_data_button, self.device_data_button):
            button.style().unpolish(button)
            button.style().polish(button)
        self.content_label.setText(self._app_html if section == "app" else self._device_html)


class ScopeConnectDialog(QDialog):
    # Startup device picker that replaces the old always-visible Connect button.
    # MainWindow decides when to show it, which keeps tests and offscreen runs
    # from being blocked by a modal dialog.
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect")
        self.setModal(True)
        self.setObjectName("connectDialog")
        self.resize(420, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Select oscilloscope")
        title.setObjectName("aboutTitle")
        layout.addWidget(title)

        self.status_label = QLabel("Choose an available oscilloscope and press Connect.")
        self.status_label.setObjectName("footerText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.device_list = QListWidget()
        self.device_list.setObjectName("connectDeviceList")
        self.device_list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.device_list, 1)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(8)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("aboutTabButton")
        buttons_row.addWidget(self.refresh_button)
        buttons_row.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("aboutTabButton")
        self.cancel_button.clicked.connect(self.reject)
        buttons_row.addWidget(self.cancel_button)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("aboutTabButton")
        self.connect_button.setProperty("selected", True)
        self.connect_button.clicked.connect(self.accept)
        buttons_row.addWidget(self.connect_button)
        layout.addLayout(buttons_row)

    def set_devices(self, devices: list[dict[str, str]], status_text: str = "") -> None:
        self.device_list.clear()
        for device in devices:
            item = QListWidgetItem(device["label"])
            item.setData(Qt.UserRole, device["serial"])
            self.device_list.addItem(item)
        if self.device_list.count() > 0:
            self.device_list.setCurrentRow(0)
        self.connect_button.setEnabled(self.device_list.count() > 0)
        self.status_label.setText(status_text or ("Choose an available oscilloscope and press Connect." if devices else "No oscilloscopes detected."))

    def selected_serial(self) -> str | None:
        item = self.device_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole) or None


