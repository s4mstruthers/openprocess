"""BPMN: reading and writing BPMN 2.0 XML, the translations between process
trees, BPMN and Petri nets (they keep the language), the boxes, and the
viewer drawing a model offscreen.

Expected behaviour comes from the mapping of Dijkman, Dumas & Ouyang (2008)
for BPMN → Petri nets, and from :func:`openprocess.mining.processtree.to_petri_net`
for the tree: a tree translated through BPMN must allow exactly the traces
its direct translation allows.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openprocess.mining import (
    Marking, PetriNet, check_soundness, compare_nets, inductive_miner, parse_simple_log, to_petri_net,
    token_replay,
)
from openprocess.mining.bpmn import (
    BPMN, END, EXCLUSIVE, PARALLEL, START, TASK, bpmn_string, bpmn_to_petri_net, parse_bpmn,
    petri_net_to_bpmn, read_bpmn, tree_to_bpmn, write_bpmn,
)
from openprocess.mining.processtree import Operator, ProcessTree

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"
L3 = "[<a,b,c,d,e,f,b,d,c,e,g>, <a,b,d,c,e,g>^2, <a,b,c,d,e,f,b,c,d,e,f,b,d,c,e,g>]"

#: A model as Camunda Modeler writes it: a pool with a lane, an inclusive
#: gateway, an intermediate event on a flow, a message flow and a data object.
SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_1" name="Order handling" processRef="Process_1"/>
    <bpmn:participant id="Participant_2" name="Customer"/>
    <bpmn:messageFlow id="MessageFlow_1" sourceRef="Participant_2" targetRef="Task_register"/>
  </bpmn:collaboration>
  <bpmn:process id="Process_1" name="Order handling" isExecutable="false">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_1" name="Sales">
        <bpmn:flowNodeRef>StartEvent_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_register</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="StartEvent_1" name="order received">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Task_register" name="register order"/>
    <bpmn:parallelGateway id="Gateway_split"/>
    <bpmn:serviceTask id="Task_check" name="check credit"/>
    <bpmn:task id="Task_stock" name="check stock"/>
    <bpmn:parallelGateway id="Gateway_join"/>
    <bpmn:inclusiveGateway id="Gateway_decide" name="decide"/>
    <bpmn:task id="Task_ship" name="ship"/>
    <bpmn:intermediateThrowEvent id="Event_notify" name="notify"/>
    <bpmn:task id="Task_reject" name="reject"/>
    <bpmn:exclusiveGateway id="Gateway_merge"/>
    <bpmn:endEvent id="EndEvent_1" name="done"/>
    <bpmn:dataObjectReference id="DataObjectReference_1" dataObjectRef="DataObject_1"/>
    <bpmn:dataObject id="DataObject_1"/>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_register"/>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_register" targetRef="Gateway_split"/>
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Gateway_split" targetRef="Task_check"/>
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Gateway_split" targetRef="Task_stock"/>
    <bpmn:sequenceFlow id="Flow_5" sourceRef="Task_check" targetRef="Gateway_join"/>
    <bpmn:sequenceFlow id="Flow_6" sourceRef="Task_stock" targetRef="Gateway_join"/>
    <bpmn:sequenceFlow id="Flow_7" sourceRef="Gateway_join" targetRef="Gateway_decide"/>
    <bpmn:sequenceFlow id="Flow_8" name="ok" sourceRef="Gateway_decide" targetRef="Task_ship"/>
    <bpmn:sequenceFlow id="Flow_9" sourceRef="Task_ship" targetRef="Event_notify"/>
    <bpmn:sequenceFlow id="Flow_10" sourceRef="Event_notify" targetRef="Gateway_merge"/>
    <bpmn:sequenceFlow id="Flow_11" name="not ok" sourceRef="Gateway_decide" targetRef="Task_reject"/>
    <bpmn:sequenceFlow id="Flow_12" sourceRef="Task_reject" targetRef="Gateway_merge"/>
    <bpmn:sequenceFlow id="Flow_13" sourceRef="Gateway_merge" targetRef="EndEvent_1"/>
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <dc:Bounds x="152" y="182" width="36" height="36"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_register_di" bpmnElement="Task_register">
        <dc:Bounds x="240" y="160" width="100" height="80"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="188" y="200"/><di:waypoint x="240" y="200"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


def l1_tree() -> ProcessTree:
    return inductive_miner(parse_simple_log(L1)).tree


# ---------------------------------------------------------------------------
# The model and the file format
# ---------------------------------------------------------------------------
def test_sample_parses_into_the_control_flow_core():
    model = parse_bpmn(SAMPLE)
    assert model.name == "Order handling"
    assert {t.name for t in model.tasks} == {"register order", "check credit", "check stock", "ship", "reject"}
    assert [n.kind for n in model.starts] == [START] and [n.kind for n in model.ends] == [END]
    kinds = {g.id: g.kind for g in model.gateways}
    assert kinds == {"Gateway_split": PARALLEL, "Gateway_join": PARALLEL,
                     "Gateway_decide": EXCLUSIVE, "Gateway_merge": EXCLUSIVE}
    # The intermediate event is a point on the flow: ship goes straight to the merge.
    assert "Event_notify" not in model.nodes
    assert [(f.source, f.target) for f in model.outgoing("Task_ship")] == [("Task_ship", "Gateway_merge")]
    assert len(model.flows) == 12                                    # 13 in the file, two spliced into one
    assert any("inclusive gateway" in w for w in model.warnings)
    assert any("Intermediate event" in w for w in model.warnings)
    # Positions come from the diagram's bounds, as centres; not every node has one here.
    assert model.nodes["StartEvent_1"].position == (170.0, 200.0)
    assert model.nodes["Task_register"].position == (290.0, 200.0)
    assert not model.has_positions()
    assert model.incoming("Task_register")[0].points == [(188.0, 200.0), (240.0, 200.0)]
    assert model.summary() == "5 tasks, 4 gateways, 12 sequence flows"


def test_not_bpmn_is_refused():
    with pytest.raises(ValueError, match="BPMN"):
        parse_bpmn("<pnml><net id='n'/></pnml>")
    with pytest.raises(ValueError, match="process"):
        parse_bpmn('<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"/>')


def test_round_trip_keeps_nodes_flows_names_and_positions(tmp_path):
    model = parse_bpmn(SAMPLE)
    text = bpmn_string(model)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"' in text
    assert "<bpmndi:BPMNDiagram" in text and "<di:waypoint" in text and 'isMarkerVisible="true"' in text
    again = parse_bpmn(text)
    assert set(again.nodes) == set(model.nodes)
    assert {(n.id, n.kind, n.name) for n in again.nodes.values()} == {(n.id, n.kind, n.name) for n in model.nodes.values()}
    assert {(f.id, f.source, f.target, f.name) for f in again.flows} == {(f.id, f.source, f.target, f.name) for f in model.flows}
    assert again.has_positions()                           # the writer laid out what had no position
    assert again.name == model.name and not again.warnings
    # A model with every position stored writes them back unchanged.
    once_more = parse_bpmn(bpmn_string(again))
    assert {k: n.position for k, n in once_more.nodes.items()} == {k: n.position for k, n in again.nodes.items()}

    path = tmp_path / "order.bpmn"
    write_bpmn(model, path)
    read = read_bpmn(path)
    assert read.info["source"] == str(path) and len(read.flows) == 12


def test_ids_are_made_valid_xml_ids_and_names_escaped():
    model = BPMN("odd")
    start = model.add_start(id="1 start")
    task = model.add_task('check <"credit"> & more', id="t 1")
    end = model.add_end(id="t_1")                           # what the task's id sanitises to
    model.add_flow(start, task)
    model.add_flow(task, end)
    text = bpmn_string(model)
    assert 'id="id_1_start"' in text and 'id="t_1"' in text and 'id="t_1_2"' in text
    again = parse_bpmn(text)
    assert [t.name for t in again.tasks] == ['check <"credit"> & more']


# ---------------------------------------------------------------------------
# Process tree -> BPMN -> Petri net
# ---------------------------------------------------------------------------
def test_tree_to_bpmn_is_block_structured():
    tree = ProcessTree.node(Operator.SEQUENCE, [
        ProcessTree.leaf("a"),
        ProcessTree.node(Operator.XOR, [
            ProcessTree.node(Operator.PARALLEL, [ProcessTree.leaf("b"), ProcessTree.leaf("c")]),
            ProcessTree.leaf("e")]),
        ProcessTree.leaf("d")])
    model = tree_to_bpmn(tree)
    assert [t.name for t in model.tasks] == ["a", "b", "c", "e", "d"]
    assert [g.kind for g in model.gateways] == [EXCLUSIVE, EXCLUSIVE, PARALLEL, PARALLEL]
    assert len(model.starts) == 1 and len(model.ends) == 1
    (a,) = [t for t in model.tasks if t.name == "a"]
    (split,) = [model.nodes[f.target] for f in model.outgoing(a.id)]
    assert split.kind == EXCLUSIVE and len(model.outgoing(split.id)) == 2
    assert model.info["algorithm"] == "Process tree"


def test_tau_leaves_become_plain_flows():
    # ×(a, τ): the skip is a flow from the split straight to the join.
    tree = ProcessTree.node(Operator.XOR, [ProcessTree.leaf("a"), ProcessTree.tau()])
    model = tree_to_bpmn(tree)
    assert [t.name for t in model.tasks] == ["a"]
    split, join = model.gateways
    assert any(f.source == split.id and f.target == join.id for f in model.flows)
    # ↺(a, τ): a flow back from the leaving gateway to the entering one.
    loop = tree_to_bpmn(ProcessTree.node(Operator.LOOP, [ProcessTree.leaf("a"), ProcessTree.tau()]))
    enter, leave = loop.gateways
    assert any(f.source == leave.id and f.target == enter.id for f in loop.flows)
    # A tree that is only τ: start straight to end.
    empty = tree_to_bpmn(ProcessTree.tau())
    assert not empty.tasks and [(f.source, f.target) for f in empty.flows] == [("StartEvent_1", "EndEvent_1")]
    assert bpmn_to_petri_net(empty).labels() == set()


@pytest.mark.parametrize("log", [L1, L3])
def test_tree_through_bpmn_has_the_language_of_the_tree(log):
    tree = inductive_miner(parse_simple_log(log)).tree
    direct = to_petri_net(tree)
    through = bpmn_to_petri_net(tree_to_bpmn(tree))
    assert through.initial_marking == Marking({"source": 1}) and through.final_marking == Marking({"sink": 1})
    assert check_soundness(through).sound
    comparison = compare_nets(through, direct)
    assert comparison.exact and comparison.equivalent, (comparison.only_first, comparison.only_second)
    assert token_replay(through, parse_simple_log(log)).fitness == pytest.approx(1.0)
    # And the way back through a file, too.
    reread = parse_bpmn(bpmn_string(tree_to_bpmn(tree)))
    assert compare_nets(bpmn_to_petri_net(reread), direct).equivalent


def test_sample_converts_to_a_sound_wf_net():
    net = bpmn_to_petri_net(parse_bpmn(SAMPLE))
    assert net.labels() == {"register order", "check credit", "check stock", "ship", "reject"}
    report = check_soundness(net)
    assert report.workflow.is_workflow_net and report.sound, report.findings
    simple = parse_simple_log("[<register order, check credit, check stock, ship>, "
                              "<register order, check stock, check credit, reject>]")
    assert token_replay(net, simple).fitness == pytest.approx(1.0)
    assert token_replay(net, parse_simple_log("[<register order, ship>]")).fitness < 1.0
    # The parallel gateways are silent transitions, the exclusive ones places.
    silent = [t for t in net.transitions.values() if t.silent]
    assert len(silent) >= 2 and net.info["algorithm"] == "BPMN"


def test_implicit_joins_and_splits_on_tasks():
    """Several flows into a task are an exclusive join; several out of it a parallel split."""
    model = BPMN("implicit")
    start, end = model.add_start(), model.add_end()
    a, b, c, d = (model.add_task(n) for n in "abcd")
    model.add_flow(start, a)
    model.add_flow(a, b)
    model.add_flow(a, c)                                  # a: implicit parallel split
    model.add_flow(b, d)
    model.add_flow(c, d)                                  # d: implicit exclusive join
    model.add_flow(d, end)
    net = bpmn_to_petri_net(model)
    # Both b and c run (a split in parallel), and d fires once per flow that
    # reaches it (an exclusive join): nothing is ever missing for <a,b,c,d,d>...
    twice = token_replay(net, parse_simple_log("[<a,b,c,d,d>, <a,c,b,d,d>]"))
    assert twice.missing == 0
    # ... and the second completion leaves a second token in the sink, as the
    # BPMN semantics says (the model completes twice), so it is not a WF-net run.
    assert twice.remaining > 0 and token_replay(net, parse_simple_log("[<a,b,c,d>]")).remaining > 0
    assert token_replay(net, parse_simple_log("[<a,b,d,d>]")).missing > 0     # c cannot be skipped


# ---------------------------------------------------------------------------
# Petri net -> BPMN
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("log", [L1, L3])
def test_wf_net_to_bpmn_and_back_keeps_the_language(log):
    net = inductive_miner(parse_simple_log(log)).net
    model = petri_net_to_bpmn(net)
    assert sorted(model.labels()) == sorted(net.labels())
    assert len(model.starts) == 1 and len(model.ends) == 1
    again = bpmn_to_petri_net(model)
    comparison = compare_nets(again, net)
    assert comparison.exact and comparison.equivalent, (comparison.only_first, comparison.only_second)
    assert check_soundness(again).sound


def test_net_to_bpmn_uses_gateways_where_the_net_splits_and_joins():
    tree = ProcessTree.node(Operator.SEQUENCE, [
        ProcessTree.leaf("a"),
        ProcessTree.node(Operator.PARALLEL, [ProcessTree.leaf("b"), ProcessTree.leaf("c")]),
        ProcessTree.node(Operator.XOR, [ProcessTree.leaf("d"), ProcessTree.leaf("e")])])
    model = petri_net_to_bpmn(to_petri_net(tree))
    kinds = sorted(g.kind for g in model.gateways)
    assert kinds == [EXCLUSIVE, EXCLUSIVE, PARALLEL, PARALLEL]
    # A labelled transition with two output places gets its parallel split after it.
    hand = PetriNet("split")
    i, p1, p2, o = (hand.add_place(n, id=n) for n in ("i", "p1", "p2", "o"))
    a, j = hand.add_transition("a", id="a"), hand.add_transition(None, id="j")
    hand.add_arc(i, a); hand.add_arc(a, p1); hand.add_arc(a, p2); hand.add_arc(p1, j); hand.add_arc(p2, j); hand.add_arc(j, o)
    hand.initial_marking, hand.final_marking = Marking({"i": 1}), Marking({"o": 1})
    model = petri_net_to_bpmn(hand)
    (task,) = model.tasks
    assert task.name == "a" and model.nodes[model.outgoing(task.id)[0].target].kind == PARALLEL
    assert compare_nets(bpmn_to_petri_net(model), hand).equivalent


def test_only_wf_nets_without_weights_translate():
    net = PetriNet("two sources")
    p, q, r = net.add_place("p"), net.add_place("q"), net.add_place("r")
    t = net.add_transition("a")
    net.add_arc(p, t); net.add_arc(q, t); net.add_arc(t, r)
    with pytest.raises(ValueError, match="WF-net"):
        petri_net_to_bpmn(net)
    weighted = PetriNet("weighted")
    i, o = weighted.add_place("i"), weighted.add_place("o")
    a = weighted.add_transition("a")
    weighted.add_arc(i, a, 2); weighted.add_arc(a, o)
    with pytest.raises(ValueError, match="weights"):
        petri_net_to_bpmn(weighted)


# ---------------------------------------------------------------------------
# The framework: type, converters, boxes, file kinds
# ---------------------------------------------------------------------------
def test_bpmn_is_a_registered_type_with_converters():
    from openprocess.flow.convert import can_convert, convert, converters
    from openprocess.flow.types import type_by_key, type_info
    info = type_info(BPMN)
    assert info is not None and info.key == "bpmn" and type_by_key("bpmn").cls is BPMN
    tree = l1_tree()
    model = convert(tree, BPMN)
    assert isinstance(model, BPMN) and can_convert(model, PetriNet)
    assert isinstance(convert(model, PetriNet), PetriNet)
    assert isinstance(convert(to_petri_net(tree), BPMN), BPMN)
    names = {source.rsplit(".", 1)[-1] + "->" + target.rsplit(".", 1)[-1] for source, target in converters()}
    assert {"BPMN->PetriNet", "PetriNet->BPMN", "ProcessTree->BPMN"} <= names


def test_boxes_open_convert_and_save(tmp_path):
    from openprocess.flow import Runner, Workflow
    from openprocess.flow.boxes.discover import bpmn_to_net, inductive_tree, net_to_bpmn, tree_to_bpmn as tree_box
    from openprocess.flow.boxes.input import open_bpmn, typed_log
    from openprocess.flow.boxes.output import save_bpmn
    from openprocess.flow.library import standard_library
    library = standard_library()
    assert {library.resolve(n).name for n in ("open_bpmn", "save_bpmn", "tree_to_bpmn", "bpmn_to_net", "net_to_bpmn")} == \
        {"Open BPMN", "Save as BPMN", "Tree to BPMN", "BPMN to net", "Net to BPMN"}
    assert open_bpmn.spec.outputs[0].type is BPMN and save_bpmn.spec.inputs[0].type is BPMN

    sample = tmp_path / "order.bpmn"
    sample.write_text(SAMPLE, encoding="utf-8")
    target = tmp_path / "out" / "tree.bpmn"
    wf = Workflow("bpmn")
    log = wf.add(typed_log, {"text": L1})
    tree = wf.add(inductive_tree); wf.connect(log, tree)
    as_bpmn = wf.add(tree_box); wf.connect(tree, as_bpmn)
    as_net = wf.add(bpmn_to_net); wf.connect(as_bpmn, as_net)
    back = wf.add(net_to_bpmn); wf.connect(as_net, back)
    saved = wf.add(save_bpmn, {"file": str(target)}); wf.connect(as_bpmn, saved)
    opened = wf.add(open_bpmn, {"file": str(sample)})
    run = Runner().run(wf)
    assert run.ok, [r.error for r in run.failed()]
    assert isinstance(run.value(as_bpmn), BPMN) and isinstance(run.value(back), BPMN)
    assert token_replay(run.value(as_net), parse_simple_log(L1)).fitness == pytest.approx(1.0)
    assert target.is_file() and parse_bpmn(target.read_text(encoding="utf-8")).labels() == {"a", "b", "c", "d", "e"}
    assert run.value(opened).name == "Order handling"
    assert any(note.startswith("Warning") for note in run.result(opened).explanation.notes)


def test_bpmn_files_are_listed_in_model():
    from openprocess.gui.studio.spaces import FILE_KINDS, MODEL, space_of_kind
    from openprocess.gui.studio.workspace import file_kind
    assert file_kind(Path("order.bpmn")) == "bpmn" and file_kind(Path("ORDER.BPMN")) == "bpmn"
    assert "bpmn" in FILE_KINDS[MODEL] and space_of_kind("bpmn") == MODEL


# ---------------------------------------------------------------------------
# The viewer, offscreen
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_viewer_draws_a_bpmn_model(app):
    from openprocess.gui.flow.viewers import result_widget
    from openprocess.gui.studio.graph_builders import bpmn_specs
    from openprocess.gui.studio.graph_view import GraphView

    model = tree_to_bpmn(l1_tree())
    nodes, edges, positions = bpmn_specs(model)
    kinds = {n.kind for n in nodes}
    assert kinds == {"activity", "gateway", "start", "end"} and positions is None
    assert {n.text for n in nodes if n.kind == "gateway"} == {"×", "+"}
    assert len(edges) == len(model.flows)

    widget = result_widget(model)
    widget.resize(900, 600)
    widget.show()
    app.processEvents()
    (view,) = widget.findChildren(GraphView)
    assert len(view.graph.nodes) == len(model.nodes) and len(view.graph.edges) == len(model.flows)
    widget.grab()                                          # paints every diamond, circle and task

    with_positions = parse_bpmn(bpmn_string(model))        # the writer stored a layout
    nodes, edges, positions = bpmn_specs(with_positions)
    assert positions is not None and set(positions) == set(with_positions.nodes)
    stored = result_widget(with_positions)
    stored.resize(900, 600)
    stored.show()
    app.processEvents()
    (view,) = stored.findChildren(GraphView)
    first = next(iter(with_positions.nodes))
    assert view.graph.nodes[first].pos().x() == pytest.approx(positions[first][0])
    stored.grab()
    widget.close()
    stored.close()
