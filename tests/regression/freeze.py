"""The regression set: tricky logs with the results OpenProcess gives for them, frozen.

    python tests/regression/freeze.py          # rewrite expected.json from the code as it is now

``logs.json`` holds small event logs that have caught, or could catch, a
mistake: short loops, skipped and repeated activities, empty traces, noise at
the threshold, logs with no cut, events the model does not know.  For each,
``expected.json`` holds what discovery and conformance checking give today,
and ``tests/test_regression_set.py`` fails as soon as one of them changes.

These are *not* reference answers: nobody proved them right.  (The textbook
and hand-calculated cases are in ``tests/test_mining.py``; the comparison
with PM4Py is ``tests/cross_check_inductive_miner.py``.)  What they give is
that no result moves without someone seeing it.  When a change to an
algorithm is meant to move one, run this script and read the diff of
``expected.json`` in the pull request: every line in it is a result that is
now different, and should be explained there.

To add a log: add an entry to ``logs.json`` (``id``, ``why``, ``log`` in the
course's notation, and ``model_from`` when conformance should be checked
against the model mined from another log), then run this script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

NOISE = 0.2


def canonical(tree) -> str:
    """The tree as text, the children of × and ∧ sorted: their order means nothing."""
    if tree.operator is None or not tree.children:
        return "τ" if tree.is_tau else str(tree.label)
    symbol = tree.operator.value
    children = [canonical(child) for child in tree.children]
    if symbol in ("×", "∧"):
        children.sort()
    return f"{symbol}({', '.join(children)})"


def _attempt(compute):
    """The value, or the error as text: an algorithm refusing a log is a result too."""
    try:
        return compute()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}: {error}"


def _group(activities) -> str:
    return "{" + ", ".join(sorted(activities)) + "}"


def results(entry: dict) -> dict:
    """Everything frozen for one entry of ``logs.json``."""
    from openprocess.mining import (align_log, alpha_miner, inductive_miner, parse_simple_log,
                                    precision, token_replay)
    from openprocess.mining.discovery.heuristics import heuristics_miner
    log = parse_simple_log(entry["log"])

    def alpha():
        found = alpha_miner(log)
        return {"start": sorted(found.T_I), "end": sorted(found.T_O),
                "places": sorted(f"{_group(a)} → {_group(b)}" for a, b in found.Y_L),
                "warnings": len(found.warnings)}

    def heuristics():
        return sorted(f"{a} → {b}" for a, b in heuristics_miner(log).edges)

    def conformance():
        if "model_from" in entry:
            net = inductive_miner(parse_simple_log(entry["model_from"])).net
            model = f"IM of {entry['model_from']}"
        else:
            net = inductive_miner(log, noise_threshold=NOISE).net
            model = f"IMf {NOISE} of the log"
        replay = token_replay(net, log)
        aligned = align_log(net, log)
        return {"model": model,
                "replay": {"p": replay.produced, "c": replay.consumed, "m": replay.missing,
                           "r": replay.remaining, "fitness": round(replay.fitness, 6)},
                "alignments": {"fitness": round(aligned.average_fitness, 6),
                               "log_fitness": round(aligned.log_fitness, 6),
                               "fitting_cases": aligned.fitting_traces,
                               "exact": aligned.exact},
                "precision": _attempt(lambda: round(precision(net, log), 6))}

    return {"inductive_miner": _attempt(lambda: canonical(inductive_miner(log).tree)),
            f"inductive_miner_noise_{NOISE}": _attempt(
                lambda: canonical(inductive_miner(log, noise_threshold=NOISE).tree)),
            "alpha": _attempt(alpha),
            "heuristics_miner": _attempt(heuristics),
            "conformance": _attempt(conformance)}


def load() -> list[dict]:
    return json.loads((HERE / "logs.json").read_text(encoding="utf-8"))


def main() -> int:
    entries = load()
    ids = [entry["id"] for entry in entries]
    if len(set(ids)) != len(ids):
        raise SystemExit("logs.json: two entries share an id")
    expected = {entry["id"]: results(entry) for entry in entries}
    (HERE / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
    print(f"Froze {len(expected)} logs into {HERE / 'expected.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
