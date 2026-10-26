"""The dots of a dotted chart, worked out without drawing them.

What a dotted chart is (course tutorial W2 L4; Song & van der Aalst 2007):
every event is a dot; *x* is when (actual time, time since the case
started, a share of the case's duration, or a logical position), the *row*
is what the dot belongs to (the case, or any event attribute), and *colour*
and *shape* show two more attributes.  :class:`Dots` computes all of that
from a log and a classifier; the Studio's chart
(:mod:`openprocess.gui.studio.dotted_chart`) draws it on screen, and the
*Dotted chart* box draws it as an SVG figure (:func:`svg`), so both show the
same picture of the same log.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field

import numpy as np

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


def _array(values, dtype) -> np.ndarray:
    """A numpy view of an ``array.array`` (or a copy of a list), typed."""
    if isinstance(values, array):
        return np.frombuffer(values, dtype=dtype) if len(values) else np.zeros(0, dtype=dtype)
    return np.asarray(values, dtype=dtype)


def _codes(values) -> tuple[np.ndarray, list[str]]:
    """Text values as small integers plus the table to read them back
    (``None`` reads as an en dash)."""
    table: list[str] = []
    index: dict[str, int] = {}
    codes = np.empty(len(values), dtype=np.int32)
    for k, value in enumerate(values):
        text = "–" if value is None else value
        code = index.get(text)
        if code is None:
            code = index[text] = len(table)
            table.append(text)
        codes[k] = code
    return codes, table


def _ranks(keys: list[str], primary: np.ndarray | None, descending: bool) -> np.ndarray:
    """The order of ``keys`` (indices into them) by ``primary`` (a number
    per key; None: by the keys alone), ties by natural order; reversed
    when descending, like ``sorted(..., reverse=True)``."""
    tie = np.empty(len(keys), dtype=np.int64)
    tie[sorted(range(len(keys)), key=lambda i: natural(keys[i]))] = np.arange(len(keys))
    order = tie.argsort(kind="stable") if primary is None else np.lexsort((tie, primary))
    return order[::-1] if descending else order


class _Events:
    """The events a dotted chart shows, in log order, as arrays: the case
    of each, its index in the case, its time in microseconds, and any
    attribute as codes into a table of text.  A columnar log (a large file)
    is read straight from its arrays, so a million-event log is drawn
    without building a million event objects."""

    def __init__(self, log, classifier, all_events: bool) -> None:
        from .columns import NO_TIME
        self.log, self.classifier = log, classifier
        self.missing = NO_TIME
        wanted = None if all_events or classifier.lifecycles is None else \
            {v.lower() for v in classifier.lifecycles}
        if log.columnar:
            store = self.store = log.traces.store
            offsets = _array(store.offsets, np.int64)
            count = store.event_count
            case = np.repeat(np.arange(store.case_count, dtype=np.int32), np.diff(offsets))
            keep = np.ones(count, dtype=bool)
            if wanted is not None:
                # accepted[code + 1]: a missing lifecycle (code -1) is accepted
                accepted = np.array([True] + [v.lower() in wanted for v in store.lifecycle.values])
                keep = accepted[_array(store.lifecycle.codes, np.int32) + 1]
            self.index = np.flatnonzero(keep)              # the event's index in the store
            self.case = case[keep]
            self.position = (self.index - offsets[self.case]).astype(np.int32)
            self.micros = _array(store.micros, np.int64)[keep]
            self.events = None
        else:
            self.store = None
            self.events = []
            from .columns import _micros
            case, position, micros = [], [], []
            for ti, trace in enumerate(log.traces):
                for k, event in enumerate(trace.events):
                    if not (all_events or classifier.accepts(event)):
                        continue
                    case.append(ti)
                    position.append(k)
                    self.events.append(event)
                    stamp = event.timestamp
                    micros.append(_micros(stamp) if stamp is not None else NO_TIME)
            self.case = np.asarray(case, dtype=np.int32)
            self.position = np.asarray(position, dtype=np.int32)
            self.micros = np.asarray(micros, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.case)

    def ranks(self) -> np.ndarray:
        """Each shown event's place among *all* the log's events, ordered by
        time (then case, then position): the x of "Logical: order in log"."""
        from .columns import _micros
        floor = -(1 << 63)
        if self.store is not None:
            store = self.store
            offsets = _array(store.offsets, np.int64)
            case = np.repeat(np.arange(store.case_count, dtype=np.int64), np.diff(offsets))
            position = np.arange(store.event_count) - offsets[case]
            micros = _array(store.micros, np.int64)
            shown = self.index
        else:
            case, position, micros = [], [], []
            for ti, trace in enumerate(self.log.traces):
                for k, event in enumerate(trace.events):
                    stamp = event.timestamp
                    case.append(ti)
                    position.append(k)
                    micros.append(_micros(stamp) if stamp is not None else floor)
            case, position = np.asarray(case, dtype=np.int64), np.asarray(position, dtype=np.int64)
            micros = np.asarray(micros, dtype=np.int64)
            # the shown events among all: by (case, position), both ascending in log order
            starts = np.r_[0, np.cumsum(np.bincount(case, minlength=len(self.log.traces)))[:-1]]
            shown = starts[self.case] + self.position
        micros = np.where(micros == self.missing, floor, micros)
        rank = np.empty(len(micros), dtype=np.int64)
        rank[np.lexsort((position, case, micros))] = np.arange(len(micros))
        return rank[shown]

    def timestamp(self, k: int):
        """The shown event ``k``'s timestamp, with the offset it was written with."""
        if self.store is not None:
            return self.store.timestamp(int(self.index[k]))
        return self.events[k].timestamp

    def values(self, component: str) -> tuple[np.ndarray, list[str]]:
        """``component`` (Case, Activity, Resource, Lifecycle, None or an
        attribute) for every shown event, as codes into a table of text."""
        if component == "None":
            return np.zeros(len(self), dtype=np.int32), [""]
        if self.store is not None:
            store, index = self.store, self.index
            if component == "Case":
                return self.case.astype(np.int32), list(store.case_ids)
            if component == "Activity":
                if tuple(self.classifier.keys) == ("concept:name",):       # the usual classifier
                    codes = _array(store.activity.codes, np.int32)[index]
                    return np.where(codes < 0, len(store.activity.values), codes), \
                        list(store.activity.values) + [""]
                codes, table = _codes(self._labels())
                return codes[index], table
            key = {"Resource": "org:resource", "Lifecycle": "lifecycle:transition"}.get(component, component)
            column = store.column_text(key)
            codes, table = _codes(column)
            return codes[index], table
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
        return _codes(out)

    def _labels(self) -> list[str]:
        """Every stored event's activity label under the classifier (the
        plain activity for an event the classifier leaves out)."""
        store, classifier = self.store, self.classifier
        names = store.column_text("concept:name")
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


