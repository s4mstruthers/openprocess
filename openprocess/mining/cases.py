"""Case by case: what the measuring boxes read from a log, fast on large ones.

Questions about a real process are mostly questions about cases: how long
from the invoice to its clearing, does the payment block go before the goods
receipt, did one user both record and clear an invoice, how much money is
involved.  Each needs, per case, its activities in order and a few of its
attributes -- not a million event objects.  :func:`cases` gives exactly
that, read from a columnar log's arrays (:mod:`.columns`) when the log has
them and from its events otherwise, with the same result either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from .log import KEY_NAME, KEY_RESOURCE, KEY_TIME, EventLog


@dataclass
class Case:
    """One case, as the measuring boxes see it."""

    index: int                         # its position in the log
    id: str
    activities: list[str]              # every event's activity (concept:name), in order
    times: list[float | None]          # seconds since the epoch, None where an event has none
    attributes: dict[str, Any]         # the case's own attributes
    columns: dict[str, list[Any]]      # the event attributes asked for, one value per event

    def first(self, activity: str, start: int = 0) -> int | None:
        """The position of ``activity``'s first event at or after ``start``."""
        for position in range(start, len(self.activities)):
            if self.activities[position] == activity:
                return position
        return None


def cases(log: EventLog, event_keys: tuple[str, ...] = ()) -> Iterator[Case]:
    """Every case with its activities, times, attributes and the event
    attributes in ``event_keys`` (``org:resource``, ``User``, a value)."""
    if log.columnar:
        yield from _columnar_cases(log, event_keys)
        return
    for index, trace in enumerate(log.traces):
        events = trace.events
        yield Case(index, trace.case_id, [str(e.attributes.get(KEY_NAME, "")) for e in events],
                   [e.timestamp.timestamp() if isinstance(e.timestamp, datetime) else None for e in events],
                   trace.attributes, {key: [e.attributes.get(key) for e in events] for key in event_keys})


def _columnar_cases(log: EventLog, event_keys: tuple[str, ...]) -> Iterator[Case]:
    from .columns import NO_TIME
    store = log.traces.store
    names = [None if code < 0 else store.activity.values[code] for code in store.activity.codes]
    columns: dict[str, list] = {}
    for key in event_keys:
        if key == KEY_RESOURCE:
            columns[key] = [None if code < 0 else store.resource.values[code] for code in store.resource.codes]
        elif key == KEY_NAME:
            columns[key] = names
        else:
            columns[key] = store.extra.get(key) or [None] * store.event_count
    micros, offsets = store.micros, store.offsets
    for case in range(store.case_count):
        start, stop = offsets[case], offsets[case + 1]
        yield Case(case, store.case_ids[case], [name or "" for name in names[start:stop]],
                   [None if m == NO_TIME else m / 1e6 for m in micros[start:stop]],
                   store.case_attributes(case), {key: column[start:stop] for key, column in columns.items()})


def case_value(case: Case, key: str) -> float | None:
    """A case's value: its own attribute ``key`` when it is a number, else the
    largest number the attribute takes on its events (BPI 2019's *Cumulative
    net worth* grows along the case, so its largest is the item's worth)."""
    if not key:
        return None
    own = case.attributes.get(key)
    if isinstance(own, (int, float)) and not isinstance(own, bool):
        return float(own)
    numbers = [v for v in case.columns.get(key, ()) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return float(max(numbers)) if numbers else None


__all__ = ["Case", "case_value", "cases"]
