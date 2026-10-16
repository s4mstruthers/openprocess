"""Smoke test for OpenProcess Studio: build every page offscreen and exercise it.

Skipped when PySide6 is not installed.  Nothing is shown on screen: Qt's
"offscreen" platform renders into memory, which is enough to catch errors
in widget construction, signal wiring and painting.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fake_bin(tmp_path_factory, monkeypatch):
    """Nothing a test moves "to the Bin" goes to your real Bin: it lands here."""
    import shutil
    from openprocess.gui.studio import app as studio_app

    folder = tmp_path_factory.mktemp("Bin")

    def move_to_trash(path) -> bool:
        target, number = folder / Path(path).name, 2
        while target.exists():
            target = folder / f"{number} {Path(path).name}"
            number += 1
        shutil.move(str(path), str(target))
        return True
    monkeypatch.setattr(studio_app, "move_to_trash", move_to_trash)
    return folder


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    # Under load one processEvents can outlast the budget; drain what came due
    # meanwhile (a page's deferred fit, say), so the test sees a settled window.
    for _ in range(3):
        app.processEvents()


def test_studio_end_to_end(app):
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument, ModelDocument
    from openprocess.mining import inductive_miner, read_xes

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    log = LogDocument(read_xes(DATA / "plane_wilma_10.xes"), path=str(DATA / "plane_wilma_10.xes"))
    window.add_document(log)
    page = window.current_page()
    for index in range(len(page.pages)):          # build and paint every tab
        page.tabs.set_index(index)
        _pump(app, 0.2)
        page.grab()
    _pump(app, 1.0)                               # discovery runs in the background

    result = inductive_miner(log.simple_log())
    window.add_document(ModelDocument(result.net, origin="test", derivation=result,
                                      source_log=log))
    model_page = window.current_page()
    # In the studio, conformance is a handoff to an analysis; Learn's model page replays inline.
    assert model_page.conformance_mode == "handoff" and model_page.check_log_button is not None
    from openprocess.gui.studio.model_page import ModelPage
    inline = ModelPage(window.documents[-1], window.logs)
    inline._check()
    _pump(app, 2.0)
    assert inline.conformance is not None
    replay, alignments = inline.conformance
    assert alignments.average_fitness == pytest.approx(1.0)

    # Step through: a real mouse click on an enabled transition fires it.
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    model_page.mode_switch.set_index(1)
    _pump(app, 0.2)
    first = model_page.net.enabled(model_page.marking)[0]
    item = model_page.view.graph.nodes[first]
    view = model_page.view
    point = view.mapFromScene(item.scenePos())
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    _pump(app, 0.1)
    assert [t for _, t in model_page.history] == [first]
    model_page._back()
    assert model_page.history == [] and model_page.marking == model_page.net.initial_marking
    # Simulate: the timer fires transitions and completes runs.
    model_page.mode_switch.set_index(2)
    model_page.speed.setValue(30)
    model_page.play_button.setChecked(True)
    _pump(app, 1.5)
    model_page.play_button.setChecked(False)
    assert model_page.runs_completed >= 1
    model_page.mode_switch.set_index(0)
    model_page.view_switch.set_index(1)
    _pump(app, 1.0)
    assert model_page.state_view.graph.nodes
    window.grab()

    # dotted chart: zoom, back, reconfigure
    log_page = window.pages[log.id]
    window._select_document(log)                        # back to Mine
    log_page.tabs.set_index(3)
    _pump(app, 0.3)
    from openprocess.gui.studio.dotted_chart import DottedChartPanel
    panel = log_page.findChild(DottedChartPanel)
    chart = panel.chart
    full = (chart.vx0, chart.vx1)
    chart.set_view(chart.vx0, (chart.vx0 + chart.vx1) / 2, 0, 3)
    assert chart.zoom_factors()[0] > 1.9
    chart.back()
    assert (chart.vx0, chart.vx1) == full
    panel.rows_box.setCurrentText("Activity")
    panel.colour_box.setCurrentText("Lifecycle")
    panel.shape_box.setCurrentText("Activity")
    panel.all_events.setChecked(True)
    panel.connect_box.setChecked(True)
    _pump(app, 0.2)
    assert len(chart.data.row_values) == 6
    panel.grab()

    # removing: a file-backed log goes without a question; the page disappears
    before = len(window.documents)
    window.remove_documents([log.id])
    assert len(window.documents) == before - 1 and log.id not in window.pages
    window.close()


def test_graph_view_zoom_controls(app):
    from openprocess.gui.studio.graph_view import GraphView, NodeSpec, EdgeSpec

    view = GraphView()
    view.resize(600, 400)
    view.graph.populate([NodeSpec("a", "transition", text="A"),
                         NodeSpec("b", "transition", text="B")], [EdgeSpec("a", "b")])
    view.show()
    view.fit()
    controls = view.zoom_controls
    assert controls is not None and controls.isVisible()
    start = view.zoom()
    controls.plus.click()
    assert view.zoom() == pytest.approx(min(start * 1.25, GraphView.MAX_ZOOM))
    assert not view.auto_fit
    assert controls.percent.text() == f"{round(view.zoom() * 100)}%"
    controls.minus.click()
    controls.minus.click()
    assert view.zoom() < start
    controls.percent.click()
    assert view.zoom() == pytest.approx(1.0)
    controls.fit.click()
    assert view.auto_fit
    for _ in range(40):
        view.zoom_out()
    assert view.zoom() == pytest.approx(GraphView.MIN_ZOOM)
    assert not controls.minus.isEnabled()
    view.close()


def test_cpn_page_edit_simulate_analyse(app, tmp_path):
    """The CPN workspace: modes, firing, back, history, bindings, editing,
    declarations, state space, saving and exporting a simulation as a log."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from models import dining_philosophers

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.cpn_page import CpnPage, simulation_to_log
    from openprocess.gui.studio.documents import CpnDocument, LogDocument

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.open_path(str(DATA / "plane_boarding.cpn"))
    page = window.current_page()
    assert isinstance(page, CpnPage)
    _pump(app, 0.2)
    assert not page.net.errors
    assert page.enabled_elements, "check-in should be enabled initially"

    # CPN Tools' marking display: the file hides Isle's tokens; clicking the
    # count shows them.
    isle = next(p for p in page.net.all_places() if p.name == "Isle")
    isle_item = page.scene.place_items[isle.id]
    assert isle_item.count_pill.isVisible() and not isle_item.marking_label.isVisible()
    isle_item.toggle_marking()
    assert isle_item.marking_label.isVisible() and not isle.graphics.marking_hidden
    isle_item.toggle_marking()
    assert not isle_item.marking_label.isVisible()

    # Edit mode: clicking a transition does not fire it.
    enabled_id = page.enabled_elements[0].transition_id
    item = page.scene.transition_items[enabled_id]
    page.view.centerOn(item)
    _pump(app, 0.1)
    point = page.view.mapFromScene(item.scenePos())
    QTest.mouseClick(page.view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    _pump(app, 0.1)
    assert page.simulator.step_count == 0

    # Step through: a real click fires, Back undoes, the history fills.
    page.mode_switch.set_index(1)
    point = page.view.mapFromScene(item.scenePos())
    QTest.mouseClick(page.view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    _pump(app, 0.1)
    assert page.simulator.step_count == 1
    page.back_button.click()
    assert page.simulator.step_count == 0
    for _ in range(5):
        page.step_button.click()
    assert page.simulator.step_count == 5
    assert page.history_list.count() == 5
    page._rewind_to_item(page.history_list.item(3))      # 4th newest = after step 2
    assert page.simulator.step_count == 2

    # A specific binding from the inspector.
    page.binding_filter.setText("Check in")
    assert page.binding_list.count() > 0
    page.binding_list.setCurrentRow(0)
    page.fire_selected.click()
    assert page.simulator.step_count == 3
    page.binding_filter.clear()

    # Simulate: fast-forward to the end of boarding.
    page.mode_switch.set_index(2)
    page.forward_steps.setValue(5000)
    page.forward_button.setChecked(True)
    deadline = time.time() + 60
    while page.forward_button.isChecked() and time.time() < deadline:
        app.processEvents()
    assert not page.forward_button.isChecked()
    seated = next(p for p in page.net.all_places() if p.name == "Seated")
    assert page.simulator.marking.get(page.net.marking_key(seated.id)).size() == 120

    # The simulation as an event log: one case per passenger.
    log = simulation_to_log(page.net, page.simulator.log, "id")
    assert len(log) == 120
    assert {e.activity for t in log.traces for e in t.events} >= {"Move in", "stow bag"}
    before = len(window.documents)
    page.log_generated.emit(log)
    assert len(window.documents) == before + 1
    assert isinstance(window.documents[-1], LogDocument)

    # Editing a guard recompiles, marks the model dirty and resets the simulation.
    window._select_document(page.document)              # the log opened in Mine; back to Model
    page.mode_switch.set_index(0)
    move_up = next(t for t in page.net.all_transitions() if t.name == "Move up")
    page.reveal_element(move_up.id)
    assert page.element is move_up
    page.guard_edit.setText("[nr=cr+1, cr<r")                 # a syntax error
    page.guard_edit.editingFinished.emit()
    assert page.document.dirty and page.simulator.step_count == 0
    assert any(e.element_id == move_up.id for e in page.net.errors)
    assert "Problems (" in page.inspector_tabs.buttons[2].text()
    assert window.items[page.document.id].text(0).endswith("•")
    page.guard_edit.setText("[nr=cr+1, cr<r]")
    page.guard_edit.editingFinished.emit()
    assert not page.net.errors

    # Saving writes a .cpn that reads back.
    target = tmp_path / "saved.cpn"
    assert page._write(target)
    assert not page.document.dirty
    from openprocess.io.cpn_reader import read_cpn
    assert read_cpn(target).compile() == []

    # A new model, built by hand: declarations, places, transition, arcs.
    net = dining_philosophers(3)
    window.add_document(CpnDocument(net))
    small = window.current_page()
    small.structure_switch.set_index(1)
    text = small.declarations_editor.toPlainText()
    assert "colset" in text
    small.declarations_editor.setPlainText(text + "\nval extra = 1;")
    assert small.apply_button.isEnabled()
    small.apply_button.click()
    assert not small.net.errors
    small.inspector_tabs.set_index(3)
    small.space_button.click()
    deadline = time.time() + 30
    while small.busy and time.time() < deadline:
        app.processEvents()
    assert small.space_results.isVisible()
    assert "Full state space" in small.space_status.text()

    # Draw: place tool, click on the canvas.
    small.mode_switch.set_index(0)
    small.tool_switch.set_index(1)
    places = sum(1 for _ in small.net.all_places())
    QTest.mouseClick(small.view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(30, 30))
    assert sum(1 for _ in small.net.all_places()) == places + 1
    assert small.document.dirty

    # Undo / redo: the new place goes and comes back; guard edits too.
    small.undo_button.click()
    assert sum(1 for _ in small.net.all_places()) == places
    small.redo_button.click()
    assert sum(1 for _ in small.net.all_places()) == places + 1
    take = next(t for t in small.net.all_transitions() if t.name == "take")
    small.reveal_element(take.id)
    small.guard_edit.setText("[p = p]")
    small.guard_edit.editingFinished.emit()
    assert next(t for t in small.net.all_transitions() if t.name == "take").guard_text == "[p = p]"
    small.undo()
    assert next(t for t in small.net.all_transitions() if t.name == "take").guard_text == ""
    assert not small.net.errors
    # Dragging a node is undoable and marks the model edited.
    small.tool_switch.set_index(0)
    item = next(iter(small.scene.transition_items.values()))
    small.view.centerOn(item)
    start = small.view.mapFromScene(item.scenePos())
    before = (item.transition.graphics.x, item.transition.graphics.y)
    QTest.mousePress(small.view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(small.view.viewport(), start + QPoint(40, 30))
    QTest.mouseRelease(small.view.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(40, 30))
    moved = small.net.find_transition(item.transition.id)
    assert (moved.graphics.x, moved.graphics.y) != before
    small.undo()
    restored = small.net.find_transition(item.transition.id)
    assert (restored.graphics.x, restored.graphics.y) == before

    small.document.dirty = False          # closing would otherwise ask to save
    window.close()


def test_compare_logs(app):
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.compare_page import ComparePage, activity_shares, key_figures
    from openprocess.gui.studio.documents import ComparisonDocument, LogDocument
    from openprocess.mining import read_xes
    from openprocess.mining.log import EventLog, parse_simple_log

    window = StudioWindow()
    window.show()
    wilma = LogDocument(read_xes(DATA / "plane_wilma_10.xes"), path=str(DATA / "plane_wilma_10.xes"))
    textbook = LogDocument(EventLog.from_simple_log(
        parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"), "L1"))
    window.add_document(wilma)
    window.add_document(textbook)
    window.compare([wilma, textbook])
    page = window.current_page()
    assert isinstance(page, ComparePage)
    assert isinstance(window.documents[-1], ComparisonDocument)
    for index in range(4):                 # every tab builds
        page.tabs.set_index(index)
        _pump(app, 0.05)
    assert len(page.charts) == 2
    # Same activity, same colour: colour values are shared.
    assert page.charts[0].data.colour_values == page.charts[1].data.colour_values
    # Zooming one chart zooms the other (same x window).
    first, second = page.charts
    first.set_view(first.vx0, (first.vx0 + first.vx1) / 2, first.vy0, first.vy1)
    assert second.vx1 == pytest.approx(first.vx1)
    figures = key_figures(textbook)
    assert figures[0][1] == "6" and figures[3][1] == "3"          # cases, variants
    rows = {name: shares for name, shares, _ in activity_shares([wilma, textbook])}
    assert rows["a"] == [0.0, 1.0]
    # Comparing the same logs again selects the existing comparison.
    count = len(window.documents)
    window.compare([wilma, textbook])
    assert len(window.documents) == count
    # Removing a log removes its comparisons too.
    window.remove_documents([wilma.id])
    assert not any(isinstance(d, ComparisonDocument) for d in window.documents)
    textbook.path = "x"                    # avoid the unsaved-log question on close
    window.close()



def test_cpn_arc_editing_like_cpn_ide(app):
    """Arcs are edited as in CPN IDE: drag the line to add a bend, drag the
    bend back into line to remove it, drag an end to reconnect; nodes snap
    into line with each other.  Everything is undoable."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest

    from openprocess.gui.items import PlaceItem
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.open_path(str(DATA / "plane_boarding.cpn"))
    page = window.current_page()
    _pump(app, 0.2)
    page.mode_switch.set_index(0)
    page.tool_switch.set_index(0)
    scene, view = page.scene, page.view
    port = view.viewport()

    def drag(start: QPointF, offset: QPoint) -> None:
        view.centerOn(start)
        _pump(app, 0.05)
        a = view.mapFromScene(start)
        QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(port, a + offset / 2)
        QTest.mouseMove(port, a + offset)
        QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, a + offset)

    # A long straight arc (not one of a parallel pair, which is drawn shifted).
    arc_item = max((a for a in scene.arc_items.values()
                    if not a.arc.bendpoints and abs(a.bow) < 1e-9),
                   key=lambda a: (a.waypoints()[0] - a.waypoints()[-1]).manhattanLength())
    arc_id = arc_item.arc.id
    ends = arc_item.waypoints()
    middle = (ends[0] + ends[-1]) / 2

    # A click (no movement) only selects the arc.
    view.centerOn(middle)
    _pump(app, 0.05)
    QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, view.mapFromScene(middle))
    assert arc_item.isSelected() and arc_item.arc.bendpoints == []

    # Dragging the line adds a bend where it was grabbed.
    drag(middle, QPoint(0, 60))
    arc_item = scene.arc_items[arc_id]
    assert len(arc_item.arc.bendpoints) == 1

    # Dragging that bend back into line removes it.
    bend = arc_item.waypoints()[1]
    back = view.mapFromScene(middle) - view.mapFromScene(bend)
    drag(bend, back)
    assert scene.arc_items[arc_id].arc.bendpoints == []
    page.undo()
    assert len(scene.arc_items[arc_id].arc.bendpoints) == 1
    page.undo()
    assert scene.arc_items[arc_id].arc.bendpoints == []

    # Dragging the place end onto another place reconnects the arc.
    arc_item = scene.arc_items[arc_id]
    old_place = arc_item.arc.place_id
    other = next(p for p in scene.place_items.values() if p.place.id != old_place)
    scene.clearSelection()
    arc_item.setSelected(True)
    end = arc_item.waypoints()[-1]
    drag(end, view.mapFromScene(other.pos()) - view.mapFromScene(end))
    assert scene.arc_items[arc_id].arc.place_id == other.place.id
    assert not page.net.errors or True          # the inscription may not fit the new place
    page.undo()
    assert scene.arc_items[arc_id].arc.place_id == old_place

    # A dragged node snaps level with another node (7 units).
    nodes = list(scene.place_items.values())
    mover, anchor = nodes[0], nodes[1]
    scene.clearSelection()
    target_y = anchor.pos().y() + 5 - mover.pos().y()
    start = mover.pos()
    view.centerOn(start)
    _pump(app, 0.05)
    a = view.mapFromScene(start)
    b = view.mapFromScene(start + QPointF(90, target_y))
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(port, (a + b) / 2)
    QTest.mouseMove(port, b)
    assert any(line.isVisible() for line in scene._guides)      # the guide line shows
    target_ys = {y for _, y in scene._snap_targets}
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, b)
    assert isinstance(mover, PlaceItem)
    final_y = scene.place_items[mover.place.id].pos().y()
    assert final_y in target_ys and abs(final_y - anchor.pos().y()) <= 7
    page.undo()

    page.document.dirty = False
    window.close()


def test_petri_net_editor_draw_and_analyse(app, tmp_path):
    """Draw a WF-net with the mouse (names typed on creation, arcs by
    dragging), then analyse: sound; break it and get a counterexample that
    replays in the token game; save as PNML and open it again."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton, QTableWidget

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import CpnDocument
    from openprocess.gui.studio.petri_page import PetriNetPage
    from openprocess.mining.analysis import check_soundness
    from openprocess.mining.petrinet import PetriNet
    from openprocess.mining.pnml import read_pnml
    from openprocess.model.plain import from_petri_net

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.action_new_petri()
    page = window.current_page()
    assert isinstance(page, PetriNetPage)
    _pump(app, 0.2)
    scene, view = page.scene, page.view
    port = view.viewport()
    view.resetTransform()
    view.auto_fit = False

    def at(x: float, y: float) -> QPoint:
        return view.mapFromScene(QPointF(x, y))

    def add(tool: int, x: float, y: float, name: str) -> None:
        page.tool_switch.set_index(tool)
        QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, at(x, y))
        _pump(app, 0.05)
        assert page.tool_switch.index() == 0          # back to Select
        editor = view.name_editor
        assert editor is not None and editor.hasSelectedText()
        editor.setText(name)
        QTest.keyClick(editor, Qt.Key_Return)
        _pump(app, 0.05)

    def connect(a: tuple, b: tuple) -> None:
        page.tool_switch.set_index(4)                 # Arc (τ sits before it)
        QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, at(*a))
        QTest.mouseMove(port, at((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        QTest.mouseMove(port, at(*b))
        QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, at(*b))
        _pump(app, 0.02)

    add(1, 0, 0, "i")
    add(2, 100, 0, "a")
    add(1, 200, -60, "p1")
    add(1, 200, 60, "p2")
    add(2, 300, 0, "b")
    add(1, 400, 0, "o")
    names = sorted(p.name for p in page.net.all_places())
    assert names == ["i", "o", "p1", "p2"]
    connect((0, 0), (100, 0))
    connect((100, 0), (200, -60))
    connect((100, 0), (200, 60))            # a second arc out of the same transition
    connect((200, -60), (300, 0))
    connect((200, 60), (300, 0))
    connect((300, 0), (400, 0))
    assert len(page.net.pages[0].arcs) == 6
    # A place-to-place drag is refused, with a message.
    messages = []
    scene.message.connect(messages.append)
    connect((0, 0), (200, -60))
    assert len(page.net.pages[0].arcs) == 6 and "Can't connect two places" in messages[-1]

    # Double-click renames.
    QTest.mouseDClick(port, Qt.LeftButton, Qt.NoModifier, at(100, 0))
    _pump(app, 0.05)
    assert view.name_editor is not None
    view.name_editor.setText("register")
    QTest.keyClick(view.name_editor, Qt.Key_Return)
    _pump(app, 0.05)
    assert any(t.name == "register" for t in page.net.all_transitions())

    # Analysis: a sound WF-net (AND-split / AND-join).
    page.inspector_tabs.set_index(2)
    _pump(app, 0.5)
    assert check_soundness(page.petri_net()).sound is True

    # Deleting the arc p2 -> b makes p2 a second sink: no longer a WF-net.
    arc = next(a for a in page.net.pages[0].arcs
               if page.net.find_place(a.place_id).name == "p2" and a.orientation == "PtoT")
    page.reveal_element(arc.id)
    _pump(app, 0.05)
    page._delete_selection()
    _pump(app, 0.3)
    assert check_soundness(page.petri_net()).sound is False   # p2 became a second sink
    page.undo()
    _pump(app, 0.3)
    report = check_soundness(page.petri_net())
    assert report.sound is True and report.paths == []

    # A deadlocking net: choice (two transitions from i) into an AND-join.
    replay_buttons = []
    petri = PetriNet("xor-and")
    i, p1, p2, o = (petri.add_place(n) for n in ("i", "p1", "p2", "o"))
    a, b, c = (petri.add_transition(n) for n in ("a", "b", "c"))
    for s, t in ((i, a), (i, b), (a, p1), (b, p2), (p1, c), (p2, c), (c, o)):
        petri.add_arc(s, t)
    window.add_document(CpnDocument(from_petri_net(petri)))
    bad = window.current_page()
    _pump(app, 0.2)
    bad.inspector_tabs.set_index(2)
    for _ in range(100):
        _pump(app, 0.05)
        replay_buttons = [b for b in bad.soundness_card.findChildren(QPushButton)
                          if b.text().startswith("Show")]
        if replay_buttons:
            break
    assert replay_buttons, "a counterexample with a Show button"
    replay_buttons[0].click()
    _pump(app, 0.1)
    assert bad.mode_switch.index() == 1 and bad.simulator.step_count == 1
    assert bad._dead()                                   # the problem marking: stuck

    # The net's footprint is shown.
    assert bad.footprint_card.findChildren(QTableWidget)

    # Save as PNML and read it back.
    target = tmp_path / "drawn.pnml"
    assert page._write(target)
    again = read_pnml(target)
    assert sorted(p.name for p in again.places.values()) == ["i", "o", "p1", "p2"]
    assert len(again.arcs) == 6
    window.open_path(str(target))
    assert isinstance(window.current_page(), PetriNetPage)
    for p in list(window.pages.values()):
        if hasattr(p, "document"):
            p.document.dirty = False
    bad.document.dirty = False
    window.close()


def test_tau_tool_and_an_export_without_highlights(app, tmp_path, monkeypatch):
    """The τ tool (toolbar and Model palette) puts a silent transition on the
    canvas in one click, with no name to type; Export image draws the net as
    it is, without the green of the transitions enabled now or the selection
    ring, and the canvas gets them back afterwards."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    from openprocess.gui import theme
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.petri_page import PetriNetPage

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.action_new_petri()
    page = window.current_page()
    assert isinstance(page, PetriNetPage)
    _pump(app, 0.2)
    view, port = page.view, page.view.viewport()
    view.resetTransform()
    view.auto_fit = False

    def at(x: float, y: float):
        return view.mapFromScene(QPointF(x, y))

    assert [kind for kind, _text, _tip in page.TOOLS] == ["select", "place", "transition", "silent", "arc"]
    assert not window.palette_tools["silent"].isHidden()          # the palette has τ too
    window.palette_tools["silent"].click()
    assert page.scene.tool == "silent" and page.tool_switch.index() == 3
    QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, at(100, 0))
    _pump(app, 0.1)
    assert page.tool_switch.index() == 0                           # back to Select
    assert view.name_editor is None                                # nothing to name
    silent = [t for t in page.net.all_transitions() if t.silent]
    assert len(silent) == 1 and silent[0].name == ""
    assert page.scene.transition_items[silent[0].id].silent
    assert page.petri_net().transitions[silent[0].id].silent      # τ for the analysis too

    # A marked place feeding τ: τ is enabled, so it is drawn green.
    page.tool_switch.set_index(1)
    QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, at(0, 0))
    _pump(app, 0.05)
    QTest.keyClick(view.name_editor, Qt.Key_Return)
    _pump(app, 0.05)
    page.tool_switch.set_index(4)
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, at(0, 0))
    QTest.mouseMove(port, at(50, 0))
    QTest.mouseMove(port, at(100, 0))
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, at(100, 0))
    _pump(app, 0.05)
    place = next(iter(page.net.all_places()))
    page.reveal_element(place.id)
    page.tokens_spin.setValue(1)
    _pump(app, 0.1)
    tau_item = page.scene.transition_items[silent[0].id]
    assert tau_item.enabled_count == 1
    tau_item.setSelected(True)
    green = theme.palette().success

    def has_green(path) -> bool:
        image = QImage(str(path))
        assert not image.isNull()
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                colour = image.pixelColor(x, y)
                if all(abs(a - b) < 24 for a, b in ((colour.red(), green.red()), (colour.green(), green.green()),
                                                     (colour.blue(), green.blue()))):
                    return True
        return False

    target = tmp_path / "net.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "PNG")))
    page._export_image()
    assert target.exists() and not has_green(target)               # the net, not the session
    assert tau_item.enabled_count == 1 and tau_item.isSelected()   # the canvas as it was
    page.document.dirty = False
    window.close()


def test_petri_net_names_inside_or_outside(app, tmp_path):
    """Names sit inside the nodes by default; "Names outside" puts them
    underneath, where they can be dragged; both survive saving as PNML.
    Renaming opens a small editor over the name, and a hand-drawn
    transition shows in the Element tab."""
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.petri_page import PetriNetPage

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_petri()
    page = window.current_page()
    assert isinstance(page, PetriNetPage)
    _pump(app, 0.2)
    view, port = page.view, page.view.viewport()
    view.resetTransform()
    view.auto_fit = False

    for tool, x in ((1, -100.0), (2, 100.0)):
        page.tool_switch.set_index(tool)
        QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, view.mapFromScene(QPointF(x, 0)))
        _pump(app, 0.05)
        editor = view.name_editor
        assert editor is not None and editor.width() < 140     # snug, not a wide bar
        QTest.keyClick(editor, Qt.Key_Return)
        _pump(app, 0.05)
    place_item = next(iter(page.scene.place_items.values()))
    transition_item = next(iter(page.scene.transition_items.values()))

    # Inside (the default): the names are within the shapes.
    assert not page.names_box.isChecked()
    assert place_item.shape().contains(place_item.name_label.mapToParent(
        place_item.name_label.boundingRect().center()))

    # The Element tab shows a hand-drawn transition.
    page.scene.clearSelection()
    transition_item.setSelected(True)
    _pump(app, 0.05)
    assert page.element_card.title_label.text().startswith("Transition")

    # Outside: names under the nodes; dragging one keeps the spot.
    page.names_box.setChecked(True)
    _pump(app, 0.1)
    place_item = next(iter(page.scene.place_items.values()))
    label = place_item.name_label
    assert label.sceneBoundingRect().top() >= place_item.sceneBoundingRect().bottom() - 8
    start = view.mapFromScene(label.sceneBoundingRect().center())
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(port, start + QPointF(40, 20).toPoint())
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, start + QPointF(40, 20).toPoint())
    offset = place_item.place.graphics.label_offsets.get("name")
    assert offset is not None

    # Renaming now edits over the label, not over the circle.
    page.start_rename(place_item.place.id)
    _pump(app, 0.05)
    editor = view.name_editor
    label_centre = view.mapFromScene(page.scene.place_items[place_item.place.id]
                                     .name_label.sceneBoundingRect().center())
    assert abs(editor.geometry().center().y() - label_centre.y()) < 6
    view.close_editor()

    # Both survive a save and a reopen.
    target = tmp_path / "names.pnml"
    assert page._write(target)
    page.undo()                                             # the drag is undoable ...
    page.undo()                                             # ... and so is the setting
    assert not page.net.names_outside and not page.names_box.isChecked()
    page.document.dirty = False
    window.remove_documents([page.document.id])
    window.open_path(str(target))
    reopened = window.current_page()
    assert reopened.net.names_outside and reopened.names_box.isChecked()
    again = next(iter(reopened.net.all_places()))
    assert again.graphics.label_offsets.get("name") == pytest.approx(offset, abs=0.2)
    for p in list(window.pages.values()):
        if hasattr(p, "document") and hasattr(p.document, "dirty"):
            p.document.dirty = False
    window.close()


def test_dotted_chart_custom_colours(app):
    """Legend values can be given their own dot colour, and reset."""
    from PySide6.QtGui import QColor

    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.dotted_chart import DottedChartPanel
    from openprocess.mining import read_xes

    panel = DottedChartPanel(LogDocument(read_xes(DATA / "plane_wilma_10.xes")))
    panel.resize(1200, 700)
    panel.show()
    _pump(app, 0.2)
    data = panel.chart.data
    default = data.colour_hex(0)
    panel.set_colour(0, "#123456")
    assert panel.chart.data.colour_hex(0) == "#123456"
    swatch = panel.legend.item(0).icon().pixmap(14, 14).toImage().pixelColor(7, 7)
    assert swatch == QColor("#123456")
    image = panel.chart.grab()                    # paints with the new colour
    assert not image.isNull()
    panel.legend.setCurrentRow(1)
    assert panel.legend.currentItem().data(Qt.UserRole) == 1
    panel._reset_colours()
    assert panel.chart.data.colour_hex(0) == default
    panel.close()


def test_hover_arrow_draws_arcs(app):
    """Hovering near a node shows a translucent arrow; dragging it to another
    node adds an arc (with the Select tool, no mode switch needed)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.mining.petrinet import PetriNet
    from openprocess.model.plain import from_petri_net
    from openprocess.gui.studio.documents import CpnDocument

    petri = PetriNet("hover")
    for name, x in (("p1", 0.0), ("p2", 300.0)):
        petri.add_place(name).position = (x, 0.0)
    petri.add_transition("t1").position = (150.0, 0.0)
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.add_document(CpnDocument(from_petri_net(petri)))
    page = window.current_page()
    _pump(app, 0.2)
    scene, view, port = page.scene, page.view, page.view.viewport()
    view.resetTransform()
    view.auto_fit = False
    view.centerOn(QPointF(150, 0))
    _pump(app, 0.05)
    p1 = next(i for i in scene.place_items.values() if i.place.name == "p1")

    # Hover just right of p1: the arrow appears on that side.  (First a move
    # elsewhere: Qt sends no move event when the cursor is already there.)
    QTest.mouseMove(port, view.mapFromScene(p1.pos() + QPointF(-200, -200)))
    _pump(app, 0.02)
    QTest.mouseMove(port, view.mapFromScene(p1.pos() + QPointF(30, 0)))
    _pump(app, 0.05)
    handle = scene._handle
    assert handle is not None and handle.isVisible() and handle.node is p1
    assert handle.pos().x() > p1.pos().x() + p1.rect().width() / 2

    # Drag the arrow onto t1: a new arc p1 -> t1.
    start = view.mapFromScene(handle.pos())
    target = view.mapFromScene(QPointF(150, 0))
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(port, (start + target) / 2)
    QTest.mouseMove(port, target)
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, target)
    _pump(app, 0.05)
    arcs = page.net.pages[0].arcs
    assert len(arcs) == 1 and arcs[0].orientation == "PtoT"
    assert page.tool_switch.index() == 0                  # still the Select tool

    # Place to place is refused.
    messages = []
    scene.message.connect(messages.append)
    p1 = next(i for i in scene.place_items.values() if i.place.name == "p1")
    QTest.mouseMove(port, view.mapFromScene(p1.pos() + QPointF(0, 30)))
    _pump(app, 0.05)
    start = view.mapFromScene(scene._handle.pos())
    target = view.mapFromScene(QPointF(300, 0))
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(port, target)
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, target)
    assert len(page.net.pages[0].arcs) == 1 and "Can't connect two places" in messages[-1]

    # Far from every node: no arrow.
    QTest.mouseMove(port, view.mapFromScene(QPointF(150, 200)))
    _pump(app, 0.05)
    assert not scene._handle.isVisible()
    page.document.dirty = False
    window.close()


def test_dragging_the_arrow_to_empty_space_adds_the_other_node(app):
    """Letting go of the arrow over empty canvas adds a transition (from a
    place) or a place (from a transition) there, joined by an arc -- shown as
    a see-through preview while dragging, and undone in one step."""
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    from openprocess.gui.items import PlaceItem, TransitionItem
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import CpnDocument
    from openprocess.mining.petrinet import PetriNet
    from openprocess.model.plain import from_petri_net

    petri = PetriNet("chain")
    petri.add_place("p1").position = (0.0, 0.0)
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.add_document(CpnDocument(from_petri_net(petri)))
    page = window.current_page()
    _pump(app, 0.2)
    scene, view, port = page.scene, page.view, page.view.viewport()
    view.resetTransform()
    view.auto_fit = False
    view.centerOn(QPointF(150, 0))
    _pump(app, 0.05)
    net_page = page.net.pages[0]

    def drag_from(node, end: QPointF) -> None:
        QTest.mouseMove(port, view.mapFromScene(node.pos() + QPointF(-200, -200)))   # a real move next
        _pump(app, 0.02)
        QTest.mouseMove(port, view.mapFromScene(node.pos() + QPointF(node.rect().width() / 2
                                                                     + 12, 0)))
        _pump(app, 0.05)
        assert scene._handle.isVisible() and scene._handle.node is node
        start = view.mapFromScene(scene._handle.pos())
        QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(port, (start + view.mapFromScene(end)) / 2)
        QTest.mouseMove(port, view.mapFromScene(end))
        _pump(app, 0.02)

    # Place -> empty space: a preview of a transition, then the real thing,
    # lined up with p1 (dropped 5 units off level) and joined to it.
    p1 = next(iter(scene.place_items.values()))
    drag_from(p1, QPointF(160, 5))
    assert scene._ghost.isVisible() and scene._ghost.kind is TransitionItem
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, view.mapFromScene(QPointF(160, 5)))
    _pump(app, 0.05)
    assert len(net_page.transitions) == 1 and len(net_page.arcs) == 1
    t1 = net_page.transitions[0]
    assert (t1.graphics.x, -t1.graphics.y) == (160, 0)            # level with p1
    assert net_page.arcs[0].orientation == "PtoT"
    assert net_page.arcs[0].transition_id == t1.id
    assert view.name_editor is not None                         # name it straight away
    view.name_editor.setText("send")

    # Transition -> empty space (the click also finishes the name): a place.
    t_item = scene.transition_items[t1.id]
    drag_from(t_item, QPointF(320, 0))
    assert t1.name == "send"
    t_item = scene.transition_items[t1.id]
    assert scene._ghost.isVisible() and scene._ghost.kind is PlaceItem
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, view.mapFromScene(QPointF(320, 0)))
    _pump(app, 0.05)
    assert len(net_page.places) == 2 and len(net_page.arcs) == 2
    assert net_page.arcs[-1].orientation == "TtoP"
    assert net_page.arcs[-1].transition_id == t1.id
    view.close_editor()

    # One undo takes away the place and its arc together.
    page.undo()
    assert len(page.net.pages[0].places) == 1
    assert len(page.net.pages[0].arcs) == 1

    # Letting go right beside the node adds nothing (no preview there either).
    scene = page.scene
    p1 = next(iter(scene.place_items.values()))
    drag_from(p1, p1.pos() + QPointF(0, p1.rect().height() / 2 + 14))
    assert scene._ghost is None or not scene._ghost.isVisible()
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier,
                       view.mapFromScene(p1.pos() + QPointF(0, p1.rect().height() / 2 + 14)))
    _pump(app, 0.05)
    assert len(page.net.pages[0].transitions) == 1
    scene._cancel_connection()
    page.document.dirty = False
    window.close()


