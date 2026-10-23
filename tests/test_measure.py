"""The measuring boxes, and the analysis boxes corrected in 0.12, on logs small
enough to work the answer out by hand."""

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from openprocess.flow.boxes.check import alignments, compare_to_true_net
from openprocess.flow.boxes.filter import case_attribute
from openprocess.flow.boxes.measure import compliance_by, order_rule, segregation_of_duties, time_between
from openprocess.flow.boxes.predict import FrequencyModel, predict, prefixes, split_by_time
from openprocess.flow.boxes.science import _bootstrap_difference
from openprocess.flow.types import Dataset, Table
from openprocess.mining import parse_simple_log
from openprocess.mining.discovery.inductive import inductive_miner
from openprocess.mining.log import Event, EventLog, Trace

T0 = datetime(2018, 1, 1, tzinfo=timezone.utc)


def purchase_log() -> EventLog:
    """Four purchase items: (activity, day, user, worth) per event, and a category and vendor per case."""
    cases = [
        ("1", "invoice before GR", "V1", [("Create PO", 0, "u1", 100.0), ("Record Invoice", 1, "u2", 100.0),
                                          ("Remove Block", 2, "u2", 100.0), ("Goods Receipt", 4, "u3", 100.0),
                                          ("Clear Invoice", 9, "u2", 100.0)]),
        ("2", "invoice before GR", "V1", [("Create PO", 0, "u1", 50.0), ("Record Invoice", 2, "u4", 50.0),
                                          ("Goods Receipt", 3, "u3", 50.0), ("Remove Block", 4, "u4", 50.0),
                                          ("Clear Invoice", 5, "u5", 60.0)]),
        ("3", "invoice after GR", "V2", [("Create PO", 0, "u1", 10.0), ("Goods Receipt", 1, "u3", 10.0),
                                         ("Record Invoice", 3, "u2", 10.0), ("Clear Invoice", 4, "u2", 10.0)]),
        ("4", "invoice before GR", "V2", [("Create PO", 0, "u1", 5.0), ("Remove Block", 1, "u4", 5.0)]),
    ]
    log = EventLog(attributes={"concept:name": "purchases"})
    for case, category, vendor, events in cases:
        trace = Trace({"concept:name": case, "Item Category": category, "Vendor": vendor})
        for activity, day, user, worth in events:
            trace.events.append(Event({"concept:name": activity, "time:timestamp": T0 + timedelta(days=day),
                                       "User": user, "worth": worth}))
        log.traces.append(trace)
    return log


def test_time_between_counts_the_first_end_after_the_first_start():
    table = time_between(purchase_log(), "Record Invoice", "Clear Invoice", by="Item Category")
    rows = {row[0]: row for row in table.rows}
    assert rows["invoice before GR"][1:3] == [2, 5.5]          # 8 days and 3 days
    assert rows["invoice after GR"][1:3] == [1, 1.0]
    assert sorted(table.samples) == [1.0, 3.0, 8.0]


def test_order_rule_puts_every_case_in_one_row_with_its_value():
    result = order_rule(purchase_log(), "Remove Block", "Goods Receipt", value="worth")
    counts = {row[0]: (row[1], row[3]) for row in result.table.rows}
    assert counts["Remove Block before Goods Receipt"] == (1, 100.0)
    assert counts["Remove Block, and no Goods Receipt at all"] == (1, 5.0)
    assert counts["Goods Receipt before Remove Block"] == (1, 60.0)       # the largest worth in the case
    assert counts["only Goods Receipt"] == (1, 10.0)
    assert counts["neither"] == (0, 0.0)
    assert [t.case_id for t in result.cases] == ["1", "4"]


def test_segregation_of_duties_finds_who_did_both():
    table = segregation_of_duties(purchase_log(), "Record Invoice", "Clear Invoice", by="User", value="worth")
    assert table.rows == [["u2", 2, 110.0]]                    # cases 1 and 3; u4 and u5 split case 2


def test_case_attribute_keeps_values_that_contain_commas():
    log = purchase_log()
    for trace in log:
        trace.attributes["Item Category"] = "3-way match, " + trace.attributes["Item Category"]
    kept = case_attribute(log, "Item Category", "3-way match, invoice after GR")
    assert [t.case_id for t in kept] == ["3"]
    gone = case_attribute(log, "Item Category", "3-way match, invoice after GR", "remove")
    assert [t.case_id for t in gone] == ["1", "2", "4"]
    with pytest.raises(ValueError):
        case_attribute(log, "Item Category", " ")


