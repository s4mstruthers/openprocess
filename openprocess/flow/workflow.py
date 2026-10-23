"""A workflow: boxes, their settings and the connections between them.

A :class:`Workflow` is a directed acyclic graph.  A :class:`Node` is one use
of a box, with its own settings and a position on the canvas; an
:class:`Edge` joins an output of one node to an input of another.  The class
checks what the canvas also refuses: an edge only between an output and an
input of the same type, no cycles, and only one edge into an input that
takes a single connection.

Python and the canvas are two views of one workflow.  A function decorated
with ``@workflow`` *records* the boxes it calls instead of running them::

    @workflow
    def compare_discovery():
        log = open_log("orders.xes")
        model = inductive_miner(log, noise=0.2)
        return check_fit(model, log)

    wf = compare_discovery.workflow        # a Workflow, ready to run or draw
    print(to_python(wf))                   # and back to source

Groups (several boxes shown as one, with their own canvas) are kept here as
data; their connection points are derived, not declared: an input of a
member fed from outside the group, an output used outside it.
"""

from __future__ import annotations

import contextvars
import itertools
import keyword
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Callable

from .box import Box, BoxSpec, Port
from .sweep import Sweep, is_sweep
from .types import type_info

_ids = itertools.count(1)


def fresh_id(prefix: str = "n") -> str:
    return f"{prefix}{next(_ids)}"


@dataclass
class Node:
    """One box on the canvas with its settings."""

    id: str
    box: str                                   # the box's id in the library
    settings: dict[str, Any] = field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    #: A name the user gave it (empty: the box's name).
    title: str = ""
    #: Where the user dragged the places and transitions of this box's result
    #: (a net), by their id, to tidy the drawing; the net itself is untouched.
    layout: dict[str, tuple[float, float]] = field(default_factory=dict)

    def swept(self) -> list[tuple[str, Sweep]]:
        return [(name, value) for name, value in self.settings.items() if is_sweep(value)]


@dataclass(frozen=True)
class Edge:
    source: str            # node id
    output: str            # output port name
    target: str
    input: str             # input port name


@dataclass
class Group:
    """Several nodes shown as one box, with their own canvas."""

    id: str
    name: str
    members: list[str] = field(default_factory=list)
    position: tuple[float, float] = (0.0, 0.0)


class WorkflowError(ValueError):
    """A connection or a structure the canvas would refuse."""


