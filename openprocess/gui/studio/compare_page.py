"""Compare two or more event logs side by side.

Four views, each answering "how do these logs differ?":

* **Overview** -- key figures in one table (one column per log, plus the
  relative difference when there are two) and a bar chart of any figure;
* **Activities** -- for every activity, the share of cases that contain it
  in each log, so missing or rarer steps stand out;
* **Variants** -- the most common variants and how often each log follows
  them;
* **Dotted charts** -- one chart per log, with the *same* time scale and the
  *same* colour for an activity in every chart; zooming one zooms all.

Each log keeps a colour of its own (the dot in front of its name), used in
the bars.  Activities keep the colours of the dotted chart.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import replace
from datetime import timedelta

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHeaderView, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ...mining.stats import format_duration
from . import style
from .charts import ColumnChart
from .documents import ComparisonDocument, LogDocument
from .dotted_chart import X_MODES, X_TITLES, DotData, DotSettings, DottedChart
from .log_page import _item, _runs, _table
from .widgets import (
    suggested_path,
    BarDelegate, Card, ColourDot, Legend, LegendSwatch, PageHeader, SegmentedControl,
    SequenceDelegate, button, hbox, label, scroll, vbox,
)

LOG_ROLE = Qt.UserRole + 1          # colour of a bar


def log_colour(index: int) -> str:
    """Logs are told apart by the categorical palette, in the order given."""
    return style.categorical(index)


# ---------------------------------------------------------------------------
# Figures (no Qt)
# ---------------------------------------------------------------------------
def _seconds(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def key_figures(document: LogDocument) -> list[tuple[str, str, float | None, str]]:
    """``(figure, shown text, number for charts, kind)`` for one log.

    ``kind`` is ``"count"``, ``"duration"`` (number in seconds), ``"percent"``
    or ``"date"`` (not charted).
    """
    summary = document.summary
    durations = [d.total_seconds() for d in summary.case_durations]
    cases = summary.case_count
    lengths = [length for length, n in summary.events_per_case.items() for _ in range(n)]
    top_share = 100.0 * summary.variants[0].count / cases if cases and summary.variants else 0.0
    span = (summary.end - summary.start) if summary.start and summary.end else None

    def duration(seconds: float | None) -> tuple[str, float | None]:
        return (format_duration(seconds) if seconds is not None else "–"), seconds

    figures: list[tuple[str, str, float | None, str]] = [
        ("Cases", f"{cases:,}", float(cases), "count"),
        ("Events", f"{summary.event_count:,}", float(summary.event_count), "count"),
        ("Activities", f"{summary.activity_count:,}", float(summary.activity_count), "count"),
        ("Variants", f"{summary.variant_count:,}", float(summary.variant_count), "count"),
        ("Cases in the most common variant", f"{top_share:.1f} %", top_share, "percent"),
        ("Events per case (mean)",
         f"{statistics.fmean(lengths):.2f}" if lengths else "–",
         statistics.fmean(lengths) if lengths else None, "count"),
        ("Events per case (max)", f"{max(lengths):,}" if lengths else "–",
         float(max(lengths)) if lengths else None, "count"),
    ]
    for name, value in (
            ("Case duration (mean)", statistics.fmean(durations) if durations else None),
            ("Case duration (median)", statistics.median(durations) if durations else None),
            ("Case duration (min)", min(durations) if durations else None),
            ("Case duration (max)", max(durations) if durations else None),
            ("Time span of the log", _seconds(span))):
        text, number = duration(value)
        figures.append((name, text, number, "duration"))
    figures.append(("First event", summary.start.strftime("%Y-%m-%d %H:%M") if summary.start
                    else "–", None, "date"))
    figures.append(("Last event", summary.end.strftime("%Y-%m-%d %H:%M") if summary.end
                    else "–", None, "date"))
    return figures


def percent(share: float) -> str:
    """0.952 -> "95.2 %", 1.0 -> "100 %"."""
    return f"{100 * share:.1f}".rstrip("0").rstrip(".") + " %"


def relative_difference(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "–"
    if a == b:
        return "="
    if a == 0:
        return "new"
    change = 100.0 * (b - a) / abs(a)
    return f"{change:+.0f} %"


def activity_shares(documents: list[LogDocument]) -> list[tuple[str, list[float], list[int]]]:
    """Every activity with, per log, the share of cases containing it (0-1)
    and the number of events.  Sorted by the largest share."""
    names: dict[str, None] = {}
    per_log = []
    for document in documents:
        summary = document.summary
        stats = {a.name: a for a in summary.activities}
        per_log.append((stats, max(summary.case_count, 1)))
        for name in stats:
            names.setdefault(name, None)
    rows = []
    for name in names:
        shares, counts = [], []
        for stats, cases in per_log:
            entry = stats.get(name)
            shares.append(entry.cases / cases if entry else 0.0)
            counts.append(entry.occurrences if entry else 0)
        rows.append((name, shares, counts))
    rows.sort(key=lambda row: (-max(row[1]), row[0]))
    return rows


def variant_shares(documents: list[LogDocument], limit: int = 60):
    """The most common variants over all logs with each log's share of cases."""
    per_log = []
    combined: dict[tuple[str, ...], float] = {}
    for document in documents:
        summary = document.summary
        cases = max(summary.case_count, 1)
        shares = {v.sequence: v.count / cases for v in summary.variants}
        per_log.append(shares)
        for sequence, share in shares.items():
            combined[sequence] = combined.get(sequence, 0.0) + share
    ordered = sorted(combined, key=lambda seq: -combined[seq])[:limit]
    return [(sequence, [shares.get(sequence, 0.0) for shares in per_log])
            for sequence in ordered], len(combined)


