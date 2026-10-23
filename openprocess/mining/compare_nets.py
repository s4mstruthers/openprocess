"""Compare two Petri nets on behaviour, not on how they are drawn.

Two nets *behave the same* when they have the same **complete traces**:
the sequences of visible labels of the firing sequences that lead from the
initial marking to the final marking.  Silent (τ) transitions leave no
trace, and transitions are matched by label, so a net laid out differently
from another -- or with other place names, or an extra τ -- still matches.

How
---
Each net is turned into an automaton over labels: its states are markings,
τ-steps are free moves, and a state accepts when it is the final marking.
Removing the τ-steps and making the automaton deterministic (the subset
construction) gives, for each sequence of labels, the set of markings it can
lead to.  Both automata are explored together, breadth-first, so the first
sequence found that one accepts and the other does not is a **shortest
differing trace**.

* For **bounded** nets both reachability graphs are finite, so this is
  exact: when nothing differs, the languages are equal.
* For **unbounded** nets (or ones too large to explore) only traces up to a
  length limit are compared, and the result says so.

When does a net *end*?  At its final marking if it has one; for a WF-net
without one, at one token in the sink; otherwise in any marking where
nothing is enabled.  A net with no tokens that is a WF-net starts from one
token in its source.

Labels are compared after trimming spaces and ignoring case; a mapping can
rename one net's labels to the other's (``register`` → ``Register request``).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .analysis import check_workflow_net, reachability_graph
from .petrinet import Marking, PetriNet

#: How many differing traces to collect each way.
EXAMPLES = 3


def normalise(label: str) -> str:
    return " ".join(label.split()).casefold()


@dataclass
class NetComparison:
    #: True when the complete traces are the same (up to the length limit
    #: when :attr:`exact` is False).
    equivalent: bool
    #: True when the comparison covers every trace (both nets bounded).
    exact: bool
    #: Shortest traces the first net (yours) allows and the second does not.
    only_first: list[tuple[str, ...]] = field(default_factory=list)
    #: Shortest traces the second net (the answer) allows and the first does not.
    only_second: list[tuple[str, ...]] = field(default_factory=list)
    #: Traces compared up to this many labels (None when exact).
    max_length: int | None = None
    #: Visible labels that occur in only one of the nets (after normalising
    #: and the mapping).
    labels_only_first: list[str] = field(default_factory=list)
    labels_only_second: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self, first: str = "yours", second: str = "the answer") -> str:
        if self.equivalent:
            scope = "" if self.exact else f" (traces up to {self.max_length} steps compared)"
            return f"Same behaviour{scope}: every complete trace of one is a trace of the other."
        return f"Differs: {first} and {second} do not allow the same complete traces."


def _start_and_end(net: PetriNet) -> tuple[Marking, Marking | None, list[str]]:
    """The initial marking, the final marking (None: any dead marking), and notes."""
    notes = []
    initial, final = net.initial_marking, net.final_marking or None
    workflow = check_workflow_net(net)
    if not initial and workflow.is_workflow_net:
        initial = Marking({workflow.source: 1})
        notes.append(f"{net.name} has no tokens: it starts from one token in its source place.")
    if final is None and workflow.is_workflow_net:
        final = Marking({workflow.sink: 1})
    if final is None:
        notes.append(f"{net.name} has no final marking: its traces end where nothing is enabled.")
    return initial, final, notes


class _Automaton:
    """The net as a (lazily explored) automaton over normalised labels."""

    def __init__(self, net: PetriNet, mapping: dict[str, str] | None = None,
                 closure_limit: int = 5_000) -> None:
        self.net = net
        self.initial, self.final, self.notes = _start_and_end(net)
        mapping = {normalise(k): normalise(v) for k, v in (mapping or {}).items()}
        self.label: dict[str, str | None] = {}
        self.spelling: dict[str, str] = {}
        for transition in net.transitions.values():
            if transition.label is None or not transition.label.strip():
                self.label[transition.id] = None
                continue
            key = normalise(transition.label)
            key = mapping.get(key, key)
            self.label[transition.id] = key
            self.spelling.setdefault(key, transition.label.strip())
        self.closure_limit = closure_limit
        self.overflow = False
        self._closures: dict[Marking, frozenset[Marking]] = {}

    def labels(self) -> set[str]:
        return {label for label in self.label.values() if label is not None}

    def closure(self, markings) -> frozenset[Marking]:
        """Every marking reachable by τ-steps alone."""
        seen = set(markings)
        queue = deque(markings)
        while queue:
            marking = queue.popleft()
            for transition in self.net.enabled(marking):
                if self.label[transition] is None:
                    following = self.net.fire(marking, transition)
                    if following not in seen:
                        if len(seen) >= self.closure_limit:
                            self.overflow = True
                            return frozenset(seen)
                        seen.add(following)
                        queue.append(following)
        return frozenset(seen)

    def start(self) -> frozenset[Marking]:
        return self.closure([self.initial])

    def step(self, state: frozenset[Marking], label: str) -> frozenset[Marking]:
        following = []
        for marking in state:
            for transition in self.net.enabled(marking):
                if self.label[transition] == label:
                    following.append(self.net.fire(marking, transition))
        return self.closure(following) if following else frozenset()

    def accepts(self, state: frozenset[Marking]) -> bool:
        if self.final is not None:
            return self.final in state
        return any(not self.net.enabled(m) for m in state)


def _bounded(net: PetriNet, initial: Marking, max_states: int) -> bool:
    graph = reachability_graph(net, initial, max_states=max_states)
    return not graph.truncated and not graph.has_omega


def compare_nets(first: PetriNet, second: PetriNet, mapping: dict[str, str] | None = None,
                 max_states: int = 20_000, max_length: int = 12,
                 max_pairs: int = 200_000) -> NetComparison:
    """Compare the complete traces of ``first`` (yours) and ``second`` (the answer).

    ``mapping`` renames labels of ``first`` to labels of ``second``.  Exact
    when both nets are bounded with at most ``max_states`` reachable
    markings; otherwise traces of up to ``max_length`` labels are compared.
    """
    one, two = _Automaton(first, mapping), _Automaton(second)
    exact = _bounded(first, one.initial, max_states) and _bounded(second, two.initial,
                                                                   max_states)
    labels = sorted(one.labels() | two.labels())
    result = NetComparison(True, exact, max_length=None if exact else max_length)
    result.notes = one.notes + two.notes
    result.labels_only_first = sorted(one.spelling[x] for x in one.labels() - two.labels())
    result.labels_only_second = sorted(two.spelling[x] for x in two.labels() - one.labels())

    def spell(trace: tuple[str, ...], mine: bool) -> tuple[str, ...]:
        """Each net's traces in its own spelling."""
        first, second = (one, two) if mine else (two, one)
        return tuple(first.spelling.get(x) or second.spelling.get(x, x) for x in trace)

    start = (one.start(), two.start())
    seen = {start}
    queue: deque[tuple[tuple[frozenset, frozenset], tuple[str, ...]]] = deque([(start, ())])
    while queue:
        (a, b), trace = queue.popleft()
        accept_a, accept_b = one.accepts(a), two.accepts(b)
        if accept_a and not accept_b and len(result.only_first) < EXAMPLES:
            result.only_first.append(spell(trace, True))
        if accept_b and not accept_a and len(result.only_second) < EXAMPLES:
            result.only_second.append(spell(trace, False))
        if len(result.only_first) >= EXAMPLES and len(result.only_second) >= EXAMPLES:
            break
        if not exact and len(trace) >= max_length:
            continue
        for label in labels:
            following = (one.step(a, label), two.step(b, label))
            if not following[0] and not following[1]:
                continue
            if following not in seen:
                if len(seen) >= max_pairs:
                    result.exact = False
                    result.max_length = len(trace)
                    result.notes.append("The comparison stopped early: the nets have too "
                                        "many states.")
                    queue.clear()
                    break
                seen.add(following)
                queue.append((following, trace + (label,)))
    if one.overflow or two.overflow:
        result.exact = False
        result.max_length = result.max_length or max_length
        result.notes.append("Long runs of silent steps were cut short.")
    result.equivalent = not result.only_first and not result.only_second
    return result


