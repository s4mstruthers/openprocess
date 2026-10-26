"""OpenProcess Learn without the GUI: notations, the new answer boxes, exams, marks,
the exam importer and the command line (see also test_teaching.py, the older
boxes through the openprocess.teaching shims)."""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openprocess.learn import answers, notation
from openprocess.learn.checks import (
    CORRECT, INCORRECT, PARTIAL, UNKNOWN, Context, TaskError, check, model_answer_text, validate,
)
from openprocess.learn.computed import COMPUTED, describe_all, lookup
from openprocess.learn.exam import ExamState, marks, marks_csv, parse_generate
from openprocess.learn.importer import guess_type, split_questions, todo_list, write_pack
from openprocess.learn.pack import Exercise, load_pack
from openprocess.learn.sheet import Task, parse_sheet

DEMO = Path(__file__).resolve().parents[1] / "openprocess" / "exercises"
MARKINGS = DEMO / "5 Markings" / "Exercise 5.1 Markings and matrices"
CUTS = DEMO / "6 Inductive Miner" / "Exercise 6.1 Cuts and trees"
CONFORMANCE = DEMO / "7 Conformance" / "Exercise 7.1 Replay, alignments and workflows"
LOG = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


def exercise(folder: Path, question: str, **files: str) -> Exercise:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "question.md").write_text(question, encoding="utf-8")
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return Exercise.at(folder)


def block(kind: str, **settings: str) -> str:
    return f"```answer\ntype: {kind}\n" + "".join(f"{k}: {v}\n" for k, v in settings.items()) + "```\n"


# -- notations --------------------------------------------------------------------------
@pytest.mark.parametrize("typed, expected", [
    ("[p1, p4^2]", {"p1": 1, "p4": 2}),
    ("[p1, p4²]", {"p1": 1, "p4": 2}),
    ("p1 + 2p4", {"p1": 1, "p4": 2}),
    ("{p1: 1, p4: 2}", {"p1": 1, "p4": 2}),
    ("[P_1]", {"p1": 1}),
    ("[]", {}),
    ("∅", {}),
])
def test_markings_are_read_leniently(typed, expected):
    assert notation.marking(typed) == Counter(expected)
    assert notation.marking(notation.show_marking(notation.marking(typed))) == Counter(expected)


def test_sets_of_markings_cuts_trees_and_logs():
    assert notation.markings("[p1], [p2, p3]") == notation.markings("{[p2,p3], [p1]}")
    assert notation.show_markings(notation.markings("[p2, p3], [p1]")) == "[p1], [p2, p3]"
    assert notation.cut("→ {a} {b, c, e} {d}") == ("→", (frozenset("a"), frozenset("bce"), frozenset("d")))
    assert notation.cut("sequence: {a}, {b,c,e}, {d}")[0] == "→"
    assert notation.cut("xor({b, c}, {e})") == ("×", (frozenset("bc"), frozenset("e")))
    with pytest.raises(answers.AnswerSyntaxError, match="operator"):
        notation.cut("plus {a} {b}")
    tree = notation.tree("→(a, ×(∧(b, c), e), d)")
    assert tree.key() == notation.tree("seq(a, xor(e, and(c, b)), d)").key()   # × and ∧ unordered
    assert str(tree) == "→(a, ×(∧(b, c), e), d)"
    assert str(notation.tree("→(a, tau)")) == "→(a, τ)"
    with pytest.raises(answers.AnswerSyntaxError):
        notation.tree("→(a, b")
    assert notation.log("[<a,b>^2, <a,c>]") == Counter({("a", "b"): 2, ("a", "c"): 1})
    assert notation.show_log(notation.log("[<a,b>^2, <a,c>]")) == "[<a,b>^2, <a,c>]"


