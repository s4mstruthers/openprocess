"""Performance analysis of a timed coloured net: many seeded runs, and what
they say about every transition and place.

The simulator plays the model out; this module watches the markings go by
and keeps the numbers a performance analyst asks for -- throughput,
utilisation, waiting times, queue lengths -- the way CPN Tools' monitors
do, but without writing one per place.  Everything is read off the firing
history: the marking before and after each firing, and the clock.

The definitions
---------------
Every run starts at the initial marking at time 0 and fires ``steps`` times,
or until the model is dead.  Statistics are collected after the first
``warm_up`` time units of each run (the usual way to leave the empty-system
start out of a steady-state estimate); the observed span of a run is
``end - warm_up``, where ``end`` is the clock when the run stopped.

For a **transition**:

* *firings*: how many times it fired at or after ``warm_up``;
* *throughput*: firings per time unit of the span;
* *utilisation*: the share of the span during which one of its firings was
  still in progress.  A firing at time ``t`` whose output tokens carry time
  stamps is in progress from ``t`` until the latest stamp it produced (the
  transition's delay plus the arc's ``@+``, as in CPN Tools); the intervals
  of all its firings are united, so parallel firings do not count twice and
  the share stays within 0 and 1.  A firing that produces no delayed token
  is over at once.

For a **place**:

* *waiting time* of a token: from the moment it became available in the
  place (its time stamp; the clock at which it was put there, for an untimed
  place) to the moment a firing consumed it.  The delay a token carries
  models work being done on it, so the waiting time starts when that work is
  over and the token sits ready; a resource token that is taken the moment
  it is free waits 0.  Time stamps make the token identity explicit; in an
  untimed place the oldest token of the value is taken first (FIFO), which
  is also what the timed rule does.  Observations are made at consumption,
  so a token that arrived during the warm-up but was consumed after it
  counts.  A firing that puts back a token identical to one it took (same
  value, same stamp, same place) is read as that token staying put;
* *mean tokens*: the time-averaged number of tokens in the place, counting
  every token of the marking, including those stamped for the future;
* *occupancy*: the share of the span during which the place was not empty.

An **untimed** net has no clock to speak of, so the firing count stands in
for time: firing ``k`` happens at "time" ``k`` (counting from 0), the span
is the number of firings, ``warm_up`` is a number of firings, throughput is
firings per step, waiting times are in steps, and utilisation is not
defined (no token carries a delay).  :attr:`PerformanceResult.notes` says
so.

Run ``k`` (from 0) uses seed ``seed + k``, so the same ``seed`` gives the
same table and run ``k`` is the run *Simulate CPN* makes with that seed.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..ml.multiset import TimedMultiset
from ..model.net import CPNet, Marking
from .simulator import DeadMarkingError, Simulator

#: The columns of :meth:`PerformanceResult.rows`.
COLUMNS = ["kind", "name", "firings", "throughput", "utilisation", "consumed",
           "waiting mean", "waiting median", "waiting max", "mean tokens", "occupancy"]


@dataclass
class TransitionStats:
    """One transition in one run."""

    name: str
    firings: int = 0
    #: Firings per time unit (per step for an untimed net); ``None`` when
    #: the span is zero.
    throughput: float | None = None
    #: Share of the span during which a firing was in progress; ``None`` for
    #: an untimed net or a zero span.
    utilisation: float | None = None
    #: ``(start, end)`` of every firing that produced a delayed token.
    intervals: list[tuple[float, float]] = field(default_factory=list, repr=False)


@dataclass
class PlaceStats:
    """One place in one run."""

    name: str
    #: The waiting time of every token consumed after the warm-up.
    waits: list[float] = field(default_factory=list, repr=False)
    mean_tokens: float | None = None
    occupancy: float | None = None
    #: Accumulators: token-time units and non-empty time, after the warm-up.
    area: float = 0.0
    nonempty: float = 0.0

    @property
    def consumed(self) -> int:
        return len(self.waits)


@dataclass
class RunStats:
    """One seeded run."""

    seed: int
    #: Firings taken in all (including the warm-up).
    firings: int
    #: The clock when the run stopped (the number of firings, untimed).
    end: float
    #: ``end - warm_up`` when positive, else 0.
    span: float
    #: The run stopped because the model was dead, before ``steps`` firings.
    dead: bool
    #: Keyed by transition id.
    transitions: dict[str, TransitionStats]
    #: Keyed by marking key (fused places and a port with its socket share one).
    places: dict[str, PlaceStats]

    @property
    def counted_firings(self) -> int:
        """Firings at or after the warm-up."""
        return sum(t.firings for t in self.transitions.values())

    @property
    def throughput(self) -> float | None:
        """Firings of the whole model per time unit of the span."""
        return self.counted_firings / self.span if self.span > 0 else None

    @property
    def waits(self) -> list[float]:
        return [w for p in self.places.values() for w in p.waits]

    @property
    def waiting_mean(self) -> float | None:
        waits = self.waits
        return statistics.fmean(waits) if waits else None


@dataclass
class PerformanceResult:
    """What ``runs`` seeded runs of a net say about its transitions and places.

    :attr:`runs` holds every run's numbers; :meth:`rows` averages them
    (waiting times are pooled over the runs) into one row per transition and
    per place under :data:`COLUMNS`, after a row for the whole model.
    """

    net_name: str
    timed: bool
    steps: int
    warm_up: float
    seed: int
    runs: list[RunStats]
    notes: list[str] = field(default_factory=list)

    columns = COLUMNS

    @property
    def unit(self) -> str:
        """What throughput is per: a time unit, or a step for an untimed net."""
        return "time unit" if self.timed else "step"

    def samples(self, measure: str = "throughput") -> list[float]:
        """One number per run: the model's throughput, or its mean waiting
        time (``measure="waiting time"``); runs where it is undefined are
        left out."""
        if measure == "waiting time":
            values = [run.waiting_mean for run in self.runs]
        elif measure == "throughput":
            values = [run.throughput for run in self.runs]
        else:
            raise ValueError(f"unknown measure {measure!r}: throughput or waiting time")
        return [float(v) for v in values if v is not None]

    def rows(self) -> list[list[Any]]:
        rows: list[list[Any]] = []
        waits = [w for run in self.runs for w in run.waits]
        rows.append(["model", self.net_name, _mean(run.counted_firings for run in self.runs),
                     _mean(run.throughput for run in self.runs), None,
                     _mean(len(run.waits) for run in self.runs),
                     *_waiting(waits), None, None])
        for key in (self.runs[0].transitions if self.runs else {}):
            per_run = [run.transitions[key] for run in self.runs]
            rows.append(["transition", per_run[0].name, _mean(t.firings for t in per_run),
                         _mean(t.throughput for t in per_run), _mean(t.utilisation for t in per_run),
                         None, None, None, None, None, None])
        for key in (self.runs[0].places if self.runs else {}):
            per_run = [run.places[key] for run in self.runs]
            pooled = [w for p in per_run for w in p.waits]
            rows.append(["place", per_run[0].name, None, None, None, _mean(p.consumed for p in per_run),
                         *_waiting(pooled), _mean(p.mean_tokens for p in per_run),
                         _mean(p.occupancy for p in per_run)])
        return rows

    def summary(self, run: RunStats) -> str:
        """One line about a run, for a report."""
        index = self.runs.index(run)
        stopped = "dead" if run.dead else f"stopped after {self.steps} firings"
        rate = f"{run.throughput:.4g} firings per {self.unit}" if run.throughput is not None else "no span"
        waiting = f", mean waiting time {run.waiting_mean:.4g}" if run.waiting_mean is not None else ""
        return (f"run {index} (seed {run.seed}): {run.firings} firings, "
                f"{'time' if self.timed else 'step'} 0 to {run.end:g}, {stopped}; "
                f"{run.counted_firings} counted after the warm-up, {rate}{waiting}")


# ---------------------------------------------------------------------------
def analyse_performance(net: CPNet, runs: int = 10, steps: int = 1000, warm_up: float = 0.0,
                        seed: int = 0) -> PerformanceResult:
    """Run ``net`` ``runs`` times (seeds ``seed``, ``seed + 1``, ...) for
    ``steps`` firings each, or until it is dead, and collect the performance
    numbers defined in the module docstring, leaving out the first
    ``warm_up`` time units (firings, for an untimed net) of each run."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if warm_up < 0:
        raise ValueError("warm_up must not be negative")
    timed = any((cs := net.colour_set_of(place)) is not None and cs.timed for place in net.all_places())
    transitions = _transition_names(net)
    places = _place_names(net)
    timed_keys = {net.marking_key(place.id) for place in net.all_places()
                  if (cs := net.colour_set_of(place)) is not None and cs.timed}
    adjacent = _adjacent_keys(net)
    results = [_run(net, seed + k, steps, float(warm_up), timed, transitions, places, timed_keys, adjacent)
               for k in range(runs)]
    result = PerformanceResult(net.name, timed, steps, float(warm_up), seed, results)
    if not timed:
        result.notes.append("The net is untimed: no place has a timed colour set, so the clock never moves. "
                            "Firings stand in for time: throughput is firings per step, the warm-up is a "
                            "number of firings, waiting times are in steps, and utilisation is not defined.")
    if timed and all(run.span == 0 for run in results):
        result.notes.append("No model time passed after the warm-up in any run, so throughput, utilisation, "
                            "mean tokens and occupancy are undefined; use fewer warm-up time units or more steps.")
    return result


