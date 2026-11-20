"""Who works with whom: social networks from an event log
(van der Aalst, Reijers & Song 2005; van der Aalst, *Process Mining*, §9.3).

An event log that records *who* did each step (``org:resource``) says how
work moves between people, not only between activities.  Two of the paper's
metrics are computed here, both by counting:

**Handover of work** (one of the paper's metrics based on causality; direct succession)
    Within a case, there is a handover of work from ``i`` to ``j`` when an
    event done by ``i`` is directly followed by an event done by ``j``.
    Counted over all cases, it is the directly-follows graph of the
    *resources*: the process map with people in place of activities.  A step
    a resource hands to itself (two events in a row by the same resource) is
    counted too, and reported apart: it is work that stays where it is.

**Working together** (the paper's metric based on joint cases)
    Two resources work together when they both do something in the same
    case; the count is the number of such cases.  It is symmetric and says
    nothing about order.

An event without the attribute belongs to no resource: it is skipped, and
the two events around it are *not* joined into a handover, since nobody
knows who stood between them.  The result says how many events that were.

Any event attribute can stand in for the resource (a role, a department),
which gives the handovers between roles or departments.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from .cases import cases
from .log import KEY_RESOURCE, EventLog


@dataclass
class SocialNetwork:
    """The counts behind both metrics, for one log and one attribute."""

    by: str
    #: (from, to) -> how often work went from one to the other, self-handovers included
    handovers: Counter = field(default_factory=Counter)
    #: frozenset({a, b}) -> the number of cases both did something in
    together: Counter = field(default_factory=Counter)
    #: resource -> the number of its events
    events: Counter = field(default_factory=Counter)
    #: resource -> the number of cases it did something in
    cases: Counter = field(default_factory=Counter)
    case_count: int = 0
    #: events without the attribute (skipped)
    unknown_events: int = 0

    @property
    def resources(self) -> list[str]:
        """Every resource, the busiest first."""
        return [name for name, _count in sorted(self.events.items(), key=lambda item: (-item[1], item[0]))]

    @property
    def handover_count(self) -> int:
        """Handovers from one resource to another (not to itself)."""
        return sum(count for (giver, taker), count in self.handovers.items() if giver != taker)

    @property
    def kept_count(self) -> int:
        """Steps a resource handed to itself."""
        return sum(count for (giver, taker), count in self.handovers.items() if giver == taker)

    def handover_rows(self) -> list[tuple[str, str, int, float]]:
        """``(from, to, times, share of the handovers from "from")``, most frequent
        first; only handovers between different resources."""
        given: Counter = Counter()
        for (giver, taker), count in self.handovers.items():
            if giver != taker:
                given[giver] += count
        rows = [(giver, taker, count, count / given[giver])
                for (giver, taker), count in self.handovers.items() if giver != taker]
        return sorted(rows, key=lambda row: (-row[2], row[0], row[1]))

    def together_rows(self) -> list[tuple[str, str, int, float]]:
        """``(a, b, cases, share of all cases)``, most frequent first."""
        rows = []
        for pair, count in self.together.items():
            first, second = sorted(pair)
            rows.append((first, second, count, count / self.case_count if self.case_count else 0.0))
        return sorted(rows, key=lambda row: (-row[2], row[0], row[1]))


def social_network(log: EventLog, by: str = KEY_RESOURCE) -> SocialNetwork:
    """Count the handovers of work and the joint cases of ``log``, with the
    event attribute ``by`` as the resource."""
    network = SocialNetwork(by)
    for case in cases(log, (by,)):
        network.case_count += 1
        previous: str | None = None
        seen: set[str] = set()
        for value in case.columns[by]:
            if value is None or value == "":
                network.unknown_events += 1
                previous = None                 # nobody knows who stood here: no handover across it
                continue
            name = str(value)
            network.events[name] += 1
            seen.add(name)
            if previous is not None:
                network.handovers[(previous, name)] += 1
            previous = name
        for name in seen:
            network.cases[name] += 1
        for pair in combinations(sorted(seen), 2):
            network.together[frozenset(pair)] += 1
    return network


__all__ = ["SocialNetwork", "social_network"]
