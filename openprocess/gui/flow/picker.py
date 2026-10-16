"""The *+ Add box* popover: every box, a click away, without a panel of its own.

The Workflows page has no permanent box list.  *+ Add box* in its header
(and a double-click on the canvas, and *Add box here…* in its menu) opens
this popover: a search field, then every box, one section per group with
its name on a rule above it (Input, Filter, Discover, Check, Compare, Output,
then Science, Predict, Coloured nets, Sweep and a folder's own boxes), the
boxes of a group in three columns.  The list scrolls when it is taller than
the screen leaves room for, so nothing is folded away and nothing is cut
off.  Typing searches every box; Enter adds the first match.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGridLayout, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ...flow.library import Library
from .. import theme
from ..studio import motion, style
from ..studio.widgets import hbox, label, vbox
from ..studio.workspace import display_name, file_kind

#: How many boxes side by side in a group's section.
COLUMNS = 3
#: The list scrolls beyond this height (the card stays a popover, not a page).
MAX_LIST_HEIGHT = 460
#: How many of the folder's files the Input section lists before "… more".
FILE_LIMIT = 6
#: The input box for each kind of file in the folder (see workspace.file_kind).
INPUT_BOXES = {"log": "open_log", "ocel": "open_ocel", "petri": "open_net", "cpn": "open_cpn",
               "ts": "open_transition_system", "typed": "typed_log"}


def input_box_for(library: Library, kind: str | None) -> str | None:
    """The id of the box that reads files of ``kind`` (None: no such box)."""
    function = INPUT_BOXES.get(kind)
    if function is None:
        return None
    for spec in library.specs.values():
        if spec.id.endswith("." + function) or spec.id == function:
            return spec.id
    return None


def folder_files(folder: str | Path | None, limit: int = 200) -> list[tuple[str, str]]:
    """(relative path, kind) of the folder's logs, nets and transition systems."""
    if folder is None or not Path(folder).is_dir():
        return []
    found = []
    for path in sorted(Path(folder).rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file() or path.name.startswith(".") or any(
                part.startswith(".") for part in path.relative_to(folder).parts[:-1]):
            continue
        kind = file_kind(path)
        if kind in INPUT_BOXES:
            found.append((path.relative_to(folder).as_posix(), kind))
    return found
#: Room around the card for its shadow, inside the see-through popup window.
SHADOW_MARGIN = 24


class BoxPicker(QWidget):
    """A popup listing the library's boxes by group; emits :attr:`chosen` with a box id.

    The window itself is see-through: what shows is :attr:`card`, a rounded
    panel with a soft shadow, so the popover has the corners of the app's
    menus rather than a square box with a hard edge."""

    chosen = Signal(str)
    #: One of the folder's files was picked: the input box's id and the file, relative to the folder.
    file_chosen = Signal(str, str)

    def __init__(self, library: Library, parent: QWidget | None = None,
                 folder: str | Path | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.card = QFrame()
        self.card.setObjectName("boxPicker")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(SHADOW_MARGIN + 8)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110 if theme.is_dark() else 50))
        self.card.setGraphicsEffect(shadow)
        self.library = library
        self.folder = folder
        self.file_buttons: list[QPushButton] = []
        self.search = QLineEdit()
        self.search.setObjectName("boxSearch")
        self.search.setPlaceholderText("Search boxes, e.g. alpha, filter, fitness")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.refill())
        self.search.returnPressed.connect(self._choose_first)
        # The sections, in a list that scrolls when there are more than fit.
        self.groups_host = QWidget()
        self.groups_host.setObjectName("plain")
        self.sections = QVBoxLayout(self.groups_host)
        self.sections.setContentsMargins(0, 0, 8, 0)      # room for the scrollbar
        self.sections.setSpacing(14)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("boxPickerList")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea#boxPickerList, QScrollArea#boxPickerList > QWidget > QWidget "
                                  "{ background: transparent; }")
        self.scroll.setWidget(self.groups_host)
        self.footer = label("", "muted", wrap=True)
        self.card.setLayout(vbox(self.search, self.scroll, self.footer, spacing=12, margins=(16, 16, 16, 16)))
        self.card.setMinimumWidth(600)
        self.setLayout(vbox(self.card, margins=(SHADOW_MARGIN,) * 4))
        self.buttons: list[QPushButton] = []
        self.refill()

    # -- showing ---------------------------------------------------------------------------
    def open_at(self, point: QPoint) -> None:
        """Show the popover with the card's top-left corner at ``point`` (global), kept on screen."""
        self.search.clear()
        self.refill()
        self.adjustSize()
        screen = self.screen().availableGeometry() if self.screen() else None
        width, height = self.card_size()
        x, y = point.x(), point.y()
        if screen is not None:
            x = max(screen.left(), min(x, screen.right() - width))
            y = max(screen.top(), min(y, screen.bottom() - height))
        self.move(x - SHADOW_MARGIN, y - SHADOW_MARGIN)
        self.show()
        motion.fade_in(self)
        self.search.setFocus()

    def card_size(self) -> tuple[int, int]:
        """Width and height of the visible card (the window is larger by the shadow's room)."""
        hint = self.sizeHint()
        return hint.width() - 2 * SHADOW_MARGIN, hint.height() - 2 * SHADOW_MARGIN

    # -- filling -----------------------------------------------------------------------------
    def shown_groups(self) -> dict[str, list]:
        """Group → boxes to list now: every group, in the library's order, or
        what matches the search (a search looks everywhere)."""
        wanted = self.search.text().strip().lower()
        groups = {}
        for group, specs in self.library.by_group().items():
            shown = [s for s in specs if not wanted or _matches(s, group, wanted)]
            if shown:
                groups[group] = shown
        return groups

    def refill(self) -> None:
        while self.sections.count():
            item = self.sections.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()               # gone now, not at the next event loop turn: no ghosts under the new rows
                widget.setParent(None)
                widget.deleteLater()
        self.buttons, self.file_buttons = [], []
        groups = self.shown_groups()
        files, _more = self.shown_files()

        def add(widget: QWidget) -> None:
            self.sections.addWidget(widget)
            widget.show()          # now, not at the next event loop turn: the list is measured below

        for group, specs in groups.items():
            add(self._section(group, specs, with_files=group == "Input"))
        if "Input" not in groups and files:
            # A search that matches files but no box: still a section for them.
            add(self._section("Input", [], with_files=True))
        elif not groups:
            add(label("No box matches. Try another word, or clear the search.", "muted", wrap=True))
        for broken in self.library.broken:
            note = label(f"{Path(broken.file).name}: {broken.reason}", "muted", wrap=True)
            note.setStyleSheet(f"color: {style.STATUS['critical']};")
            note.setToolTip("This box file could not be loaded; fix it and it reloads when saved")
            add(note)
        self.sections.addStretch(1)
        self._refresh_footer()
        # The list takes the height of its sections, up to the limit; past
        # that it scrolls.  (A scroll area's own size hint is a guess.)
        self.groups_host.adjustSize()
        self.scroll.setFixedHeight(min(self.groups_host.sizeHint().height() + 2, MAX_LIST_HEIGHT))
        self.scroll.verticalScrollBar().setValue(0)
        for layout in (self.card.layout(), self.layout()):
            layout.activate()          # the card follows the list's new height at once, not a turn later
        self.adjustSize()

    def _section(self, group: str, specs: list, with_files: bool = False) -> QWidget:
        """One group: its name on a rule, then its boxes in columns (and, for
        Input, the folder's own files under a caption of their own)."""
        t = style.tokens()
        section = QWidget()
        section.setObjectName("plain")
        rows = QVBoxLayout(section)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        rows.addLayout(self._heading(group.upper()))
        if specs:
            grid = self._grid()
            for index, spec in enumerate(specs):
                choice = QPushButton(spec.name + ("  (Yours)" if spec.custom else ""))
                choice.setObjectName("boxChoice")
                choice.setCursor(Qt.PointingHandCursor)
                choice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                tip = (spec.help or "").strip().split("\n\n")[0].replace("\n", " ")
                if not spec.available:
                    tip += f"\n({spec.unavailable_reason})"
                    choice.setStyleSheet(f"color: {t.text_muted};")
                choice.setToolTip(tip)
                choice.clicked.connect(lambda _checked=False, box_id=spec.id: self._choose(box_id))
                grid.addWidget(choice, index // COLUMNS, index % COLUMNS)
                self.buttons.append(choice)
            rows.addLayout(grid)
        if with_files:
            self._add_files(rows)
        return section

    @staticmethod
    def _heading(text: str) -> "QHBoxLayout":
        """A section's name, with a thin rule running to the right edge."""
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {style.tokens().border};")
        rule.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        heading = hbox(label(text, "sectionLabel"), rule, spacing=10)
        heading.setAlignment(rule, Qt.AlignVCenter)
        return heading

    @staticmethod
    def _grid() -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        for column in range(COLUMNS):
            grid.setColumnStretch(column, 1)
        return grid

    def shown_files(self) -> tuple[list[tuple[str, str]], int]:
        """The folder's files to list under Input (those with an input box),
        and how many more there are.  A search looks through all of them."""
        wanted = self.search.text().strip().lower()
        files = [(relative, kind) for relative, kind in folder_files(self.folder)
                 if input_box_for(self.library, kind) is not None
                 and all(word in relative.lower() for word in wanted.split())]
        limit = 12 if wanted else FILE_LIMIT
        return files[:limit], max(0, len(files) - limit)

    def _add_files(self, rows: QVBoxLayout) -> None:
        """Under the Input boxes: the folder's files, one click each."""
        files, more = self.shown_files()
        if not files:
            return
        from ..studio.app import _faded_icon            # the sidebar's file icons (no cycle: app is loaded)
        rows.addSpacing(8)
        rows.addWidget(label("IN THIS FOLDER", "sectionLabel"))
        names = [Path(relative).name for relative, _kind in files]
        icons = {"log": "log", "ocel": "log", "cpn": "cpn", "ts": "ts", "petri": "model"}
        grid = self._grid()
        for index, (relative, kind) in enumerate(files):
            name = Path(relative).name
            text = display_name(name)
            if names.count(name) > 1 and "/" in relative:
                text += f"  ({Path(relative).parent})"
            choice = QPushButton(text)
            choice.setObjectName("boxChoice")
            choice.setIcon(_faded_icon(icons.get(kind, "model")))
            choice.setCursor(Qt.PointingHandCursor)
            choice.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            choice.setToolTip(f"{relative}\nAdds an input box that reads this file")
            choice.clicked.connect(lambda _checked=False, r=relative, k=kind: self._choose_file(k, r))
            grid.addWidget(choice, index // COLUMNS, index % COLUMNS)
            self.file_buttons.append(choice)
        rows.addLayout(grid)
        if more:
            rows.addWidget(label(f"… {more} more (Choose… in Settings)", "muted"))

    def _choose_file(self, kind: str, relative: str) -> None:
        box_id = input_box_for(self.library, kind)
        if box_id is None:
            return
        self.close()
        self.file_chosen.emit(box_id, relative)

    def _refresh_footer(self) -> None:
        total = len(self.library)
        shown = len(self.buttons)
        if self.search.text().strip():
            self.footer.setText(f"{shown} of {total} boxes match. Return adds the first.")
        else:
            self.footer.setText(f"All {total} boxes, by group. Type to search them; Return adds the first match.")

    # -- choosing ---------------------------------------------------------------------------
    def _choose(self, box_id: str) -> None:
        self.close()
        self.chosen.emit(box_id)

    def _choose_first(self) -> None:
        if self.buttons:
            self.buttons[0].click()


def _matches(spec, group: str, wanted: str) -> bool:
    """Every word typed is in the box's name, its group, or its function's
    name ("alpha" finds the α-algorithm, whose function is alpha_miner)."""
    haystack = " ".join([spec.name, group, spec.function.__name__.replace("_", " "), spec.id]).lower()
    return all(word in haystack for word in wanted.split())


__all__ = ["BoxPicker", "folder_files", "input_box_for"]
