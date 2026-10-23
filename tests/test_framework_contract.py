"""The workflow framework keeps the promises docs/framework.md makes.

Each test names the section it checks.  Several are regressions found in the
0.12 sweep: a promise the code did not keep.
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from openprocess import flow
from openprocess.flow import BoxError, Runner, Sweep, Table, Workflow, box, load, save, to_python
from openprocess.flow.box import Setting, _doc_hints
from openprocess.flow.record import FINGERPRINT, Record, check, export_experiment, fingerprint
from openprocess.flow.types import EventLog, PetriNet, Scores
from openprocess.mining import parse_simple_log

ROOT = Path(__file__).resolve().parents[1]
L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


# -- §7: fingerprints are content, the same in every session -----------------------------
DETERMINISM = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from openprocess.flow import Workflow, Runner
from openprocess.flow.record import fingerprint
out = {}
for i, text in enumerate(["[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>, <a,b,f,c,d>]",
                          "[<a,b,c,e,f>^5, <a,c,b,e,f>^4, <a,b,c,d,b,c,e,f>^2, <a,g,f>^3, <a,b,f>]"]):
    wf = Workflow("p")
    log = wf.add("typed_log", {"text": text})
    nets = {}
    for b in ["alpha_miner", "inductive_miner", "inductive_tree", "heuristics_miner", "classical_states",
              "directly_follows", "footprint", "top_variants", "prefixes"]:
        n = wf.add(b); wf.connect(log, n); nets[b] = n
    for net in ["alpha_miner", "inductive_miner", "heuristics_miner"]:
        for b in ["check_fit", "token_replay", "alignments", "soundness", "footprint_conformance"]:
            n = wf.add(b); wf.connect(nets[net], n, "model")
            if b != "soundness":
                wf.connect(log, n, "log")
    run = Runner().run(wf)
    for node in wf.order():
        r = run.result(node)
        out[f"{i}:{node.box}:{node.id}"] = fingerprint(r.value) if r.ok else r.status
json.dump(out, sys.stdout, sort_keys=True)
"""


def test_every_standard_box_gives_the_same_fingerprints_under_any_hash_seed(tmp_path):
    """Regression (0.12): the α-algorithm, the Heuristics Miner and the footprint gave other
    fingerprints in another session (their output followed the order of Python sets), so
    `openprocess run --check` failed although nothing had changed."""
    script = tmp_path / "determinism.py"
    script.write_text(DETERMINISM)
    results = []
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run([sys.executable, str(script), str(ROOT)], capture_output=True, text=True, env=env)
        assert done.returncode == 0, done.stderr
        results.append(json.loads(done.stdout))
    assert results[0] == results[1] == results[2]
    assert all(value != "failed" for value in results[0].values())


def test_fingerprints_ignore_construction_order_but_not_content():
    a, b = PetriNet("n"), PetriNet("n")
    for net, order in ((a, ["p", "q"]), (b, ["q", "p"])):
        for place in order:
            net.add_place(place, id=place)
    a.add_transition("x", id="t"); b.add_transition("x", id="t")
    a.add_arc("p", "t"); a.add_arc("t", "q")
    b.add_arc("t", "q"); b.add_arc("p", "t")
    assert fingerprint(a) == fingerprint(b)
    b.add_arc("t", "p")
    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(Scores("m", {"x": 1.0, "y": {1, 2}})) == fingerprint(Scores("m", {"y": {2, 1}, "x": 1.0}))


def test_a_replay_is_fingerprinted_by_its_content_not_its_net():
    """Regression (0.12): scheme 1 fingerprinted a replay by its net, so a replay of another log matched."""
    from openprocess.mining.conformance.token_replay import token_replay
    from openprocess.mining.discovery.inductive import inductive_miner
    net = inductive_miner(parse_simple_log(L1)).net
    one, other = token_replay(net, parse_simple_log(L1)), token_replay(net, parse_simple_log("[<a,e,d>^9]"))
    assert fingerprint(one, 1) == fingerprint(other, 1)          # the old scheme could not tell them apart
    assert fingerprint(one) != fingerprint(other)


def test_a_record_is_checked_with_the_scheme_it_was_made_with(tmp_path):
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    model = wf.add("alpha_miner"); wf.connect(log, model)
    run = Runner().run(wf)
    record = save(wf, tmp_path / "w.cpnflow", run)
    assert record.scheme == FINGERPRINT == 2
    data = json.loads((tmp_path / "w.cpnflow").read_text())
    assert data["record"]["fingerprint"] == 2
    old = Record.from_dict({"results": {model.id: fingerprint(run.value(model), 1)}})     # a 0.11 file
    assert old.scheme == 1 and check(wf, old, run) == []


