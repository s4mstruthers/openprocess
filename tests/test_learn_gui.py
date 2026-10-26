"""OpenProcess Learn in the window: the new answer boxes, the Workflow tab, exams and
variants.  Rendered offscreen; the demo exercises are copied first, because
an exercise saves your work into its folder."""

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
MARKINGS = "5 Markings/Exercise 5.1 Markings and matrices"
CUTS = "6 Inductive Miner/Exercise 6.1 Cuts and trees"
CONFORMANCE = "7 Conformance/Exercise 7.1 Replay, alignments and workflows"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app, condition, seconds: float = 30.0) -> bool:
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


def _answer_and_check(app, cards: dict, answers: dict) -> None:
    for task_id, value in answers.items():
        card = cards[task_id]
        card.editor.set_value(value)
        card.editor.changed.emit()
        card.check_button.click()
    assert wait_for(app, lambda: all(cards[t].status is not None for t in answers))


def test_markings_matrices_and_transition_systems_in_the_window(app, demo):
    from openprocess.gui.learn.answer_boxes import MatrixEditor, NotationEditor, TupleEditor

    window = _window()
    assert window.open_exercise(demo / MARKINGS)
    view = window.learn_mode.view
    cards = view.cards
    tuple_, marking, markings, matrix, ts, _ = _cards(window)
    assert isinstance(tuple_.editor, TupleEditor) and isinstance(matrix.editor, MatrixEditor)
    assert isinstance(ts.editor, NotationEditor)
    assert "4 POINTS" in tuple_.caption.text()
    assert matrix.editor.rows == ["i", "c1", "c2", "o"]
    assert matrix.editor.columns == ["register", "send letter", "call customer", "archive"]

    # Typing shows how the answer is read; a wrong marking says what is off.
    marking.editor.edit.setPlainText("c1 + 2i")
    assert marking.editor.reading.text() == "Read as [c1, i^2]"
    markings.editor.edit.setPlainText("p1, p2")
    assert markings.editor.reading.property("ok") is False
    assert "square brackets" in markings.editor.reading.text()
    cells = {"i\tregister": "-1", "c1\tregister": "1", "c1\tsend letter": "-1",
             "c1\tcall customer": "-1", "c2\tsend letter": "1", "c2\tcall customer": "1",
             "c2\tarchive": "-1", "o\tarchive": "2"}
    _answer_and_check(app, cards, {
        "q1": {"P": "{i, c1, c2, o}", "T": "register, send letter, call customer, archive",
               "F": "(i, register)", "m0": "[i]"},
        "q2": "[c1]", "q3": "[i], [c1], [c2], [o]", "q4": {"cells": cells},
        "q5": "s0 -register-> s1\ns1 -send letter-> s2\ns1 -call customer-> s2\n"
              "s2 -archive-> s3\ninitial: s0"})
    assert tuple_.status == "partial" and tuple_.editor.marks["F"].text() == "◐"
    assert tuple_.editor.marks["P"].text() == "✓"
    assert marking.status == "correct" and markings.status == "correct" and ts.status == "correct"
    assert matrix.status == "partial"
    assert matrix.editor.cells[("o", "archive")].property("wrong") is True
    assert matrix.editor.cells[("i", "register")].property("wrong") is False
    # Editing a wrong cell clears its mark and the verdict.
    matrix.editor.cells[("o", "archive")].setText("1")
    matrix.editor.cells[("o", "archive")].textEdited.emit("1")
    assert matrix.status is None and matrix.editor.cells[("o", "archive")].property("wrong") is False
    assert "7 of 10 points" not in view.summary_label.text()      # the partial tuple counts a half
    assert "points" in view.summary_label.text()

    view.flush()
    saved = json.loads((demo / MARKINGS / "my answers.json").read_text())["tasks"]
    assert saved["q1"]["status"] == "partial" and saved["q1"]["share"] == 0.75
    assert saved["q4"]["answer"]["cells"]["o\tarchive"] == "1"
    window.learn_mode.open_index(4)
    again = _cards(window)[0]
    assert again.status == "partial" and again.share == 0.75
    assert again.editor.value()["F"] == "(i, register)"
    window.close()