def test_compliance_by_vendor_uses_whether_each_case_fits():
    log = purchase_log()
    model = inductive_miner(Counter({("Create PO", "Record Invoice", "Remove Block", "Goods Receipt",
                                      "Clear Invoice"): 1})).net
    table = compliance_by(model, log, attribute="Vendor", value="worth", colour="Item Category")
    rows = {row[0]: row for row in table.rows}
    assert rows["V1"][1:4] == [2, 50.0, 160.0]                 # case 1 fits, case 2 does not
    assert rows["V2"][1:4] == [2, 0.0, 15.0]


# -- Compare to true net: real recall and precision -------------------------------------------
def test_compare_to_true_net_counts_traces_exactly():
    """Regression (0.12): "recall" and "precision" were 1 − n/(n+3) of a few example traces."""
    from openprocess.mining.compare_nets import trace_overlap
    truth = inductive_miner(parse_simple_log("[<a,b,d>, <a,c,d>]")).net
    model = inductive_miner(parse_simple_log("[<a,b,d>, <a,c,d>, <a,e,d>]")).net
    overlap = trace_overlap(model, truth, 6)
    assert (overlap.first, overlap.second, overlap.both) == (3, 2, 2)
    scores = compare_to_true_net(model, truth, max_length=6)
    assert scores.metrics["recall"] == 1.0 and scores.metrics["precision"] == pytest.approx(2 / 3)


def test_a_net_with_no_complete_trace_has_no_recall():
    from openprocess.mining.petrinet import Marking, PetriNet
    stuck = PetriNet("stuck")
    stuck.add_place("i", id="i"); stuck.add_place("o", id="o")
    stuck.initial_marking, stuck.final_marking = Marking({"i": 1}), Marking({"o": 1})
    model = inductive_miner(parse_simple_log("[<a>]")).net
    assert compare_to_true_net(model, stuck, max_length=4).metrics["recall"] == "n/a"


# -- Alignments on many variants ---------------------------------------------------------------
def test_alignments_can_take_only_the_most_frequent_variants():
    log = EventLog.from_simple_log(parse_simple_log("[<a,b>^5, <a,c>^3, <a,d>^1]"))
    net = inductive_miner(parse_simple_log("[<a,b>, <a,c>]")).net
    result = alignments(net, log, variants=2)
    assert result.trace_count == 8 and result.fitting_traces == 8


# -- The prediction pipeline ---------------------------------------------------------------------
def test_outcome_prefixes_never_contain_the_outcome():
    """Regression (0.12): the whole case was one of its prefixes, so its last activity, the label, was a feature."""
    log = EventLog.from_simple_log(parse_simple_log("[<a,b,x>^3, <a,c,y>^3]"))
    data = prefixes(log, label="outcome", encoding="index", max_prefix=5)
    assert max(data.prefix_lengths) == 2


def test_the_frequency_model_keys_one_hot_rows_on_the_last_activity():
    log = EventLog.from_simple_log(parse_simple_log("[<a,b,c>^4, <a,c,b>^4]"))
    data = prefixes(log, label="next activity", encoding="one-hot", max_prefix=3)
    model = FrequencyModel().fit(data)
    keys = {model._key(row, data) for row in data.X}
    assert keys == {"a", "b", "c"}


def test_predict_gives_a_scikit_learn_style_model_the_feature_rows():
    class Majority:                      # fit(X, y) and predict(X), as scikit-learn's
        name = "majority"

        def predict(self, X):
            assert isinstance(X, list)
            return ["b"] * len(X)
    data = Dataset([[0], [1]], ["b", "c"], ["f"], "next activity")
    assert predict(Majority(), data).values == ["b", "b"]


# -- Bootstrap comparison ----------------------------------------------------------------------
def test_bootstrap_difference_is_paired_when_the_resamples_are_the_same():
    a = Table("A", ["statistic", "fitness"], [["samples", 4], ["seed", 0]], samples=[0.9, 0.8, 0.95, 0.85])
    b = Table("B", ["statistic", "fitness"], [["samples", 4], ["seed", 0]], samples=[0.8, 0.7, 0.85, 0.75])
    table = _bootstrap_difference(a, b, a.samples, b.samples, 0.875, 0.775)
    rows = dict((row[0], row[1]) for row in table.rows)
    assert rows["paired"] == "yes" and rows["2.5 %"] == rows["97.5 %"] == pytest.approx(0.1)
    assert "A scores higher" in table.note
    c = Table("C", ["statistic", "fitness"], [["samples", 4], ["seed", 1]], samples=b.samples)
    assert dict((r[0], r[1]) for r in _bootstrap_difference(a, c, a.samples, c.samples, 0.875, 0.775).rows)["paired"] == "no"
