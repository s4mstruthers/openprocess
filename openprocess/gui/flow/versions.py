"""The *Versions* view of an analysis: where the numbers stand, and every version kept.

At the top, where the analysis stands against its record (the same words
as the badge in the header, with every change listed) and *Keep as
version…*.  Under it, newest first, every version kept: its note, when
and with which OpenProcess it was kept, what changed before it, *Compare
with now* (the boxes whose result differs, first) and *Restore* (the whole
workflow as it was then; ⌘Z undoes it).  Runs since the latest version are
listed in between, in a line each, so a look says what has happened since.
"""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...flow.snapshots import Snapshot, compare, take
from ...flow.versions import Version
from ..studio import style
from ..studio.widgets import Card, button, hbox, label, vbox

#: How many changes the status card lists before "and n more".
SHOWN = 6


def when(iso: str) -> str:
    """``2026-10-26T14:02:33`` → ``26 Oct 2026, 14:02``."""
    try:
        moment = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso.replace("T", " ")
    return f"{moment.day} {moment:%b %Y, %H:%M}"


def runs_since(snapshots: list[Snapshot], latest: Version | None) -> list[Snapshot]:
    """The runs after the one ``latest`` kept: the history after the last run
    with the version's settings and results (or, when that run is not in the
    history any more, the runs taken after the version was)."""
    if latest is None:
        return list(snapshots)
    kept = latest.snapshot
    for index in range(len(snapshots) - 1, -1, -1):
        if (snapshots[index].settings, snapshots[index].fingerprints) == (kept.settings, kept.fingerprints):
            return snapshots[index + 1:]
    return [s for s in snapshots if s.taken > latest.taken]


class VersionsWidget(QWidget):
    """The status card, the comparison (when asked for) and the timeline; :meth:`refresh` rebuilds all three."""

    def __init__(self, page, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.status = Card()
        layout.addWidget(self.status)
        self.comparison = Card("Compared", "…")
        self.comparison.setVisible(False)
        self.diff = QTableWidget(0, 4)
        self.diff.setHorizontalHeaderLabels(["Box", "Then", "Now", "What changed"])
        self.diff.verticalHeader().setVisible(False)
        self.diff.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diff.setSelectionMode(QAbstractItemView.NoSelection)
        self.diff.setShowGrid(False)
        header = self.diff.horizontalHeader()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.diff.setMinimumHeight(120)
        self.comparison.add(self.diff)
        self.comparison.add(hbox(None, button("Done", lambda: self.comparison.setVisible(False))))
        layout.addWidget(self.comparison)
        self.timeline = QVBoxLayout()
        self.timeline.setSpacing(10)
        layout.addLayout(self.timeline)
        layout.addStretch(1)
        self.cards: list[Card] = []
        self.compared: Version | None = None

    # -- building --------------------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_status()
        self._refresh_timeline()
        if self.compared is not None and self.comparison.isVisible():
            still = self.page.versions.get(self.compared.number)
            if still is None:
                self.comparison.setVisible(False)
            else:
                self.compare(still)

    def _refresh_status(self) -> None:
        card, status = self.status, self.page._repro
        card.clear()
        headline = label(status.headline, "cardTitle", wrap=True)
        card.add(headline)
        details = list(status.details)
        for line in details[:SHOWN]:
            card.add(label("· " + line, "muted", wrap=True))
        if len(details) > SHOWN:
            card.add(label(f"· and {len(details) - SHOWN} more", "muted"))
        buttons = []
        if status.state != "unrecorded":
            keep = button("Keep as version…", self.page.keep_version,
                          kind="primary" if status.can_keep else None,
                          tooltip="Record this run as the next version, with a note on what changed")
            keep.setEnabled(self.page.run is not None and not self.page.run.failed() and not self.page._running)
            buttons.append(keep)
        buttons.append(button("Run from scratch", self.page.rerun,
                              tooltip="Compute every box again, nothing from the cache, and check the record"))
        card.add(hbox(*buttons, None))

    def _refresh_timeline(self) -> None:
        while self.timeline.count():
            item = self.timeline.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = []
        versions = list(reversed(self.page.versions.items))
        latest = versions[0] if versions else None
        since = runs_since(self.page.history.snapshots, latest)
        if latest is not None and since:
            card = Card(f"Since v{latest.number} · {len(since)} run{'s' if len(since) != 1 else ''} not kept")
            for snapshot in reversed(since):
                card.add(label(f"{when(snapshot.taken)} · {snapshot.label}", "muted"))
            self.timeline.addWidget(card)
            self.cards.append(card)
        for version in versions:
            self.timeline.addWidget(self._version_card(version, version is latest))
        if not versions:
            card = Card("Versions", "The first run in which no box fails is kept here as v1; after that, "
                        "keep a version whenever the numbers are worth coming back to.")
            self.timeline.addWidget(card)
            self.cards.append(card)

    def _version_card(self, version: Version, is_latest: bool) -> Card:
        parts = [when(version.taken)]
        if version.software:
            parts.append(f"OpenProcess {version.software}")
        snapshot = version.snapshot
        parts.append(f"{snapshot.done} of {len(snapshot.statuses)} boxes done")
        card = Card(version.title, " · ".join(parts))
        if is_latest:
            tag = label("current record", "muted")
            tag.setToolTip("What every run is checked against (openprocess run --check too)")
            card.header.addWidget(tag)
        if version.label and version.label != version.note:
            card.add(label(version.label, "muted", wrap=True))
        compare_button = button("Compare with now", lambda _=False, v=version: self.compare(v),
                                tooltip="Each box's key figure then and now; the boxes whose result differs first")
        restore = button("Restore", lambda _=False, v=version: self.page.restore_version(v),
                         tooltip="Put the whole workflow back as it was in this version (⌘Z undoes it)")
        card.add(hbox(compare_button, restore, None))
        card.version = version
        self.cards.append(card)
        return card

    # -- comparing -------------------------------------------------------------------------------
    def compare(self, version: Version) -> None:
        """``version`` against the workflow and run as they are now, the differing boxes first."""
        self.compared = version
        now: Snapshot = take(self.page.workflow, self.page.run)
        rows = compare(version.snapshot, now)
        rows.sort(key=lambda r: not r.changed)
        changed = sum(1 for r in rows if r.changed)
        self.comparison.title_label.setText(f"{version.title} → now")
        self.comparison.caption_label.setText(
            f"{changed} of {len(rows)} boxes give a different result." if changed
            else f"Every box gives the result it gave in v{version.number}.")
        self.diff.setHorizontalHeaderLabels(["Box", f"v{version.number}", "Now", "What changed"])
        self.diff.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self.diff.setItem(index, 0, QTableWidgetItem(row.title))
            self.diff.setItem(index, 1, QTableWidgetItem(row.a or "–"))
            self.diff.setItem(index, 2, QTableWidgetItem(row.b or "–"))
            what = "; ".join(row.changes) if row.changes else ("the result differs" if row.changed else "")
            item = QTableWidgetItem(what)
            if row.changed:
                item.setForeground(style.qc(style.STATUS["warning"]))
            self.diff.setItem(index, 3, item)
        self.comparison.setVisible(True)


__all__ = ["VersionsWidget", "runs_since", "when"]
