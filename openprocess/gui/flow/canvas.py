"""The workflow canvas: boxes, wires, and dragging a wire to connect.

A :class:`BoxItem` draws one node of the workflow the way the sketch does:
the group as a small eyebrow, the name, a line about its result, a status
dot, and its connection points as coloured dots (inputs on the left,
outputs on the right).  A :class:`WireItem` is a curve between an output
and an input.  The :class:`WorkflowScene` keeps the items in step with a
:class:`~openprocess.flow.workflow.Workflow` and handles the gestures:

* drag a box to move it (snapped to the grid, wires follow);
* drag from an output dot to connect it: while dragging, only the inputs
  it may go to light up, the others fade, and letting go near one makes
  the edge (the workflow refuses the wrong type, a loop, a duplicate);
* click a wire to select it, Delete removes it;
* double-click the background to add a box there (the page shows a list);
* a :class:`GroupItem` shows several boxes as one, with the connection
  points that reach outside the group; double-click it to open its own
  canvas (the members, with stubs for the wires that come from outside).

The scene only edits the workflow and says what changed (``edited``); the
page runs the boxes and shows the results.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene

from ...flow.box import Port
from ...flow.runner import BLOCKED, DONE, FAILED, IDLE, RUNNING, WAITING
from ...flow.types import type_info
from ...flow.workflow import Edge, Group, Node, Workflow
from .. import theme
from ..studio import motion, style
from ..studio.graph_view import GraphView

NODE_W = 178.0
GRID = 10.0
PORT_R = 6.0
STATUS_COLOUR = {DONE: "good", RUNNING: "accent", FAILED: "critical", BLOCKED: "warning",
                 WAITING: "muted", IDLE: "muted"}


def port_colour(port: Port) -> str:
    info = type_info(port.type) if port.type is not None else None
    return info.colour if info else style.tokens().text_muted


def _font(size: float, bold: bool = False) -> QFont:
    font = theme.ui_font(size)
    font.setBold(bold)
    return font


def node_height(inputs: int, outputs: int) -> float:
    rows = max(inputs, outputs)
    return 72 + 20 * (rows - 1) + 14 if rows > 1 else 66


class BoxItem(QGraphicsObject):
    """One box on the canvas."""

    def __init__(self, scene: "WorkflowScene", node: Node) -> None:
        super().__init__()
        self.flow_scene = scene
        self.node = node
        self.spec = scene.workflow.spec(node)
        self.status = WAITING
        self.subtitle = ""
        self.highlight: set[str] = set()      # inputs that fit the wire being dragged
        self.dimmed = False
        self.locked = False
        self._glow = 0.0                      # a ring around the dot that swells once when the box finishes
        self.pulse_animation: QPropertyAnimation | None = None
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setPos(*node.position)
        self._moving = False

    # -- geometry ------------------------------------------------------------------
    @property
    def height(self) -> float:
        return node_height(len(self.spec.inputs), len(self.spec.outputs))

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-PORT_R - 2, -14, NODE_W + 2 * PORT_R + 4, self.height + 18)

    def rect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self.height)

    def port_point(self, side: str, name: str) -> QPointF:
        """Where a port's dot is, in item coordinates."""
        ports = self.spec.inputs if side == "in" else self.spec.outputs
        index = max(0, next((i for i, p in enumerate(ports) if p.name == name), 0))
        y = 72 + 20 * index if len(ports) > 1 else self.height / 2
        return QPointF(0 if side == "in" else NODE_W, y)

    def scene_port(self, side: str, name: str) -> QPointF:
        return self.mapToScene(self.port_point(side, name))

    def drop_inputs(self) -> list[tuple[str, str, Port]]:
        """``(id, node id, Port)`` for every input a wire may be dropped on."""
        return [(p.name, self.node.id, p) for p in self.spec.inputs]

    def port_at(self, point: QPointF) -> tuple[str, Port] | None:
        """The (side, port) whose dot is under ``point`` (item coordinates)."""
        for side, ports in (("out", self.spec.outputs), ("in", self.spec.inputs)):
            for port in ports:
                centre = self.port_point(side, port.name)
                if math.hypot(point.x() - centre.x(), point.y() - centre.y()) <= PORT_R + 4:
                    return side, port
        return None

    # -- state ---------------------------------------------------------------------------
    def set_status(self, status: str, subtitle: str, tooltip: str = "") -> None:
        finished = status in (DONE, FAILED) and status != self.status
        self.status, self.subtitle = status, subtitle
        self.setToolTip(tooltip)
        self.update()
        if finished:
            self.pulse()

    def get_glow(self) -> float:
        return self._glow

    def set_glow(self, value: float) -> None:
        self._glow = float(value)
        self.update()

    glow = Property(float, get_glow, set_glow)

    def pulse(self) -> None:
        """The status dot swells once: the box has just finished."""
        if not motion.enabled:
            return
        animation = QPropertyAnimation(self, b"glow", self)
        animation.setDuration(480)
        animation.setStartValue(0.0)
        animation.setKeyValueAt(0.35, 1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self.pulse_animation = animation
        animation.start()

    def set_highlight(self, inputs: set[str], dimmed: bool) -> None:
        self.highlight, self.dimmed = inputs, dimmed
        self.update()

    def eyebrow(self) -> str:
        """The small line over the title: the group, and the file a box reads
        ("INPUT · boarding.xes"), so the canvas says what feeds it."""
        text = ("Yours · " if self.spec.custom else "") + self.spec.group.upper()
        for setting in self.spec.settings:
            if setting.kind == "path" and self.node.settings.get(setting.name) not in (None, ""):
                return f"{text} · {Path(str(self.node.settings[setting.name])).name}"
        return text

    # -- Qt --------------------------------------------------------------------------------
    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionChange and not self.locked:
            snapped = QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
            return snapped
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.node.position = (self.pos().x(), self.pos().y())
            self.flow_scene.wires_of(self.node.id, rebuild=True)
            self._moving = True
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        hit = self.port_at(event.pos())
        if hit is not None and hit[0] == "out" and event.button() == Qt.LeftButton:
            self.flow_scene.begin_wire(self.node.id, hit[1], self.scene_port("out", hit[1].name), event.scenePos())
            event.accept()
            return
        self._moving = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._moving:
            self._moving = False
            self.flow_scene.moved.emit(self.node.id)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        hit = self.port_at(event.pos())
        self.setCursor(Qt.CrossCursor if hit and hit[0] == "out" else Qt.SizeAllCursor
                       if not self.locked else Qt.ArrowCursor)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.unsetCursor()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = style.tokens()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        failed = self.status == FAILED
        if self.dimmed:
            painter.setOpacity(0.32)
        fill = QColor(style.qc(style.STATUS["critical"], 0.10)) if failed else QColor(t.surface)
        stroke = QColor(t.selection) if self.isSelected() else \
            QColor(style.STATUS["critical"]) if failed else QColor(t.border)
        pen = QPen(stroke, 2.0 if self.isSelected() else 1.5 if failed else 1.0)
        if self.locked:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 9, 9)
        # Eyebrow: the group (and "Yours" for a custom box).
        painter.setPen(QColor(t.text_muted))
        painter.setFont(_font(8.5, True))
        painter.drawText(QPointF(12, 16), QFontMetricsF(painter.font()).elidedText(
            self.eyebrow(), Qt.ElideMiddle, NODE_W - 36))
        # Status dot.
        colour = STATUS_COLOUR.get(self.status, "muted")
        dot = QColor({"good": style.STATUS["good"], "accent": t.accent, "critical": style.STATUS["critical"],
                      "warning": style.STATUS["warning"], "muted": t.text_muted}[colour])
        if self._glow > 0:
            ring = QColor(dot)
            ring.setAlphaF(0.45 * (1 - self._glow))
            painter.setPen(Qt.NoPen)
            painter.setBrush(ring)
            painter.drawEllipse(QPointF(NODE_W - 14, 13), 4.5 + 9 * self._glow, 4.5 + 9 * self._glow)
        painter.setPen(QPen(dot, 1.5))
        painter.setBrush(dot if self.status not in (WAITING, IDLE) else Qt.NoBrush)
        painter.drawEllipse(QPointF(NODE_W - 14, 13), 4.5, 4.5)
        # Title and subtitle.
        painter.setPen(QColor(t.text))
        painter.setFont(_font(12.5, True))
        metrics = QFontMetricsF(painter.font())
        title = self.node.title or self.spec.name
        painter.drawText(QPointF(12, 34), metrics.elidedText(title, Qt.ElideRight, NODE_W - 36))
        painter.setPen(QColor(style.STATUS["critical"] if failed else t.text_muted))
        painter.setFont(_font(10.5))
        metrics = QFontMetricsF(painter.font())
        painter.drawText(QPointF(12, 52), metrics.elidedText(self.subtitle, Qt.ElideRight, NODE_W - 24))
        # Ports.
        for side, ports in (("in", self.spec.inputs), ("out", self.spec.outputs)):
            for port in ports:
                centre = self.port_point(side, port.name)
                lit = side == "in" and port.name in self.highlight
                painter.setPen(QPen(QColor(t.selection if lit else t.surface), 3 if lit else 2))
                painter.setBrush(QColor(port_colour(port)))
                painter.drawEllipse(centre, PORT_R + (2 if lit else 0), PORT_R + (2 if lit else 0))
                if len(ports) > 1:
                    painter.setPen(QColor(t.text_secondary))
                    painter.setFont(_font(9.5))
                    text = port.label or port.name
                    if side == "in":
                        painter.drawText(QPointF(centre.x() + 10, centre.y() + 3.5), text)
                    else:
                        width = QFontMetricsF(painter.font()).horizontalAdvance(text)
                        painter.drawText(QPointF(centre.x() - 10 - width, centre.y() + 3.5), text)
        painter.setOpacity(1.0)