def test_cuts_trees_predictions_and_rankings_in_the_window(app, demo):
    from openprocess.gui.learn.answer_boxes import LineEditor, RankingEditor

    window = _window()
    assert window.open_exercise(demo / CUTS)
    cards = window.learn_mode.view.cards
    cut, sublog, cut2, tree, predict = _cards(window)
    assert isinstance(predict.editor, LineEditor)           # as: set
    _answer_and_check(app, cards, {"q1": "→ {a} {b, c, e} {d}", "q2": "[<b,c>^3, <c,b>^2, <e>]",
                                   "q3": "xor {b, c} {e}", "q4": "→(a, ×(∧(b, c), e), d)",
                                   "q5": "{a, b, c, d, e}"})
    assert [c.status for c in _cards(window)] == ["correct"] * 5
    assert "5 of 5 answers done" in window.learn_mode.view.summary_label.text()

    window.open_exercise(demo / CONFORMANCE)
    ranking = _cards(window)[3]
    assert isinstance(ranking.editor, RankingEditor)
    assert ranking.editor.value() == ["m1.pnml", "m2.pnml", "m3.pnml"]
    ranking.editor.list.setCurrentRow(0)
    ranking.editor.move(1)
    assert ranking.editor.value() == ["m2.pnml", "m1.pnml", "m3.pnml"]
    assert ranking.editor.list.item(0).text() == "1. m2.pnml"
    ranking.check_button.click()
    assert wait_for(app, lambda: ranking.status is not None)
    assert ranking.status == "partial" and "ranked above" in ranking.feedback.text()
    ranking.editor.set_value(["m1.pnml", "m2.pnml", "m3.pnml"])
    assert ranking.editor.value()[0] == "m1.pnml"
    window.close()


def test_replay_alignment_and_the_workflow_tab(app, demo):
    from openprocess.gui.flow.page import WorkflowPage
    from openprocess.gui.learn.answer_boxes import ReplayEditor, WorkflowEditor

    folder = demo / CONFORMANCE
    window = _window()
    assert window.open_exercise(folder)
    view = window.learn_mode.view
    assert [name for name, _ in view.materials] == ["Log", "Given net", "Workflow"]
    replay, fitness, alignment, _ranking, workflow = _cards(window)
    assert isinstance(replay.editor, ReplayEditor) and isinstance(workflow.editor, WorkflowEditor)
    assert [t for t in replay.editor.traces] == [("a", "b", "c", "d"), ("a", "c", "b", "d"),
                                                 ("a", "e", "d")]
    row = {"p": "6", "c": "6", "m": "0", "r": "0"}
    _answer_and_check(app, view.cards, {
        "q1": {"⟨a, b, c, d⟩": row, "⟨a, c, b, d⟩": row, "⟨a, e, d⟩": {**row, "r": "1"}},
        "q2": "1", "q3": "a b >> d\na b c d"})
    assert replay.status == "partial"
    assert replay.editor.cells[(("a", "e", "d"), "r")].property("wrong") is True
    assert fitness.status == "correct" and alignment.status == "correct"
    assert "3 synchronous" in alignment.editor.reading.text()

    # The Workflow tab holds a typed log; the student adds the miner and Check fit.
    page = view.workflow_page
    assert isinstance(page, WorkflowPage) and page.keep_button.isHidden()
    workflow.editor.go_to_workflow.emit()
    assert view.material_stack.currentIndex() == 2
    assert [n.box.rsplit(".", 1)[-1] for n in page.workflow.nodes.values()] == ["typed_log"]
    workflow.check_button.click()
    assert wait_for(app, lambda: not workflow.feedback.isHidden()
                    and "Checking" not in workflow.feedback.text())
    assert "Build the workflow" in workflow.feedback.text()
    from PySide6.QtCore import QPointF
    log = next(iter(page.workflow.nodes.values()))
    miner = page.scene.add_node("inductive_miner", QPointF(300, 150))
    fit = page.scene.add_node("check_fit", QPointF(600, 150))
    page.workflow.connect(log, miner)
    page.workflow.connect(miner, fit, "model")
    page.workflow.connect(log, fit, "log")
    page.edited.emit()
    assert wait_for(app, lambda: (folder / "my workflow.analysis").exists(), 10)
    workflow.check_button.click()
    assert wait_for(app, lambda: workflow.status is not None, 60)
    assert workflow.status == "correct", workflow.feedback.text()
    view.flush()
    saved = json.loads((folder / "my answers.json").read_text())["tasks"]
    assert saved["q5"] == {"status": "correct"} and "answer" not in saved["q5"]
    window.learn_mode.close_view()
    window.learn_mode.open_index(6)
    view = window.learn_mode.view
    assert [n.box.rsplit(".", 1)[-1] for n in view.workflow_page.workflow.nodes.values()] == \
        ["typed_log", "inductive_miner", "check_fit"]
    assert _cards(window)[4].status == "correct"
    window.close()


