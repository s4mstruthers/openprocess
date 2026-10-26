"""Place/Transition nets, the model class that discovery produces.

Definitions (van der Aalst, *Workflow Verification*, §2)
-------------------------------------------------------
A Petri net is a triple ``(P, T, F)``:

* ``P`` -- a finite set of **places** (drawn as circles),
* ``T`` -- a finite set of **transitions** (drawn as squares), disjoint from P,
* ``F ⊆ (P × T) ∪ (T × P)`` -- the **flow relation** (the arcs).

We also allow an arc *weight* (default 1), which the textbook's basic nets do
not need but PNML files sometimes contain.

The **pre-set** ``•x`` of a node is the set of nodes with an arc *into* x,
the **post-set** ``x•`` those with an arc *out of* x.

A **marking** assigns a number of tokens to every place; formally a multiset
over P, e.g. ``[i]`` or ``[p1, p2^2]``.

**Firing rule.**  Transition ``t`` is *enabled* in marking M iff every input
place holds at least as many tokens as the arc weight requires.  Firing t
removes those tokens and adds tokens to every output place::

    M' = M - •t + t•

Labelled nets
-------------
In process mining every transition carries an **activity label** (e.g.
``"register request"``) -- or no label at all.  An unlabelled transition is a
**silent** (τ, "tau") transition: it moves tokens but leaves no trace in the
log.  Discovery algorithms such as the Inductive Miner use τ transitions to
express skips and loop-backs.  Two transitions may share a label
(*duplicate* labels); the α-algorithm never produces those, but hand-made
models can have them.

Accepting Petri nets
--------------------
For conformance checking a net also needs an **initial** and a **final**
marking: a log trace "fits" if the model can start in the initial marking,
replay the trace, and end in the final marking.  :class:`PetriNet` therefore
stores both.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping


# ---------------------------------------------------------------------------
# Markings
# ---------------------------------------------------------------------------
class Marking(Mapping[str, int]):
    """An immutable multiset of place ids.

    Immutability matters: markings are stored in sets and used as dictionary
    keys during state-space exploration, which requires a hash that never
    changes.  Every operation therefore returns a *new* marking.

    Zero counts are never stored, so ``Marking({"p": 0}) == Marking()``.
    """

    __slots__ = ("_counts", "_hash")

    def __init__(self, counts: Mapping[str, int] | Iterable[str] | None = None) -> None:
        if counts is None:
            data: dict[str, int] = {}
        elif isinstance(counts, Mapping):
            # Counts are ints, or math.inf (ω) in coverability graphs.
            data = {place: n for place, n in counts.items() if n}
        else:
            data = dict(Counter(counts))
        self._counts = data
        self._hash: int | None = None

    # Mapping protocol -------------------------------------------------------
    def __getitem__(self, place: str) -> int:
        return self._counts.get(place, 0)

    def __iter__(self) -> Iterator[str]:
        return iter(self._counts)

    def __len__(self) -> int:
        return len(self._counts)

    def __contains__(self, place: object) -> bool:
        return place in self._counts

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(frozenset(self._counts.items()))
        return self._hash

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Marking):
            return self._counts == other._counts
        if isinstance(other, Mapping):
            return self._counts == {k: v for k, v in other.items() if v}
        return NotImplemented

    # Multiset algebra -------------------------------------------------------
    def __add__(self, other: Mapping[str, int]) -> "Marking":
        result = dict(self._counts)
        for place, n in other.items():
            result[place] = result.get(place, 0) + n
        return Marking(result)

    def __sub__(self, other: Mapping[str, int]) -> "Marking":
        """Multiset difference; the caller guarantees ``other ≤ self``."""
        result = dict(self._counts)
        for place, n in other.items():
            result[place] = result.get(place, 0) - n
        return Marking(result)

    def __ge__(self, other: Mapping[str, int]) -> bool:  # type: ignore[override]
        """``self ≥ other`` pointwise: every place has at least as many tokens."""
        return all(self._counts.get(place, 0) >= n for place, n in other.items())

    def __le__(self, other: Mapping[str, int]) -> bool:  # type: ignore[override]
        return all(other.get(place, 0) >= n for place, n in self._counts.items())

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def items(self):  # type: ignore[override]
        return self._counts.items()

    def describe(self, net: "PetriNet | None" = None) -> str:
        """Textbook notation, e.g. ``[p1, p2^2]``; ``[]`` for the empty marking."""
        def name(place_id: str) -> str:
            if net is not None and place_id in net.places:
                return net.places[place_id].name
            return place_id
        parts = []
        for place_id in sorted(self._counts, key=name):
            n = self._counts[place_id]
            count = "ω" if n == float("inf") else str(n)
            parts.append(name(place_id) + (f"^{count}" if n != 1 else ""))
        return "[" + ", ".join(parts) + "]"

    def __repr__(self) -> str:
        return f"Marking({self._counts!r})"


# ---------------------------------------------------------------------------
# Nodes and arcs
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class Place:
    id: str
    name: str
    position: tuple[float, float] | None = None
    #: Where the name label sits, relative to the node (drawing only).
    name_offset: tuple[float, float] | None = None


@dataclass(eq=False)
class Transition:
    id: str
    name: str
    label: str | None = None     # None means silent (τ)
    position: tuple[float, float] | None = None
    #: Where the name label sits, relative to the node (drawing only).
    name_offset: tuple[float, float] | None = None

    @property
    def silent(self) -> bool:
        return self.label is None


@dataclass(eq=False)
class Arc:
    source: str      # a place id or transition id
    target: str
    weight: int = 1
    #: Bend points from source to target (drawing only; PNML ``<position>``s).
    points: list[tuple[float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The net
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class PetriNet:
    """A labelled P/T net with an initial and a final marking."""

    name: str = "Petri net"
    places: dict[str, Place] = field(default_factory=dict)
    transitions: dict[str, Transition] = field(default_factory=dict)
    arcs: list[Arc] = field(default_factory=list)
    initial_marking: Marking = field(default_factory=Marking)
    final_marking: Marking = field(default_factory=Marking)
    #: Free-form provenance, e.g. ``{"algorithm": "Alpha", "log": "..."}``.
    info: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._ids = itertools.count(1)
        self._cache: dict | None = None

    # A net travels to worker processes (parallel alignments and replay) pickled:
    # the id counter and the structure cache stay behind and are made afresh
    # (new ids skip those in use; the cache is rebuilt when first needed).
    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_ids", None)
        state["_cache"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._ids = itertools.count(1)

    # -- construction -----------------------------------------------------
    def _fresh_id(self, prefix: str) -> str:
        while True:
            candidate = f"{prefix}{next(self._ids)}"
            if candidate not in self.places and candidate not in self.transitions:
                return candidate

    def add_place(self, name: str | None = None, id: str | None = None) -> Place:
        place_id = id or self._fresh_id("p")
        place = Place(place_id, name if name is not None else place_id)
        self.places[place_id] = place
        self._cache = None
        return place

    def add_transition(self, label: str | None, name: str | None = None,
                       id: str | None = None) -> Transition:
        """Add a transition; ``label=None`` makes it silent."""
        transition_id = id or self._fresh_id("t")
        display = name if name is not None else (label if label is not None else "τ")
        transition = Transition(transition_id, display, label)
        self.transitions[transition_id] = transition
        self._cache = None
        return transition

    def add_arc(self, source: Place | Transition | str, target: Place | Transition | str,
                weight: int = 1) -> Arc:
        source_id = source if isinstance(source, str) else source.id
        target_id = target if isinstance(target, str) else target.id
        source_is_place = source_id in self.places
        target_is_place = target_id in self.places
        if source_is_place == target_is_place:
            raise ValueError("An arc must connect a place and a transition "
                             f"({source_id!r} -> {target_id!r}).")
        arc = Arc(source_id, target_id, weight)
        self.arcs.append(arc)
        self._cache = None
        return arc

    def remove_place(self, place_id: str) -> None:
        self.places.pop(place_id, None)
        self.arcs = [a for a in self.arcs if place_id not in (a.source, a.target)]
        self._cache = None

    def remove_transition(self, transition_id: str) -> None:
        self.transitions.pop(transition_id, None)
        self.arcs = [a for a in self.arcs if transition_id not in (a.source, a.target)]
        self._cache = None

    # -- structure (cached; rebuilt after any edit) ------------------------
    def _structure(self) -> dict:
        if self._cache is None:
            pre: dict[str, Counter] = {t: Counter() for t in self.transitions}
            post: dict[str, Counter] = {t: Counter() for t in self.transitions}
            place_in: dict[str, set[str]] = {p: set() for p in self.places}
            place_out: dict[str, set[str]] = {p: set() for p in self.places}
            for arc in self.arcs:
                if arc.source in self.transitions:          # t -> p
                    post[arc.source][arc.target] += arc.weight
                    place_in[arc.target].add(arc.source)
                else:                                         # p -> t
                    pre[arc.target][arc.source] += arc.weight
                    place_out[arc.source].add(arc.target)
            self._cache = {
                "pre": {t: Marking(c) for t, c in pre.items()},
                "post": {t: Marking(c) for t, c in post.items()},
                "place_in": place_in,
                "place_out": place_out,
            }
        return self._cache

    def pre(self, transition_id: str) -> Marking:
        """The input places of ``t`` with arc weights (•t as a multiset)."""
        return self._structure()["pre"][transition_id]

    def post(self, transition_id: str) -> Marking:
        """The output places of ``t`` with arc weights (t• as a multiset)."""
        return self._structure()["post"][transition_id]

    def preset(self, node_id: str) -> set[str]:
        """•x for any node."""
        if node_id in self.transitions:
            return set(self.pre(node_id))
        return set(self._structure()["place_in"][node_id])

    def postset(self, node_id: str) -> set[str]:
        """x• for any node."""
        if node_id in self.transitions:
            return set(self.post(node_id))
        return set(self._structure()["place_out"][node_id])

    # -- behaviour --------------------------------------------------------
    def is_enabled(self, marking: Marking, transition_id: str) -> bool:
        return marking >= self.pre(transition_id)

    def enabled(self, marking: Marking) -> list[str]:
        """Ids of all transitions enabled in ``marking``, in a stable order."""
        return [t for t in self.transitions if marking >= self.pre(t)]

    def fire(self, marking: Marking, transition_id: str) -> Marking:
        """``M' = M - •t + t•``.  Raises if ``t`` is not enabled."""
        pre = self.pre(transition_id)
        if not marking >= pre:
            raise ValueError(f"Transition {self.transitions[transition_id].name!r} "
                             f"is not enabled in {marking.describe(self)}.")
        return marking - pre + self.post(transition_id)

    # -- queries ----------------------------------------------------------
    def labels(self) -> set[str]:
        """The set of visible activity labels."""
        return {t.label for t in self.transitions.values() if t.label is not None}

    def transitions_with_label(self, label: str) -> list[str]:
        return [t.id for t in self.transitions.values() if t.label == label]

    def source_places(self) -> list[str]:
        """Places with an empty pre-set."""
        return [p for p in self.places if not self._structure()["place_in"][p]]

    def sink_places(self) -> list[str]:
        """Places with an empty post-set."""
        return [p for p in self.places if not self._structure()["place_out"][p]]

    def node_name(self, node_id: str) -> str:
        if node_id in self.places:
            return self.places[node_id].name
        if node_id in self.transitions:
            return self.transitions[node_id].name
        return node_id

    def copy(self) -> "PetriNet":
        clone = PetriNet(self.name)
        for p in self.places.values():
            clone.places[p.id] = Place(p.id, p.name, p.position)
        for t in self.transitions.values():
            clone.transitions[t.id] = Transition(t.id, t.name, t.label, t.position)
        clone.arcs = [Arc(a.source, a.target, a.weight) for a in self.arcs]
        clone.initial_marking = self.initial_marking
        clone.final_marking = self.final_marking
        clone.info = dict(self.info)
        return clone

    def summary(self) -> str:
        silent = sum(1 for t in self.transitions.values() if t.silent)

        def count(number: int, noun: str) -> str:
            return f"{number} {noun}{'' if number == 1 else 's'}"
        return (f"{count(len(self.places), 'place')}, "
                f"{count(len(self.transitions), 'transition')} ({silent} silent), "
                f"{count(len(self.arcs), 'arc')}")

    def __repr__(self) -> str:
        return f"<PetriNet {self.name!r}: {self.summary()}>"
