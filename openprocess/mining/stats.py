"""Descriptive statistics of an event log.

Before discovering anything it pays to *look* at the log: how many cases,
how many distinct behaviours (variants), which activities start and end
cases, how long cases take.  This module computes those numbers once so the
views can display them without recomputing.

Variants
--------
A *variant* is a distinct activity sequence.  Two cases belong to the same
variant iff they have exactly the same sequence under the chosen classifier.
The number of variants relative to the number of cases is a quick measure of
how structured the process is: 1 variant means perfectly repetitive; one
variant per case means every case is unique (a "spaghetti" process).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .log import Classifier, EventLog


@dataclass
class Variant:
    """One distinct sequence, with the indices of the traces that follow it."""

    sequence: tuple[str, ...]
    trace_indices: list[int] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.trace_indices)


@dataclass
class ActivityStats:
    name: str
    occurrences: int          # total number of events with this label
    cases: int                # number of cases containing it at least once
    as_start: int             # cases starting with it
    as_end: int               # cases ending with it


@dataclass
class LogSummary:
    """Everything the overview page shows."""

    case_count: int
    event_count: int                       # events *kept* by the classifier
    variants: list[Variant]                # most frequent first
    activities: list[ActivityStats]        # most frequent first
    start: datetime | None                 # earliest timestamp
    end: datetime | None                   # latest timestamp
    case_durations: list[timedelta]        # one per case that has timestamps
    events_per_case: Counter               # length -> number of cases

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def activity_count(self) -> int:
        return len(self.activities)

    @property
    def mean_case_duration(self) -> timedelta | None:
        if not self.case_durations:
            return None
        seconds = statistics.fmean(d.total_seconds() for d in self.case_durations)
        return timedelta(seconds=seconds)

    @property
    def median_case_duration(self) -> timedelta | None:
        if not self.case_durations:
            return None
        return timedelta(seconds=statistics.median(
            d.total_seconds() for d in self.case_durations))


def summarise(log: EventLog, classifier: Classifier | None = None) -> LogSummary:
    """Compute a :class:`LogSummary` in one pass over the log."""
    classifier = classifier or log.default_classifier()
    if log.columnar:
        return _summarise_columns(log, classifier)

    by_sequence: dict[tuple[str, ...], Variant] = {}
    occurrences: Counter = Counter()
    cases_with: Counter = Counter()
    starts: Counter = Counter()
    ends: Counter = Counter()
    durations: list[timedelta] = []
    lengths: Counter = Counter()
    first: datetime | None = None
    last: datetime | None = None
    kept_events = 0

    for index, trace in enumerate(log.traces):
        sequence = log.sequence(trace, classifier)
        by_sequence.setdefault(sequence, Variant(sequence)).trace_indices.append(index)
        occurrences.update(sequence)
        cases_with.update(set(sequence))
        kept_events += len(sequence)
        lengths[len(sequence)] += 1
        if sequence:
            starts[sequence[0]] += 1
            ends[sequence[-1]] += 1

        # Timestamps are taken from *all* events, not only the classified
        # ones: a case's duration does not change with the classifier.
        stamps = [e.timestamp for e in trace.events if e.timestamp is not None]
        if stamps:
            low, high = min(stamps), max(stamps)
            durations.append(high - low)
            first = low if first is None or low < first else first
            last = high if last is None or high > last else last

    variants = sorted(by_sequence.values(), key=lambda v: (-v.count, v.sequence))
    activities = sorted(
        (ActivityStats(name, occurrences[name], cases_with[name], starts[name], ends[name])
         for name in occurrences),
        key=lambda a: (-a.occurrences, a.name))

    return LogSummary(
        case_count=len(log.traces),
        event_count=kept_events,
        variants=variants,
        activities=activities,
        start=first,
        end=last,
        case_durations=durations,
        events_per_case=lengths,
    )


def _summarise_columns(log: EventLog, classifier: Classifier) -> LogSummary:
    """:func:`summarise` for a columnar log: the same numbers, read from the
    arrays, without building a single trace or event object (a million-event
    log would otherwise be turned into a million dicts just to be counted)."""
    store = log.traces.store
    by_sequence: dict[tuple[str, ...], Variant] = {}
    occurrences: Counter = Counter()
    cases_with: Counter = Counter()
    starts: Counter = Counter()
    ends: Counter = Counter()
    lengths: Counter = Counter()
    durations: list[timedelta] = []
    first = last = None                          # event indices of the earliest and latest timestamp
    kept_events = 0
    micros = store.micros
    for index, sequence in enumerate(log.sequences(classifier)):
        variant = by_sequence.get(sequence)
        if variant is None:
            variant = by_sequence[sequence] = Variant(sequence)
        variant.trace_indices.append(index)
        lengths[len(sequence)] += 1
        kept_events += len(sequence)
        if sequence:
            starts[sequence[0]] += 1
            ends[sequence[-1]] += 1
        span = store.span(index)
        if span is not None:
            low, high = span
            durations.append(timedelta(microseconds=micros[high] - micros[low]))
            if first is None or micros[low] < micros[first]:
                first = low
            if last is None or micros[high] > micros[last]:
                last = high
    for variant in by_sequence.values():          # per variant, not per case: far fewer
        occurrences.update({a: n * variant.count for a, n in Counter(variant.sequence).items()})
        cases_with.update({a: variant.count for a in set(variant.sequence)})
    variants = sorted(by_sequence.values(), key=lambda v: (-v.count, v.sequence))
    activities = sorted(
        (ActivityStats(name, occurrences[name], cases_with[name], starts[name], ends[name])
         for name in occurrences),
        key=lambda a: (-a.occurrences, a.name))
    return LogSummary(case_count=len(log.traces), event_count=kept_events, variants=variants,
                      activities=activities,
                      start=store.timestamp(first) if first is not None else None,
                      end=store.timestamp(last) if last is not None else None,
                      case_durations=durations, events_per_case=lengths)


def format_duration(delta: timedelta | float | None) -> str:
    """Human-friendly duration: ``3d 4h``, ``12m 5s``, ``850ms``.

    Two units at most -- enough precision to compare, short enough to scan.
    """
    if delta is None:
        return "–"
    seconds = delta.total_seconds() if isinstance(delta, timedelta) else float(delta)
    if seconds < 0:
        return "-" + format_duration(-seconds)
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    units = [("y", 365 * 86400), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    parts: list[str] = []
    remaining = seconds
    for suffix, size in units:
        if remaining >= size or parts:
            amount = int(remaining // size)
            remaining -= amount * size
            if amount or parts:
                parts.append(f"{amount}{suffix}")
        if len(parts) == 2:
            break
    return " ".join(p for p in parts if not p.startswith("0")) or "0s"
