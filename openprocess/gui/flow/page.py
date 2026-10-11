"""The Workflows page: the canvas, *+ Add box*, and a side panel on demand.

The page opens with the workflow alone on its canvas.  *+ Add box* in the
header (or a double-click on the canvas) opens the box picker
(:mod:`.picker`).  Click a box and the side panel appears beside the
canvas, on *Result* (the viewer for its output type); its other tabs are
*How* (what it reported while it ran: notes, intermediate values, the
derivation), *Code* (its source) and *Settings* (one control per setting).
Click the canvas, or the panel's ✕, and the panel goes again.  Change a
setting and only the boxes after it run again.  Every box shows a status
dot: waiting, running, done, failed, or waiting for your OK (a custom box).

Boxes run on a worker thread (:mod:`openprocess.gui.studio.workers`); the runner
reports each box's status through a signal, so the canvas updates while
the rest keeps working.  An exception stays in its box.

The page edits a :class:`~openprocess.flow.workflow.Workflow` and saves it as a
``.cpnflow`` file with its record (:mod:`openprocess.flow.record`).
"""

from __future__ import annotations

import textwrap
import re
import json
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QStackedWidget,
    QDialog, QFileDialog, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget,
)

from ...flow import record as records
from ...flow import reproducibility, snapshots
from ...flow.library import Library
from ...flow.runner import (BLOCKED, DONE, FAILED, IDLE, RUNNING, WAITING, Cache, Result, Run, Runner,
                            columns_connected)
from ...flow.types import EventLog, Figure, PetriNet, Scores, Table
from ...flow.history import History as EditHistory
from ...flow.script import script_text
from ...flow.workflow import Edge, Workflow, group_to_python, to_python
from .. import theme
from ..studio import motion, style
from ..studio.widgets import Card, NoticeBar, PageHeader, SegmentedControl, button, hbox, label, scroll, vbox
from ..studio.workers import run_in_background
from .canvas import WorkflowScene, WorkflowView
from .picker import BoxPicker, input_box_for
from .runs import RunsWidget
from .summary import SummaryWidget
from .viewers import SettingsWidget, code_widget, how_widget, pop_out, result_widget, status_text

TABS = ["Result", "How", "Code", "Settings"]
HINT = "Click a box to see what it gives. Drag from the dot on its right to connect it to another."


class _Relay(QObject):
    """Carries a box's status from the worker thread to the page (queued)."""

    status = Signal(object)
    finished = Signal(object)


def brief(value) -> str:
    """One line about a result, for the box's subtitle."""
    if isinstance(value, Scores):
        first = next(((k, v) for k, v in value.metrics.items()), None)
        if first is None:
            return value.model
        key, number = first
        return f"{key} {number:.2f}" if isinstance(number, float) else f"{key} {number}"
    if isinstance(value, Table):
        return f"table {len(value.rows)} × {len(value.columns)}"
    if isinstance(value, Figure):
        return value.caption[:40] or "figure"
    if isinstance(value, PetriNet):
        return f"{len(value.places)} places · {len(value.transitions)} transitions"
    if isinstance(value, EventLog):
        return f"{len(value)} cases · {value.event_count} events"
    summary = getattr(value, "summary", None)
    if callable(summary):
        try:
            return str(summary())[:48]
        except Exception:   # noqa: BLE001
            pass
    if value is None:
        return "done"
    if hasattr(value, "__len__"):
        try:
            return f"{len(value)} {type(value).__name__.lower()}"
        except TypeError:
            pass
    return type(value).__name__


