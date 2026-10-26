"""flow.parallel_map: a box of your own on all cores, with the loop's result, notes and record."""

from __future__ import annotations

import textwrap

import pytest

from openprocess import flow
from openprocess.flow import Runner, Workflow, check, library_for, load, save

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>, <a,b,c,d,e>, <a,e,b,d>, <a,c,d>]"

BOX = textwrap.dedent('''
    import random

    from openprocess import flow
    from openprocess.flow import box, EventLog, PetriNet, Table


    def fit_of(model: PetriNet, variant):
        """One variant: replay it, and a random draw seeded per item."""
        from openprocess.mining.conformance.token_replay import replay_trace
        index, (trace, count) = variant
        replay = replay_trace(model, trace, count)
        draw = random.Random(flow.item_seed(7, index)).random()
        flow.note(f"variant {index}: {len(trace)} events, missing {replay.missing}")
        return [", ".join(trace), count, replay.missing, round(draw, 12)]


    def count_events(variant):
        index, (trace, count) = variant
        return len(trace) * count


    def broken(variant):
        index, (trace, count) = variant
        if index == 2:
            raise ValueError("this variant is broken")
        return index


    @box(group="Check")
    def parallel_fit(model: PetriNet, log: EventLog, workers: int = 0) -> Table:
        """Replays every variant on all cores.

        workers: how many processes (0: as many as the machine has)
        """
        variants = list(enumerate(sorted(log.simple_log().items())))
        rows = flow.parallel_map(fit_of, variants, shared=model, workers=workers or None)
        events = flow.parallel_map(count_events, variants, workers=workers or None)
        flow.note(f"{sum(events)} events in all")
        return Table("Fit per variant", ["variant", "cases", "missing", "draw"], rows)
''')


def _workflow(tmp_path, workers: int):
    (tmp_path / "boxes").mkdir(exist_ok=True)
    (tmp_path / "boxes" / "mine.py").write_text(BOX)
    library = library_for(tmp_path)
    spec = next(s for s in library if s.name == "Parallel fit")
    wf = Workflow("parallel", library)
    log = wf.add("typed_log", {"text": L1})
    model = wf.add("alpha_miner")
    fit = wf.add(spec.id, {"workers": workers})
    wf.connect(log, model)
    wf.connect(model, fit, "model")
    wf.connect(log, fit, "log")
    return wf, fit, library


def test_a_box_runs_on_all_cores_with_the_loops_result(tmp_path):
    wf_one, fit_one, library = _workflow(tmp_path, 1)
    one = Runner(library, folder=tmp_path).run(wf_one)
    wf_many, fit_many, library = _workflow(tmp_path, 3)
    many = Runner(library, folder=tmp_path).run(wf_many)
    assert one.status(fit_one) == many.status(fit_many) == "done", many.result(fit_many).error
    assert many.value(fit_many).rows == one.value(fit_one).rows             # values, order, random draws
    assert many.result(fit_many).explanation.notes == one.result(fit_one).explanation.notes
    notes = many.result(fit_many).explanation.notes
    assert notes[0].startswith("variant 0:") and notes[-1].endswith("events in all")
    assert any(row[2] > 0 for row in many.value(fit_many).rows)            # the α net misses some


def test_the_record_holds_whatever_the_number_of_workers(tmp_path):
    wf, fit, library = _workflow(tmp_path, 3)
    run = Runner(library, folder=tmp_path).run(wf)
    save(wf, tmp_path / "parallel.cpnflow", run)
    again, record = load(tmp_path / "parallel.cpnflow", library_for(tmp_path))
    again.set(next(n for n in again.nodes.values() if n.settings.get("workers") == 3), workers=1)
    # Settings differ (workers), so compare the box's result itself: same fingerprint.
    from openprocess.flow.record import fingerprint
    rerun = Runner(library_for(tmp_path), folder=tmp_path).run(again)
    node = next(n for n in again.nodes.values() if "workers" in n.settings)
    assert fingerprint(rerun.value(node)) == record.results[node.id]


