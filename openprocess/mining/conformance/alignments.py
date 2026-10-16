"""Alignments (Carmona, van Dongen, Solti & Weidlich, ch. 7; course reading 5).

An **alignment** pairs up a trace with a complete run of the model, move by
move.  Each move is one of:

=============  =================  =============================================
move           written            meaning
=============  =================  =============================================
synchronous    ``(a, a)``         the log did a, the model did a -- agreement
log move       ``(a, ≫)``         the log did a, the model could not
model move     ``(≫, a)``         the model had to do a, the log did not
silent move    ``(≫, τ)``         the model fired a τ transition (free)
=============  =================  =============================================

With the **standard cost function** (log and model moves cost 1, synchronous
and silent moves cost 0), an *optimal* alignment is one with minimal total
cost: the fewest deviations needed to explain the trace with the model.

How it is computed
------------------
Search the **synchronous product**: states are ``(marking, i)`` where ``i``
is how many events have been explained so far.  From each state:

* a synchronous move fires a transition labelled ``trace[i]`` and moves to
  ``i + 1``;
* a log move stays in the marking and moves to ``i + 1``;
* a model move fires any enabled transition and stays at ``i``.

The goal is ``(final marking, len(trace))``.  Costs are non-negative, so
Dijkstra/A* returns an optimal path.  As in PM4Py, costs are scaled: a real
deviation costs 10000 and a τ move costs 1, so that among equally good
alignments the one with fewest τ moves wins; the reported cost is the
integer division by 10000.

Heuristic: every remaining event whose label does not occur in the model
*must* become a log move, so their count is a lower bound on the remaining
cost -- admissible, so A* stays optimal.

Dead ends: a token in a place no transition consumes from can never go
away, so a marking with more such tokens than the final marking asks for
can never reach it.  Those states are not explored.  This is what keeps a
transition with no input place (a common slip when drawing a net by hand)
from pumping tokens into a corner of the net forever.

When the search has no end: an *unbounded* net, where a loop of silent
transitions can produce tokens without limit, has infinitely many runs
cheaper than one deviation (a τ move costs 1, a deviation 10000), and the
search would look at all of them before accepting the deviation.  Every
search therefore stops after ``max_states`` states and reports the
alignment as not found (``complete=False``, fitness 0); the result's
``notes`` say so, and say whether the net is unbounded.  The limit is a
count, not a clock, so the same input gives the same answer every time.

Fitness
-------
``fitness(σ) = 1 − cost(σ) / (|σ| + cost(⟨⟩))`` where ``cost(⟨⟩)`` is the
cost of aligning the empty trace -- the length of the shortest visible run of
the model.  The denominator is the worst case: every event a log move plus a
shortest run of model moves.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

from ..log import SimpleLog
from ..petrinet import Marking, PetriNet

SKIP = "≫"
DEVIATION = 10_000
TAU_COST = 1


@dataclass
class Move:
    log: str               # activity, or SKIP
    model: str             # label, "τ", or SKIP
    transition: str | None  # transition id for model/sync moves

    @property
    def kind(self) -> str:
        if self.log == SKIP:
            return "silent" if self.model == "τ" else "model"
        if self.model == SKIP:
            return "log"
        return "sync"


@dataclass
class Alignment:
    trace: tuple[str, ...]
    count: int
    moves: list[Move]
    cost: int                  # number of deviations (standard cost function)
    fitness: float
    states_visited: int = 0
    #: False if the search gave up (state limit); then moves are empty, the
    #: cost is a stand-in (every event a log move) and the fitness 0.
    complete: bool = True

    @property
    def log_moves(self) -> int:
        return sum(1 for m in self.moves if m.kind == "log")

    @property
    def model_moves(self) -> int:
        return sum(1 for m in self.moves if m.kind == "model")


@dataclass
class AlignmentResult:
    net: PetriNet
    alignments: list[Alignment] = field(default_factory=list)   # per variant
    empty_trace_cost: int = 0
    #: What the reader should know: searches that were cut short, and why.
    notes: list[str] = field(default_factory=list)

    @property
    def trace_count(self) -> int:
        return sum(a.count for a in self.alignments)

    @property
    def incomplete(self) -> list[Alignment]:
        """The variants whose optimal alignment was not found (state limit)."""
        return [a for a in self.alignments if not a.complete]

    @property
    def average_fitness(self) -> float:
        """Mean of the per-trace fitness values (weighted by frequency)."""
        total = self.trace_count
        return sum(a.fitness * a.count for a in self.alignments) / total if total else 1.0

    @property
    def log_fitness(self) -> float:
        """1 − total cost / total worst-case cost."""
        cost = sum(a.cost * a.count for a in self.alignments)
        worst = sum((len(a.trace) + self.empty_trace_cost) * a.count for a in self.alignments)
        return 1 - cost / worst if worst else 1.0

    @property
    def fitting_traces(self) -> int:
        return sum(a.count for a in self.alignments if a.cost == 0 and a.complete)

    def move_statistics(self) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """(sync, log-move, model-move) counts per activity, frequency-weighted."""
        sync: dict[str, int] = {}
        log_moves: dict[str, int] = {}
        model_moves: dict[str, int] = {}
        for alignment in self.alignments:
            for move in alignment.moves:
                if move.kind == "sync":
                    sync[move.log] = sync.get(move.log, 0) + alignment.count
                elif move.kind == "log":
                    log_moves[move.log] = log_moves.get(move.log, 0) + alignment.count
                elif move.kind == "model":
                    model_moves[move.model] = model_moves.get(move.model, 0) + alignment.count
        return sync, log_moves, model_moves


class _CompiledNet:
    """The net flattened into integer arrays for a fast inner loop.

    A marking becomes a tuple of token counts indexed by place number, and
    each transition becomes its input requirements plus its effect vector.
    Tuples hash and compare much faster than :class:`Marking` objects, which
    matters because an alignment search can visit hundreds of thousands of
    states.
    """

    def __init__(self, net: PetriNet) -> None:
        self.places = list(net.places)
        index = {p: i for i, p in enumerate(self.places)}
        self.transitions = list(net.transitions.values())
        self.pre = [tuple((index[p], n) for p, n in net.pre(t.id).items())
                    for t in self.transitions]
        self.delta = []
        for t in self.transitions:
            change = [0] * len(self.places)
            for p, n in net.pre(t.id).items():
                change[index[p]] -= n
            for p, n in net.post(t.id).items():
                change[index[p]] += n
            self.delta.append(tuple((i, d) for i, d in enumerate(change) if d))
        self.initial = self.encode(net.initial_marking, index)
        self.final = self.encode(net.final_marking, index)
        # Places nothing consumes from: tokens there only ever pile up, so
        # more of them than the final marking wants is a dead end.
        consumed = {i for pre in self.pre for i, _n in pre}
        self.dead_ends = tuple((i, self.final[i]) for i in range(len(self.places))
                               if i not in consumed)

    def dead_end(self, marking: tuple[int, ...]) -> bool:
        """Can ``marking`` never reach the final marking?  (A sufficient test.)"""
        return any(marking[i] > cap for i, cap in self.dead_ends)

    def encode(self, marking: Marking, index: dict[str, int]) -> tuple[int, ...]:
        vector = [0] * len(self.places)
        for p, n in marking.items():
            vector[index[p]] = n
        return tuple(vector)

    def enabled(self, marking: tuple[int, ...]) -> list[int]:
        return [k for k, pre in enumerate(self.pre)
                if all(marking[i] >= n for i, n in pre)]

    def fire(self, marking: tuple[int, ...], k: int) -> tuple[int, ...]:
        vector = list(marking)
        for i, d in self.delta[k]:
            vector[i] += d
        return tuple(vector)


#: How many states one trace's search may discover before it gives up.  A
#: state costs a few hundred bytes (its marking, its cost, its parent), so
#: this also caps the memory a search can take: a few hundred megabytes.
MAX_STATES = 500_000


def align_trace(net: PetriNet, trace: tuple[str, ...], count: int = 1,
                max_states: int = MAX_STATES, compiled: "_CompiledNet | None" = None) -> Alignment:
    """An optimal alignment of one trace (A* on the synchronous product).

    The search gives up, with ``complete=False``, once it has discovered
    ``max_states`` states (counting the frontier, not only the states
    expanded, so that the memory it takes is bounded too).
    """
    c = compiled or _CompiledNet(net)
    labels = net.labels()
    # Suffix heuristic: events not in the model are unavoidable log moves.
    unavoidable = [0] * (len(trace) + 1)
    for i in range(len(trace) - 1, -1, -1):
        unavoidable[i] = unavoidable[i + 1] + (DEVIATION if trace[i] not in labels else 0)

    start = (c.initial, 0)
    tie = itertools.count()
    frontier: list = [(unavoidable[0], 0, next(tie), start)]
    best_cost: dict = {start: 0}
    parent: dict = {start: None}
    closed: set = set()
    goal = (c.final, len(trace))

    while frontier:
        _, cost, _, state = heapq.heappop(frontier)
        if state in closed:
            continue
        closed.add(state)
        if state == goal:
            moves = []
            node = state
            while parent[node] is not None:
                previous, move = parent[node]
                moves.append(move)
                node = previous
            return Alignment(trace, count, moves[::-1], cost // DEVIATION, 0.0, len(closed))
        if len(best_cost) > max_states:
            break

        marking, position = state
        enabled = c.enabled(marking)
        successors = []
        if position < len(trace):
            activity = trace[position]
            successors.append(((marking, position + 1), DEVIATION, Move(activity, SKIP, None)))
            for k in enabled:
                t = c.transitions[k]
                if t.label == activity:
                    successors.append(((c.fire(marking, k), position + 1), 0,
                                       Move(activity, activity, t.id)))
        for k in enabled:
            t = c.transitions[k]
            step_cost = TAU_COST if t.label is None else DEVIATION
            successors.append(((c.fire(marking, k), position), step_cost,
                               Move(SKIP, "τ" if t.label is None else t.label, t.id)))

        for successor, step_cost, move in successors:
            if successor in closed or c.dead_end(successor[0]):
                continue
            new_cost = cost + step_cost
            if new_cost < best_cost.get(successor, 1 << 62):
                best_cost[successor] = new_cost
                parent[successor] = (state, move)
                heapq.heappush(frontier, (new_cost + unavoidable[successor[1]], new_cost,
                                          next(tie), successor))

    return Alignment(trace, count, [], len(trace), 0.0, len(closed), complete=False)


def align_log(net: PetriNet, log: SimpleLog, max_states: int = MAX_STATES,
              progress=None) -> AlignmentResult:
    """Align every variant; fitness per trace uses the empty-trace cost.

    A variant whose search gave up (see :func:`align_trace`) counts as
    fitness 0, and ``result.notes`` says how many did and, when the net is
    unbounded, that this is why.
    """
    compiled = _CompiledNet(net)
    empty = align_trace(net, (), 1, max_states, compiled)
    result = AlignmentResult(net, empty_trace_cost=empty.cost)
    variants = sorted(log.items(), key=lambda item: (-item[1], item[0]))
    for number, (trace, count) in enumerate(variants, 1):
        alignment = align_trace(net, trace, count, max_states, compiled)
        worst = len(trace) + empty.cost
        alignment.fitness = 1 - alignment.cost / worst if worst else 1.0
        if not alignment.complete:
            alignment.fitness = 0.0
        result.alignments.append(alignment)
        if progress is not None:
            progress(number, len(variants))
    gave_up = result.incomplete
    if not empty.complete:
        gave_up = [empty, *gave_up]
    if gave_up:
        cases = sum(a.count for a in result.incomplete)
        result.notes.append(
            f"No optimal alignment was found for {len(result.incomplete)} of {len(variants)} "
            f"variants ({cases:,} cases): the search stopped at {max_states:,} states. Their "
            f"cost is unknown and their fitness is counted as 0."
            if result.incomplete else
            f"The search for the model's shortest run stopped at {max_states:,} states.")
        reason = _unbounded_reason(net)
        if reason:
            result.notes.append(reason)
    return result


def _unbounded_reason(net: PetriNet, max_states: int = 20_000) -> str | None:
    """Why a search may have no end: the net is unbounded (tokens pile up
    without limit), or its state space is simply very large."""
    from ..analysis import reachability_graph
    graph = reachability_graph(net, max_states=max_states)
    if graph.has_omega:
        return ("The net is unbounded: some loop or transition produces tokens without "
                "limit, so there is no end to the runs an alignment could use. A "
                "transition with no input place, or a loop that puts back more tokens "
                "than it takes, is the usual cause; the Analysis tab's soundness check "
                "points at it.")
    if graph.truncated:
        return (f"The net has more than {max_states:,} reachable markings, so an "
                "alignment has a very large space to search.")
    return None