def test_matrices_alignments_replay_tables_and_rankings():
    matrix = notation.matrix(".   a   b\np1  -1  1\np2   0 −1")
    assert matrix.rows == ["p1", "p2"] and matrix.columns == ["a", "b"]
    assert matrix.cells[("p2", "b")] == -1 and matrix.get("P_1", "A") == -1
    assert notation.matrix("| | a | b |\n|---|---|---|\n| p1 | -1 | 1 |").cells[("p1", "a")] == -1
    with pytest.raises(answers.AnswerSyntaxError, match="values for 2 columns"):
        notation.matrix("a b\np1 1 2 3")
    with pytest.raises(answers.AnswerSyntaxError, match="not a number"):
        notation.matrix("a b\np1 x")
    assert "p1" in notation.show_matrix(matrix)
    assert notation.alignment("a b >> d\na b c d") == (["a", "b", "≫", "d"], ["a", "b", "c", "d"])
    assert notation.alignment("log | a | - |\nmodel | a | b |") == (["a", "≫"], ["a", "b"])
    with pytest.raises(answers.AnswerSyntaxError, match="different lengths"):
        notation.alignment("a b\na")
    with pytest.raises(answers.AnswerSyntaxError, match="not a move"):
        notation.alignment("a >>\na >>")
    table = notation.replay_table("<a, b>: 4 4 0 0\n⟨a, c⟩ | 4 | 3 | 1 | 1")
    assert table[("a", "b")] == {"p": 4, "c": 4, "m": 0, "r": 0}
    assert table[("a", "c")]["m"] == 1
    assert notation.replay_table({"⟨a, b⟩": {"p": "4", "c": "", "m": "0", "r": "0"}}) == \
        {("a", "b"): {"p": 4, "m": 0, "r": 0}}
    assert notation.ranking("m2 > m1 > m3") == ["m2", "m1", "m3"]
    assert notation.ranking("1. m2.pnml\n2. m1") == ["m2", "m1"]
    assert notation.ranking(["m2", "m1"]) == ["m2", "m1"]
    with pytest.raises(answers.AnswerSyntaxError, match="twice"):
        notation.ranking("m1, m1")
    read = notation.net_tuple("P = {p1, p2}; T = {a}; F = {(p1, a), (a, p2)}; m0 = [p1]")
    assert read["P"] == frozenset({"p1", "p2"}) and read["F"] == frozenset({("p1", "a"), ("a", "p2")})
    assert read["m0"] == frozenset({("p1", 1)})
    with pytest.raises(answers.AnswerSyntaxError, match="pairs"):
        notation.net_tuple({"P": "p1", "T": "a", "F": "p1, a", "m0": ""})


# -- the demo pack ------------------------------------------------------------------------
def test_the_demo_pack_has_the_new_exercises():
    pack = load_pack(DEMO)
    assert len(pack.exercises) == 7
    assert [name for name, _ in pack.chapters()][4:] == ["5 Markings", "6 Inductive Miner",
                                                           "7 Conformance"]
    types = [t.type for e in pack.exercises[4:] for t in e.sheet.tasks]
    assert types == ["tuple", "marking", "markings", "matrix", "ts", "yesno",
                     "cut", "log", "cut", "tree", "predict",
                     "replay", "number", "alignment", "ranking", "workflow"]
    for exercise in pack.exercises:
        assert validate(exercise) == [], exercise.title
    assert pack.points == 46 and pack.exercises[4].points == 10
    assert not pack.is_exam and not pack.wants_name


def test_markings_matrices_and_transition_systems_are_checked():
    ex = Exercise.at(MARKINGS)
    context = Context(ex)
    tuple_, marking, markings, matrix, ts, _safe = ex.sheet.tasks
    right = {"P": "{i, c1, c2, o}", "T": "register, send letter, call customer, archive",
             "F": "(i, register), (register, c1), (c1, send letter), (c1, call customer), "
                  "(send letter, c2), (call customer, c2), (c2, archive), (archive, o)",
             "m0": "[i]"}
    assert check(ex, tuple_, right, context).correct
    result = check(ex, tuple_, {**right, "F": "(i, register)", "m0": "[o]"}, context)
    assert result.status == PARTIAL and result.share == 0.5
    assert result.parts == {"P": CORRECT, "T": CORRECT, "F": PARTIAL, "m0": INCORRECT}
    assert "P = {c1, c2, i, o}" in model_answer_text(ex, tuple_, context)

    assert check(ex, marking, "[c1]", context).correct
    assert check(ex, marking, "c1 + i", context).status == INCORRECT
    assert check(ex, markings, "[i], [c1], [c2], [o]", context).correct
    result = check(ex, markings, "[i], [c1], [c1, c2]", context)
    assert result.status == PARTIAL and "1 is not reachable" in result.message
    assert "[c1], [c2], [i], [o]" in model_answer_text(ex, markings, context)

    cells = {"i\tregister": "-1", "c1\tregister": "1", "c1\tsend letter": "-1",
             "c1\tcall customer": "-1", "c2\tsend letter": "1", "c2\tcall customer": "1",
             "c2\tarchive": "-1", "o\tarchive": "1"}
    grid = {"rows": ["i", "c1", "c2", "o"],
            "columns": ["register", "send letter", "call customer", "archive"], "cells": cells}
    assert check(ex, matrix, grid, context).correct             # empty cells count as 0
    wrong = check(ex, matrix, {**grid, "cells": {**cells, "o\tarchive": "2"}}, context)
    assert wrong.status == PARTIAL and wrong.wrong_cells == [("o", "archive")]
    typed = "., register, send letter, call customer, archive\ni, -1, 0, 0, 0\nc1, 1, -1, -1, 0\n" \
            "c2, 0, 1, 1, -1\no, 0, 0, 0, 1"            # names with spaces: commas or | between cells
    assert check(ex, matrix, typed, context).correct
    assert check(ex, matrix, "a\np1 1", context).status == INCORRECT   # other rows and columns

    mine = "s0 -register-> s1\ns1 -send letter-> s2\ns1 -call customer-> s2\ns2 -archive-> s3\ninitial: s0"
    assert check(ex, ts, mine, context).correct
    result = check(ex, ts, "s0 -register-> s1\ninitial: s0", context)
    assert result.status == INCORRECT and "2 reachable states" in result.message
    assert check(ex, ts, "nonsense", context).status == UNKNOWN


