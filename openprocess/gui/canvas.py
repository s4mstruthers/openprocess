"""The net canvas: the drawing surface and its editing tools.

Two classes:

:class:`NetScene`
    Owns the mapping from model objects to graphics items and rebuilds the
    scene when the page changes.  It also implements the editing tools, because
    a tool's behaviour is defined by where you click in the *scene*.

:class:`NetView`
    A thin :class:`QGraphicsView` adding zooming and panning.  Kept separate so
    that the interaction logic in the scene is testable without a viewport.

Editing model
-------------
The canvas edits the model objects **in place**.  There is no separate
"document" layer: dragging a place writes its new coordinates straight onto
``place.graphics``.  Signals tell the main window what changed so it can mark
the file dirty and refresh the panels.

Tools
-----
``select``
    Default.  Click to select, drag to move.
``place`` / ``transition``
    Click on empty canvas to create one there.
``arc``
    Click a place then a transition (or the reverse) to connect them.  The
    direction is inferred from the order of the two clicks, which is how CPN
    Tools behaves and saves having two separate arc tools.

With ``select``, the arrow beside a hovered node draws arcs the same way.
Dragging an arc (either way) onto empty canvas adds the other kind of node
there, joined by it -- the quick way to grow a net.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QApplication, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsScene,
    QGraphicsSceneMouseEvent, QGraphicsView, QWidget,
)

from ..model.net import Arc, CPNet, Page, Place, Transition
from . import arc_editing as edit
from . import theme
from .panning import CanvasPanning
from .items import (
    PLAIN_PLACE, PLAIN_TRANSITION, ArcItem, PlaceItem, TransitionItem, to_model,
)

#: Grid spacing in scene units: the background's dots, and what nodes and
#: arc bends snap to when :attr:`NetScene.snap_to_grid` is on.
GRID_STEP = 28


def on_grid(point: QPointF) -> QPointF:
    """The grid dot nearest to ``point``."""
    return QPointF(round(point.x() / GRID_STEP) * GRID_STEP,
                   round(point.y() / GRID_STEP) * GRID_STEP)
#: Perpendicular separation between two arcs joining the same pair of nodes.
PARALLEL_ARC_SPACING = 14.0


class _ConnectHandle(QGraphicsPathItem):
    """The translucent arrow that appears next to a hovered place or
    transition: drag it to another node to draw an arc.  It sits on the
    side facing the mouse and keeps the same size at every zoom."""

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(60)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip("Drag to another place or transition to add an arc")
        path = QPainterPath()
        path.moveTo(-9, -2.2)
        path.lineTo(3, -2.2)
        path.lineTo(3, -7)
        path.lineTo(11, 0)
        path.lineTo(3, 7)
        path.lineTo(3, 2.2)
        path.lineTo(-9, 2.2)
        path.closeSubpath()
        self.setPath(path)
        accent = theme.palette().accent
        self.setBrush(accent)
        self.setPen(QPen(QColor(255, 255, 255, 140), 1.0))
        #: The node it belongs to.
        self.node = None
        self.set_hot(False)

    #: Opacity at rest (a hint that it is there, not in the way) and with the
    #: mouse on it (clear enough to aim at).
    RESTING, HOT = 0.3, 0.75

    def set_node(self, node) -> None:
        self.node = node
        other = "transition" if isinstance(node, PlaceItem) else "place"
        self.setToolTip(f"Drag to a {other} to add an arc, or to empty space to add "
                        f"a new {other} joined by one")

    def set_hot(self, hot: bool) -> None:
        """Mouse on the handle: clearer and a little larger."""
        self.setOpacity(self.HOT if hot else self.RESTING)
        self.setScale(1.15 if hot else 1.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(1, 0), 13, 13)         # easy to grab
        return path

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-14, -14, 28, 28)


class _GhostNode(QGraphicsPathItem):
    """While an arc is dragged over empty canvas: a see-through preview of
    the place or transition that letting go there will add."""

    def __init__(self) -> None:
        super().__init__()
        accent = theme.palette().accent
        pen = QPen(accent, 1.6, Qt.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        fill = QColor(accent)
        fill.setAlpha(40)
        self.setBrush(fill)
        self.setZValue(41)                     # just above the rubber-band line
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.kind: type | None = None

    def show_as(self, kind: type, rect: QRectF, centre: QPointF, radius: float) -> None:
        if kind is not self.kind or rect != self.path().boundingRect():
            path = QPainterPath()
            if kind is PlaceItem:
                path.addEllipse(rect)
            else:
                path.addRoundedRect(rect, radius, radius)
            self.setPath(path)
            self.kind = kind
        self.setPos(centre)
        self.show()


def _rect(item: QGraphicsItem) -> tuple[float, float, float, float]:
    """A node's outline in scene coordinates: (left, top, width, height)."""
    rect = item.rect().translated(item.pos())
    return rect.left(), rect.top(), rect.width(), rect.height()


