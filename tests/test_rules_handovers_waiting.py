"""Handover of work, Declare rules and waiting times (0.17), on logs small
enough to work every number out by hand."""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from openprocess.flow import Runner, Workflow, box
from openprocess.flow.boxes.measure import handover_of_work, waiting_times as waiting_box
from openprocess.flow.boxes.rules import check_rules, discover_rules, typed_rules
from openprocess.flow.record import fingerprint
from openprocess.flow.types import Table
from openprocess.mining import declare, inductive_miner, parse_simple_log, token_replay
from openprocess.mining.conformance.token_replay import replay_trace
from openprocess.mining.conformance.waiting import waiting_times
from openprocess.mining.declare import Rule
from openprocess.mining.log import Event, EventLog, Trace
from openprocess.mining.organisation import social_network

T0 = datetime(2024, 1, 1, 9, tzinfo=timezone.utc)
HOUR = 3600.0


def timed_log(*cases, name: str = "orders") -> EventLog:
    """Cases as lists of (activity, hour, resource); None for no time or nobody."""
    log = EventLog(attributes={"concept:name": name})
    for number, events in enumerate(cases, 1):
        trace = Trace({"concept:name": str(number)})
        for activity, hour, who in events:
            attributes = {"concept:name": activity}
            if hour is not None:
                attributes["time:timestamp"] = T0 + timedelta(hours=hour)
            if who is not None:
                attributes["org:resource"] = who
            trace.events.append(Event(attributes))
        log.traces.append(trace)
    return log


ORDERS = [
    [("a", 0, "Ann"), ("b", 1, "Bob"), ("c", 5, "Cy"), ("d", 6, "Ann")],
    [("a", 0, "Ann"), ("c", 2, "Cy"), ("b", 3, "Bob"), ("d", 10, "Dee")],
    [("a", 0, "Ann"), ("b", 1, "Bob"), ("d", 2, "Ann")],
]


# ---------------------------------------------------------------------------
# Handover of work
# ---------------------------------------------------------------------------
def test_handovers_are_counted_between_directly_following_events():
    network = social_network(timed_log(*ORDERS))
    assert network.handovers == Counter({("Ann", "Bob"): 2, ("Bob", "Cy"): 1, ("Cy", "Ann"): 1, ("Ann", "Cy"): 1,
                                         ("Cy", "Bob"): 1, ("Bob", "Dee"): 1, ("Bob", "Ann"): 1})
    assert network.handover_count == 8 and network.kept_count == 0
    assert network.resources[0] == "Ann" and network.events["Ann"] == 5
    first = network.handover_rows()[0]
    assert first[:3] == ("Ann", "Bob", 2) and first[3] == pytest.approx(2 / 3)       # Ann hands over 3 times
    assert network.together[frozenset(("Ann", "Bob"))] == 3
    assert network.together[frozenset(("Cy", "Dee"))] == 1
    assert network.cases == Counter({"Ann": 3, "Bob": 3, "Cy": 2, "Dee": 1})


def test_an_event_by_nobody_breaks_the_chain_and_a_step_kept_is_counted_apart():
    log = timed_log([("a", 0, "Ann"), ("b", 1, None), ("c", 2, "Cy"), ("d", 3, "Cy")])
    network = social_network(log)
    assert network.unknown_events == 1
    assert network.handovers == Counter({("Cy", "Cy"): 1})          # not Ann -> Cy: nobody knows who did b
    assert network.handover_count == 0 and network.kept_count == 1
    assert network.handover_rows() == []


def test_the_handover_box_gives_both_tables_and_refuses_a_log_without_resources():
    log = timed_log(*ORDERS)
    table = handover_of_work(log)
    assert isinstance(table, Table) and table.columns[:3] == ["from", "to", "times"]
    assert table.rows[0] == ["Ann", "Bob", 2, 66.67]
    together = handover_of_work(log, show="Working together")
    assert together.rows[0] == ["Ann", "Bob", 3, 100.0]
    roles = handover_of_work(log, by="concept:name")                 # any attribute: here the activity itself
    assert ["a", "b", 2, 66.67] in roles.rows
    with pytest.raises(ValueError, match="org:resource"):
        handover_of_work(EventLog.from_simple_log(parse_simple_log("[<a,b>]"), "typed"))


