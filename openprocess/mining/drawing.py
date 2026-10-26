"""Process diagrams as SVG, without Qt: nets, process maps, trees, state graphs.

The app draws with Qt (``openprocess.gui.studio.graph_view``); a script, a
report, the CLI or an AI assistant talking to the MCP server have no Qt.
This module draws the same things the same way -- places as circles with
their tokens, transitions as boxes (silent ones as black bars), activities
as rounded boxes shaded by frequency, arrows with their counts -- as plain
SVG text, laid out by :func:`~.layout.layered_layout`, so a diagram is one
call away from any of them::

    from openprocess.mining.drawing import svg_of
    text = svg_of(net)                    # a PetriNet, DFG, ProcessTree, TransitionSystem …
    Path("net.svg").write_text(text)

:func:`png_of` turns the SVG into PNG bytes when PySide6 is installed (the
app's own renderer, offscreen), so an assistant that can look at pictures
can see the diagram; without PySide6 it raises ``RuntimeError`` and the SVG
is still there.  Colours come from :mod:`openprocess.palette`, so the
figures match the app's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from ..palette import CATEGORICAL_LIGHT
from .dfg import END, START, DFG
from .layout import layered_layout
from .petrinet import Marking, PetriNet
from .processtree import ProcessTree
from .transition_system import TransitionSystem

#: Light-theme tones, the same as the app's (``gui/studio/style.py``).
INK = "#1d1d1f"
INK_MUTED = "#6e6e73"
SURFACE = "#ffffff"
BORDER = "#8e8e93"
ACCENT = "#0a84ff"
SEQUENTIAL = ["#eef4fb", "#d6e5f6", "#b5d0ef", "#8db6e6", "#619adb", "#3b7fd0", "#2a63b4", "#1e4a8f"]
GOOD = "#1baf7a"
CRITICAL = "#d93025"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


@dataclass
class Node:
    id: str
    kind: str                       # place | transition | silent | activity | start | end | state | operator
    text: str = ""
    subtext: str = ""
    below: str = ""
    fill: str | None = None
    stroke: str | None = None
    tokens: int = 0
    emphasis: bool = False


@dataclass
class Edge:
    source: str
    target: str
    text: str = ""
    width: float = 1.4
    colour: str = INK_MUTED
    dashed: bool = False
    arrow: bool = True


@dataclass
class Drawing:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    title: str = ""
    legend: list[tuple[str, str]] = field(default_factory=list)      # (label, colour)


def _text_width(text: str, size: float = 12.0) -> float:
    """A fair estimate of a proportional font's width (no font metrics without Qt)."""
    narrow = sum(1 for c in text if c in "iljtfI.,:;'| ")
    wide = sum(1 for c in text if c.isupper() or c in "mwMW@")
    return size * (0.56 * (len(text) - narrow - wide) + 0.3 * narrow + 0.75 * wide)


def node_size(node: Node) -> tuple[float, float]:
    if node.kind == "place":
        return 36.0, 36.0
    if node.kind in ("start", "end"):
        return 26.0, 26.0
    if node.kind == "operator":
        return 34.0, 34.0
    if node.kind == "silent":
        return 14.0, 40.0
    if node.kind == "state":
        return max(_text_width(node.text, 10.5) + 22, 44.0), 30.0
    width = _text_width(node.text) + 28
    if node.subtext:
        width = max(width, _text_width(node.subtext, 10.5) + 28)
    if node.kind == "activity":
        return max(width, 104.0), 48.0 if node.subtext else 40.0
    return max(width, 64.0), 42.0 if not node.subtext else 50.0


# ---------------------------------------------------------------------------
# From the engine's objects to a drawing
# ---------------------------------------------------------------------------
def drawing_of_net(net: PetriNet, marking: Marking | None = None, enabled: set[str] | None = None,
                   place_names: bool = True) -> Drawing:
    """A Petri net: places with their tokens (dots up to 5, a number above),
    transitions as boxes, silent ones as bars, enabled ones outlined green."""
    marking = marking if marking is not None else net.initial_marking
    enabled = enabled or set()
    drawing = Drawing(title=net.name)
    for place in net.places.values():
        drawing.nodes.append(Node(place.id, "place", below=place.name if place_names else "",
                                  tokens=marking[place.id],
                                  emphasis=bool(net.initial_marking[place.id] or net.final_marking[place.id])))
    for transition in net.transitions.values():
        on = transition.id in enabled
        drawing.nodes.append(Node(transition.id, "silent" if transition.silent else "transition",
                                  text="" if transition.silent else transition.name,
                                  fill=("#dff5e8" if on and not transition.silent else None),
                                  stroke=GOOD if on else None, emphasis=on))
    drawing.edges = [Edge(a.source, a.target, text=str(a.weight) if a.weight != 1 else "") for a in net.arcs]
    return drawing