def test_petri_analysis_follows_the_paper_and_trace_highlights(app):
    """The Analysis tab shows Theorem 1 (N̄ live and bounded) and the §6
    structure checks; N̄ opens as a net of its own; stepping through lights
    up the path taken, and the Trace box switches that off."""
    from PySide6.QtWidgets import QLabel

    from openprocess.gui.studio import petri_page
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.model.examples import order_handling_unsound

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.open_example_net(order_handling_unsound())
    page = window.current_page()
    original = petri_page.run_in_background
    petri_page.run_in_background = lambda work, done, failed=None: done(work())
    try:
        page.run_analysis()
    finally:
        petri_page.run_in_background = original

    def texts(card):
        return " ".join(label.text() for label in card.findChildren(QLabel))

    theorem = texts(page.theorem_card)
    assert "Bounded" in theorem and "Unbounded ⇒ not sound" in theorem
    structure = texts(page.structure_card)
    assert "Free-choice" in structure and "ship and reject share c3" in structure
    assert "PT-handle" in structure and "S-coverable" in structure

    before = len(window.documents) if hasattr(window, "documents") else None
    page._open_short_circuited()
    closed = window.current_page()
    assert closed is not page
    assert any(t.name == "t*" for t in closed.net.all_transitions())
    if before is not None:
        assert len(window.documents) == before + 1
    closed.document.dirty = False

    # -- the trace while stepping through
    window.select_document(page.document) if hasattr(window, "select_document") else None
    page.mode_switch.set_index(1)
    page.trace_box.setChecked(True)
    for name in ("register", "check stock"):
        page._fire_transition(next(t for t in page.net.all_transitions() if t.name == name))
    items = {i.transition.name: i for i in page.scene.transition_items.values()}
    assert items["register"].trace_steps == [1] and items["check stock"].trace_steps == [2]
    assert items["check stock"].trace == 1.0 > items["register"].trace > 0
    assert items["ship"].trace_steps == []
    lit = [a for a in page.scene.arc_items.values() if a.trace > 0]
    assert len(lit) == 5                 # start→register→c1,c2 and c1→check stock→c3
    page.trace_box.setChecked(False)
    assert all(a.trace == 0 for a in page.scene.arc_items.values())
    assert items["register"].trace_steps == []
    page.trace_box.setChecked(True)
    page.mode_switch.set_index(0)        # editing: no trace
    assert items["register"].trace_steps == []
    page.document.dirty = False
    window.close()


