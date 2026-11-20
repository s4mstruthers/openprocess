"""Cross-check OpenProcess's Inductive Miner against PM4Py's, log by log.

Not a test (pytest does not collect it: no ``test_`` prefix) but a report,
because the two implementations are *allowed* to differ: the Inductive Miner
guarantees a sound model that fits the log, not a unique one.  What this
script reports is how often they differ, on which logs, and whether the
guarantees hold on the OpenProcess side regardless.  Run it after touching
``openprocess/mining/discovery/inductive.py``::

    pip install pm4py           # AGPL-3.0; a reference, not a dependency
    python tests/cross_check_inductive_miner.py [seed] [random logs]
    python tests/cross_check_inductive_miner.py [seed] [logs] 0.2     # IMf, see below

What it does, for the six textbook logs and (by default) 400 random ones:

1. runs both miners with no noise filtering;
2. canonicalises both process trees, sorting the children of the commutative
   operators × and ∧ so that ``×(a, b)`` and ``×(b, a)`` compare equal;
3. checks the two IM guarantees on the OpenProcess result: the net is sound
   and token replay gives fitness 1;
4. for every differing pair, compares the ETC precision (PM4Py's
   token-based precision) of the two models on the same log, so a difference
   can be read as "ours is less precise", "equal" or "more precise".

Baseline against PM4Py 2.7.23, seed 2026, 400 random logs:

* OpenProcess 0.11.1: guarantees hold on 406, identical trees on 369 (all six
  textbook logs among them), 37 differ, of which 30 less precise.  Every one
  of the 37 came from the sequence cut: groups that are only ever skipped
  *together* (``<b>, <b,b,a,c>``: ``a`` and ``c`` never appear without each
  other) were modelled as skippable *separately*, ``→(…, ×(a,τ), ×(c,τ))``,
  where the reference gives ``→(…, ×(→(a,c),τ))``; and a cut was accepted
  when *some* pair of activities reached across it rather than *every* pair.
* After the strict sequence cut (Leemans 2017, p. 233) and the every-pair
  test: guarantees hold on 406, identical trees on 401, 5 differ (2 less
  precise, 2 equal, 1 more precise), all of them fall-throughs on logs that
  have no cut at all, where the two implementations try the fall-throughs
  in a different order.

With a third argument, a noise threshold, it compares **IMf** instead
(issue #74), on logs that look like a real process with a few odd cases:
each is played out from a random process tree (4–8 activities; sequence,
choice, parallel, loop) and gets up to four rare traces with an event
dropped or added.  Only the trees and the soundness of ours are compared:
IMf does not promise fitness 1.  Against PM4Py 2.7.23, seed 1, 150 logs,
f = 0.2: 129 identical, 21 different.  What the 21 show:

* 8 come from the **end activities**: OpenProcess filters them like the
  edges, PM4Py keeps them all, so a rare trace that ends inside a loop
  (``<c,d>`` among ``<c>, <c,d,c>``) stops PM4Py from finding the loop cut
  that OpenProcess finds on the filtered graph.
* a **sequence cut on the filtered graph** can leave a group whose sublog is
  empty: OpenProcess keeps it as a τ in the sequence, ``→(c, τ, …)``;
* a **loop with several redo parts** is ``↺(body, r1, r2)`` here and
  ``↺(body, ×(r1, r2))`` in PM4Py: the same behaviour, written differently;
* after a cut on the filtered graph the **sublogs differ** (which events of
  an odd trace go to which side), and a different cut follows further down.
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import warnings
from collections import Counter
from pathlib import Path

# The repository root, so the script runs from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openprocess.mining.analysis import check_soundness            # noqa: E402
from openprocess.mining.conformance.token_replay import token_replay  # noqa: E402
from openprocess.mining.discovery.inductive import inductive_miner  # noqa: E402
from openprocess.mining.log import SimpleLog, parse_simple_log      # noqa: E402
from openprocess.mining.pnml import pnml_string                     # noqa: E402

TEXTBOOK = [
    "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]",
    "[<a,b,c,d>^3, <a,c,b,d>^4, <a,b,c,e,f,b,c,d>^2, <a,b,c,e,f,c,b,d>, "
    "<a,c,b,e,f,b,c,d>^2, <a,c,b,e,f,b,c,e,f,c,b,d>]",
    "[<a,c,d>^45, <b,c,d>^42, <a,c,e>^38, <b,c,e>^22]",
    "[<a,b,c>, <a,c,b>, <a,b>]",
    "[<a,a,b>, <a,b>, <b>]",
    "[<a,b,a,b,c>, <a,b,c>]",
]


# ---------------------------------------------------------------------------
# Canonical tree strings, so trees from the two libraries can be compared
# ---------------------------------------------------------------------------
def canonical_openprocess(tree) -> str:
    """``->(a,X(b,tau))`` for an OpenProcess ProcessTree."""
    if tree.operator is None or not tree.children:
        return "tau" if tree.is_tau else str(tree.label)
    symbol = {"→": "->", "×": "X", "∧": "+", "↺": "*"}[tree.operator.value]
    kids = [canonical_openprocess(child) for child in tree.children]
    if symbol in ("X", "+"):
        kids = sorted(kids)
    return f"{symbol}({','.join(kids)})"


def canonical_pm4py(tree) -> str:
    """The same string for a PM4Py ProcessTree."""
    from pm4py.objects.process_tree.obj import Operator
    if tree.operator is None:
        return "tau" if tree.label is None else tree.label
    symbol = {Operator.SEQUENCE: "->", Operator.XOR: "X",
              Operator.PARALLEL: "+", Operator.LOOP: "*"}[tree.operator]
    kids = [canonical_pm4py(child) for child in tree.children]
    if symbol in ("X", "+"):
        kids = sorted(kids)
    return f"{symbol}({','.join(kids)})"


# ---------------------------------------------------------------------------
# Logs: as OpenProcess's SimpleLog (a Counter of traces) and as a PM4Py frame
# ---------------------------------------------------------------------------
def to_dataframe(log: SimpleLog):
    import pandas as pd
    rows, case = [], 0
    for trace, count in log.items():
        for _ in range(count):
            case += 1
            for position, activity in enumerate(trace):
                rows.append({"case:concept:name": f"c{case}", "concept:name": activity,
                             "time:timestamp": pd.Timestamp("2026-01-01")
                             + pd.Timedelta(seconds=position)})
    return pd.DataFrame(rows)


def structured_logs(seed: int, count: int) -> list[SimpleLog]:
    """Logs played out from random process trees, with a few rare odd traces."""
    rng = random.Random(seed)

    def tree(activities: list[str], depth: int = 0):
        if len(activities) == 1:
            return activities[0]
        operator = rng.choice(["seq", "seq", "xor", "and", "loop"] if depth else ["seq"])
        cut = rng.randint(1, len(activities) - 1)
        return (operator, tree(activities[:cut], depth + 1), tree(activities[cut:], depth + 1))

    def play(node) -> list[str]:
        if isinstance(node, str):
            return [node]
        operator, left, right = node
        if operator == "seq":
            return play(left) + play(right)
        if operator == "xor":
            return play(left) if rng.random() < 0.6 else play(right)
        if operator == "and":
            first, second, mixed = play(left), play(right), []
            while first or second:
                source = first if (first and (not second or rng.random() < 0.5)) else second
                mixed.append(source.pop(0))
            return mixed
        run = play(left)
        while rng.random() < 0.3:
            run += play(right) + play(left)
        return run

    logs = []
    for _ in range(count):
        activities = list("abcdefgh"[: rng.randint(4, 8)])
        model = tree(activities)
        log: SimpleLog = Counter(tuple(play(model)) for _ in range(rng.randint(30, 120)))
        for _ in range(rng.randint(0, 4)):
            odd = list(rng.choice(list(log)))
            if len(odd) > 1 and rng.random() < 0.5:
                del odd[rng.randrange(len(odd))]
            else:
                odd.insert(rng.randrange(len(odd) + 1), rng.choice(activities))
            log[tuple(odd)] += rng.randint(1, 2)
        logs.append(log)
    return logs


def cross_check_noise(seed: int, count: int, noise: float) -> int:
    import pm4py
    same, different, unsound = 0, [], 0
    for log in structured_logs(seed, count):
        result = inductive_miner(log, noise)
        reference = pm4py.discover_process_tree_inductive(to_dataframe(log), noise_threshold=noise)
        ours, theirs = canonical_openprocess(result.tree), canonical_pm4py(reference)
        unsound += not check_soundness(result.net).sound
        if ours == theirs:
            same += 1
        else:
            different.append((sum(len(trace) for trace in log), log, ours, theirs))
    print(f"OpenProcess IMf (f = {noise:g}) vs PM4Py {pm4py.__version__}, seed {seed}")
    print(f"{count} logs: identical trees {same}, different {len(different)}; "
          f"OpenProcess's model unsound on {unsound}")
    print("\nDifferences (the 10 smallest logs):")
    for _size, log, ours, theirs in sorted(different, key=lambda entry: entry[0])[:10]:
        print(f"- {notation(log)}\n     OpenProcess  {ours}\n     PM4Py        {theirs}")
    return 0


def notation(log: SimpleLog) -> str:
    return "[" + ", ".join(f"<{','.join(t)}>^{n}" for t, n in log.items()) + "]"


def random_logs(seed: int, count: int) -> list[SimpleLog]:
    """Small logs over 2–5 activities, 2–8 variants of 1–6 events each."""
    rng = random.Random(seed)
    logs = []
    for _ in range(count):
        alphabet = "abcdef"[: rng.randint(2, 5)]
        log: SimpleLog = Counter()
        for _ in range(rng.randint(2, 8)):
            trace = tuple(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
            log[trace] += rng.randint(1, 4)
        logs.append(log)
    return logs


# ---------------------------------------------------------------------------
# Precision of both models on the same log, through PM4Py
# ---------------------------------------------------------------------------
def precisions(log: SimpleLog, ours, theirs_tree) -> tuple[float, float]:
    import pm4py
    from pm4py.objects.conversion.process_tree import converter as tree_to_net
    frame = to_dataframe(log)
    handle, path = tempfile.mkstemp(suffix=".pnml")
    os.close(handle)
    try:
        Path(path).write_text(pnml_string(ours), encoding="utf-8")
        net, initial, final = pm4py.read_pnml(path)
    finally:
        os.unlink(path)
    from pm4py.algo.evaluation.precision import algorithm as precision_algorithm
    quiet = {"show_progress_bar": False}
    mine = precision_algorithm.apply(frame, net, initial, final, parameters=quiet)
    net, initial, final = tree_to_net.apply(theirs_tree)
    reference = precision_algorithm.apply(frame, net, initial, final, parameters=quiet)
    return mine, reference


def main(argv: list[str]) -> int:
    warnings.filterwarnings("ignore")
    import pm4py
    seed = int(argv[1]) if len(argv) > 1 else 2026
    count = int(argv[2]) if len(argv) > 2 else 400
    if len(argv) > 3 and float(argv[3]) > 0:
        return cross_check_noise(seed, count, float(argv[3]))
    logs = [("textbook", parse_simple_log(s)) for s in TEXTBOOK]
    logs += [("random", log) for log in random_logs(seed, count)]

    same, different, broken = 0, [], []
    for kind, log in logs:
        result = inductive_miner(log)
        reference = pm4py.discover_process_tree_inductive(to_dataframe(log), noise_threshold=0.0)
        ours, theirs = canonical_openprocess(result.tree), canonical_pm4py(reference)
        sound = check_soundness(result.net).sound
        fitness = token_replay(result.net, log).fitness
        if not sound or abs(fitness - 1.0) > 1e-9:
            broken.append((notation(log), sound, fitness))
        if ours == theirs:
            same += 1
        else:
            different.append((kind, log, ours, theirs, *precisions(log, result.net, reference)))

    less = sum(1 for d in different if d[4] < d[5] - 1e-9)
    more = sum(1 for d in different if d[4] > d[5] + 1e-9)
    print(f"OpenProcess Inductive Miner vs PM4Py {pm4py.__version__}, seed {seed}")
    print(f"{len(logs)} logs: identical trees {same}, different {len(different)} "
          f"(ours less precise {less}, equal {len(different) - less - more}, more precise {more})")
    print(f"IM guarantees (sound, fitness 1) broken on {len(broken)} logs")
    for entry in broken[:10]:
        print("   ", entry)
    print("\nDifferences (first 15):")
    for kind, log, ours, theirs, mine, ref in different[:15]:
        print(f"- [{kind}] {notation(log)}\n     OpenProcess  {ours}   precision {mine:.3f}"
              f"\n     PM4Py        {theirs}   precision {ref:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
