"""Measure boxes: the questions an audit or a business asks of a real log.

How long from the invoice to its clearing?  Does the payment block go before
the goods receipt?  Did one user record an invoice and also clear it?  How
compliant is each vendor, and with how much money?  Each box answers one such
question for every case, in a table (and, where it helps, the cases
themselves, to analyse further), and works on a large log without building
its events (:func:`openprocess.mining.cases.cases`).  The BPI Challenge 2019
analysis in ``examples/bpi2019`` is built from them.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from ... import flow
from ...mining.cases import case_value, cases
from ...mining.log import EventLog
from ...mining.petrinet import PetriNet
from ..box import box
from ..types import Table

UNITS = {"hours": 3600.0, "days": 86400.0}


def _group(case, by: str) -> str:
    if not by:
        return "all cases"
    value = case.attributes.get(by)
    return "–" if value is None else str(value)


def _money(value: float) -> float:
    return round(value, 2)


@box(name="Time between", group="Measure")
def time_between(log: EventLog, start: str = "Record Invoice Receipt", end: str = "Clear Invoice",
                 by: str = "", unit: Literal["days", "hours"] = "days") -> Table:
    """Throughput: per case, the time from the first *start* to the first
    *end* after it, summarised (cases, mean, median, 90th percentile, longest),
    for all cases or per value of the case attribute *by*. A case without
    both, in that order, is not counted; the table says how many were.

    start: the activity the time starts at
    end: the activity it ends at (its first occurrence after start)
    by: a case attribute to split by, e.g. Item Category (empty: all cases)
    unit: days or hours
    """
    scale = UNITS[unit]
    durations: dict[str, list[float]] = {}
    skipped = 0
    for case in cases(log):
        first = case.first(start)
        last = case.first(end, first + 1) if first is not None else None
        if last is None or case.times[first] is None or case.times[last] is None:
            skipped += 1
            continue
        durations.setdefault(_group(case, by), []).append((case.times[last] - case.times[first]) / scale)
    rows = []
    for group, values in sorted(durations.items(), key=lambda item: -len(item[1])):
        values.sort()
        rows.append([group, len(values), round(statistics.fmean(values), 2), round(statistics.median(values), 2),
                     round(values[min(len(values) - 1, int(0.9 * len(values)))], 2), round(values[-1], 2)])
    counted = sum(len(v) for v in durations.values())
    flow.note(f"{counted:,} cases have {start} and then {end}; {skipped:,} do not and are not counted")
    return Table(f"{start} → {end}", [by or "cases", "cases", f"mean ({unit})", f"median ({unit})",
                                      f"90 % within ({unit})", f"longest ({unit})"], rows,
                 samples=[v for values in durations.values() for v in values],
                 note=f"Time from the first {start} to the first {end} after it, in {unit}.")


@dataclass
class RuleResult:
    #: The cases where *first* comes before any *then*: the ones to look into.
    cases: EventLog
    table: Table


@box(name="Order rule", group="Measure")
def order_rule(log: EventLog, first: str = "Remove Payment Block", then: str = "Record Goods Receipt",
               value: str = "Cumulative net worth (EUR)") -> RuleResult:
    """Does *first* happen before *then*? Every case falls in one row: *first*
    before any *then* (and whether *then* follows at all), *then* first, only
    one of them, or neither, with the cases' share and their total value.
    The cases where *first* comes before any *then* (with or without it
    afterwards) are the first output, to look into further; the table the second.

    first: the activity that should not come first (e.g. Remove Payment Block)
    then: the activity it should come after (e.g. Record Goods Receipt)
    value: a numeric attribute summed as the cases' value (empty: none)
    """
    labels = [f"{first} before {then}", f"{first}, and no {then} at all", f"{then} before {first}",
              f"only {then}", "neither"]
    counts, values = Counter(), Counter()
    flagged: list[int] = []
    total = 0
    for case in cases(log, (value,) if value else ()):
        total += 1
        a, b = case.first(first), case.first(then)
        if a is not None and b is None:
            row = 1
        elif a is not None and a < b:
            row = 0
        elif a is not None:
            row = 2
        elif b is not None:
            row = 3
        else:
            row = 4
        if row in (0, 1):
            flagged.append(case.index)
        counts[row] += 1
        values[row] += case_value(case, value) or 0.0
    rows = [[labels[r], counts[r], round(100 * counts[r] / max(total, 1), 2), _money(values[r])] for r in range(5)]
    flow.note(f"{counts[0] + counts[1]:,} of {total:,} cases have {first} before any {then}; "
              f"{counts[1]:,} of them never get {then}")
    matching = log.filtered(flagged)
    matching.attributes["concept:name"] = f"{log.name}: {first} before {then}"
    return RuleResult(matching, Table(f"{first} before {then}?", ["cases where", "cases", "% of cases",
                                                                  value or "value"], rows))


@box(name="Segregation of duties", group="Measure")
def segregation_of_duties(log: EventLog, first: str = "Record Invoice Receipt", second: str = "Clear Invoice",
                          by: str = "User", value: str = "Cumulative net worth (EUR)") -> Table:
    """Four eyes: the cases where the same person (the event attribute *by*)
    did both *first* and *second*, per person, with the cases' total value,
    most value first. An empty table means the duties were separated.

    first: one duty (e.g. Record Invoice Receipt)
    second: the other (e.g. Clear Invoice)
    by: the event attribute naming who did it (User, org:resource)
    value: a numeric attribute summed as the cases' value (empty: none)
    """
    keys = tuple(k for k in (by, value) if k)
    per_person: dict[str, list] = {}
    examined = 0
    for case in cases(log, keys):
        who = case.columns.get(by) or [None] * len(case.activities)
        did_first = {w for a, w in zip(case.activities, who) if a == first and w is not None}
        did_second = {w for a, w in zip(case.activities, who) if a == second and w is not None}
        if did_first and did_second:
            examined += 1
        for person in did_first & did_second:
            entry = per_person.setdefault(str(person), [0, 0.0])
            entry[0] += 1
            entry[1] += case_value(case, value) or 0.0
    rows = [[person, n, _money(v)] for person, (n, v) in sorted(per_person.items(), key=lambda item: -item[1][1])]
    flow.note(f"{sum(r[1] for r in rows):,} of the {examined:,} cases with both {first} and {second} had them "
              f"done by the same {by}, by {len(rows)} different people")
    return Table(f"Same {by}: {first} and {second}", [by, "cases", value or "value"], rows)


@box(name="Compliance by attribute", group="Measure")
def compliance_by(model: PetriNet, log: EventLog, attribute: str = "Vendor",
                  value: str = "Cumulative net worth (EUR)", colour: str = "Item Type") -> Table:
    """For every value of a case attribute (each vendor, say): how many cases,
    the share that fit the model (replay with no missing or remaining
    tokens), and their total value; with *colour*, the most common value of
    another case attribute, to colour a bubble chart by.

    attribute: the case attribute to group by (Vendor, Company, ...)
    value: a numeric attribute summed as the cases' value (empty: none)
    colour: another case attribute shown per group (empty: none)
    """
    from ...mining.conformance.token_replay import token_replay
    replay = token_replay(model, log.simple_log())
    fits = {t.trace: t.fits for t in replay.traces}
    groups: dict[str, list] = {}
    sequences = log.sequences()
    for case in cases(log, (value,) if value else ()):
        key = case.attributes.get(attribute)
        entry = groups.setdefault("–" if key is None else str(key), [0, 0, 0.0, Counter()])
        entry[0] += 1
        entry[1] += bool(fits.get(sequences[case.index]))
        entry[2] += case_value(case, value) or 0.0
        if colour:
            entry[3][str(case.attributes.get(colour, "–"))] += 1
    rows = []
    for key, (n, fit, money, kinds) in sorted(groups.items(), key=lambda item: -item[1][0]):
        row = [key, n, round(100 * fit / n, 2), _money(money)]
        if colour:
            row.append(kinds.most_common(1)[0][0] if kinds else "–")
        rows.append(row)
    flow.note(f"{len(rows):,} values of {attribute}; {replay.fitting_traces:,} of {replay.trace_count:,} cases fit "
              f"the model ({100 * replay.fitting_traces / max(replay.trace_count, 1):.1f} %)")
    columns = [attribute, "cases", "compliance (%)", value or "value"] + ([colour] if colour else [])
    return Table(f"Compliance by {attribute}", columns, rows)


__all__ = ["RuleResult", "compliance_by", "order_rule", "segregation_of_duties", "time_between"]
