"""Filter boxes: keep part of a log, as the log viewer's Filter… does."""

from __future__ import annotations

from typing import Literal

from ... import flow
from ...mining import filtering
from ...mining.log import EventLog
from ..box import box


def _names(text: str) -> set[str]:
    return {part.strip() for part in text.split(",") if part.strip()}


def _kept(log: EventLog, kept: EventLog) -> EventLog:
    """Note what is left, as every filter box does, and hand the log on."""
    flow.note(f"Kept {len(kept)} of {len(log)} cases, {kept.event_count} of {log.event_count} events")
    return kept


@box(name="Top variants", group="Filter")
def top_variants(log: EventLog, k: int = 3) -> EventLog:
    """Keeps the cases of the *k* most frequent variants.

    k: how many variants to keep
    """
    before = log.simple_log()
    kept = filtering.filter_variants(log, count=k)
    flow.note(f"Kept {len(kept.simple_log())} of {len(before)} variants, "
              f"{len(kept)} of {len(log)} cases")
    return kept


@box(name="Variants covering", group="Filter")
def variants_covering(log: EventLog, percent: float = 80.0) -> EventLog:
    """Keeps just enough of the most frequent variants to cover *percent* of the cases."""
    kept = filtering.filter_variants(log, coverage=percent)
    flow.note(f"Kept {len(kept)} of {len(log)} cases")
    return kept


@box(name="Activities", group="Filter")
def filter_activities(log: EventLog, activities: str = "a, b",
                      mode: Literal["keep events", "mandatory", "forbidden"] = "keep events") -> EventLog:
    """Keeps only the events of these activities, or the cases that must
    (*mandatory*) or must not (*forbidden*) contain one of them.

    activities: activity names, separated by commas
    """
    kept = filtering.filter_activities(log, _names(activities), mode)
    flow.note(f"{len(kept)} of {len(log)} cases, {kept.event_count} of {log.event_count} events left")
    return kept


@box(name="Start activities", group="Filter")
def start_activities(log: EventLog, activities: str = "a") -> EventLog:
    """Keeps the cases that start with one of these activities.

    activities: activity names, separated by commas
    """
    return _kept(log, filtering.filter_start_activities(log, _names(activities)))


@box(name="End activities", group="Filter")
def end_activities(log: EventLog, activities: str = "d") -> EventLog:
    """Keeps the cases that end with one of these activities.

    activities: activity names, separated by commas
    """
    return _kept(log, filtering.filter_end_activities(log, _names(activities)))


@box(name="Case length", group="Filter")
def case_length(log: EventLog, minimum: int = 1, maximum: int = 100) -> EventLog:
    """Keeps the cases with between *minimum* and *maximum* events.

    minimum: the fewest events a kept case has
    maximum: the most events a kept case has
    """
    return _kept(log, filtering.filter_case_length(log, minimum, maximum))


@box(name="Time frame", group="Filter")
def time_frame(log: EventLog, start: str = "2000-01-01", end: str = "2100-01-01",
               mode: Literal["contained", "started", "intersecting"] = "contained") -> EventLog:
    """Keeps the cases contained in, started in, or touching the period.

    start: the first day, as YYYY-MM-DD
    end: the last day, as YYYY-MM-DD
    """
    from datetime import datetime, timezone
    begin = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    finish = datetime.fromisoformat(end).replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
    if not any(event.timestamp for trace in log for event in trace):
        flow.note("The log has no timestamps, so no case falls in any period")
    return _kept(log, filtering.filter_time_frame(log, begin, finish, mode))


@box(name="Case attribute", group="Filter")
def case_attribute(log: EventLog, attribute: str = "Item Category", values: str = "",
                   mode: Literal["keep", "remove"] = "keep") -> EventLog:
    """Keeps (or removes) the cases whose attribute has one of these values,
    e.g. one item category of a purchase-to-pay log. Sweep *values* to get
    one log, and so one model, per value.

    attribute: the case attribute, e.g. Item Category, Company, Vendor
    values: the values, separated by semicolons (a value may contain commas)
    """
    from ...mining.cases import cases
    wanted = {part.strip() for part in values.split(";") if part.strip()}
    if not wanted:
        raise ValueError(f"Give the values of {attribute} to {mode}, separated by semicolons")
    seen: dict[str, int] = {}
    kept = []
    for case in cases(log):
        value = case.attributes.get(attribute)
        text = "" if value is None else str(value)
        seen[text] = seen.get(text, 0) + 1
        if (text in wanted) == (mode == "keep"):
            kept.append(case.index)
    missing = sorted(wanted - set(seen))
    if missing:
        flow.note(f"No case has {attribute} = {', '.join(missing)}; it has "
                  + ", ".join(f"{v or '–'} ({n:,})" for v, n in sorted(seen.items(), key=lambda x: -x[1])[:8]))
    result = log.filtered(kept)
    result.attributes["concept:name"] = f"{log.name} · {attribute} {'=' if mode == 'keep' else '≠'} {'; '.join(sorted(wanted))}"
    return _kept(log, result)
