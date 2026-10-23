"""Running a workflow: in order, only what changed, each box on its own.

The :class:`Runner` walks the workflow in topological order.  Every box's
result is kept under a **key** made of the box's code, its settings and the
keys of its inputs, so

* changing one setting re-runs only the boxes after it,
* re-opening a workflow whose inputs are unchanged runs nothing at all,
* the same box with the same inputs anywhere in any workflow is computed once.

An exception stays in its box: the box fails with the message and the
traceback, the boxes after it wait, and the rest keeps its results.

Sweeps (:mod:`.sweep`): the boxes after a swept setting run once per value;
a box that takes any number of inputs collects the results of every value.

The runner is plain Python; the app calls :meth:`Runner.run` on a worker
thread and listens to ``on_status``.  ``stop`` (a ``threading.Event``) is
checked between boxes.  A box marked ``heavy=True`` can be run in a
separate process with :func:`run_in_process`, which can be killed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .box import BoxSpec, Port
from .convert import can_convert, convert
from .explain import Explanation, collect_result, explaining
from .sweep import assignments, is_sweep
from .workflow import Node, Workflow

WAITING, RUNNING, DONE, FAILED, IDLE, BLOCKED, SKIPPED = (
    "waiting", "running", "done", "failed", "idle", "blocked", "skipped")


def columns_connected(workflow: Workflow, run: "Run | None", node: Node) -> list[str]:
    """The columns of the tables connected to ``node``, as of ``run``: what a
    ``Column`` setting lists.  Empty until the boxes before it have run."""
    if run is None:
        return []
    from .types import Table
    out: list[str] = []
    for sources in workflow.inputs_of(node).values():
        for source_id, output in sources:
            result = run.result(source_id)
            if result is None or result.status != DONE:
                continue
            value = result.values.get(output, result.value)
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                if isinstance(item, Table):
                    out.extend(column for column in item.columns if column not in out)
    return out


@dataclass
class Result:
    """What one box produced in one run (one per sweep variant)."""

    node: str
    status: str = WAITING
    value: Any = None
    values: dict[str, Any] = field(default_factory=dict)   # by output name
    error: str = ""
    traceback: str = ""
    message: str = ""                                       # why it is idle
    duration: float = 0.0
    explanation: Explanation = field(default_factory=Explanation)
    key: str = ""
    cached: bool = False
    variant: int = 0

    @property
    def ok(self) -> bool:
        return self.status == DONE


@dataclass
class Run:
    """Everything a run of a workflow produced."""

    workflow: Workflow
    #: ``(node id, variant)`` -> Result.  Variant 0 is the only one without sweeps.
    results: dict[tuple[str, int], Result] = field(default_factory=dict)
    #: The swept settings of each variant: ``{(node, setting): value}``.
    variants: list[dict[tuple[str, str], Any]] = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0
    stopped: bool = False

    def result(self, node: Node | str, variant: int = 0) -> Result | None:
        node_id = node if isinstance(node, str) else node.id
        return self.results.get((node_id, variant))

    def value(self, node: Node | str, output: str = "out", variant: int = 0) -> Any:
        result = self.result(node, variant)
        if result is None or not result.ok:
            return None
        return result.values.get(output, result.value)

    def status(self, node: Node | str, variant: int = 0) -> str:
        result = self.result(node, variant)
        return result.status if result else WAITING

    def failed(self) -> list[Result]:
        return [r for r in self.results.values() if r.status == FAILED]

    @property
    def ok(self) -> bool:
        return all(r.status == DONE for r in self.results.values())

    def summary(self) -> str:
        done = sum(1 for r in self.results.values() if r.status == DONE)
        return (f"{done} of {len(self.results)} boxes done"
                + (f", {len(self.failed())} failed" if self.failed() else "")
                + (f", {len(self.variants)} sweep variants" if len(self.variants) > 1 else ""))


class Cache:
    """Results by key.  In memory; the app keeps one per folder."""

    def __init__(self, limit: int = 500) -> None:
        self.entries: dict[str, tuple[dict[str, Any], Explanation]] = {}
        self.limit = limit

    def get(self, key: str):
        return self.entries.get(key)

    def put(self, key: str, values: dict[str, Any], explanation: Explanation) -> None:
        if len(self.entries) >= self.limit:
            del self.entries[next(iter(self.entries))]
        self.entries[key] = (values, explanation)

    def clear(self) -> None:
        self.entries.clear()


def _file_signature(value) -> str:
    """``size:mtime_ns`` of a file setting's file, or ``missing``."""
    from pathlib import Path
    try:
        stat = Path(value).stat()
    except OSError:
        return "missing"
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def settings_key(settings: dict[str, Any]) -> str:
    return json.dumps({k: (str(v) if not isinstance(v, (int, float, bool, str, type(None), list, dict)) else v)
                       for k, v in sorted(settings.items())}, sort_keys=True, default=str)


