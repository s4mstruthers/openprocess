"""The Workflows page, offscreen: boxes run and show their status, the side
panel's four tabs, settings re-run only what follows, wires are dragged and
refused, files are saved and reopened, and custom boxes wait for an OK."""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLabel  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    # Under load one processEvents can outlast the budget; drain what came due
    # meanwhile (a page's deferred fit, say), so the test sees a settled window.
    for _ in range(3):
        app.processEvents()


def _wait_run(app, page, seconds: float = 6.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if page.run is not None and not page._running:
            return
        time.sleep(0.02)
    raise AssertionError("the workflow did not finish running")


def _node(page, suffix: str):
    return next(n for n in page.workflow.nodes.values() if n.box.endswith(suffix))


def test_workflow_page_runs_and_shows_every_tab(app):
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[1][1])                     # Compare discovery
    page = window.current_page()
    assert isinstance(page, WorkflowPage) and len(page.workflow.nodes) == 9
    _wait_run(app, page)
    assert page.run.ok, [r.error for r in page.run.failed()]
    assert {item.status for item in page.scene.boxes.values()} == {"done"}
    compare = _node(page, "compare")
    assert page.scene.boxes[compare.id].subtitle == "table 3 × 5"
    for tab in range(4):
        page.select(compare.id, tab)
        _pump(app, 0.1)
        texts = [w.text() for w in page.panel_host.findChildren(QLabel)]
        assert any("Compare" in t for t in texts)
    page.select(_node(page, "alpha_miner").id, 1)                 # How: the eight steps
    _pump(app, 0.1)
    html = " ".join(w.text() for w in page.panel_host.findChildren(QLabel))
    assert "T_L" in html and "Footprint" in html.upper() or "FOOTPRINT" in html.upper()
    page.select(_node(page, "alpha_miner").id, 2)                 # Code
    _pump(app, 0.1)
    from PySide6.QtWidgets import QPlainTextEdit, QPushButton
    edits = [w.toPlainText() for w in page.panel_host.findChildren(QPlainTextEdit)]
    assert "def alpha_miner(log: EventLog)" in edits[0]                 # the box: a thin wrapper
    labels = " ".join(w.text() for w in page.panel_host.findChildren(QLabel))
    assert "This box" in labels and "The algorithms it calls" in labels     # α calls two functions
    assert "openprocess/mining/discovery/alpha.py" in labels
    assert "alpha_miner is" in labels and "lines; the other" in labels   # the snippet is one function of the file
    assert "Follows" in labels and "Weijters" in labels                 # the work the code follows
    assert any("def alpha_miner(log: SimpleLog" in text for text in edits)   # the algorithm itself
    assert any("def footprint_of_log" in text for text in edits)
    from openprocess.gui.flow import viewers
    whole = next(b for b in page.panel_host.findChildren(QPushButton) if b.objectName() == "algorithmFile")
    whole.click()
    _pump(app, 0.1)
    assert viewers._windows and viewers._windows[-1].isVisible()
    shown = viewers._windows[-1].findChild(QPlainTextEdit).toPlainText()
    assert shown.startswith('"""The α-algorithm') and "def alpha_miner(log: SimpleLog" in shown
    page.pop_out_tab()
    _pump(app, 0.1)
    assert viewers._windows and viewers._windows[-1].isVisible()
    for dialog in list(viewers._windows):          # leave no window behind for the next test
        dialog.close()
    page.document.dirty = False                   # closing would otherwise ask to save
    window.close()


def test_changing_a_setting_reruns_only_what_follows(app):
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.show()
    window.action_new_workflow(TEMPLATES[1][1])
    page = window.current_page()
    _wait_run(app, page)
    heuristics = _node(page, "heuristics_miner")
    page.select(heuristics.id, 3)
    _pump(app, 0.1)
    spin = page.panel_host.findChild(QDoubleSpinBox)
    assert spin is not None and spin.value() == pytest.approx(0.9)
    spin.setValue(0.5)
    _pump(app, 0.6)                                                # the 400 ms debounce
    _wait_run(app, page)
    assert heuristics.settings["dependency"] == pytest.approx(0.5)
    cached = {page.workflow.title(n): page.run.result(n).cached for n in page.workflow.order()}
    assert cached["α-algorithm"] and cached["Top variants"] and not cached["Heuristics Miner"]
    assert not cached["Compare"]
    page.document.dirty = False
    window.close()


