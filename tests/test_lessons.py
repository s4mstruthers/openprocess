"""The lessons that ship with OpenProcess (Introduction to process mining).

A lesson that marks a right answer wrong teaches the wrong thing, so every
answer box is answered here the way a student would: the right answer must
be accepted and a plausible wrong one refused.  The answers below are worked
out by hand from the lessons' own log and net, not copied from the checker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprocess.learn.checks import CORRECT, Context, check, validate
from openprocess.learn.pack import load_pack

LESSONS = Path(__file__).resolve().parents[1] / "openprocess" / "lessons"

#: lesson number -> one (right, wrong) per answer box, in the order of the sheet.
#: None: the box is not answered by typing (a drawn net, a built workflow, an open question).
ANSWERS = {
    1: [("10", "3"), ("44", "10"), ("{order, confirm, pay, bake, deliver, cancel}", "{order, pay}"), ("3", "10"),
        ("{order}", "{order, confirm}"), ("{deliver, cancel}", "{deliver}"), (["two cases"], ["one event"])],
    2: [("yes", "no"), ("no", "yes"),
        ("(order, confirm), (confirm, pay), (pay, bake), (bake, deliver), (confirm, bake), (bake, pay), "
         "(pay, deliver), (order, cancel)", "(order, confirm), (confirm, deliver)"),
        (["pay ‖ bake"], ["pay → bake"]), (["cancel # pay"], ["cancel ‖ pay"]), None],
    3: [("{order}", "{order, cancel}"), ("[c1]", "[i]"), ("{cancel, confirm}", "{confirm}"), ("[c2, c3]", "[c2]"),
        ("no", "yes"), ("order, cancel", "order, confirm, pay"), ("[c2, c5]", "[c4, c5]")],
    4: [("no", "yes"), ("yes", "no"), ("no", "yes"), ("3", "2"),
        ("[i], [c1], [o], [c2, c3], [c3, c4], [c2, c5], [c4, c5]", "[i], [c1], [o]"), ("6", "3")],
    5: [("no", "yes"), ("order, confirm, pay", "order, cancel"), ("{deliver}", "{bake, deliver}"),
        (["1: you can always finish", "3: nothing is useless"], ["2: you finish cleanly"]), None],
    6: [("{order}", "{confirm}"), ("{cancel, deliver}", "{deliver}"),
        (["one place after order, with arrows to confirm and to cancel"],
         ["two places after order, one for confirm and one for cancel"]),
        (["two places after confirm, one for pay and one for bake"],
         ["one place after confirm, with arrows to pay and to bake"]),
        ("yes", "no"), ("no", "yes"), ("{order, confirm, pay, bake, deliver, cancel}", "{order}")],
    7: [(["sequence →"], ["choice ×"]), ("→ {order} {confirm, pay, bake, deliver, cancel}", "× {order} {cancel}"),
        ("[<confirm, pay, bake, deliver>^5, <confirm, bake, pay, deliver>^3, <cancel>^2]", "[<cancel>^2]"),
        ("× {cancel} {confirm, pay, bake, deliver}", "→ {cancel} {confirm, pay, bake, deliver}"),
        ("→(order, ×(cancel, →(confirm, ∧(pay, bake), deliver)))", "→(order, confirm, pay, bake, deliver)"),
        ("no", "yes")],
    8: [None, ("6", "8"), (["the cuts the miner made, one after the other"], ["only the finished model again"]),
        (["every order in the log can be played on the model from start to end"], ["the model is drawn tidily"])],
    9: [("<order, confirm, pay, bake, deliver>: 7 7 0 0\n<order, confirm, bake, deliver>: 6 6 1 1\n"
         "<order, deliver>: 3 4 2 1",
         "<order, confirm, pay, bake, deliver>: 7 7 0 0\n<order, confirm, bake, deliver>: 6 6 0 0\n"
         "<order, deliver>: 3 3 0 0"),
        (["a step happened that the model did not allow at that moment"], ["the log has too few cases"]),
        ("0.93", "1"), None],
    10: [("1", "0"), ("order >> deliver\norder cancel >>", "order deliver\norder deliver"), ("2", "3"), ("0", "1"),
         (["it blames the case for as little as possible, so what remains is a real difference"],
          ["it is the fastest to compute"])],
    11: [("shop > anything > strict", "strict > shop > anything"), ("no", "yes"), (["anything.pnml"], ["shop.pnml"]),
         (["its fitness is low: it cannot replay half of what the shop does"], ["nothing"]),
         (["generalisation"], ["fitness"]), (["shop.pnml"], ["anything.pnml"])],
    12: [("yes", "no"), ("no", "yes"), ("yes", "no"), ("no", "yes"),
         (["not_co_existence(cancel, deliver)"], ["response(cancel, deliver)"]), None,
         (["the cancelled orders"], ["the baked-then-paid orders"])],
}


@pytest.fixture(scope="module")
def pack():
    return load_pack(LESSONS)


def test_there_are_twelve_lessons_and_none_has_a_mistake_in_its_blocks(pack):
    assert pack.title == "Introduction to process mining"
    assert [e.title.split(" — ")[0] for e in pack.exercises] == [f"Lesson {n}" for n in range(1, 13)]
    for exercise in pack.exercises:
        assert validate(exercise) == [], exercise.title
    assert not pack.is_exam
    assert sum(len(e.sheet.tasks) for e in pack.exercises) == sum(len(a) for a in ANSWERS.values())


def _options(task, wanted: list[str]) -> list[int]:
    labels = [option.text if hasattr(option, "text") else str(option) for option in task.options]
    return [labels.index(text) for text in wanted]


@pytest.mark.parametrize("number", sorted(ANSWERS))
def test_a_right_answer_is_accepted_and_a_wrong_one_is_not(pack, number):
    exercise = pack.exercises[number - 1]
    context = Context(exercise)
    assert len(exercise.sheet.tasks) == len(ANSWERS[number])
    for task, pair in zip(exercise.sheet.tasks, ANSWERS[number]):
        if pair is None:
            continue
        right, wrong = pair
        if task.type == "choice":
            right, wrong = _options(task, right), _options(task, wrong)
        result = check(exercise, task, right, context)
        assert result.status == CORRECT, (exercise.title, task.id, right, result.message)
        assert check(exercise, task, wrong, context).status != CORRECT, (exercise.title, task.id, wrong)


def test_the_broken_net_is_repaired_by_what_the_hint_says(pack, tmp_path):
    """Lesson 5: a place of its own for bake makes the net sound and the same as the shop's."""
    from openprocess.mining import check_soundness, compare_nets, read_pnml
    folder = pack.exercises[4].files.folder
    broken, model = read_pnml(folder / "net.pnml"), read_pnml(folder / "answer.pnml")
    assert check_soundness(model).sound and not check_soundness(broken).sound
    broken.add_place("c3", id="c3")
    broken.add_arc("confirm", "c3")
    broken.add_arc("c3", "bake")
    broken.arcs = [arc for arc in broken.arcs if (arc.source, arc.target) != ("c2", "bake")]
    broken._cache = None
    assert check_soundness(broken).sound
    assert compare_nets(broken, model).equivalent


