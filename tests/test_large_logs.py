"""Large logs: every fast path gives exactly what the plain path gives.

A log above 20 MB is read into columns (openprocess.mining.columns) and the
summary, the filters, the fingerprint and the measuring boxes read the
arrays.  These tests read the same files both ways and compare; the timing
on the BPI Challenge 2019 log (1.6 million events) is in
examples/bpi2019/README.md.
"""

import gzip
import io
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openprocess.flow.record import fingerprint
from openprocess.mining import filtering, parse_simple_log
from openprocess.mining.cases import case_value, cases
from openprocess.mining.stats import summarise
from openprocess.mining.xes import read_xes

ROOT = Path(__file__).resolve().parents[1]
WILMA = ROOT / "tests" / "data" / "plane_wilma_10.xes"

EDGE = """<?xml version="1.0" encoding="UTF-8"?>
<log xmlns="http://www.xes-standard.org/" xes.version="1.0">
 <extension name="Concept" prefix="concept" uri="u"/>
 <global scope="trace"><string key="concept:name" value="UNKNOWN"/></global>
 <global scope="event"><date key="time:timestamp" value="1970-01-01T00:00:00Z"/><int key="n" value="0"/></global>
 <classifier name="Both" keys="concept:name 'my key'"/>
 <string key="concept:name" value="edge &amp; cases"/>
 <trace>
  <string key="concept:name" value="c1"/>
  <event><string key="concept:name" value="a"/><date key="time:timestamp" value="2020-01-01T10:00:00.1234567+02:00"/>
   <list key="items"><values><int key="i" value="1"/><string key="s" value="x"/></values></list>
   <container key="box"><float key="f" value="1.5"/><boolean key="b" value="TRUE"/></container>
   <string key="meta" value="v"><int key="inner" value="9"/></string>
   <int key="bad" value="notanint"/><foo key="weird" value="w"/>
   <float key="worth" value="-0.0"/>
  </event>
  <event><string key="lifecycle:transition" value="complete"/><string key="org:resource" value="r"/>
   <string key="concept:name" value="b"/><date key="time:timestamp" value="2020-01-02T10:00:00Z"/><float key="worth" value="12.5"/></event>
  <list key="tl"><int key="i" value="3"/></list>
 </trace>
 <trace><event><string key="concept:name" value="x"/></event><string key="concept:name" value="c2 after events"/></trace>
 <trace></trace>
</log>"""


def both(source):
    """The same log read as objects and into columns."""
    if isinstance(source, str):
        return read_xes(io.BytesIO(source.encode()), columnar=False), read_xes(io.BytesIO(source.encode()), columnar=True)
    return read_xes(source, columnar=False), read_xes(source, columnar=True)


@pytest.mark.parametrize("source", [WILMA, EDGE], ids=["wilma", "edge cases"])
def test_the_expat_reader_reads_what_the_object_reader_reads(source):
    plain, columns = both(source)
    assert columns.columnar
    assert [t.attributes for t in plain] == [t.attributes for t in columns]
    assert [[e.attributes for e in t] for t in plain] == [[e.attributes for e in t] for t in columns]
    assert (plain.attributes, plain.extensions, plain.global_trace_attributes, plain.global_event_attributes) == \
        (columns.attributes, columns.extensions, columns.global_trace_attributes, columns.global_event_attributes)
    assert [(c.name, c.keys) for c in plain.declared_classifiers] == [(c.name, c.keys) for c in columns.declared_classifiers]


def test_a_gzipped_file_is_read_into_columns_too(tmp_path):
    path = tmp_path / "edge.xes.gz"
    path.write_bytes(gzip.compress(EDGE.encode()))
    assert fingerprint(read_xes(path, columnar=True)) == fingerprint(both(EDGE)[0])


def test_a_malformed_file_still_raises_a_parse_error():
    from xml.etree.ElementTree import ParseError
    with pytest.raises(ParseError):
        read_xes(io.BytesIO(b"<log><trace><event></trace></log>"), columnar=True)


@pytest.mark.parametrize("source", [WILMA, EDGE], ids=["wilma", "edge cases"])
def test_a_columnar_log_has_the_fingerprint_of_the_same_log_as_objects(source):
    plain, columns = both(source)
    fast = fingerprint(columns)
    assert not any(columns.traces._built)                 # no trace was built to hash it
    columns.traces.materialise()
    assert fast == fingerprint(plain) == fingerprint(columns)


def test_the_summary_from_columns_is_the_summary_from_objects():
    plain, columns = both(WILMA)
    a, b = summarise(plain), summarise(columns)
    assert not any(columns.traces._built)
    assert (a.case_count, a.event_count, a.start, a.end, a.case_durations, a.events_per_case) == \
        (b.case_count, b.event_count, b.start, b.end, b.case_durations, b.events_per_case)
    assert [(v.sequence, v.trace_indices) for v in a.variants] == [(v.sequence, v.trace_indices) for v in b.variants]
    assert a.activities == b.activities