@dataclass
class TraceOverlap:
    """How many complete traces of up to ``max_length`` labels each net
    allows, and how many both allow (distinct traces, not cases)."""

    max_length: int
    first: int
    second: int
    both: int
    #: False when the exploration hit its limit and the counts are a lower bound.
    complete: bool = True

    @property
    def recall(self) -> float:
        """The share of the second net's traces that the first allows."""
        return self.both / self.second if self.second else 1.0

    @property
    def precision(self) -> float:
        """The share of the first net's traces that the second allows."""
        return self.both / self.first if self.first else 1.0


def trace_overlap(first: PetriNet, second: PetriNet, max_length: int = 12,
                  mapping: dict[str, str] | None = None, max_pairs: int = 200_000) -> TraceOverlap:
    """Count the complete traces of up to ``max_length`` labels that each net
    allows and that both allow, exactly.

    The two nets are explored together as deterministic automata over labels
    (as :func:`compare_nets` does); a pair of states reached by *n* label
    sequences of length *k* carries the count *n*, so no trace is ever listed
    and a net with loops costs no more than its states times the length."""
    one, two = _Automaton(first, mapping), _Automaton(second)
    labels = sorted(one.labels() | two.labels())
    layer: dict[tuple[frozenset, frozenset], int] = {(one.start(), two.start()): 1}
    counts = TraceOverlap(max_length, 0, 0, 0)
    for length in range(max_length + 1):
        for (a, b), n in layer.items():
            in_first, in_second = bool(a) and one.accepts(a), bool(b) and two.accepts(b)
            counts.first += n * in_first
            counts.second += n * in_second
            counts.both += n * (in_first and in_second)
        if length == max_length:
            break
        following: dict[tuple[frozenset, frozenset], int] = {}
        for (a, b), n in layer.items():
            for label in labels:
                pair = (one.step(a, label) if a else frozenset(), two.step(b, label) if b else frozenset())
                if pair[0] or pair[1]:
                    following[pair] = following.get(pair, 0) + n
        if len(following) > max_pairs:
            counts.complete = False
            break
        layer = following
    counts.complete = counts.complete and not (one.overflow or two.overflow)
    return counts


