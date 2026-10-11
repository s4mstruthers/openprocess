"""The answer boxes of a worksheet (see :mod:`openprocess.learn.sheet`).

Every answer block of ``question.md`` becomes a :class:`TaskCard`: the box to
answer in (an *editor*, one per type), a **Check** button, and *Hint* and
*Show answer* when the block has them.  Editors share a small interface:

* ``value()`` -- the answer as stored in ``my answers.json`` (JSON);
* ``set_value(value)`` -- put a saved answer back;
* ``changed`` -- emitted on every edit (the card saves and clears old feedback);
* ``show_result(result)`` -- mark what was wrong (the footprint's cells).

The card does not check anything itself: it asks (``check_requested``) and
the exercise view answers with :meth:`TaskCard.show_result`.
"""

from __future__ import annotations

import re
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ...learn import answers, notation
from ...learn.checks import CORRECT, INCORRECT, PARTIAL, UNKNOWN
from ..studio.markdown_view import MarkdownLabel
from ..studio.widgets import Chip, button, hbox, label

#: What the card's caption says above each kind of box.
CAPTIONS = {
    "net": "Draw your net", "footprint": "Your footprint", "yesno": "Your answer",
    "choice": "Your answer", "set": "Your answer", "trace": "Your firing sequence",
    "number": "Your answer", "text": "Your answer", "open": "Your answer",
    "marking": "Your marking", "markings": "Your markings", "tuple": "Your net as (P, T, F, m₀)",
    "ts": "Your transition system", "matrix": "Your matrix", "cut": "Your cut",
    "log": "Your log", "tree": "Your process tree", "replay": "Your replay",
    "alignment": "Your alignment", "ranking": "Your ranking", "predict": "Your prediction",
    "workflow": "Build your workflow",
}

#: Status of a task -> (chip text, status colour key).
STATUS = {
    CORRECT: ("Correct", "good"), PARTIAL: ("Almost", "warning"),
    INCORRECT: ("Not yet", "critical"), "done": ("Done", "good"),
}


def inline_markdown(text: str) -> str:
    """``*x*``, ``**x**`` and `` `x` `` as rich text (option labels, captions)."""
    html = escape(text)
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
    html = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", html)
    html = re.sub(r"`(.+?)`", r"<code>\1</code>", html)
    return html


# ---------------------------------------------------------------------------
# Editors
# ---------------------------------------------------------------------------
class Editor(QWidget):
    changed = Signal()

    def value(self):
        return None

    def set_value(self, value) -> None:
        pass

    def show_result(self, result) -> None:
        pass


