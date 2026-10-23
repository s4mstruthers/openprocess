"""Opening a large file: a page that says what is happening, instead of a window that seems stuck.

A file is read on a worker thread (:func:`.workers.run_in_background`), so the
window keeps working; this page is what the main area shows meanwhile for a
file the user opened: its name and size, a spinner, how much of the file has
been read (a real share of its bytes when the reader can tell), the cases
read so far, how long it has taken, and *Cancel*.  The reader reports into a
:class:`Loading` (plain attributes, written by the worker, read by a timer
here), so no signal crosses threads for every 500 cases.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QProgressBar, QWidget

from . import style
from .widgets import Card, button, hbox, label, vbox


class Cancelled(Exception):
    """Raised in the reading thread when the user pressed Cancel."""


@dataclass
class Loading:
    """One file being read: what the page shows about it."""

    path: str
    title: str = ""                       # "Reading the event log"
    size: int = 0                         # bytes on disk
    started: float = field(default_factory=time.monotonic)
    cases: int = 0
    share: float | None = None            # 0..1 of the file, when known
    cancelled: bool = False

    def report(self, cases: int, share: float | None = None) -> None:
        """The reader's progress callback (runs on the reading thread)."""
        if self.cancelled:
            raise Cancelled()
        self.cases, self.share = cases, share


def size_text(size: int) -> str:
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}" if size < 10 * scale else f"{size / scale:.0f} {unit}"
    return f"{size} bytes"


class Spinner(QWidget):
    """A turning arc (still when motion is off: ``OPENPROCESS_NO_MOTION``)."""

    def __init__(self, diameter: int = 36) -> None:
        super().__init__()
        self.setFixedSize(diameter, diameter)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._turn)

    def start(self) -> None:
        if not os.environ.get("OPENPROCESS_NO_MOTION"):
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _turn(self) -> None:
        self.angle = (self.angle + 6) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = style.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        painter.setPen(QPen(QColor(t.border), 3.5))
        painter.drawEllipse(rect)
        pen = QPen(QColor(t.accent), 3.5)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int((90 - self.angle) * 16), int(-100 * 16))


class LoadingPage(QWidget):
    """What the main area shows while a file the user opened is being read."""

    cancel_requested = Signal(str)        # the path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loading: Loading | None = None
        self.spinner = Spinner()
        self.title = label("", "pageTitle")
        self.title.setAlignment(Qt.AlignHCenter)
        self.what = label("", "muted")
        self.what.setAlignment(Qt.AlignHCenter)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.detail = label("", "muted")
        self.detail.setAlignment(Qt.AlignHCenter)
        self.hint = label("The window keeps working: open or look at anything else meanwhile, and the "
                          "file appears in the sidebar when it is ready.", "muted", wrap=True)
        self.hint.setAlignment(Qt.AlignHCenter)
        self.cancel = button("Cancel", self._cancel, tooltip="Stop reading this file")
        card = Card(padding=28)
        card.setMaximumWidth(520)
        card.add(hbox(None, self.spinner, None))
        for widget in (self.title, self.what, self.bar, self.detail, self.hint):
            card.add(widget)
        card.add(hbox(None, self.cancel, None))
        self.setLayout(vbox(None, hbox(None, card, None), None, margins=(24, 24, 24, 24)))
        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)

    def show_loading(self, loading: Loading) -> None:
        self.loading = loading
        self.title.setText(os.path.basename(loading.path))
        self.what.setText(loading.title + (f" · {size_text(loading.size)}" if loading.size else ""))
        self.cancel.setEnabled(True)
        self.refresh()
        self.spinner.start()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self.spinner.stop()

    def refresh(self) -> None:
        loading = self.loading
        if loading is None:
            return
        elapsed = time.monotonic() - loading.started
        if loading.share is None:
            self.bar.setRange(0, 0)                      # busy: the share cannot be told
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * loading.share))
        parts = []
        if loading.share is not None:
            parts.append(f"{loading.share:.0%} read")
        if loading.cases:
            parts.append(f"{loading.cases:,} cases")
        parts.append(f"{elapsed:.0f} s")
        if loading.share and loading.share > 0.05 and elapsed > 2:
            left = elapsed * (1 - loading.share) / loading.share
            parts.append(f"about {left:.0f} s to go" if left >= 1 else "nearly done")
        self.detail.setText(" · ".join(parts))

    def _cancel(self) -> None:
        if self.loading is not None:
            self.loading.cancelled = True
            self.cancel.setEnabled(False)
            self.detail.setText("Stopping…")
            self.cancel_requested.emit(self.loading.path)


__all__ = ["Cancelled", "Loading", "LoadingPage", "Spinner", "size_text"]
