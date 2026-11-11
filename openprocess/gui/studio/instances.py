"""The definitions, filled in for the net being analysed.

A definition says ``∀M: [i] →* M ⇒ M →* [o]``; for the order-handling net
the pop-up should also say what that means *there* -- ``i`` is "start", and
when the property fails, which marking and which firing sequence show it:
``[start] →⟨register, check stock, reject⟩ [c4, end]`` and
``[c4, end] ↛* [end]``.  Each function here returns such formulas, as LaTeX
lines for :mod:`.mathtext` (names go in ``\\text{…}`` via
:func:`.mathtext.name`, so any node name is safe).

Everything is computed from the analysis reports; nothing here touches Qt.
"""

from __future__ import annotations

from ...mining.analysis import OMEGA, PropertyReport, ShortCircuitReport, SoundnessReport
from ...mining.petrinet import Marking, PetriNet
from .mathtext import name, name_set, sequence

T_STAR = "t_star"


def node(net: PetriNet, node_id: str) -> str:
    """A place or transition as it appears in formulas (``t*`` stays maths)."""
    return "t^*" if node_id == T_STAR else name(net.node_name(node_id))


def marking(net: PetriNet, value: Marking) -> str:
    """``[\\text{c4}, 2\\,\\text{c2}]`` -- the multiset notation of the course."""
    parts = []
    for place, count in sorted(value.items(), key=lambda item: net.node_name(item[0])):
        if count == OMEGA:
            parts.append(f"\\omega\\, {node(net, place)}")
        elif count == 1:
            parts.append(node(net, place))
        elif count:
            parts.append(f"{int(count)}\\, {node(net, place)}")
    return "[" + ", ".join(parts) + "]"


_SIGMA = "\x00sigma:"


def firing(net: PetriNet, path: list[str]) -> str:
    """The arrow for firing ``path``: labelled with the transition itself for
    one step, and with σ for longer sequences -- σ is then spelled out on a
    line of its own (see :func:`_spell_out`), which reads far better than a
    long superscript."""
    if not path:
        return "\\xrightarrow{\\langle \\rangle}"
    names = ["t*" if t == T_STAR else net.node_name(t) for t in path]
    if len(names) == 1:
        return "\\xrightarrow{" + node(net, path[0]) + "}"
    return "\\xrightarrow{\\sigma}" + _SIGMA + sequence(names, limit=10) + _SIGMA


def _spell_out(function):
    """Move the σ = ⟨…⟩ that :func:`firing` tucked into a line onto a line of
    its own, after the formulas that use it."""
    def wrapper(*args, **kwargs) -> list[str]:
        out, spelled_out = [], []
        for line in function(*args, **kwargs):
            if _SIGMA not in line:
                out.append(line)
                continue
            before, spelled, after = line.split(_SIGMA, 2)
            out.append(before + after)
            where = f"\\text{{where }} \\sigma = {spelled}"
            if where not in spelled_out:
                spelled_out.append(where)
        return out + spelled_out
    wrapper.__doc__ = function.__doc__
    wrapper.__name__ = function.__name__
    return wrapper


def _transitions(net: PetriNet, ids, limit: int = 6) -> str:
    items = [node(net, t) for t in ids]
    if len(items) > limit:
        items = items[:limit - 1] + ["\\ldots"]
    return "\\lbrace " + ", ".join(items) + " \\rbrace" if items else "\\emptyset"


