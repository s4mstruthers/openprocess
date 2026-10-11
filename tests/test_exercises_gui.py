"""Exercise mode, transition systems and regions, and Compare nets… in OpenProcess Studio.

Rendered offscreen, like the other GUI tests.  The demo exercises that ship
with the app (``openprocess/exercises``) are copied to a temporary folder first,
because an exercise saves your work into its folder.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DEMO = Path(__file__).resolve().parents[1] / "openprocess" / "exercises"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app, condition, seconds: float = 8.0) -> bool:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return True
        _pump(app, 0.02)
    return condition()


@pytest.fixture
def demo(tmp_path):
    target = tmp_path / "Exercises"
    shutil.copytree(DEMO, target)
    return target


def _window():
    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.resize(1500, 950)
    window.show()
    return window


def _cards(window):
    return list(window.learn_mode.view.cards.values())


def test_a_pack_opens_on_its_overview_and_steps_through(app, demo):
    from openprocess.gui.learn.mode import ExerciseRow

    window = _window()
    window.open_workspace(str(demo))
    assert window.open_exercise(demo)
    mode = window.learn_mode
    assert window.in_learn and mode.view is None
    rows = mode.home.findChildren(ExerciseRow)
    assert len(rows) == 7
    rows[1].open_it()
    assert mode.index == 1 and "Spot the flaw" in mode.view.title.text()
    assert window.windowTitle() == "Exercise 2.1 — Spot the flaw — OpenProcess demo exercises"
    assert mode.position.text() == "2 / 7"
    mode.step(1)
    assert "α-algorithm" in mode.view.title.text()
    mode.show_home()
    assert mode.stack.currentWidget() is mode.home_area

    # Exit: back to the folder, which is as it was.
    mode.exit_button.click()
    assert not window.in_learn and window.workspace is not None
    assert "Spot the flaw" not in window.windowTitle()
    window.close()


def test_learn_in_the_switcher_opens_the_folders_exercises(app, demo):
    """Exercises are Learn's, not rows in Mine: the switcher opens the open
    folder's pack on its overview, and Mine takes you back."""
    from openprocess.gui.studio.app import EXERCISE_ROLE

    window = _window()
    window.open_workspace(str(demo))
    assert not [i for i in window._all_rows() if i.data(0, EXERCISE_ROLE)]
    assert window.set_space("learn") and window.in_learn
    assert window.space_switch.index() == 2
    assert len(window.learn_mode.pack.exercises) == 7
    assert window.learn_mode.view is None                      # the overview, not an exercise
    window.set_space("mine")
    assert not window.in_learn and window.space == "mine" and window.space_switch.index() == 0
    window.close()


def test_answers_are_checked_saved_and_restored(app, demo):
    folder = demo / "2 Soundness" / "Exercise 2.1 Spot the flaw"
    window = _window()
    window.open_exercise(folder)
    wf, sound, conditions, deadlock, repair = _cards(window)
    assert [c.task.type for c in _cards(window)] == ["yesno", "yesno", "choice", "trace", "net"]

    wf.editor.buttons["yes"].click()
    wf.check_button.click()
    sound.editor.buttons["yes"].click()
    sound.check_button.click()
    conditions.editor.rows[0].indicator.click()
    conditions.check_button.click()
    deadlock.editor.edit.setText("register, call customer")
    deadlock.editor._edited()
    deadlock.check_button.click()
    assert wait_for(app, lambda: None not in (wf.status, sound.status, deadlock.status))
    assert wf.status == "correct" and sound.status == "incorrect"
    assert conditions.status == "partial" and deadlock.status == "correct"
    assert "Not right" in sound.feedback.text()

    # Show answer: the solution from the block; a hint on request.
    sound.reveal_solution()
    assert "No" in sound.solution_label.body.plain_text()
    conditions.toggle_hint()
    assert not conditions.hint_label.isHidden()

    # Changing an answer clears its old verdict.
    sound.editor.buttons["no"].click()
    assert sound.status is None and sound.feedback.isHidden()

    window.learn_mode.close_view()
    saved = json.loads((folder / "my answers.json").read_text())["tasks"]
    assert saved["q1"] == {"answer": "yes", "status": "correct"}
    assert saved["q2"]["answer"] == "no" and "status" not in saved["q2"]
    assert saved["q4"]["answer"] == "register, call customer"

    # Opening it again puts everything back.
    window.learn_mode.open_index(1)
    wf, sound, conditions, deadlock, _ = _cards(window)
    assert wf.editor.value() == "yes" and wf.status == "correct"
    assert sound.editor.value() == "no" and conditions.editor.value() == [0]
    assert deadlock.editor.edit.text() == "register, call customer"
    window.close()


