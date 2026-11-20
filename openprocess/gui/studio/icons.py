"""The small icons of the sidebar and the window: painted, so the app ships no image files
beside its logo."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from . import style


def app_icon() -> QIcon:
    """The OpenProcess logo (Dock, window and About box)."""
    return QIcon(str(Path(__file__).resolve().parents[1] / "resources" / "openprocess-icon.png"))


def icon(kind: str) -> QIcon:
    """Tiny painted icons, so the app ships no image files.

    Drawn twice: in colour, and in the selected row's text colour, which Qt
    uses for the selected row (a blue icon on the blue highlight vanished).
    """
    icon = QIcon(icon_pixmap(kind, None))
    icon.addPixmap(icon_pixmap(kind, QColor(style.tokens().accent_text)), QIcon.Selected)
    return icon


def faded_icon(kind: str) -> QIcon:
    """The kind's icon at reduced opacity: a workspace file that is not open yet."""
    source = icon_pixmap(kind, None)
    faded = QPixmap(source.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.45)
    painter.drawPixmap(0, 0, source)
    painter.end()
    icon = QIcon(faded)
    icon.addPixmap(icon_pixmap(kind, QColor(style.tokens().accent_text)), QIcon.Selected)
    return icon


def icon_pixmap(kind: str, ink: QColor | None) -> QPixmap:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = ink if ink is not None else QColor(
        {"log": style.categorical(0), "cpn": style.categorical(2)}.get(kind, style.categorical(6)))
    pen = QPen(colour, 3)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    if kind == "folder":
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink if ink is not None else QColor(style.tokens().accent))
        tab = QRectF(3, 7, 11, 6)
        painter.drawRoundedRect(tab, 2, 2)
        painter.drawRoundedRect(QRectF(3, 10, 26, 16), 2.5, 2.5)
    elif kind == "compare":
        painter.setPen(Qt.NoPen)
        for index, (x, height) in enumerate(((5, 12), (12, 20), (19, 8), (26, 16))):
            painter.setBrush(ink if ink is not None else QColor(style.categorical(index % 2)))
            painter.drawRoundedRect(QRectF(x - 2, 27 - height, 5, height), 1.5, 1.5)
    elif kind == "cpn":
        painter.drawEllipse(QRectF(3, 9, 13, 13))
        painter.drawRoundedRect(QRectF(19, 8, 10, 15), 2, 2)
        painter.setBrush(colour)
        painter.drawEllipse(QRectF(8, 14, 3, 3))
    elif kind == "log":
        for y in (9, 16, 23):
            painter.drawLine(7, y, 25, y)
        painter.setBrush(colour)
        painter.drawEllipse(4, 7, 1, 1)
    elif kind == "ts":
        # Two states and an arrow between them.
        painter.drawEllipse(QRectF(3, 11, 9, 9))
        painter.drawEllipse(QRectF(20, 11, 9, 9))
        painter.drawLine(13, 15, 18, 15)
        painter.drawLine(15, 12, 18, 15)
        painter.drawLine(15, 18, 18, 15)
    elif kind == "workflow":
        # Two boxes joined by a wire.
        painter.drawRoundedRect(QRectF(3, 6, 11, 9), 2, 2)
        painter.drawRoundedRect(QRectF(18, 17, 11, 9), 2, 2)
        painter.drawLine(14, 10, 18, 21)
    elif kind == "exercise":
        # A sheet with a question mark.
        painter.drawRoundedRect(QRectF(6, 4, 20, 24), 3, 3)
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(17)
        painter.setFont(font)
        painter.drawText(QRectF(6, 4, 20, 24), Qt.AlignCenter, "?")
    else:
        painter.drawEllipse(QRectF(4, 10, 11, 11))
        painter.drawRect(QRectF(19, 9, 9, 13))
    painter.end()
    return pixmap


__all__ = ["app_icon", "faded_icon", "icon", "icon_pixmap"]
