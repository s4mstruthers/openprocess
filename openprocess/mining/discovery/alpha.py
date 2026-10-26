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

#: X_L is listed in full (for the eight steps and the exercises) up to this many pairs.
X_L_LIMIT = 20_000


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
    #: False when X_L had too many pairs to list (a wide choice): then
    #: ``X_L`` holds only the maximal pairs, which are what the net is built from.
    X_L_listed: bool = True

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
            ("4. X_L — candidate (A, B) pairs", pairs(self.X_L) if self.X_L_listed else
             f"more than {X_L_LIMIT:,} pairs (every sub-pair of a pair in Y_L is one): not listed"),
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
    warnings: list[str] = []
    T_L = fp.activities
    T_I = sorted({trace[0] for trace in log if trace})
    T_O = sorted({trace[-1] for trace in log if trace})

    def independent(group: frozenset[str]) -> bool:
        """All members pairwise # -- including each with itself."""
        return all(fp.choice(x, y) for x in group for y in group)

    def causal(a_set: frozenset[str], b_set: frozenset[str]) -> bool:
        return all(fp.causal(a, b) for a in a_set for b in b_set)

    # Steps 4 and 5.  A pair (A, B) is in X_L when A and B are each
    # independent (pairwise #, each member with itself) and every a → b;
    # Y_L keeps the maximal ones.  Written as one graph -- an A-copy and a
    # B-copy of every activity without a self-loop, a # b joining two
    # members of the same side, a → b joining the sides -- a pair of X_L is
    # a clique with members on both sides and a pair of Y_L a *maximal*
    # clique.  Bron–Kerbosch with pivoting lists the maximal cliques without
    # passing through the sub-pairs, of which a wide choice a → {b1, …, bk}
    # has 2^k: the net needs only Y_L.  X_L is then listed from Y_L (every
    # non-empty sub-pair of a maximal pair) while that stays small.
    candidates = [a for a in T_L if fp.choice(a, a)]
    nodes = [("A", a) for a in candidates] + [("B", b) for b in candidates]

    def adjacent(u: tuple[str, str], v: tuple[str, str]) -> bool:
        (side_u, a), (side_v, b) = u, v
        if side_u == side_v:
            return a != b and fp.choice(a, b)
        return fp.causal(a, b) if side_u == "A" else fp.causal(b, a)

    neighbours = {u: frozenset(v for v in nodes if v != u and adjacent(u, v)) for u in nodes}
    maximal: list[frozenset] = []

    def bron_kerbosch(r: frozenset, p: set, x: set) -> None:
        if not p and not x:
            if any(s == "A" for s, _ in r) and any(s == "B" for s, _ in r):
                maximal.append(r)
            return
        pivot = max(p | x, key=lambda u: len(neighbours[u] & p))
        for v in list(p - neighbours[pivot]):
            bron_kerbosch(r | {v}, p & neighbours[v], x & neighbours[v])
            p = p - {v}
            x = x | {v}

    if nodes:
        bron_kerbosch(frozenset(), set(nodes), set())
    Y_L: list[Pair] = [(frozenset(a for s, a in r if s == "A"), frozenset(b for s, b in r if s == "B"))
                       for r in maximal]

    X_L: set[Pair] = set()
    listed = sum((2 ** len(a) - 1) * (2 ** len(b) - 1) for a, b in Y_L) <= X_L_LIMIT
    if listed:
        from itertools import combinations
        for a_set, b_set in Y_L:
            a_subsets = [frozenset(c) for n in range(1, len(a_set) + 1) for c in combinations(sorted(a_set), n)]
            b_subsets = [frozenset(c) for n in range(1, len(b_set) + 1) for c in combinations(sorted(b_set), n)]
            X_L.update((a, b) for a in a_subsets for b in b_subsets)
    else:
        X_L = set(Y_L)
        warnings.append(f"X_L has more than {X_L_LIMIT:,} pairs (a wide choice): only the maximal pairs, "
                        "Y_L, are listed; they are all the net needs.")

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
    return AlphaResult(fp, T_L, T_I, T_O, X_sorted, Y_sorted, net, warnings, listed)
