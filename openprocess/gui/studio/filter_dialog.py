"""The Filter… dialog of a log page: ProM's and Disco's log filters in one form.

Each filter is a group box that is switched off until you tick it; the line
at the bottom previews the result as you change anything (worked out off
the GUI thread: on a million events a filter takes a second or two).
Accepting opens the filtered log as a new document next to the original
(see :mod:`openprocess.mining.filtering` for what each filter does).

The lists and ranges come from the document's summary, which the log page
already has, and a columnar log's time range from its arrays, so opening
the dialog on a large log builds no event.
"""

from __future__ import annotations

from collections import Counter
from datetime import timezone

from PySide6.QtCore import QDateTime, Qt, QTimer, QTimeZone
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ...mining.filtering import FilterSettings, apply_filters
from ...mining.stats import summarise
from .widgets import button, hbox, label, scroll
from .workers import run_in_background

ACTIVITY_MODE_TEXT = [
    ("keep events", "Keep only the events of the ticked activities"),
    ("mandatory", "Keep cases that contain a ticked activity"),
    ("forbidden", "Remove cases that contain a ticked activity"),
]
TIME_MODE_TEXT = [("contained", "Cases that start and end in the period"),
                  ("started", "Cases that start in the period"),
                  ("intersecting", "Cases active at some point in the period")]


def _checklist(counts: Counter, total: int) -> QListWidget:
    """Ticked items, most frequent first, each with its share of the cases."""
    widget = QListWidget()
    for name, count in counts.most_common():
        item = QListWidgetItem(f"{name}   ({count:,} · {count / max(total, 1):.0%})")
        item.setData(Qt.UserRole, name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        widget.addItem(item)
    widget.setMinimumHeight(min(60 + 22 * len(counts), 180))
    return widget


def _ticked(widget: QListWidget) -> set[str]:
    return {widget.item(i).data(Qt.UserRole) for i in range(widget.count())
            if widget.item(i).checkState() == Qt.Checked}


def _set_all(widget: QListWidget, checked: bool) -> None:
    for i in range(widget.count()):
        widget.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)


