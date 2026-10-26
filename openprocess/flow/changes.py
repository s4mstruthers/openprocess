"""A journal of the files an assistant changes, so every change can be reviewed and undone.

An AI assistant working through the MCP server (:mod:`openprocess.mcp`)
writes files into the user's folder: a discovered net as PNML, a workflow, an
exercise's ``question.md``, a box.  Nothing it does should be invisible or
irreversible, so every write goes through :func:`record`:

* the file as it was is copied into ``<folder>/.openprocess-changes/backups/``
  (nothing when the file is new);
* one line is appended to ``<folder>/.openprocess-changes/journal.jsonl``:
  when, which tool, which file, a one-line summary, the client's name, and
  the hashes before and after.

The app reads the journal (``Review AI changes…``, and a pill in the window
when there is something new), shows each change as a diff, and *Undo* puts
the backup back (:func:`undo`).  ``Keep`` marks it reviewed.  A folder with
no assistant has no journal and nothing changes for it.

No Qt here: the server writes the journal, the app and the CLI read it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

FOLDER = ".openprocess-changes"
JOURNAL = "journal.jsonl"
BACKUPS = "backups"


@dataclass
class Change:
    """One line of the journal."""

    id: str
    time: str                           # ISO 8601, local time
    tool: str                           # the MCP tool (or "cli", "notebook")
    path: str                           # absolute path of the file written
    summary: str = ""
    client: str = ""                    # who asked: "Claude Desktop", "Claude Code", …
    before_sha256: str | None = None    # None: the file is new
    after_sha256: str | None = None     # None: the file was removed
    backup: str | None = None           # path of the copy of the file as it was
    reviewed: bool = False
    undone: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.after_sha256 is None:
            return "removed"
        return "created" if self.before_sha256 is None else "changed"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Change":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def journal_folder(folder: str | Path) -> Path:
    """Where a folder's journal lives (``<folder>/.openprocess-changes``)."""
    return Path(folder) / FOLDER


def folder_for(path: str | Path, folder: str | Path | None = None) -> Path:
    """The folder whose journal a change of ``path`` belongs to: the one
    given; else the nearest folder above the file that OpenProcess works in
    (it has the app's ``.openprocess`` state, or a journal already), so a box
    written to ``boxes/`` or an exercise in ``Week 3/`` is listed where the
    app shows its review; else the file's own folder (a ``boxes/`` folder's
    parent)."""
    if folder is not None:
        return Path(folder)
    parent = Path(path).resolve().parent
    for candidate in (parent, *parent.parents):
        if (candidate / ".openprocess").exists() or (candidate / FOLDER).is_dir():
            return candidate
        if candidate == Path.home() or candidate.parent == candidate:
            break
    return parent.parent if parent.name == "boxes" else parent


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _append(folder: Path, change: Change) -> None:
    where = journal_folder(folder)
    where.mkdir(parents=True, exist_ok=True)
    with open(where / JOURNAL, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(change.to_dict(), ensure_ascii=False) + "\n")


class recording:
    """``with recording(path, tool, summary, client=...) as change:`` around a
    write: the file as it was is backed up before the block, the hash after
    the block is journaled.  Use :func:`record` for the one-call form."""

    def __init__(self, path: str | Path, tool: str, summary: str = "", client: str = "",
                 folder: str | Path | None = None, **extra) -> None:
        self.path = Path(path).resolve()
        self.folder = folder_for(self.path, folder)
        stamp = _now()
        # A random id: two changes of one file within a second are two changes.
        self.change = Change(id=uuid.uuid4().hex[:12],
                             time=stamp, tool=tool, path=str(self.path), summary=summary, client=client,
                             before_sha256=_sha256(self.path), extra=dict(extra))

    def __enter__(self) -> Change:
        if self.change.before_sha256 is not None:
            backups = journal_folder(self.folder) / BACKUPS
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups / f"{self.change.id} {self.path.name}"
            shutil.copy2(self.path, backup)
            self.change.backup = str(backup)
        return self.change

    def __exit__(self, kind, _error, _trace) -> None:
        if kind is not None:
            return                                        # the write failed: nothing to journal
        self.change.after_sha256 = _sha256(self.path)
        if self.change.after_sha256 == self.change.before_sha256:
            if self.change.backup:
                Path(self.change.backup).unlink(missing_ok=True)
            return                                        # nothing changed
        _append(self.folder, self.change)


def record(path: str | Path, content: str | bytes, tool: str, summary: str = "", client: str = "",
           folder: str | Path | None = None, **extra) -> Change:
    """Write ``content`` to ``path`` through a temporary file, journaling the
    change (and backing the old file up) in the folder's journal."""
    path = Path(path)
    with recording(path, tool, summary, client, folder, **extra) as change:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-openprocess")
        if isinstance(content, bytes):
            temporary.write_bytes(content)
        else:
            temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    return change


def changes(folder: str | Path) -> list[Change]:
    """Every change in the folder's journal, oldest first."""
    path = journal_folder(folder) / JOURNAL
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Change.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    # Later lines about the same change (reviewed, undone) replace earlier ones.
    by_id: dict[str, Change] = {}
    for change in out:
        by_id[change.id] = change
    return list(by_id.values())


def pending(folder: str | Path) -> list[Change]:
    """The changes nobody has reviewed yet."""
    return [c for c in changes(folder) if not c.reviewed and not c.undone]


def _update(folder: Path, change: Change) -> None:
    _append(folder, change)


def mark_reviewed(folder: str | Path, change_id: str) -> Change | None:
    """*Keep*: the change stays, and is no longer listed as new."""
    for change in changes(folder):
        if change.id == change_id:
            change.reviewed = True
            _update(Path(folder), change)
            return change
    return None


def undo(folder: str | Path, change_id: str) -> Change | None:
    """Put the file back as it was before the change (the backup; nothing
    when the file was new, which removes it).  The current content is backed
    up first, so an undo can itself be undone by hand."""
    for change in changes(folder):
        if change.id != change_id or change.undone:
            continue
        target = Path(change.path)
        if target.exists():
            backups = journal_folder(folder) / BACKUPS
            backups.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backups / f"{change.id} undone {target.name}")
        if change.backup and Path(change.backup).is_file():
            shutil.copy2(change.backup, target)
        elif change.before_sha256 is None:
            target.unlink(missing_ok=True)
        change.undone = True
        change.reviewed = True
        _update(Path(folder), change)
        return change
    return None


def diff_of(change: Change, context: int = 3) -> str:
    """A unified diff of the change, for text files (an empty string for
    binary files or when nothing can be compared)."""
    import difflib
    before, after = "", ""
    try:
        if change.backup and Path(change.backup).is_file():
            before = Path(change.backup).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        if Path(change.path).is_file():
            after = Path(change.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    name = Path(change.path).name
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        f"{name} (before)", f"{name} (after)", n=context))


def describe(change: Change) -> str:
    """One line: ``changed net.pnml — Discovered with the Inductive Miner (Claude Desktop, 14:02)``."""
    when = change.time[11:16] if len(change.time) >= 16 else change.time
    who = f"{change.client}, " if change.client else ""
    return f"{change.kind} {Path(change.path).name} — {change.summary or change.tool} ({who}{when})"


__all__ = ["BACKUPS", "Change", "FOLDER", "JOURNAL", "changes", "describe", "diff_of", "folder_for",
           "journal_folder", "mark_reviewed", "pending", "record", "recording", "undo"]
