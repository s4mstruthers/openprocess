"""Versions of an analysis: its record, kept each time you say so.

The record (:mod:`.record`) says what the numbers were and what they came
from.  Saving as you work never replaces it: it moves only when a run is
**kept as a version**, with a note saying why ("after the 0.15 update",
"noise 0.2 instead of 0").  A :class:`Version` holds, for that run, the
whole workflow (so it can be restored), its record (inputs, seeds, software
versions, every result's fingerprint) and a :class:`~.snapshots.Snapshot`
(every box's key figure, for comparing).  :class:`Versions` is the list the
analysis file keeps, oldest first; the file's ``record`` is the latest one's.

The first run in which no box fails is kept as version 1 by itself, so a
new analysis is recorded without a word; from then on the numbers are the
user's to move.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .record import Record
from .runner import DONE, Run
from .snapshots import Snapshot, describe_change, take
from .workflow import Workflow


@dataclass
class Version:
    number: int
    note: str = ""
    taken: str = ""                               # ISO time
    #: What changed since the version before ("Inductive Miner: noise 0 → 0.2").
    label: str = ""
    workflow: dict = field(default_factory=dict)   # Workflow.to_dict()
    record: Record = field(default_factory=Record)
    snapshot: Snapshot = field(default_factory=lambda: Snapshot(""))

    @property
    def title(self) -> str:
        return f"v{self.number}" + (f" · {self.note}" if self.note else "")

    @property
    def software(self) -> str:
        """The OpenProcess version it was kept with."""
        return self.record.versions.get("openprocess", "")

    def to_dict(self) -> dict:
        record = {k: v for k, v in self.record.to_dict().items() if k != "history"}
        return {"number": self.number, "note": self.note, "taken": self.taken, "label": self.label,
                "workflow": self.workflow, "record": record, "snapshot": self.snapshot.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> "Version":
        return cls(int(data.get("number", 0)), str(data.get("note", "")), str(data.get("taken", "")),
                   str(data.get("label", "")), dict(data.get("workflow", {})),
                   Record.from_dict(data.get("record")), Snapshot.from_dict(data.get("snapshot", {})))


@dataclass
class Versions:
    items: list[Version] = field(default_factory=list)      # oldest first

    @property
    def latest(self) -> Version | None:
        return self.items[-1] if self.items else None

    def get(self, number: int) -> Version | None:
        return next((v for v in self.items if v.number == number), None)

    def keep(self, workflow: Workflow, run: Run | None, record: Record, note: str = "") -> Version:
        """Keep ``run`` (whose record is ``record``) as the next version."""
        previous = self.latest
        snapshot = take(workflow, run, previous.snapshot if previous is not None else None)
        version = Version((previous.number if previous is not None else 0) + 1, note.strip(),
                          _dt.datetime.now().isoformat(timespec="seconds"), snapshot.label,
                          workflow.to_dict(), record, snapshot)
        self.items.append(version)
        return version

    def to_list(self) -> list[dict]:
        return [v.to_dict() for v in self.items]

    @classmethod
    def from_list(cls, data) -> "Versions":
        return cls([Version.from_dict(d) for d in (data or []) if isinstance(d, dict)])

    @classmethod
    def first_from(cls, workflow: Workflow, record: Record) -> "Versions":
        """Version 1 of a file saved before versions: its record, with the
        workflow as it is now (the file kept no other)."""
        snapshot = take(workflow, None)
        snapshot.taken = record.saved.replace("+00:00", "")
        snapshot.statuses = {n: (DONE if n in record.results else "waiting") for n in snapshot.statuses}
        snapshot.fingerprints = dict(record.results)
        snapshot.scheme = record.scheme
        snapshot.label = describe_change(None, snapshot)
        return cls([Version(1, "Recorded before versions", snapshot.taken, snapshot.label,
                            workflow.to_dict(), record, snapshot)])


__all__ = ["Version", "Versions"]