def test_properties_show_their_definitions(app):
    """Hovering over a property shows its definition filled in for the net;
    clicking opens a pop-up whose links lead to related definitions; Help ▸
    Definitions lists them all."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from openprocess.gui.studio import petri_page
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.definition_view import show_reference
    from openprocess.gui.studio.widgets import Verdict
    from openprocess.model.examples import order_handling_unsound

    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.open_example_net(order_handling_unsound())
    page = window.current_page()
    original = petri_page.run_in_background
    petri_page.run_in_background = lambda work, done, failed=None: done(work())
    try:
        page.run_analysis()
    finally:
        petri_page.run_in_background = original

    verdicts = {v.definition.key: v for v in page.findChildren(Verdict)
                if getattr(v, "definition", None) is not None}
    for key in ("wf_net", "option_to_complete", "live", "bounded", "free_choice",
                "well_structured", "s_coverable", "soundness_theorem"):
        assert key in verdicts, key
    tip = verdicts["option_to_complete"].title.toolTip()
    assert "For this net" in tip and "start" in tip and "c4" in tip

    QTest.mouseClick(verdicts["free_choice"].title, Qt.LeftButton)
    popup = verdicts["free_choice"].last_popup
    assert "reject" in popup.browser.toPlainText()
    popup._link("def:preset")                         # follow "Builds on"
    assert popup.current.key == "preset" and popup.back_button.isVisibleTo(popup)
    popup._back()
    assert popup.current.key == "free_choice"
    popup.close()

    dialog = show_reference("sound", window)
    assert "Soundness theorem" in dialog.browser.toPlainText()
    dialog.close()
    page.document.dirty = False
    window.close()


def test_removing_a_petri_net_and_a_log_without_times(app, monkeypatch):
    """Regressions: removing a Petri net raised ValueError (its sidebar
    section was missing from the ordering), and the dotted chart of a log
    without timestamps raised AttributeError while it was being built."""
    import sys
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.dotted_chart import DottedChartPanel
    from openprocess.mining import EventLog, parse_simple_log

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *info: errors.append(info))
    window = StudioWindow()
    window.show()
    shared_axis = DottedChartPanel.shared.x_mode
    notation = LogDocument(EventLog.from_simple_log(parse_simple_log("[<a,b>^2, <a,c>]"), "L"))
    window.add_document(notation)
    page = window.current_page()
    page.tabs.set_index(3)                         # the dotted chart
    _pump(app, 0.3)
    assert errors == []
    assert page.findChild(DottedChartPanel).settings.x_mode == 4       # logical order
    assert DottedChartPanel.shared.x_mode == shared_axis               # the default is untouched

    root = Path(__file__).resolve().parents[1]
    window.open_path(str(root / "examples" / "petri" / "order_handling_sound.pnml"))
    net = window.documents[-1]
    window.remove_documents([net.id])
    assert net not in window.documents and net.id not in window.pages
    assert errors == []
    window.close()


def test_filter_dialog_opens_a_filtered_log(app):
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.filter_dialog import FilterDialog
    from openprocess.mining import EventLog, parse_simple_log

    window = StudioWindow()
    window.show()
    log = LogDocument(EventLog.from_simple_log(
        parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"), "L1"))
    window.add_document(log)
    page = window.current_page()
    dialog = FilterDialog(log, page)
    dialog.activities_box.setChecked(True)
    dialog.activity_mode.setCurrentIndex(2)                # remove cases with e
    for i in range(dialog.activity_list.count()):
        item = dialog.activity_list.item(i)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == "e" else Qt.Unchecked)
    _pump(app, 0.3)
    assert dialog.preview.text().startswith("Result: 5 of 6 cases")
    filtered = LogDocument(dialog.result_log())
    page.open_log.emit(filtered)
    assert window.documents[-1] is filtered and filtered.name == "L1 (filtered)"
    assert ("a", "e", "d") not in filtered.simple_log()
    window.close()


def test_substitution_transition_opens_its_subpage(app, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_hierarchy import hierarchical
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.io.cpn_writer import write_cpn

    net = hierarchical()
    net.compile()
    write_cpn(net, tmp_path / "hierarchy.cpn")
    window = StudioWindow()
    window.show()
    window.open_path(str(tmp_path / "hierarchy.cpn"))
    page = window.current_page()
    handle = page.net.pages[0].transitions[0]
    page._show_element(handle)
    assert not page.subpage_button.isHidden()
    page._open_subpage()
    _pump(app, 0.2)
    assert page.scene.page.name == "Handle"
    page.mode_switch.set_index(1)
    for _ in range(4):
        page._step(None)
    assert [r.binding.describe(page.net).split()[0] for r in page.simulator.log].count("pack") == 2
    window.close()


def test_clicking_away_from_the_name_box_keeps_the_name(app):
    """The box that opens to name a new node closes when you click anywhere
    else, as if Return were pressed -- also when the name was left as it is
    (Qt 6 only reports "editing finished" on focus loss after a change, so
    an untouched box used to stay open over the node)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.show()
    window.action_new_petri()
    page = window.current_page()
    net = page.net
    view = page.view
    _pump(app, 0.2)
    for tool, spot in ((1, QPoint(120, 150)), (2, QPoint(320, 150))):
        page.tool_switch.set_index(tool)                  # Place, then Transition
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, spot)
        _pump(app, 0.1)
        assert view.name_editor is not None and view.name_editor.isVisible()
        # Click on empty canvas without typing: the box closes, the name stays.
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(40, 380))
        _pump(app, 0.1)
        assert view.name_editor is None
    assert sorted(p.name for p in net.all_places()) == ["p1"]
    assert sorted(t.name for t in net.all_transitions()) == ["t1"]

    # Typed, then a click on a toolbar button: the typed name is kept.
    place = next(net.all_places())
    page.start_rename(place.id)
    _pump(app, 0.1)
    QTest.keyClicks(view.name_editor, "start")
    QTest.mouseClick(page.tool_switch.buttons[0], Qt.LeftButton)       # Select
    _pump(app, 0.1)
    assert view.name_editor is None
    assert place.name == "start"

    # Esc still cancels.
    page.start_rename(place.id)
    _pump(app, 0.1)
    QTest.keyClicks(view.name_editor, "other")
    QTest.keyClick(view.name_editor, Qt.Key_Escape)
    _pump(app, 0.1)
    assert view.name_editor is None and place.name == "start"
    page.document.dirty = False           # closing would otherwise ask to save
    window.close()


