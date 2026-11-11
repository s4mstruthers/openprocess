"""The regression set: results for a set of tricky logs, frozen (see tests/regression/freeze.py).

Each log in ``tests/regression/logs.json`` is mined (Inductive Miner with and
without noise filtering, α-algorithm, Heuristics Miner) and checked against a
model (token replay, alignments, precision), and the results must be the ones
in ``expected.json``.  A failure here does not say the new result is wrong:
it says a result changed.  If the change is meant, run
``python tests/regression/freeze.py`` and explain the diff of
``expected.json`` in the pull request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REGRESSION = Path(__file__).parent / "regression"
sys.path.insert(0, str(REGRESSION))
import freeze  # noqa: E402

ENTRIES = freeze.load()
EXPECTED = json.loads((REGRESSION / "expected.json").read_text(encoding="utf-8"))
HOW = ("\nIf this change is meant: run `python tests/regression/freeze.py` and explain the "
       "diff of tests/regression/expected.json in the pull request.")


def test_every_log_has_a_frozen_result_and_the_other_way_round():
    ids = [entry["id"] for entry in ENTRIES]
    assert len(ids) == len(set(ids)), "two logs share an id"
    assert set(ids) == set(EXPECTED), "logs.json and expected.json list different logs" + HOW
    for entry in ENTRIES:
        assert entry.get("why"), f"{entry['id']}: say why the log is in the set"


@pytest.mark.parametrize("entry", ENTRIES, ids=[entry["id"] for entry in ENTRIES])
def test_results_are_the_frozen_ones(entry):
    now = json.loads(json.dumps(freeze.results(entry)))      # tuples become lists, as in the file
    frozen = EXPECTED[entry["id"]]
    changed = [key for key in frozen if now.get(key) != frozen[key]]
    report = "".join(f"\n  {key}:\n    frozen: {json.dumps(frozen[key], ensure_ascii=False)}"
                     f"\n    now:    {json.dumps(now.get(key), ensure_ascii=False)}" for key in changed)
    assert now == frozen, f"{entry['id']} ({entry['why']}) changed in: {', '.join(changed)}{report}{HOW}"


@pytest.mark.parametrize("entry", ENTRIES, ids=[entry["id"] for entry in ENTRIES])
def test_the_inductive_miner_keeps_its_guarantees(entry):
    """Whatever the tree, the Inductive Miner's model is sound and fits its log."""
    from openprocess.mining import check_soundness, inductive_miner, parse_simple_log, token_replay
    log = parse_simple_log(entry["log"])
    net = inductive_miner(log).net
    assert check_soundness(net).sound is True
    assert token_replay(net, log).fitness == pytest.approx(1.0)
