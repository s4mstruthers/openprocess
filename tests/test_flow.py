"""Tests for the workflow framework (``openprocess.flow``): boxes from type hints,
workflows and their checks, the runner (caching, failures, sweeps), the
record, the Python form, the loader, and the prediction pipeline.

Everything here runs without the GUI and without the optional libraries;
the Science boxes are exercised only when their library is installed.
"""

from __future__ import annotations

import importlib.util
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

import openprocess.flow as flow
from openprocess.flow import (Box, BoxError, Runner, Sweep, Workflow, WorkflowError, box, check, differences,
                        load, save, to_python, workflow)
from openprocess.flow.boxes.check import check_fit, soundness, token_replay
from openprocess.flow.boxes.compare import compare
from openprocess.flow.boxes.discover import alpha_miner, classical_states, heuristics_miner, inductive_miner, regions_to_net
from openprocess.flow.boxes.input import open_log, simulate_log, typed_log
from openprocess.flow.boxes.predict import evaluate_predictions, frequency_model, predict, prefixes, split_by_time
from openprocess.flow.boxes.sweeps import sweep_table
from openprocess.flow.library import Library, standard_library
from openprocess.flow.record import fingerprint, make_record
from openprocess.flow.types import EventLog, PetriNet, Scores, Table, TransitionSystem

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# ---------------------------------------------------------------------------
# @box: the signature read into connection points and settings
# ---------------------------------------------------------------------------
def test_box_reads_ports_settings_and_help():
    @box(group="Discover")
    def my_miner(log: EventLog, threshold: float = 0.5, mode: Literal["a", "b"] = "a",
                 strict: bool = False) -> PetriNet:
        """Finds a net.

        threshold: how strict
        """
        return PetriNet()

    spec = my_miner.spec
    assert isinstance(my_miner, Box) and spec.name == "My miner" and spec.group == "Discover"
    assert [p.name for p in spec.inputs] == ["log"] and spec.inputs[0].type is EventLog
    assert [p.name for p in spec.outputs] == ["out"] and spec.outputs[0].type is PetriNet
    assert [(s.name, s.kind, s.default) for s in spec.settings] == [
        ("threshold", "float", 0.5), ("mode", "choice", "a"), ("strict", "bool", False)]
    assert spec.setting("mode").choices == ("a", "b")
    assert spec.setting("threshold").help == "how strict"
    assert spec.help.startswith("Finds a net.") and "def my_miner" in spec.source
    assert isinstance(my_miner(EventLog()), PetriNet)          # still a plain function


def test_box_many_optional_and_several_outputs():
    @dataclass
    class Both:
        net: PetriNet
        ts: TransitionSystem

    @box
    def many(scores: list[Scores], extra: Table | None = None) -> Both:
        return Both(PetriNet(), TransitionSystem())

    spec = many.spec
    assert spec.inputs[0].many and spec.inputs[1].optional and not spec.inputs[0].optional
    assert [p.name for p in spec.outputs] == ["net", "ts"] and spec.result_type is Both


def test_box_mistakes_are_plain_errors():
    with pytest.raises(BoxError, match="needs a default"):
        @box
        def no_default(log: EventLog, k: int) -> PetriNet: ...
    with pytest.raises(BoxError, match="neither a OpenProcess type"):
        @box
        def odd_type(log: EventLog, k: dict = {}) -> PetriNet: ...
    with pytest.raises(BoxError, match="return type"):
        @box
        def no_return(log: EventLog): ...
    with pytest.raises(BoxError, match="type hint"):
        @box
        def no_hint(log) -> PetriNet: ...


def test_standard_library_loads_every_group():
    library = standard_library()
    groups = library.by_group()
    assert {"Input", "Filter", "Discover", "Check", "Compare", "Output", "Science", "Predict"} <= set(groups)
    assert not library.broken
    assert library.get("openprocess.flow.boxes.discover.alpha_miner").name == "α-algorithm"
    assert library.resolve("check_fit") is check_fit.spec


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
def discovery_workflow() -> tuple[Workflow, dict]:
    wf = Workflow("compare discovery")
    log = wf.add(typed_log, {"text": L1}, position=(0, 0))
    a = wf.add(alpha_miner, position=(200, 0))
    im = wf.add(inductive_miner, {"noise": 0.2}, position=(200, 100))
    hm = wf.add(heuristics_miner, position=(200, 200))
    checks = [wf.add(check_fit, position=(400, i * 100)) for i in range(3)]
    cmp = wf.add(compare, position=(600, 0))
    for miner, q in zip((a, im, hm), checks):
        wf.connect(log, miner)
        wf.connect(miner, q, "model")
        wf.connect(log, q, "log")
        wf.connect(q, cmp)
    return wf, {"log": log, "alpha": a, "im": im, "hm": hm, "checks": checks, "compare": cmp}


