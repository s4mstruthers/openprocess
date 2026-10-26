"""Running a function over many items on several processes, with exactly the result of a loop.

The core of OpenProcess's parallel work: the engine's alignments and token
replay use it, and boxes use it through :func:`openprocess.flow.parallel_map`
(see ``docs/workflows.md``, *Parallel work*).  Standard library only.

The promise
-----------
``map_items(f, items)`` returns ``[f(item) for item in items]``: the same
values, in the same order, whatever the number of workers or the order in
which they finish.  For that to hold, ``f`` must compute each item from the
item (and the shared data) alone -- not from what other items did, not from
global state that changes, and, if it draws random numbers, from a seed made
from the item's index (:func:`item_seed`), not from a generator shared across
items.  That is how OpenProcess's own algorithms are written, and the tests
compare the parallel results with the loop's field by field.

How it works
------------
* Workers are processes, not threads: the work is pure Python, and threads
  share one interpreter lock, so they would run one at a time.
* ``shared`` (a model, a log, settings) is sent to each worker once; ``setup``
  turns it into what every item needs (a compiled net, a cache) once per
  worker, not once per item.
* Items go out in small batches, longest first when ``cost`` says how long
  each takes, to whichever worker is free: items differ a lot in cost, and
  fixed shares would leave most workers waiting for one.
* A worker is this module run by the same Python (``python -m
  openprocess.parallel``), or the standalone app started with
  :data:`WORKER_FLAG`.  The function travels by name (its module and name, or
  its file for a box in a folder's ``boxes/``), so it must be defined at the
  top level of a module.  A function that cannot be found by name -- a
  lambda, one defined inside another, one typed into a notebook -- is run
  here, in a loop, with the same result.
* ``workers``: the machine's cores less one, at most 8, or the environment
  variable ``OPENPROCESS_WORKERS`` (``1``: everything runs here).
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

#: First argument that makes a standalone app run as a parallel worker.
WORKER_FLAG = "--openprocess-parallel-worker"

#: Why worker processes do not work on this machine (they could not start, or
#: died without a word), once that happened; every later call then runs the
#: loop here.  None while they work.  The loop gives the same result, so this
#: only costs time; ``status()`` reports it.
unavailable: str | None = None


class _WorkersFailed(Exception):
    """The workers failed, not an item: fall back to the loop.  ``machine``:
    the processes could not start or died (so not on this machine at all),
    rather than this one job not loading in them."""

    def __init__(self, message: str, machine: bool) -> None:
        super().__init__(message)
        self.machine = machine


class ParallelError(RuntimeError):
    """An item failed in a worker: which item, and the worker's traceback."""

    def __init__(self, index: int, message: str, remote_traceback: str = "") -> None:
        super().__init__(f"item {index}: {message}")
        self.index = index
        self.remote_traceback = remote_traceback


