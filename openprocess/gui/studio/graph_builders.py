"""Turn mining objects into canvas descriptions (:class:`NodeSpec` / :class:`EdgeSpec`).

Keeping this separate from the canvas means the drawing code knows nothing
about Petri nets or logs, and these functions know nothing about Qt.
"""

from __future__ import annotations

import math

from ...mining.analysis import StateGraph
from ...mining.bpmn import BPMN, END as BPMN_END, GATEWAY_SYMBOLS, START as BPMN_START, TASK
from ...mining.dfg import DFG, END, START
from ...mining.discovery.heuristics import DependencyGraph
from ...mining.petrinet import Marking, PetriNet
from ...mining.stats import format_duration
from . import style
from .graph_view import EdgeSpec, NodeSpec


ENABLED_FILL = "#dff5e4"
ENABLED_FILL_DARK = "#1e4028"
ENABLED_STROKE = "#2e9e4f"


def compact(n: float) -> str:
    """1284 -> 1,284;  12900 -> 12.9K."""
    if n >= 100_000:
        return f"{n / 1000:.0f}K"
    if n >= 10_000:
        return f"{n / 1000:.1f}K"
    return f"{int(n):,}"


# ---------------------------------------------------------------------------
# Petri nets
# ---------------------------------------------------------------------------
def petri_net_specs(net: PetriNet, marking: Marking | None = None,
                    enabled: set[str] | None = None,
                    badges: dict[str, list[tuple[str, str]]] | None = None,
                    fills: dict[str, str] | None = None,
                    show_place_names: bool = True) -> tuple[list[NodeSpec], list[EdgeSpec]]:
    t = style.tokens()
    marking = marking if marking is not None else net.initial_marking
    enabled = enabled or set()
    badges = badges or {}
    fills = fills or {}
    nodes: list[NodeSpec] = []
    for place in net.places.values():
        roles = []
        if net.initial_marking[place.id]:
            roles.append("initial")
        if net.final_marking[place.id]:
            roles.append("final")
        name = place.name
        tip = f"Place {name}" + (f" ({', '.join(roles)})" if roles else "")
        tip += f"\nTokens: {marking[place.id]}"
        nodes.append(NodeSpec(place.id, "place", below=name if show_place_names else "",
                              tokens=marking[place.id], badges=badges.get(place.id, []),
                              tooltip=tip, fill=fills.get(place.id)))
    for transition in net.transitions.values():
        is_enabled = transition.id in enabled
        tip = ("Silent transition (τ)" if transition.silent else f"Transition {transition.label}")
        if is_enabled:
            tip += "\nEnabled — click to fire"
        # Enabled transitions are green, as in CPN Tools and CPN IDE; a silent
        # transition keeps its solid fill but gets the green outline.
        enabled_fill = ENABLED_FILL_DARK if style.theme.is_dark() else ENABLED_FILL
        fill = fills.get(transition.id)
        if fill is None and is_enabled:
            fill = ENABLED_STROKE if transition.silent else enabled_fill
        nodes.append(NodeSpec(
            transition.id, "silent" if transition.silent else "transition",
            text="" if transition.silent else transition.name,
            fill=fill, stroke=ENABLED_STROKE if is_enabled else None, emphasis=is_enabled,
            badges=badges.get(transition.id, []), tooltip=tip))
    edges = [EdgeSpec(a.source, a.target, text=str(a.weight) if a.weight != 1 else "",
                      width=1.4) for a in net.arcs]
    return nodes, edges


