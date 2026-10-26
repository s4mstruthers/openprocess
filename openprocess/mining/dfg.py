"""Directly-follows graphs (DFGs) -- the "process map" of tools like Disco.

Definition
----------
Activity ``b`` *directly follows* ``a`` in a log, written ``a >_L b``, iff
some trace contains ``a`` immediately followed by ``b``.  The DFG has one
node per activity and an edge ``a -> b`` weighted by *how often* that
happened, plus two artificial nodes ▶ (start) and ■ (end) with edges to the
first and from the last activity of every trace.

A DFG is not a Petri net: it has no concurrency, and every path through it is
allowed, so it usually *overgeneralises* (reading: Leemans, Poppe & Wynn,
"Directly-follows-based process mining").  It is nonetheless the most useful
first look at a log, and the Inductive Miner family works directly on it.

Performance
-----------
When events carry timestamps, each edge also records the time elapsed between
``a`` and the ``b`` that directly followed it.  Mean and median are shown in
performance mode.

Simplification
--------------
Real logs produce "spaghetti".  :meth:`DFG.simplified` keeps a fraction of
the activities (most frequent first) and a fraction of the edges, while
guaranteeing that every kept activity stays connected: each keeps its most
frequent incoming and outgoing edge.  This is the same idea as Disco's two
sliders.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .log import Classifier, EventLog, SimpleLog

START = "▶"
END = "■"


@dataclass
class DFG:
    activities: Counter = field(default_factory=Counter)       # activity -> events
    edges: Counter = field(default_factory=Counter)            # (a, b) -> count
    start: Counter = field(default_factory=Counter)            # activity -> traces
    end: Counter = field(default_factory=Counter)
    durations: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    trace_count: int = 0
    empty_traces: int = 0
    #: Each edge's (mean, median) duration once asked for: a map of a million
    #: events is redrawn at every move of a slider, and the durations do not
    #: change.  Simplified maps share the entries.
    _stats: dict = field(default_factory=dict, repr=False, compare=False)

    # -- queries --------------------------------------------------------
    def successors(self, activity: str) -> set[str]:
        return {b for (a, b) in self.edges if a == activity}

    def predecessors(self, activity: str) -> set[str]:
        return {a for (a, b) in self.edges if b == activity}

    def _duration_stats(self, edge: tuple[str, str]) -> tuple[float | None, float | None]:
        stats = self._stats.get(edge)
        if stats is None:
            values = self.durations.get(edge)
            stats = (statistics.fmean(values), statistics.median(values)) if values else (None, None)
            self._stats[edge] = stats
        return stats

    def mean_duration(self, edge: tuple[str, str]) -> float | None:
        return self._duration_stats(edge)[0]

    def median_duration(self, edge: tuple[str, str]) -> float | None:
        return self._duration_stats(edge)[1]

    def all_edges(self) -> list[tuple[str, str, int]]:
        """Edges including the artificial start/end ones, as (a, b, count)."""
        edges = [(START, a, n) for a, n in self.start.items()]
        edges += [(a, b, n) for (a, b), n in self.edges.items()]
        edges += [(a, END, n) for a, n in self.end.items()]
        return edges

    # -- simplification ------------------------------------------------
    def simplified(self, activity_fraction: float = 1.0, edge_fraction: float = 1.0) -> "DFG":
        """Keep the top ``activity_fraction`` of activities and ``edge_fraction`` of edges.

        Fractions are in [0, 1].  At least one activity is always kept.
        Removing activities *projects* them out: we do not reconnect their
        neighbours, because that would invent directly-follows pairs that
        never occurred.  Edges touching removed activities disappear.
        """
        ranked = [a for a, _ in sorted(self.activities.items(), key=lambda x: (-x[1], x[0]))]
        keep_count = max(1, round(len(ranked) * activity_fraction))
        kept = set(ranked[:keep_count])

        candidates = [(a, b, n) for (a, b, n) in self.all_edges()
                      if (a in kept or a == START) and (b in kept or b == END)]
        candidates.sort(key=lambda edge: (-edge[2], edge[0], edge[1]))
        keep_edges = set((a, b) for a, b, _ in candidates[:round(len(candidates) * edge_fraction)])

        # Connectivity guarantee: best incoming and best outgoing per activity.
        for activity in kept:
            incoming = [e for e in candidates if e[1] == activity]
            outgoing = [e for e in candidates if e[0] == activity]
            if incoming:
                keep_edges.add(incoming[0][:2])
            if outgoing:
                keep_edges.add(outgoing[0][:2])

        result = DFG(trace_count=self.trace_count, empty_traces=self.empty_traces)
        result.activities = Counter({a: self.activities[a] for a in kept})
        for a, b, n in candidates:
            if (a, b) not in keep_edges:
                continue
            if a == START:
                result.start[b] = n
            elif b == END:
                result.end[a] = n
            else:
                result.edges[(a, b)] = n
                if (a, b) in self.durations:
                    result.durations[(a, b)] = self.durations[(a, b)]
                    if (a, b) in self._stats:
                        result._stats[(a, b)] = self._stats[(a, b)]
        return result


def dfg_from_simple_log(log: SimpleLog) -> DFG:
    """DFG of a multiset of sequences (no performance information)."""
    dfg = DFG()
    for sequence, count in log.items():
        dfg.trace_count += count
        if not sequence:
            dfg.empty_traces += count
            continue
        for activity in sequence:
            dfg.activities[activity] += count
        dfg.start[sequence[0]] += count
        dfg.end[sequence[-1]] += count
        for a, b in zip(sequence, sequence[1:]):
            dfg.edges[(a, b)] += count
    return dfg


def discover_dfg(log: EventLog | SimpleLog, classifier: Classifier | None = None) -> DFG:
    """DFG of a log; with an :class:`EventLog`, edge durations are recorded too."""
    if not isinstance(log, EventLog):
        return dfg_from_simple_log(log)

    classifier = classifier or log.default_classifier()
    if log.columnar:
        return _dfg_from_columns(log, classifier)
    dfg = DFG()
    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trace in log.traces:
        dfg.trace_count += 1
        events = [e for e in trace.events if classifier.accepts(e)]
        if not events:
            dfg.empty_traces += 1
            continue
        labels = [classifier.label(e) for e in events]
        dfg.activities.update(labels)
        dfg.start[labels[0]] += 1
        dfg.end[labels[-1]] += 1
        for (first, second), (a, b) in zip(zip(events, events[1:]), zip(labels, labels[1:])):
            dfg.edges[(a, b)] += 1
            if first.timestamp is not None and second.timestamp is not None:
                durations[(a, b)].append((second.timestamp - first.timestamp).total_seconds())
    dfg.durations = dict(durations)
    return dfg


def _dfg_from_columns(log: EventLog, classifier: Classifier) -> DFG:
    """The same counts and durations, from a columnar log's arrays at once:
    the labels as codes, the pairs of events that follow each other within
    a case, their counts and the time between them, grouped per edge."""
    import numpy as np
    from .columns import NO_TIME
    store = log.traces.store
    dfg = DFG(trace_count=store.case_count)
    codes, table = store.label_codes(classifier.keys)
    case = store.case_of_event()
    micros = store.micros_array
    mask = store.accepted_mask(classifier.lifecycles)
    if mask is not None:
        codes, case, micros = codes[mask], case[mask], micros[mask]
    if len(codes) == 0:
        dfg.empty_traces = store.case_count
        return dfg
    labels = np.asarray(table, dtype=object)
    dfg.activities = Counter({str(labels[code]): int(n) for code, n in enumerate(np.bincount(codes)) if n})
    # The first and last kept event of each case that has one.
    boundary = np.flatnonzero(np.concatenate(([True], case[1:] != case[:-1])))
    ends = np.concatenate((boundary[1:] - 1, [len(case) - 1]))
    dfg.empty_traces = store.case_count - len(boundary)
    for counter, where in ((dfg.start, boundary), (dfg.end, ends)):
        for code, n in enumerate(np.bincount(codes[where], minlength=len(table))):
            if n:
                counter[str(labels[code])] = int(n)
    # Directly-follows pairs: consecutive kept events of the same case.
    same = case[1:] == case[:-1]
    a, b = codes[:-1][same], codes[1:][same]
    key = a * len(table) + b
    unique, counts = np.unique(key, return_counts=True)
    for k, n in zip(unique.tolist(), counts.tolist()):
        dfg.edges[(str(labels[k // len(table)]), str(labels[k % len(table)]))] = n
    before, after = micros[:-1][same], micros[1:][same]
    timed = (before != NO_TIME) & (after != NO_TIME)
    if timed.any():
        seconds = (after[timed] - before[timed]) / 1_000_000
        keys = key[timed]
        order = np.argsort(keys, kind="stable")             # per edge, in the log's order
        sorted_keys = keys[order]
        starts = np.flatnonzero(np.concatenate(([True], sorted_keys[1:] != sorted_keys[:-1])))
        for k, values in zip(sorted_keys[starts].tolist(), np.split(seconds[order], starts[1:])):
            dfg.durations[(str(labels[k // len(table)]), str(labels[k % len(table)]))] = values.tolist()
    return dfg