def test_save_as_names_the_net_after_its_file(app, tmp_path, monkeypatch):
    """Regression: Save As wrote the file but the net kept the name
    "Untitled 1" in the header, the sidebar and the window title."""
    from openprocess.gui.studio import cpn_page, petri_page
    from openprocess.gui.studio.app import APPLICATION_NAME, StudioWindow

    window = StudioWindow()
    window.show()

    # A new Petri net, saved as PNML through the (stubbed) save dialog.
    window.action_new_petri()
    page, document = window.current_page(), window.documents[-1]
    assert document.name.startswith("Untitled")
    target = tmp_path / "Example net week 4.pnml"
    monkeypatch.setattr(petri_page.QFileDialog, "getSaveFileName",
                        lambda *args, **kwargs: (str(target), "PNML Petri net (*.pnml)"))
    assert page.export()
    assert target.exists()
    assert document.name == "Example net week 4"
    assert window.items[document.id].text(0).strip() == "Example net week 4"
    assert window.windowTitle() == f"Example net week 4 — {APPLICATION_NAME}"
    # Reopening the file gives the same name.
    window.open_path(str(target))
    assert window.documents[-1].name == "Example net week 4"

    # The same for a new coloured net saved as .cpn.
    window.action_new_cpn()
    page, document = window.current_page(), window.documents[-1]
    target = tmp_path / "boarding model.cpn"
    monkeypatch.setattr(cpn_page.QFileDialog, "getSaveFileName",
                        lambda *args, **kwargs: (str(target), "CPN Tools model (*.cpn)"))
    assert page.export()
    assert document.name == "boarding model"
    assert window.windowTitle() == f"boarding model — {APPLICATION_NAME}"
    window.close()


def test_renaming_a_net_renames_its_file(app, tmp_path, monkeypatch):
    """Double-clicking the title (or a sidebar entry) renames the document;
    a net named after its file takes the file with it."""
    import shutil

    from PySide6.QtTest import QTest
    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import APPLICATION_NAME, StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining import EventLog, parse_simple_log

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "order.pnml"
    shutil.copy(root / "examples" / "petri" / "order_handling_sound.pnml", source)
    answers, warnings = [], []
    monkeypatch.setattr(studio_app.QInputDialog, "getText",
                        lambda *args, **kwargs: (answers.pop(0), True))
    monkeypatch.setattr(studio_app.QMessageBox, "warning",
                        lambda *args, **kwargs: warnings.append(args[2]))

    window = StudioWindow()
    window.show()
    window.open_path(str(source))
    document, page = window.documents[-1], window.current_page()
    assert document.name == "order"

    # Double-click on the page title -> rename -> the file follows.
    answers.append("order v2")
    QTest.mouseDClick(page.header.title, Qt.LeftButton)
    renamed = tmp_path / "order v2.pnml"
    assert document.name == "order v2" and document.path == str(renamed)
    assert renamed.exists() and not source.exists()
    assert window.windowTitle() == f"order v2 — {APPLICATION_NAME}"

    # Double-click in the sidebar does the same.
    answers.append("final")
    item = window.items[document.id]
    window.tree.itemDoubleClicked.emit(item, 0)
    assert (tmp_path / "final.pnml").exists() and document.name == "final"

    # An existing file is never overwritten, and characters files cannot have are refused.
    (tmp_path / "taken.pnml").write_text("keep me")
    answers.append("taken")
    window.rename_document(document)
    answers.append("a/b")
    window.rename_document(document)
    assert len(warnings) == 2 and document.name == "final"
    assert (tmp_path / "taken.pnml").read_text() == "keep me"

    # A log is named by its concept:name, not its file: only the name changes.
    log_file = tmp_path / "events.xes"
    from openprocess.mining.xes import write_xes
    write_xes(EventLog.from_simple_log(parse_simple_log("[<a,b>^2]"), "Boarding"), log_file)
    window.open_path(str(log_file))
    for _ in range(300):                       # logs are read in the background
        logs = [d for d in window.documents if isinstance(d, LogDocument)]
        if logs:
            break
        _pump(app, 0.02)
    log_doc = logs[0]
    assert log_doc.name == "Boarding"
    answers.append("Boarding (week 4)")
    window.rename_document(log_doc)
    assert log_doc.name == "Boarding (week 4)" and log_file.exists()
    window.close()


def test_workspace_folder(app, tmp_path, monkeypatch):
    """A folder as workspace: its files are listed (lighter until opened),
    open with one click, come back after closing, follow changes on disk,
    receive new nets, and the open files are restored next time."""
    import shutil

    from openprocess.gui.studio import petri_page
    from openprocess.gui.studio.app import APPLICATION_NAME, FILE_ROLE, StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes

    root = Path(__file__).resolve().parents[1]
    week = tmp_path / "Week 2"
    (week / "logs").mkdir(parents=True)
    shutil.copy(root / "examples" / "petri" / "order_handling_sound.pnml", week / "order.pnml")
    shutil.copy(root / "examples" / "simple_transfer.cpn", week / "transfer.cpn")
    write_xes(EventLog.from_simple_log(parse_simple_log("[<a,b>^2, <a,c>]"), "Boarding"),
              week / "logs" / "boarding.xes")
    (week / "notes.txt").write_text("not a model")

    def rows(section):
        return [section.child(i).text(0).strip() for i in range(section.childCount())]

    def wait_for(condition, seconds=6.0):
        for _ in range(int(seconds / 0.02)):
            if condition():
                return True
            _pump(app, 0.02)
        return condition()

    window = StudioWindow()
    window.show()
    # A file from elsewhere (saved, so closing it needs no confirmation).
    window.open_path(str(root / "examples" / "petri" / "order_handling_unsound.pnml"))
    assert window.open_workspace(str(week))

    # The outside file was closed.  Mine lists the folder's logs (folded under
    # their subfolder); Model lists its nets.
    assert [d.name for d in window.documents] == []
    assert window.sidebar_title.text() == "Week 2"
    assert not window.workspace_caption.isHidden()
    assert window.windowTitle() == f"Week 2 — {APPLICATION_NAME}"
    assert window.space == "mine" and window.analyses_section.isHidden()
    assert window.material_section.text(0) == "LOGS  ·  1"
    assert rows(window.material_section) == ["logs"]
    assert rows(window.folder_items["logs"]) == ["boarding"]
    assert window.folder_items["logs"].child(0).toolTip(0).startswith("logs/boarding.xes")
    assert window.set_space("model") and window.space_switch.index() == 1
    assert rows(window.models_section) == ["order", "transfer"]

    # One click opens a file; its row becomes the open document.
    window._open_placeholder(window.models_section.child(0))
    net = window.documents[-1]
    assert net.name == "order" and window.models_section.childCount() == 2
    assert window.models_section.child(0).data(0, FILE_ROLE) is None
    assert window.windowTitle() == "order — Week 2"
    # A double-click right after that click does not ask to rename it.
    window._on_double_click(window.items[net.id], 0)

    # A log opens in the background, in Mine.
    window.set_space("mine")
    window._open_placeholder(window.folder_items["logs"].child(0))
    assert wait_for(lambda: any(isinstance(d, LogDocument) for d in window.documents))
    assert window.space == "mine" and rows(window.folder_items["logs"]) == ["Boarding"]

    # Closing a file lists it again, ready to reopen.
    window.set_space("model")
    assert window.windowTitle() == "order — Week 2"          # Model remembers its document
    window.remove_documents([net.id])
    assert rows(window.models_section) == ["order", "transfer"]
    assert window.models_section.child(0).data(0, FILE_ROLE)

    # Files added in Finder appear by themselves (the folder is watched).
    shutil.copy(week / "order.pnml", week / "another net.pnml")
    assert wait_for(lambda: rows(window.models_section) == ["another net", "order", "transfer"])

    # A new net's Save dialog starts in the workspace folder.
    window.action_new_petri()
    offered = {}

    def save_dialog(_parent, _title, suggested, _filters):
        offered["path"] = suggested
        return str(week / "my model.pnml"), "PNML Petri net (*.pnml)"
    monkeypatch.setattr(petri_page.QFileDialog, "getSaveFileName", save_dialog)
    assert window.current_page().export()
    assert Path(offered["path"]).parent == week
    window._rescan_workspace()
    assert rows(window.models_section) == ["another net", "my model", "order", "transfer"]
    assert sum(1 for i in range(4) if window.models_section.child(i).data(0, FILE_ROLE)) == 3

    # What was open, and which space, is remembered in the folder and comes back next time.
    assert (week / ".openprocess").exists()
    window.close()
    again = StudioWindow()
    again.show()
    assert again.open_workspace(str(week))
    assert wait_for(lambda: len(again.documents) == 2)
    assert sorted(d.name for d in again.documents) == ["Boarding", "my model"]
    assert again.space == "model"
    assert again.windowTitle() == "my model — Week 2"          # it was selected
    again.set_space("mine")
    assert again.windowTitle() == f"Week 2 — {APPLICATION_NAME}"  # nothing was current in Mine

    # Closing the workspace closes everything and goes back to "OpenProcess Studio".
    assert again.close_workspace()
    assert again.documents == [] and again.sidebar_title.text() == APPLICATION_NAME
    assert all(s.isHidden() for s in (again.analyses_section, again.models_section,
                                      again.material_section))
    again.close()


