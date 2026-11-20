"""Qt graphics items that draw the net on the canvas.

Coordinate systems
------------------
CPN Tools files place the origin at the centre of the page with **y increasing
upwards**.  Qt's scene has y increasing *downwards*.  The whole GUI therefore
uses one rule, applied in exactly two functions here (:func:`to_scene` /
:func:`to_model`): flip the sign of y at the boundary.  Everywhere else in the
GUI, coordinates are scene coordinates.

Item structure
--------------
Each net element is one top-level item with its labels as *child* items, so
moving a place moves its annotations with it for free.  Labels are plain text
items rather than editable ones: editing happens in the properties panel, which
keeps this module free of input handling.

Where the annotations go, and why
---------------------------------
A place carries up to four pieces of text, and none of them may collide --
neither with each other nor with the annotations of the *next* place along::

          ③ 1`ph(1)++1`ph(3)     <- current marking, centred ABOVE
             ( Think )  PH       <- colour set, to the RIGHT
          1`ph(1)++1`ph(2)…      <- initial marking, centred BELOW

The three long annotations are stacked vertically and each is **centred on the
place**, which is what keeps neighbouring nodes apart: a label capped at ~130 px
and centred extends only ~65 px either side, so two places 250 units apart can
never overprint one another.  Putting the live marking and the declared initial
marking on the same horizontal line -- the obvious first arrangement -- fails
badly here, because those two strings are usually similar in length and content
and end up sitting side by side across the whole diagram.

Long annotations are shortened with an ellipsis and carry the full text in a
tooltip; nothing is lost, because the complete marking is always listed in the
simulation panel.

Arc inscriptions sit at **35 % along the arc**, not at the midpoint.  Two arcs
running between the same neighbourhood in opposite directions have their
midpoints in nearly the same place, so midpoint labels overlap; measuring from
each arc's own source separates them.

Colours come from :mod:`openprocess.gui.theme`, so everything here follows the system
light/dark appearance.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath,
    QPainterPathStroker, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsSimpleTextItem, QStyle,
)

from . import theme

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.net import Arc, Place, Transition


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------
def to_scene(x: float, y: float) -> QPointF:
    """Model coordinates -> scene coordinates (flip y)."""
    return QPointF(x, -y)


def to_model(point: QPointF) -> tuple[float, float]:
    """Scene coordinates -> model coordinates (flip y back)."""
    return point.x(), -point.y()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
#: Longest annotation we will draw before shortening it, in scene units.
#: A marking such as ``1`ph(1)++1`ph(2)++1`ph(3)`` is wider than the gap
#: between two nodes, so drawn in full it runs straight over its neighbour.
#: The full text is always available in the tooltip and in the simulation
#: panel, so shortening on the canvas loses nothing.
MAX_ANNOTATION_WIDTH = 132.0
#: Inscriptions placed by the modeller (positions from a ``.cpn`` file) were
#: laid out for their full text, so they may be much longer before shortening.
MAX_AUTHORED_WIDTH = 340.0


class _Annotation(QGraphicsSimpleTextItem):
    """Text on the canvas, optionally on a soft backdrop of the canvas colour.

    Inscriptions often sit on top of arcs or grid dots; a translucent
    backdrop keeps them legible without boxing them in.
    """

    PAD = 2.0

    def __init__(self, parent: QGraphicsItem, backdrop: bool) -> None:
        super().__init__("", parent)
        self.backdrop = backdrop
        #: Called with the label's new centre (parent coordinates) after the
        #: user dragged it; ``None`` = the label cannot be dragged.
        self.on_dragged = None
        self._drag_from: QPointF | None = None

    # -- dragging (edit mode only) ------------------------------------------------
    def _draggable(self) -> bool:
        scene = self.scene()
        return (self.on_dragged is not None and bool(self.text())
                and getattr(scene, "editable", False) and getattr(scene, "tool", "") == "select")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or not self._draggable():
            event.ignore()                       # let the node underneath handle it
            return
        event.accept()
        owner = self.parentItem()
        if owner is not None and owner.flags() & QGraphicsItem.ItemIsSelectable \
                and not owner.isSelected():
            if not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.scene().clearSelection()
            owner.setSelected(True)
        self._drag_from = event.scenePos() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        self.setPos(event.scenePos() - self._drag_from)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        self._drag_from = None
        centre = self.pos() + super().boundingRect().center()
        self.on_dragged(centre)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.OpenHandCursor if self._draggable() else Qt.ArrowCursor)

    def boundingRect(self) -> QRectF:  # noqa: N802
        rect = super().boundingRect()
        return rect.adjusted(-self.PAD, -1, self.PAD, 1) if self.backdrop else rect

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        if self.backdrop and self.text():
            colour = QColor(theme.palette().canvas)
            colour.setAlphaF(0.86)
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(self.boundingRect(), 3, 3)
        super().paint(painter, option, widget)


class _MarkingBox(QGraphicsSimpleTextItem):
    """The current marking of a place, in a pale green box with a shadow --
    how CPN Tools and CPN IDE show it next to the token count."""

    PAD_X, PAD_Y = 5.0, 2.0

    def boundingRect(self) -> QRectF:  # noqa: N802
        return super().boundingRect().adjusted(-self.PAD_X, -self.PAD_Y,
                                               self.PAD_X + 1.5, self.PAD_Y + 1.5)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        box = super().boundingRect().adjusted(-self.PAD_X, -self.PAD_Y,
                                              self.PAD_X, self.PAD_Y)
        dark = theme.is_dark()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(box.translated(1.5, 1.5), 3, 3)
        painter.setBrush(QColor("#1F4D24" if dark else "#C9F5C4"))
        painter.setPen(QPen(QColor("#3C8C44" if dark else "#7CCB76"), 0.8))
        painter.drawRoundedRect(box, 3, 3)
        super().paint(painter, option, widget)


class _CountBubble(QGraphicsPathItem):
    """The green token count.  Clicking it shows or hides the marking box,
    as in CPN Tools and CPN IDE."""

    def __init__(self, parent: "PlaceItem") -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptedMouseButtons(Qt.LeftButton)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()                      # not a drag of the place
        self.parentItem().toggle_marking()


def _label(parent: QGraphicsItem, colour: QColor, font: QFont,
           backdrop: bool = True) -> QGraphicsSimpleTextItem:
    item = _Annotation(parent, backdrop)
    item.setBrush(QBrush(colour))
    item.setFont(font)
    return item


def _draggable_label(item: _Annotation, node: "_NodeItem", key: str) -> None:
    """Let the user drag a node's inscription; its spot is kept in the model
    (as an offset from the node's centre, in model units -- the ``.cpn`` way)."""
    def dragged(centre: QPointF) -> None:
        offsets = node.graphics().label_offsets
        offsets[key] = (centre.x(), -centre.y())
        node.refresh()

    item.on_dragged = dragged
    item.setAcceptHoverEvents(True)


#: Sizes of plain-net nodes (scene units): places are circles, transitions
#: squares that grow to fit their name, silent transitions black bars.
PLAIN_PLACE = 40.0
PLAIN_TRANSITION = 40.0
PLAIN_SILENT = (14.0, 40.0)


def _paint_tokens(painter: QPainter, count: int, rect: QRectF) -> None:
    """Black tokens inside a place, arranged like dice up to five; a number
    beyond that -- how the course's figures draw markings."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    colour = theme.palette().text
    if count > 5:
        painter.setPen(colour)
        font = theme.ui_font(12, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, str(count))
        painter.restore()
        return
    r = min(rect.width(), rect.height()) / 2
    d = r * 0.36                                    # distance from the centre
    spots = {1: [(0, 0)], 2: [(-d, 0), (d, 0)], 3: [(-d, d * 0.6), (d, d * 0.6), (0, -d * 0.8)],
             4: [(-d, -d), (d, -d), (-d, d), (d, d)],
             5: [(-d, -d), (d, -d), (0, 0), (-d, d), (d, d)]}[count]
    dot = r * (0.2 if count > 1 else 0.24)
    painter.setPen(Qt.NoPen)
    painter.setBrush(colour)
    centre = rect.center()
    for x, y in spots:
        painter.drawEllipse(QPointF(centre.x() + x, centre.y() + y), dot, dot)
    painter.restore()


def _paint_token_row(painter: QPainter, count: int, centre: QPointF) -> None:
    """Tokens as a small row of dots under a name (a number beyond five)."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    colour = theme.palette().text
    if count > 5:
        painter.setPen(colour)
        painter.setFont(theme.ui_font(9, QFont.Bold))
        painter.drawText(QRectF(centre.x() - 20, centre.y() - 7, 40, 14), Qt.AlignCenter,
                         f"{count} ●")
    else:
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        gap = 6.5
        start = centre.x() - gap * (count - 1) / 2
        for index in range(count):
            painter.drawEllipse(QPointF(start + gap * index, centre.y()), 2.6, 2.6)
    painter.restore()


def _rect_of(item: "_NodeItem") -> tuple[float, float, float, float]:
    """A node's outline rectangle in scene coordinates (left, top, width, height)."""
    rect = item.rect().translated(item.pos())
    return rect.left(), rect.top(), rect.width(), rect.height()


def _place_at_offset(item: QGraphicsSimpleTextItem, offsets: dict, key: str) -> bool:
    """Centre ``item`` where the model file put it (an offset in model units).

    Returns False when the file gave no position, so the caller can fall back
    to the automatic placement.
    """
    offset = offsets.get(key)
    if offset is None:
        return False
    bounds = item.boundingRect()
    # Model y points up, scene y points down.
    item.setPos(offset[0] - bounds.width() / 2, -offset[1] - bounds.height() / 2)
    return True


def _set_elided(item: QGraphicsSimpleTextItem, text: str,
                max_width: float = MAX_ANNOTATION_WIDTH) -> None:
    """Set an annotation, shortening it with an ellipsis if it is too wide.

    The untruncated string goes into the tooltip, so hovering always shows the
    real inscription.
    """
    metrics = QFontMetricsF(item.font())
    if metrics.horizontalAdvance(text) > max_width:
        item.setText(metrics.elidedText(text, Qt.ElideRight, max_width))
        item.setToolTip(text)
    else:
        item.setText(text)
        item.setToolTip("")


class _NodeItem(QGraphicsPathItem):
    """Shared behaviour for places and transitions.

    Both are rounded shapes with a name inside, both are movable, and both
    write their position back to the model as they move.  Factoring that out
    keeps the two concrete classes to just their own drawing and labels.

    Selection is drawn by us, not by Qt: the default is a dashed marching-ants
    rectangle, which looks dated and obscures the annotations.  We strip the
    selected state before delegating to the base paint and draw an accent ring
    instead.
    """

    def __init__(self, rect: QRectF) -> None:
        super().__init__()
        self._rect = rect
        #: Draw a soft accent halo (the transition that fired last).
        self.halo = False
        #: Step numbers at which this transition fired (the trace badge).
        self.trace_steps: list[int] = []
        #: How recent its last firing is, 1 (just now) .. 0 (not in the trace).
        self.trace = 0.0
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(10)

    def rect(self) -> QRectF:
        """The node's bounding shape, used by :class:`ArcItem` for clipping."""
        return self._rect

    def _build_path(self) -> QPainterPath:
        """The outline for the current :attr:`_rect`.  Supplied by subclasses."""
        raise NotImplementedError

    def _grow_to_fit(self, content_width: float, padding: float) -> bool:
        """Widen the node so its name fits, and report whether it changed.

        A node narrower than its own label looks broken, and CPN Tools sizes
        nodes to their names too.  The new width is written back to the model,
        so it survives a save and the diagram opens the same way next time.
        """
        needed = content_width + padding
        if needed <= self._rect.width() + 0.5:
            return False
        height = self._rect.height()
        self._rect = QRectF(-needed / 2, -height / 2, needed, height)
        self.setPath(self._build_path())
        return True

    def boundingRect(self) -> QRectF:  # noqa: N802
        # Room for the halo and the selection ring outside the outline, and
        # for the trace badge above the top-right corner.
        extra = (24, 90) if self.trace_steps else (6, 6)
        return super().boundingRect().adjusted(-6, -extra[0], extra[1], 6)

    def set_halo(self, on: bool) -> None:
        if on != self.halo:
            self.halo = on
            self.update()

    def set_trace(self, steps: list[int], strength: float) -> None:
        """Mark the node as part of the step-through trace (see NetScene.set_trace)."""
        if steps == self.trace_steps and abs(strength - self.trace) < 1e-9:
            return
        self.prepareGeometryChange()
        self.trace_steps, self.trace = list(steps), strength
        self.update()

    def _paint_trace_badge(self, painter: QPainter) -> None:
        """The step numbers, in a small accent pill above the top-right corner."""
        steps = self.trace_steps
        text = ("… " if len(steps) > 3 else "") + ", ".join(str(s) for s in steps[-3:])
        font = theme.ui_font(9, QFont.DemiBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        width, height = metrics.horizontalAdvance(text) + 10, metrics.height() + 2
        corner = self._rect.topRight()
        pill = QRectF(corner.x() - width * 0.35, corner.y() - height - 3, width, height)
        colour = QColor(theme.palette().accent)
        colour.setAlphaF(0.45 + 0.55 * self.trace)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(pill, height / 2, height / 2)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pill, Qt.AlignCenter, text)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        selected = bool(option.state & QStyle.State_Selected)
        option.state &= ~QStyle.State_Selected
        if self.halo:
            colour = QColor(theme.palette().accent)
            colour.setAlphaF(0.30)
            painter.setPen(QPen(colour, 8.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path())
        super().paint(painter, option, widget)
        if selected:
            palette = theme.palette()
            pen = QPen(palette.accent, 2.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path())
        if self.trace_steps:
            self._paint_trace_badge(painter)

    def graphics(self):
        """The model's graphics record for this node."""
        raise NotImplementedError

    def name_anchor(self) -> tuple[QPointF, float]:
        """Where the name is shown (scene point) and how wide an editor for
        it should be at least: over the node when the name is inside it,
        over the label when it is outside."""
        label = getattr(self, "name_label", None)
        if getattr(self, "names_outside", False) and label is not None and label.isVisible():
            return label.sceneBoundingRect().center(), 60.0
        return self.scenePos(), max(60.0, self._rect.width())

    def refresh(self) -> None:
        raise NotImplementedError

    def _notify_arcs(self) -> None:
        """Ask every arc touching this node to recompute its route."""
        scene = self.scene()
        if scene is None:
            return
        identifier = getattr(self, "model_id", None)
        for item in scene.items():
            if isinstance(item, ArcItem) and item.touches(identifier):
                item.reroute()


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------
class PlaceItem(_NodeItem):
    """An ellipse with its name inside and four annotations around it."""

    def __init__(self, place: "Place", plain: bool = False,
                 names_outside: bool = False) -> None:
        #: Drawn as in the course's plain Petri nets: a circle with black
        #: tokens inside.
        self.plain = plain
        #: The name under the node (draggable) rather than inside it.
        self.names_outside = names_outside
        if plain:
            width = height = PLAIN_PLACE
        else:
            width = place.graphics.width or 76.0
            height = place.graphics.height or 48.0
        super().__init__(QRectF(-width / 2, -height / 2, width, height))
        self.place = place
        self.model_id = place.id
        self.tokens = 0

        self.setPath(self._build_path())

        palette = theme.palette()
        self.setBrush(QBrush(palette.surface))
        self.setPen(QPen(palette.border_strong, 1.6))

        self.name_label = _label(self, palette.text, theme.ui_font(12, QFont.DemiBold), False)
        type_font = theme.ui_font(10)
        type_font.setItalic(True)
        self.type_label = _label(self, palette.text_muted, type_font)
        self.initial_label = _label(self, palette.inscription, theme.mono_font(10))
        _draggable_label(self.type_label, self, "type")
        _draggable_label(self.initial_label, self, "initmark")
        if names_outside:
            self.name_label.backdrop = True
            _draggable_label(self.name_label, self, "name")

        # The live marking, as in CPN Tools: a green token count at the
        # place's right-hand edge, and next to it the tokens themselves in a
        # pale green box that clicking the count shows or hides.
        self.count_pill = _CountBubble(self)
        self.count_pill.setPen(QPen(Qt.NoPen))
        self.count_pill.setBrush(QBrush(palette.success))
        self.count_label = _label(self.count_pill, QColor("#FFFFFF"),
                                  theme.ui_font(10, QFont.Bold), False)
        self.marking_label = _MarkingBox(self)
        self.marking_label.setFont(theme.mono_font(10))
        self.marking_label.setBrush(QBrush(QColor("#D7F5D7" if theme.is_dark() else "#113311")))
        self.marking_label.setZValue(2)
        self._marking_state = (0, "", None)
        self.set_marking(0, "")

        self.setPos(to_scene(place.graphics.x, place.graphics.y))
        self.refresh()

    # -- presentation --------------------------------------------------------
    def graphics(self):
        return self.place.graphics

    def _build_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self._rect)
        return path

    def refresh(self) -> None:
        """Re-read the model object and lay the annotations out."""
        self.name_label.setText(self.place.name)
        bounds = self.name_label.boundingRect()
        if self.names_outside:
            # The name under the node (or where the user dragged it).
            if not _place_at_offset(self.name_label, self.place.graphics.label_offsets, "name"):
                self.name_label.setPos(-bounds.width() / 2, self._rect.bottom() + 1)
        elif self.plain:
            # A circle just big enough for the name.
            diameter = max(PLAIN_PLACE, bounds.width() + 16)
            if abs(diameter - self._rect.width()) > 0.5:
                self.prepareGeometryChange()
                self._rect = QRectF(-diameter / 2, -diameter / 2, diameter, diameter)
                self.setPath(self._build_path())
            self._layout_inside_name()
        else:
            # An ellipse only offers about 70 % of its width at the text's
            # height, so the name needs proportionally more room here than in
            # a rectangle.
            if self._grow_to_fit(bounds.width() / 0.70, 12.0):
                self.place.graphics.width = self._rect.width()
            self.name_label.setPos(-bounds.width() / 2, -bounds.height() / 2)
        if self.plain:
            self.type_label.hide()
            self.initial_label.hide()
            return
        rect = self._rect

        # Inscriptions go where the model file put them; otherwise at CPN
        # Tools' default spots: colour set below right, initial marking above
        # right.
        offsets = self.place.graphics.label_offsets
        self.type_label.setText(self.place.colour_set_name)
        if not _place_at_offset(self.type_label, offsets, "type"):
            self.type_label.setPos(rect.right() - 4, rect.bottom() - 4)

        _set_elided(self.initial_label, self.place.initial_marking_text,
                    MAX_AUTHORED_WIDTH if "initmark" in offsets else MAX_ANNOTATION_WIDTH)
        if not _place_at_offset(self.initial_label, offsets, "initmark"):
            initial_bounds = self.initial_label.boundingRect()
            self.initial_label.setPos(rect.right() - 4, rect.top() - initial_bounds.height() + 4)

    def set_marking(self, count: int, text: str, show_values: bool | None = None) -> None:
        """Show the simulator's current contents at the place.

        ``count`` goes in the green bubble at the place's right-hand edge;
        ``text`` (the multiset) in the box beside it, which is shown or
        hidden per place -- the model file's own setting at first, then by
        clicking the bubble -- unless ``show_values`` forces all on or off.
        The tooltip always has the full marking.  An empty place shows
        nothing.
        """
        self._marking_state = (count, text, show_values)
        if self.plain:
            self.tokens = count
            self.count_pill.hide()
            self.marking_label.hide()
            self.setToolTip(f"{self.place.name}: {count} token(s)")
            if not self.names_outside:
                self._layout_inside_name()
            self.update()
            return
        visible = count > 0
        self.count_pill.setVisible(visible)
        if show_values is None:
            show_values = not self.place.graphics.marking_hidden
        self.marking_label.setVisible(visible and show_values)
        tip = "" if not visible else f"{self.place.name}: {count} token(s)\n" + (
            text if len(text) < 1500 else text[:1500] + " …")
        self.setToolTip(tip)
        self.count_pill.setToolTip(tip + ("\n\nClick to show or hide the tokens" if tip else ""))
        if not visible:
            return

        self.count_label.setText(str(count))
        label_bounds = self.count_label.boundingRect()
        pill_height = 16.0
        pill_width = max(pill_height, label_bounds.width() + 9)
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(0, 0, pill_width, pill_height),
                            pill_height / 2, pill_height / 2)
        self.count_pill.setPath(pill)
        self.count_label.setPos((pill_width - label_bounds.width()) / 2,
                                (pill_height - label_bounds.height()) / 2)

        # CPN Tools' convention (as CPN IDE reads it): the count's corner is
        # at the place's right edge, level with its centre, moved by the
        # file's "token" offset; the marking box is to the right of the count,
        # moved by the "marking" offset.  Offsets from a file that would put
        # them far away are ignored.
        tx, ty = self.place.graphics.label_offsets.get("token", (0.0, 0.0))
        if abs(tx) > 60 or abs(ty) > 60:
            tx = ty = 0.0
        left, top = self._rect.right() + tx - 4, -ty - pill_height / 2
        self.count_pill.setPos(left, top)

        _set_elided(self.marking_label, text, 260.0)
        mx, my = self.place.graphics.label_offsets.get("marking", (0.0, 0.0))
        if abs(mx) > 120 or abs(my) > 120:
            mx = my = 0.0
        box_left = max(left + pill_width + 6, left + pill_width + 6 + mx)
        text_bounds = self.marking_label.boundingRect()
        self.marking_label.setPos(box_left + _MarkingBox.PAD_X,
                                  top - my + (pill_height - text_bounds.height()) / 2
                                  + _MarkingBox.PAD_Y)

    def _name_inside_with_tokens(self) -> bool:
        return self.plain and not self.names_outside and bool(self.place.name) and \
            self.tokens > 0

    def _layout_inside_name(self) -> None:
        """Centre the name in the circle; with tokens, the name moves up and
        the tokens go in a row underneath it."""
        bounds = self.name_label.boundingRect()
        lift = 6.0 if self._name_inside_with_tokens() else 0.0
        self.name_label.setPos(-bounds.width() / 2, -bounds.height() / 2 - lift)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        super().paint(painter, option, widget)
        if not (self.plain and self.tokens):
            return
        if self._name_inside_with_tokens():
            _paint_token_row(painter, self.tokens, QPointF(0, 9.0))
        else:
            _paint_tokens(painter, self.tokens, self._rect)

    def toggle_marking(self) -> None:
        """Clicking the count: show or hide this place's tokens."""
        self.place.graphics.marking_hidden = self.marking_label.isVisible()
        count, text, _forced = self._marking_state
        self.set_marking(count, text, None)

    def set_error(self, has_error: bool) -> None:
        palette = theme.palette()
        self.setPen(QPen(palette.danger if has_error else palette.border_strong, 1.6))

    def itemChange(self, change, value):  # noqa: N802 - Qt naming
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.place.graphics.x, self.place.graphics.y = to_model(self.pos())
            self._notify_arcs()
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------
class TransitionItem(_NodeItem):
    """A rounded rectangle with name, guard and time labels, plus the enabling cue."""

    def __init__(self, transition: "Transition", plain: bool = False,
                 names_outside: bool = False) -> None:
        #: Plain nets: a square with the name inside; a silent (τ) transition
        #: is a black bar, as in the course's figures.
        self.plain = plain
        #: The name under the box (draggable) rather than inside it.
        self.names_outside = names_outside
        self.silent = plain and getattr(transition, "silent", False)
        if self.silent:
            width, height = PLAIN_SILENT
        elif plain:
            width = height = PLAIN_TRANSITION
        else:
            width = transition.graphics.width or 82.0
            height = transition.graphics.height or 48.0
        super().__init__(QRectF(-width / 2, -height / 2, width, height))
        self.transition = transition
        self.model_id = transition.id
        self.enabled_count = 0
        self._has_error = False

        self.setPath(self._build_path())

        palette = theme.palette()
        self.name_label = _label(self, palette.text, theme.ui_font(12, QFont.DemiBold), False)
        self.guard_label = _label(self, palette.inscription, theme.mono_font(10))
        self.time_label = _label(self, palette.inscription, theme.mono_font(10))
        _draggable_label(self.guard_label, self, "cond")
        _draggable_label(self.time_label, self, "time")
        if names_outside:
            self.name_label.backdrop = True
            _draggable_label(self.name_label, self, "name")

        # The binding count sits in its own pill, mirroring the place's marking
        # pill so the two read as the same kind of information.
        self.count_pill = QGraphicsPathItem(self)
        self.count_pill.setPen(QPen(Qt.NoPen))
        self.count_pill.setBrush(QBrush(palette.success))
        self.count_label = _label(self.count_pill, QColor("#FFFFFF"),
                                  theme.ui_font(10, QFont.Bold), False)
        self.count_pill.setVisible(False)

        self.setPos(to_scene(transition.graphics.x, transition.graphics.y))
        self.refresh()
        self.set_enabled_count(0)

    def graphics(self):
        return self.transition.graphics

    def _build_path(self) -> QPainterPath:
        path = QPainterPath()
        radius = 2.0 if getattr(self, "plain", False) else theme.RADIUS
        path.addRoundedRect(self._rect, radius, radius)
        return path

    def refresh(self) -> None:
        self.name_label.setText(self.transition.name)
        bounds = self.name_label.boundingRect()
        if self.names_outside:
            if not _place_at_offset(self.name_label, self.transition.graphics.label_offsets,
                                    "name"):
                self.name_label.setPos(-bounds.width() / 2, self._rect.bottom() + 1)
        if self.plain:
            self.guard_label.hide()
            self.time_label.hide()
            self.name_label.setVisible(not self.silent)
            self.setToolTip(f"τ (silent){': ' + self.transition.name if self.transition.name else ''}"
                            if self.silent else "")
            if not self.silent and not self.names_outside:
                self._grow_to_fit(bounds.width(), 14.0)
                self.name_label.setPos(-bounds.width() / 2, -bounds.height() / 2)
            return
        if not self.names_outside:
            if self._grow_to_fit(bounds.width(), 26.0):
                self.transition.graphics.width = self._rect.width()
            self.name_label.setPos(-bounds.width() / 2, -bounds.height() / 2)
        rect = self._rect

        # Guards are conventionally shown in square brackets, to the left.
        offsets = self.transition.graphics.label_offsets
        guard = self.transition.guard_text.strip()
        if guard and not guard.startswith("["):
            guard = f"[{guard}]"
        _set_elided(self.guard_label, guard, MAX_AUTHORED_WIDTH if "cond" in offsets else 200)
        if not _place_at_offset(self.guard_label, offsets, "cond"):
            # CPN Tools' default spot: above, to the left.
            bounds = self.guard_label.boundingRect()
            self.guard_label.setPos(rect.left() - 5 - bounds.width() + 8,
                                    rect.top() - bounds.height() + 2)

        # The file may store "@+5" or just "5"; show it once as "@+5".
        delay = self.transition.time_text.strip()
        if delay.startswith("@+"):
            delay = delay[2:].strip()
        _set_elided(self.time_label, f"@+{delay}" if delay else "",
                    MAX_AUTHORED_WIDTH if "time" in offsets else 200)
        if not _place_at_offset(self.time_label, offsets, "time"):
            # Above, to the right.
            bounds = self.time_label.boundingRect()
            self.time_label.setPos(rect.right() - 3, rect.top() - bounds.height() + 2)

    def set_enabled_count(self, count: int) -> None:
        """Colour the transition according to how many bindings are enabled."""
        self.enabled_count = count
        palette = theme.palette()

        if self.silent:
            self.setBrush(QBrush(palette.text))
            self.setPen(QPen(palette.success if count else palette.text, 2.4 if count else 1.2))
        elif self._has_error:
            self.setBrush(QBrush(palette.surface))
            self.setPen(QPen(palette.danger, 1.8))
        elif count:
            self.setBrush(QBrush(palette.success_soft))
            self.setPen(QPen(palette.success, 2.2))
        else:
            self.setBrush(QBrush(palette.surface))
            # A substitution transition gets a heavier border, as in CPN Tools,
            # because it stands for a whole subpage rather than an event.
            self.setPen(QPen(palette.border_strong,
                             3.0 if self.transition.is_substitution else 1.6))

        # Only show the pill when there is a genuine choice to make.
        self.count_pill.setVisible(count > 1 and not self.plain)
        if count > 1 and not self.plain:
            self.count_label.setText(str(count))
            bounds = self.count_label.boundingRect()
            width = max(18.0, bounds.width() + 10)
            height = 16.0
            pill = QPainterPath()
            pill.addRoundedRect(QRectF(0, 0, width, height), height / 2, height / 2)
            self.count_pill.setPath(pill)
            self.count_label.setPos((width - bounds.width()) / 2,
                                    (height - bounds.height()) / 2)
            self.count_pill.setPos(self._rect.right() - width / 2,
                                   self._rect.bottom() - height / 2 - 1)

    def set_error(self, has_error: bool) -> None:
        self._has_error = has_error
        self.set_enabled_count(self.enabled_count)

    def itemChange(self, change, value):  # noqa: N802 - Qt naming
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.transition.graphics.x, self.transition.graphics.y = to_model(self.pos())
            self._notify_arcs()
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# Arcs
# ---------------------------------------------------------------------------
class ArcItem(QGraphicsPathItem):
    """A directed arc with an arrowhead and an inscription label.

    Three routing features, each fixing a way that a naive straight line
    between two centres goes wrong:

    **Endpoint clipping.**  The line stops at the boundary of each shape, so
    the arrowhead lands on the edge of the transition rather than at its centre.

    **Bendpoints.**  ``.cpn`` files store polyline bendpoints, and real models
    use them heavily to route around nodes.  Ignoring them would make an
    imported model look nothing like it does in CPN Tools, so the path follows
    them when present.

    **Parallel arcs.**  Two arcs between the *same* place and transition -- a
    consume-and-return pair, say -- would otherwise be drawn exactly on top of
    each other.  When the scene reports that an arc has siblings, it is
    shifted sideways (``bow``) so both are visible, as in CPN IDE.

    Editing (CPN IDE's rules, see :mod:`openprocess.gui.arc_editing`) is driven by
    the scene; this item draws the handles: a small circle on every waypoint
    while the arc is hovered or selected, one more following the mouse along
    the line, and a bar in the middle of each horizontal or vertical segment.
    """

    #: Trace highlight strength, 0 (off) .. 1 (the latest step).
    trace = 0.0

    ARROW_LENGTH = 10.0
    ARROW_HALF_WIDTH = 4.2
    #: Where along the arc the inscription sits.  Not 0.5: see the module
    #: docstring -- opposing arcs would collide at the midpoint.
    LABEL_POSITION = 0.35
    #: Handle radius on screen, in pixels (diagram-js: 4).
    HANDLE_RADIUS = 4.0

    def __init__(self, arc: "Arc", place_item: PlaceItem,
                 transition_item: TransitionItem, bow: float = 0.0,
                 plain: bool = False) -> None:
        super().__init__()
        self.plain = plain
        self.arc = arc
        self.place_item = place_item
        self.transition_item = transition_item
        self.bow = bow

        palette = theme.palette()
        self.setPen(QPen(palette.border_strong, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setBrush(QBrush(palette.border_strong))
        self.setZValue(5)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

        self.inscription_label = _label(self, palette.inscription, theme.mono_font(10))
        self.inscription_label.on_dragged = self._inscription_dragged
        self.inscription_label.setAcceptHoverEvents(True)
        #: Where the modeller put the inscription, relative to the midpoint
        #: of the two nodes (so it follows them when they move).
        self._label_offset: QPointF | None = None
        absolute = arc.graphics.label_offsets.get("annot")
        if absolute is not None and arc.graphics.label_offsets:
            self._label_offset = to_scene(*absolute) - self._node_midpoint()
        #: While nodes are dragged: the arc as it was when the drag began
        #: (transition position, place position, bends, both end points).
        self.drag_origin: tuple | None = None
        #: While one end is dragged to another node: which end and where.
        self.loose_end: tuple[str, QPointF] | None = None
        #: Hover state for the handles.
        self.hovered = False
        self.floating: QPointF | None = None
        #: True while the scene drags part of this arc (handles hidden).
        self.editing = False
        self._waypoints: list[QPointF] = []
        self.reroute()

    def _node_midpoint(self) -> QPointF:
        return (self.place_item.pos() + self.transition_item.pos()) / 2

    def touches(self, element_id: str | None) -> bool:
        return element_id is not None and element_id in (
            self.arc.place_id, self.arc.transition_id
        )

    def waypoints(self) -> list[QPointF]:
        """The drawn route, from the transition end to the place end: where
        it leaves one node, the bends, where it meets the other."""
        return list(self._waypoints)

    # -- geometry ------------------------------------------------------------
    #: A bendpoint within this distance (horizontally or vertically) of a
    #: node's centre makes the arc leave the node straight across or straight
    #: down -- CPN Tools' tidy right-angled arcs.
    SNAP = 20.0

    def reroute(self) -> None:
        """Recompute the path, the arrowhead(s) and the label position.

        The rules are those of CPN Tools and CPN IDE:

        * an input arc runs place -> transition, every other one (output
          and double-headed) transition -> place;
        * bendpoints are stored from the transition end to the place end,
          and are joined by straight segments;
        * a first or last bendpoint roughly level with (or above) its node
          pulls the end of the arc level with it, so it leaves the node at a
          right angle instead of heading for the exact centre;
        * arcs between the same place and transition without bendpoints are
          drawn side by side as parallel lines.
        """
        self._repair_after_node_move()
        stored = [to_scene(x, y) for x, y in self.arc.bendpoints]   # transition -> place
        loose = self.loose_end
        if loose is not None:
            # One end follows the mouse (reconnecting): no cropping there.
            which, point = loose
            if which == "place":
                towards = stored[0] if stored else point
                t_end = self._crop(self.transition_item,
                                   self._anchor(self.transition_item, towards), towards)
                p_end = point
            else:
                towards = stored[-1] if stored else point
                p_end = self._crop(self.place_item,
                                   self._anchor(self.place_item, towards), towards)
                t_end = point
        elif stored:
            t_end = self._crop(self.transition_item,
                               self._anchor(self.transition_item, stored[0]), stored[0])
            p_end = self._crop(self.place_item,
                               self._anchor(self.place_item, stored[-1]), stored[-1])
        else:
            # Parallel arcs: shift both ends sideways by the same amount.
            # The side is measured in one fixed direction (place ->
            # transition), so an input and an output arc between the same
            # nodes go to opposite sides instead of on top of each other.
            chord = self.transition_item.pos() - self.place_item.pos()
            length = math.hypot(chord.x(), chord.y()) or 1.0
            shift = QPointF(-chord.y() / length, chord.x() / length) * self.bow
            a = self.transition_item.pos() + shift
            b = self.place_item.pos() + shift
            t_end = self._crop(self.transition_item, a, b)
            p_end = self._crop(self.place_item, b, a)
        self._waypoints = [t_end, *stored, p_end]

        route = self._waypoints if not (self.arc.is_input and not self.arc.is_output) \
            else self._waypoints[::-1]
        path = QPainterPath(route[0])
        for point in route[1:]:
            path.lineTo(point)

        # The line is stroked and the arrowheads are filled, so they live in
        # separate paths.  (One combined, filled path would also fill the area
        # enclosed by a bent line -- the "black triangles" bug.)
        self.prepareGeometryChange()
        self.line_path = QPainterPath(path)
        arrows = QPainterPath()
        self._append_arrowhead(arrows, self._tangent_source(path, 0.97), route[-1])
        if self.arc.orientation == "BOTHDIR":
            self._append_arrowhead(arrows, self._tangent_source(path, 0.03), route[0])
        self.arrow_path = arrows
        combined = QPainterPath(path)
        combined.addPath(arrows)
        self.setPath(combined)

        self._place_label(path)
        self.update()

    def _repair_after_node_move(self) -> None:
        """While nodes are dragged: CPN IDE's rule for the bends.

        Moving both ends moves the whole arc.  Moving one end leaves the
        bends where they are, except that the bend next to the moved node
        stays level with (or in line with) the arc's end if it was, and
        bends the node now covers are dropped
        (:func:`~openprocess.gui.arc_editing.repair_after_move`).
        """
        if self.drag_origin is None:
            return
        t0, p0, bends, t_end, p_end = self.drag_origin
        dt = self.transition_item.pos() - t0
        dp = self.place_item.pos() - p0
        t_moved, p_moved = dt.manhattanLength() > 1e-6, dp.manhattanLength() > 1e-6
        if not bends or not (t_moved or p_moved):
            return
        if t_moved and p_moved:
            self.arc.bendpoints = [to_model(QPointF(x, y) + dt) for x, y in bends]
            return
        from .arc_editing import repair_after_move
        t_rect, p_rect = _rect_of(self.transition_item), _rect_of(self.place_item)
        if t_moved:
            points = [t_end, *bends, p_end]
            moved_end = (t_end[0] + dt.x(), t_end[1] + dt.y())
            result = repair_after_move(points, moved_end, t_rect, p_rect)
        else:
            points = [p_end, *bends[::-1], t_end]
            moved_end = (p_end[0] + dp.x(), p_end[1] + dp.y())
            result = repair_after_move(points, moved_end, p_rect, t_rect)[::-1]
        self.arc.bendpoints = [to_model(QPointF(*p)) for p in result[1:-1]]

    def start_node_drag(self) -> None:
        """Remember the arc before its nodes are dragged (see above)."""
        ends = self._waypoints
        self.drag_origin = (self.transition_item.pos(), self.place_item.pos(),
                            [(p.x(), p.y()) for p in (to_scene(x, y)
                                                      for x, y in self.arc.bendpoints)],
                            (ends[0].x(), ends[0].y()), (ends[-1].x(), ends[-1].y()))

    def _inscription_dragged(self, centre: QPointF) -> None:
        self._label_offset = centre - self._node_midpoint()
        self.reroute()

    # -- handles ---------------------------------------------------------------
    def handles_visible(self) -> bool:
        scene = self.scene()
        return (not self.editing and (self.isSelected() or self.hovered)
                and getattr(scene, "editable", False)
                and getattr(scene, "tool", "") == "select")

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self.hovered = True
        self.hoverMoveEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        from .arc_editing import closest_on_segment, distance
        p = (event.scenePos().x(), event.scenePos().y())
        points = [(w.x(), w.y()) for w in self._waypoints]
        feet = [closest_on_segment(p, a, b) for a, b in zip(points, points[1:])]
        foot = min(feet, key=lambda f: distance(f, p)) if feet else None
        self.floating = QPointF(*foot) if foot else None
        self.update()

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = False
        self.floating = None
        self.update()

    def itemChange(self, change, value):  # noqa: N802 - Qt naming
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    @staticmethod
    def _tangent_source(path: QPainterPath, percent: float) -> QPointF:
        """A point just short of an end, giving the direction to point the head."""
        return path.pointAtPercent(max(0.0, min(1.0, percent)))

    def _place_label(self, path: QPainterPath) -> None:
        """Put the inscription a third of the way along, clear of the line."""
        if self.plain:
            # Plain nets: only a weight above 1 is written, as a number.
            from ..model.plain import weight_of
            weight = weight_of(self.arc)
            self.inscription_label.setText(str(weight) if weight > 1 else "")
            if weight <= 1:
                return
            if self._label_offset is None:
                point = path.pointAtPercent(0.5)
                angle = math.radians(path.angleAtPercent(0.5))
                normal = QPointF(math.sin(angle), math.cos(angle)) * 9.0
                bounds = self.inscription_label.boundingRect()
                if normal.y() > 0:
                    normal = -normal
                self.inscription_label.setPos(point.x() + normal.x() - bounds.width() / 2,
                                              point.y() + normal.y() - bounds.height() / 2)
                return
            centre = self._node_midpoint() + self._label_offset
            bounds = self.inscription_label.boundingRect()
            self.inscription_label.setPos(centre.x() - bounds.width() / 2,
                                          centre.y() - bounds.height() / 2)
            self.arc.graphics.label_offsets["annot"] = to_model(centre)
            return
        _set_elided(self.inscription_label, self.arc.expression_text,
                    MAX_AUTHORED_WIDTH if self._label_offset is not None
                    else MAX_ANNOTATION_WIDTH)
        if not self.arc.expression_text:
            return
        if self._label_offset is not None:
            centre = self._node_midpoint() + self._label_offset
            bounds = self.inscription_label.boundingRect()
            self.inscription_label.setPos(centre.x() - bounds.width() / 2,
                                          centre.y() - bounds.height() / 2)
            # Keep the model in step, so saving writes where it is drawn.
            self.arc.graphics.label_offsets["annot"] = to_model(centre)
            return

        anchor = path.pointAtPercent(self.LABEL_POSITION)
        angle = math.radians(path.angleAtPercent(self.LABEL_POSITION))
        # Qt reports the angle counter-clockwise from the x axis in a
        # y-down scene, hence the negated sine.
        tangent = QPointF(math.cos(angle), -math.sin(angle))
        normal = QPointF(-tangent.y(), tangent.x())

        bounds = self.inscription_label.boundingRect()
        if abs(self.bow) > 0.01 and not self.arc.bendpoints:
            # One of a pair of parallel arcs: write on its own side, so the
            # two inscriptions of a pair end up on opposite sides.
            chord = self.transition_item.pos() - self.place_item.pos()
            length = math.hypot(chord.x(), chord.y()) or 1.0
            side = QPointF(-chord.y() / length, chord.x() / length) * math.copysign(1, self.bow)
            # Far enough that the text box clears the line, whatever the angle.
            reach = 6.0 + abs(side.x()) * bounds.width() / 2 + abs(side.y()) * bounds.height() / 2
            offset = side * reach
        else:
            # Offset to whichever side of the line points "up" on screen, so
            # the text never sits on top of the arc.
            offset = normal * (12.0 if normal.y() < 0 else -12.0)
        self.inscription_label.setPos(
            anchor.x() + offset.x() - bounds.width() / 2,
            anchor.y() + offset.y() - bounds.height() / 2,
        )

    def _anchor(self, item: _NodeItem, towards: QPointF) -> QPointF:
        """Where inside ``item`` the arc starts: its centre, moved level with
        (or in line with) the neighbouring bendpoint when that is close, as
        long as the point stays inside the node."""
        centre = item.pos()
        rect = item.rect()
        x, y = centre.x(), centre.y()
        if abs(towards.y() - y) < self.SNAP and abs(towards.y() - y) < rect.height() / 2 - 3:
            y = towards.y()
        if abs(towards.x() - x) < self.SNAP and abs(towards.x() - x) < rect.width() / 2 - 3:
            x = towards.x()
        return QPointF(x, y)

    @staticmethod
    def _inside(item: _NodeItem, point: QPointF) -> bool:
        local = point - item.pos()
        rect = item.rect()
        a, b = rect.width() / 2, rect.height() / 2
        if isinstance(item, PlaceItem):
            return (local.x() / a) ** 2 + (local.y() / b) ** 2 <= 1.0
        return abs(local.x()) <= a and abs(local.y()) <= b

    def _crop(self, item: _NodeItem, inside: QPointF, outside: QPointF) -> QPointF:
        """The point where the segment ``inside`` -> ``outside`` leaves the node,
        plus two pixels of air so the arrowhead does not touch the outline."""
        if not self._inside(item, inside):
            inside = item.pos()
        if self._inside(item, outside):
            return outside                   # overlapping nodes: nothing to crop
        low, high = 0.0, 1.0
        for _ in range(24):                  # bisection: exact to well under a pixel
            middle = (low + high) / 2
            if self._inside(item, inside + (outside - inside) * middle):
                low = middle
            else:
                high = middle
        direction = outside - inside
        length = math.hypot(direction.x(), direction.y()) or 1.0
        return inside + direction * high + direction * (2.0 / length)

    def _append_arrowhead(self, path: QPainterPath, tail: QPointF, tip: QPointF) -> None:
        """Add a filled triangle at ``tip``, pointing away from ``tail``."""
        direction = tip - tail
        length = math.hypot(direction.x(), direction.y())
        if length < 1e-6:
            return
        unit = QPointF(direction.x() / length, direction.y() / length)
        normal = QPointF(-unit.y(), unit.x())

        base = tip - unit * self.ARROW_LENGTH
        path.addPolygon(
            QPolygonF([
                tip,
                base + normal * self.ARROW_HALF_WIDTH,
                base - normal * self.ARROW_HALF_WIDTH,
                tip,
            ])
        )

    def shape(self) -> QPainterPath:
        """Widen the clickable region (a 1.5 px line is very hard to hit), but
        only around the line and the arrowheads, not the area a bent arc
        encloses.  With its handles showing, the circles around the
        waypoints count too, so the ends can be grabbed."""
        stroker = QPainterPathStroker()
        stroker.setWidth(14.0)                  # diagram-js: a 15 px hit line
        shape = stroker.createStroke(self.line_path).united(self.arrow_path)
        if self.handles_visible():
            for point in self._waypoints:
                shape.addEllipse(point, 10.0, 10.0)
        return shape

    def boundingRect(self) -> QRectF:  # noqa: N802
        # Room for the handles, which are drawn a fixed size on screen.
        return super().boundingRect().adjusted(-24, -24, 24, 24)

    def _paint_handles(self, painter: QPainter) -> None:
        """CPN IDE's handles: a circle on every waypoint, one following the
        mouse along the line, a bar across each straight segment's middle."""
        from .arc_editing import aligned
        scale = painter.worldTransform().m11() or 1.0
        radius = self.HANDLE_RADIUS / scale
        accent = theme.palette().accent
        fill = QColor(accent)
        fill.setAlphaF(0.25)
        outline = QPen(accent, 1.2 / scale)
        points = self._waypoints
        painter.setPen(outline)
        painter.setBrush(fill)
        for a, b in zip(points, points[1:]):
            direction = aligned((a.x(), a.y()), (b.x(), b.y()))
            if direction is None:
                continue
            middle = (a + b) / 2
            long_side, short_side = 22.0 / scale, 6.0 / scale
            # A bar across the segment, pointing the way it can be slid.
            if direction == "vertical":
                width, height = long_side, short_side
            else:
                width, height = short_side, long_side
            painter.drawRoundedRect(QRectF(middle.x() - width / 2, middle.y() - height / 2,
                                           width, height), 2 / scale, 2 / scale)
        painter.setBrush(QColor(theme.palette().canvas))
        for point in points:
            painter.drawEllipse(point, radius, radius)
        if self.hovered and self.floating is not None:
            painter.setBrush(fill)
            painter.drawEllipse(self.floating, radius, radius)

    def set_trace(self, strength: float) -> None:
        """Underlay the arc in the accent colour: tokens travelled along it
        in the trace, ``strength`` 1 for the latest step fading to 0."""
        if abs(strength - self.trace) > 1e-9:
            self.trace = strength
            self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        painter.setRenderHint(QPainter.Antialiasing)
        trace = self.trace
        if trace > 0:
            glow = QColor(theme.palette().accent)
            glow.setAlphaF(0.15 + 0.45 * trace)
            painter.setPen(QPen(glow, 3.0 + 4.0 * trace, Qt.SolidLine, Qt.RoundCap,
                                Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.line_path)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            colour = theme.palette().accent
            pen = QPen(colour, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        else:
            pen = QPen(self.pen())
            colour = pen.color()
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)          # never fill the line itself
        painter.drawPath(self.line_path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(colour))
        painter.drawPath(self.arrow_path)
        if self.handles_visible():
            self._paint_handles(painter)

    def set_error(self, has_error: bool) -> None:
        """Draw the arc in red when its inscription failed to compile."""
        palette = theme.palette()
        colour = palette.danger if has_error else palette.border_strong
        self.setPen(QPen(colour, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self.setBrush(QBrush(colour))
        self.inscription_label.setBrush(
            QBrush(palette.danger if has_error else palette.inscription)
        )
