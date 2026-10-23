"""Snapshots of an analysis: every finished run, kept, so two can be compared.

A sweep shows the effect of one setting over a range.  Across sessions the
question is wider: what did I change, and what did it do?  A
:class:`Snapshot` keeps, for one finished run, every box's settings and key
figure and a fingerprint of its result; a :class:`History` keeps the last
few snapshots with the workflow file.  :func:`describe_change` says what
changed between two runs ("Inductive Miner: noise 0 → 0.2"), and
:func:`compare` lines the two up box by box.  A snapshot's settings can be
put back on the workflow, so a run can be returned to.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from .figures import tile_parts
from .record import FINGERPRINT, fingerprint
from .runner import DONE, Run
from .sweep import Sweep, is_sweep
from .workflow import Workflow, _json_value

#: How many snapshots a history keeps.
LIMIT = 20


@dataclass
class Snapshot:
    taken: str                                   # ISO time
    label: str = ""                              # what changed before this run
    #: box id -> {setting: JSON value} (a sweep as {"sweep": [...]})
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    #: box id -> (key figure, line under it), for boxes that have one
    figures: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: box id -> result fingerprint (done boxes only)
    fingerprints: dict[str, str] = field(default_factory=dict)
    #: The fingerprint scheme (see :data:`.record.FINGERPRINT`); 1 for snapshots kept before 0.12.
    scheme: int = FINGERPRINT

    def to_dict(self) -> dict:
        return {"taken": self.taken, "label": self.label, "settings": self.settings, "titles": self.titles,
                "statuses": self.statuses, "figures": {k: list(v) for k, v in self.figures.items()},
                "fingerprints": self.fingerprints, "scheme": self.scheme}

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        return cls(str(data.get("taken", "")), str(data.get("label", "")), dict(data.get("settings", {})),
                   dict(data.get("titles", {})), dict(data.get("statuses", {})),
                   {k: (str(v[0]), str(v[1])) for k, v in data.get("figures", {}).items()
                    if isinstance(v, (list, tuple)) and len(v) == 2},
                   dict(data.get("fingerprints", {})), int(data.get("scheme", 1)))

    @property
    def done(self) -> int:
        return sum(1 for s in self.statuses.values() if s == DONE)


def _settings_json(workflow: Workflow) -> dict[str, dict[str, Any]]:
    return {node.id: {k: (v.to_json() if is_sweep(v) else _json_value(v)) for k, v in node.settings.items()}
            for node in workflow.nodes.values()}


def take(workflow: Workflow, run: Run | None, previous: "Snapshot | None" = None) -> Snapshot:
    """A snapshot of ``run``; its label says what changed since ``previous``."""
    snapshot = Snapshot(_dt.datetime.now().isoformat(timespec="seconds"), settings=_settings_json(workflow),
                        titles={node.id: workflow.title(node.id) for node in workflow.nodes.values()})
    for node in workflow.nodes.values():
        result = run.result(node) if run is not None else None
        snapshot.statuses[node.id] = result.status if result is not None else "waiting"
        if result is not None and result.status == DONE:
            parts = tile_parts(result.value)
            if parts is not None:
                snapshot.figures[node.id] = parts
            try:
                snapshot.fingerprints[node.id] = fingerprint(result.value, remember=True)
            except Exception:  # noqa: BLE001 - a value that cannot be fingerprinted is left out
                pass
    snapshot.label = describe_change(previous, snapshot)
    return snapshot


def _show(value) -> str:
    if isinstance(value, dict) and "sweep" in value:
        return "sweep " + ", ".join(str(v) for v in value["sweep"][:4]) + ("…" if len(value["sweep"]) > 4 else "")
    return str(value)


def setting_changes(before: Snapshot | None, after: Snapshot) -> list[str]:
    """"Inductive Miner: noise 0 → 0.2", one per setting that differs."""
    if before is None:
        return []
    out = []
    for node_id, settings in after.settings.items():
        then = before.settings.get(node_id)
        if then is None:
            continue
        for name, value in settings.items():
            if name in then and then[name] != value:
                out.append(f"{after.titles.get(node_id, node_id)}: {name} {_show(then[name])} → {_show(value)}")
    return out


def describe_change(before: Snapshot | None, after: Snapshot) -> str:
    """What changed between two runs, in a few words."""
    if before is None:
        return "First run"
    parts = setting_changes(before, after)
    added = [after.titles.get(n, n) for n in after.settings if n not in before.settings]
    removed = [before.titles.get(n, n) for n in before.settings if n not in after.settings]
    parts += [f"+ {name}" for name in added] + [f"− {name}" for name in removed]
    if parts:
        return "; ".join(parts)
    same = before.scheme == after.scheme and all(
        after.fingerprints.get(n) == before.fingerprints.get(n) for n in after.fingerprints)
    return "Run again, same results" if same and after.fingerprints else "Run again"


@dataclass
class Row:
    node_id: str
    title: str
    a: str          # A's key figure ("" when A had no such box)
    b: str
    changed: bool
    changes: list[str] = field(default_factory=list)


def compare(a: Snapshot, b: Snapshot) -> list[Row]:
    """Box by box: the key figure in A and in B, and whether the result differs."""
    rows = []
    order = list(a.settings) + [n for n in b.settings if n not in a.settings]
    for node_id in order:
        title = b.titles.get(node_id) or a.titles.get(node_id, node_id)
        fa, fb = a.figures.get(node_id), b.figures.get(node_id)
        in_both = node_id in a.fingerprints and node_id in b.fingerprints and a.scheme == b.scheme
        changed = (a.fingerprints.get(node_id) != b.fingerprints.get(node_id)) if in_both else (fa != fb)
        changes = [c for c in setting_changes(a, b) if c.startswith(title + ":")]
        rows.append(Row(node_id, title, fa[0] if fa else "", fb[0] if fb else "", changed, changes))
    return rows


@dataclass
class History:
    snapshots: list[Snapshot] = field(default_factory=list)      # oldest first

    @property
    def latest(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    def add(self, snapshot: Snapshot) -> bool:
        """Keep ``snapshot`` unless it repeats the latest (same settings, same
        results); at most :data:`LIMIT`.  Returns whether it was kept."""
        latest = self.latest
        if latest is not None and latest.settings == snapshot.settings \
                and latest.fingerprints == snapshot.fingerprints and set(latest.statuses) == set(snapshot.statuses):
            return False
        self.snapshots.append(snapshot)
        del self.snapshots[:-LIMIT]
        return True

    def to_dict(self) -> list[dict]:
        return [s.to_dict() for s in self.snapshots]

    @classmethod
    def from_dict(cls, data) -> "History":
        return cls([Snapshot.from_dict(d) for d in (data or []) if isinstance(d, dict)][-LIMIT:])


def settings_to_apply(snapshot: Snapshot, node_id: str) -> dict[str, Any]:
    """A snapshot's settings for one box, as :meth:`Workflow.set` takes them."""
    out = {}
    for name, value in snapshot.settings.get(node_id, {}).items():
        out[name] = Sweep(tuple(value["sweep"])) if isinstance(value, dict) and "sweep" in value else value
    return out


__all__ = ["Snapshot", "History", "Row", "take", "compare", "describe_change", "setting_changes",
           "settings_to_apply", "LIMIT"]