def test_drawing_a_net_saves_my_answer_and_checks_it(app, demo):
    from openprocess.mining.pnml import read_pnml

    folder = demo / "2 Soundness" / "Exercise 2.1 Spot the flaw"
    given = (folder / "net.pnml").read_bytes()
    window = _window()
    window.open_exercise(folder)
    view = window.learn_mode.view
    repair = _cards(window)[-1]
    page = view.net_pages[repair.task.id]
    assert [name for name, _ in view.materials] == ["Given net", "Your net"]
    assert page.document.path is None                   # the given net, not yet yours

    # The given net is checked as it is: not sound.
    repair.check_button.click()
    assert wait_for(app, lambda: repair.status is not None)
    assert repair.status == "incorrect" and "not sound" in repair.feedback.text()

    # An edit saves your net as "my answer.pnml"; the given net stays.
    page._edited()
    view.flush()
    assert (folder / "my answer.pnml").exists()
    assert (folder / "net.pnml").read_bytes() == given

    # The repaired net (as if drawn): right.
    fixed = read_pnml(str(folder / "answer.pnml"))
    page.petri_net = lambda: fixed
    repair.check_button.click()
    assert wait_for(app, lambda: repair.status == "correct")
    assert "same behaviour" in repair.feedback.text()
    window.close()


def test_a_wrong_net_shows_traces_to_replay(app, demo):
    """Check finds an XOR where the answer has an AND, with traces that replay
    in your net's token game."""
    from openprocess.gui.studio.net_comparison import NetComparisonView
    from openprocess.mining.pnml import read_pnml, write_pnml

    folder = demo / "1 Petri nets" / "Exercise 1.1 Order handling"
    answer = read_pnml(str(folder / "answer.pnml"))
    xor = answer.copy()
    for place in ("p2", "p4"):
        xor.remove_place(place)
    xor.add_arc("p1", "t_ship")
    xor.add_arc("t_ship", "p3")
    write_pnml(xor, str(folder / "my answer.pnml"))
    window = _window()
    window.open_exercise(folder)
    view = window.learn_mode.view
    card = _cards(window)[1]
    page = view.net_pages[card.task.id]
    assert page.document.path == str(folder / "my answer.pnml")
    card.check_button.click()
    assert wait_for(app, lambda: card.status is not None)
    assert card.status == "incorrect"
    comparison = card.findChildren(NetComparisonView)[0]
    assert comparison.comparison.only_second == [("receive", "pay", "ship", "close")]
    comparison.replay_buttons[0].click()
    assert page.simulator.step_count == 2
    window.close()


