"""Build the energy-contract exam log analysis (2AMI10, 2026) as a workflow file.

    python make_workflow.py                   # writes "Energy contracts.cpnflow" beside this file
    openprocess run "Energy contracts.cpnflow" --report report.html

One group per kind of exam question: the dotted charts, the discovered
models, their fitness, where the log deviates from them, and properties of
the process.  See README.md for what each box shows on this log.
"""

from pathlib import Path

from openprocess.flow import Workflow, save

HERE = Path(__file__).resolve().parent
LOG = "Exam Eventlog 2026.xes"                  # put the log from Canvas beside this file under this name


def build() -> Workflow:
    wf = Workflow("Energy contracts · exam log 2026")
    log = wf.add("open_log", {"file": LOG}, (0, 520), title="Exam event log")

    # 1. Dotted charts: when things happen, and who does them.
    charts = [({}, "Cases over time"),
              ({"x": "Time since case start", "unit": "Hours"}, "Cases from their start"),
              ({"x": "Time since case start", "sort": "Duration", "unit": "Hours"}, "Cases by duration"),
              ({"rows": "Resource"}, "Resources over time"),
              ({"rows": "Activity", "x": "Relative to case duration (%)", "colour": "Resource"},
               "Activities within a case")]
    dotted = []
    for row, (settings, title) in enumerate(charts):
        chart = wf.add("dotted_chart", settings, (260, 130 * row), title=title)
        wf.connect(log, chart)
        dotted.append(chart.id)
    roles = wf.add("who_does_what", {}, (260, 130 * len(charts)), title="Who does what")
    wf.connect(log, roles)
    charts_group = wf.group(dotted + [roles.id], "1 · Dotted charts")

    # 2. Discovery: the process map and the miners of the course.
    process_map = wf.add("directly_follows", {}, (560, 0), title="Process map")
    footprint = wf.add("footprint", {}, (560, 130), title="Footprint")
    miners = [("alpha_miner", {}, "α-algorithm"),
              ("inductive_miner", {}, "Inductive Miner"),
              ("inductive_miner", {"noise": 0.2}, "Inductive Miner (IMf 0.2)"),
              ("heuristics_miner", {"dependency": 0.9}, "Heuristics Miner")]
    models = []
    for row, (name, settings, title) in enumerate(miners):
        model = wf.add(name, settings, (560, 260 + 130 * row), title=title)
        wf.connect(log, model)
        models.append(model)
    for node in (process_map, footprint):
        wf.connect(log, node)
    wf.group([process_map.id, footprint.id] + [m.id for m in models], "2 · Discovery")

    # 3. Fitness of every model (alignments, as ProM's replayer), side by side;
    #    and soundness, since only a sound net has a well-defined fitness.
    fits, sound = [], []
    for row, model in enumerate(models):
        fit = wf.add("check_fit", {"method": "Alignments"}, (860, 260 + 130 * row),
                     title=f"Fit: {model.title}")
        wf.connect(model, fit, "model")
        wf.connect(log, fit, "log")
        fits.append(fit)
        check = wf.add("soundness", {}, (1160, 260 + 130 * row), title=f"Sound? {model.title}")
        wf.connect(model, check)
        sound.append(check.id)
    table = wf.add("compare", {}, (1160, 0), title="Fitness of the models")
    for fit in fits:
        wf.connect(fit, table)
    wf.group([f.id for f in fits] + sound + [table.id], "3 · Fitness")

    # 4. Where the log deviates: alignments (the log on the model, move by
    #    move) and token replay (the course's p, c, m, r) of the models that do not fit.
    deviations = []
    for row, model in enumerate(m for m in models if m.title != "Inductive Miner"):
        aligned = wf.add("alignments", {}, (1460, 260 + 130 * row), title=f"Deviations: {model.title}")
        wf.connect(model, aligned, "model")
        wf.connect(log, aligned, "log")
        replay = wf.add("token_replay", {}, (1760, 260 + 130 * row), title=f"Token replay: {model.title}")
        wf.connect(model, replay, "model")
        wf.connect(log, replay, "log")
        deviations += [aligned.id, replay.id]
    wf.group(deviations, "4 · Deviations")

    # 5. Properties of the process.
    questions = [
        ("order_rule", {"first": "Cancel previous contract", "then": "Approve contract", "value": ""},
         "Cancelled before approval?"),
        ("order_rule", {"first": "Start cancellation", "then": "Cancel previous contract", "value": ""},
         "Start cancellation, then cancel"),
        ("segregation_of_duties", {"first": "Add customer details", "second": "Add meter details",
                                   "by": "org:resource", "value": ""}, "Same person adds both details"),
        ("time_between", {"start": "Receive application", "end": "Approve contract", "unit": "hours"},
         "Application to approval"),
        ("time_between", {"start": "Receive application", "end": "Reject contract", "unit": "hours"},
         "Application to rejection"),
    ]
    found = []
    for row, (name, settings, title) in enumerate(questions):
        node = wf.add(name, settings, (560, 860 + 130 * row), title=title)
        wf.connect(log, node)
        found.append(node.id)
    wf.group(found, "5 · The process")

    # 1 (continued). The managers' decisions on their own: each day is one
    #    short vertical dash, because both managers decide in batches.  Added
    #    last so that the ids of the boxes above stay as they were.
    decisions = wf.add("filter_activities",
                       {"activities": "Approve contract, Reject contract", "mode": "keep events"},
                       (260, 780), title="Only the decisions")
    wf.connect(log, decisions)
    decision_chart = wf.add("dotted_chart", {"rows": "Resource"}, (260, 910),
                            title="Managers' decisions over time")
    wf.connect(decisions, decision_chart)
    charts_group.members += [decisions.id, decision_chart.id]
    return wf


if __name__ == "__main__":
    workflow = build()
    target = HERE / "Energy contracts.cpnflow"
    save(workflow, target, folder=HERE)
    print(f"Wrote {target.name}: {len(workflow.nodes)} boxes in {len(workflow.groups)} groups")