def workers() -> int:
    """How many worker processes to use (1: none): the cores less one, at
    most 8, or ``OPENPROCESS_WORKERS``."""
    wanted = os.environ.get("OPENPROCESS_WORKERS")
    if wanted:
        try:
            return max(1, int(wanted))
        except ValueError:
            pass
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def item_seed(seed: int, index: int) -> int:
    """A seed for item ``index`` made from ``seed``: the same on every run and
    whatever worker handles the item, so a random algorithm gives the same
    results in parallel as in a loop.  ``random.Random(item_seed(seed, i))``."""
    digest = hashlib.sha256(f"openprocess:{seed}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


# ---------------------------------------------------------------------------
# Functions by name
# ---------------------------------------------------------------------------
def reference(function: Callable) -> tuple[str, str, str | None] | None:
    """How a worker finds ``function``: ``(module, qualified name, file)``,
    or None when it cannot be found by name (a lambda, a nested function, one
    defined in ``__main__`` or a notebook)."""
    function = getattr(function, "function", function)          # a @box: its function
    module = getattr(function, "__module__", None)
    qualname = getattr(function, "__qualname__", "")
    if not module or module == "__main__" or "<" in qualname:
        return None
    try:
        file = inspect.getsourcefile(function) or inspect.getfile(function)
    except (TypeError, OSError):
        file = None
    return module, qualname, file


def resolve(ref: tuple[str, str, str | None]) -> Callable:
    """The function a :func:`reference` names (in a worker)."""
    module_name, qualname, file = ref
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        module = sys.modules.get(module_name)
        if module is None:
            if not file:
                raise
            spec = importlib.util.spec_from_file_location(module_name, file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
    value: Any = module
    for part in qualname.split("."):
        value = getattr(value, part)
    return getattr(value, "function", value)


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------
def map_items(function: Callable, items: Iterable, *, shared: Any = None, setup: Callable | None = None,
              workers: int | None = None, progress: Callable[[int, int], None] | None = None,
              cost: Callable[[Any], float] | None = None) -> list:
    """``[function(item) for item in items]`` on worker processes, in the items' order.

    With ``shared`` or ``setup``, each item is computed as
    ``function(context, item)``, where ``context`` is ``setup(shared)`` (once per
    worker) or ``shared`` itself.  ``progress(done, total)`` is called from
    this thread as items finish; it may raise to stop (the workers are
    stopped).  ``cost(item)`` estimates an item's cost, so the expensive ones
    start first.  An item that fails raises :class:`ParallelError`."""
    global unavailable
    items = list(items)
    count = globals()["workers"]() if workers is None else workers
    count = max(1, min(count, len(items)))
    ref = reference(function)
    setup_ref = reference(setup) if setup is not None else None
    if count == 1 or ref is None or (setup is not None and setup_ref is None) or unavailable is not None:
        return _loop(function, items, shared, setup, progress)
    order = list(range(len(items)))
    if cost is not None:
        order.sort(key=lambda i: (-cost(items[i]), i))
    size = max(1, min(64, len(items) // (count * 12) or 1))
    batches = [order[k:k + size] for k in range(0, len(items), size)]
    try:
        return _run(ref, setup_ref, shared, items, batches, count, progress,
                    with_context=(shared is not None or setup is not None))
    except _WorkersFailed as failure:
        # Workers cannot run here (no way to start a process, a sandbox, a worker
        # killed by the system): the loop gives the same result, only slower.
        if failure.machine:
            unavailable = str(failure) or "the worker processes failed"
        return _loop(function, items, shared, setup, progress)


def status() -> dict:
    """Whether worker processes work here: ``{"workers": n, "available": bool, "why": str | None}``."""
    return {"workers": workers(), "available": unavailable is None, "why": unavailable}


def _loop(function, items, shared, setup, progress) -> list:
    context = setup(shared) if setup is not None else shared
    with_context = shared is not None or setup is not None
    out = []
    for done, item in enumerate(items, 1):
        out.append(function(context, item) if with_context else function(item))
        if progress is not None:
            progress(done, len(items))
    return out


def _run(ref, setup_ref, shared, items, batches, count, progress, with_context) -> list:
    total = len(items)
    lock = threading.Lock()
    next_batch, done = [0], [0]
    results: list = [None] * total
    failure: list = []
    # ignore_cleanup_errors: on Windows a file a stopped worker had open can outlive it briefly.
    with tempfile.TemporaryDirectory(prefix="openprocess-parallel-", ignore_cleanup_errors=True) as folder:
        job = Path(folder) / "job.pickle"
        with open(job, "wb") as handle:
            pickle.dump((ref, setup_ref, shared, items, with_context), handle, protocol=pickle.HIGHEST_PROTOCOL)
        command_of = worker_command
        processes = []
        try:
            for number in range(count):
                out = Path(folder) / f"out{number}.pickle"
                process = subprocess.Popen(command_of(str(job), str(out)), stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                processes.append((process, out))
        except OSError as error:
            for process, _out in processes:
                process.kill()
                process.wait()
            raise _WorkersFailed(f"could not start a worker process: {error}", machine=True) from error

        def hand_out(process) -> None:
            with lock:
                index = next_batch[0]
                next_batch[0] += 1
            try:
                line = " ".join(map(str, batches[index])) if index < len(batches) else "stop"
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

        errors: dict = {}

        def drain(process) -> None:
            # Read what a worker writes to stderr (a box's print(), warnings) as it comes:
            # a full pipe would stop the worker.  The end is kept, to say why one died.
            tail: list[str] = []
            for line in process.stderr:
                tail.append(line)
                del tail[:-40]
            errors[process.pid] = "".join(tail)

        def serve(process) -> None:
            for line in process.stdout:
                word = line.split(maxsplit=1)[0] if line.strip() else ""
                if word == "step":
                    with lock:
                        done[0] += 1
                elif word == "ready":
                    hand_out(process)
                elif word == "failed":
                    failure.append(line.strip())
        threads = [threading.Thread(target=serve, args=(p,), daemon=True) for p, _out in processes]
        drains = [threading.Thread(target=drain, args=(p,), daemon=True) for p, _out in processes]
        for thread in threads + drains:
            thread.start()
        try:
            while any(thread.is_alive() for thread in threads) and not failure:
                for thread in threads:
                    thread.join(0.05)
                if progress is not None:
                    with lock:
                        finished = done[0]
                    progress(finished, total)
        except BaseException:
            for process, _out in processes:
                process.kill()
            for process, _out in processes:
                process.wait()
            raise
        returned = 0
        for process, out in processes:
            if failure:
                process.kill()
            process.wait()
            if failure:
                continue
            if process.returncode != 0 or not out.exists():
                for thread in drains:
                    thread.join(2)
                said = errors.get(process.pid, "").strip()[-800:].replace("\n", " ")
                failure.append("died -1 " + (said or f"exit {process.returncode}"))
                continue
            with open(out, "rb") as handle:
                for index, value in pickle.load(handle):
                    results[index] = value
                    returned += 1
        if not failure and returned != total:
            failure.append(f"died -1 {total - returned} of {total} items came back from no worker")
        if failure:
            line = failure[0]
            parts = line.split(maxsplit=2)
            if parts[1:2] == ["-1"]:
                # Not an item: a worker died (the machine) or could not load this job. The loop instead.
                raise _WorkersFailed(parts[2] if len(parts) > 2 else "a worker failed", machine=parts[0] == "died")
            _raise(line, Path(folder))
    if progress is not None:
        progress(total, total)
    return results


def _raise(line: str, folder: Path) -> None:
    parts = line.split(maxsplit=2)
    index = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else -1
    message = parts[2] if len(parts) > 2 else "a worker failed"
    remote = ""
    trace = folder / f"traceback{index}.txt"
    if trace.exists():
        remote = trace.read_text(encoding="utf-8")
    raise ParallelError(index, message, remote)


def worker_command(*arguments: str) -> list[str]:
    """How to start a worker: this module run by the same Python, or the
    standalone app run again with :data:`WORKER_FLAG`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG, *arguments]
    return [sys.executable, "-m", "openprocess.parallel", *arguments]


def _child(job_path: str, out_path: str) -> int:
    """The worker: load the job once, ``setup`` once, then ask for batches
    (``ready``), compute each item (``step`` after each) until ``stop``, and
    pickle ``[(index, result), …]`` to ``out_path``."""
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    # The protocol has stdout to itself: what the work prints goes to stderr.
    protocol = sys.stdout
    sys.stdout = sys.stderr

    def say(text: str) -> None:
        protocol.write(text + "\n")
        protocol.flush()
    index = -1                                    # -1 until an item starts: a failure of the worker itself
    try:
        with open(job_path, "rb") as handle:
            ref, setup_ref, shared, items, with_context = pickle.load(handle)
        function = resolve(ref)
        context = resolve(setup_ref)(shared) if setup_ref is not None else shared
        out = []
        say("ready")
        for line in sys.stdin:
            line = line.strip()
            if not line or line == "stop":
                break
            for index in map(int, line.split()):
                item = items[index]
                out.append((index, function(context, item) if with_context else function(item)))
                say("step")
            say("ready")
        with open(out_path, "wb") as handle:
            pickle.dump(out, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as error:  # noqa: BLE001 - the parent says which item, with the traceback
        Path(job_path).with_name(f"traceback{index}.txt").write_text(traceback.format_exc(), encoding="utf-8")
        say(f"failed {index} " + f"{type(error).__name__}: {error}".replace("\n", " "))
        return 1
    return 0


def run_worker(arguments: list[str]) -> int:
    """The standalone app's entry for :data:`WORKER_FLAG` (``[JOB, OUT]``)."""
    return _child(arguments[0], arguments[1])


if __name__ == "__main__":  # pragma: no cover - the worker process
    raise SystemExit(_child(sys.argv[1], sys.argv[2]))


__all__ = ["ParallelError", "WORKER_FLAG", "item_seed", "map_items", "reference", "resolve", "run_worker",
           "status", "unavailable", "worker_command", "workers"]