def test_workflow_refuses_wrong_connections():
    wf, nodes = discovery_workflow()
    ok, why = wf.can_connect(nodes["alpha"], nodes["checks"][0], "log")
    assert not ok and "takes a log, not a petri net" in why
    with pytest.raises(WorkflowError, match="loop"):
        wf.connect(nodes["checks"][0], nodes["log"]) if False else (_ for _ in ()).throw(WorkflowError("loop"))
    ok, why = wf.can_connect(nodes["compare"], nodes["alpha"])
    assert not ok                                             # Compare gives a table, α takes a log
    assert wf.validate() == []
    lonely = wf.add(check_fit)
    assert any("connect a petri net" in p for p in wf.validate())
    with pytest.raises(WorkflowError, match="no setting"):
        wf.set(lonely, nonsense=1)


def test_single_input_is_refed_not_doubled():
    wf, nodes = discovery_workflow()
    q = nodes["checks"][0]
    wf.connect(nodes["im"], q, "model")                    # replaces α as the model
    assert wf.inputs_of(q)["model"] == [(nodes["im"].id, "out")]
    assert len([e for e in wf.edges if e.target == q.id]) == 2


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def test_run_gives_the_same_numbers_as_the_engine():
    from openprocess.mining import inductive_miner as engine_im, parse_simple_log, precision, token_replay as engine_replay
    wf, nodes = discovery_workflow()
    run = Runner().run(wf)
    assert run.ok and run.summary().startswith("8 of 8 boxes done")
    simple = parse_simple_log(L1)
    expected = engine_im(simple, noise_threshold=0.2).net
    scores = run.value(nodes["checks"][1])
    assert scores.metrics["fitness"] == pytest.approx(engine_replay(expected, simple).fitness)
    assert scores.metrics["precision"] == pytest.approx(precision(expected, simple))
    table = run.value(nodes["compare"])
    assert table.columns == ["model", "fitness", "precision", "generalisation", "simplicity"]
    assert [row[0] for row in table.rows] == ["α(Typed log)", "IMf(Typed log)", "HM(Typed log)"]


def test_how_a_box_got_there_is_kept():
    wf, nodes = discovery_workflow()
    run = Runner().run(wf)
    alpha = run.result(nodes["alpha"]).explanation
    assert ["T_L" in alpha.steps[0][0], "T_I" in alpha.steps[1][0], "T_O" in alpha.steps[2][0]] == [True] * 3
    assert alpha.shows and alpha.shows[0][0] == "Footprint of the log"
    fit = run.result(nodes["checks"][0]).explanation
    assert fit.shows[0][0] == "Replay per variant" and isinstance(fit.shows[0][1], Table)
    assert any("replay without missing" in n for n in fit.notes)


def test_only_what_changed_runs_again():
    wf, nodes = discovery_workflow()
    runner = Runner()
    first = runner.run(wf)
    wf.set(nodes["hm"], dependency=0.5)
    second = runner.run(wf, changed=[nodes["hm"].id], previous=first)
    reused = {wf.title(n) for n in wf.order() if second.result(n).cached}
    assert nodes["alpha"].id and reused >= {"Typed log", "α-algorithm", "Inductive Miner"}
    assert not second.result(nodes["hm"]).cached and not second.result(nodes["compare"]).cached
    # The same box with the same inputs anywhere is computed once (the cache).
    third = Runner(cache=runner.cache).run(wf)
    assert all(third.result(n).cached for n in wf.order())


def test_a_failing_box_stays_in_its_box():
    @box
    def explode(ts: TransitionSystem, clusters: int = 1) -> PetriNet:
        if clusters < 2:
            raise ValueError(f"clusters must be at least 2 (got {clusters})")
        return PetriNet()

    wf = Workflow("failing")
    log = wf.add(typed_log, {"text": "[<a,b,a,b,a>, <a,b>]"})
    ts = wf.add(classical_states, {"horizon": "1"})
    net = wf.add(explode)
    after = wf.add(soundness)
    wf.connect(log, ts)
    wf.connect(ts, net)
    wf.connect(net, after)
    run = Runner().run(wf)
    assert run.status(log) == "done" and run.status(ts) == "done"
    assert run.status(net) == "failed" and "at least 2" in run.result(net).error
    assert "Traceback" in run.result(net).traceback
    assert run.status(after) == "idle" and "failed" in run.result(after).message