class LineEditor(Editor):
    """One line of text: sets, numbers, short answers, traces."""

    def __init__(self, placeholder: str, preview=None, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setObjectName("answerLine")
        self.edit.setPlaceholderText(placeholder)
        self.edit.textEdited.connect(lambda _text: self._edited())
        self.row = hbox(self.edit, spacing=6)
        layout.addLayout(self.row)
        #: preview(text) -> (shown, ok): how the answer is read, as you type.
        self.preview = preview
        self.reading = label("", "answerReading", wrap=True)
        self.reading.setHidden(True)
        layout.addWidget(self.reading)

    def _edited(self) -> None:
        self._show_reading()
        self.changed.emit()

    def _show_reading(self) -> None:
        if self.preview is None:
            return
        text = self.edit.text()
        if not text.strip():
            self.reading.setHidden(True)
            return
        shown, ok = self.preview(text)
        self.reading.setText(shown)
        self.reading.setProperty("ok", ok)
        self.reading.style().unpolish(self.reading)
        self.reading.style().polish(self.reading)
        self.reading.setHidden(False)

    def value(self):
        return self.edit.text()

    def set_value(self, value) -> None:
        self.edit.setText("" if value is None else str(value))
        self._show_reading()


def set_preview(text: str) -> tuple[str, bool]:
    try:
        items = answers.parse(text)
    except answers.AnswerSyntaxError as error:
        return f"Cannot read this yet: {error}.", False
    if len(items) == 1 and isinstance(items[0], frozenset):
        shown = answers.show(items[0])
    else:
        shown = answers.show(frozenset(items))
    return f"Read as {shown}", True


def trace_preview(text: str) -> tuple[str, bool]:
    try:
        steps = answers.trace(text)
    except answers.AnswerSyntaxError as error:
        return f"Cannot read this yet: {error}.", False
    return f"{len(steps)} step{'s' * (len(steps) != 1)}: " + " → ".join(steps), True


def number_preview(text: str) -> tuple[str, bool]:
    try:
        value = answers.number(text)
    except (ValueError, ZeroDivisionError):
        return "Not a number yet.", False
    return f"Read as {value:g}", True


class OptionRow(QFrame):
    """A choice option: a radio button or check box with rich text, all clickable."""

    def __init__(self, text: str, multiple: bool, group: QButtonGroup) -> None:
        super().__init__()
        self.setObjectName("choiceOption")
        self.setCursor(Qt.PointingHandCursor)
        self.indicator = QCheckBox() if multiple else QRadioButton()
        self.indicator.setCursor(Qt.PointingHandCursor)
        group.addButton(self.indicator)
        text_label = QLabel(inline_markdown(text))
        text_label.setWordWrap(True)
        text_label.setTextFormat(Qt.RichText)
        text_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(10)
        layout.addWidget(self.indicator, 0, Qt.AlignTop)
        layout.addWidget(text_label, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.indicator.click()
        super().mousePressEvent(event)


class ChoiceEditor(Editor):
    def __init__(self, options, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.multiple = sum(o.correct for o in options) > 1
        self.group = QButtonGroup(self)
        self.group.setExclusive(not self.multiple)
        self.rows = []
        for index, option in enumerate(options):
            row = OptionRow(option.text, self.multiple, self.group)
            self.group.setId(row.indicator, index)
            layout.addWidget(row)
            self.rows.append(row)
        if self.multiple:
            layout.addWidget(label("More than one is right: tick all of them.", "muted"))
        self.group.buttonToggled.connect(lambda *_: self.changed.emit())

    def value(self):
        chosen = [i for i, row in enumerate(self.rows) if row.indicator.isChecked()]
        return chosen if self.multiple else (chosen[0] if chosen else None)

    def set_value(self, value) -> None:
        chosen = set(value if isinstance(value, list) else [] if value is None else [value])
        self.group.blockSignals(True)
        for index, row in enumerate(self.rows):
            row.indicator.setChecked(index in chosen)
        self.group.blockSignals(False)


class YesNoEditor(Editor):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.buttons = {}
        row = hbox(spacing=6)
        for key, text in (("yes", "Yes"), ("no", "No")):
            pill = QPushButton(text)
            pill.setObjectName("choicePill")
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            self.group.addButton(pill)
            self.buttons[key] = pill
            row.addWidget(pill)
        row.addStretch(1)
        self.setLayout(row)
        self.group.buttonClicked.connect(lambda _b: self.changed.emit())

    def value(self):
        return next((key for key, pill in self.buttons.items() if pill.isChecked()), None)

    def set_value(self, value) -> None:
        if value in self.buttons:
            self.buttons[value].setChecked(True)


class TextEditor(Editor):
    """A few lines of free text (``open``)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("answerText")
        self.edit.setPlaceholderText("Write your answer here.")
        self.edit.setFixedHeight(96)
        self.edit.textChanged.connect(self.changed.emit)
        layout.addWidget(self.edit)

    def value(self):
        return self.edit.toPlainText()

    def set_value(self, value) -> None:
        self.edit.blockSignals(True)
        self.edit.setPlainText(value or "")
        self.edit.blockSignals(False)


#: Click order of a footprint cell, and the keys that set each relation.
SYMBOLS = ["→", "←", "‖", "#"]
KEYS = {">": "→", "<": "←", "|": "‖", "#": "#", "=": "#", "p": "‖"}


class FootprintCell(QToolButton):
    def __init__(self, editor: "FootprintEditor", row: str, column: str) -> None:
        super().__init__()
        self.setObjectName("footprintCell")
        self.editor = editor
        self.row, self.column = row, column
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 32)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(f"{row} and {column}: click to change (or type > < | #)")
        self.symbol = ""
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def set_symbol(self, symbol: str, wrong: bool = False) -> None:
        self.symbol = symbol
        self.setText(symbol)
        self.set_wrong(wrong)

    def set_wrong(self, wrong: bool) -> None:
        if self.property("wrong") != wrong:
            self.setProperty("wrong", wrong)
            self.style().unpolish(self)
            self.style().polish(self)

    def _menu(self, position) -> None:
        menu = QMenu(self)
        for symbol, meaning in zip(SYMBOLS, ("causality", "inverse causality",
                                             "parallel", "choice (never next to each other)")):
            menu.addAction(f"{symbol}   {meaning}", lambda s=symbol: self.editor.choose(self, s))
        menu.addSeparator()
        menu.addAction("Clear", lambda: self.editor.choose(self, ""))
        menu.exec(self.mapToGlobal(position))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        text = event.text()
        if text in KEYS:
            self.editor.choose(self, KEYS[text])
        elif event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            self.editor.choose(self, "")
        else:
            super().keyPressEvent(event)


class FootprintEditor(Editor):
    """A grid of activities × activities; each cell cycles → ← ‖ #."""

    def __init__(self, activities: list[str], parent=None) -> None:
        super().__init__(parent)
        self.activities = activities
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        grid_host = QWidget()
        grid_host.setObjectName("footprintGrid")
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(3)
        self.grid.setVerticalSpacing(3)
        self.cells: dict[tuple[str, str], FootprintCell] = {}
        for index, activity in enumerate(activities):
            top = label(escape(activity), "footprintHeading")
            top.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(top, 0, index + 1)
            side = label(escape(activity), "footprintHeading")
            side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.grid.addWidget(side, index + 1, 0)
        for r, a in enumerate(activities):
            for c, b in enumerate(activities):
                cell = FootprintCell(self, a, b)
                cell.clicked.connect(lambda _=False, cell=cell: self.cycle(cell))
                self.grid.addWidget(cell, r + 1, c + 1)
                self.cells[(a, b)] = cell
        outer.addLayout(hbox(grid_host, None))
        outer.addWidget(label("Click a cell to change it (→ ← ‖ #), or type > < | # on "
                              "it.", "muted", wrap=True))
        fill = button("Fill the empty cells with #", self.fill_choice, kind="ghost")
        outer.addLayout(hbox(fill, None))

    def cycle(self, cell: FootprintCell) -> None:
        index = SYMBOLS.index(cell.symbol) + 1 if cell.symbol in SYMBOLS else 0
        self.choose(cell, SYMBOLS[index] if index < len(SYMBOLS) else "")

    def choose(self, cell: FootprintCell, symbol: str) -> None:
        cell.set_symbol(symbol)
        self.changed.emit()

    def fill_choice(self) -> None:
        for cell in self.cells.values():
            if not cell.symbol:
                cell.set_symbol("#")
        self.changed.emit()

    def value(self):
        return {f"{a}\t{b}": cell.symbol for (a, b), cell in self.cells.items() if cell.symbol}

    def set_value(self, value) -> None:
        value = value if isinstance(value, dict) else {}
        for (a, b), cell in self.cells.items():
            cell.set_symbol(value.get(f"{a}\t{b}", ""))

    def show_result(self, result) -> None:
        wrong = set(result.wrong_cells) if result is not None else set()
        for key, cell in self.cells.items():
            cell.set_wrong(key in wrong and bool(cell.symbol))


class NetEditor(Editor):
    """The net is drawn in the editor beside the sheet; here only a pointer to it."""

    go_to_net = Signal()

    def __init__(self, tab_name: str, parent=None) -> None:
        super().__init__(parent)
        layout = hbox(spacing=8)
        self.setLayout(layout)
        layout.addWidget(label(f"Draw it in <b>{escape(tab_name)}</b>, on the right. "
                               "It is saved as you go.", "muted", wrap=True), 1)
        layout.addWidget(button(f"Show {tab_name}", self.go_to_net.emit))


class WorkflowEditor(Editor):
    """The workflow is built on the canvas beside the sheet; here a pointer to it."""

    go_to_workflow = Signal()

    def __init__(self, tab_name: str = "Workflow", parent=None) -> None:
        super().__init__(parent)
        layout = hbox(spacing=8)
        self.setLayout(layout)
        layout.addWidget(label(f"Build it in <b>{escape(tab_name)}</b>, on the right: drag boxes "
                               "from the list and wire them. It is saved as you go.",
                               "muted", wrap=True), 1)
        layout.addWidget(button(f"Show {tab_name}", self.go_to_workflow.emit))


# ---------------------------------------------------------------------------
# Notations typed in a text box (markings, cuts, trees, logs, transition
# systems, alignments): several lines, read back as you type
# ---------------------------------------------------------------------------
class NotationEditor(Editor):
    """A few lines in one of the course's notations, with how it is read below."""

    def __init__(self, placeholder: str, preview, lines: int = 3, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("answerText")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setTabChangesFocus(True)
        self.edit.setFixedHeight(26 + 20 * lines)
        self.edit.textChanged.connect(self._edited)
        layout.addWidget(self.edit)
        self.preview = preview
        self.reading = label("", "answerReading", wrap=True)
        self.reading.setHidden(True)
        layout.addWidget(self.reading)

    def _edited(self) -> None:
        self._show_reading()
        self.changed.emit()

    def _show_reading(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            self.reading.setHidden(True)
            return
        shown, ok = self.preview(text)
        self.reading.setText(shown)
        self.reading.setProperty("ok", ok)
        self.reading.style().unpolish(self.reading)
        self.reading.style().polish(self.reading)
        self.reading.setHidden(False)

    def value(self):
        return self.edit.toPlainText()

    def set_value(self, value) -> None:
        self.edit.blockSignals(True)
        self.edit.setPlainText("" if value is None else str(value))
        self.edit.blockSignals(False)
        self._show_reading()


def _reading(reader, show):
    """A preview function from a notation reader and how to write it back."""
    def preview(text: str) -> tuple[str, bool]:
        try:
            value = reader(text)
        except answers.AnswerSyntaxError as error:
            return f"Cannot read this yet: {error}.", False
        except ValueError as error:
            return f"Cannot read this yet: {error}.", False
        return "Read as " + show(value), True
    return preview


def _ts_preview(text: str) -> tuple[str, bool]:
    from ...mining.transition_system import parse_transition_system
    try:
        ts = parse_transition_system(text)
    except ValueError as error:
        return f"Cannot read this yet: {error}.", False
    states, arcs = len(ts.reachable()), len(ts.transitions)
    initial = ", ".join(ts.initial) if ts.initial else "none (write initial: s0)"
    return (f"Read as {states} reachable state{'s' * (states != 1)}, {arcs} "
            f"transition{'s' * (arcs != 1)}; initial: {initial}"), True


def _alignment_preview(text: str) -> tuple[str, bool]:
    try:
        top, bottom = notation.alignment(text)
    except answers.AnswerSyntaxError as error:
        return f"Cannot read this yet: {error}.", False
    kinds = {"sync": 0, "log": 0, "model": 0}
    for a, b in zip(top, bottom):
        kinds["log" if b == notation.SKIP else "model" if a == notation.SKIP else "sync"] += 1
    return (f"Read as {len(top)} moves: {kinds['sync']} synchronous, {kinds['log']} log only "
            f"(≫ below), {kinds['model']} model only (≫ above)"), True


NOTATIONS = {
    "marking": ("e.g. [p1, p4^2]  (p4 twice), or [] for the empty marking", 1,
                _reading(notation.marking, notation.show_marking)),
    "markings": ("one marking per item, e.g. [p1], [p2, p3], [p4]", 2,
                 _reading(notation.markings, notation.show_markings)),
    "cut": ("the operator, then the groups: → {a} {b, c, e} {d}", 1,
            _reading(notation.cut, notation.show_cut)),
    "tree": ("e.g. →(a, ×(∧(b, c), e), d)   (or seq, xor, and, loop)", 2,
             _reading(notation.tree, str)),
    "log": ("e.g. [<a, b, c>^2, <a, c>]", 2, _reading(notation.log, notation.show_log)),
    "ts": ("one line per arc: s0 -a-> s1\n…\ninitial: s0", 5, _ts_preview),
    "alignment": ("the log moves on the first line, the model moves on the second; "
                  "≫ (or >>) where there is no move", 2, _alignment_preview),
}


# ---------------------------------------------------------------------------
# (P, T, F, m0): four boxes
# ---------------------------------------------------------------------------
TUPLE_HINTS = {"P": "the places: {p1, p2, …}", "T": "the transitions: {a, b, …}",
               "F": "the arcs as pairs: (p1, a), (a, p2), …", "m0": "the initial marking: [p1]"}


class TupleEditor(Editor):
    """``(P, T, F, m₀)``: one line each, checked part by part."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        self.fields: dict[str, QLineEdit] = {}
        self.marks: dict[str, QLabel] = {}
        for row, key in enumerate(notation.TUPLE_FIELDS):
            name = label({"m0": "m₀"}.get(key, key) + " =", "tupleName")
            name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            edit = QLineEdit()
            edit.setObjectName("answerLine")
            edit.setPlaceholderText(TUPLE_HINTS[key])
            edit.textEdited.connect(lambda _t: self._edited())
            mark = label("", "tupleMark")
            mark.setFixedWidth(18)
            grid.addWidget(name, row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(mark, row, 2)
            self.fields[key] = edit
            self.marks[key] = mark

    def _edited(self) -> None:
        self.show_result(None)
        self.changed.emit()

    def value(self):
        return {key: edit.text() for key, edit in self.fields.items()}

    def set_value(self, value) -> None:
        value = value if isinstance(value, dict) else {}
        for key, edit in self.fields.items():
            edit.setText(str(value.get(key, "") or ""))

    def show_result(self, result) -> None:
        parts = getattr(result, "parts", None) or {}
        for key, mark in self.marks.items():
            verdict = parts.get(key)
            mark.setText({CORRECT: "✓", PARTIAL: "◐", INCORRECT: "✕"}.get(verdict, ""))
            mark.setProperty("state", {CORRECT: "good", PARTIAL: "warning",
                                       INCORRECT: "critical"}.get(verdict, ""))
            mark.style().unpolish(mark)
            mark.style().polish(mark)


# ---------------------------------------------------------------------------
# Grids of numbers: a matrix, a replay table
# ---------------------------------------------------------------------------
class GridCell(QLineEdit):
    def __init__(self, editor, key) -> None:
        super().__init__()
        self.setObjectName("gridCell")
        self.key = key
        self.setFixedSize(54, 30)
        self.setAlignment(Qt.AlignCenter)
        self.textEdited.connect(lambda _t: editor._cell_edited(self))

    def set_wrong(self, wrong: bool) -> None:
        if self.property("wrong") != wrong:
            self.setProperty("wrong", wrong)
            self.style().unpolish(self)
            self.style().polish(self)


class MatrixEditor(Editor):
    """A grid with the rows and columns given (an incidence matrix, M or M′)."""

    def __init__(self, rows: list[str], columns: list[str], parent=None) -> None:
        super().__init__(parent)
        self.rows, self.columns = list(rows), list(columns)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        host = QWidget()
        host.setObjectName("footprintGrid")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(3)
        for index, column in enumerate(self.columns):
            top = label(escape(column), "footprintHeading")
            top.setAlignment(Qt.AlignCenter)
            grid.addWidget(top, 0, index + 1)
        self.cells: dict[tuple[str, str], GridCell] = {}
        for r, row in enumerate(self.rows):
            side = label(escape(row), "footprintHeading")
            side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(side, r + 1, 0)
            for c, column in enumerate(self.columns):
                cell = GridCell(self, (row, column))
                grid.addWidget(cell, r + 1, c + 1)
                self.cells[(row, column)] = cell
        outer.addLayout(hbox(host, None))
        outer.addWidget(label("A number in every cell (0 where nothing happens). Tab moves "
                              "to the next cell.", "muted", wrap=True))

    def _cell_edited(self, cell) -> None:
        cell.set_wrong(False)
        self.changed.emit()

    def value(self):
        return {"rows": self.rows, "columns": self.columns,
                "cells": {f"{r}\t{c}": cell.text() for (r, c), cell in self.cells.items()
                          if cell.text().strip()}}

    def set_value(self, value) -> None:
        cells = (value or {}).get("cells", {}) if isinstance(value, dict) else {}
        for (r, c), cell in self.cells.items():
            cell.setText(str(cells.get(f"{r}\t{c}", "")))

    def show_result(self, result) -> None:
        wrong = {(answers.name(r), answers.name(c))
                 for r, c in (result.wrong_cells if result is not None else [])}
        for (r, c), cell in self.cells.items():
            cell.set_wrong((answers.name(r), answers.name(c)) in wrong)


class ReplayEditor(Editor):
    """Produced, consumed, missing and remaining tokens, one row per trace."""

    def __init__(self, traces: list[tuple[str, ...]], parent=None) -> None:
        super().__init__(parent)
        self.traces = [tuple(t) for t in traces]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        host = QWidget()
        host.setObjectName("footprintGrid")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(3)
        for index, column in enumerate(notation.REPLAY_COLUMNS):
            top = label(column, "footprintHeading")
            top.setToolTip({"p": "produced", "c": "consumed", "m": "missing",
                            "r": "remaining"}[column])
            top.setAlignment(Qt.AlignCenter)
            grid.addWidget(top, 0, index + 1)
        self.cells: dict[tuple[tuple[str, ...], str], GridCell] = {}
        for r, trace in enumerate(self.traces):
            side = label(escape("⟨" + ", ".join(trace) + "⟩"), "footprintHeading")
            side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(side, r + 1, 0)
            for c, column in enumerate(notation.REPLAY_COLUMNS):
                cell = GridCell(self, (trace, column))
                grid.addWidget(cell, r + 1, c + 1)
                self.cells[(trace, column)] = cell
        outer.addLayout(hbox(host, None))
        outer.addWidget(label("p produced, c consumed, m missing, r remaining; count the "
                              "token in the source and the sink too.", "muted", wrap=True))

    def _cell_edited(self, cell) -> None:
        cell.set_wrong(False)
        self.changed.emit()

    @staticmethod
    def _key(trace) -> str:
        return "⟨" + ", ".join(trace) + "⟩"

    def value(self):
        rows: dict[str, dict] = {}
        for (trace, column), cell in self.cells.items():
            if cell.text().strip():
                rows.setdefault(self._key(trace), {})[column] = cell.text()
        return rows

    def set_value(self, value) -> None:
        value = value if isinstance(value, dict) else {}
        for (trace, column), cell in self.cells.items():
            row = value.get(self._key(trace)) or {}
            cell.setText(str(row.get(column, "") if isinstance(row, dict) else ""))

    def show_result(self, result) -> None:
        wrong = set()
        for shown, column in (result.wrong_cells if result is not None else []):
            try:
                wrong.add((tuple(answers.name(a) for a in answers.trace(shown)), column))
            except answers.AnswerSyntaxError:
                continue
        for (trace, column), cell in self.cells.items():
            cell.set_wrong((tuple(answers.name(a) for a in trace), column) in wrong)


# ---------------------------------------------------------------------------
# A ranking: the names in order, best first
# ---------------------------------------------------------------------------
class RankingEditor(Editor):
    """The candidates as a list to reorder (▲ ▼, or drag), best first."""

    def __init__(self, candidates: list[str], parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractItemView, QListWidget
        self.candidates = list(candidates)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setObjectName("rankingList")
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setFixedHeight(10 + 30 * max(1, len(self.candidates)))
        for name in self.candidates:
            self.list.addItem(name)
        self.list.model().rowsMoved.connect(lambda *_: self._moved())
        buttons = hbox(button("▲ Up", lambda: self.move(-1), kind="ghost"),
                       button("▼ Down", lambda: self.move(1), kind="ghost"),
                       label("Best first: 1 is the highest.", "muted"), None, spacing=6)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        self._renumber()

    def _renumber(self) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            name = item.data(Qt.UserRole) or item.text().split(". ", 1)[-1]
            item.setData(Qt.UserRole, name)
            item.setText(f"{index + 1}. {name}")

    def _moved(self) -> None:
        self._renumber()
        self.changed.emit()

    def move(self, delta: int) -> None:
        row = self.list.currentRow()
        if row < 0 or not 0 <= row + delta < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + delta, item)
        self.list.setCurrentRow(row + delta)
        self._moved()

    def value(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def set_value(self, value) -> None:
        if not isinstance(value, list) or sorted(map(str, value)) != sorted(self.candidates):
            return
        self.list.clear()
        for name in value:
            self.list.addItem(str(name))
        self._renumber()


# ---------------------------------------------------------------------------
# The card around an editor
# ---------------------------------------------------------------------------
class TaskCard(QFrame):
    """One answer box with its Check, Hint and Show answer."""

    check_requested = Signal(object)          # the task
    changed = Signal(object)                  # the task (save the answer)
    solution_requested = Signal(object)       # the task (the card asks for the text)

    def __init__(self, task, editor: Editor, folder=None, parent=None,
                 exam: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
        self.task = task
        self.editor = editor
        self.folder = folder
        self.status: str | None = None
        #: The share of the block's points the last check gave (for partial answers).
        self.share: float = 0.0
        #: In an exam there is no hint, no answer, and Check only says if it is right.
        self.exam = exam
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(9)

        caption = CAPTIONS.get(task.type, "Your answer")
        if task.points != 1:
            caption += f"  ·  {task.points:g} points"
        self.caption = label(caption.upper(), "taskCaption")
        self.chip = Chip()
        self.chip.setHidden(True)
        layout.addLayout(hbox(self.caption, None, self.chip))
        layout.addWidget(editor)

        self.check_button = button("Check", lambda: self.check_requested.emit(self.task),
                                   kind="primary")
        self.check_button.setVisible(task.checkable)
        self.hint_button = button("Hint", self.toggle_hint, kind="ghost")
        self.hint_button.setVisible(bool(task.hint) and not exam)
        answer_text = "Show model answer" if task.type == "open" else "Show answer"
        self.answer_button = button(answer_text, self.reveal_solution, kind="ghost")
        # A drawn net or a free answer has a model answer only when the author wrote one.
        self.answer_button.setVisible((task.type not in ("net", "open") or bool(task.solution))
                                      and not exam)
        footer = hbox(self.check_button, self.hint_button, self.answer_button, None, spacing=6)
        layout.addLayout(footer)

        self.feedback = label("", "feedback", wrap=True)
        self.feedback.setTextFormat(Qt.RichText)
        self.feedback.setHidden(True)
        layout.addWidget(self.feedback)
        #: Extra detail under the feedback (a net comparison).
        self.detail_host = QVBoxLayout()
        self.detail_host.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.detail_host)

        self.hint_label = self._note("Hint", task.hint or "")
        layout.addWidget(self.hint_label)
        self.solution_label = self._note("Answer", "")
        layout.addWidget(self.solution_label)
        # Open questions: after the model answer, say how you did.
        self.assess_row = QWidget()
        self.assess_row.setLayout(hbox(label("How did you do?", "muted"),
                                       button("I had it", lambda: self.assess(True)),
                                       button("Not quite", lambda: self.assess(False)),
                                       None, spacing=6))
        self.assess_row.setHidden(True)
        layout.addWidget(self.assess_row)

        editor.changed.connect(self._edited)

    def _note(self, title: str, text: str) -> QFrame:
        note = QFrame()
        note.setObjectName("taskNote")
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(10, 8, 10, 9)
        note_layout.setSpacing(3)
        note_layout.addWidget(label(title.upper(), "taskCaption"))
        note.body = MarkdownLabel(text, self.folder)
        note_layout.addWidget(note.body)
        note.setHidden(True)
        return note

    # -- feedback ------------------------------------------------------------------------
    def _edited(self) -> None:
        # Feedback is about the answer as it was: it goes when you change it.
        if self.status in (CORRECT, PARTIAL, INCORRECT) or not self.feedback.isHidden():
            self.set_status(None)
            self.feedback.setHidden(True)
            self._clear_detail()
            self.editor.show_result(None)
        self.changed.emit(self.task)

    def set_status(self, status: str | None) -> None:
        self.status = status
        shown = STATUS.get(status)
        self.chip.setHidden(shown is None)
        if shown is not None:
            text, colour = shown
            self.chip.setText(("✓ " if colour == "good" else "") + text)
            self.chip.set_state(colour)
        self.setProperty("state", (shown or ("", ""))[1])
        self.style().unpolish(self)
        self.style().polish(self)

    def show_checking(self) -> None:
        self.feedback.setText("Checking…")
        self.feedback.setProperty("state", "")
        self._repolish(self.feedback)
        self.feedback.setHidden(False)
        self.check_button.setEnabled(False)

    def lock(self) -> None:
        """The exam is over: the answer stays as it is and can still be read."""
        self.editor.setEnabled(False)
        self.check_button.setEnabled(False)
        self.assess_row.setHidden(True)

    def show_result(self, result, detail: QWidget | None = None) -> None:
        self.check_button.setEnabled(True)
        self._clear_detail()
        status = result.status
        self.share = float(getattr(result, "share", 0.0) or 0.0)
        icon = {CORRECT: "✓", PARTIAL: "◐", INCORRECT: "✕"}.get(status, "")
        self.feedback.setText(f"<b>{icon}</b>&nbsp; {escape(result.message)}" if icon
                              else escape(result.message))
        self.feedback.setProperty("state", STATUS.get(status, ("", ""))[1])
        self._repolish(self.feedback)
        self.feedback.setHidden(False)
        self.set_status(status if status != UNKNOWN else self.status)
        self.editor.show_result(result)
        if detail is not None:
            self.detail_host.addWidget(detail)

    def _clear_detail(self) -> None:
        while self.detail_host.count():
            item = self.detail_host.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    @staticmethod
    def _repolish(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # -- hint and answer -------------------------------------------------------------------
    def toggle_hint(self) -> None:
        self.hint_label.setHidden(not self.hint_label.isHidden())
        self.hint_button.setText("Hide hint" if not self.hint_label.isHidden() else "Hint")

    def reveal_solution(self) -> None:
        self.solution_requested.emit(self.task)

    def show_solution(self, text: str) -> None:
        if not text:
            text = "No model answer for this one."
        self.solution_label.body.set_markdown(text)
        self.solution_label.setHidden(False)
        self.answer_button.setHidden(True)
        if self.task.type == "open" and self.status != "done":
            self.assess_row.setHidden(False)

    def assess(self, had_it: bool) -> None:
        self.assess_row.setHidden(True)
        self.set_status("done" if had_it else INCORRECT)
        self.changed.emit(self.task)


def make_editor(task, activities=None, net_tab: str = "Your net", shape=None,
                kind: str | None = None) -> Editor:
    """The editor for ``task``.  ``activities`` are the footprint's; ``shape`` is
    what a grid needs (a matrix's rows and columns, a replay's traces, a
    ranking's candidates); ``kind`` overrides the task's type (a prediction
    answered as a set, a number…)."""
    kind = kind or task.type
    if kind == "set":
        return LineEditor("e.g. {a, b}", set_preview)
    if kind == "trace":
        return LineEditor("e.g. register, send letter", trace_preview)
    if kind == "number":
        return LineEditor("a number, e.g. 0.75 or 3/4", number_preview)
    if kind == "text":
        return LineEditor("your answer")
    if kind == "choice":
        return ChoiceEditor(task.options)
    if kind == "yesno":
        return YesNoEditor()
    if kind == "footprint":
        return FootprintEditor(activities or [])
    if kind == "net":
        return NetEditor(net_tab)
    if kind == "workflow":
        return WorkflowEditor()
    if kind in NOTATIONS:
        placeholder, lines, preview = NOTATIONS[kind]
        return NotationEditor(placeholder, preview, lines)
    if kind == "tuple":
        return TupleEditor()
    if kind == "matrix":
        if shape is None:
            return NotationEditor("the column names on the first line, then one row per line:\n"
                                  ".   a   b\np1  -1  1", _reading(notation.matrix,
                                                                     notation.show_matrix), 4)
        rows, columns = shape
        return MatrixEditor(rows, columns)
    if kind == "replay":
        return ReplayEditor(shape or [])
    if kind == "ranking":
        return RankingEditor(shape or [])
    if kind == "predict":
        from ...learn.checks import _predict_kind
        return make_editor(task, activities, net_tab, shape, _predict_kind(task))
    return TextEditor()


def debounce(parent, delay: int, slot) -> QTimer:
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(delay)
    timer.timeout.connect(slot)
    return timer
