"""Discover boxes: from a log to a model, each one showing how."""

from __future__ import annotations

from typing import Literal

from ... import flow
from ...mining.bpmn import BPMN, bpmn_to_petri_net, petri_net_to_bpmn, tree_to_bpmn as _tree_to_bpmn
from ...mining.dfg import DFG, discover_dfg
from ...mining.discovery import alpha as alpha_module
from ...mining.discovery.heuristics import heuristics_net
from ...mining.discovery.inductive import inductive_miner as _inductive
from ...mining.discovery.state_regions import region_result
from ...mining.footprint import Footprint, footprint_of_log
from ...mining.log import EventLog
from ...mining.petrinet import PetriNet
from ...mining.processtree import ProcessTree, to_petri_net
from ...mining.transition_system import TransitionSystem, transition_system_from_log
from ..box import box
from ..types import OCDFG, OCEL, Table


@box(name="α-algorithm", group="Discover")
def alpha_miner(log: EventLog) -> PetriNet:
    """The α-algorithm (van der Aalst, Weijters & Maruster, 2004): a Petri
    net from the footprint of the log, in the eight steps of the book."""
    result = alpha_module.alpha_miner(log.simple_log())
    flow.show(footprint_of_log(log.simple_log()), "Footprint of the log")
    flow.steps(result.steps())
    flow.show(result, "The derivation, typeset")
    for warning in result.warnings:
        flow.note(f"Warning: {warning}")
    result.net.name = f"α({log.name})"
    return result.net


@box(name="Inductive Miner", group="Discover")
def inductive_miner(log: EventLog, noise: float = 0.0) -> PetriNet:
    """Splits the log recursively into sequence, choice, parallel and loop
    parts (Leemans, Fahland & van der Aalst). With *noise* above 0 this is
    IMf, which ignores behaviour rarer than the threshold.

    noise: the IMf noise threshold, 0 for the plain Inductive Miner
    """
    result = _inductive(log.simple_log(), noise_threshold=noise)
    flow.show(result, "Process tree and the recursion")
    flow.steps([(f"{'  ' * s.depth}{s.kind}", s.text) for s in result.steps])
    result.net.name = f"IM{'f' if noise > 0 else ''}({log.name})"
    return result.net


@box(name="Inductive Miner tree", group="Discover")
def inductive_tree(log: EventLog, noise: float = 0.0) -> ProcessTree:
    """The Inductive Miner's process tree itself, to convert or inspect."""
    result = _inductive(log.simple_log(), noise_threshold=noise)
    flow.steps([(f"{'  ' * s.depth}{s.kind}", s.text) for s in result.steps])
    return result.tree


@box(name="Tree to net", group="Discover")
def tree_to_net(tree: ProcessTree) -> PetriNet:
    """Translates a process tree into a sound WF-net (block by block)."""
    return to_petri_net(tree)


@box(name="Tree to BPMN", group="Discover")
def tree_to_bpmn(tree: ProcessTree) -> BPMN:
    """Translates a process tree into a BPMN model, block by block: a
    sequence is a chain, a choice exclusive gateways, a parallel block
    parallel gateways, a loop exclusive gateways with a flow back, and τ an
    empty flow."""
    model = _tree_to_bpmn(tree)
    flow.note(model.summary())
    return model


@box(name="BPMN to net", group="Discover")
def bpmn_to_net(model: BPMN) -> PetriNet:
    """The Petri net semantics of a BPMN model (Dijkman, Dumas & Ouyang,
    2008): tasks become transitions, sequence flows places, parallel
    gateways silent transitions, exclusive gateways places, the start event
    the source place and the end event the sink place."""
    net = bpmn_to_petri_net(model)
    flow.note(net.summary())
    for warning in model.warnings:
        flow.note(f"Warning: {warning}")
    return net