def test_cuts_logs_trees_and_predictions_are_checked():
    ex = Exercise.at(CUTS)
    context = Context(ex)
    cut, sublog, cut2, tree, predict = ex.sheet.tasks
    assert check(ex, cut, "→ {a} {b, c, e} {d}", context).correct
    assert check(ex, cut, "seq {d} {b, c, e} {a}", context).status == PARTIAL   # right groups, wrong order
    assert check(ex, cut, "× {a} {b}", context).status == INCORRECT
    assert check(ex, sublog, "[<b,c>^3, <c,b>^2, <e>]", context).correct
    assert check(ex, sublog, "[<b,c>^3, <e>]", context).status == PARTIAL
    assert check(ex, cut2, "xor {e} {b, c}", context).correct               # × is unordered
    assert check(ex, tree, "→(a, ×(e, ∧(c, b)), d)", context).correct
    assert check(ex, tree, "→(a, ×(b, c, e), d)", context).status == PARTIAL
    assert check(ex, tree, "→(a, b)", context).status == INCORRECT
    assert check(ex, predict, "a, b, c, d, e", context).correct             # box(alpha_miner)
    assert check(ex, predict, "a, b", context).status == PARTIAL
    assert "{a, b, c, d, e}" in model_answer_text(ex, Task("predict", "p", settings={
        "box": "alpha_miner", "value": "net.transitions"}), context)


def test_replay_alignments_rankings_and_workflows_are_checked(tmp_path):
    folder = tmp_path / "ex"
    shutil.copytree(CONFORMANCE, folder)
    ex = Exercise.at(folder)
    context = Context(ex)
    replay, fitness, alignment, ranking, workflow = ex.sheet.tasks
    row = {"p": "6", "c": "6", "m": "0", "r": "0"}
    table = {"⟨a, b, c, d⟩": row, "⟨a, c, b, d⟩": row, "⟨a, e, d⟩": dict(row)}
    assert check(ex, replay, table, context).correct
    table["⟨a, e, d⟩"]["m"] = "1"
    result = check(ex, replay, table, context)
    assert result.status == PARTIAL and result.wrong_cells == [("⟨a, e, d⟩", "m")]
    assert check(ex, fitness, "1", context).correct
    assert check(ex, alignment, "a b >> d\na b c d", context).correct
    assert check(ex, alignment, "a b ≫ ≫ d\na b c ≫ d", context).status == UNKNOWN    # ≫ over ≫
    assert check(ex, alignment, "a ≫ b ≫ d\na e ≫ c d", context).status in (PARTIAL, INCORRECT)
    assert "a  b  ≫  d" in model_answer_text(ex, alignment, context)
    assert check(ex, ranking, ["m1.pnml", "m2.pnml", "m3.pnml"], context).correct
    assert check(ex, ranking, "m2 > m1 > m3", context).status == PARTIAL
    assert check(ex, ranking, "m1, m2", context).status == UNKNOWN            # rank all three
    assert "m1.pnml (1) > m2.pnml" in model_answer_text(ex, ranking, context)

    # The workflow: not built yet, then built with the boxes asked for.
    assert check(ex, workflow, "", context).status == UNKNOWN
    from openprocess.flow import Runner, Workflow, save
    wf = Workflow("my workflow", context.library)
    log = wf.add("typed_log", {"text": LOG, "name": "L"})
    miner = wf.add("inductive_miner", {})
    fit = wf.add("check_fit", {})
    wf.connect(log, miner)
    wf.connect(miner, fit, "model")
    wf.connect(log, fit, "log")
    save(wf, ex.workflow_path, Runner(context.library).run(wf), folder)
    result = check(ex, workflow, "my workflow.analysis", context)
    assert result.correct, result.message
    alpha = Workflow("my workflow", context.library)
    log = alpha.add("typed_log", {"text": LOG, "name": "L"})
    alpha.add("alpha_miner", {})
    alpha.connect(log, list(alpha.nodes)[-1])
    save(alpha, ex.workflow_path, None, folder)
    result = check(ex, workflow, "", context)
    assert result.status == INCORRECT and "missing: inductive_miner, check_fit" in result.message
    assert ex.summary().state == "new"
    ex.reset()
    assert not ex.workflow_path.exists()


