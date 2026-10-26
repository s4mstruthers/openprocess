"""The Python a researcher writes, exactly as the README and docs/workflows.md show it.

These examples are the promise of the framework: a box is a typed function,
a workflow is boxes connected in a few lines, and both run from a notebook
with nothing but ``pip install openprocess``.  If a change makes any of
them longer, or needs another import, this file fails, so the simple way
stays the way.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


def test_the_readme_workflow_runs_as_written(tmp_path, monkeypatch):
    """README, *Workflows from Python*: open a log, IMf, Check fit, save, load, check."""
    from openprocess.mining import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes
    write_xes(EventLog.from_simple_log(parse_simple_log(L1), "orders"), tmp_path / "orders.xes")
    monkeypatch.chdir(tmp_path)

    from openprocess.flow import Runner, Workflow, save
    from openprocess.flow.boxes.check import check_fit
    from openprocess.flow.boxes.discover import inductive_miner
    from openprocess.flow.boxes.input import open_log

    wf = Workflow("orders")
    log = wf.add(open_log, {"file": "orders.xes"})
    model = wf.add(inductive_miner, {"noise": 0.2})
    fit = wf.add(check_fit)
    wf.connect(log, model); wf.connect(model, fit, "model"); wf.connect(log, fit, "log")
    run = Runner().run(wf)
    assert set(run.value(fit).metrics) == {"fitness", "precision", "generalisation", "simplicity"}
    assert run.result(model).explanation.steps
    save(wf, "orders.cpnflow", run)

    from openprocess.flow import check, load
    again, record = load("orders.cpnflow")
    assert check(again, record, Runner().run(again)) == []


def test_a_box_of_your_own_is_one_typed_function(tmp_path):
    """docs/workflows.md, *Writing a box*: the decorator reads the hints, the
    function stays a function, and the folder's boxes/ is picked up."""
    boxes = tmp_path / "boxes"
    boxes.mkdir()
    (boxes / "last_two.py").write_text(textwrap.dedent('''
        from openprocess.flow import box, EventLog, TransitionSystem
        from openprocess.mining.transition_system import transition_system_from_log
        from openprocess import flow


        @box(group="Discover")
        def last_two(log: EventLog, representation: str = "multiset") -> TransitionSystem:
            """A transition system whose state is the last two activities.

            representation: multiset or sequence
            """
            ts = transition_system_from_log(log.simple_log(), "prefix", representation, 2)
            flow.note(f"{len(ts.states)} states")
            return ts
    '''))
    from openprocess.flow import Runner, Workflow, library_for
    from openprocess.flow.boxes.input import typed_log
    library = library_for(tmp_path)
    spec = next(s for s in library if s.name == "Last two")
    assert [p.name for p in spec.inputs] == ["log"] and [s.name for s in spec.settings] == ["representation"]
    assert spec.setting("representation").help == "multiset or sequence"

    wf = Workflow("mine", library)
    log = wf.add(typed_log, {"text": L1})
    states = wf.add(spec.id)
    wf.connect(log, states)
    run = Runner(library, folder=tmp_path).run(wf)
    assert run.status(states) == "done" and run.result(states).explanation.notes

    # The function is still a function, in a notebook or a test.
    from openprocess.mining import EventLog, parse_simple_log
    assert spec.function(EventLog.from_simple_log(parse_simple_log(L1))).states


def test_a_workflow_as_a_python_function():
    """docs/workflows.md: ``@workflow`` records the boxes a function calls."""
    from openprocess.flow import Runner, to_python, workflow
    from openprocess.flow.boxes.check import check_fit
    from openprocess.flow.boxes.discover import inductive_miner
    from openprocess.flow.boxes.input import typed_log

    @workflow(name="demo")
    def demo():
        log = typed_log(text=L1)
        model = inductive_miner(log, noise=0.2)
        return check_fit(model, log)

    wf = demo.workflow
    assert len(wf.nodes) == 3 and len(wf.edges) == 3
    assert "def demo" in to_python(wf)
    assert Runner().run(wf).ok


def test_the_engines_work_without_the_framework():
    """openprocess.mining's quick start: plain functions on a log."""
    from openprocess.mining import align_log, inductive_miner, parse_simple_log, precision
    simple = parse_simple_log(L1)
    model = inductive_miner(simple).net
    assert align_log(model, simple).average_fitness == 1.0
    assert 0 < precision(model, simple) <= 1


def test_the_framework_imports_nothing_heavy():
    """``import openprocess.flow`` in a fresh interpreter loads no Qt, no MCP
    SDK and no data-science library beyond numpy (which the engines rely on):
    a notebook gets the framework alone."""
    code = ("import sys, openprocess, openprocess.flow, openprocess.mining\n"
            "heavy = [m for m in sys.modules if m.split('.')[0] in "
            "('PySide6', 'mcp', 'pydantic', 'starlette', 'pandas', 'scipy', 'matplotlib')]\n"
            "print(heavy)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_the_parallel_example_in_the_guide_runs_as_written(tmp_path):
    """docs/workflows.md, *Parallel work*: the box file from the guide, run on
    several processes and as a loop, gives the same table and the same notes."""
    import re
    from openprocess.flow import Runner, Workflow, library_for
    from openprocess.flow.boxes.input import typed_log
    guide = (ROOT / "docs" / "workflows.md").read_text(encoding="utf-8")
    section = guide.split("### Parallel work", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    (tmp_path / "boxes").mkdir()
    (tmp_path / "boxes" / "variant_fit.py").write_text(code, encoding="utf-8")
    library = library_for(tmp_path)
    spec = next(s for s in library if s.name == "Variant fit")
    outcomes = []
    for workers in ("1", "3"):
        import os
        os.environ["OPENPROCESS_WORKERS"] = workers
        try:
            wf = Workflow("guide", library)
            log = wf.add(typed_log, {"text": "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>, <a,b,d>, <a,c,d,e>]"})
            model = wf.add("alpha_miner")
            fit = wf.add(spec.id)
            wf.connect(log, model); wf.connect(model, fit, "model"); wf.connect(log, fit, "log")
            run = Runner(library, folder=tmp_path).run(wf)
            assert run.status(fit) == "done", run.result(fit).error
            outcomes.append((run.value(fit).rows, run.result(fit).explanation.notes))
        finally:
            del os.environ["OPENPROCESS_WORKERS"]
    assert outcomes[0] == outcomes[1] and len(outcomes[0][0]) == 5
