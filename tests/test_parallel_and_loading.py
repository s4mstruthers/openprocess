"""Speed without changing a result: parallel variants, logs read once, in a worker.

Every test here compares the fast path with the plain one and requires them
to be identical: the alignments move for move, the replays token for token,
the log read by a worker process event for event (its fingerprint) and its
summary.
"""

from __future__ import annotations

import pickle
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from openprocess.mining import parallel
from openprocess.mining.conformance.alignments import align_log
from openprocess.mining.conformance.token_replay import token_replay
from openprocess.mining.discovery.inductive import inductive_miner
from openprocess.mining.log import parse_simple_log
from openprocess.mining.playout import play_out

DATA = Path(__file__).parent / "data"


def _logs():
    """A model with loops, choices and silent steps, and a noisy log of it with many variants."""
    base = parse_simple_log("[<a,b,c,d,e>^5, <a,c,b,d,e>^3, <a,b,c,f,b,c,d,e>^2, <a,g,e>^2, <a,b,c,d,h,e>]")
    net = inductive_miner(base, noise_threshold=0.0).net
    played = play_out(net, traces=400, max_length=40, seed=7).log.simple_log()
    noisy = Counter()
    for k, (trace, count) in enumerate(sorted(played.items())):
        if k % 3 == 0 and len(trace) > 3:                  # swap two events: deviations to align
            trace = trace[:1] + (trace[2], trace[1]) + trace[3:]
        if k % 5 == 0:
            trace = trace + ("x",)                         # an event the model does not know
        noisy[trace] += count
    return net, noisy


def _spy(monkeypatch) -> list:
    """Lower the size threshold so the test log goes to processes, and record that it did."""
    monkeypatch.setattr(parallel, "THRESHOLDS", {"align": (10, 0), "replay": (10, 0)})
    used, real = [], parallel.map_variants

    def spy(kind, *args, **kwargs):
        used.append(kind)
        return real(kind, *args, **kwargs)
    monkeypatch.setattr(parallel, "map_variants", spy)
    return used


def test_parallel_alignments_are_identical_to_sequential(monkeypatch):
    net, log = _logs()
    used = _spy(monkeypatch)
    one = align_log(net, log, workers=1)
    many = align_log(net, log, workers=3)
    assert used == ["align"]                                       # the parallel path did run
    assert many.alignments == one.alignments                       # every move of every variant
    assert (many.average_fitness, many.log_fitness, many.fitting_traces, many.notes) == \
        (one.average_fitness, one.log_fitness, one.fitting_traces, one.notes)
    assert one.average_fitness < 1.0                               # there were deviations to find


def test_parallel_replay_is_identical_to_sequential(monkeypatch):
    net, log = _logs()
    used = _spy(monkeypatch)
    one = token_replay(net, log, workers=1)
    many = token_replay(net, log, workers=3)
    assert used == ["replay"]
    assert many.traces == one.traces
    assert (many.fitness, many.produced, many.consumed, many.missing, many.remaining) == \
        (one.fitness, one.produced, one.consumed, one.missing, one.remaining)


def test_progress_and_small_logs_stay_here(monkeypatch):
    net, log = _logs()
    _spy(monkeypatch)
    seen = []
    align_log(net, log, workers=2, progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1] == (len(log), len(log))
    called = []
    monkeypatch.setattr(parallel, "THRESHOLDS", {"align": (16, 400), "replay": (2_000, 60_000)})
    monkeypatch.setattr(parallel, "map_variants", lambda *a, **k: called.append(1))
    small = parse_simple_log("[<a,b,c,d,e>^5, <a,c,b,d,e>^3]")
    token_replay(net, small, workers=4)
    align_log(net, small, workers=4)
    assert not called                                              # too small to be worth processes


def test_a_net_pickles_without_its_counter():
    net, _log = _logs()
    again = pickle.loads(pickle.dumps(net))
    assert again.transitions.keys() == net.transitions.keys()
    assert [(a.source, a.target, a.weight) for a in again.arcs] == [(a.source, a.target, a.weight) for a in net.arcs]
    assert again.initial_marking == net.initial_marking and again.final_marking == net.final_marking
    added = again.add_place("new")
    assert added.id not in net.places


# -- reading once, and in a worker ---------------------------------------------------------------
def test_a_file_is_read_once_for_everyone(tmp_path):
    import shutil
    from openprocess.flow import loading
    path = tmp_path / "wilma.xes"
    shutil.copy(DATA / "plane_wilma_10.xes", path)
    first = loading.read_log(path)
    assert loading.read_log(path) is first and loading.cached(path) is first
    # Two callers at once share one read.
    from openprocess.mining import xes
    calls = []
    real = xes.read_xes

    def slow(source, progress=None, columnar=None):
        calls.append(source)
        time.sleep(0.3)
        return real(source, progress=progress, columnar=columnar)
    other = tmp_path / "other.xes"
    shutil.copy(path, other)
    import unittest.mock as mock
    got = []
    with mock.patch.object(xes, "read_xes", slow):
        threads = [threading.Thread(target=lambda: got.append(loading.read_log(other))) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert len(calls) == 1 and len(got) == 3 and all(g is got[0] for g in got)
    # A changed file is read again.
    time.sleep(0.01)
    other.write_bytes(other.read_bytes())
    import os
    os.utime(other, None)
    assert loading.read_log(other) is not got[0]


def test_a_worker_reads_the_same_log(tmp_path, monkeypatch):
    import shutil
    from openprocess.flow import loading, record
    from openprocess.mining.stats import summarise
    from openprocess.mining.xes import read_xes
    path = tmp_path / "wilma.xes"
    shutil.copy(DATA / "plane_wilma_10.xes", path)
    monkeypatch.setattr(loading, "use_processes", True)
    monkeypatch.setattr(loading, "PROCESS_FROM", 0)                # this small log goes to a worker too
    seen = []
    log = loading.read_log(path, progress=lambda n, share: seen.append(n))
    direct = read_xes(path)
    assert record.fingerprint(log, remember=True) == record.fingerprint(direct)     # event for event
    assert record.sha256_file(path) == record.sha256_bytes(path.read_bytes())
    ready = next(iter(log.precomputed_summaries.values()))
    plain = summarise(direct, direct.default_classifier())
    assert (ready.case_count, ready.event_count, [v.sequence for v in ready.variants],
            [(a.name, a.occurrences) for a in ready.activities]) == \
        (plain.case_count, plain.event_count, [v.sequence for v in plain.variants],
         [(a.name, a.occurrences) for a in plain.activities])
    assert seen and seen[-1] == len(direct)


def test_cancelling_a_worker_read(tmp_path, monkeypatch):
    import shutil
    from openprocess.flow import loading
    path = tmp_path / "wilma.xes"
    shutil.copy(DATA / "plane_wilma_10.xes", path)
    monkeypatch.setattr(loading, "use_processes", True)
    monkeypatch.setattr(loading, "PROCESS_FROM", 0)

    def stop(_cases, _share):
        raise loading.Cancelled()
    with pytest.raises(loading.Cancelled):
        loading.read_log(path, progress=stop)
    assert loading.read_log(path) is not None                      # a later read works