# ---------------------------------------------------------------------------
# Declare rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rule, trace, applies, holds", [
    ("existence(a)", "bc", True, False),
    ("init(a)", "ab", True, True), ("init(a)", "ba", True, False), ("end(b)", "ab", True, True),
    ("responded_existence(a, b)", "ba", True, True), ("responded_existence(a, b)", "ac", True, False),
    ("responded_existence(a, b)", "bc", False, True),
    ("co_existence(a, b)", "bc", True, False), ("co_existence(a, b)", "cc", False, True),
    ("response(a, b)", "abab", True, True), ("response(a, b)", "aba", True, False),
    ("response(a, b)", "bcb", False, True),
    ("precedence(a, b)", "acb", True, True), ("precedence(a, b)", "bab", True, False),
    ("precedence(a, b)", "ac", False, True),
    ("succession(a, b)", "ab", True, True), ("succession(a, b)", "a", True, False),
    ("succession(a, b)", "b", True, False),
    ("alternate_response(a, b)", "acbab", True, True), ("alternate_response(a, b)", "aab", True, False),
    ("alternate_precedence(a, b)", "abab", True, True), ("alternate_precedence(a, b)", "abb", True, False),
    ("alternate_succession(a, b)", "abab", True, True), ("alternate_succession(a, b)", "abba", True, False),
    ("chain_response(a, b)", "abcab", True, True), ("chain_response(a, b)", "acb", True, False),
    ("chain_response(a, b)", "ba", True, False),
    ("chain_precedence(a, b)", "cab", True, True), ("chain_precedence(a, b)", "acb", True, False),
    ("chain_precedence(a, b)", "b", True, False),
    ("chain_succession(a, b)", "abab", True, True), ("chain_succession(a, b)", "abb", True, False),
    ("not_co_existence(a, b)", "ac", True, True), ("not_co_existence(a, b)", "ba", True, False),
    ("not_succession(a, b)", "ba", True, True), ("not_succession(a, b)", "acb", True, False),
    ("not_chain_succession(a, b)", "acb", True, True), ("not_chain_succession(a, b)", "cab", True, False),
])
def test_each_template_means_what_the_table_says(rule, trace, applies, holds):
    parsed = declare.parse_rule(rule)
    assert declare.applies_to(parsed, tuple(trace)) is applies
    assert declare.holds_in(parsed, tuple(trace)) is holds


def test_discovery_counts_what_checking_counts():
    """Discovery works from positions, checking from the definitions: on
    random logs they give the same numbers for every rule."""
    rng = random.Random(4)
    compared = 0
    for _ in range(60):
        activities = "abcde"[:rng.randint(2, 5)]
        log = Counter(tuple(rng.choice(activities) for _ in range(rng.randint(1, 7))) for _ in range(rng.randint(1, 12)))
        present = sorted({activity for trace in log for activity in trace})
        found = {c.rule: c for c in declare.discover(log, holds=0.0, applies=0.0, negative=True, all_rules=True)}
        rules = [Rule(t, a) for t in declare.TEMPLATES if declare.ARITY[t] == 1 for a in present]
        rules += [Rule(t, a, b) for t in declare.TEMPLATES if declare.ARITY[t] == 2
                  for a in present for b in present if a != b
                  and not (t in ("co_existence", "not_co_existence") and a > b)]
        assert set(found) == set(rules)
        for checked in declare.check(log, rules):
            assert (found[checked.rule].applies, found[checked.rule].holds) == (checked.applies, checked.holds), \
                (dict(log), str(checked.rule))
            compared += 1
    assert compared > 3000