# ---------------------------------------------------------------------------
# BPMN
# ---------------------------------------------------------------------------
def bpmn_specs(model: BPMN) -> tuple[list[NodeSpec], list[EdgeSpec], dict[str, tuple[float, float]] | None]:
    """Nodes, edges and positions for a BPMN model: rounded rectangles for the
    tasks, diamonds with × or + for the gateways, circles for the events.
    Positions are the diagram's when every node has one, else None (laid out)."""
    t = style.tokens()
    nodes: list[NodeSpec] = []
    for node in model.nodes.values():
        if node.kind == TASK:
            nodes.append(NodeSpec(node.id, "activity", text=node.name, fill=t.surface, stroke=t.border,
                                  tooltip=f"Task {node.name}"))
        elif node.kind in (BPMN_START, BPMN_END):
            what = "Start event" if node.kind == BPMN_START else "End event"
            nodes.append(NodeSpec(node.id, node.kind, below=node.name,
                                  tooltip=what + (f" {node.name}" if node.name else "")))
        else:
            symbol = GATEWAY_SYMBOLS[node.kind]
            what = "Exclusive gateway (one path)" if symbol == "×" else "Parallel gateway (every path)"
            nodes.append(NodeSpec(node.id, "gateway", text=symbol, below=node.name, fill=t.accent_soft,
                                  stroke=t.accent, tooltip=what + (f": {node.name}" if node.name else "")))
    edges = [EdgeSpec(f.source, f.target, text=f.name, width=1.4,
                      tooltip=f"{model.nodes[f.source].name or f.source} → {model.nodes[f.target].name or f.target}")
             for f in model.flows]
    positions = {n.id: n.position for n in model.nodes.values()} if model.has_positions() else None
    return nodes, edges, positions


# ---------------------------------------------------------------------------
# Process maps
# ---------------------------------------------------------------------------
def ocdfg_specs(graph) -> tuple[list[NodeSpec], list[EdgeSpec], dict[str, str]]:
    """The object-centric map: the activities once, and one set of arrows per
    object type in that type's colour, side by side where types share a path.
    Returns the nodes, the edges and ``{object type: colour}`` for a legend."""
    t = style.tokens()
    colours = {object_type: style.categorical(index) for index, object_type in enumerate(graph.object_types)}
    nodes = [NodeSpec(activity, "activity", text=activity, subtext=f"{count:,} events",
                      fill=t.surface, stroke=t.border,
                      tooltip=f"{activity}: {count:,} events\n" + "\n".join(
                          f"{object_type}: {graph.objects_at.get((activity, object_type), 0)} objects"
                          for object_type in graph.object_types))
             for activity, count in graph.activities.most_common()]
    edges: list[EdgeSpec] = []
    busiest = max((n for counts in graph.edges.values() for n in counts.values()), default=1)
    count_types = len(graph.object_types) or 1
    for index, object_type in enumerate(graph.object_types):
        offset = (index - (count_types - 1) / 2) * 12.0
        for (a, b), n in graph.edges[object_type].items():
            edges.append(EdgeSpec(a, b, text=str(n), width=1.2 + 2.6 * n / busiest, colour=colours[object_type],
                                  tooltip=f"{object_type}: {n} object{'s' if n != 1 else ''} went {a} → {b}",
                                  bend=offset if count_types > 1 else 0.0))
    return nodes, edges, colours


