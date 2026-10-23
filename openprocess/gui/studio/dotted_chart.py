"""The dotted chart, with the configuration and navigation of ProM's plug-in.

What a dotted chart is (course tutorial W2 L4; Song & van der Aalst 2007)
------------------------------------------------------------------------
Every event is a dot.  Three choices decide the picture:

* **x** -- *when*: actual time, time since the case started, the same as a
  percentage of the case's duration, or a *logical* position (the event's
  rank in the whole log, or its index inside its case);
* **rows** -- *what the dot belongs to*: the case (the classic view), or any
  event attribute -- activity, resource, lifecycle transition, ...;
* **colour** and **shape** -- two more attributes, e.g. colour by activity and
  shape by lifecycle, so start and complete events are distinguishable.

Rows can be sorted by name, first event, last event, duration (last − first)
or number of events.  Patterns this makes visible: arrival rate (slope of the
first dots), batching (vertical stripes), bottlenecks (long horizontal gaps),
and in the plane-boarding logs, how each boarding strategy changes who blocks
whom.

Navigation
----------
============================  =============================================
drag a rectangle              zoom into that area
⌘ / Ctrl + scroll, pinch      zoom around the pointer (⇧ as well: rows only)
scroll / ⇧ + scroll           pan rows / pan time
Alt + drag, middle drag       pan
double-click, **Reset**       show everything
**Back**, ⌫                   undo the last zoom
click a dot                   select its case (Esc clears)
============================  =============================================

The sliders under the chart set the horizontal and vertical zoom factors,
and the legend in the side panel shows or hides individual values.

Colours follow the app's validated categorical palette: the eight most
frequent values get the eight hues in fixed order and everything else is a
neutral "Other" -- the legend always names what each colour means.
"""

from __future__ import annotations

import bisect
import math
from array import array
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QScrollBar, QSlider, QSplitter, QToolTip, QVBoxLayout, QWidget,
)

from ...mining.stats import format_duration
from . import style
from .documents import LogDocument
from .widgets import (
    suggested_path,
    Card, ElidedLabel, button, flow, hbox, label, modifier_text, scroll, shortcut_text, vbox,
)

X_MODES = ["Actual time", "Time since case start", "Relative to case duration (%)",
           "Logical: order in log", "Logical: position in case"]
#: Axis titles for each x mode.
X_TITLES = ["Time", "Time since case start", "Share of case duration (%)",
            "Event order in the log", "Event position in the case"]
#: Time units for the x axis, as in ProM's dotted chart: with a unit chosen
#: the axis shows plain numbers in that unit ("0  5  10 …", titled
#: "… (minutes)"); "Auto" shows clock times / durations instead.  Months and
#: years are fixed lengths (30 and 365 days), because the axis is linear.
TIME_UNITS = {"Auto": None, "Seconds": 1.0, "Minutes": 60.0, "Hours": 3600.0,
              "Days": 86400.0, "Weeks": 604800.0, "Months (30 d)": 2592000.0,
              "Years (365 d)": 31536000.0}
UNIT_SUFFIX = {"Seconds": "s", "Minutes": "min", "Hours": "h", "Days": "d", "Weeks": "wk",
               "Months (30 d)": "mo", "Years (365 d)": "yr"}
SORT_MODES = ["First event", "Last event", "Duration", "Number of events", "Name"]
STANDARD_COMPONENTS = ["Case", "Activity", "Resource", "Lifecycle"]
SHAPES = ["circle", "square", "triangle", "diamond", "cross", "star"]
#: Above this many visible dots they are drawn as round points, a batch per
#: colour, instead of one shape at a time: a million-event log stays a
#: picture of seconds, not minutes.  Shapes come back as soon as a zoom shows fewer.
BATCH_FROM = 40_000


@dataclass
class DotSettings:
    x_mode: int = 0
    rows: str = "Case"
    sort: int = 0
    descending: bool = False
    colour_by: str = "Activity"
    shape_by: str = "None"
    all_events: bool = False        # ignore the classifier's event filter
    dot_size: float = 4.0
    connect: bool = False           # line through the events of each case
    unit: str = "Auto"              # time unit of the x axis (see TIME_UNITS)
    grid_step: float = 0.0          # distance between gridlines in axis units; 0 = automatic
    grid_lines: bool = True         # vertical gridlines
    row_lines: bool = True          # a hairline per row when rows are tall enough
    #: Colours picked in the legend: "<attribute>\x1f<value>" -> "#rrggbb".
    #: Values without an entry keep the default palette.
    colours: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Data preparation (no Qt)
# ---------------------------------------------------------------------------
class _Events:
    """The events a dotted chart shows, in log order, with what it needs of
    each: its case, its index in the case, its time, and any attribute as
    text.  An object log is read through its events; a columnar log (a large
    file) straight from its arrays, so a million-event log is drawn without
    building a million event objects."""

    def __init__(self, log, classifier, all_events: bool) -> None:
        from ...mining.columns import NO_TIME
        self.log, self.classifier = log, classifier
        self.case = array("i")                  # trace index
        self.position = array("i")              # index in trace.events
        self.micros = array("q")                # microseconds since the epoch; NO_TIME when absent
        self.missing = NO_TIME
        if log.columnar:
            store = self.store = log.traces.store
            wanted = None if all_events or classifier.lifecycles is None else \
                {v.lower() for v in classifier.lifecycles}
            lifecycle = store.lifecycle
            self.index = array("q")             # the event's index in the store
            for case in range(store.case_count):
                start, stop = store.offsets[case], store.offsets[case + 1]
                for i in range(start, stop):
                    if wanted is not None:
                        code = lifecycle.codes[i]
                        if code >= 0 and lifecycle.values[code].lower() not in wanted:
                            continue
                    self.case.append(case)
                    self.position.append(i - start)
                    self.index.append(i)
                    self.micros.append(store.micros[i])
            self.events = None
        else:
            self.store = None
            self.events = []
            from ...mining.columns import _micros
            for ti, trace in enumerate(log.traces):
                for position, event in enumerate(trace.events):
                    if not (all_events or classifier.accepts(event)):
                        continue
                    self.case.append(ti)
                    self.position.append(position)
                    self.events.append(event)
                    stamp = event.timestamp
                    self.micros.append(_micros(stamp) if stamp is not None else NO_TIME)

    def __len__(self) -> int:
        return len(self.case)

    def ranks(self) -> array:
        """Each shown event's place among *all* the log's events, ordered by
        time (then case, then position): the x of "Logical: order in log"."""
        from ...mining.columns import _micros
        floor = -(1 << 63)
        everything: list[tuple[int, int, int]] = []
        if self.store is not None:
            store = self.store
            for case in range(store.case_count):
                start = store.offsets[case]
                for i in range(start, store.offsets[case + 1]):
                    m = store.micros[i]
                    everything.append((m if m != self.missing else floor, case, i - start))
        else:
            for ti, trace in enumerate(self.log.traces):
                for position, event in enumerate(trace.events):
                    stamp = event.timestamp
                    everything.append((_micros(stamp) if stamp is not None else floor, ti, position))
        everything.sort()
        place = {(case, position): rank for rank, (_m, case, position) in enumerate(everything)}
        return array("q", (place[(c, p)] for c, p in zip(self.case, self.position)))

    def timestamp(self, k: int):
        """The shown event ``k``'s timestamp, with the offset it was written with."""
        if self.store is not None:
            return self.store.timestamp(self.index[k])
        return self.events[k].timestamp

    def values(self, component: str) -> list[str]:
        """``component`` (Case, Activity, Resource, Lifecycle, None or an
        attribute) as text for every shown event."""
        if component == "None":
            return [""] * len(self)
        if self.store is not None:
            store, index = self.store, self.index
            if component == "Case":
                return [store.case_ids[c] for c in self.case]
            if component == "Activity":
                labels = self._labels()
                return [labels[i] for i in index]
            key = {"Resource": "org:resource", "Lifecycle": "lifecycle:transition"}.get(component, component)
            column = store.column_text(key)
            return [column[i] or "–" for i in index]
        classifier, traces = self.classifier, self.log.traces
        out = []
        for k, event in enumerate(self.events):
            if component == "Case":
                out.append(traces[self.case[k]].case_id)
            elif component == "Activity":
                out.append(classifier.label(event) if classifier.accepts(event) else str(event.activity or "–"))
            elif component == "Resource":
                out.append(str(event.resource or "–"))
            elif component == "Lifecycle":
                out.append(str(event.lifecycle or "–"))
            else:
                raw = event.get(component)
                out.append("–" if raw is None else str(raw))
        return out

    def _labels(self) -> list[str]:
        """Every stored event's activity label under the classifier (the
        plain activity for an event the classifier leaves out)."""
        store, classifier = self.store, self.classifier
        names = store.column_text("concept:name")
        if tuple(classifier.keys) == ("concept:name",):
            return [name or "" for name in names]        # the usual classifier: the names themselves
        columns = [store.column_text(key) for key in classifier.keys]
        lifecycle = store.lifecycle
        wanted = None if classifier.lifecycles is None else {v.lower() for v in classifier.lifecycles}
        labels = []
        for i in range(store.event_count):
            code = lifecycle.codes[i]
            if wanted is not None and code >= 0 and lifecycle.values[code].lower() not in wanted:
                labels.append(names[i] or "–")
            else:
                labels.append("+".join(column[i] or "" for column in columns))
        return labels