def _run(net: CPNet, seed: int, steps: int, warm_up: float, timed: bool,
         transitions: dict[str, str], places: dict[str, str], timed_keys: set[str],
         adjacent: dict[str, list[str]]) -> RunStats:
    simulator = Simulator(net, seed=seed)
    tstats = {tid: TransitionStats(name) for tid, name in transitions.items()}
    pstats = {key: PlaceStats(name) for key, name in places.items()}
    before = simulator.marking
    # Arrival time of every token in an untimed place, oldest first.
    fifo: dict[str, dict[Any, deque[float]]] = {}
    for key in places:
        if key not in timed_keys:
            fifo[key] = {value: deque([0.0] * count) for value, count in before.get(key).items()}
    last = 0.0
    fired = 0
    dead = False
    for _ in range(steps):
        try:
            element = simulator.step()
        except DeadMarkingError:
            dead = True
            break
        now = float(simulator.clock) if timed else float(fired)
        _accumulate(pstats, before, last, now, warm_up)
        last = now
        after = simulator.marking
        counted = now >= warm_up
        busy_until = now
        for key in adjacent.get(element.transition_id, ()):
            consumed, produced = _diff(before.get(key), after.get(key))
            stats = pstats[key]
            if key in timed_keys:
                for (_value, stamp), count in consumed.items():
                    if counted:
                        stats.waits.extend([now - float(stamp)] * count)
                for (_value, stamp), count in produced.items():
                    busy_until = max(busy_until, float(stamp))
            else:
                queues = fifo.setdefault(key, {})
                for (value, _stamp), count in consumed.items():
                    queue = queues.get(value)
                    for _ in range(count):
                        arrived = queue.popleft() if queue else now
                        if counted:
                            stats.waits.append(now - arrived)
                for (value, _stamp), count in produced.items():
                    queues.setdefault(value, deque()).extend([now] * count)
        transition = tstats.get(element.transition_id)
        if transition is not None:
            if counted:
                transition.firings += 1
            if busy_until > now:
                transition.intervals.append((now, busy_until))
        before = after
        simulator.log.clear()        # the markings are read as they go by; no need to keep them
        fired += 1
    end = float(simulator.clock) if timed else float(fired)
    _accumulate(pstats, before, last, end, warm_up)
    span = max(0.0, end - warm_up)
    for transition in tstats.values():
        if span > 0:
            transition.throughput = transition.firings / span
            if timed:
                transition.utilisation = _covered(transition.intervals, warm_up, end) / span
    for stats in pstats.values():
        if span > 0:
            stats.mean_tokens = stats.area / span
            stats.occupancy = stats.nonempty / span
    return RunStats(seed, fired, end, span, dead, tstats, pstats)