# ---------------------------------------------------------------------------
# Soundness
# ---------------------------------------------------------------------------
@_spell_out
def soundness(report: SoundnessReport, key: str) -> list[str]:
    """Formulas for ``wf_net``, ``sound`` and the three soundness conditions."""
    workflow = report.workflow
    net = report.graph.net if report.graph is not None else None
    if key == "wf_net":
        if not workflow.is_workflow_net:
            return ["\\text{" + problem.replace("{", "(").replace("}", ")") + "}"
                    for problem in workflow.problems[:3]]
        return _wf(report)
    if not workflow.is_workflow_net or net is None:
        return []
    i, o = node(net, workflow.source), node(net, workflow.sink)
    start, end = f"[{i}]", f"[{o}]"
    graph = report.graph
    if key == "option_to_complete":
        lines = [f"\\forall M \\colon {start} \\xrightarrow{{*}} M \\Rightarrow "
                 f"M \\xrightarrow{{*}} {end}"]
        state = report.witness.get("option_to_complete")
        if state is not None:
            m = marking(net, graph.states[state])
            lines += [f"\\text{{but }} {start} {firing(net, graph.path_to(state))} {m}",
                      f"\\text{{and }} {m} \\not\\xrightarrow{{*}} {end}"]
        return lines
    if key == "proper_completion":
        lines = [f"\\forall M \\colon {start} \\xrightarrow{{*}} M \\land M \\geq {end} "
                 f"\\Rightarrow M = {end}"]
        state = report.witness.get("proper_completion")
        if state is not None:
            m = marking(net, graph.states[state])
            lines += [f"\\text{{but }} {start} {firing(net, graph.path_to(state))} {m}",
                      f"\\text{{and }} {m} \\geq {end}, \\; {m} \\neq {end}"]
        return lines
    if key == "no_dead_transitions":
        if report.dead_transitions:
            return [f"\\neg \\exists M \\colon {start} \\xrightarrow{{*}} M \\land "
                    f"{node(net, t)} \\text{{ enabled in }} M"
                    for t in report.dead_transitions[:3]]
        return [f"\\forall t \\in {_transitions(net, net.transitions)} \\; \\exists M, M' "
                f"\\colon {start} \\xrightarrow{{*}} M \\xrightarrow{{t}} M'"]
    if key == "sound":
        def verdict(value):
            return "\\text{holds}" if value else (
                "\\text{fails}" if value is False else "\\text{undecided}")
        lines = [f"\\text{{(i) }} {verdict(report.option_to_complete)} \\; \\land \\; "
                 f"\\text{{(ii) }} {verdict(report.proper_completion)} \\; \\land \\; "
                 f"\\text{{(iii) }} {verdict(report.no_dead_transitions)}"]
        if report.bounded is False:
            state = report.witness.get("bounded")
            if state is not None:
                lines.append(f"\\text{{unbounded: }} {start} "
                             f"{firing(net, graph.path_to(state))} "
                             f"{marking(net, graph.states[state])}")
        return lines
    return []


def _wf(report: SoundnessReport) -> list[str]:
    net = report.graph.net if report.graph is not None else None
    workflow = report.workflow
    if net is None:
        return []
    i, o = node(net, workflow.source), node(net, workflow.sink)
    return [f"i = {i}, \\quad o = {o}",
            f"\\bullet {i} = \\emptyset, \\quad {o} \\bullet = \\emptyset"]