def test_the_analyses_the_lessons_ask_for_give_what_the_lessons_say(pack):
    """Lessons 8 and 12: the workflows a student builds, run without the app."""
    from openprocess.flow import Runner, Workflow, box
    from openprocess.flow.library import standard_library
    from openprocess.mining import parse_simple_log
    from openprocess.mining.log import EventLog
    text = (pack.exercises[0].files.folder / "log.txt").read_text(encoding="utf-8")
    log = EventLog.from_simple_log(parse_simple_log(text), "pizza")

    @box
    def given() -> EventLog:
        return log

    workflow = Workflow("lesson 8", standard_library())
    source, miner, fit = workflow.add(given), workflow.add("inductive_miner"), workflow.add("check_fit")
    workflow.connect(source, miner)
    workflow.connect(miner, fit, "model")
    workflow.connect(source, fit, "log")
    run = Runner().run(workflow)
    assert run.value(fit).metrics["fitness"] == 1
    assert sum(1 for t in run.value(miner).transitions.values() if t.label) == 6

    workflow = Workflow("lesson 12", standard_library())
    source = workflow.add(given)
    rules, checked = workflow.add("discover_rules", {"holds": 0.8}), workflow.add("check_rules")
    workflow.connect(source, rules)
    workflow.connect(source, checked, "log")
    workflow.connect(rules, checked, "rules")
    breaking = Runner().run(workflow).value(checked).breaking
    assert dict(breaking.simple_log()) == {("order", "cancel"): 2}


def test_the_lessons_open_from_the_learn_menu_into_a_copy_of_your_own(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QFileDialog

    from openprocess.gui.studio.app import StudioWindow
    app = QApplication.instance() or QApplication([])
    assert StudioWindow.bundled_pack_source("lessons") == LESSONS
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    window = StudioWindow()
    window.show()
    actions = [a.text() for menu in window.menuBar().actions() if menu.text() == "&Learn"
               for a in menu.menu().actions()]
    assert actions[0] == "Introduction to Process Mining"
    window.open_lessons()
    app.processEvents()
    target = tmp_path / "Introduction to Process Mining"
    assert (target / "pack.md").exists() and window.in_learn
    assert len(window.learn_mode.pack.exercises) == 12
    assert not list(LESSONS.rglob("my answers.json"))             # answers go to the copy, never the package
    window.close()