@box(name="Net to BPMN", group="Discover")
def net_to_bpmn(net: PetriNet) -> BPMN:
    """A BPMN model of a WF-net: transitions become tasks (with parallel
    gateways where they split or join), places with several producers or
    consumers exclusive gateways, and silent steps plain flows. Exact for
    free-choice nets, the kind the Inductive Miner produces."""
    model = petri_net_to_bpmn(net)
    flow.note(model.summary())
    return model


@box(name="Heuristics Miner", group="Discover")
def heuristics_miner(log: EventLog, dependency: float = 0.9) -> PetriNet:
    """Keeps the directly-follows relations whose dependency measure
    a ⇒ b = (|a>b| − |b>a|) / (|a>b| + |b>a| + 1) reaches the threshold, then
    turns the dependency graph into a Petri net through a causal net.

    dependency: the threshold a ⇒ b must reach
    """
    result = heuristics_net(log.simple_log(), dependency_threshold=dependency)
    flow.show(result, "Dependency graph and bindings")
    graph = result.graph
    rows = [[a, b, round(measure, 3), "kept" if (a, b) in graph.edges else "dropped"]
            for (a, b), measure in sorted(graph.all_dependencies.items())]
    flow.show(Table("Dependency measures", ["a", "b", "a ⇒ b", "in the graph"], rows), "Dependency measures")
    flow.note(f"{len(graph.edges)} relations reach {dependency:g}")
    result.net.name = f"HM({log.name})"
    return result.net


@box(name="Classical states", group="Discover")
def classical_states(log: EventLog, direction: Literal["prefix", "postfix", "both"] = "prefix",
                     representation: Literal["sequence", "multiset", "set"] = "set",
                     horizon: Literal["1", "2", "3", "all"] = "all") -> TransitionSystem:
    """A transition system from the log with a classical state function:
    each prefix (or postfix) becomes a state through its sequence, multiset
    or set, over the last k events or all of them."""
    k = None if horizon == "all" else int(horizon)
    ts = transition_system_from_log(log.simple_log(), direction, representation, k)
    flow.note(f"{len(ts.states)} states, {len(ts.transitions)} transitions ({ts.abstraction})")
    return ts


@box(name="Regions to net", group="Discover")
def regions_to_net(ts: TransitionSystem) -> PetriNet:
    """Synthesises a Petri net from the minimal regions of a transition
    system: one place per minimal region (Cortadella et al.)."""
    result = region_result(ts)
    flow.show(result, "Regions, checks and synthesis")
    flow.note(f"{len(result.analysis.regions)} regions, {len(result.analysis.minimal)} minimal")
    if result.synthesis.isomorphic is not None:
        flow.note("The net's reachability graph is isomorphic to the transition system"
                  if result.synthesis.isomorphic else
                  "The net's reachability graph is not isomorphic to the transition system")
    if result.net is None:
        raise ValueError(" ".join(result.warnings) or "No net could be synthesised.")
    return result.net


@box(name="Object-centric map", group="Discover")
def object_centric_map(ocel: OCEL) -> OCDFG:
    """The object-centric directly-follows graph: every object followed
    through its events, so each object type gets its own directly-follows
    counts, drawn in its own colour on the shared activities. No case id is
    invented, so nothing converges or diverges that did not."""
    from ...mining.ocel import object_centric_dfg
    graph = object_centric_dfg(ocel)
    for object_type in graph.object_types:
        flow.note(f"{object_type}: {len(graph.edges[object_type])} paths, "
                  f"starts at {', '.join(sorted(graph.starts[object_type])) or '–'}")
    return graph


@box(name="Directly-follows graph", group="Discover")
def directly_follows(log: EventLog) -> DFG:
    """The process map: which activity directly follows which, how often."""
    dfg = discover_dfg(log)
    flow.note(f"{len(dfg.activities)} activities, {len(dfg.edges)} arcs")
    return dfg


@box(name="Footprint", group="Discover")
def footprint(log: EventLog) -> Footprint:
    """The → ← ‖ # matrix of the log's ordering relations."""
    return footprint_of_log(log.simple_log())
