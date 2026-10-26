"""The *Report* of an analysis: every box's result, in run order, on one page.

A workflow opens on it.  Each box is a section: the group it is in and its
name, the one-line summary the canvas shows under the box, its status, and
its result drawn by the same viewer as the side panel's *Result* tab (a
table scrolls sideways inside its section when it is wider; a figure keeps
its proportions; a score is a key-figure tile besides), then its settings
as chips and *Open ›*, *How* and *Code*, which open the box in the side
panel on the canvas.  A slim *Steps* outline on the left lists the sections,
grouped as the canvas groups the boxes, with each box's key figure; a click
scrolls to the section.  On a narrow window the outline folds into a
*Steps* menu.

Nothing is computed here: a section is the box's result, as on the canvas,
and while a run goes each section is redrawn as its box reports.  A box
that has not run says so in place, a failed one shows its error.  The
process map is the result of a *Directly-follows graph* box; an analysis
with a log and no such box is offered one (added, fed by the first log,
and run), so the map too is something you can click, read and re-run.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget

from ...flow.figures import tile_parts
from ...flow.runner import BLOCKED, DONE, FAILED, RUNNING, Result
from ...flow.sweep import is_sweep
from ...flow.types import DFG, OCDFG, OCEL, EventLog, Figure, PetriNet, Table
from ...flow.workflow import Group, Node, Workflow, WorkflowError
from ..studio import style
from ..studio.widgets import Card, Chip, ColourDot, ElidedLabel, StatTile, button, flow, hbox, label, scroll
from .viewers import status_text

#: Below this width (the page's, without its margins) the outline folds into the Steps menu.
NARROW = 860
#: How many setting chips a section shows at most.
CHIP_LIMIT = 8


def report_order(workflow: Workflow) -> list[tuple[Group | None, list[Node]]]:
    """The boxes in run order, a group's members together under their group:
    ``[(group or None, nodes), ...]``.

    The units are the boxes on their own and the groups: a unit comes once
    the units feeding it have, and among those ready at the same time, as a
    page is read, top to bottom and then left to right on the canvas (a
    group by its group's position).  Inside a group the members keep their
    run order.  Should the
    groups make a loop of their own (one feeds another that feeds it back
    through a third box), the plain run order is used instead."""
    order = workflow.order()
    units: dict[str, tuple[Group | None, list[Node]]] = {}
    unit_of: dict[str, str] = {}
    for node in order:
        group = workflow.group_of(node.id)
        unit = group.id if group is not None else node.id
        unit_of[node.id] = unit
        units.setdefault(unit, (group, []))[1].append(node)
    position = {unit: tuple(reversed(group.position if group is not None else nodes[0].position))
                for unit, (group, nodes) in units.items()}
    indegree = {unit: 0 for unit in units}
    successors: dict[str, set[str]] = {unit: set() for unit in units}
    for edge in workflow.edges:
        source, target = unit_of.get(edge.source), unit_of.get(edge.target)
        if source is None or target is None or source == target or target in successors[source]:
            continue
        successors[source].add(target)
        indegree[target] += 1
    ready = sorted((unit for unit in units if indegree[unit] == 0), key=position.__getitem__)
    sequence: list[str] = []
    while ready:
        unit = ready.pop(0)
        sequence.append(unit)
        for successor in sorted(successors[unit]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
        ready.sort(key=position.__getitem__)
    if len(sequence) != len(units):
        sequence, seen = [], set()
        for node in order:
            if unit_of[node.id] not in seen:
                seen.add(unit_of[node.id])
                sequence.append(unit_of[node.id])
    out: list[tuple[Group | None, list[Node]]] = []
    for unit in sequence:
        group, nodes = units[unit]
        if group is None and out and out[-1][0] is None:
            out[-1][1].extend(nodes)
        else:
            out.append((group, list(nodes)))
    return out


def key_figure(value) -> tuple[str, str] | None:
    """The key-figure tile of a result that is not a drawing or a table of its
    own: a score's first metric, a replay's fitness; None for the rest."""
    if isinstance(value, (Table, Figure, EventLog, OCEL, PetriNet, DFG, OCDFG)):
        return None
    return tile_parts(value)


def _status_colour(status: str) -> str:
    t = style.tokens()
    return {DONE: style.STATUS["good"], FAILED: style.STATUS["critical"], RUNNING: t.accent,
            BLOCKED: style.STATUS["warning"]}.get(status, t.text_muted)


def _chip_colours(kind: str) -> tuple:
    """The status chip's fill and ink for a :func:`status_text` kind."""
    t = style.tokens()
    if kind == "good":
        return style.qc(style.STATUS["good"], 0.13), style.STATUS["good"]
    if kind == "critical":
        return style.qc(style.STATUS["critical"], 0.12), style.STATUS["critical"]
    if kind == "warning":
        return style.qc(style.STATUS["warning"], 0.25), t.text
    if kind == "accent":
        return t.accent_soft, t.accent
    return style.qc(t.text, 0.08), t.text_secondary


def _clear(layout, keep=()) -> None:
    """Empty ``layout``; widgets in ``keep`` are taken out and hidden, not deleted."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            if widget not in keep:
                widget.setParent(None)
                widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout(), keep)


def setting_chips(spec, node: Node) -> list[str]:
    """One short text per setting: ``noise 0.2``, ``file boarding.xes``, ``noise: swept over 5 values``."""
    out = []
    for setting in spec.settings:
        value = node.settings.get(setting.name, setting.default)
        if value is None or value == "":
            continue
        if is_sweep(value):
            out.append(f"{setting.name}: swept over {len(value.values)} values")
        elif setting.kind == "path":
            out.append(f"{setting.name} {Path(str(value)).name}")
        elif isinstance(value, bool):
            out.append(f"{setting.name} {'yes' if value else 'no'}")
        else:
            text = str(value)
            out.append(f"{setting.name} {text if len(text) <= 40 else text[:39] + '…'}")
    return out


class OutlineRow(QFrame):
    """One step in the outline: a status dot, the box's name, its key figure.  Clickable."""

    clicked = Signal(str)

    def __init__(self, node_id: str, title: str) -> None:
        super().__init__()
        self.node_id = node_id
        self.setObjectName("outlineRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("current", False)
        self.dot = ColourDot(style.tokens().text_muted, 8)
        self.title = ElidedLabel(title)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.figure = ElidedLabel("", "muted")
        self.figure.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.figure.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)    # its width; the title gives way
        self.figure.setMinimumWidth(0)
        self.figure.setMaximumWidth(90)
        self.setLayout(hbox(self.dot, self.title, self.figure, margins=(10, 5, 10, 5)))
        self.setToolTip(f"{title}: scroll to its section")

    def set_state(self, status: str, figure: str) -> None:
        self.dot.colour = _status_colour(status)
        self.dot.update()
        self.figure.setText(figure)

    def set_current(self, current: bool) -> None:
        if self.property("current") == current:
            return
        self.setProperty("current", current)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.node_id)
            event.accept()
            return
        super().mousePressEvent(event)


