"""State space (reachability graph) generation and analysis.

What a state space is
---------------------
Simulation shows you *one* behaviour of a model.  The state space shows you
*all* of them: every marking reachable from the initial one, and every binding
element leading between them.  From that graph you can prove properties rather
than sample them -- that the model never deadlocks, that a place never holds
more than three tokens, that every transition can eventually occur.

The graph
---------
* **Node**: a :class:`State`, i.e. a marking together with the model clock (the
  clock matters, because the same marking at two different times can behave
  differently in a timed net).
* **Arc**: a binding element that leads from one state to another.

Generation is a plain breadth-first search with a visited set.  Because
markings are hashable and immutable, the visited set is an ordinary Python
``set`` and equality does the work.

The properties we compute, and how
----------------------------------
``Best integer bounds``
    For each place, the largest and smallest number of tokens it holds across
    all reachable markings.  Direct scan.

``Best upper multiset bounds``
    The smallest multiset that dominates every reachable marking of a place,
    i.e. the pointwise maximum of the coefficients.  Also a direct scan.

``Dead markings``
    Nodes with no outgoing arcs.  These are the states the model can get stuck
    in; an empty list is the usual definition of "the model does not deadlock".

``Dead / live transitions``
    Computed from the **strongly connected component** (SCC) graph.  Condensing
    each SCC to a single node turns the state space into a DAG, and the DAG's
    *terminal* components (no arcs leaving them) are the sets of states the
    system ends up cycling within forever.  A transition is
    :term:`live` exactly when it occurs somewhere inside **every** terminal
    component -- meaning that no matter where the system settles, that
    transition can still happen again.  A transition is *dead* when it occurs
    on no arc at all.

``Home markings``
    A marking you can always get back to.  In SCC terms: if the condensed
    graph has exactly one terminal component, every state in it is a home
    marking, because every path eventually reaches that component.  If there is
    more than one, no home markings exist.

Tarjan's algorithm is used for the SCCs, written iteratively so that a state
space of a hundred thousand nodes does not blow the Python recursion limit.

Bounding the search
-------------------
Many interesting models have infinite state spaces.  :meth:`StateSpace.generate`
therefore takes ``max_nodes``; hitting the cap sets :attr:`StateSpace.partial`,
and the report says so, because properties computed from a partial state space
are *not* proofs.
"""

from __future__ import annotations

from collections import deque

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from ..ml.multiset import Multiset, TimedMultiset
from ..model.net import CPNet, Marking, Place, Transition
from ..sim.binding import BindingElement
from ..sim.simulator import Simulator


@dataclass(frozen=True)
class State:
    """A node of the state space: a marking plus the model clock."""

    marking: Marking
    time: int = 0

    def __hash__(self) -> int:
        return hash((self.marking, self.time))


@dataclass(frozen=True)
class StateArc:
    """An arc of the state space: one binding element, from one state to another."""

    source: int
    target: int
    binding: BindingElement


