"""State-based regions: from a transition system to a Petri net.

Definitions (Cortadella et al. 1998; van der Aalst, *Process Mining*, Section 7.4.2)
--------------------------------------------------------------------------------------
Let ``TS = (S, E, T, s_in)`` be a transition system.

region
    A set of states ``R ⊆ S`` such that, for every event ``e``, *all*
    transitions labelled ``e`` do the same thing with respect to ``R``:
    they all **enter** it (start outside, end inside), all **exit** it
    (start inside, end outside), or all **do not cross** it (both ends
    inside, or both outside).  ``∅`` and ``S`` are the trivial regions.  The
    complement of a region is a region too (enter and exit swap).
minimal region
    A non-empty region that contains no smaller non-empty region.
pre-region / post-region
    ``R`` is a pre-region of ``e`` if ``e`` exits ``R``, a post-region if
    ``e`` enters it.  A region is a place in disguise: its pre-events put a
    token in, its post-events take it out.
generalised excitation region
    ``GER(e)``: the states in which ``e`` is enabled.
elementary transition system
    One that satisfies
    *state separation* -- any two distinct states are told apart by some
    region (one is inside it, the other is not) -- and
    *forward closure* -- for every event, the intersection of its
    pre-regions is exactly ``GER(e)``, i.e. the places before ``e`` are
    marked only where ``e`` really is enabled.

Synthesis
---------
One place per minimal region, an arc ``R → e`` for each pre-region and
``e → R`` for each post-region, and a token in every minimal region that
contains ``s_in``.  For an elementary transition system the reachability
graph of the result is isomorphic to the transition system (checked by
:func:`synthesise`).

Finding the regions
-------------------
Course-sized transition systems are solved exactly: every assignment of the
states to inside/outside is explored, abandoning an assignment as soon as two
transitions with the same event cross it differently.  That is the same as
checking every subset of states, only much faster, and it is limited to
:data:`MAX_STATES` states (and :data:`MAX_REGIONS` regions); the result says
when a limit cut it short.  (Larger systems need the expansion algorithm of
Cortadella et al., which is not implemented.)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .petrinet import Marking, PetriNet
from .transition_system import TransitionSystem, isomorphic, transition_system_of_graph

#: Larger transition systems are not searched for regions.
MAX_STATES = 26
#: Stop collecting regions after this many (the result says so).
MAX_REGIONS = 50_000

ENTER, EXIT, INSIDE, OUTSIDE = "enter", "exit", "inside", "outside"
#: ``inside`` and ``outside`` are both "does not cross".
NO_CROSS = "no cross"


def crossing(region: frozenset[str] | set[str], source: str, target: str) -> str:
    """What one transition does with respect to ``region``."""
    a, b = source in region, target in region
    if a and b:
        return INSIDE
    if not a and not b:
        return OUTSIDE
    return EXIT if a else ENTER


def _kind(relation: str) -> str:
    return NO_CROSS if relation in (INSIDE, OUTSIDE) else relation


def format_states(states, order: list[str] | None = None) -> str:
    """``{s0, s2, s3}`` in the transition system's own order."""
    items = list(states)
    if order is not None:
        position = {s: i for i, s in enumerate(order)}
        items.sort(key=lambda s: position.get(s, len(position)))
    else:
        items.sort()
    return "{" + ", ".join(items) + "}"