def test_footprint_and_sets_on_a_log(app, demo):
    from openprocess.mining import footprint_of_log, parse_simple_log

    folder = demo / "3 Discovery" / "Exercise 3.1 The alpha-algorithm"
    window = _window()
    window.open_exercise(folder)
    view = window.learn_mode.view
    assert [name for name, _ in view.materials] == ["Log", "Your net"]
    footprint, t_l, t_i = _cards(window)[:3]
    truth = footprint_of_log(parse_simple_log((folder / "log.txt").read_text()))
    editor = footprint.editor
    assert editor.activities == truth.activities
    for (a, b), cell in editor.cells.items():
        editor.choose(cell, truth.relation(a, b))
    editor.choose(editor.cells[("b", "c")], "→")         # one mistake
    footprint.check_button.click()
    assert wait_for(app, lambda: footprint.status is not None)
    assert footprint.status == "partial"
    assert editor.cells[("b", "c")].property("wrong") is True
    assert editor.cells[("a", "b")].property("wrong") is False
    editor.choose(editor.cells[("b", "c")], "‖")         # fixed: the mark goes
    assert editor.cells[("b", "c")].property("wrong") is False
    footprint.check_button.click()
    assert wait_for(app, lambda: footprint.status == "correct")

    t_i.editor.edit.setText("{a")
    t_i.editor._edited()
    assert t_i.editor.reading.property("ok") is False      # read as you type
    t_i.editor.edit.setText("{a}")
    t_i.editor._edited()
    assert "{a}" in t_i.editor.reading.text()
    t_i.check_button.click()
    assert wait_for(app, lambda: t_i.status == "correct")

    # The log's footprint is hidden beside the question until revealed.
    log_page = view.materials[0][1]
    log_page.tabs.set_index(5)
    card = [c for c, key in log_page._concealed_cards if key == "footprint"][0]
    assert card.concealed
    view.concealment.reveal_all()
    assert not card.concealed
    window.close()


def test_regions_exercise_and_transition_system_page(app, demo):
    from openprocess.gui.studio.regions_view import TransitionSystemPage

    folder = demo / "4 Regions" / "Exercise 4.1 Regions of a transition system"
    given = (folder / "ts.txt").read_text()
    window = _window()
    window.open_exercise(folder)
    view = window.learn_mode.view
    (name, page), = view.materials
    assert isinstance(page, TransitionSystemPage)
    assert wait_for(app, lambda: page.panel is not None)
    panel = page.panel
    # The checker works in the exercise; the analysis is hidden.
    panel.region_edit.setText("s1, s3")
    panel.check_region()
    assert panel.last_check.is_region
    panel.system_view._clicked(panel.system_view.ids["s4"])     # click a state: {s1, s3, s4}
    assert panel.region_edit.text() == "s1, s3, s4" and not panel.last_check.is_region
    hidden = [card for card, _ in panel._concealed_cards]
    assert hidden and all(card.concealed for card in hidden)
    view.concealment.reveal_all()
    assert not any(card.concealed for card in hidden)

    pre_c = _cards(window)[2]
    pre_c.editor.set_value("{s1, s3}, {s2, s3}")
    pre_c.check_button.click()
    assert wait_for(app, lambda: pre_c.status == "correct")

    # Trying another system recomputes, but never writes over the given file.
    page.editor.setPlainText("s0 -a-> s1, s1 -b-> s2")
    page.apply()
    assert wait_for(app, lambda: page.panel is not None and len(page.panel.ts.states) == 3)
    assert (folder / "ts.txt").read_text() == given
    window.close()


def test_starting_again_and_an_open_question(app, tmp_path):
    folder = tmp_path / "pack" / "Ex"
    folder.mkdir(parents=True)
    (folder / "question.md").write_text(
        "# Reflect\n\nWhy?\n\n```answer\ntype: open\nsolution: Because.\n```\n")
    window = _window()
    window.open_exercise(folder)
    (card,) = _cards(window)
    assert not card.check_button.isVisible()
    card.editor.set_value("My thoughts")
    card.editor.changed.emit()
    card.reveal_solution()
    assert "Because." in card.solution_label.body.plain_text()
    assert not card.assess_row.isHidden()
    card.assess(True)
    assert card.status == "done"
    window.learn_mode.view.flush()
    assert json.loads((folder / "my answers.json").read_text())["tasks"]["q1"]["status"] == \
        "done"
    from PySide6.QtWidgets import QMessageBox
    original = QMessageBox.question
    QMessageBox.question = lambda *a, **k: QMessageBox.Yes
    try:
        window.learn_mode.reset_exercise()
    finally:
        QMessageBox.question = original
    assert not (folder / "my answers.json").exists()
    assert _cards(window)[0].status is None
    window.close()