class Workflow:
    """Nodes, edges and groups, with the checks the canvas relies on."""

    def __init__(self, name: str = "workflow", library=None) -> None:
        from .library import standard_library
        self.name = name
        self.library = library or standard_library()
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.groups: dict[str, Group] = {}

    # -- building ---------------------------------------------------------------
    def spec(self, node: Node | str) -> BoxSpec:
        node = self.nodes[node] if isinstance(node, str) else node
        return self.library.get(node.box)

    def add(self, box: Box | BoxSpec | str, settings: dict[str, Any] | None = None,
            position: tuple[float, float] = (0.0, 0.0), id: str | None = None,
            title: str = "") -> Node:
        """Put a box on the canvas, with its default settings unless given."""
        spec = self.library.resolve(box)
        if id is None:
            # The counter is per process, so a workflow opened from a file (or
            # made in an earlier session) may already hold the next number:
            # take the first one it does not.
            node_id = fresh_id()
            while node_id in self.nodes:
                node_id = fresh_id()
        else:
            node_id = id
        if node_id in self.nodes:
            raise WorkflowError(f"There is already a box with id {node_id!r}")
        values = spec.defaults()
        for key, value in (settings or {}).items():
            setting = spec.setting(key)
            if setting is None:
                raise WorkflowError(f"{spec.name} has no setting {key!r}")
            values[key] = value if is_sweep(value) else setting.coerce(value)
        node = Node(node_id, spec.id, values, tuple(position), title)
        self.nodes[node_id] = node
        return node

    def set(self, node: Node | str, **settings: Any) -> None:
        node = self.nodes[node if isinstance(node, str) else node.id]      # always this workflow's node
        spec = self.spec(node)
        for key, value in settings.items():
            setting = spec.setting(key)
            if setting is None:
                raise WorkflowError(f"{spec.name} has no setting {key!r}")
            node.settings[key] = value if is_sweep(value) else setting.coerce(value)

    def can_connect(self, source: Node | str, target: Node | str, input: str | None = None,
                    output: str = "out") -> tuple[bool, str]:
        """Whether an edge may be made, and if not, why (plain words)."""
        source = self.nodes[source] if isinstance(source, str) else source
        target = self.nodes[target] if isinstance(target, str) else target
        if source.id == target.id:
            return False, "A box cannot feed itself"
        src_spec, dst_spec = self.spec(source), self.spec(target)
        out_port = src_spec.output(output)
        if out_port is None:
            return False, f"{src_spec.name} has no output {output!r}"
        if input is None:
            fitting = [p for p in dst_spec.inputs if p.accepts(out_port)]
            if not fitting:
                return False, (f"{dst_spec.name} takes {_list(p.type_name for p in dst_spec.inputs) or 'nothing'}, "
                               f"not a {out_port.type_name.lower()}")
            # The first input of that type still free (or taking any number), so
            # connecting two logs to a box with inputs a and b fills a, then b,
            # instead of replacing a.
            fed = {e.input for e in self.edges if e.target == target.id}
            free = [p for p in fitting if p.many or p.name not in fed]
            input = (free or fitting)[0].name
        in_port = dst_spec.input(input)
        if in_port is None:
            return False, f"{dst_spec.name} has no input {input!r}"
        if not in_port.accepts(out_port):
            return False, f"“{in_port.label or in_port.name}” takes a {in_port.type_name.lower()}, not a {out_port.type_name.lower()}"
        if source.id in self.descendants([target.id]):
            return False, "That would make a loop"
        if any(e.source == source.id and e.output == output and e.target == target.id and e.input == input
               for e in self.edges):
            return False, "They are connected already"
        return True, input

    def connect(self, source: Node | str, target: Node | str, input: str | None = None,
                output: str = "out") -> Edge:
        """Join an output to an input.  A single-connection input that was
        already fed is re-fed from the new source (as dropping a wire does)."""
        ok, detail = self.can_connect(source, target, input, output)
        if not ok:
            raise WorkflowError(detail)
        source_id = source if isinstance(source, str) else source.id
        target_id = target if isinstance(target, str) else target.id
        port = self.spec(target_id).input(detail)
        if not port.many:
            self.edges = [e for e in self.edges if not (e.target == target_id and e.input == detail)]
        edge = Edge(source_id, output, target_id, detail)
        self.edges.append(edge)
        return edge

    def disconnect(self, edge: Edge) -> None:
        self.edges = [e for e in self.edges if e != edge]

    def remove(self, node: Node | str) -> None:
        node_id = node if isinstance(node, str) else node.id
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges if node_id not in (e.source, e.target)]
        for group in list(self.groups.values()):
            group.members = [m for m in group.members if m != node_id]
            if not group.members:
                del self.groups[group.id]

    # -- groups ---------------------------------------------------------------------
    def group(self, members: list[str], name: str = "Group", id: str | None = None) -> Group:
        if not members:
            raise WorkflowError("A group needs at least one box")
        for m in members:
            if m not in self.nodes:
                raise WorkflowError(f"No box {m!r}")
            if self.group_of(m) is not None:
                raise WorkflowError("A box can be in one group only")
        if id is None:
            id = fresh_id("g")
            while id in self.groups:                        # see add(): ids are per process
                id = fresh_id("g")
        group = Group(id, name, list(members))
        xs = [self.nodes[m].position[0] for m in members]
        ys = [self.nodes[m].position[1] for m in members]
        group.position = (min(xs), min(ys))
        self.groups[group.id] = group
        return group

    def ungroup(self, group: Group | str) -> None:
        group_id = group if isinstance(group, str) else group.id
        self.groups.pop(group_id, None)

    def group_of(self, node_id: str) -> Group | None:
        return next((g for g in self.groups.values() if node_id in g.members), None)

    def group_ports(self, group: Group) -> tuple[list[tuple[str, str, Port]], list[tuple[str, str, Port]]]:
        """The derived inputs and outputs: ``(node, port name, Port)`` each."""
        inside = set(group.members)
        inputs, outputs = [], []
        for member in group.members:
            spec = self.spec(member)
            for port in spec.inputs:
                feeders = [e for e in self.edges if e.target == member and e.input == port.name]
                if not feeders or any(e.source not in inside for e in feeders):
                    inputs.append((member, port.name, port))
            for port in spec.outputs:
                users = [e for e in self.edges if e.source == member and e.output == port.name]
                if not users or any(e.target not in inside for e in users):
                    outputs.append((member, port.name, port))
        return inputs, outputs

    # -- structure ------------------------------------------------------------------
    def inputs_of(self, node: Node | str) -> dict[str, list[tuple[str, str]]]:
        """For every input port: the ``(node, output)`` pairs feeding it."""
        node_id = node if isinstance(node, str) else node.id
        spec = self.spec(node_id)
        result = {p.name: [] for p in spec.inputs}
        for e in self.edges:
            if e.target == node_id and e.input in result:
                result[e.input].append((e.source, e.output))
        return result

    def descendants(self, ids) -> set[str]:
        out, stack = set(ids), list(ids)
        while stack:
            current = stack.pop()
            for e in self.edges:
                if e.source == current and e.target not in out:
                    out.add(e.target)
                    stack.append(e.target)
        return out

    def ancestors(self, ids) -> set[str]:
        out, stack = set(ids), list(ids)
        while stack:
            current = stack.pop()
            for e in self.edges:
                if e.target == current and e.source not in out:
                    out.add(e.source)
                    stack.append(e.source)
        return out

    def order(self) -> list[Node]:
        """The nodes in an order every box's inputs are ready, left to right
        on ties.  Raises on a cycle."""
        indegree = {n: 0 for n in self.nodes}
        for e in self.edges:
            indegree[e.target] += 1
        ready = sorted((n for n, d in indegree.items() if d == 0),
                       key=lambda n: (self.nodes[n].position[0], self.nodes[n].position[1]))
        out = []
        while ready:
            current = ready.pop(0)
            out.append(self.nodes[current])
            for e in self.edges:
                if e.source == current:
                    indegree[e.target] -= 1
                    if indegree[e.target] == 0:
                        ready.append(e.target)
                        ready.sort(key=lambda n: (self.nodes[n].position[0], self.nodes[n].position[1]))
        if len(out) != len(self.nodes):
            raise WorkflowError("The workflow has a loop")
        return out

    def swept(self) -> list[tuple[str, str, Sweep]]:
        """Every swept setting as ``(node id, setting name, Sweep)``."""
        return [(node.id, name, sweep) for node in self.order() for name, sweep in node.swept()]

    def validate(self) -> list[str]:
        """What stops the workflow from running, in plain words (empty: nothing)."""
        problems = []
        try:
            self.order()
        except WorkflowError as error:
            problems.append(str(error))
        for node in self.nodes.values():
            spec = self.spec(node)
            fed = self.inputs_of(node)
            for port in spec.inputs:
                if not fed[port.name] and not port.optional:
                    problems.append(f"{self.title(node)}: connect a {port.type_name.lower()} to “{port.label or port.name}”")
            if not spec.available:
                problems.append(f"{self.title(node)}: {spec.unavailable_reason}")
        return problems

    def title(self, node: Node | str) -> str:
        node = self.nodes[node] if isinstance(node, str) else node
        return node.title or self.spec(node).name

    # -- files ----------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "boxes": [{"id": n.id, "box": n.box, "settings": {k: (v.to_json() if is_sweep(v) else _json_value(v))
                                                             for k, v in n.settings.items()},
                       "position": list(n.position), **({"title": n.title} if n.title else {}),
                       **({"layout": {k: list(v) for k, v in n.layout.items()}} if n.layout else {})}
                      for n in self.nodes.values()],
            "connections": [{"from": e.source, "output": e.output, "to": e.target, "input": e.input}
                            for e in self.edges],
            "groups": [{"id": g.id, "name": g.name, "boxes": list(g.members), "position": list(g.position)}
                       for g in self.groups.values()],
        }

    @classmethod
    def from_dict(cls, data: dict, library=None) -> "Workflow":
        wf = cls(data.get("name", "workflow"), library)
        for item in data.get("boxes", []):
            settings = {k: Sweep(tuple(v["sweep"])) if isinstance(v, dict) and "sweep" in v else v
                        for k, v in item.get("settings", {}).items()}
            try:
                node = wf.add(item["box"], settings, tuple(item.get("position", (0, 0))), id=item["id"],
                              title=item.get("title", ""))
            except KeyError:
                stand_in = _stand_in(wf.library, item["box"])
                if stand_in is None:
                    raise WorkflowError(f"The workflow uses a box that is not available: {item['box']}")
                node = wf.add(stand_in, settings, tuple(item.get("position", (0, 0))), id=item["id"],
                              title=item.get("title", ""))
            layout = item.get("layout") or {}
            if isinstance(layout, dict):
                node.layout = {str(k): (float(v[0]), float(v[1])) for k, v in layout.items()
                               if isinstance(v, (list, tuple)) and len(v) == 2}
        for item in data.get("connections", []):
            wf.connect(item["from"], item["to"], item["input"], item.get("output", "out"))
        for item in data.get("groups", []):
            group = wf.group(item["boxes"], item.get("name", "Group"), id=item.get("id"))
            group.position = tuple(item.get("position", group.position))
        return wf