# ---------------------------------------------------------------------------
# Is this a region?
# ---------------------------------------------------------------------------
@dataclass
class RegionCheck:
    states: frozenset[str]
    is_region: bool
    #: For a region: what each event does (enter, exit or no cross).
    events: dict[str, str] = field(default_factory=dict)
    #: When it is not: the event that breaks it, and two of its transitions
    #: that cross the set differently, with what each does.
    event: str | None = None
    conflict: list[tuple[str, str, str]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    #: The empty set or every state.
    trivial: bool = False

    def explanation(self, order: list[str] | None = None) -> str:
        shown = format_states(self.states, order)
        if self.unknown:
            return "Not states of this transition system: " + ", ".join(self.unknown) + "."
        if self.is_region:
            trivial = " (a trivial region)" if self.trivial else ""
            parts = [f"{e} {what}s" if what != NO_CROSS else f"{e} does not cross"
                     for e, what in self.events.items()]
            return f"{shown} is a region{trivial}: " + ", ".join(parts) + "."
        (s1, t1, r1), (s2, t2, r2) = self.conflict[:2]

        def said(relation: str) -> str:
            return {ENTER: "enters", EXIT: "exits", INSIDE: "does not cross (stays inside)",
                    OUTSIDE: "does not cross (stays outside)"}[relation]
        return (f"{shown} is not a region: event {self.event} has {s1} -{self.event}-> {t1}, "
                f"which {said(r1)}, but {s2} -{self.event}-> {t2}, which {said(r2)}.")


def check_region(ts: TransitionSystem, states) -> RegionCheck:
    """Is ``states`` a region of ``ts``?  If not, which event breaks it and how.

    ``states`` is a collection of state names, or the set written as text
    (``"{s1, s3}"``)."""
    if isinstance(states, str):
        states = [s.strip() for s in states.strip().strip("{}").split(",") if s.strip()]
    region = frozenset(states)
    unknown = sorted(region - set(ts.states))
    if unknown:
        return RegionCheck(region, False, unknown=unknown)
    events: dict[str, str] = {}
    for event in ts.events:
        first: tuple[str, str, str] | None = None
        for source, target in ts.transitions_of(event):
            relation = crossing(region, source, target)
            if first is None:
                first = (source, target, relation)
                events[event] = _kind(relation)
            elif _kind(relation) != _kind(first[2]):
                return RegionCheck(region, False, event=event,
                                   conflict=[first, (source, target, relation)])
    return RegionCheck(region, True, events=events,
                       trivial=not region or region == frozenset(ts.states))


# ---------------------------------------------------------------------------
# All regions
# ---------------------------------------------------------------------------
@dataclass
class EventRegions:
    """Everything about one event's place in the net."""

    event: str
    ger: frozenset[str]
    pre: list[frozenset[str]]                # every pre-region (e exits)
    post: list[frozenset[str]]               # every post-region (e enters)
    minimal_pre: list[frozenset[str]]
    minimal_post: list[frozenset[str]]
    #: ⋂ pre(e); S when e has no pre-region.
    intersection: frozenset[str]

    @property
    def forward_closed(self) -> bool:
        return self.intersection == self.ger


@dataclass
class Separation:
    """Two states no region tells apart, and why."""

    first: str
    second: str
    #: Events whose transitions force every region to contain both or neither,
    #: as (event, shared neighbour, "target" | "source").
    reasons: list[tuple[str, str, str]] = field(default_factory=list)

    def explanation(self) -> str:
        a, b = self.first, self.second
        if not self.reasons:
            return (f"{a} and {b} cannot be separated: every region contains both of them "
                    "or neither.")
        lines = []
        for event, other, role in self.reasons:
            if role == "target":
                lines.append(f"{a} -{event}-> {other} and {b} -{event}-> {other}: a region "
                             f"with {a} but not {b} would have one {event} step stay put and "
                             f"the other cross it, whether {other} is inside or not")
            else:
                lines.append(f"{other} -{event}-> {a} and {other} -{event}-> {b}: a region "
                             f"with {a} but not {b} would have one {event} step stay put and "
                             f"the other cross it, whether {other} is inside or not")
        return f"{a} and {b} cannot be separated. " + "; ".join(lines) + "."


@dataclass
class RegionAnalysis:
    ts: TransitionSystem
    #: Every non-trivial region, smallest first.
    regions: list[frozenset[str]] = field(default_factory=list)
    minimal: list[frozenset[str]] = field(default_factory=list)
    by_event: dict[str, EventRegions] = field(default_factory=dict)
    #: Pairs of distinct states no region separates.
    inseparable: list[Separation] = field(default_factory=list)
    #: The search stopped early (too many states or regions): verdicts are not proofs.
    truncated: bool = False
    #: Why the search did not run or stopped.
    limit_message: str = ""

    @property
    def state_separation(self) -> bool | None:
        return None if self.truncated else not self.inseparable

    @property
    def forward_closure(self) -> bool | None:
        return None if self.truncated else all(r.forward_closed for r in self.by_event.values())

    @property
    def elementary(self) -> bool | None:
        if self.state_separation is False or self.forward_closure is False:
            return False
        if self.state_separation is None or self.forward_closure is None:
            return None
        return True

    def closure_failures(self) -> list[tuple[str, frozenset[str]]]:
        """Each event whose pre-regions meet outside GER(e), with those extra states."""
        return [(event, r.intersection - r.ger) for event, r in self.by_event.items()
                if not r.forward_closed]

    def name_of(self, region: frozenset[str]) -> str:
        """``r3`` for the third minimal region (places are named so)."""
        try:
            return f"r{self.minimal.index(region) + 1}"
        except ValueError:
            return format_states(region, self.ts.states)

    def region_text(self, region: frozenset[str]) -> str:
        return format_states(region, self.ts.states)


def _enumerate(ts: TransitionSystem, limit: int) -> tuple[list[int], bool]:
    """Every non-trivial region as a bitmask over ``ts.states`` (and whether cut short)."""
    states = ts.states
    index = {s: i for i, s in enumerate(states)}
    n = len(states)
    # Visit states breadth-first from the start, so neighbours are decided
    # close together and conflicts show up early.
    order: list[int] = []
    seen: set[int] = set()
    adjacency: dict[int, set[int]] = {i: set() for i in range(n)}
    for s, _, t in ts.transitions:
        adjacency[index[s]].add(index[t])
        adjacency[index[t]].add(index[s])
    for root in [index[s] for s in ts.initial if s in index] + list(range(n)):
        if root in seen:
            continue
        seen.add(root)
        queue = deque([root])
        while queue:
            node = queue.popleft()
            order.append(node)
            for other in sorted(adjacency[node]):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
    events = ts.events
    event_index = {e: k for k, e in enumerate(events)}
    # For each state: the transitions touching it, as (event no., source, target).
    touching: dict[int, list[tuple[int, int, int]]] = {i: [] for i in range(n)}
    for s, e, t in ts.transitions:
        step = (event_index[e], index[s], index[t])
        touching[index[s]].append(step)
        if t != s:
            touching[index[t]].append(step)
    value = [-1] * n
    # counts[event][kind]: decided transitions of the event of each kind (0 enter, 1 exit, 2 no).
    counts = [[0, 0, 0] for _ in events]
    found: list[int] = []
    full = (1 << n) - 1
    stopped = [False]

    def kind(source: int, target: int) -> int:
        a, b = value[source], value[target]
        if a == b:
            return 2
        return 0 if b == 1 else 1

    def assign(position: int, mask: int) -> None:
        if stopped[0]:
            return
        if position == n:
            if mask and mask != full:
                found.append(mask)
                if len(found) >= limit:
                    stopped[0] = True
            return
        state = order[position]
        for choice in (0, 1):
            value[state] = choice
            changed = []
            ok = True
            for event, source, target in touching[state]:
                if value[source] < 0 or value[target] < 0:
                    continue
                k = kind(source, target)
                counts[event][k] += 1
                changed.append((event, k))
                tally = counts[event]
                if (tally[0] > 0) + (tally[1] > 0) + (tally[2] > 0) > 1:
                    ok = False
                    break
            if ok:
                assign(position + 1, mask | (1 << state) if choice else mask)
            for event, k in changed:
                counts[event][k] -= 1
            value[state] = -1
            if stopped[0]:
                return

    assign(0, 0)
    return found, stopped[0]


def analyse_regions(ts: TransitionSystem, max_states: int = MAX_STATES,
                    max_regions: int = MAX_REGIONS) -> RegionAnalysis:
    """Regions, minimal regions, pre-/post-regions, GER and the elementary checks."""
    analysis = RegionAnalysis(ts)
    states = ts.states
    if len(states) > max_states:
        analysis.truncated = True
        analysis.limit_message = (f"The transition system has {len(states)} states; regions "
                                  f"are only searched for up to {max_states}.")
        return analysis
    masks, cut = _enumerate(ts, max_regions)
    if cut:
        analysis.truncated = True
        analysis.limit_message = (f"Stopped after {max_regions:,} regions; the lists are "
                                  "incomplete and the verdicts are not proofs.")
    masks.sort(key=lambda m: (bin(m).count("1"), m))

    def as_set(mask: int) -> frozenset[str]:
        return frozenset(s for i, s in enumerate(states) if mask >> i & 1)

    minimal_masks: list[int] = []
    for mask in masks:
        if not any(m & mask == m for m in minimal_masks):
            minimal_masks.append(mask)
    analysis.regions = [as_set(m) for m in masks]
    analysis.minimal = [as_set(m) for m in minimal_masks]
    minimal_set = set(minimal_masks)

    index = {s: i for i, s in enumerate(states)}
    every = (1 << len(states)) - 1
    for event in ts.events:
        steps = [(index[s], index[t]) for s, t in ts.transitions_of(event)]
        ger_mask = 0
        for source, _ in steps:
            ger_mask |= 1 << source
        pre, post = [], []
        for mask in masks:
            source, target = steps[0]
            a, b = mask >> source & 1, mask >> target & 1
            if a and not b:
                pre.append(mask)
            elif b and not a:
                post.append(mask)
        intersection = every
        for mask in pre:
            intersection &= mask
        analysis.by_event[event] = EventRegions(
            event, as_set(ger_mask), [as_set(m) for m in pre], [as_set(m) for m in post],
            [as_set(m) for m in pre if m in minimal_set],
            [as_set(m) for m in post if m in minimal_set], as_set(intersection))

    # State separation: two states are told apart by some region unless every
    # region contains both or neither, i.e. they have the same "signature".
    signature: dict[str, int] = {s: 0 for s in states}
    for number, mask in enumerate(masks):
        for i, s in enumerate(states):
            if mask >> i & 1:
                signature[s] |= 1 << number
    groups: dict[int, list[str]] = {}
    for s in states:
        groups.setdefault(signature[s], []).append(s)
    for group in groups.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                analysis.inseparable.append(Separation(a, b, _separation_reasons(ts, a, b)))
    return analysis


def _separation_reasons(ts: TransitionSystem, a: str, b: str) -> list[tuple[str, str, str]]:
    """Events that go from ``a`` and ``b`` to one state (or come from one state):
    those two steps would cross a region containing only one of them differently."""
    reasons = []
    for event in ts.events:
        steps = ts.transitions_of(event)
        from_a = {t for s, t in steps if s == a}
        from_b = {t for s, t in steps if s == b}
        for other in sorted(from_a & from_b):
            reasons.append((event, other, "target"))
        into_a = {s for s, t in steps if t == a}
        into_b = {s for s, t in steps if t == b}
        for other in sorted(into_a & into_b):
            reasons.append((event, other, "source"))
    return reasons


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------
@dataclass
class Synthesis:
    analysis: RegionAnalysis
    net: PetriNet | None
    #: Is the net's reachability graph isomorphic to the transition system?
    #: None when it could not be checked.
    isomorphic: bool | None = None
    warnings: list[str] = field(default_factory=list)
    #: Place id -> its minimal region.
    places: dict[str, frozenset[str]] = field(default_factory=dict)


def synthesise(ts: TransitionSystem, analysis: RegionAnalysis | None = None,
               name: str | None = None) -> Synthesis:
    """The Petri net of the minimal regions (see the module docstring)."""
    analysis = analysis or analyse_regions(ts)
    result = Synthesis(analysis, None)
    if analysis.limit_message and not analysis.regions:
        result.warnings.append(analysis.limit_message)
        return result
    start = ts.initial_state
    if start is None:
        result.warnings.append(
            f"The transition system has {len(ts.initial)} initial states; region synthesis "
            "needs exactly one (the postfix gives every trace its own start, so use the "
            "prefix).")
        return result
    if analysis.truncated:
        result.warnings.append(analysis.limit_message)
    net = PetriNet(name or f"Regions · {ts.name}")
    net.info["algorithm"] = "State-based regions"
    if ts.abstraction:
        net.info["abstraction"] = ts.abstraction
    transition_of = {}
    for event in ts.events:
        transition_of[event] = net.add_transition(event, id=f"t_{len(transition_of) + 1}").id
    for number, region in enumerate(analysis.minimal, 1):
        place = net.add_place(f"r{number}", id=f"r{number}")
        result.places[place.id] = region
        for event, regions in analysis.by_event.items():
            if region in regions.minimal_pre:
                net.add_arc(place.id, transition_of[event])
            if region in regions.minimal_post:
                net.add_arc(transition_of[event], place.id)
    net.initial_marking = Marking({p: 1 for p, region in result.places.items() if start in region})
    # The final marking: when every final state stands for the same marking.
    finals = {Marking({p: 1 for p, region in result.places.items() if end in region})
              for end in ts.final}
    if len(finals) == 1:
        net.final_marking = finals.pop()
    elif finals:
        result.warnings.append(f"The traces end in {len(ts.final)} different states that stand for "
                               f"{len(finals)} different markings, so the net has no single final marking; "
                               "token replay and alignments need one, so set it before checking "
                               "conformance (an abstraction with a shorter horizon or the postfix "
                               "direction usually gives one).")
    loops = sorted({e for s, e, t in ts.transitions if s == t})
    if loops:
        result.warnings.append("Self-loops (" + ", ".join(loops) + ") cross no region, so "
                               "the net cannot show them.")
    if analysis.elementary is False:
        result.warnings.append("The transition system is not elementary, so the net does not "
                               "behave exactly like it (it may allow more).")
    result.net = net
    from .analysis import reachability_graph
    graph = reachability_graph(net, max_states=max(2_000, 20 * len(ts.states)))
    if not graph.truncated:
        result.isomorphic = isomorphic(transition_system_of_graph(graph), ts)
    return result