class GroupItem(QGraphicsObject):
    """Several boxes shown as one: a stacked box with the derived connection points."""

    def __init__(self, scene: "WorkflowScene", group: Group) -> None:
        super().__init__()
        self.flow_scene = scene
        self.group = group
        self.inputs, self.outputs = scene.workflow.group_ports(group)
        self.status = WAITING
        self.subtitle = ""
        self.highlight: set[str] = set()
        self.dimmed = False
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self._last = QPointF(*group.position)
        self.setPos(*group.position)
        self._moving = False

    @property
    def height(self) -> float:
        return node_height(len(self.inputs), len(self.outputs))

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-PORT_R - 2, -14, NODE_W + 2 * PORT_R + 12, self.height + 26)

    def rect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self.height)

    @staticmethod
    def port_id(node_id: str, port_name: str) -> str:
        return f"{node_id}:{port_name}"

    def port_point(self, side: str, port_id: str) -> QPointF:
        ports = self.inputs if side == "in" else self.outputs
        ids = [self.port_id(n, p) for n, p, _ in ports]
        index = max(0, ids.index(port_id) if port_id in ids else 0)
        y = 72 + 20 * index if len(ports) > 1 else self.height / 2
        return QPointF(0 if side == "in" else NODE_W, y)

    def scene_port(self, side: str, port_id: str) -> QPointF:
        return self.mapToScene(self.port_point(side, port_id))

    def drop_inputs(self) -> list[tuple[str, str, Port]]:
        return [(self.port_id(n, p), n, port) for n, p, port in self.inputs]

    def port_at(self, point: QPointF):
        for side, ports in (("out", self.outputs), ("in", self.inputs)):
            for node_id, name, port in ports:
                centre = self.port_point(side, self.port_id(node_id, name))
                if math.hypot(point.x() - centre.x(), point.y() - centre.y()) <= PORT_R + 4:
                    return side, node_id, port
        return None

    def set_status(self, status: str, subtitle: str, tooltip: str = "") -> None:
        self.status, self.subtitle = status, subtitle
        self.setToolTip(tooltip)
        self.update()

    def set_highlight(self, inputs: set[str], dimmed: bool) -> None:
        self.highlight, self.dimmed = inputs, dimmed
        self.update()

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
        if change == QGraphicsItem.ItemPositionHasChanged:
            delta = self.pos() - self._last
            self._last = QPointF(self.pos())
            self.group.position = (self.pos().x(), self.pos().y())
            for member in self.group.members:
                node = self.flow_scene.workflow.nodes.get(member)
                if node is not None:
                    node.position = (node.position[0] + delta.x(), node.position[1] + delta.y())
            self.flow_scene.wires_of(self.group.id, rebuild=True)
            self._moving = True
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        hit = self.port_at(event.pos())
        if hit is not None and hit[0] == "out" and event.button() == Qt.LeftButton:
            _side, node_id, port = hit
            self.flow_scene.begin_wire(node_id, port, self.scene_port("out", self.port_id(node_id, port.name)),
                                       event.scenePos())
            event.accept()
            return
        self._moving = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._moving:
            self._moving = False
            self.flow_scene.moved.emit(self.group.id)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.flow_scene.open_group.emit(self.group.id)
        event.accept()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        hit = self.port_at(event.pos())
        self.setCursor(Qt.CrossCursor if hit and hit[0] == "out" else Qt.SizeAllCursor)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.unsetCursor()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = style.tokens()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        if self.dimmed:
            painter.setOpacity(0.32)
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(QColor(t.surface_alt))
        painter.drawRoundedRect(rect.translated(6, 6), 9, 9)          # the stack behind
        stroke = QColor(t.selection) if self.isSelected() else QColor(t.text_secondary)
        painter.setPen(QPen(stroke, 2.0 if self.isSelected() else 1.2))
        painter.setBrush(QColor(t.surface))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QColor(t.text_muted))
        painter.setFont(_font(8.5, True))
        painter.drawText(QPointF(12, 16), f"WORKFLOW · {len(self.group.members)} BOXES")
        colour = STATUS_COLOUR.get(self.status, "muted")
        dot = QColor({"good": style.STATUS["good"], "accent": t.accent, "critical": style.STATUS["critical"],
                      "warning": style.STATUS["warning"], "muted": t.text_muted}[colour])
        painter.setPen(QPen(dot, 1.5))
        painter.setBrush(dot if self.status not in (WAITING, IDLE) else Qt.NoBrush)
        painter.drawEllipse(QPointF(NODE_W - 14, 13), 4.5, 4.5)
        painter.setPen(QColor(t.text))
        painter.setFont(_font(12.5, True))
        metrics = QFontMetricsF(painter.font())
        painter.drawText(QPointF(12, 34), metrics.elidedText(self.group.name, Qt.ElideRight, NODE_W - 36))
        painter.setPen(QColor(t.text_muted))
        painter.setFont(_font(10.5))
        metrics = QFontMetricsF(painter.font())
        painter.drawText(QPointF(12, 52), metrics.elidedText(self.subtitle or "Double-click to open",
                                                             Qt.ElideRight, NODE_W - 24))
        for side, ports in (("in", self.inputs), ("out", self.outputs)):
            for node_id, name, port in ports:
                port_id = self.port_id(node_id, name)
                centre = self.port_point(side, port_id)
                lit = side == "in" and port_id in self.highlight
                painter.setPen(QPen(QColor(t.selection if lit else t.surface), 3 if lit else 2))
                painter.setBrush(QColor(port_colour(port)))
                painter.drawEllipse(centre, PORT_R + (2 if lit else 0), PORT_R + (2 if lit else 0))
                if len(ports) > 1:
                    painter.setPen(QColor(t.text_secondary))
                    painter.setFont(_font(9.5))
                    text = (port.label or port.name) if side == "in" else port.type_name.lower()
                    if side == "in":
                        painter.drawText(QPointF(centre.x() + 10, centre.y() + 3.5), text)
                    else:
                        width = QFontMetricsF(painter.font()).horizontalAdvance(text)
                        painter.drawText(QPointF(centre.x() - 10 - width, centre.y() + 3.5), text)
        painter.setOpacity(1.0)