def test_a_mistake_in_a_sheet_is_shown_not_hidden(app, tmp_path):
    folder = tmp_path / "Ex"
    folder.mkdir()
    (folder / "question.md").write_text("# Broken\n\n```answer\ntype: essay\n```\n")
    window = _window()
    window.open_exercise(folder)
    view = window.learn_mode.view
    assert view.exercise.error and "needs a type" in view.exercise.error
    from PySide6.QtWidgets import QLabel
    assert any("mistake" in w.text() for w in view.findChildren(QLabel)
               if w.objectName() == "sheetProblem")
    window.close()


def test_open_demo_exercises_asks_where_and_updates_an_old_copy(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from openprocess.gui.studio.app import StudioWindow

    # First time: you choose the folder; cancelling copies nothing.
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    window = _window()
    window.open_demo_exercises()
    assert not window.in_learn and not list(tmp_path.iterdir())
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    window.open_demo_exercises()
    target = tmp_path / "OpenProcess Demo Exercises"
    assert (target / "pack.md").exists() and window.in_learn
    assert window.workspace is None                  # your folder is left as it was
    window.close()

    # A copy made by an older version is brought up to date; yours stays.
    old = tmp_path / "old" / "1 Petri nets" / "Exercise 1.1 Order handling"
    old.mkdir(parents=True)
    (old / "question.md").write_text("# Old\n\nDraw it.")
    (old / "my answer.pnml").write_text("mine")
    monkeypatch.setattr(StudioWindow, "demo_exercises_target",
                        lambda self: tmp_path / "old")
    window = _window()
    window.open_demo_exercises()
    assert "```answer" in (old / "question.md").read_text()
    assert (old / "my answer.pnml").read_text() == "mine"
    assert window.in_learn and len(window.learn_mode.pack.exercises) == 7
    window.close()


def test_discover_with_state_based_regions(app):
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.regions_view import RegionsPanel
    from openprocess.mining import EventLog, parse_simple_log

    from openprocess.gui.studio.log_page import LogPage

    log = EventLog.from_simple_log(parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"),
                                   "L")
    page = LogPage(LogDocument(log))             # Learn's log page: the miners run inline
    page.resize(1200, 800)
    page.show()
    page.tabs.set_index(6)                       # Discover
    from PySide6.QtWidgets import QComboBox
    chooser = next(box for box in page.findChildren(QComboBox)
                   if box.findData("regions") >= 0)
    chooser.setCurrentIndex(chooser.findData("regions"))
    assert wait_for(app, lambda: page.findChildren(RegionsPanel))
    panel = page.findChildren(RegionsPanel)[-1]
    assert panel.result.analysis.elementary and panel.result.synthesis.isomorphic
    # The state function: a sequence with no horizon is a tree, not elementary.
    direction, representation, horizon = page.region_controls
    representation.setCurrentIndex(representation.findData("sequence"))
    assert wait_for(app, lambda: any(p.result.ts.states[0] == "⟨⟩"
                                     for p in page.findChildren(RegionsPanel)))
    page.close()


def test_compare_nets_outside_an_exercise(app):
    from openprocess.gui.studio.documents import ModelDocument
    from openprocess.model.examples import order_handling_sound, order_handling_unsound

    window = _window()
    window.add_document(ModelDocument(order_handling_sound(), origin="test"))
    window.add_document(ModelDocument(order_handling_unsound(), origin="test"))
    first, second = window.documents
    window.compare_nets(first, second)
    assert wait_for(app, lambda: getattr(window, "net_comparison_dialog", None) is not None)
    from openprocess.gui.studio.net_comparison import NetComparisonView
    view = window.net_comparison_dialog.findChildren(NetComparisonView)[0]
    assert isinstance(view.comparison.equivalent, bool)
    window.net_comparison_dialog.close()
    window.close()


def test_question_markdown_has_maths_and_tables():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from openprocess.gui.studio.markdown_view import markdown_html
    html = markdown_html("| a | b |\n|---|---|\n| 1 | 2 |\n\nInline $s_{in} \\in S$ and\n\n"
                         "$$\\bigcap_{R} R$$\n\nand $\\frobnicate$ stays as written.")
    assert "<table" in html and "<sub>" in html and "⋂" in html
    assert "frobnicate" in html and "MATHX" not in html
    # A formula wrapped over a line break is one formula, and the prose between
    # two formulas is prose: "$L = [...\n...]$ and the net $N$" once gave "andthenetN".
    wrapped = markdown_html("The log $L = [\\langle a,b \\rangle^3, \\langle\na,e \\rangle]$ and the net $N$ are given.")
    assert "and the net" in wrapped and "andthenet" not in wrapped and "\\langle" not in wrapped
    apart = markdown_html("Costs $5 now.\n\nAnd $7 later.")
    assert "$5" in apart and "$7" in apart


def test_help_shows_references_and_the_authoring_guide(app):
    from openprocess import references
    from openprocess.gui.studio.definition_view import show_guide
    dialog = show_guide("references")
    text = dialog.browser.toPlainText()
    assert "Works cited" in text and "Process Mining: Data Science in Action" in text
    guide = show_guide("exercise-packs")
    assert "compute" in guide.browser.toPlainText()
    # Every topic cites works that exist.
    for _, topics in references.TOPICS:
        for topic in topics:
            assert topic.sources and all(k in references.BY_KEY for k in topic.sources)
    dialog.close()
    guide.close()


def test_references_doc_is_up_to_date():
    from openprocess import references
    path = Path(__file__).resolve().parents[1] / "docs" / "references.md"
    assert path.read_text(encoding="utf-8") == references.markdown(), \
        "regenerate with: python -m openprocess.references > docs/references.md"


def test_old_style_parts_get_boxes_and_notes_are_kept(app, tmp_path):
    """A sheet written before answer blocks gets a box under each lettered part,
    with that part of answer.md as its model answer; Notes are saved."""
    folder = tmp_path / "Ex"
    folder.mkdir()
    (folder / "question.md").write_text(
        "# Flaw\n\nThe net.\n\na. Is it a WF-net?\n\nb. Change the net so that it is "
        "sound.\n")
    (folder / "answer.md").write_text("**a.** Yes.\n\n**b.** Merge c2 and c3.\n")
    shutil.copy(DEMO / "2 Soundness" / "Exercise 2.1 Spot the flaw" / "net.pnml", folder)
    window = _window()
    window.open_exercise(folder)
    mode = window.learn_mode
    first, second = _cards(window)
    assert (first.task.type, second.task.type) == ("open", "net")
    first.reveal_solution()
    assert "Yes." in first.solution_label.body.plain_text()

    view = mode.view
    assert not view.notes_visible
    mode.notes_button.click()
    assert view.notes_visible and mode.notes_open
    view.notes.setPlainText("[i] -register-> [c1]")
    mode.show_home()                                   # leaving saves them
    assert (folder / "my notes.md").read_text() == "[i] -register-> [c1]"
    mode.close_view()
    mode.open_index(0)
    assert mode.view.notes_visible and "register" in mode.view.notes.toPlainText()
    window.close()


def test_wide_pictures_are_scaled_to_the_worksheet(app, tmp_path):
    from PySide6.QtGui import QImage
    from openprocess.gui.studio.markdown_view import MarkdownLabel
    picture = QImage(1600, 400, QImage.Format_RGB32)
    picture.fill(0)
    picture.save(str(tmp_path / "wide.png"))
    QImage(200, 100, QImage.Format_RGB32).save(str(tmp_path / "small.png"))
    text = MarkdownLabel("![w](wide.png) ![s](small.png)", tmp_path).text()
    assert 'wide.png" width="640"' in text and 'small.png" width' not in text