class Section(QFrame):
    """One box on the Report: its header, result, settings chips and links."""

    def __init__(self, report: "ReportWidget", node_id: str) -> None:
        super().__init__()
        self.report = report
        self.node_id = node_id
        #: The result the section shows (None before a run), and its body.
        self.result: Result | None = None
        self.body_widget: QWidget | None = None
        self.chip_widgets: list[Chip] = []
        self.setObjectName("card")
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(10)
        self.eyebrow = label("", "sectionLabel")
        self.title = label("", "reportTitle")                 # as wide as its text; the summary gives way
        self.summary = ElidedLabel("", "muted")
        self.summary.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.chip = Chip("")
        column.addLayout(hbox(self.eyebrow, self.title, self.summary, self.chip, spacing=12))
        self.body_host = QWidget()
        self.body_host.setObjectName("plain")
        self.body_layout = QVBoxLayout(self.body_host)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        column.addWidget(self.body_host)
        self.chips_host = QWidget()
        self.chips_host.setObjectName("plain")
        self.chips_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.chips_layout = QHBoxLayout(self.chips_host)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.open_button = button("Open ›", lambda: report.page.open_box(node_id, 0), kind="ghost",
                                  tooltip="This box on the canvas, with its Result in the side panel")
        self.how_button = button("How", lambda: report.page.open_box(node_id, 1), kind="ghost",
                                 tooltip="How it got there: notes, intermediate values, the derivation")
        self.code_button = button("Code", lambda: report.page.open_box(node_id, 2), kind="ghost",
                                  tooltip="The box's code and the algorithm it calls")
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(4)
        footer.addWidget(self.chips_host, 1, Qt.AlignTop)
        for link in (self.open_button, self.how_button, self.code_button):
            footer.addWidget(link, 0, Qt.AlignTop)
        column.addLayout(footer)

    # -- what it shows ----------------------------------------------------------------------
    def update(self, result: Result | None) -> None:
        """Header, chips and outline row now; the body when the page gets to it."""
        self.result = result
        page = self.report.page
        node = page.workflow.nodes.get(self.node_id)
        if node is None:
            return
        spec = page.workflow.spec(node)
        self.title.setText(page.workflow.title(node))
        self.eyebrow.setText((("Yours · " if spec.custom else "") + spec.group).upper())
        text, kind = status_text(result)
        self.chip.setText(text)
        self.chip.set_colours(*_chip_colours(kind))
        done = result is not None and result.status == DONE
        self.summary.setText(page.scene.statuses.get(node.id, ("", "", ""))[1] if done else "")
        _clear(self.chips_layout)
        self.chip_widgets = []
        texts = setting_chips(spec, node)
        if texts:
            for chip_text in texts[:CHIP_LIMIT]:
                chip = Chip(chip_text)
                chip.set_state("muted")
                self.chip_widgets.append(chip)
            more = len(texts) - CHIP_LIMIT
            if more > 0:
                chip = Chip(f"+{more} more")
                chip.set_state("muted")
                self.chip_widgets.append(chip)
            self.chips_layout.addWidget(flow(label("Settings", "muted"), *self.chip_widgets, spacing=6), 1)
        row = self.report.rows.get(self.node_id)
        if row is not None:
            row.title.setText(page.workflow.title(node))
            parts = tile_parts(result.value) if done else None
            row.set_state(result.status if result is not None else "idle", parts[0] if parts else "")
        self.report.schedule(self)

    def render_body(self) -> None:
        """The result, by the side panel's Result viewer; a key-figure tile beside it where there is one."""
        _clear(self.body_layout)
        page = self.report.page
        node = page.workflow.nodes.get(self.node_id)
        if node is None:
            return
        result = self.result
        if result is not None and result.status == DONE:
            parts = key_figure(result.value)
            if parts is not None:
                tile = StatTile("Key figure", *parts)
                tile.setMaximumWidth(320)
                self.body_layout.addWidget(tile, 0, Qt.AlignLeft)
        self.body_widget = page.result_view(node, result)
        self.body_layout.addWidget(self.body_widget)