class WireItem(QGraphicsPathItem):
    """A curve from an output dot to an input dot."""

    def __init__(self, scene: "WorkflowScene", edge: Edge | None, colour: str, faint: bool = False) -> None:
        super().__init__()
        self.flow_scene = scene
        self.edge = edge
        self.colour = colour
        self.faint = faint
        self.hovered = False
        self.setZValue(1)
        self.setAcceptHoverEvents(edge is not None)
        if edge is not None:
            self.setFlag(QGraphicsItem.ItemIsSelectable)
            self.setToolTip("Click to select, Delete to remove this connection")
        self.start = QPointF()
        self.end = QPointF()
        #: "from X" / "to Y" on a wire that crosses an open group's boundary.
        self.stub: str | None = None

    def set_points(self, start: QPointF, end: QPointF) -> None:
        self.start, self.end = start, end
        dx = max(40.0, abs(end.x() - start.x()) / 2)
        path = QPainterPath(start)
        path.cubicTo(QPointF(start.x() + dx, start.y()), QPointF(end.x() - dx, end.y()), end)
        self.setPath(path)

    def shape(self) -> QPainterPath:
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(self.path())

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self.hovered = True
        self.update()

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = False
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(style.tokens().selection) if self.isSelected() else QColor(self.colour)
        if self.faint and not self.isSelected():
            colour.setAlphaF(0.45)
        pen = QPen(colour, 4.0 if self.hovered or self.isSelected() else 2.2)
        if self.edge is None or self.stub:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.path())
        if self.stub:
            painter.setPen(QColor(style.tokens().text_secondary))
            painter.setFont(_font(9.5))
            if self.stub.startswith("from"):
                painter.drawText(QPointF(self.start.x(), self.start.y() - 6), self.stub)
            else:
                width = QFontMetricsF(painter.font()).horizontalAdvance(self.stub)
                painter.drawText(QPointF(self.end.x() - width, self.end.y() - 6), self.stub)