def _stand_in(library, box_id: str) -> str | None:
    """A folder box's id holds a hash of its file's absolute path, so the same
    file in a folder that was moved, copied or imported elsewhere has another
    id: the folder's box with that function name stands in."""
    if not box_id.startswith("openprocess_boxes_") or library is None:
        return None
    function = box_id.rsplit(".", 1)[-1]
    for spec in library:
        if spec.custom and spec.id.rsplit(".", 1)[-1] == function:
            return spec.id
    return None


def _json_value(value):
    from pathlib import Path
    return str(value) if isinstance(value, Path) else value


def _list(items) -> str:
    items = list(items)
    return ", ".join(items[:-1]) + (" or " if len(items) > 1 else "") + items[-1] if items else ""


# ---------------------------------------------------------------------------
# Recording: a Python function as a workflow
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    """Stands for an output while a function is being recorded."""

    node: str
    output: str = "out"


class _Builder:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow
        self.column = 0

    def call(self, box: Box, args: tuple, kwargs: dict) -> Any:
        spec = box.spec
        if getattr(box, "composite", None) is not None:
            # A @workflow function used as a box: record what it calls, as a group.
            before = set(self.workflow.nodes)
            value = box.composite(*args, **kwargs)
            new = [n for n in self.workflow.nodes if n not in before and self.workflow.group_of(n) is None]
            if len(new) >= 2:
                self.workflow.group(new, spec.name)
            return value
        bound = _bind(spec, args, kwargs)
        settings = {k: v for k, v in bound.items() if spec.setting(k) is not None}
        node = self.workflow.add(spec, settings, position=(self.column * 230.0, 0.0))
        self.column += 1
        for port in spec.inputs:
            value = bound.get(port.name)
            if value is None:
                continue
            refs = value if port.many else [value]
            for ref in refs:
                if not isinstance(ref, Ref):
                    raise WorkflowError(
                        f"{spec.name}: {port.name!r} must come from another box while recording "
                        f"(got {type(ref).__name__})")
                self.workflow.connect(ref.node, node, port.name, ref.output)
        if not spec.outputs:
            return None
        if spec.result_type is not None:
            return spec.result_type(**{p.name: Ref(node.id, p.name) for p in spec.outputs})
        return Ref(node.id, "out")