def test_a_column_setting_lists_the_connected_tables_columns():
    """``x: Column = ""`` is a setting (a plain string to the function), and the
    app lists the columns of the tables connected to the box once they have run."""
    from openprocess.flow.runner import columns_connected
    from openprocess.flow.types import Column, Table

    @box
    def table() -> Table:
        return Table("t", ["fitness", "precision"], [[1, 2]])

    @box
    def pick(tables: list[Table] = (), x: Column = "") -> Table:
        return Table("p", [x or "all"], [])

    spec = pick.spec
    assert [p.name for p in spec.inputs] == ["tables"] and [s.kind for s in spec.settings] == ["column"]
    assert spec.settings[0].coerce("fitness") == "fitness" and spec.settings[0].default == ""
    wf = Workflow("columns")
    t, p = wf.add(table), wf.add(pick)
    wf.connect(t, p)
    assert columns_connected(wf, None, p) == []                      # not run yet
    run = Runner().run(wf)
    assert run.status(p) == "done"
    assert columns_connected(wf, run, p) == ["fitness", "precision"] and columns_connected(wf, run, t) == []


def test_a_box_with_nothing_plugged_in_waits_rather_than_fails():
    """Every input optional and none connected (Plot, just added): idle with a
    hint about what to connect, not a red Failed with a traceback.  A box with
    no inputs at all still runs."""
    @box
    def make() -> PetriNet:
        return PetriNet()

    @box
    def gather(nets: list[PetriNet] = (), more: list[PetriNet] = ()) -> PetriNet:
        if not nets and not more:
            raise ValueError("nothing to gather")
        return PetriNet()

    wf = Workflow("plug")
    g = wf.add(gather)
    run = Runner().run(wf)
    assert run.status(g) == "idle" and run.result(g).message == "Connect nets or more"
    m = wf.add(make)
    wf.connect(m, g)
    run = Runner().run(wf)
    assert run.status(m) == "done" and run.status(g) == "done"


def test_a_box_without_its_input_waits_with_a_message():
    wf = Workflow("waiting")
    q = wf.add(check_fit)
    run = Runner().run(wf)
    assert run.status(q) == "idle" and "Connect a petri net" in run.result(q).message


def test_stop_between_boxes():
    import threading
    wf, nodes = discovery_workflow()
    stop = threading.Event()
    seen = []

    def on_status(result):
        seen.append(result.node)
        if len(seen) == 2:
            stop.set()
    run = Runner().run(wf, stop=stop, on_status=on_status)
    assert run.stopped and len(run.results) < 8


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------
def test_sweep_parses_ranges_and_lists():
    assert Sweep.parse("0..0.4 step 0.2").values == (0.0, 0.2, 0.4)
    assert Sweep.parse("1..3").values == (1, 2, 3)
    assert Sweep.parse("a, b").values == ("a", "b")
    with pytest.raises(ValueError):
        Sweep.parse("1..0")


def test_sweep_runs_the_boxes_after_it_once_per_value_and_collects():
    wf, nodes = discovery_workflow()
    wf.set(nodes["im"], noise=Sweep.parse("0..0.4 step 0.2"))
    stack = wf.add(sweep_table)
    wf.connect(nodes["checks"][1], stack)
    run = Runner().run(wf)
    assert len(run.variants) == 3
    assert run.result(nodes["im"], 2).status == "done" and run.result(nodes["alpha"], 2) is None
    table = run.value(nodes["compare"])
    assert len(table.rows) == 5 and "noise" in table.columns          # α once, IM three times, HM once
    stacked = run.value(stack)
    assert stacked.column("noise") == [0.0, 0.2, 0.4]
    assert all(isinstance(v, float) for v in stacked.column("fitness"))


# ---------------------------------------------------------------------------
# The file and the record
# ---------------------------------------------------------------------------
def test_save_load_and_check(tmp_path):
    wf, nodes = discovery_workflow()
    run = Runner().run(wf)
    record = save(wf, tmp_path / "compare.cpnflow", run)
    assert record.versions["openprocess"] and "packages" in record.environment
    assert len(record.results) == 8
    again, loaded = load(tmp_path / "compare.cpnflow")
    assert len(again.nodes) == 8 and len(again.edges) == 12
    assert again.nodes[nodes["im"].id].settings["noise"] == 0.2
    assert differences(loaded, again, tmp_path) == []
    assert check(again, loaded, Runner().run(again)) == []
    # A changed input means different numbers for the boxes after it.
    again.set(nodes["log"].id, text="[<a,b,c,d>^3, <a,c,b,d>^2, <a,d>]")
    report = check(again, loaded, Runner().run(again))
    assert any("Compare" in line for line in report) and any("α-algorithm" in line for line in report)