def align_colours(datas: list[DotData]) -> list[str]:
    """Give every activity the same colour in all charts.

    Each :class:`DotData` ranks its own colour values by frequency; re-rank
    them over all logs together and renumber the dots accordingly.
    """
    totals: dict[str, int] = {}
    for data in datas:
        for value, count in data.colour_counts.items():
            totals[value] = totals.get(value, 0) + count
    shared = sorted(totals, key=lambda v: (-totals[v], v))
    index = {v: i for i, v in enumerate(shared)}
    for data in datas:
        mapping = [index[v] for v in data.colour_values]
        data.colour = np.asarray(mapping, dtype=np.int32)[data.colour]
        data.colour_counts = {v: data.colour_counts.get(v, 0) for v in shared}
        data.colour_values = list(shared)
    return shared


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
class ComparePage(QWidget):
    status = Signal(str)
    saved = Signal()

    TABS = ["Overview", "Activities", "Variants", "Dotted charts"]

    def __init__(self, document: ComparisonDocument, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.logs: list[LogDocument] = document.logs
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(document.name, self._subtitle())
        self.header.actions.addWidget(button("Export figures…", self.export,
                                             tooltip="Save the key figures as CSV"))
        root.addWidget(self.header)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 0, 20, 16)
        layout.setSpacing(12)
        legend = QWidget()
        legend_row = hbox(spacing=14)
        for index, log in enumerate(self.logs):
            legend_row.addLayout(hbox(ColourDot(log_colour(index), 11), label(log.name),
                                      spacing=6))
        legend_row.addStretch(1)
        legend.setLayout(legend_row)
        self.log_legend = legend
        self.tabs = SegmentedControl(self.TABS)
        layout.addLayout(hbox(self.tabs, 16, legend))
        self.stack = QStackedWidget()
        self.builders = [self._build_overview, self._build_activities, self._build_variants,
                         self._build_dotted]
        self.built: dict[int, QWidget] = {}
        for _ in self.TABS:
            self.stack.addWidget(QWidget())
        self.tabs.changed.connect(self._show_tab)
        layout.addWidget(self.stack, 1)
        root.addWidget(body, 1)
        self._show_tab(0)

    def _subtitle(self) -> str:
        return f"Comparing {len(self.logs)} event logs · " + \
            " · ".join(f"{log.summary.case_count:,} cases" for log in self.logs)

    def refresh_title(self) -> None:
        self.header.set_text(self.document.name, self._subtitle())

    def _show_tab(self, index: int) -> None:
        """Tabs are built the first time they are shown (dotted charts cost)."""
        if index not in self.built:
            widget = self.builders[index]()
            old = self.stack.widget(index)
            self.stack.insertWidget(index, widget)
            self.stack.removeWidget(old)
            old.deleteLater()
            self.built[index] = widget
        self.stack.setCurrentIndex(index)
        # In the dotted charts colours mean activities, not logs.
        self.log_legend.setVisible(index != 3)

    # -- overview ------------------------------------------------------------------
    def _build_overview(self) -> QWidget:
        figures = [key_figures(log) for log in self.logs]
        self.figures = figures
        two = len(self.logs) == 2
        card = Card("Key figures", "The same numbers as each log's Overview, side by side."
                    + (" The last column is the change from the first log to the second."
                       if two else ""))
        model = QStandardItemModel()
        headers = ["Figure"] + [log.name for log in self.logs] + (["Change"] if two else [])
        model.setHorizontalHeaderLabels(headers)
        for row, name in enumerate(f[0] for f in figures[0]):
            items = [_item(name)]
            for log_figures in figures:
                items.append(_item(log_figures[row][1], align_right=True))
            if two:
                a, b = figures[0][row][2], figures[1][row][2]
                change = relative_difference(a, b) if figures[0][row][3] != "date" else ""
                item = _item(change, align_right=True)
                if change.startswith("+") or change.startswith("-"):
                    item.setForeground(QColor(style.tokens().text_secondary))
                items.append(item)
            model.appendRow(items)
        table = _table(model)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        table.setMinimumHeight(30 * (model.rowCount() + 1) + 8)
        card.add(table)

        chart_card = Card("Chart")
        self.figure_box = QComboBox()
        self.chartable = [(i, f[0]) for i, f in enumerate(figures[0]) if f[3] != "date"]
        self.figure_box.addItems([name for _, name in self.chartable])
        chart_card.header.addWidget(self.figure_box)
        self.chart = ColumnChart()
        self.chart.setMinimumHeight(240)
        chart_card.add(self.chart)
        self.chart_note = label("", "muted")
        chart_card.add(self.chart_note)
        self.figure_box.currentIndexChanged.connect(self._update_chart)
        self._update_chart(0)

        page = QWidget()
        page.setLayout(vbox(card, chart_card, None, spacing=12))
        return scroll(page)

    def _update_chart(self, index: int) -> None:
        if not 0 <= index < len(self.chartable):
            return
        row, name = self.chartable[index]
        kind = self.figures[0][row][3]
        numbers = [f[row][2] for f in self.figures]
        unit, scale = "", 1.0
        if kind == "duration":
            largest = max((n for n in numbers if n is not None), default=0)
            unit, scale = next(((u, s) for u, s in (("days", 86400), ("hours", 3600),
                                                     ("minutes", 60)) if largest >= 2 * s),
                               ("seconds", 1))
        elif kind == "percent":
            unit = "%"
        values = [(log.name, (n or 0.0) / scale) for log, n in zip(self.logs, numbers)]
        self.chart.set_values(values)
        self.chart.y_title = unit
        self.chart_note.setText(f"{name}" + (f", in {unit}" if unit and unit != "%" else ""))

    # -- activities ---------------------------------------------------------------------
    def _build_activities(self) -> QWidget:
        rows = activity_shares(self.logs)
        two = len(self.logs) == 2
        card = Card("Activities", "Share of cases in which each activity occurs (the number "
                    "is the percentage; hover for event counts). Activities that occur in only "
                    "some of the logs are marked.")
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Activity"] + [log.name for log in self.logs]
                                        + (["Change (pp)"] if two else []) + ["Note"])
        for name, shares, counts in rows:
            items = [_item(name)]
            for index, (share, count) in enumerate(zip(shares, counts)):
                item = _item(percent(share), data=share, colour=log_colour(index))
                item.setToolTip(f"{count:,} events")
                items.append(item)
            if two:
                items.append(_item(f"{100 * (shares[1] - shares[0]):+.1f}", align_right=True))
            missing = [log.name for log, share in zip(self.logs, shares) if share == 0]
            items.append(_item("not in " + ", ".join(missing) if missing else ""))
            if missing:
                items[-1].setForeground(QColor(style.STATUS["serious"]))
            model.appendRow(items)
        table = _table(model)
        for column in range(1, len(self.logs) + 1):
            table.setItemDelegateForColumn(column, BarDelegate(LOG_ROLE, table))
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, len(self.logs) + 1):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        card.add(table, 1)
        return card

    # -- variants ------------------------------------------------------------------------
    def _build_variants(self) -> QWidget:
        rows, total = variant_shares(self.logs)
        card = Card("Variants", f"The {len(rows)} most common of {total:,} distinct variants "
                    "(over all logs), with the share of each log's cases that follows it.")
        colours = self._activity_colours()
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels([log.name for log in self.logs] + ["Variant"])
        for sequence, shares in rows:
            items = []
            for index, share in enumerate(shares):
                items.append(_item(percent(share), data=share, colour=log_colour(index)))
            chips = [(a if n == 1 else f"{a} ×{n}", colours.get(a, style.categorical(99)), None)
                     for a, n in _runs(sequence)]
            variant = _item(" → ".join(sequence), data=chips)
            variant.setToolTip(" → ".join(sequence))
            items.append(variant)
            model.appendRow(items)
        table = _table(model)
        for column in range(len(self.logs)):
            table.setItemDelegateForColumn(column, BarDelegate(LOG_ROLE, table))
            table.setColumnWidth(column, 190)
        table.setItemDelegateForColumn(len(self.logs), SequenceDelegate(table))
        table.horizontalHeader().setSectionResizeMode(len(self.logs), QHeaderView.Stretch)
        card.add(table, 1)
        return card

    def _activity_colours(self) -> dict[str, str]:
        """One colour per activity, ranked over all logs (as in the dotted charts)."""
        totals: dict[str, int] = {}
        for log in self.logs:
            for activity in log.summary.activities:
                totals[activity.name] = totals.get(activity.name, 0) + activity.occurrences
        ranked = sorted(totals, key=lambda a: (-totals[a], a))
        return {a: style.categorical(i) for i, a in enumerate(ranked)}

    # -- dotted charts -------------------------------------------------------------------
    def _build_dotted(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.dot_settings = replace(DotSettings(), x_mode=1)
        self.x_box = QComboBox()
        self.x_box.addItems(X_MODES)
        self.x_box.setCurrentIndex(1)
        self.x_box.setToolTip("Time since case start (the default here) lines all cases up at "
                              "zero, which is what makes different logs comparable.")
        self.colour_box = QComboBox()
        self.colour_box.addItems(["Activity", "Resource", "Lifecycle", "None"])
        self.size_box = QDoubleSpinBox()
        self.size_box.setRange(1.0, 10.0)
        self.size_box.setSingleStep(0.5)
        self.size_box.setValue(3.5)
        self.sync_box = QCheckBox("Zoom together")
        self.sync_box.setChecked(True)
        self.sync_box.setToolTip("Zooming or panning one chart does the same to the others")
        controls = Card()
        controls.add(hbox(label("x axis", "muted"), self.x_box, 12, label("Colour by", "muted"),
                          self.colour_box, 12, label("Dot size", "muted"), self.size_box, 12,
                          self.sync_box, None,
                          button("Reset zoom", lambda: [c.reset_view() for c in self.charts])))
        self.dot_legend = Legend()
        controls.add(self.dot_legend)
        controls.add(label("All charts share one horizontal scale and one colour per value. "
                           "Drag a rectangle to zoom, double-click to zoom out.", "muted",
                           wrap=True))
        layout.addWidget(controls)

        self.charts: list[DottedChart] = []
        self.chart_cards: list[Card] = []
        charts_host = QWidget()
        charts_layout = QVBoxLayout(charts_host)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(10)
        for index, log in enumerate(self.logs):
            card = Card(log.name)
            chart = DottedChart()
            chart.setMinimumHeight(max(200, min(360, 520 // len(self.logs))))
            chart.view_changed.connect(lambda c=chart: self._sync_views(c))
            card.add(chart, 1)
            charts_layout.addWidget(card, 1)
            self.charts.append(chart)
            self.chart_cards.append(card)
        layout.addWidget(scroll(charts_host), 1)
        self._syncing = False
        for widget in (self.x_box, self.colour_box):
            widget.currentIndexChanged.connect(lambda _: self._rebuild_dots())
        self.size_box.valueChanged.connect(lambda _: self._resize_dots())
        self._rebuild_dots()
        return page

    def _resize_dots(self) -> None:
        """A new dot size: the same dots, painted again."""
        for chart in self.charts:
            if chart.data is not None:
                chart.data.settings.dot_size = self.size_box.value()
                chart._invalidate()

    def _rebuild_dots(self) -> None:
        settings = replace(self.dot_settings, x_mode=self.x_box.currentIndex(),
                           colour_by=self.colour_box.currentText(),
                           dot_size=self.size_box.value())
        datas = [DotData(log, settings) for log in self.logs]
        shared = align_colours(datas)
        x_min = min(d.x_min for d in datas)
        x_max = max(d.x_max for d in datas)
        for chart, data in zip(self.charts, datas):
            chart.shared_x = (x_min, x_max)
            chart.set_data(data)
        self._syncing = False
        items = [(LegendSwatch("dot", style.categorical(i)), value or "–")
                 for i, value in enumerate(shared[:8])]
        if len(shared) > 8:
            items.append((LegendSwatch("dot", style.categorical(99)),
                          f"{len(shared) - 8} other values"))
        if settings.colour_by == "None":
            items = []
        self.dot_legend.set_items(items)
        title = X_TITLES[datas[0].mode] if datas else ""
        for card, data, log in zip(self.chart_cards, datas, self.logs):
            card.setToolTip(f"{log.name}: {len(data):,} events · {len(data.row_values):,} rows"
                            f" · x = {title}")

    def _sync_views(self, source: DottedChart) -> None:
        if self._syncing or not getattr(self, "sync_box", None) or not self.sync_box.isChecked():
            return
        self._syncing = True
        try:
            for chart in self.charts:
                if chart is not source and chart.data is not None:
                    chart.set_view(source.vx0, source.vx1, chart.vy0, chart.vy1, record=False)
        finally:
            self._syncing = False

    # -- export ----------------------------------------------------------------------------
    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export figures", suggested_path("comparison.csv"),
                                              "CSV (*.csv)")
        if not path:
            return
        figures = [key_figures(log) for log in self.logs]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Figure"] + [log.name for log in self.logs])
            for row, name in enumerate(f[0] for f in figures[0]):
                writer.writerow([name] + [f[row][1] for f in figures])
            writer.writerow([])
            writer.writerow(["Activity (share of cases)"] + [log.name for log in self.logs])
            for name, shares, _counts in activity_shares(self.logs):
                writer.writerow([name] + [f"{100 * s:.1f}" for s in shares])
        self.status.emit(f"Exported the comparison to {path}")