class NetScene(QGraphicsScene):
    """Draws one page of a model and handles clicks on it."""

    #: Put new and moved nodes, and arc bends, on the grid's dots (one
    #: setting for every editor; the Snap to grid box).
    snap_to_grid = False

    #: Emitted with the selected model object (or ``None``) when selection changes.
    selection_changed = Signal(object)
    #: Emitted whenever the model was modified by a canvas interaction.
    model_changed = Signal()
    #: Emitted when the user clicks a transition while the simulator is active.
    transition_activated = Signal(object)
    #: Emitted just *before* the canvas changes the model (for undo).
    about_to_change = Signal()
    #: A drag may be starting (for undo: the state before a possible move).
    drag_started = Signal()
    #: Elements were dragged to new positions.
    moved = Signal()
    #: A place or transition was just created (the page offers to name it).
    created = Signal(object)
    #: A place or transition was double-clicked: rename it.
    rename_requested = Signal(object)
    #: A short explanation for the status bar (e.g. why an arc was refused).
    message = Signal(str)

    def __init__(self, net: CPNet) -> None:
        super().__init__()
        self.net = net
        self.page: Page | None = net.pages[0] if net.pages else None
        self.tool = "select"
        #: When False, clicking an enabled transition only selects it (edit
        #: mode); when True it fires it (the token game).
        self.fire_on_click = True
        #: Token values next to the count bubbles: None = as the model says
        #: (CPN Tools' per-place "hide marking"), True = all, False = none.
        self.show_token_values: bool | None = None
        #: While the arc tool is mid-draw: the node it started from, the
        #: rubber-band line and the node under the mouse.
        self._connecting: dict | None = None
        #: The "drag to connect" arrow next to a hovered node (made on demand).
        self._handle: _ConnectHandle | None = None
        #: The preview of the node a drag to empty space will add (on demand).
        self._ghost: _GhostNode | None = None
        #: Edit mode: arcs and labels can be dragged (CPN IDE's rules, see
        #: :mod:`openprocess.gui.arc_editing`).
        self.editable = True
        #: An arc being edited: see :meth:`_start_arc_drag`.
        self._arc_drag: dict | None = None
        #: A node being dragged: what its centre can snap to.
        self._snap_node: PlaceItem | TransitionItem | None = None
        self._snap_targets: list[tuple[float, float]] = []
        self._guides: list[QGraphicsLineItem] = []

        self.place_items: dict[str, PlaceItem] = {}
        self.transition_items: dict[str, TransitionItem] = {}
        self.arc_items: dict[str, ArcItem] = {}

        self.setBackgroundBrush(theme.palette().canvas)
        self.selectionChanged.connect(self._on_selection_changed)

    # =======================================================================
    # Building
    # =======================================================================
    def show_page(self, page: Page | None) -> None:
        """Replace the scene contents with ``page``."""
        self.page = page
        self.rebuild()

    def rebuild(self) -> None:
        """Recreate every item from the model.

        Rebuilding wholesale rather than diffing keeps the code honest: there
        is exactly one way the canvas can get out of step with the model, and
        it is fixed by calling this.  Nets are small enough that the cost is
        irrelevant.
        """
        self.clear()
        self._guides = []                      # cleared with the scene
        self._arc_drag = None
        self._connecting = None
        self._handle = None                    # deleted with the scene
        self._ghost = None
        self._snap_node = None
        self.place_items.clear()
        self.transition_items.clear()
        self.arc_items.clear()

        if self.page is None:
            return

        plain = getattr(self.net, "plain", False)
        outside = getattr(self.net, "names_outside", False)
        for place in self.page.places:
            item = PlaceItem(place, plain, outside)
            self.addItem(item)
            self.place_items[place.id] = item

        for transition in self.page.transitions:
            item = TransitionItem(transition, plain, outside)
            self.addItem(item)
            self.transition_items[transition.id] = item

        # Arcs joining the same place and transition must be separated, or a
        # consume-and-return pair would be drawn exactly on top of itself.
        # Group them first, then give each member of a group its own bow.
        groups: dict[tuple[str, str], list[Arc]] = {}
        for arc in self.page.arcs:
            groups.setdefault((arc.place_id, arc.transition_id), []).append(arc)

        bows: dict[str, float] = {}
        for members in groups.values():
            count = len(members)
            for index, arc in enumerate(members):
                # Symmetric offsets about zero: a lone arc gets 0 and stays
                # straight, a pair gets -s/+s, a triple -s/0/+s, and so on.
                bows[arc.id] = (index - (count - 1) / 2) * PARALLEL_ARC_SPACING

        for arc in self.page.arcs:
            place_item = self.place_items.get(arc.place_id)
            transition_item = self.transition_items.get(arc.transition_id)
            if place_item is None or transition_item is None:
                # A dangling arc (endpoint deleted); skip rather than crash.
                continue
            item = ArcItem(arc, place_item, transition_item, bow=bows.get(arc.id, 0.0),
                           plain=plain)
            self.addItem(item)
            self.arc_items[arc.id] = item

        self._apply_error_highlighting()
        # The margin matches zoom_to_fit's, so a fitted page shows no scroll bars.
        self.setSceneRect(self.itemsBoundingRect().adjusted(-40, -40, 40, 40))

    def refresh_labels(self) -> None:
        """Re-read inscriptions from the model without rebuilding the scene."""
        for item in self.place_items.values():
            item.refresh()
        for item in self.transition_items.values():
            item.refresh()
        for item in self.arc_items.values():
            item.reroute()
        self._apply_error_highlighting()

    def _apply_error_highlighting(self) -> None:
        """Outline in red every element whose inscription failed to compile.

        Doing this for all three element kinds -- not just arcs -- means a bad
        initial marking or a bad guard is visible on the canvas, not only in
        the problems list.
        """
        failing = {issue.element_id for issue in self.net.errors}
        for identifier, item in self.arc_items.items():
            item.set_error(identifier in failing)
        for identifier, place_item in self.place_items.items():
            place_item.set_error(identifier in failing)
        for identifier, transition_item in self.transition_items.items():
            transition_item.set_error(identifier in failing)

    # =======================================================================
    # Simulation feedback
    # =======================================================================
    def update_enabling(self, counts: dict[str, int]) -> None:
        """Colour transitions by their number of enabled binding elements."""
        for transition_id, item in self.transition_items.items():
            item.set_enabled_count(counts.get(transition_id, 0))

    #: How many steps the trace fades over.
    TRACE_FADE = 8

    def set_trace(self, fired: list[tuple[int, str]] | None) -> None:
        """Highlight the path the tokens took: ``fired`` is ``(step number,
        transition id)`` for each firing so far (None switches it off).

        Every fired transition gets a badge with its step numbers, and its
        input and output arcs are underlaid in the accent colour -- strongest
        for the latest step, fading over :attr:`TRACE_FADE` steps.
        """
        steps: dict[str, list[int]] = {}
        age: dict[str, int] = {}
        for index, (step, transition_id) in enumerate(fired or []):
            steps.setdefault(transition_id, []).append(step)
            age[transition_id] = len(fired) - 1 - index
        strength = {t: max(0.15, 1.0 - a / self.TRACE_FADE) for t, a in age.items()}
        for transition_id, item in self.transition_items.items():
            item.set_trace(steps.get(transition_id, []), strength.get(transition_id, 0.0))
        for item in self.arc_items.values():
            item.set_trace(strength.get(item.arc.transition_id, 0.0))

    def update_markings(self, describe: Callable[[str], tuple[int, str]]) -> None:
        """Show the simulator's current token contents on each place.

        ``describe`` returns ``(token_count, multiset_text)``; the count drives
        the pill and the text the annotation beside it.
        """
        for place_id, item in self.place_items.items():
            count, text = describe(place_id)
            item.set_marking(count, text, self.show_token_values)

    # =======================================================================
    # Interaction
    # =======================================================================
    def _on_selection_changed(self) -> None:
        items = self.selectedItems()
        if not items:
            self.selection_changed.emit(None)
            return
        item = items[0]
        if isinstance(item, PlaceItem):
            self.selection_changed.emit(item.place)
        elif isinstance(item, TransitionItem):
            self.selection_changed.emit(item.transition)
        elif isinstance(item, ArcItem):
            self.selection_changed.emit(item.arc)
        else:
            self.selection_changed.emit(None)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self.page is None or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        position = event.scenePos()
        if self._connecting is not None and self._connecting.get("clicked"):
            self._arc_press(position)             # the second click of click-click
            return
        if not self._handle_hit(position):
            # A redraw (say, finishing a name with this very click) takes the
            # arrow away: put it back where the mouse is, if it belongs there.
            self._update_connect_handle(position)
        if self._handle_hit(position):
            node = self._handle.node
            self.hide_connect_handle()
            self._begin_connection(node, position)
            event.accept()
            return
        self.hide_connect_handle()
        item = self.itemAt(position, QTransform())
        if self.tool == "select":
            # Remember where everything is, to tell on release whether this
            # press turned into a move.
            self._positions_before = self._layout_state()
            self.drag_started.emit()
            if self.editable and self._start_arc_drag(event, item):
                return
            self._start_node_drag(item)

        if self.tool == "place":
            self._create_place(position)
            return
        if self.tool == "transition":
            self._create_transition(position)
            return
        if self.tool == "silent":
            self._create_transition(position, silent=True)
            return
        if self.tool == "arc":
            self._arc_press(position)
            return

        # Select tool: a plain click on an enabled transition asks the main
        # window to fire it.  This is the "token game" interaction.
        owner = self._owner_of(item)
        if isinstance(owner, Transition) and event.modifiers() == Qt.NoModifier \
                and self.fire_on_click:
            transition_item = self.transition_items.get(owner.id)
            if transition_item is not None and transition_item.enabled_count:
                self.transition_activated.emit(owner)
        super().mousePressEvent(event)

    def _layout_state(self) -> tuple:
        """Everything a drag can change: node positions, bends, label spots."""
        items = list(self.place_items.items()) + list(self.transition_items.items())
        nodes = {key: (item.pos().x(), item.pos().y(),
                       tuple(sorted(item.graphics().label_offsets.items())))
                 for key, item in items}
        arcs = {key: (tuple(item.arc.bendpoints),
                      item.arc.graphics.label_offsets.get("annot"))
                for key, item in self.arc_items.items()}
        return nodes, arcs

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._connecting is not None:
            self._arc_move(event.scenePos())
            return
        if not event.buttons():
            self._update_connect_handle(event.scenePos())
        if self._arc_drag is not None:
            self._drag_arc(event)
            return
        super().mouseMoveEvent(event)
        if self._snap_node is not None and event.buttons() & Qt.LeftButton:
            self._snap_dragged_nodes()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._connecting is not None:
            self._arc_release(event.scenePos())
            return
        if self._arc_drag is not None:
            self._finish_arc_drag(event)
        else:
            super().mouseReleaseEvent(event)
        self._end_node_drag()
        before = getattr(self, "_positions_before", None)
        self._positions_before = None
        if before is not None and before != self._layout_state():
            self.moved.emit()

    # -- moving nodes (with CPN IDE's snapping) ------------------------------
    def _start_node_drag(self, item: QGraphicsItem | None) -> None:
        """A press on a node may start moving it (and the rest of the selection).

        Every arc remembers its shape, so its bends can be kept in order
        while its nodes move; the pressed node's centre will snap level
        with (or in line with) other nodes and with the corners of
        right-angled arcs, as in CPN IDE.
        """
        node = item
        while node is not None and not isinstance(node, (PlaceItem, TransitionItem, ArcItem)):
            node = node.parentItem()
        if not isinstance(node, (PlaceItem, TransitionItem)):
            node = None
        self._snap_node = node
        for arc_item in self.arc_items.values():
            arc_item.start_node_drag()
        if node is None:
            return
        moving = {node, *self.selected_nodes()}
        targets = [(n.pos().x(), n.pos().y())
                   for n in [*self.place_items.values(), *self.transition_items.values()]
                   if n not in moving]
        for arc_item in self.arc_items.values():
            points = [(p.x(), p.y()) for p in arc_item.waypoints()]
            targets.extend(edit.corner_bends(points))
        self._snap_targets = targets

    def selected_nodes(self) -> list[PlaceItem | TransitionItem]:
        return [i for i in self.selectedItems() if isinstance(i, (PlaceItem, TransitionItem))]

    def _snap_dragged_nodes(self) -> None:
        node = self._snap_node
        # Only while the node itself is dragged (not one of its labels).
        if node is None or not node.isSelected() or self.mouseGrabberItem() is not node:
            return
        centre = (node.pos().x(), node.pos().y())
        if self.snap_to_grid:
            # The pressed node goes dot to dot; the rest of the selection
            # moves with it, keeping its shape (so it stays on the grid too
            # if it was).  On the grid, nodes line up by themselves: no guides.
            target = on_grid(node.pos())
            delta = target - node.pos()
            if delta.x() or delta.y():
                for other in self.selected_nodes():
                    other.setPos(other.pos() + delta)
            return
        sx, sy = edit.snap_node(centre, self._snap_targets)
        dx = 0.0 if sx is None else sx - centre[0]
        dy = 0.0 if sy is None else sy - centre[1]
        if dx or dy:
            for other in self.selected_nodes():
                other.setPos(other.pos() + QPointF(dx, dy))
        self._show_guides(sx, sy)

    def _show_guides(self, x: float | None, y: float | None) -> None:
        """Dashed lines showing what the dragged node lined up with."""
        if not self._guides:
            pen = QPen(theme.palette().accent, 1.0, Qt.DashLine)
            pen.setCosmetic(True)
            for _ in range(2):
                line = QGraphicsLineItem()
                line.setPen(pen)
                line.setZValue(50)
                self.addItem(line)
                self._guides.append(line)
        area = self.sceneRect().adjusted(-2000, -2000, 2000, 2000)
        vertical, horizontal = self._guides
        vertical.setVisible(x is not None)
        horizontal.setVisible(y is not None)
        if x is not None:
            vertical.setLine(x, area.top(), x, area.bottom())
        if y is not None:
            horizontal.setLine(area.left(), y, area.right(), y)

    def _end_node_drag(self) -> None:
        for arc_item in self.arc_items.values():
            arc_item.drag_origin = None
        self._snap_node = None
        for line in self._guides:
            if line.scene() is self:
                self.removeItem(line)
        self._guides = []

    # -- editing arcs (CPN IDE's rules) --------------------------------------
    def _tolerance(self) -> float:
        """Grab distance in scene units: 10, or more when zoomed far out."""
        views = self.views()
        scale = views[0].transform().m11() if views else 1.0
        return max(edit.HIT_TOLERANCE, 6.0 / max(scale, 1e-6))

    def _start_arc_drag(self, event: QGraphicsSceneMouseEvent,
                        item: QGraphicsItem | None) -> bool:
        """A press on an arc: decide what dragging will do (see
        :func:`~openprocess.gui.arc_editing.hit_test`).  Nothing changes until the
        mouse actually moves; a plain click just selects the arc."""
        position = event.scenePos()
        p = (position.x(), position.y())
        tolerance = self._tolerance()
        # The handles of a hovered or selected arc sit above everything else.
        candidates = [a for a in self.arc_items.values() if a.handles_visible()]
        if isinstance(item, ArcItem) and item not in candidates:
            candidates.append(item)
        hit = None
        for arc_item in candidates:
            points = [(w.x(), w.y()) for w in arc_item.waypoints()]
            found = edit.hit_test(points, p, tolerance)
            if found is None:
                continue
            kind, index, _ = found
            on_handle = kind == "bend"
            if on_handle or arc_item is item:
                hit = (arc_item, found)
                break
        if hit is None:
            return False
        arc_item, (kind, index, point) = hit
        last = len(arc_item.waypoints()) - 1
        if kind == "bend" and index in (0, last):
            kind = "reconnect"
        if not arc_item.isSelected():
            if not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.clearSelection()
            arc_item.setSelected(True)
        self._arc_drag = {
            "item": arc_item, "kind": kind, "index": index, "press": position,
            "screen": event.screenPos(), "started": False,
            "bends": list(arc_item.arc.bendpoints),
            "waypoints": [(w.x(), w.y()) for w in arc_item.waypoints()],
            "target": None,
        }
        event.accept()
        return True

    def _drag_arc(self, event: QGraphicsSceneMouseEvent) -> None:
        drag = self._arc_drag
        arc_item: ArcItem = drag["item"]
        if not drag["started"]:
            moved = event.screenPos() - drag["screen"]
            if abs(moved.x()) + abs(moved.y()) < 4:
                return                                # still a click
            drag["started"] = True
            arc_item.editing = True
            if drag["kind"] == "insert":              # a new bend where the line was grabbed
                arc_item.arc.bendpoints.insert(drag["index"] - 1, to_model(drag["press"]))
                drag["kind"], drag["waypoints"] = "bend", (
                    drag["waypoints"][:drag["index"]]
                    + [(drag["press"].x(), drag["press"].y())]
                    + drag["waypoints"][drag["index"]:])
        position = event.scenePos()
        p = (position.x(), position.y())
        kind, index, points = drag["kind"], drag["index"], drag["waypoints"]
        if kind == "bend":
            # Snap level with / in line with the neighbours (a node's centre
            # stands in for an end of the arc).
            neighbours = [points[index - 1], points[index + 1]]
            if index - 1 == 0:
                neighbours[0] = self._centre(arc_item.transition_item)
            if index + 1 == len(points) - 1:
                neighbours[1] = self._centre(arc_item.place_item)
            snapped = (on_grid(position).toTuple() if self.snap_to_grid
                       else edit.snap_bend(p, neighbours))
            arc_item.arc.bendpoints[index - 1] = to_model(QPointF(*snapped))
            arc_item.reroute()
        elif kind == "segment":
            a, b = points[index - 1], points[index]
            axis = 1 if edit.aligned(a, b) == "horizontal" else 0
            delta = p[axis] - (drag["press"].y() if axis else drag["press"].x())
            if self.snap_to_grid:                     # the segment lands on a grid line
                delta = round((a[axis] + delta) / GRID_STEP) * GRID_STEP - a[axis]
            new_points = edit.move_segment(
                points, index, delta,
                (self._centre(arc_item.transition_item), _rect(arc_item.transition_item)),
                (self._centre(arc_item.place_item), _rect(arc_item.place_item)))
            arc_item.arc.bendpoints = [to_model(QPointF(*q)) for q in new_points[1:-1]]
            arc_item.reroute()
        elif kind == "reconnect":
            which = "transition" if index == 0 else "place"
            arc_item.loose_end = (which, position)
            target = self._node_at(position, PlaceItem if which == "place" else TransitionItem)
            if drag["target"] is not None and drag["target"] is not target:
                drag["target"].set_halo(False)
            if target is not None:
                target.set_halo(True)
            drag["target"] = target
            arc_item.reroute()

    def _finish_arc_drag(self, event: QGraphicsSceneMouseEvent) -> None:
        drag, self._arc_drag = self._arc_drag, None
        arc_item: ArcItem = drag["item"]
        arc_item.editing = False
        if not drag["started"]:
            arc_item.update()
            return
        if drag["kind"] == "reconnect":
            target = drag["target"]
            arc_item.loose_end = None
            if target is not None:
                target.set_halo(False)
            which = "transition" if drag["index"] == 0 else "place"
            current = arc_item.transition_item if which == "transition" else arc_item.place_item
            if target is None or target is current:
                arc_item.reroute()
                return
            self.about_to_change.emit()
            if which == "place":
                arc_item.arc.place_id = target.model_id
            else:
                arc_item.arc.transition_id = target.model_id
            arc_id = arc_item.arc.id
            self.rebuild()
            self.model_changed.emit()
            new_item = self.arc_items.get(arc_id)
            if new_item is not None:
                new_item.setSelected(True)
            return
        # Bends left in a straight line with their neighbours disappear.
        points = [(w.x(), w.y()) for w in arc_item.waypoints()]
        tidy = edit.without_redundant(points)
        arc_item.arc.bendpoints = [to_model(QPointF(*q)) for q in tidy[1:-1]]
        arc_item.reroute()
        arc_item.update()

    @staticmethod
    def _centre(item: QGraphicsItem) -> tuple[float, float]:
        return item.pos().x(), item.pos().y()

    def _node_at(self, position: QPointF, kind: type):
        for item in self.items(position):
            while item is not None and not isinstance(item, (PlaceItem, TransitionItem)):
                item = item.parentItem()
            if isinstance(item, kind):
                return item
        return None

    @staticmethod
    def _owner_of(item: QGraphicsItem | None):
        """Walk up to the top-level item and return the model object it draws."""
        while item is not None and not isinstance(item, (PlaceItem, TransitionItem, ArcItem)):
            item = item.parentItem()
        if isinstance(item, PlaceItem):
            return item.place
        if isinstance(item, TransitionItem):
            return item.transition
        if isinstance(item, ArcItem):
            return item.arc
        return None

    # -- creation ------------------------------------------------------------
    @property
    def plain(self) -> bool:
        return getattr(self.net, "plain", False)

    def _hold_views_still(self) -> None:
        """Adding a node must not shift the drawing on screen: a net smaller
        than the window is otherwise re-centred as it grows, moving the new
        node away from the mouse that just put it there."""
        for view in self.views():
            if isinstance(view, NetView):
                view.hold_still()

    def _create_place(self, position: QPointF) -> None:
        self.about_to_change.emit()
        self._hold_views_still()
        place = self._new_place(on_grid(position) if self.snap_to_grid else position)
        self.rebuild()
        self.model_changed.emit()
        self.created.emit(place)

    def _create_transition(self, position: QPointF, silent: bool = False) -> None:
        self.about_to_change.emit()
        self._hold_views_still()
        transition = self._new_transition(on_grid(position) if self.snap_to_grid else position, silent)
        self.rebuild()
        self.model_changed.emit()
        self.created.emit(transition)

    def _new_place(self, position: QPointF) -> Place:
        """Add a place centred on ``position`` to the model (no redraw)."""
        assert self.page is not None
        prefix = "p" if self.plain else "P"
        place = Place(name=self._unique_name(prefix, [p.name for p in self.net.all_places()]),
                      colour_set_name="UNIT")
        place.graphics.x, place.graphics.y = to_model(position)
        if self.plain:
            place.graphics.width = place.graphics.height = PLAIN_PLACE
        self.page.places.append(place)
        return place

    def _new_transition(self, position: QPointF, silent: bool = False) -> Transition:
        """Add a transition centred on ``position`` to the model (no redraw).
        A silent (τ) one has no name: it is drawn as a black bar."""
        assert self.page is not None
        prefix = "t" if self.plain else "T"
        transition = Transition(
            name="" if silent else self._unique_name(prefix, [t.name for t in self.net.all_transitions()]),
            silent=silent,
        )
        transition.graphics.x, transition.graphics.y = to_model(position)
        self.page.transitions.append(transition)
        return transition

    # -- drawing arcs: drag from one node to another (or click both) -----------
    def _node_under(self, position: QPointF):
        """The place or transition under ``position`` (arcs and labels ignored)."""
        return self._node_at(position, (PlaceItem, TransitionItem))

    def _arc_press(self, position: QPointF) -> None:
        node = self._node_under(position)
        pending = self._connecting
        if pending is not None and pending.get("clicked"):
            # Second click of the click-click way: finish (or cancel) here.
            self._finish_connection(node)
            return
        if node is None:
            self._cancel_connection()
            self.message.emit("Press on a place or transition and drag to the node to connect")
            return
        self._begin_connection(node, position)

    def _begin_connection(self, node, position: QPointF) -> None:
        """Start drawing an arc from ``node``: a dashed line follows the mouse."""
        line = QGraphicsLineItem()
        pen = QPen(theme.palette().accent, 1.6, Qt.DashLine)
        pen.setCosmetic(True)
        line.setPen(pen)
        line.setOpacity(0.55)                  # a hint, not a wall over the drawing
        line.setZValue(40)
        self.addItem(line)
        line.setLine(node.pos().x(), node.pos().y(), position.x(), position.y())
        self._connecting = {"source": node, "line": line, "press": position,
                            "clicked": False, "target": None, "spot": None}
        node.set_halo(True)

    def _arc_move(self, position: QPointF) -> None:
        pending = self._connecting
        if pending is None:
            return
        source = pending["source"]
        target = self._node_under(position)
        if pending["target"] is not None and pending["target"] is not target \
                and pending["target"] is not source:
            pending["target"].set_halo(False)
        valid = target is not None and type(target) is not type(source)
        if valid:
            target.set_halo(True)
        pending["target"] = target if valid else None
        # Over empty canvas, a drag (not the click-click way) shows the node
        # that letting go will add, and the line runs to it.
        spot = None
        if target is None and not pending["clicked"]:
            spot = self._drop_spot(source, position)
        if spot is not None and pending["spot"] is None:
            other = "transition" if isinstance(source, PlaceItem) else "place"
            self.message.emit(f"Let go to add a {other} here, joined by an arc "
                              f"(Esc cancels)")
        pending["spot"] = spot
        self._show_ghost(source, spot)
        end = position if spot is None else spot
        pending["line"].setLine(source.pos().x(), source.pos().y(), end.x(), end.y())
        pen = pending["line"].pen()
        pen.setColor(theme.palette().danger if target is not None and not valid
                     and target is not source else theme.palette().accent)
        pending["line"].setPen(pen)

    def _arc_release(self, position: QPointF) -> None:
        pending = self._connecting
        if pending is None or pending.get("clicked"):
            return
        source = pending["source"]
        node = self._node_under(position)
        moved = (position - pending["press"]).manhattanLength()
        if node is source or (node is None and moved < 6):
            # A click, not a drag: wait for a click on the second node.
            pending["clicked"] = True
            self._show_ghost(source, None)
            self.message.emit("Now click the place or transition to connect to "
                              "(Esc cancels)")
            return
        spot = self._drop_spot(source, position) if node is None else None
        if spot is not None:
            self._cancel_connection()
            self._connect_to_new_node(source, spot)
            return
        self._finish_connection(node)

    # -- dragging an arc to empty canvas adds the node at its end ---------------
    #: Least space (scene units) between the source and a node added beside it.
    NEW_NODE_GAP = 16.0
    #: How far (scene units) a new node moves to line up with another node.
    NEW_NODE_ALIGN = 12.0

    def _new_node_size(self, kind: type) -> tuple[float, float]:
        """The size a new place or transition gets (see :meth:`_new_place`)."""
        if self.plain:
            return (PLAIN_PLACE, PLAIN_PLACE) if kind is PlaceItem \
                else (PLAIN_TRANSITION, PLAIN_TRANSITION)
        return (76.0, 48.0) if kind is PlaceItem else (82.0, 48.0)

    def _drop_spot(self, source, position: QPointF) -> QPointF | None:
        """Where a node added by letting go at ``position`` would be centred:
        on the grid, or else lined up with a nearby node when it is almost
        level with it.  ``None`` while it would still overlap ``source``
        (too close to tell a drag from a fumble)."""
        if self.snap_to_grid:
            centre = on_grid(position)
        else:
            nodes = [*self.place_items.values(), *self.transition_items.values()]
            sx, sy = edit.snap_node((position.x(), position.y()),
                                    [self._centre(n) for n in nodes], self.NEW_NODE_ALIGN)
            centre = QPointF(position.x() if sx is None else sx,
                             position.y() if sy is None else sy)
        kind = TransitionItem if isinstance(source, PlaceItem) else PlaceItem
        width, height = self._new_node_size(kind)
        new = QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)
        gap = self.NEW_NODE_GAP
        if new.adjusted(-gap, -gap, gap, gap).intersects(QRectF(*_rect(source))):
            return None
        return centre

    def _show_ghost(self, source, spot: QPointF | None) -> None:
        if spot is None:
            if self._ghost is not None and self._ghost.scene() is self:
                self._ghost.hide()
            return
        if self._ghost is None or self._ghost.scene() is not self:
            self._ghost = _GhostNode()
            self.addItem(self._ghost)
        kind = TransitionItem if isinstance(source, PlaceItem) else PlaceItem
        width, height = self._new_node_size(kind)
        self._ghost.show_as(kind, QRectF(-width / 2, -height / 2, width, height), spot,
                            2.0 if self.plain else theme.RADIUS)

    def _connect_to_new_node(self, source, spot: QPointF) -> Arc:
        """Add the other kind of node at ``spot`` and an arc to it from
        ``source`` -- one edit, so one undo takes both away."""
        self.about_to_change.emit()
        self._hold_views_still()
        if isinstance(source, PlaceItem):
            new = self._new_transition(spot)
            arc = self._add_arc(source.place, new, "PtoT")
        else:
            new = self._new_place(spot)
            arc = self._add_arc(new, source.transition, "TtoP")
        self.rebuild()
        self.model_changed.emit()
        what = "transition" if isinstance(new, Transition) else "place"
        self.message.emit(f"Added {what} {new.name}, joined by an arc — type its name, "
                          f"or drag on from it")
        self.created.emit(new)
        return arc

    def _finish_connection(self, target) -> None:
        pending = self._connecting
        source = pending["source"] if pending else None
        self._cancel_connection()
        if source is None or target is None or target is source:
            return
        if type(target) is type(source):
            kind = "places" if isinstance(source, PlaceItem) else "transitions"
            self.message.emit(f"Can't connect two {kind}: an arc always joins a place and "
                              "a transition")
            return
        if isinstance(source, PlaceItem):
            self.connect(source.place, target.transition, "PtoT")
        else:
            self.connect(target.place, source.transition, "TtoP")

    def _cancel_connection(self) -> None:
        pending, self._connecting = self._connecting, None
        if pending is None:
            return
        line = pending["line"]
        if line.scene() is self:
            self.removeItem(line)
        for node in (pending["source"], pending.get("target")):
            if node is not None and node.scene() is self:
                node.set_halo(False)
        if self._ghost is not None and self._ghost.scene() is self:
            self._ghost.hide()

    def connect(self, place: Place, transition: Transition, orientation: str) -> None:
        """Add an arc (in a plain net, a second arc the same way adds weight)."""
        assert self.page is not None
        existing = next((a for a in self.page.arcs if a.place_id == place.id
                         and a.transition_id == transition.id
                         and a.orientation == orientation), None)
        self.about_to_change.emit()
        if self.plain and existing is not None:
            from ..model.plain import tokens_text, weight_of
            existing.expression_text = tokens_text(weight_of(existing) + 1)
            self.message.emit(f"That arc already exists: its weight is now "
                              f"{weight_of(existing)}")
        else:
            self._add_arc(place, transition, orientation)
            what = (f"{place.name} → {transition.name}" if orientation == "PtoT"
                    else f"{transition.name} → {place.name}")
            self.message.emit(f"Arc {what} added — keep dragging to add more")
        self.rebuild()
        self.model_changed.emit()

    def _add_arc(self, place: Place, transition: Transition, orientation: str) -> Arc:
        """Add an arc to the model (no redraw), with the usual inscription."""
        assert self.page is not None
        default = "" if self.plain else ("1`()" if place.colour_set_name == "UNIT" else "")
        arc = Arc(place_id=place.id, transition_id=transition.id,
                  orientation=orientation, expression_text=default)
        self.page.arcs.append(arc)
        return arc

    # -- the "drag to connect" arrow ------------------------------------------------
    #: How near (screen pixels) the mouse must come to a node for its arrow.
    HANDLE_REACH = 26.0
    #: Gap between a node's outline and its arrow (screen pixels).
    HANDLE_GAP = 16.0

    def _view_scale(self) -> float:
        views = self.views()
        return max(views[0].transform().m11(), 1e-6) if views else 1.0

    def _handle_hit(self, position: QPointF) -> bool:
        handle = self._handle
        if handle is None or not handle.isVisible() or handle.node is None:
            return False
        gap = position - handle.pos()
        return (gap.x() ** 2 + gap.y() ** 2) ** 0.5 <= 14.0 / self._view_scale()

    def hide_connect_handle(self) -> None:
        if self._handle is not None and self._handle.scene() is self:
            self._handle.hide()
            self._handle.node = None

    def _update_connect_handle(self, position: QPointF) -> None:
        """Show the arrow beside the node nearest the mouse (Select tool, edit
        mode), on the side the mouse is on -- only once the mouse is outside the
        node: over the node itself it would just be in the way of selecting,
        moving or reading it."""
        if not (self.editable and self.tool == "select") or self.page is None \
                or self._arc_drag is not None:
            self.hide_connect_handle()
            return
        scale = self._view_scale()
        nodes = [*self.place_items.values(), *self.transition_items.values()]
        if any(item.contains(item.mapFromScene(position)) for item in nodes):
            # Over a node the arrow is never shown, whatever the zoom: zoomed
            # out, the arrow's hit zone (14 screen px) can reach a neighbour,
            # and the node must win there.
            self.hide_connect_handle()
            return
        if self._handle_hit(position):
            self._handle.set_hot(True)
            return
        import math
        reach = self.HANDLE_REACH / scale
        # The nearest, not the first: zoomed out, the reach can span several nodes.
        near = [item for item in nodes
                if item.sceneBoundingRect().adjusted(-reach, -reach, reach, reach)
                .contains(position)]
        node = min(near, key=lambda item: math.hypot(*(position - item.pos()).toTuple()),
                   default=None)
        if node is None:
            self.hide_connect_handle()
            return
        direction = position - node.pos()
        length = math.hypot(direction.x(), direction.y())
        ux, uy = (1.0, 0.0) if length < 1e-6 else (direction.x() / length,
                                                     direction.y() / length)
        rect = node.rect()
        a, b = rect.width() / 2, rect.height() / 2
        if isinstance(node, PlaceItem):                   # distance to the outline
            edge = 1.0 / math.sqrt((ux / a) ** 2 + (uy / b) ** 2)
        else:
            edge = min(a / abs(ux) if abs(ux) > 1e-9 else math.inf,
                       b / abs(uy) if abs(uy) > 1e-9 else math.inf)
        if length <= edge or any(other.contains(other.mapFromScene(position))
                                 for other in [*self.place_items.values(),
                                               *self.transition_items.values()]):
            self.hide_connect_handle()                    # on a node: no arrow
            return
        if any(self._owner_of(item) is not None and item.parentItem() is not None
               for item in self.items(position)):
            self.hide_connect_handle()                    # on a label: it moves itself
            return
        if self._handle is None or self._handle.scene() is not self:
            self._handle = _ConnectHandle()
            self.addItem(self._handle)
        handle = self._handle
        handle.set_node(node)
        handle.setPos(node.pos() + QPointF(ux, uy) * (edge + self.HANDLE_GAP / scale))
        handle.setRotation(math.degrees(math.atan2(uy, ux)))
        handle.set_hot(False)
        handle.show()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self._connecting is not None:
            self._cancel_connection()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        """Double-click a place or transition to rename it (edit mode)."""
        if self.editable and self.page is not None and event.button() == Qt.LeftButton:
            node = self._node_under(event.scenePos())
            if node is not None:
                self.rename_requested.emit(node.place if isinstance(node, PlaceItem)
                                           else node.transition)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    @staticmethod
    def _unique_name(prefix: str, existing: list[str]) -> str:
        index = 1
        while f"{prefix}{index}" in existing:
            index += 1
        return f"{prefix}{index}"

    # -- deletion ------------------------------------------------------------
    def delete_selected(self) -> None:
        """Remove selected elements, plus any arcs left dangling."""
        if self.page is None:
            return
        if self.selectedItems():
            self.about_to_change.emit()
        removed = False
        for item in list(self.selectedItems()):
            owner = self._owner_of(item)
            if isinstance(owner, Place):
                self.page.places.remove(owner)
                self.page.arcs = [a for a in self.page.arcs if a.place_id != owner.id]
                removed = True
            elif isinstance(owner, Transition):
                self.page.transitions.remove(owner)
                self.page.arcs = [a for a in self.page.arcs if a.transition_id != owner.id]
                removed = True
            elif isinstance(owner, Arc) and owner in self.page.arcs:
                self.page.arcs.remove(owner)
                removed = True
        if removed:
            self.rebuild()
            self.model_changed.emit()

    def set_editable(self, editable: bool) -> None:
        """Edit mode on or off (arcs and labels can only be dragged when on)."""
        self.editable = editable
        if not editable:
            self.hide_connect_handle()
        for item in self.arc_items.values():
            item.update()

    # -- background ----------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        """A faint dot grid, purely as a visual aid for alignment.

        Drawn with a cosmetic pen so the dots stay one pixel across at every
        zoom level -- a scaled grid quickly turns into either invisible specks
        or distracting blobs.
        """
        painter.fillRect(rect, theme.palette().canvas)

        pen = QPen(theme.palette().grid, 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)

        left = int(rect.left()) - (int(rect.left()) % GRID_STEP)
        top = int(rect.top()) - (int(rect.top()) % GRID_STEP)
        points = []
        y = top
        while y < rect.bottom():
            x = left
            while x < rect.right():
                points.append(QPointF(x, y))
                x += GRID_STEP
            y += GRID_STEP
        painter.drawPoints(points)


class _ClickAway(QObject):
    """While a name box is open: a mouse press anywhere outside it, or the box
    losing the focus, finishes the edit -- as if Return had been pressed.

    The click itself is not swallowed, so it still selects, drags or presses
    whatever it landed on.  Switching to another application or opening the
    box's own context menu does not count.
    """

    def __init__(self, editor, commit: Callable[[], None]) -> None:
        super().__init__(editor)
        self.editor = editor
        self.commit = commit

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            # A press reaches the window first, then the widget under the
            # mouse: only a widget outside the box counts.
            if isinstance(watched, QWidget) and not self._inside(watched):
                self.commit()
        elif kind == QEvent.FocusOut and watched is self.editor:
            if event.reason() not in (Qt.PopupFocusReason, Qt.ActiveWindowFocusReason):
                self.commit()
        return False

    def _inside(self, widget: QWidget) -> bool:
        """The box itself, or something it owns -- such as its right-click
        menu, which is a window of its own (so ``isAncestorOf`` says no)."""
        while widget is not None:
            if widget is self.editor:
                return True
            widget = widget.parentWidget()
        return False


class NetView(CanvasPanning, QGraphicsView):
    """An endless canvas with zooming (floating − % + Fit bar, ⌘-scroll, pinch,
    all about the pointer) and panning (scroll, or drag with the middle button
    or with Space held; see :mod:`.panning`)."""

    zoom_changed = Signal(float)
    drawing_name = "Your net"
    MIN_ZOOM, MAX_ZOOM = 0.1, 5.0
    MIN_SCALE, MAX_SCALE = MIN_ZOOM, MAX_ZOOM      # older names
    STEP = 1.25
    #: Zooming to fit will not magnify beyond this, so a two-node model does
    #: not fill the window with enormous circles.
    FIT_MAX_SCALE = 1.4

    def __init__(self, scene: NetScene, zoom_controls: bool = True) -> None:
        super().__init__(scene)
        self.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        # Repaint the whole viewport on change: with antialiased curves and
        # text, partial updates leave smearing artefacts at the edges.
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        # Zoom towards the pointer rather than the view centre.
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)
        #: While True, resizing the view re-fits the page; any manual zoom
        #: turns it off so the user's zoom level is respected.
        self.auto_fit = True
        from .studio.graph_view import ZoomControls
        self.zoom_controls = ZoomControls(self) if zoom_controls else None
        self._place_controls()
        self.viewport().setMouseTracking(True)      # the rubber-band arc follows the mouse
        #: The inline name editor while one is open, and where it belongs:
        #: a function giving the scene point it is centred on and its least
        #: width, asked again whenever the view moves (see :meth:`edit_text`).
        self.name_editor = None
        self._editor_anchor = None
        self.zoom_changed.connect(lambda _zoom: self._place_editor())
        self.init_canvas()

    # -- editing a name in place ------------------------------------------------
    def edit_text(self, centre, text: str, done, min_width: float = 60.0) -> None:
        """Open a small text field over the canvas with ``text`` selected.

        ``centre`` is the scene point to centre it on, or a function that
        returns ``(centre, min_width)`` -- or None once there is nothing to
        name any more.  A function lets the box follow a node that is moved
        or redrawn while it is open.  Either way the box also follows the
        view: it stays over its spot when the canvas scrolls, zooms or is
        resized (the view re-fits itself when the window changes, which used
        to leave the box where the node *was*; issue #35).

        The box is at least ``min_width`` scene units wide (the node's
        width), growing with the text.  Return, a click anywhere else, or
        moving the focus away calls ``done(new_text)``; Esc closes it
        without a change.
        """
        from PySide6.QtWidgets import QLineEdit
        self.close_editor()
        if callable(centre):
            anchor = centre
        else:
            point, width = QPointF(centre), float(min_width)
            anchor = lambda: (point, width)
        editor = QLineEdit(text, self.viewport())
        editor.setObjectName("canvasNameEditor")
        editor.setAlignment(Qt.AlignCenter)
        accent = theme.palette().accent.name()
        editor.setStyleSheet(f"QLineEdit#canvasNameEditor {{ padding: 1px 3px; border-radius: 4px;"
                             f" border: 1.5px solid {accent}; }}")
        finished = {"done": False}

        def commit() -> None:
            if finished["done"]:
                return
            finished["done"] = True
            value = editor.text()
            self.close_editor()
            done(value)

        def cancel() -> None:
            finished["done"] = True
            self.close_editor()

        self.name_editor = editor
        self._editor_anchor = anchor
        self._place_editor()
        editor.textChanged.connect(lambda _text: self._place_editor())   # grow with the text
        editor.returnPressed.connect(commit)
        editor.editingFinished.connect(commit)
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence(Qt.Key_Escape), editor, cancel)
        editor.show()
        editor.selectAll()
        editor.setFocus(Qt.MouseFocusReason)
        # Qt 6 reports editingFinished on focus loss only after a change, and
        # a click on the canvas or a toolbar button may not move the focus at
        # all -- so an untouched box (a new node's suggested name) stayed
        # open.  Watch for clicks elsewhere and focus loss ourselves.
        self._click_away = _ClickAway(editor, commit)
        QApplication.instance().installEventFilter(self._click_away)

    def _place_editor(self) -> None:
        """Put the name box over its spot at the current zoom and scroll
        position: the same size as the name on the canvas, at least as wide
        as the node, centred on the name.  Closes it if its node is gone."""
        editor, anchor = self.name_editor, self._editor_anchor
        if editor is None or anchor is None:
            return
        where = anchor()
        if where is None:
            self.close_editor()
            return
        centre, min_width = where
        scale = self._scale
        editor.setFont(theme.ui_font(max(9, min(28, round(12 * scale))), theme.QFont.DemiBold))
        width = max(int(min_width * scale), editor.fontMetrics().horizontalAdvance(editor.text()) + 18)
        height = editor.sizeHint().height()
        if (width, height) != (editor.width(), editor.height()):
            editor.resize(width, height)
        point = self.mapFromScene(centre)
        editor.move(point.x() - width // 2, point.y() - height // 2)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self._place_editor()

    def close_editor(self) -> None:
        editor, self.name_editor = self.name_editor, None
        self._editor_anchor = None
        watcher, self._click_away = getattr(self, "_click_away", None), None
        if watcher is not None:
            QApplication.instance().removeEventFilter(watcher)
        if editor is not None:
            editor.blockSignals(True)          # hiding must not count as "finished"
            editor.hide()
            editor.deleteLater()
            self.setFocus()

    # -- zoom -----------------------------------------------------------------
    @property
    def _scale(self) -> float:
        return self.transform().m11()

    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float, around_centre: bool = False, at=None) -> None:
        """Zoom by ``factor`` about the pointer (or ``at``, a viewport point),
        or about the middle of the view."""
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

    def zoom_to_fit(self) -> None:
        """Frame the whole page, within sensible magnification limits."""
        self.auto_fit = True
        items_rect = self.scene().itemsBoundingRect()
        if items_rect.isEmpty():
            return
        self.fitInView(items_rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        scale = self.transform().m11()
        if scale > self.FIT_MAX_SCALE:
            # fitInView has already applied the transform, so scale back down.
            self.scale(self.FIT_MAX_SCALE / scale, self.FIT_MAX_SCALE / scale)
        self.zoom_changed.emit(self.transform().m11())

    fit = zoom_to_fit

    def reset_zoom(self) -> None:
        self.auto_fit = False
        self.resetTransform()
        self.zoom_changed.emit(1.0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """⌘/Ctrl + scroll zooms; plain scrolling pans, as in every Mac app."""
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.zoom_by(1.0015 ** event.angleDelta().y(), at=event.position())
            event.accept()
            return
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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if event.modifiers() == Qt.NoModifier and key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
        elif event.modifiers() == Qt.NoModifier and key == Qt.Key_Minus:
            self.zoom_out()
        elif event.modifiers() == Qt.NoModifier and key == Qt.Key_0:
            self.zoom_to_fit()
        else:
            super().keyPressEvent(event)

    # -- floating zoom bar --------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_controls()
        if self.auto_fit:
            self.zoom_to_fit()
        self._place_editor()

    def _place_controls(self) -> None:
        if self.zoom_controls is None:
            return
        self.zoom_controls.adjustSize()
        size = self.zoom_controls.sizeHint()
        area = self.viewport().geometry()
        self.zoom_controls.setVisible(area.width() >= size.width() + 60 and area.height() >= 90)
        self.zoom_controls.move(area.right() - size.width() - 9, area.bottom() - size.height() - 9)
        self.zoom_controls.raise_()

    # -- panning ----------------------------------------------------------------
    def leaveEvent(self, event) -> None:  # noqa: N802
        scene = self.scene()
        if isinstance(scene, NetScene):
            scene.hide_connect_handle()
        super().leaveEvent(event)

