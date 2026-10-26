"""Token-based replay (van der Aalst, *Process Mining*, §8.2).

Replay each trace on the net and *count tokens*:

``p`` produced
    tokens put into places (the environment first produces the initial
    marking, then every fired transition produces its output tokens);
``c`` consumed
    tokens taken out (every fired transition consumes its inputs, and at the
    end the environment consumes the final marking);
``m`` missing
    tokens that had to be *created artificially* because the transition for
    the next event was not enabled -- evidence the log did something the
    model did not allow;
``r`` remaining
    tokens left behind after the final marking was consumed -- evidence the
    model expected something the log did not do.

Fitness of a trace σ on net N::

    fitness(σ, N) = ½ (1 − m / c) + ½ (1 − r / p)

It is 1 exactly when nothing was missing or remaining.  For a whole log the
four counts are summed over all traces (weighted by frequency) before the
formula is applied.

Silent transitions and duplicates
---------------------------------
Events never refer to τ transitions, so the replayer may fire them on its
own: when the transition for the next event is not enabled, it first looks
for a (shortest) sequence of silent transitions that enables it.  With
duplicate labels, it prefers a transition that is enabled, then one reachable
through silent moves, then the one missing the fewest tokens.  These are
heuristics -- token replay is fast but not always optimal, which is why
alignments exist (see :mod:`alignments`).

Diagnostics
-----------
Per place we keep the total missing and remaining tokens, and per transition
how often it fired with missing tokens.  The app paints these on the model
(ProM's "replay" view): a red ``−n`` badge means the log wanted a token here
that was not there; a ``+n`` badge means a token was left behind.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from ..log import SimpleLog
from ..petrinet import Marking, PetriNet


@dataclass
class TraceReplay:
    trace: tuple[str, ...]
    count: int
    produced: int
    consumed: int
    missing: int
    remaining: int
    #: activities that do not occur in the model at all
    unknown_activities: list[str] = field(default_factory=list)
    #: indices of events whose transition was not enabled
    problem_events: list[int] = field(default_factory=list)
    missing_by_place: Counter = field(default_factory=Counter)
    remaining_by_place: Counter = field(default_factory=Counter)
    fired: Counter = field(default_factory=Counter)             # transition id -> times
    forced: Counter = field(default_factory=Counter)            # fired with missing tokens

    @property
    def fitness(self) -> float:
        return _fitness(self.produced, self.consumed, self.missing, self.remaining)

    @property
    def fits(self) -> bool:
        return self.missing == 0 and self.remaining == 0 and not self.unknown_activities


def _fitness(p: int, c: int, m: int, r: int) -> float:
    part_missing = 1 - m / c if c else 1.0
    part_remaining = 1 - r / p if p else 1.0
    return 0.5 * part_missing + 0.5 * part_remaining


@dataclass
class ReplayResult:
    net: PetriNet
    traces: list[TraceReplay]           # one per variant, most frequent first

    def _total(self, attribute: str) -> int:
        return sum(getattr(t, attribute) * t.count for t in self.traces)

    @property
    def produced(self) -> int:
        return self._total("produced")

    @property
    def consumed(self) -> int:
        return self._total("consumed")

    @property
    def missing(self) -> int:
        return self._total("missing")

    @property
    def remaining(self) -> int:
        return self._total("remaining")

    @property
    def fitness(self) -> float:
        """Log-level fitness from the summed counts."""
        return _fitness(self.produced, self.consumed, self.missing, self.remaining)

    @property
    def fitting_traces(self) -> int:
        return sum(t.count for t in self.traces if t.fits)

    @property
    def trace_count(self) -> int:
        return sum(t.count for t in self.traces)

    def place_totals(self) -> tuple[Counter, Counter]:
        """(missing, remaining) per place, frequency-weighted."""
        missing, remaining = Counter(), Counter()
        for t in self.traces:
            for place, n in t.missing_by_place.items():
                missing[place] += n * t.count
            for place, n in t.remaining_by_place.items():
                remaining[place] += n * t.count
        return missing, remaining

    def transition_totals(self) -> tuple[Counter, Counter]:
        """(fired, forced) per transition, frequency-weighted."""
        fired, forced = Counter(), Counter()
        for t in self.traces:
            for transition, n in t.fired.items():
                fired[transition] += n * t.count
            for transition, n in t.forced.items():
                forced[transition] += n * t.count
        return fired, forced


# ---------------------------------------------------------------------------
# Silent moves
# ---------------------------------------------------------------------------
def silent_path(net: PetriNet, marking: Marking, goal, max_states: int = 200_000) -> list[str] | None:
    """Shortest sequence of τ transitions from ``marking`` to a marking where
    ``goal(marking)`` holds.  ``[]`` if it already holds, ``None`` if none found.

    For a goal that is "transition t is enabled", :func:`enabling_path` is
    exact and much faster on nets with many τ transitions."""
    if goal(marking):
        return []
    silent = [t for t, tr in net.transitions.items() if tr.silent]
    if not silent:
        return None
    parent: dict[Marking, tuple[Marking, str] | None] = {marking: None}
    queue = deque([marking])
    while queue and len(parent) < max_states:
        current = queue.popleft()
        for transition in silent:
            if not net.is_enabled(current, transition):
                continue
            successor = net.fire(current, transition)
            if successor in parent:
                continue
            parent[successor] = (current, transition)
            if goal(successor):
                path = []
                node = successor
                while parent[node] is not None:
                    previous, step = parent[node]  # type: ignore[misc]
                    path.append(step)
                    node = previous
                return path[::-1]
            queue.append(successor)
    return None


_SEARCHERS: "dict[int, tuple[PetriNet, _SilentSearch]]" = {}


def searcher_for(net: PetriNet) -> "_SilentSearch":
    """The τ-search of ``net``, built once per net object (it is read-only)."""
    found = _SEARCHERS.get(id(net))
    if found is None or found[0] is not net:
        if len(_SEARCHERS) > 32:
            _SEARCHERS.clear()
        found = _SEARCHERS[id(net)] = (net, _SilentSearch(net))
    return found[1]


def enabling_path(net: PetriNet, marking: Marking, transition: str) -> list[str] | None:
    """Shortest τ moves from ``marking`` after which ``transition`` is enabled
    (``[]`` if it is, ``None`` if no τ moves enable it); exact."""
    searcher = searcher_for(net)
    return searcher.enabling(marking, searcher.requirement(net.pre(transition)))


class _SilentSearch:
    """Shortest τ-sequences for one net, on markings as tuples of token counts.

    Two kinds of question, both answered exactly:

    * :meth:`enabling`: τ moves after which the marking covers a requirement
      (a transition's preset, or the final marking "at least").  Only τ
      transitions that can feed a token, through τ moves, into a place that
      is short can help: firing any other only interleaves independent
      branches, which in a block-structured net (the Inductive Miner's, with
      its skips and parallel joins) multiplies the markings to visit without
      bringing the goal closer.  The search is restricted to those (a
      backward closure over the τ transitions from the short places).  It is
      still exact: removing the other τ steps from any enabling sequence
      leaves an enabling sequence (they put no token anywhere the goal can
      use, and taking tokens away from them only leaves more), and no longer
      a one, so the shortest path found is a shortest path of the whole net.
    * :meth:`path`: τ moves to a marking where any ``goal`` holds (the final
      marking exactly), restricted to the τ transitions that lead into the
      goal's places or remove tokens, by the same argument.

    The cap on visited markings is a safety net, not the normal end of a
    search; when it is hit the replay forces the transition, as before.
    """

    def __init__(self, net: PetriNet, max_states: int = 200_000) -> None:
        self.net, self.max_states = net, max_states
        self.places = list(net.places)
        index = {place: i for i, place in enumerate(self.places)}
        self.index = index
        self.silent = []
        #: by τ: the places it takes from and puts into (indices)
        self._pre: list[frozenset[int]] = []
        self._post: list[frozenset[int]] = []
        for transition_id, transition in net.transitions.items():
            if not transition.silent:
                continue
            need = [(index[p], n) for p, n in net.pre(transition_id).items()]
            change = Counter()
            for p, n in net.pre(transition_id).items():
                change[index[p]] -= n
            for p, n in net.post(transition_id).items():
                change[index[p]] += n
            self.silent.append((transition_id, need, [(i, d) for i, d in change.items() if d]))
            self._pre.append(frozenset(index[p] for p in net.pre(transition_id)))
            self._post.append(frozenset(index[p] for p in net.post(transition_id)))
        self._relevant: dict[frozenset, list] = {}

    def vector(self, marking: Marking) -> tuple[int, ...]:
        return tuple(marking[p] for p in self.places)

    def requirement(self, marking: Marking) -> list[tuple[int, int]]:
        return [(self.index[p], n) for p, n in marking.items()]

    def relevant(self, places, removers: bool = False) -> list:
        """The τ transitions that can put a token, through τ moves, into one of
        ``places``; with ``removers``, also those that put tokens nowhere (they
        only take tokens away)."""
        key = (frozenset(places), removers)
        found = self._relevant.get(key)
        if found is None:
            wanted = set(places)
            chosen: set[int] = set()
            changed = True
            while changed:
                changed = False
                for k in range(len(self.silent)):
                    if k not in chosen and (self._post[k] & wanted or (removers and not self._post[k])):
                        chosen.add(k)
                        wanted |= self._pre[k]
                        changed = True
            found = self._relevant[key] = [self.silent[k] for k in sorted(chosen)]
        return found

    def enabling(self, marking: Marking, need: list[tuple[int, int]]) -> list[str] | None:
        """Shortest τ moves after which every ``need`` (place index, tokens) is
        covered: ``[]`` if it already is, ``None`` if no τ moves do it."""
        start = self.vector(marking)
        short = [i for i, n in need if start[i] < n]
        if not short:
            return []
        return self._search(start, lambda v: all(v[i] >= n for i, n in need), self.relevant(short))

    def path(self, marking: Marking, goal, places=None) -> list[str] | None:
        """``goal(vector)`` decides; see :func:`silent_path`.  ``places``: the
        places the goal needs tokens in (searching only the τ moves that feed
        them, or only take tokens away); all τ moves when not given."""
        start = self.vector(marking)
        if goal(start):
            return []
        moves = self.silent if places is None else self.relevant(places, removers=True)
        return self._search(start, goal, moves)

    def _search(self, start: tuple, goal, moves: list) -> list[str] | None:
        if not moves:
            return None
        parent: dict[tuple, tuple | None] = {start: None}
        queue = deque([start])
        while queue and len(parent) < self.max_states:
            current = queue.popleft()
            for transition, need, change in moves:
                if any(current[i] < n for i, n in need):
                    continue
                following = list(current)
                for i, d in change:
                    following[i] += d
                successor = tuple(following)
                if successor in parent:
                    continue
                parent[successor] = (current, transition)
                if goal(successor):
                    path = []
                    node = successor
                    while parent[node] is not None:
                        previous, step = parent[node]
                        path.append(step)
                        node = previous
                    return path[::-1]
                queue.append(successor)
        return None


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay_trace(net: PetriNet, trace: tuple[str, ...], count: int = 1,
                 paths: dict | None = None, searcher: _SilentSearch | None = None) -> TraceReplay:
    """Replay one trace.  ``paths`` remembers the silent paths found, keyed
    by the marking and what was looked for, so the many variants of a large
    log (which pass through the same markings again and again) search each
    one once; the answers are the same as searching every time."""
    marking = net.initial_marking
    result = TraceReplay(trace, count, produced=marking.total, consumed=0, missing=0,
                         remaining=0)
    paths = {} if paths is None else paths
    searcher = searcher or _SilentSearch(net)
    by_label: dict[str, list[str]] = {}
    for transition in net.transitions.values():
        if transition.label is not None:
            by_label.setdefault(transition.label, []).append(transition.id)

    final_vector = searcher.vector(net.final_marking)
    final_places = [i for i, n in enumerate(final_vector) if n]
    final_need = searcher.requirement(net.final_marking)

    def search(current: Marking, key) -> list[str] | None:
        found = paths.get((current, key), paths)       # the dict itself: "not searched yet"
        if found is paths:
            if key == ("final", "="):
                found = searcher.path(current, lambda v: v == final_vector, final_places)
            elif key == ("final", "≥"):
                found = searcher.enabling(current, final_need)
            else:                                      # ("enable", transition)
                found = searcher.enabling(current, searcher.requirement(net.pre(key[1])))
            paths[(current, key)] = found
        return found

    def fire(transition: str, current: Marking) -> Marking:
        pre, post = net.pre(transition), net.post(transition)
        short = Marking({p: n - current[p] for p, n in pre.items() if current[p] < n})
        if short:
            result.missing += short.total
            result.missing_by_place.update(dict(short.items()))
            result.forced[transition] += 1
            current = current + short
        result.consumed += pre.total
        result.produced += post.total
        result.fired[transition] += 1
        return current - pre + post

    for index, activity in enumerate(trace):
        candidates = by_label.get(activity)
        if not candidates:
            result.unknown_activities.append(activity)
            result.problem_events.append(index)
            continue
        chosen: str | None = next((t for t in candidates if net.is_enabled(marking, t)), None)
        if chosen is None:
            # Try enabling one of the candidates with silent moves.
            best_path = None
            for transition in candidates:
                path = search(marking, ("enable", transition))
                if path is not None and (best_path is None or len(path) < len(best_path[1])):
                    best_path = (transition, path)
            if best_path is not None:
                chosen, path = best_path
                for step in path:
                    marking = fire(step, marking)
        if chosen is None:
            # Force the transition missing the fewest tokens.
            chosen = min(candidates, key=lambda t: sum(
                max(0, n - marking[p]) for p, n in net.pre(t).items()))
            result.problem_events.append(index)
        marking = fire(chosen, marking)

    # Finish: move silently to the final marking if possible, then let the
    # environment consume it.
    final = net.final_marking
    path = search(marking, ("final", "="))
    if path is None:
        path = search(marking, ("final", "≥")) or []
    for step in path:
        marking = fire(step, marking)
    short = Marking({p: n - marking[p] for p, n in final.items() if marking[p] < n})
    if short:
        result.missing += short.total
        result.missing_by_place.update(dict(short.items()))
        marking = marking + short
    result.consumed += final.total
    leftover = marking - final
    result.remaining = leftover.total
    result.remaining_by_place.update(dict(leftover.items()))
    return result


def token_replay(net: PetriNet, log: SimpleLog, workers: int | None = None) -> ReplayResult:
    """Replay every variant once and weight the results by frequency.

    A large log is replayed on several processes (:mod:`openprocess.mining.parallel`):
    each variant's replay depends only on the net and its trace, so the result
    is the same as replaying them one after the other.  ``workers=1`` does that."""
    from .. import parallel
    variants = sorted(log.items(), key=lambda item: (-item[1], item[0]))
    count = parallel.workers() if workers is None else workers
    if parallel.worth_it(variants, count, "replay"):
        return ReplayResult(net, parallel.map_variants("replay", net, variants, count=count))
    paths: dict = {}
    searcher = _SilentSearch(net)
    traces = [replay_trace(net, trace, count, paths, searcher) for trace, count in variants]
    return ReplayResult(net, traces)
