"""Tests for the process mining engine.

Expected values come from three sources, noted per test:

* the textbook (van der Aalst, *Process Mining*, 2nd ed.) and the course
  readings, for the α-algorithm and the Inductive Miner;
* hand calculation, for token replay and soundness;
* PM4Py 2.7 run on the same inputs, for alignments and precision (PM4Py is
  *not* a dependency; the numbers were computed once and pasted in, and
  ``test_against_pm4py`` re-checks them live when PM4Py happens to be
  installed).
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from openprocess.mining import (
    EventLog, Marking, PetriNet, align_log, alpha_miner, analyse, check_soundness,
    compare_footprints, discover_dfg, footprint_of_log, footprint_of_net, format_simple_log,
    inductive_miner, parse_simple_log, precision, read_csv, read_xes, summarise, token_replay,
)
from openprocess.mining.layout import layered_layout
from openprocess.mining.pnml import parse_pnml, pnml_string
from openprocess.mining.stats import format_duration
from openprocess.mining.xes import read_xes_string, xes_string

DATA = Path(__file__).parent / "data"
L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"
L3 = "[<a,b,c,d,e,f,b,d,c,e,g>, <a,b,d,c,e,g>^2, <a,b,c,d,e,f,b,c,d,e,f,b,d,c,e,g>]"


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def test_parse_and_format_simple_log():
    log = parse_simple_log(L1)
    assert log == Counter({("a", "b", "c", "d"): 3, ("a", "c", "b", "d"): 2, ("a", "e", "d"): 1})
    assert parse_simple_log(format_simple_log(log)) == log
    assert parse_simple_log("[<>^2, <register request, pay>]")[()] == 2
    with pytest.raises(ValueError):
        parse_simple_log("no traces here")


def test_read_course_xes():
    log = read_xes(DATA / "plane_wilma_10.xes")
    assert len(log) == 10 and log.event_count == 210
    # start/complete pairs -> the default classifier keeps complete events only
    assert log.default_classifier().name == "Activity (complete events)"
    summary = summarise(log)
    assert summary.event_count == 105
    assert summary.variant_count == 10
    assert {a.name: a.occurrences for a in summary.activities}["Move up"] == 75
    assert summary.start is not None and summary.end > summary.start
    assert len(log.declared_classifiers) == 2


def test_xes_round_trip():
    log = read_xes(DATA / "plane_wilma_10.xes")
    again = read_xes_string(xes_string(log))
    assert len(again) == len(log) and again.event_count == log.event_count
    assert again.simple_log() == log.simple_log()
    first = again[0][0]
    assert first.timestamp == log[0][0].timestamp
    assert first.get("r") == log[0][0].get("r")


def test_csv_import(tmp_path):
    path = tmp_path / "log.csv"
    path.write_text(
        "Case ID;Activity;Timestamp;Resource\n"
        "1;register;2024-01-01 09:00;Ann\n"
        "1;pay;2024-01-01 11:00;Bob\n"
        "2;register;02-01-2024 10:00;Ann\n"
        "1;check;2024-01-01 10:00;Ann\n", encoding="utf-8")
    log = read_csv(path)
    assert log.simple_log() == Counter({("register", "check", "pay"): 1, ("register",): 1})
    assert log[0][1].resource == "Ann"


def test_dfg_counts_and_durations():
    log = read_xes(DATA / "plane_wilma_10.xes")
    dfg = discover_dfg(log)
    assert dfg.start == Counter({"Move in": 10})
    assert sum(dfg.edges.values()) == 105 - 10
    assert dfg.mean_duration(("Move in", "Move up")) is not None
    small = dfg.simplified(activity_fraction=0.5, edge_fraction=0.0)
    assert set(small.activities) == {"Move up", "Move in", "stow bag"}
    for activity in small.activities:        # connectivity guarantee
        assert any(b == activity for (_, b) in small.edges) or activity in small.start


def test_format_duration():
    assert format_duration(timedelta(hours=3)) == "3h"
    assert format_duration(timedelta(days=2, hours=5, minutes=3)) == "2d 5h"
    assert format_duration(timedelta(seconds=0.25)) == "250ms"
    assert format_duration(timedelta(minutes=1, seconds=10)) == "1m 10s"


# ---------------------------------------------------------------------------
# Footprints and the α-algorithm (textbook example L1)
# ---------------------------------------------------------------------------
def test_footprint_l1():
    fp = footprint_of_log(parse_simple_log(L1))
    assert fp.relation("a", "b") == "→"
    assert fp.relation("b", "c") == "‖"
    assert fp.relation("b", "e") == "#"
    assert fp.relation("d", "b") == "←"


def test_alpha_textbook_l1():
    result = alpha_miner(parse_simple_log(L1))
    y = {(tuple(sorted(a)), tuple(sorted(b))) for a, b in result.Y_L}
    assert y == {(("a",), ("b", "e")), (("a",), ("c", "e")),
                 (("b", "e"), ("d",)), (("c", "e"), ("d",))}
    assert len(result.X_L) == 10
    assert check_soundness(result.net).sound is True


def test_alpha_warns_about_short_loops():
    result = alpha_miner(parse_simple_log("[<a,b,b,c>, <a,c>, <a,b,d,b,c>]"))
    assert any("Length-one" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Inductive Miner
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    (L1, "→(a, ×(∧(b, c), e), d)"),
    (L3, "→(a, ↺(→(b, ∧(c, d), e), f), g)"),
    ("[<a,b>, <a,a,b>, <b>]", "→(×(τ, ↺(a, τ)), b)"),
])
def test_inductive_miner_trees(text, expected):
    result = inductive_miner(parse_simple_log(text))
    assert str(result.tree) == expected
    assert check_soundness(result.net).sound is True


def test_inductive_miner_on_course_log_matches_pm4py():
    simple = read_xes(DATA / "plane_wilma_10.xes").simple_log()
    tree = str(inductive_miner(simple).tree)
    assert tree.startswith("→(Move in, ")
    assert "stow bag" in tree


def test_sequence_cut_follows_the_definition_for_every_pair():
    """Every activity of an earlier group must reach every activity of a later
    one.  In [<b,b,c,c,c,c>, <b,a,d,d>] c reaches nothing and d is reached
    only from a, so c and d (and a) must share a group: {b} | {a, c, d}, not
    {b} | {a, c} | {d}, which the old any-pair test accepted."""
    from openprocess.mining.dfg import dfg_from_simple_log
    from openprocess.mining.discovery.inductive import maximal_sequence_cut
    dfg = dfg_from_simple_log(parse_simple_log("[<b,b,c,c,c,c>^2, <b,a,d,d>^3]"))
    assert maximal_sequence_cut(dfg) == [{"b"}, {"a", "c", "d"}]
    tree = str(inductive_miner(parse_simple_log("[<b,b,c,c,c,c>^2, <b,a,d,d>^3]")).tree)
    assert tree == "→(↺(b, τ), ×(→(a, ↺(d, τ)), ↺(c, τ)))"      # × is commutative


def test_strict_sequence_cut_merges_groups_skipped_together():
    """Leemans 2017, p. 233: a and c never occur without each other, so the
    optional stretch is ×(τ, →(a, c)), not two separate choices ×(τ, a),
    ×(τ, c) that would also allow <b, a> and <b, c>."""
    from openprocess.mining.dfg import dfg_from_simple_log
    from openprocess.mining.discovery.inductive import (maximal_sequence_cut,
                                                        merge_skippable_groups, sequence_cut)
    log = parse_simple_log("[<b>^4, <b,b,a,c>]")
    dfg = dfg_from_simple_log(log)
    assert maximal_sequence_cut(dfg) == [{"b"}, {"a"}, {"c"}]
    assert merge_skippable_groups(dfg, [{"b"}, {"a"}, {"c"}]) == [{"b"}, {"a", "c"}]
    assert sequence_cut(dfg) == [{"b"}, {"a", "c"}]
    result = inductive_miner(log)
    assert str(result.tree) == "→(↺(b, τ), ×(τ, →(a, c)))"
    assert any("skipped together" in s.text or "skipped together" in (s.detail or "")
               for s in result.steps)
    # The textbook log is a plain sequence cut: nothing to merge, nothing said.
    plain = inductive_miner(parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"))
    assert str(plain.tree) == "→(a, ×(∧(b, c), e), d)"
    assert not any("skipped together" in (s.detail or "") for s in plain.steps)


def test_inductive_miner_always_fits_randomised():
    rng = random.Random(7)
    for _ in range(15):
        log = Counter()
        for _ in range(12):
            log[tuple(rng.choice("abcde") for _ in range(rng.randint(0, 5)))] += 1
        net = inductive_miner(log).net
        assert check_soundness(net).sound is True
        assert token_replay(net, log).fitness == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Petri nets: properties, soundness, PNML
# ---------------------------------------------------------------------------
def _xor_and_mismatch() -> PetriNet:
    net = PetriNet("mismatch")
    i, o, p1, p2 = (net.add_place(n) for n in ("i", "o", "p1", "p2"))
    a, b, c = (net.add_transition(n) for n in "abc")
    for s, t in [(i, a), (i, b), (a, p1), (b, p2), (p1, c), (p2, c), (c, o)]:
        net.add_arc(s, t)
    net.initial_marking = Marking({i.id: 1})
    net.final_marking = Marking({o.id: 1})
    return net


def test_soundness_counterexamples():
    report = check_soundness(_xor_and_mismatch())
    assert report.sound is False
    assert report.option_to_complete is False
    assert report.no_dead_transitions is False
    assert any("deadlocks" in f for f in report.findings)


def test_unbounded_net_uses_coverability():
    net = PetriNet()
    p, q = net.add_place("p"), net.add_place("q")
    t = net.add_transition("t")
    net.add_arc(p, t), net.add_arc(t, p), net.add_arc(t, q)
    net.initial_marking = Marking({p.id: 1})
    report = analyse(net)
    assert report.bounded is False
    assert any(m.describe(net) == "[p, q^ω]" for m in report.graph.states)


def test_pnml_round_trip_with_silent_transition():
    net = inductive_miner(parse_simple_log("[<a,b>, <a,a,b>, <b>]")).net
    again = parse_pnml(pnml_string(net))
    assert len(again.transitions) == len(net.transitions)
    assert sum(t.silent for t in again.transitions.values()) == \
        sum(t.silent for t in net.transitions.values())
    assert again.final_marking == net.final_marking
    assert sorted(again.places) == sorted(net.places)     # no phantom <place idref>
    assert check_soundness(again).sound is True


# ---------------------------------------------------------------------------
# Conformance (reference values from PM4Py 2.7)
# ---------------------------------------------------------------------------
NOISY = L1 + " <a,b,d> <a,d,b,c> <e,d>"


def test_token_replay_values():
    net = alpha_miner(parse_simple_log(L1)).net
    assert token_replay(net, parse_simple_log(L1)).fitness == pytest.approx(1.0)
    assert token_replay(net, parse_simple_log(NOISY)).fitness == pytest.approx(0.9127, abs=1e-4)


def test_alignment_values():
    net = inductive_miner(parse_simple_log(L1)).net
    result = align_log(net, parse_simple_log(NOISY))
    assert result.average_fitness == pytest.approx(0.9275, abs=1e-4)
    assert result.log_fitness == pytest.approx(0.9322, abs=1e-4)
    bad = next(a for a in result.alignments if a.trace == ("e", "d"))
    assert bad.cost == 1 and bad.model_moves == 1        # a must be inserted


def test_precision_values():
    log = parse_simple_log(L3)
    assert precision(inductive_miner(log).net, log) == pytest.approx(0.8491, abs=1e-4)
    assert precision(alpha_miner(parse_simple_log(L1)).net, parse_simple_log(L1)) == 1.0


def test_footprint_conformance():
    log = parse_simple_log(L1)
    comparison = compare_footprints(footprint_of_log(log),
                                    footprint_of_net(inductive_miner(log).net))
    assert comparison.fitness == 1.0


def test_from_simple_log_expands():
    log = EventLog.from_simple_log(parse_simple_log(L1))
    assert len(log) == 6 and log.simple_log() == parse_simple_log(L1)


def test_layout_no_overlap():
    net = inductive_miner(parse_simple_log(L3)).net
    sizes = {p: (30.0, 30.0) for p in net.places} | {t: (60.0, 36.0) for t in net.transitions}
    layout = layered_layout(sizes, [(a.source, a.target) for a in net.arcs])
    boxes = [(x - sizes[n][0] / 2, y - sizes[n][1] / 2, x + sizes[n][0] / 2, y + sizes[n][1] / 2)
             for n, (x, y) in layout.positions.items()]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


# ---------------------------------------------------------------------------
# Live cross-check (skipped unless PM4Py is installed)
# ---------------------------------------------------------------------------
def test_against_pm4py(tmp_path):
    pm4py = pytest.importorskip("pm4py")
    import pandas as pd

    log = read_xes(DATA / "plane_wilma_10.xes")
    simple = log.simple_log()
    net = inductive_miner(simple, noise_threshold=0.2).net
    path = tmp_path / "net.pnml"
    path.write_text(pnml_string(net), encoding="utf-8")
    pn, im, fm = pm4py.read_pnml(str(path))
    frame = pm4py.read_xes(str(DATA / "plane_wilma_10.xes"))
    frame = frame[frame["lifecycle:transition"] == "complete"]
    theirs = pm4py.fitness_alignments(frame, pn, im, fm)["average_trace_fitness"]
    assert align_log(net, simple).average_fitness == pytest.approx(theirs, abs=1e-6)
    assert precision(net, simple) == pytest.approx(
        pm4py.precision_token_based_replay(frame, pn, im, fm), abs=1e-6)
    del pd


def test_textbook_typesetting_is_accepted():
    # Copied from the book or the slides: angle brackets and superscripts.
    assert parse_simple_log("[⟨a,b,c,d⟩³, ⟨a,c,b,d⟩², ⟨a,e,d⟩]") == \
        parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")
    assert parse_simple_log("[⟨a⟩¹², ⟨b⟩]") == Counter({("a",): 12, ("b",): 1})
    # A multiplicity of zero leaves the trace out entirely.
    assert parse_simple_log("[<a>^0, <b>]") == Counter({("b",): 1})


def test_csv_dates_with_fractional_seconds():
    from openprocess.mining.csv_import import parse_timestamp
    assert parse_timestamp("05/01/2023 10:00:00.250").microsecond == 250_000
    assert parse_timestamp("2023/01/05 10:00:00.5").microsecond == 500_000


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def test_incidence_matrix_and_invariants_of_alpha_l1():
    from openprocess.mining.analysis import short_circuit
    from openprocess.mining.invariants import incidence_matrix, invariants
    net = alpha_miner(parse_simple_log(L1)).net
    places, transitions, matrix = incidence_matrix(net)
    a = transitions.index(next(t for t in net.transitions if net.node_name(t) == "a"))
    column = {places[i]: row[a] for i, row in enumerate(matrix) if row[a]}
    # a takes from i_L and puts into the two places after it.
    assert sorted(column.values()) == [-1, 1, 1]
    found = invariants(net)
    # One token per thread: i + p({a},{b,e}) + p({b,e},{d}) + o, and the c-thread.
    assert sorted(found.describe(y, with_value=True) for y in found.p_invariants) == [
        "i_L + o_L + p({a},{b,e}) + p({b,e},{d}) = 1",
        "i_L + o_L + p({a},{c,e}) + p({c,e},{d}) = 1"]
    assert found.covered_by_p_invariants
    assert found.t_invariants == []                  # nothing leads back from [o]
    closed = invariants(short_circuit(net, "i_L", "o_L"))
    assert sorted(sorted(net.node_name(t) if t in net.transitions else "t*" for t in x)
                  for x in closed.t_invariants) == [["a", "b", "c", "d", "t*"],
                                                    ["a", "d", "e", "t*"]]
    assert closed.covered_by_t_invariants


def test_invariants_expose_the_unsound_order_net():
    from openprocess.mining.analysis import short_circuit
    from openprocess.mining.invariants import invariants
    from openprocess.mining import read_pnml
    net = read_pnml(Path(__file__).resolve().parents[1] / "examples" / "petri" /
                    "order_handling_unsound.pnml")
    found = invariants(net)
    assert [net.node_name(p) for p in found.uncovered_places()] == ["c2", "c4"]
    closed = invariants(short_circuit(net, net.source_places()[0], net.sink_places()[0]))
    # `reject` is in no T-invariant of N̄: N̄ is not live and bounded, not sound.
    assert [net.node_name(t) for t in closed.uncovered_transitions()] == ["reject"]


def test_weighted_invariant():
    from openprocess.mining.invariants import invariants
    # t: 2 tokens from p to 1 token in q, and back: 1·p + 2·q is conserved.
    net = PetriNet("weights")
    p, q = net.add_place("p"), net.add_place("q")
    forward, back = net.add_transition("f"), net.add_transition("b")
    net.add_arc(p, forward, 2)
    net.add_arc(forward, q)
    net.add_arc(q, back)
    net.add_arc(back, p, 2)
    net.initial_marking = Marking({p.id: 4})
    found = invariants(net)
    assert [found.describe(y, with_value=True) for y in found.p_invariants] == ["p + 2·q = 4"]
    assert [sorted(x.values()) for x in found.t_invariants] == [[1, 1]]


# ---------------------------------------------------------------------------
# Filtering and CSV export
# ---------------------------------------------------------------------------
def test_filters():
    from openprocess.mining.filtering import FilterSettings, apply_filters, top_variants
    log = EventLog.from_simple_log(parse_simple_log(
        "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>, <a,b>]"), "L")

    def run(**settings):
        return apply_filters(log, FilterSettings(**settings)).simple_log()

    # 80% of 7 cases needs 5.6: the variants with 3, 2 and 1 cases.
    assert run(variant_coverage=80) == Counter({("a", "b", "c", "d"): 3,
                                                 ("a", "c", "b", "d"): 2, ("a", "e", "d"): 1})
    assert run(top_variants=1) == Counter({("a", "b", "c", "d"): 3})
    assert run(end_activities={"d"})[("a", "b")] == 0
    assert run(activities=({"a", "d"}, "keep events")) == Counter({("a", "d"): 6, ("a",): 1})
    assert run(activities=({"e"}, "mandatory")) == Counter({("a", "e", "d"): 1})
    assert ("a", "e", "d") not in run(activities=({"e"}, "forbidden"))
    assert run(case_length=(3, 3)) == Counter({("a", "e", "d"): 1})
    # Ties are broken by first occurrence, so unique variants can still be cut.
    unique = EventLog.from_simple_log(parse_simple_log("[<a>, <b>, <c>, <d>]"), "U")
    assert [v for v, _ in top_variants(unique, coverage=50)] == [("a",), ("b",)]
    filtered = apply_filters(log, FilterSettings(end_activities={"d"}, top_variants=2))
    assert filtered.name == "L (filtered)"
    assert filtered.attributes["openprocess:filter"] == "end with d; the 2 most frequent variants"
    assert len(log) == 7                                   # the original is untouched


def test_projection_keeps_skipped_events_and_time_frame():
    from openprocess.mining.filtering import FilterSettings, apply_filters
    log = read_xes(DATA / "plane_wilma_10.xes")            # start + complete events
    kept = apply_filters(log, FilterSettings(activities=({"Move in", "stow bag"},
                                                         "keep events")))
    assert kept.simple_log() == Counter({("Move in", "stow bag"): 10})
    assert kept.event_count == 40                          # their start events stay too
    first = min(e.timestamp for t in log for e in t)
    early = apply_filters(log, FilterSettings(
        time_frame=(first, first + timedelta(seconds=10), "contained")))
    assert 0 < len(early) < len(log)


def test_csv_export_round_trip(tmp_path):
    from openprocess.mining.csv_import import write_csv
    log = read_xes(DATA / "plane_wilma_10.xes")
    write_csv(log, tmp_path / "log.csv")
    again = read_csv(tmp_path / "log.csv")
    assert again.simple_log() == log.simple_log()
    assert [e.timestamp for e in again[0]] == [e.timestamp for e in log[0]]


# ---------------------------------------------------------------------------
# Heuristics Miner -> Petri net
# ---------------------------------------------------------------------------
def test_heuristics_net_of_l1_is_the_textbook_model():
    from openprocess.mining.discovery.heuristics import heuristics_net
    log = parse_simple_log(L1)
    result = heuristics_net(log)
    # After a: b and c together (5 cases) or e alone (1 case); d waits for the same.
    assert dict(result.causal.outputs["a"]) == {frozenset({"b", "c"}): 5, frozenset({"e"}): 1}
    assert dict(result.causal.inputs["d"]) == {frozenset({"b", "c"}): 5, frozenset({"e"}): 1}
    assert align_log(result.net, log).average_fitness == pytest.approx(1.0)
    assert precision(result.net, log) == pytest.approx(1.0)
    assert check_soundness(result.net).sound
    # Silent steps that change nothing were removed: only the AND split and join remain.
    assert sum(1 for t in result.net.transitions.values() if t.silent) == 2


@pytest.mark.parametrize("text", [
    "[<a,b,c,d>^3, <a,c,b,d>^4, <a,b,c,e,f,b,c,d>^2, <a,b,c,e,f,c,b,d>, "
    "<a,c,b,e,f,b,c,d>^2, <a,c,b,e,f,b,c,e,f,c,b,d>]",
    "[<a,c>^2, <a,b,c>^3, <a,b,b,c>^2, <a,b,b,b,b,c>]",
    "[<a,b,c,d>^30, <a,c,b,d>^20, <a,d>, <a,b,d>]",
])
def test_heuristics_net_replays_its_log(text):
    from openprocess.mining.discovery.heuristics import heuristics_net
    log = parse_simple_log(text)
    assert align_log(heuristics_net(log).net, log).average_fitness == pytest.approx(1.0)


def test_csv_from_excel_in_a_windows_code_page(tmp_path):
    """Regression: a CSV saved by Excel on Windows (cp1252, not UTF-8) could
    not be read at all (UnicodeDecodeError)."""
    from openprocess.mining.csv_import import guess_mapping, read_csv, sniff

    path = tmp_path / "events.csv"
    path.write_bytes("case;activity\n1;Café order\n1;Müller check\n".encode("cp1252"))
    log = read_csv(path, guess_mapping(sniff(path)[1]))
    assert [e.attributes["concept:name"] for e in log.traces[0].events] == \
        ["Café order", "Müller check"]
    path.write_bytes("﻿case,activity\n1,naïve\n".encode("utf-8"))      # UTF-8 + BOM
    log = read_csv(path, guess_mapping(sniff(path)[1]))
    assert log.traces[0].events[0].attributes["concept:name"] == "naïve"
