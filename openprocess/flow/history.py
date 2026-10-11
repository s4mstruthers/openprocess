"""Undo and redo for a workflow, by snapshots.

A workflow is small (its dict form, :meth:`Workflow.to_dict`, is a few
kilobytes), so every change keeps the whole state before it: adding,
removing, wiring, moving or grouping boxes, a setting, a rename.  Undo puts
a snapshot back; the page then runs the boxes again, and the cache serves
the ones that did not change.  Snapshots are taken where the page already
learns of a change, so no edit is instrumented by hand, and a box author
never sees any of this.
"""

from __future__ import annotations

import time

#: How many steps back are kept.
LIMIT = 100
#: A drag arrives as several moves; those within this many seconds are one step.
MOVE_SECONDS = 1.5


def describe(before: dict, after: dict) -> str:
    """What changed from ``before`` to ``after``, for "Undo adding a box"."""
    b = {n["id"]: n for n in before.get("boxes", [])}
    a = {n["id"]: n for n in after.get("boxes", [])}
    added, removed = set(a) - set(b), set(b) - set(a)
    if added:
        return "adding a box" if len(added) == 1 else f"adding {len(added)} boxes"
    if removed:
        return "removing a box" if len(removed) == 1 else f"removing {len(removed)} boxes"
    if before.get("connections") != after.get("connections"):
        return "a connection"
    if before.get("groups") != after.get("groups"):
        return "grouping"
    for node_id in a:
        if a[node_id].get("settings") != b[node_id].get("settings"):
            return "a setting"
        if a[node_id].get("title", "") != b[node_id].get("title", ""):
            return "a rename"
    if any(a[i].get("position") != b[i].get("position") for i in a):
        return "a move"
    if any(a[i].get("layout") != b[i].get("layout") for i in a):
        return "tidying a drawing"
    if before.get("name") != after.get("name"):
        return "a rename"
    return "a change"


class History:
    """The states a workflow went through; :attr:`state` is the current one."""

    def __init__(self, snapshot: dict, limit: int = LIMIT) -> None:
        self.state = snapshot
        self.limit = limit
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._last: tuple[str | None, float] = (None, 0.0)

    def record(self, snapshot: dict, kind: str = "change", now: float | None = None) -> bool:
        """The workflow is now ``snapshot``; the state before it becomes undoable.
        Returns whether anything changed.  ``kind`` "move" folds a drag's
        steps into one."""
        if snapshot == self.state:
            return False
        now = time.monotonic() if now is None else now
        last_kind, last_time = self._last
        if not (kind == "move" and last_kind == "move" and now - last_time < MOVE_SECONDS and self.undo_stack):
            self.undo_stack.append(self.state)
            del self.undo_stack[:-self.limit]
        self.redo_stack.clear()
        self.state = snapshot
        self._last = (kind, now)
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    @property
    def undo_label(self) -> str:
        return describe(self.undo_stack[-1], self.state) if self.undo_stack else ""

    @property
    def redo_label(self) -> str:
        return describe(self.state, self.redo_stack[-1]) if self.redo_stack else ""

    def undo(self) -> tuple[str, dict] | None:
        """Step back: (what is undone, the state to restore), or None."""
        if not self.undo_stack:
            return None
        before = self.undo_stack.pop()
        label = describe(before, self.state)
        self.redo_stack.append(self.state)
        self.state = before
        self._last = (None, 0.0)
        return label, before

    def redo(self) -> tuple[str, dict] | None:
        if not self.redo_stack:
            return None
        after = self.redo_stack.pop()
        label = describe(self.state, after)
        self.undo_stack.append(self.state)
        self.state = after
        self._last = (None, 0.0)
        return label, after


__all__ = ["History", "describe", "LIMIT", "MOVE_SECONDS"]
