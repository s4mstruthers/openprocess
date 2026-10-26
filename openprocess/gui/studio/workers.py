"""Run slow computations off the GUI thread.

Alignments, state spaces and large imports can take seconds.  Running them
on the GUI thread would freeze the window (the spinning beach ball), so they
run on Qt's thread pool and report back through signals, which Qt delivers
on the GUI thread -- the only thread allowed to touch widgets.

Usage::

    run_in_background(lambda: align_log(net, log), on_done, on_error)

With ``on_progress``, the function is called with a ``report`` callable it
may call from its thread; each report reaches ``on_progress`` on the GUI
thread::

    run_in_background(lambda report: write_xes(log, path, progress=report),
                      on_done, on_error, on_progress=lambda n, total: ...)
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class _Task(QRunnable):
    def __init__(self, function, reports: bool = False) -> None:
        super().__init__()
        self.function = function
        self.reports = reports
        self.signals = _Signals()

    def run(self) -> None:
        try:
            if self.reports:
                result = self.function(lambda *values: self.signals.progress.emit(values))
            else:
                result = self.function()
        except Exception as error:  # noqa: BLE001 - report anything to the UI
            detail = "".join(traceback.format_exception_only(type(error), error)).strip()
            self.signals.failed.emit(detail)
        else:
            self.signals.done.emit(result)


# Keep references: a QRunnable's signal object must outlive the thread.
_running: set[_Task] = set()


def run_in_background(function, on_done, on_error=None, on_progress=None) -> None:
    task = _Task(function, reports=on_progress is not None)
    _running.add(task)
    if on_progress is not None:
        task.signals.progress.connect(lambda values: on_progress(*values))

    def finished(result) -> None:
        _running.discard(task)
        on_done(result)

    def failed(message: str) -> None:
        _running.discard(task)
        if on_error is not None:
            on_error(message)

    task.signals.done.connect(finished)
    task.signals.failed.connect(failed)
    QThreadPool.globalInstance().start(task)