def test_a_stricter_rule_implies_the_weaker_ones_it_hides():
    rng = random.Random(5)
    for _ in range(3000):
        trace = tuple(rng.choice("abc") for _ in range(rng.randint(0, 7)))
        for template in declare.TEMPLATES:
            if declare.ARITY[template] == 1:
                continue
            for a, b in (("a", "b"), ("b", "a"), ("a", "c")):
                rule = Rule(template, a, b)
                if declare.holds_in(rule, trace):
                    for weaker in declare._implied(rule):
                        assert declare.holds_in(weaker, trace), (trace, str(rule), str(weaker))


def test_the_rules_of_the_textbook_log():
    log = parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")
    found = {str(c.rule): (c.applies, c.holds) for c in declare.discover(log)}
    assert found["init(a)"] == (6, 6) and found["end(d)"] == (6, 6)
    assert found["alternate_succession(a, d)"] == (6, 6)
    assert found["co_existence(b, c)"] == (5, 5)
    assert "response(a, d)" not in found                      # implied by the alternate succession
    assert "existence(e)" not in found                        # holds in 1 of 6
    assert "chain_response(e, d)" in found                    # e is in 1 of 6 cases: 17 % >= 10 %
    assert "chain_response(e, d)" not in {str(c.rule) for c in declare.discover(log, applies=0.5)}
    loose = {str(c.rule): c for c in declare.discover(log, holds=0.8, all_rules=True)}
    assert loose["existence(b)"].holds == 5 and loose["existence(b)"].share == pytest.approx(5 / 6)
    negative = {str(c.rule) for c in declare.discover(log, negative=True)}
    assert "not_co_existence(b, e)" in negative and "not_succession(d, a)" in negative


def test_rules_are_read_from_text_and_a_wrong_one_says_how():
    assert declare.parse_rules("response(Register order, pay); init(Register order)\n\nend(pay)") == [
        Rule("response", "Register order", "pay"), Rule("init", "Register order"), Rule("end", "pay")]
    assert str(Rule("response", "a", "b")) == "response(a, b)"
    assert Rule("precedence", "check", "ship").meaning == "every ship has check before it"
    for wrong, said in (("follows(a, b)", "no rule template"), ("response(a)", "two activities"),
                        ("init(a, b)", "one activity"), ("response a b", "template\\(a\\)")):
        with pytest.raises(ValueError, match=said):
            declare.parse_rule(wrong)


