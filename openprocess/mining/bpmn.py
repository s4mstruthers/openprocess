"""BPMN: the business process notation most practical models come in.

BPMN 2.0 (OMG, formal/2011-01-03) draws a process as a graph of *flow
nodes* joined by *sequence flows*: a start event (a thin circle), tasks
(rounded rectangles), gateways (diamonds: ``×`` for an exclusive choice,
``+`` for parallel branches) and an end event (a thick circle).  Many logs
come with a BPMN model of the process they record, and modelling tools
(Camunda Modeler, bpmn.io, Signavio) write it as BPMN 2.0 XML.

What this module handles is the control-flow core, enough for process
mining: start and end events, tasks (of every kind), exclusive and parallel
gateways and sequence flows, with the diagram's positions when the file has
them.  Pools, lanes, message flows, data objects and annotations are read
past; an inclusive gateway is read as an exclusive one with a warning; an
intermediate event is read as a pass-through, as a point on the flow.

Three translations connect BPMN to the models the miners produce:

* :func:`tree_to_bpmn` -- the block-structured mapping: a sequence is a
  chain, a choice an exclusive split and join, a parallel block a parallel
  split and join, a loop an exclusive join before the body and an exclusive
  split after it with a flow back, and a silent step τ is no node at all,
  just the flow.
* :func:`bpmn_to_petri_net` -- the mapping of Dijkman, Dumas & Ouyang,
  *Semantics and analysis of business process models in BPMN* (Information
  and Software Technology 50(12), 2008): a task is a transition, a flow a
  place, a parallel gateway a silent transition, an exclusive gateway routes
  a token (here: one place), a start event the source place, an end event
  the sink place.  The result is a WF-net with ``[source]`` and ``[sink]``
  as its markings, as everywhere else in the code.
* :func:`petri_net_to_bpmn` -- the way back for a WF-net: transitions are
  tasks, a transition with several input or output places gets parallel
  gateways, a place with several producers or consumers becomes exclusive
  gateways, and a silent transition with one input and one output is just
  a flow.  Exact for free-choice nets; see the function for the limitation.

The file format::

    <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" ...>
      <bpmn:process id="Process_1">
        <bpmn:startEvent id="StartEvent_1"/>
        <bpmn:task id="Task_1" name="register request"/>
        <bpmn:exclusiveGateway id="Gateway_1"/>
        <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1"/>
        ...
      </bpmn:process>
      <bpmndi:BPMNDiagram id="BPMNDiagram_1">
        <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
          <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1">
            <dc:Bounds x="200" y="100" width="100" height="80"/>
          </bpmndi:BPMNShape>
          <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
            <di:waypoint x="136" y="140"/><di:waypoint x="200" y="140"/>
          </bpmndi:BPMNEdge>
        </bpmndi:BPMNPlane>
      </bpmndi:BPMNDiagram>
    </bpmn:definitions>
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .layout import layered_layout
from .petrinet import Marking, PetriNet
from .processtree import Operator, ProcessTree, reduce_silent_transitions

MODEL_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
DI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DD_NS = "http://www.omg.org/spec/DD/20100524/DI"

#: The kinds of flow node.
START, END, TASK, EXCLUSIVE, PARALLEL = "start", "end", "task", "exclusive", "parallel"
GATEWAYS = (EXCLUSIVE, PARALLEL)
#: The symbol drawn in a gateway's diamond.
GATEWAY_SYMBOLS = {EXCLUSIVE: "×", PARALLEL: "+"}

#: Drawn sizes (width, height) by kind, the conventional ones.
SIZES = {START: (36.0, 36.0), END: (36.0, 36.0), TASK: (100.0, 80.0),
         EXCLUSIVE: (50.0, 50.0), PARALLEL: (50.0, 50.0)}

#: XML element names read as each kind of node.
_TASK_TAGS = {"task", "userTask", "serviceTask", "manualTask", "scriptTask", "businessRuleTask",
              "sendTask", "receiveTask", "callActivity", "subProcess", "adHocSubProcess", "transaction"}
_EVENT_TAGS = {"startEvent": START, "endEvent": END}
_GATEWAY_TAGS = {"exclusiveGateway": EXCLUSIVE, "parallelGateway": PARALLEL}
#: Gateways read as exclusive, with a warning: their semantics is not kept.
_LOOSE_GATEWAY_TAGS = {"inclusiveGateway": "inclusive", "eventBasedGateway": "event-based",
                       "complexGateway": "complex"}
_INTERMEDIATE_TAGS = {"intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class FlowNode:
    """A start or end event, a task or a gateway."""

    id: str
    kind: str                     # start | end | task | exclusive | parallel
    name: str = ""
    #: The centre of the shape in the diagram (drawing only).
    position: tuple[float, float] | None = None

    @property
    def is_gateway(self) -> bool:
        return self.kind in GATEWAYS

    @property
    def size(self) -> tuple[float, float]:
        return SIZES[self.kind]


@dataclass(eq=False)
class SequenceFlow:
    id: str
    source: str
    target: str
    name: str = ""
    #: Bend points from source to target (drawing only; the diagram's waypoints).
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass(eq=False)
class BPMN:
    """A BPMN process: flow nodes and the sequence flows between them."""

    name: str = "BPMN"
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    flows: list[SequenceFlow] = field(default_factory=list)
    #: Free-form provenance, e.g. ``{"source": "order.bpmn"}``.
    info: dict[str, str] = field(default_factory=dict)
    #: What the reader or a translation had to leave out or approximate.
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._ids: dict[str, itertools.count] = {}

    # -- construction -----------------------------------------------------
    def _fresh_id(self, prefix: str) -> str:
        counter = self._ids.setdefault(prefix, itertools.count(1))
        while True:
            candidate = f"{prefix}_{next(counter)}"
            if candidate not in self.nodes and all(f.id != candidate for f in self.flows):
                return candidate

    def add_node(self, kind: str, name: str = "", id: str | None = None,
                 position: tuple[float, float] | None = None) -> FlowNode:
        if kind not in SIZES:
            raise ValueError(f"Unknown kind of flow node: {kind!r}")
        prefix = {START: "StartEvent", END: "EndEvent", TASK: "Task"}.get(kind, "Gateway")
        node = FlowNode(id or self._fresh_id(prefix), kind, name, position)
        if node.id in self.nodes:
            raise ValueError(f"Duplicate node id {node.id!r}")
        self.nodes[node.id] = node
        return node

    def add_start(self, name: str = "", id: str | None = None) -> FlowNode:
        return self.add_node(START, name, id)

    def add_end(self, name: str = "", id: str | None = None) -> FlowNode:
        return self.add_node(END, name, id)

    def add_task(self, name: str, id: str | None = None) -> FlowNode:
        return self.add_node(TASK, name, id)

    def add_gateway(self, kind: str = EXCLUSIVE, name: str = "", id: str | None = None) -> FlowNode:
        if kind not in GATEWAYS:
            raise ValueError(f"A gateway is {EXCLUSIVE!r} or {PARALLEL!r}, not {kind!r}")
        return self.add_node(kind, name, id)

    def add_flow(self, source: FlowNode | str, target: FlowNode | str, name: str = "",
                 id: str | None = None) -> SequenceFlow:
        source_id = source if isinstance(source, str) else source.id
        target_id = target if isinstance(target, str) else target.id
        for end in (source_id, target_id):
            if end not in self.nodes:
                raise ValueError(f"No flow node {end!r}")
        flow = SequenceFlow(id or self._fresh_id("Flow"), source_id, target_id, name)
        self.flows.append(flow)
        return flow

    # -- queries ----------------------------------------------------------
    def of_kind(self, *kinds: str) -> list[FlowNode]:
        return [n for n in self.nodes.values() if n.kind in kinds]

    @property
    def starts(self) -> list[FlowNode]:
        return self.of_kind(START)

    @property
    def ends(self) -> list[FlowNode]:
        return self.of_kind(END)

    @property
    def tasks(self) -> list[FlowNode]:
        return self.of_kind(TASK)

    @property
    def gateways(self) -> list[FlowNode]:
        return self.of_kind(*GATEWAYS)

    def incoming(self, node_id: str) -> list[SequenceFlow]:
        return [f for f in self.flows if f.target == node_id]

    def outgoing(self, node_id: str) -> list[SequenceFlow]:
        return [f for f in self.flows if f.source == node_id]

    def labels(self) -> set[str]:
        """The task names."""
        return {t.name for t in self.tasks if t.name}

    def has_positions(self) -> bool:
        """Every node has a position: the diagram can be drawn as stored."""
        return bool(self.nodes) and all(n.position is not None for n in self.nodes.values())

    def summary(self) -> str:
        def count(number: int, noun: str) -> str:
            return f"{number} {noun}{'' if number == 1 else 's'}"
        return (f"{count(len(self.tasks), 'task')}, {count(len(self.gateways), 'gateway')}, "
                f"{count(len(self.flows), 'sequence flow')}")

    def __repr__(self) -> str:
        return f"<BPMN {self.name!r}: {self.summary()}>"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_bpmn(text: str | bytes) -> BPMN:
    """The process in a BPMN 2.0 XML document.

    Every ``<process>`` is read into the one model (a collaboration's pools
    are then side by side).  Sub-processes become single tasks.  An
    intermediate event is dropped and its flows spliced into one, so the
    flow passes through where the event was; a boundary event is dropped
    with the flows leaving it.  Inclusive, event-based and complex gateways
    become exclusive gateways, with a warning in :attr:`BPMN.warnings`.
    """
    root = ET.fromstring(text)
    if _local(root.tag) != "definitions":
        raise ValueError("No <definitions> element found; is this a BPMN 2.0 file?")
    processes = [e for e in root.iter() if _local(e.tag) == "process"]
    if not processes:
        raise ValueError("The file has no <process>; is this a BPMN 2.0 file?")

    name = next((p.get("name") for p in processes if p.get("name")), None) or root.get("id") or "BPMN"
    model = BPMN(name)
    flows: list[tuple[str, str, str, str]] = []            # (id, source, target, name)
    pass_through: dict[str, str] = {}                        # intermediate event id -> its name
    dropped: set[str] = set()                                # boundary events: no node, no flows
    for process in processes:
        for element in process.iter():
            tag, element_id = _local(element.tag), element.get("id")
            if element_id is None:
                continue
            label = element.get("name") or ""
            if tag in _EVENT_TAGS:
                model.add_node(_EVENT_TAGS[tag], label, id=element_id)
            elif tag in _TASK_TAGS:
                model.add_node(TASK, label or element_id, id=element_id)
                if tag in ("subProcess", "adHocSubProcess", "transaction"):
                    model.warnings.append(f"Sub-process {label or element_id!r} is read as one task; "
                                          "what is inside it is left out")
            elif tag in _GATEWAY_TAGS:
                model.add_node(_GATEWAY_TAGS[tag], label, id=element_id)
            elif tag in _LOOSE_GATEWAY_TAGS:
                model.add_node(EXCLUSIVE, label, id=element_id)
                model.warnings.append(f"The {_LOOSE_GATEWAY_TAGS[tag]} gateway {label or element_id!r} is read "
                                      "as an exclusive gateway")
            elif tag == "boundaryEvent":
                dropped.add(element_id)
                model.warnings.append(f"Boundary event {label or element_id!r} and the flows leaving it are left out")
            elif tag in _INTERMEDIATE_TAGS:
                pass_through[element_id] = label
            elif tag == "sequenceFlow":
                flows.append((element_id, element.get("sourceRef", ""), element.get("targetRef", ""), label))
    # Sub-process children are reached by iter() too: keep only nodes whose
    # flows connect known nodes, and drop the flows inside sub-processes.
    inside = {child.get("id") for process in processes for element in process.iter()
              if _local(element.tag) in ("subProcess", "adHocSubProcess", "transaction")
              for child in element.iter() if child is not element and child.get("id")}
    for element_id in inside:
        model.nodes.pop(element_id, None)
        pass_through.pop(element_id, None)
    flows = [f for f in flows if f[0] not in inside]

    # Splice intermediate events out: a → event → b becomes a → b.
    by_source: dict[str, list[tuple[str, str, str, str]]] = {}
    for flow in flows:
        by_source.setdefault(flow[1], []).append(flow)
    for event_id, label in pass_through.items():
        model.warnings.append(f"Intermediate event {label or event_id!r} is read as a point on the flow")
    kept: list[tuple[str, str, str, str]] = []
    for flow_id, source, target, label in flows:
        if source in pass_through or source in dropped:
            continue                                     # re-attached from its predecessor, or gone
        seen = set()
        while target in pass_through and target not in seen:
            seen.add(target)
            following = by_source.get(target, [])
            if not following:
                target = None
                break
            target = following[0][2]
            for extra in following[1:]:                  # an event with several outgoing flows: keep them all
                kept.append((extra[0], source, extra[2], extra[3]))
        if target is None or target in dropped:
            continue
        kept.append((flow_id, source, target, label))

    diagram = {_shape.get("bpmnElement"): _shape for _shape in root.iter() if _local(_shape.tag) == "BPMNShape"}
    for node in model.nodes.values():
        shape = diagram.get(node.id)
        bounds = next((b for b in shape.iter() if _local(b.tag) == "Bounds"), None) if shape is not None else None
        if bounds is not None:
            try:
                x, y = float(bounds.get("x", 0)), float(bounds.get("y", 0))
                w, h = float(bounds.get("width", 0)), float(bounds.get("height", 0))
                node.position = (x + w / 2, y + h / 2)
            except ValueError:
                pass
    edges = {_edge.get("bpmnElement"): _edge for _edge in root.iter() if _local(_edge.tag) == "BPMNEdge"}
    for flow_id, source, target, label in kept:
        if source not in model.nodes or target not in model.nodes:
            model.warnings.append(f"Sequence flow {flow_id!r} connects something that is not a flow node; left out")
            continue
        flow = model.add_flow(source, target, label, id=flow_id)
        edge = edges.get(flow_id)
        if edge is not None:
            for point in edge:
                if _local(point.tag) == "waypoint":
                    try:
                        flow.points.append((float(point.get("x", 0)), float(point.get("y", 0))))
                    except ValueError:
                        pass
    return model


def read_bpmn(path: str | Path) -> BPMN:
    model = parse_bpmn(Path(path).read_bytes())
    model.info.setdefault("source", str(path))
    if model.name == "BPMN":
        model.name = Path(path).stem
    return model


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
_TAGS = {START: "startEvent", END: "endEvent", TASK: "task", EXCLUSIVE: "exclusiveGateway",
         PARALLEL: "parallelGateway"}


def _xml_ids(model: BPMN) -> dict[str, str]:
    """Each node's and flow's id as a valid XML id (an NCName), kept when it is one."""
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for original in [*model.nodes, *(f.id for f in model.flows)]:
        candidate = re.sub(r"[^A-Za-z0-9_.\-]", "_", original) or "id"
        if not re.match(r"[A-Za-z_]", candidate):
            candidate = "id_" + candidate
        unique, number = candidate, 2
        while unique in taken:
            unique = f"{candidate}_{number}"
            number += 1
        taken.add(unique)
        mapping[original] = unique
    return mapping


def _boundary(node: FlowNode, centre: tuple[float, float], towards: tuple[float, float]) -> tuple[float, float]:
    """Where a line from the node's centre towards ``towards`` leaves its outline."""
    (cx, cy), (w, h) = centre, node.size
    dx, dy = towards[0] - cx, towards[1] - cy
    if dx == 0 and dy == 0:
        return centre
    if node.kind in (START, END):
        length = (dx * dx + dy * dy) ** 0.5
        return cx + dx / length * w / 2, cy + dy / length * h / 2
    if node.is_gateway:                                  # a diamond: |dx|/hw + |dy|/hh = 1
        scale = 1 / (abs(dx) / (w / 2) + abs(dy) / (h / 2))
        return cx + dx * scale, cy + dy * scale
    scale = min((w / 2) / abs(dx) if dx else float("inf"), (h / 2) / abs(dy) if dy else float("inf"))
    return cx + dx * scale, cy + dy * scale


