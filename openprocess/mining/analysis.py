"""Behavioural analysis of P/T nets: state spaces, properties, soundness.

Everything here answers questions of the form "can the net ever reach a
marking where ...?", so everything starts by exploring the markings the net
can reach.

Reachability graph
------------------
Nodes are the reachable markings, and an edge ``M --t--> M'`` means firing
``t`` in ``M`` gives ``M'``.  It is built breadth-first from the initial
marking.  It is finite iff the net is **bounded** (no place can hold an
unbounded number of tokens).

Coverability graph (Karp–Miller)
--------------------------------
For an unbounded net the reachability graph is infinite, so it cannot be
built.  The coverability graph is a finite over-approximation: when a new
marking ``M'`` *strictly covers* an ancestor ``M`` on its path (``M' ≥ M`` and
``M' ≠ M``), the difference can be pumped forever by repeating the same
firing sequence, so every place where ``M'(p) > M(p)`` is set to **ω**
("arbitrarily many").  ω is represented by ``math.inf``, which already obeys
``ω + n = ω - n = ω``.

Properties (course lecture "Petri net properties")
--------------------------------------------------
bounded / k-bounded / safe
    Every place holds at most k tokens in every reachable marking (safe:
    k = 1).  Read off the coverability graph: bounded iff no ω appears.
deadlock-free
    No reachable marking in which nothing is enabled.
dead transition
    A transition that can never fire (no edge carries it).
live transition
    From *every* reachable marking the transition can eventually fire again.
    In a finite graph: it must occur inside every *bottom* SCC (a strongly
    connected component with no way out), because the net always ends up
    trapped in some bottom SCC.
reversible
    The initial marking can be reached back from every reachable marking.

Soundness of workflow nets (van der Aalst, Workflow Verification, Def. 2 & 3)
------------------------------------------------------------------------------
A **WF-net** has one source place ``i`` (empty pre-set), one sink place ``o``
(empty post-set), and every node lies on a path from ``i`` to ``o``.
It is **sound** iff, starting from ``[i]``:

1. *option to complete* -- from every reachable marking, ``[o]`` is reachable;
2. *proper completion* -- if a reachable marking has a token in ``o``, it is
   exactly ``[o]`` (no tokens left behind);
3. *no dead transitions* -- every transition can fire in some reachable
   marking.

A sound WF-net is necessarily bounded, so if exploration finds the net to be
unbounded we can stop and report unsoundness.  (The paper's Theorem 1 gives
the equivalent formulation: N is sound iff the short-circuited net, with an
extra transition from ``o`` back to ``i``, is live and bounded.)
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .petrinet import Marking, PetriNet

OMEGA = math.inf


# ---------------------------------------------------------------------------
# State spaces
# ---------------------------------------------------------------------------
@dataclass
class StateGraph:
    """A reachability or coverability graph.

    ``states[0]`` is always the initial marking.  ``edges`` holds
    ``(source index, transition id, target index)`` triples.
    """

    net: PetriNet
    states: list[Marking] = field(default_factory=list)
    edges: list[tuple[int, str, int]] = field(default_factory=list)
    #: True when exploration stopped at ``max_states`` before finishing.
    truncated: bool = False
    #: True for a coverability graph that contains ω somewhere.
    has_omega: bool = False
    kind: str = "reachability"

    def __post_init__(self) -> None:
        self.index: dict[Marking, int] = {}
        self._successors: list[list[tuple[str, int]]] = []
        self._predecessors: list[list[tuple[str, int]]] = []

    def _add_state(self, marking: Marking) -> int:
        self.index[marking] = len(self.states)
        self.states.append(marking)
        self._successors.append([])
        self._predecessors.append([])
        return len(self.states) - 1

    def _add_edge(self, source: int, transition: str, target: int) -> None:
        self.edges.append((source, transition, target))
        self._successors[source].append((transition, target))
        self._predecessors[target].append((transition, source))

    def successors(self, state: int) -> list[tuple[str, int]]:
        return self._successors[state]

    def predecessors(self, state: int) -> list[tuple[str, int]]:
        return self._predecessors[state]

    def dead_states(self) -> list[int]:
        return [s for s in range(len(self.states)) if not self._successors[s]]

    def fired_transitions(self) -> set[str]:
        return {t for _, t, _ in self.edges}

    def backward_reachable(self, targets: set[int]) -> set[int]:
        """All states from which some state in ``targets`` can be reached."""
        seen = set(targets)
        queue = deque(targets)
        while queue:
            state = queue.popleft()
            for _, previous in self._predecessors[state]:
                if previous not in seen:
                    seen.add(previous)
                    queue.append(previous)
        return seen

    def distances(self) -> dict[int, int]:
        """Firing-sequence length of the shortest path to each state."""
        distance = {0: 0}
        queue = deque([0])
        while queue:
            state = queue.popleft()
            for _, successor in self._successors[state]:
                if successor not in distance:
                    distance[successor] = distance[state] + 1
                    queue.append(successor)
        return distance

    def path_to(self, target: int) -> list[str]:
        """A shortest firing sequence (transition ids) from the initial state."""
        parent: dict[int, tuple[int, str] | None] = {0: None}
        queue = deque([0])
        while queue:
            state = queue.popleft()
            if state == target:
                break
            for transition, successor in self._successors[state]:
                if successor not in parent:
                    parent[successor] = (state, transition)
                    queue.append(successor)
        if target not in parent:
            return []
        path: list[str] = []
        node = target
        while parent[node] is not None:
            previous, transition = parent[node]  # type: ignore[misc]
            path.append(transition)
            node = previous
        return path[::-1]

    def describe_path(self, target: int) -> str:
        names = [self.net.transitions[t].name for t in self.path_to(target)]
        return " → ".join(names) if names else "(initial marking)"

    def bottom_sccs(self) -> list[set[int]]:
        """Strongly connected components with no edge leaving them."""
        components = strongly_connected_components(
            len(self.states), lambda s: [t for _, t in self._successors[s]])
        component_of = {}
        for number, component in enumerate(components):
            for state in component:
                component_of[state] = number
        bottoms = []
        for number, component in enumerate(components):
            if all(component_of[t] == number
                   for s in component for _, t in self._successors[s]):
                bottoms.append(component)
        return bottoms


def reachability_graph(net: PetriNet, initial: Marking | None = None,
                       max_states: int = 100_000) -> StateGraph:
    """Breadth-first exploration of all reachable markings.

    Stops early (``truncated=True``) after ``max_states`` states, and also
    stops early if the net is found to be *unbounded*: a new marking strictly
    covering one of its own ancestors proves that tokens can be pumped
    forever, so the graph would never finish.  In that case
    ``graph.has_omega`` is set and :func:`coverability_graph` gives the finite
    picture.
    """
    graph = StateGraph(net)
    start = initial if initial is not None else net.initial_marking
    graph._add_state(start)
    parent: dict[int, int | None] = {0: None}
    queue = deque([0])
    while queue:
        state = queue.popleft()
        marking = graph.states[state]
        for transition in net.enabled(marking):
            successor = net.fire(marking, transition)
            target = graph.index.get(successor)
            if target is None:
                if len(graph.states) >= max_states:
                    graph.truncated = True
                    return graph
                target = graph._add_state(successor)
                parent[target] = state
                if _covers_ancestor(graph, parent, target):
                    graph._add_edge(state, transition, target)
                    graph.has_omega = True
                    graph.truncated = True
                    return graph
                queue.append(target)
            graph._add_edge(state, transition, target)
    return graph


def _covers_ancestor(graph: StateGraph, parent: dict[int, int | None], node: int) -> bool:
    marking = graph.states[node]
    ancestor = parent[node]
    while ancestor is not None:
        earlier = graph.states[ancestor]
        if marking >= earlier and marking != earlier:
            return True
        ancestor = parent[ancestor]
    return False


def coverability_graph(net: PetriNet, initial: Marking | None = None,
                       max_states: int = 50_000) -> StateGraph:
    """The Karp–Miller coverability graph (always finite)."""
    graph = StateGraph(net, kind="coverability")
    start = initial if initial is not None else net.initial_marking
    graph._add_state(start)
    parent: dict[int, int | None] = {0: None}
    queue = deque([0])
    while queue:
        state = queue.popleft()
        marking = graph.states[state]
        for transition in net.enabled(marking):
            successor = net.fire(marking, transition)
            # Acceleration: compare with every ancestor on the tree path,
            # including the current state itself.
            accelerated = dict(successor.items())
            ancestor: int | None = state
            while ancestor is not None:
                earlier = graph.states[ancestor]
                current = Marking(accelerated)
                if current >= earlier and current != earlier:
                    for place, tokens in accelerated.items():
                        if tokens > earlier[place]:
                            accelerated[place] = OMEGA
                ancestor = parent[ancestor]
            successor = Marking(accelerated)
            if any(v == OMEGA for v in accelerated.values()):
                graph.has_omega = True
            target = graph.index.get(successor)
            if target is None:
                if len(graph.states) >= max_states:
                    graph.truncated = True
                    return graph
                target = graph._add_state(successor)
                parent[target] = state
                queue.append(target)
            graph._add_edge(state, transition, target)
    return graph


def strongly_connected_components(count: int, successors) -> list[set[int]]:
    """Tarjan's algorithm, iterative so deep graphs cannot hit the recursion limit."""
    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    components: list[set[int]] = []
    counter = 0
    for root in range(count):
        if root in index_of:
            continue
        work = [(root, iter(successors(root)))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(successors(child))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent_node = work[-1][0]
                low[parent_node] = min(low[parent_node], low[node])
            if low[node] == index_of[node]:
                component = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                components.append(component)
    return components


# ---------------------------------------------------------------------------
# General properties
# ---------------------------------------------------------------------------
@dataclass
class PropertyReport:
    graph: StateGraph
    bounded: bool
    bound: float                     # max tokens in any place (inf if unbounded)
    place_bounds: dict[str, float]
    dead_markings: list[int]         # state indices
    dead_transitions: list[str]
    live_transitions: list[str] | None   # None when not decidable here
    reversible: bool | None

    @property
    def safe(self) -> bool:
        return self.bounded and self.bound <= 1

    @property
    def deadlock_free(self) -> bool:
        return not self.dead_markings

    @property
    def terminating(self) -> bool | None:
        """Does every run end? No once the graph has a cycle or an ω place."""
        if self.graph.has_omega:
            return False                 # unbounded: some run never ends
        if self.graph.truncated:
            return None
        graph = self.graph
        for component in strongly_connected_components(
                len(graph.states), lambda s: [t for _, t in graph.successors(s)]):
            state = next(iter(component))
            if len(component) > 1 or any(t == state for _, t in graph.successors(state)):
                return False
        return True

    def lines(self) -> list[tuple[str, bool | None, str]]:
        """(property, verdict, detail) rows for display."""
        net = self.graph.net
        rows: list[tuple[str, bool | None, str]] = []
        partial = self.graph.truncated and not self.graph.has_omega
        rows.append(("Bounded", self.bounded,
                     f"at most {int(self.bound)} token(s) per place" if self.bounded
                     else "places marked ω can hold arbitrarily many tokens: "
                     + ", ".join(net.places[p].name for p, b in self.place_bounds.items()
                                 if b == OMEGA)))
        rows.append(("Safe", self.safe, "every place holds at most one token"
                     if self.safe else "some place can hold two or more tokens"))
        terminating = self.terminating
        rows.append(("Terminating", terminating,
                     "not decided (truncated state space)" if terminating is None else
                     "every run ends: the reachability graph has no cycle" if terminating
                     else "some run can go on forever"
                     + (" (the net is unbounded)" if self.graph.has_omega
                        else ": the reachability graph has a cycle")))
        rows.append(("Deadlock-free", self.deadlock_free,
                     "every reachable marking enables something" if self.deadlock_free
                     else f"{len(self.dead_markings)} dead marking(s), e.g. "
                     + self.graph.states[self.dead_markings[0]].describe(net)
                     + " via " + self.graph.describe_path(self.dead_markings[0])))
        rows.append(("No dead transitions", not self.dead_transitions,
                     "every transition can fire" if not self.dead_transitions
                     else "never fire: " + ", ".join(net.transitions[t].name
                                                     for t in self.dead_transitions)))
        if self.live_transitions is None:
            rows.append(("Live", None, "not decided (unbounded or truncated state space)"))
        else:
            not_live = [t for t in net.transitions if t not in self.live_transitions]
            rows.append(("Live", not not_live, "every transition can always fire again"
                         if not not_live else "not live: " + ", ".join(
                             net.transitions[t].name for t in not_live)))
        rows.append(("Reversible", self.reversible,
                     "not decided" if self.reversible is None else
                     ("the initial marking is reachable from every marking" if self.reversible
                      else "some marking cannot return to the initial marking")))
        if partial:
            rows.insert(0, ("Complete state space", False,
                            f"stopped after {len(self.graph.states)} states; "
                            "verdicts below are NOT proofs"))
        return rows


def analyse(net: PetriNet, max_states: int = 100_000) -> PropertyReport:
    """Compute the standard behavioural properties of ``(net, initial marking)``."""
    graph = reachability_graph(net, max_states=max_states)
    if graph.has_omega:
        graph = coverability_graph(net, max_states=max_states)

    place_bounds: dict[str, float] = {p: 0 for p in net.places}
    for marking in graph.states:
        for place, tokens in marking.items():
            place_bounds[place] = max(place_bounds[place], tokens)
    bound = max(place_bounds.values(), default=0)
    bounded = not graph.has_omega

    fired = graph.fired_transitions()
    dead_transitions = [t for t in net.transitions if t not in fired]
    dead_markings = graph.dead_states()

    live: list[str] | None = None
    reversible: bool | None = None
    if bounded and not graph.truncated:
        bottoms = graph.bottom_sccs()
        live = []
        for transition in net.transitions:
            if all(any(t == transition and target in component
                       for state in component for t, target in graph.successors(state))
                   for component in bottoms):
                live.append(transition)
        reversible = len(graph.backward_reachable({0})) == len(graph.states)

    return PropertyReport(graph, bounded, bound, place_bounds, dead_markings,
                          dead_transitions, live, reversible)


# ---------------------------------------------------------------------------
# Workflow nets and soundness
# ---------------------------------------------------------------------------
@dataclass
class WorkflowCheck:
    """Structural WF-net check (Definition 2 in the paper)."""

    is_workflow_net: bool
    source: str | None
    sink: str | None
    problems: list[str]


def check_workflow_net(net: PetriNet) -> WorkflowCheck:
    problems: list[str] = []
    sources, sinks = net.source_places(), net.sink_places()
    names = net.node_name
    if len(sources) != 1:
        problems.append("needs exactly one source place (empty pre-set); found "
                        f"{len(sources)}" + (": " + ", ".join(map(names, sources)) if sources else ""))
    if len(sinks) != 1:
        problems.append("needs exactly one sink place (empty post-set); found "
                        f"{len(sinks)}" + (": " + ", ".join(map(names, sinks)) if sinks else ""))
    # Source/sink transitions also violate the definition.
    for t in net.transitions:
        if not net.pre(t):
            problems.append(f"transition {names(t)!r} has no input place")
        if not net.post(t):
            problems.append(f"transition {names(t)!r} has no output place")

    source = sources[0] if len(sources) == 1 else None
    sink = sinks[0] if len(sinks) == 1 else None
    if source and sink:
        forward = _reach(net, source, net.postset)
        backward = _reach(net, sink, net.preset)
        every = set(net.places) | set(net.transitions)
        off_path = [n for n in every if n not in forward or n not in backward]
        if off_path:
            problems.append("not on a path from source to sink: "
                            + ", ".join(sorted(map(names, off_path))))
    return WorkflowCheck(not problems, source, sink, problems)


def short_circuit(net: PetriNet, source: str, sink: str) -> PetriNet:
    """The short-circuited net N̄: N plus a transition t* from the sink back to the source.

    Theorem 1 of the paper: a WF-net N is sound iff (N̄, [i]) is live and
    bounded.  Analysing N̄ therefore turns "can every case finish?" into the
    classical questions "is every transition live?" and "is it bounded?".
    """
    closed = net.copy()
    closed.name = f"{net.name} (short-circuited)"
    back = closed.add_transition(None, name="t*", id="t_star")
    into, out = closed.add_arc(sink, back), closed.add_arc(back, source)
    positions = [n.position for n in list(net.places.values()) + list(net.transitions.values())
                 if n.position is not None]
    start, end = net.places[source].position, net.places[sink].position
    if positions and start and end:
        # Draw t* above the net, with the arcs going round the top.
        top = min(y for _, y in positions) - 90
        back.position = ((start[0] + end[0]) / 2, top)
        into.points = [(end[0], top)]
        out.points = [(start[0], top)]
    closed.initial_marking = Marking({source: 1})
    closed.final_marking = Marking()
    return closed


def _reach(net: PetriNet, start: str, step) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbour in step(node):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


@dataclass
class SoundnessReport:
    workflow: WorkflowCheck
    graph: StateGraph | None = None
    bounded: bool | None = None
    option_to_complete: bool | None = None
    proper_completion: bool | None = None
    no_dead_transitions: bool | None = None
    #: human-readable counterexamples, one string each
    findings: list[str] = field(default_factory=list)
    #: for each finding, the firing sequence (transition ids) that leads to
    #: the problem from the initial marking -- or None when there is none
    #: (a structural problem, dead transitions)
    paths: list[list[str] | None] = field(default_factory=list)
    #: Theorem 1: the short-circuited net N̄, analysed from [i]
    short_circuit: "ShortCircuitReport | None" = None
    #: §6: free-choice, well-structured, S-coverable (see :mod:`.structure`)
    structure: object | None = None
    #: A state index in :attr:`graph` witnessing each violated condition:
    #: ``"option_to_complete"`` (cannot reach [o]), ``"proper_completion"``
    #: (a token in o with others left behind), ``"bounded"`` (an ω marking).
    witness: dict[str, int] = field(default_factory=dict)
    #: Transitions that can never fire from [i].
    dead_transitions: list[str] = field(default_factory=list)

    def add(self, finding: str, path: list[str] | None = None) -> None:
        self.findings.append(finding)
        self.paths.append(path)

    @property
    def sound(self) -> bool | None:
        """True / False, or None if it could not be decided (state-space cap)."""
        if not self.workflow.is_workflow_net:
            return False
        verdicts = [self.bounded, self.option_to_complete, self.proper_completion,
                    self.no_dead_transitions]
        if any(v is False for v in verdicts):
            return False
        if any(v is None for v in verdicts):
            return None
        return True


def check_soundness(net: PetriNet, max_states: int = 200_000) -> SoundnessReport:
    """Decide classical soundness, with a counterexample for each violation."""
    workflow = check_workflow_net(net)
    report = SoundnessReport(workflow)
    if not workflow.is_workflow_net:
        for problem in workflow.problems:
            report.add(f"Not a WF-net: {problem}.")
        return report

    from .structure import check_structure
    report.structure = check_structure(net, workflow.source, workflow.sink)
    report.short_circuit = check_short_circuited(net, workflow.source, workflow.sink,
                                                 max_states=max_states)

    initial = Marking({workflow.source: 1})
    final = Marking({workflow.sink: 1})
    graph = reachability_graph(net, initial, max_states=max_states)
    report.graph = graph
    names = net.node_name

    if graph.has_omega:
        report.bounded = False
        witness = len(graph.states) - 1
        report.witness["bounded"] = witness
        report.add(
            "Unbounded: tokens can accumulate without limit, e.g. after "
            f"{graph.describe_path(witness)} the marking {graph.states[witness].describe(net)} "
            "strictly covers an earlier one. A sound WF-net is always bounded.",
            graph.path_to(witness))
        return report
    if graph.truncated:
        report.add(f"State space exceeds {max_states} markings; soundness "
                   "could not be decided.")
        return report
    report.bounded = True

    # 1. option to complete: every state can reach [o]
    final_index = graph.index.get(final)
    can_finish = graph.backward_reachable({final_index}) if final_index is not None else set()
    stuck = [s for s in range(len(graph.states)) if s not in can_finish]
    report.option_to_complete = not stuck
    if stuck:
        # Prefer a dead marking as the example: it is the most intuitive one.
        dead = [s for s in stuck if not graph.successors(s)]
        example = dead[0] if dead else stuck[0]
        report.witness["option_to_complete"] = example
        kind = "deadlocks in" if dead else "can never complete from"
        report.add(
            f"No option to complete: the net {kind} {graph.states[example].describe(net)} "
            f"after firing {graph.describe_path(example)}. "
            f"({len(stuck)} of {len(graph.states)} reachable markings cannot reach [o].)",
            graph.path_to(example))

    # 2. proper completion: M ≥ [o] implies M = [o]
    improper = [s for s, m in enumerate(graph.states) if m >= final and m != final]
    report.proper_completion = not improper
    if improper:
        example = improper[0]
        report.witness["proper_completion"] = example
        report.add(
            f"No proper completion: {graph.states[example].describe(net)} is reachable "
            f"via {graph.describe_path(example)}. A token reached {names(workflow.sink)!r} "
            "while other tokens were left behind.", graph.path_to(example))

    # 3. no dead transitions
    fired = graph.fired_transitions()
    dead = [t for t in net.transitions if t not in fired]
    report.no_dead_transitions = not dead
    report.dead_transitions = dead
    if dead:
        report.add("Dead transitions (can never fire): "
                   + ", ".join(names(t) for t in dead) + ".")
    return report


# ---------------------------------------------------------------------------
# Theorem 1: the short-circuited net
# ---------------------------------------------------------------------------
@dataclass
class ShortCircuitReport:
    """(N̄, [i]) -- the WF-net plus ``t*`` from ``o`` back to ``i``.

    Theorem 1 of the paper: N is sound **iff** (N̄, [i]) is *live* and
    *bounded*.  Why this works, informally:

    * t* can only fire once a case has finished with a token in ``o``; it
      starts the next case.  So "every transition can always fire again"
      (live) includes "t* can always fire again", i.e. every case can
      finish -- option to complete.  And it includes every ordinary
      transition, so none is dead.
    * If a case could finish with tokens left behind, t* would start the
      next case on top of them; cycling like that piles tokens up without
      limit -- unbounded.  So bounded rules out improper completion.

    Deadlock-freedom is weaker than liveness (a live net never deadlocks,
    but a net can keep going in a loop while some transition is dead), so
    it is shown for information only.  Note that N itself always stops in
    [o]; in N̄ that marking enables t*, so there deadlocks mean "stuck".
    """

    net: PetriNet
    properties: PropertyReport
    #: transition id -> a reachable state from which it can never fire again
    not_live: dict[str, int] = field(default_factory=dict)

    @property
    def bounded(self) -> bool:
        return self.properties.bounded

    @property
    def safe(self) -> bool:
        return self.properties.safe

    @property
    def deadlock_free(self) -> bool:
        return self.properties.deadlock_free

    @property
    def live(self) -> bool | None:
        if self.properties.live_transitions is None:
            return None
        return len(self.properties.live_transitions) == len(self.net.transitions)

    @property
    def sound(self) -> bool | None:
        """Theorem 1's verdict: live and bounded."""
        if not self.bounded or self.live is False:
            return False
        return self.live


def check_short_circuited(net: PetriNet, source: str, sink: str,
                          max_states: int = 200_000) -> ShortCircuitReport:
    closed = short_circuit(net, source, sink)
    properties = analyse(closed, max_states=max_states)
    report = ShortCircuitReport(closed, properties)
    graph = properties.graph
    if properties.live_transitions is not None:
        distance = graph.distances()
        for transition in closed.transitions:
            if transition in properties.live_transitions:
                continue
            enabling = {state for state in range(len(graph.states))
                        if any(t == transition for t, _ in graph.successors(state))}
            can_fire = graph.backward_reachable(enabling) if enabling else set()
            never = [s for s in range(len(graph.states)) if s not in can_fire]
            if never:
                # the one reached by the shortest firing sequence
                report.not_live[transition] = min(
                    never, key=lambda s: distance.get(s, len(graph.states)))
    return report
