"""Small reusable widgets: cards, stat tiles, segmented controls, chips.

These are deliberately plain ``QWidget`` subclasses styled through object
names (``#card``, ``#statValue`` ...) so that every look-and-feel decision
stays in :mod:`style`.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QMenu, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from .. import theme
from . import style


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def shortcut_text(keys: str) -> str:
    """A shortcut as this computer writes it: ``"Ctrl+S"`` is shown as ⌘S on
    a Mac and as Ctrl+S on Windows and Linux (Qt swaps Ctrl and ⌘ for the
    actual key presses in the same way)."""
    from PySide6.QtGui import QKeySequence
    return QKeySequence(keys).toString(QKeySequence.NativeText)


def modifier_text(name: str) -> str:
    """A modifier key's name as this computer writes it: ``"Ctrl"`` is ⌘ on a
    Mac, ``"Alt"`` is ⌥, ``"Shift"`` is ⇧; unchanged elsewhere."""
    import sys
    if sys.platform != "darwin":
        return name
    return {"Ctrl": "⌘", "Alt": "⌥", "Shift": "⇧", "Meta": "⌃"}.get(name, name)


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_last=False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        elif isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
    if stretch_last:
        layout.addStretch(1)
    return layout


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        elif isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
    return layout


def label(text: str = "", name: str | None = None, wrap: bool = False,
          selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    if wrap:
        # Wrapped labels must be allowed to grow vertically, otherwise some
        # layouts give them their one-line height and the text is clipped.
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def button(text: str, slot=None, kind: str | None = None, tooltip: str | None = None) -> QPushButton:
    widget = QPushButton(text)
    if kind:
        widget.setObjectName(kind)
    if slot is not None:
        widget.clicked.connect(slot)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.PointingHandCursor)
    return widget


class Card(QFrame):
    """A rounded surface with an optional title row."""

    def __init__(self, title: str | None = None, caption: str | None = None,
                 padding: int = 16, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding - 2, padding, padding)
        self.body.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            self.title_label = label(title, "cardTitle")
            self.header.addWidget(self.title_label)
        self.header.addStretch(1)
        if title or caption:
            self.body.addLayout(self.header)
        if caption:
            self.caption_label = label(caption, "cardCaption", wrap=True)
            self.body.addWidget(self.caption_label)
        #: Items before this index are chrome (title, caption); see clear().
        self.fixed_count = self.body.count()

    def clear(self) -> None:
        """Remove all content, keeping the title and caption."""
        while self.body.count() > self.fixed_count:
            item = self.body.takeAt(self.body.count() - 1)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                _delete_layout(item.layout())

    def add(self, item, stretch: int = 0):
        if isinstance(item, QWidget):
            self.body.addWidget(item, stretch)
        else:
            self.body.addLayout(item, stretch)
        if self.concealed:
            _set_visible(item, False)
        return item

    # -- hidden in an exercise (see concealment.py) ---------------------------------
    concealed = False

    def set_concealment(self, concealment, key: str) -> None:
        """Follow ``concealment`` (None: never hidden) for the result ``key``."""
        previous = getattr(self, "_concealment", None)
        if previous is not None and previous is not concealment:
            try:
                previous.changed.disconnect(self._follow_concealment)
            except (RuntimeError, TypeError):
                pass
        if concealment is not None and concealment is not previous:
            concealment.changed.connect(self._follow_concealment)
        self._concealment, self._conceal_key = concealment, key
        self._follow_concealment()

    def _follow_concealment(self) -> None:
        concealment = getattr(self, "_concealment", None)
        hidden = concealment is not None and concealment.hidden(self._conceal_key)
        if hidden == self.concealed:
            return
        if hidden and not hasattr(self, "reveal_row"):
            from ..learn.concealment import RESULTS
            what = RESULTS.get(self._conceal_key, "this result")
            self.reveal_row = QFrame()
            self.reveal_row.setObjectName("revealRow")
            reveal = button("Reveal", lambda: self._concealment and self._concealment.reveal(
                self._conceal_key), tooltip=f"Show {what}")
            reveal.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.reveal_button = reveal
            text = QVBoxLayout()
            text.setSpacing(1)
            text.addWidget(label("Hidden in this exercise", "revealTitle"))
            text.addWidget(label(f"Work out {what} yourself first.", "muted", wrap=True))
            row = hbox(spacing=10, margins=(12, 9, 10, 9))
            row.addLayout(text, 1)
            row.addWidget(reveal, 0, Qt.AlignVCenter)
            self.reveal_row.setLayout(row)
            self.body.insertWidget(self.fixed_count, self.reveal_row)
            self.fixed_count += 1
        self.concealed = hidden
        if hasattr(self, "reveal_row"):
            self.reveal_row.setVisible(hidden)
            # In an exam nothing is revealed: the button goes, the notice stays.
            self.reveal_button.setVisible(not getattr(concealment, "locked", False))
        for index in range(self.fixed_count, self.body.count()):
            _set_visible(self.body.itemAt(index), not hidden)


def _set_visible(item, visible: bool) -> None:
    """Show or hide a widget, or every widget in a layout (or layout item)."""
    if isinstance(item, QWidget):
        item.setVisible(visible)
        return
    widget = item.widget() if hasattr(item, "widget") else None
    if widget is not None:
        widget.setVisible(visible)
        return
    layout = item.layout() if hasattr(item, "layout") and not hasattr(item, "count") \
        else item
    if layout is None or not hasattr(layout, "count"):
        return
    for index in range(layout.count()):
        _set_visible(layout.itemAt(index), visible)


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            _delete_layout(item.layout())


class StatTile(QFrame):
    """label · value · sub-line, the standard figure tile."""

    def __init__(self, caption: str, value: str = "–", sub: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(2)
        self.caption = label(caption, "statLabel")
        self.value = label(value, "statValue")
        self.sub = label(sub, "statSub", wrap=True)
        self.value.setWordWrap(True)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.sub)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class SegmentedControl(QFrame):
    """A row of mutually exclusive pill buttons (like NSSegmentedControl)."""

    changed = Signal(int)

    def __init__(self, labels: list[str], parent=None, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("segmented")
        # Compact: tighter padding and a smaller font, for narrow panels.
        self.setProperty("compact", compact)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for index, text in enumerate(labels):
            item = QPushButton(text)
            item.setCheckable(True)
            item.setCursor(Qt.PointingHandCursor)
            self.group.addButton(item, index)
            layout.addWidget(item)
            self.buttons.append(item)
        if self.buttons:
            self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.changed.emit)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)
            self.changed.emit(index)


class Verdict(QWidget):
    """Icon + property name + explanation, coloured by status.

    The icon glyph and the word carry the meaning; colour only reinforces it.
    """

    def __init__(self, name: str, status: str, detail: str, parent=None,
                 definition: str | None = None, instance: list[str] | None = None) -> None:
        """``definition`` is a key of :mod:`openprocess.mining.definitions` (by default
        looked up from ``name``); ``instance`` are LaTeX lines that fill the
        definition in for the net at hand.  With a definition, hovering over the
        property shows it and clicking opens it (see :mod:`.definition_view`)."""
        super().__init__(parent)
        from html import escape
        from ...mining.definitions import lookup
        known = lookup(definition or name)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)
        badge = _StatusBadge(status)
        layout.addWidget(badge, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        if known is not None:
            muted = style.tokens().text_muted
            title = label(f"<span>{escape(name)} <span style='color: {muted}; "
                          "font-weight: 400'>ⓘ</span></span>")
        else:
            title = label(name)
        title.setStyleSheet("font-weight: 600;")
        text.addWidget(title)
        #: The title label (tests click it to open the definition).
        self.title = title
        if detail:
            text.addWidget(label(detail, "muted", wrap=True, selectable=True))
        layout.addLayout(text, 1)
        if known is not None:
            from .definition_view import attach_definition
            attach_definition(self, known.key, instance, targets=[badge, title])


class _StatusBadge(QWidget):
    def __init__(self, status: str) -> None:
        super().__init__()
        self.status = status
        self.setFixedSize(20, 20)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(style.STATUS.get(self.status, style.tokens().text_muted))
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QRectF(1, 1, 18, 18))
        painter.setPen(QColor("#ffffff") if self.status != "warning" else QColor("#0b0b0b"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, 20, 20), Qt.AlignCenter,
                         style.STATUS_ICON.get(self.status, "?"))


def status_for(value: bool | None) -> str:
    return "unknown" if value is None else ("good" if value else "critical")


def fitness_status(value: float) -> str:
    """Traffic-light bands for a quality value in [0, 1]."""
    if value >= 0.95:
        return "good"
    if value >= 0.8:
        return "warning"
    return "critical"


# ---------------------------------------------------------------------------
class Chip(QWidget):
    """A small pill with a word or two in it: "not installed", "ready", "✓ correct".

    Painted here rather than styled.  A label's background and corner radius
    come from the style's box model, which polishes when it likes: the
    downloaded app showed square chips beside round ones.  A pill must look
    the same everywhere, so this one draws itself from its font metrics.
    ``fill`` and ``ink`` are colours; :meth:`set_state` picks the soft tints of
    the status colours instead (good, warning, critical, or muted).
    """

    def __init__(self, text: str = "", fill=None, ink=None, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._fill = QColor(fill) if fill is not None else QColor(style.tokens().text_muted)
        self._ink = QColor(ink) if ink is not None else QColor("white")
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - a label's name, so a chip can stand in for one
        self._text = text
        self.updateGeometry()
        self.update()

    def set_colours(self, fill, ink) -> None:
        self._fill, self._ink = QColor(fill), QColor(ink)
        self.update()

    def set_state(self, state: str) -> None:
        """The soft look of a status: a tint of its colour behind ink of the same."""
        t = style.tokens()
        if state == "good":
            self.set_colours(style.qc(style.STATUS["good"], 0.13), style.STATUS["good"])
        elif state == "warning":
            self.set_colours(style.qc(style.STATUS["warning"], 0.25), t.text)
        elif state == "critical":
            self.set_colours(style.qc(style.STATUS["critical"], 0.12), style.STATUS["critical"])
        else:
            self.set_colours(style.qc(t.text, 0.08), t.text_secondary)

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetricsF(self.font())
        return QSize(int(metrics.horizontalAdvance(self._text)) + 18, max(20, int(metrics.height()) + 6))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._fill)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(self._ink))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        painter.end()


# Activity chips
# ---------------------------------------------------------------------------
def paint_chips(painter: QPainter, rect: QRectF, sequence: list[tuple[str, str, str | None]],
                font, max_chip: float = 150.0) -> float:
    """Paint ``(text, fill, outline)`` chips left to right inside ``rect``.

    Returns the x coordinate after the last chip.  Chips that do not fit are
    replaced by a ``+n`` counter, so a long variant never overflows its row.
    """
    metrics = QFontMetricsF(font)
    painter.setFont(font)
    x = rect.left()
    height = min(rect.height(), metrics.height() + 8)
    y = rect.center().y() - height / 2
    for index, (text, fill, outline) in enumerate(sequence):
        shown = metrics.elidedText(text, Qt.ElideRight, max_chip - 16)
        width = metrics.horizontalAdvance(shown) + 16
        remaining = len(sequence) - index
        if x + width > rect.right() - (34 if remaining > 1 else 0):
            more = f"+{remaining}"
            painter.setPen(QColor(style.tokens().text_muted))
            painter.drawText(QRectF(x + 2, y, 40, height), Qt.AlignVCenter | Qt.AlignLeft, more)
            return x + 40
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, width, height), height / 2, height / 2)
        painter.setPen(QPen(QColor(outline), 1.5) if outline else Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawPath(path)
        painter.setPen(QColor(style.text_on(fill)))
        painter.drawText(QRectF(x, y, width, height), Qt.AlignCenter, shown)
        x += width + 4
    return x


class SequenceDelegate(QStyledItemDelegate):
    """Draws a list/table cell's ``Qt.UserRole`` value as activity chips.

    The role holds ``list[(text, fill, outline)]``.
    """

    def paint(self, painter: QPainter, option, index) -> None:
        sequence = index.data(Qt.UserRole)
        if not sequence:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(style.tokens().accent_soft))
        font = option.font
        theme.set_px(font, max(theme.px(font) - 1, 9))
        paint_chips(painter, QRectF(option.rect).adjusted(6, 3, -6, -3), sequence, font)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), 30))


class BarDelegate(QStyledItemDelegate):
    """Draws a number with a thin horizontal bar (``Qt.UserRole`` = fraction)."""

    def __init__(self, colour_role: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.colour_role = colour_role

    def paint(self, painter: QPainter, option, index) -> None:
        fraction = index.data(Qt.UserRole)
        if fraction is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(style.tokens().accent_soft))
        rect = QRectF(option.rect).adjusted(8, 0, -8, 0)
        text = str(index.data(Qt.DisplayRole) or "")
        metrics = QFontMetricsF(option.font)
        text_width = 64.0
        bar_rect = QRectF(rect.left() + text_width + 6, rect.center().y() - 4,
                          max(rect.width() - text_width - 6, 10) * float(fraction), 8)
        colour = index.data(self.colour_role) if self.colour_role else None
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour or style.categorical(0)))
        if bar_rect.width() > 0.5:
            painter.drawRoundedRect(bar_rect, 3, 3)
        painter.setPen(QColor(style.tokens().text))
        painter.drawText(QRectF(rect.left(), rect.top(), text_width, rect.height()),
                         Qt.AlignVCenter | Qt.AlignRight, metrics.elidedText(
                             text, Qt.ElideRight, text_width))
        painter.restore()


class ColourDot(QWidget):
    """A small filled circle: the identity mark beside a legend label."""

    def __init__(self, colour: str, size: int = 10) -> None:
        super().__init__()
        self.colour = colour
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colour))
        painter.drawEllipse(self.rect().adjusted(0, 0, -1, -1))


def is_dark() -> bool:
    return theme.is_dark()


class ElidedLabel(QLabel):
    """A one-line label that shortens itself with "…" instead of forcing width.

    A normal QLabel insists on being as wide as its text, which is how a long
    file name in a header can push the whole window wider than the screen.
    This one reports a tiny minimum width and elides when squeezed; the full
    text is always available as a tooltip.
    """

    def __init__(self, text: str = "", name: str | None = None) -> None:
        super().__init__(text)
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.hint = ""
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self._update_tooltip()

    def set_hint(self, hint: str) -> None:
        """A second tooltip line that stays when the text changes ("Double-click to rename")."""
        self.hint = hint
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        self.setToolTip(self.text() + (f"\n{self.hint}" if self.hint else ""))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, rect.width())
        painter.drawText(rect, int(self.alignment() | Qt.AlignVCenter), text)


class PageHeader(QWidget):
    """Large title, muted subtitle, and a right-aligned row of actions.

    Double-clicking the title emits :attr:`title_double_clicked`; the main
    window uses it to rename the document.
    """

    title_double_clicked = Signal()

    def __init__(self, title: str = "", subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 10)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = ElidedLabel(title, "pageTitle")
        # In a row with the badge, the title keeps its natural width and only
        # shrinks (eliding) when the header is short of space.
        self.title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.subtitle = ElidedLabel(subtitle, "pageSubtitle")
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.title)
        title_row.addStretch(1)
        text.addLayout(title_row)
        text.addWidget(self.subtitle)
        self._text = text
        self._title_row = title_row
        self.back: QPushButton | None = None
        self.badge: QPushButton | None = None
        layout.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def set_badge(self, text: str, tone: str = "muted", tooltip: str = "", slot=None) -> None:
        """A pill beside the title ("● Reproducible"): a soft tint of the tone with
        a dot and a few words; the full sentence is the tooltip.  ``tone``: good,
        warning, critical or muted.  Empty text hides it.  Short of room, the
        pill keeps only its dot (see :meth:`resizeEvent`)."""
        if self.badge is None:
            self.badge = QPushButton()
            self.badge.setObjectName("reproBadge")
            self.badge.setCursor(Qt.PointingHandCursor)
            self.badge.setFocusPolicy(Qt.NoFocus)
            self.badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self._title_row.insertWidget(1, self.badge, 0, Qt.AlignVCenter)
            self._badge_slot = None
        from . import style
        t = style.tokens()
        ink = {"good": style.STATUS["good"], "warning": style.STATUS["warning"],
               "critical": style.STATUS["critical"]}.get(tone, t.text_secondary)
        tint = style.qc(ink, 0.14 if tone != "muted" else 0.08).name(QColor.HexArgb)
        # The whole rule here, not only the colours: on macOS a button whose own
        # sheet sets a background but no border falls back to a square frame.
        self.badge.setStyleSheet(
            f"QPushButton#reproBadge {{ background: {tint}; color: {ink}; border: none; border-radius: 11px; "
            f"min-height: 22px; max-height: 22px; padding: 0 11px 0 9px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton#reproBadge:hover {{ background: {style.qc(ink, 0.22 if tone != 'muted' else 0.14).name(QColor.HexArgb)}; }}")
        self._badge_text = text
        self.badge.setToolTip(tooltip)
        self.badge.setVisible(bool(text))
        self._fit_badge()
        if self._badge_slot is not None:
            self.badge.clicked.disconnect(self._badge_slot)
        self._badge_slot = slot
        if slot is not None:
            self.badge.clicked.connect(slot)

    def _fit_badge(self) -> None:
        """The pill's words when there is room for them beside the title, else its dot alone."""
        if self.badge is None or not getattr(self, "_badge_text", ""):
            return
        metrics = self.badge.fontMetrics()
        full = f"●  {self._badge_text}"
        needed = metrics.horizontalAdvance(full) + 24 + self.title.sizeHint().width() + self._title_row.spacing()
        room = self._title_row.contentsRect().width() if self._title_row.contentsRect().width() > 0 else needed
        self.badge.setText(full if needed <= room else "●")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_badge()

    def set_back(self, text: str, slot) -> None:
        """A link above the title, back to where this page was opened from
        ("‹ Back to Discover and check")."""
        if self.back is None:
            self.back = button(text, slot, kind="backLink")
            self.back.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            self._text.insertWidget(0, self.back, 0, Qt.AlignLeft)
        else:
            self.back.setText(text)
            self.back.clicked.disconnect()
            self.back.clicked.connect(slot)

    def set_text(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Labels ignore mouse events, so a double-click on the title lands here.
        if self.title.geometry().contains(event.position().toPoint()):
            self.title_double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _RoundedMenus(QObject):
    """Gives every pop-up -- menus, the list a combo box drops down, tooltips --
    a see-through window, so the stylesheet's rounded corners show (otherwise
    the corners are filled in square)."""

    POPUPS = ("QComboBoxPrivateContainer", "QTipLabel")

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.MouseButtonPress and isinstance(watched, QComboBox):
            # Size the list before it opens.  Resizing the popup once it is
            # showing (what _fit_list did) makes macOS flicker and close it,
            # so a list with long entries could not be used at all.
            self._prepare_list(watched)
        if event.type() == QEvent.Show and isinstance(watched, QWidget) \
                and watched.metaObject().className() == "QComboBoxPrivateContainer" \
                and sys.platform != "darwin":
            self._fit_list(watched)
        if event.type() == QEvent.Polish and isinstance(watched, QWidget) \
                and (isinstance(watched, QMenu) or watched.metaObject().className()
                     in self.POPUPS) \
                and not watched.testAttribute(Qt.WA_WState_Created):
            watched.setAttribute(Qt.WA_TranslucentBackground)
            if sys.platform != "darwin":
                # Windows and Linux draw a square shadow; macOS follows the shape.
                watched.setWindowFlag(Qt.NoDropShadowWindowHint)
        return False


    @staticmethod
    def _prepare_list(combo: "QComboBox") -> None:
        """Before the list opens: at least as wide as its longest entry (within
        reason), so the popup comes up at its final size."""
        view = combo.view()
        if view is None or view.model() is None or combo.count() == 0:
            return
        needed = view.sizeHintForColumn(0) + 2 * view.frameWidth() + 24
        screen = combo.screen()
        if screen is not None:
            needed = min(needed, int(screen.availableGeometry().width() * 0.6))
        if needed > combo.width():
            view.setMinimumWidth(needed)

    @staticmethod
    def _fit_list(container: QWidget) -> None:
        """A combo box's list is as wide as the box: widen it to its longest
        entry, so nothing is cut off ("Activity (co…lete events)")."""
        from PySide6.QtWidgets import QAbstractItemView
        view = container.findChild(QAbstractItemView)
        if view is None or view.model() is None:
            return
        needed = view.sizeHintForColumn(0) + 2 * view.frameWidth() + 24
        if view.verticalScrollBar().isVisible():
            needed += view.verticalScrollBar().width()
        if needed > container.width():
            container.resize(needed, container.height())


_rounded_menus: _RoundedMenus | None = None


def round_menus(application) -> None:
    """Round the corners of every pop-up in the app (once per application)."""
    global _rounded_menus
    if _rounded_menus is None:
        _rounded_menus = _RoundedMenus(application)
        application.installEventFilter(_rounded_menus)


class NoticeBar(QFrame):
    """A bar above a page about its file: "changed on disk", "missing", …

    Hidden until :meth:`show_notice`; the buttons are (text, slot) pairs.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(8)
        self.text = QLabel()
        self.text.setWordWrap(True)
        layout.addWidget(self.text, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        layout.addLayout(self.buttons)
        self.kind: str | None = None
        self.setVisible(False)

    def show_notice(self, kind: str, text: str, actions: list) -> None:
        """Show ``text`` with buttons; ``kind`` names the notice ("changed", "missing")."""
        self.kind = kind
        self.text.setText(text)
        while self.buttons.count():
            widget = self.buttons.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for index, (caption, slot) in enumerate(actions):
            self.buttons.addWidget(button(caption, slot, kind="primary" if index == 0 else None))
        self.setVisible(True)

    def clear(self, kind: str | None = None) -> None:
        """Hide the bar (only if it shows ``kind``, when given)."""
        if kind is None or self.kind == kind:
            self.kind = None
            self.setVisible(False)


# ---------------------------------------------------------------------------
# Where file dialogs start
# ---------------------------------------------------------------------------
#: The open workspace folder ("" when there is none).  Save and open dialogs
#: start there, so a new net is saved into the week you are working on.
_dialog_folder = ""


def set_dialog_folder(folder: str | None) -> None:
    global _dialog_folder
    _dialog_folder = folder or ""


def dialog_folder() -> str:
    return _dialog_folder


def suggested_path(file_name: str) -> str:
    """``file_name`` inside the workspace folder, or bare (the dialog's own choice)."""
    if _dialog_folder:
        from pathlib import Path
        return str(Path(_dialog_folder) / file_name)
    return file_name


def scroll(widget: QWidget, horizontal: bool = False) -> "QScrollArea":
    """Wrap ``widget`` in a frameless scroll area.

    With ``horizontal=False`` the content always matches the viewport width
    (only vertical scrolling).  With ``True`` the content may also scroll
    sideways when the viewport is narrower than its minimum width -- used
    around whole pages so that the *window* can always be made smaller.
    """
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    if not horizontal:
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


class LegendSwatch(QWidget):
    """A small painted key: a colour ramp, a line (thin/thick/dashed), or a dot."""

    def __init__(self, kind: str, colour: str | None = None, width: float = 1.5) -> None:
        super().__init__()
        self.kind, self.colour, self.line_width = kind, colour, width
        self.setFixedSize(46 if kind in ("ramp", "widths") else 30, 16)

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = style.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(self.colour or t.text_secondary)
        if self.kind == "ramp":
            steps = 6
            w = self.width() / steps
            for i in range(steps):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(style.sequential(0.1 + 0.8 * i / (steps - 1))))
                painter.drawRoundedRect(QRectF(i * w, 3, w - 1, 10), 2, 2)
        elif self.kind == "widths":
            for i, width in enumerate((1.0, 2.5, 4.5)):
                pen = QPen(colour, width)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                x = 4 + i * 14
                painter.drawLine(QPointF(x, 12), QPointF(x + 8, 4))
        elif self.kind == "dot":
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QRectF(9, 2, 12, 12))
        else:
            pen = QPen(colour, self.line_width, Qt.DashLine if self.kind == "dashed" else Qt.SolidLine)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(2, 8), QPointF(28, 8))


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto new lines when needed.

    Used for toolbars and legends, so a narrow window wraps them instead of
    forcing the whole page wider than the screen.
    """

    def __init__(self, parent=None, spacing: int = 8, line_spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self._line_spacing = line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            # Never wider than the layout: a long label gets the whole line
            # (and elides or wraps) instead of running off the edge.
            hint.setWidth(min(hint.width(), max(area.width(), 1)))
            if x + hint.width() > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._line_spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def flow(*widgets, spacing: int = 8) -> QWidget:
    """A widget holding ``widgets`` in a :class:`FlowLayout` (ints = extra gaps)."""
    host = QWidget()
    layout = FlowLayout(host, spacing=spacing)
    for widget in widgets:
        if isinstance(widget, int):
            spacer = QWidget()
            spacer.setFixedSize(max(widget - spacing, 0), 1)
            layout.addWidget(spacer)
        elif widget is not None:
            layout.addWidget(widget)
    host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    return host


class Legend(QWidget):
    """A wrapped row of (swatch, explanation) pairs under a chart or graph."""

    def __init__(self) -> None:
        super().__init__()
        self.layout_ = FlowLayout(self, spacing=16, line_spacing=4)
        self.layout_.setContentsMargins(0, 2, 0, 0)

    def set_items(self, items: list[tuple["LegendSwatch", str]]) -> None:
        while self.layout_.count():
            entry = self.layout_.takeAt(0)
            if entry.widget():
                entry.widget().hide()            # gone now, not at the next event loop
                entry.widget().deleteLater()
        for swatch, text in items:
            pair = QWidget()
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(swatch)
            row.addWidget(label(text, "muted"))
            self.layout_.addWidget(pair)
        self.layout_.invalidate()
        self.updateGeometry()


def short_names(names: list[str]) -> list[str]:
    """Distinct abbreviations for column headers: the initials of each word
    (``check credit`` -> ``cc``), numbered where two would clash; names of up
    to three characters stay as they are."""
    result = []
    for name in names:
        words = name.replace("_", " ").split()
        result.append(name if len(name) <= 3 else "".join(w[0] for w in words).lower()
                      or name[:3])
    for index, value in enumerate(result):
        clashes = [i for i, other in enumerate(result) if other == value]
        if len(clashes) > 1:
            for number, i in enumerate(clashes, 1):
                result[i] = f"{value}{number}"
    return result


def footprint_table(fp, compact: bool = False) -> "QTableWidget":
    """A footprint matrix (row activity vs column activity: → ← ‖ #) as a
    table, tinted by relation.  Used for logs and for models alike.

    ``compact`` (narrow panels): columns are headed by short names, with the
    full name in the header's tooltip."""
    from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem
    from ...mining.footprint import CAUSAL, INVERSE, PARALLEL
    activities = fp.activities
    table = QTableWidget(len(activities), len(activities))
    headers = short_names(activities) if compact else activities
    table.setHorizontalHeaderLabels(headers)
    if compact:
        for index, name in enumerate(activities):
            table.horizontalHeaderItem(index).setToolTip(name)
    table.setVerticalHeaderLabels(activities)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.horizontalHeader().setDefaultSectionSize(
        40 if compact else max(52, min(140, 900 // max(len(activities), 1))))
    table.verticalHeader().setDefaultSectionSize(30 if compact else 34)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
    t = style.tokens()
    tint = {CAUSAL: style.qc(style.categorical(0), 0.16),
            INVERSE: style.qc(style.categorical(0), 0.08),
            PARALLEL: style.qc(style.categorical(1), 0.18)}
    big = theme.ui_font(13 if compact else 15)
    for i, a in enumerate(activities):
        for j, b in enumerate(activities):
            relation = fp.relation(a, b)
            cell = QTableWidgetItem(relation)
            cell.setTextAlignment(Qt.AlignCenter)
            cell.setFont(big)
            cell.setToolTip({"→": f"{a} → {b}: causality ({a} > {b}, never {b} > {a})",
                             "←": f"{a} ← {b}: inverse causality",
                             "‖": f"{a} ‖ {b}: parallel (both orders occur)",
                             "#": f"{a} # {b}: never directly follow each other"}[relation])
            if relation in tint:
                cell.setBackground(tint[relation])
            else:
                cell.setForeground(QColor(t.text_muted))
            table.setItem(i, j, cell)
    # No bigger than its cells: a small matrix should not sit in a large grey
    # area.  A large one still scrolls.
    frame = 2 * table.frameWidth()
    table.setMaximumWidth(table.verticalHeader().sizeHint().width() + len(activities)
                          * table.horizontalHeader().defaultSectionSize() + frame + 2)
    if not compact:         # (the Analysis tab sets the height of the compact one)
        table.setMaximumHeight(table.horizontalHeader().sizeHint().height() + len(activities)
                               * table.verticalHeader().defaultSectionSize() + frame + 2)
    return table