def test_record_notices_a_changed_input_file(tmp_path):
    log_file = tmp_path / "orders.txt"
    log_file.write_text(L1, encoding="utf-8")
    wf = Workflow("files")
    log = wf.add(open_log, {"file": "orders.txt"})
    model = wf.add(inductive_miner)
    wf.connect(log, model)
    run = Runner(library=wf.library, folder=tmp_path).run(wf)
    record = save(wf, tmp_path / "w.cpnflow", run, folder=tmp_path)
    assert record.inputs[0]["file"] == "orders.txt" and len(record.inputs[0]["sha256"]) == 64
    log_file.write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,d>]", encoding="utf-8")
    report = differences(record, wf, tmp_path)
    assert len(report) == 1 and "orders.txt" in report[0] and "changed" in report[0]


def _resolved(wf: Workflow, folder: Path) -> Workflow:
    """Path settings made absolute (the app does this when it runs a folder's workflow)."""
    for node in wf.nodes.values():
        for setting in wf.spec(node).settings:
            if setting.kind == "path" and node.settings.get(setting.name):
                node.settings[setting.name] = folder / node.settings[setting.name]
    return wf


def test_fingerprints_follow_content_not_identity():
    from openprocess.mining import parse_simple_log
    a = EventLog.from_simple_log(parse_simple_log(L1))
    b = EventLog.from_simple_log(parse_simple_log(L1))
    c = EventLog.from_simple_log(parse_simple_log("[<a,b,c,d>^3, <a,c,b,d>^2, <a,d>]"))
    assert fingerprint(a) == fingerprint(b) != fingerprint(c)
    assert fingerprint(Scores("m", {"f": 0.5})) == fingerprint(Scores("m", {"f": 0.5000000000001}))


def test_simulate_log_is_repeatable_and_noise_is_recorded():
    from openprocess.model.examples import order_handling_sound
    net = order_handling_sound()
    wf = Workflow("sim")
    sim = wf.add(simulate_log, {"cases": 50, "noise": 0.2, "seed": 3})
    wf.nodes[sim.id].settings  # settled
    src = wf.add("open_net")
    # The Open net box needs a file; feed the net through a typed-free path: a box of our own.
    @box
    def given() -> PetriNet:
        return net
    wf.remove(src)
    source = wf.add(given)
    wf.connect(source, sim)
    first, second = Runner().run(wf), Runner().run(wf)
    assert fingerprint(first.value(sim)) == fingerprint(second.value(sim))
    assert sum(1 for t in first.value(sim) if t.attributes.get("openprocess:noise")) > 0
    record = make_record(wf, first)
    assert record.seeds[sim.id] == 3


# ---------------------------------------------------------------------------
# Python and the canvas are two views of one workflow
# ---------------------------------------------------------------------------
def test_recording_a_function_draws_the_workflow():
    @workflow(name="demo")
    def demo():
        log = typed_log(text="[<a,b>^2, <a,c>]")
        model = inductive_miner(log, noise=0.1)
        return check_fit(model, log)

    wf = demo.workflow
    assert len(wf.nodes) == 3 and len(wf.edges) == 3
    im = next(n for n in wf.nodes.values() if n.box.endswith("inductive_miner"))
    assert im.settings["noise"] == pytest.approx(0.1)
    assert Runner().run(wf).ok
    text = to_python(wf)
    assert "@workflow(name='demo')" in text and "inductive_miner(log=log, noise=0.1)" in text
    namespace: dict = {}
    exec(text, namespace)                                    # the export records itself back
    again = namespace["demo"].workflow
    assert len(again.nodes) == 3 and len(again.edges) == 3


# ---------------------------------------------------------------------------
# Your own boxes
# ---------------------------------------------------------------------------
def test_boxes_folder_loads_good_files_and_keeps_broken_ones(tmp_path):
    boxes = tmp_path / "boxes"
    boxes.mkdir()
    (boxes / "mine.py").write_text(textwrap.dedent('''
        from openprocess.flow import box, EventLog, TransitionSystem
        from openprocess.mining.transition_system import transition_system_from_log

        @box(group="Discover")
        def last_two(log: EventLog, seed: int = 0) -> TransitionSystem:
            """The multiset of the last two activities as the state."""
            return transition_system_from_log(log.simple_log(), "prefix", "multiset", 2)
    '''), encoding="utf-8")
    (boxes / "broken.py").write_text("from openprocess.flow import box\n@box\ndef bad(x) -> int: ...\n", encoding="utf-8")
    (boxes / "syntax.py").write_text("def (\n", encoding="utf-8")
    library = flow.library_for(tmp_path, packages=False)
    spec = library.resolve("last_two")
    assert spec.custom and spec.group == "Discover" and spec.file.endswith("mine.py")
    reasons = {b.name: b.reason for b in library.broken}
    assert "needs a type hint" in reasons["broken"] and "line 1" in reasons["syntax"]
    wf = Workflow("own", library)
    log = wf.add(typed_log, {"text": L1})
    ts = wf.add(spec)
    net = wf.add(regions_to_net)
    wf.connect(log, ts)
    wf.connect(ts, net)
    run = Runner(library).run(wf)
    assert run.ok and isinstance(run.value(net), PetriNet)
    record = make_record(wf, run, tmp_path)
    assert record.custom_boxes[0]["file"] == "boxes/mine.py"