def _bind(spec: BoxSpec, args: tuple, kwargs: dict) -> dict[str, Any]:
    import inspect
    signature = inspect.signature(spec.function)
    bound = signature.bind_partial(*args, **kwargs)
    return dict(bound.arguments)


_recording: contextvars.ContextVar[_Builder | None] = contextvars.ContextVar(
    "openprocess.flow.recording", default=None)


def recording() -> _Builder | None:
    return _recording.get()


def record(function: Callable, name: str | None = None, library=None) -> Workflow:
    """Run ``function`` in record mode and return the workflow it drew."""
    wf = Workflow(name or function.__name__.replace("_", " "), library)
    token = _recording.set(_Builder(wf))
    try:
        function()
    finally:
        _recording.reset(token)
    _tidy_positions(wf)
    return wf


class Recorded:
    """A ``@workflow`` function: still callable, with ``.workflow`` drawn on
    first use.  One with typed parameters is also a *composite box*: under
    *+ Add box* it is one box, on the canvas it can be opened as a group."""

    def __init__(self, fn: Callable, name: str | None, group: str) -> None:
        self.fn = fn
        self.name = name or fn.__name__.replace("_", " ")
        self.group_name = group
        self._workflow: Workflow | None = None
        self.__name__ = fn.__name__
        self.__doc__ = fn.__doc__
        self.__wrapped__ = fn
        self.__module__ = fn.__module__

    @property
    def workflow(self) -> Workflow:
        if self._workflow is None:
            self._workflow = record(self.fn, self.name)
        return self._workflow

    def __call__(self, *args, **kwargs):
        builder = recording()
        if builder is not None:
            box = self.as_box()
            if box is not None:
                return builder.call(box, args, kwargs)
        return self.fn(*args, **kwargs)

    def as_box(self) -> Box | None:
        """The function as a box (None when its parameters are not typed as a box's)."""
        from .box import BoxError, make_spec
        try:
            spec = make_spec(self.fn, name=self.name, group=self.group_name)
        except BoxError:
            return None
        if not spec.inputs and not spec.settings:
            return None
        box = Box(self.fn, spec)
        box.composite = self.fn
        return box


