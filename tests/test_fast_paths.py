"""The inner loops of conformance checking and the state space were rewritten
for speed (0.16.3).  Each fast path must give exactly what the plain
definition gives: these tests compare them on random nets and logs.
"""

from __future__ import annotations

import random
from collections import Counter, deque

import pytest

from openprocess.mining import align_log, inductive_miner, token_replay
from openprocess.mining.conformance.alignments import _CompiledNet
from openprocess.mining.conformance.token_replay import _SilentSearch
from openprocess.mining.petrinet import Marking


EXPECTED = [(("d",), 2, 2, 1, 1), (("a", "b", "d"), 6, 5, 1, 2), (("a", "e", "e", "d"), 5, 5, 1, 1)]


def _random_log(rng: random.Random, activities: int, traces: int, longest: int) -> Counter:
    names = [chr(97 + i) for i in range(activities)]
    return Counter(tuple(rng.choice(names) for _ in range(rng.randint(1, longest))) for _ in range(traces))


def _nets(seed: int, count: int):
    rng = random.Random(seed)
    for _ in range(count):
        log = _random_log(rng, rng.randint(3, 7), rng.randint(5, 30), rng.randint(3, 8))
        for threshold in (0.0, 0.3):
            yield rng, log, inductive_miner(log, threshold).net


def _reachable(net, limit: int = 300) -> list[Marking]:
    seen, queue = {net.initial_marking}, deque([net.initial_marking])
    while queue and len(seen) < limit:
        marking = queue.popleft()
        for transition in net.enabled(marking):
            following = net.fire(marking, transition)
            if following not in seen:
                seen.add(following)
                queue.append(following)
    return list(seen)


def test_firing_is_marking_minus_pre_plus_post():
    for _rng, _log, net in _nets(1, 12):
        for marking in _reachable(net):
            enabled = [t for t in net.transitions if all(marking[p] >= n for p, n in net.pre(t).items())]
            assert net.enabled(marking) == enabled
            for transition in enabled:
                fired = net.fire(marking, transition)
                assert fired == marking - net.pre(transition) + net.post(transition)
                assert hash(fired) == hash(Marking(dict(fired.items())))
                assert all(count for _place, count in fired.items())          # no zero is stored
            for transition in set(net.transitions) - set(enabled):
                with pytest.raises(ValueError):
                    net.fire(marking, transition)


def test_a_marking_compares_with_any_mapping():
    marking = Marking({"p": 2, "q": 1})
    assert marking >= Marking({"p": 2}) and marking >= {"p": 1, "q": 1} and marking >= Marking()
    assert not marking >= Marking({"p": 3}) and not marking >= {"r": 1}
    assert Marking._of({"p": 1}) == Marking({"p": 1, "q": 0})


def test_the_compiled_net_fires_as_the_net_does():
    for _rng, _log, net in _nets(2, 10):
        compiled = _CompiledNet(net)
        index = {place: i for i, place in enumerate(compiled.places)}
        ids = [t.id for t in compiled.transitions]
        for marking in _reachable(net, 120):
            vector = compiled.encode(marking, index)
            steps = compiled.steps(vector)
            assert compiled.steps(vector) is steps                             # remembered
            assert [ids[k] for k, _following in steps] == net.enabled(marking)
            assert [ids[k] for k in compiled.enabled(vector)] == net.enabled(marking)
            for k, following in steps:
                assert following == compiled.encode(net.fire(marking, ids[k]), index)


def test_a_full_memory_of_markings_changes_no_alignment(monkeypatch):
    from openprocess.mining.conformance import alignments
    rng = random.Random(3)
    log = _random_log(rng, 6, 25, 7)
    net = inductive_miner(log, 0.2).net
    other = _random_log(rng, 7, 15, 7)

    def aligned():
        result = align_log(net, other, workers=1)
        return [(a.trace, a.cost, a.complete, a.states_visited, [(m.log, m.model, m.kind) for m in a.moves])
                for a in result.alignments]

    remembered = aligned()
    monkeypatch.setattr(alignments, "_REMEMBERED", 0)                          # nothing is remembered
    assert aligned() == remembered


def test_the_silent_search_finds_the_first_shortest_path():
    """Breadth-first, the moves tried in their order: the reference, written plainly."""
    def reference(searcher, start, need, moves):
        if all(start[i] >= n for i, n in need):
            return []
        parent, queue = {start: None}, deque([start])
        while queue:
            current = queue.popleft()
            for transition, required, change in moves:
                if any(current[i] < n for i, n in required):
                    continue
                following = list(current)
                for i, d in change:
                    following[i] += d
                successor = tuple(following)
                if successor in parent:
                    continue
                parent[successor] = (current, transition)
                if all(successor[i] >= n for i, n in need):
                    path = []
                    while parent[successor] is not None:
                        successor, step = parent[successor]
                        path.append(step)
                    return path[::-1]
                queue.append(successor)
        return None

    checked = 0
    for _rng, _log, net in _nets(4, 8):
        searcher = _SilentSearch(net)
        for marking in _reachable(net, 60):
            start = searcher.vector(marking)
            for transition in net.transitions:
                need = searcher.requirement(net.pre(transition))
                short = [i for i, n in need if start[i] < n]
                expected = reference(searcher, start, need, searcher.relevant(short)) if short else []
                assert searcher.enabling(marking, need) == expected
                checked += 1
    assert checked > 500


def test_replay_counts_are_what_they_were():
    """A handful of replays whose counts were worked out with the code of 0.16.2."""
    log = Counter({("a", "b", "c", "d"): 3, ("a", "c", "b", "d"): 2, ("a", "e", "d"): 1})
    net = inductive_miner(log).net
    replay = token_replay(net, Counter({("a", "b", "d"): 1, ("a", "e", "e", "d"): 1, ("d",): 2}))
    assert [(t.trace, t.produced, t.consumed, t.missing, t.remaining) for t in replay.traces] == EXPECTED
