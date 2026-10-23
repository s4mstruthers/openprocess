"""Is this analysis reproducible, right now?  One status for the header.

The record (:mod:`openprocess.flow.record`) is a file; this turns it into a
visible promise.  :func:`status` compares the record with the world now
(the inputs' fingerprints, the box files, the seeds, the versions) and with
the latest run (every box's result against its recorded fingerprint), and
says one of:

* **Not recorded yet** -- the analysis has never been saved with a run;
* **Recorded · this run reproduces it** -- every result matches the record and
  nothing it depends on has changed;
* **Recorded · n changes since** -- an input file, a box file, a seed or a
  version differs from the record (a re-run may give other numbers);
* **This run differs from the record** -- the same inputs gave another result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .record import Record, check, differences
from .runner import BLOCKED, DONE, Run
from .workflow import Workflow


@dataclass
class Status:
    #: "unrecorded", "reproduces", "unchecked", "changed" or "differs".
    state: str
    headline: str
    details: list[str] = field(default_factory=list)

    @property
    def tone(self) -> str:
        return {"reproduces": "good", "changed": "warning", "differs": "critical"}.get(self.state, "muted")

    @property
    def short(self) -> str:
        """Two or three words for a badge; :attr:`headline` is the sentence."""
        if self.state == "changed":
            count = len(self.details)
            return f"{count} change{'s' if count != 1 else ''} since"
        return {"unrecorded": "Not recorded", "reproduces": "Reproducible", "differs": "Differs from the record",
                "unchecked": "Recorded, not run"}.get(self.state, self.headline)


def status(workflow: Workflow, record: Record | None, run: Run | None, folder=None) -> Status:
    """The analysis's reproducibility, from its record and its latest run."""
    if record is None or not record.results:
        return Status("unrecorded", "Not recorded yet", [
            "Save the analysis (it is saved as you work in a folder): the record keeps the inputs' "
            "fingerprints, the versions and the seeds, so the numbers can be checked again later."])
    changed = differences(record, workflow, folder)
    # A box that could not run here (a library is missing) is not a difference
    # in its result: say why it could not be compared instead.
    blocked = {node_id for node_id in record.results
               if run is not None and run.result(node_id, 0) is not None and run.result(node_id, 0).status == BLOCKED}
    if blocked:
        record = Record(**{**record.__dict__, "results": {k: v for k, v in record.results.items() if k not in blocked}})
        changed = changed + [f"“{workflow.title(n)}” could not run here ({run.result(n, 0).message}), so its "
                             "result was not compared." for n in sorted(blocked)]
    mismatches = check(workflow, record, run) if run is not None else []
    compared = [node_id for node_id in record.results
                if run is not None and run.result(node_id, 0) is not None
                and run.result(node_id, 0).status == DONE]
    if mismatches:
        return Status("differs", "This run differs from the record", mismatches + changed)
    if changed:
        count = len(changed)
        return Status("changed", f"Recorded · {count} change{'s' if count != 1 else ''} since", changed)
    if not compared:
        return Status("unchecked", "Recorded · not run since", [
            "Run ▶ and every result is checked against its recorded fingerprint."])
    return Status("reproduces", "Recorded · this run reproduces it", [
        f"{len(compared)} result{'s' if len(compared) != 1 else ''} match the record's fingerprints, and "
        "nothing the analysis depends on has changed."])


__all__ = ["Status", "status"]