def test_empty_workspace_says_so(app, tmp_path):
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.show()
    window.open_workspace(str(tmp_path))
    assert not window.empty_hint.isHidden()
    window.close_workspace()
    assert window.empty_hint.isHidden()
    window.close()


def _middle_drag(app, view, start, end, steps=4):
    """Press the middle button at ``start``, move to ``end`` and release."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    def send(kind, point, button, buttons):
        event = QMouseEvent(kind, QPointF(point), view.viewport().mapToGlobal(point).toPointF(),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), event)

    from PySide6.QtCore import QEvent, QPoint
    send(QEvent.MouseButtonPress, QPoint(*start), Qt.MiddleButton, Qt.MiddleButton)
    for step in range(1, steps + 1):
        point = QPoint(start[0] + (end[0] - start[0]) * step // steps,
                       start[1] + (end[1] - start[1]) * step // steps)
        send(QEvent.MouseMove, point, Qt.NoButton, Qt.MiddleButton)
    send(QEvent.MouseButtonRelease, QPoint(*end), Qt.MiddleButton, Qt.NoButton)
    app.processEvents()


def test_middle_drag_pans_the_net_editor(app):
    """Regression (#11): middle-drag panning did nothing (Qt's hand drag only
    starts on the left button), did nothing while the whole net was in view,
    and could leave the view stuck in hand-drag mode."""
    from PySide6.QtWidgets import QGraphicsView
    from openprocess.gui.studio.app import StudioWindow

    root = Path(__file__).resolve().parents[1]
    window = StudioWindow()
    window.resize(1300, 850)
    window.show()
    window.open_path(str(root / "examples" / "petri" / "order_handling_sound.pnml"))
    page = window.current_page()
    view = page.view
    _pump(app, 0.2)
    view.zoom_to_fit()                    # the whole net in view: nothing to scroll
    landmark = page.scene.itemsBoundingRect().center()     # a fixed point of the drawing

    def on_screen():
        point = view.mapFromScene(landmark)
        return point.x(), point.y()

    start_position = on_screen()
    for tool in (0, 1):                   # whatever tool is picked (Select, Place)
        page.tool_switch.set_index(tool)
        cursor = view.viewport().cursor().shape()
        before = on_screen()
        _middle_drag(app, view, (300, 300), (380, 340))
        # The drawing follows the pointer exactly.
        assert on_screen() == (before[0] + 80, before[1] + 40)
        assert view.dragMode() != QGraphicsView.ScrollHandDrag
        assert view.viewport().cursor().shape() == cursor      # the tool's cursor is back
    assert on_screen() == (start_position[0] + 160, start_position[1] + 80)

    # Over a node too: the node is not dragged, the view pans.
    node = next(iter(page.scene.place_items.values()))
    position = node.pos()
    start = view.mapFromScene(node.sceneBoundingRect().center())
    before = on_screen()
    _middle_drag(app, view, (start.x(), start.y()), (start.x() - 50, start.y() - 30))
    assert node.pos() == position and on_screen() == (before[0] - 50, before[1] - 30)

    # Zoom to fit brings the whole net back.
    view.zoom_to_fit()
    assert view.viewport().rect().contains(view.mapFromScene(page.scene.itemsBoundingRect()
                                                             ).boundingRect())
    window.close()


def _wheel(app, view, point, pixels=(0, 0), angle=(0, 0), modifiers=Qt.NoModifier):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    event = QWheelEvent(QPointF(*point), view.viewport().mapToGlobal(QPointF(*point)),
                        QPoint(*pixels), QPoint(*angle), Qt.NoButton, modifiers,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), event)
    app.processEvents()


@pytest.mark.parametrize("which", ["editor", "graph"])
def test_the_canvas_is_endless(app, which):
    """With the whole net in view, scrolling still pans (so there is room to
    start a new part beside it), Space + drag pans, and zooming keeps the point
    under the pointer where it is."""
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.graph_view import GraphView
    from openprocess.mining import alpha_miner, parse_simple_log
    from openprocess.gui.studio.graph_builders import petri_net_specs

    root = Path(__file__).resolve().parents[1]
    window = StudioWindow()
    window.resize(1300, 850)
    window.show()
    if which == "editor":
        window.open_path(str(root / "examples" / "petri" / "order_handling_sound.pnml"))
        view = window.current_page().view
        view.zoom_to_fit()
        landmark = view.scene().itemsBoundingRect().center()
    else:
        view = GraphView()
        view.resize(700, 500)
        view.show()
        net = alpha_miner(parse_simple_log("[<a,b,c>^2, <a,c,b>]")).net
        view.graph.populate(*petri_net_specs(net))
        view.fit()
        landmark = view.graph.itemsBoundingRect().center()
    _pump(app, 0.2)

    def on_screen():
        point = view.mapFromScene(landmark)
        return point.x(), point.y()

    before = on_screen()
    _wheel(app, view, (200, 200), pixels=(-120, -60))         # two-finger scroll
    assert on_screen() == (before[0] - 120, before[1] - 60)
    before = on_screen()
    _wheel(app, view, (200, 200), angle=(0, -120), modifiers=Qt.ShiftModifier)
    assert on_screen() == (before[0] - 40, before[1])          # Shift + wheel: sideways

    # Space + left drag pans, even over a node.
    view.setFocus()
    QApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))
    before = on_screen()
    start = QPoint(*before)

    def send(kind, point, button, buttons):
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            kind, QPointF(point), view.viewport().mapToGlobal(QPointF(point)), button, buttons,
            Qt.NoModifier))
    send(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, start + QPoint(30, 20), Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, start + QPoint(30, 20), Qt.LeftButton, Qt.NoButton)
    QApplication.sendEvent(view, QKeyEvent(QEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier, " "))
    assert on_screen() == (before[0] + 30, before[1] + 20)

    # ⌘-scroll zooms about the pointer: the point under it stays put.
    pointer = (150, 120)
    under = view.mapToScene(QPoint(*pointer))
    zoom = view.zoom()
    _wheel(app, view, pointer, angle=(0, 240), modifiers=Qt.ControlModifier)
    assert view.zoom() > zoom
    after = view.mapFromScene(under)
    assert abs(after.x() - pointer[0]) <= 1 and abs(after.y() - pointer[1]) <= 1

    # Panned right out of view: a hint points back to it, and brings it back.
    _pump(app, 0.1)
    assert view.offscreen_hint.isHidden()
    view.pan_by(-5000, 0)                        # the drawing is now far to the left
    _pump(app, 0.1)
    assert view.offscreen_hint.isVisible() and view.offscreen_hint.text().startswith("←")
    view.offscreen_hint.click()
    _pump(app, 0.1)
    assert view.offscreen_hint.isHidden()
    assert view.viewport().rect().intersects(
        view.mapFromScene(view.scene().itemsBoundingRect()).boundingRect())
    if which == "graph":
        view.close()
    window.close()


# ---------------------------------------------------------------------------
# Working in a folder: the Folder view, organising, two-way sync, bringing
# files in (issues #7, #8, #9, #10)
# ---------------------------------------------------------------------------
def _wait_for(app, condition, seconds: float = 6.0) -> bool:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return True
        _pump(app, 0.02)
    return condition()


def _week(tmp_path) -> Path:
    """A course folder: a net at the top, a log and a net in subfolders, an empty one."""
    import shutil

    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes

    root = Path(__file__).resolve().parents[1]
    week = tmp_path / "Week 2"
    (week / "logs").mkdir(parents=True)
    (week / "models").mkdir()
    (week / "empty").mkdir()
    shutil.copy(root / "examples" / "petri" / "order_handling_sound.pnml",
                week / "models" / "order.pnml")
    shutil.copy(root / "examples" / "simple_transfer.cpn", week / "transfer.cpn")
    write_xes(EventLog.from_simple_log(parse_simple_log("[<a,b>^2, <a,c>]"), "Boarding"),
              week / "logs" / "boarding.xes")
    return week


def _layout(window) -> list[str]:
    """The sidebar as text: one line per visible row, indented by depth."""
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    lines = []
    iterator = QTreeWidgetItemIterator(window.tree)
    while iterator.value() is not None:
        item = iterator.value()
        depth, parent, visible = 0, item.parent(), not item.isHidden()
        while parent is not None:
            depth += 1
            visible = visible and parent.isExpanded() and not parent.isHidden()
            parent = parent.parent()
        if visible:
            lines.append("  " * depth + item.text(0).strip())
        iterator += 1
    return lines


def test_each_space_lists_its_own_files_and_remembers_them(app, tmp_path):
    """Mine lists logs under LOGS (folded), Model lists the nets; in both, the
    list is flat, with each subfolder's files under a caption naming it, so
    nothing is nested and nothing moves between the spaces.  The space and
    the unfolded LOGS are remembered in the folder."""
    from openprocess.gui.studio.app import FOLDER_ROLE, GROUP_ROLE, StudioWindow
    from openprocess.gui.studio.workspace import Workspace

    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    assert window.space == "mine" and window.space_switch.index() == 0
    assert not window.footers["mine"].isHidden() and window.footers["model"].isHidden()
    # No analyses yet, so ANALYSES holds only the empty subfolder's caption (a
    # folder with nothing in it shows in both spaces, waiting); LOGS is folded.
    assert _layout(window) == ["ANALYSES", "  empty", "LOGS  ·  1"]
    window.material_section.setExpanded(True)
    assert _layout(window) == ["ANALYSES", "  empty", "LOGS  ·  1", "  logs", "    boarding"]
    caption = window.folder_items["logs"]
    assert caption.data(0, GROUP_ROLE) and caption.data(0, FOLDER_ROLE) == str(week.resolve() / "logs")
    # A click folds the subfolder; the fold is remembered in the folder and
    # survives a rebuild; another click unfolds it.
    window._sidebar_clicked(caption)
    assert not caption.isExpanded()
    assert _layout(window) == ["ANALYSES", "  empty", "LOGS  ·  1", "  logs"]
    assert Workspace(week).settings()["collapsed_folders"] == ["logs"]
    window._rebuild_sidebar()
    assert not window.folder_items["logs"].isExpanded()
    window._sidebar_clicked(window.folder_items["logs"])
    assert _layout(window) == ["ANALYSES", "  empty", "LOGS  ·  1", "  logs", "    boarding"]
    assert "collapsed_folders" not in Workspace(week).settings()

    # Model: the folder's own files first, then each subfolder's under its caption.
    window.set_space("model")
    assert not window.footers["model"].isHidden() and window.footers["mine"].isHidden()
    assert _layout(window) == ["MODELS", "  transfer", "  empty", "  models", "    order"]
    assert window.folder_items["models"].data(0, FOLDER_ROLE) == str(week.resolve() / "models")

    # Opening a file keeps it under its caption.
    window._open_placeholder(window.placeholders[window._key(week / "models" / "order.pnml")])
    assert window.documents[-1].name == "order"
    assert _layout(window) == ["MODELS", "  transfer", "  empty", "  models", "    order"]
    assert window.tree.currentItem() is window.items[window.documents[-1].id]
    for i in range(window.models_section.childCount()):          # the rows are laid out
        assert window.tree.visualItemRect(window.models_section.child(i)).height() > 0

    # The space and the unfolded LOGS are remembered in the folder.
    assert Workspace(week).settings() == {"material_expanded": True, "space": "model"}
    window.close()
    again = StudioWindow()
    again.show()
    again.open_workspace(str(week))
    assert again.space == "model" and again.space_switch.index() == 1
    again.set_space("mine")
    assert again.material_section.isExpanded()
    assert _layout(again) == ["ANALYSES", "  empty", "LOGS  ·  1", "  logs", "    boarding"]
    again.close()


def test_organising_files_from_the_sidebar(app, tmp_path, monkeypatch, fake_bin):
    """New Folder, drag to move (an open file follows), rename a folder with an
    open file in it, and Move to Bin (recoverable), with the disk matching."""
    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow

    week = _week(tmp_path)
    answers = []
    monkeypatch.setattr(studio_app.QInputDialog, "getText",
                        lambda *args, **kwargs: (answers.pop(0), True))
    monkeypatch.setattr(studio_app.QMessageBox, "exec", lambda box: box.setProperty(
        "clicked", True) or None)
    # Every "Move to Bin?" is answered yes.
    monkeypatch.setattr(studio_app.QMessageBox, "clickedButton",
                        lambda box: next(b for b in box.buttons()
                                         if box.buttonRole(b) == studio_app.QMessageBox.DestructiveRole))

    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    window.set_space("model")                      # the nets' space

    # New Folder, inside "models".
    answers.append("assignment 1")
    window.new_folder(str(week / "models"))
    assert (week / "models" / "assignment 1").is_dir()
    assert "models/assignment 1" in window.folder_items
    assert window.folder_items["models"].isExpanded()

    # Open order.pnml, then drag it into the new folder: the file moves, the
    # document stays open with its new path, even with unsaved edits.
    window.set_autosave(False)
    window.open_path(str(week / "models" / "order.pnml"))
    net, page = window.documents[-1], window.current_page()
    page.set_dirty(True)
    target = week / "models" / "assignment 1"
    window.tree.paths_dropped.emit([net.path], str(target), True)
    assert (target / "order.pnml").exists() and not (week / "models" / "order.pnml").exists()
    assert Path(net.path) == (target / "order.pnml").resolve() and net.dirty
    assert window.items[net.id].text(0) == "order  •"
    assert "order.pnml" in page.header.subtitle.text()
    assert window.items[net.id].parent() is window.folder_items["models/assignment 1"]

    # A file that is not open moves too, and dropping on a file means its folder.
    transfer = window.placeholders[window._key(week / "transfer.cpn")]
    window.tree.paths_dropped.emit([str(week / "transfer.cpn")],
                                   window._drop_folder(window.items[net.id]), True)
    assert (target / "transfer.cpn").exists() and not (week / "transfer.cpn").exists()
    del transfer

    # Renaming the folder takes the open file with it.
    answers.append("Assignment one")
    window.rename_path(str(target))
    renamed = week / "models" / "Assignment one"
    assert Path(net.path) == (renamed / "order.pnml").resolve()
    assert "models/Assignment one" in window.folder_items

    # Move to Bin: a file that is not open, then the folder with the open net.
    window.trash_paths([str(renamed / "transfer.cpn")])
    assert not (renamed / "transfer.cpn").exists() and (fake_bin / "transfer.cpn").exists()
    window.trash_paths([str(renamed)])
    assert not renamed.exists() and (fake_bin / "Assignment one" / "order.pnml").exists()
    assert net not in window.documents
    assert "models/Assignment one" not in window.folder_items
    window.close()


def test_changes_on_disk_reach_the_app(app, tmp_path):
    """Folder → app: an open file changed elsewhere is reloaded (or, with
    edits here, the user is asked); one deleted is marked missing; a new
    subfolder with a file in it is listed; the app's own saves are not
    mistaken for changes made elsewhere."""
    import shutil

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.pnml import read_pnml, write_pnml
    from openprocess.mining.xes import write_xes

    root = Path(__file__).resolve().parents[1]
    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    path = week / "models" / "order.pnml"
    window.open_path(str(path))
    net = window.documents[-1]
    before = net.net

    def arcs() -> int:
        return sum(len(page.arcs) for page in net.net.pages)
    sound_arcs = arcs()

    # Changed in another app (no edits here): reloaded by itself.
    other = read_pnml(str(root / "examples" / "petri" / "order_handling_unsound.pnml"))
    write_pnml(other, str(path))
    assert _wait_for(app, lambda: net.net is not before)
    assert arcs() == sound_arcs - 1
    assert "changed on disk — reloaded" in window.statusBar().currentMessage()
    assert window.current_page().document is net

    # The app's own save: nothing is reloaded.
    reloaded_page = window.pages[net.id]
    window.set_autosave(False)
    reloaded_page.set_dirty(True)
    assert reloaded_page.save()
    _pump(app, 1.0)
    assert window.pages[net.id] is reloaded_page

    # Changed elsewhere while it has edits here: the user is asked.
    reloaded_page.set_dirty(True)
    shutil.copy(root / "examples" / "petri" / "order_handling_sound.pnml", path)
    banner = window.banners[net.id]
    assert _wait_for(app, lambda: banner.isVisibleTo(window) and banner.kind == "changed")
    assert "changed on disk" in banner.text.text()
    reload_button = banner.buttons.itemAt(0).widget()
    reload_button.click()
    assert arcs() == sound_arcs and not net.dirty

    # Deleted in Finder: still open, marked missing, not recreated by autosave.
    window.set_autosave(True)
    path.unlink()
    assert _wait_for(app, lambda: net.missing)
    assert net in window.documents and not path.exists()
    assert window.items[net.id].font(0).italic()
    assert window.banners[net.id].kind == "missing"
    window.pages[net.id].set_dirty(True)
    _pump(app, 1.5)
    assert not path.exists()                   # autosave does not bring it back

    # A new subfolder made in Finder, with a log copied straight into it (listed in Mine).
    window.set_space("mine")
    (week / "week 3").mkdir()
    write_xes(EventLog.from_simple_log(parse_simple_log("[<x,y>]"), "New"),
              week / "week 3" / "fresh.xes")
    assert _wait_for(app, lambda: window._key(week / "week 3" / "fresh.xes")
                     in window.placeholders)
    assert "week 3" in window.folder_items
    net.dirty = False                          # (quitting would ask about the edits)
    window.close()


def test_the_app_keeps_the_folder_up_to_date(app, tmp_path):
    """App → folder: new nets and logs are files from the start, edits are
    autosaved, results go next to their source, and Keep saves a model."""
    from openprocess.gui.studio.app import AUTOSAVE_DELAY, StudioWindow
    from openprocess.gui.studio.documents import LogDocument, ModelDocument
    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.discovery.alpha import alpha_miner
    from openprocess.mining.pnml import read_pnml

    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))

    # A new Petri net is "Untitled 1.pnml" straight away, autosaved without a dot.
    window.action_new_petri()
    net, page = window.documents[-1], window.current_page()
    assert net.path and Path(net.path).name == "Untitled 1.pnml" and net.autosave
    from openprocess.model.net import Place
    page._checkpoint()
    net.net.pages[0].places.append(Place(name="start"))
    page._edited()
    assert net.dirty and window.items[net.id].text(0) == "Untitled 1"   # no dot
    assert "edited" not in page.header.subtitle.text()
    assert _wait_for(app, lambda: not net.dirty, AUTOSAVE_DELAY / 1000 + 2)
    assert [p.name for p in read_pnml(net.path).places.values()] == ["start"]
    _pump(app, 0.8)
    assert window.pages[net.id] is page          # its own save is not reloaded

    # Renaming the net renames the file; a new coloured net is a .cpn.
    import openprocess.gui.studio.app as studio_app
    original = studio_app.QInputDialog.getText
    studio_app.QInputDialog.getText = lambda *args, **kwargs: ("first net", True)
    try:
        window.rename_document(net)
    finally:
        studio_app.QInputDialog.getText = original
    assert (week / "first net.pnml").exists() and not (week / "Untitled 1.pnml").exists()
    window.action_new_cpn()
    assert Path(window.documents[-1].path).name == "Untitled 1.cpn"

    # A log typed in notation is saved as <name>.xes; a filtered log goes next to its source.
    window.add_document(LogDocument(EventLog.from_simple_log(
        parse_simple_log("[<a,b,c>^2, <a,c>]"), "L1")))
    assert (week / "L1.xes").exists() and window.documents[-1].path == str(week / "L1.xes")
    window.open_path(str(week / "logs" / "boarding.xes"))
    assert _wait_for(app, lambda: any(d.name == "Boarding" for d in window.documents))
    boarding = next(d for d in window.documents if d.name == "Boarding")
    filtered_log = EventLog.from_simple_log(parse_simple_log("[<a,b>^2]"), "Boarding (filtered)")
    window.pages[boarding.id].open_log.emit(LogDocument(filtered_log))
    assert (week / "logs" / "Boarding (filtered).xes").exists()

    # A discovered model waits for Keep, then goes next to its log.
    model = ModelDocument(alpha_miner(boarding.simple_log()).net, origin="α",
                          source_log=boarding)
    model.net.name = "α · Boarding"
    window.add_document(model)
    model_page = window.current_page()
    assert model.path is None and model_page.keep_button.isVisibleTo(window)
    assert window.items[model.id].parent() is window.unsaved_section
    assert window.tree.visualItemRect(window.items[model.id]).height() > 0
    model_page.keep_button.click()
    kept = week / "logs" / "α · Boarding.pnml"
    assert kept.exists() and Path(model.path) == kept and read_pnml(str(kept)).transitions
    assert not model_page.keep_button.isVisibleTo(window)

    # Turning autosave off brings the edited dot back.
    window.set_autosave(False)
    page.set_dirty(True)
    assert window.items[net.id].text(0) == "first net  •"
    window.set_autosave(True)
    assert _wait_for(app, lambda: not net.dirty, AUTOSAVE_DELAY / 1000 + 2)
    window.close()


def test_opening_a_file_from_outside_the_folder(app, tmp_path, monkeypatch, fake_bin):
    """#9: copy (the default), move or open in place; name clashes; the
    remembered choice; dropping onto a subfolder."""
    import shutil

    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.workspace import Workspace

    root = Path(__file__).resolve().parents[1]
    week = _week(tmp_path)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    source = downloads / "unsound.pnml"
    shutil.copy(root / "examples" / "petri" / "order_handling_unsound.pnml", source)

    asked = []

    class FakeImportDialog:
        choice_to_make, always = "copy", False

        def __init__(self, names, folder, parent=None, allow_open=True, default="copy"):
            asked.append((names, allow_open))
            from PySide6.QtWidgets import QCheckBox
            self.always = QCheckBox()
            self.always.setChecked(FakeImportDialog.always)

        def exec(self):
            return studio_app.QDialog.Accepted

        def choice(self):
            return FakeImportDialog.choice_to_make

    clash_answers = []
    monkeypatch.setattr(studio_app, "ImportDialog", FakeImportDialog)
    monkeypatch.setattr(studio_app, "ask_about_clash", lambda parent, target: clash_answers.pop(0))

    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))

    # Copy: the original stays, the copy in the folder is what opens.
    window.open_files([str(source)])
    assert source.exists() and (week / "unsound.pnml").exists()
    assert window.documents[-1].path == str(week / "unsound.pnml")
    assert asked[-1] == (["unsound.pnml"], True)

    # The same name again: keep both ("unsound 2.pnml"), or replace.
    clash_answers.append("keep")
    window.open_files([str(source)])
    assert (week / "unsound 2.pnml").exists()
    clash_answers.append("replace")
    (week / "unsound 2.pnml").write_text("old")
    window.open_files([str(source)])                 # "unsound.pnml" is open: replaced
    assert (fake_bin / "unsound.pnml").exists()       # the replaced one is in the Bin

    # Move, remembered for this folder ("Always do this").
    FakeImportDialog.choice_to_make, FakeImportDialog.always = "move", True
    other = downloads / "other.pnml"
    shutil.copy(source, other)
    window.open_files([str(other)])
    assert not other.exists() and (week / "other.pnml").exists()
    assert Workspace(week).settings()["import"] == "move"
    asked.clear()
    third = downloads / "third.pnml"
    shutil.copy(source, third)
    window.open_files([str(third)])
    assert asked == [] and (week / "third.pnml").exists() and not third.exists()

    # Open in place (the folder's choice changed back to asking).
    Workspace(week).update_settings(**{"import": None})
    FakeImportDialog.choice_to_make, FakeImportDialog.always = "open", False
    window.open_files([str(source)])
    assert window.documents[-1].path == str(source)
    assert window.items[window.documents[-1].id].parent() is window.elsewhere_section

    # Dropped from Finder onto a subfolder: it goes there ("open in place" is not offered).
    FakeImportDialog.choice_to_make = "copy"
    log = downloads / "events.xes"
    shutil.copy(week / "logs" / "boarding.xes", log)
    window.tree.paths_dropped.emit([str(log)], str(week / "logs"), False)
    assert (week / "logs" / "events.xes").exists() and asked[-1] == (["events.xes"], False)
    window.close()


def test_a_file_moved_in_finder_stays_open(app, tmp_path):
    """Regression: an open file moved into a subfolder outside the app was
    marked missing and listed again, unopened, in its new place."""
    import os

    from openprocess.gui.studio.app import StudioWindow

    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    window.open_path(str(week / "transfer.cpn"))
    net = window.documents[-1]
    os.rename(week / "transfer.cpn", week / "models" / "transfer.cpn")      # as Finder does
    target = (week / "models" / "transfer.cpn").resolve()
    assert _wait_for(app, lambda: Path(net.path).resolve() == target)
    _pump(app, 1.0)
    assert not net.missing and net in window.documents
    assert window._key(target) not in window.placeholders                   # no duplicate row
    window.close()


def test_recent_folder_buttons_on_the_welcome_page(app, tmp_path, monkeypatch):
    """Regression: clicking a recent folder on the welcome page raised
    TypeError (the button's "checked" argument replaced the folder)."""
    from PySide6.QtWidgets import QPushButton
    from openprocess.gui.studio.app import StudioWindow

    week = _week(tmp_path)
    window = StudioWindow()
    monkeypatch.setattr(window, "_recent_workspaces", lambda: [str(week)])
    window._refresh_welcome_recent()
    window.show()
    buttons = [b for b in window.welcome_recent.findChildren(QPushButton)
               if b.text() == "Week 2"]
    assert buttons
    buttons[0].click()
    assert window.workspace is not None and window.workspace.folder == week.resolve()
    window.close()


def test_autosave_that_cannot_write_says_so_once(app, tmp_path, monkeypatch):
    """Regression: when autosave could not write (a read-only folder, a full
    disk), every attempt popped up an error dialog, a second after each edit.
    Now a bar says so once, and the net keeps its "edited" dot until saved."""
    import os
    import stat

    from openprocess.gui.studio import cpn_page
    from openprocess.gui.studio.app import AUTOSAVE_DELAY, StudioWindow

    popups = []
    monkeypatch.setattr(cpn_page.QMessageBox, "critical",
                        lambda *args, **kwargs: popups.append(args[2]))
    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    window.open_path(str(week / "models" / "order.pnml"))
    net, page = window.documents[-1], window.current_page()
    assert net.autosave
    models = week / "models"
    models.chmod(stat.S_IRUSR | stat.S_IXUSR)             # read-only folder
    try:
        for _ in range(3):
            page.set_dirty(True)
            _pump(app, AUTOSAVE_DELAY / 1000 + 0.5)
        assert popups == []
        banner = window.banners[net.id]
        assert banner.isVisibleTo(window) and "could not be saved" in banner.text.text()
        assert net.dirty and not net.autosave
        assert window.items[net.id].text(0).endswith("•")
    finally:
        models.chmod(stat.S_IRWXU)
    net.dirty = False
    window.close()


def test_a_log_still_loading_stays_with_its_folder(app, tmp_path):
    """Regression: a log still loading in the background when the folder was
    switched (or closed) was added to the next folder anyway."""
    import shutil

    from openprocess.gui.studio.app import StudioWindow

    first, second = tmp_path / "A", tmp_path / "B"
    first.mkdir()
    second.mkdir()
    shutil.copy(DATA / "plane_wilma_10.xes", first / "log.xes")
    window = StudioWindow()
    window.show()
    window.open_workspace(str(first))
    window.open_path(str(first / "log.xes"))         # loads in the background
    window.open_workspace(str(second))
    _pump(app, 1.5)
    assert window.documents == []
    window.open_workspace(str(first))                # what was open comes back as usual
    window.open_path(str(first / "log.xes"))
    assert _wait_for(app, lambda: len(window.documents) == 1)
    window.close()


def test_snap_to_grid_and_tidy_make_neat_nets(app, monkeypatch):
    """Snap to grid puts new and dragged nodes on the dots; Snap All to Grid
    snaps a whole page (and undo puts it back); the box is one setting for
    every editor."""
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    from openprocess.gui.canvas import GRID_STEP, NetScene
    from openprocess.gui.studio.app import StudioWindow

    def on_grid(item) -> bool:
        return item.pos().x() % GRID_STEP == 0 and item.pos().y() % GRID_STEP == 0

    monkeypatch.setattr(NetScene, "snap_to_grid", False)
    root = Path(__file__).resolve().parents[1]
    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    window.open_path(str(root / "examples" / "petri" / "order_handling_sound.pnml"))
    first = window.current_page()
    window.action_new_petri()
    page = window.current_page()
    _pump(app, 0.2)
    view, port = page.view, page.view.viewport()
    view.resetTransform()
    view.auto_fit = False

    def at(x: float, y: float):
        return view.mapFromScene(QPointF(x, y))

    page.grid_box.setChecked(True)
    assert NetScene.snap_to_grid and first.grid_box.isChecked()       # every editor
    # A new place lands on the nearest dot.
    page.tool_switch.set_index(1)
    QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier, at(37, 45))
    _pump(app, 0.1)
    editor = view.name_editor
    if editor is not None:
        QTest.keyClick(editor, Qt.Key_Return)
    place = next(iter(page.scene.place_items.values()))
    assert on_grid(place) and (place.pos().x(), place.pos().y()) == (28, 56)
    # Dragging it goes dot to dot.
    page.tool_switch.set_index(0)
    QTest.mousePress(port, Qt.LeftButton, Qt.NoModifier, at(28, 56))
    QTest.mouseMove(port, at(60, 70))
    QTest.mouseMove(port, at(97, 125))
    QTest.mouseRelease(port, Qt.LeftButton, Qt.NoModifier, at(97, 125))
    _pump(app, 0.1)
    place = next(iter(page.scene.place_items.values()))
    assert on_grid(place) and place.pos().x() > 28

    # Snap All to Grid on a net drawn freely (the button), then undo.
    window._select_document(window.documents[0])
    scene = first.scene
    before = {k: (i.pos().x(), i.pos().y()) for k, i in scene.place_items.items()}
    first.tidy_button.click()
    assert all(on_grid(i) for i in [*scene.place_items.values(),
                                    *scene.transition_items.values()])
    first.undo()
    assert {k: (i.pos().x(), i.pos().y()) for k, i in first.scene.place_items.items()} == before
    first.snap_everything()
    assert window.documents[0].dirty                                  # an edit: autosaved/undoable
    first.grid_box.setChecked(False)
    assert not NetScene.snap_to_grid and not page.grid_box.isChecked()
    for document in window.documents:
        document.dirty = False
    window.close()


