"""Reviewing what an assistant changed: a list, a diff, Keep or Undo.

Every file an AI assistant writes through the MCP server is journaled by
:mod:`openprocess.flow.changes` with a copy of what it replaced.  The window
shows a pill in its status bar while there are changes nobody has looked
at ("AI changed 3 files · Review"); this dialog lists them, shows each as a
diff, and *Undo* puts the file back as it was, *Keep* marks it reviewed.
Nothing here blocks the assistant: its change is already in the folder
(and visible in the app); the review is the user's, when they want it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QSplitter, QVBoxLayout, QWidget)

from ...flow import changes as journal
from .. import theme
from .widgets import button, hbox, label


class ReviewDialog(QDialog):
    """The assistant's changes in a folder, newest first."""

    def __init__(self, folder: str | Path, window=None, parent=None) -> None:
        super().__init__(parent or window)
        self.folder = Path(folder)
        self.window_ = window
        self.setWindowTitle("AI changes")
        self.resize(900, 560)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self.intro = label("", "muted", wrap=True)
        layout.addWidget(self.intro)
        splitter = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        splitter.addWidget(self.list)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("")
        self.title.setObjectName("cardTitle")
        self.title.setWordWrap(True)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(theme.mono_font(11))
        self.diff.setLineWrapMode(QPlainTextEdit.NoWrap)
        right_layout.addWidget(self.title)
        right_layout.addWidget(self.diff, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.keep_button = button("Keep", self._keep, kind="primary",
                                  tooltip="The change stays; it is no longer listed as new")
        self.undo_button = button("Undo", self._undo,
                                  tooltip="Put the file back as it was before this change (the file is "
                                          "removed when the assistant created it)")
        self.open_button = button("Open file", self._open, tooltip="Open the file in OpenProcess")
        self.keep_all_button = button("Keep all", self._keep_all)
        self.show_all = button("Show reviewed too", self._toggle_all)
        self._all = False
        layout.addLayout(hbox(self.keep_button, self.undo_button, self.open_button, None, self.keep_all_button,
                              self.show_all))
        self.refresh()

    # -- the list -----------------------------------------------------------------------
    def refresh(self) -> None:
        current = self._current_id()
        self.list.clear()
        items = journal.changes(self.folder) if self._all else journal.pending(self.folder)
        for change in reversed(items):
            item = QListWidgetItem(journal.describe(change))
            item.setData(Qt.UserRole, change.id)
            if change.undone:
                item.setText(item.text() + "  (undone)")
            elif change.reviewed:
                item.setText(item.text() + "  (kept)")
            self.list.addItem(item)
            if change.id == current:
                self.list.setCurrentItem(item)
        count = len(journal.pending(self.folder))
        self.intro.setText(
            f"{count} change{'s' * (count != 1)} by an AI assistant not reviewed yet in {self.folder.name}. "
            "Each one is already in the folder; Undo puts the file back as it was, Keep leaves it. "
            f"Backups are in {journal.FOLDER}/." if count else
            f"Nothing new: every change an assistant made in {self.folder.name} has been reviewed.")
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._show()

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _current(self) -> journal.Change | None:
        wanted = self._current_id()
        return next((c for c in journal.changes(self.folder) if c.id == wanted), None)

    def _show(self) -> None:
        change = self._current()
        enabled = change is not None and not change.undone
        self.keep_button.setEnabled(enabled and not change.reviewed)
        self.undo_button.setEnabled(enabled)
        self.open_button.setEnabled(change is not None and Path(change.path).exists())
        if change is None:
            self.title.setText("")
            self.diff.setPlainText("")
            return
        self.title.setText(f"{change.kind.capitalize()} {Path(change.path).name} — {change.summary or change.tool}"
                           f"\n{change.path}")
        text = journal.diff_of(change)
        if not text:
            if change.kind == "created":
                try:
                    text = Path(change.path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = "(a binary file, or one that is no longer there)"
            elif change.kind == "removed":
                text = "(the file was removed)"
            else:
                text = "(the same text, or a binary file; the backup is in the journal folder)"
        self.diff.setPlainText(text)

    # -- the buttons ----------------------------------------------------------------------
    def _keep(self) -> None:
        change = self._current()
        if change is not None:
            journal.mark_reviewed(self.folder, change.id)
            self._after()

    def _keep_all(self) -> None:
        for change in journal.pending(self.folder):
            journal.mark_reviewed(self.folder, change.id)
        self._after()

    def _undo(self) -> None:
        change = self._current()
        if change is not None:
            journal.undo(self.folder, change.id)
            self._after()

    def _open(self) -> None:
        change = self._current()
        if change is not None and self.window_ is not None and Path(change.path).exists():
            self.window_.open_files([change.path])

    def _toggle_all(self) -> None:
        self._all = not self._all
        self.show_all.setText("Show new only" if self._all else "Show reviewed too")
        self.refresh()

    def _after(self) -> None:
        self.refresh()
        refresh = getattr(self.window_, "refresh_review", None)
        if callable(refresh):
            refresh()


__all__ = ["ReviewDialog"]
