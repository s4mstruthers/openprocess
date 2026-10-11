"""Workflows: boxes, their connections, and running them reproducibly.

A box is a Python function with type hints::

    from openprocess.flow import box, EventLog, PetriNet

    @box(group="Discover")
    def my_miner(log: EventLog, threshold: float = 0.5) -> PetriNet:
        \"\"\"The help text.\"\"\"
        ...

A workflow is boxes wired together, built on the canvas, in a file, or in
Python::

    from openprocess.flow import Workflow, Runner, save
    from openprocess.flow.boxes.input import typed_log
    from openprocess.flow.boxes.discover import inductive_miner
    from openprocess.flow.boxes.check import check_fit

    wf = Workflow("demo")
    log = wf.add(typed_log, {"text": "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"})
    model = wf.add(inductive_miner)
    fit = wf.add(check_fit)
    wf.connect(log, model); wf.connect(model, fit, "model"); wf.connect(log, fit, "log")
    run = Runner().run(wf)
    print(run.value(fit).metrics)
    save(wf, "demo.cpnflow", run)      # with the record that makes it reproducible

See ``docs/workflows.md``.
"""

from .box import Box, BoxError, BoxSpec, Port, Setting, box
from .explain import Explanation, current, note, show, steps
from .library import Library, library_for, standard_library
from .record import check, differences, load, save
from .runner import Cache, Result, Run, Runner
from .sweep import Sweep
from .types import (Column, AlignmentResult, Any, CPNet, DFG, Dataset, EventLog, Figure, Footprint, Marking,
                    PetriNet, Predictions, Predictor, ProcessTree, Regions, ReplayResult, Scores,
                    SimpleLog, Table, Text, TransitionSystem, register_type)
from .workflow import Edge, Group, Node, Workflow, WorkflowError, to_python, workflow

__all__ = [
    "Column",
    "AlignmentResult", "Any", "Box", "BoxError", "BoxSpec", "CPNet", "Cache", "DFG", "Dataset", "Edge",
    "EventLog", "Explanation", "Figure", "Footprint", "Group", "Library", "Marking", "Node", "PetriNet",
    "Port", "Predictions", "Predictor", "ProcessTree", "Regions", "ReplayResult", "Result", "Run",
    "Runner", "Scores", "Setting", "SimpleLog", "Sweep", "Table", "Text", "TransitionSystem", "Workflow",
    "WorkflowError", "box", "check", "current", "differences", "library_for", "load", "note",
    "register_type", "save", "show", "standard_library", "steps", "to_python", "workflow",
]