class Dots:
    """All dots of a log under given settings, ready for drawing.

    Parallel numpy arrays keep the per-dot data compact: ``xs[i]``,
    ``rows[i]`` (index into ``row_values``), ``colour[i]`` (index into
    ``colour_values``), ``shape[i]``, ``case[i]`` (trace index) and
    ``position[i]`` (the event's index in its trace).  ``order`` lists the
    dots by x and ``xs_sorted`` their x, so a window finds its dots by
    bisection (:meth:`between`); the dots of a case are consecutive
    (:meth:`case_dots`), as events are read case by case.
    """

    def __init__(self, log, classifier, settings: DotSettings) -> None:
        self.settings = settings
        s = settings
        events = _Events(log, classifier, s.all_events)
        micros, case, missing = events.micros, events.case, events.missing

        # 1. Select events and compute raw x values -------------------------
        timed = micros != missing
        self.has_time = bool(timed.any())
        mode = s.x_mode if self.has_time or s.x_mode >= 3 else 4
        if self.has_time:
            earliest = int(np.flatnonzero(timed)[micros[timed].argmin()])
            origin = micros[earliest]
            # The axis shows clock times in the offset the log was written with.
            self.origin = events.timestamp(earliest)
        else:
            origin, self.origin = None, None
        if mode == 4:                                 # position among the shown events of the case
            new_case = np.r_[True, case[1:] != case[:-1]] if len(case) else np.zeros(0, dtype=bool)
            group = np.cumsum(new_case) - 1
            xs = (np.arange(len(case)) - np.flatnonzero(new_case)[group]).astype(np.float64)
            kept = slice(None)
        elif mode == 3:
            xs = events.ranks().astype(np.float64)
            kept = slice(None)
        else:
            kept = timed
            seconds = (micros[kept] - origin) / 1e6 if origin is not None else np.zeros(0)
            if mode == 0:
                xs = seconds
            else:
                # Each case's first and last shown timestamp, in seconds.
                count = int(case.max()) + 1 if len(case) else 0
                first = np.full(count, np.inf)
                last = np.full(count, -np.inf)
                np.minimum.at(first, case[kept], seconds)
                np.maximum.at(last, case[kept], seconds)
                since_start = seconds - first[case[kept]]
                if mode == 1:
                    xs = since_start
                else:
                    span = (last - first)[case[kept]]
                    xs = np.where(span > 0, 100.0 * since_start / np.where(span > 0, span, 1.0), 0.0)
        self.mode = mode
        self.xs = np.ascontiguousarray(xs, dtype=np.float64)
        self.case = case[kept]
        self.position = events.position[kept]

        def component(name: str) -> tuple[np.ndarray, list[str]]:
            codes, table = events.values(name)
            return codes[kept], table

        # 2. Rows: one per distinct component value, sorted ------------------
        row_code, row_table = component(s.rows)
        present, rows = np.unique(row_code, return_inverse=True)
        values = [row_table[p] for p in present]
        counts = np.bincount(rows, minlength=len(present)).astype(np.float64)
        first = np.full(len(present), np.inf)
        last = np.full(len(present), -np.inf)
        np.minimum.at(first, rows, self.xs)
        np.maximum.at(last, rows, self.xs)
        primary = {0: first, 1: last, 2: last - first, 3: counts, 4: None}[s.sort]
        order = _ranks(values, primary, s.descending)
        self.row_values = [values[i] for i in order]
        self.row_stats = {values[i]: [float(first[i]), float(last[i]), int(counts[i])]
                          for i in range(len(values))}
        row_index = np.empty(len(order), dtype=np.int32)
        row_index[order] = np.arange(len(order))
        self.rows = row_index[rows]

        # 3. Colour and shape assignments, by frequency ----------------------
        def ranked(name: str) -> tuple[np.ndarray, list[str], np.ndarray]:
            code, table = component(name)
            present, inverse = np.unique(code, return_inverse=True)
            values = [table[p] for p in present]
            counts = np.bincount(inverse, minlength=len(present))
            order = _ranks(values, -counts.astype(np.float64), False)
            index = np.empty(len(order), dtype=np.int32)
            index[order] = np.arange(len(order))
            return index[inverse], [values[i] for i in order], counts[order]
        self.colour, self.colour_values, colour_counts = ranked(s.colour_by)
        self.shape, self.shape_values, _ = ranked(s.shape_by)
        self.colour_counts = {v: int(n) for v, n in zip(self.colour_values, colour_counts)}

        self.x_min = float(self.xs.min()) if len(self.xs) else 0.0
        self.x_max = float(self.xs.max()) if len(self.xs) else 1.0
        if self.x_max <= self.x_min:
            self.x_max = self.x_min + 1.0
        # 4. The dots by x, for the windows of a chart.
        self.order = np.argsort(self.xs, kind="stable").astype(np.int64)
        self.xs_sorted = self.xs[self.order]
        self.traces = log.traces
        self.classifier = classifier

    def __len__(self) -> int:
        return len(self.xs)

    def between(self, x0: float, x1: float) -> np.ndarray:
        """The dots with ``x0 <= x <= x1``, by increasing x."""
        return self.order[np.searchsorted(self.xs_sorted, x0, "left"):
                          np.searchsorted(self.xs_sorted, x1, "right")]

    def case_dots(self, case: int) -> range:
        """The dot indices of a case (consecutive: events come case by case)."""
        return range(int(np.searchsorted(self.case, case, "left")),
                     int(np.searchsorted(self.case, case, "right")))

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
        return self.traces[int(self.case[i])].events[int(self.position[i])]