class StateSpace:
    """The reachability graph of a model, plus the analyses over it."""

    def __init__(self, net: CPNet) -> None:
        self.net = net
        #: Node index -> state.  Index 0 is always the initial state.
        self.states: list[State] = []
        #: State -> node index, for the visited check.
        self.index_of: dict[State, int] = {}
        #: Outgoing arcs per node index.
        self.arcs: dict[int, list[StateArc]] = {}
        #: True if generation stopped at ``max_nodes`` rather than exhausting.
        self.partial = False
        #: True if generation was stopped by ``should_stop`` (the user).
        self.cancelled = False
        #: Nodes whose successors were all computed and kept.  In a partial
        #: state space the others are the unexplored frontier: they have no
        #: outgoing arcs *yet*, which must not be mistaken for deadlock.
        self.complete: set[int] = set()

    # =======================================================================
    # Generation
    # =======================================================================
    def generate(self, initial: Marking | None = None, max_nodes: int = 10_000,
                 max_arcs: int = 200_000, should_stop=None, progress=None) -> "StateSpace":
        """Breadth-first exploration from ``initial`` (the model's if omitted).

        The simulator is used in its *pure* mode: we call
        :meth:`~openprocess.sim.simulator.Simulator.fire` with an explicit marking and
        clock, so the simulator's own state is never touched and exploration
        does not disturb an interactive session.

        ``should_stop`` (a no-argument callable) is polled regularly; when it
        returns true, exploration stops and the result is marked ``partial``
        and ``cancelled``.  ``progress(nodes, arcs)`` is called at the same
        points.  Both exist for the GUI, which runs this in a worker thread.
        """
        import time
        simulator = Simulator(self.net, initial)
        start = State(simulator.marking, 0)
        self.states = [start]
        self.index_of = {start: 0}
        self.arcs = {}
        self.partial = False

        self.cancelled = False
        self.complete = set()
        queue: deque[int] = deque([0])
        arc_count = 0
        processed = 0

        while queue:
            processed += 1
            if processed % 5 == 0:
                if progress is not None:
                    progress(len(self.states), arc_count)
                if should_stop is not None and should_stop():
                    self.cancelled = self.partial = True
                    break
                # Let other threads (the window) run: pure-Python exploration
                # would otherwise hold the interpreter lock almost all the time.
                time.sleep(0)
            current_index = queue.popleft()
            current = self.states[current_index]
            self.arcs.setdefault(current_index, [])
            dropped = stopped = False

            for binding, successor in self._successors(simulator, current):
                if successor not in self.index_of:
                    if len(self.states) >= max_nodes:
                        self.partial = dropped = True
                        continue
                    self.index_of[successor] = len(self.states)
                    self.states.append(successor)
                    queue.append(self.index_of[successor])
                self.arcs[current_index].append(
                    StateArc(current_index, self.index_of[successor], binding)
                )
                arc_count += 1
                if arc_count >= max_arcs:
                    self.partial = stopped = True
                    queue.clear()
                    break
            if not (dropped or stopped):
                self.complete.add(current_index)
            if dropped:
                # The node limit is reached: expanding the rest of the queue
                # could only add arcs between known nodes, at the full cost of
                # computing every successor.  Stop here, as CPN Tools does.
                break

        return self

    def _successors(self, simulator: Simulator,
                    state: State) -> Iterator[tuple[BindingElement, State]]:
        """All (binding element, next state) pairs out of one state.

        Mirrors the simulator's own stepping rule, including the time jump: if
        nothing is enabled at the state's clock but tokens are stamped for the
        future, the clock advances and we look again.  Without that, every
        timed model would appear to deadlock.
        """
        simulator.marking = state.marking
        simulator.clock = state.time

        enabled = simulator.all_enabled()
        if not enabled:
            # Let time pass, one token-release moment at a time, until
            # something is enabled -- like Simulator.step.  Stopping after the
            # first jump would call a state dead when the token released
            # first enables nothing but a later one does.
            while not enabled and simulator.advance_time():
                enabled = simulator.all_enabled()
            # The time jump is itself a state change, so we model it as
            # firing from the *advanced* state.
            advanced = simulator.clock
            for element in enabled:
                marking, clock = simulator.fire(element, state.marking, advanced)
                yield element, State(marking, clock)
            return

        for element in enabled:
            marking, clock = simulator.fire(element, state.marking, state.time)
            yield element, State(marking, clock)

    # =======================================================================
    # Basic statistics
    # =======================================================================
    @property
    def node_count(self) -> int:
        return len(self.states)

    @property
    def arc_count(self) -> int:
        return sum(len(arcs) for arcs in self.arcs.values())

    # =======================================================================
    # Boundedness
    # =======================================================================
    def integer_bounds(self) -> dict[str, tuple[int, int]]:
        """Place id -> ``(lower, upper)`` token counts over all reachable states."""
        bounds: dict[str, tuple[int, int]] = {}
        for place in self.net.all_places():
            key = self.net.marking_key(place.id)
            counts = [state.marking.get(key).size() for state in self.states]
            bounds[place.id] = (min(counts), max(counts)) if counts else (0, 0)
        return bounds

    def upper_multiset_bounds(self) -> dict[str, Multiset]:
        """Place id -> the pointwise maximum multiset over all reachable states.

        This is CPN Tools' "best upper multiset bound": the smallest multiset
        ``B`` with ``M(p) <= B`` for every reachable ``M``.  It is strictly more
        informative than the integer bound, because it says *which* colours can
        be present and how many of each.
        """
        if getattr(self, "_multiset_bounds", None) is not None and \
                self._multiset_bounds[0] == len(self.states):
            return self._multiset_bounds[1]
        result: dict[str, Multiset] = {}
        for place in self.net.all_places():
            key = self.net.marking_key(place.id)
            maximum: dict[Any, int] = {}
            # Successive states share the multiset objects of places a firing
            # did not touch, so each distinct object only needs one look.
            seen: set[int] = set()
            for state in self.states:
                tokens = state.marking.get(key)
                if id(tokens) in seen:
                    continue
                seen.add(id(tokens))
                if isinstance(tokens, TimedMultiset):
                    tokens = tokens.available_at(10**12)  # ignore stamps here
                for value, count in tokens._counts.items():
                    if count > maximum.get(value, 0):
                        maximum[value] = count
            result[place.id] = Multiset(maximum)
        self._multiset_bounds = (len(self.states), result)
        return result

    # =======================================================================
    # Strongly connected components (Tarjan, iterative)
    # =======================================================================
    def strongly_connected_components(self) -> list[list[int]]:
        """Partition the nodes into SCCs.

        Iterative Tarjan: the explicit stack replaces recursion so that deep
        state spaces do not hit Python's recursion limit.  Components are
        returned in reverse topological order, which is what Tarjan produces
        naturally.
        """
        index_counter = 0
        indices: dict[int, int] = {}
        low_links: dict[int, int] = {}
        on_stack: set[int] = set()
        stack: list[int] = []
        components: list[list[int]] = []

        for root in range(len(self.states)):
            if root in indices:
                continue
            # Each work item is (node, iterator over its successors).
            work: list[tuple[int, Iterator[int]]] = [
                (root, iter(self._successor_indices(root)))
            ]
            indices[root] = low_links[root] = index_counter
            index_counter += 1
            stack.append(root)
            on_stack.add(root)

            while work:
                node, successors = work[-1]
                advanced = False
                for successor in successors:
                    if successor not in indices:
                        indices[successor] = low_links[successor] = index_counter
                        index_counter += 1
                        stack.append(successor)
                        on_stack.add(successor)
                        work.append((successor, iter(self._successor_indices(successor))))
                        advanced = True
                        break
                    if successor in on_stack:
                        low_links[node] = min(low_links[node], indices[successor])
                if advanced:
                    continue

                work.pop()
                if work:
                    parent = work[-1][0]
                    low_links[parent] = min(low_links[parent], low_links[node])
                if low_links[node] == indices[node]:
                    component: list[int] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    components.append(component)

        return components

    def _successor_indices(self, node: int) -> list[int]:
        return [arc.target for arc in self.arcs.get(node, [])]

    def terminal_components(self) -> list[list[int]]:
        """SCCs with no arcs leaving them -- where the system ends up."""
        components = self.strongly_connected_components()
        component_of: dict[int, int] = {}
        for number, component in enumerate(components):
            for node in component:
                component_of[node] = number

        terminal: list[list[int]] = []
        for number, component in enumerate(components):
            members = set(component)
            if all(
                component_of[arc.target] == number
                for node in component
                for arc in self.arcs.get(node, [])
            ):
                terminal.append(component)
        return terminal

    # =======================================================================
    # Liveness and home properties
    # =======================================================================
    def dead_markings(self) -> list[int]:
        """Nodes from which nothing can happen -- the deadlocks.

        Only fully explored nodes count: in a partial state space a frontier
        node has no arcs simply because it was never expanded.
        """
        return [index for index in sorted(self.complete) if not self.arcs.get(index)]

    @property
    def unexplored_count(self) -> int:
        """Nodes found but not (fully) expanded -- zero for a full state space."""
        return len(self.states) - len(self.complete)

    def occurring_transitions(self) -> set[str]:
        """Transition ids that label at least one arc."""
        return {
            arc.binding.transition_id
            for arcs in self.arcs.values()
            for arc in arcs
        }

    def dead_transitions(self) -> list[Transition]:
        """Transitions that can never occur from the initial marking."""
        occurring = self.occurring_transitions()
        return [
            transition
            for transition in self.net.all_transitions()
            if not transition.is_substitution and transition.id not in occurring
        ]

    def live_transitions(self) -> list[Transition]:
        """Transitions that can always eventually occur again.

        A transition is live iff it labels an arc inside **every** terminal
        SCC.  Intuition: whichever cycle the system ultimately settles into,
        this transition still occurs within it.
        """
        terminals = self.terminal_components()
        if not terminals:
            return []

        # The transitions occurring inside each terminal component.
        occurring: list[set[str]] = []
        for component in terminals:
            members = set(component)
            occurring.append({
                arc.binding.transition_id
                for node in component
                for arc in self.arcs.get(node, [])
                if arc.target in members
            })
        return [
            transition for transition in self.net.all_transitions()
            if not transition.is_substitution
            and all(transition.id in inside for inside in occurring)
        ]

    def home_markings(self) -> list[int]:
        """Markings reachable from every reachable marking.

        Exists iff the condensed graph has exactly one terminal SCC, in which
        case every state in that SCC is a home marking.
        """
        terminals = self.terminal_components()
        if len(terminals) != 1:
            return []
        return sorted(terminals[0])

    # =======================================================================
    # Reporting
    # =======================================================================
    def report(self, max_listed: int = 10) -> str:
        """A textual report modelled on CPN Tools' state space report."""
        lines: list[str] = []
        add = lines.append

        add("=" * 66)
        add(f" State space report for: {self.net.name}")
        add("=" * 66)
        add("")
        add("Statistics")
        add("----------")
        add(f"  Nodes: {self.node_count}")
        add(f"  Arcs:  {self.arc_count}")
        status = ("PARTIAL (stopped by the user)" if self.cancelled else
                  "PARTIAL (node/arc limit reached)" if self.partial else "Full")
        add(f"  Status: {status}")
        if self.partial:
            add("  WARNING: the properties below describe only the explored")
            add("           fragment and are not proofs about the whole model.")
        ignored = getattr(self.net, "warnings", [])
        if ignored:
            add(f"  WARNING: {len(ignored)} transition priorities or code segments are not")
            add("           implemented and were not used: this state space can differ")
            add("           from the one CPN Tools computes.")
            for issue in ignored[:max_listed]:
                add(f"             {issue.element_name or issue.element_id} ({issue.field_name})")
        add("")

        add("Boundedness Properties")
        add("----------------------")
        add("  Best Integer Bounds        Upper      Lower")
        bounds = self.integer_bounds()
        for place in self.net.all_places():
            lower, upper = bounds[place.id]
            add(f"    {place.name:<24}{upper:<11}{lower}")
        add("")
        add("  Best Upper Multiset Bounds")
        multiset_bounds = self.upper_multiset_bounds()          # computed once
        for place in self.net.all_places():
            add(f"    {place.name:<24}{multiset_bounds[place.id]}")
        add("")

        add("Home Properties")
        add("---------------")
        home = [] if self.partial else self.home_markings()
        if self.partial:
            add("  Home Markings: not determined (needs the full state space)")
        elif not home:
            add("  Home Markings: None")
        elif len(home) == self.node_count:
            add("  Home Markings: All")
        else:
            shown = ", ".join(str(node) for node in home[:max_listed])
            more = "" if len(home) <= max_listed else f", ... ({len(home)} total)"
            add(f"  Home Markings: [{shown}{more}]")
        add("")

        add("Liveness Properties")
        add("-------------------")
        dead = self.dead_markings()
        if self.partial:
            add(f"  Unexplored nodes: {self.unexplored_count} (not counted as dead)")
        if not dead:
            add("  Dead Markings: None" + (" among the explored nodes" if self.partial else ""))
        else:
            shown = ", ".join(str(node) for node in dead[:max_listed])
            more = "" if len(dead) <= max_listed else f", ... ({len(dead)} total)"
            add(f"  Dead Markings: [{shown}{more}]")

        dead_transitions = self.dead_transitions()
        caption = ("Transitions not seen in the explored part" if self.partial
                   else "Dead Transition Instances")
        add(f"  {caption}: "
            f"{', '.join(t.name for t in dead_transitions) if dead_transitions else 'None'}")

        if self.partial:
            add("  Live Transition Instances: not determined (needs the full state space)")
        else:
            live = self.live_transitions()
            add(f"  Live Transition Instances: "
                f"{', '.join(t.name for t in live) if live else 'None'}")
        add("")
        add("Fairness Properties")
        add("-------------------")
        add("  Not computed by this implementation.")
        add("")
        return "\n".join(lines)

    # -- convenience ---------------------------------------------------------
    def describe_state(self, index: int) -> str:
        """Human-readable dump of one node."""
        state = self.states[index]
        header = f"Node {index}" + (f" (time {state.time})" if state.time else "")
        return f"{header}\n{state.marking.describe(self.net)}"
