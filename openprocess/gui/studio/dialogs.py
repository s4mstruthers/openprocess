"""Two small dialogs of the Studio window: which logs to compare, and which
columns of a CSV file are the case, the activity and the time."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout

from ...mining.csv_import import ColumnMapping, guess_mapping, sniff
from .widgets import label


class CompareDialog(QDialog):
    """Pick the logs to compare (at least two)."""

    def __init__(self, logs: list, preselected: list, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.setWindowTitle("Compare logs")
        self.logs = logs
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label("Choose two or more event logs to show side by side.",
                               "muted", wrap=True))
        self.list = QListWidget()
        chosen = {d.id for d in preselected}
        for log in logs:
            item = QListWidgetItem(log.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if log.id in chosen or len(logs) == 2
                               else Qt.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _item: self._validate())
        layout.addWidget(self.list)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Compare")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def chosen(self) -> list:
        return [log for index, log in enumerate(self.logs)
                if self.list.item(index).checkState() == Qt.Checked]

    def _validate(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(len(self.chosen()) >= 2)


class CsvDialog(QDialog):
    """Confirm which CSV columns hold case id, activity, timestamp, ..."""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Import {Path(path).name}")
        _, header = sniff(path)
        guess = guess_mapping(header) or ColumnMapping(header[0] if header else "",
                                                       header[1] if len(header) > 1 else "")
        self.boxes: dict[str, QComboBox] = {}
        form = QFormLayout()
        for role, title, optional in (("case", "Case id", False), ("activity", "Activity", False),
                                      ("timestamp", "Timestamp", True),
                                      ("resource", "Resource", True),
                                      ("lifecycle", "Lifecycle", True)):
            box = QComboBox()
            if optional:
                box.addItem("— none —", None)
            for column in header:
                box.addItem(column, column)
            current = getattr(guess, role)
            if current is not None:
                box.setCurrentIndex(box.findData(current))
            self.boxes[role] = box
            form.addRow(title, box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Choose the columns that identify cases and activities. "
                               "Other columns become event attributes.", "muted", wrap=True))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def mapping(self) -> ColumnMapping:
        return ColumnMapping(**{role: box.currentData() for role, box in self.boxes.items()})


__all__ = ["CompareDialog", "CsvDialog"]