def workflow(function: Callable | None = None, *, name: str | None = None, group: str = "Yours"):
    """``@workflow``: the function is recorded when first asked for, as
    ``function.workflow``; calling the function still runs it normally.  A
    ``@workflow`` function with typed parameters is also a box (a group on the
    canvas), listed in ``group``."""
    def decorate(fn: Callable) -> Recorded:
        return Recorded(fn, name, group)
    return decorate(function) if function is not None else decorate


def _tidy_positions(wf: Workflow) -> None:
    """Columns by longest path from a source, rows by order: readable, not pretty."""
    depth: dict[str, int] = {}
    for node in wf.order():
        feeders = [e.source for e in wf.edges if e.target == node.id]
        depth[node.id] = max((depth[f] + 1 for f in feeders), default=0)
    rows: dict[int, int] = {}
    for node in wf.order():
        column = depth[node.id]
        node.position = (column * 230.0, rows.get(column, 0) * 110.0)
        rows[column] = rows.get(column, 0) + 1


# ---------------------------------------------------------------------------
# The workflow as Python
# ---------------------------------------------------------------------------
def _identifier(text: str) -> str:
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", text.strip().lower()).strip("_") or "workflow"
    if text[0].isdigit():
        text = "w_" + text
    return text + "_" if keyword.iskeyword(text) else text


def _call_lines(wf: Workflow, nodes: list[Node], names: dict, used: dict, imports: set, customs: set,
                types_used: set, params: dict | None = None) -> list[str]:
    """The box calls for ``nodes`` (in order), naming each result."""
    lines = []
    params = params or {}

    def fresh(base: str) -> str:
        used[base] = used.get(base, 0) + 1
        return base if used[base] == 1 else f"{base}_{used[base]}"

    for node in nodes:
        spec = wf.spec(node)
        function = spec.function.__name__
        (customs if spec.custom else imports).add((spec.module, function))
        fed = wf.inputs_of(node)
        args = []
        for port in spec.inputs:
            sources = [names.get(s, params.get((node.id, port.name))) for s in fed[port.name]]
            sources = [s for s in sources if s]
            if not sources and (node.id, port.name) in params:
                sources = [params[(node.id, port.name)]]
            if port.many:
                args.append(f"{port.name}=[{', '.join(sources)}]")
            elif sources:
                args.append(f"{port.name}={sources[0]}")
        for setting in spec.settings:
            value = node.settings.get(setting.name, setting.default)
            if is_sweep(value):
                args.append(f"{setting.name}=Sweep({list(value.values)!r})")
            elif value != setting.default:
                if isinstance(value, PurePath):            # a file setting: the path as text, not PosixPath(...)
                    value = value.as_posix()
                args.append(f"{setting.name}={value!r}")
        call = f"{function}({', '.join(args)})"
        if not spec.outputs:
            lines.append(call)
        elif spec.result_type is not None:
            var = fresh(_identifier(spec.name))
            lines.append(f"{var} = {call}")
            for port in spec.outputs:
                names[(node.id, port.name)] = f"{var}.{port.name}"
                types_used.add(port.info.python if port.info else "Any")
        else:
            port = spec.outputs[0]
            var = fresh(_identifier(port.info.key if port.info else "value"))
            names[(node.id, "out")] = var
            types_used.add(port.info.python if port.info else "Any")
            lines.append(f"{var} = {call}")
    return lines