class WorkflowPage(QWidget):
    status = Signal(str)
    saved = Signal()
    #: A run finished (not stopped): the record wants updating (the window autosaves in a folder).
    ran = Signal()
    #: Something changed that should be saved (the window autosaves in a folder).
    edited = Signal()
    #: A result should open as a page of its own (a LogDocument, ModelDocument, CpnDocument).
    open_document = Signal(object)
    #: "Open a copy in Model" of a net: the net, and where it came from ("from Inductive Miner in …").
    edit_requested = Signal(object, str)
    keep_requested = Signal()
    #: The user allowed the folder's custom boxes to run.
    custom_allowed_changed = Signal()
    #: The side panel was resized (the window remembers its width).
    layout_changed = Signal()

    #: The layout every new Workflows page starts with; the window restores a
    #: saved one into it.  ``sizes`` are the splitter's: canvas, side panel.
    LAYOUT: dict = {"sizes": [820, 380]}

    def __init__(self, document, library: Library, folder: str | Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.workflow: Workflow = document.workflow
        #: Every state the workflow was in, for ⌘Z and ⇧⌘Z (:mod:`openprocess.flow.history`);
        #: ``history`` is the Runs tab's snapshots.
        self.edits = EditHistory(self.workflow.to_dict())
        self._restoring = False
        self.library = library
        self.folder = Path(folder) if folder else None
        self.runner = Runner(library, Cache(), folder=self.folder)
        self.run: Run | None = None
        self._running = False
        #: Boxes to run once the current run is over (None: all of them), and
        #: whether anything is queued at all (None alone would be ambiguous).
        self._queued: list[str] | None = None
        self._has_queue = False
        self._stop = threading.Event()
        self._relay = _Relay()
        self._relay.status.connect(self._status_arrived)
        self._relay.finished.connect(self._run_finished)
        self.selected: str | None = None
        self.tab = 0
        self._opening_tab: int | None = None
        self._selecting = False
        self.custom_allowed = False
        self._setting_timer = QTimer(self)
        self._setting_timer.setSingleShot(True)
        self._setting_timer.setInterval(400)
        self._setting_timer.timeout.connect(self._apply_pending_settings)
        self._pending: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(self.workflow.name, self._subtitle())
        #: Reproducibility, as last computed (a run finishing, a save): see refresh_title.
        self._repro = reproducibility.status(self.workflow, getattr(document, "record", None), None, self.folder)
        # Canvas: the boxes and wires.  Summary: every result's figure, and the process map.
        self.summary_switch = SegmentedControl(["Canvas", "Summary", "Runs"], compact=True)
        self.summary_switch.setToolTip("Canvas: the boxes and wires.\nSummary: every result's key "
                                       "figure, and the process map.\nRuns: every finished run, two compared.")
        record = getattr(document, "record", None)
        #: The last runs, with what changed before each (kept in the workflow file).
        self.history = snapshots.History.from_dict(record.history if record is not None else [])
        self.summary_switch.changed.connect(self.show_view)
        self.header.actions.addWidget(self.summary_switch)
        self.keep_button = button("Keep", self.keep_requested.emit, kind="primary",
                                  tooltip="Save this workflow into the folder")
        self.keep_button.setVisible(False)
        self.header.actions.addWidget(self.keep_button)
        # Three buttons: add, run, and a menu for the rest (re-run, record, export, save).
        self.add_button = button("+ Add box", self.add_box_menu, kind="primary",
                                 tooltip="Every box, by group; or double-click the canvas")
        self.header.actions.addWidget(self.add_button)
        self.run_button = button("Run ▶", lambda: self.run_from(None), tooltip="Run every box")
        self.header.actions.addWidget(self.run_button)
        self.more_menu = QMenu(self)
        self.more_menu.addAction("Re-run", self.rerun).setToolTip(
            "Run again, after checking the record for changed inputs")
        self.more_menu.addAction("Record…", self.show_record).setToolTip(
            "The workflow as Python, and the file with its record")
        self.more_menu.addAction("Export experiment…", self.export_experiment).setToolTip(
            "A zip with the workflow, its inputs, your boxes, every result and a README: "
            "supplementary material for a paper")
        self.more_menu.addAction("Export as Python script…", self.export_script).setToolTip(
            "A .py file with the same boxes, settings and connections: python runs it and writes the "
            "results beside it")
        self.more_menu.addAction("Export report…", self.export_report).setToolTip(
            "One HTML page: every box's result, how it got there, the code it ran, the papers it "
            "follows, what it read, and how to cite. For a supervisor, a reviewer, a hand-in")
        self.more_menu.addSeparator()
        self.more_menu.addAction("Save", self.save).setToolTip("Save the workflow and its record")
        self.more_button = button("⋯", self._show_more_menu, tooltip="Re-run, Record, Export experiment, Export as Python script, Export report, Save")
        self.more_button.setObjectName("moreButton")
        self.more_button.setFixedWidth(40)
        self.header.actions.addWidget(self.more_button)
        root.addWidget(self.header)
        self.custom_bar = NoticeBar()
        root.addWidget(self.custom_bar)

        self.scene = WorkflowScene(self.workflow)
        self.view = WorkflowView(self.scene)
        self.scene.selected.connect(self._select)
        self.scene.edited.connect(self._edited)
        self.scene.moved.connect(lambda _id: self._mark_edited("move"))
        self.scene.add_here.connect(self.quick_add)
        self.scene.menu.connect(self._menu)
        self.scene.refused.connect(lambda text: self.status.emit(text))
        self.scene.open_group.connect(self.open_group)
        self.view.files_dropped.connect(self.add_files)

        # The canvas, and a side panel that appears when a box is clicked.
        # Drag the gap to resize the panel; double-click it for the default.
        splitter = _Splitter()
        canvas = Card()
        self.back_button = button("← Back", self.close_group, tooltip="Back to the whole workflow")
        self.group_crumb = label("", "muted")
        self.group_bar = QWidget()
        self.group_bar.setObjectName("plain")
        self.group_bar.setLayout(hbox(self.back_button, self.group_crumb, None))
        self.group_bar.setVisible(False)
        canvas.add(self.group_bar)
        canvas.add(self.view, 1)
        self.legend = label(HINT, "muted", wrap=True)
        canvas.add(self.legend)
        splitter.addWidget(canvas)
        self.panel_host = QWidget()
        self.panel_layout = QVBoxLayout(self.panel_host)
        self.panel_layout.setContentsMargins(0, 0, 0, 0)
        self.panel = scroll(self.panel_host)
        self.panel.setMinimumWidth(280)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setCollapsible(0, False)
        self.splitter = splitter
        self.panel.setVisible(False)
        splitter.setSizes(_sizes(self.LAYOUT.get("sizes")))
        splitter.splitterMoved.connect(lambda *_: self._layout_changed())
        splitter.reset_requested.connect(self.reset_layout)
        wrapper = QWidget()
        wrapper.setLayout(vbox(splitter, margins=(20, 0, 20, 16)))
        self.summary = SummaryWidget(self)
        self.summary.tile_clicked.connect(self._tile_clicked)
        summary_wrapper = QWidget()
        summary_wrapper.setLayout(vbox(scroll(self.summary), margins=(20, 0, 20, 16)))
        self.runs = RunsWidget(self)
        runs_wrapper = QWidget()
        runs_wrapper.setLayout(vbox(scroll(self.runs), margins=(20, 0, 20, 16)))
        self.body = QStackedWidget()
        self.body.addWidget(wrapper)
        self.body.addWidget(summary_wrapper)
        self.body.addWidget(runs_wrapper)
        root.addWidget(self.body, 1)
        self._picker: BoxPicker | None = None

        QShortcut(QKeySequence(Qt.Key_Delete), self.view, self.scene.remove_selected)
        QShortcut(QKeySequence(Qt.Key_Backspace), self.view, self.scene.remove_selected)
        QShortcut(QKeySequence("Ctrl+D"), self.view, lambda: self.scene.duplicate(self.selected) if self.selected else None)
        QShortcut(QKeySequence("Ctrl+G"), self.view, self.group_selected)
        QShortcut(QKeySequence.Undo, self.view, self.undo)
        QShortcut(QKeySequence.Redo, self.view, self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+G"), self.view, lambda: self.scene.ungroup(self.selected)
                  if self.selected in self.workflow.groups else None)
        self._refresh_custom_bar()
        self._render_panel()
        QTimer.singleShot(0, self.view.fit)
        if not self.workflow.nodes:
            self.status.emit("An empty workflow: press + Add box, or double-click the canvas")

    # -- the layout: the side panel shown, hidden, resized ------------------------------
    def show_panel(self, show: bool) -> None:
        """Show or hide the side panel (the canvas takes its width when hidden)."""
        if show == self.panel.isVisible():
            return
        self.panel.setVisible(show)
        if show:
            self.splitter.setSizes(_sizes(WorkflowPage.LAYOUT.get("sizes")))
            QTimer.singleShot(0, lambda: motion.lift(self.panel))     # once it has its width
        self._layout_changed()

    def close_panel(self) -> None:
        """The panel's ✕: nothing selected, the canvas alone."""
        self.select(None)

    def show_view(self, index: int) -> None:
        """Canvas (0), Summary (1) or Runs (2); the latter two are rebuilt when shown."""
        if self.body.currentIndex() != index:
            self.body.setCurrentIndex(index)
            motion.lift(self.body)
        if self.summary_switch.index() != index:
            self.summary_switch.blockSignals(True)
            self.summary_switch.buttons[index].setChecked(True)
            self.summary_switch.blockSignals(False)
        if index == 1:
            self.summary.refresh()
        elif index == 2:
            self.runs.refresh()

    def apply_snapshot(self, snapshot: snapshots.Snapshot) -> None:
        """Put a run's settings back on the boxes that still exist, and run."""
        changed = []
        for node_id in list(self.workflow.nodes):
            values = snapshots.settings_to_apply(snapshot, node_id)
            if not values:
                continue
            before = dict(self.workflow.nodes[node_id].settings)
            try:
                self.workflow.set(node_id, **values)
            except (ValueError, TypeError) as error:
                self.status.emit(str(error))
                continue
            if self.workflow.nodes[node_id].settings != before:
                changed.append(node_id)
        if not changed:
            self.status.emit("Those are the settings already")
            return
        self._mark_edited()
        self._render_panel()
        self.show_view(0)
        self.status.emit(f"Settings of the run at {snapshot.taken.replace('T', ' ')} are back on "
                         f"{len(changed)} box{'es' if len(changed) != 1 else ''}; running")
        self.run_from(changed)

    def _tile_clicked(self, node_id: str) -> None:
        """A Summary tile: that box's Result, on the canvas."""
        self.show_view(0)
        self.select(node_id, 0)

    def reset_layout(self) -> None:
        """The side panel at its default width."""
        self.splitter.setSizes([max(400, self.splitter.width() - 380), 380])
        self._layout_changed()

    def layout_state(self) -> dict:
        sizes = self.splitter.sizes()
        if not self.panel.isVisible():                # keep the width the panel had, for when it is back
            sizes = [sizes[0], _sizes(WorkflowPage.LAYOUT.get("sizes"))[1]]
        return {"sizes": [max(int(s), 1) for s in sizes]}

    def _layout_changed(self) -> None:
        WorkflowPage.LAYOUT = self.layout_state()
        if self.view.auto_fit:
            QTimer.singleShot(0, self.view.fit)
        self.layout_changed.emit()

    # -- header --------------------------------------------------------------------------
    def _subtitle(self) -> str:
        count = len(self.workflow.nodes)
        text = f"{count} box{'es' if count != 1 else ''}"
        if self.run is not None:
            text += " · " + self.run.summary()
        return text

    def refresh_title(self) -> None:
        self.header.set_text(self.workflow.name, self._subtitle())
        status = self._repro
        self.header.set_badge(status.short, status.tone,
                              "\n".join([status.headline] + ["· " + d for d in status.details]
                                        + ["", "Click for the record."]),
                              self.show_record)

    def refresh_reproducibility(self) -> None:
        """Compare the record with the inputs on disk and the latest run (a run
        finished, the analysis was saved)."""
        self._repro = reproducibility.status(self.workflow, getattr(self.document, "record", None),
                                             self.run, self.folder)
        self.refresh_title()

    # -- adding boxes: the picker ---------------------------------------------------------------
    def picker(self) -> BoxPicker:
        """The *+ Add box* popover, built fresh each time (the library may have changed)."""
        if self._picker is not None:
            self._picker.deleteLater()
        self._picker = BoxPicker(self.library, self, self.folder)
        return self._picker

    def add_box_menu(self) -> None:
        """*+ Add box*: the picker under the button; the box lands in free space on the canvas."""
        picker = self.picker()
        picker.chosen.connect(self.add_box_in_view)
        picker.file_chosen.connect(lambda box_id, relative: self.add_box_in_view(box_id, {"file": relative}))
        corner = self.add_button.mapToGlobal(QPoint(0, self.add_button.height() + 4))
        picker.open_at(QPoint(corner.x() + self.add_button.width() - picker.card_size()[0], corner.y()))

    def free_spot(self) -> QPointF:
        """Where a new box lands when nowhere was pointed at: bottom-left of the view, staggered."""
        count = len(self.workflow.nodes) % 6
        rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        return QPointF(rect.left() + 30 + count * 24, rect.bottom() - 150 - count * 12)

    def add_box_in_view(self, box_id: str, settings: dict | None = None):
        """Add ``box_id`` where it can be seen."""
        return self.add_box(box_id, self.free_spot(), settings)

    def quick_add(self, where: QPointF) -> None:
        """A double-click (or *Add box here…*) at ``where`` (scene coordinates): the picker there."""
        picker = self.picker()
        at = QPointF(where.x() - 89, where.y() - 30)
        picker.chosen.connect(lambda box_id: self.add_box(box_id, at))
        picker.file_chosen.connect(lambda box_id, relative: self.add_box(box_id, at, {"file": relative}))
        picker.open_at(self.view.mapToGlobal(self.view.mapFromScene(where)))

    def add_box(self, box_id: str, where: QPointF, settings: dict | None = None):
        """Put a box on the canvas (with ``settings``, e.g. the file it reads) and say so."""
        node = self.scene.add_node(box_id, where, settings)
        spec = self.workflow.spec(node)
        unchosen = [s for s in spec.settings if s.kind == "path" and node.settings.get(s.name) in (None, "")]
        chosen = [s for s in spec.settings if s.kind == "path" and node.settings.get(s.name) not in (None, "")]
        if unchosen:
            # A box that needs a file: open its Settings, where the file is chosen.
            self.select(node.id, 3)
            self.status.emit(f"Added {spec.name}: press Choose… under {unchosen[0].name} in Settings, on the right")
        elif chosen:
            self.select(node.id)
            self.status.emit(f"Added {spec.name} reading {Path(str(node.settings[chosen[0].name])).name}: "
                             "drag from the dot on its right to connect it")
        else:
            self.status.emit(f"Added {spec.name}: drag from a dot on the right of a box to connect it")
        return node

    def add_box_fed_by(self, box_id: str, source_id: str | None):
        """Add a box and connect it to ``source_id``'s output (the first input
        that takes it), as dropping a wire would; then it runs."""
        node = self.add_box_in_view(box_id)
        if source_id is not None and source_id in self.workflow.nodes:
            ok, detail = self.workflow.can_connect(source_id, node.id)
            if ok:
                self.workflow.connect(source_id, node.id)
                self.scene.rebuild()
                self._edited([node.id])
            else:
                self.status.emit(detail)
        return node

    def add_files(self, paths: list[str], where: QPointF | None = None) -> list:
        """Files dropped on the canvas (or chosen for it) become input boxes:
        a log an *Open log*, a PNML file an *Open net*, and so on, at ``where``
        (None: in free space).  A file inside the workflow's folder is kept by
        its relative path, one elsewhere by its full path.  Returns the nodes."""
        from ..studio.workspace import file_kind
        added, refused = [], []
        for index, path in enumerate(paths):
            kind = file_kind(Path(path))
            box_id = input_box_for(self.library, kind) if kind else None
            if kind == "workflow":
                refused.append(f"{Path(path).name} is a workflow: open it from the sidebar")
                continue
            if box_id is None:
                refused.append(f"{Path(path).name}: not a log, net or transition system")
                continue
            value = str(Path(path))
            if self.folder is not None:
                try:
                    value = Path(path).resolve().relative_to(Path(self.folder).resolve()).as_posix()
                except ValueError:
                    pass
            spot = self.free_spot() if where is None else QPointF(where.x() - 89 + index * 24,
                                                                      where.y() - 30 + index * 70)
            added.append(self.add_box(box_id, spot, {"file": value}))
        if refused:
            self.status.emit("; ".join(refused))
        return added
        self.refresh_title()

    def reload_library(self) -> None:
        """The folder's boxes changed: load them again and refresh the list."""
        if self.folder is not None:
            self.library.load_folder(self.folder / "boxes")
        self._refresh_custom_bar()
        self.scene.rebuild()
        self.run_from(None)

    # -- custom boxes ----------------------------------------------------------------------------
    def custom_specs(self) -> list:
        return [s for s in self.library if s.custom]

    def _refresh_custom_bar(self) -> None:
        customs = self.custom_specs()
        if not customs or self.custom_allowed:
            self.custom_bar.clear("custom")
            return
        names = ", ".join(sorted({Path(s.file).name for s in customs if s.file}))
        self.custom_bar.show_notice(
            "custom", f"<b>This folder has {len(customs)} custom box{'es' if len(customs) != 1 else ''}</b> "
            f"({names}). They run Python code from the folder. Run them?",
            [("Run them", self.allow_custom), ("Not now", lambda: self.custom_bar.clear("custom"))])

    def allow_custom(self) -> None:
        self.custom_allowed = True
        self.custom_bar.clear("custom")
        self.custom_allowed_changed.emit()
        self.run_from([n.id for n in self.workflow.nodes.values() if self.workflow.spec(n).custom])

    # -- running ---------------------------------------------------------------------------------
    def run_from(self, changed: list[str] | None) -> None:
        """Run the boxes from ``changed`` on (all of them: None), in the background."""
        if self._running:
            # Queue it for after this run (stopping the run early); "all" wins
            # over any list.  Before, a box added during a run was queued as
            # "all", which then counted as nothing queued: it never ran.
            if changed is None or (self._has_queue and self._queued is None):
                self._queued = None
            else:
                self._queued = sorted(set(self._queued or []) | set(changed))
            self._has_queue = True
            self._stop.set()
            return
        problems = [p for p in self.workflow.validate() if "connect" not in p.lower()]
        for problem in problems[:1]:
            self.status.emit(problem)
        customs = {n.id for n in self.workflow.nodes.values() if self.workflow.spec(n).custom}
        self._running = True
        self._stop = threading.Event()
        previous = self.run
        library, workflow, relay, stop, allowed = self.library, self.workflow, self._relay, self._stop, self.custom_allowed
        blocked = set() if allowed else customs
        for node in workflow.order():
            if changed is None or node.id in workflow.descendants(changed):
                self.scene.set_status(node.id, WAITING, "Waiting…")

        def work():
            if blocked:
                # Custom boxes wait for the user's OK: run with them marked unavailable.
                return self._run_with_blocked(workflow, changed, previous, stop, relay, blocked)
            return self.runner.run(workflow, changed, previous, stop, on_status=relay.status.emit)

        run_in_background(work, relay.finished.emit, lambda message: relay.finished.emit(message))

    def _run_with_blocked(self, workflow, changed, previous, stop, relay, blocked):
        """Like the runner, but custom boxes (not yet allowed) are blocked."""
        original = {node_id: workflow.spec(node_id).needs for node_id in blocked}
        for node_id in blocked:
            spec = workflow.spec(node_id)
            spec.needs = ("your OK",)
        try:
            run = self.runner.run(workflow, changed, previous, stop, on_status=relay.status.emit)
        finally:
            for node_id, needs in original.items():
                workflow.spec(node_id).needs = needs
        for (node_id, _variant), result in run.results.items():
            if node_id in blocked and result.status == BLOCKED:
                result.message = "Waiting for your OK (a custom box)"
        return run

    def _status_arrived(self, result: Result) -> None:
        self._show_status(result)
        if self.selected == result.node:
            self._render_panel()

    def _show_status(self, result: Result) -> None:
        if result.variant != 0:
            return
        if result.status == DONE:
            subtitle = brief(result.value)
        elif result.status == FAILED:
            subtitle = "Failed. Click for details"
        elif result.status == RUNNING:
            subtitle = "Running…"
        elif result.status == BLOCKED:
            subtitle = result.message or "Waiting for your OK"
        else:
            subtitle = result.message or "Waiting…"
        self.scene.set_status(result.node, result.status, subtitle, result.error or result.message)

    def _run_finished(self, outcome) -> None:
        self._running = False
        if isinstance(outcome, Run):
            self.run = outcome
            for result in outcome.results.values():
                self._show_status(result)
            failed = outcome.failed()
            if failed:
                self.status.emit(f"{len(failed)} box{'es' if len(failed) != 1 else ''} failed: "
                                 + ", ".join(self.workflow.title(r.node) for r in failed))
            elif not outcome.stopped:
                self.status.emit(outcome.summary())
        else:
            self.status.emit(f"The run stopped: {outcome}")
        self.refresh_reproducibility()
        self._render_panel()
        if isinstance(outcome, Run) and not outcome.stopped and not self._has_queue:
            if self.history.add(snapshots.take(self.workflow, outcome, self.history.latest)) \
                    and self.body.currentIndex() == 2:
                self.runs.refresh()
            self.ran.emit()
        if self.body.currentIndex() == 1:
            self.summary.refresh()
        if self._has_queue or (isinstance(outcome, Run) and outcome.stopped):
            queued, self._queued, self._has_queue = self._queued, None, False
            if queued is not None and isinstance(outcome, Run) and outcome.stopped:
                # The stopped run left boxes unfinished: they run again too, or
                # a box added during a run would never get its result.
                unfinished = [node.id for node in self.workflow.nodes.values()
                              if outcome.result(node) is None
                              or outcome.result(node).status in (WAITING, RUNNING)]
                queued = sorted(set(queued) | set(unfinished))
            self.run_from(queued)

    def rerun(self) -> None:
        """Run everything again, after saying what differs from the record."""
        record = getattr(self.document, "record", None)
        differences = records.differences(record, self.workflow, self.folder) if record and record.results else []
        if differences:
            box = QMessageBox(self)
            box.setWindowTitle("This is not the recorded experiment")
            box.setText("Re-running now would not reproduce the saved results, because:")
            box.setInformativeText("\n".join("• " + d for d in differences))
            box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
            box.button(QMessageBox.Ok).setText("Run anyway")
            if box.exec() != QMessageBox.Ok:
                return
        self.runner.cache.clear()
        self.run = None
        self.run_from(None)

    # -- edits ----------------------------------------------------------------------------------
    def _edited(self, changed: list[str]) -> None:
        self._mark_edited()
        self.refresh_title()
        self.run_from(changed if changed else None)

    def _mark_edited(self, kind: str = "change") -> None:
        self.document.dirty = True
        if not self._restoring:
            self.edits.record(self.workflow.to_dict(), kind)
        self.edited.emit()

    # -- undo and redo ---------------------------------------------------------------------------
    def undo(self) -> None:
        """⌘Z: the state before the last change, run again (the cache serves what did not change)."""
        self._restore(self.edits.undo(), "undo", "Undid")

    def redo(self) -> None:
        self._restore(self.edits.redo(), "redo", "Redid")

    def _restore(self, entry, verb: str, past: str) -> None:
        if entry is None:
            self.status.emit(f"Nothing to {verb}")
            return
        label, data = entry
        try:
            fresh = Workflow.from_dict(data, self.library)
        except Exception as error:  # noqa: BLE001 - a box gone missing since, say
            self.status.emit(f"Could not {verb}: {error}")
            return
        self._restoring = True
        try:
            self._pending.clear()
            self.workflow.name = fresh.name
            self.workflow.nodes, self.workflow.edges, self.workflow.groups = fresh.nodes, fresh.edges, fresh.groups
            if self.selected is not None and self.selected not in self.workflow.nodes:
                self.selected = None
            self.scene.rebuild()
            self.select(self.selected)
        finally:
            self._restoring = False
        self.document.dirty = True
        self.edited.emit()
        self.refresh_title()
        self.run_from(None)
        self.status.emit(f"{past} {label}")

    def _setting_changed(self, node_id: str, name: str, value) -> None:
        self._pending.setdefault(node_id, {})[name] = value
        self._setting_timer.start()

    def _apply_pending_settings(self) -> None:
        pending, self._pending = self._pending, {}
        changed = []
        for node_id, values in pending.items():
            if node_id not in self.workflow.nodes:
                continue
            before = dict(self.workflow.nodes[node_id].settings)
            try:
                self.workflow.set(node_id, **values)
            except (ValueError, TypeError) as error:
                self.status.emit(str(error))
                continue
            if self.workflow.nodes[node_id].settings != before:    # the same value again: nothing to run
                changed.append(node_id)
                item = self.scene.boxes.get(node_id)
                if item is not None:
                    item.update()                                    # the eyebrow may name a new file
        if changed:
            self._mark_edited()
            self.run_from(changed)

    # -- groups ------------------------------------------------------------------------------
    def group_selected(self) -> None:
        group = self.scene.group_selected()
        if group is not None:
            self._mark_edited()
            self.select(group.id)
            self.status.emit(f"Grouped {len(group.members)} boxes. Double-click the group to open it; "
                             "⇧⌘G ungroups it")

    def open_group(self, group_id: str) -> None:
        if group_id not in self.workflow.groups:
            return
        self.scene.show_group(group_id)
        self.group_bar.setVisible(True)
        self.group_crumb.setText(f"{self.workflow.name} › {self.workflow.groups[group_id].name}")
        self.selected = None
        self._render_panel()
        QTimer.singleShot(0, self.view.fit)

    def close_group(self) -> None:
        group_id = self.scene.view_group
        self.scene.show_group(None)
        self.group_bar.setVisible(False)
        self.group_crumb.setText("")
        self.select(group_id if group_id in self.workflow.groups else None)
        QTimer.singleShot(0, self.view.fit)

    def save_group_as_box(self, group_id: str) -> None:
        """Write the group as a @workflow function into the folder's boxes/."""
        group = self.workflow.groups.get(group_id)
        if group is None:
            return
        if self.folder is None:
            QMessageBox.information(self, "Save as a box", "Open a folder first: the box is saved as a "
                                    "Python file in its boxes/ subfolder.")
            return
        from ...flow.workflow import _identifier
        source = group_to_python(self.workflow, group)
        header = _box_file_header(self.workflow, group)
        boxes = self.folder / "boxes"
        boxes.mkdir(exist_ok=True)
        target = boxes / f"{_identifier(group.name)}.py"
        if target.exists():
            answer = QMessageBox.question(self, "Save as a box", f"{target.name} exists in boxes/. Replace it?")
            if answer != QMessageBox.Yes:
                return
        target.write_text(header + source, encoding="utf-8")
        self.reload_library()
        self.status.emit(f"Saved {target.name}: “{group.name}” is in the box list under Yours")

    def _select(self, node_id: str | None) -> None:
        """The canvas's selection changed (a click)."""
        if self._selecting:                 # select() is at work: it renders once, at the end
            return
        if node_id != self.selected:
            self.selected = node_id
            self._render_panel()

    def select(self, node_id: str | None, tab: int | None = None) -> None:
        if tab is not None:
            self.tab = tab
            self._opening_tab = tab
        # The scene clears its selection before selecting: without the guard,
        # the panel would close and reopen (back on Result) at every select.
        self._selecting = True
        try:
            self.scene.select(node_id)
        finally:
            self._selecting = False
        self.selected = node_id
        self._render_panel()

    def _render_panel(self) -> None:
        """The side panel for what is selected; hidden when nothing is."""
        layout = self.panel_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                _delete_layout(item.layout())
        node = self.workflow.nodes.get(self.selected) if self.selected else None
        group = self.workflow.groups.get(self.selected) if self.selected else None
        if group is None and node is None and self.scene.view_group:
            group = self.workflow.groups.get(self.scene.view_group)
        if group is None and node is None:
            self.show_panel(False)
            return
        if not self.panel.isVisible():
            # The panel opens on Result, unless select() asked for a tab (a new
            # file box opens on Settings).  While it stays open, the tab sticks.
            self.tab = self._opening_tab if self._opening_tab is not None else 0
            self.show_panel(True)
        self._opening_tab = None
        if group is not None:
            self._render_group_panel(group)
            return
        spec = self.workflow.spec(node)
        result = self.run.result(node) if self.run else None
        title = label(node.title or spec.name, "pageTitle")
        close = button("✕", self.close_panel, tooltip="Close the panel (click the canvas does too)")
        close.setObjectName("panelClose")
        close.setFixedWidth(32)
        layout.addLayout(hbox(title, None, close))
        chip_text, chip_kind = status_text(result)
        colour = {"good": style.STATUS["good"], "critical": style.STATUS["critical"], "warning": style.STATUS["warning"],
                  "accent": style.tokens().accent, "muted": style.tokens().text_muted}[chip_kind]
        chips = label(f"<span style='color: {style.tokens().text_muted}'>{'Yours · ' if spec.custom else ''}{spec.group}"
                      f"</span> &nbsp; <span style='color: {colour}; font-weight: 600'>{chip_text}</span>")
        chips.setTextFormat(Qt.RichText)
        layout.addWidget(chips)
        help_text = label(_paragraphs(spec.help), "muted", wrap=True)
        layout.addWidget(help_text)
        tabs = SegmentedControl(TABS, compact=True)
        tabs.set_index(self.tab)
        tabs.changed.connect(self._tab_changed)
        pop = button("⤢", lambda: self.pop_out_tab(), tooltip="Open this tab in its own window")
        pop.setFixedWidth(34)
        layout.addLayout(hbox(tabs, None, pop))
        self.tabs = tabs
        layout.addWidget(self._tab_widget(node, spec, result, self.tab))
        connections = ", ".join(f"{p.label or p.name}: {p.type_name}" for p in spec.inputs) or "nothing"
        gives = ", ".join(p.type_name for p in spec.outputs)
        layout.addWidget(label(f"Takes {connections}." + (f" Gives a {gives}." if gives else ""), "muted", wrap=True))
        layout.addLayout(hbox(button("Duplicate", lambda: self.scene.duplicate(node.id)),
                              button("Disconnect all", lambda: self.scene.disconnect_all(node.id)),
                              button("Remove box", lambda: self.scene.remove_node(node.id), kind="danger"), None))
        layout.addStretch(1)

    def _render_group_panel(self, group) -> None:
        layout = self.panel_layout
        inside = self.scene.view_group == group.id
        close = button("✕", self.close_panel, tooltip="Close the panel")
        close.setObjectName("panelClose")
        close.setFixedWidth(32)
        layout.addLayout(hbox(label(group.name, "pageTitle"), None, close))
        statuses = [self.scene.statuses.get(m, ("waiting", "", ""))[0] for m in group.members]
        state = ("failed" if "failed" in statuses else "running" if "running" in statuses else
                 "done" if statuses and all(s == "done" for s in statuses) else "waiting")
        layout.addWidget(label(f"Workflow · {len(group.members)} boxes · {state}", "muted"))
        layout.addWidget(label("You are inside this group: boxes you add here become part of it." if inside else
                               "A sub-workflow shown as one box. Its inputs and outputs are the connections that "
                               "reach outside it. Double-click it to open its own canvas.", "muted", wrap=True))
        name = QLineEdit(group.name)
        name.setPlaceholderText("Name")
        name.editingFinished.connect(lambda g=group, n=name: self._rename_group(g, n.text()))
        layout.addWidget(label("NAME", "sectionLabel"))
        layout.addWidget(name)
        layout.addWidget(label("INSIDE", "sectionLabel"))
        for member in group.members:
            if member in self.workflow.nodes:
                status = self.scene.statuses.get(member, ("waiting", "", ""))
                row = button(f"{self.workflow.title(member)}  ·  {status[0]}",
                             lambda _=False, m=member, g=group: (self.open_group(g.id), self.select(m)))
                layout.addWidget(row)
        inputs, outputs = self.workflow.group_ports(group)
        takes = ", ".join(f"{p.label or p.name} ({p.type_name})" for _, _, p in inputs) or "nothing"
        gives = ", ".join(p.type_name for _, _, p in outputs) or "nothing"
        layout.addWidget(label(f"Takes {takes}. Gives {gives}.", "muted", wrap=True))
        layout.addWidget(label("AS PYTHON", "sectionLabel"))
        code = QPlainTextEdit(group_to_python(self.workflow, group))
        code.setReadOnly(True)
        code.setFont(theme.mono_font(10.5))
        code.setMaximumHeight(220)
        from .viewers import PythonHighlighter
        PythonHighlighter(code.document())
        layout.addWidget(code)
        layout.addWidget(label("The group and this function are the same thing: Save as a box writes it to the "
                               "folder's boxes/ and it appears in the box list; a file like it opens as this group.",
                               "muted", wrap=True))
        layout.addLayout(hbox(button("Close" if inside else "Open", self.close_group if inside else
                                     (lambda: self.open_group(group.id)), kind="primary"),
                              button("Save as a box", lambda: self.save_group_as_box(group.id)),
                              button("Ungroup", lambda: (self.scene.ungroup(group.id), self._mark_edited(),
                                                         self.select(None))),
                              button("Remove", lambda: self.scene.remove_group(group.id), kind="danger"), None))
        layout.addStretch(1)

    def _rename_group(self, group, name: str) -> None:
        name = name.strip()
        if name and name != group.name:
            group.name = name
            self._mark_edited()
            self.scene.rebuild()
            self.group_crumb.setText(f"{self.workflow.name} › {name}" if self.scene.view_group == group.id else "")

    def _tab_changed(self, index: int) -> None:
        self.tab = index
        self._render_panel()

    def _tab_widget(self, node, spec, result: Result | None, index: int) -> QWidget:
        if index == 0:
            if result is None:
                return label("Runs first; the result shows here.", "muted", wrap=True)
            if result.status == FAILED:
                # The box's own words first (a missing file, a dataset to fetch
                # by hand, a setting out of range), with any address or folder
                # in them clickable; the traceback is for whoever fixes the box.
                kind, text = _error_message(result.error)
                message = label(_linkified(text), "failedMessage", wrap=True, selectable=True)
                message.setTextFormat(Qt.RichText)
                message.setOpenExternalLinks(True)
                parts = [label("WHAT WENT WRONG", "sectionLabel"), message]
                if kind:
                    parts.append(label(kind, "muted"))
                if result.traceback:
                    parts += [label("DETAILS", "sectionLabel"), _error_box(result.traceback)]
                parts.append(label("The rest of the workflow keeps working. Fix the setting or the file, and "
                                   "it runs again on its own.", "muted", wrap=True))
                host = QWidget()
                host.setLayout(vbox(*parts))
                return host
            if result.status == BLOCKED:
                host = QWidget()
                host.setLayout(vbox(label(result.message or "Not allowed to run yet", wrap=True),
                                    hbox(button("Run it", self.allow_custom, kind="primary"), None)
                                    if spec.custom and not self.custom_allowed else label("")))
                return host
            if result.status != DONE:
                return label((result.message or "Waiting for the boxes before it") + ".", "muted", wrap=True)
            if spec.result_type is not None and len(result.values) > 1:
                host = QWidget()
                column = QVBoxLayout(host)
                column.setContentsMargins(0, 0, 0, 0)
                for port in spec.outputs:
                    column.addWidget(label(port.name.upper(), "sectionLabel"))
                    column.addWidget(result_widget(result.values.get(port.name), self, node))
                return host
            widget = result_widget(result.value, self, node)
            if self.run and len(self.run.variants) > 1 and self.run.result(node, 1) is not None:
                host = QWidget()
                host.setLayout(vbox(label(f"The first of {len(self.run.variants)} sweep values; the collecting "
                                          "boxes after it show all of them.", "muted", wrap=True), widget))
                return host
            return widget
        if index == 1:
            if result is None or result.status not in (DONE, FAILED):
                return label("Runs first, then shows how it got there.", "muted", wrap=True)
            return how_widget(result.explanation, self, result.value)
        if index == 2:
            return code_widget(spec, self)
        settings = SettingsWidget(spec, node.settings, self.folder,
                                  columns=columns_connected(self.workflow, self.run, node))
        settings.changed.connect(lambda name, value, nid=node.id: self._setting_changed(nid, name, value))
        return settings

    def pop_out_tab(self) -> None:
        node = self.workflow.nodes.get(self.selected) if self.selected else None
        if node is None:
            return
        spec, index = self.workflow.spec(node), self.tab
        result = self.run.result(node) if self.run else None
        title = f"{node.title or spec.name} · {TABS[index]}"
        if index == 2:
            pop_out(title, lambda: code_widget(spec, self, compact=False), self.window())
        else:
            pop_out(title, lambda: self._tab_widget(node, spec, result, index), self.window())

    # -- results opening elsewhere ------------------------------------------------------------
    def _open_elsewhere(self, document) -> None:
        """Open a result on its own page, which keeps a way back here."""
        document.opened_from = self.document
        self.open_document.emit(document)

    def open_as_log(self, log: EventLog) -> None:
        from ..studio.documents import LogDocument
        self._open_elsewhere(LogDocument(log))

    def open_as_model(self, net: PetriNet) -> None:
        from ..studio.documents import ModelDocument
        self._open_elsewhere(ModelDocument(net, origin=f"from {self.workflow.name}"))

    def open_as_cpn(self, net) -> None:
        from ..studio.documents import CpnDocument
        self._open_elsewhere(CpnDocument(net))

    def edit_copy(self, net: PetriNet) -> None:
        """*Open a copy in Model ›*: the window opens a copy on the net canvas."""
        where = self.workflow.title(self.selected) if self.selected in self.workflow.nodes else "a box"
        self.edit_requested.emit(net, f"from {where} in {self.workflow.name}")

    def layout_tidied(self) -> None:
        """The user dragged a result's places or transitions: kept with the workflow."""
        self._mark_edited()

    def save_table(self, table: Table) -> None:
        """Save as CSV… under a table: the file, and where it came from beside it."""
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Save table", suggested_path(f"{table.name}.csv"), "CSV (*.csv)")
        if path:
            try:
                table.to_csv(path)
            except OSError as error:
                QMessageBox.warning(self, "Could not save the table", str(error))
                return
            self.status.emit(f"Saved {Path(path).name}, with where it came from in "
                             f"{Path(path).stem}.provenance.json beside it")

    def export_script(self) -> None:
        """⋯ ▸ Export as Python script…: the analysis as a .py that runs on its own."""
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Export as Python script",
                                              suggested_path(f"{self.workflow.name}.py"), "Python script (*.py)")
        if not path:
            return
        try:
            Path(path).write_text(script_text(self.workflow, Path(path).name), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Could not export", str(error))
            return
        self.status.emit(f"Wrote {Path(path).name}: python {Path(path).name} runs the same analysis and "
                         "writes the results beside it")

    def save_figure(self, figure: Figure) -> None:
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Save figure", suggested_path(f"{figure.name}.svg"),
                                              "SVG (*.svg);;PNG (*.png)")
        if path:
            try:
                figure.save(path)
                self.status.emit(f"Saved {Path(path).name}")
            except ValueError as error:
                QMessageBox.warning(self, "Could not save the figure", str(error))

    # -- menus and quick add --------------------------------------------------------------
    def _menu(self, kind: str, what, screen_pos) -> None:
        menu = QMenu(self)
        if kind == "node":
            node_id = what
            menu.addAction("Run from here", lambda: self.run_from([node_id]))
            menu.addAction("Duplicate", lambda: self.scene.duplicate(node_id))
            menu.addAction("Disconnect all", lambda: self.scene.disconnect_all(node_id))
            menu.addSeparator()
            menu.addAction("Remove", lambda: self.scene.remove_node(node_id))
        elif kind == "group":
            group_id = what
            menu.addAction("Open", lambda: self.open_group(group_id))
            menu.addAction("Save as a box", lambda: self.save_group_as_box(group_id))
            menu.addAction("Ungroup", lambda: (self.scene.ungroup(group_id), self._mark_edited(), self.select(None)))
            menu.addSeparator()
            menu.addAction("Remove", lambda: self.scene.remove_group(group_id))
        elif kind == "edge":
            edge: Edge = what
            menu.addAction("Remove connection", lambda: self.scene.remove_edge(edge))
        else:
            point: QPointF = what
            menu.addAction("Add box here…", lambda: self.quick_add(point))
            undo = menu.addAction(f"Undo {self.edits.undo_label}" if self.edits.can_undo else "Undo", self.undo)
            undo.setEnabled(self.edits.can_undo)
            redo = menu.addAction(f"Redo {self.edits.redo_label}" if self.edits.can_redo else "Redo", self.redo)
            redo.setEnabled(self.edits.can_redo)
            selected = [i for i in self.scene.selectedItems() if hasattr(i, "node")]
            if len(selected) >= 2 and self.scene.view_group is None:
                menu.addAction(f"Group {len(selected)} boxes", self.group_selected)
            menu.addAction("Fit to window", self.view.fit)
        menu.exec(QPoint(int(screen_pos.x()), int(screen_pos.y())) if hasattr(screen_pos, "x") else screen_pos)

    # -- the record, saving ------------------------------------------------------------------
    def show_record(self) -> None:
        python = to_python(self.workflow)
        record = records.make_record(self.workflow, self.run, self.folder, lock=False)
        data = {"format": records.FORMAT, **self.workflow.to_dict(), "record": record.to_dict()}
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        dialog = QDialog(self.window())
        dialog.setWindowTitle(f"{self.workflow.name}: the record")
        dialog.resize(900, 700)
        code = QPlainTextEdit(python)
        code.setReadOnly(True)
        code.setFont(theme.mono_font(11))
        from .viewers import PythonHighlighter
        PythonHighlighter(code.document())
        file_text = QPlainTextEdit(text)
        file_text.setReadOnly(True)
        file_text.setFont(theme.mono_font(11))
        from PySide6.QtWidgets import QApplication
        status = self._repro
        repro = label(f"<b>{status.headline}.</b> " + " ".join(status.details), "muted", wrap=True)
        repro.setTextFormat(Qt.RichText)
        dialog.setLayout(vbox(
            label("REPRODUCIBILITY", "sectionLabel"), repro,
            label("Everything needed to run this experiment again and get the same numbers. The Python and the "
                  "canvas are two views of the same workflow.", "muted", wrap=True),
            label("AS PYTHON", "sectionLabel"), code, label("THE WORKFLOW FILE", "sectionLabel"), file_text,
            hbox(button("Copy Python", lambda: QApplication.clipboard().setText(python)),
                 button("Copy file", lambda: QApplication.clipboard().setText(text)), None,
                 button("Done", dialog.accept, kind="primary")), margins=(16, 16, 16, 16)))
        dialog.show()
        self._record_dialog = dialog

    def write_to(self, path: str, quiet: bool = False) -> None:
        """Save the workflow and its record to ``path`` (the window's Keep / Save As)."""
        target = Path(path)
        self.workflow.name = target.stem if target.stem else self.workflow.name
        self.document.record = records.save(self.workflow, target, self.run, self.folder or target.parent,
                                            history=self.history.to_dict())
        self.document.path = str(target)
        self.document.dirty = False
        self.refresh_reproducibility()
        if not quiet:
            self.status.emit(f"Saved {target.name}")
        self.saved.emit()

    def save(self) -> bool:
        if self.document.path:
            self.write_to(self.document.path)
            return True
        self.export()
        return bool(self.document.path)

    def autosave(self) -> bool:
        try:
            self.write_to(self.document.path, quiet=True)
            return True
        except OSError as error:
            self.save_error = str(error)
            return False

    def export(self) -> None:
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Save workflow", suggested_path(f"{self.workflow.name}.cpnflow"),
                                              "Workflow (*.cpnflow)")
        if path:
            if not path.lower().endswith(".cpnflow"):
                path += ".cpnflow"
            self.write_to(path)

    def drawings(self) -> dict[str, str]:
        """SVG per box whose result the canvas can draw: nets, maps, trees (for the report)."""
        from ...flow.types import DFG, OCDFG, PetriNet, ProcessTree
        from ..studio.graph_builders import dfg_specs, ocdfg_specs, petri_net_specs
        from ..studio.graph_view import GraphView
        out: dict[str, str] = {}
        if self.run is None:
            return out
        for node in self.workflow.order():
            result = self.run.result(node)
            if result is None or result.status != DONE:
                continue
            value = result.value
            view = GraphView()
            if isinstance(value, PetriNet):
                nodes, edges = petri_net_specs(value, show_place_names=len(value.places) <= 40)
                positions = None
                ids = {n.id for n in nodes}
                if node.layout and ids <= set(node.layout):
                    positions = {k: v for k, v in node.layout.items() if k in ids}
                view.graph.populate(nodes, edges, positions)
            elif isinstance(value, DFG):
                nodes, edges = dfg_specs(value, "frequency")
                view.graph.populate(nodes, edges, layer_gap=64)
            elif isinstance(value, OCDFG):
                nodes, edges, _colours = ocdfg_specs(value)
                view.graph.populate(nodes, edges, layer_gap=64)
            elif isinstance(value, ProcessTree):
                from ..studio.derivation_view import tree_specs
                nodes, edges, positions = tree_specs(value)
                view.graph.populate(nodes, edges, positions, layer_gap=40)
            else:
                continue
            try:
                out[node.id] = view.svg_text()
            except Exception:  # noqa: BLE001 - a drawing that fails is left out, the report still comes
                continue
        return out

    def export_report(self) -> None:
        """⋯ ▸ Export report…: the analysis as one HTML page."""
        from ...flow import report as reports
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Export report", suggested_path(f"{self.workflow.name}.html"),
                                              "Web page (*.html)")
        if not path:
            return
        try:
            reports.write_report(path, workflow=self.workflow, run=self.run, folder=self.folder,
                                 drawings=self.drawings(), record=getattr(self.document, "record", None),
                                 status=self._repro)
        except OSError as error:
            QMessageBox.warning(self, "Could not export", str(error))
            return
        self.status.emit(f"Exported the report to {Path(path).name}")
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def export_experiment(self) -> None:
        from ..studio.widgets import suggested_path
        path, _ = QFileDialog.getSaveFileName(self, "Export experiment", suggested_path(f"{self.workflow.name}.zip"),
                                              "Zip archive (*.zip)")
        if not path:
            return
        try:
            target = records.export_experiment(self.workflow, self.run, path, self.folder, self.library)
        except OSError as error:
            QMessageBox.warning(self, "Could not export", str(error))
            return
        self.status.emit(f"Exported the experiment to {Path(target).name}")

    def _show_more_menu(self) -> None:
        self.more_menu.exec(self.more_button.mapToGlobal(QPoint(0, self.more_button.height() + 4)))

    def stop(self) -> None:
        self._stop.set()