class FilterDialog(QDialog):
    """Choose filters for ``document``'s log; :meth:`result` is the new log."""

    def __init__(self, document, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.log = document.log
        self.classifier = document.classifier
        self.setWindowTitle(f"Filter {document.name}")
        self.setMinimumWidth(560)
        summary = document.summary                    # cached on the document
        cases = summary.case_count
        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._preview)
        #: The newest preview asked for, and the one ``_result`` answers.
        self._request = 0
        self._result_request = -1

        # -- variants
        self.variants_box = self._group("Variants", "Keep the most frequent behaviour.")
        self.by_coverage = QRadioButton("Most frequent variants covering")
        self.by_count = QRadioButton("The most frequent")
        self.by_coverage.setChecked(True)
        choice = QButtonGroup(self)
        choice.addButton(self.by_coverage)
        choice.addButton(self.by_count)
        self.coverage = QDoubleSpinBox()
        self.coverage.setRange(1, 100)
        self.coverage.setDecimals(0)
        self.coverage.setValue(80)
        self.coverage.setSuffix(" % of the cases")
        self.count = QSpinBox()
        self.count.setRange(1, max(summary.variant_count, 1))
        self.count.setValue(min(5, self.count.maximum()))
        self.count.setSuffix(" variants")
        self.variants_box.layout().addLayout(hbox(self.by_coverage, self.coverage, None))
        self.variants_box.layout().addLayout(hbox(self.by_count, self.count, None))

        # -- activities
        activity_cases = Counter({a.name: a.cases for a in summary.activities})
        self.activities_box = self._group("Activities", "Share = the cases the activity "
                                          "occurs in.")
        self.activity_mode = QComboBox()
        for key, text in ACTIVITY_MODE_TEXT:
            self.activity_mode.addItem(text, key)
        self.activity_list = _checklist(activity_cases, cases)
        self.activities_box.layout().addWidget(self.activity_mode)
        self.activities_box.layout().addWidget(self.activity_list)
        self.activities_box.layout().addLayout(hbox(
            button("All", lambda: _set_all(self.activity_list, True), kind="ghost"),
            button("None", lambda: _set_all(self.activity_list, False), kind="ghost"), None))

        # -- start and end activities
        self.start_box = self._group("Start activities", "Keep cases that start with a "
                                     "ticked activity.")
        self.start_list = _checklist(Counter({a.name: a.as_start for a in summary.activities
                                              if a.as_start}), cases)
        self.start_box.layout().addWidget(self.start_list)
        self.end_box = self._group("End activities", "Keep cases that end with a ticked "
                                   "activity, e.g. to drop cases that were still running.")
        self.end_list = _checklist(Counter({a.name: a.as_end for a in summary.activities
                                            if a.as_end}), cases)
        self.end_box.layout().addWidget(self.end_list)

        # -- case length
        lengths = list(summary.events_per_case) or [0]
        self.length_box = self._group("Case length", "Number of events per case (after the "
                                      "activity filter).")
        self.min_length, self.max_length = QSpinBox(), QSpinBox()
        for box, value in ((self.min_length, min(lengths)), (self.max_length, max(lengths))):
            box.setRange(0, 1_000_000)
            box.setValue(value)
        self.length_box.layout().addLayout(hbox(label("From"), self.min_length, label("to"),
                                                self.max_length, label("events"), None))

        # -- time frame
        first, last = _time_range(self.log)
        self.time_box = self._group("Time frame", "Times in UTC.")
        self.time_mode = QComboBox()
        for key, text in TIME_MODE_TEXT:
            self.time_mode.addItem(text, key)
        self.time_from, self.time_to = QDateTimeEdit(), QDateTimeEdit()
        for edit, stamp in ((self.time_from, first), (self.time_to, last)):
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setCalendarPopup(True)
            if hasattr(edit, "setTimeZone"):                  # Qt 6.7 and later
                edit.setTimeZone(QTimeZone.utc())
            else:
                edit.setTimeSpec(Qt.UTC)
            if stamp is not None:
                edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(stamp.timestamp()),
                                                              QTimeZone.utc()))
        form = QFormLayout()
        form.addRow("Keep", self.time_mode)
        form.addRow("From", self.time_from)
        form.addRow("To", self.time_to)
        self.time_box.layout().addLayout(form)
        if first is None:
            self.time_box.setEnabled(False)
            self.time_box.setTitle("Time frame (this log has no timestamps)")

        sections = QWidget()
        column = QVBoxLayout(sections)
        column.setContentsMargins(0, 0, 8, 0)
        for box in (self.variants_box, self.activities_box, self.start_box, self.end_box,
                    self.length_box, self.time_box):
            column.addWidget(box)
        column.addStretch(1)

        self.name = QLineEdit(f"{document.name} (filtered)")
        self.preview = label("", "muted", wrap=True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Create filtered log")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(label("Tick the filters to apply. The result opens as a new log; "
                               "this one stays as it is.", "muted", wrap=True))
        layout.addWidget(scroll(sections), 1)
        name_row = QFormLayout()
        name_row.addRow("Name", self.name)
        layout.addLayout(name_row)
        layout.addWidget(self.preview)
        layout.addWidget(self.buttons)
        self.resize(620, 720)

        # Any change updates the preview (after a short pause).
        for widget in (self.variants_box, self.activities_box, self.start_box, self.end_box,
                       self.length_box, self.time_box):
            widget.toggled.connect(self._changed)
        for widget in (self.by_coverage, self.by_count):
            widget.toggled.connect(self._changed)
        for widget in (self.coverage, self.count, self.min_length, self.max_length):
            widget.valueChanged.connect(self._changed)
        for widget in (self.activity_mode, self.time_mode):
            widget.currentIndexChanged.connect(self._changed)
        for widget in (self.activity_list, self.start_list, self.end_list):
            widget.itemChanged.connect(self._changed)
        for widget in (self.time_from, self.time_to):
            widget.dateTimeChanged.connect(self._changed)
        self._result = None
        self._preview()

    def _group(self, title: str, hint: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setCheckable(True)
        box.setChecked(False)
        layout = QVBoxLayout(box)
        layout.addWidget(label(hint, "muted", wrap=True))
        return box

    def settings(self) -> FilterSettings:
        """The filters as currently ticked."""
        settings = FilterSettings()
        if self.variants_box.isChecked():
            if self.by_coverage.isChecked():
                settings.variant_coverage = self.coverage.value()
            else:
                settings.top_variants = self.count.value()
        if self.activities_box.isChecked():
            settings.activities = (_ticked(self.activity_list),
                                   self.activity_mode.currentData())
        if self.start_box.isChecked():
            settings.start_activities = _ticked(self.start_list)
        if self.end_box.isChecked():
            settings.end_activities = _ticked(self.end_list)
        if self.length_box.isChecked():
            settings.case_length = (self.min_length.value(), self.max_length.value())
        if self.time_box.isChecked() and self.time_box.isEnabled():
            start = self.time_from.dateTime().toPython().replace(tzinfo=timezone.utc)
            end = self.time_to.dateTime().toPython().replace(tzinfo=timezone.utc)
            settings.time_frame = (start, end, self.time_mode.currentData())
        return settings

    def _changed(self, *_args) -> None:
        self._timer.start()

    def _compute(self, settings: FilterSettings):
        """The filtered log and its summary (nothing ticked: the log as it is)."""
        result = apply_filters(self.log, settings, self.classifier,
                               name=self.name.text().strip() or None)
        if not settings.describe():
            return result, self.document.summary
        return result, summarise(result, self.classifier)

    def _preview(self) -> None:
        """Work out the result in the background; only the newest request is shown."""
        self._request += 1
        request, settings = self._request, self.settings()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.preview.setText("Working out the result…")

        def show(outcome) -> None:
            if request != self._request:
                return
            self._result, after = outcome
            self._result_request = request
            before = self.document.summary
            share = after.case_count / max(before.case_count, 1)
            self.preview.setText(
                f"Result: {after.case_count:,} of {before.case_count:,} cases ({share:.0%}) · "
                f"{after.event_count:,} events · {after.variant_count:,} variants · "
                f"{after.activity_count} activities")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(after.case_count > 0)

        def failed(message: str) -> None:
            if request == self._request:
                self.preview.setText(f"The filter failed: {message}")
        run_in_background(lambda: self._compute(settings), show, failed)

    def result_log(self):
        """The filtered log (named as typed): the previewed one when it is up to
        date, else worked out now."""
        if self._timer.isActive() or self._result_request != self._request:
            self._timer.stop()
            self._request += 1
            self._result, _after = self._compute(self.settings())
            self._result_request = self._request
        return self._result


def _time_range(log):
    """The earliest and latest timestamp of a log (None, None: it has none);
    from the arrays of a columnar log."""
    if log.columnar:
        import numpy as np
        from ...mining.columns import NO_TIME
        store = log.traces.store
        micros = store.micros_array
        timed = micros != NO_TIME
        if not timed.any():
            return None, None
        where = np.flatnonzero(timed)
        values = micros[where]
        return store.timestamp(int(where[np.argmin(values)])), store.timestamp(int(where[np.argmax(values)]))
    stamps = [e.timestamp for t in log for e in t if e.timestamp is not None]
    return (min(stamps), max(stamps)) if stamps else (None, None)