def group_to_python(wf: Workflow, group: Group, annotations: set[str] | None = None) -> str:
    """A group as a ``@workflow`` function: its outside inputs are the
    parameters, its outside outputs the return value.  The type names its
    signature uses are added to ``annotations`` (for the imports)."""
    annotations = annotations if annotations is not None else set()
    inputs, outputs = wf.group_ports(group)
    params, seen, signature = {}, {}, []
    for node_id, port_name, port in inputs:
        base = _identifier(port.label or port.name)
        seen[base] = seen.get(base, 0) + 1
        name = base if seen[base] == 1 else f"{base}_{seen[base]}"
        params[(node_id, port_name)] = name
        type_name = port.info.python if port.info else "Any"
        annotations.add(type_name)
        signature.append(f"{name}: {f'list[{type_name}]' if port.many else type_name}")
    names, used, imports, customs, types_used = {}, {}, set(), set(), set()
    members = [n for n in wf.order() if n.id in group.members]
    lines = _call_lines(wf, members, names, used, imports, customs, types_used, params)
    returns = [names.get((node_id, port_name), "None") for node_id, port_name, _ in outputs]
    if returns:
        lines.append("return " + ", ".join(returns))
    output_types = [o[2].info.python if o[2].info else "Any" for o in outputs]
    annotations.update(output_types)
    if len(outputs) == 1:
        return_type = output_types[0]
    elif outputs:
        return_type = "tuple[" + ", ".join(output_types) + "]"
    else:
        return_type = "None"
    body = "\n".join("    " + line for line in lines) or "    pass"
    return (f"@workflow(name={group.name!r})\ndef {_identifier(group.name)}({', '.join(signature)}) -> {return_type}:\n"
            f"{body}\n")


def to_python(wf: Workflow) -> str:
    """The workflow as a ``@workflow`` function that would record it back;
    every group becomes a function of its own, called once."""
    names, used, imports, customs, types_used = {}, {}, set(), set(), set()
    annotations: set[str] = set()                   # the types the groups' signatures name
    lines, group_defs = [], []
    done_groups: set[str] = set()
    for node in wf.order():
        group = wf.group_of(node.id)
        if group is None:
            lines.extend(_call_lines(wf, [node], names, used, imports, customs, types_used))
            continue
        if group.id in done_groups:
            continue
        done_groups.add(group.id)
        group_defs.append(group_to_python(wf, group, annotations))
        inputs, outputs = wf.group_ports(group)
        args = []
        for node_id, port_name, port in inputs:
            sources = [names.get(s) for s in wf.inputs_of(node_id)[port_name]]
            sources = [s for s in sources if s]
            if sources:
                args.append(f"[{', '.join(sources)}]" if port.many else sources[0])
            else:
                args.append("None")
        function = _identifier(group.name)
        if not outputs:
            lines.append(f"{function}({', '.join(args)})")
        else:
            targets = []
            for node_id, port_name, port in outputs:
                base = _identifier(port.info.key if port.info else "value")
                used[base] = used.get(base, 0) + 1
                var = base if used[base] == 1 else f"{base}_{used[base]}"
                names[(node_id, port_name)] = var
                targets.append(var)
            lines.append(f"{', '.join(targets)} = {function}({', '.join(args)})")
        # Spec imports of the members are collected by group_to_python; redo for the header.
        _call_lines(wf, [n for n in wf.order() if n.id in group.members], {}, {}, imports, customs, set(),
                    {(n, p): "x" for n, p, _ in inputs})
    header = ["from openprocess.flow import workflow" + (", Sweep" if wf.swept() else "")
              + (", " + ", ".join(sorted(annotations)) if annotations else "")]
    by_module: dict[str, list[str]] = {}
    for module, function in sorted(imports):
        by_module.setdefault(module, []).append(function)
    for module, functions in by_module.items():
        header.append(f"from {module} import {', '.join(functions)}")
    for module, function in sorted(customs):
        header.append(f"from {module} import {function}")
    body = "\n".join("    " + line for line in lines) or "    pass"
    parts = "\n".join(header) + "\n\n\n"
    for definition in group_defs:
        parts += definition + "\n\n"
    return parts + f"@workflow(name={wf.name!r})\ndef {_identifier(wf.name)}():\n{body}\n"


__all__ = ["Edge", "Group", "Node", "Recorded", "Ref", "Workflow", "WorkflowError", "fresh_id",
           "group_to_python", "record", "recording", "to_python", "workflow"]