class DotData:
    """All dots of a log under given settings, ready for drawing.

    Parallel arrays keep the per-dot data compact: ``xs[i]``, ``rows[i]``,
    ``colour[i]`` (index into ``colour_values``), ``shape[i]``, ``case[i]``
    (trace index) and ``position[i]`` (the event's index in its trace).
    """

    def __init__(self, document: LogDocument, settings: DotSettings) -> None:
        self.settings = settings
        log = document.log
        classifier = document.classifier
        s = settings
        events = _Events(log, classifier, s.all_events)
        micros, case, missing = events.micros, events.case, events.missing

        # 1. Select events and compute raw x values -------------------------
        earliest = None                          # the shown event with the earliest timestamp
        for k, m in enumerate(micros):
            if m != missing and (earliest is None or m < micros[earliest]):
                earliest = k
        self.has_time = earliest is not None
        origin = micros[earliest] if earliest is not None else None
        # The axis shows clock times in the offset the log was written with.
        self.origin = events.timestamp(earliest) if earliest is not None else None
        mode = s.x_mode if self.has_time or s.x_mode >= 3 else 4

        # Each case's first and last shown timestamp (for modes 1 and 2).
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        if mode in (1, 2):
            for k, m in enumerate(micros):
                if m == missing:
                    continue
                c = case[k]
                if c not in first or m < first[c]:
                    first[c] = m
                if c not in last or m > last[c]:
                    last[c] = m
        rank = events.ranks() if mode == 3 else None   # the event's place in the whole log, by time

        kept: list[int] = []                    # shown events that have an x
        xs = array("d")
        position_in_shown: dict[int, int] = {}
        for k in range(len(events)):
            m = micros[k]
            if mode == 4:
                c = case[k]
                x = float(position_in_shown.get(c, 0))
                position_in_shown[c] = x + 1
            elif mode == 3:
                x = float(rank[k])
            elif m == missing:
                continue
            elif mode == 0:
                x = (m - origin) / 1e6
            elif mode == 1:
                x = (m - first[case[k]]) / 1e6
            else:
                span = (last[case[k]] - first[case[k]]) / 1e6       # seconds, as timedelta.total_seconds()
                x = 100.0 * ((m - first[case[k]]) / 1e6) / span if span else 0.0
            kept.append(k)
            xs.append(x)
        self.mode = mode

        def component(name: str) -> list[str]:
            values = events.values(name)
            return [values[k] for k in kept]
        row_of, colour_of, shape_of = component(s.rows), component(s.colour_by), component(s.shape_by)

        # 2. Rows: one per distinct component value, sorted ------------------
        stats: dict[str, list[float]] = {}          # value -> [first, last, count]
        for x, row_value in zip(xs, row_of):
            entry = stats.get(row_value)
            if entry is None:
                stats[row_value] = [x, x, 1]
            else:
                if x < entry[0]:
                    entry[0] = x
                if x > entry[1]:
                    entry[1] = x
                entry[2] += 1
        keys = {
            0: lambda v: (stats[v][0], _natural(v)),
            1: lambda v: (stats[v][1], _natural(v)),
            2: lambda v: (stats[v][1] - stats[v][0], _natural(v)),
            3: lambda v: (stats[v][2], _natural(v)),
            4: lambda v: _natural(v),
        }
        self.row_values = sorted(stats, key=keys[s.sort], reverse=s.descending)
        row_index = {v: i for i, v in enumerate(self.row_values)}
        self.row_stats = stats

        # 3. Colour and shape assignments, by frequency ----------------------
        def ranked(values: list[str]) -> list[str]:
            counts: dict[str, int] = {}
            for value in values:
                counts[value] = counts.get(value, 0) + 1
            return sorted(counts, key=lambda v: (-counts[v], _natural(v)))
        self.colour_values = ranked(colour_of)
        self.shape_values = ranked(shape_of)
        colour_index = {v: i for i, v in enumerate(self.colour_values)}
        shape_index = {v: i for i, v in enumerate(self.shape_values)}

        self.xs = xs
        self.rows = array("i", (row_index[v] for v in row_of))
        self.colour = array("i", (colour_index[v] for v in colour_of))
        self.shape = array("i", (shape_index[v] for v in shape_of))
        self.case = array("i", (case[k] for k in kept))
        self.position = array("i", (events.position[k] for k in kept))
        self.colour_counts = {v: 0 for v in self.colour_values}
        for v in colour_of:
            self.colour_counts[v] += 1
        self.x_min = min(self.xs, default=0.0)
        self.x_max = max(self.xs, default=1.0)
        if self.x_max <= self.x_min:
            self.x_max = self.x_min + 1.0
        self.traces = log.traces
        self.classifier = classifier

    def __len__(self) -> int:
        return len(self.xs)

    def case_duration(self, case: int) -> float | None:
        """Seconds from a case's first to its last timestamp (None: fewer
        than two), from the arrays when the log is columnar."""
        if self.traces.__class__.__name__ == "LazyTraces" and not self.traces.modified:
            store = self.traces.store
            span = store.span(case)
            if span is None or span[0] == span[1]:
                return None
            return (store.micros[span[1]] - store.micros[span[0]]) / 1e6
        stamps = [e.timestamp for e in self.traces[case].events if e.timestamp is not None]
        return (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else None

    def event(self, i: int):
        """The event behind dot ``i`` (builds only its trace, for a large log)."""
        return self.traces[self.case[i]].events[self.position[i]]

    def colour_slot(self, colour_index: int) -> int:
        """Palette slot for a colour value (slots 0-7, then 'Other')."""
        return colour_index if colour_index < 8 else 99

    def colour_key(self, colour_index: int) -> str:
        return f"{self.settings.colour_by}\x1f{self.colour_values[colour_index]}"

    def colour_hex(self, colour_index: int) -> str:
        """The dot colour of a value: the one picked in the legend, if any,
        else the palette's."""
        custom = self.settings.colours.get(self.colour_key(colour_index))
        return custom or style.categorical(self.colour_slot(colour_index))


# ---------------------------------------------------------------------------
# The chart widget
# ---------------------------------------------------------------------------
class DottedChart(QWidget):
    """Draws a :class:`DotData` inside a zoomable window over its full extent."""

    view_changed = Signal()
    case_selected = Signal(int)            # trace index, or -1

    MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT, MARGIN_BOTTOM = 74, 10, 14, 46

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.data: DotData | None = None
        self.hidden_colours: set[int] = set()
        self.selected_case = -1
        # the visible window, in data units: x range and row range
        self.vx0 = self.vx1 = 0.0
        self.vy0 = self.vy1 = 0.0
        self.history: list[tuple[float, float, float, float]] = []
        #: A common x range forced on several charts (the Compare view), so
        #: equal distances mean equal times in every chart.
        self.shared_x: tuple[float, float] | None = None
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None
        self._panning = False
        self._pan_origin: tuple[QPointF, tuple[float, float, float, float]] | None = None
        self._cache: QPixmap | None = None
        self._cache_key = None
        #: Dots by screen cell for hit-testing (None: build from _grid_dots on first hover).
        self._grid: dict[tuple[int, int], list[int]] | None = {}
        self._grid_dots: list[int] = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(QSize(240, 200))
        self.grabGesture(Qt.PinchGesture)

    # -- data and view window -------------------------------------------------
    def set_data(self, data: DotData, keep_view: bool = False) -> None:
        self.data = data
        self._invalidate()
        if not keep_view:
            self.history.clear()
            self.reset_view(record=False)
        else:
            self.update()

    def full_extent(self) -> tuple[float, float, float, float]:
        d = self.data
        if d is None:
            return 0.0, 1.0, 0.0, 1.0
        x_min, x_max = self.shared_x or (d.x_min, d.x_max)
        pad = (x_max - x_min) * 0.01
        return x_min - pad, x_max + pad, 0.0, float(max(len(d.row_values), 1))

    def reset_view(self, record: bool = True) -> None:
        self.set_view(*self.full_extent(), record=record)

    def set_view(self, x0: float, x1: float, y0: float, y1: float, record: bool = True) -> None:
        fx0, fx1, fy0, fy1 = self.full_extent()
        # Keep at least a sliver visible and stay inside the data.
        min_w = (fx1 - fx0) / 5000
        if x1 - x0 < min_w:
            middle = (x0 + x1) / 2
            x0, x1 = middle - min_w / 2, middle + min_w / 2
        if y1 - y0 < 0.5:
            middle = (y0 + y1) / 2
            y0, y1 = middle - 0.25, middle + 0.25
        width, height = min(x1 - x0, fx1 - fx0), min(y1 - y0, fy1 - fy0)
        x0 = min(max(x0, fx0), fx1 - width)
        y0 = min(max(y0, fy0), fy1 - height)
        new = (x0, x0 + width, y0, y0 + height)
        if record and new != (self.vx0, self.vx1, self.vy0, self.vy1):
            self.history.append((self.vx0, self.vx1, self.vy0, self.vy1))
            del self.history[:-50]
        self.vx0, self.vx1, self.vy0, self.vy1 = new
        self._invalidate()
        self.view_changed.emit()

    def back(self) -> None:
        if self.history:
            self.vx0, self.vx1, self.vy0, self.vy1 = self.history.pop()
            self._invalidate()
            self.view_changed.emit()

    def zoom_factors(self) -> tuple[float, float]:
        fx0, fx1, fy0, fy1 = self.full_extent()
        return (fx1 - fx0) / (self.vx1 - self.vx0), (fy1 - fy0) / (self.vy1 - self.vy0)

    def set_zoom(self, fx: float | None = None, fy: float | None = None) -> None:
        """Zoom to absolute factors, keeping the centre of the view."""
        full = self.full_extent()
        cx, cy = (self.vx0 + self.vx1) / 2, (self.vy0 + self.vy1) / 2
        w = (full[1] - full[0]) / fx if fx else self.vx1 - self.vx0
        h = (full[3] - full[2]) / fy if fy else self.vy1 - self.vy0
        self.set_view(cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2, record=False)

    def zoom_at(self, point: QPointF, fx: float, fy: float) -> None:
        """Zoom by factors around a screen point (the data under it stays put)."""
        rect = self.plot_rect()
        ax = (point.x() - rect.left()) / rect.width()
        ay = (point.y() - rect.top()) / rect.height()
        ax, ay = min(max(ax, 0), 1), min(max(ay, 0), 1)
        x = self.vx0 + ax * (self.vx1 - self.vx0)
        y = self.vy0 + ay * (self.vy1 - self.vy0)
        w = (self.vx1 - self.vx0) / fx
        h = (self.vy1 - self.vy0) / fy
        self.set_view(x - ax * w, x + (1 - ax) * w, y - ay * h, y + (1 - ay) * h, record=False)

    def pan_by(self, dx_fraction: float, dy_fraction: float) -> None:
        w, h = self.vx1 - self.vx0, self.vy1 - self.vy0
        self.set_view(self.vx0 + dx_fraction * w, self.vx1 + dx_fraction * w,
                      self.vy0 + dy_fraction * h, self.vy1 + dy_fraction * h, record=False)

    # -- geometry --------------------------------------------------------------
    def plot_rect(self) -> QRectF:
        left = self.MARGIN_LEFT + self._label_width()
        return QRectF(left, self.MARGIN_TOP, max(self.width() - left - self.MARGIN_RIGHT, 20),
                      max(self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM, 20))

    def _label_width(self) -> float:
        """Extra left margin for row names when they are readable."""
        if self.data is None or self._row_height_estimate() < 11:
            return 0.0
        metrics = QFontMetricsF(self._small_font())
        widest = max((metrics.horizontalAdvance(v) for v in self._visible_row_values()), default=0)
        return min(widest + 6, 150.0) - 40 if widest > 34 else 0.0

    def _row_height_estimate(self) -> float:
        height = max(self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM, 20)
        return height / max(self.vy1 - self.vy0, 1e-9)

    def _visible_row_values(self) -> list[str]:
        if self.data is None:
            return []
        first, last = int(math.floor(self.vy0)), int(math.ceil(self.vy1))
        return self.data.row_values[max(first, 0):min(last, len(self.data.row_values))]

    def to_screen(self, x: float, row: float, rect: QRectF) -> QPointF:
        sx = rect.left() + (x - self.vx0) / (self.vx1 - self.vx0) * rect.width()
        sy = rect.top() + (row + 0.5 - self.vy0) / (self.vy1 - self.vy0) * rect.height()
        return QPointF(sx, sy)

    def to_data(self, point: QPointF) -> tuple[float, float]:
        rect = self.plot_rect()
        x = self.vx0 + (point.x() - rect.left()) / rect.width() * (self.vx1 - self.vx0)
        y = self.vy0 + (point.y() - rect.top()) / rect.height() * (self.vy1 - self.vy0)
        return x, y

    @staticmethod
    def _small_font():
        from .. import theme
        return theme.ui_font(10)

    # -- painting ----------------------------------------------------------------
    def _invalidate(self) -> None:
        self._cache = None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        key = (self.width(), self.height(), self.vx0, self.vx1, self.vy0, self.vy1,
               tuple(sorted(self.hidden_colours)), self.selected_case, id(self.data),
               self.devicePixelRatioF(), style.tokens().surface)
        if self._cache is None or self._cache_key != key:
            self._cache = self._render()
            self._cache_key = key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        # Rubber band on top of the cached picture: dragging stays fluid.
        if self._drag_start is not None and self._drag_now is not None and not self._panning:
            t = style.tokens()
            band = QRectF(self._drag_start, self._drag_now).normalized()
            fill = QColor(t.accent)
            fill.setAlphaF(0.12)
            painter.setPen(QPen(QColor(t.accent), 1, Qt.DashLine))
            painter.setBrush(fill)
            painter.drawRect(band)

    def _render(self) -> QPixmap:
        t = style.tokens()
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(t.surface))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        self.paint_chart(painter, QRectF(0, 0, self.width(), self.height()))
        painter.end()
        return pixmap

    def paint_chart(self, painter: QPainter, area: QRectF) -> None:
        """Draw axes and dots.  Also used for image export."""
        t = style.tokens()
        d = self.data
        self._grid = {}
        if d is None or not len(d):
            painter.setPen(QColor(t.text_muted))
            painter.drawText(area, Qt.AlignCenter, "No events to show with these settings "
                             "(does the log have timestamps?)")
            return
        s = d.settings
        rect = self.plot_rect()
        self._paint_axes(painter, rect, t, s.grid_lines)

        row_h = rect.height() / max(self.vy1 - self.vy0, 1e-9)
        radius = max(1.6, min(s.dot_size, row_h * 0.45 if row_h < 2.2 * s.dot_size else s.dot_size))
        ring = 1.2 if radius >= 3.5 else 0.0
        selected = self.selected_case
        painter.save()
        painter.setClipRect(rect.adjusted(-radius - 1, -radius - 1, radius + 1, radius + 1))

        # Visible dots, grouped for drawing order and cheap hit-testing later.
        visible: list[int] = []
        x0, x1, y0, y1 = self.vx0, self.vx1, self.vy0 - 1, self.vy1
        for i in range(len(d.xs)):
            if d.colour[i] in self.hidden_colours:
                continue
            x, row = d.xs[i], d.rows[i]
            if x0 <= x <= x1 and y0 <= row <= y1:
                visible.append(i)

        if s.connect:
            self._paint_connections(painter, rect, visible, t)

        colours = [QColor(d.colour_hex(c)) for c in range(len(d.colour_values))]
        faded = QColor(t.border)
        ring_pen = QPen(QColor(t.surface), ring) if ring else Qt.NoPen
        cell = 8
        # Unselected first, the selected case last so it sits on top.
        ordered = sorted(visible, key=lambda i: d.case[i] == selected) if selected >= 0 else visible
        if len(ordered) > BATCH_FROM:
            self._paint_batched(painter, rect, ordered, colours, faded, radius, selected)
            painter.restore()
            self._visible_count = len(visible)
            return
        for i in ordered:
            point = self.to_screen(d.xs[i], d.rows[i], rect)
            is_dim = selected >= 0 and d.case[i] != selected
            painter.setPen(ring_pen)
            painter.setBrush(faded if is_dim else colours[d.colour[i]])
            _draw_shape(painter, SHAPES[d.shape[i]] if d.shape[i] < len(SHAPES) else "ring",
                        point, radius)
            self._grid.setdefault((int(point.x()) // cell, int(point.y()) // cell), []).append(i)
        painter.restore()
        self._visible_count = len(visible)

    def _paint_batched(self, painter: QPainter, rect: QRectF, ordered: list[int], colours: list[QColor],
                       faded: QColor, radius: float, selected: int) -> None:
        """Many dots: one polyline of points per colour, with the mapping to
        the screen done inline.  The hover index is built on the first hover."""
        d = self.data
        left, top, width, height = rect.left(), rect.top(), rect.width(), rect.height()
        sx = width / (self.vx1 - self.vx0)
        sy = height / (self.vy1 - self.vy0)
        x0, y0 = self.vx0, self.vy0 - 0.5
        xs, rows, colour, case = d.xs, d.rows, d.colour, d.case
        # -1: the dimmed dots of the other cases when one is selected; drawn first.
        batches: dict[int, list[QPointF]] = {}
        for i in ordered:
            point = QPointF(left + (xs[i] - x0) * sx, top + (rows[i] - y0) * sy)
            dimmed = selected >= 0 and case[i] != selected
            batches.setdefault(-1 if dimmed else colour[i], []).append(point)
        painter.setRenderHint(QPainter.Antialiasing, radius > 1.5)
        for key in sorted(batches):
            pen = QPen(faded if key < 0 else colours[key], 2 * radius)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(QPolygonF(batches[key]))
        self._grid = None
        self._grid_dots = ordered

    def _hover_grid(self) -> dict[tuple[int, int], list[int]]:
        """The dots by screen cell, for hit-testing; built here, lazily, when
        the picture was drawn in batches."""
        if self._grid is None:
            grid: dict[tuple[int, int], list[int]] = {}
            rect, d, cell = self.plot_rect(), self.data, 8
            for i in self._grid_dots:
                point = self.to_screen(d.xs[i], d.rows[i], rect)
                grid.setdefault((int(point.x()) // cell, int(point.y()) // cell), []).append(i)
            self._grid = grid
        return self._grid

    def _paint_connections(self, painter: QPainter, rect: QRectF, visible: list[int], t) -> None:
        d = self.data
        by_case: dict[int, list[int]] = {}
        for i in visible:
            by_case.setdefault(d.case[i], []).append(i)
        pen_colour = QColor(t.text_muted)
        pen_colour.setAlphaF(0.35)
        painter.setPen(QPen(pen_colour, 1))
        for case, indices in by_case.items():
            if len(indices) < 2:
                continue
            indices.sort(key=lambda i: d.xs[i])
            polygon = QPolygonF([self.to_screen(d.xs[i], d.rows[i], rect) for i in indices])
            if case == self.selected_case:
                painter.save()
                painter.setPen(QPen(QColor(t.accent), 1.6))
                painter.drawPolyline(polygon)
                painter.restore()
            else:
                painter.drawPolyline(polygon)

    def _paint_axes(self, painter: QPainter, rect: QRectF, t, grid: bool) -> None:
        font = self._small_font()
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        d = self.data
        # vertical gridlines; labels only where they do not collide
        ticks = self._x_ticks(rect.width())
        last_label_right = -1e9
        for value, text in ticks:
            x = rect.left() + (value - self.vx0) / (self.vx1 - self.vx0) * rect.width()
            if grid:
                painter.setPen(QPen(QColor(t.grid), 1))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            w = metrics.horizontalAdvance(text) + 8
            if x - w / 2 > last_label_right + 6:
                painter.setPen(QColor(t.text_muted))
                painter.drawText(QRectF(x - w / 2, rect.bottom() + 5, w, 16), Qt.AlignCenter, text)
                last_label_right = x + w / 2
        painter.setPen(QPen(QColor(t.axis), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topLeft(), rect.bottomLeft())

        # axis titles, in the muted text colour
        title_font = self._small_font()
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(t.text_secondary))
        x_title = axis_title(d.mode, d.settings.unit)
        if d.mode == 0 and d.origin is not None:
            x_title += f"  (from {d.origin:%d %b %Y %H:%M:%S})"
        painter.drawText(QRectF(rect.left(), rect.bottom() + 24, rect.width(), 16),
                         Qt.AlignCenter, x_title)
        painter.save()
        painter.translate(11, rect.center().y())
        painter.rotate(-90)
        y_title = d.settings.rows if d.settings.rows != "Case" else "Cases"
        painter.drawText(QRectF(-rect.height() / 2, -8, rect.height(), 16), Qt.AlignCenter,
                         f"{y_title}  ({len(d.row_values):,})")
        painter.restore()
        painter.setFont(font)

        # rows: names when there is room, otherwise counts
        row_h = rect.height() / max(self.vy1 - self.vy0, 1e-9)
        first, last = max(int(math.floor(self.vy0)), 0), min(int(math.ceil(self.vy1)), len(d.row_values))
        painter.setPen(QColor(t.text_muted))
        label_left = 22.0
        if row_h >= 11:
            width = rect.left() - 6 - label_left
            for row in range(first, last):
                y = rect.top() + (row + 0.5 - self.vy0) * row_h
                if d.settings.row_lines and row_h >= 16:
                    painter.save()
                    painter.setPen(QPen(QColor(t.grid), 1))
                    painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                    painter.restore()
                text = metrics.elidedText(d.row_values[row], Qt.ElideMiddle, width - 2)
                painter.drawText(QRectF(label_left, y - 8, width, 16),
                                 Qt.AlignRight | Qt.AlignVCenter, text)
        else:
            span = self.vy1 - self.vy0
            step = _nice_step(span / 6)
            value = math.ceil(self.vy0 / step) * step
            while value <= self.vy1:
                y = rect.top() + (value - self.vy0) * row_h
                painter.drawText(QRectF(label_left, y - 8, rect.left() - 6 - label_left, 16),
                                 Qt.AlignRight | Qt.AlignVCenter, f"{int(value):,}")
                value += step

    def _x_ticks(self, pixels: float) -> list[tuple[float, str]]:
        """Gridline positions (in data units) and their labels.

        With a grid step set, lines go exactly every ``grid_step`` axis units
        (never closer than 4 px, so zooming far out does not turn the chart
        grey); otherwise a round step giving a line every ~110 px.
        """
        d = self.data
        s = d.settings
        span = self.vx1 - self.vx0
        count = max(int(pixels / 110), 2)
        unit = axis_unit(d.mode, s.unit)            # data units per axis unit
        if s.grid_step > 0:
            step = max(s.grid_step * unit, span / max(pixels / 4, 1))
        elif d.mode in (0, 1) and s.unit == "Auto":
            step = _nice_time_step(span / count)
        else:
            step = _nice_step(span / unit / count) * unit
            if d.mode >= 3:
                step = max(step, 1.0)
        first = math.ceil(self.vx0 / step - 1e-9) * step
        return [(v, self._format_tick(v, step, span)) for v in _frange(first, self.vx1, step)]

    def _format_tick(self, v: float, step: float, span: float) -> str:
        d = self.data
        if d.mode >= 3:
            return f"{round(v):,}"
        if d.mode == 2:
            return _number(v) + "%"
        if d.settings.unit != "Auto":
            return _number(v / TIME_UNITS[d.settings.unit])
        if d.mode == 0 and d.origin is not None:
            moment = d.origin + timedelta(seconds=v)
            if step >= 86400:
                return moment.strftime("%d %b %Y" if span > 86400 * 300 else "%d %b")
            if step >= 60:
                return moment.strftime("%d %b %H:%M" if span > 86400 else "%H:%M")
            return moment.strftime("%H:%M:%S")
        return "+" + format_duration(v) if v else "0"

    # -- hit testing ------------------------------------------------------------------
    def dot_at(self, point: QPointF, tolerance: float = 7.0) -> int | None:
        if self.data is None:
            return None
        rect = self.plot_rect()
        cell = 8
        cx, cy = int(point.x()) // cell, int(point.y()) // cell
        best, best_distance = None, tolerance
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in self._hover_grid().get((gx, gy), []):
                    screen = self.to_screen(self.data.xs[i], self.data.rows[i], rect)
                    distance = math.hypot(screen.x() - point.x(), screen.y() - point.y())
                    if distance <= best_distance:
                        best, best_distance = i, distance
        return best

    def describe_dot(self, i: int) -> str:
        d = self.data
        event = d.event(i)
        trace = d.traces[d.case[i]]
        lines = [f"<b>{d.classifier.label(event) if d.classifier.accepts(event) else event.activity}</b>",
                 f"case {trace.case_id}"]
        if event.timestamp:
            lines.append(event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
        for key, value in list(event.attributes.items())[:10]:
            if key not in ("concept:name", "time:timestamp"):
                text = str(value)
                lines.append(f"{key}: {text[:60] + '…' if len(text) > 60 else text}")
        return "<br>".join(lines)

    # -- interaction ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setFocus()
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and
                                                  event.modifiers() & Qt.AltModifier):
            self._panning = True
            self._pan_origin = (event.position(), (self.vx0, self.vx1, self.vy0, self.vy1))
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            self._drag_start = event.position()
            self._drag_now = event.position()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning and self._pan_origin is not None:
            origin, (x0, x1, y0, y1) = self._pan_origin
            rect = self.plot_rect()
            dx = (event.position().x() - origin.x()) / rect.width() * (x1 - x0)
            dy = (event.position().y() - origin.y()) / rect.height() * (y1 - y0)
            self.set_view(x0 - dx, x1 - dx, y0 - dy, y1 - dy, record=False)
            return
        if self._drag_start is not None:
            self._drag_now = event.position()
            self.update()
            return
        index = self.dot_at(event.position())
        if index is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPosition().toPoint(), self.describe_dot(index), self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            self._panning = False
            self._pan_origin = None
            self.unsetCursor()
            return
        if self._drag_start is None:
            return
        start, end = self._drag_start, event.position()
        self._drag_start = self._drag_now = None
        if abs(end.x() - start.x()) < 6 and abs(end.y() - start.y()) < 6:
            # A click, not a drag: select (or deselect) the case under the pointer.
            index = self.dot_at(end)
            self.select_case(self.data.case[index] if index is not None else -1)
            return
        (ax, ay), (bx, by) = self.to_data(start), self.to_data(end)
        self.set_view(min(ax, bx), max(ax, bx), min(ay, by), max(ay, by))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.reset_view()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta()
        modifiers = event.modifiers()
        if modifiers & (Qt.ControlModifier | Qt.MetaModifier):
            factor = 1.0015 ** delta.y()
            only_rows = bool(modifiers & Qt.ShiftModifier)
            self.zoom_at(event.position(), 1.0 if only_rows else factor, factor)
        else:
            # Trackpads send both axes; Shift turns a vertical wheel into horizontal.
            dx, dy = delta.x(), delta.y()
            if modifiers & Qt.ShiftModifier and dx == 0:
                dx, dy = dy, 0
            self.pan_by(-dx / 1200, -dy / 1200)
        event.accept()

    def event(self, event) -> bool:
        if event.type() == event.Type.NativeGesture:
            try:
                if event.gestureType() == Qt.ZoomNativeGesture:
                    factor = 1 + event.value()
                    self.zoom_at(event.position(), factor, factor)
                    return True
            except AttributeError:  # pragma: no cover
                pass
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.select_case(-1)
        elif key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.back()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_at(QPointF(self.plot_rect().center()), 1.5, 1.5)
        elif key == Qt.Key_Minus:
            self.zoom_at(QPointF(self.plot_rect().center()), 1 / 1.5, 1 / 1.5)
        elif key == Qt.Key_0:
            self.reset_view()
        else:
            super().keyPressEvent(event)

    def select_case(self, case: int) -> None:
        self.selected_case = case
        self._invalidate()
        self.case_selected.emit(case)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate()
        super().resizeEvent(event)

    # -- export ----------------------------------------------------------------------------
    def export_image(self, path: str) -> None:
        if path.lower().endswith(".svg"):
            from PySide6.QtSvg import QSvgGenerator
            generator = QSvgGenerator()
            generator.setFileName(path)
            generator.setSize(self.size())
            generator.setViewBox(QRectF(0, 0, self.width(), self.height()))
            painter = QPainter(generator)
            painter.fillRect(self.rect(), QColor(style.tokens().surface))
            self.paint_chart(painter, QRectF(self.rect()))
            painter.end()
        else:
            image = QImage(self.width() * 2, self.height() * 2, QImage.Format_ARGB32)
            image.setDevicePixelRatio(2)
            image.fill(QColor(style.tokens().surface))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            self.paint_chart(painter, QRectF(self.rect()))
            painter.end()
            image.save(path)


def _draw_shape(painter: QPainter, shape: str, centre: QPointF, r: float) -> None:
    x, y = centre.x(), centre.y()
    if shape == "circle":
        painter.drawEllipse(centre, r, r)
    elif shape == "square":
        painter.drawRect(QRectF(x - r * 0.9, y - r * 0.9, r * 1.8, r * 1.8))
    elif shape == "triangle":
        painter.drawPolygon(QPolygonF([QPointF(x, y - r * 1.15), QPointF(x + r * 1.05, y + r * 0.8),
                                       QPointF(x - r * 1.05, y + r * 0.8)]))
    elif shape == "diamond":
        painter.drawPolygon(QPolygonF([QPointF(x, y - r * 1.25), QPointF(x + r * 1.1, y),
                                       QPointF(x, y + r * 1.25), QPointF(x - r * 1.1, y)]))
    elif shape == "cross":
        path = QPainterPath()
        w = r * 0.42
        path.addRect(QRectF(x - r, y - w, 2 * r, 2 * w))
        path.addRect(QRectF(x - w, y - r, 2 * w, 2 * r))
        painter.drawPath(path.simplified())
    elif shape == "star":
        points = []
        for k in range(10):
            radius = r * 1.25 if k % 2 == 0 else r * 0.55
            angle = -math.pi / 2 + k * math.pi / 5
            points.append(QPointF(x + radius * math.cos(angle), y + radius * math.sin(angle)))
        painter.drawPolygon(QPolygonF(points))
    else:  # "Other": hollow ring in the fill colour
        brush_colour = painter.brush().color()
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(brush_colour, 1.4))
        painter.drawEllipse(centre, r * 0.9, r * 0.9)
        painter.restore()


# ---------------------------------------------------------------------------
# The complete tab: chart, navigation bar, settings, legend, statistics
# ---------------------------------------------------------------------------
class DottedChartPanel(QWidget):
    #: The most recent configuration, shared by every dotted chart: switching
    #: to another log's dotted chart shows it the same way (like-for-like
    #: comparison), while each chart keeps its own zoom and selection.
    shared: DotSettings = DotSettings()

    def __init__(self, document: LogDocument, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.settings = replace(DottedChartPanel.shared)
        self._last_x_mode = self.settings.x_mode
        self.chart = DottedChart()

        # -- navigation bar above the chart
        back = button("‹ Back", self.chart.back, tooltip=f"Undo the last zoom ({shortcut_text('Backspace')})")
        reset = button("Reset", self.chart.reset_view, tooltip="Show everything (double-click, 0)")
        zoom_in = button("＋", lambda: self.chart.zoom_at(QPointF(self.chart.plot_rect().center()),
                                                        1.5, 1.5), tooltip="Zoom in (+)")
        zoom_out = button("－", lambda: self.chart.zoom_at(QPointF(self.chart.plot_rect().center()),
                                                         1 / 1.5, 1 / 1.5), tooltip="Zoom out (−)")
        export = button("Export image…", self._export)
        self.view_info = ElidedLabel("", "muted")
        hint = label(f"Drag to zoom into an area · {modifier_text('Ctrl')}-scroll or pinch to zoom · "
                     f"{modifier_text('Alt')}-drag or middle-drag to pan · "
                     "click a dot to select its case", "muted", wrap=True)

        # -- zoom sliders and scroll bars
        self.h_zoom = self._zoom_slider("Horizontal zoom")
        self.v_zoom = self._zoom_slider("Vertical zoom")
        self.h_bar = QScrollBar(Qt.Horizontal)
        self.v_bar = QScrollBar(Qt.Vertical)
        for bar in (self.h_bar, self.v_bar):
            bar.setRange(0, 0)
            bar.valueChanged.connect(self._scrolled)
        self.h_zoom.valueChanged.connect(lambda v: self._slider_zoom())
        self.v_zoom.valueChanged.connect(lambda v: self._slider_zoom())
        self._syncing = False

        chart_area = QWidget()
        grid = QHBoxLayout(chart_area)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        grid.addWidget(self.chart, 1)
        grid.addWidget(self.v_bar)

        chart_card = Card("Dotted chart")
        chart_card.header.addWidget(back)
        chart_card.header.addWidget(reset)
        chart_card.header.addWidget(zoom_out)
        chart_card.header.addWidget(zoom_in)
        chart_card.header.addWidget(export)
        chart_card.add(chart_area, 1)
        chart_card.add(self.h_bar)
        chart_card.add(hbox(label("Zoom  ↔", "muted"), self.h_zoom, 14, label("↕", "muted"),
                            self.v_zoom, None))
        chart_card.add(self.view_info)
        chart_card.add(hint)

        # -- settings side panel
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 4, 0)
        side_layout.setSpacing(12)
        side_layout.addWidget(self._build_settings())
        side_layout.addWidget(self._build_legend())
        side_layout.addWidget(self._build_statistics())
        side_layout.addStretch(1)
        side_scroll = scroll(side)
        side_scroll.setMinimumWidth(304)
        side_scroll.setMaximumWidth(340)

        splitter = QSplitter()
        splitter.setHandleWidth(12)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        splitter.addWidget(chart_card)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([900, 290])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.chart.view_changed.connect(self._sync_navigation)
        self.chart.case_selected.connect(self._show_case)
        self.rebuild()

    # -- settings ------------------------------------------------------------------
    def _combo(self, items: list[str], current: str | int) -> QComboBox:
        box = QComboBox()
        box.addItems(items)
        if isinstance(current, int):
            box.setCurrentIndex(current)
        else:
            box.setCurrentText(current)
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(6)
        box.currentIndexChanged.connect(lambda _: self._settings_changed())
        return box

    def _build_settings(self) -> QWidget:
        log = self.document.log
        extra = sorted({k for t in log.traces[:500] for e in t.events for k in e.attributes}
                       - {"concept:name", "time:timestamp", "lifecycle:transition", "org:resource"})
        components = STANDARD_COMPONENTS + extra
        s = self.settings
        self.x_box = self._combo(X_MODES, s.x_mode)
        self.rows_box = self._combo(components, s.rows)
        self.sort_box = self._combo(SORT_MODES, s.sort)
        self.colour_box = self._combo(components + ["None"], s.colour_by)
        self.shape_box = self._combo(["None"] + components, s.shape_by)
        self.descending = QCheckBox("Descending")
        self.all_events = QCheckBox("Show skipped events")
        self.all_events.setToolTip("E.g. show 'start' events too when the classifier keeps "
                                   "only 'complete' ones")
        self.connect_box = QCheckBox("Connect events of a case")
        # The time unit and the grid step, as in ProM: pick a unit, and the
        # axis counts in it; the grid step is a plain number in that unit.
        self.unit_box = self._combo(list(TIME_UNITS), s.unit)
        self.unit_box.setToolTip("Unit of the x axis. Auto shows clock times and durations; a "
                                 "unit shows plain numbers (e.g. minutes since the case "
                                 "started), as in ProM.")
        self.unit_box.currentIndexChanged.disconnect()
        self.unit_box.currentIndexChanged.connect(lambda _: self._settings_changed(keep_view=True))
        self.grid_step = QDoubleSpinBox()
        self.grid_step.setRange(0.0, 1e6)
        self.grid_step.setDecimals(2)
        self.grid_step.setMinimumWidth(100)          # room for "1,000.00 min", not 10⁹
        self.grid_step.setSpecialValueText("Auto")        # shown at 0
        self.grid_step.setValue(s.grid_step)
        self.grid_step.setToolTip("Distance between the vertical gridlines, in the unit of the "
                                  "x axis. 0 = automatic (a round number that fits the zoom).")
        self.grid_step.valueChanged.connect(lambda _: self._settings_changed(keep_view=True))
        self.grid_lines_box = QCheckBox("Grid lines")
        self.grid_lines_box.setChecked(s.grid_lines)
        self.grid_lines_box.toggled.connect(lambda _: self._settings_changed(keep_view=True))
        self.row_lines_box = QCheckBox("Row lines")
        self.row_lines_box.setChecked(s.row_lines)
        self.descending.setChecked(s.descending)
        self.all_events.setChecked(s.all_events)
        self.connect_box.setChecked(s.connect)
        self.row_lines_box.toggled.connect(lambda _: self._settings_changed(keep_view=True))
        for box in (self.descending, self.all_events, self.connect_box):
            box.toggled.connect(lambda _: self._settings_changed())
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(2, 10)
        self.size_slider.setValue(int(s.dot_size))
        self.size_slider.valueChanged.connect(lambda _: self._settings_changed(keep_view=True))

        self._has_time = any(e.timestamp for t in log.traces[:200] for e in t.events)
        if not self._has_time:
            # No times: only the logical axes make sense.  Switch quietly --
            # this panel is still being built, and a log without times should
            # not change the default axis of the next log's chart.
            self.x_box.blockSignals(True)
            self.x_box.setCurrentIndex(4)
            self.x_box.blockSignals(False)
            self.settings.x_mode = self._last_x_mode = 4
            for index in range(3):
                self.x_box.model().item(index).setEnabled(False)

        card = Card("Configure")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setSpacing(8)
        form.addRow("x axis", self.x_box)
        form.addRow("Rows", self.rows_box)
        form.addRow("Sort rows", self.sort_box)
        form.addRow("", self.descending)
        form.addRow("Colour by", self.colour_box)
        form.addRow("Shape by", self.shape_box)
        form.addRow("Dot size", self.size_slider)
        form.addRow("Time unit", self.unit_box)
        form.addRow("Grid every", self.grid_step)
        card.add(form)
        card.add(hbox(self.grid_lines_box, self.row_lines_box, None))
        card.add(self.all_events)
        card.add(self.connect_box)
        return card

    def _update_axis_controls(self) -> None:
        """The unit only applies to time axes; the grid step's suffix names
        the unit it is measured in."""
        mode = self.x_box.currentIndex()
        is_time = mode in (0, 1)
        self.unit_box.setEnabled(is_time)
        unit = self.unit_box.currentText()
        if is_time:
            suffix = UNIT_SUFFIX.get(unit, "s")
        elif mode == 2:
            suffix = "%"
        else:
            suffix = "events"
        self.grid_step.blockSignals(True)
        self.grid_step.setSuffix(f" {suffix}")
        self.grid_step.setDecimals(0 if mode >= 3 else 2)
        self.grid_step.blockSignals(False)

    def _build_legend(self) -> QWidget:
        card = Card("Legend", "Untick to hide. Double-click to show only that value. "
                    "Right-click a value (or select it and press Colour…) to pick its colour.")
        self.legend = QListWidget()
        self.legend.setMinimumHeight(140)
        self.legend.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.legend.setTextElideMode(Qt.ElideRight)
        self.legend.itemChanged.connect(self._legend_toggled)
        self.legend.itemDoubleClicked.connect(self._legend_isolate)
        self.legend.setContextMenuPolicy(Qt.CustomContextMenu)
        self.legend.customContextMenuRequested.connect(self._legend_menu)
        card.add(self.legend)
        self.colour_button = button("Colour…", self._choose_colour, kind="ghost",
                                    tooltip="Pick the colour of the selected value")
        self.reset_colours_button = button("Reset colours", self._reset_colours, kind="ghost",
                                           tooltip="Back to the default palette")
        card.add(flow(button("Show all", self._legend_show_all, kind="ghost"),
                      self.colour_button, self.reset_colours_button))
        return card

    # -- choosing colours --------------------------------------------------------------------
    def _legend_menu(self, point) -> None:
        from PySide6.QtWidgets import QMenu
        item = self.legend.itemAt(point)
        index = item.data(Qt.UserRole) if item is not None else None
        menu = QMenu(self)
        if index is not None:
            self.legend.setCurrentItem(item)
            menu.addAction("Choose colour…", lambda: self.choose_colour(index))
            key = self.chart.data.colour_key(index)
            reset = menu.addAction("Default colour", lambda: self.set_colour(index, None))
            reset.setEnabled(key in self.settings.colours)
            menu.addSeparator()
        menu.addAction("Reset all colours", self._reset_colours)
        menu.exec(self.legend.viewport().mapToGlobal(point))

    def _choose_colour(self) -> None:
        item = self.legend.currentItem()
        index = item.data(Qt.UserRole) if item is not None else None
        if index is None:
            self._toast("Select a value in the legend first")
            return
        self.choose_colour(index)

    def choose_colour(self, index: int) -> None:
        from PySide6.QtWidgets import QColorDialog
        d = self.chart.data
        value = d.colour_values[index] or "–"
        colour = QColorDialog.getColor(QColor(d.colour_hex(index)), self,
                                       f"Colour of “{value}”")
        if colour.isValid():
            self.set_colour(index, colour.name())

    def set_colour(self, index: int, colour: str | None) -> None:
        """Give a legend value its own dot colour (``None``: the default)."""
        key = self.chart.data.colour_key(index)
        if colour is None:
            self.settings.colours.pop(key, None)
        else:
            self.settings.colours[key] = colour
        self._colours_changed()

    def _reset_colours(self) -> None:
        prefix = f"{self.settings.colour_by}\x1f"
        for key in [k for k in self.settings.colours if k.startswith(prefix)]:
            del self.settings.colours[key]
        self._colours_changed()

    def _colours_changed(self) -> None:
        current = self.legend.currentRow()
        self._fill_legend()
        self.legend.setCurrentRow(current)
        self.chart._invalidate()

    def _toast(self, text: str) -> None:
        QToolTip.showText(self.colour_button.mapToGlobal(self.colour_button.rect().center()),
                          text, self.colour_button)

    def _build_statistics(self) -> QWidget:
        card = Card("Statistics")
        self.stats_label = label("", "muted", wrap=True, selectable=True)
        self.case_label = label("Click a dot to select its case.", "muted", wrap=True,
                                selectable=True)
        card.add(self.stats_label)
        card.add(label("Selected case", "sectionLabel"))
        card.add(self.case_label)
        return card

    def _settings_changed(self, keep_view: bool = False) -> None:
        s = self.settings
        s.x_mode = self.x_box.currentIndex()
        s.rows = self.rows_box.currentText()
        s.sort = self.sort_box.currentIndex()
        s.descending = self.descending.isChecked()
        s.colour_by = self.colour_box.currentText()
        s.shape_by = self.shape_box.currentText()
        s.all_events = self.all_events.isChecked()
        s.dot_size = float(self.size_slider.value())
        s.connect = self.connect_box.isChecked()
        if s.x_mode != self._last_x_mode:
            if self._last_x_mode != -1:
                # A step in minutes means nothing on a percentage axis.
                self.grid_step.blockSignals(True)
                self.grid_step.setValue(0.0)
                self.grid_step.blockSignals(False)
            self._last_x_mode = s.x_mode
        s.unit = self.unit_box.currentText()
        self._update_axis_controls()
        s.grid_step = self.grid_step.value()
        s.grid_lines = self.grid_lines_box.isChecked()
        s.row_lines = self.row_lines_box.isChecked()
        shared = replace(s)                       # new panels start from these settings
        if not self._has_time and DottedChartPanel.shared.x_mode < 3:
            # This log forces a logical axis; that is not a choice to pass on.
            shared.x_mode = DottedChartPanel.shared.x_mode
        DottedChartPanel.shared = shared
        self.rebuild(keep_view)

    def _for_this_log(self, s: DotSettings) -> DotSettings:
        """``s`` as this log can show it: without times, a time axis becomes
        logical order."""
        if not self._has_time and s.x_mode < 3:
            return replace(s, x_mode=4)
        return s

    def rebuild(self, keep_view: bool = False) -> None:
        data = DotData(self.document, self.settings)
        if not keep_view:
            self.chart.hidden_colours = set()
            self.chart.selected_case = -1
            self._show_case(-1)
        self.chart.set_data(data, keep_view=keep_view)
        self._fill_legend()
        self._update_statistics()
        self._sync_navigation()

    def showEvent(self, event) -> None:  # noqa: N802
        """Pick up configuration changes made on another log's dotted chart."""
        super().showEvent(event)
        shared = self._for_this_log(DottedChartPanel.shared)
        if shared != self.settings and self.document.log.traces:
            self._apply_settings_to_controls(shared)

    def _apply_settings_to_controls(self, s: DotSettings) -> None:
        widgets = (self.x_box, self.rows_box, self.sort_box, self.colour_box, self.shape_box,
                   self.descending, self.all_events, self.connect_box, self.size_slider,
                   self.unit_box, self.grid_step, self.grid_lines_box, self.row_lines_box)
        for widget in widgets:
            widget.blockSignals(True)
        self.x_box.setCurrentIndex(self._for_this_log(s).x_mode)
        for box, value in ((self.rows_box, s.rows), (self.colour_box, s.colour_by),
                           (self.shape_box, s.shape_by)):
            index = box.findText(value)
            box.setCurrentIndex(index if index >= 0 else 0)
        self.sort_box.setCurrentIndex(s.sort)
        self.descending.setChecked(s.descending)
        self.all_events.setChecked(s.all_events)
        self.connect_box.setChecked(s.connect)
        self.size_slider.setValue(int(s.dot_size))
        self.row_lines_box.setChecked(s.row_lines)
        self.unit_box.setCurrentIndex(max(self.unit_box.findText(s.unit), 0))
        self.grid_step.setValue(s.grid_step)
        self.grid_lines_box.setChecked(s.grid_lines)
        self._last_x_mode = -1
        for widget in widgets:
            widget.blockSignals(False)
        self._settings_changed()

    # -- legend ------------------------------------------------------------------------------
    def _fill_legend(self) -> None:
        d = self.chart.data
        self.legend.blockSignals(True)
        self.legend.clear()
        if self.settings.colour_by == "None":
            item = QListWidgetItem("All events (colour off)")
            item.setFlags(Qt.ItemIsEnabled)
            self.legend.addItem(item)
        else:
            for index, value in enumerate(d.colour_values):
                slot = d.colour_slot(index)
                name = value if value else "–"
                text = f"{name}   ({d.colour_counts[value]:,})"
                if slot == 99 and d.colour_key(index) not in self.settings.colours:
                    text += "  · Other"
                item = QListWidgetItem(_swatch(d.colour_hex(index)), text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                item.setCheckState(Qt.Unchecked if index in self.chart.hidden_colours else Qt.Checked)
                item.setData(Qt.UserRole, index)
                item.setToolTip(value)
                self.legend.addItem(item)
        self.legend.blockSignals(False)

    def _legend_toggled(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        if index is None:
            return
        if item.checkState() == Qt.Checked:
            self.chart.hidden_colours.discard(index)
        else:
            self.chart.hidden_colours.add(index)
        self.chart._invalidate()
        self._update_statistics()

    def _legend_isolate(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        if index is None:
            return
        d = self.chart.data
        self.chart.hidden_colours = {i for i in range(len(d.colour_values)) if i != index}
        self._fill_legend()
        self.chart._invalidate()
        self._update_statistics()

    def _legend_show_all(self) -> None:
        self.chart.hidden_colours = set()
        self._fill_legend()
        self.chart._invalidate()
        self._update_statistics()

    # -- statistics (like ProM's metric panel) --------------------------------------------
    def _update_statistics(self) -> None:
        d = self.chart.data
        if d is None:
            return
        shown = [i for i in range(len(d.xs)) if d.colour[i] not in self.chart.hidden_colours]
        cases = {d.case[i] for i in shown}
        durations = [duration for duration in map(d.case_duration, cases) if duration is not None]
        lines = [f"{len(shown):,} events in {len(cases):,} cases · {len(d.row_values):,} rows"]
        if durations:
            durations.sort()
            mean = sum(durations) / len(durations)
            lines.append(f"Case duration: mean {format_duration(mean)}, median "
                         f"{format_duration(durations[len(durations) // 2])}, "
                         f"min {format_duration(durations[0])}, max {format_duration(durations[-1])}")
        if d.origin is not None and d.mode == 0:
            lines.append(f"Log starts {d.origin.strftime('%Y-%m-%d %H:%M:%S')}")
        self.stats_label.setText("\n".join(lines))

    def _show_case(self, case: int) -> None:
        if case < 0:
            self.case_label.setText("Click a dot to select its case.")
            return
        d = self.chart.data
        trace = d.traces[case]
        events = trace.events
        stamps = [e.timestamp for e in events if e.timestamp is not None]
        duration = format_duration(max(stamps) - min(stamps)) if stamps else "–"
        sequence = d.classifier and [d.classifier.label(e) for e in events if d.classifier.accepts(e)]
        runs: list[str] = []
        for activity in sequence:
            if runs and runs[-1].split(" ×")[0] == activity:
                base, _, count = runs[-1].partition(" ×")
                runs[-1] = f"{base} ×{int(count or 1) + 1}"
            else:
                runs.append(activity)
        attributes = ", ".join(f"{k}={v}" for k, v in trace.attributes.items()
                               if k != "concept:name")
        self.case_label.setText(
            f"Case {trace.case_id}: {len(events)} events, duration {duration}\n"
            + (f"{attributes}\n" if attributes else "")
            + " → ".join(runs))

    # -- navigation sync -------------------------------------------------------------------
    @staticmethod
    def _zoom_slider(tooltip: str) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 1000)          # log scale: 1× … 1000×
        slider.setToolTip(tooltip)
        slider.setMinimumWidth(60)
        slider.setMaximumWidth(200)
        return slider

    def _slider_zoom(self) -> None:
        if self._syncing:
            return
        fx = 1000 ** (self.h_zoom.value() / 1000)
        fy = 1000 ** (self.v_zoom.value() / 1000)
        self.chart.set_zoom(fx, fy)

    def _scrolled(self) -> None:
        if self._syncing:
            return
        fx0, fx1, fy0, fy1 = self.chart.full_extent()
        w, h = self.chart.vx1 - self.chart.vx0, self.chart.vy1 - self.chart.vy0
        x0 = fx0 + self.h_bar.value() / 10000 * (fx1 - fx0)
        y0 = fy0 + self.v_bar.value() / 10000 * (fy1 - fy0)
        self.chart.set_view(x0, x0 + w, y0, y0 + h, record=False)

    def _sync_navigation(self) -> None:
        chart = self.chart
        if chart.data is None:
            return
        self._syncing = True
        fx0, fx1, fy0, fy1 = chart.full_extent()
        zx, zy = chart.zoom_factors()
        self.h_zoom.setValue(int(round(1000 * math.log(max(zx, 1.0), 1000))))
        self.v_zoom.setValue(int(round(1000 * math.log(max(zy, 1.0), 1000))))
        for bar, lo, hi, v0, v1 in ((self.h_bar, fx0, fx1, chart.vx0, chart.vx1),
                                    (self.v_bar, fy0, fy1, chart.vy0, chart.vy1)):
            span = hi - lo
            page = int(10000 * (v1 - v0) / span) if span else 10000
            bar.setPageStep(max(page, 1))
            bar.setSingleStep(max(page // 10, 1))
            bar.setRange(0, max(10000 - page, 0))
            bar.setValue(int(10000 * (v0 - lo) / span) if span else 0)
            bar.setVisible(page < 10000)
        self._syncing = False
        d = chart.data
        if d.mode == 0 and d.origin is not None:
            a = d.origin + timedelta(seconds=chart.vx0)
            b = d.origin + timedelta(seconds=chart.vx1)
            window = f"{a:%d %b %H:%M:%S} – {b:%d %b %H:%M:%S}"
        elif d.mode in (1,):
            window = f"{format_duration(max(chart.vx0, 0))} – {format_duration(chart.vx1)}"
        else:
            window = f"{chart.vx0:,.0f} – {chart.vx1:,.0f}"
        rows_shown = max(int(math.ceil(chart.vy1) - math.floor(chart.vy0)), 0)
        self.view_info.setText(f"{zx:.1f}× · {zy:.1f}× · showing {window}, "
                               f"{min(rows_shown, len(d.row_values)):,} of {len(d.row_values):,} rows")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export dotted chart",
                                              suggested_path(f"{self.document.name} dotted chart.png"),
                                              "PNG image (*.png);;SVG drawing (*.svg)")
        if path:
            self.chart.export_image(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _swatch(colour: str):
    from PySide6.QtGui import QIcon
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(QRectF(2, 2, 10, 10))
    painter.end()
    return QIcon(pixmap)


def _natural(text: str):
    """Sort '2' before '10', numbers before words."""
    return (0, int(text), "") if text.isdigit() else (1, 0, text.lower())


def axis_unit(mode: int, unit: str) -> float:
    """Data units (seconds, %, events) per unit shown on the axis."""
    if mode in (0, 1):
        return TIME_UNITS.get(unit) or 1.0
    return 1.0


def axis_title(mode: int, unit: str) -> str:
    if mode in (0, 1) and unit != "Auto":
        name = unit.split(" (")[0].lower()
        return ("Time since the first event" if mode == 0 else X_TITLES[1]) + f" ({name})"
    return X_TITLES[mode]


def _number(value: float) -> str:
    """12.0 -> "12", 2.5 -> "2.5", 0.125 -> "0.125": no float noise."""
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1
    exponent = math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        step = multiple * 10 ** exponent
        if step >= raw:
            return step
    return 10 ** (exponent + 1)


_TIME_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60,
               120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800,
               1209600, 2592000, 7776000, 15552000, 31536000]


def _nice_time_step(raw: float) -> float:
    index = bisect.bisect_left(_TIME_STEPS, raw)
    return _TIME_STEPS[min(index, len(_TIME_STEPS) - 1)]


def _frange(start: float, stop: float, step: float):
    value = start
    count = 0
    while value <= stop + 1e-9 and count < 400:
        yield value
        value += step
        count += 1


__all__ = ["DotData", "DotSettings", "DottedChart", "DottedChartPanel", "vbox", "QImage"]
