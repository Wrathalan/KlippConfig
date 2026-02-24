from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class RouteDefinition:
    key: str
    label: str
    active: bool = True


class LeftNav(QListWidget):
    route_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("left_nav")
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.currentItemChanged.connect(self._emit_route)

    def set_routes(self, routes: list[RouteDefinition]) -> None:
        self.clear()
        for route in routes:
            if not route.active:
                continue
            item = QListWidgetItem(route.label, self)
            item.setData(Qt.ItemDataRole.UserRole, route.key)
        if self.count() > 0:
            self.setCurrentRow(0)

    def select_route(self, route_key: str) -> None:
        for index in range(self.count()):
            item = self.item(index)
            if item is None:
                continue
            key = item.data(Qt.ItemDataRole.UserRole)
            if key == route_key:
                self.blockSignals(True)
                self.setCurrentRow(index)
                self.blockSignals(False)
                return

    def _emit_route(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        route_key = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(route_key, str) and route_key:
            self.route_selected.emit(route_key)


class RightContextPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("right_context_panel")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.content = QPlainTextEdit(self)
        self.content.setReadOnly(True)
        self.content.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content.setPlaceholderText("Right context panel placeholder (Context | Validation | Logs).")
        layout.addWidget(self.content, 1)


class BottomStatusBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bottom_status_bar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.connection_label = QLabel("Disconnected", self)
        self.target_label = QLabel("Target: none", self)
        self.state_label = QLabel("State: idle", self)
        layout.addWidget(self.connection_label)
        layout.addStretch(1)
        layout.addWidget(self.target_label)
        layout.addSpacing(12)
        layout.addWidget(self.state_label)

    def set_connection(self, connected: bool, target: str) -> None:
        self.connection_label.setText("Connected" if connected else "Disconnected")
        self.target_label.setText(f"Target: {target or 'none'}")

    def set_state(self, text: str) -> None:
        self.state_label.setText(f"State: {text or 'idle'}")
