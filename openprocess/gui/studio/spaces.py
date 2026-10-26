"""The three spaces of the window: Mine, Model and Learn.

One window, one folder, three spaces switched at the top of the window.
Each space has its own sidebar and footer, so process mining and net
modelling never share a list:

* **Mine** -- event logs and analyses (workflows): discover, check, compare.
  Its sidebar lists the folder's analyses, and the logs underneath them.
* **Model** -- Petri nets and coloured nets: draw, play, simulate, analyse.
  Its sidebar lists the folder's models.
* **Learn** -- an exercise pack, which takes the whole window
  (:mod:`openprocess.gui.learn.mode`).

A document belongs to exactly one space (:func:`space_of`), and opening or
selecting it switches the window there.  The two spaces hand things to each
other through two kinds of object only, an event log and a net; each handoff
is one button that moves the whole window.
"""

from __future__ import annotations

from .documents import (ComparisonDocument, CpnDocument, LogDocument, ModelDocument,
                        TransitionSystemDocument, WorkflowDocument)

MINE, MODEL, LEARN = "mine", "model", "learn"
SPACES = (MINE, MODEL, LEARN)
LABELS = {MINE: "Mine", MODEL: "Model", LEARN: "Learn"}

#: The folder's file kinds (see :func:`~.workspace.file_kind`) each space lists.
FILE_KINDS = {MINE: ("workflow", "log", "ocel", "ts"), MODEL: ("petri", "cpn", "bpmn")}
#: The file kinds a space's main section is made of; the rest are its material.
MAIN_KINDS = {MINE: ("workflow",), MODEL: ("petri", "cpn", "bpmn")}


def space_of(document) -> str:
    """The space a document lives in: nets in Model, everything else in Mine."""
    if isinstance(document, (ModelDocument, CpnDocument)):
        return MODEL
    return MINE


def space_of_kind(kind: str | None) -> str:
    """The space a file of ``kind`` opens in."""
    return MODEL if kind in ("petri", "cpn", "bpmn") else MINE


def is_main(document, space: str) -> bool:
    """Whether a document sits in the space's main section (analyses or models)
    rather than among its material (logs, transition systems, comparisons)."""
    if space == MODEL:
        return isinstance(document, (ModelDocument, CpnDocument))
    return isinstance(document, WorkflowDocument)


def material_kinds(document) -> bool:
    return isinstance(document, (LogDocument, TransitionSystemDocument, ComparisonDocument))


__all__ = ["MINE", "MODEL", "LEARN", "SPACES", "LABELS", "FILE_KINDS", "MAIN_KINDS",
           "space_of", "space_of_kind", "is_main", "material_kinds"]
