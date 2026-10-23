"""Heuristics Miner: frequency-aware dependency graphs (Weijters & van der Aalst).

Where the α-algorithm asks the yes/no question "does a ever directly precede
b?", the Heuristics Miner asks *how strongly* the log supports ``a`` causing
``b``, using counts ``|a >_L b|`` (how often b directly follows a):

Dependency measure, for ``a ≠ b``::

             |a > b| − |b > a|
    a ⇒ b = ───────────────────        value in (−1, 1)
             |a > b| + |b > a| + 1

It is close to 1 when b follows a often and a almost never follows b, close
to 0 when both orders are equally common (concurrency, or noise), negative
when the dependency is the other way round.  The ``+ 1`` damps small counts:
one occurrence gives only 0.5, a hundred give 0.99.

Length-one loop (``a`` directly followed by itself)::

    a ⇒ a = |a > a| / (|a > a| + 1)

Length-two loop (``a b a`` patterns, counted as ``|a >> b|``)::

    a ⇒2 b = (|a >> b| + |b >> a|) / (|a >> b| + |b >> a| + 1)

An edge ``a → b`` is kept when ``a ⇒ b`` reaches the *dependency threshold*
and ``|a > b|`` reaches the *positive observations* threshold.  With
*all tasks connected* on (the default), every activity additionally keeps its
single best incoming and outgoing edge, so no activity is left floating.

The output of :func:`heuristics_miner` is a *dependency graph*: which
activity causes which, without saying whether a fork is a choice (XOR) or
runs in parallel (AND).

From dependency graph to Petri net (:func:`heuristics_net`)
-----------------------------------------------------------
The split/join semantics come from the log, as a **causal net** (C-net,
van der Aalst, *Process Mining*, 2016, Section 7.3).  Every activity gets a
set of *input bindings* and *output bindings*: which of its predecessors it
waited for, and which of its successors it enabled.  Replaying each trace
on the dependency graph gives them:

* the output binding of ``a`` at position ``i`` is every successor ``b`` of
  ``a`` that occurs after ``i`` with no other ``a`` in between (its first
  such occurrence);
* the input binding of ``b`` at position ``j`` is every predecessor ``a``
  of ``b`` that occurs before ``j`` with no other ``b`` in between.

These two rules match up exactly: ``b`` is in ``a``'s output binding iff
``a`` is in the input binding of that occurrence of ``b``.  A virtual start
activity precedes the first event of every trace and a virtual end activity
follows the last one.  So after ``a`` in ``[⟨a,b,c,d⟩³, ⟨a,c,b,d⟩², ⟨a,e,d⟩]``,
``{b, c}`` (5 times) and ``{e}`` (once) are the output bindings: b and c
run in parallel, as an alternative to e.

The C-net becomes a Petri net in the standard way: a place for every arc
``(a, b)`` of the dependency graph, a transition per activity, and where an
activity has several bindings on a side, a silent transition per binding
that takes (or fills) exactly the places of that binding.  Silent steps that
make no difference to the behaviour are then removed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..dfg import DFG, dfg_from_simple_log
from ..log import SimpleLog


@dataclass
class DependencyGraph:
    activities: Counter
    #: (a, b) -> (dependency value, |a > b|) for every kept edge
    edges: dict[tuple[str, str], tuple[float, int]] = field(default_factory=dict)
    start: Counter = field(default_factory=Counter)
    end: Counter = field(default_factory=Counter)
    #: dependency value of every ordered pair that directly follows at least once
    all_dependencies: dict[tuple[str, str], float] = field(default_factory=dict)


def dependency(dfg: DFG, a: str, b: str) -> float:
    ab, ba = dfg.edges.get((a, b), 0), dfg.edges.get((b, a), 0)
    if a == b:
        return ab / (ab + 1)
    return (ab - ba) / (ab + ba + 1)


def heuristics_miner(log: SimpleLog, dependency_threshold: float = 0.5,
                     min_observations: int = 1, loop_two_threshold: float = 0.5,
                     all_tasks_connected: bool = True) -> DependencyGraph:
    dfg = dfg_from_simple_log(log)

    # |a >> b|: occurrences of the pattern a b a.
    two_loops: Counter = Counter()
    for trace, n in log.items():
        for i in range(len(trace) - 2):
            if trace[i] == trace[i + 2] and trace[i] != trace[i + 1]:
                two_loops[(trace[i], trace[i + 1])] += n

    graph = DependencyGraph(Counter(dfg.activities), start=Counter(dfg.start),
                            end=Counter(dfg.end))
    for (a, b), count in dfg.edges.items():
        value = dependency(dfg, a, b)
        graph.all_dependencies[(a, b)] = value
        if value >= dependency_threshold and count >= min_observations:
            graph.edges[(a, b)] = (value, count)

    # Length-two loops: a b a with both a⇒a and b⇒b weak (otherwise they are
    # already length-one loops) keeps both directions a→b and b→a.
    for (a, b), count in two_loops.items():
        mutual = count + two_loops.get((b, a), 0)
        value = mutual / (mutual + 1)
        if value >= loop_two_threshold:
            for x, y in ((a, b), (b, a)):
                if (x, y) in dfg.edges:
                    graph.edges[(x, y)] = (value, dfg.edges[(x, y)])

    if all_tasks_connected:
        for activity in dfg.activities:
            outgoing = [(dependency(dfg, activity, b), b) for (x, b) in dfg.edges
                        if x == activity and b != activity]
            incoming = [(dependency(dfg, a, activity), a) for (a, y) in dfg.edges
                        if y == activity and a != activity]
            if outgoing and activity not in dfg.end:
                value, best = max(outgoing)
                graph.edges.setdefault((activity, best), (value, dfg.edges[(activity, best)]))
            if incoming and activity not in dfg.start:
                value, best = max(incoming)
                graph.edges.setdefault((best, activity), (value, dfg.edges[(best, activity)]))
    return graph


# ---------------------------------------------------------------------------
# Causal nets and their Petri nets
# ---------------------------------------------------------------------------
#: The virtual activities before the first and after the last event.
START, END = "▶ start", "■ end"


@dataclass
class CausalNet:
    """A dependency graph with, for every activity, its observed input and
    output bindings (frozensets of activities) and how often each occurred."""

    graph: DependencyGraph
    inputs: dict[str, Counter] = field(default_factory=dict)
    outputs: dict[str, Counter] = field(default_factory=dict)


@dataclass
class HeuristicsResult:
    """What the Heuristics Miner found: the graph, the C-net, the Petri net."""

    net: "PetriNet"
    causal: CausalNet
    dependency_threshold: float

    @property
    def graph(self) -> DependencyGraph:
        return self.causal.graph


def causal_net(log: SimpleLog, graph: DependencyGraph) -> CausalNet:
    """Learn the input and output bindings by replaying ``log`` on ``graph``."""
    successors: dict[str, set[str]] = {}
    predecessors: dict[str, set[str]] = {}
    for a, b in graph.edges:
        successors.setdefault(a, set()).add(b)
        predecessors.setdefault(b, set()).add(a)
    net = CausalNet(graph)

    def record(side: dict[str, Counter], activity: str, binding: set[str], n: int) -> None:
        if binding:
            side.setdefault(activity, Counter())[frozenset(binding)] += n

    for trace, n in log.items():
        if not trace:
            record(net.outputs, START, {END}, n)
            record(net.inputs, END, {START}, n)
            continue
        record(net.outputs, START, {trace[0]}, n)
        record(net.inputs, END, {trace[-1]}, n)
        for i, a in enumerate(trace):
            outputs = {END} if i == len(trace) - 1 else set()
            for b in successors.get(a, ()):
                if b == a:                                # a self-loop: only a a
                    if i + 1 < len(trace) and trace[i + 1] == a:
                        outputs.add(a)
                    continue
                for later in trace[i + 1:]:
                    if later == a:
                        break
                    if later == b:
                        outputs.add(b)
                        break
            record(net.outputs, a, outputs, n)
            inputs = {START} if i == 0 else set()
            for c in predecessors.get(a, ()):
                if c == a:
                    if i > 0 and trace[i - 1] == a:
                        inputs.add(a)
                    continue
                for earlier in reversed(trace[:i]):
                    if earlier == a:
                        break
                    if earlier == c:
                        inputs.add(c)
                        break
            record(net.inputs, a, inputs, n)
    return net


def causal_net_to_petri_net(causal: CausalNet, name: str = "Heuristics net") -> "PetriNet":
    """The Petri net of a C-net: arc places, activity transitions, and a
    silent transition per binding where an activity has more than one."""
    from ..petrinet import Marking, PetriNet
    net = PetriNet(name)
    activities = set(causal.inputs) | set(causal.outputs)
    places: dict[tuple[str, str], str] = {}

    def arc_place(a: str, b: str) -> str:
        if (a, b) not in places:
            places[(a, b)] = net.add_place(f"({a},{b})").id
        return places[(a, b)]

    transitions: dict[str, str] = {}
    for activity in sorted(activities, key=lambda x: (x != START, x == END, x)):
        silent = activity in (START, END)
        transitions[activity] = net.add_transition(None if silent else activity,
                                                   name=activity).id
    source, sink = net.add_place("start"), net.add_place("end")
    net.add_arc(source.id, transitions[START])
    net.add_arc(transitions[END], sink.id)

    for activity, bindings in sorted(causal.inputs.items()):
        target = transitions[activity]
        if len(bindings) == 1:
            for before in sorted(next(iter(bindings))):     # sorted: the same place ids in every session
                net.add_arc(arc_place(before, activity), target)
            continue
        joined = net.add_place(f"in({activity})").id
        net.add_arc(joined, target)
        for binding in sorted(bindings, key=sorted):
            join = net.add_transition(
                None, name=f"τ {{{', '.join(sorted(binding))}}} → {activity}").id
            for before in sorted(binding):
                net.add_arc(arc_place(before, activity), join)
            net.add_arc(join, joined)
    for activity, bindings in sorted(causal.outputs.items()):
        origin = transitions[activity]
        if len(bindings) == 1:
            for after in sorted(next(iter(bindings))):
                net.add_arc(origin, arc_place(activity, after))
            continue
        split = net.add_place(f"out({activity})").id
        net.add_arc(origin, split)
        for binding in sorted(bindings, key=sorted):
            fork = net.add_transition(
                None, name=f"τ {activity} → {{{', '.join(sorted(binding))}}}").id
            net.add_arc(split, fork)
            for after in sorted(binding):
                net.add_arc(fork, arc_place(activity, after))
    net.initial_marking = Marking({source.id: 1})
    net.final_marking = Marking({sink.id: 1})
    _remove_redundant_silent_steps(net)
    return net


def _remove_redundant_silent_steps(net) -> None:
    """Remove τ transitions that only pass one token from one place to the next.

    Two classic reductions, both keeping the visible behaviour:

    * τ is the only consumer of its input place p: drop τ and p, and let
      whatever filled p fill τ's output place instead;
    * τ is the only producer of its output place q: drop τ and q, and let
      whatever took from q take from τ's input place instead.

    A τ with one input and one output place is applied only when the arcs
    have weight 1 and the places are different.

    The reductions are tried in the transitions' order and the search starts
    again from the first after each one, so the result does not depend on how
    the search is done; the arcs are looked up in an index rebuilt after each
    reduction (one pass over the arcs), not by scanning them for every
    transition, which on a large log (thousands of τ bindings) is the
    difference between a second and minutes.
    """
    from ..petrinet import Marking
    changed = True
    while changed:
        changed = False
        into: dict[str, list] = {}
        out_of: dict[str, list] = {}
        for arc in net.arcs:
            out_of.setdefault(arc.source, []).append(arc)
            into.setdefault(arc.target, []).append(arc)
        for transition_id, transition in list(net.transitions.items()):
            if not transition.silent:
                continue
            ins, outs = into.get(transition_id, []), out_of.get(transition_id, [])
            if len(ins) != 1 or len(outs) != 1 or ins[0].weight != 1 or outs[0].weight != 1:
                continue
            p, q = ins[0].source, outs[0].target
            if p == q:
                continue
            if len(out_of.get(p, [])) == 1 and not net.final_marking[p]:
                # p only feeds τ: whatever filled p fills q.  (Not when p is in
                # the final marking: removing it would change where runs end.)
                for arc in list(into.get(p, [])):
                    net.add_arc(arc.source, q, arc.weight)
                if net.initial_marking[p]:
                    net.initial_marking = Marking({**dict(net.initial_marking.items()),
                                                   q: net.initial_marking[p], p: 0})
                net.remove_transition(transition_id)
                net.remove_place(p)
                changed = True
                break
            if len(into.get(q, [])) == 1 and not net.final_marking[q] \
                    and not net.initial_marking[q]:
                # q is only filled by τ: whatever took from q takes from p.
                for arc in list(out_of.get(q, [])):
                    net.add_arc(p, arc.target, arc.weight)
                net.remove_transition(transition_id)
                net.remove_place(q)
                changed = True
                break


def heuristics_net(log: SimpleLog, dependency_threshold: float = 0.5,
                   **options) -> HeuristicsResult:
    """Heuristics Miner all the way to a Petri net (dependency graph, C-net,
    Petri net), without PM4Py.  ``options`` go to :func:`heuristics_miner`."""
    graph = heuristics_miner(log, dependency_threshold=dependency_threshold, **options)
    causal = causal_net(log, graph)
    net = causal_net_to_petri_net(causal)
    net.info["algorithm"] = f"Heuristics Miner (dependency ≥ {dependency_threshold:g})"
    return HeuristicsResult(net, causal, dependency_threshold)