class WorkflowScene(QGraphicsScene):
    """The items of one workflow, kept in step with it."""

    #: A node was selected (its id), or nothing (None).
    selected = Signal(object)
    #: The workflow changed: the ids of the nodes to run from.
    edited = Signal(list)
    #: A node was moved (position only: nothing to re-run).
    moved = Signal(str)
    #: Double-click on empty canvas, at this scene point.
    add_here = Signal(QPointF)
    #: A context menu was asked for: ("node", id) / ("group", id) / ("edge", edge) / ("canvas", point).
    menu = Signal(str, object, object)
    #: Something the user tried and the canvas refused, in plain words.
    refused = Signal(str)
    #: A group was double-clicked: show its own canvas.
    open_group = Signal(str)

    def __init__(self, workflow: Workflow) -> None:
        super().__init__()
        self.workflow = workflow
        self.boxes: dict[str, BoxItem] = {}
        self.groups: dict[str, GroupItem] = {}
        self.wires: list[WireItem] = []
        #: The group whose canvas is shown (None: the whole workflow).
        self.view_group: str | None = None
        #: The last status of every node, kept across rebuilds.
        self.statuses: dict[str, tuple[str, str, str]] = {}
        self._drag: dict | None = None
        self.selectionChanged.connect(self._selection_changed)
        self.rebuild()

    # -- building ----------------------------------------------------------------------
    def rebuild(self) -> None:
        """Items from the workflow (positions and statuses are kept for known nodes).

        At the top level, the nodes outside any group and one item per group;
        inside a group (``view_group``), its members, with stubs for the wires
        that cross its boundary."""
        selected = [b.node.id for b in self.boxes.values() if b.isSelected()] + \
            [g.group.id for g in self.groups.values() if g.isSelected()]
        if self.view_group is not None and self.view_group not in self.workflow.groups:
            self.view_group = None
        self.blockSignals(True)
        self.clear()
        self.boxes, self.groups, self.wires = {}, {}, []
        shown = self.workflow.groups[self.view_group].members if self.view_group else \
            [n for n in self.workflow.nodes if self.workflow.group_of(n) is None]
        for node_id in shown:
            node = self.workflow.nodes.get(node_id)
            if node is None:
                continue
            item = BoxItem(self, node)
            if node.id in self.statuses:
                item.set_status(*self.statuses[node.id])
            self.addItem(item)
            self.boxes[node.id] = item
        if self.view_group is None:
            for group in self.workflow.groups.values():
                item = GroupItem(self, group)
                self.addItem(item)
                self.groups[group.id] = item
                self._group_status(group.id)
        for edge in self.workflow.edges:
            self._add_wire(edge)
        for key in selected:
            if key in self.boxes:
                self.boxes[key].setSelected(True)
            elif key in self.groups:
                self.groups[key].setSelected(True)
        self.blockSignals(False)
        self.setSceneRect(self.itemsBoundingRect().adjusted(-400, -300, 400, 300))

    def owner(self, node_id: str):
        """The visible item standing for a node: its box, or its group's item."""
        if node_id in self.boxes:
            return self.boxes[node_id]
        group = self.workflow.group_of(node_id)
        return self.groups.get(group.id) if group is not None else None

    def _endpoint(self, item, side: str, node_id: str, port_name: str) -> QPointF:
        if isinstance(item, GroupItem):
            return item.scene_port(side, GroupItem.port_id(node_id, port_name))
        return item.scene_port(side, port_name)

    def _add_wire(self, edge: Edge) -> WireItem | None:
        source, target = self.owner(edge.source), self.owner(edge.target)
        if source is None and target is None:
            return None
        if source is target and isinstance(source, GroupItem):
            return None                                     # hidden inside the group
        port = self.workflow.spec(edge.source).output(edge.output)
        colour = port_colour(port) if port else style.tokens().text_muted
        faint = edge.input in ("log", "ref", "true_net") and len(self.workflow.spec(edge.target).inputs) > 1
        wire = WireItem(self, edge, colour, faint or source is None or target is None)
        self._route(wire, source, target)
        self.addItem(wire)
        self.wires.append(wire)
        return wire

    def _route(self, wire: WireItem, source, target) -> None:
        edge = wire.edge
        if source is not None and target is not None:
            wire.set_points(self._endpoint(source, "out", edge.source, edge.output),
                            self._endpoint(target, "in", edge.target, edge.input))
            wire.stub = None
        elif target is not None:                               # from outside the open group
            end = self._endpoint(target, "in", edge.target, edge.input)
            wire.set_points(QPointF(end.x() - 120, end.y()), end)
            wire.stub = f"from {self.workflow.title(edge.source)}"
        else:                                                   # to outside the open group
            start = self._endpoint(source, "out", edge.source, edge.output)
            wire.set_points(start, QPointF(start.x() + 120, start.y()))
            wire.stub = f"to {self.workflow.title(edge.target)}"

    def wires_of(self, key: str, rebuild: bool = False) -> list[WireItem]:
        """The wires touching a node or a group (by id)."""
        members = set(self.workflow.groups[key].members) if key in self.workflow.groups else {key}
        found = [w for w in self.wires if w.edge is not None and (w.edge.source in members or w.edge.target in members)]
        if rebuild:
            for wire in found:
                self._route(wire, self.owner(wire.edge.source), self.owner(wire.edge.target))
        return found

    def set_status(self, node_id: str, status: str, subtitle: str, tooltip: str = "") -> None:
        self.statuses[node_id] = (status, subtitle, tooltip)
        item = self.boxes.get(node_id)
        if item is not None:
            item.set_status(status, subtitle, tooltip)
        group = self.workflow.group_of(node_id)
        if group is not None and group.id in self.groups:
            self._group_status(group.id)

    def _group_status(self, group_id: str) -> None:
        group, item = self.workflow.groups.get(group_id), self.groups.get(group_id)
        if group is None or item is None:
            return
        statuses = [self.statuses.get(m, (WAITING, "", ""))[0] for m in group.members]
        blocked = next((self.statuses[m][1] for m in group.members
                        if self.statuses.get(m, (WAITING,))[0] == BLOCKED), "")
        for status, text in ((FAILED, "A box inside failed"), (RUNNING, "Running…"),
                             (BLOCKED, f"A box inside {blocked}" if blocked.startswith("needs") else
                              "Waiting for your OK")):
            if status in statuses:
                item.set_status(status, text)
                return
        if statuses and all(s == DONE for s in statuses):
            item.set_status(DONE, "Double-click to open")
        else:
            item.set_status(IDLE if IDLE in statuses else WAITING, "Waiting for an earlier box")

    # -- groups ---------------------------------------------------------------------------
    def group_selected(self, name: str = "") -> Group | None:
        """Group the selected top-level boxes (two or more)."""
        if self.view_group is not None:
            self.refused.emit("Groups are made on the main canvas")
            return None
        ids = [b.node.id for b in self.boxes.values() if b.isSelected()]
        if len(ids) < 2:
            self.refused.emit("Select two or more boxes (drag around them, or shift-click), then group them")
            return None
        group = self.workflow.group(ids, name or f"Group of {len(ids)} boxes")
        self.rebuild()
        self.clearSelection()
        self.groups[group.id].setSelected(True)
        self.moved.emit(group.id)
        return group

    def ungroup(self, group_id: str) -> None:
        if group_id not in self.workflow.groups:
            return
        members = list(self.workflow.groups[group_id].members)
        self.workflow.ungroup(group_id)
        if self.view_group == group_id:
            self.view_group = None
        self.rebuild()
        self.clearSelection()
        for member in members:
            if member in self.boxes:
                self.boxes[member].setSelected(True)
        self.moved.emit(group_id)

    def show_group(self, group_id: str | None) -> None:
        self.view_group = group_id if group_id in self.workflow.groups else None
        self.rebuild()
        self.clearSelection()

    def add_node(self, box, position: QPointF, settings: dict | None = None) -> Node:
        node = self.workflow.add(box, settings, (round(position.x() / GRID) * GRID,
                                                 round(position.y() / GRID) * GRID))
        if self.view_group is not None:                        # added inside an open group
            self.workflow.groups[self.view_group].members.append(node.id)
        item = BoxItem(self, node)
        self.addItem(item)
        self.boxes[node.id] = item
        self.clearSelection()
        item.setSelected(True)
        self.setSceneRect(self.itemsBoundingRect().adjusted(-400, -300, 400, 300))
        self.edited.emit([node.id])
        return node

    def remove_node(self, node_id: str) -> None:
        after = [e.target for e in self.workflow.edges if e.source == node_id]
        self.workflow.remove(node_id)
        self.rebuild()
        self.edited.emit(after)

    def remove_edge(self, edge: Edge) -> None:
        self.workflow.disconnect(edge)
        self.rebuild()
        self.edited.emit([edge.target])

    def remove_group(self, group_id: str) -> None:
        """Remove a group and every box in it."""
        group = self.workflow.groups.get(group_id)
        if group is None:
            return
        after = [e.target for e in self.workflow.edges
                 if e.source in group.members and e.target not in group.members]
        for member in list(group.members):
            self.workflow.remove(member)
        self.workflow.groups.pop(group_id, None)
        self.rebuild()
        self.edited.emit([n for n in after if n in self.workflow.nodes])

    def remove_selected(self) -> None:
        nodes = [item.node.id for item in self.selectedItems() if isinstance(item, BoxItem) and not item.locked]
        edges = [item.edge for item in self.selectedItems() if isinstance(item, WireItem) and item.edge]
        for item in self.selectedItems():
            if isinstance(item, GroupItem):
                nodes.extend(item.group.members)
        if not nodes and not edges:
            return
        after: list[str] = []
        for edge in edges:
            after.append(edge.target)
            self.workflow.disconnect(edge)
        for node_id in nodes:
            after.extend(e.target for e in self.workflow.edges if e.source == node_id)
            self.workflow.remove(node_id)
        self.rebuild()
        self.edited.emit([n for n in after if n in self.workflow.nodes])

    def duplicate(self, node_id: str) -> Node | None:
        node = self.workflow.nodes.get(node_id)
        if node is None:
            return None
        import copy
        copy_node = self.workflow.add(node.box, copy.deepcopy(node.settings),
                                      (node.position[0] + 30, node.position[1] + node_height(
                                          len(self.workflow.spec(node).inputs),
                                          len(self.workflow.spec(node).outputs)) + 20))
        for edge in [e for e in self.workflow.edges if e.target == node_id]:
            self.workflow.connect(edge.source, copy_node, edge.input, edge.output)
        self.rebuild()
        self.clearSelection()
        self.boxes[copy_node.id].setSelected(True)
        self.edited.emit([copy_node.id])
        return copy_node

    def disconnect_all(self, node_id: str) -> None:
        touching = [e for e in self.workflow.edges if node_id in (e.source, e.target)]
        if not touching:
            return
        for edge in touching:
            self.workflow.disconnect(edge)
        self.rebuild()
        self.edited.emit([node_id] + [e.target for e in touching if e.source == node_id])

    # -- selection ---------------------------------------------------------------------
    def _selection_changed(self) -> None:
        self.selected.emit(self.selected_node())

    def selected_node(self) -> str | None:
        """The id of the selected box or group (the last one), or None."""
        items = [item for item in self.selectedItems() if isinstance(item, (BoxItem, GroupItem))]
        if not items:
            return None
        item = items[-1]
        return item.node.id if isinstance(item, BoxItem) else item.group.id

    def select(self, key: str | None) -> None:
        self.clearSelection()
        if key in self.boxes:
            self.boxes[key].setSelected(True)
        elif key in self.groups:
            self.groups[key].setSelected(True)

    # -- dragging a wire -----------------------------------------------------------------
    def _items(self):
        return [*self.boxes.values(), *self.groups.values()]

    def begin_wire(self, source_id: str, port: Port, start: QPointF, at: QPointF) -> None:
        """Start dragging a wire from node ``source_id``'s output ``port``."""
        wire = WireItem(self, None, port_colour(port))
        wire.setZValue(3)
        self.addItem(wire)
        source = self.workflow.nodes[source_id]
        self._drag = {"from": source_id, "port": port, "wire": wire, "start": start}
        for other in self._items():
            fitting = {pid for pid, node_id, p in other.drop_inputs()
                       if self.workflow.can_connect(source, node_id, p.name, port.name)[0]}
            own = other is self.owner(source_id)
            other.set_highlight(fitting, dimmed=not own and not fitting)
        self._drag_to(at)

    def _drag_to(self, at: QPointF) -> None:
        drag = self._drag
        drag["wire"].set_points(drag["start"], at)

    def _drop_target(self, at: QPointF) -> tuple[str, Port] | None:
        """``(target node id, Port)`` of the lit input the wire was dropped on."""
        best = None
        for other in self._items():
            local = other.mapFromScene(at)
            inside = other.rect().contains(local)
            inputs = other.drop_inputs()
            for pid, node_id, port in inputs:
                if pid not in other.highlight:
                    continue
                centre = other.port_point("in", pid)
                distance = math.hypot(local.x() - centre.x(), local.y() - centre.y())
                if distance < 30 or (inside and len(inputs) == 1):
                    if best is None or distance < best[2]:
                        best = (node_id, port, distance)
        return (best[0], best[1]) if best else None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag_to(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            drag, self._drag = self._drag, None
            self.removeItem(drag["wire"])
            target = self._drop_target(event.scenePos())         # before the highlights go
            for other in self._items():
                other.set_highlight(set(), False)
            source = self.workflow.nodes[drag["from"]]
            if target is not None:
                node_id, port = target
                ok, detail = self.workflow.can_connect(source, node_id, port.name, drag["port"].name)
                if ok:
                    self.workflow.connect(source, node_id, port.name, drag["port"].name)
                    self.rebuild()
                    self.edited.emit([node_id])
                else:
                    self.refused.emit(detail)
            else:
                # Dropped on a box that does not take it: say why, about the nearest input.
                for other in self._items():
                    local = other.mapFromScene(event.scenePos())
                    inputs = other.drop_inputs()
                    if other.rect().contains(local) and other is not self.owner(drag["from"]) and inputs:
                        pid, node_id, nearest = min(inputs, key=lambda i: abs(other.port_point("in", i[0]).y() - local.y()))
                        ok, detail = self.workflow.can_connect(source, node_id, nearest.name, drag["port"].name)
                        if not ok:
                            self.refused.emit(detail)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.itemAt(event.scenePos(), self.views()[0].transform() if self.views() else None) is None:
            self.add_here.emit(event.scenePos())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        item = self.itemAt(event.scenePos(), self.views()[0].transform() if self.views() else None)
        while item is not None and not isinstance(item, (BoxItem, GroupItem, WireItem)):
            item = item.parentItem()
        if isinstance(item, BoxItem):
            self.select(item.node.id)
            self.menu.emit("node", item.node.id, event.screenPos())
        elif isinstance(item, GroupItem):
            self.select(item.group.id)
            self.menu.emit("group", item.group.id, event.screenPos())
        elif isinstance(item, WireItem) and item.edge is not None:
            self.menu.emit("edge", item.edge, event.screenPos())
        else:
            self.menu.emit("canvas", event.scenePos(), event.screenPos())
        event.accept()


class WorkflowView(GraphView):
    """The canvas view: panning, zooming and the floating zoom bar, from
    GraphView; files dropped on it become input boxes (:attr:`files_dropped`)."""

    drawing_name = "The workflow"
    #: Local files were dropped: their paths, and where (scene coordinates).
    files_dropped = Signal(list, QPointF)

    def __init__(self, scene: WorkflowScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setDragMode(self.DragMode.RubberBandDrag)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths, self.mapToScene(event.position().toPoint()))

    def fit(self) -> None:
        if self.fit_waits():
            return
        self.auto_fit = True
        rect = self.graph.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-30, -30, 30, 60), Qt.KeepAspectRatio)
        if self.transform().m11() > 1.25:
            self.resetTransform()
            self.scale(1.25, 1.25)
            self.centerOn(rect.center())
        self.zoom_changed.emit(self.transform().m11())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Empty canvas: drag pans (as the net editor); on a box, the box moves.
        if event.button() == Qt.LeftButton and self.itemAt(event.position().toPoint()) is None:
            self.setDragMode(self.DragMode.ScrollHandDrag)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.setDragMode(self.DragMode.RubberBandDrag)
