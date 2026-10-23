"""Filtering event logs: keep the part of the log you want to look at.

Real logs are messy: rare variants, noise activities, cases cut off by the
extraction window.  Filtering is therefore the usual first step before
discovery, as in ProM ("Filter Log using Simple Heuristics") and Disco.  Every
filter here returns a **new** log and never changes the original, so you can
always compare the filtered log with the one it came from.

The filters, in the order :func:`apply_filters` applies them:

1. **Time frame** -- keep cases *contained in*, *started in*, or
   *intersecting* a period.  Cases without timestamps cannot be placed in
   time and are dropped.
2. **Start and end activities** -- keep cases that start (end) with one of
   the chosen activities, e.g. to drop cases cut off by the extraction.
3. **Activities** -- three modes, as in Disco:

   * *keep events*: remove every event of the other activities (a projection;
     cases keep their other events);
   * *mandatory*: keep cases that contain at least one of the activities;
   * *forbidden*: keep cases that contain none of them.

4. **Case length** -- keep cases with between ``min`` and ``max`` events.
5. **Variants** -- keep the most frequent variants, either the top ``k`` or
   as many as needed to cover a percentage of the cases.

Activities, variants and lengths are seen through the log's *classifier*
(see :mod:`.log`), so they match what the rest of the app shows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .log import KEY_NAME, Classifier, EventLog, Trace

TIME_MODES = ("contained", "started", "intersecting")
ACTIVITY_MODES = ("keep events", "mandatory", "forbidden")


@dataclass
class FilterSettings:
    """Which filters to apply.  ``None`` (or empty) means "do not filter on this"."""

    #: ``(from, to, mode)`` with ``mode`` in :data:`TIME_MODES`.
    time_frame: tuple[datetime, datetime, str] | None = None
    start_activities: set[str] | None = None
    end_activities: set[str] | None = None
    #: ``(activities, mode)`` with ``mode`` in :data:`ACTIVITY_MODES`.
    activities: tuple[set[str], str] | None = None
    #: ``(minimum, maximum)`` number of events (after the activity filter).
    case_length: tuple[int, int] | None = None
    #: Keep the variants covering this percentage of the cases (0-100].
    variant_coverage: float | None = None
    #: Or: keep the ``k`` most frequent variants.
    top_variants: int | None = None

    def describe(self) -> list[str]:
        """One line per active filter, for the record kept in the new log."""
        lines: list[str] = []
        if self.time_frame is not None:
            start, end, mode = self.time_frame
            lines.append(f"cases {mode} {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M}")
        if self.start_activities is not None:
            lines.append("start with " + ", ".join(sorted(self.start_activities)))
        if self.end_activities is not None:
            lines.append("end with " + ", ".join(sorted(self.end_activities)))
        if self.activities is not None:
            names, mode = self.activities
            lines.append(f"activities ({mode}): " + ", ".join(sorted(names)))
        if self.case_length is not None:
            lines.append(f"{self.case_length[0]} to {self.case_length[1]} events per case")
        if self.variant_coverage is not None:
            lines.append(f"most frequent variants covering {self.variant_coverage:g}% of cases")
        if self.top_variants is not None:
            lines.append(f"the {self.top_variants} most frequent variants")
        return lines


# ---------------------------------------------------------------------------
# Single filters (each returns a new log)
# ---------------------------------------------------------------------------
def _derived(log: EventLog, traces: list[Trace]) -> EventLog:
    result = log.filtered([])
    result.traces = traces
    return result


def _keep(log: EventLog, indices) -> EventLog:
    """The cases at ``indices``, as :meth:`EventLog.filtered` keeps them (a
    large columnar log stays columnar, so a filter costs no event objects)."""
    return log.filtered(indices)


def _spans(log: EventLog):
    """Each case's (first, last) timestamp, or None."""
    if log.columnar:
        store = log.traces.store
        for case in range(store.case_count):
            span = store.span(case)
            yield None if span is None else (store.timestamp(span[0]), store.timestamp(span[1]))
    else:
        for trace in log:
            yield _span(trace)


def _span(trace: Trace) -> tuple[datetime, datetime] | None:
    stamps = [event.timestamp for event in trace if event.timestamp is not None]
    return (min(stamps), max(stamps)) if stamps else None


def filter_time_frame(log: EventLog, start: datetime, end: datetime,
                      mode: str = "contained") -> EventLog:
    """Cases *contained* in, *started* in, or *intersecting* ``[start, end]``."""
    if mode not in TIME_MODES:
        raise ValueError(f"mode must be one of {TIME_MODES}")
    kept = []
    for index, span in enumerate(_spans(log)):
        if span is None:
            continue
        first, last = span
        if (mode == "contained" and start <= first and last <= end) or \
                (mode == "started" and start <= first <= end) or \
                (mode == "intersecting" and first <= end and last >= start):
            kept.append(index)
    return _keep(log, kept)


