"""A zoomable canvas for laid-out graphs, and builders for each graph kind.

One canvas serves every picture in the mining workspace:

* Petri nets (discovered or imported), optionally with a live marking for
  the token game and with conformance overlays;
* directly-follows graphs ("process maps") in frequency or performance mode;
* dependency graphs from the Heuristics Miner;
* reachability / coverability graphs.

Positions come from :func:`openprocess.mining.layout.layered_layout` unless the
model already carries coordinates (a PNML file drawn by another tool).

Interaction: scroll to pan (trackpad friendly), pinch or ⌘/Ctrl + scroll to
zoom, drag the background to pan, click a node to emit ``node_clicked``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QToolButton,
)

from ...mining.layout import layered_layout
from ..panning import CanvasPanning
from . import style
from .widgets import shortcut_text


# ---------------------------------------------------------------------------
# Scene description (what to draw), independent of Qt items
# ---------------------------------------------------------------------------
@dataclass
class NodeSpec:
    id: str
    kind: str                       # place | transition | silent | activity | start | end | state
    text: str = ""
    subtext: str = ""               # second line (counts, durations)
    below: str = ""                 # label under the node (place names)
    fill: str | None = None
    stroke: str | None = None
    tokens: int = 0
    badges: list[tuple[str, str]] = field(default_factory=list)   # (text, colour)
    tooltip: str = ""
    emphasis: bool = False          # thicker outline (enabled / initial state)
    dimmed: bool = False


@dataclass
class EdgeSpec:
    source: str
    target: str
    text: str = ""
    width: float = 1.4
    colour: str | None = None
    dashed: bool = False
    tooltip: str = ""
    arrow: bool = True              # False: a plain line (a tree's "contains")
    bend: float = 0.0               # sideways offset, so parallel edges (object types) stay apart


def _font(size: float = 11.5, bold: bool = False) -> QFont:
    from .. import theme
    font = theme.ui_font(size)
    font.setBold(bold)
    return font


def node_size(spec: NodeSpec) -> tuple[float, float]:
    metrics = QFontMetricsF(_font(12, True))
    if spec.kind == "place":
        return 36.0, 36.0
    if spec.kind in ("start", "end"):
        return 26.0, 26.0
    if spec.kind == "operator":
        return 34.0, 34.0
    if spec.kind == "silent":
        return 14.0, 40.0
    if spec.kind == "state":
        small = QFontMetricsF(_font(10.5))
        return max(small.horizontalAdvance(spec.text) + 22, 44.0), 30.0
    width = metrics.horizontalAdvance(spec.text) + 28
    if spec.subtext:
        width = max(width, QFontMetricsF(_font(10.5)).horizontalAdvance(spec.subtext) + 28)
    if spec.kind == "activity":
        return max(width, 104.0), 48.0 if spec.subtext else 40.0
    return max(width, 64.0), 42.0 if not spec.subtext else 50.0


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------
class NodeItem(QGraphicsObject):
    def __init__(self, spec: NodeSpec) -> None:
        super().__init__()
        self.spec = spec
        self.w, self.h = node_size(spec)
        self.hovered = False
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        if spec.tooltip:
            self.setToolTip(spec.tooltip)
        self.setCursor(Qt.PointingHandCursor)

    def boundingRect(self) -> QRectF:  # noqa: N802
        extra_bottom = 24 if self.spec.below else 6
        return QRectF(-self.w / 2 - 20, -self.h / 2 - 16, self.w + 40, self.h + 16 + extra_bottom + 6)

    def shape_rect(self) -> QRectF:
        return QRectF(-self.w / 2, -self.h / 2, self.w, self.h)

    def is_round(self) -> bool:
        return self.spec.kind in ("place", "start", "end", "operator")

    def boundary_point(self, towards: QPointF) -> QPointF:
        """Where a line from the centre towards ``towards`` leaves the outline."""
        centre = self.pos()
        dx, dy = towards.x() - centre.x(), towards.y() - centre.y()
        if dx == 0 and dy == 0:
            return centre
        if self.is_round():
            length = math.hypot(dx, dy)
            r = self.w / 2 + 1
            return QPointF(centre.x() + dx / length * r, centre.y() + dy / length * r)
        half_w, half_h = self.w / 2 + 1, self.h / 2 + 1
        scale = min(half_w / abs(dx) if dx else math.inf, half_h / abs(dy) if dy else math.inf)
        return QPointF(centre.x() + dx * scale, centre.y() + dy * scale)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, GraphScene) and event.button() == Qt.LeftButton:
            scene.node_clicked.emit(self.spec.id)
            if not (self.flags() & QGraphicsItem.ItemIsMovable):
                event.accept()
                return
        super().mousePressEvent(event)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if isinstance(scene, GraphScene):
                scene.node_dragged(self)
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = style.tokens()
        spec = self.spec
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.shape_rect()
        stroke = QColor(spec.stroke or t.node_stroke)
        if spec.dimmed:
            stroke.setAlphaF(0.35)
        width = 2.4 if spec.emphasis else 1.4
        if self.hovered:
            stroke = QColor(t.selection)
            width = 2.2
        painter.setPen(QPen(stroke, width))
        fill = QColor(spec.fill or t.node_fill)

        if spec.kind == "place":
            painter.setBrush(fill)
            painter.drawEllipse(rect)
            self._paint_tokens(painter, rect, t)
        elif spec.kind in ("start", "end"):
            painter.setBrush(QColor(spec.fill or t.text_secondary))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(rect)
            painter.setBrush(QColor(t.surface))
            if spec.kind == "start":
                path = QPainterPath()
                path.moveTo(-3.5, -6)
                path.lineTo(6.5, 0)
                path.lineTo(-3.5, 6)
                path.closeSubpath()
                painter.drawPath(path)
            else:
                painter.drawRect(QRectF(-4.5, -4.5, 9, 9))
        elif spec.kind == "operator":
            painter.setBrush(fill)
            painter.drawEllipse(rect)
            painter.setPen(QColor(spec.stroke or t.text))
            painter.setFont(_font(15, True))
            painter.drawText(rect, Qt.AlignCenter, spec.text)
        elif spec.kind == "silent":
            painter.setBrush(QColor(spec.fill or t.silent_fill))
            painter.drawRoundedRect(rect, 2.5, 2.5)
        else:
            radius = 9 if spec.kind in ("activity", "state") else 7
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, radius, radius)
            ink = QColor(style.text_on(fill.name())) if spec.fill else QColor(t.text)
            if spec.dimmed:
                ink.setAlphaF(0.45)
            painter.setPen(ink)
            if spec.kind == "state":
                painter.setFont(_font(10.5))
                painter.drawText(rect, Qt.AlignCenter, spec.text)
            elif spec.subtext:
                painter.setFont(_font(12, True))
                painter.drawText(rect.adjusted(6, 5, -6, -rect.height() / 2 + 3),
                                 Qt.AlignHCenter | Qt.AlignBottom, spec.text)
                painter.setFont(_font(10.5))
                sub = QColor(ink)
                sub.setAlphaF(0.75 * ink.alphaF())
                painter.setPen(sub)
                painter.drawText(rect.adjusted(6, rect.height() / 2 + 1, -6, -4),
                                 Qt.AlignHCenter | Qt.AlignTop, spec.subtext)
            else:
                painter.setFont(_font(12, True))
                painter.drawText(rect, Qt.AlignCenter, spec.text)

        if spec.below:
            painter.setPen(QColor(t.text_muted))
            painter.setFont(_font(10))
            metrics = QFontMetricsF(painter.font())
            text = metrics.elidedText(spec.below, Qt.ElideRight, 150)
            text_width = metrics.horizontalAdvance(text)
            painter.drawText(QRectF(-text_width / 2 - 2, rect.bottom() + 3, text_width + 4, 16),
                             Qt.AlignCenter, text)

        # Badges sit above the node's top-right corner, stacked leftwards.
        x = rect.right() + 6
        for text, colour in reversed(spec.badges):
            painter.setFont(_font(10, True))
            metrics = QFontMetricsF(painter.font())
            w = metrics.horizontalAdvance(text) + 12
            x -= w
            badge = QRectF(x, rect.top() - 13, w, 17)
            painter.setPen(QPen(QColor(t.surface), 2))
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(badge, 8.5, 8.5)
            painter.setPen(QColor(style.text_on(colour)))
            painter.drawText(badge, Qt.AlignCenter, text)
            x -= 3

    def _paint_tokens(self, painter: QPainter, rect: QRectF, t) -> None:
        count = self.spec.tokens
        if count <= 0:
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t.text))
        centres = {1: [(0, 0)], 2: [(-5.5, 0), (5.5, 0)], 3: [(-5.5, 4), (5.5, 4), (0, -5.5)],
                   4: [(-5, -5), (5, -5), (-5, 5), (5, 5)]}.get(count)
        if centres is None:
            painter.setPen(QColor(t.text))
            painter.setFont(_font(12, True))
            painter.drawText(rect, Qt.AlignCenter, "ω" if count == math.inf else str(count))
            return
        for dx, dy in centres:
            painter.drawEllipse(QPointF(dx, dy), 3.8, 3.8)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, spec: EdgeSpec, source: NodeItem, target: NodeItem,
                 route: list[QPointF]) -> None:
        super().__init__()
        self.spec = spec
        self.source_item, self.target_item = source, target
        self.route = route
        self.setZValue(1)
        if spec.tooltip:
            self.setToolTip(spec.tooltip)
        self.label_rect: QRectF | None = None
        #: Sideways offset (px) for a straight edge that has a twin running
        #: the other way, so the two do not draw on top of each other.
        self.bend = 0.0
        self.label_point = QPointF()
        self.arrow = QPainterPath()
        self.rebuild()

    def rebuild(self) -> None:
        s, t = self.source_item, self.target_item
        if s is t:
            self._self_loop()
            return
        points = [s.pos()] + [p for p in self.route[1:-1]] + [t.pos()]
        start = s.boundary_point(points[1])
        end = t.boundary_point(points[-2])
        points[0], points[-1] = start, end
        path = QPainterPath(start)
        if len(points) == 2 and self.bend:
            middle = (start + end) / 2
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = math.hypot(dx, dy) or 1
            control = middle + QPointF(-dy / length * self.bend, dx / length * self.bend)
            start = s.boundary_point(control)
            end = t.boundary_point(control)
            path = QPainterPath(start)
            path.quadTo(control, end)
        elif len(points) == 2:
            path.lineTo(end)
        else:
            # Catmull-Rom spline through the dummy points, as cubic Béziers.
            extended = [points[0]] + points + [points[-1]]
            for i in range(1, len(extended) - 2):
                p0, p1, p2, p3 = extended[i - 1], extended[i], extended[i + 1], extended[i + 2]
                c1 = p1 + (p2 - p0) / 6
                c2 = p2 - (p3 - p1) / 6
                path.cubicTo(c1, c2, p2)
        self.setPath(path)
        self._arrowhead(path)
        middle = path.pointAtPercent(0.5)
        self.label_point = middle

    #: Geometry of self-loops, shared with the layout so it can reserve room.
    LOOP_HEIGHT = 46.0

    def _self_loop(self) -> None:
        """A loop above the node: leaves the top edge on the right, returns on the left.

        Its width is capped: a loop taken a thousand times would otherwise be
        drawn as a thick blob.  The frequency is in the label, and the
        tooltip, not in the stroke.
        """
        node = self.source_item
        top = node.pos() + QPointF(0, -node.h / 2)
        spread = min(node.w * 0.22, 20.0)
        rise = self.LOOP_HEIGHT
        start = top + QPointF(spread, 0)
        end = top + QPointF(-spread, 0)
        path = QPainterPath(start)
        path.cubicTo(top + QPointF(spread + 30, -rise), top + QPointF(-spread - 30, -rise), end)
        self.setPath(path)
        self._arrowhead(path)
        # The curve's highest point is at 3/4 of the control height.
        self.label_point = top + QPointF(0, -rise * 0.75 - 12)

    @property
    def stroke_width(self) -> float:
        width = self.spec.width
        return min(width, 2.4) if self.source_item is self.target_item else width

    def _arrowhead(self, path: QPainterPath) -> None:
        """A filled triangle at the end, and a copy of the path that stops at its base.

        Stopping the line at the arrow's base (rather than at the tip) keeps
        thick lines from bulging out around the arrowhead.  An edge without an
        arrow (``spec.arrow`` False) is just the path.
        """
        if not self.spec.arrow:
            self.arrow = QPainterPath()
            self.draw_path = path
            return
        size = 7.0 + 0.9 * min(self.stroke_width, 4.0)
        length = max(path.length(), 1.0)
        tip = path.pointAtPercent(1.0)
        base_percent = path.percentAtLength(max(length - size * 0.85, 0.0))
        base = path.pointAtPercent(base_percent)
        angle = math.atan2(tip.y() - base.y(), tip.x() - base.x())
        left = tip - QPointF(math.cos(angle - 0.4) * size, math.sin(angle - 0.4) * size)
        right = tip - QPointF(math.cos(angle + 0.4) * size, math.sin(angle + 0.4) * size)
        self.arrow = QPainterPath(tip)
        self.arrow.lineTo(left)
        self.arrow.lineTo(right)
        self.arrow.closeSubpath()
        trimmed = QPainterPath(path.pointAtPercent(0.0))
        steps = 48
        for k in range(1, steps + 1):
            trimmed.lineTo(path.pointAtPercent(base_percent * k / steps))
        self.draw_path = trimmed

    def boundingRect(self) -> QRectF:  # noqa: N802
        return super().boundingRect().united(self.arrow.boundingRect()).adjusted(-60, -20, 60, 20)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = style.tokens()
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(self.spec.colour or t.text_secondary)
        pen = QPen(colour, self.stroke_width, Qt.DashLine if self.spec.dashed else Qt.SolidLine)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.draw_path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawPath(self.arrow)
        if self.spec.text:
            painter.setFont(_font(10))
            metrics = QFontMetricsF(painter.font())
            w = metrics.horizontalAdvance(self.spec.text) + 10
            box = QRectF(self.label_point.x() - w / 2, self.label_point.y() - 9, w, 18)
            painter.setBrush(QColor(t.canvas))
            painter.drawRoundedRect(box, 5, 5)
            painter.setPen(QColor(t.text_secondary))
            painter.drawText(box, Qt.AlignCenter, self.spec.text)


# ---------------------------------------------------------------------------
# Scene and view
# ---------------------------------------------------------------------------
class GraphScene(QGraphicsScene):
    node_clicked = Signal(str)
    #: A node was dragged (the scene is movable): its id and new position.
    node_moved = Signal(str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[str, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        #: Nodes can be dragged to tidy the drawing (nothing else changes).
        self.movable = False

    def populate(self, nodes: list[NodeSpec], edges: list[EdgeSpec],
                 positions: dict[str, tuple[float, float]] | None = None,
                 layer_gap: float = 70.0, movable: bool = False) -> None:
        """Replace the scene contents.  Lays out unless ``positions`` is given.
        ``movable``: the nodes can be dragged (see :attr:`node_moved`)."""
        self.clear()
        self.nodes, self.edges = {}, []
        self.movable = movable
        sizes = {n.id: node_size(n) for n in nodes}
        # Make room between columns for the widest edge label, so labels
        # never sit on top of the nodes they connect (capped for sanity).
        label_metrics = QFontMetricsF(_font(10))
        widest = max((label_metrics.horizontalAdvance(e.text) for e in edges if e.text), default=0)
        layer_gap = max(layer_gap, min(widest + 34, 280))
        # A node with a self-loop gets extra (symmetric) height in the layout,
        # so the loop and its label never run into the node above.
        looped = {e.source for e in edges if e.source == e.target}
        for node_id in looped:
            if node_id in sizes:
                w, h = sizes[node_id]
                sizes[node_id] = (w, h + 2 * (EdgeItem.LOOP_HEIGHT + 8))
        routes: dict[int, list[tuple[float, float]]] = {}
        if positions is None or any(n.id not in positions for n in nodes):
            layout = layered_layout(sizes, [(e.source, e.target) for e in edges],
                                    layer_gap=layer_gap)
            positions, routes = layout.positions, layout.routes
        for spec in nodes:
            item = NodeItem(spec)
            x, y = positions[spec.id]
            item.setPos(x, y)
            if movable:
                item.setFlag(QGraphicsItem.ItemIsMovable, True)
                item.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
                item.setCursor(Qt.OpenHandCursor)
            self.addItem(item)
            self.nodes[spec.id] = item
        pairs = {(e.source, e.target) for e in edges if e.source != e.target}
        for index, spec in enumerate(edges):
            if spec.source not in self.nodes or spec.target not in self.nodes:
                continue
            route = [QPointF(*p) for p in routes.get(index, [])]
            if len(route) < 2:
                route = [self.nodes[spec.source].pos(), self.nodes[spec.target].pos()]
            item = EdgeItem(spec, self.nodes[spec.source], self.nodes[spec.target], route)
            if spec.bend:
                item.route = [self.nodes[spec.source].pos(), self.nodes[spec.target].pos()]
                item.bend = spec.bend
                item.rebuild()
            elif len(route) == 2 and (spec.target, spec.source) in pairs:
                item.bend = 16.0
                item.rebuild()
            self.addItem(item)
            self.edges.append(item)
        rect = self.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.setSceneRect(rect)

    def node_dragged(self, item: NodeItem) -> None:
        """A node moved: its edges follow (as straight lines now) and listeners hear."""
        for edge in self.edges:
            if edge.source_item is item or edge.target_item is item:
                edge.route = [edge.source_item.pos(), edge.target_item.pos()]
                edge.prepareGeometryChange()
                edge.rebuild()
        self.node_moved.emit(item.spec.id, item.pos().x(), item.pos().y())

    def positions(self) -> dict[str, tuple[float, float]]:
        """Every node's position, by id (to keep a tidied layout)."""
        return {node_id: (item.pos().x(), item.pos().y()) for node_id, item in self.nodes.items()}

    def update_node(self, spec: NodeSpec) -> None:
        """Change a node's appearance in place (token game, overlays)."""
        item = self.nodes.get(spec.id)
        if item is not None:
            item.prepareGeometryChange()
            item.spec = spec
            item.setToolTip(spec.tooltip)
            item.update()