def _compact(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 10_000:
        return f"{count / 1000:.0f}k"
    return f"{count:,}"


def drawing_of_dfg(dfg: DFG, mode: str = "frequency") -> Drawing:
    """The process map: activities shaded by how often they happen, arrows
    as wide as they are frequent (or labelled with mean durations)."""
    from .stats import format_duration
    drawing = Drawing(title="Directly-follows graph")
    most = max(dfg.activities.values(), default=1)
    drawing.nodes.append(Node(START, "start"))
    drawing.nodes.append(Node(END, "end"))
    for activity, count in dfg.activities.items():
        fraction = math.log1p(count) / math.log1p(most) if most else 0
        shade = SEQUENTIAL[min(len(SEQUENTIAL) - 1, int(0.1 + 0.8 * fraction * len(SEQUENTIAL)))]
        ink = INK if shade in SEQUENTIAL[:4] else "#ffffff"
        drawing.nodes.append(Node(activity, "activity", text=activity, subtext=_compact(count),
                                  fill=shade, stroke=SEQUENTIAL[-3] + ("" if ink == INK else ""),))
    all_edges = dfg.all_edges()
    top = max((n for _, _, n in all_edges), default=1)
    durations = {edge: dfg.mean_duration(edge) for edge in dfg.edges}
    slowest = max((d for d in durations.values() if d is not None), default=0)
    for a, b, count in all_edges:
        weight = count / top if top else 0
        if mode == "performance" and (a, b) in dfg.edges:
            mean = durations.get((a, b))
            relative = (mean / slowest) if (mean is not None and slowest) else 0
            drawing.edges.append(Edge(a, b, text=format_duration(mean) if mean is not None else "",
                                      width=1.2 + 4.5 * relative,
                                      colour=CRITICAL if relative > 0.66 else INK_MUTED))
        else:
            drawing.edges.append(Edge(a, b, text=_compact(count) if mode == "frequency" else "",
                                      width=1.0 + 4.5 * weight,
                                      colour=INK_MUTED if weight > 0.15 else "#aeaeb2",
                                      dashed=a == START or b == END))
    return drawing


def drawing_of_tree(tree: ProcessTree) -> Drawing:
    """A process tree, operators as circles over their children (drawn top-down)."""
    drawing = Drawing(title="Process tree")
    counter = [0]

    def visit(node: ProcessTree) -> str:
        counter[0] += 1
        node_id = f"n{counter[0]}"
        if node.is_leaf:
            drawing.nodes.append(Node(node_id, "silent" if node.is_tau else "transition",
                                      text="" if node.is_tau else str(node.label)))
        else:
            drawing.nodes.append(Node(node_id, "operator", text=node.operator.value, fill="#eef4fb"))
            for child in node.children:
                child_id = visit(child)
                drawing.edges.append(Edge(node_id, child_id, arrow=False, colour=BORDER))
        return node_id
    visit(tree)
    return drawing


def drawing_of_transition_system(ts: TransitionSystem, limit: int = 250) -> Drawing:
    drawing = Drawing(title=getattr(ts, "name", "Transition system"))
    states = list(ts.states)[:limit]
    shown = set(states)
    initial = ts.initial_state()
    for state in states:
        drawing.nodes.append(Node(state, "state", text=state, emphasis=state == initial,
                                  fill="#e5f1ff" if state == initial else None))
    labels: dict[tuple[str, str], list[str]] = {}
    for source, event, target in ts.transitions:
        if source in shown and target in shown:
            labels.setdefault((source, target), []).append(event)
    for (source, target), events in labels.items():
        drawing.edges.append(Edge(source, target, text=", ".join(events), width=1.2))
    return drawing


def drawing_of_dependency_graph(graph) -> Drawing:
    """The Heuristics Miner's dependency graph, edges labelled with a ⇒ b."""
    drawing = Drawing(title="Dependency graph")
    most = max(graph.activities.values(), default=1)
    drawing.nodes.append(Node(START, "start"))
    drawing.nodes.append(Node(END, "end"))
    for activity, count in graph.activities.items():
        fraction = math.log1p(count) / math.log1p(most) if most else 0
        shade = SEQUENTIAL[min(len(SEQUENTIAL) - 1, int(0.1 + 0.8 * fraction * len(SEQUENTIAL)))]
        drawing.nodes.append(Node(activity, "activity", text=activity, subtext=_compact(count), fill=shade,
                                  stroke=SEQUENTIAL[-3]))
    drawing.edges += [Edge(START, a, dashed=True, colour="#aeaeb2") for a in graph.start]
    drawing.edges += [Edge(a, END, dashed=True, colour="#aeaeb2") for a in graph.end]
    for (a, b), (value, _count) in graph.edges.items():
        drawing.edges.append(Edge(a, b, text=f"{value:.2f}", width=1.0 + 3 * max(value, 0)))
    return drawing


def drawing_of(value: Any, **options) -> Drawing:
    """The drawing of whatever ``value`` is (a net, a map, a tree, a state graph, a
    dependency graph, a discovery result with a ``net``)."""
    if isinstance(value, Drawing):
        return value
    if isinstance(value, PetriNet):
        return drawing_of_net(value, **options)
    if isinstance(value, DFG):
        return drawing_of_dfg(value, **options)
    if isinstance(value, ProcessTree):
        return drawing_of_tree(value)
    if isinstance(value, TransitionSystem):
        return drawing_of_transition_system(value, **options)
    if type(value).__name__ == "DependencyGraph":
        return drawing_of_dependency_graph(value)
    if hasattr(value, "graph") and type(getattr(value, "graph", None)).__name__ == "DependencyGraph":
        return drawing_of_dependency_graph(value.graph)
    if hasattr(value, "net") and isinstance(value.net, PetriNet):
        return drawing_of_net(value.net, **options)
    if hasattr(value, "tree") and isinstance(getattr(value, "tree"), ProcessTree):
        return drawing_of_tree(value.tree)
    raise TypeError(f"Nothing to draw for a {type(value).__name__}")


# ---------------------------------------------------------------------------
# To SVG
# ---------------------------------------------------------------------------
def _layout(drawing: Drawing, top_down: bool):
    sizes = {n.id: node_size(n) for n in drawing.nodes}
    if top_down:                                        # lay out sideways, then swap the axes
        sizes = {k: (h, w) for k, (w, h) in sizes.items()}
    edges = [(e.source, e.target) for e in drawing.edges]
    result = layered_layout(sizes, edges)
    positions = result.positions
    routes = result.routes
    if top_down:
        positions = {k: (y, x) for k, (x, y) in positions.items()}
        routes = {k: [(y, x) for x, y in pts] for k, pts in routes.items()}
    return positions, routes


def _tokens_svg(cx: float, cy: float, count: int) -> str:
    if count <= 0:
        return ""
    if count > 5:
        return (f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" font-size="13" '
                f'font-weight="600" fill="{INK}">{count}</text>')
    spots = {1: [(0, 0)], 2: [(-6, 0), (6, 0)], 3: [(-7, 4), (7, 4), (0, -6)],
             4: [(-6, -6), (6, -6), (-6, 6), (6, 6)], 5: [(-7, -7), (7, -7), (0, 0), (-7, 7), (7, 7)]}[count]
    return "".join(f'<circle cx="{cx + dx:.1f}" cy="{cy + dy:.1f}" r="3.6" fill="{INK}"/>' for dx, dy in spots)


def _node_svg(node: Node, x: float, y: float) -> str:
    w, h = node_size(node)
    stroke = node.stroke or (ACCENT if node.emphasis and node.kind == "place" else BORDER)
    stroke_width = 2.2 if node.emphasis else 1.4
    out = []
    if node.kind == "place":
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{w / 2:.1f}" fill="{node.fill or SURFACE}" '
                   f'stroke="{stroke}" stroke-width="{stroke_width}"/>')
        out.append(_tokens_svg(x, y, node.tokens))
    elif node.kind in ("start", "end"):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{w / 2:.1f}" fill="{SURFACE}" stroke="{INK}" '
                   f'stroke-width="{3 if node.kind == "end" else 1.6}"/>')
        if node.kind == "start":
            out.append(f'<polygon points="{x - 4:.1f},{y - 6:.1f} {x - 4:.1f},{y + 6:.1f} {x + 6:.1f},{y:.1f}" '
                       f'fill="{INK}"/>')
        else:
            out.append(f'<rect x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10" fill="{INK}"/>')
    elif node.kind == "silent":
        out.append(f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                   f'fill="{INK}" stroke="{stroke if node.stroke else INK}" stroke-width="{stroke_width}"/>')
    elif node.kind == "operator":
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{w / 2:.1f}" fill="{node.fill or SURFACE}" '
                   f'stroke="{stroke}" stroke-width="1.4"/>')
        out.append(f'<text x="{x:.1f}" y="{y + 6:.1f}" text-anchor="middle" font-size="17" fill="{INK}">'
                   f'{escape(node.text)}</text>')
    elif node.kind == "state":
        out.append(f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="15" '
                   f'fill="{node.fill or SURFACE}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
        out.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="10.5" fill="{INK}">'
                   f'{escape(node.text)}</text>')
    else:                                               # transition, activity
        rx = 9 if node.kind == "activity" else 3
        out.append(f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                   f'fill="{node.fill or SURFACE}" stroke="{stroke}" stroke-width="{stroke_width}"/>')
        dark = node.fill in SEQUENTIAL[4:]
        ink = "#ffffff" if dark else INK
        if node.subtext:
            out.append(f'<text x="{x:.1f}" y="{y - 1:.1f}" text-anchor="middle" font-size="12" '
                       f'font-weight="600" fill="{ink}">{escape(node.text)}</text>')
            out.append(f'<text x="{x:.1f}" y="{y + 14:.1f}" text-anchor="middle" font-size="10.5" '
                       f'fill="{ink if dark else INK_MUTED}">{escape(node.subtext)}</text>')
        else:
            out.append(f'<text x="{x:.1f}" y="{y + 4.5:.1f}" text-anchor="middle" font-size="12" '
                       f'font-weight="600" fill="{ink}">{escape(node.text)}</text>')
    if node.below:
        out.append(f'<text x="{x:.1f}" y="{y + h / 2 + 15:.1f}" text-anchor="middle" font-size="11" '
                   f'fill="{INK}">{escape(node.below)}</text>')
    return "".join(out)