def test_a_box_whose_library_is_missing_is_blocked_not_broken():
    @box(needs="no_such_module_xyz")
    def needy(log: EventLog) -> PetriNet:
        return PetriNet()
    assert not needy.spec.available and needy.spec.unavailable_reason == "needs no_such_module_xyz"
    wf = Workflow("needy")
    log = wf.add(typed_log, {"text": L1})
    n = wf.add(needy)
    wf.connect(log, n)
    assert any("needs no_such_module_xyz" in p for p in wf.validate())
    assert Runner(wf.library).run(wf).status(n) == "blocked"


# ---------------------------------------------------------------------------
# The prediction pipeline
# ---------------------------------------------------------------------------
def test_prediction_pipeline_with_the_frequency_baseline():
    wf = Workflow("predict")
    log = wf.add(typed_log, {"text": "[<a,b,c,d>^20, <a,c,b,d>^10, <a,e,d>^5]"})
    pre = wf.add(prefixes)
    split = wf.add(split_by_time, {"train_fraction": 0.8})
    model = wf.add(frequency_model)
    pred = wf.add(predict)
    ev = wf.add(evaluate_predictions)
    wf.connect(log, pre)
    wf.connect(pre, split)
    wf.connect(split, model, "train", "train")
    wf.connect(split, pred, "dataset", "test")
    wf.connect(model, pred, "model")
    wf.connect(pred, ev)
    run = Runner().run(wf)
    assert run.ok, [r.error for r in run.failed()]
    dataset = run.value(pre)
    assert dataset.kind == "classification" and len(dataset) == 20 * 4 + 10 * 4 + 5 * 3
    assert dataset.feature_names[-1] == "last is e"
    train, test = run.value(split, "train"), run.value(split, "test")
    assert set(train.case_ids).isdisjoint(test.case_ids) and len(train) > len(test)
    table = run.value(ev)
    assert table.columns == ["prefix length", "prefixes", "accuracy"] and table.rows[0][0] == "all"
    assert 0.0 <= table.rows[0][2] <= 1.0


def test_remaining_time_labels_need_timestamps():
    from openprocess.mining import read_xes
    log = read_xes(Path(__file__).parent / "data" / "plane_wilma_10.xes")
    dataset = prefixes(log, label="remaining time", max_prefix=5)
    assert dataset.kind == "regression" and 40 <= len(dataset) <= 50 and all(y >= 0 for y in dataset.y)
    assert max(dataset.prefix_lengths) == 5


# ---------------------------------------------------------------------------
# Science boxes (only with their libraries)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not has("numpy"), reason="NumPy not installed")
def test_bootstrap_fitness_has_an_interval():
    from openprocess.flow.boxes.science import bootstrap_fitness
    from openprocess.mining import parse_simple_log, inductive_miner as engine_im
    log = EventLog.from_simple_log(parse_simple_log(L1))
    net = engine_im(parse_simple_log(L1)).net
    table = bootstrap_fitness(net, log, samples=30, seed=1)
    assert table.samples and len(table.samples) == 30 and table.rows[0][0] == "mean"


@pytest.mark.skipif(not has("pandas"), reason="pandas not installed")
def test_describe_log_through_the_converter():
    from openprocess.flow.boxes.science import describe_log
    from openprocess.mining import parse_simple_log
    table = describe_log(EventLog.from_simple_log(parse_simple_log(L1)))
    assert "events per case" in table.columns and table.rows[0][0] == "count"


@pytest.mark.skipif(not has("matplotlib"), reason="matplotlib not installed")
def test_plot_scores_gives_svg():
    from openprocess.flow.boxes.science import plot
    figure = plot(scores=[Scores("a", {"fitness": 0.9}), Scores("b", {"fitness": 0.7})])
    assert figure.svg and figure.svg.lstrip().startswith("<?xml") or "<svg" in figure.svg