def test_hover_arrow_only_outside_the_node(app):
    """The arrow to draw an arc shows when the mouse is just outside a place or
    transition, not while it is over the node (where it was in the way)."""
    from PySide6.QtCore import QPointF
    from openprocess.gui.studio.app import StudioWindow

    root = Path(__file__).resolve().parents[1]
    window = StudioWindow()
    window.show()
    window.open_path(str(root / "examples" / "petri" / "order_handling_sound.pnml"))
    scene = window.current_page().scene
    for item in [next(iter(scene.place_items.values())),
                 next(iter(scene.transition_items.values()))]:
        scene._update_connect_handle(item.pos())                         # over the node
        assert scene._handle is None or not scene._handle.isVisible()
        half = item.rect().width() / 2
        scene._update_connect_handle(item.pos() + QPointF(half + 8, 0))  # just outside
        assert scene._handle.isVisible() and scene._handle.node is item
        assert scene._handle.opacity() < scene._handle.HOT              # faint until aimed at
    window.close()


def test_a_new_file_reusing_a_deleted_files_number_is_not_taken_for_it(app, tmp_path):
    """Regression (seen on Linux): a deleted open file's inode number was given
    to the next new file, which the app then took for the open file "moved"
    there -- its document followed it, and autosave could write over it.  A
    move keeps the size and time too; a new file with a reused number does not."""
    import os

    from openprocess.gui.studio.app import StudioWindow

    week = _week(tmp_path)
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    window.open_path(str(week / "models" / "order.pnml"))
    net = window.documents[-1]
    old = window._identities[net.id]
    (week / "models" / "order.pnml").unlink()
    fresh = week / "logs" / "fresh.pnml"
    fresh.write_text("<pnml/>")
    # As Linux would: the new file gets the deleted file's number.
    status = os.stat(fresh)
    window._identities[net.id] = (status.st_dev, status.st_ino, old[2], old[3])
    window._rescan_workspace(force=True)
    assert net.missing and Path(net.path).name == "order.pnml"
    assert window._key(fresh) in window.placeholders           # listed as itself
    net.dirty = False
    window.close()