def _sizes(saved) -> list[int]:
    """The splitter's sizes from a saved layout: canvas, panel.  A layout saved
    before 0.8 had three (box list, canvas, panel); its last two are kept."""
    try:
        sizes = [int(v) for v in saved or []]
    except (TypeError, ValueError):
        sizes = []
    if len(sizes) == 3:
        sizes = sizes[1:]
    if len(sizes) != 2 or min(sizes) < 1:
        sizes = [820, 380]
    return sizes


def _box_file_header(workflow, group) -> str:
    """The imports a group's Python needs, as a file in boxes/."""
    imports: dict[str, set[str]] = {}
    types: set[str] = set()
    for member in group.members:
        spec = workflow.spec(member)
        imports.setdefault(spec.module, set()).add(spec.function.__name__)
        for port in [*spec.inputs, *spec.outputs]:
            if port.info is not None:
                types.add(port.info.python)
    lines = ['"""' + f"{group.name}: a workflow saved as a box from OpenProcess.\n\nEdit it as any box file: "
             'the app reloads it when you save."""', "", "from openprocess.flow import workflow"
             + (", " + ", ".join(sorted(types)) if types else "")]
    for module, functions in sorted(imports.items()):
        lines.append(f"from {module} import {', '.join(sorted(functions))}")
    return "\n".join(lines) + "\n\n\n"