def test_dragging_a_wire_connects_and_refuses(app):
    from openprocess.flow.workflow import Workflow
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.studio.documents import WorkflowDocument

    wf = Workflow("drag")
    log = wf.add("typed_log", {"text": "[<a,b>^2, <a,c>]"}, (0, 0))
    miner = wf.add("inductive_miner", {}, (300, 0))
    fit = wf.add("check_fit", {}, (600, 0))
    page = WorkflowPage(WorkflowDocument(wf), wf.library)
    page.resize(1300, 700)
    page.show()
    _pump(app, 0.2)
    page.view.resetTransform()
    page.view.auto_fit = False
    page.view.centerOn(300, 50)
    view, scene = page.view, page.scene
    refused = []
    scene.refused.connect(refused.append)

    def drag(from_node, to_node, input_name=None):
        start = view.mapFromScene(scene.boxes[from_node.id].scene_port("out", "out"))
        target_item = scene.boxes[to_node.id]
        end = view.mapFromScene(target_item.scene_port("in", input_name) if input_name
                                else target_item.mapToScene(target_item.rect().center()))
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(view.viewport(), QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        _pump(app, 0.05)

    drag(log, miner)                                                # a log into the miner
    assert [(e.source, e.target, e.input) for e in wf.edges] == [(log.id, miner.id, "log")]
    drag(miner, fit, "model")
    drag(log, fit, "log")
    assert len(wf.edges) == 3
    drag(miner, fit, "log")                                         # a net into the log input
    assert len(wf.edges) == 3 and refused and "takes a log, not a petri net" in refused[-1]
    _wait_run(app, page)
    assert page.run.ok
    # Delete removes a selected wire; the box after it waits.
    wire = next(w for w in scene.wires if w.edge.target == fit.id and w.edge.input == "model")
    scene.clearSelection()
    wire.setSelected(True)
    scene.remove_selected()
    assert len(wf.edges) == 2
    _wait_run(app, page)
    assert page.run.status(fit) == "idle" and "Connect a petri net" in page.run.result(fit).message
    page.close()


def test_workflow_files_in_a_folder(app, tmp_path):
    from openprocess.flow.record import load
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    (tmp_path / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    window = StudioWindow()
    window.show()
    assert window.open_workspace(str(tmp_path))
    _pump(app, 0.3)
    window.action_new_workflow(TEMPLATES[0][1])                     # Discover and check, on the folder's log
    page = window.current_page()
    document = page.document
    assert document.path and document.path.endswith(".cpnflow") and Path(document.path).parent == tmp_path
    assert str(_node(page, "open_log").settings["file"]) == "orders.log.txt"
    _wait_run(app, page)
    assert page.run.ok, [r.error for r in page.run.failed()]
    page.save()
    workflow, record = load(document.path, page.library)
    assert len(record.results) == 3 and record.inputs[0]["file"] == "orders.log.txt"
    # Close and reopen from the sidebar: the same workflow, run again, the same numbers.
    window.remove_documents([document.id], confirm=False)
    window.open_path(document.path)
    again = window.current_page()
    assert isinstance(again, WorkflowPage) and len(again.workflow.nodes) == 3
    _wait_run(app, again)
    fit = _node(again, "check_fit")
    assert again.run.value(fit).metrics["fitness"] == pytest.approx(1.0)
    # The record notices a changed log before Re-run.
    (tmp_path / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,d>]", encoding="utf-8")
    from openprocess.flow.record import differences
    assert any("orders.log.txt" in line for line in differences(again.document.record, again.workflow, tmp_path))
    again.document.dirty = False
    window.close()


def test_custom_boxes_wait_for_an_ok(app, tmp_path):
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    boxes = tmp_path / "boxes"
    boxes.mkdir()
    (boxes / "mine.py").write_text(textwrap.dedent('''
        from openprocess.flow import box, EventLog, TransitionSystem
        from openprocess.mining.transition_system import transition_system_from_log

        @box(group="Discover")
        def last_two(log: EventLog) -> TransitionSystem:
            """The last two activities as the state."""
            return transition_system_from_log(log.simple_log(), "prefix", "multiset", 2)
    '''), encoding="utf-8")
    window = StudioWindow()
    window.show()
    assert window.open_workspace(str(tmp_path))
    _pump(app, 0.3)
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    spec = page.library.resolve("last_two")
    assert spec.custom and page.custom_bar.isVisible()
    log = _node(page, "open_log") if "open_log" in [n.box.rsplit(".", 1)[-1] for n in page.workflow.nodes.values()] \
        else _node(page, "typed_log")
    node = page.scene.add_node(spec.id, QPointF(300, 300))
    page.workflow.connect(log, node)
    page.scene.rebuild()
    page.run_from([node.id])
    _wait_run(app, page)
    assert page.run.status(node) == "blocked" and "OK" in page.run.result(node).message
    page.allow_custom()
    _wait_run(app, page)
    assert page.run.status(node) == "done" and window.workspace.settings().get("boxes_allowed") is True
    page.document.dirty = False
    window.close()


def test_groups_show_as_one_box_and_open_their_own_canvas(app, tmp_path):
    from openprocess.gui.flow.canvas import GroupItem
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.flow.templates import compare_discovery
    from openprocess.gui.studio.documents import WorkflowDocument
    from openprocess.flow.library import library_for

    library = library_for(tmp_path)
    wf = compare_discovery(library)
    page = WorkflowPage(WorkflowDocument(wf), library, tmp_path)
    page.resize(1300, 760)
    page.show()
    page.run_from(None)
    _wait_run(app, page, 15)
    alpha, fit = _node(page, "alpha_miner"), [n for n in wf.nodes.values() if n.box.endswith("check_fit")][0]
    page.scene.clearSelection()
    page.scene.boxes[alpha.id].setSelected(True)
    page.scene.boxes[fit.id].setSelected(True)
    page.group_selected()
    _pump(app, 0.1)
    group = next(iter(wf.groups.values()))
    assert group.members == [alpha.id, fit.id] or set(group.members) == {alpha.id, fit.id}
    assert alpha.id not in page.scene.boxes and group.id in page.scene.groups
    item = page.scene.groups[group.id]
    assert isinstance(item, GroupItem) and item.status == "done"
    inputs, outputs = wf.group_ports(group)
    assert {p.name for _, _, p in inputs} == {"log"} and len(inputs) == 2 and len(outputs) == 1
    # The side panel shows the group, with its Python.
    page.select(group.id)
    _pump(app, 0.1)
    texts = " ".join(w.text() for w in page.panel_host.findChildren(QLabel))
    assert "Workflow · 2 boxes" in texts
    # Open it: the members, with stubs for the wires that come from outside.
    page.open_group(group.id)
    _pump(app, 0.1)
    assert page.scene.view_group == group.id and set(page.scene.boxes) == {alpha.id, fit.id}
    assert any(w.stub and w.stub.startswith("from") for w in page.scene.wires)
    assert any(w.stub and w.stub.startswith("to") for w in page.scene.wires)
    page.close_group()
    assert page.scene.view_group is None
    # Save as a box: a @workflow file in boxes/, which the library lists under Yours.
    page.save_group_as_box(group.id)
    _pump(app, 0.2)
    files = list((tmp_path / "boxes").glob("*.py"))
    assert len(files) == 1 and "@workflow" in files[0].read_text(encoding="utf-8")
    spec = next(s for s in page.library if s.custom)
    assert spec.name == group.name and [p.name for p in spec.inputs] == ["log", "log_2"]
    # Ungroup puts the boxes back.
    page.scene.ungroup(group.id)
    assert not wf.groups and alpha.id in page.scene.boxes
    page.close()


def test_the_page_opens_calm_and_the_panel_comes_with_a_click(app):
    """A workflow opens as its canvas alone: three header buttons, no box
    list, no side panel.  Click a box and the panel appears on Result; the
    tab sticks while it stays open; ✕ (or clicking the canvas) hides it."""
    from PySide6.QtWidgets import QPushButton

    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    header = [b.text() for b in page.header.findChildren(QPushButton)
              if b.isVisible() and b.objectName() != "reproBadge"]
    assert header == ["Canvas", "Summary", "Runs", "+ Add box", "Run ▶", "⋯"]
    assert [a.text() for a in page.more_menu.actions() if a.text()] == [
        "Re-run", "Record…", "Export experiment…", "Export report…", "Save"]
    assert not page.panel.isVisible() and not hasattr(page, "box_tree")
    log, miner = page.workflow.order()[:2]
    page.select(log.id)
    assert page.panel.isVisible() and page.tab == 0
    page.tabs.set_index(2)                                          # Code
    _pump(app, 0.1)
    page.select(miner.id)
    assert page.tab == 2                                            # the tab sticks while the panel is open
    page.close_panel()
    assert not page.panel.isVisible() and page.selected is None
    page.select(log.id, 3)                                          # sent to Settings on purpose
    assert page.panel.isVisible() and page.tab == 3
    page.scene.clearSelection()                                     # a click on the canvas
    page.scene.selected.emit(None)
    assert not page.panel.isVisible()
    page.select(miner.id)
    assert page.tab == 0                                            # reopened: Result again
    page.document.dirty = False
    window.close()


def test_add_box_lists_the_core_groups_and_finds_the_rest(app):
    """+ Add box shows the six core groups; Show all adds the others; a
    search looks everywhere; a click adds the box to the canvas."""
    from openprocess.gui.flow.picker import CORE_GROUPS
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow()
    page = window.current_page()
    page.add_box_menu()
    picker = page._picker
    _pump(app, 0.1)
    assert picker.isVisible()
    assert list(picker.shown_groups()) == [g for g in CORE_GROUPS if g in page.library.by_group()]
    assert "Coloured nets" not in picker.shown_groups() and picker.more_button.text().startswith("Show all")
    picker.more_button.click()
    assert "Coloured nets" in picker.shown_groups() and "Sweep" in picker.shown_groups()
    picker.more_button.click()
    picker.search.setText("alpha")
    shown = picker.shown_groups()
    assert list(shown) == ["Discover"] and [s.name for s in shown["Discover"]] == ["α-algorithm"]
    assert not picker.more_button.isVisible()                       # a search looks everywhere already
    picker.search.setText("simulate cpn")
    assert "Coloured nets" in picker.shown_groups()
    picker.search.setText("Typed log")
    picker.buttons[0].click()
    _pump(app, 0.1)
    assert not picker.isVisible()
    assert [page.workflow.spec(n).name for n in page.workflow.order()] == ["Typed log"]
    # A double-click on the canvas: the same picker, the box lands there.
    page.quick_add(QPointF(400, 300))
    next(b for b in page._picker.buttons if b.text() == "Footprint").click()
    _pump(app, 0.1)
    from openprocess.gui.flow.canvas import GRID
    footprint = next(n for n in page.workflow.nodes.values() if page.workflow.spec(n).name == "Footprint")
    assert abs(footprint.position[0] - 311) <= GRID and abs(footprint.position[1] - 270) <= GRID
    page.document.dirty = False
    window.close()


def test_the_pages_no_longer_offer_as_a_workflow(app):
    """File ▸ New Workflow is the one way in: the log, model and net pages
    have no *As a workflow* / *Use in a workflow* buttons."""
    from PySide6.QtWidgets import QPushButton

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining import read_xes

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    path = Path(__file__).parent / "data" / "plane_wilma_10.xes"
    window.add_document(LogDocument(read_xes(path), path=str(path)))
    log_page = window.current_page()
    log_page.tabs.set_index(6)                                      # Discover: the handoff to Mine
    _pump(app, 0.8)
    texts = [b.text() for b in log_page.findChildren(QPushButton)]
    assert any(t.startswith("Discover in Mine") for t in texts)
    assert not any("workflow" in t.lower() for t in texts)
    window.action_new_cpn()
    net_page = window.current_page()
    assert not any("workflow" in b.text().lower() for b in net_page.header.findChildren(QPushButton))
    for document in window.documents:
        document.dirty = False
    window.close()


def test_a_log_result_offers_the_log_page(app):
    """A log box's Result says where the dotted chart and the footprint are:
    one *Open as log ›* card above its variants, which opens a log page."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.flow.viewers import _OpenCard
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.log_page import LogPage

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    log = page.workflow.order()[0]
    page.select(log.id, 0)
    _pump(app, 0.1)
    cards = page.panel_host.findChildren(_OpenCard)
    assert len(cards) == 1 and "Dotted chart" in cards[0].toolTip() and "footprint" in cards[0].toolTip()
    cards[0].slot()
    _pump(app, 0.3)
    assert isinstance(window.current_page(), LogPage)
    for document in window.documents:
        document.dirty = False
    window.close()


def test_a_failed_box_says_what_went_wrong_in_its_own_words(app, tmp_path, monkeypatch):
    """Open dataset without the file in the cache: the message and its links
    come first, the traceback under Details."""
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    from openprocess.flow import datasets
    from openprocess.gui.flow.page import _error_message, _linkified

    from PySide6.QtCore import QPointF

    from openprocess.gui.studio.app import StudioWindow

    monkeypatch.setattr(datasets, "cache_dir", lambda: tmp_path)
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow()                                    # empty
    page = window.current_page()
    node = page.scene.add_node("open_dataset", QPointF(0, 300))
    _wait_run(app, page)
    result = page.run.result(node)
    assert result.status == "failed" and "not in the cache" in result.error
    page.select(node.id, 0)
    _pump(app, 0.1)
    labels = page.panel_host.findChildren(QLabel)
    texts = [w.text() for w in labels]
    assert any("not in the cache" in t and 'href="https://doi.org' in t for t in texts)
    assert "DatasetMissing" in texts
    assert "Traceback" in page.panel_host.findChild(QPlainTextEdit).toPlainText()
    assert _error_message("ValueError: the noise must be 0..1") == ("ValueError", "the noise must be 0..1")
    assert _error_message("Not a box error") == ("", "Not a box error")
    assert _linkified(f"put it in {tmp_path}.") == f'put it in <a href="file://{tmp_path}">{tmp_path}</a>.'
    assert _linkified("see https://doi.org/10.1/x, then") == 'see <a href="https://doi.org/10.1/x">https://doi.org/10.1/x</a>, then'
    page.document.dirty = False                   # closing would otherwise ask to save
    window.close()


def test_adding_a_file_box_opens_its_settings(app):
    """Open log from + Add box: the box waits for its file and the Settings
    tab opens, instead of a red box with a traceback."""
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow()
    page = window.current_page()
    page.add_box_menu()
    next(b for b in page._picker.buttons if b.text() == "Open log").click()
    _wait_run(app, page)
    node = _node(page, "open_log")
    assert page.selected == node.id and page.tab == 3                 # Settings
    assert page.run.result(node).status == "idle"
    assert "Choose the file" in page.run.result(node).message
    page.document.dirty = False
    window.close()


def test_choose_picks_the_file_in_a_dialog(app, tmp_path, monkeypatch):
    """Choose… under a file setting opens a file dialog in the workflow's
    folder; a file inside the folder is kept by its relative name, one
    elsewhere by its full path, and the box runs with either."""
    from PySide6.QtWidgets import QComboBox, QFileDialog, QPushButton

    from openprocess.flow.library import library_for
    from openprocess.flow.workflow import Workflow
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.studio.documents import WorkflowDocument

    week = tmp_path / "Week 2"
    week.mkdir()
    inside = week / "orders.log.txt"
    inside.write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    elsewhere = tmp_path / "other place" / "boarding.txt"
    elsewhere.parent.mkdir()
    elsewhere.write_text("[<a,b>^2]", encoding="utf-8")

    library = library_for(week)
    wf = Workflow("Pick a file", library)
    node = wf.add(next(s for s in library.specs.values() if s.id.endswith("open_log")))
    page = WorkflowPage(WorkflowDocument(wf), library, week)
    page.resize(1300, 760)
    page.show()
    page.run_from(None)
    _wait_run(app, page)
    assert page.run.result(node).status == "idle"                   # no file yet
    page.select(node.id, 3)                                          # Settings
    _pump(app, 0.1)

    def controls():                                                  # the panel is rebuilt after each run
        return (next(b for b in page.panel_host.findChildren(QPushButton) if b.text() == "Choose…"),
                next(c for c in page.panel_host.findChildren(QComboBox) if c.isEditable()))

    choose, field = controls()
    asked = {}

    def fake_dialog(parent, title, start, filters):
        asked.update(title=title, start=start, filters=filters)
        return asked.pop("answer"), "Logs and nets"

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_dialog))
    asked["answer"] = str(inside)
    choose.click()
    assert asked["title"] == "Choose file" and Path(asked["start"]) == week
    assert asked["filters"] == "Event logs (*.xes *.xes.gz *.gz *.csv *.txt);;All files (*)"
    assert field.currentText() == "orders.log.txt"                 # relative: the folder may move
    _pump(app, 0.6)                                                  # settings apply after a short pause
    _wait_run(app, page)
    assert page.run.result(node).status == "done", page.run.result(node).error
    assert page.workflow.nodes[node.id].settings["file"] == Path("orders.log.txt")

    asked["answer"] = str(elsewhere)
    choose, field = controls()
    choose.click()
    assert Path(asked["start"]) == inside                           # the dialog opens where the file is
    assert field.currentText() == str(elsewhere)                    # outside the folder: the full path
    _pump(app, 0.6)
    _wait_run(app, page)
    assert page.run.result(node).status == "done", page.run.result(node).error
    assert page.scene.boxes[node.id].subtitle == "2 cases · 4 events"

    asked["answer"] = ""                                             # Cancel changes nothing
    choose, field = controls()
    choose.click()
    assert field.currentText() == str(elsewhere)

    field.setCurrentText(f'"{str(inside).replace(" ", chr(92) + " ")}"')   # pasted from a shell
    field.lineEdit().editingFinished.emit()
    assert field.currentText() == str(inside)
    page.document.dirty = False
    page.close()


def test_picking_a_file_from_the_list_runs_the_box(app, tmp_path):
    """Opening the file field's list must not rebuild the panel under it
    (focus leaving the field is not a change), and picking an entry runs
    the box with that file."""
    from PySide6.QtWidgets import QComboBox

    from openprocess.flow.library import library_for
    from openprocess.flow.workflow import Workflow
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.studio.documents import WorkflowDocument

    week = tmp_path / "Week 2"
    (week / "logs").mkdir(parents=True)
    (week / "logs" / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    (week / "net.pnml").write_text("<pnml/>", encoding="utf-8")          # not a log: not listed
    (week / "notes.txt").write_text("not a log either", encoding="utf-8")
    library = library_for(week)
    wf = Workflow("Pick from the list", library)
    node = wf.add(next(s for s in library.specs.values() if s.id.endswith("open_log")))
    page = WorkflowPage(WorkflowDocument(wf), library, week)
    page.resize(1300, 760)
    page.show()
    page.run_from(None)
    _wait_run(app, page)
    page.select(node.id, 3)
    _pump(app, 0.1)
    field = next(c for c in page.panel_host.findChildren(QComboBox) if c.isEditable())
    assert [field.itemText(i) for i in range(field.count())] == ["orders.log.txt"]   # logs only, short names
    assert field.itemData(0) == "logs/orders.log.txt"                               # the path behind
    runs_before = page.run
    field.lineEdit().setFocus()
    field.showPopup()                                        # the focus leaves the field
    field.lineEdit().editingFinished.emit()
    _pump(app, 0.8)                                          # longer than the settings delay
    assert page.run is runs_before                           # nothing ran: the value did not change
    assert field.view().isVisible()                          # and the list is still open
    field.hidePopup()
    field.setCurrentIndex(0)
    field.activated.emit(0)
    _pump(app, 0.6)
    _wait_run(app, page)
    assert page.run.result(node).status == "done", page.run.result(node).error
    assert page.scene.boxes[node.id].subtitle == "6 cases · 23 events"
    assert page.workflow.nodes[node.id].settings["file"] == Path("logs/orders.log.txt")
    page.document.dirty = False
    page.close()


def test_files_flow_into_the_canvas(app, tmp_path, monkeypatch):
    """The folder's files are one click away in the picker's Input group, a
    file dropped on the canvas becomes its input box, the box names its file,
    and Mine's footer puts a log or a typed log on the current analysis."""
    from PySide6.QtCore import QPointF

    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import WorkflowDocument

    week = tmp_path / "Week 2"
    (week / "logs").mkdir(parents=True)
    (week / "logs" / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    (week / "net.pnml").write_text(
        (Path(__file__).resolve().parents[1] / "examples" / "petri" / "order_handling_sound.pnml").read_text(),
        encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.log.txt"
    elsewhere.write_text("[<x,y>]", encoding="utf-8")
    (week / "notes.txt").write_text("not a log", encoding="utf-8")

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    window.action_new_workflow(None)
    page = window.current_page()
    _wait_run(app, page)

    # The picker's Input group lists the folder's files; one click adds the box that reads it.
    page.add_box_menu()
    picker = page._picker
    assert [b.text() for b in picker.file_buttons] == ["orders", "net"]     # by path: logs/… first
    picker.search.setText("orders")
    assert [b.text() for b in picker.file_buttons] == ["orders"]
    picker.file_buttons[0].click()
    _wait_run(app, page)
    node = _node(page, "open_log")
    assert node.settings["file"] == Path("logs/orders.log.txt")
    assert page.scene.boxes[node.id].eyebrow() == "INPUT · orders.log.txt"
    assert page.run.result(node).status == "done"
    assert page.selected == node.id and page.tab == 0                 # its Result, not Settings

    # A drop on the canvas: the matching box for each file, at that spot; a file from
    # outside the folder keeps its full path; a stray file is refused with a message.
    statuses = []
    page.status.connect(statuses.append)
    nodes = page.add_files([str(week / "net.pnml"), str(elsewhere), str(week / "notes.txt")], QPointF(300, 200))
    _wait_run(app, page)
    assert [page.workflow.spec(n).name for n in nodes] == ["Open net", "Open log"]
    assert nodes[0].settings["file"] == Path("net.pnml") and nodes[1].settings["file"] == elsewhere
    assert page.scene.boxes[nodes[0].id].eyebrow() == "INPUT · net.pnml"
    assert all(page.run.result(n).status == "done" for n in nodes), [page.run.result(n).error for n in nodes]
    assert any("notes.txt" in s for s in statuses)

    # Mine's footer: Open log… lands on the current analysis as a box …
    monkeypatch.setattr(studio_app.QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([str(week / "logs" / "orders.log.txt")], "")))
    before = len(page.workflow.nodes)
    window.action_open_log_box()
    assert len(page.workflow.nodes) == before + 1 and window.current_page() is page
    # … and with no analysis open, on a new one named after the file.
    window.content.setCurrentIndex(0)
    window.tree.setCurrentItem(None)
    window.action_open_log_box()
    new_page = window.current_page()
    assert new_page is not page and new_page.workflow.name == "orders"
    assert [new_page.workflow.spec(n).name for n in new_page.workflow.order()] == ["Open log"]
    # Log from notation…: a Typed log box with the text.
    monkeypatch.setattr(studio_app.NotationDialog, "exec", lambda self: studio_app.QDialog.Accepted)
    monkeypatch.setattr(studio_app.NotationDialog, "text", lambda self: "[<p,q>^2]")
    window.action_notation_box()
    typed = _node(new_page, "typed_log")
    assert typed.settings["text"] == "[<p,q>^2]"
    _wait_run(app, new_page)
    assert page.scene.boxes[node.id].eyebrow() == "INPUT · orders.log.txt"
    for document in list(window.documents):
        document.dirty = False
    window.close()


def test_a_page_opened_from_a_box_has_a_way_back(app):
    """Open as log from a box's result: the log page shows "‹ Back to …", which
    returns to the analysis (switching the space if need be)."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import LogDocument

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    log_node = page.workflow.order()[0]
    page.open_as_log(page.run.result(log_node).value)
    _pump(app, 0.2)
    log_page = window.current_page()
    document = window.documents[-1]
    assert isinstance(document, LogDocument) and document.opened_from is page.document
    assert log_page.header.back is not None and log_page.header.back.text() == f"‹ Back to {page.workflow.name}"
    log_page.header.back.click()
    assert window.current_page() is page and window.space == "mine"
    # A discovered net opens in Model; its Back link crosses the spaces.
    miner = page.workflow.order()[1]
    page.open_as_model(page.run.result(miner).value)
    _pump(app, 0.2)
    assert window.space == "model" and window.current_page().header.back is not None
    window.current_page().header.back.click()
    assert window.space == "mine" and window.current_page() is page
    for document in list(window.documents):
        document.dirty = False
    window.close()


def test_a_result_net_can_be_tidied_but_not_changed(app):
    """Dragging a place in a box's Result moves it (edges follow), the layout
    is kept with the box and shown again; the net is untouched."""
    from PySide6.QtWidgets import QGraphicsItem

    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.graph_view import GraphView

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    miner = page.workflow.order()[1]
    net = page.run.result(miner).value
    page.select(miner.id, 0)
    _pump(app, 0.2)
    view = next(v for v in page.panel_host.findChildren(GraphView))
    graph = view.graph
    place_id, item = next(iter(graph.nodes.items()))
    assert item.flags() & QGraphicsItem.ItemIsMovable
    edge = next(e for e in graph.edges if e.source_item is item or e.target_item is item)
    before_edge = edge.path().pointAtPercent(0.5)
    page.document.dirty = False
    item.setPos(item.pos().x() + 90, item.pos().y() - 40)
    _pump(app, 0.1)
    assert edge.path().pointAtPercent(0.5) != before_edge                  # the edge followed
    assert miner.layout[place_id] == (item.pos().x(), item.pos().y())      # kept with the box
    assert set(miner.layout) == set(graph.nodes)
    assert page.document.dirty                                             # an edit, to save
    assert len(net.places) == len(graph.nodes) - len(net.transitions)      # the net is the same
    # Shown again, the drawing is as tidied.
    page.select(miner.id, 1)
    page.select(miner.id, 0)
    _pump(app, 0.2)
    again = next(v for v in page.panel_host.findChildren(GraphView)).graph
    assert again.nodes[place_id].pos() == item.pos()
    page.document.dirty = False
    window.close()


def test_the_handoffs_between_mine_and_model(app, tmp_path, monkeypatch):
    """Open a copy in Model: a "(copy)" in Model that says where it is from;
    Mine a simulated log: a file in the folder and an analysis in Mine reading
    it; Check against a log: an analysis with the net, the log and Check fit."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.documents import CpnDocument, WorkflowDocument

    week = tmp_path / "Week 2"
    week.mkdir()
    (week / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    miner = page.workflow.order()[1]
    page.select(miner.id)
    net = page.run.result(miner).value
    places_before = len(net.places)

    # Open a copy in Model.
    page.edit_copy(net)
    _pump(app, 0.2)
    copy = window.documents[-1]
    assert isinstance(copy, CpnDocument) and copy.name.endswith("(copy)") and window.space == "model"
    assert copy.origin == f"from {page.workflow.title(miner.id)} in {page.workflow.name}"
    back = window.current_page().header.back
    assert back.text() == f"‹ A copy, {copy.origin}"
    assert Path(copy.path).name == f"{copy.name}.pnml"                       # saved in the folder
    assert len(net.places) == places_before                                  # the original is untouched
    model_page = window.current_page()

    # Check against a log: an analysis in Mine with the three boxes, connected.
    monkeypatch.setattr(studio_app.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(week / "orders.log.txt"), "")))
    model_page.check_log_button.click()
    _pump(app, 0.2)
    check = window.current_page()
    assert window.space == "mine" and isinstance(check.document, WorkflowDocument)
    assert check.workflow.name == f"{copy.name} vs orders"
    names = [check.workflow.spec(n).name for n in check.workflow.order()]
    assert names == ["Open net", "Open log", "Check fit"] or names == ["Open log", "Open net", "Check fit"]
    assert len(check.workflow.edges) == 2
    _wait_run(app, check)
    fit = next(n for n in check.workflow.nodes.values() if check.workflow.spec(n).name == "Check fit")
    assert check.run.result(fit).status == "done", check.run.result(fit).error

    # Mine a simulated log: the play-out becomes a file in the folder and an analysis reading it.
    monkeypatch.setattr(studio_app.QInputDialog, "getInt", staticmethod(lambda *a, **k: (20, True)))
    model_page.export_log_button.click()
    assert _wait_for_documents(app, window, lambda docs: any(
        isinstance(d, WorkflowDocument) and d.workflow.name.startswith("Play-out") for d in docs))
    mined = window.current_page()
    assert window.space == "mine" and mined.workflow.name.startswith("Play-out")
    log_box = mined.workflow.order()[0]
    assert mined.workflow.spec(log_box).name == "Open log"
    assert (week / Path(str(log_box.settings["file"]))).exists()
    _wait_run(app, mined)
    assert mined.run.result(log_box).status == "done"
    back.click()                                                              # the copy's way back
    assert window.current_page() is page and window.space == "mine"
    for document in list(window.documents):
        document.dirty = False
    window.close()


def _wait_for_documents(app, window, condition, seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if condition(window.documents):
            return True
        time.sleep(0.02)
    return condition(window.documents)


def test_the_summary_writes_itself(app):
    """Canvas | Summary: a tile per result in run order, the first log's process
    map with a detail slider, and a tile click opens the box on the canvas."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    page.summary_switch.set_index(1)
    _pump(app, 0.3)
    assert page.body.currentIndex() == 1
    titles = [page.workflow.title(t.node_id) for t in page.summary.tiles]
    assert titles == [page.workflow.title(n.id) for n in page.workflow.order()]   # one per box, in run order
    assert len(titles) == 3
    # No Directly-follows graph box yet: no map is computed behind the scenes; one click adds the box.
    assert not page.summary.map_card.isVisibleTo(page) and page.summary.no_map_card.isVisibleTo(page)
    page.summary.add_map_button.click()
    _pump(app, 0.3)
    _wait_run(app, page)
    names = [page.workflow.spec(n).name for n in page.workflow.order()]
    assert "Directly-follows graph" in names and len(page.workflow.edges) == 4   # fed by the log
    page.summary.refresh()
    _pump(app, 0.2)
    assert page.summary.map_card.isVisibleTo(page) and page.summary.map_view.graph.nodes
    assert "Directly-follows graph" in page.summary.map_card.title_label.text()
    full = len(page.summary.map_view.graph.nodes)
    page.summary.detail.setValue(10)
    assert len(page.summary.map_view.graph.nodes) < full
    assert len(page.summary.tiles) == 4                                          # the new box has a tile too
    last = page.summary.tiles[2]
    last.clicked.emit(last.node_id)
    _pump(app, 0.1)
    assert page.body.currentIndex() == 0 and page.summary_switch.index() == 0
    assert page.selected == last.node_id and page.tab == 0
    page.document.dirty = False
    window.close()


def test_motion_settles_the_window_and_can_be_turned_off(app, monkeypatch):
    """A space switch and Canvas ↔ Summary lift a veil that is gone a moment
    later; a box pulses once when it finishes; OPENPROCESS_NO_MOTION turns it off."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio import motion
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.motion import Veil

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    box = page.scene.boxes[page.workflow.order()[0].id]
    assert box.pulse_animation is not None                          # it finished: one pulse
    window.set_space("model")
    assert window.modes.findChildren(Veil)
    _pump(app, 0.5)
    assert not window.modes.findChildren(Veil)                      # lifted and gone
    window.set_space("mine")
    _pump(app, 0.5)
    page.summary_switch.set_index(1)
    assert page.body.findChildren(Veil)
    _pump(app, 0.5)
    assert not page.body.findChildren(Veil)
    monkeypatch.setattr(motion, "enabled", False)
    assert motion.lift(window.modes) is None and motion.fade_in(window) is None
    page.document.dirty = False
    window.close()


def test_the_header_says_whether_the_analysis_is_reproducible(app, tmp_path):
    """A badge beside the title: not recorded, then recorded and reproducing
    once the run is saved, then "changes since" when an input file is edited."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    week = tmp_path / "Week 2"
    week.mkdir()
    (week / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    window.action_new_workflow(TEMPLATES[0][1])              # on the folder's first log
    page = window.current_page()
    assert page.header.badge is not None and page.header.badge.text() == "●  Not recorded"
    assert "Not recorded yet" in page.header.badge.toolTip()
    _wait_run(app, page)
    window._flush_autosaves()                                # the run is recorded on save
    assert page.header.badge.text() == "●  Reproducible"
    assert page.document.record is not None and page.document.record.results
    (week / "orders.log.txt").write_text("[<a,b>^9]", encoding="utf-8")
    page.refresh_reproducibility()
    assert page.header.badge.text() == "●  1 change since"
    assert "has changed" in page.header.badge.toolTip()
    window.resize(700, 600)                                  # short of room: the dot alone
    _pump(app, 0.3)
    assert page.header.badge.text() == "●"
    window.resize(1400, 900)
    _pump(app, 0.3)
    assert page.header.badge.text() == "●  1 change since"
    page.header.badge.click()                                # the record, with the status on top
    _pump(app, 0.2)
    from PySide6.QtWidgets import QDialog, QLabel
    dialog = next(d for d in window.findChildren(QDialog) if "the record" in d.windowTitle())
    assert any("1 change since" in w.text() for w in dialog.findChildren(QLabel))
    dialog.close()
    page.document.dirty = False
    window.close()


def test_cite_the_app_and_an_algorithm(app):
    """Help ▸ Cite OpenProcess… gives the app's BibTeX; an algorithm's Code tab
    has Cite for the papers it follows."""
    from PySide6.QtWidgets import QPushButton

    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.cite_app()
    assert window.cite_dialog.bibtex.startswith("@software{openprocess,")
    assert "version = {" in window.cite_dialog.bibtex
    window.cite_dialog.close()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    page.select(page.workflow.order()[1].id, 2)             # the miner's Code tab
    _pump(app, 0.2)
    cite = next(b for b in page.panel_host.findChildren(QPushButton) if b.objectName() == "algorithmCite")
    cite.click()
    _pump(app, 0.2)
    from PySide6.QtWidgets import QDialog
    dialog = next(d for d in window.findChildren(QDialog) if d.windowTitle().startswith("Cite:"))
    assert "@inproceedings{leemans2013," in dialog.bibtex
    dialog.close()
    page.document.dirty = False
    window.close()


def test_step_through_plays_the_derivation_over_the_result(app):
    """The α-algorithm's How tab can be stepped: each step lights up on the net
    what it names (all activities, the start ones, the places…), the moments
    accumulate, and Play advances by itself."""
    from PySide6.QtWidgets import QPushButton

    from openprocess.gui.flow import stepper as stepping
    from openprocess.gui.flow.stepper import StepThrough
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[1][1])              # Compare discovery: it has the α-algorithm
    page = window.current_page()
    _wait_run(app, page)
    alpha = _node(page, "alpha_miner")
    page.select(alpha.id, 1)                                  # How
    _pump(app, 0.2)
    toggle = next(b for b in page.panel_host.findChildren(QPushButton) if b.objectName() == "stepThrough")
    toggle.click()
    _pump(app, 0.2)
    player = page.panel_host.findChildren(StepThrough)[0]
    net = page.run.result(alpha).value
    titles = [m[1] for m in player.moments if m[0] == "step"]
    assert titles[0].startswith("1. T_L") and titles[-1].startswith("8.")
    first_step = next(i for i, m in enumerate(player.moments) if m[0] == "step")
    player.show_moment(first_step)                            # all activities light up
    assert player.highlighted(first_step) == {t.id for t in net.transitions.values()}
    player.next()                                             # the start activities only
    lit = {net.transitions[i].name for i in player.highlighted(player.index)}
    assert lit == {"a"} and player.body_layout.count() == first_step + 2
    places = next(i for i, m in enumerate(player.moments) if m[0] == "step" and m[1].startswith("6."))
    player.show_moment(places)
    assert player.highlighted(places) >= {p.id for p in net.places.values()}   # every place is named
    assert not player.next_button.isEnabled() if places == len(player.moments) - 1 else True
    stepping.PLAY_MS = 60
    player.timer.setInterval(60)
    player.show_moment(0)
    player.toggle_play()
    assert player.play_button.text() == "Pause"
    _pump(app, 1.2)
    assert player.index == len(player.moments) - 1 and player.play_button.text() == "Play"
    toggle.click()                                            # back to the full report
    _pump(app, 0.1)
    assert not page.panel_host.findChildren(StepThrough)
    page.document.dirty = False
    window.close()


def test_export_report_writes_one_page_with_the_drawings(app, tmp_path, monkeypatch):
    """⋯ ▸ Export report…: an HTML file with the net and the map drawn as SVG."""
    from PySide6.QtGui import QDesktopServices

    from openprocess.gui.flow import page as flow_page
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    target = tmp_path / "report.html"
    monkeypatch.setattr(flow_page.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toLocalFile()) or True))
    assert any(a.text() == "Export report…" for a in page.more_menu.actions())
    page.export_report()
    text = target.read_text(encoding="utf-8")
    assert "<svg" in text and page.workflow.name in text and "How to cite" in text
    miner = page.workflow.order()[1]
    assert miner.id in page.drawings()                       # the net was drawn
    assert opened == [str(target)]
    page.document.dirty = False
    window.close()


def test_runs_keeps_every_run_and_puts_settings_back(app):
    """Canvas | Summary | Runs: each finished run is a snapshot with what
    changed; A and B compare box by box; Use A's settings goes back."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow

    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    assert [s.label for s in page.history.snapshots] == ["First run"]
    miner = page.workflow.order()[1]
    before = page.workflow.nodes[miner.id].settings["noise"]        # the template's IMf noise
    page.workflow.set(miner.id, noise=0.4)
    page.run_from([miner.id])
    _wait_run(app, page)
    assert [s.label for s in page.history.snapshots] == ["First run", f"Inductive Miner: noise {before} → 0.4"]
    page.summary_switch.set_index(2)
    _pump(app, 0.3)
    assert page.body.currentIndex() == 2 and page.runs.table.rowCount() == 2
    assert page.runs.a is page.history.snapshots[0] and page.runs.b is page.history.snapshots[1]
    rows = [page.runs.diff.item(r, 0).text() for r in range(page.runs.diff.rowCount())]
    assert rows == [page.workflow.title(n.id) for n in page.workflow.order()]
    assert f"noise {before} → 0.4" in page.runs.diff.item(1, 3).text()
    page.runs.use_a.click()                                   # back to the first run's noise
    assert page.workflow.nodes[miner.id].settings["noise"] == before and page.body.currentIndex() == 0
    _wait_run(app, page)
    assert page.history.snapshots[-1].label == f"Inductive Miner: noise 0.4 → {before}"
    page.document.dirty = False
    window.close()


def test_an_object_centric_log_flows_through_the_canvas(app, tmp_path):
    """A .jsonocel dropped on the canvas becomes an Open object-centric log box;
    the map box draws one colour per object type; the Summary has their tiles;
    the folder lists the file under LOGS."""
    import shutil

    from openprocess.gui.flow.picker import input_box_for
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.graph_view import GraphView
    from openprocess.gui.studio.widgets import Legend

    week = tmp_path / "Week 2"
    week.mkdir()
    shutil.copy(Path(__file__).parent / "data" / "orders.jsonocel", week / "orders.jsonocel")
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    assert "orders" in [window.material_section.child(i).text(0) for i in range(window.material_section.childCount())]
    window.action_new_workflow(None)
    page = window.current_page()
    _wait_run(app, page)
    (ocel,) = page.add_files([str(week / "orders.jsonocel")])
    assert page.workflow.spec(ocel).name == "Open object-centric log"
    graph = page.add_box_fed_by(input_box_for(page.library, "ocel") and next(
        s.id for s in page.library.specs.values() if s.id.endswith(".object_centric_map")), ocel.id)
    _pump(app, 0.3)
    _wait_run(app, page)
    assert page.run.result(graph).status == "done", page.run.result(graph).error
    page.select(graph.id, 0)
    _pump(app, 0.3)
    view = page.panel_host.findChildren(GraphView)[0]
    assert len(view.graph.nodes) == 5 and len(view.graph.edges) == 4          # 5 activities, 4 typed paths
    assert page.panel_host.findChildren(Legend)
    page.summary_switch.set_index(1)
    _pump(app, 0.3)
    assert [page.workflow.title(t.node_id) for t in page.summary.tiles] == [
        "Open object-centric log", "Object-centric map"]
    assert page.drawings()[graph.id].startswith("<svg")
    page.document.dirty = False
    window.close()


def test_a_page_opened_from_a_box_is_a_view_until_kept(app, tmp_path, monkeypatch):
    """Open as log › writes no file into the folder; the page has Keep in folder
    for when you want one; ✕ closes it without a question and leaves nothing
    in the sidebar."""
    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio import app as studio_app
    from openprocess.gui.studio.app import StudioWindow

    week = tmp_path / "Week 2"
    week.mkdir()
    window = StudioWindow()
    window.resize(1400, 900)
    window.show()
    assert window.open_workspace(str(week))
    window.action_new_workflow(TEMPLATES[0][1])
    page = window.current_page()
    _wait_run(app, page)
    files_before = sorted(p.name for p in week.iterdir() if not p.name.startswith("."))
    page.open_as_log(page.run.result(page.workflow.order()[0]).value)
    _pump(app, 0.3)
    log_page = window.current_page()
    document = log_page.document
    assert window.is_view(document) and document.path is None
    assert sorted(p.name for p in week.iterdir() if not p.name.startswith(".")) == files_before   # nothing written
    assert log_page.keep_view_button.isVisibleTo(log_page)
    # ✕: closed, no question asked, nothing left behind.
    monkeypatch.setattr(studio_app.QMessageBox, "exec", lambda self: (_ for _ in ()).throw(AssertionError("asked")))
    assert window.remove_documents([document.id])
    assert document not in window.documents and window.material_section.childCount() == 0
    # Opened again and kept: now it is a file of the folder.
    page.open_as_log(page.run.result(page.workflow.order()[0]).value)
    _pump(app, 0.3)
    kept = window.current_page()
    kept.keep_view_button.click()
    assert kept.document.path and Path(kept.document.path).parent == week.resolve()
    assert not window.is_view(kept.document) and not kept.keep_view_button.isVisibleTo(kept)
    assert window.material_section.childCount() == 1
    # Open as model › likewise: a view, its own Keep, closed without a question.
    from openprocess.flow.types import PetriNet
    nets = [page.run.result(n).value for n in page.workflow.order()
            if page.run.result(n) is not None and isinstance(page.run.result(n).value, PetriNet)]
    assert nets, "the template discovers a net"
    page.open_as_model(nets[0])
    _pump(app, 0.3)
    model_page = window.current_page()
    assert window.is_view(model_page.document) and model_page.document.path is None
    assert model_page.keep_button.isVisibleTo(model_page) and not hasattr(model_page, "keep_view_button")
    assert window.remove_documents([model_page.document.id])
    assert model_page.document not in window.documents and kept.document in window.documents
    new_files = sorted(p.name for p in week.iterdir() if not p.name.startswith(".")
                       and p.name not in files_before)
    assert new_files == [Path(kept.document.path).name]              # the kept log, nothing else
    for d in window.documents:
        d.dirty = False
    window.close()


def test_a_column_setting_is_a_dropdown_of_the_tables_columns(app):
    from openprocess.flow import Column, Table, box
    from openprocess.gui.flow.viewers import SettingsWidget

    @box
    def pick(tables: list[Table] = (), x: Column = "") -> Table:
        return Table("p", [x], [])

    widget = SettingsWidget(pick.spec, {"x": ""}, None, columns=["fitness", "precision"])
    combo = widget.controls["x"]
    assert [combo.itemText(i) for i in range(combo.count())] == ["—", "fitness", "precision"]
    assert combo.currentText() == "" and combo.lineEdit().placeholderText() == "Pick a column…"
    got = []
    widget.changed.connect(lambda name, value: got.append((name, value)))
    combo.setCurrentIndex(1)
    assert got == [("x", "fitness")] and combo.currentText() == "fitness"
    combo.setCurrentIndex(0)                                            # back to no column at all
    assert got[-1] == ("x", "") and combo.currentText() == ""
    # Nothing connected or not run yet: a name typed in still works, and the field says why the list is empty.
    empty = SettingsWidget(pick.spec, {"x": "fitness"}, None, columns=[])
    assert empty.controls["x"].currentText() == "fitness"
    assert "Connect a table" in empty.controls["x"].lineEdit().placeholderText()
