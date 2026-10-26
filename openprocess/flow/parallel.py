"""Parallel work in a box: ``flow.parallel_map``.

A box whose work is the same computation over many independent items (the
variants of a log, the cases, the settings of a sweep inside one box, the
resamples of a bootstrap) can spread them over the machine's cores::

    from openprocess import flow
    from openprocess.flow import box, EventLog, PetriNet, Table


    def score(model: PetriNet, variant) -> float:          # top level: workers find it by name
        trace, count = variant
        ...
        flow.note(f"{trace}: ...")                          # still reaches the How tab
        return value


    @box(group="Check")
    def my_check(model: PetriNet, log: EventLog) -> Table:
        variants = sorted(log.simple_log().items())
        values = flow.parallel_map(score, variants, shared=model)
        return Table("Scores", ["variant", "score"], [[v, s] for (v, _n), s in zip(variants, values)])

What it promises: the result is ``[score(model, v) for v in variants]``, the
same values in the same order, whatever the number of workers -- so the
workflow's record and ``openprocess run --check`` see the same numbers -- and
the notes, steps and values the item function reports appear on the box's
*How* tab in item order, as if the loop had run here.  What it asks of the
function (``docs/workflows.md``, *Parallel work*): define it at the top level
of a module (a folder's ``boxes/`` file is fine); compute each item from the
item and ``shared`` alone; for randomness, seed per item with
:func:`item_seed`.

The machinery is :mod:`openprocess.parallel` (worker processes, batches,
``OPENPROCESS_WORKERS``); this module adds the *How* tab.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .. import parallel as _core
from ..parallel import ParallelError, item_seed
from .explain import Explanation, current, explaining


def parallel_map(function: Callable, items: Iterable, *, shared: Any = None, setup: Callable | None = None,
                 workers: int | None = None, progress: Callable[[int, int], None] | None = None,
                 cost: Callable[[Any], float] | None = None) -> list:
    """``[function(item) for item in items]`` on the machine's cores, in the
    items' order; with ``shared`` (or ``setup``), ``function(context, item)``.

    ``shared``: data every item needs (a model, a log), sent to each worker
    once.  ``setup(shared)``: run once per worker, its result is the
    ``context`` (a compiled model, a cache).  ``workers``: how many processes
    (default: the cores less one, at most 8; ``1`` runs the loop here).
    ``progress(done, total)``: called as items finish.  ``cost(item)``: an
    estimate of an item's cost, so the expensive ones start first.

    Inside a running box, what ``function`` reports with :func:`flow.note`,
    :func:`flow.steps` and :func:`flow.show` is added to the box's *How* tab
    in item order.  An item that fails raises :class:`ParallelError` with the
    worker's traceback."""
    items = list(items)
    explanation = current()
    if explanation is None or _core.reference(function) is None:
        # Outside a box there is nothing to collect; a function the workers
        # cannot find by name runs here, where its notes reach the tab directly.
        return _core.map_items(function, items, shared=shared, setup=setup, workers=workers, progress=progress,
                               cost=cost)
    setup_ref = _core.reference(setup) if setup is not None else None
    if setup is not None and setup_ref is None:
        return _core.map_items(function, items, shared=shared, setup=setup, workers=1, progress=progress)
    package = (_core.reference(function), setup_ref, shared, shared is not None or setup is not None)
    outcomes = _core.map_items(_explained, items, shared=package, setup=_explained_setup, workers=workers,
                               progress=progress, cost=cost)
    values = []
    for value, entries in outcomes:
        _replay(explanation, entries)
        values.append(value)
    return values


def _explained_setup(package):
    """In a worker: the user's function and its context."""
    function_ref, setup_ref, shared, with_context = package
    function = _core.resolve(function_ref)
    context = _core.resolve(setup_ref)(shared) if setup_ref is not None else shared
    return function, context, with_context


def _explained(prepared, item):
    """In a worker: one item, with what it reported."""
    function, context, with_context = prepared
    with explaining() as explanation:
        value = function(context, item) if with_context else function(item)
    return value, explanation.entries


def _replay(explanation: Explanation, entries: list) -> None:
    """Add a worker's report to the box's, as if it had been made here."""
    for entry in entries:
        explanation.entries.append(entry)
        if entry[0] == "note":
            explanation.notes.append(entry[1])
        elif entry[0] == "steps":
            explanation.steps.extend(entry[1])
        elif entry[0] == "show":
            explanation.shows.append((entry[1], entry[2]))


def workers() -> int:
    """How many worker processes :func:`parallel_map` uses by default."""
    return _core.workers()


__all__ = ["ParallelError", "item_seed", "parallel_map", "workers"]