def dfg_specs(dfg: DFG, mode: str = "frequency") -> tuple[list[NodeSpec], list[EdgeSpec]]:
    """Frequency mode: node fill = how often; edge width = how often.
    Performance mode: edge labels are mean durations; the slowest edges are
    drawn thickest, in the accent-free critical tone with a text label."""
    t = style.tokens()
    most = max(dfg.activities.values(), default=1)
    nodes = [NodeSpec(START, "start", tooltip=f"Start — {sum(dfg.start.values()):,} cases"),
             NodeSpec(END, "end", tooltip=f"End — {sum(dfg.end.values()):,} cases")]
    for activity, count in dfg.activities.items():
        fraction = math.log1p(count) / math.log1p(most) if most else 0
        fill = style.sequential(0.1 + 0.8 * fraction)
        nodes.append(NodeSpec(activity, "activity", text=activity, subtext=compact(count),
                              fill=fill, stroke=style.SEQUENTIAL_BLUE[-3],
                              tooltip=f"{activity}\n{count:,} events"))

    all_edges = dfg.all_edges()
    top = max((n for _, _, n in all_edges), default=1)
    durations = {edge: dfg.mean_duration(edge) for edge in dfg.edges}
    slowest = max((d for d in durations.values() if d is not None), default=0)
    edges: list[EdgeSpec] = []
    for a, b, count in all_edges:
        weight = count / top if top else 0
        if mode == "performance" and (a, b) in dfg.edges:
            mean = durations.get((a, b))
            relative = (mean / slowest) if (mean is not None and slowest) else 0
            edges.append(EdgeSpec(
                a, b, text=format_duration(mean) if mean is not None else "",
                width=1.2 + 4.5 * relative,
                colour=style.STATUS["critical"] if relative > 0.66 else t.text_secondary,
                tooltip=f"{a} → {b}\nmean {format_duration(mean)} · median "
                        f"{format_duration(dfg.median_duration((a, b)))} · {count:,}×"))
        else:
            edges.append(EdgeSpec(a, b, text=compact(count) if mode == "frequency" else "",
                                  width=1.0 + 4.5 * weight,
                                  colour=t.text_secondary if weight > 0.15 else t.text_muted,
                                  dashed=a == START or b == END,
                                  tooltip=f"{a} → {b}: {count:,} times"))
    return nodes, edges


def dependency_specs(graph: DependencyGraph) -> tuple[list[NodeSpec], list[EdgeSpec]]:
    t = style.tokens()
    most = max(graph.activities.values(), default=1)
    nodes = [NodeSpec(START, "start"), NodeSpec(END, "end")]
    for activity, count in graph.activities.items():
        fraction = math.log1p(count) / math.log1p(most)
        nodes.append(NodeSpec(activity, "activity", text=activity, subtext=compact(count),
                              fill=style.sequential(0.1 + 0.8 * fraction),
                              stroke=style.SEQUENTIAL_BLUE[-3]))
    edges = [EdgeSpec(START, a, dashed=True, colour=t.text_muted) for a in graph.start]
    edges += [EdgeSpec(a, END, dashed=True, colour=t.text_muted) for a in graph.end]
    for (a, b), (value, count) in graph.edges.items():
        edges.append(EdgeSpec(a, b, text=f"{value:.2f}", width=1.0 + 3 * max(value, 0),
                              colour=t.text_secondary,
                              tooltip=f"{a} ⇒ {b} = {value:.3f}   (|{a} > {b}| = {count})"))
    return nodes, edges


# ---------------------------------------------------------------------------
# State spaces
# ---------------------------------------------------------------------------
def state_graph_specs(graph: StateGraph, limit: int = 250) -> tuple[list[NodeSpec], list[EdgeSpec]]:
    t = style.tokens()
    net = graph.net
    shown = min(len(graph.states), limit)
    dead = set(graph.dead_states())
    nodes = []
    for index in range(shown):
        marking = graph.states[index]
        is_final = marking == net.final_marking and bool(net.final_marking)
        tip = f"State {index}: {marking.describe(net)}"
        if index == 0:
            tip += "\nInitial marking"
        if index in dead:
            tip += "\nDead: nothing is enabled" + (" (final marking)" if is_final else "")
        stroke = None
        if index in dead and not is_final:
            stroke = style.STATUS["critical"]
        nodes.append(NodeSpec(str(index), "state", text=marking.describe(net), tooltip=tip,
                              emphasis=index == 0 or is_final, stroke=stroke,
                              fill=t.accent_soft if index == 0 else None))
    # Several transitions can lead from the same state to the same state
    # (e.g. a choice between three seats that all end in [sink]); draw them
    # as one edge carrying all labels instead of stacking identical curves.
    labels: dict[tuple[int, int], list[str]] = {}
    for s, tr, d in graph.edges:
        if s < shown and d < shown:
            labels.setdefault((s, d), []).append(net.transitions[tr].name)
    edges = [EdgeSpec(str(s), str(d), width=1.2,
                      text=names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}",
                      tooltip="\n".join(names))
             for (s, d), names in labels.items()]
    return nodes, edges