def replayable_prefix(net: PetriNet, trace, mapping: dict[str, str] | None = None
                      ) -> tuple[list[str], int]:
    """Transition ids that replay as much of ``trace`` (labels) on ``net`` as
    possible, τ-steps included, and how many labels they cover."""
    automaton = _Automaton(net, mapping)
    wanted = [normalise(x) for x in trace]
    # Breadth-first over (marking, labels done), remembering how we got there.
    start = (automaton.initial, 0)
    parent: dict[tuple[Marking, int], tuple[tuple[Marking, int], str] | None] = {start: None}
    queue = deque([start])
    best = start
    while queue and len(parent) < 50_000:
        marking, done = queue.popleft()
        if done > best[1] or (done == len(wanted) and best[1] == done and
                              automaton.final is not None and marking == automaton.final):
            best = (marking, done)
        for transition in net.enabled(marking):
            label = automaton.label[transition]
            if label is None:
                node = (net.fire(marking, transition), done)
            elif done < len(wanted) and label == wanted[done]:
                node = (net.fire(marking, transition), done + 1)
            else:
                continue
            if node not in parent:
                parent[node] = ((marking, done), transition)
                queue.append(node)
    path: list[str] = []
    node = best
    while parent[node] is not None:
        previous, transition = parent[node]   # type: ignore[misc]
        path.append(transition)
        node = previous
    return path[::-1], best[1]