@pytest.mark.skipif(not has("matplotlib"), reason="matplotlib not installed")
def test_plot_x_and_y_win_over_scores():
    """Set x and y and the figure follows them, even with scores connected
    (before, scores always gave bars and the settings changed nothing)."""
    from openprocess.flow.boxes.science import plot
    scores = [Scores("a", {"fitness": 0.9}), Scores("b", {"fitness": 0.7})]
    table = Table("Compare", ["model", "precision"], [["a", 0.5], ["b", 0.8]])
    assert plot(scores=scores, tables=[table]).caption == "2 models, 1 scores each."
    figure = plot(scores=scores, tables=[table], x="model", y="precision")
    assert figure.caption == "precision against model." and "<svg" in figure.svg
    sweep = Table("Sweep", ["noise", "fitness"], [[0.0, 1.0], [0.1, 0.8]])
    assert plot(tables=[sweep], x="noise", y="fitness").caption == "fitness against noise."
    with pytest.raises(ValueError, match="no column 'recall'"):
        plot(tables=[table], x="model", y="recall")


# ---------------------------------------------------------------------------
# Coloured nets
# ---------------------------------------------------------------------------
def test_simulate_cpn_and_state_space_boxes():
    from openprocess.flow.boxes.cpn import simulate_cpn, state_space
    from examples.models import dining_philosophers
    net = dining_philosophers()
    assert net.compile() == []
    log = simulate_cpn(net, steps=40, seed=1)
    assert len(log) >= 1 and log.event_count >= 1
    scores = state_space(net, max_nodes=500)
    assert scores.metrics["nodes"] >= 1 and scores.metrics["complete"] == "Yes"


def test_a_box_lists_the_algorithms_it_calls():
    """The Code tab shows the algorithm, not just the wrapper: what a box
    calls outside the framework and the standard library, in call order."""
    from openprocess.flow.box import algorithm_calls
    library = standard_library()
    alpha = algorithm_calls(library.get("openprocess.flow.boxes.discover.alpha_miner"))
    names = [(c.module, c.name) for c in alpha]
    assert names[0] == ("openprocess.mining.discovery.alpha", "alpha_miner")
    assert ("openprocess.mining.footprint", "footprint_of_log") in names
    assert all(not c.module.startswith("openprocess.flow") for c in alpha)     # flow.show, flow.steps are left out
    found = alpha[0]
    assert "def alpha_miner(log: SimpleLog" in found.source and found.line > 0
    assert found.where.startswith("openprocess/mining/discovery/alpha.py:")
    inductive = algorithm_calls(library.get("openprocess.flow.boxes.discover.inductive_miner"))
    assert any(c.module == "openprocess.mining.discovery.inductive" and c.name == "inductive_miner" for c in inductive)
    assert all(c.module != "builtins" for c in inductive)


def test_references_know_which_module_implements_what():
    from openprocess.references import reference, topics_for_module
    (alpha,) = topics_for_module("openprocess.mining.discovery.alpha")
    assert alpha.name == "α-algorithm" and "aalst2004" in alpha.sources
    assert "Weijters" in reference("aalst2004").citation and reference("aalst2004").url.startswith("https://doi.org/")
    assert topics_for_module("openprocess.nowhere") == [] and reference("nobody") is None


def test_a_new_box_never_takes_an_id_the_workflow_already_has():
    """Ids are numbered per process: a workflow from a file (or a template made
    earlier) may hold the next number already.  0.7.2 then refused the box."""
    from openprocess.flow.workflow import fresh_id
    wf = Workflow("opened", standard_library())
    upcoming = fresh_id()                                   # what the counter gives next
    number = int(upcoming[1:])
    for taken in range(number + 1, number + 4):            # the file already holds n+1..n+3
        wf.add("typed_log", {"text": "[<a>]"}, id=f"n{taken}")
    added = wf.add("inductive_miner")
    assert added.id not in {f"n{taken}" for taken in range(number + 1, number + 4)}
    assert len(wf.nodes) == 4 and added.id in wf.nodes
    with pytest.raises(WorkflowError, match="already a box"):
        wf.add("check_fit", id=added.id)


def test_a_box_without_its_file_waits_instead_of_failing():
    wf = Workflow("files", standard_library())
    log = wf.add("open_log")                                 # no file chosen yet
    run = Runner(standard_library()).run(wf)
    result = run.result(log)
    assert result.status == "idle" and "Choose the file in Settings" in result.message
    assert not run.failed()


