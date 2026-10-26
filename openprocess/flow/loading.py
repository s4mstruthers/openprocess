"""Reading event logs once, and large ones in a process of their own.

Why
---
Reading a large XES file is pure Python.  In a thread it holds Python's
interpreter lock most of the time, and the window, which needs the lock for
every widget it builds and every repaint, waits its turn thousands of times:
the app seems frozen although the read is "in the background".  The BPI
Challenge 2019 log (728 MB) froze OpenProcess for over half a minute at
launch, twice over, because the log page and a workflow reading the same
file each parsed it.

What this module does
---------------------
* :func:`read_log` reads a file **once** per content: a second request for
  the same file (same path, size and modification time) gets the same
  :class:`~openprocess.mining.log.EventLog` while anyone still holds it, and
  a request made while it is being read waits for that read instead of
  starting another (the log page and the workflow's *Open log* box share one).
* With :data:`use_processes` set (the app sets it), a log of
  :data:`PROCESS_FROM` bytes or more is read by a **worker process**, which
  has its own interpreter lock, so the window stays responsive.  The worker
  also hashes the file and fingerprints the log, the two things the
  reproducibility badge needs, so the app does not redo them on the window's
  thread.  The log comes back pickled: a columnar log is a few arrays and
  string tables, which unpickle in a fraction of a second.
* Scripts, notebooks and ``openprocess run`` read in-process as before
  (``use_processes`` is False): no process to start, the same result.

Logs are read-only by convention (a box never changes its inputs, §9 of
``docs/framework.md``; filters make new logs), which is what makes sharing
one object safe.
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import tempfile
import threading
import weakref
from concurrent.futures import Future
from pathlib import Path
from typing import Callable

from ..mining.log import EventLog

#: Read large logs in a worker process (the app turns this on; scripts do not need it).
use_processes = False
#: From this size a log is read by a worker process (when :data:`use_processes`).
PROCESS_FROM = 20 * 1024 * 1024
#: First argument that makes a standalone app run as a log-reading worker.
WORKER_FLAG = "--openprocess-log-worker"

_lock = threading.Lock()
_cache: "weakref.WeakValueDictionary[tuple, EventLog]" = weakref.WeakValueDictionary()
_reading: dict[tuple, Future] = {}


class Cancelled(Exception):
    """The read was stopped (the loading page's *Cancel*)."""


def file_key(path: str | Path) -> tuple:
    """What identifies a file's content here: its resolved path, size and
    modification time."""
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return (str(resolved), stat.st_size, stat.st_mtime_ns)


def cached(path: str | Path) -> EventLog | None:
    """The log already read from this file (unchanged since), or None."""
    try:
        return _cache.get(file_key(path))
    except OSError:
        return None


def read_log(path: str | Path, progress: Callable | None = None) -> EventLog:
    """The event log in ``path`` (``.xes``, ``.xes.gz``, ``.csv``, ``.txt`` in course
    notation), read once for everyone who asks while it is in use.

    ``progress(cases, share)`` is called while reading, as by
    :func:`openprocess.mining.xes.read_xes`; it may raise to stop the read
    (then :class:`Cancelled` or that exception comes out, for this caller;
    another caller waiting on the same read gets it too)."""
    key = file_key(path)
    with _lock:
        log = _cache.get(key)
        if log is not None:
            return log
        pending = _reading.get(key)
        if pending is None:
            pending = _reading[key] = Future()
            mine = True
        else:
            mine = False
    if not mine:
        return pending.result()                       # waits without holding the interpreter lock
    try:
        log = _read(Path(path), key, progress)
    except BaseException as error:
        with _lock:
            _reading.pop(key, None)
        pending.set_exception(error)
        raise
    with _lock:
        _reading.pop(key, None)
        _cache[key] = log
    pending.set_result(log)
    return log


def _read(path: Path, key: tuple, progress) -> EventLog:
    lower = path.name.lower()
    if lower.endswith(".csv"):
        from ..mining.csv_import import read_csv
        return read_csv(path)
    if lower.endswith(".txt"):
        from ..mining.log import parse_simple_log
        return EventLog.from_simple_log(parse_simple_log(path.read_text(encoding="utf-8")), path.stem)
    if use_processes and key[1] >= PROCESS_FROM:
        return _read_in_process(path, progress)
    from ..mining.xes import read_xes
    return read_xes(path, progress=progress)


# ---------------------------------------------------------------------------
# The worker process
# ---------------------------------------------------------------------------
def worker_command(*arguments: str) -> list[str]:
    """How to start a worker: this module run by the same Python, or the
    standalone app run again with :data:`WORKER_FLAG`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG, *arguments]
    return [sys.executable, "-m", "openprocess.flow.loading", *arguments]


def _read_in_process(path: Path, progress) -> EventLog:
    from . import record
    with tempfile.TemporaryDirectory(prefix="openprocess-log-", ignore_cleanup_errors=True) as folder:
        output = Path(folder) / "log.pickle"
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        process = subprocess.Popen(worker_command(str(path), str(output)), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", env=environment,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        error_lines: list[str] = []

        def drain_errors() -> None:
            for line in process.stderr:
                error_lines.append(line)
        threading.Thread(target=drain_errors, daemon=True).start()
        outcome = None
        try:
            for line in process.stdout:
                words = line.split()
                if not words:
                    continue
                if words[0] == "progress" and progress is not None and len(words) >= 3:
                    share = None if words[2] == "-" else float(words[2])
                    progress(int(words[1]), share)                  # may raise: Cancel
                elif words[0] in ("done", "error"):
                    outcome = line.strip()
                    break
        except BaseException:
            process.kill()
            process.wait()
            raise
        process.wait()
        if outcome is None or outcome.startswith("error"):
            detail = outcome[6:] if outcome else ("".join(error_lines[-8:]).strip() or
                                                  f"the reader stopped (exit {process.returncode})")
            raise ValueError(detail)
        with open(output, "rb") as handle:
            log, file_hash, digest, summary = pickle.load(handle)
    # What the worker worked out, so the badge does not redo it on the window's thread.
    stat = path.resolve().stat()
    record._file_hashes[(str(path.resolve()), stat.st_size, stat.st_mtime_ns)] = file_hash
    record._remember(log, digest)
    log.source_path = str(path)
    # The overview's summary under the log's default classifier, worked out by the
    # same summarise() in the worker: the log page shows it without computing it.
    log.precomputed_summaries = {summary_key(log.default_classifier()): summary}
    return log


def summary_key(classifier) -> tuple:
    """How a precomputed summary is found again: the classifier's identity."""
    return (classifier.name, tuple(getattr(classifier, "keys", ()) or ()),
            tuple(sorted(getattr(classifier, "lifecycles", None) or ())))


def _child(path: str, output: str) -> int:
    """The worker: read the log, hash the file, fingerprint the log and
    summarise it (the overview), pickle all four to ``output``; ``progress <cases> <share>`` lines while reading,
    then ``done`` or ``error <message>``.  Stops when the parent goes away."""
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    def watch_parent() -> None:
        for _line in sys.stdin:
            pass
        os._exit(1)                                   # stdin closed: the parent is gone or cancelled
    threading.Thread(target=watch_parent, daemon=True).start()

    def report(cases: int, share) -> None:
        print(f"progress {cases} {'-' if share is None else f'{share:.4f}'}", flush=True)
    try:
        from ..mining.xes import read_xes
        from .record import fingerprint, sha256_file
        log = read_xes(path, progress=report)
        print(f"progress {len(log)} 1.0", flush=True)
        from ..mining.stats import summarise
        payload = (log, sha256_file(path), fingerprint(log), summarise(log, log.default_classifier()))
        with open(output, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as error:  # noqa: BLE001 - say what went wrong, in one line
        print("error " + f"{type(error).__name__}: {error}".replace("\n", " "), flush=True)
        return 1
    print("done", flush=True)
    return 0


def run_worker(arguments: list[str]) -> int:
    """The standalone app's entry for :data:`WORKER_FLAG` (``[PATH, OUT]``)."""
    return _child(arguments[0], arguments[1])


if __name__ == "__main__":  # pragma: no cover - the worker process
    raise SystemExit(_child(sys.argv[1], sys.argv[2]))


__all__ = ["Cancelled", "PROCESS_FROM", "WORKER_FLAG", "cached", "file_key", "read_log", "run_worker",
           "use_processes", "worker_command"]