# ---------------------------------------------------------------------------
# The short-circuited net (soundness theorem)
# ---------------------------------------------------------------------------
@_spell_out
def short_circuit(report: SoundnessReport, key: str) -> list[str]:
    closed: ShortCircuitReport | None = report.short_circuit
    if closed is None:
        return []
    net, graph, props = closed.net, closed.properties.graph, closed.properties
    workflow = report.workflow
    i, o = node(net, workflow.source), node(net, workflow.sink)
    marked = f"(\\overline{{N}}, [{i}])"
    if key == "short_circuit":
        return [f"\\overline{{N}} = (P, \\; T \\cup \\lbrace t^* \\rbrace, \\; F \\cup "
                f"\\lbrace ({o}, t^*), (t^*, {i}) \\rbrace)"]
    if key == "live":
        if closed.not_live:
            transition, state = next(iter(closed.not_live.items()))
            m = marking(net, graph.states[state])
            return [f"[{i}] {firing(net, graph.path_to(state))} {m} \\in R{marked[:-1]})",
                    f"\\neg \\exists M' \\in R(\\overline{{N}}, {m}) \\colon "
                    f"{node(net, transition)} \\text{{ enabled in }} M'"]
        if closed.live:
            return [f"\\forall t \\in T \\cup \\lbrace t^* \\rbrace \\; \\forall M \\in "
                    f"R{marked[:-1]}) \\; \\exists M' \\in R(\\overline{{N}}, M) \\colon "
                    "t \\text{ enabled in } M'"]
        return ["\\text{not decided: the state space is unbounded or too large}"]
    if key == "bounded":
        return _bounds(net, props, f"R{marked[:-1]})")
    if key == "safe":
        lines = _bounds(net, props, f"R{marked[:-1]})")
        return lines
    if key == "deadlock_free":
        if props.dead_markings:
            state = props.dead_markings[0]
            m = marking(net, graph.states[state])
            return [f"[{i}] {firing(net, graph.path_to(state))} {m}",
                    f"\\forall t \\in T \\cup \\lbrace t^* \\rbrace \\colon t "
                    f"\\text{{ is not enabled in }} {m}"]
        return [f"\\forall M \\in R{marked[:-1]}) \\; \\exists t \\in T \\cup "
                f"\\lbrace t^* \\rbrace \\colon t \\text{{ enabled in }} M"]
    if key == "soundness_theorem":
        sound = {True: "\\text{sound}", False: "\\text{not sound}",
                 None: "\\text{undecided}"}[closed.sound]
        if not closed.bounded:
            return [f"{marked} \\text{{ is unbounded}} \\; \\Rightarrow \\; "
                    f"N \\text{{ is }} {sound}"]
        live = {True: "\\text{live}", False: "\\text{not live}",
                None: "\\text{(liveness undecided)}"}[closed.live]
        return [f"{marked} \\text{{ is }} {live} \\land \\text{{bounded}} \\; "
                f"\\Rightarrow \\; N \\text{{ is }} {sound}"]
    return []


def _bounds(net: PetriNet, props: PropertyReport, reachable: str) -> list[str]:
    """``∀M ∈ R: ∀p: M(p) ≤ k`` or the place that is unbounded / holds most."""
    bounds = props.place_bounds
    if not bounds:
        return []
    if not props.bounded:
        places = [p for p, b in bounds.items() if b == OMEGA][:3]
        return [f"M({node(net, p)}) = \\omega \\text{{ (unbounded)}}" for p in places]
    k = int(props.bound)
    busiest = max(bounds, key=lambda p: bounds[p])
    lines = [f"\\forall M \\in {reachable} \\; \\forall p \\in P \\colon M(p) \\leq {k}"]
    if k > 1:
        lines.append(f"\\text{{e.g. }} M({node(net, busiest)}) = {k} \\text{{ is reachable}}")
    return lines