def filter_start_activities(log: EventLog, allowed: set[str],
                            classifier: Classifier | None = None) -> EventLog:
    """Cases whose first activity is one of ``allowed``."""
    kept = [i for i, sequence in enumerate(log.sequences(classifier)) if sequence and sequence[0] in allowed]
    return _keep(log, kept)


def filter_end_activities(log: EventLog, allowed: set[str],
                          classifier: Classifier | None = None) -> EventLog:
    """Cases whose last activity is one of ``allowed``."""
    kept = [i for i, sequence in enumerate(log.sequences(classifier)) if sequence and sequence[-1] in allowed]
    return _keep(log, kept)


def filter_activities(log: EventLog, activities: set[str], mode: str = "keep events",
                      classifier: Classifier | None = None) -> EventLog:
    """Project onto ``activities``, or keep cases that must (not) contain them.

    *keep events* removes the events of every other activity -- including
    events the classifier skips (a *start* event of a kept activity stays, so
    the dotted chart still shows it).  Cases left without events are dropped.
    """
    if mode not in ACTIVITY_MODES:
        raise ValueError(f"mode must be one of {ACTIVITY_MODES}")
    classifier = classifier or log.default_classifier()
    if mode == "keep events":
        kept = []
        for trace in log:
            events = [event for event in trace if classifier.label(event) in activities]
            if any(classifier.accepts(event) for event in events):
                kept.append(Trace(dict(trace.attributes), events))
        return _derived(log, kept)
    kept = [i for i, sequence in enumerate(log.sequences(classifier))
            if any(activity in activities for activity in sequence) == (mode == "mandatory")]
    return _keep(log, kept)


def filter_case_length(log: EventLog, minimum: int, maximum: int,
                       classifier: Classifier | None = None) -> EventLog:
    """Cases with between ``minimum`` and ``maximum`` events (inclusive)."""
    kept = [i for i, sequence in enumerate(log.sequences(classifier)) if minimum <= len(sequence) <= maximum]
    return _keep(log, kept)


def top_variants(log: EventLog, classifier: Classifier | None = None, *,
                 coverage: float | None = None, count: int | None = None
                 ) -> list[tuple[tuple[str, ...], int]]:
    """The most frequent variants: the top ``count``, or just enough of them
    to cover ``coverage`` percent of the cases (always at least one).

    Variants with the same frequency are taken in the order they first occur
    in the log, so the choice is deterministic.  (Keeping every tied variant
    instead would make the filter useless on the many real logs where most
    cases have a variant of their own.)
    """
    counts = Counter(log.sequences(classifier))       # insertion order = first occurrence
    ranked = sorted(counts.items(), key=lambda item: -item[1])     # stable sort
    if count is not None:
        return ranked[:max(count, 0)]
    if coverage is None:
        return ranked
    needed = sum(counts.values()) * min(max(coverage, 0), 100) / 100
    chosen, covered = [], 0
    for variant, frequency in ranked:
        if chosen and covered >= needed:
            break
        chosen.append((variant, frequency))
        covered += frequency
    return chosen


def filter_variants(log: EventLog, classifier: Classifier | None = None, *,
                    coverage: float | None = None, count: int | None = None) -> EventLog:
    """Keep the cases of the most frequent variants (see :func:`top_variants`)."""
    keep = {variant for variant, _ in top_variants(log, classifier, coverage=coverage,
                                                   count=count)}
    kept = [i for i, sequence in enumerate(log.sequences(classifier)) if sequence in keep]
    return _keep(log, kept)


# ---------------------------------------------------------------------------
# All together
# ---------------------------------------------------------------------------
def apply_filters(log: EventLog, settings: FilterSettings,
                  classifier: Classifier | None = None, name: str | None = None) -> EventLog:
    """Apply every active filter of ``settings``, in the order of the module
    docstring, and name the result.  The new log records what was done in
    its ``openprocess:filter`` attribute."""
    classifier = classifier or log.default_classifier()
    result = log
    if settings.time_frame is not None:
        result = filter_time_frame(result, *settings.time_frame)
    if settings.start_activities is not None:
        result = filter_start_activities(result, settings.start_activities, classifier)
    if settings.end_activities is not None:
        result = filter_end_activities(result, settings.end_activities, classifier)
    if settings.activities is not None:
        result = filter_activities(result, *settings.activities, classifier=classifier)
    if settings.case_length is not None:
        result = filter_case_length(result, *settings.case_length, classifier=classifier)
    if settings.variant_coverage is not None or settings.top_variants is not None:
        result = filter_variants(result, classifier, coverage=settings.variant_coverage,
                                 count=settings.top_variants)
    if result is log:
        result = _derived(log, list(log.traces))
    result.attributes = dict(log.attributes)
    result.attributes[KEY_NAME] = name or f"{log.name} (filtered)"
    result.attributes["openprocess:filter"] = "; ".join(settings.describe()) or "none"
    result.source_path = None
    return result


__all__ = [
    "ACTIVITY_MODES", "FilterSettings", "TIME_MODES", "apply_filters",
    "filter_activities", "filter_case_length", "filter_end_activities",
    "filter_start_activities", "filter_time_frame", "filter_variants", "top_variants",
]