def test_check_reports_boxes_that_did_not_run_when_asked_for_a_complete_check():
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    record = Record(results={log.id: "x", "n999": "y"})
    run = Runner().run(wf)
    assert check(wf, record, run) == [f"“{wf.title(log)}” gives a different result than recorded."]
    assert len(check(wf, record, run, complete=True)) == 2


def test_check_on_the_command_line_fails_without_recorded_results(tmp_path):
    """Regression (0.12): `--check` on a file with nothing recorded said every result was reproduced."""
    wf = Workflow("w")
    wf.add("typed_log", {"text": L1})
    save(wf, tmp_path / "w.cpnflow")                                      # no run: no results
    done = subprocess.run([sys.executable, "-m", "openprocess.cli", "run", str(tmp_path / "w.cpnflow"), "--check"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 1 and "no recorded results" in done.stderr


# -- §8.3: an exported experiment runs where it is unpacked --------------------------------
def test_an_exported_experiment_reproduces_from_its_own_zip(tmp_path):
    """Regression (0.12): the zip moved the inputs into inputs/ but the workflow still read them
    from the folder, and run.py wrote `PosixPath('…')`, which it did not import."""
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "orders.log.txt").write_text(L1)
    wf = Workflow("orders")
    log = wf.add("open_log", {"file": "orders.log.txt"})
    model = wf.add("inductive_miner"); wf.connect(log, model)
    fit = wf.add("check_fit"); wf.connect(model, fit, "model"); wf.connect(log, fit, "log")
    run = Runner(folder=folder).run(wf)
    export_experiment(wf, run, tmp_path / "orders.zip", folder)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(tmp_path / "orders.zip") as zipped:
        zipped.extractall(unpacked)
    assert (unpacked / "inputs" / "orders.log.txt").is_file()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-m", "openprocess.cli", "run", "orders.cpnflow", "--check"],
                          capture_output=True, text=True, cwd=unpacked, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    done = subprocess.run([sys.executable, "run.py"], capture_output=True, text=True, cwd=unpacked, env=env)
    assert done.returncode == 0, done.stdout + done.stderr


# -- §4.3: Python and back ------------------------------------------------------------------
def test_python_written_for_a_workflow_with_groups_and_files_runs():
    """Regression (0.12): a group's function named types it did not import (NameError: Scores),
    and a file setting was written as PosixPath(...)."""
    wf = Workflow("g")
    log = wf.add("open_log", {"file": "orders.xes"})
    model = wf.add("inductive_miner"); wf.connect(log, model)
    fit = wf.add("check_fit"); wf.connect(model, fit, "model"); wf.connect(log, fit, "log")
    wf.group([model.id, fit.id], "mine and check")
    code = to_python(wf)
    assert "PosixPath" not in code and "file='orders.xes'" in code
    namespace = {}
    exec(compile(code, "generated", "exec"), namespace)
    again = namespace["g"].workflow
    assert sorted(n.box for n in again.nodes.values()) == sorted(n.box for n in wf.nodes.values())
    assert len(again.edges) == len(wf.edges) and len(again.groups) == 1


# -- §4.2: connecting -------------------------------------------------------------------------
def test_connecting_without_naming_the_input_fills_a_free_one():
    @box
    def two_logs(a: EventLog, b: EventLog) -> Table:
        return Table("t", ["a", "b"], [[len(a), len(b)]])
    wf = Workflow("w")
    first, second = wf.add("typed_log", {"text": "[<a>]"}), wf.add("typed_log", {"text": "[<b>^2]"})
    both = wf.add(two_logs)
    wf.connect(first, both)
    wf.connect(second, both)
    assert {(e.source, e.input) for e in wf.edges} == {(first.id, "a"), (second.id, "b")}
    assert Runner().run(wf).value(both).rows == [[1, 2]]


# -- §5.2: one box -------------------------------------------------------------------------------
def test_a_box_returning_another_type_fails_alone_with_a_plain_message():
    @box
    def wrong(log: EventLog) -> PetriNet:
        return {"not": "a net"}

    @box
    def returns_nothing(log: EventLog) -> Scores:
        return None
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    bad, empty = wf.add(wrong), wf.add(returns_nothing)
    fine = wf.add("alpha_miner")
    for node in (bad, empty, fine):
        wf.connect(log, node)
    run = Runner().run(wf)
    assert run.status(bad) == "failed" and "returned a dict" in run.result(bad).error
    assert run.status(empty) == "failed" and "returned None" in run.result(empty).error
    assert run.status(fine) == "done"


def test_a_failed_conversion_fails_the_box_not_the_run():
    """Regression (0.12): a converter raising stopped the whole run with an exception."""
    from openprocess.flow import convert

    class Odd:
        pass
    convert.register(Odd, EventLog, lambda value: (_ for _ in ()).throw(ValueError("cannot")))

    @box
    def gives_odd() -> flow.Any:
        return Odd()

    @box
    def takes_any(x: flow.Any) -> flow.Any:
        return x
    try:
        wf = Workflow("w")
        source = wf.add(gives_odd)
        miner = wf.add("alpha_miner")
        wf.edges.append(flow.Edge(source.id, "out", miner.id, "log"))      # what a converter would allow
        run = Runner().run(wf)
        assert run.status(miner) == "failed" and "Could not convert" in run.result(miner).error
        assert run.status(source) == "done"
    finally:
        convert._converters.pop(next(k for k in convert._converters if k[0].endswith("Odd")), None)


def test_strict_runs_catch_a_box_that_changes_its_input():
    @box
    def clears(log: EventLog) -> EventLog:
        log.traces.clear()
        return log
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    node = wf.add(clears); wf.connect(log, node)
    assert Runner().run(wf).status(node) == "done"
    run = Runner(strict=True).run(wf)
    assert run.status(node) == "failed" and "changed its input" in run.result(node).error


# -- §3: reading a signature ------------------------------------------------------------------
def test_int_settings_refuse_fractions_and_none_stays_none():
    setting = Setting("k", "int", 3)
    assert setting.coerce("4") == 4 and setting.coerce(5.0) == 5 and setting.coerce("6.0") == 6
    with pytest.raises(ValueError):
        setting.coerce(2.5)
    assert Setting("label", "str", None).coerce(None) is None


def test_setting_help_is_read_from_google_style_docstrings_too():
    hints = _doc_hints("Mines.\n\nArgs:\n    noise (float): the threshold\n    k: how many\n")
    assert hints == {"noise": "the threshold", "k": "how many", "Args": hints.get("Args")} or \
        (hints["noise"] == "the threshold" and hints["k"] == "how many")

    @box
    def documented(log: EventLog, noise: float = 0.2) -> EventLog:
        """Filters.

        Args:
            noise (float): the threshold
        """
        return log
    assert documented.spec.setting("noise").help == "the threshold"
    assert documented.spec.description == "Filters."


def test_box_mistakes_still_raise_plain_errors():
    with pytest.raises(BoxError, match="needs a default"):
        @box
        def no_default(log: EventLog, k: int) -> PetriNet: ...


# -- §13: the documented interface ------------------------------------------------------------------
def test_the_specification_describes_every_public_name():
    text = (ROOT / "docs" / "framework.md").read_text(encoding="utf-8")
    missing = [name for name in flow.__all__ if f"`{name}`" not in text]
    assert not missing, f"docs/framework.md does not describe: {', '.join(missing)}"


def test_sweeps_still_collect_every_value():
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    miner = wf.add("inductive_miner", {"noise": Sweep((0.0, 0.3))}); wf.connect(log, miner)
    fit = wf.add("check_fit"); wf.connect(miner, fit, "model"); wf.connect(log, fit, "log")
    table = wf.add("sweep_table"); wf.connect(fit, table)
    run = Runner().run(wf)
    assert len(run.variants) == 2 and len(run.value(table).rows) == 2
    assert "noise" in run.value(table).columns


def test_a_box_that_cannot_run_here_is_not_called_a_difference():
    """The badge said "Differs from the record" when a box needed a library this machine lacks;
    nothing differed, it could not be compared."""
    from openprocess.flow.reproducibility import status
    from openprocess.flow.runner import BLOCKED

    @box(needs="a_module_nobody_has")
    def needs_more(log: EventLog) -> EventLog:
        return log
    wf = Workflow("w")
    log = wf.add("typed_log", {"text": L1})
    extra = wf.add(needs_more); wf.connect(log, extra)
    run = Runner().run(wf)
    assert run.status(extra) == BLOCKED
    record = Record(results={log.id: fingerprint(run.value(log)), extra.id: "recorded elsewhere"})
    verdict = status(wf, record, run)
    assert verdict.state == "changed" and any("could not run here" in d for d in verdict.details)