# ---------------------------------------------------------------------------
# Properties of the net as drawn (from its own initial marking)
# ---------------------------------------------------------------------------
@_spell_out
def properties(props: PropertyReport, key: str) -> list[str]:
    net, graph = props.graph.net, props.graph
    m0 = marking(net, graph.states[0]) if graph.states else "[\\,]"
    reach = f"R(N, {m0})"
    if key in ("bounded", "safe"):
        return _bounds(net, props, reach)
    if key == "deadlock_free":
        if props.dead_markings:
            state = props.dead_markings[0]
            m = marking(net, graph.states[state])
            return [f"{m0} {firing(net, graph.path_to(state))} {m}",
                    f"\\forall t \\in T \\colon t \\text{{ is not enabled in }} {m}"]
        if props.deadlock_free is None:
            return ["\\text{not decided: the state space is unbounded or too large}"]
        return [f"\\forall M \\in {reach} \\; \\exists t \\in T \\colon t "
                "\\text{ enabled in } M"]
    if key == "dead_transition":
        if props.dead_transitions:
            return [f"\\neg \\exists M \\in {reach} \\colon {node(net, t)} "
                    "\\text{ enabled in } M" for t in props.dead_transitions[:3]]
        return [f"\\forall t \\in {_transitions(net, net.transitions)} \\; \\exists M "
                f"\\in {reach} \\colon t \\text{{ enabled in }} M"]
    if key == "live":
        if props.live_transitions is None:
            return ["\\text{not decided: the state space is unbounded or too large}"]
        dead = [t for t in net.transitions if t not in props.live_transitions]
        if not dead:
            return [f"\\forall t \\in T \\; \\forall M \\in {reach} \\; \\exists M' \\in "
                    "R(N, M) \\colon t \\text{ enabled in } M'"]
        return [f"{_transitions(net, dead)} \\text{{ can stop being fireable for good}}"]
    if key == "reversible":
        if props.reversible is None:
            return ["\\text{not decided}"]
        if props.reversible:
            return [f"\\forall M \\in {reach} \\colon M \\xrightarrow{{*}} {m0}"]
        back = graph.backward_reachable({0})
        stuck = next(s for s in range(len(graph.states)) if s not in back)
        m = marking(net, graph.states[stuck])
        return [f"{m0} {firing(net, graph.path_to(stuck))} {m}",
                f"{m} \\not\\xrightarrow{{*}} {m0}"]
    return []


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
@_spell_out
def structure(report: SoundnessReport, net: PetriNet, key: str) -> list[str]:
    result = report.structure
    if result is None:
        return []

    def pre(t: str) -> str:
        return name_set(net.node_name(p) for p in sorted(net.preset(t)))

    if key == "free_choice":
        if not result.free_choice:
            return ["\\forall t_1, t_2 \\in T \\colon \\bullet t_1 \\cap \\bullet t_2 "
                    "\\neq \\emptyset \\Rightarrow \\bullet t_1 = \\bullet t_2"]
        t1, t2, _ = result.free_choice[0]
        a, b = node(net, t1), node(net, t2)
        shared = name_set(net.node_name(p) for p in sorted(net.preset(t1) & net.preset(t2)))
        return [f"\\bullet {a} \\cap \\bullet {b} = {shared} \\neq \\emptyset",
                f"\\text{{but }} \\bullet {a} = {pre(t1)} \\neq {pre(t2)} = \\bullet {b}"]
    if key == "well_structured":
        if result.handles is None:
            return ["\\text{not checked (the net is very large)}"]
        if not result.handles:
            return ["\\overline{N} \\text{ has no handles: every pair of elementary paths "
                    "between a place and a transition meets in between}"]
        handle = result.handles[0]

        def path(nodes):
            names = [node(net, n[2:]) for n in nodes]
            return "\\langle " + ", ".join(names) + " \\rangle"

        x, y = node(net, handle.start[2:]), node(net, handle.end[2:])
        return [f"C_1 = {path(handle.paths[0])}",
                f"C_2 = {path(handle.paths[1])}",
                f"\\alpha(C_1) \\cap \\alpha(C_2) = \\lbrace {x}, {y} \\rbrace \\land "
                f"C_1 \\neq C_2 \\quad \\text{{({handle.kind}-handle)}}"]
    if key == "s_coverable":
        coverage = result.coverage
        if coverage is None:
            return ["\\text{not checked (the net is very large)}"]
        lines = []
        for number, component in enumerate(coverage.components[:4], start=1):
            places = sorted(net.node_name(p) for p in component)
            lines.append(f"P_{{{number}}} = {name_set(places)}")
        for missing in coverage.uncovered[:3]:
            lines.append(f"\\neg \\exists \\text{{ S-component }} N_s \\colon "
                         f"{node(net, missing)} \\in N_s")
        return lines
    return []


# ---------------------------------------------------------------------------
# Transition systems and regions
# ---------------------------------------------------------------------------
def _states(ts, states, limit: int = 8) -> str:
    """``{s0, s2}`` in the transition system's own order."""
    position = {s: i for i, s in enumerate(ts.states)}
    items = sorted(states, key=lambda s: position.get(s, len(position)))
    if not items:
        return "\\emptyset"
    shown = [name(s) for s in items[:limit]] + (["\\ldots"] if len(items) > limit else [])
    return "\\lbrace " + ", ".join(shown) + " \\rbrace"


