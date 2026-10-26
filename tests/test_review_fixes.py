"""Regressions from the 0.13 review: each test is one bug that was found and fixed."""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from openprocess.mining import (BY_NAME, Event, EventLog, Trace, align_log, alpha_miner, check_soundness,
                                parse_simple_log, precision, read_xes, region_miner, token_replay)
from openprocess.mining.pnml import parse_pnml, pnml_string

ROOT = Path(__file__).resolve().parents[1]
L1 = parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")


# -- mining ------------------------------------------------------------------------------
def test_alpha_on_a_wide_choice_is_fast_and_keeps_the_textbook_answer():
    """Step 4 used to list every subset of a #-clique: 16 alternatives took 14 s."""
    started = time.time()
    result = alpha_miner(Counter({("a", f"b{i}", "d"): 1 for i in range(40)}))
    assert time.time() - started < 2
    assert len(result.Y_L) == 2 and token_replay(result.net, Counter({("a", "b7", "d"): 1})).fitness == 1.0
    book = alpha_miner(L1)
    assert len(book.X_L) == 10 and len(book.Y_L) == 4 and book.X_L_listed


def test_pnml_without_an_initial_marking_starts_in_the_source_place():
    """Alignments reported fitness 0 for an unmarked WF-net while replay said 1."""
    net = alpha_miner(L1).net
    unmarked = parse_pnml(pnml_string(net).replace("<initialMarking><text>1</text></initialMarking>", ""))
    assert dict(unmarked.initial_marking) == {"i_L": 1}
    assert align_log(unmarked, L1).average_fitness == 1.0 and token_replay(unmarked, L1).fitness == 1.0


def test_pnml_marking_naming_no_place_is_refused():
    with pytest.raises(ValueError, match="not in the net"):
        parse_pnml(pnml_string(alpha_miner(L1).net).replace('idref="o_L"', 'idref="nowhere"'))


def test_heuristics_net_with_a_loop_back_to_the_start_is_a_wf_net():
    from openprocess.mining.discovery.heuristics import heuristics_net
    for text in ("[<a,b,c>^5, <a,b,a,b,c>^2]", "[<a,b,a,b>, <a,b>]"):
        log = parse_simple_log(text)
        net = heuristics_net(log).net
        assert len(net.source_places()) == 1 and check_soundness(net).workflow.is_workflow_net
        assert token_replay(net, log).fitness == 1.0


def test_naive_timestamps_count_as_utc():
    from openprocess.mining.dotted import Dots, DotSettings
    from openprocess.mining.filtering import filter_time_frame
    log = EventLog(traces=[Trace({"concept:name": "c"}, [Event({"concept:name": "a",
                                                                "time:timestamp": datetime(2024, 1, 1)})])])
    assert len(Dots(log, BY_NAME, DotSettings())) == 1
    wilma = read_xes(ROOT / "tests" / "data" / "plane_wilma_10.xes")
    assert len(filter_time_frame(wilma, datetime(1900, 1, 1), datetime(4000, 1, 1))) == len(wilma)


def test_an_unreachable_final_marking_is_named_not_blamed_on_the_state_cap():
    from openprocess.mining.petrinet import Marking
    net = alpha_miner(L1).net.copy()
    net.final_marking = Marking({"i_L": 1, "o_L": 1})
    notes = align_log(net, L1).notes
    assert "cannot be reached" in notes[0] and "500,000" not in notes[0]


def test_region_miner_says_when_it_has_no_final_marking():
    result = region_miner(L1, "prefix", "set")
    assert not result.net.final_marking and any("final marking" in w for w in result.warnings)
    with pytest.raises(ValueError, match="empty"):
        region_miner(Counter())


def test_check_region_takes_the_set_as_text():
    from openprocess.mining.regions import check_region
    from openprocess.mining.transition_system import parse_transition_system
    ts = parse_transition_system("s0 -a-> s1, s1 -b-> s2\ninitial: s0")
    assert check_region(ts, "{s1}").states == frozenset({"s1"})


def test_terminating_cites_a_publication():
    from openprocess.mining.definitions import BY_KEY
    terminating = BY_KEY["terminating"]
    assert "Desel" in terminating.source and "Section 2" in terminating.source
    assert "at least one transition" in terminating.notes[0]


# -- flow and the command line ---------------------------------------------------------------
def test_running_a_workflow_does_not_import_the_scripts_beside_it(tmp_path):
    from openprocess.flow.library import library_for
    (tmp_path / "make_workflow.py").write_text("raise SystemExit('this must not run')\n")
    library = library_for(tmp_path)
    assert not library.broken and all(not s.custom for s in library)


def test_a_swept_choice_must_be_one_of_the_choices():
    from openprocess.flow import Sweep, Workflow
    from openprocess.flow.boxes.check import check_fit
    wf = Workflow("w")
    with pytest.raises(ValueError):
        wf.add(check_fit, {"method": Sweep(("Token replay", "Bogus"))})


