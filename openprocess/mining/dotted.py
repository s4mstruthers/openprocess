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


class _Events:
    """The events a dotted chart shows, in log order, with what it needs of
    each: its case, its index in the case, its time, and any attribute as
    text.  An object log is read through its events; a columnar log (a large
    file) straight from its arrays, so a million-event log is drawn without
    building a million event objects."""

    def __init__(self, log, classifier, all_events: bool) -> None:
        from .columns import NO_TIME
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
            from .columns import _micros
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
        from .columns import _micros
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


class Dots:
    """All dots of a log under given settings, ready for drawing.

    Parallel arrays keep the per-dot data compact: ``xs[i]``, ``rows[i]``,
    ``colour[i]`` (index into ``colour_values``), ``shape[i]``, ``case[i]``
    (trace index) and ``position[i]`` (the event's index in its trace).
    """

    def __init__(self, log, classifier, settings: DotSettings) -> None:
        self.settings = settings
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
            0: lambda v: (stats[v][0], natural(v)),
            1: lambda v: (stats[v][1], natural(v)),
            2: lambda v: (stats[v][1] - stats[v][0], natural(v)),
            3: lambda v: (stats[v][2], natural(v)),
            4: lambda v: natural(v),
        }
        self.row_values = sorted(stats, key=keys[s.sort], reverse=s.descending)
        row_index = {v: i for i, v in enumerate(self.row_values)}
        self.row_stats = stats

        # 3. Colour and shape assignments, by frequency ----------------------
        def ranked(values: list[str]) -> list[str]:
            counts: dict[str, int] = {}
            for value in values:
                counts[value] = counts.get(value, 0) + 1
            return sorted(counts, key=lambda v: (-counts[v], natural(v)))
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
    plot_h = min(max(n_rows * (16 if labelled else 0.5), 220), 620)
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
    seen: set[tuple[int, int, int]] = set()
    by_colour: dict[int, list[tuple[int, int]]] = {}
    for x, row, colour in zip(dots.xs, dots.rows, dots.colour):
        slot = colour if colour < 8 else 8
        key = (round(px(x) * 2), round(py(row) * 2), slot)
        if key not in seen:
            seen.add(key)
            by_colour.setdefault(slot, []).append(key[:2])
    for slot in sorted(by_colour, key=lambda k: -k):
        fill = CATEGORICAL_LIGHT[slot] if slot < 8 else OTHER_LIGHT
        out.append(f'<g fill="{fill}" fill-opacity="0.85">')
        out.extend(f'<circle cx="{a / 2:g}" cy="{b / 2:g}" r="{radius}"/>' for a, b in by_colour[slot])
        out.append("</g>")

    # Legend: what each colour means, with how many events.
    items = [(CATEGORICAL_LIGHT[i], v, dots.colour_counts[v]) for i, v in enumerate(dots.colour_values[:8])]
    rest = dots.colour_values[8:]
    if rest:
        items.append((OTHER_LIGHT, f"Other ({len(rest)})", sum(dots.colour_counts[v] for v in rest)))
    column = (width - left - right) / 3
    for i, (fill, value, count) in enumerate(items):
        lx, ly = left + (i % 3) * column, 32 + (i // 3) * 18
        name = (value or "–") if len(value) <= 34 else value[:33] + "…"
        out.append(f'<circle cx="{lx + 5:.1f}" cy="{ly - 4}" r="4.5" fill="{fill}"/>')
        out.append(f'<text x="{lx + 14:.1f}" y="{ly}" fill="#333">{_escape(name)} '
                   f'<tspan fill="#888">{count:,}</tspan></text>')
    out.append("</svg>")
    return "\n".join(out)