class ZoomControls(QFrame):
    """The small floating zoom bar in the bottom-right corner of a canvas.

    ``−`` / ``+`` zoom around the centre of the view, the percentage resets
    to 100 % (actual size) when clicked, and ``Fit`` shows the whole graph.
    """

    def __init__(self, view) -> None:
        super().__init__(view)
        self.view = view
        self.setObjectName("zoomControls")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(0)

        def tool(text: str, tip: str, slot, width: int | None = None) -> QToolButton:
            b = QToolButton(self)
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            if width:
                b.setFixedWidth(width)
            b.clicked.connect(slot)
            layout.addWidget(b)
            return b

        self.minus = tool("−", f"Zoom out  ({shortcut_text('Ctrl+-')})", view.zoom_out, 26)
        self.percent = tool("100%", "Actual size (100 %)", view.actual_size, 50)
        self.plus = tool("+", f"Zoom in  ({shortcut_text('Ctrl++')})", view.zoom_in, 26)
        divider = QFrame(self)
        divider.setFrameShape(QFrame.VLine)
        divider.setFixedWidth(9)
        layout.addWidget(divider)
        self.fit = tool("Fit", f"Zoom to fit the whole graph  ({shortcut_text('Ctrl+0')})", view.fit)
        view.zoom_changed.connect(self.show_zoom)
        self.restyle()

    def restyle(self) -> None:
        t = style.tokens()
        self.setStyleSheet(
            f"QFrame#zoomControls {{ background: {t.surface}; border: 1px solid {t.border}; "
            f"border-radius: 7px; }}"
            f"QFrame#zoomControls QToolButton {{ color: {t.text_secondary}; border: none; "
            f"padding: 2px 6px; font-size: 13px; border-radius: 5px; }}"
            f"QFrame#zoomControls QToolButton:hover {{ background: {t.surface_alt}; "
            f"color: {t.text}; }}"
            f"QFrame#zoomControls QFrame {{ color: {t.border}; }}")

    def show_zoom(self, scale: float) -> None:
        self.percent.setText(f"{round(scale * 100)}%")
        self.minus.setEnabled(scale > self.view.MIN_ZOOM + 1e-6)
        self.plus.setEnabled(scale < self.view.MAX_ZOOM - 1e-6)


