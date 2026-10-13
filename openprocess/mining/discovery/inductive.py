"""The Inductive Miner (IM) and Inductive Miner – infrequent (IMf).

Reading: Leemans, Fahland & van der Aalst, "Discovering block-structured
process models from event logs" (course reading 4); the strict sequence cut
is from Leemans, "Robust Process Mining with Guarantees" (PhD thesis, 2017).

The idea: divide and conquer
----------------------------
Look at the directly-follows graph of the log and try to find a **cut**: a
partition of the activities into groups ``Σ1, ..., Σn`` that matches one of
the four process-tree operators.  If one is found, **split** the log into one
sub-log per group, recurse on each, and combine the results under that
operator.  Recursion stops at **base cases** (a log with one activity, or
only empty traces).  When no cut exists, a **fall-through** produces a
general but still correct model.

The four cuts, in the order they are tried
------------------------------------------
× exclusive choice
    The groups are the *connected components* of the DFG (ignoring edge
    direction).  No trace mixes two groups.
→ sequence
    Every activity of an earlier group can reach every activity of a later
    group, and never the other way round.  Computed from the strongly
    connected components, merging components that are mutually unreachable.
    Then the *strict* step (Leemans 2017, p. 233): groups that can only be
    skipped together are merged, so an optional stretch ``a, c`` becomes
    ``×(→(a,c), τ)`` rather than ``×(a,τ), ×(c,τ)``.  ProM and PM4Py do the
    same, and ``tests/cross_check_inductive_miner.py`` checks that the trees
    agree.
∧ parallel
    Between two different groups, *every* pair of activities directly
    follows each other in both directions, and every group contains a start
    and an end activity.  Computed as the connected components of the
    *negated* DFG (an edge where the pair is **not** in both directions).
↺ loop
    The *body* group contains all start and end activities; each *redo*
    group is entered only from end activities and exits only to start
    activities.

Guarantees (IM): the result always *fits* the log perfectly and is always
*sound*.  The price is that fall-throughs may overgeneralise.

IMf: handling infrequent behaviour
----------------------------------
IM treats one odd trace the same as a thousand normal ones.  IMf adds a
**noise threshold** ``f`` in [0, 1]: when no cut is found on the full DFG,
edges whose frequency is below ``f`` × (the most frequent outgoing edge of
the same activity) are removed and cut detection is retried.  The log split
is then made *filtering-aware*: events that do not fit the chosen cut are
dropped.  Fitness is no longer guaranteed to be 1, but the models are much
simpler.  ``f = 0`` gives plain IM.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from ..dfg import DFG, dfg_from_simple_log
from ..log import SimpleLog
from ..petrinet import PetriNet
from ..processtree import Operator, ProcessTree, to_petri_net


@dataclass
class Step:
    """One recursion step, structured so the app can typeset it.

    ``kind`` is ``"cut"``, ``"base"``, ``"fall"`` (fall-through) or
    ``"filter"`` (IMf noise handling).  ``operator`` is the process-tree
    symbol produced (→ × ∧ ↺), ``groups`` the partition found by a cut, and
    ``result`` the leaf of a base case.
    """

    depth: int
    kind: str
    text: str
    operator: str | None = None
    groups: list[list[str]] | None = None
    result: str | None = None
    detail: str | None = None
    filtered: bool = False


@dataclass
class InductiveResult:
    tree: ProcessTree
    net: PetriNet
    #: One line per recursion step: which cut or fall-through was applied.
    trace_of_steps: list[str] = field(default_factory=list)
    #: The same steps, structured (see :class:`Step`).
    steps: list[Step] = field(default_factory=list)


def inductive_miner(log: SimpleLog, noise_threshold: float = 0.0,
                    name: str | None = None) -> InductiveResult:
    """Discover a process tree (and its Petri net) with IM / IMf."""
    miner = _Miner(noise_threshold)
    tree = miner.mine(Counter({t: n for t, n in log.items() if n > 0}), depth=0).simplified()
    label = name or ("IMf" if noise_threshold > 0 else "IM")
    net = to_petri_net(tree, label)
    net.info["algorithm"] = (f"Inductive Miner – infrequent (f = {noise_threshold:g})"
                             if noise_threshold > 0 else "Inductive Miner")
    net.info["process tree"] = str(tree)
    return InductiveResult(tree, net, miner.steps, miner.records)


# ---------------------------------------------------------------------------
# The recursion
# ---------------------------------------------------------------------------
class _Miner:
    def __init__(self, noise_threshold: float) -> None:
        self.f = noise_threshold
        self.steps: list[str] = []
        self.records: list[Step] = []

    def note(self, depth: int, text: str, kind: str = "fall", **extra) -> None:
        self.steps.append("  " * depth + text)
        self.records.append(Step(depth, kind, text, **extra))

    def mine(self, log: SimpleLog, depth: int) -> ProcessTree:
        activities = sorted({a for trace in log for a in trace})
        total = sum(log.values())
        empty = log.get((), 0)

        # ---- base cases --------------------------------------------------
        if total == 0 or not activities:
            self.note(depth, "base case: only empty traces → τ", "base", result="τ")
            return ProcessTree.tau()
        if len(activities) == 1 and empty == 0 and all(len(t) == 1 for t in log):
            self.note(depth, f"base case: single activity → {activities[0]}", "base",
                      result=activities[0])
            return ProcessTree.leaf(activities[0])

        # ---- empty traces --------------------------------------------------
        if empty:
            if self.f > 0 and empty / total < self.f:
                self.note(depth, f"IMf: ignoring {empty} infrequent empty trace(s)", "filter",
                          detail=f"{empty} of {total} traces are empty, below f = {self.f:g}")
                log = Counter({t: n for t, n in log.items() if t})
                return self.mine(log, depth)
            self.note(depth, f"fall-through: empty traces → ×(τ, …)  [{empty} empty]",
                      operator="×", detail=f"{empty} empty trace(s): the part may be skipped")
            rest = Counter({t: n for t, n in log.items() if t})
            return ProcessTree.node(Operator.XOR, [ProcessTree.tau(), self.mine(rest, depth + 1)])

        # ---- cuts on the full DFG ---------------------------------------------
        dfg = dfg_from_simple_log(log)
        found = self.find_cut(dfg)
        filtered = False
        if found is None and self.f > 0:
            found = self.find_cut(filter_dfg(dfg, self.f))
            filtered = found is not None
        if found is not None:
            operator, groups = found
            described = " | ".join("{" + ", ".join(sorted(g)) + "}" for g in groups)
            self.note(depth, f"{operator.value} cut{' (on filtered DFG)' if filtered else ''}: "
                             f"{described}", "cut", operator=operator.value,
                      groups=[sorted(g) for g in groups], filtered=filtered)
            if operator is Operator.SEQUENCE:
                maximal = maximal_sequence_cut(dfg) or []
                if len(maximal) != len(groups):
                    merged = [sorted(g) for g in groups
                              if not any(set(g) == m for m in maximal)]
                    self.records[-1].detail = (
                        "strict sequence cut: groups that can only be skipped together are "
                        "one group: " + " ".join("{" + ", ".join(g) + "}" for g in merged))
                    self.steps.append("  " * (depth + 1) + self.records[-1].detail)
            sublogs = split_log(log, operator, groups, dfg)
            children = [self.mine(sub, depth + 1) for sub in sublogs]
            if operator is Operator.LOOP and len(children) > 2:
                # ↺(body, r1, r2, …) is kept as a loop with several redos.
                return ProcessTree.node(Operator.LOOP, children)
            return ProcessTree.node(operator, children)

        # ---- fall-throughs ---------------------------------------------------
        return self.fall_through(log, dfg, activities, depth)

    # -------------------------------------------------------------------------
    def find_cut(self, dfg: DFG):
        for finder, operator in ((xor_cut, Operator.XOR), (sequence_cut, Operator.SEQUENCE),
                                 (parallel_cut, Operator.PARALLEL), (loop_cut, Operator.LOOP)):
            groups = finder(dfg)
            if groups is not None and len(groups) > 1:
                return operator, groups
        return None

    def fall_through(self, log: SimpleLog, dfg: DFG, activities: list[str],
                     depth: int) -> ProcessTree:
        # 1. Activity once per trace: it can be put in parallel with the rest.
        for activity in activities:
            if all(trace.count(activity) == 1 for trace in log):
                self.note(depth, f"fall-through: '{activity}' occurs once per trace → ∧({activity}, …)",
                          operator="∧", detail=f"{activity} occurs exactly once in every trace")
                rest = _project_out(log, activity)
                return ProcessTree.node(Operator.PARALLEL,
                                        [ProcessTree.leaf(activity), self.mine(rest, depth + 1)])

        # 2. Activity concurrent: removing one activity makes a cut appear.
        if len(activities) > 2:
            for activity in activities:
                rest = _project_out(log, activity)
                if self.find_cut(dfg_from_simple_log(rest)) is not None:
                    self.note(depth, f"fall-through: activity concurrent '{activity}' → ∧({activity}, …)",
                              operator="∧", detail=f"without {activity} a cut exists")
                    return ProcessTree.node(Operator.PARALLEL, [
                        self.mine(_keep_only(log, {activity}), depth + 1),
                        self.mine(rest, depth + 1)])

        # 3. Strict τ-loop: split traces where an end activity is followed by
        #    a start activity.
        split = _split_on(log, lambda a, b: a in dfg.end and b in dfg.start)
        if sum(split.values()) > sum(log.values()):
            self.note(depth, "fall-through: strict τ-loop → ↺(…, τ)", operator="↺",
                      detail="split traces where an end activity is followed by a start activity")
            return ProcessTree.node(Operator.LOOP, [self.mine(split, depth + 1), ProcessTree.tau()])

        # 4. τ-loop: split traces before every start activity.
        split = _split_on(log, lambda a, b: b in dfg.start)
        if sum(split.values()) > sum(log.values()):
            self.note(depth, "fall-through: τ-loop → ↺(…, τ)", operator="↺",
                      detail="split traces before every start activity")
            return ProcessTree.node(Operator.LOOP, [self.mine(split, depth + 1), ProcessTree.tau()])

        # 5. Flower model: anything goes, in any order, any number of times.
        self.note(depth, "fall-through: flower model ↺(τ, ×(" + ", ".join(activities) + "))",
                  operator="↺", detail="nothing else applies: allow any order (flower model)")
        return ProcessTree.node(Operator.LOOP, [
            ProcessTree.tau(),
            ProcessTree.node(Operator.XOR, [ProcessTree.leaf(a) for a in activities])
            if len(activities) > 1 else ProcessTree.leaf(activities[0])])


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------
def _undirected_components(nodes: list[str], connected) -> list[set[str]]:
    """Connected components where ``connected(a, b)`` says whether a–b touch."""
    remaining = set(nodes)
    components = []
    while remaining:
        seed = min(remaining)
        component = {seed}
        queue = deque([seed])
        remaining.discard(seed)
        while queue:
            node = queue.popleft()
            for other in list(remaining):
                if connected(node, other):
                    remaining.discard(other)
                    component.add(other)
                    queue.append(other)
        components.append(component)
    return components


def _reachability(dfg: DFG) -> dict[str, set[str]]:
    """For every activity, the activities reachable from it via ≥ 1 edge."""
    succ = {a: set() for a in dfg.activities}
    for a, b in dfg.edges:
        succ[a].add(b)
    reach = {}
    for start in dfg.activities:
        seen: set[str] = set()
        queue = deque(succ[start])
        while queue:
            node = queue.popleft()
            if node not in seen:
                seen.add(node)
                queue.extend(succ[node])
        reach[start] = seen
    return reach


# ---------------------------------------------------------------------------
# Cut detection
# ---------------------------------------------------------------------------
def xor_cut(dfg: DFG) -> list[set[str]] | None:
    nodes = sorted(dfg.activities)
    edges = set(dfg.edges)
    groups = _undirected_components(nodes, lambda a, b: (a, b) in edges or (b, a) in edges)
    return groups if len(groups) > 1 else None


def sequence_cut(dfg: DFG) -> list[set[str]] | None:
    """The *strict* sequence cut: the maximal sequence cut, then the groups
    that can only be skipped together merged into one (Leemans 2017, p. 233).

    The maximal cut puts every activity in the finest possible position of
    the sequence.  That is correct but can be needlessly general: in
    ``[<b>^4, <b,b,a,c>]`` the maximal cut is ``{b} | {a} | {c}``, and since
    ``a`` and ``c`` are absent from four of the five traces, each becomes
    optional on its own, ``→(↺(b,τ), ×(a,τ), ×(c,τ))``, a model that also
    allows ``<b,a>`` and ``<b,c>``, which never happened.  ``c`` can only be
    reached through ``a``, so whenever ``a`` is skipped ``c`` is skipped too:
    merging them gives ``{b} | {a, c}`` and ``→(↺(b,τ), ×(→(a,c),τ))``.
    """
    groups = maximal_sequence_cut(dfg)
    return None if groups is None else merge_skippable_groups(dfg, groups)


def maximal_sequence_cut(dfg: DFG) -> list[set[str]] | None:
    """The sequence cut with as many groups as the reachability relation
    allows, ordered first to last.

    The definition (Leemans, Fahland & van der Aalst 2013; Leemans 2017,
    p. 188): a partition ``Σ1, …, Σn`` such that for every earlier group
    ``Σi`` and later group ``Σj``, *every* ``a ∈ Σi`` reaches *every*
    ``b ∈ Σj`` through the directly-follows graph, and no ``b`` reaches
    any ``a``.  Two activities therefore belong to the same group exactly
    when they reach each other (a loop) or when neither reaches the other
    (two branches of a choice, or two concurrent activities): one of them
    could not be put strictly before the other.

    Computed as the thesis does it: start with one group per activity and
    merge any two groups that hold such a pair, until nothing merges.  The
    groups are then totally ordered by reachability.
    """
    reach = _reachability(dfg)
    groups: list[set[str]] = [{a} for a in sorted(dfg.activities)]
    if not groups:
        return None

    def must_share_a_group(g1: set[str], g2: set[str]) -> bool:
        for a in g1:
            for b in g2:
                forward, backward = b in reach[a], a in reach[b]
                if forward == backward:          # both ways (a loop) or neither
                    return True
        return False

    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if must_share_a_group(groups[i], groups[j]):
                    groups[i] |= groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    if len(groups) < 2:
        return None

    # After the fixpoint, for any two groups every pair reaches in exactly one
    # direction, so "how many groups does this one reach" orders them.
    def reaches(g1: set[str], g2: set[str]) -> bool:
        return any(b in reach[a] for a in g1 for b in g2)

    score = [sum(1 for other in groups if other is not g and reaches(g, other)) for g in groups]
    groups = [g for _, g in sorted(zip(score, groups), key=lambda item: -item[0])]
    # Validate the definition literally: every pair, forward only.
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            for a in groups[i]:
                for b in groups[j]:
                    if b not in reach[a] or a in reach[b]:
                        return None
    return groups


def merge_skippable_groups(dfg: DFG, groups: list[set[str]]) -> list[set[str]]:
    """Merge the groups of a sequence cut that can only be skipped together.

    Follows the function on page 233 of S.J.J. Leemans, *Robust Process
    Mining with Guarantees* (PhD thesis, 2017), which is also what ProM's and
    PM4Py's Inductive Miner do, so the trees agree.

    A group ``p`` is *skippable* when some trace gets past it without
    visiting it: an edge from a group before ``p`` to one after it, a start
    activity after ``p``, or an end activity before ``p``.  A skippable
    group then absorbs

    * the groups just before it whose edges never go past ``p`` (they can
      only leave the sequence through ``p`` or earlier, so skipping ``p``
      means skipping them), and
    * the groups just after it that are only ever entered from ``p`` or
      later (they can only be reached through ``p``, so skipping ``p``
      skips them as well).

    The merged group takes ``p``'s position, so the sequence order is kept.
    Groups that do not merge are returned as they were.
    """
    n = len(groups)
    index = {a: i for i, g in enumerate(groups) for a in g}
    # comes_from[i]: the earliest group an edge into group i comes from; before
    # everything (-1) when the group holds a start activity.  goes_to[i]: the
    # latest group an edge out of group i goes to; past everything (n) when
    # the group holds an end activity.
    comes_from = [-1 if any(a in dfg.start for a in g) else n for g in groups]
    goes_to = [n if any(a in dfg.end for a in g) else -1 for g in groups]
    for a, b in dfg.edges:
        comes_from[index[b]] = min(comes_from[index[b]], index[a])
        goes_to[index[a]] = max(goes_to[index[a]], index[b])

    merged = [set(g) for g in groups]
    where = dict(index)                      # activity -> its group's current position

    def skippable(p: int) -> bool:
        if any(where[a] < p < where[b] for a, b in dfg.edges):
            return True
        return any(where[a] > p for a in dfg.start) or any(where[a] < p for a in dfg.end)

    def absorb(p: int, q: int) -> None:
        merged[p] |= merged[q]
        for a in merged[q]:
            where[a] = p
        merged[q] = set()

    for p in range(n):
        if not merged[p] or not skippable(p):
            continue
        q = p - 1
        while q >= 0 and goes_to[q] <= p:
            absorb(p, q)
            q -= 1
        q = p + 1
        while q < n and comes_from[q] >= p:
            absorb(p, q)
            q += 1
    return [g for g in merged if g]


def parallel_cut(dfg: DFG) -> list[set[str]] | None:
    nodes = sorted(dfg.activities)
    edges = set(dfg.edges)
    # Negated graph: connect a, b unless they directly follow in both directions.
    groups = _undirected_components(
        nodes, lambda a, b: not ((a, b) in edges and (b, a) in edges))
    if len(groups) < 2:
        return None
    # Every group needs a start and an end activity; merge groups that lack one.
    good = [g for g in groups if g & set(dfg.start) and g & set(dfg.end)]
    bad = [g for g in groups if not (g & set(dfg.start) and g & set(dfg.end))]
    if not good:
        return None
    for group in bad:
        good[0] |= group
    if len(good) < 2:
        return None
    # Re-check the defining property after merging.
    for i, g1 in enumerate(good):
        for g2 in good[i + 1:]:
            if not all((a, b) in edges and (b, a) in edges for a in g1 for b in g2):
                return None
    return good


def loop_cut(dfg: DFG) -> list[set[str]] | None:
    starts, ends = set(dfg.start), set(dfg.end)
    body = starts | ends
    others = sorted(set(dfg.activities) - body)
    if not others:
        return None
    edges = set(dfg.edges)
    # Components of the graph *without* the start/end activities.
    redo_candidates = _undirected_components(
        others, lambda a, b: (a, b) in edges or (b, a) in edges)

    body = set(body)
    redos: list[set[str]] = []
    for group in redo_candidates:
        belongs_to_body = False
        for a in group:
            # Entered from a start activity (other than via an end) → body.
            if any((s, a) in edges for s in starts - ends):
                belongs_to_body = True
            # Leaves to an end activity (other than a start) → body.
            if any((a, e) in edges for e in ends - starts):
                belongs_to_body = True
            # If some end activity leads to a, every end activity must.
            if any((e, a) in edges for e in ends) and not all((e, a) in edges for e in ends):
                belongs_to_body = True
            # If a leads to some start activity, it must lead to all of them.
            if any((a, s) in edges for s in starts) and not all((a, s) in edges for s in starts):
                belongs_to_body = True
        if belongs_to_body:
            body |= group
        else:
            redos.append(group)
    # Activities inside the body that are fed only by body nodes are fine;
    # redo groups must actually connect end → redo → start.
    redos = [g for g in redos
             if any((e, a) in edges for e in ends for a in g)
             and any((a, s) in edges for a in g for s in starts)]
    leftover = set(dfg.activities) - body - set().union(*redos) if redos else set()
    body |= leftover
    if not redos:
        return None
    return [body] + redos


def filter_dfg(dfg: DFG, threshold: float) -> DFG:
    """IMf filtering: drop edges below ``threshold`` × strongest outgoing edge."""
    strongest: dict[str, int] = Counter()
    for (a, _), n in dfg.edges.items():
        strongest[a] = max(strongest[a], n)
    for a, n in dfg.end.items():
        strongest[a] = max(strongest[a], n)
    result = DFG(activities=Counter(dfg.activities), trace_count=dfg.trace_count)
    result.edges = Counter({(a, b): n for (a, b), n in dfg.edges.items()
                            if n >= threshold * strongest[a]})
    max_start = max(dfg.start.values(), default=0)
    max_end = max(dfg.end.values(), default=0)
    result.start = Counter({a: n for a, n in dfg.start.items() if n >= threshold * max_start})
    result.end = Counter({a: n for a, n in dfg.end.items() if n >= threshold * strongest[a]
                          or n >= threshold * max_end})
    return result


# ---------------------------------------------------------------------------
# Log splitting
# ---------------------------------------------------------------------------
def _project_out(log: SimpleLog, activity: str) -> SimpleLog:
    result: SimpleLog = Counter()
    for trace, n in log.items():
        result[tuple(a for a in trace if a != activity)] += n
    return result


def _keep_only(log: SimpleLog, keep: set[str]) -> SimpleLog:
    result: SimpleLog = Counter()
    for trace, n in log.items():
        result[tuple(a for a in trace if a in keep)] += n
    return result


def _split_on(log: SimpleLog, boundary) -> SimpleLog:
    """Cut every trace between consecutive a, b where ``boundary(a, b)``."""
    result: SimpleLog = Counter()
    for trace, n in log.items():
        piece: list[str] = []
        for index, activity in enumerate(trace):
            if piece and boundary(trace[index - 1], activity):
                result[tuple(piece)] += n
                piece = []
            piece.append(activity)
        result[tuple(piece)] += n
    return result


def split_log(log: SimpleLog, operator: Operator, groups: list[set[str]],
              dfg: DFG) -> list[SimpleLog]:
    """Divide the log over the groups of a cut."""
    sublogs: list[SimpleLog] = [Counter() for _ in groups]
    if operator is Operator.XOR:
        # Each trace goes to the group it overlaps most with; with IM every
        # trace lies entirely in one group, with IMf stray events are dropped.
        for trace, n in log.items():
            best = max(range(len(groups)), key=lambda i: sum(a in groups[i] for a in trace))
            sublogs[best][tuple(a for a in trace if a in groups[best])] += n
    elif operator in (Operator.SEQUENCE, Operator.PARALLEL):
        # Projection on each group.  For a sequence cut found on the full DFG
        # this is exactly the split into consecutive segments.
        for trace, n in log.items():
            for i, group in enumerate(groups):
                sublogs[i][tuple(a for a in trace if a in group)] += n
    elif operator is Operator.LOOP:
        # Cut every trace into alternating body / redo segments.  A body
        # segment always comes first and last; if a trace starts or ends in
        # a redo group (only possible with noise), an empty body segment is
        # implied, which becomes an empty trace in the body log.
        group_of = {a: i for i, g in enumerate(groups) for a in g}
        for trace, n in log.items():
            # 1. Chop the trace into maximal runs belonging to one group.
            runs: list[tuple[int, list[str]]] = []
            for activity in trace:
                g = group_of.get(activity, 0)
                if runs and runs[-1][0] == g:
                    runs[-1][1].append(activity)
                else:
                    runs.append((g, [activity]))
            # 2. Enforce body, redo, body, redo, ..., body.
            normalised: list[tuple[int, list[str]]] = []
            for g, run in runs:
                previous_is_redo = normalised and normalised[-1][0] != 0
                if g != 0 and (not normalised or previous_is_redo):
                    normalised.append((0, []))
                normalised.append((g, run))
            if not normalised or normalised[-1][0] != 0:
                normalised.append((0, []))
            for g, run in normalised:
                sublogs[g][tuple(run)] += n
    return [Counter({t: c for t, c in sub.items() if c}) for sub in sublogs]