# ---------------------------------------------------------------------------
def _accumulate(pstats: dict[str, PlaceStats], marking: Marking, start: float, end: float,
                warm_up: float) -> None:
    """``marking`` held from ``start`` to ``end``: add the part after the warm-up."""
    begin = max(start, warm_up)
    if end <= begin:
        return
    dt = end - begin
    for key, stats in pstats.items():
        size = marking.get(key).size()
        if size:
            stats.area += size * dt
            stats.nonempty += dt


def _diff(before: Any, after: Any) -> tuple[dict[tuple[Any, Any], int], dict[tuple[Any, Any], int]]:
    """Tokens a firing took from a place and tokens it put there, as
    ``{(value, stamp): count}`` (the stamp is ``None`` in an untimed place)."""
    old, new = _counts(before), _counts(after)
    consumed = {k: c - new.get(k, 0) for k, c in old.items() if c > new.get(k, 0)}
    produced = {k: c - old.get(k, 0) for k, c in new.items() if c > old.get(k, 0)}
    return consumed, produced


def _counts(tokens: Any) -> dict[tuple[Any, Any], int]:
    if isinstance(tokens, TimedMultiset):
        return {(value, stamp): count for value, stamp, count in tokens.items()}
    return {(value, None): count for value, count in tokens.items()}