def test_command_line_messages(tmp_path, capsys):
    from openprocess import cli
    assert cli.main(["datasets", "fetch", "No such dataset"]) == 1
    assert "No dataset called" in capsys.readouterr().err
    (tmp_path / "x.cpn").write_text("<workspaceElements/>")
    assert cli.main(["mine", "stats", str(tmp_path / "x.cpn")]) == 1
    assert "not an XES event log" in capsys.readouterr().err


# -- learn ------------------------------------------------------------------------------------
def _exercise(tmp_path, question: str, net: bool = False):
    from openprocess.learn.pack import Exercise
    from openprocess.mining import write_pnml
    folder = tmp_path / "Exercise"
    folder.mkdir()
    (folder / "log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")
    if net:
        write_pnml(alpha_miner(L1).net, folder / "net.pnml")
    (folder / "question.md").write_text(question)
    return Exercise.at(folder)


def test_learn_box_computes_choices_and_places(tmp_path):
    from openprocess.learn.checks import check, validate
    exercise = _exercise(tmp_path, '''# E

```answer
type: number
compute: box(check_fit).metrics.fitness
```

```answer
type: choice
compute: sound
- [ ] Yes
- [ ] No
```

```answer
type: set
compute: alpha.P_L
```

```answer
type: set
compute: box(alpha_miner).net.T
```
''', net=True)
    assert validate(exercise) == []
    fitness, sound, places, transitions = exercise.sheet.tasks
    assert check(exercise, fitness, "1").correct
    assert check(exercise, sound, 0).correct and not check(exercise, sound, 1).correct
    assert check(exercise, places, "i_L, o_L, p({a},{b,e}), p({a},{c,e}), p({b,e},{d}), p({c,e},{d})").correct
    assert check(exercise, transitions, "{a, b, c, d, e}").correct


def test_learn_validate_catches_what_would_fail_at_check_time(tmp_path):
    from openprocess.learn.checks import validate
    exercise = _exercise(tmp_path, '''---
point: 3
---
# E

```answer
type: trace
ends: finish
```
''', net=True)
    problems = " ".join(validate(exercise))
    assert "unknown key “point”" in problems and "ends: finish" in problems


def test_learn_readers():
    from openprocess.learn import answers, notation
    assert notation.alignment("m a b\nm a b") == (["m", "a", "b"], ["m", "a", "b"])   # m is an activity
    assert notation.alignment("L | a | b\nM | a | b") == (["a", "b"], ["a", "b"])        # L and M are labels
    assert notation.alignment("log: a b\nmodel: a b") == (["a", "b"], ["a", "b"])
    assert answers.number("−1") == -1 and answers.number("∞") == float("inf")
    assert str(notation.tree("\\rightarrow(a, \\times(b, c))")) == str(notation.tree("→(a, ×(b, c))"))


# -- found on BPI 2019 by the cross-check against PM4Py ---------------------------------------
def test_replay_through_many_silent_steps_keeps_the_inductive_miners_fitness():
    """The silent-step search gave up after 2,000 markings on a block-structured
    net with many optional parallel branches, and replay counted the tokens it
    then had to force: IM fitness 0.935 on the BPI 2019 log instead of 1.
    Here a trace skips 14 optional branches in parallel: enabling *end* needs
    every skip, in some order, 2^14 markings for a search over all τ moves."""
    from openprocess.mining.conformance.quality import precision
    from openprocess.mining.processtree import Operator, ProcessTree, to_petri_net
    branches = [ProcessTree.node(Operator.XOR, [ProcessTree.leaf(f"x{i}"), ProcessTree.tau()]) for i in range(14)]
    tree = ProcessTree.node(Operator.SEQUENCE, [ProcessTree.leaf("start"),
                                                ProcessTree.node(Operator.PARALLEL, branches),
                                                ProcessTree.leaf("end")])
    net = to_petri_net(tree)
    log = Counter({("start", "end"): 3, ("start", "x3", "x7", "end"): 2, ("start", *[f"x{i}" for i in range(14)], "end"): 1})
    started = time.time()
    replay = token_replay(net, log)
    assert replay.fitness == 1.0 and replay.fitting_traces == replay.trace_count
    assert 0 < precision(net, log) <= 1
    assert time.time() - started < 30


def test_imf_cuts_a_trace_at_the_best_split_points():
    """A sequence cut's sublogs are consecutive segments of each trace; an
    event out of order is dropped, not pulled into its group (Leemans,
    Fahland & van der Aalst 2013; ProM and PM4Py do the same)."""
    from openprocess.mining.dfg import dfg_from_simple_log
    from openprocess.mining.discovery.inductive import split_log
    from openprocess.mining.processtree import Operator
    log = Counter({("a", "b", "c"): 5, ("a", "c", "b", "c"): 1})
    a, b, c = split_log(log, Operator.SEQUENCE, [{"a"}, {"b"}, {"c"}], dfg_from_simple_log(log))
    assert a == Counter({("a",): 6})
    assert b == Counter({("b",): 5, (): 1})            # the late b of the odd trace is dropped
    assert c == Counter({("c",): 5, ("c", "c"): 1})