def regions(analysis, key: str, synthesis=None) -> list[str]:
    """Formulas for the region definitions, filled in for ``analysis.ts``."""
    ts = analysis.ts
    events = list(analysis.by_event.values())
    if key == "transition_system":
        start = name(ts.initial_state) if ts.initial_state else "\\text{(" + \
            str(len(ts.initial)) + " initial states)}"
        return [f"|S| = {len(ts.states)}, \\quad E = "
                + "\\lbrace " + ", ".join(name(e) for e in ts.events[:10])
                + (", \\ldots" if len(ts.events) > 10 else "") + " \\rbrace"
                + f", \\quad |T| = {len(ts.transitions)}",
                f"s_{{in}} = {start}"]
    if key == "minimal_region":
        lines = [f"\\text{{{len(analysis.regions)} non-trivial regions, "
                 f"{len(analysis.minimal)} minimal}}"]
        for number, region in enumerate(analysis.minimal[:6], 1):
            lines.append(f"r_{{{number}}} = {_states(ts, region)}")
        return lines
    if key == "pre_region":
        lines = []
        for item in events[:5]:
            pre = ", ".join(f"r_{{{analysis.minimal.index(r) + 1}}}" for r in item.minimal_pre)
            post = ", ".join(f"r_{{{analysis.minimal.index(r) + 1}}}" for r in item.minimal_post)
            lines.append(f"\\text{{pre}}({name(item.event)}) \\cap \\text{{minimal}} = "
                         f"\\lbrace {pre or ' '} \\rbrace, \\quad \\text{{post}}"
                         f"({name(item.event)}) \\cap \\text{{minimal}} = "
                         f"\\lbrace {post or ' '} \\rbrace")
        return lines
    if key == "ger":
        return [f"\\text{{GER}}({name(item.event)}) = {_states(ts, item.ger)}"
                for item in events[:6]]
    if key == "forward_closure":
        lines = []
        failing = [item for item in events if not item.forward_closed]
        for item in (failing or events)[:5]:
            relation = "=" if item.forward_closed else "\\neq"
            lines.append(f"\\bigcap \\text{{pre}}({name(item.event)}) = "
                         f"{_states(ts, item.intersection)} {relation} "
                         f"\\text{{GER}}({name(item.event)}) = {_states(ts, item.ger)}")
        return lines
    if key == "state_separation":
        if analysis.inseparable:
            return [f"{name(pair.first)} \\neq {name(pair.second)}, \\text{{ but every region "
                    f"contains both or neither}}" for pair in analysis.inseparable[:4]]
        return [f"\\text{{all {len(ts.states)} states lie in different sets of regions}}"]
    if key == "elementary_ts":
        def verdict(value):
            return "\\text{holds}" if value else (
                "\\text{fails}" if value is False else "\\text{undecided}")
        return [f"\\text{{state separation }} {verdict(analysis.state_separation)} \\; "
                f"\\land \\; \\text{{forward closure }} {verdict(analysis.forward_closure)}"]
    if key == "region_synthesis" and synthesis is not None and synthesis.net is not None:
        net = synthesis.net
        marked = [p for p in net.initial_marking]
        lines = [f"|P| = {len(net.places)}, \\quad |T| = {len(net.transitions)}, \\quad "
                 f"M_0 = [" + ", ".join(f"p_{{r_{{{p[1:]}}}}}" for p in marked) + "]"]
        if synthesis.isomorphic is not None:
            lines.append("RG(N, M_0) \\cong TS" if synthesis.isomorphic else
                         "RG(N, M_0) \\text{ and } TS \\text{ are not isomorphic}")
        return lines
    return []


def region_check(check, ts) -> list[str]:
    """The definition of a region filled in for one set of states."""
    if check.unknown:
        return []
    region = _states(ts, check.states)
    if check.is_region:
        parts = []
        for event, what in list(check.events.items())[:6]:
            word = {"enter": "enter", "exit": "exit"}.get(what, "nocross")
            parts.append(f"\\text{{{word}}}({name(event)}, R)")
        return [f"R = {region}", " \\land ".join(parts)]
    (s1, t1, _), (s2, t2, _) = check.conflict[:2]
    e = name(check.event)
    return [f"R = {region}",
            f"({name(s1)}, {e}, {name(t1)}) \\in T \\text{{ and }} ({name(s2)}, {e}, "
            f"{name(t2)}) \\in T \\text{{ cross }} R \\text{{ differently}}"]