class Runner:
    def __init__(self, library=None, cache: Cache | None = None, folder=None, strict: bool = False) -> None:
        """``folder`` is where a workflow's relative file settings point (its
        own folder in the app); without it they are taken as they are.

        ``strict`` also fingerprints every box's inputs before and after it
        runs, and fails a box that changed one in place (which would quietly
        change the results of every other box reading the same value).  It
        costs a fingerprint per input, so it is for tests and CI
        (``openprocess run --strict``)."""
        from .library import standard_library
        self.library = library or standard_library()
        self.cache = cache or Cache()
        self.folder = folder
        self.strict = strict
        self._per_variant: set[str] = set()

    # -- the public call ----------------------------------------------------------------
    def run(self, workflow: Workflow, changed: list[str] | None = None, previous: Run | None = None,
            stop: threading.Event | None = None,
            on_status: Callable[[Result], None] | None = None) -> Run:
        """Run the workflow (all of it, or from ``changed`` on, reusing
        ``previous`` for the rest).  Returns a :class:`Run`."""
        run = Run(workflow, started=time.time())
        order = workflow.order()
        swept = workflow.swept()
        run.variants = assignments(swept)
        swept_nodes = {node for node, _, _ in swept}
        under_sweep = workflow.descendants(swept_nodes) if swept_nodes else set()
        collectors = {n.id for n in order if n.id in under_sweep
                      and any(p.many for p in workflow.spec(n).inputs)
                      and self._fed_from(workflow, n, under_sweep)}
        after_collectors = workflow.descendants(collectors) if collectors else set()
        per_variant = under_sweep - after_collectors
        self._per_variant = per_variant
        dirty = workflow.descendants(changed) if changed is not None else set(workflow.nodes)
        if previous is None or previous.variants != run.variants:
            dirty = set(workflow.nodes)
        notify = on_status or (lambda r: None)

        def reuse(node_id: str, variant: int) -> bool:
            if node_id in dirty or previous is None:
                return False
            old = previous.result(node_id, variant)
            if old is None:
                return False
            kept = copy.copy(old)
            kept.cached = True
            run.results[(node_id, variant)] = kept
            return True

        for variant_index, assignment in enumerate(run.variants):
            if variant_index > 0 and not per_variant:
                break
            for node in order:
                if node.id in after_collectors or node.id in collectors:
                    continue
                if node.id not in per_variant and variant_index > 0:
                    continue
                if stop is not None and stop.is_set():
                    run.stopped = True
                    run.finished = time.time()
                    return run
                if reuse(node.id, variant_index):
                    continue
                result = self._run_node(workflow, node, run, variant_index, assignment, notify)
                run.results[(node.id, variant_index)] = result
                notify(result)
        for node in order:
            if node.id not in collectors and node.id not in after_collectors:
                continue
            if stop is not None and stop.is_set():
                run.stopped = True
                break
            if reuse(node.id, 0):
                continue
            result = self._run_node(workflow, node, run, 0, {}, notify, collect=node.id in collectors)
            run.results[(node.id, 0)] = result
            notify(result)
        run.finished = time.time()
        return run

    @staticmethod
    def _fed_from(workflow: Workflow, node: Node, nodes: set[str]) -> bool:
        return any(e.source in nodes for e in workflow.edges if e.target == node.id)

    # -- one box ---------------------------------------------------------------------------
    def _run_node(self, workflow: Workflow, node: Node, run: Run, variant: int,
                  assignment: dict, notify, collect: bool = False) -> Result:
        spec = workflow.spec(node)
        result = Result(node.id, variant=variant)
        settings = dict(node.settings)
        for (node_id, name), value in assignment.items():
            if node_id == node.id:
                settings[name] = value
        if any(is_sweep(v) for v in settings.values()):          # a swept node outside its variants
            for name, value in list(settings.items()):
                if is_sweep(value):
                    settings[name] = value.values[0]
        if not spec.available:
            result.status, result.message = BLOCKED, spec.unavailable_reason
            return result
        if self.folder is not None:
            from pathlib import Path
            for setting in spec.settings:
                value = settings.get(setting.name)
                if setting.kind == "path" and value and not Path(value).is_absolute():
                    settings[setting.name] = Path(self.folder) / value
        # Gather inputs
        inputs: dict[str, Any] = {}
        keys: list[str] = []
        fed = workflow.inputs_of(node)
        for port in spec.inputs:
            sources = fed[port.name]
            if not sources:
                if port.optional:
                    inputs[port.name] = [] if port.many else None
                    continue
                result.status = IDLE
                result.message = f"Connect a {port.type_name.lower()} to “{port.label or port.name}”"
                return result
            values = []
            for source_id, output in sources:
                variants = (range(len(run.variants)) if collect and source_id in self._per_variant
                            else [variant if source_id in self._per_variant else 0])
                for v in variants:
                    upstream = run.result(source_id, v) or run.result(source_id, 0)
                    if upstream is None or upstream.status != DONE:
                        if port.many and upstream is not None and upstream.status in (IDLE, FAILED, BLOCKED) and collect:
                            continue
                        result.status = IDLE
                        why = {FAILED: "failed", BLOCKED: "is not allowed to run", IDLE: "is waiting"}.get(
                            upstream.status if upstream else WAITING, "has not run")
                        result.message = f"Waiting for “{workflow.title(source_id)}” ({why})"
                        return result
                    value = upstream.values.get(output, upstream.value)
                    try:
                        values.append(self._fit(value, port))
                    except Exception as error:   # noqa: BLE001 - a converter's failure stays in this box
                        result.status = FAILED
                        result.error = (f"Could not convert the {type(value).__name__} from "
                                        f"“{workflow.title(source_id)}” to a {port.type_name.lower()} "
                                        f"for “{port.label or port.name}”: {error}")
                        result.traceback = traceback.format_exc()
                        return result
                    keys.append(upstream.key + ":" + output)
            inputs[port.name] = values if port.many else values[0]
        # Nothing plugged in at all (every input optional, as Plot's are): the box
        # would run on nothing and fail.  Wait instead, and say what to connect.
        if spec.inputs and not any(fed[port.name] for port in spec.inputs):
            labels = [port.label or port.name for port in spec.inputs]
            result.status = IDLE
            result.message = "Connect " + (labels[0] if len(labels) == 1 else
                                           ", ".join(labels[:-1]) + " or " + labels[-1])
            return result
        # A file not chosen yet (a box just added from the list): wait and say
        # so, rather than run and fail with a traceback about None.
        for setting in spec.settings:
            if setting.kind == "path" and settings.get(setting.name) in (None, ""):
                result.status = IDLE
                result.message = f"Choose the {setting.name} in Settings"
                return result
        # A file setting is keyed by the file as it is now (size and time), not
        # just its name: an input edited on disk gets computed again, never
        # served from the cache as if nothing had happened.
        for setting in spec.settings:
            if setting.kind == "path" and settings.get(setting.name) not in (None, ""):
                keys.append(f"{setting.name}={_file_signature(settings[setting.name])}")
        result.key = hashlib.sha256(
            (spec.fingerprint + "|" + settings_key(settings) + "|" + "|".join(keys)).encode()).hexdigest()
        cached = self.cache.get(result.key)
        if cached is not None:
            result.values, result.explanation = cached
            result.value = result.values.get("out", next(iter(result.values.values()), None))
            result.status, result.cached = DONE, True
            self._stamp(result, workflow, node, spec, settings)
            return result
        # Run it
        result.status = RUNNING
        notify(result)
        started = time.perf_counter()
        before = self._input_fingerprints(inputs) if self.strict else {}
        with explaining() as explanation:
            try:
                value = spec.function(**inputs, **settings)
                values = self._outputs(spec, value)
                problem = self._changed_inputs(inputs, before) if self.strict else ""
            except Exception as error:   # noqa: BLE001 - whatever the box raised stays in the box
                result.status = FAILED
                result.error = "".join(traceback.format_exception_only(type(error), error)).strip()
                result.traceback = traceback.format_exc()
                result.duration = time.perf_counter() - started
                result.explanation = explanation
                return result
        result.duration = time.perf_counter() - started
        if problem:
            result.status, result.error, result.explanation = FAILED, problem, explanation
            return result
        for out in values.values():
            if node.id in self._per_variant:
                self._tag(out, assignment, spec)
            collect_result(out, explanation)
        result.values, result.value, result.explanation = values, values.get("out", value), explanation
        result.status = DONE
        self.cache.put(result.key, values, explanation)
        self._stamp(result, workflow, node, spec, settings)
        return result

    @staticmethod
    def _stamp(result: Result, workflow: Workflow, node: Node, spec: BoxSpec, settings: dict) -> None:
        """Every value a box gives carries where it came from (:mod:`openprocess.flow.provenance`)."""
        from .provenance import make, stamp
        info = make(workflow, node, spec, settings, result.key)
        for out in result.values.values():
            stamp(out, info)

    @staticmethod
    def _outputs(spec: BoxSpec, value) -> dict[str, Any]:
        """The box's return value by output name, each checked against the
        type its signature promises (and converted when a converter exists),
        so a wrong return fails this box with a plain message instead of a
        puzzling error in the box after it."""
        if spec.result_type is not None:
            if not isinstance(value, spec.result_type):
                raise TypeError(f"{spec.name} returned {_a(value)}, but its signature promises "
                                f"a {spec.result_type.__name__}")
            raw = {p.name: getattr(value, p.name) for p in spec.outputs}
        elif not spec.outputs:
            return {}
        else:
            raw = {"out": value}
        return {port.name: _promised(spec, port, raw[port.name]) for port in spec.outputs}

    @staticmethod
    def _input_fingerprints(inputs: dict[str, Any]) -> dict[str, str]:
        from .record import fingerprint
        return {name: fingerprint(value) for name, value in inputs.items() if value is not None}

    def _changed_inputs(self, inputs: dict[str, Any], before: dict[str, str]) -> str:
        after = self._input_fingerprints(inputs)
        changed = [name for name in before if after.get(name) != before[name]]
        if not changed:
            return ""
        return (f"The box changed its input{'s' if len(changed) > 1 else ''} "
                f"{', '.join(repr(n) for n in changed)} in place. A box must leave its inputs as it got "
                "them (other boxes read the same value): copy it first, e.g. copy.deepcopy(log).")

    @staticmethod
    def _fit(value, port: Port):
        """Convert a value to the input's type when a converter exists."""
        if port.type is None or value is None or isinstance(value, port.type):
            return value
        if can_convert(value, port.type):
            return convert(value, port.type)
        return value

    @staticmethod
    def _tag(value, assignment: dict, spec: BoxSpec) -> None:
        """Scores and tables made under a sweep remember the swept values."""
        context = getattr(value, "context", None)
        if isinstance(context, dict) and assignment:
            for (_node, name), v in assignment.items():
                context.setdefault(name, v)


