"""Is this analysis reproducible, right now?  One status for the header.

The record (:mod:`openprocess.flow.record`) is a file; this turns it into a
visible promise.  :func:`status` compares the record (the latest version,
:mod:`.versions`) with the world now (the inputs' fingerprints, the box
files, the seeds, the settings, the software versions) and with the latest
run (every box's result against its recorded fingerprint), and says one of:

* **Not recorded yet** -- the analysis has never run without a failure;
* **v3 · this run reproduces it** -- every result matches the record and
  nothing it depends on has changed;
* **v3 · n changes since** -- a setting, an input file, a box file, a seed
  or a version differs from the record (a re-run may give other numbers);
* **Changed since v3** -- those changes, and the results differ: the
  numbers moved for a reason you can see; keep a version when you are happy
  with them;
* **Differs from v3** -- the same inputs, settings and versions gave another
  result: a reproducibility problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .record import Record, check, differences
from .runner import BLOCKED, DONE, Run
from .snapshots import setting_changes, take
from .versions import Versions
from .workflow import Workflow


@dataclass
class Status:
    #: "unrecorded", "reproduces", "unchecked", "changed", "moved" or "differs".
    state: str
    headline: str
    details: list[str] = field(default_factory=list)
    #: The version the record is ("v3"; "" when nothing is recorded).
    version: str = ""
    #: How many of the details are changes since the record (the rest: what could not be compared).
    changes: int = 0

    @property
    def tone(self) -> str:
        return {"reproduces": "good", "changed": "warning", "moved": "warning",
                "differs": "critical"}.get(self.state, "muted")

    @property
    def short(self) -> str:
        """Two or three words for a badge; :attr:`headline` is the sentence."""
        v = self.version
        if self.state == "changed":
            return f"{v} · {self.changes} change{'s' if self.changes != 1 else ''}"
        return {"unrecorded": "Not recorded", "reproduces": f"{v} · reproducible", "differs": f"Differs from {v}",
                "moved": f"Changed since {v}", "unchecked": f"{v} · not run"}.get(self.state, self.headline)

    @property
    def can_keep(self) -> bool:
        """Whether keeping a version now would say something new."""
        return self.state in ("changed", "moved", "differs")


def status(workflow: Workflow, record: Record | None, run: Run | None, folder=None,
           versions: Versions | None = None) -> Status:
    """The analysis's reproducibility, from its record, its versions and its latest run."""
    if record is None or not record.results:
        return Status("unrecorded", "Not recorded yet", [
            "The first run in which no box fails is kept as version 1: the record keeps the inputs' "
            "fingerprints, the versions, the seeds and every result, so the numbers can be checked again later."])
    latest = versions.latest if versions is not None else None
    v = f"v{latest.number}" if latest is not None else "the record"
    name = f"v{latest.number}" if latest is not None else "Recorded"       # "v3 · reproducible"
    changed = differences(record, workflow, folder)
    if latest is not None:
        now = take(workflow, None)
        changed += setting_changes(latest.snapshot, now)
        changed += [f"“{now.titles.get(n, n)}” was added." for n in now.settings if n not in latest.snapshot.settings]
        changed += [f"“{latest.snapshot.titles.get(n, n)}” was removed."
                    for n in latest.snapshot.settings if n not in now.settings]
    # A box that could not run here (a library is missing) is not a difference
    # in its result: say why it could not be compared instead.
    blocked = {node_id for node_id in record.results
               if run is not None and run.result(node_id, 0) is not None and run.result(node_id, 0).status == BLOCKED}
    uncompared = []
    if blocked:
        record = Record(**{**record.__dict__, "results": {k: v for k, v in record.results.items() if k not in blocked}})
        uncompared = [f"“{workflow.title(n)}” could not run here ({run.result(n, 0).message}), so its "
                      "result was not compared." for n in sorted(blocked)]
    changed += uncompared                         # not a reproduction either: say so, as a change
    mismatches = check(workflow, record, run) if run is not None else []
    compared = [node_id for node_id in record.results
                if run is not None and run.result(node_id, 0) is not None
                and run.result(node_id, 0).status == DONE]
    count = len(changed)
    plural = f"{count} change{'s' if count != 1 else ''}"
    if mismatches and changed:
        return Status("moved", f"The results differ from {v} after {plural}", changed + mismatches, v, count)
    if mismatches:
        return Status("differs", f"This run differs from {v}, with nothing changed", mismatches, v, 0)
    if changed:
        return Status("changed", f"{name} · {plural} since", changed, name, count)
    if not compared:
        return Status("unchecked", f"{name} · not run since", [
            "Run ▶ and every result is checked against its recorded fingerprint."], name, 0)
    return Status("reproduces", f"{name} · this run reproduces it", [
        f"{len(compared)} result{'s' if len(compared) != 1 else ''} match the record's fingerprints, and "
        "nothing the analysis depends on has changed."], name, 0)


__all__ = ["Status", "status"]