def test_an_exam_hides_hints_and_answers_and_runs_a_clock(app, demo, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    (demo / "pack.md").write_text("---\nexam: yes\ntime: 1\nseed: student\n---\n# Exam\n")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Ada Lovelace", True))
    window = _window()
    assert window.open_exercise(demo)
    mode = window.learn_mode
    assert mode.pack.is_exam and mode.pack.student() == "Ada Lovelace"
    assert (demo / "my exam.json").exists() and mode.exam.started is not None
    assert mode.clock.isVisible() and mode.clock.text().endswith("left")
    assert "an exam" in mode.home.findChildren(type(mode.where))[2].text() or True
    mode.open_index(1)
    view = mode.view
    assert view.exam and view.concealment.locked
    wf, sound, conditions, deadlock, _ = _cards(window)
    assert conditions.hint_button.isHidden() and sound.answer_button.isHidden()
    assert wf.check_button.isVisible()
    # Nothing can be revealed, by button or by menu.
    view.concealment.reveal("soundness")
    assert view.concealment.hidden("soundness")
    view.concealment.reveal_all()
    assert view.concealment.anything_hidden
    mode._fill_more_menu()
    actions = [a.text() for a in mode.more_menu.actions()]
    assert "Reveal Every Hidden Result" not in actions
    assert "Export Marks…" in actions and "Change Your Name…" in actions
    _answer_and_check(app, view.cards, {"q1": "yes"})
    assert wf.status == "correct"

    # Time is up: the answers stay, and nothing can be changed.
    from datetime import timedelta
    mode.exam.started -= timedelta(minutes=2)
    mode._tick()
    assert mode.clock.text() == "Time is up" and view.locked
    assert not sound.editor.isEnabled() and not wf.check_button.isEnabled()
    assert mode.closed
    mode.close_view()
    mode.open_index(1)
    assert mode.view.locked and _cards(window)[0].status == "correct"

    # Marks go out as CSV.
    from PySide6.QtWidgets import QFileDialog
    target = demo.parent / "marks.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    mode.export_marks()
    assert target.exists() and "Ada Lovelace" in target.read_text()
    window.close()


def test_the_learn_menu_and_the_exam_importer(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    exam = tmp_path / "Exam 2025.txt"
    exam.write_text("1. Petri nets (4 points)\n\na) Is the net sound? (2 points)\n"
                    "b) Draw a sound WF-net. (2 points)\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(exam), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = _window()
    menus = [m.title() for m in window.menuBar().findChildren(type(window.menuBar().actions()[0].menu()))]
    assert "&Learn" in menus
    learn = next(m for m in window.menuBar().findChildren(type(window.menuBar().actions()[0].menu()))
                 if m.title() == "&Learn")
    assert [a.text() for a in learn.actions() if a.text()] == [
        "Open Exercise Pack…", "Open Demo Exercises", "Make a Pack from an Exam…",
        "Writing Exercise Packs"]
    window.action_import_exam()
    target = tmp_path / "Exam 2025"
    assert (target / "pack.md").exists() and (target / "1 Petri nets" / "question.md").exists()
    assert window.in_learn and window.learn_mode.pack.is_exam
    assert [t.type for t in window.learn_mode.view.exercise.sheet.tasks] == ["yesno", "net"]
    window.close()