def test_blocks_with_the_new_keys_are_validated(tmp_path):
    ex = exercise(tmp_path / "ex", "# Q\n" + block("ranking", over="m1.pnml") + block("predict")
                  + block("workflow") + block("alignment", trace="a, b") + block("matrix"),
                  **{"log.txt": LOG})
    problems = validate(ex)
    assert len(problems) == 5
    assert "at least two" in problems[0] and "needs “box:”" in problems[1]
    assert "needs “needs:” or “result:”" in problems[2]
    assert "no net" in problems[3] and "nothing to check against" in problems[4]
    with pytest.raises(TaskError, match="unknown compute"):
        lookup("nothing")
    assert lookup("box(alpha_miner).net.transitions")[1] == "alpha_miner|net.transitions"
    names = {name for name, _of, _help in describe_all()}
    assert {"im.cut", "incidence", "reachable", "replay", "box", "workflow"} <= names
    assert all(COMPUTED[k].help for k in COMPUTED)


# -- points, exams and variants -----------------------------------------------------------
def test_points_shares_and_marks(tmp_path):
    folder = tmp_path / "pack"
    ex = exercise(folder / "1 Sets", "# Sets\n\n" + block("set", answer="{a, b, c, d}", points="4")
                  + block("yesno", answer="yes") + block("open"), **{"log.txt": LOG})
    (folder / "pack.md").write_text("---\nexam: yes\ntime: 90\n---\n# Test\n")
    assert ex.points == 6 and ex.sheet.tasks[0].points == 4
    ex.save_progress({"q1": {"answer": "a, b", "status": "partial", "share": 0.5},
                      "q2": {"answer": "yes", "status": "correct"},
                      "q3": {"answer": "…", "status": "done"}})
    summary = ex.summary()
    assert summary.points == 6 and summary.earned == 4 and summary.state == "started"
    pack = load_pack(folder)
    assert pack.is_exam and pack.points == 6
    (row,) = marks(pack)
    assert row["earned"] == 4 and row["blocks"]["q1"] == {"status": "partial", "points": 4,
                                                         "earned": 2}
    csv = marks_csv(pack)
    assert csv.splitlines()[0].startswith("student,exercise") and ",TOTAL,,6.0,4.0" in csv
    # A partial answer's share comes from the check.
    result = check(ex, ex.sheet.tasks[0], "a, b", Context(ex))
    assert result.status == PARTIAL and result.share == 0.5


def test_an_exam_has_a_clock(tmp_path):
    folder = tmp_path / "exam"
    exercise(folder / "1 Q", "# Q\n" + block("yesno", answer="yes"))
    (folder / "pack.md").write_text("---\nexam: yes\ntime: 2h\n---\n# Exam\n")
    state = ExamState.load(load_pack(folder))
    assert state.started is None and state.minutes == 120 and state.deadline is None
    assert state.clock_text() == "Exam" and not state.closed()
    now = datetime(2026, 11, 1, 9, 0)
    state.start(now)
    assert state.deadline == now + timedelta(hours=2)
    assert state.clock_text(now + timedelta(minutes=5)) == "1:55:00 left"
    assert state.clock_text(now + timedelta(minutes=119)) == "1:00 left"
    assert state.closed(now + timedelta(hours=2)) and state.clock_text(now + timedelta(hours=3)) == "Time is up"
    again = ExamState.load(load_pack(folder))
    assert again.started == now                         # kept in my exam.json
    (folder / "pack.md").write_text("---\nexam: yes\ndeadline: 2026-11-01 10:00\n---\n# Exam\n")
    assert ExamState.load(load_pack(folder)).deadline == datetime(2026, 11, 1, 10, 0)
    assert parse_generate("net.pnml, cases: 30, length: 40") == ("net.pnml", 30, 40)
    with pytest.raises(ValueError):
        parse_generate("net.pnml, colour: red")