def natural(text: str):
    """Sort '2' before '10', numbers before words."""
    return (0, int(text), "") if text.isdigit() else (1, 0, text.lower())


# ---------------------------------------------------------------------------
# As a figure
# ---------------------------------------------------------------------------
_TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200,
               86400, 172800, 604800, 1209600, 2592000, 7776000, 15552000, 31536000]


def _step(span: float, ticks: int, steps: list[float] | None = None) -> float:
    """A round distance between gridlines that gives about ``ticks`` of them."""
    import math
    raw = span / max(ticks, 1)
    if steps:
        return next((s for s in steps if s >= raw), steps[-1] * math.ceil(raw / steps[-1]))
    if raw <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw))
    return next(m * 10 ** exponent for m in (1, 2, 5, 10) if m * 10 ** exponent >= raw)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def svg(dots: Dots, title: str = "", unit: str = "Auto", width: int = 900) -> str:
    """The chart as SVG text: one dot per event (drawn once per pixel and
    colour, so a large log stays a small file), the x axis in the settings'
    mode and unit, row labels when there are few rows, and a legend of the
    eight most frequent colour values (the rest are a grey "Other", drawn
    underneath)."""
    from datetime import timedelta
    from ..palette import CATEGORICAL_LIGHT, OTHER_LIGHT
    from .stats import format_duration

    s, mode, n_rows = dots.settings, dots.mode, max(len(dots.row_values), 1)
    labelled = n_rows <= 40
    longest = max((len(v) for v in dots.row_values), default=0) if labelled else 0
    left = 16 + (min(longest, 28) * 6.2 if labelled else 0) + 18
    legend_rows = (min(len(dots.colour_values), 9) + 2) // 3
    top = 34 + 18 * legend_rows
    bottom, right = 44, 16
    plot_h = min(max(n_rows * (16 if labelled else 0.5), 220), 440)     # landscape: it fits a side panel
    plot_w = width - left - right
    height = top + plot_h + bottom
    x0, x1 = dots.x_min, dots.x_max
    scale = 1.0 if mode not in (0, 1) else (TIME_UNITS.get(unit) or 1.0)

    def px(x: float) -> float:
        return left + (x - x0) / (x1 - x0) * plot_w

    def py(row: int) -> float:
        return top + (row + 0.5) / n_rows * plot_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height:.0f}" '
           f'width="{width}" height="{height:.0f}" font-family="Helvetica, Arial, sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height:.0f}" fill="white"/>']
    if title:
        out.append(f'<text x="{left}" y="18" font-size="13" font-weight="bold" fill="#222">{_escape(title)}</text>')

    # Gridlines and the x axis.
    if mode in (0, 1) and unit == "Auto":
        step = _step(x1 - x0, 8, _TIME_STEPS)
    else:
        step = _step((x1 - x0) / scale, 8) * scale
    first = (x0 // step) * step
    tick = first
    while tick <= x1 + 1e-9:
        if tick >= x0 - 1e-9:
            x = px(tick)
            if mode == 0 and unit == "Auto" and dots.origin is not None:
                moment = dots.origin + timedelta(seconds=tick)
                text = moment.strftime("%d %b %Y" if step >= 86400 else "%d %b %H:%M")
            elif mode in (0, 1) and unit == "Auto":
                text = format_duration(tick)
            else:
                value = tick / scale
                text = f"{value:,.2f}".rstrip("0").rstrip(".") or "0"
            out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h:.1f}" stroke="#e6e6e6"/>')
            out.append(f'<text x="{x:.1f}" y="{top + plot_h + 15:.1f}" text-anchor="middle" fill="#555">'
                       f'{_escape(text)}</text>')
        tick += step
    axis = X_TITLES[mode]
    if mode in (0, 1) and unit != "Auto":
        axis = ("Time since the first event" if mode == 0 else X_TITLES[1]) + f" ({unit.split(' (')[0].lower()})"
    out.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 8:.0f}" text-anchor="middle" fill="#333">'
               f'{_escape(axis)}</text>')
    out.append(f'<rect x="{left}" y="{top}" width="{plot_w:.1f}" height="{plot_h:.1f}" fill="none" stroke="#bbb"/>')

    # Rows.
    if labelled:
        for row, value in enumerate(dots.row_values):
            name = value if len(value) <= 28 else value[:27] + "…"
            out.append(f'<text x="{left - 6:.1f}" y="{py(row) + 4:.1f}" text-anchor="end" fill="#333">'
                       f'{_escape(name)}</text>')
    else:
        out.append(f'<text x="{left - 8:.1f}" y="{top + plot_h / 2:.1f}" text-anchor="middle" fill="#333" '
                   f'transform="rotate(-90 {left - 8:.1f} {top + plot_h / 2:.1f})">'
                   f'{_escape(s.rows)} ({n_rows:,}, by {SORT_MODES[s.sort].lower()})</text>')

    # Dots: once per pixel and colour, "Other" first so the named colours are on top.
    radius = 3.0 if labelled else 1.6
    half_px = np.round(2 * (left + (dots.xs - x0) / (x1 - x0) * plot_w)).astype(np.int64)
    half_py = np.round(2 * (top + (dots.rows + 0.5) / n_rows * plot_h)).astype(np.int64)
    slot = np.minimum(dots.colour, 8).astype(np.int64)
    # Each (pixel, colour) once, in the order it is first met: the same text
    # as drawing dot by dot gives, so a figure's fingerprint stays what it was.
    keys, first = np.unique((half_px * (1 << 24) + half_py) * 9 + slot, return_index=True)
    keys = keys[np.argsort(first, kind="stable")]
    for s_ in sorted(set((keys % 9).tolist()), reverse=True):
        mine = keys[keys % 9 == s_] // 9
        fill = CATEGORICAL_LIGHT[s_] if s_ < 8 else OTHER_LIGHT
        out.append(f'<g fill="{fill}" fill-opacity="0.85">')
        out.extend(f'<circle cx="{a / 2:g}" cy="{b / 2:g}" r="{radius}"/>'
                   for a, b in zip((mine >> 24).tolist(), (mine & ((1 << 24) - 1)).tolist()))
        out.append("</g>")

    # Legend: what each colour means, with how many events.
    items = [(CATEGORICAL_LIGHT[i], v, dots.colour_counts[v]) for i, v in enumerate(dots.colour_values[:8])]
    rest = dots.colour_values[8:]
    if rest:     # name the grey values when there are few, so none is hidden
        name = "Other: " + ", ".join(rest) if len(rest) <= 3 else f"Other ({len(rest)} values)"
        items.append((OTHER_LIGHT, name, sum(dots.colour_counts[v] for v in rest)))
    column = (width - left - right) / 3
    for i, (fill, value, count) in enumerate(items):
        lx, ly = left + (i % 3) * column, 32 + (i // 3) * 18
        name = (value or "–") if len(value) <= 48 else value[:47] + "…"
        out.append(f'<circle cx="{lx + 5:.1f}" cy="{ly - 4}" r="4.5" fill="{fill}"/>')
        out.append(f'<text x="{lx + 14:.1f}" y="{ly}" fill="#333">{_escape(name)} '
                   f'<tspan fill="#888">{count:,}</tspan></text>')
    out.append("</svg>")
    return "\n".join(out)


def how_to_read(settings: DotSettings, mode: int) -> str:
    """What the chart's axes mean and which patterns to look for, for the
    x mode it was drawn in and its rows."""
    by_case = settings.rows == "Case"
    rows = ("Each row is a case and each dot one of its events" if by_case else
            f"Each row is one value of {settings.rows} and each dot an event of it")
    order = f"; rows are sorted by {SORT_MODES[settings.sort].lower()}" if by_case else ""
    x = {
        0: ", at the time it happened. "
           + ("The slope of the first dots is the arrival rate, vertical lines are events handled "
              "together (batches), and horizontal gaps are cases waiting."
              if by_case else
              "Gaps are times it was not active (nights, weekends, days off), and vertical lines "
              "are batches."),
        1: ", at the time since its case began, so every case starts at 0. Compare how long each "
           "step takes after the start; long horizontal gaps are waiting.",
        2: ", at its share of the case's duration (0 % the first event, 100 % the last), so cases "
           "of different lengths line up: a column shows where in a case something usually happens.",
        3: ", at its place among all events of the log ordered by time: actual time without the gaps.",
        4: ", at its position in the case (0 is the first event): which step comes where, whatever "
           "the time.",
    }[mode]
    return f"{rows}{order}{x}"