class GraphView(CanvasPanning, QGraphicsView):
    """Pan/zoom view with 'fit', a floating zoom bar and image export.

    Zooming: the ``− 100% + Fit`` bar, ⌘/Ctrl + scroll wheel, trackpad pinch,
    the View menu (⌘+ / ⌘− / ⌘0 fit), or plain + / − / 0 / 1 while the canvas
    has keyboard focus.  Drag (with either the left or the middle button) to pan.
    """

    zoom_changed = Signal(float)
    MIN_ZOOM = 0.05
    MAX_ZOOM = 5.0
    STEP = 1.25

    def __init__(self, scene: GraphScene | None = None, parent=None,
                 zoom_controls: bool = True) -> None:
        # Keep a Python reference: QGraphicsView does not own its scene, so a
        # temporary GraphScene() would be garbage-collected immediately.
        self._scene = scene or GraphScene()
        super().__init__(self._scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setMinimumSize(QSize(200, 160))
        self.setFocusPolicy(Qt.StrongFocus)
        self.grabGesture(Qt.PinchGesture)
        #: While True, resizing the view re-fits the graph.  Any manual zoom
        #: turns it off, so the user's zoom level is respected.
        self.auto_fit = True
        # A fit asked for while the left button is down on the canvas waits
        # for the release: see :meth:`fit_waits`.
        self._pressed = False
        self._fit_pending = False
        self.zoom_controls = ZoomControls(self) if zoom_controls else None
        self._place_controls()
        self.init_canvas()

    @property
    def graph(self) -> GraphScene:
        return self._scene

    def zoom(self) -> float:
        return self.transform().m11()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        t = style.tokens()
        painter.fillRect(rect, QColor(t.canvas))
        # A faint dot grid, the quietest way to say "this is a canvas".
        spacing = 24
        painter.setPen(QPen(QColor(t.grid), 1.4))
        left = int(math.floor(rect.left() / spacing) * spacing)
        top = int(math.floor(rect.top() / spacing) * spacing)
        if self.transform().m11() > 0.45:
            points = [QPointF(x, y) for x in range(left, int(rect.right()) + 1, spacing)
                      for y in range(top, int(rect.bottom()) + 1, spacing)]
            if len(points) < 20000:
                painter.drawPoints(points)

    def sizeHint(self) -> QSize:  # noqa: N802
        # QGraphicsView asks for the drawing's own size (up to three quarters
        # of the screen), and a page laid out by height for width (the
        # Summary's row of tiles) grants it: a process map then towered over
        # the window.  A view is a window on the graph, which fits itself to
        # whatever it gets, so ask for a modest one and let the page's layout
        # give it the room the window has.
        return QSize(480, 320)

    def fit_waits(self) -> bool:
        """True when a fit must wait for the mouse button to come up.

        Clicking a box opens the side panel; the canvas gets narrower and,
        with ``auto_fit`` on, re-fits itself, all while the button is still
        down on the box.  A moving item's drag anchor is a scene point taken
        at the press, so once the view's transform has changed under the
        pointer, the first pixel of movement moves the box by the whole
        difference: it lands far across the canvas.  So while the left button
        is down on this view, a fit is only noted, and done at the release.
        """
        if not self._pressed:
            return False
        self.auto_fit = True
        self._fit_pending = True
        return True

    def fit(self) -> None:
        if self.fit_waits():
            return
        self.auto_fit = True
        rect = self.graph.sceneRect()
        if rect.isEmpty():
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        # Never blow a small graph up beyond 140 %: it looks cartoonish.
        if self.transform().m11() > 1.4:
            self.resetTransform()
            self.scale(1.4, 1.4)
            self.centerOn(rect.center())
        self.zoom_changed.emit(self.transform().m11())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_controls()
        if self.auto_fit:
            self.fit()

    def _place_controls(self) -> None:
        if self.zoom_controls is None:
            return
        self.zoom_controls.adjustSize()
        size = self.zoom_controls.sizeHint()
        # Hide the bar when the canvas is too small to carry it comfortably.
        # Anchor to the viewport, so the bar never sits on a scroll bar.
        area = self.viewport().geometry()
        self.zoom_controls.setVisible(area.width() >= size.width() + 60 and area.height() >= 90)
        self.zoom_controls.move(area.right() - size.width() - 9,
                                area.bottom() - size.height() - 9)
        self.zoom_controls.raise_()

    def zoom_by(self, factor: float, around_centre: bool = False, at=None) -> None:
        self.auto_fit = False
        current = self.transform().m11()
        target = min(max(current * factor, self.MIN_ZOOM), self.MAX_ZOOM)
        if target == current:
            return
        self.zoom_about(target / current, QPointF(self.viewport().rect().center())
                        if around_centre else at)
        self.zoom_changed.emit(target)

    def zoom_in(self) -> None:
        self.zoom_by(self.STEP, around_centre=True)

    def zoom_out(self) -> None:
        self.zoom_by(1 / self.STEP, around_centre=True)

    def actual_size(self) -> None:
        self.zoom_by(1 / self.transform().m11(), around_centre=True)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pressed = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pressed and not event.buttons() & Qt.LeftButton:   # released outside the window
            self._pressed = False
            self._fit_if_pending()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self._fit_if_pending()

    def _fit_if_pending(self) -> None:
        if self._fit_pending:
            self._fit_pending = False
            if self.auto_fit:
                self.fit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_out()
        elif key == Qt.Key_0:
            self.fit()
        elif key == Qt.Key_1:
            self.actual_size()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.zoom_by(1.0015 ** event.angleDelta().y(), at=event.position())
            event.accept()
        else:
            self.wheel_pan(event)

    def event(self, event) -> bool:
        # macOS trackpad pinch arrives as a native zoom gesture.
        if event.type() == event.Type.NativeGesture:
            try:
                if event.gestureType() == Qt.ZoomNativeGesture:
                    self.zoom_by(1 + event.value(),
                                 at=self.viewport().mapFromGlobal(event.globalPosition()))
                    return True
            except AttributeError:  # pragma: no cover
                pass
        return super().event(event)

    def export_png(self, path: str, scale: float = 2.0) -> None:
        rect = self.graph.sceneRect()
        image = QImage(int(rect.width() * scale), int(rect.height() * scale), QImage.Format_ARGB32)
        image.fill(QColor(style.tokens().canvas))
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.graph.render(painter, QRectF(image.rect()), rect)
        painter.end()
        image.save(path)

    def svg_text(self) -> str:
        """The drawing as SVG text (for a report)."""
        from PySide6.QtCore import QBuffer, QByteArray
        from PySide6.QtSvg import QSvgGenerator
        rect = self.graph.sceneRect()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.WriteOnly)
        generator = QSvgGenerator()
        generator.setOutputDevice(buffer)
        generator.setSize(rect.size().toSize())
        generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
        painter = QPainter(generator)
        self.graph.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
        painter.end()
        buffer.close()
        text = bytes(data).decode("utf-8", errors="replace")
        return text[text.index("<svg"):] if "<svg" in text else text

    def export_svg(self, path: str) -> None:
        from PySide6.QtSvg import QSvgGenerator
        rect = self.graph.sceneRect()
        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(rect.size().toSize())
        generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
        painter = QPainter(generator)
        self.graph.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
        painter.end()


__all__ = ["EdgeSpec", "GraphScene", "GraphView", "ZoomControls", "NodeItem", "NodeSpec", "QGraphicsItem",
           "QBrush"]