def _covered(intervals: list[tuple[float, float]], low: float, high: float) -> float:
    """Length of the union of ``intervals`` clipped to ``[low, high]``."""
    clipped = sorted((max(a, low), min(b, high)) for a, b in intervals if min(b, high) > max(a, low))
    total = 0.0
    current_start = current_end = None
    for a, b in clipped:
        if current_end is None or a > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = a, b
        elif b > current_end:
            current_end = b
    if current_end is not None:
        total += current_end - current_start
    return total


def _transition_names(net: CPNet) -> dict[str, str]:
    """Transition id to display name; a name two pages share gets its page."""
    transitions = [t for t in net.all_transitions() if not t.is_substitution]
    counts: dict[str, int] = {}
    for t in transitions:
        counts[t.name] = counts.get(t.name, 0) + 1
    names = {}
    for t in transitions:
        page = net.page_of(t)
        names[t.id] = f"{t.name} ({page.name})" if counts[t.name] > 1 and page else t.name
    return names


def _place_names(net: CPNet) -> dict[str, str]:
    """Marking key to display name, one entry per marking (fused places and
    a port with its socket are one place to the simulator)."""
    names: dict[str, str] = {}
    for place in net.all_places():
        key = net.marking_key(place.id)
        if key not in names:
            owner = net.find_place(key)
            names[key] = owner.name if owner else place.name
    return names


def _adjacent_keys(net: CPNet) -> dict[str, list[str]]:
    """Transition id to the marking keys its arcs touch."""
    adjacent: dict[str, list[str]] = {}
    for arc in net.all_arcs():
        key = net.marking_key(arc.place_id)
        keys = adjacent.setdefault(arc.transition_id, [])
        if key not in keys:
            keys.append(key)
    return adjacent


def _mean(values) -> float | None:
    """Mean of the values that are defined, rounded; ``None`` if none is."""
    kept = [float(v) for v in values if v is not None]
    return round(statistics.fmean(kept), 4) if kept else None


def _waiting(waits: list[float]) -> list[float | None]:
    """``[mean, median, max]`` of waiting times, or three ``None``."""
    if not waits:
        return [None, None, None]
    return [round(statistics.fmean(waits), 4), round(statistics.median(waits), 4), round(max(waits), 4)]
