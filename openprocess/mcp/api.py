"""What the MCP server does, as plain functions: no MCP SDK, no Qt in here.

Every function takes paths and plain values and returns a JSON-able dict, so
the server (:mod:`.server`) is a thin layer of tool definitions over it and
the tests run without the SDK.  Three things hold for every answer:

* **It says where it comes from.**  ``cite`` lists the works the algorithm
  follows (from :mod:`openprocess.references`), and ``how`` carries the
  derivation the box reported (the α-algorithm's eight steps, the Inductive
  Miner's cuts, the replay per variant) as Markdown.
* **It says how to get it again.**  ``reproduce`` is the Python or the
  ``openprocess`` command that gives the same numbers; a discovered model or
  a built workflow is saved as a file with its record, so ``openprocess run
  --check`` can confirm it later.
* **Every file it writes is journaled** (:mod:`openprocess.flow.changes`): a
  backup of what it replaced and one line in the folder's journal, which the
  app shows as *AI changed N files · Review*, with Undo.  An existing file is
  never overwritten unless asked (``overwrite=True``).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .. import references
from ..flow import changes
from ..flow.derivation import to_markdown, value_markdown
from ..mining.log import EventLog, SimpleLog, format_simple_log, parse_simple_log
from ..mining.petrinet import Marking, PetriNet

#: Discovery algorithms by the names an assistant would use.
ALGORITHMS = {
    "alpha": "α-algorithm", "α": "α-algorithm", "alpha-algorithm": "α-algorithm",
    "inductive": "Inductive Miner", "im": "Inductive Miner", "imf": "Inductive Miner",
    "inductive miner": "Inductive Miner", "heuristics": "Heuristics Miner", "heuristic": "Heuristics Miner",
    "heuristics miner": "Heuristics Miner", "dfg": "Directly-follows graph", "map": "Directly-follows graph",
    "process map": "Directly-follows graph", "regions": "State-based regions", "region": "State-based regions",
}

_ALGORITHM_MODULES = {
    "α-algorithm": "openprocess.mining.discovery.alpha",
    "Inductive Miner": "openprocess.mining.discovery.inductive",
    "Heuristics Miner": "openprocess.mining.discovery.heuristics",
    "Directly-follows graph": "openprocess.mining.dfg",
    "State-based regions": "openprocess.mining.discovery.state_regions",
}


class ToolError(ValueError):
    """A message for the assistant (and the user): what went wrong, in words."""


# ---------------------------------------------------------------------------
# Citations and reproduction
# ---------------------------------------------------------------------------
def citations(*modules: str) -> list[dict]:
    """The works the code in ``modules`` follows: ``{key, citation, url}`` each."""
    out: list[dict] = []
    for module in modules:
        for topic in references.topics_for_module(module):
            for key in topic.sources:
                reference = references.reference(key)
                if reference is not None and all(reference.key != r["key"] for r in out):
                    out.append({"key": reference.key, "citation": reference.citation.replace("*", ""),
                                "url": reference.url})
    return out


def app_citation() -> dict:
    from .. import citation
    return {"text": citation.app_text(), "bibtex": citation.app_bibtex()}


def _q(path: str | Path) -> str:
    text = str(path)
    return f'"{text}"' if " " in text or "(" in text else text


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
_LOGS: dict[str, tuple[float, EventLog]] = {}
NOTATION = re.compile(r"^\s*\[?\s*[<⟨]", re.S)


def is_notation(text: str) -> bool:
    return bool(NOTATION.match(text or ""))


def load_log(source: str) -> EventLog:
    """An event log from a path (.xes, .xes.gz, .csv, .txt in course notation,
    .jsonocel flattened on its first object type) or from a log typed in the
    course notation (``[<a,b,c>^3, <a,c>]``).  Files are kept in memory while
    they do not change on disk."""
    if is_notation(source):
        return EventLog.from_simple_log(parse_simple_log(source), "Typed log")
    path = Path(source).expanduser()
    if not path.is_file():
        raise ToolError(f"No such log file: {source}. Give a path to a .xes, .xes.gz, .csv or .txt file, "
                        "or type the log in the course notation, like [<a,b,c>^3, <a,c>].")
    key = str(path.resolve())
    stamp = path.stat().st_mtime
    cached = _LOGS.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    lower = path.name.lower()
    if lower.endswith(".csv"):
        from ..mining.csv_import import read_csv
        log = read_csv(path)
    elif lower.endswith(".txt") or lower.endswith(".log.txt"):
        log = EventLog.from_simple_log(parse_simple_log(path.read_text(encoding="utf-8")), path.stem)
    elif lower.endswith((".jsonocel", ".json")):
        from ..mining.ocel import flatten, read_ocel
        log = flatten(read_ocel(path))
    else:
        from ..mining.xes import read_xes
        log = read_xes(path)
    if len(_LOGS) > 6:                                   # a few large logs are plenty
        _LOGS.pop(next(iter(_LOGS)))
    _LOGS[key] = (stamp, log)
    return log


def _simple(source: str) -> SimpleLog:
    return load_log(source).simple_log()


def _duration(delta) -> str:
    from ..mining.stats import format_duration
    return format_duration(delta) if delta is not None else ""


def log_summary(source: str, top: int = 10) -> dict:
    """What the Overview tab shows: cases, events, activities, variants, the
    time span, and the most frequent variants and activities."""
    from ..mining.stats import summarise
    log = load_log(source)
    s = summarise(log)
    sequences = [(v.sequence, v.count) for v in s.variants]
    return {
        "name": log.name,
        "source": source if is_notation(source) else str(Path(source).expanduser().resolve()),
        "cases": s.case_count, "events": s.event_count, "activities": s.activity_count,
        "variants": s.variant_count,
        "classifier": log.default_classifier().name,
        "start": s.start.isoformat() if s.start else None, "end": s.end.isoformat() if s.end else None,
        "median_case_duration": _duration(s.median_case_duration),
        "mean_case_duration": _duration(s.mean_case_duration),
        "start_activities": sorted({seq[0] for seq, _ in sequences if seq}),
        "end_activities": sorted({seq[-1] for seq, _ in sequences if seq}),
        "top_variants": [{"trace": list(seq), "cases": n} for seq, n in sequences[:top]],
        "top_activities": [{"activity": a.name, "events": a.occurrences, "cases": a.cases}
                           for a in s.activities[:top]],
        "as_notation": format_simple_log(log.simple_log(), max_traces=top),
        "cite": citations("openprocess.mining.log", "openprocess.mining.stats"),
        "reproduce": f"openprocess mine stats {_q(source)}" if not is_notation(source) else
                     f"from openprocess.mining import parse_simple_log, EventLog, summarise\n"
                     f"summarise(EventLog.from_simple_log(parse_simple_log({source!r})))",
    }


def log_variants(source: str, top: int = 50) -> dict:
    """The variants (distinct traces) of a log, most frequent first."""
    from ..mining.stats import summarise
    log = load_log(source)
    s = summarise(log)
    total = s.case_count or 1
    rows, covered = [], 0
    for v in s.variants[:top]:
        covered += v.count
        rows.append({"trace": list(v.sequence), "cases": v.count, "share": round(v.count / total, 4),
                     "cumulative": round(covered / total, 4)})
    return {"variants": s.variant_count, "cases": s.case_count, "listed": len(rows), "rows": rows,
            "notation": format_simple_log(log.simple_log(), max_traces=top)}


def _target(path: str | None, default: Path, overwrite: bool) -> Path:
    target = Path(path).expanduser() if path else default
    if target.exists() and not overwrite:
        raise ToolError(f"{target} exists. Give another path, or overwrite=true to replace it (the old file "
                        "is kept as a backup in the folder's journal).")
    return target


def filter_log(source: str, output: str | None = None, *, start_activities: list[str] | None = None,
               end_activities: list[str] | None = None, activities: list[str] | None = None,
               activity_mode: str = "keep events", min_length: int | None = None, max_length: int | None = None,
               top_variants: int | None = None, variant_coverage: float | None = None,
               time_start: str | None = None, time_end: str | None = None, time_mode: str = "contained",
               overwrite: bool = False, client: str = "") -> dict:
    """Keep part of a log as a new log file (XES), as the log page's Filter… does."""
    from datetime import datetime
    from ..mining.filtering import FilterSettings, apply_filters
    from ..mining.stats import summarise
    from ..mining.xes import xes_string
    log = load_log(source)
    settings = FilterSettings(
        start_activities=set(start_activities) if start_activities else None,
        end_activities=set(end_activities) if end_activities else None,
        activities=(set(activities), activity_mode) if activities else None,
        case_length=(min_length or 0, max_length if max_length is not None else 10**9)
        if (min_length is not None or max_length is not None) else None,
        variant_coverage=variant_coverage, top_variants=top_variants)
    if time_start or time_end:
        settings.time_frame = (datetime.fromisoformat(time_start or "1900-01-01"),
                               datetime.fromisoformat(time_end or "2200-01-01"), time_mode)
    filtered = apply_filters(log, settings)
    base = Path(source).expanduser() if not is_notation(source) else Path.cwd() / "typed log.txt"
    target = _target(output, base.with_name(f"{base.name.split('.')[0]} (filtered).xes"), overwrite)
    change = changes.record(target, xes_string(filtered), "filter_log",
                            "Filtered log: " + "; ".join(settings.describe()), client=client)
    before, after = summarise(log), summarise(filtered)
    return {"file": str(target), "filters": settings.describe(),
            "kept_cases": after.case_count, "of_cases": before.case_count,
            "kept_events": after.event_count, "kept_variants": after.variant_count,
            "change": change.id, "cite": citations("openprocess.mining.filtering"),
            "reproduce": f"openprocess mine filter {_q(source)} -o {_q(target)}  # with the same options"}