class ReportWidget(QWidget):
    """The Report view of a workflow page; :meth:`refresh` brings it up to date
    with the page's run, :meth:`update_box` redraws one box while a run goes."""

    def __init__(self, page, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        self.sections: list[Section] = []
        self.rows: dict[str, OutlineRow] = {}
        self.headings: list = []
        self._key = None
        self._queue: list[Section] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)
        self._current: str | None = None
        self._narrow = False
        #: True when something changed while another view was showing: refresh when shown.
        self.stale = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.steps_button = button("Steps ▾", self._show_steps, tooltip="Every box; click one to scroll to it")
        self.top = QWidget()
        self.top.setObjectName("plain")
        self.top.setLayout(hbox(self.steps_button, None))
        self.top.setVisible(False)
        outer.addWidget(self.top)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        outer.addLayout(row, 1)
        self.outline_host = QWidget()
        self.outline_host.setObjectName("plain")
        self.outline_layout = QVBoxLayout(self.outline_host)
        self.outline_layout.setContentsMargins(0, 0, 0, 0)
        self.outline_layout.setSpacing(2)
        self.outline = scroll(self.outline_host)
        self.outline.setFixedWidth(240)
        row.addWidget(self.outline)
        self.column_host = QWidget()
        self.column_host.setObjectName("plain")
        self.column = QVBoxLayout(self.column_host)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(16)
        self.scroll = scroll(self.column_host)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._mark_current())
        row.addWidget(self.scroll, 1)
        # No process map box yet: say so, and offer to add it.
        self.no_map_card = Card("Process map", "The process map is the result of a Directly-follows graph "
                                "box (Discover ▸), so it too can be clicked, read and re-run. This "
                                "analysis has none yet.")
        self.add_map_button = button("Add a Directly-follows graph box", self._add_map_box,
                                     tooltip="Adds the box, fed by the first log, and runs it")
        self.no_map_card.add(hbox(self.add_map_button, None))
        self.no_map_card.setVisible(False)

    # -- building ----------------------------------------------------------------------------
    def refresh(self) -> None:
        """Bring the page up to date: rebuilt when boxes came, went or changed,
        else each section follows its box's latest result."""
        workflow = self.page.workflow
        try:
            groups = report_order(workflow)
        except WorkflowError:
            groups = [(None, list(workflow.nodes.values()))]
        key = tuple((group.id if group else None, group.name if group else "",
                     tuple((n.id, workflow.title(n)) for n in nodes)) for group, nodes in groups)
        if key != self._key:
            self._key = key
            self._rebuild(groups)
        else:
            for section in self.sections:
                self._sync(section)
        self._refresh_offer()
        self.stale = False

    def _rebuild(self, groups: list[tuple[Group | None, list[Node]]]) -> None:
        _clear(self.outline_layout)
        _clear(self.column, keep=(self.no_map_card,))
        self.sections, self.rows, self.headings = [], {}, []
        self._current = None
        self.outline_layout.addWidget(label("STEPS", "sectionLabel"))
        if not groups or not any(nodes for _g, nodes in groups):
            self.column.addWidget(label("No boxes yet. Press + Add box, or double-click the canvas: each box's "
                                        "result appears here, in the order the boxes run.", "muted", wrap=True))
        for group, nodes in groups:
            if group is not None:
                heading = label(group.name.upper(), "sectionLabel")
                heading.setContentsMargins(10, 10, 10, 2)
                self.outline_layout.addWidget(heading)
                title = label(group.name, "reportGroup")
                count = label(f"{len(nodes)} box{'es' if len(nodes) != 1 else ''}", "muted")
                self.headings.append(title)
                self.column.addLayout(hbox(title, count, None, margins=(2, 6, 0, 0)))
            for node in nodes:
                row = OutlineRow(node.id, self.page.workflow.title(node))
                row.clicked.connect(self.scroll_to)
                self.outline_layout.addWidget(row)
                self.rows[node.id] = row
                section = Section(self, node.id)
                self.sections.append(section)
                self.column.addWidget(section)
        self.outline_layout.addStretch(1)
        self.column.addWidget(self.no_map_card)
        self.column.addStretch(1)
        for section in self.sections:
            self._sync(section, force=True)
        QTimer.singleShot(0, self._mark_current)

    def _sync(self, section: Section, force: bool = False) -> None:
        result = self.page.run.result(section.node_id) if self.page.run is not None else None
        if force or _signature(result) != _signature(section.result):
            section.update(result)

    def update_box(self, result: Result) -> None:
        """A box reported while the run goes: its section follows."""
        if result.variant != 0:
            return
        section = self.section(result.node)
        if section is None:
            self.stale = True                 # a box the page has not listed yet
            return
        section.update(result)

    def section(self, node_id: str) -> Section | None:
        return next((s for s in self.sections if s.node_id == node_id), None)

    # -- the bodies, one per turn of the event loop -------------------------------------------
    def schedule(self, section: Section) -> None:
        if section not in self._queue:
            self._queue.append(section)
        self._timer.start()

    def _tick(self) -> None:
        if self._queue:
            section = self._queue.pop(0)
            if section in self.sections:
                section.render_body()
        if self._queue:
            self._timer.start()

    def flush(self) -> None:
        """Draw every pending body now (the tests, a screenshot)."""
        while self._queue:
            self._tick()

    # -- the outline ---------------------------------------------------------------------------
    def scroll_to(self, node_id: str) -> None:
        """Scroll the page to the box's section (as far as the page goes: the last
        sections cannot reach the top) and mark its step."""
        section = self.section(node_id)
        if section is not None:
            self.scroll.verticalScrollBar().setValue(max(0, section.y() - 4))
            self._set_current(node_id)

    def _mark_current(self) -> None:
        """The outline row of the section at the top of the view is marked."""
        top = self.scroll.verticalScrollBar().value() + 60
        current = None
        for section in self.sections:
            if section.y() <= top:
                current = section.node_id
        self._set_current(current)

    def _set_current(self, current: str | None) -> None:
        if current == self._current:
            return
        self._current = current
        for node_id, row in self.rows.items():
            row.set_current(node_id == current)

    def steps_menu(self) -> QMenu:
        """The outline as a menu, for a window too narrow to show it beside the page."""
        menu = QMenu(self)
        try:
            groups = report_order(self.page.workflow)
        except WorkflowError:
            groups = [(None, list(self.page.workflow.nodes.values()))]
        for group, nodes in groups:
            if group is not None:
                menu.addSection(group.name)
            for node in nodes:
                row = self.rows.get(node.id)
                figure = row.figure.text() if row is not None else ""
                action = menu.addAction(f"{self.page.workflow.title(node)}" + (f"   {figure}" if figure else ""))
                action.setData(node.id)
                action.triggered.connect(lambda _checked=False, node_id=node.id: self.scroll_to(node_id))
        return menu

    def _show_steps(self) -> None:
        self.steps_menu().exec(self.steps_button.mapToGlobal(QPoint(0, self.steps_button.height() + 4)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        narrow = event.size().width() < NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self.outline.setVisible(not narrow)
            self.top.setVisible(narrow)

    # -- the process map offer -------------------------------------------------------------------
    def _refresh_offer(self) -> None:
        page = self.page
        has_log = page.run is not None and any(
            r.status == DONE and isinstance(r.value, EventLog) for r in page.run.results.values())
        has_map = any(n.box.endswith(".directly_follows") for n in page.workflow.nodes.values())
        self.no_map_card.setVisible(has_log and not has_map and self._map_box_id() is not None)

    def _map_box_id(self) -> str | None:
        return next((s.id for s in self.page.library.specs.values() if s.id.endswith(".directly_follows")), None)

    def _add_map_box(self) -> None:
        """Add a Directly-follows graph box, fed by the first log, and run it."""
        box_id = self._map_box_id()
        if box_id is None:
            return
        page = self.page
        source = next((n for n in page.workflow.order()
                       if page.run is not None and page.run.result(n) is not None
                       and isinstance(page.run.result(n).value, EventLog)), None)
        page.add_box_fed_by(box_id, source.id if source is not None else None)


def _signature(result: Result | None):
    """What tells one result from another: a run's reuse of an unchanged box is
    a copy of its result, so the key (the same) counts, not the object."""
    if result is None:
        return None
    return result.status, result.key or id(result), result.error


__all__ = ["ReportWidget", "Section", "OutlineRow", "report_order", "setting_chips", "key_figure"]