@pytest.mark.parametrize("name, keep", [
    ("start", lambda log: filtering.filter_start_activities(log, {"Move in"})),
    ("end", lambda log: filtering.filter_end_activities(log, {"Sit in Isle Seat"})),
    ("length", lambda log: filtering.filter_case_length(log, 5, 12)),
    ("variants", lambda log: filtering.filter_variants(log, count=3)),
    ("mandatory", lambda log: filtering.filter_activities(log, {"stow bag"}, "mandatory")),
    ("forbidden", lambda log: filtering.filter_activities(log, {"Sit in Middle Seat"}, "forbidden")),
    ("time", lambda log: filtering.filter_time_frame(log, datetime(3924, 1, 1, tzinfo=timezone.utc),
                                                      datetime(3925, 1, 1, tzinfo=timezone.utc), "started")),
])
def test_a_filtered_columnar_log_stays_columnar_and_keeps_the_same_cases(name, keep):
    plain, columns = both(WILMA)
    a, b = keep(plain), keep(columns)
    assert b.columnar, f"{name}: the filter turned the log into objects"
    assert fingerprint(a) == fingerprint(b)


def test_cases_reads_the_same_from_columns_and_objects():
    plain, columns = both(EDGE)
    for a, b in zip(cases(plain, ("worth", "org:resource")), cases(columns, ("worth", "org:resource"))):
        assert (a.id, a.activities, a.times, a.attributes, a.columns) == (b.id, b.activities, b.times, b.attributes, b.columns)
    first = next(cases(columns, ("worth",)))
    assert case_value(first, "worth") == 12.5


# -- the engines on many variants: the same answers, without the old costs ---------------------
def test_precision_handles_a_trace_longer_than_the_recursion_limit():
    """Regression (0.12): prefixes were replayed recursively, one call per event."""
    from openprocess.mining.conformance.quality import precision
    from openprocess.mining.discovery.inductive import inductive_miner
    log = Counter({tuple(["a"] + ["b", "c"] * 1500 + ["d"]): 1, ("a", "d"): 3})
    assert 0.0 <= precision(inductive_miner(log).net, log) <= 1.0


def test_replay_answers_do_not_depend_on_remembering_silent_paths():
    """Token replay remembers the τ paths it found, across variants; replaying each
    variant on its own (nothing remembered) gives the same counts."""
    from openprocess.mining.conformance.token_replay import replay_trace, token_replay
    from openprocess.mining.discovery.inductive import inductive_miner
    log = parse_simple_log("[<a,b,c,e,f>^5, <a,c,b,e,f>^4, <a,b,c,d,b,c,e,f>^2, <a,g,f>^3, <a,b,f>, <f,a>, <a,b,d,f>]")
    net = inductive_miner(parse_simple_log("[<a,b,c,e,f>, <a,c,b,e,f>, <a,b,c,d,b,c,e,f>]")).net
    together = token_replay(net, log)
    for shared in together.traces:
        alone = replay_trace(net, shared.trace, shared.count)
        assert (alone.produced, alone.consumed, alone.missing, alone.remaining, dict(alone.fired)) == \
            (shared.produced, shared.consumed, shared.missing, shared.remaining, dict(shared.fired))


def test_the_heuristics_net_is_the_same_in_every_session_and_keeps_its_final_place():
    from openprocess.mining.discovery.heuristics import heuristics_net
    from openprocess.mining.pnml import pnml_string
    log = parse_simple_log("[<a,b,c,e,f>^5, <a,c,b,e,f>^4, <a,b,c,d,b,c,e,f>^2, <a,g,f>^3, <a,b,f>]")
    first, second = heuristics_net(log, 0.5).net, heuristics_net(log, 0.5).net
    assert pnml_string(first) == pnml_string(second)
    assert first.final_marking and all(place in first.places for place in first.final_marking)


def test_crossings_are_counted_exactly():
    """The layout counts crossing edges in O(E log E) (it compared every pair, which took
    minutes on a large net); the count is the plain pairwise one, with ties not crossing."""
    import random
    from openprocess.mining.layout import count_inversions
    random.seed(4)
    for _ in range(300):
        pairs = sorted((random.randrange(12), random.randrange(12)) for _ in range(random.randrange(0, 40)))
        ends = [b for _a, b in pairs]
        brute = sum(1 for i in range(len(pairs)) for j in range(i + 1, len(pairs))
                    if pairs[i][0] < pairs[j][0] and pairs[i][1] > pairs[j][1])
        assert count_inversions(ends) == brute


def test_a_large_graph_is_laid_out_with_every_node_placed():
    import random
    from openprocess.mining.layout import layered_layout
    random.seed(4)
    nodes = {f"n{i}": (40.0, 30.0) for i in range(300)}
    edges = [(f"n{random.randrange(300)}", f"n{random.randrange(300)}") for _ in range(900)]
    result = layered_layout(nodes, edges)
    assert set(result.positions) == set(nodes)