def test_variants_give_every_student_their_own_log(tmp_path):
    folder = tmp_path / "pack"
    shutil.copytree(MARKINGS, folder / "1 Variant")
    (folder / "1 Variant" / "question.md").write_text(
        "---\nseed: student\ngenerate: net.pnml, cases: 12\n---\n# V\n"
        + block("number", compute="cases") + block("set", compute="activities"))
    (folder / "pack.md").write_text("# Variants\n")
    pack = load_pack(folder)
    assert pack.wants_name and pack.student() is None
    pack.set_student("Ada")
    ada = Context(pack.exercises[0])
    assert ada.student == "Ada" and ada.seed == Context(pack.exercises[0], "ada ").seed
    assert Context(pack.exercises[0], "Bob").seed != ada.seed
    log = ada.event_log()
    assert sum(log.simple_log().values()) == 12
    assert check(pack.exercises[0], pack.exercises[0].sheet.tasks[0], "12", ada).correct
    assert check(pack.exercises[0], pack.exercises[0].sheet.tasks[1],
                 "register, send letter, call customer, archive", ada).correct
    assert validate(pack.exercises[0]) == []


# -- turning an exam into a pack ----------------------------------------------------------
EXAM = """1. Petri nets (10 points)

The net below models a complaint process.

a) Is the net sound? Motivate your answer. (2 points)
b) Give a firing sequence that ends in a deadlock. (3 points)
c) Draw a sound WF-net for the process. (5 points)

2. Process discovery (8 points)

a. Fill in the footprint of the log.
b. Which cut does the Inductive Miner find first?
c. How many places does the α-algorithm's net have?
"""


def test_an_exam_becomes_a_skeleton_pack(tmp_path):
    questions = split_questions(EXAM)
    assert [q.title for q in questions] == ["Petri nets", "Process discovery"]
    assert questions[0].points == 10 and [p.letter for p in questions[0].parts] == ["a", "b", "c"]
    assert questions[0].parts[1].points == 3 and questions[0].intro.startswith("The net below")
    assert [p.kind for p in questions[0].parts] == ["yesno", "trace", "net"]
    assert [p.kind for p in questions[1].parts] == ["footprint", "cut", "number"]
    assert guess_type("Explain why the net is not free-choice.") == "open"
    assert guess_type("Give an optimal alignment of the trace.") == "alignment"
    assert guess_type("Rank the models by precision.") == "ranking"
    target = tmp_path / "Exam 2025"
    written = write_pack(EXAM, target, "Exam 2025")
    assert [p.name for p in written] == ["pack.md", "question.md", "question.md"]
    assert (target / "pack.md").read_text().startswith("---\nexam: yes\n")
    sheet = parse_sheet((target / "1 Petri nets" / "question.md").read_text())
    assert sheet.title == "1 · Petri nets (10 points)"
    assert [t.type for t in sheet.tasks] == ["yesno", "trace", "net"]
    assert sheet.tasks[1].points == 3 and sheet.tasks[1].get("ends") == "deadlock"
    todos = todo_list(target)
    assert len(todos) >= 6 and all("question.md:" in t for t in todos)
    pack = load_pack(target)
    assert pack.is_exam and len(pack.exercises) == 2 and pack.points == 18
    with pytest.raises(ValueError, match="no questions"):
        write_pack("Nothing numbered here.", tmp_path / "empty")


# -- the command line -----------------------------------------------------------------------
def test_the_command_line_marks_imports_and_lists_computes(tmp_path, capsys):
    from openprocess.cli import main
    assert main(["exercises", "computes"]) == 0
    out = capsys.readouterr().out
    assert "Of the net" in out and "im.cut" in out and "reachability graph" in out
    target = tmp_path / "Exam"
    (tmp_path / "exam.txt").write_text(EXAM)
    assert main(["exercises", "import", str(tmp_path / "exam.txt"), str(target)]) == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "TODO(s) left" in out and (target / "pack.md").exists()
    folder = tmp_path / "pack"
    shutil.copytree(DEMO, folder)
    ex = Exercise.at(folder / "5 Markings" / "Exercise 5.1 Markings and matrices")
    ex.save_progress({"q2": {"answer": "[c1]", "status": "correct"},
                      "q4": {"status": "partial", "share": 0.5}})
    assert main(["exercises", "marks", str(folder), "--blocks"]) == 0
    out = capsys.readouterr().out
    assert "Markings and matrices" in out and "2 / 10" in out and "TOTAL" in out
    assert "q4         partial         1 / 2" in out
    assert main(["exercises", "marks", str(folder), "--csv"]) == 0
    assert "q2,1.0,1.0,correct" in capsys.readouterr().out
    assert main(["exercises", "check", str(folder)]) == 0
    assert "No problems found" in capsys.readouterr().out
    (folder / "5 Markings" / "Exercise 5.1 Markings and matrices" / "my answers.json").unlink()