def layout_positions(model: BPMN, margin: float = 60.0
                     ) -> tuple[dict[str, tuple[float, float]], dict[int, list[tuple[float, float]]]]:
    """Centres for every node and a route per flow index, from the layered
    layout (left to right), with ``margin`` from the top-left corner."""
    layout = layered_layout({n.id: n.size for n in model.nodes.values()},
                            [(f.source, f.target) for f in model.flows], layer_gap=80.0, node_gap=40.0)
    positions = {k: (x + margin, y + margin) for k, (x, y) in layout.positions.items()}
    routes = {i: [(x + margin, y + margin) for x, y in route] for i, route in layout.routes.items()}
    return positions, routes


def bpmn_string(model: BPMN) -> str:
    """Serialise ``model`` as BPMN 2.0 XML with a diagram, so Camunda Modeler
    and bpmn.io open it.  Stored positions are used when every node has one;
    otherwise the layered layout places everything."""
    ids = _xml_ids(model)
    if model.has_positions():
        positions = {n.id: n.position for n in model.nodes.values()}
        routes: dict[int, list[tuple[float, float]]] = {}
    else:
        positions, routes = layout_positions(model)

    def attribute(name: str, value: str) -> str:
        return f" {name}={quoteattr(value)}" if value else ""

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<bpmn:definitions xmlns:bpmn="{MODEL_NS}" xmlns:bpmndi="{DI_NS}" xmlns:dc="{DC_NS}" '
             f'xmlns:di="{DD_NS}" id="Definitions_1" targetNamespace="http://openprocess.app/bpmn" '
             'exporter="OpenProcess">',
             f'  <bpmn:process id="Process_1"{attribute("name", model.name)} isExecutable="false">']
    for node in model.nodes.values():
        lines.append(f'    <bpmn:{_TAGS[node.kind]} id="{ids[node.id]}"{attribute("name", node.name)}>')
        for flow in model.incoming(node.id):
            lines.append(f"      <bpmn:incoming>{ids[flow.id]}</bpmn:incoming>")
        for flow in model.outgoing(node.id):
            lines.append(f"      <bpmn:outgoing>{ids[flow.id]}</bpmn:outgoing>")
        lines.append(f"    </bpmn:{_TAGS[node.kind]}>")
    for flow in model.flows:
        lines.append(f'    <bpmn:sequenceFlow id="{ids[flow.id]}"{attribute("name", flow.name)} '
                     f'sourceRef="{ids[flow.source]}" targetRef="{ids[flow.target]}"/>')
    lines += ["  </bpmn:process>", '  <bpmndi:BPMNDiagram id="BPMNDiagram_1">',
              '    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">']
    for node in model.nodes.values():
        (x, y), (w, h) = positions[node.id], node.size
        marker = ' isMarkerVisible="true"' if node.kind == EXCLUSIVE else ""
        lines.append(f'      <bpmndi:BPMNShape id="{ids[node.id]}_di" bpmnElement="{ids[node.id]}"{marker}>')
        lines.append(f'        <dc:Bounds x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.0f}" height="{h:.0f}"/>')
        if node.name and node.kind != TASK:              # a task's name sits inside; the others' underneath
            lines.append(f'        <bpmndi:BPMNLabel><dc:Bounds x="{x - 45:.1f}" y="{y + h / 2 + 4:.1f}" '
                         'width="90" height="14"/></bpmndi:BPMNLabel>')
        lines.append("      </bpmndi:BPMNShape>")
    for index, flow in enumerate(model.flows):
        source, target = model.nodes[flow.source], model.nodes[flow.target]
        if model.has_positions() and len(flow.points) >= 2:
            points = list(flow.points)
        else:
            route = routes.get(index) or [positions[flow.source], positions[flow.target]]
            inner = route[1:-1]
            first = _boundary(source, positions[flow.source], inner[0] if inner else positions[flow.target])
            last = _boundary(target, positions[flow.target], inner[-1] if inner else positions[flow.source])
            points = [first, *inner, last]
        lines.append(f'      <bpmndi:BPMNEdge id="{ids[flow.id]}_di" bpmnElement="{ids[flow.id]}">')
        lines += [f'        <di:waypoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in points]
        lines.append("      </bpmndi:BPMNEdge>")
    lines += ["    </bpmndi:BPMNPlane>", "  </bpmndi:BPMNDiagram>", "</bpmn:definitions>", ""]
    return "\n".join(lines)


def write_bpmn(model: BPMN, path: str | Path) -> None:
    Path(path).write_text(bpmn_string(model), encoding="utf-8")


# ---------------------------------------------------------------------------
# Process tree -> BPMN
# ---------------------------------------------------------------------------
def tree_to_bpmn(tree: ProcessTree, name: str = "Process tree") -> BPMN:
    """The block-structured translation of a process tree.

    Every block is a fragment with a first and a last node, built so that
    the flow into the fragment enters at the first node and leaves from the
    last:

    * leaf ``a``:        one task;
    * τ:                 no node -- an empty fragment, which becomes a plain
                         sequence flow wherever it sits;
    * ``→(C1..Cn)``:     the children chained, each one's last node to the
                         next one's first;
    * ``×(C1..Cn)``:     an exclusive split gateway, every child between it
                         and an exclusive join gateway;
    * ``∧(C1..Cn)``:     the same with parallel gateways;
    * ``↺(do, r1..rn)``: an exclusive join *enter* before ``do``, an exclusive
                         split *leave* after it, and each redo from *leave*
                         back to *enter* (a τ redo is a flow straight back).

    A start event feeds the root fragment and its last node feeds an end
    event.  Firing the model's Petri net (:func:`bpmn_to_petri_net`) then
    allows exactly the traces of :func:`~.processtree.to_petri_net`.
    """
    model = BPMN(name)
    model.info["algorithm"] = "Process tree"

    def flow(source: str, target: str) -> None:
        if not any(f.source == source and f.target == target for f in model.flows):
            model.add_flow(source, target)

    def build(node: ProcessTree) -> tuple[str, str] | None:
        """(first, last) of the fragment, or None for an empty one."""
        if node.is_leaf:
            if node.label is None:
                return None
            task = model.add_task(node.label)
            return task.id, task.id
        if node.operator is Operator.SEQUENCE:
            fragments = [f for f in (build(child) for child in node.children) if f is not None]
            if not fragments:
                return None
            for (_first, last), (first, _last) in zip(fragments, fragments[1:]):
                flow(last, first)
            return fragments[0][0], fragments[-1][1]
        if node.operator in (Operator.XOR, Operator.PARALLEL):
            if len(node.children) == 1:
                return build(node.children[0])
            kind = EXCLUSIVE if node.operator is Operator.XOR else PARALLEL
            split, join = model.add_gateway(kind), model.add_gateway(kind)
            for child in node.children:
                fragment = build(child)
                if fragment is None:
                    flow(split.id, join.id)
                else:
                    flow(split.id, fragment[0])
                    flow(fragment[1], join.id)
            return split.id, join.id
        if node.operator is Operator.LOOP:
            if len(node.children) == 1:
                return build(node.children[0])
            enter, leave = model.add_gateway(EXCLUSIVE), model.add_gateway(EXCLUSIVE)
            body = build(node.children[0])
            if body is None:
                flow(enter.id, leave.id)
            else:
                flow(enter.id, body[0])
                flow(body[1], leave.id)
            for redo in node.children[1:]:
                fragment = build(redo)
                if fragment is None:
                    flow(leave.id, enter.id)
                else:
                    flow(leave.id, fragment[0])
                    flow(fragment[1], enter.id)
            return enter.id, leave.id
        raise ValueError(f"Unknown operator {node.operator!r}")

    start, end = model.add_start(), model.add_end()
    root = build(tree)
    if root is None:
        flow(start.id, end.id)
    else:
        flow(start.id, root[0])
        flow(root[1], end.id)
    return model


# ---------------------------------------------------------------------------
# BPMN -> Petri net
# ---------------------------------------------------------------------------
def bpmn_to_petri_net(model: BPMN, name: str | None = None) -> PetriNet:
    """The Petri net semantics of the model.

    The mapping is the one of Dijkman, Dumas & Ouyang (2008), *Semantics and
    analysis of business process models in BPMN*, Information and Software
    Technology 50(12):1281–1294, for the control-flow core:

    * a **sequence flow** is a place, holding the token that travels along it;
    * a **task** is a transition labelled with its name, from the place of its
      incoming flow to the place(s) of its outgoing flows (several outgoing
      flows are an implicit parallel split, as the standard says; several
      incoming flows an implicit exclusive join: a silent transition per flow
      into one place before the task);
    * a **parallel gateway** is a silent transition consuming from every
      incoming flow and producing to every outgoing one;
    * an **exclusive gateway** routes one token from one of its incoming flows
      to one of its outgoing flows.  Dijkman et al. write one silent
      transition per (in, out) pair; here the gateway is one place that its
      incoming and outgoing flows share, which allows the same complete
      traces with fewer silent steps (the choice is made when a consumer
      fires, not before).  A flow between two exclusive gateways keeps a
      silent transition between the two places;
    * a **start event** is the source place with a silent transition onto its
      outgoing flow; an **end event** a silent transition from each incoming
      flow into the sink place.  Several start events share the source
      (either may start a case), several end events share the sink.

    Afterwards silent transitions in series are fused away
    (:func:`~.processtree.reduce_silent_transitions`).  The result has
    ``[source]`` and ``[sink]`` as its initial and final marking; with one
    start event and one end event reached by every path it is a WF-net.
    """
    net = PetriNet(name or model.name)
    source = net.add_place("source", id="source")
    sink = net.add_place("sink", id="sink")
    gateway_place = {g.id: net.add_place(g.name or g.id, id=_free(net, g.id)) for g in model.of_kind(EXCLUSIVE)}
    for gateway in model.of_kind(EXCLUSIVE):
        gateway_place[gateway.id].position = gateway.position

    place_of_flow: dict[str, str] = {}
    for flow in model.flows:
        before, after = model.nodes[flow.source], model.nodes[flow.target]
        if before.kind == EXCLUSIVE and after.kind == EXCLUSIVE:
            if before.id != after.id:
                between = net.add_transition(None, name="τ")
                net.add_arc(gateway_place[before.id], between)
                net.add_arc(between, gateway_place[after.id])
            else:
                model.warnings.append(f"Gateway {before.id!r} flows into itself; left out")
        elif before.kind == EXCLUSIVE:
            place_of_flow[flow.id] = gateway_place[before.id].id
        elif after.kind == EXCLUSIVE:
            place_of_flow[flow.id] = gateway_place[after.id].id
        else:
            place = net.add_place(flow.name or flow.id, id=_free(net, flow.id))
            if before.position is not None and after.position is not None:
                place.position = ((before.position[0] + after.position[0]) / 2,
                                  (before.position[1] + after.position[1]) / 2)
            place_of_flow[flow.id] = place.id

    for node in model.nodes.values():
        incoming = [place_of_flow[f.id] for f in model.incoming(node.id) if f.id in place_of_flow]
        outgoing = [place_of_flow[f.id] for f in model.outgoing(node.id) if f.id in place_of_flow]
        if node.kind == EXCLUSIVE:
            continue
        if node.kind == START:
            if len(model.starts) == 1:
                source.position = node.position
            enter = net.add_transition(None, name="τ start")
            net.add_arc(source, enter)
            for place in outgoing:
                net.add_arc(enter, place)
        elif node.kind == END:
            if len(model.ends) == 1:
                sink.position = node.position
            for place in incoming:
                leave = net.add_transition(None, name="τ end")
                net.add_arc(place, leave)
                net.add_arc(leave, sink)
        elif node.kind == PARALLEL:
            transition = net.add_transition(None, name="τ " + (node.name or node.id))
            transition.position = node.position
            for place in incoming:
                net.add_arc(place, transition)
            for place in outgoing:
                net.add_arc(transition, place)
        else:                                            # a task
            transition = net.add_transition(node.name or node.id, id=_free(net, node.id))
            transition.position = node.position
            if len(incoming) > 1:                        # implicit exclusive join
                before = net.add_place()
                for place in incoming:
                    merge = net.add_transition(None, name="τ")
                    net.add_arc(place, merge)
                    net.add_arc(merge, before)
                incoming = [before.id]
            for place in incoming:
                net.add_arc(place, transition)
            for place in outgoing:
                net.add_arc(transition, place)

    net.initial_marking = Marking({source.id: 1})
    net.final_marking = Marking({sink.id: 1})
    reduce_silent_transitions(net)
    net.info["algorithm"] = "BPMN"
    if any(t.position is None for t in net.transitions.values()) or \
            any(p.position is None for p in net.places.values()):
        for element in [*net.places.values(), *net.transitions.values()]:
            element.position = None                      # all or nothing: the canvas lays out
    return net


def _free(net: PetriNet, wanted: str) -> str | None:
    """``wanted`` as a node id when it is still free, else a fresh one."""
    return wanted if wanted not in net.places and wanted not in net.transitions else None


# ---------------------------------------------------------------------------
# Petri net -> BPMN
# ---------------------------------------------------------------------------
def petri_net_to_bpmn(net: PetriNet, name: str | None = None) -> BPMN:
    """A BPMN model of a WF-net, the inverse of :func:`bpmn_to_petri_net`.

    * the source place is the start event, the sink place the end event;
    * a labelled transition is a task; when it has several input places a
      parallel join gateway stands before it, when it has several output
      places a parallel split gateway after it (a task with several incoming
      flows would be an *exclusive* join in BPMN, so the gateways are
      explicit);
    * a silent transition with several inputs or outputs is a parallel
      gateway (join, split, or a join followed by a split); one with a single
      input and a single output is dropped: the sequence flow itself is the
      silent step;
    * a place with several producers becomes an exclusive join gateway, one
      with several consumers an exclusive split gateway (both when it has
      both); a place with one producer and one consumer is just the flow
      between them.

    **Limitation.**  The mapping keeps the behaviour of *free-choice* nets,
    where every choice is a place deciding between its consumers on its own
    (what the Inductive Miner and the translation of a process tree produce).
    In a non-free-choice net a transition's enabling depends on several
    places at once; drawn as an exclusive split followed by a parallel join,
    the BPMN model may choose a branch the net would never have taken and
    get stuck there.  Arc weights above one have no BPMN counterpart and are
    refused.  Place names are not kept: gateways are unnamed.
    """
    from .analysis import check_workflow_net
    workflow = check_workflow_net(net)
    if not workflow.is_workflow_net:
        raise ValueError("Only a WF-net translates to BPMN: " + "; ".join(workflow.problems))
    if any(a.weight != 1 for a in net.arcs):
        raise ValueError("Arc weights above one have no counterpart in BPMN")
    model = BPMN(name or net.name)
    model.info["algorithm"] = "Petri net"
    if net.info.get("algorithm"):
        model.info["net"] = net.info["algorithm"]

    #: Where a flow into / out of each net node attaches: (entry node, exit node) in the model.
    entry: dict[str, str | None] = {}
    exit_: dict[str, str | None] = {}

    for transition in net.transitions.values():
        fan_in, fan_out = len(net.preset(transition.id)), len(net.postset(transition.id))
        chain: list[FlowNode] = []
        if fan_in > 1:
            chain.append(model.add_gateway(PARALLEL))
        if not transition.silent:
            task = model.add_task(transition.label, id=_free_in(model, transition.id))
            task.position = transition.position
            chain.append(task)
        if fan_out > 1:
            chain.append(model.add_gateway(PARALLEL))
        if transition.silent and len(chain) == 1:
            chain[0].position = transition.position
        for a, b in zip(chain, chain[1:]):
            model.add_flow(a, b)
        # A silent transition with one input and one output: nothing; the
        # flow from its input place runs straight to its output place.
        entry[transition.id] = chain[0].id if chain else None
        exit_[transition.id] = chain[-1].id if chain else None

    for place in net.places.values():
        producers, consumers = len(net.preset(place.id)), len(net.postset(place.id))
        chain = []
        if place.id == workflow.source:
            chain.append(model.add_start(id=_free_in(model, place.id)))
        if producers > 1:
            chain.append(model.add_gateway(EXCLUSIVE))
        if consumers > 1:
            chain.append(model.add_gateway(EXCLUSIVE))
        if place.id == workflow.sink:
            chain.append(model.add_end(id=_free_in(model, place.id)))
        if len(chain) == 1:
            chain[0].position = place.position
        for a, b in zip(chain, chain[1:]):
            model.add_flow(a, b)
        entry[place.id] = chain[0].id if chain else None
        exit_[place.id] = chain[-1].id if chain else None

    # Every arc is a flow from the exit of its source to the entry of its
    # target -- looking through nodes that became nothing (a plain place
    # between two transitions, a silent transition in series).
    def node_after(place_id: str) -> str | None:
        """The model node a token in ``place_id`` moves to, through dropped nodes."""
        if entry[place_id] is not None:
            return entry[place_id]
        (consumer,) = net.postset(place_id)
        return node_after_transition(consumer)

    def node_after_transition(transition_id: str) -> str | None:
        if entry[transition_id] is not None:
            return entry[transition_id]
        (following,) = net.postset(transition_id)
        return node_after(following)

    def node_before(place_id: str) -> str | None:
        if exit_[place_id] is not None:
            return exit_[place_id]
        (producer,) = net.preset(place_id)
        return node_before_transition(producer)

    def node_before_transition(transition_id: str) -> str | None:
        if exit_[transition_id] is not None:
            return exit_[transition_id]
        (preceding,) = net.preset(transition_id)
        return node_before(preceding)

    for arc in net.arcs:
        source_node, target_node = exit_[arc.source], entry[arc.target]
        if source_node is not None and target_node is not None:
            _flow_once(model, source_node, target_node)
    # Arcs touching a dropped node: connect what is before it to what is after it.
    for place in net.places.values():
        if entry[place.id] is None:
            before = node_before(place.id)
            after = node_after(place.id)
            if before is not None and after is not None:
                _flow_once(model, before, after)
    for transition in net.transitions.values():
        if entry[transition.id] is None:
            before = node_before_transition(transition.id)
            after = node_after_transition(transition.id)
            if before is not None and after is not None:
                _flow_once(model, before, after)
    if not model.has_positions():
        for node in model.nodes.values():
            node.position = None
    return model


def _free_in(model: BPMN, wanted: str) -> str | None:
    return wanted if wanted not in model.nodes else None


def _flow_once(model: BPMN, source: str, target: str) -> None:
    if not any(f.source == source and f.target == target for f in model.flows):
        model.add_flow(source, target)


__all__ = ["BPMN", "FlowNode", "SequenceFlow", "START", "END", "TASK", "EXCLUSIVE", "PARALLEL",
           "GATEWAY_SYMBOLS", "SIZES", "parse_bpmn", "read_bpmn", "bpmn_string", "write_bpmn",
           "layout_positions", "tree_to_bpmn", "bpmn_to_petri_net", "petri_net_to_bpmn"]