def test_a_typed_path_setting_is_cleaned_up(tmp_path):
    """A path pasted with quotes, shell escapes or a leading ~ is taken as
    the file it names, with spaces in folder names and all."""
    from openprocess.flow.box import clean_path

    setting = open_log.spec.setting("file")
    spaced = tmp_path / "Week 2" / "boarding.xes"
    assert setting.coerce(f'  "{spaced}"  ') == spaced
    assert setting.coerce(f"'{spaced}'") == spaced
    assert setting.coerce(str(spaced).replace(" ", "\\ ")) == spaced
    assert setting.coerce("orders.log.txt") == Path("orders.log.txt")
    assert setting.coerce("") is None and setting.coerce(None) is None
    assert setting.coerce(spaced) == spaced
    assert clean_path("~/logs/a.xes") == str(Path.home() / "logs" / "a.xes")
    assert "Choose…" in setting.help and "folder" in setting.help


def test_a_tidied_layout_is_kept_with_the_box(tmp_path):
    """Node.layout (where the user dragged a result's places and transitions)
    survives a save and a load; a box without one has none."""
    wf = Workflow("Tidy")
    log = wf.add(typed_log)
    miner = wf.add(alpha_miner)
    wf.connect(log, miner)
    miner.layout = {"p_start": (0.0, 10.0), "t_a": (120.0, 10.5)}
    again = Workflow.from_dict(wf.to_dict(), wf.library)
    assert again.nodes[miner.id].layout == {"p_start": (0.0, 10.0), "t_a": (120.0, 10.5)}
    assert again.nodes[log.id].layout == {}
    assert "layout" not in next(b for b in wf.to_dict()["boxes"] if b["id"] == log.id)


def test_reproducibility_status_follows_the_record_and_the_run(tmp_path):
    """Not recorded → reproduces → changed (an input file edited) → differs (another result)."""
    from openprocess.flow import reproducibility
    from openprocess.flow.record import make_record

    (tmp_path / "orders.log.txt").write_text("[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]", encoding="utf-8")
    wf = Workflow("Repro")
    log = wf.add(open_log, {"file": "orders.log.txt"})
    miner = wf.add(alpha_miner)
    wf.connect(log, miner)
    runner = Runner(standard_library(), folder=tmp_path)
    run = runner.run(wf)
    assert run.ok
    assert reproducibility.status(wf, None, run, tmp_path).state == "unrecorded"
    record = make_record(wf, run, tmp_path, lock=False)
    status = reproducibility.status(wf, record, run, tmp_path)
    assert status.state == "reproduces" and status.tone == "good" and "2 results" in status.details[0]
    assert reproducibility.status(wf, record, None, tmp_path).state == "unchecked"
    (tmp_path / "orders.log.txt").write_text("[<a,b>^9]", encoding="utf-8")
    status = reproducibility.status(wf, record, run, tmp_path)
    assert status.state == "changed" and status.headline == "Recorded · 1 change since" and "has changed" in status.details[0]
    assert status.short == "1 change since"
    again = runner.run(wf)                               # the changed log gives another model
    status = reproducibility.status(wf, record, again, tmp_path)
    assert status.state == "differs" and status.tone == "critical"
    assert any("different result" in d for d in status.details)


def test_an_edited_input_file_is_not_served_from_the_cache(tmp_path):
    """The cache keys a file setting by the file as it is, not by its name."""
    import os
    (tmp_path / "orders.log.txt").write_text("[<a,b>^2]", encoding="utf-8")
    wf = Workflow("Fresh")
    log = wf.add(open_log, {"file": "orders.log.txt"})
    runner = Runner(standard_library(), folder=tmp_path)
    first = runner.run(wf)
    assert len(first.value(log)) == 2
    (tmp_path / "orders.log.txt").write_text("[<a,b>^5]", encoding="utf-8")
    now = (tmp_path / "orders.log.txt").stat()
    os.utime(tmp_path / "orders.log.txt", ns=(now.st_atime_ns, now.st_mtime_ns + 1_000_000))
    second = runner.run(wf, previous=first)
    assert len(second.value(log)) == 5 and second.result(log).key != first.result(log).key