def test_net_editor_fits_a_laptop_window_and_the_sidebar_hides(app):
    """On a laptop-sized window the net editor fits without scrolling sideways,
    so the divider between canvas and inspector can be dragged; the sidebar
    button hides the sidebar and the menu tick follows."""
    from PySide6.QtWidgets import QScrollArea
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1280, 800)
    window.show()
    window.action_new_petri()
    _pump(app, 0.3)
    page = window.current_page()
    holder = page.parentWidget()
    while not isinstance(holder, QScrollArea):
        holder = holder.parentWidget()
    assert holder.horizontalScrollBar().maximum() == 0

    splitter = page.splitter
    canvas, inspector = splitter.sizes()[1:]
    splitter.moveSplitter(splitter.handle(2).x() - 100, 2)
    assert splitter.sizes()[1:] == [canvas - 100, inspector + 100]

    assert window.sidebar_action.isChecked()
    window.toggle_sidebar()
    _pump(app, 0.1)
    assert window.sidebar.isHidden() and not window.sidebar_action.isChecked()
    assert splitter.sizes()[1] > canvas - 100                 # the canvas got the room
    window.sidebar_action.trigger()
    assert not window.sidebar.isHidden()
    window.close()


def test_notes_overlay(app, tmp_path):
    """✎ Notes (in the status bar) opens a card over the pages that moves and
    resizes; its text is kept with the folder that is open (outside a folder,
    in the settings)."""
    from PySide6.QtCore import QPoint
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.workspace import NOTES_FILE

    window = StudioWindow()
    window.resize(1200, 800)
    window.show()
    _pump(app, 0.05)
    notes = window.notes
    assert window.notes_button.isVisible() and not notes.is_open

    window.notes_button.click()
    assert notes.is_open and window.notes_button.isChecked()
    notes._drag(QPoint(80, -60))
    notes._resize(QPoint(-40, 50))
    moved = notes.card.geometry()
    assert notes.card.parentWidget().rect().contains(moved)
    window.resize(1100, 760)
    _pump(app, 0.05)
    assert notes.card.size() == moved.size()           # a resize of the window keeps it

    notes.editor.setPlainText("loose")
    window.open_workspace(str(tmp_path))
    assert notes.text() == ""                          # a folder has notes of its own
    notes.editor.setPlainText("M0 = [p1]")
    window.close_workspace()
    assert notes.text() == "loose"
    assert (tmp_path / NOTES_FILE).read_text(encoding="utf-8") == "M0 = [p1]"
    window.open_workspace(str(tmp_path))
    assert notes.text() == "M0 = [p1]"

    window.notes_action.trigger()
    assert not notes.is_open and not window.notes_button.isChecked()
    window.set_notes_button(False)
    assert not window.notes_button.isVisible()
    window.close()


def test_process_tree_drawn_top_down(app):
    """The Inductive Miner's tree reads as in the course: root on top, children
    left to right in order, plain lines, τ a leaf (not a black bar)."""
    from collections import Counter
    from openprocess.gui.studio.derivation_view import tree_specs
    from openprocess.mining.discovery.inductive import inductive_miner

    log = Counter({("a", "b", "d"): 3, ("a", "c", "d"): 2, ("a", "d"): 1})
    tree = inductive_miner(log).tree
    nodes, edges, positions = tree_specs(tree)
    root = nodes[0].id
    children = [e.target for e in edges if e.source == root]
    assert len(children) == len(tree.children)
    xs = [positions[c][0] for c in children]
    assert xs == sorted(xs)                                      # in order, left to right
    assert all(positions[c][1] > positions[root][1] for c in children)   # below the root
    assert min(xs) < positions[root][0] < max(xs)                # centred over them
    assert not any(e.arrow for e in edges)
    assert all(n.kind != "silent" for n in nodes)
    assert any(n.text == "τ" for n in nodes)


def test_log_tabs_fit_the_window(app):
    """Canvases on a log's tabs fill the window instead of asking for their whole
    graph's height (which made the page scroll), and the drawn process tree
    keeps its zoom bar inside the card."""
    from PySide6.QtWidgets import QComboBox, QScrollArea
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.derivation_view import TreeView
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining import read_xes

    window = StudioWindow()
    window.resize(1200, 760)
    window.show()
    window.add_document(LogDocument(read_xes(DATA / "plane_wilma_10.xes"),
                                    path=str(DATA / "plane_wilma_10.xes")))
    page = window.current_page()
    area = page.parentWidget()
    while not isinstance(area, QScrollArea):
        area = area.parentWidget()
    names = [b.text() for b in page.tabs.buttons]
    for name in ("Process map", "Discover"):              # Discover: the handoff card, in the studio
        page.tabs.set_index(names.index(name))
        _pump(app, 0.3)
    assert page.height() <= area.viewport().height()
    # Learn's log page runs the miners inline: its drawn process tree keeps its zoom bar in the card.
    from openprocess.gui.studio.log_page import LogPage
    inline = LogPage(LogDocument(read_xes(DATA / "plane_wilma_10.xes")))
    holder = QScrollArea()
    holder.setWidgetResizable(True)
    holder.setWidget(inline)
    holder.resize(1200, 760)
    holder.show()
    inline.tabs.set_index([b.text() for b in inline.tabs.buttons].index("Discover"))
    _pump(app, 0.3)
    for box in inline.findChildren(QComboBox):
        if box.findData("im") >= 0:
            box.setCurrentIndex(box.findData("im"))
    _pump(app, 2.0)
    assert inline.height() <= holder.viewport().height()
    tree = inline.findChildren(TreeView)[0]
    bar = tree.zoom_controls
    assert bar.isVisible() and tree.viewport().geometry().contains(bar.geometry())
    holder.close()
    window.close()