class _Splitter(QSplitter):
    """The page's splitter: a wide, quiet handle that lights up under the
    mouse; double-click it to get the default panel width back."""
    reset_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setHandleWidth(14)
        self.setChildrenCollapsible(False)

    def createHandle(self):  # noqa: N802
        handle = super().createHandle()
        handle.installEventFilter(self)
        return handle

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.MouseButtonDblClick:
            self.reset_requested.emit()
            return True
        return super().eventFilter(watched, event)


def _paragraphs(text: str | None) -> str:
    """A docstring as paragraphs: the source's line breaks joined, blank lines kept."""
    blocks = re.split(r"\n\s*\n", textwrap.dedent(text or "").strip())
    return "\n\n".join(" ".join(line.strip() for line in block.splitlines()) for block in blocks)


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _delete_layout(item.layout())


def _error_message(error: str) -> tuple[str, str]:
    """``"DatasetMissing: Sepsis cases is not in the cache…"`` → the kind of error
    (shown small) and what it says; an error without a message keeps its kind
    as the message."""
    error = (error or "The box failed without saying why.").strip()
    kind, separator, text = error.partition(": ")
    if separator and text.strip() and " " not in kind:
        return kind.rsplit(".", 1)[-1], text.strip()      # the class, not its module path
    return "", error


def _linkified(text: str) -> str:
    """The text as rich text, with web addresses and folder paths as links."""
    from html import escape
    from urllib.parse import quote

    def link(match) -> str:
        address = match.group(0)
        trailing = ""
        while address and address[-1] in ".,;:)”\"'":
            trailing = address[-1] + trailing
            address = address[:-1]
        shown = escape(address)
        if address.startswith("http"):
            return f'<a href="{escape(address)}">{shown}</a>{escape(trailing)}'
        folder = Path(address)
        target = folder if folder.is_dir() else folder.parent
        if target.is_dir():
            return f'<a href="file://{quote(str(target))}">{shown}</a>{escape(trailing)}'
        return shown + escape(trailing)
    return re.sub(r"https?://\S+|(?<![\w/])(?:/|[A-Za-z]:\\)[^\s“”\"']+", link, escape(text)).replace("\n", "<br>")


def _error_box(text: str) -> QPlainTextEdit:
    box = QPlainTextEdit(text)
    box.setReadOnly(True)
    box.setFont(theme.mono_font(10))
    box.setMaximumHeight(160)
    box.setStyleSheet(f"color: {style.STATUS['critical']};")
    return box
