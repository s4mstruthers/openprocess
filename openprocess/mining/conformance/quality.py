"""Precision, generalisation and simplicity -- the other three quality dimensions.

Fitness (token replay, alignments) asks "can the model replay the log?".
Three more dimensions complete the picture (course: the four quality
criteria):

precision
    Does the model allow *only* what the log shows?  A flower model fits
    everything but has terrible precision.
generalisation
    Does the model generalise beyond the examples, rather than overfitting
    exactly the traces seen?
simplicity
    Is the model as simple as possible (Occam's razor)?

Precision: escaping edges (ETConformance, Muñoz-Gama & Carmona)
---------------------------------------------------------------
Walk through every *prefix* of every trace.  After prefix ``h``, the log
continued with some set of activities ``log_next(h)``; the model, in the
marking reached by replaying ``h``, would *allow* a set ``model_next(h)``.
Anything the model allows but the log never did is an **escaping edge**::

                     Σ_h  freq(h) · |model_next(h) \\ log_next(h)|
    precision = 1 −  ─────────────────────────────────────────────
                     Σ_h  freq(h) · |model_next(h)|

Prefixes that do not fit the model are skipped (there is no meaningful
marking to compare against).  ``model_next`` looks through τ transitions.

Generalisation (token-based, as in PM4Py)
-----------------------------------------
A transition that fired only a few times during replay gives little evidence
that the model is right about it.  With ``n_t`` the number of firings of
transition t::

    generalisation = 1 − mean_t ( 1 / sqrt(n_t) )

Simplicity (arc degree, as in PM4Py)
------------------------------------
The mean number of arcs per node; a pure sequence has degree 2.  Anything
above that makes the model harder to read::

    simplicity = 1 / (1 + max(mean degree − 2, 0))

These last two are rough proxies -- which is also what the literature says
about them -- but they make the trade-off between the four dimensions
visible when comparing models discovered from the same log.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque

from ..log import SimpleLog
from ..petrinet import Marking, PetriNet
from .token_replay import ReplayResult, silent_path


def _visible_enabled(net: PetriNet, marking: Marking, max_states: int = 2000) -> set[str]:
    """Labels that can fire next, possibly after τ moves."""
    result: set[str] = set()
    seen = {marking}
    queue = deque([marking])
    while queue and len(seen) < max_states:
        current = queue.popleft()
        for transition in net.enabled(current):
            label = net.transitions[transition].label
            if label is not None:
                result.add(label)
            else:
                successor = net.fire(current, transition)
                if successor not in seen:
                    seen.add(successor)
                    queue.append(successor)
    return result


def _step(net: PetriNet, before: Marking | None, activity: str, paths: dict) -> Marking | None:
    """The marking after ``activity`` from ``before`` without missing tokens
    (after τ moves if they enable it), or None.  ``paths`` remembers the
    silent paths searched, by marking and transition."""
    if before is None:
        return None
    candidates = net.transitions_with_label(activity)
    for transition in candidates:
        if net.is_enabled(before, transition):
            return net.fire(before, transition)
    for transition in candidates:
        key = (before, transition)
        if key not in paths:
            paths[key] = silent_path(net, before, lambda m, t=transition: net.is_enabled(m, t))
        path = paths[key]
        if path is not None:
            marking = before
            for step in path:
                marking = net.fire(marking, step)
            return net.fire(marking, transition)
    return None


def precision(net: PetriNet, log: SimpleLog) -> float:
    """ETConformance precision with escaping edges.

    The log's prefixes are walked as a tree (a prefix is a node, the next
    activity an edge), so each distinct prefix is replayed once, from its
    parent's marking, and a prefix is never stored as a tuple of its own: a
    log with long traces and thousands of variants (BPI 2019 has a trace of
    990 events) costs memory in proportion to its events, not their square."""
    children: dict[tuple[int, str], int] = {}
    observed: list[set[str]] = [set()]          # per prefix: the activities seen next
    frequency: list[int] = [0]                  # per prefix: how often an activity followed it
    markings: list[Marking | None] = [net.initial_marking]
    paths: dict = {}
    for trace, count in log.items():
        node = 0
        for activity in trace:
            observed[node].add(activity)
            frequency[node] += count
            child = children.get((node, activity))
            if child is None:
                child = children[(node, activity)] = len(observed)
                observed.append(set())
                frequency.append(0)
                markings.append(_step(net, markings[node], activity, paths))
            node = child

    enabled_at: dict[Marking, set[str]] = {}
    escaping = allowed = 0
    for node, seen in enumerate(observed):
        marking = markings[node]
        if not seen or marking is None:
            continue
        enabled = enabled_at.get(marking)
        if enabled is None:
            enabled = enabled_at[marking] = _visible_enabled(net, marking)
        allowed += frequency[node] * len(enabled)
        escaping += frequency[node] * len(enabled - seen)
    return 1.0 if allowed == 0 else 1 - escaping / allowed


def generalisation(replay: ReplayResult) -> float:
    fired, _ = replay.transition_totals()
    net = replay.net
    if not net.transitions:
        return 1.0
    terms = [1 / math.sqrt(fired[t]) if fired[t] else 1.0 for t in net.transitions]
    return 1 - sum(terms) / len(terms)


def simplicity(net: PetriNet) -> float:
    nodes = len(net.places) + len(net.transitions)
    if nodes == 0:
        return 1.0
    mean_degree = 2 * len(net.arcs) / nodes
    return 1 / (1 + max(mean_degree - 2, 0))
