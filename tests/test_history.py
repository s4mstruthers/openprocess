"""Undo and redo for a workflow: snapshots, labels, folding a drag into one step."""

from __future__ import annotations

from openprocess.flow.history import History, describe


def state(boxes=(), connections=(), groups=(), name="w"):
    return {"name": name, "boxes": list(boxes), "connections": list(connections), "groups": list(groups)}


def node(node_id="n1", position=(0, 0), settings=None, title="", layout=None):
    item = {"id": node_id, "box": "b", "settings": settings or {}, "position": list(position)}
    if title:
        item["title"] = title
    if layout:
        item["layout"] = layout
    return item


def test_history_records_undoes_and_redoes():
    history = History(state())
    assert not history.can_undo and not history.record(state())            # the same state: nothing
    one = state([node()])
    assert history.record(one) and history.undo_label == "adding a box"
    history.record(state([node(position=(100, 0))]), "move", now=10.0)
    history.record(state([node(position=(200, 0))]), "move", now=10.5)     # the same drag: one step
    assert len(history.undo_stack) == 2
    label, restored = history.undo()
    assert label == "a move" and restored == one and history.can_redo and history.redo_label == "a move"
    label, restored = history.undo()
    assert label == "adding a box" and restored["boxes"] == []
    assert history.undo() is None and not history.can_undo
    label, restored = history.redo()
    assert label == "adding a box" and restored == one
    history.record(state([node(settings={"k": 2})]))                        # a new change: no way forward
    assert not history.can_redo and history.undo_label == "a setting"


def test_describe_names_the_change():
    a, b = node("n1"), node("n2")
    assert describe(state([a]), state([a, b])) == "adding a box"
    assert describe(state([a, b]), state([])) == "removing 2 boxes"
    assert describe(state([a, b]), state([a, b], [{"from": "n1", "to": "n2"}])) == "a connection"
    assert describe(state([a]), state([a], groups=[{"id": "g"}])) == "grouping"
    assert describe(state([a]), state([node("n1", title="Mine")])) == "a rename"
    assert describe(state([a]), state([node("n1", layout={"p1": [1, 2]})])) == "tidying a drawing"
    assert describe(state([a]), state([a], name="other")) == "a rename"
    assert describe(state([a]), state([a])) == "a change"


def test_history_keeps_a_limit():
    history = History(state(), limit=3)
    for i in range(6):
        history.record(state([node(f"n{j}") for j in range(i + 1)]), now=float(i) * 10)
    assert len(history.undo_stack) == 3
    assert [len(history.undo()[1]["boxes"]) for _ in range(3)] == [5, 4, 3]
