"""The α-algorithm (van der Aalst, Weijters & Maruster, 2004).

The eight steps (book, Definition 6.4), for a log L over activities T:

1. ``T_L = { t | t occurs in some trace }``                  -- the transitions
2. ``T_I = { t | t is the first activity of some trace }``   -- start activities
3. ``T_O = { t | t is the last activity of some trace }``    -- end activities
4. ``X_L = { (A, B) | A ⊆ T_L, A ≠ ∅, B ⊆ T_L, B ≠ ∅,
   ∀a∈A ∀b∈B: a →_L b,  ∀a1,a2∈A: a1 #_L a2,  ∀b1,b2∈B: b1 #_L b2 }``
5. ``Y_L = { (A, B) ∈ X_L | no (A', B') ∈ X_L with A ⊆ A', B ⊆ B', (A,B) ≠ (A',B') }``
   -- keep only the *maximal* pairs
6. ``P_L = { p_(A,B) | (A,B) ∈ Y_L } ∪ { i_L, o_L }``
7. ``F_L`` connects every ``a ∈ A`` to ``p_(A,B)``, ``p_(A,B)`` to every ``b ∈ B``,
   ``i_L`` to every start activity and every end activity to ``o_L``
8. ``α(L) = (P_L, T_L, F_L)``

Intuition for step 4: a place between A and B must be *fed* by the
activities in A and *consumed* by those in B.  Every a must cause every b
(``→``), and no two members of A (or of B) may ever be seen together in
either order (``#``) -- if they were, they would be concurrent, and a single
place would wrongly force a choice between them.  ``a1 # a2`` with
``a1 = a2`` forces ``a # a``, which is why an activity in a length-one loop
(``a > a``) can never be in any pair.

Known limitations, reported by :func:`alpha_miner` as warnings:

* **length-one loops** (``a > a``) -- the activity ends up disconnected;
* **length-two loops** (``a b a``) -- ``a > b`` and ``b > a`` make them look
  parallel, so the loop is lost;
* non-free-choice constructs and duplicate or silent activities cannot be
  represented at all.

The result object keeps every intermediate set so the app can show the
derivation step by step -- exactly how exam questions ask for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..footprint import Footprint, footprint_of_log
from ..log import SimpleLog
from ..petrinet import Marking, PetriNet

Pair = tuple[frozenset[str], frozenset[str]]


@dataclass
class AlphaResult:
    footprint: Footprint
    T_L: list[str]
    T_I: list[str]
    T_O: list[str]
    X_L: list[Pair]
    Y_L: list[Pair]
    net: PetriNet
    warnings: list[str] = field(default_factory=list)

    def steps(self) -> list[tuple[str, str]]:
        """The derivation as (step, content) rows, in textbook notation."""
        def s(items) -> str:
            return "{" + ", ".join(sorted(items)) + "}"

        def pairs(collection: list[Pair]) -> str:
            if not collection:
                return "∅"
            return "{ " + ", ".join(f"({s(a)}, {s(b)})" for a, b in collection) + " }"

        places = ["i_L"] + [f"p({s(a)},{s(b)})" for a, b in self.Y_L] + ["o_L"]
        return [
            ("1. T_L — all activities", s(self.T_L)),
            ("2. T_I — start activities", s(self.T_I)),
            ("3. T_O — end activities", s(self.T_O)),
            ("4. X_L — candidate (A, B) pairs", pairs(self.X_L)),
            ("5. Y_L — maximal pairs", pairs(self.Y_L)),
            ("6. P_L — places", "{" + ", ".join(places) + "}"),
            ("7. F_L — arcs", f"{len(self.net.arcs)} arcs"),
            ("8. α(L) = (P_L, T_L, F_L)", self.net.summary()),
        ]


def _pair_key(pair: Pair) -> tuple:
    return (len(pair[0]) + len(pair[1]), sorted(pair[0]), sorted(pair[1]))


def alpha_miner(log: SimpleLog, name: str = "α(L)") -> AlphaResult:
    """Run the α-algorithm on a multiset of activity sequences."""
    fp = footprint_of_log(log)
    T_L = fp.activities
    T_I = sorted({trace[0] for trace in log if trace})
    T_O = sorted({trace[-1] for trace in log if trace})

    def independent(group: frozenset[str]) -> bool:
        """All members pairwise # -- including each with itself."""
        return all(fp.choice(x, y) for x in group for y in group)

    def causal(a_set: frozenset[str], b_set: frozenset[str]) -> bool:
        return all(fp.causal(a, b) for a in a_set for b in b_set)

    # Step 4.  Grow pairs from the singleton pairs ({a},{b}) with a → b.
    # Any (A, B) in X_L can be reached this way by adding one element at a
    # time, because every sub-pair of a valid pair is itself valid.  The
    # search is exponential in the worst case, but course-sized logs have at
    # most a few dozen activities, so this is instantaneous in practice.
    seeds = {(frozenset({a}), frozenset({b}))
             for a in T_L for b in T_L
             if fp.causal(a, b) and fp.choice(a, a) and fp.choice(b, b)}
    X_L: set[Pair] = set(seeds)
    frontier = list(seeds)
    while frontier:
        next_frontier = []
        for a_set, b_set in frontier:
            for extra in T_L:
                if extra not in a_set:
                    grown_a = a_set | {extra}
                    candidate = (grown_a, b_set)
                    if candidate not in X_L and independent(grown_a) and causal(grown_a, b_set):
                        X_L.add(candidate)
                        next_frontier.append(candidate)
                if extra not in b_set:
                    grown_b = b_set | {extra}
                    candidate = (a_set, grown_b)
                    if candidate not in X_L and independent(grown_b) and causal(a_set, grown_b):
                        X_L.add(candidate)
                        next_frontier.append(candidate)
        frontier = next_frontier

    # Step 5.  Keep only the maximal pairs.
    Y_L = [pair for pair in X_L
           if not any(pair != other and pair[0] <= other[0] and pair[1] <= other[1]
                      for other in X_L)]

    X_sorted = sorted(X_L, key=_pair_key)
    Y_sorted = sorted(Y_L, key=_pair_key)

    # Steps 6-8.  Build the net.
    net = PetriNet(name)
    transitions = {a: net.add_transition(a, id=f"t_{a}") for a in T_L}
    source = net.add_place("i_L", id="i_L")
    sink = net.add_place("o_L", id="o_L")
    for number, (a_set, b_set) in enumerate(Y_sorted, 1):
        label = "p({" + ",".join(sorted(a_set)) + "},{" + ",".join(sorted(b_set)) + "})"
        place = net.add_place(label, id=f"p{number}")
        for a in sorted(a_set):                  # sorted: the same net, arc for arc, in every session
            net.add_arc(transitions[a], place)
        for b in sorted(b_set):
            net.add_arc(place, transitions[b])
    for a in T_I:
        net.add_arc(source, transitions[a])
    for a in T_O:
        net.add_arc(transitions[a], sink)
    net.initial_marking = Marking({source.id: 1})
    net.final_marking = Marking({sink.id: 1})
    net.info["algorithm"] = "α-algorithm"

    warnings = []
    loops1 = [a for a in T_L if (a, a) in fp.directly_follows]
    if loops1:
        warnings.append("Length-one loops detected (" + ", ".join(loops1) + "). The "
                        "α-algorithm cannot discover these; the activities end up "
                        "without input/output places.")
    loops2 = sorted({tuple(sorted((trace[i], trace[i + 1])))
                     for trace in log for i in range(len(trace) - 2)
                     if trace[i] == trace[i + 2] and trace[i] != trace[i + 1]})
    if loops2:
        warnings.append("Length-two loops detected (" + ", ".join(f"{a}–{b}" for a, b in loops2)
                        + "). The α-algorithm treats these pairs as parallel.")
    return AlphaResult(fp, T_L, T_I, T_O, X_sorted, Y_sorted, net, warnings)