def test_the_name_box_follows_the_node(app):
    """The box that opens to name a new node stays over the node (#35).

    The first node makes the inspector fill in, the canvas gets a little
    shorter, and the view re-fits itself a moment later: the box used to stay
    where the node *was*.  It must also follow panning, zooming and a redraw
    of the net (a new item for the same node), and still rename the node."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_petri()
    page = window.current_page()
    view = page.view
    port = view.viewport()
    _pump(app, 0.3)

    def offset() -> int:
        """How far the box's centre is from the node's name, in pixels."""
        item = next(iter(page.scene.transition_items.values()))
        anchor = view.mapFromScene(item.name_anchor()[0])
        centre = view.name_editor.geometry().center()
        return abs(centre.x() - anchor.x()) + abs(centre.y() - anchor.y())

    page.tool_switch.set_index(2)                                     # Transition
    QTest.mouseClick(port, Qt.LeftButton, Qt.NoModifier,
                     QPoint(port.width() // 2 + 150, port.height() // 2 + 40))
    _pump(app, 0.02)
    assert view.name_editor is not None and offset() <= 2
    _pump(app, 0.5)                               # the deferred re-fit, if any
    assert view.name_editor is not None and offset() <= 2
    view.zoom_to_fit()                            # the window was resized
    assert offset() <= 2
    view.pan_by(120, 80)
    assert offset() <= 2
    view.zoom_by(1.5, around_centre=True)
    assert offset() <= 2 and view.name_editor.font().pointSize() > 12
    page.scene.rebuild()                          # the node is drawn afresh
    _pump(app, 0.05)
    assert view.name_editor is not None and offset() <= 2

    view.name_editor.setText("pay")
    QTest.keyClick(view.name_editor, Qt.Key_Return)
    _pump(app, 0.05)
    assert view.name_editor is None
    assert [t.name for t in page.net.all_transitions()] == ["pay"]
    page.document.dirty = False
    window.close()


def test_connections_view_and_the_model_palette(app, tmp_path):
    """Connections draws what flows in and out and the tools that plug in, from
    the library; in Model, a net page's tools are a palette in the sidebar."""
    from openprocess.gui.studio import connections
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.show()
    window.show_connections()
    assert window.content.currentWidget() is window.connections_page
    cards = window.connections_page.cards
    assert {"Event logs", "Petri nets", "PNML", "OpenProcess", "Your own boxes"} <= set(cards)
    tools = {t["name"]: t for t in connections.tools(window.library())}
    assert "pandas" in tools and "Describe log" in tools["pandas"]["boxes"]
    assert tools["pandas"]["name"] in cards

    window.action_new_petri()
    page = window.current_page()
    assert window.space == "model" and not window.palette.isHidden()
    assert page.tool_switch.isHidden()                       # the palette stands in for it
    window.palette_group.button(1).click()
    assert page.tool_switch.index() == 1 and page.scene.tool == "place"
    page.tool_switch.set_index(3)
    assert window.palette_group.checkedId() == 3
    window.toggle_sidebar(False, remember=False)
    assert not page.tool_switch.isHidden()                   # no sidebar: the page's own tools
    window.toggle_sidebar(True, remember=False)
    assert page.tool_switch.isHidden()
    window.set_space("mine")
    assert window.palette.isHidden()
    window.set_space("model")
    assert not window.palette.isHidden() and window.current_page() is page
    page.document.dirty = False
    window.close()


def test_the_studio_log_page_hands_discovery_to_the_canvas(app, tmp_path):
    """In the studio, the log page's Discover tab runs no miner: it builds an
    analysis in Mine with the log and the chosen miner, connected and run,
    so every step is shown.  (Learn keeps the inline miners.)"""
    from PySide6.QtWidgets import QComboBox

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument, WorkflowDocument
    from openprocess.gui.studio.log_page import LogPage
    from openprocess.mining import EventLog, parse_simple_log

    week = tmp_path / "Week 2"
    week.mkdir()
    window = StudioWindow()
    window.show()
    window.open_workspace(str(week))
    log = EventLog.from_simple_log(parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"), "L")
    window.add_document(LogDocument(log, notation="[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"))
    page = window.current_page()
    assert isinstance(page, LogPage) and page.discover_mode == "handoff"
    page.tabs.set_index(6)                                   # Discover
    _pump(app, 0.1)
    assert not [b for b in page.findChildren(QComboBox) if b.findData("im") >= 0]   # no inline miners
    page.discover_chooser.setCurrentIndex(page.discover_chooser.findData("alpha_miner"))
    page.discover_button.click()
    _pump(app, 0.2)
    analysis = window.current_page()
    assert isinstance(analysis.document, WorkflowDocument) and window.space == "mine"
    assert analysis.workflow.name == "Discover L"
    names = [analysis.workflow.spec(n).name for n in analysis.workflow.order()]
    assert names == ["Open log", "α-algorithm"] and len(analysis.workflow.edges) == 1
    miner = analysis.workflow.order()[1]
    assert analysis.selected == miner.id                     # its Result is open
    end = time.time() + 8
    while time.time() < end and (analysis.run is None or analysis._running):
        _pump(app, 0.02)
    assert analysis.run.result(miner).status == "done", analysis.run.result(miner).error
    # Learn's log page keeps the miners inline.
    inline = LogPage(LogDocument(log))
    assert inline.discover_mode == "inline"
    for document in list(window.documents):
        document.dirty = False
    window.close()


def test_a_tool_can_be_installed_from_connections(app, monkeypatch):
    """Install… on a tool's card runs pip for this app's Python in the
    background, after asking; the card says Installing…, then the boxes
    that need the tool are available."""
    from openprocess.gui.studio import app as studio_app, connections
    from openprocess.gui.studio.app import StudioWindow

    fake = {"name": "fakestats", "installed": False, "install": "pip install fakestats", "boxes": ["Fake box"]}
    monkeypatch.setattr(connections, "tools", lambda library: [fake])
    monkeypatch.setattr(connections, "can_install", lambda: True)
    monkeypatch.setattr(studio_app.QMessageBox, "question",
                        staticmethod(lambda *a, **k: studio_app.QMessageBox.Yes))
    ran = []

    def pip(command, on_line):
        ran.append(command)
        on_line("Collecting fakestats\n")
        on_line("Successfully installed fakestats\n")
        fake["installed"] = True
        return 0, "Successfully installed fakestats"
    monkeypatch.setattr(studio_app, "PIP_RUNNER", pip)

    window = StudioWindow()
    window.show()
    window.show_connections()
    page = window.connections_page
    assert "fakestats" in page.install_buttons
    arrived = []
    original = page.append_install_log
    monkeypatch.setattr(page, "append_install_log", lambda name, line: (arrived.append(line), original(name, line)))
    page.install_buttons["fakestats"].click()
    assert "fakestats" in page.installing                     # at once: Installing…
    assert "fakestats" not in page.install_buttons
    assert "fakestats" in page.log_views and page.log_views["fakestats"].toPlainText().startswith("$ ")
    end = time.time() + 6
    while time.time() < end and page.installing:
        _pump(app, 0.05)
    assert ran == [[studio_app.sys.executable, "-m", "pip", "install", "fakestats"]]
    assert [line.rstrip("\n") for line in arrived] == ["Collecting fakestats", "Successfully installed fakestats"]
    assert "fakestats" not in page.logs                       # installed: the log is done with
    assert not page.installing and "fakestats" not in page.install_buttons   # installed: no button
    assert "Installed fakestats" in window.statusBar().currentMessage()
    window.close()


def test_every_computed_card_links_to_its_code(app):
    """What a page computes on its own (a process map, a footprint, soundness,
    regions) is as open as a box: its card has a { } code link that opens the
    file, scrolled to the function."""
    from PySide6.QtWidgets import QPlainTextEdit, QPushButton

    from openprocess.gui.flow import viewers
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining import read_xes

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.add_document(LogDocument(read_xes(DATA / "plane_wilma_10.xes"), path=str(DATA / "plane_wilma_10.xes")))
    page = window.current_page()
    names = [b.text() for b in page.tabs.buttons]
    page.tabs.set_index(names.index("Footprint"))
    _pump(app, 0.2)
    links = [b for b in page.findChildren(QPushButton) if b.text() == "{ } code" and b.isVisible()]
    assert links
    links[0].click()
    _pump(app, 0.2)
    shown = viewers._windows[-1].findChild(QPlainTextEdit).toPlainText()
    assert "def footprint_of_log" in shown
    for dialog in list(viewers._windows):
        dialog.close()
    window.action_new_petri()
    net_page = window.current_page()
    net_page.inspector_tabs.set_index(2)                     # Analysis
    _pump(app, 0.3)
    texts = [b.text() for b in net_page.findChildren(QPushButton)]
    assert texts.count("{ } code") >= 6                      # soundness, theorem, structure, invariants, …
    assert not any("Conformance with a log" in t for t in texts)   # one route: Check against a log ›
    for document in window.documents:
        document.dirty = False
    window.close()


def test_the_command_palette_finds_and_runs_anything(app, tmp_path):
    """⌘K: documents, the folder's files, boxes, spaces and menu actions, all
    by typing; Enter runs the best match."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.command_palette import match

    week = tmp_path / "Week 2"
    week.mkdir()
    (week / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    window.action_new_workflow(None)
    page = window.current_page()
    entries = window.palette_entries()
    kinds = {e.kind for e in entries}
    assert {"Analysis", "File", "Box", "Space", "Action"} <= kinds
    assert [e.title for e in match(entries, "alpha")][0] == "α-algorithm"
    assert any(e.title == "orders" and e.kind == "File" for e in match(entries, "orders"))
    assert match(entries, "cite openprocess")[0].kind == "Action"
    assert match(entries, "zzzz") == []
    # Typing and Enter: a box lands on the current analysis.
    window.show_command_palette()
    palette = window.command_palette
    assert palette.isVisible()
    QTest.keyClicks(palette.search, "alpha")
    assert palette.matches[0].title == "α-algorithm"
    QTest.keyClick(palette.search, Qt.Key_Return)
    _pump(app, 0.2)
    assert not palette.isVisible()
    assert [page.workflow.spec(n).name for n in page.workflow.order()] == ["α-algorithm"]
    # A space, by name.
    window.show_command_palette()
    QTest.keyClicks(window.command_palette.search, "model")
    QTest.keyClick(window.command_palette.search, Qt.Key_Return)
    _pump(app, 0.2)
    assert window.space == "model"
    # A file: it opens.
    window.show_command_palette()
    QTest.keyClicks(window.command_palette.search, "orders")
    QTest.keyClick(window.command_palette.search, Qt.Key_Return)
    _pump(app, 0.3)
    assert any(d.name == "orders" or (d.path or "").endswith("orders.log.txt") for d in window.documents)
    for document in window.documents:
        document.dirty = False
    window.close()


def test_a_chip_is_a_painted_pill(app):
    """The status chips on cards draw themselves: round ends wherever they are shown."""
    from openprocess.gui.studio.widgets import Chip

    chip = Chip("not installed", fill="#fab219", ink="white")
    chip.show()
    app.processEvents()
    assert chip.height() >= 20 and chip.width() > 60
    image = chip.grab().toImage()
    inside = image.pixelColor(4, image.height() // 2).name()
    corner = image.pixelColor(0, 0).name()
    assert inside == "#fab219" and corner != "#fab219"          # fill inside, the window behind the corner
    chip.setText("ready")
    assert chip.sizeHint().width() < 70
    chip.set_state("good")
    app.processEvents()
    assert chip.grab().toImage().pixelColor(4, image.height() // 2).name() != "#fab219"


def test_an_exported_experiment_opens_as_a_folder_of_its_own(app, tmp_path, monkeypatch):
    """Drop (or Open…) the zip Export experiment wrote: it is unpacked into a
    folder next to it, the app works in that folder, and the analysis runs."""
    from openprocess.flow import Runner, Workflow
    from openprocess.flow.record import export_experiment
    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import WorkflowDocument

    wf = Workflow("Shared")
    a, b = wf.add("typed_log"), wf.add("alpha_miner")
    wf.connect(a, b)
    archive = export_experiment(wf, Runner().run(wf), tmp_path / "Shared.zip", tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    asked = []
    monkeypatch.setattr(studio_app, "EXPERIMENT_FOLDER_CHOOSER",
                        lambda parent, title, default: (asked.append((title, default)), str(home))[1])
    window = StudioWindow()
    window.show()
    window.open_files([str(archive)])
    _pump(app, 0.5)
    assert asked and "Shared.zip" in asked[0][0] and asked[0][1] == str(tmp_path)
    assert window.workspace is not None and Path(window.workspace.folder).resolve() == (home / "Shared").resolve()
    documents = [d for d in window.documents if isinstance(d, WorkflowDocument)]
    assert len(documents) == 1 and documents[0].path.endswith("Shared.cpnflow")
    page = window.pages[documents[0].id]
    end = time.time() + 15
    while time.time() < end and (page._running or page.run is None):
        _pump(app, 0.05)
    assert page.run is not None and all(page.run.status(n) == "done" for n in page.workflow.nodes.values())
    assert (home / "Shared" / "run.py").is_file()
    window.close()


def test_a_saved_box_file_is_reloaded_by_itself(app, tmp_path):
    """Save a file under boxes/ in an editor: the folder's boxes are loaded again
    and the message names the file (the file itself is watched, not only the folder)."""
    import os
    import textwrap
    from openprocess.gui.studio.app import StudioWindow

    boxes = tmp_path / "boxes"
    boxes.mkdir()
    source = textwrap.dedent('''
        from openprocess.flow import EventLog, Table, box

        @box
        def count_cases(log: EventLog) -> Table:
            """How many cases the log has."""
            return Table("cases", ["cases"], [[len(log)]])
    ''')
    file = boxes / "mine.py"
    file.write_text(source, encoding="utf-8")
    window = StudioWindow()
    window.show()
    assert window.open_workspace(str(tmp_path))
    _pump(app, 0.3)
    window._rescan_workspace()
    assert str(file) in window.watcher.files()
    window.library().resolve("count_cases")
    file.write_text(source.replace("count_cases", "count_traces"), encoding="utf-8")
    stat = file.stat()
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    window.statusBar().clearMessage()
    window._file_changed(str(file))
    _pump(app, 0.8)                                            # the file timer, then the rescan timer
    window.library().resolve("count_traces")
    assert "mine.py" in window.statusBar().currentMessage()
    window.close()