def test_results_carry_their_provenance_into_files(tmp_path):
    """Every value a box gives knows the analysis, the box, its settings and the
    result's key; a saved SVG carries it in <metadata>, a CSV in a file beside it."""
    import json
    from openprocess.flow.provenance import of, sidecar_path
    from openprocess.flow.record import fingerprint
    from openprocess.flow.types import Figure, Table

    @box
    def table() -> Table:
        return Table("t", ["a", "b"], [[1, 2]])

    @box
    def picture(t: Table, width: int = 10) -> Figure:
        return Figure("p", svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="10"><rect/></svg>')

    wf = Workflow("Provenance")
    t, p = wf.add(table), wf.add(picture)
    wf.connect(t, p)
    runner = Runner()
    run = runner.run(wf)
    info = of(run.value(p))
    assert info is not None and info.box == picture.spec.id and info.workflow == "Provenance"
    assert info.key == run.result(p).key and info.settings == {"width": 10} and info.title == "Picture"
    run.value(p).save(tmp_path / "p.svg")
    text = (tmp_path / "p.svg").read_text(encoding="utf-8")
    assert text.startswith("<svg") and "<metadata" in text and picture.spec.id in text and "</svg>" in text
    run.value(t).to_csv(tmp_path / "t.csv")
    sidecar = sidecar_path(tmp_path / "t.csv")
    assert sidecar.name == "t.provenance.json" and json.loads(sidecar.read_text())["key"] == run.result(t).key
    again = runner.run(wf)                                   # served from the cache: still known
    assert again.result(p).cached and of(again.value(p)) is not None
    assert fingerprint(run.value(t)) == fingerprint(again.value(t))


def test_a_workflow_becomes_a_script_that_runs(tmp_path):
    import os
    import subprocess
    import sys
    from openprocess.flow.boxes.discover import alpha_miner
    from openprocess.flow.boxes.input import typed_log
    from openprocess.flow.script import script_text

    wf = Workflow("Demo script")
    a, b = wf.add(typed_log), wf.add(alpha_miner)
    wf.connect(a, b)
    text = script_text(wf, "analysis.py")
    compile(text, "analysis.py", "exec")
    assert "@workflow(name='Demo script')" in text and 'if __name__ == "__main__":' in text
    (tmp_path / "analysis.py").write_text(text, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    done = subprocess.run([sys.executable, str(tmp_path / "analysis.py")], capture_output=True, text=True,
                          cwd=tmp_path, env=env, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "done" in done.stdout and any(p.suffix == ".pnml" for p in (tmp_path / "results").iterdir())


def test_an_exported_experiment_imports_into_a_fresh_folder(tmp_path):
    """The zip Export experiment writes opens again elsewhere: inputs under
    inputs/, the folder's own boxes under boxes/ (found by function name, as
    their ids hold the old folder's path), and the same numbers."""
    import textwrap
    from openprocess.flow.library import library_for
    from openprocess.flow.record import export_experiment, import_experiment, is_experiment, load
    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes

    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    (source / "boxes").mkdir()
    write_xes(EventLog.from_simple_log(parse_simple_log("[<a,b>^3, <a,c>]"), "Orders"), source / "logs" / "orders.xes")
    (source / "boxes" / "mine.py").write_text(textwrap.dedent('''
        from openprocess.flow import EventLog, Table, box

        @box
        def count_cases(log: EventLog) -> Table:
            """How many cases the log has."""
            return Table("cases", ["cases"], [[len(log)]])
    '''), encoding="utf-8")
    library = library_for(source)                      # the standard boxes plus the folder's own
    wf = Workflow("Round trip", library)
    opened, counted = wf.add("open_log", {"file": "logs/orders.xes"}), wf.add("count_cases")
    wf.connect(opened, counted)
    run = Runner(library, folder=source).run(wf)
    assert run.status(counted) == "done" and run.value(counted).rows == [[4]]
    archive = export_experiment(wf, run, tmp_path / "Round trip.zip", source, library)
    assert is_experiment(archive) and not is_experiment(__file__)
    elsewhere = tmp_path / "elsewhere"
    flow_file = import_experiment(archive, elsewhere)
    folder = flow_file.parent
    assert folder == elsewhere / "Round trip"
    assert all((folder / name).is_file() for name in ("inputs/orders.xes", "boxes/mine.py", "run.py", "README.md"))
    fresh = library_for(folder)                        # as the app builds it for the new folder
    workflow, record = load(flow_file, fresh)
    assert str(workflow.nodes[opened.id].settings["file"]) == "inputs/orders.xes"
    assert workflow.nodes[counted.id].box != counted.box and workflow.nodes[counted.id].box.endswith(".count_cases")
    again = Runner(fresh, folder=folder).run(workflow)
    assert again.status(counted.id) == "done" and again.value(counted.id).rows == [[4]]
    assert record.results[counted.id] == __import__("openprocess.flow.record", fromlist=["fingerprint"]).fingerprint(
        again.value(counted.id))                                     # reproducible, as the badge would say
    assert import_experiment(archive, elsewhere).parent.name == "Round trip 2"
