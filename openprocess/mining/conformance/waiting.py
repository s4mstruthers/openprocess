"""Where a case waits: time in the places of a model
(van der Aalst, *Process Mining*, §9.4, "Time and Probabilities").

Token replay says *whether* the log fits the model.  Replaying it with the
events' timestamps says *where the time goes*: a token put in a place at
10:00 and taken out at 14:00 waited four hours there.  A place between
"check stock" and "ship" with a long waiting time is where orders queue
up: a bottleneck.

How it is computed
------------------
Every case is replayed as :mod:`.token_replay` replays it (the same choices
of transitions and silent steps), and each firing is given a time:

* a transition fired for an event gets that event's timestamp;
* a silent step fired to enable an event's transition gets that event's
  timestamp too (it happened at some unknown moment before; "at the latest
  then" is all the log says);
* the initial marking is put down at the case's first event;
* the silent steps that finish a case, and the final marking's removal,
  happen at the case's last event.

When a transition fires it takes, from each input place, the token that has
been there longest (first in, first out), and that token's stay is recorded
for the place.  A token that had to be made up (a *missing* token of the
replay) was never produced, so it has no stay and is not counted; a token
left behind (*remaining*) was never taken, and is not counted either.  The
result says how many of each there were per place, because a place with
many of them is measured on little evidence.

Only cases whose events all have a timestamp, in non-decreasing order, are
measured; the result says how many were not.

Reading the result
------------------
A stay is *waiting* in a place between two activities, and *synchronisation*
in a place before a join (the token waits for the other branch).  The
replay cannot tell them apart and does not try to.  With only completion
events in the log (one timestamp per activity, the usual case) the stay in
the place before an activity includes the time the activity itself took.

This is OpenProcess's reading of the replay with timestamps the book
describes; the first-in-first-out choice among tokens in one place is a
convention (in a safe net, where a place holds at most one token, there is
nothing to choose).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

from ..cases import cases
from ..log import EventLog
from ..petrinet import PetriNet
from .token_replay import _SilentSearch, replay_trace


@dataclass
class PlaceWaiting:
    """The stays of tokens in one place, in seconds."""

    place: str
    #: labels of the transitions that put tokens in (τ for a silent one), sorted
    after: list[str] = field(default_factory=list)
    #: labels of the transitions that take tokens out, sorted
    before: list[str] = field(default_factory=list)
    stays: list[float] = field(default_factory=list)
    #: tokens that had to be made up here / were left behind here (not measured)
    missing: int = 0
    remaining: int = 0

    @property
    def tokens(self) -> int:
        return len(self.stays)

    @property
    def total(self) -> float:
        return sum(self.stays)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.stays) if self.stays else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.stays) if self.stays else 0.0

    @property
    def longest(self) -> float:
        return max(self.stays) if self.stays else 0.0

    def percentile(self, share: float) -> float:
        """The stay that ``share`` (0 to 1) of the tokens did not exceed."""
        if not self.stays:
            return 0.0
        ordered = sorted(self.stays)
        return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


@dataclass
class WaitingResult:
    net: PetriNet
    places: list[PlaceWaiting] = field(default_factory=list)       # the most total waiting first
    #: cases measured, and cases left out for want of usable timestamps
    cases: int = 0
    skipped: int = 0

    def place(self, place_id: str) -> PlaceWaiting:
        return next(p for p in self.places if p.place == place_id)

    @property
    def total(self) -> float:
        return sum(p.total for p in self.places)


def _labels(net: PetriNet, transitions) -> list[str]:
    return sorted({net.transitions[t].label or "τ" for t in transitions})


def waiting_times(net: PetriNet, log: EventLog) -> WaitingResult:
    """Replay ``log`` on ``net`` with its timestamps; see the module docstring."""
    by_place = {place: PlaceWaiting(place, _labels(net, net.preset(place)), _labels(net, net.postset(place)))
                for place in net.places}
    result = WaitingResult(net)
    searcher, paths = _SilentSearch(net), {}
    fired: dict[tuple[str, ...], list[tuple[int, str]]] = {}       # variant -> what fired for which event
    initial, final = net.initial_marking, net.final_marking
    for case in cases(log):
        times = case.times
        if not times or any(t is None for t in times) or any(b < a for a, b in zip(times, times[1:])):
            result.skipped += 1
            continue
        result.cases += 1
        trace = tuple(case.activities)
        steps = fired.get(trace)
        if steps is None:
            steps = fired[trace] = []
            replay_trace(net, trace, 1, paths, searcher, steps)
        tokens: dict[str, deque] = {place: deque() for place in net.places}
        start, end = times[0], times[-1]
        for place, count in initial.items():
            tokens[place].extend([start] * count)
        for index, transition in steps:
            now = times[index] if index < len(times) else end
            for place, count in net.pre(transition).items():
                waiting, record = tokens[place], by_place[place]
                for _ in range(count):
                    if waiting:
                        record.stays.append(now - waiting.popleft())
                    else:
                        record.missing += 1
            for place, count in net.post(transition).items():
                tokens[place].extend([now] * count)
        for place, count in final.items():                          # the environment takes the final marking
            waiting, record = tokens[place], by_place[place]
            for _ in range(count):
                if waiting:
                    record.stays.append(end - waiting.popleft())
                else:
                    record.missing += 1
        for place, waiting in tokens.items():
            by_place[place].remaining += len(waiting)
    result.places = sorted(by_place.values(), key=lambda p: (-p.total, p.place))
    return result


__all__ = ["PlaceWaiting", "WaitingResult", "waiting_times"]