def _a(value) -> str:
    return "None" if value is None else f"a {type(value).__name__}"


def _promised(spec: BoxSpec, port: Port, value):
    """``value`` as the output ``port`` promises it, or :class:`TypeError`."""
    from .types import Predictor
    if port.type is None:                               # typing.Any: anything goes
        return value
    if value is None:
        if port.optional:
            return None
        raise TypeError(f"{spec.name} returned None for “{port.label or port.name}”, but its signature "
                        f"promises a {port.type_name.lower()} (write -> {port.type.__name__} | None "
                        "if nothing is a possible result)")
    if isinstance(value, port.type):
        return value
    if port.type is Predictor and callable(getattr(value, "predict", None)):
        return value                                    # a Predictor is anything with predict (scikit-learn)
    if can_convert(value, port.type):
        return convert(value, port.type)
    raise TypeError(f"{spec.name} returned {_a(value)} for “{port.label or port.name}”, but its signature "
                    f"promises a {port.type_name.lower()} ({port.type.__name__})")


# ---------------------------------------------------------------------------
# Heavy boxes in a process that can be killed
# ---------------------------------------------------------------------------
def _in_process(box_id: str, inputs: dict, settings: dict, queue) -> None:   # pragma: no cover - child
    try:
        from .library import standard_library
        spec = standard_library().get(box_id)
        with explaining() as explanation:
            value = spec.function(**inputs, **settings)
        queue.put(("ok", value, explanation))
    except Exception as error:   # noqa: BLE001
        queue.put(("error", "".join(traceback.format_exception_only(type(error), error)).strip(),
                   traceback.format_exc()))


def run_in_process(spec: BoxSpec, inputs: dict, settings: dict, timeout: float | None = None):
    """Run one box in a separate process (standard-library boxes only, since
    the child imports the box by id).  Returns ``(value, explanation)`` or
    raises ``RuntimeError`` with the child's error."""
    import multiprocessing
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_in_process, args=(spec.id, inputs, settings, queue))
    process.start()
    try:
        outcome = queue.get(timeout=timeout)
    except Exception:   # noqa: BLE001 - queue.Empty or a dead child
        process.kill()
        raise RuntimeError(f"{spec.name} did not finish in time and was stopped")
    process.join()
    if outcome[0] == "error":
        raise RuntimeError(outcome[1])
    return outcome[1], outcome[2]


__all__ = ["columns_connected", "BLOCKED", "Cache", "DONE", "FAILED", "IDLE", "RUNNING", "Result", "Run", "Runner",
           "WAITING", "run_in_process", "settings_key"]