def test_a_failing_item_says_which_and_why(tmp_path):
    (tmp_path / "boxes").mkdir()
    (tmp_path / "boxes" / "mine.py").write_text(BOX)
    library = library_for(tmp_path)
    module = next(s for s in library if s.name == "Parallel fit").function.__module__
    import sys
    broken = sys.modules[module].broken
    items = list(enumerate([(("a",), 1)] * 5))
    with pytest.raises(flow.ParallelError) as raised:
        flow.parallel_map(broken, items, workers=2)
    assert raised.value.index == 2 and "this variant is broken" in str(raised.value)
    assert "ValueError" in raised.value.remote_traceback


def test_a_function_without_a_name_runs_here():
    """A lambda (or a function typed into a notebook) cannot be found by a worker: the loop runs here."""
    assert flow.parallel_map(lambda x: x * 2, range(20), workers=4) == [x * 2 for x in range(20)]


def test_item_seeds_do_not_depend_on_workers():
    assert flow.item_seed(7, 3) == flow.item_seed(7, 3) != flow.item_seed(7, 4) != flow.item_seed(8, 3)


def test_workers_that_cannot_start_fall_back_to_the_loop(monkeypatch):
    """No way to start processes (a sandbox, a broken install): the loop's result, and status() says why."""
    from openprocess import parallel
    monkeypatch.setattr(parallel, "unavailable", None)
    monkeypatch.setattr(parallel, "worker_command", lambda *a: ["/nonexistent/openprocess-worker", *a])
    expected = [flow.item_seed(3, i) for i in range(30)]
    assert parallel.map_items(flow.item_seed, range(30), shared=3, workers=3) == expected
    assert parallel.status()["available"] is False and "could not start" in parallel.status()["why"]
    # Later calls go straight to the loop.
    assert parallel.map_items(flow.item_seed, range(5), shared=3, workers=3) == expected[:5]


def test_workers_that_die_fall_back_to_the_loop(monkeypatch):
    import sys
    from openprocess import parallel
    monkeypatch.setattr(parallel, "unavailable", None)
    monkeypatch.setattr(parallel, "worker_command", lambda *a: [sys.executable, "-c", "raise SystemExit(3)"])
    assert parallel.map_items(flow.item_seed, range(12), shared=1, workers=2) == \
        [flow.item_seed(1, i) for i in range(12)]
    assert parallel.unavailable is not None


def test_a_job_the_workers_cannot_load_runs_here_once(tmp_path, monkeypatch):
    """A function a worker cannot import (its file is gone): the loop for this call, workers stay on."""
    import importlib.util
    import sys
    from openprocess import parallel
    monkeypatch.setattr(parallel, "unavailable", None)
    source = tmp_path / "vanishing_box.py"
    source.write_text("def double(x):\n    return 2 * x\n")
    spec = importlib.util.spec_from_file_location("vanishing_box", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "vanishing_box", module)
    source.unlink()                                                  # workers cannot find it now
    assert parallel.map_items(module.double, range(10), workers=2) == [2 * x for x in range(10)]
    assert parallel.unavailable is None


def test_a_function_that_prints_a_lot_does_not_disturb_the_workers(tmp_path):
    """print() in the work goes to the worker's stderr, which is read as it comes (a full pipe would hang)."""
    import importlib.util
    import sys
    source = tmp_path / "chatty_box.py"
    source.write_text("import sys\n"
                      "def chatty(x):\n"
                      "    print('step ready failed -1 ' * 50)\n"
                      "    sys.stderr.write('warning ' * 2000 + '\\n')\n"
                      "    return x + 1\n")
    spec = importlib.util.spec_from_file_location("chatty_box", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["chatty_box"] = module
    try:
        from openprocess import parallel
        assert parallel.map_items(module.chatty, range(60), workers=3) == list(range(1, 61))
    finally:
        del sys.modules["chatty_box"]
