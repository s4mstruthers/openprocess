"""Alignments and token replay, variant by variant, on several processes (exactly).

Aligning or replaying a log is the same computation once per variant, each
depending only on the model and that variant's trace: each A* search has its
own tie-breaking counter, and each replay finds the same shortest silent
paths whatever was searched before.  So :func:`openprocess.parallel.map_items`
can spread the variants over worker processes and give back exactly what
aligning them one after the other gives; the tests compare the two field by
field.  This module says which work that is (an item function and a setup
per kind), and when a log is large enough for processes to pay for
themselves (:func:`worth_it`).
"""

from __future__ import annotations

from typing import Callable

from .. import parallel as _core

#: When the work is worth processes: (variants, events over the distinct variants), per kind.
#: An alignment is an A* search per variant; a replay is a walk along it, so it needs far more.
THRESHOLDS = {"align": (16, 400), "replay": (2_000, 60_000)}


def workers() -> int:
    return _core.workers()


def worth_it(variants: list[tuple[tuple[str, ...], int]], count: int | None = None, kind: str = "align") -> bool:
    """Is the log large enough to spread over processes?"""
    count = _core.workers() if count is None else count
    least_variants, least_events = THRESHOLDS[kind]
    return count > 1 and len(variants) >= least_variants and sum(len(t) for t, _ in variants) >= least_events


# -- the work, by name (a worker finds these functions by their module and name) -------------------
def setup_align(shared):
    """Once per worker: the net compiled for the search."""
    from .conformance.alignments import _CompiledNet
    net, max_states = shared
    return net, _CompiledNet(net), max_states


def align_one(context, variant):
    from .conformance.alignments import align_trace
    net, compiled, max_states = context
    trace, count = variant
    return align_trace(net, trace, count, max_states, compiled)


def setup_replay(net):
    """Once per worker: the silent-step search and its cache of paths."""
    from .conformance.token_replay import _SilentSearch
    return net, _SilentSearch(net), {}


def replay_one(context, variant):
    from .conformance.token_replay import replay_trace
    net, searcher, paths = context
    trace, count = variant
    return replay_trace(net, trace, count, paths, searcher)


def map_variants(kind: str, net, variants: list[tuple[tuple[str, ...], int]], options: dict | None = None,
                 progress: Callable[[int, int], None] | None = None, count: int | None = None) -> list:
    """``kind`` (``"align"`` or ``"replay"``) for every variant on worker
    processes; the results in the order of ``variants``."""
    if kind == "align":
        from .conformance.alignments import MAX_STATES
        max_states = (options or {}).get("max_states") or MAX_STATES
        function, setup, shared = align_one, setup_align, (net, max_states)
    elif kind == "replay":
        function, setup, shared = replay_one, setup_replay, net
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return _core.map_items(function, variants, shared=shared, setup=setup, workers=count, progress=progress,
                           cost=lambda variant: len(variant[0]))


__all__ = ["THRESHOLDS", "align_one", "map_variants", "replay_one", "setup_align", "setup_replay", "workers",
           "worth_it"]