def _trim(points: list[tuple[float, float]], end_size: tuple[float, float], round_end: bool,
          start_size: tuple[float, float], round_start: bool) -> list[tuple[float, float]]:
    """Shorten a route so the arrow meets the border of its end nodes."""
    def border_point(centre, towards, size, round_shape):
        dx, dy = towards[0] - centre[0], towards[1] - centre[1]
        length = math.hypot(dx, dy) or 1.0
        w, h = size
        if round_shape:
            r = min(w, h) / 2
            return centre[0] + dx / length * r, centre[1] + dy / length * r
        sx = (w / 2) / abs(dx) if dx else math.inf
        sy = (h / 2) / abs(dy) if dy else math.inf
        s = min(sx, sy)
        return centre[0] + dx * s, centre[1] + dy * s
    points = list(points)
    if len(points) >= 2:
        points[0] = border_point(points[0], points[1], start_size, round_start)
        points[-1] = border_point(points[-1], points[-2], end_size, round_end)
    return points


def svg_of_drawing(drawing: Drawing, top_down: bool = False, padding: float = 24.0) -> str:
    """The drawing as SVG text."""
    if not drawing.nodes:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 60" width="320" height="60" '
                f'font-family="{FONT}"><text x="12" y="34" fill="{INK_MUTED}">Nothing to draw.</text></svg>')
    positions, routes = _layout(drawing, top_down)
    by_id = {n.id: n for n in drawing.nodes}
    xs, ys = [], []
    for node in drawing.nodes:
        x, y = positions[node.id]
        w, h = node_size(node)
        xs += [x - w / 2, x + w / 2]
        ys += [y - h / 2, y + h / 2 + (20 if node.below else 0)]
    for pts in routes.values():
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    min_x, min_y = min(xs) - padding, min(ys) - padding - (22 if drawing.title else 0)
    width, height = max(xs) + padding - min_x, max(ys) + padding - min_y
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.1f} {min_y:.1f} {width:.1f} {height:.1f}" '
           f'width="{width:.0f}" height="{height:.0f}" font-family="{FONT}">',
           f'<rect x="{min_x:.1f}" y="{min_y:.1f}" width="{width:.1f}" height="{height:.1f}" fill="{SURFACE}"/>',
           '<defs>']
    colours = sorted({e.colour for e in drawing.edges if e.arrow})
    for index, colour in enumerate(colours):
        out.append(f'<marker id="arrow{index}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                   f'markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" '
                   f'fill="{colour}"/></marker>')
    out.append('</defs>')
    marker_of = {colour: f"arrow{index}" for index, colour in enumerate(colours)}
    if drawing.title:
        out.append(f'<text x="{min_x + padding:.1f}" y="{min_y + 18:.1f}" font-size="12" fill="{INK_MUTED}">'
                   f'{escape(drawing.title)}</text>')
    for index, edge in enumerate(drawing.edges):
        if edge.source not in positions or edge.target not in positions:
            continue
        points = routes.get(index) or [positions[edge.source], positions[edge.target]]
        if edge.source == edge.target:                   # a self-loop: a small arc above the node
            x, y = positions[edge.source]
            w, h = node_size(by_id[edge.source])
            path = (f'M {x - 10:.1f} {y - h / 2:.1f} C {x - 26:.1f} {y - h / 2 - 34:.1f}, '
                    f'{x + 26:.1f} {y - h / 2 - 34:.1f}, {x + 10:.1f} {y - h / 2:.1f}')
            label_at = (x, y - h / 2 - 30)
        else:
            source, target = by_id[edge.source], by_id[edge.target]
            points = _trim(points, node_size(target), target.kind in ("place", "start", "end", "operator"),
                           node_size(source), source.kind in ("place", "start", "end", "operator"))
            path = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in points)
            mid = points[len(points) // 2] if len(points) % 2 else (
                (points[len(points) // 2 - 1][0] + points[len(points) // 2][0]) / 2,
                (points[len(points) // 2 - 1][1] + points[len(points) // 2][1]) / 2)
            label_at = (mid[0], mid[1] - 5)
        dash = ' stroke-dasharray="5 4"' if edge.dashed else ""
        marker = f' marker-end="url(#{marker_of[edge.colour]})"' if edge.arrow else ""
        out.append(f'<path d="{path}" fill="none" stroke="{edge.colour}" stroke-width="{edge.width:.1f}"'
                   f'{dash}{marker}/>')
        if edge.text:
            tw = _text_width(edge.text, 10.5) + 6
            out.append(f'<rect x="{label_at[0] - tw / 2:.1f}" y="{label_at[1] - 10:.1f}" width="{tw:.1f}" '
                       f'height="14" rx="3" fill="{SURFACE}" fill-opacity="0.9"/>')
            out.append(f'<text x="{label_at[0]:.1f}" y="{label_at[1] + 1:.1f}" text-anchor="middle" '
                       f'font-size="10.5" fill="{INK_MUTED}">{escape(edge.text)}</text>')
    for node in drawing.nodes:
        x, y = positions[node.id]
        out.append(_node_svg(node, x, y))
    if drawing.legend:
        lx, ly = min_x + padding, max(ys) + padding - 4
        for label, colour in drawing.legend:
            out.append(f'<rect x="{lx:.1f}" y="{ly - 9:.1f}" width="10" height="10" fill="{colour}"/>'
                       f'<text x="{lx + 14:.1f}" y="{ly:.1f}" font-size="10.5" fill="{INK_MUTED}">'
                       f'{escape(label)}</text>')
            lx += _text_width(label, 10.5) + 34
    out.append("</svg>")
    return "\n".join(out)


def svg_of(value: Any, **options) -> str:
    """The SVG of a net, a map, a tree, a transition system or a discovery result.

    ``top_down=True`` lays the diagram out from top to bottom (the default
    for a process tree); other options go to the drawing (a net's ``marking``
    and ``enabled`` set, a map's ``mode``: frequency or performance)."""
    top_down = options.pop("top_down", isinstance(value, ProcessTree) or
                           isinstance(getattr(value, "tree", None), ProcessTree) and not isinstance(value, PetriNet)
                           and not hasattr(value, "net"))
    return svg_of_drawing(drawing_of(value, **options), top_down=top_down)


def png_of(svg: str, scale: float = 2.0) -> bytes:
    """The SVG rendered to PNG bytes with Qt's SVG renderer, offscreen.

    Needs PySide6 (the ``app`` extra); raises ``RuntimeError`` without it."""
    import os
    import re
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except ImportError as error:
        raise RuntimeError("Rendering PNG needs PySide6 (pip install 'openprocess[app]'); "
                           "the SVG is available without it") from error
    if QGuiApplication.instance() is None:
        QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    match = re.search(r'width="(\d+)" height="(\d+)"', svg)
    width, height = (int(match.group(1)), int(match.group(2))) if match else (800, 400)
    image = QImage(int(width * scale), int(height * scale), QImage.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


__all__ = ["Drawing", "Edge", "Node", "drawing_of", "drawing_of_dfg", "drawing_of_dependency_graph",
           "drawing_of_net", "drawing_of_transition_system", "drawing_of_tree", "node_size", "png_of",
           "svg_of", "svg_of_drawing"]
