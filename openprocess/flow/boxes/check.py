"""Check boxes: how well a model fits a log, and what a net is like."""

from __future__ import annotations

from typing import Literal

from ... import flow
from ...mining.analysis import check_soundness
from ...mining.compare_nets import compare_nets
from ...mining.conformance.alignments import AlignmentResult, align_log
from ...mining.conformance.quality import generalisation, precision, simplicity
from ...mining.conformance.token_replay import ReplayResult, token_replay as _replay
from ...mining.footprint import compare_footprints, footprint_of_log, footprint_of_net
from ...mining.log import EventLog
from ...mining.petrinet import PetriNet
from ..box import box
from ..types import Scores, Table


def _replay_table(replay: ReplayResult) -> Table:
    rows = [["⟨" + ", ".join(t.trace) + "⟩", t.count, t.produced, t.consumed, t.missing, t.remaining,
             round(t.fitness, 4)] for t in replay.traces]
    return Table("Replay per variant", ["variant", "cases", "p", "c", "m", "r", "fitness"], rows,
                 note=f"Over the log: p = {replay.produced}, c = {replay.consumed}, "
                      f"m = {replay.missing}, r = {replay.remaining}.")


@box(name="Check fit", group="Check")
def check_fit(model: PetriNet, log: EventLog,
              method: Literal["Token replay", "Alignments"] = "Token replay") -> Scores:
    """Replays every case of the log on the model and scores fitness,
    precision (escaping edges), generalisation and simplicity.

    method: token replay (fast, the course's p c m r) or optimal alignments
    """
    simple = log.simple_log()
    replay = _replay(model, simple)
    flow.show(_replay_table(replay), "Replay per variant")
    if method == "Alignments":
        alignments = align_log(model, simple)
        fitness = alignments.average_fitness
        flow.note(f"{alignments.fitting_traces} of {alignments.trace_count} cases align without a deviation")
        for note in alignments.notes:
            flow.note(note)
    else:
        fitness = replay.fitness
        flow.note(f"{replay.fitting_traces} of {replay.trace_count} cases replay without missing or remaining tokens")
    scores = Scores(model.name, {"fitness": fitness, "precision": precision(model, simple),
                                 "generalisation": generalisation(replay), "simplicity": simplicity(model)})
    scores.note = f"Checked against all {replay.trace_count} cases of {log.name} ({method.lower()})."
    return scores


@box(name="Token replay", group="Check")
def token_replay(model: PetriNet, log: EventLog) -> ReplayResult:
    """Token-based replay with the counts the course uses: produced,
    consumed, missing and remaining tokens, per variant."""
    replay = _replay(model, log.simple_log())
    flow.show(_replay_table(replay), "Replay per variant")
    return replay


@box(name="Alignments", group="Check")
def alignments(model: PetriNet, log: EventLog) -> AlignmentResult:
    """Optimal alignments of every variant with the model (A* over the
    synchronous product, standard cost function)."""
    result = align_log(model, log.simple_log())
    rows = [["⟨" + ", ".join(a.trace) + "⟩", a.count, a.cost if a.complete else "?", round(a.fitness, 4)]
            for a in result.alignments]
    flow.show(Table("Alignments", ["variant", "cases", "cost", "fitness"], rows), "Alignment per variant")
    for note in result.notes:
        flow.note(note)
    return result


@box(name="Soundness", group="Check")
def soundness(model: PetriNet) -> Scores:
    """Is the net a sound WF-net? The three conditions, with each
    violation's counterexample in the notes."""
    report = check_soundness(model)
    for finding in report.findings:
        flow.note(finding)
    verdict = {True: "Sound", False: "Not sound", None: "Undecided"}[report.sound]
    return Scores(model.name, {"soundness": verdict,
                               "WF-net": "Yes" if report.workflow.is_workflow_net else "No",
                               "option to complete": _yes(report.option_to_complete),
                               "proper completion": _yes(report.proper_completion),
                               "no dead transitions": _yes(report.no_dead_transitions)})


def _yes(value) -> str:
    return {True: "Yes", False: "No", None: "?"}[value]


@box(name="Compare to true net", group="Check")
def compare_to_true_net(model: PetriNet, true_net: PetriNet, max_length: int = 12) -> Scores:
    """Model to model: the share of the true net's complete traces the
    model allows (recall), the share of the model's traces the true net
    allows (precision), simplicity, and soundness."""
    comparison = compare_nets(model, true_net, max_length=max_length)
    only_model, only_truth = len(comparison.only_first), len(comparison.only_second)
    for trace in comparison.only_first[:5]:
        flow.note("The model allows ⟨" + ", ".join(trace) + "⟩, the true net does not")
    for trace in comparison.only_second[:5]:
        flow.note("The true net allows ⟨" + ", ".join(trace) + "⟩, the model does not")
    sound = check_soundness(model).sound
    recall = 1.0 if comparison.equivalent else max(0.0, 1 - only_truth / max(1, only_truth + 3))
    prec = 1.0 if comparison.equivalent else max(0.0, 1 - only_model / max(1, only_model + 3))
    flow.note(comparison.summary("the model", "the true net"))
    return Scores(model.name, {"recall": recall, "precision": prec, "simplicity": simplicity(model),
                               "soundness": {True: "Sound", False: "Not sound", None: "Undecided"}[sound]},
                  note="Recall and precision here count the shortest differing traces; "
                       "equivalent nets score 1.")


@box(name="Footprint conformance", group="Check")
def footprint_conformance(log: EventLog, model: PetriNet) -> Scores:
    """Compares the footprint of the log with the footprint of the net."""
    comparison = compare_footprints(footprint_of_log(log.simple_log()), footprint_of_net(model))
    cells = len(comparison.activities) ** 2
    flow.note(f"{len(comparison.differences)} of {cells} cells differ")
    for a, b, in_log, in_model in comparison.differences[:8]:
        flow.note(f"{a} ? {b}: {in_log} in the log, {in_model} in the net")
    return Scores(model.name, {"footprint conformance": comparison.fitness})