def test_rules_discovered_on_one_log_are_checked_on_another_in_a_workflow():
    last_year = EventLog.from_simple_log(parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"), "last year")
    this_year = EventLog.from_simple_log(parse_simple_log("[<a,b,c,d>^4, <a,d,b>^2, <b,d>]"), "this year")

    @box
    def reference() -> EventLog:
        return last_year

    @box
    def current() -> EventLog:
        return this_year

    workflow = Workflow("rules")
    old, new = workflow.add(reference), workflow.add(current)
    rules = workflow.add(discover_rules)
    check = workflow.add(check_rules)
    workflow.connect(old, rules)
    workflow.connect(new, check, "log")
    workflow.connect(rules, check, "rules")
    run = Runner().run(workflow)
    assert run.ok, [(r.node, r.error) for r in run.failed()]
    result = run.value(check)
    by_rule = {row[0]: row for row in result.table.rows}
    assert by_rule["init(a)"][2:6] == [7, 6, 85.71, 1]
    assert by_rule["end(d)"][5] == 2                                  # <a,d,b> twice
    assert by_rule["alternate_succession(a, d)"][5] == 1              # <b,d>: d without a
    assert sum(result.breaking.simple_log().values()) == 3            # <a,d,b>^2 and <b,d>
    assert fingerprint(run.value(check)) == fingerprint(Runner().run(workflow).value(check))

    typed = typed_rules("response(a, d); not_succession(d, b)")
    by_hand = check_rules(this_year, typed)
    assert [row[0] for row in by_hand.table.rows] == ["response(a, d)", "not_succession(d, b)"]
    assert by_hand.table.rows[0][2:4] == [6, 6] and by_hand.table.rows[1][5] == 2
    with pytest.raises(ValueError, match="rule"):
        check_rules(this_year, Table("scores", ["model"], [["m"]]))


# ---------------------------------------------------------------------------
# Waiting times
# ---------------------------------------------------------------------------
def test_the_replay_says_what_fired_for_which_event_and_counts_as_before():
    log = parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")
    net = inductive_miner(log).net
    for trace in (("a", "b", "c", "d"), ("a", "b", "d"), ("d",), ("a", "x", "d")):
        steps: list = []
        told, plain = replay_trace(net, trace, steps=steps), replay_trace(net, trace)
        assert fingerprint(told) == fingerprint(plain)
        assert Counter(t for _i, t in steps) == told.fired
        assert [i for i, _t in steps] == sorted(i for i, _t in steps)       # in the order of the events
        visible = [(i, net.transitions[t].label) for i, t in steps if net.transitions[t].label]
        assert visible == [(i, a) for i, a in enumerate(trace) if a in net.labels()]


def test_waiting_is_the_time_between_a_token_s_arrival_and_its_removal():
    log = timed_log(*ORDERS)
    net = inductive_miner(log.simple_log()).net                       # →(a, ∧(b, ×(τ, c)), d)
    result = waiting_times(net, log)
    assert result.cases == 3 and result.skipped == 0
    by_neighbours = {(tuple(p.after), tuple(p.before)): p for p in result.places}
    after_b = by_neighbours[(("b",), ("τ",))]                         # b is done; waits for the join before d
    assert sorted(after_b.stays) == [1 * HOUR, 5 * HOUR, 7 * HOUR]    # d at 2, 6, 10; b at 1, 1, 3
    assert after_b.mean == pytest.approx(13 * HOUR / 3) and after_b.longest == 7 * HOUR
    assert after_b.median == 5 * HOUR and after_b.percentile(0.9) == 7 * HOUR
    before_b = by_neighbours[(("τ",), ("b",))]                        # from a (the split fires with b or c)
    assert sorted(before_b.stays) == [0.0, 0.0, 1 * HOUR]             # case 2: split at c (2 h), b at 3 h
    assert result.places[0] is after_b                                # the most waiting first
    assert all(p.missing == 0 and p.remaining == 0 for p in result.places)
    assert result.place(net.initial_marking and next(iter(net.initial_marking))).stays == [0.0, 0.0, 0.0]
    # Every token produced was consumed: as many stays as the replay consumed tokens.
    replay = token_replay(net, log.simple_log(), workers=1)
    assert sum(p.tokens for p in result.places) == replay.consumed


def test_missing_and_left_behind_tokens_are_counted_not_timed_and_untimed_cases_are_skipped():
    model_log = parse_simple_log("[<a,b,c>]")
    net = inductive_miner(model_log).net
    log = timed_log([("a", 0, None), ("c", 4, None)],                 # b skipped: its token is missing
                    [("a", 0, None), ("b", None, None), ("c", 2, None)],    # an event without a time
                    [("a", 5, None), ("b", 3, None), ("c", 6, None)],       # out of order
                    [("a", 0, None), ("b", 1, None), ("c", 3, None)])
    result = waiting_times(net, log)
    assert result.cases == 2 and result.skipped == 2
    replay = token_replay(net, Counter({("a", "c"): 1, ("a", "b", "c"): 1}), workers=1)
    assert sum(p.missing for p in result.places) == replay.missing == 1
    assert sum(p.remaining for p in result.places) == replay.remaining == 1
    assert sum(p.tokens for p in result.places) == replay.consumed - replay.missing
    table = waiting_box(net, log)
    assert table.columns[:4] == ["place", "after", "before", "tokens"] and "total (hours)" in table.columns
    assert sum(row[9] for row in table.rows) == 1 and sum(row[10] for row in table.rows) == 1
    days = waiting_box(net, log, unit="days")
    assert days.rows[0][4] == pytest.approx(table.rows[0][4] / 24, abs=0.01)
    with pytest.raises(ValueError, match="timestamp"):
        waiting_box(net, EventLog.from_simple_log(model_log, "typed"))