def dotted_chart(source: str, output: str | None = None, *, x: str = "actual", rows: str = "Case",
                 colour: str = "Activity", sort: str = "first", unit: str = "Auto", width: int = 900,
                 overwrite: bool = False, client: str = "") -> dict:
    """The dotted chart (Song & van der Aalst 2007) as an SVG file: every
    event a dot, x by time (``actual``, ``since start``, ``relative``, ``order in
    log``, ``position in case``), one row per case (or resource, activity…),
    coloured by an attribute."""
    from ..mining.dotted import Dots, DotSettings, how_to_read, svg
    log = load_log(source)
    modes = {"actual": 0, "since start": 1, "relative": 2, "order in log": 3, "position in case": 4}
    sorts = {"first": 0, "last": 1, "duration": 2, "events": 3, "name": 4}
    settings = DotSettings(x_mode=modes.get(x.lower(), 0), rows=rows, colour_by=colour,
                           sort=sorts.get(sort.lower(), 0), unit=unit)
    dots = Dots(log, log.default_classifier(), settings)
    text = svg(dots, f"Dotted chart of {log.name}", unit, width)
    base = Path(source).expanduser() if not is_notation(source) else Path.cwd() / "typed log.txt"
    target = _target(output, base.with_name(f"{base.name.split('.')[0]} dotted chart.svg"), overwrite)
    change = changes.record(target, text, "dotted_chart", f"Dotted chart ({x}, rows {rows}, colour {colour})",
                            client=client)
    return {"file": str(target), "dots": len(dots), "rows": len(dots.row_values),
            "how_to_read": how_to_read(settings, settings.x_mode), "change": change.id,
            "cite": citations("openprocess.mining.dotted"),
            "reproduce": "the Dotted chart box of a workflow with the same settings, or the log page's "
                         "Dotted chart tab in OpenProcess"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def load_net(path: str) -> PetriNet:
    """A Petri net from a PNML file (or a BPMN file, converted; or a .cpnflow's
    first net result)."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise ToolError(f"No such model file: {path}")
    lower = target.name.lower()
    if lower.endswith(".bpmn"):
        try:
            from ..mining.bpmn import bpmn_to_petri_net, read_bpmn
        except ImportError:
            raise ToolError("BPMN support is not in this build") from None
        return bpmn_to_petri_net(read_bpmn(target))
    if lower.endswith(".pnml"):
        from ..mining.pnml import read_pnml
        return read_pnml(target)
    raise ToolError(f"{path} is not a .pnml (or .bpmn) file")


def _net_dict(net: PetriNet) -> dict:
    return {
        "name": net.name, "summary": net.summary(),
        "places": [{"id": p.id, "name": p.name, "tokens": net.initial_marking[p.id]} for p in net.places.values()],
        "transitions": [{"id": t.id, "label": None if t.silent else t.label, "name": t.name, "silent": t.silent}
                        for t in net.transitions.values()],
        "arcs": [{"from": a.source, "to": a.target, "weight": a.weight} for a in net.arcs],
        "initial_marking": net.initial_marking.describe(net), "final_marking": net.final_marking.describe(net),
        "info": dict(net.info),
    }


def _soundness_dict(net: PetriNet, max_states: int = 100_000) -> dict:
    from ..mining.analysis import check_soundness
    report = check_soundness(net, max_states=max_states)
    counterexamples = []
    for finding, path in zip(report.findings, report.paths):
        counterexamples.append({"finding": finding,
                                "firing_sequence": [net.node_name(t) for t in path] if path else None})
    return {"is_workflow_net": report.workflow.is_workflow_net, "sound": report.sound,
            "option_to_complete": report.option_to_complete, "proper_completion": report.proper_completion,
            "no_dead_transitions": report.no_dead_transitions, "bounded": report.bounded,
            "dead_transitions": [net.node_name(t) for t in report.dead_transitions],
            "findings": counterexamples,
            "reachable_markings": len(report.graph.states) if report.graph is not None else None}


def _quick_fit(net: PetriNet, simple: SimpleLog) -> dict:
    from ..mining.conformance.token_replay import token_replay
    replay = token_replay(net, simple)
    return {"fitness_token_replay": round(replay.fitness, 4), "fitting_cases": replay.fitting_traces,
            "cases": replay.trace_count}


def discover(source: str, algorithm: str = "inductive", *, noise: float = 0.0, dependency: float = 0.9,
             representation: str = "set", horizon: int = 1, output: str | None = None, overwrite: bool = False,
             draw: bool = False, client: str = "") -> dict:
    """Discover a model from a log with one of the course's algorithms, save it
    as PNML beside the log, and return the model, its derivation (how), a
    soundness check, a quick fitness, the sources and how to reproduce it."""
    from ..flow.explain import collect_result, explaining
    from ..mining.pnml import pnml_string
    name = ALGORITHMS.get(algorithm.strip().lower())
    if name is None:
        raise ToolError(f"Unknown algorithm {algorithm!r}: use alpha, inductive (noise 0-1 for IMf), "
                        "heuristics, dfg or regions.")
    log = load_log(source)
    simple = log.simple_log()
    module = _ALGORITHM_MODULES[name]
    from ..flow.boxes import discover as boxes
    with explaining() as explanation:
        # The box the app runs, so `how` is what its How tab shows; the
        # engine's own result below adds the data an assistant reports.
        if name == "α-algorithm":
            from ..mining.discovery.alpha import alpha_miner
            net = boxes.alpha_miner.function(log)
            result = alpha_miner(simple)
            extra = {"X_L": [[sorted(a), sorted(b)] for a, b in result.X_L][:200],
                     "Y_L": [[sorted(a), sorted(b)] for a, b in result.Y_L],
                     "T_I": sorted(result.T_I), "T_O": sorted(result.T_O), "warnings": list(result.warnings)}
            box_id = "alpha_miner"
            code = ("from openprocess.mining import alpha_miner\n"
                    "net = alpha_miner(log.simple_log()).net")
            command = f"openprocess mine discover {_q(source)} --algorithm alpha -o {{output}}"
        elif name == "Inductive Miner":
            from ..mining.discovery.inductive import inductive_miner
            net = boxes.inductive_miner.function(log, noise=noise)
            result = inductive_miner(simple, noise_threshold=noise)
            extra = {"process_tree": str(result.tree), "noise": noise, "steps": list(result.trace_of_steps)}
            box_id = "inductive_miner"
            code = ("from openprocess.mining import inductive_miner\n"
                    f"result = inductive_miner(log.simple_log(), noise_threshold={noise})   # result.tree, result.net")
            command = f"openprocess mine discover {_q(source)} --algorithm inductive --noise {noise} -o {{output}}"
        elif name == "Heuristics Miner":
            from ..mining.discovery.heuristics import heuristics_net
            net = boxes.heuristics_miner.function(log, dependency=dependency)
            result = heuristics_net(simple, dependency_threshold=dependency)
            extra = {"dependency_threshold": dependency,
                     "dependency_graph": [{"from": a, "to": b, "dependency": round(v, 3), "count": n}
                                          for (a, b), (v, n) in sorted(result.graph.edges.items())]}
            box_id = "heuristics_miner"
            code = ("from openprocess.mining.discovery.heuristics import heuristics_net\n"
                    f"net = heuristics_net(log.simple_log(), dependency_threshold={dependency}).net")
            command = (f"openprocess mine discover {_q(source)} --algorithm heuristics --dependency {dependency} "
                       "-o {output}")
        elif name == "Directly-follows graph":
            dfg = boxes.directly_follows.function(log)
            out = {"algorithm": name, "activities": dict(dfg.activities.most_common()),
                   "edges": [{"from": a, "to": b, "count": n}
                             for a, b, n in sorted(dfg.all_edges(), key=lambda e: -e[2])],
                   "start": dict(dfg.start), "end": dict(dfg.end), "how": to_markdown(explanation),
                   "cite": citations(module),
                   "reproduce": {"python": "from openprocess.mining import discover_dfg\ndfg = discover_dfg(log)",
                                 "box": "directly_follows"}}
            if draw:
                from ..mining.drawing import svg_of
                out["svg"] = svg_of(dfg)
            return out
        else:
            horizon_text = "all" if not horizon else str(horizon)
            ts = boxes.classical_states.function(log, "prefix", representation,
                                                 horizon_text if horizon_text in ("1", "2", "3") else "all")
            net = boxes.regions_to_net.function(ts)
            from ..mining.discovery.state_regions import region_miner
            result = region_miner(simple, "prefix", representation, horizon or None)
            extra = {"warnings": list(result.warnings), "representation": representation, "horizon": horizon_text}
            box_id = "classical_states + regions_to_net"
            code = ("from openprocess.mining import region_miner\n"
                    f"net = region_miner(log.simple_log(), 'prefix', {representation!r}, {horizon or None}).net")
            command = "(no CLI form; build a workflow with the Classical states and Regions to net boxes)"
    base = Path(source).expanduser() if not is_notation(source) else Path.cwd() / "typed log.txt"
    short = {"α-algorithm": "alpha", "Inductive Miner": "IMf" if noise > 0 else "IM",
             "Heuristics Miner": "HM", "State-based regions": "regions"}[name]
    target = _target(output, base.with_name(f"{base.name.split('.')[0]} ({short}).pnml"), overwrite)
    net.info.setdefault("log", str(base))
    change = changes.record(target, pnml_string(net), "discover", f"{name} on {log.name}", client=client)
    out = {"algorithm": name, "file": str(target), "model": _net_dict(net), **extra,
           "how": to_markdown(explanation), "soundness": _soundness_dict(net), **_quick_fit(net, simple),
           "change": change.id, "cite": citations(module, "openprocess.mining.petrinet"),
           "reproduce": {"python": code, "command": command.format(output=_q(target)), "box": box_id,
                         "note": "The same log and settings give the same model: the algorithms are "
                                 "deterministic. For a record that can be checked later, build a workflow "
                                 "(build_workflow) and keep the .cpnflow with its results."}}
    if draw:
        from ..mining.drawing import svg_of
        out["svg"] = svg_of(net)
    return out


def conformance(log_source: str, model: str, method: str = "token replay", top: int = 20) -> dict:
    """Fitness (token replay or alignments), precision (ETC), generalisation and
    simplicity of a model against a log, with the replay or alignment per
    variant."""
    from ..flow.explain import explaining
    from ..mining.conformance.alignments import align_log
    from ..mining.conformance.quality import generalisation, precision, simplicity
    from ..mining.conformance.token_replay import token_replay
    net = load_net(model)
    log = load_log(log_source)
    simple = log.simple_log()
    with explaining() as explanation:
        replay = token_replay(net, simple)
    per_variant = [{"trace": list(t.trace), "cases": t.count, "produced": t.produced, "consumed": t.consumed,
                    "missing": t.missing, "remaining": t.remaining,
                    "fitness": round(0.5 * (1 - t.missing / t.consumed if t.consumed else 1)
                                     + 0.5 * (1 - t.remaining / t.produced if t.produced else 1), 4)}
                   for t in replay.traces[:top]]
    out = {"model": net.name, "log": log.name, "method": method,
           "token_replay": {"fitness": round(replay.fitness, 4), "produced": replay.produced,
                            "consumed": replay.consumed, "missing": replay.missing, "remaining": replay.remaining,
                            "fitting_cases": replay.fitting_traces, "cases": replay.trace_count,
                            "per_variant": per_variant},
           "precision": round(precision(net, simple), 4), "generalisation": round(generalisation(replay), 4),
           "simplicity": round(simplicity(net), 4), "notes": [],
           "cite": citations("openprocess.mining.conformance.token_replay",
                             "openprocess.mining.conformance.quality"),
           "reproduce": {"command": f"openprocess mine conform {_q(log_source)} {_q(model)}",
                         "python": "from openprocess.mining import read_pnml, token_replay, align_log, precision\n"
                                   f"net = read_pnml({str(model)!r}); replay = token_replay(net, log)"}}
    if method.lower().startswith("align"):
        alignments = align_log(net, simple)
        out["alignments"] = {"fitness": round(alignments.average_fitness, 4),
                             "fitting_cases": alignments.fitting_traces, "cases": alignments.trace_count,
                             "per_variant": [{"trace": list(a.trace), "cases": a.count, "cost": a.cost,
                                              "fitness": round(a.fitness, 4),
                                              "moves": [[m.log, m.model] for m in a.moves]}
                                             for a in alignments.alignments[:top]]}
        out["notes"] += list(alignments.notes)
        out["cite"] = citations("openprocess.mining.conformance.alignments",
                                "openprocess.mining.conformance.token_replay",
                                "openprocess.mining.conformance.quality")
    out["how"] = to_markdown(explanation)
    return out


def analyse_net(model: str, max_states: int = 100_000) -> dict:
    """Everything the Analysis tab says about a net: WF-net, soundness with
    counterexamples, the short-circuited net, behavioural properties,
    structure (free-choice, well-structured, S-coverable), invariants, and
    the footprint."""
    from ..mining.analysis import analyse, check_soundness, check_workflow_net
    from ..mining.footprint import footprint_of_net
    from ..mining.invariants import invariants
    from ..mining.structure import check_structure
    net = load_net(model)
    workflow = check_workflow_net(net)
    soundness = check_soundness(net, max_states=max_states)
    out: dict[str, Any] = {"model": _net_dict(net), "workflow_net": {
        "is_workflow_net": workflow.is_workflow_net, "source": workflow.source, "sink": workflow.sink,
        "problems": list(getattr(workflow, "problems", []) or [])},
        "soundness": _soundness_dict(net, max_states)}
    try:
        properties = analyse(net, max_states=max_states)
        out["properties"] = [{"property": name, "holds": verdict, "detail": detail}
                             for name, verdict, detail in properties.lines()]
    except Exception as error:  # noqa: BLE001 - a net with no marking, say
        out["properties"] = [{"property": "error", "holds": None, "detail": str(error)}]
    if workflow.is_workflow_net:
        if soundness.short_circuit is not None:
            sc = soundness.short_circuit
            out["short_circuited"] = {"live": sc.live, "bounded": sc.bounded, "safe": sc.safe,
                                      "deadlock_free": sc.deadlock_free, "sound_by_theorem": sc.sound}
        try:
            structure = check_structure(net, workflow.source, workflow.sink)
            out["structure"] = {
                "free_choice": structure.is_free_choice,
                "free_choice_violations": [list(v) for v in structure.free_choice][:20],
                "well_structured": structure.well_structured,
                "handles": [{"kind": h.kind, "from": net.node_name(h.start), "to": net.node_name(h.end),
                             "paths": [[net.node_name(n) for n in p] for p in h.paths]}
                            for h in (structure.handles or [])][:20],
                "s_coverable": structure.coverage.s_coverable if structure.coverage else None}
        except Exception as error:  # noqa: BLE001
            out["structure"] = {"error": str(error)}
    try:
        found = invariants(net)
        out["invariants"] = {
            "p_invariants": [found.describe(i, with_value=True) for i in found.p_invariants],
            "covered_by_p_invariants": found.covered_by_p_invariants,
            "t_invariants": [found.describe(i) for i in found.t_invariants],
            "covered_by_t_invariants": found.covered_by_t_invariants,
            "incidence": {"places": [net.node_name(p) for p in found.places],
                          "transitions": [net.node_name(t) for t in found.transitions],
                          "matrix": found.incidence}}
    except Exception as error:  # noqa: BLE001
        out["invariants"] = {"error": str(error)}
    try:
        fp = footprint_of_net(net)
        out["footprint"] = {"activities": fp.activities, "matrix": fp.matrix()}
    except Exception as error:  # noqa: BLE001
        out["footprint"] = {"error": str(error)}
    out["definitions"] = "openprocess://guide/definitions (every property, defined mathematically)"
    out["cite"] = citations("openprocess.mining.analysis", "openprocess.mining.structure",
                            "openprocess.mining.invariants")
    out["reproduce"] = {"command": f"openprocess mine soundness {_q(model)}; openprocess mine invariants {_q(model)}",
                        "python": f"from openprocess.mining import read_pnml, check_soundness, analyse\n"
                                  f"net = read_pnml({str(model)!r}); check_soundness(net); analyse(net)"}
    return out


def compare_models(first: str, second: str, max_length: int = 12) -> dict:
    """Do two nets allow the same complete traces? With the shortest traces
    that tell them apart."""
    from ..mining.compare_nets import compare_nets
    a, b = load_net(first), load_net(second)
    result = compare_nets(a, b, max_length=max_length)
    return {"equivalent": result.equivalent, "exact": result.exact, "max_length": result.max_length,
            "only_in_first": [list(t) for t in result.only_first[:20]],
            "only_in_second": [list(t) for t in result.only_second[:20]],
            "labels_only_first": result.labels_only_first, "labels_only_second": result.labels_only_second,
            "notes": result.notes, "cite": citations("openprocess.mining.compare_nets"),
            "reproduce": f"from openprocess.mining import read_pnml, compare_nets\n"
                         f"compare_nets(read_pnml({first!r}), read_pnml({second!r}))"}


def make_net(spec: dict, output: str, *, overwrite: bool = False, client: str = "") -> dict:
    """Write a Petri net described as JSON to a PNML file and analyse it.

    ``spec``: ``{"name": …, "places": [{"id", "name"?, "tokens"?}], "transitions":
    [{"id", "label"? (null or omitted with "silent": true for τ)}], "arcs":
    [["from", "to"] or ["from", "to", weight]], "initial": {"place": tokens}?,
    "final": {"place": tokens}?}``.  Without markings a WF-net gets [i] and [o]."""
    from ..mining.pnml import pnml_string
    net = PetriNet(str(spec.get("name") or "Net"))
    ids: set[str] = set()
    for item in spec.get("places") or []:
        item = {"id": item} if isinstance(item, str) else dict(item)
        place_id = str(item.get("id") or item.get("name"))
        if not place_id or place_id in ids:
            raise ToolError(f"every place needs a unique id (problem: {item})")
        ids.add(place_id)
        net.add_place(str(item.get("name") or place_id), id=place_id)
        if item.get("tokens"):
            net.initial_marking = net.initial_marking + Marking({place_id: int(item["tokens"])})
    for item in spec.get("transitions") or []:
        item = {"id": item} if isinstance(item, str) else dict(item)
        transition_id = str(item.get("id") or item.get("label") or item.get("name"))
        if not transition_id or transition_id in ids:
            raise ToolError(f"every transition needs a unique id (problem: {item})")
        ids.add(transition_id)
        silent = bool(item.get("silent")) or ("label" in item and item["label"] in (None, "", "τ", "tau"))
        label = None if silent else str(item.get("label") or item.get("name") or transition_id)
        net.add_transition(label, name=str(item.get("name") or label or transition_id), id=transition_id)
    for arc in spec.get("arcs") or []:
        if isinstance(arc, dict):
            source, target, weight = arc.get("from"), arc.get("to"), int(arc.get("weight", 1))
        else:
            source, target = arc[0], arc[1]
            weight = int(arc[2]) if len(arc) > 2 else 1
        if source not in ids or target not in ids:
            raise ToolError(f"an arc names an unknown node: {source} → {target}")
        if (source in net.places) == (target in net.places):
            raise ToolError(f"an arc must join a place and a transition: {source} → {target}")
        net.add_arc(source, target, weight)
    if spec.get("initial"):
        net.initial_marking = Marking({str(k): int(v) for k, v in dict(spec["initial"]).items()})
    if spec.get("final"):
        net.final_marking = Marking({str(k): int(v) for k, v in dict(spec["final"]).items()})
    from ..mining.analysis import check_workflow_net
    workflow = check_workflow_net(net)
    if workflow.is_workflow_net:
        if not net.initial_marking:
            net.initial_marking = Marking({workflow.source: 1})
        if not net.final_marking:
            net.final_marking = Marking({workflow.sink: 1})
    for place_id in list(net.initial_marking) + list(net.final_marking):
        if place_id not in net.places:
            raise ToolError(f"the marking names a place that is not in the net: {place_id}")
    target = _target(output, Path(output).expanduser(), overwrite)
    change = changes.record(target, pnml_string(net), "make_net", f"Net {net.name} drawn by an assistant",
                            client=client)
    return {"file": str(target), "model": _net_dict(net), "soundness": _soundness_dict(net), "change": change.id,
            "svg": None, "cite": citations("openprocess.mining.petrinet", "openprocess.mining.analysis"),
            "reproduce": f"openprocess mine soundness {_q(target)}"}


def draw(what: str, *, fmt: str = "svg", output: str | None = None, kind: str = "auto", mode: str = "frequency",
         overwrite: bool = False, client: str = "") -> dict:
    """A diagram of a model file (.pnml, .bpmn), a log (its process map:
    kind ``dfg``; or the Inductive Miner's tree: kind ``tree``) or a transition
    system (.txt), as SVG text or PNG bytes; saved when ``output`` is given."""
    from ..mining.drawing import png_of, svg_of
    lower = what.lower()
    title = Path(what).name if not is_notation(what) else "typed log"
    if kind == "auto":
        kind = "net" if lower.endswith(".pnml") else "bpmn" if lower.endswith(".bpmn") else \
            "ts" if lower.endswith("ts.txt") else "dfg"
    if kind == "net":
        value = load_net(what) if lower.endswith((".pnml", ".bpmn")) else None
        if value is None:
            raise ToolError("kind=net needs a .pnml or .bpmn file")
        svg = svg_of(value)
    elif kind == "bpmn" and lower.endswith(".bpmn"):
        from ..mining.bpmn import read_bpmn
        from ..mining.drawing import svg_of_drawing
        svg = svg_of_drawing(_bpmn_drawing(read_bpmn(what)))
    elif kind == "dfg":
        from ..mining.dfg import discover_dfg
        svg = svg_of(discover_dfg(load_log(what)), mode=mode)
    elif kind == "tree":
        from ..mining.discovery.inductive import inductive_miner
        svg = svg_of(inductive_miner(_simple(what)).tree)
    elif kind == "ts":
        from ..mining.transition_system import parse_transition_system
        svg = svg_of(parse_transition_system(Path(what).read_text(encoding="utf-8")))
    else:
        raise ToolError(f"Unknown kind {kind!r}: use net, dfg, tree, ts or bpmn")
    out: dict[str, Any] = {"kind": kind, "title": title, "format": fmt}
    if fmt.lower() == "png":
        try:
            out["png"] = png_of(svg)
        except RuntimeError as error:
            out["format"] = "svg"
            out["svg"] = svg
            out["note"] = f"{error}: here is the SVG instead."
    else:
        out["svg"] = svg
    if output:
        target = _target(output, Path(output).expanduser(), overwrite)
        content = out.get("png") if "png" in out else svg
        if "png" not in out and target.suffix.lower() == ".png":
            target = target.with_suffix(".svg")
        change = changes.record(target, content, "draw", f"Diagram of {title} ({kind})", client=client)
        out["file"], out["change"] = str(target), change.id
    return out


def _bpmn_drawing(model):
    from ..mining.drawing import Drawing, Edge, Node
    drawing = Drawing(title=getattr(model, "name", "BPMN"))
    for node in model.nodes.values():
        kind = {"start": "start", "end": "end", "task": "activity", "exclusive": "operator",
                "parallel": "operator"}.get(node.kind, "activity")
        text = {"exclusive": "×", "parallel": "+"}.get(node.kind, node.name or "")
        drawing.nodes.append(Node(node.id, kind, text=text))
    for flow in model.flows:
        drawing.edges.append(Edge(flow.source, flow.target, text=getattr(flow, "name", "") or ""))
    return drawing


def convert_model(source: str, output: str, *, overwrite: bool = False, client: str = "") -> dict:
    """Convert a model between PNML and BPMN 2.0 (by the files' extensions):
    a sound WF-net to BPMN (exact for free-choice nets), a BPMN model to its
    Petri net semantics (Dijkman, Dumas & Ouyang 2008).  The BPMN file opens
    in Camunda Modeler or bpmn.io as well as in OpenProcess."""
    from ..mining import bpmn as bpmn_module
    from ..mining.pnml import pnml_string
    src, dst = Path(source).expanduser(), Path(output).expanduser()
    target = _target(output, dst, overwrite)
    if src.suffix.lower() == ".pnml" and dst.suffix.lower() == ".bpmn":
        try:
            model = bpmn_module.petri_net_to_bpmn(load_net(str(src)))
        except ValueError as error:
            raise ToolError(f"This net has no BPMN form: {error}") from None
        content, summary = bpmn_module.bpmn_string(model), model.summary()
        reverse = "petri_net_to_bpmn"
    elif src.suffix.lower() == ".bpmn" and dst.suffix.lower() == ".pnml":
        model = bpmn_module.read_bpmn(src)
        net = bpmn_module.bpmn_to_petri_net(model)
        content, summary = pnml_string(net), net.summary()
        reverse = "bpmn_to_petri_net"
    else:
        raise ToolError("convert_model goes from .pnml to .bpmn or from .bpmn to .pnml")
    change = changes.record(target, content, "convert_model", f"{src.name} as {dst.suffix[1:].upper()}",
                            client=client)
    out = {"file": str(target), "summary": summary, "warnings": list(getattr(model, "warnings", []) or []),
           "change": change.id, "cite": citations("openprocess.mining.bpmn"),
           "reproduce": f"from openprocess.mining.bpmn import {reverse}"}
    if reverse == "bpmn_to_petri_net":
        out["soundness"] = _soundness_dict(net)
    return out


# ---------------------------------------------------------------------------
# Workflows: the reproducible way
# ---------------------------------------------------------------------------
def list_boxes(folder: str | None = None, group: str | None = None) -> dict:
    """Every box an analysis can use (OpenProcess's own, installed packages',
    and the folder's ``boxes/``), with inputs, outputs and settings."""
    from ..flow.library import library_for
    library = library_for(folder)
    out = []
    for name, specs in library.by_group().items():
        if group and name.lower() != group.lower():
            continue
        for spec in specs:
            out.append({
                "id": spec.id.rsplit(".", 1)[-1] if not spec.custom else spec.id, "full_id": spec.id,
                "name": spec.name, "group": name, "help": spec.help.strip().split("\n\n")[0],
                "inputs": [{"name": p.name, "type": p.type_name, "many": p.many, "optional": p.optional}
                           for p in spec.inputs],
                "outputs": [{"name": p.name, "type": p.type_name} for p in spec.outputs],
                "settings": [{"name": s.name, "kind": s.kind, "default": s.default,
                              "choices": list(s.choices) if s.choices else None, "help": s.help}
                             for s in spec.settings],
                "available": spec.available, "needs": list(spec.needs), "custom": spec.custom,
                "file": spec.file or spec.module})
    broken = [{"file": b.file, "reason": b.reason} for b in library.broken]
    return {"boxes": out, "count": len(out), "broken": broken,
            "note": "Connect boxes by type: an output of type Log fits an input of type Log. "
                    "Ids are what build_workflow takes (a custom box by its full_id)."}


def box_info(box_id: str, folder: str | None = None) -> dict:
    """One box in full: its description, settings, the code it runs, the
    algorithms it calls and the works they follow."""
    from ..flow.box import algorithm_calls
    from ..flow.derivation import sources_of
    from ..flow.library import library_for
    library = library_for(folder)
    spec = None
    for candidate in library:
        if candidate.id == box_id or candidate.id.rsplit(".", 1)[-1] == box_id or candidate.name == box_id:
            spec = candidate
            break
    if spec is None:
        raise ToolError(f"No box called {box_id!r}; list_boxes gives the ids.")
    called = []
    try:
        called = [{"function": c.name, "module": c.module, "where": c.where()} for c in algorithm_calls(spec)]
    except Exception:  # noqa: BLE001
        pass
    return {"id": spec.id, "name": spec.name, "group": spec.group, "description": spec.describe(),
            "help": spec.help, "source": spec.source, "file": spec.file or spec.module, "line": spec.line,
            "calls": called, "cite": [{"key": r.key, "citation": r.citation.replace("*", ""), "url": r.url}
                                      for r in sources_of(spec)]}


def _library(folder):
    from ..flow.library import library_for
    return library_for(folder)


def _result_summary(workflow, run, node, how: bool) -> dict:
    result = run.result(node)
    if result is None:
        return {"status": "not run"}
    out: dict[str, Any] = {"status": result.status, "box": workflow.title(node), "seconds": round(result.duration, 3)}
    if result.status == "done":
        out["result"] = value_markdown(result.value)
        from ..flow.record import fingerprint
        try:
            out["fingerprint"] = fingerprint(result.value)[:16]
        except Exception:  # noqa: BLE001
            pass
        if how and not result.explanation.empty:
            out["how"] = to_markdown(result.explanation)
    elif result.error or result.message:
        out["problem"] = result.error or result.message
    return out


def build_workflow(spec: dict, output: str, *, folder: str | None = None, run: bool = True,
                   how: bool = True, overwrite: bool = False, client: str = "") -> dict:
    """Build an analysis from a description, save it as a ``.cpnflow`` and (by
    default) run it and keep the record that makes it reproducible.

    ``spec``: ``{"name": "…", "boxes": [{"id": "n1", "box": "open_log", "settings": {"file":
    "log.xes"}, "title": "…"?}, …], "connections": [{"from": "n1", "to": "n2", "input":
    "log"?, "output": "out"?}, …]}``.  Box ids come from list_boxes; an input
    name may be left out when the type decides it.  Files in settings are
    relative to the workflow's folder."""
    from ..flow.record import save
    from ..flow.runner import Runner
    from ..flow.workflow import Workflow, WorkflowError
    target = _target(output, Path(output).expanduser(), overwrite)
    base = Path(folder).expanduser() if folder else target.parent
    library = _library(base)
    wf = Workflow(str(spec.get("name") or target.stem), library)
    x = 0.0
    try:
        for item in spec.get("boxes") or []:
            node = wf.add(item["box"], dict(item.get("settings") or {}), (x, 0.0), id=item.get("id"),
                          title=str(item.get("title") or ""))
            x += 220
            item["_node"] = node.id
        ids = {item.get("id") or item["_node"]: item["_node"] for item in spec.get("boxes") or []}
        for link in spec.get("connections") or []:
            wf.connect(ids.get(link["from"], link["from"]), ids.get(link["to"], link["to"]),
                       link.get("input"), link.get("output", "out"))
    except (WorkflowError, KeyError, ValueError) as error:
        raise ToolError(f"The workflow could not be built: {error}") from None
    problems = wf.validate()
    run_object = None
    results = {}
    if run and not problems:
        run_object = Runner(library, folder=base).run(wf)
        results = {node.id: _result_summary(wf, run_object, node, how) for node in wf.order()}
    with changes.recording(target, "build_workflow", f"Analysis {wf.name}" + (" (run and recorded)" if run_object else ""),
                           client=client) as change:
        save(wf, target, run_object, folder=base)
    return {"file": str(target), "name": wf.name, "boxes": [{"id": n.id, "box": n.box, "title": wf.title(n),
                                                             "settings": n.settings} for n in wf.order()],
            "problems": problems, "ran": run_object is not None, "results": results, "change": change.id,
            "cite": citations(*{wf.spec(n).module for n in wf.nodes.values()}),
            "reproduce": {"command": f"openprocess run {_q(target)} --check",
                          "note": "The file holds the boxes, settings, connections and a record (input hashes, "
                                  "seeds, versions, result fingerprints); --check re-runs it and compares. "
                                  "Open it in OpenProcess to see every box's result, How and Code."}}


def run_workflow(path: str, *, check: bool = False, how: bool = False, report: str | None = None,
                 client: str = "") -> dict:
    """Run a saved ``.cpnflow`` (headless), optionally checking every result
    against the file's record, and optionally writing the HTML report."""
    from ..flow.record import check as check_record, differences, load
    from ..flow.runner import Runner
    target = Path(path).expanduser()
    if not target.is_file():
        raise ToolError(f"No such workflow: {path}")
    library = _library(target.parent)
    wf, record = load(target, library)
    notes = list(differences(record, wf, target.parent))
    problems = wf.validate()
    if problems:
        return {"file": str(target), "problems": problems, "notes": notes}
    run = Runner(library, folder=target.parent).run(wf)
    out: dict[str, Any] = {"file": str(target), "name": wf.name, "notes": notes,
                           "results": {n.id: _result_summary(wf, run, n, how) for n in wf.order()},
                           "ok": run.ok, "cite": citations(*{wf.spec(n).module for n in wf.nodes.values()})}
    if check:
        if not record.results:
            out["check"] = {"status": "no record", "message": "The file has no recorded results to check against "
                                                               "(run it in OpenProcess or with --save once)."}
        else:
            differing = check_record(wf, record, run, complete=True)
            out["check"] = {"status": "reproduced" if not differing else "differs", "differences": differing}
    if report:
        from ..flow.report import write_report
        rpath = _target(report, Path(report).expanduser(), True)
        with changes.recording(rpath, "run_workflow", f"Report of {wf.name}", client=client):
            write_report(rpath, workflow=wf, run=run, folder=target.parent)
        out["report"] = str(rpath)
    out["reproduce"] = f"openprocess run {_q(target)}" + (" --check" if check else "")
    return out


def write_box(folder: str, name: str, source: str, *, overwrite: bool = False, client: str = "") -> dict:
    """Add a box of your own to a folder: a Python file with a ``@box`` function
    in ``<folder>/boxes/<name>.py``.  The file is loaded first, so a mistake
    (a setting without a default, an unknown type) is reported instead of a
    broken box.  Every analysis that uses it records the file's hash."""
    import tempfile
    from ..flow.library import Library
    if not name.isidentifier():
        raise ToolError("name must be a Python identifier (it becomes the file's name)")
    if "@box" not in source:
        raise ToolError("the source must define a function decorated with @box (from openprocess.flow import box)")
    with tempfile.TemporaryDirectory() as temporary:
        trial = Path(temporary) / f"{name}.py"
        trial.write_text(source, encoding="utf-8")
        library = Library()
        specs = library.load_file(trial)
        if library.broken:
            raise ToolError("The box does not load: " + "; ".join(b.reason for b in library.broken))
    target = _target(None, Path(folder).expanduser() / "boxes" / f"{name}.py", overwrite)
    change = changes.record(target, source, "write_box", f"Box {name}: " + ", ".join(s.name for s in specs),
                            client=client, folder=changes.folder_for(Path(folder).expanduser() / "x"))
    return {"file": str(target), "boxes": [s.describe() for s in specs], "change": change.id,
            "note": "OpenProcess lists it under Yours (+ Add box) as soon as the file is saved; the app asks "
                    "once before running a folder's own boxes."}


# ---------------------------------------------------------------------------
# Learn
# ---------------------------------------------------------------------------
def list_computes() -> dict:
    """Every ``compute:`` an exercise's answer block may use, with what it works out."""
    from ..learn.computed import describe_all
    rows = describe_all()
    return {"computes": [{"name": n, "of": of or "block", "help": h} for n, of, h in rows], "count": len(rows)}


def validate_pack(folder: str, answers: bool = True) -> dict:
    """Check an exercise pack as ``openprocess exercises check --answers`` does:
    every exercise and answer block, every computed answer, every problem."""
    from ..learn.checks import Context, TaskError, computed_answer_text, validate
    from ..learn.pack import load_pack
    root = Path(folder).expanduser()
    pack = load_pack(root)
    if not pack.exercises:
        raise ToolError(f"No exercises in {folder}: an exercise is a folder with a question.md")
    exercises, total = [], 0
    for exercise in pack.exercises:
        problems = validate(exercise)
        context = Context(exercise)
        blocks = []
        for task in exercise.sheet.tasks:
            row = {"id": task.id, "type": task.type, "checked": task.checkable, "points": task.points,
                   "compute": task.get("compute"), "line": task.line}
            if answers and task.type not in ("net", "footprint", "trace", "open"):
                try:
                    row["answer"] = " ".join(computed_answer_text(exercise, task, context).split())[:300]
                except TaskError as error:
                    row["answer"] = f"(cannot work it out: {error})"
            if task.solution:
                row["solution"] = " ".join(task.solution.split())[:200]
            blocks.append(row)
        exercises.append({"folder": str(exercise.folder), "title": exercise.title,
                          "chapter": pack.chapter(exercise), "points": exercise.points, "blocks": blocks,
                          "problems": problems})
        total += len(problems)
    return {"pack": pack.title, "folder": str(root), "exam": pack.is_exam, "points": pack.points,
            "exercises": exercises, "problems": total,
            "reproduce": f"openprocess exercises check {_q(root)} --answers"}


def compute_answer(exercise: str, expression: str, **keys: str) -> dict:
    """Work out one ``compute:`` expression for an exercise folder (``alpha.Y_L``,
    ``sound``, ``im.tree``, ``fire(a, [i])``…), as the app would check it."""
    from ..learn.checks import Context, show_value
    from ..learn.computed import compute
    from ..learn.pack import Exercise
    from ..learn.sheet import Task
    found = Exercise.at(Path(exercise).expanduser())
    if found is None:
        raise ToolError(f"{exercise} is not an exercise folder (no question.md)")
    task = Task("set", "q", settings={"compute": expression, **{k: v for k, v in keys.items() if v}})
    value = compute(Context(found), task, expression)
    return {"expression": expression, "answer": show_value(value), "raw": _plain(value)}


def _plain(value):
    if isinstance(value, (frozenset, set)):
        return sorted(_plain(v) for v in value) if all(isinstance(v, (str, int, float)) for v in value) \
            else [_plain(v) for v in value]
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def exercise_guide() -> str:
    from ..learn import __file__ as learn
    return (Path(learn).parent / "exercise-packs.md").read_text(encoding="utf-8")


def write_exercise(pack: str, name: str, question: str, files: dict[str, str] | None = None, *,
                   pack_title: str | None = None, overwrite: bool = False, client: str = "") -> dict:
    """Write one exercise into a pack: ``<pack>/<name>/question.md`` plus the
    files it gives (``log.txt`` in course notation, ``net.pnml``, ``ts.txt``,
    ``answer.pnml``, pictures as text are not supported), then validate it.
    A missing ``pack.md`` is written with ``pack_title``."""
    from ..learn.checks import validate
    from ..learn.pack import Exercise
    from ..learn.sheet import SheetError, parse_sheet
    root = Path(pack).expanduser()
    try:
        sheet = parse_sheet(question, name)
    except SheetError as error:
        raise ToolError(f"question.md has a mistake: {error}") from None
    if not sheet.tasks:
        raise ToolError("question.md has no ```answer block; see the guide (openprocess://guide/exercise-packs)")
    folder = root / name
    journal_root = changes.folder_for(root / "pack.md")
    written = []
    if not (root / "pack.md").exists():
        title = pack_title or root.name
        written.append(changes.record(root / "pack.md", f"# {title}\n", "write_exercise", f"Pack {title}",
                                      client=client, folder=journal_root).path)
    for file_name, content in (files or {}).items():
        safe = Path(file_name).name
        target = _target(None, folder / safe, overwrite)
        written.append(changes.record(target, content, "write_exercise", f"{name}: {safe}", client=client,
                                      folder=journal_root).path)
    target = _target(None, folder / "question.md", overwrite)
    written.append(changes.record(target, question, "write_exercise", f"Exercise {name}", client=client,
                                  folder=journal_root).path)
    exercise = Exercise.at(folder)
    problems = validate(exercise) if exercise is not None else ["the folder is not read as an exercise"]
    return {"folder": str(folder), "written": written, "blocks": len(sheet.tasks), "points": sheet.points,
            "problems": problems, "valid": not problems,
            "next": f"openprocess exercises check {_q(root)} --answers; open the pack in OpenProcess (Learn ▸ "
                    "Open Exercise Pack…) to try it."}


def import_exam(text: str, target: str, title: str = "Exam", client: str = "") -> dict:
    """A past exam's text into a skeleton pack (one exercise per numbered
    question, an answer block per lettered part, TODOs to finish)."""
    from ..learn.importer import todo_list, write_pack
    root = Path(target).expanduser()
    try:
        with changes.recording(root / "pack.md", "import_exam", f"Pack {title} from an exam's text",
                               client=client):
            written = write_pack(text, root, title)
    except ValueError as error:
        raise ToolError(str(error)) from None
    return {"folder": str(root), "written": [str(p) for p in written], "todos": todo_list(root)}


# ---------------------------------------------------------------------------
# Coloured nets
# ---------------------------------------------------------------------------
def _cpn(path: str):
    from ..io.cpn_reader import read_cpn
    target = Path(path).expanduser()
    if not target.is_file():
        raise ToolError(f"No such .cpn file: {path}")
    return read_cpn(str(target))


def cpn_info(path: str) -> dict:
    """A coloured net's pages, colour sets, variables, initial marking and
    compile problems."""
    net = _cpn(path)
    return {"name": net.name, "problems": [str(e) for e in net.errors],
            "pages": [{"name": p.name, "places": len(p.places), "transitions": len(p.transitions),
                       "arcs": len(p.arcs)} for p in net.pages],
            "colour_sets": {n: type(c).__name__ for n, c in sorted(net.declarations.colour_sets.items())},
            "variables": dict(sorted(net.declarations.variables.items())),
            "initial_marking": net.initial_marking().describe(net),
            "cite": citations("openprocess.model", "openprocess.sim"),
            "reproduce": f"openprocess info {_q(path)}"}


def cpn_simulate(path: str, steps: int = 50, seed: int = 0) -> dict:
    """Run the simulator for ``steps`` firings from the initial marking (the
    seed makes it repeatable) and return the firing log and the final marking."""
    from ..sim.simulator import Simulator
    net = _cpn(path)
    if net.errors:
        raise ToolError("The model does not compile: " + "; ".join(str(e) for e in net.errors))
    simulator = Simulator(net, seed=seed)
    taken = simulator.run(steps)
    return {"steps": taken, "asked": steps, "clock": simulator.clock, "dead": taken < steps,
            "log": [record.describe(net) for record in simulator.log][-200:],
            "final_marking": simulator.marking.describe(net),
            "cite": citations("openprocess.sim"),
            "reproduce": f"openprocess simulate {_q(path)} --steps {steps} --seed {seed}"}


def cpn_state_space(path: str, max_nodes: int = 10_000) -> dict:
    """The reachability graph of a coloured net and its report (bounds, dead
    and home markings, dead and live transitions), as CPN Tools reports them."""
    from ..analysis.state_space import StateSpace
    net = _cpn(path)
    if net.errors:
        raise ToolError("The model does not compile: " + "; ".join(str(e) for e in net.errors))
    space = StateSpace(net).generate(max_nodes=max_nodes)
    return {"report": space.report(), "cite": citations("openprocess.analysis"),
            "reproduce": f"openprocess statespace {_q(path)} --max-nodes {max_nodes}"}


def cpn_performance(path: str, runs: int = 10, steps: int = 1000, warm_up: float = 0.0, seed: int = 0) -> dict:
    """Waiting times, utilisation and throughput of a timed coloured net, over
    ``runs`` seeded simulation runs (run k uses seed + k), with the warm-up
    excluded; the definitions are in the result's notes."""
    from ..sim.performance import analyse_performance
    net = _cpn(path)
    if net.errors:
        raise ToolError("The model does not compile: " + "; ".join(str(e) for e in net.errors))
    result = analyse_performance(net, runs=runs, steps=steps, warm_up=warm_up, seed=seed)
    return {"columns": list(result.columns), "rows": result.rows(), "notes": list(result.notes),
            "runs": [result.summary(run) for run in result.runs],
            "cite": citations("openprocess.sim"),
            "reproduce": {"python": "from openprocess import read_cpn\nfrom openprocess.sim.performance import "
                                    f"analyse_performance\nanalyse_performance(read_cpn({str(path)!r}), runs={runs}, "
                                    f"steps={steps}, warm_up={warm_up}, seed={seed})",
                          "box": "Performance (Coloured nets) with the same settings"}}


# ---------------------------------------------------------------------------
# The app, references and the journal
# ---------------------------------------------------------------------------
def open_in_studio(paths: list[str], folder: str | None = None, start: bool = True) -> dict:
    """Open files (a workflow, a net, a log, an exercise pack) in OpenProcess
    Studio so the user sees them; starts the app if it is not running."""
    from ..flow import studio
    for path in paths:
        if not Path(path).expanduser().exists():
            raise ToolError(f"No such file or folder: {path}")
    try:
        return studio.open_in_studio(*[str(Path(p).expanduser()) for p in paths], folder=folder, start=start)
    except ConnectionError as error:
        raise ToolError(f"{error}. Start OpenProcess Studio (openprocess studio) and try again.") from None


def studio_status() -> dict:
    from ..flow import studio
    found = studio.is_running()
    return {"running": found is not None, **(found or {}), "socket": studio.socket_path()}


def references_list(topic: str | None = None) -> dict:
    """What OpenProcess's notation and algorithms follow: topics with their sources."""
    out = []
    for section, topics in references.TOPICS:
        for item in topics:
            if topic and topic.lower() not in (item.name + " " + section + " " + item.module).lower():
                continue
            out.append({"section": section, "topic": item.name, "note": item.note, "module": item.module,
                        "sources": [{"key": k, "citation": references.reference(k).citation.replace("*", ""),
                                     "url": references.reference(k).url}
                                    for k in item.sources if references.reference(k)]})
    return {"topics": out, "app": app_citation()}


def definition(key: str | None = None) -> dict:
    """The mathematical definition of a property (sound, live, bounded, free
    choice…), or the list of all of them."""
    from ..mining.definitions import DEFINITIONS
    if key is None:
        return {"definitions": [{"key": d.key, "name": d.name, "summary": d.summary} for d in DEFINITIONS]}
    wanted = key.strip().lower().replace("-", "_").replace(" ", "_")
    for d in DEFINITIONS:
        if d.key == wanted or d.name.lower() == key.strip().lower():
            return {"key": d.key, "name": d.name, "summary": d.summary, "formulas": list(d.formulas),
                    "notes": list(d.notes), "source": d.source, "builds_on": list(d.uses)}
    raise ToolError(f"No definition called {key!r}; call without a key for the list.")


def journal(folder: str) -> dict:
    """What an assistant changed in a folder (the review journal): pending and all."""
    root = Path(folder).expanduser()
    every = changes.changes(root)
    return {"folder": str(root), "pending": [changes.describe(c) for c in changes.pending(root)],
            "all": [{"id": c.id, "time": c.time, "tool": c.tool, "file": c.path, "summary": c.summary,
                     "kind": c.kind, "reviewed": c.reviewed, "undone": c.undone} for c in every]}


__all__ = ["ALGORITHMS", "ToolError", "analyse_net", "app_citation", "box_info", "build_workflow", "citations",
           "compare_models", "compute_answer", "conformance", "convert_model", "cpn_info", "cpn_performance",
           "cpn_simulate", "cpn_state_space",
           "definition", "discover", "dotted_chart", "draw", "exercise_guide", "filter_log", "import_exam",
           "is_notation", "journal", "list_boxes", "list_computes", "load_log", "load_net", "log_summary",
           "log_variants", "make_net", "open_in_studio", "references_list", "run_workflow", "studio_status",
           "validate_pack", "write_box", "write_exercise"]
