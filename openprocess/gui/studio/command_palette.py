"""The command palette: one key, type, Enter.

⌘K (Ctrl+K) opens a small window with a search field.  Typing finds, all at
once, the open analyses, models and logs, the folder's files, every box (to
add to the current analysis), the three spaces and Connections, and every
menu action by its name.  Enter runs the first match; ↑ ↓ pick another.  It
fits the calm design better than more menus: nothing is added to the
window, and anything in it is a few keystrokes away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QLineEdit, QListWidget, QListWidgetItem,
                               QWidget)
from PySide6.QtGui import QColor

from .. import theme
from . import motion, style
from .widgets import label, vbox

SHADOW_MARGIN = 24
LIMIT = 40
#: The order kinds come in when nothing is typed, and their captions.
KINDS = ("Analysis", "Model", "Log", "Comparison", "Transition system", "File", "Space", "Box", "Action")


@dataclass
class Entry:
    kind: str
    title: str
    detail: str = ""
    run: Callable[[], None] = lambda: None
    keywords: str = ""
    #: Lower is earlier when nothing is typed.
    rank: int = field(default=0)

    @property
    def haystack(self) -> str:
        return f"{self.title} {self.detail} {self.keywords} {self.kind}".lower()


def match(entries: list[Entry], query: str, limit: int = LIMIT) -> list[Entry]:
    """The entries every word of ``query`` is in, best first: a title that
    starts with the query, then a title that contains it, then the rest."""
    words = query.lower().split()
    if not words:
        return sorted(entries, key=lambda e: (KINDS.index(e.kind) if e.kind in KINDS else len(KINDS), e.rank,
                                              e.title.lower()))[:limit]
    scored = []
    for entry in entries:
        hay = entry.haystack
        if not all(word in hay for word in words):
            continue
        title = entry.title.lower()
        score = 0 if title.startswith(query.lower()) else 1 if query.lower() in title else \
            2 if all(word in title for word in words) else 3
        scored.append((score, KINDS.index(entry.kind) if entry.kind in KINDS else len(KINDS), entry.rank,
                       title, entry))
    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored[:limit]]


class CommandPalette(QWidget):
    """A popup: a search field and the matches; emits :attr:`ran` with the entry it ran."""

    ran = Signal(object)

    def __init__(self, entries: list[Entry], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.entries = entries
        self.card = QFrame()
        self.card.setObjectName("boxPicker")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(SHADOW_MARGIN + 8)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110 if theme.is_dark() else 50))
        self.card.setGraphicsEffect(shadow)
        self.search = QLineEdit()
        self.search.setObjectName("boxSearch")
        self.search.setPlaceholderText("Type to find an analysis, a file, a box, a space or an action")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refill())
        self.search.returnPressed.connect(self.run_current)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(lambda _item: self.run_current())
        self.list.itemClicked.connect(lambda _item: self.run_current())
        self.hint = label("↑ ↓ to pick · Enter to run · Esc to close", "muted")
        self.card.setLayout(vbox(self.search, self.list, self.hint, spacing=10, margins=(14, 14, 14, 12)))
        self.card.setMinimumWidth(560)
        self.setLayout(vbox(self.card, margins=(SHADOW_MARGIN,) * 4))
        self.matches: list[Entry] = []
        self.refill()

    def refill(self) -> None:
        self.matches = match(self.entries, self.search.text())
        self.list.clear()
        t = style.tokens()
        for entry in self.matches:
            item = QListWidgetItem(f"{entry.title}")
            detail = f"{entry.kind}" + (f" · {entry.detail}" if entry.detail else "")
            item.setText(f"{entry.title}    {detail}")
            item.setToolTip(detail)
            item.setForeground(QColor(t.text))
            self.list.addItem(item)
        if not self.matches:
            item = QListWidgetItem("Nothing matches. Try another word.")
            item.setFlags(Qt.NoItemFlags)
            item.setForeground(QColor(t.text_muted))
            self.list.addItem(item)
        elif self.list.count():
            self.list.setCurrentRow(0)
        rows = max(1, min(len(self.matches) or 1, 12))
        self.list.setFixedHeight(rows * 28 + 8)

    def run_current(self) -> None:
        row = self.list.currentRow()
        if not self.matches:
            return
        entry = self.matches[row if 0 <= row < len(self.matches) else 0]
        self.close()
        entry.run()
        self.ran.emit(entry)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent
        if watched is self.search and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if key == Qt.Key_Down else -1)
                if 0 <= row < self.list.count():
                    self.list.setCurrentRow(row)
                return True
            if key == Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)

    def open_at(self, point: QPoint) -> None:
        """Show with the card's top-left at ``point`` (global), kept on screen."""
        self.adjustSize()
        screen = self.screen().availableGeometry() if self.screen() else None
        hint = self.sizeHint()
        width, height = hint.width() - 2 * SHADOW_MARGIN, hint.height() - 2 * SHADOW_MARGIN
        x, y = point.x(), point.y()
        if screen is not None:
            x = max(screen.left(), min(x, screen.right() - width))
            y = max(screen.top(), min(y, screen.bottom() - height))
        self.move(x - SHADOW_MARGIN, y - SHADOW_MARGIN)
        self.show()
        motion.fade_in(self)
        self.search.setFocus()


__all__ = ["CommandPalette", "Entry", "match", "KINDS"]
