"""Icons and mouse cursors for the drawing tools.

Each tool gets a picture of what it does -- the mouse pointer for *Select*, a
circle for *Place*, a square for *Transition*, a black bar for the silent
*τ* transition, an arrow for *Arc* -- on its button and as the mouse cursor
over the canvas, so the active tool is obvious wherever you look.  Painted
in code: the app ships no image files.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import style


def _pointer(painter: QPainter, size: float, colour: QColor) -> None:
    """The arrow of a mouse pointer, drawn in a ``size`` square: what every
    drawing program shows for its Select tool."""
    s = size / 24.0
    path = QPainterPath(QPointF(6.0 * s, 3.0 * s))
    path.lineTo(QPointF(6.0 * s, 19.5 * s))
    path.lineTo(QPointF(10.2 * s, 15.6 * s))
    path.lineTo(QPointF(13.0 * s, 21.4 * s))
    path.lineTo(QPointF(15.8 * s, 20.0 * s))
    path.lineTo(QPointF(13.0 * s, 14.4 * s))
    path.lineTo(QPointF(18.6 * s, 14.0 * s))
    path.closeSubpath()
    pen = QPen(colour, 1.6 * s)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPath(path)


def _shape(painter: QPainter, kind: str, size: float, colour: QColor) -> None:
    s = size / 24.0
    pen = QPen(colour, 2.0 * s)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    if kind == "place":
        painter.drawEllipse(QRectF(4 * s, 4 * s, 16 * s, 16 * s))
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(12 * s, 12 * s), 2.4 * s, 2.4 * s)
    elif kind == "transition":
        painter.drawRect(QRectF(5 * s, 4 * s, 14 * s, 16 * s))
    elif kind == "silent":
        # The black bar a silent transition is drawn as on the canvas.
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(9 * s, 4 * s, 6 * s, 16 * s), 1.2 * s, 1.2 * s)
    elif kind == "arc":
        painter.drawLine(QPointF(4 * s, 19 * s), QPointF(18 * s, 6 * s))
        head = QPainterPath(QPointF(20 * s, 4 * s))
        head.lineTo(QPointF(12.5 * s, 6.2 * s))
        head.lineTo(QPointF(17.8 * s, 11.5 * s))
        head.closeSubpath()
        painter.setBrush(colour)
        painter.drawPath(head)
    elif kind == "select":
        _pointer(painter, size, colour)


def tool_icon(kind: str) -> QIcon:
    """The button icon for a tool (``select``, ``place``, ``transition``, ``silent``, ``arc``)."""
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    _shape(painter, kind, 24, QColor(style.tokens().text))
    painter.end()
    return QIcon(pixmap)


def tool_cursor(kind: str) -> QCursor:
    """The canvas cursor for a tool: a crosshair with the tool's shape next to
    it (the plain pointer for Select)."""
    if kind == "select":
        return QCursor(Qt.ArrowCursor)
    pixmap = QPixmap(64, 64)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    black = QColor(20, 20, 20)
    for width, colour in ((3.0, QColor(255, 255, 255)), (1.2, black)):   # outlined cross
        painter.setPen(QPen(colour, width))
        painter.drawLine(QPointF(8, 1), QPointF(8, 15))
        painter.drawLine(QPointF(1, 8), QPointF(15, 8))
    painter.translate(13, 13)
    _shape(painter, kind, 16, QColor(style.tokens().accent))
    painter.end()
    return QCursor(pixmap, 8, 8)


def sidebar_icon() -> QIcon:
    """A window with a pane on the left: the button that shows or hides the sidebar."""
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = QColor(style.tokens().text_secondary)
    pen = QPen(colour, 1.5)
    pen.setJoinStyle(Qt.RoundJoin)
    frame = QRectF(2.5, 4.5, 15, 11)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(colour.red(), colour.green(), colour.blue(), 60))
    painter.drawRoundedRect(QRectF(frame.x(), frame.y(), 5.5, frame.height()), 2.5, 2.5)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(frame, 2.5, 2.5)
    painter.drawLine(QPointF(8, frame.top()), QPointF(8, frame.bottom()))
    painter.end()
    return QIcon(pixmap)


def question_icon() -> QIcon:
    """A sheet with a question mark: the button that brings back an exercise's question."""
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = QColor(style.tokens().text_secondary)
    pen = QPen(colour, 1.5)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawRoundedRect(QRectF(4.5, 2.5, 11, 15), 2.5, 2.5)
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(10)
    painter.setFont(font)
    painter.drawText(QRectF(4.5, 2.5, 11, 15), Qt.AlignCenter, "?")
    painter.end()
    return QIcon(pixmap)
