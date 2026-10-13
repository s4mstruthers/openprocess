"""Typeset derivations: how a discovery algorithm arrived at its model.

α-algorithm
    The eight steps as a numbered list of formulas, typeset like the book:
    ``T_L``, ``X_L``, ``Y_L`` with subscripts, sets in braces, one (A, B)
    pair per line, and places written as ``p_(A,B)``.

Inductive Miner
    Three views of the same result:

    * the **process tree drawn as a tree** (operators as circles, activities
      as boxes, τ as a black bar);
    * the tree as a **formula** with the operators highlighted;
    * the **recursion** as an expandable outline: every cut with its
      partition, every base case and fall-through, nested exactly as the
      algorithm recursed.

State-based regions
    The transition system, its regions and the elementary checks
    (:class:`.regions_view.RegionsPanel`).

Rendering uses Qt's rich-text engine (a subset of HTML): subscripts, italics
and a serif maths font give readable formulas without a browser engine.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QFrame, QLabel, QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...mining.discovery.alpha import AlphaResult
from ...mining.discovery.inductive import InductiveResult
from ...mining.processtree import Operator, ProcessTree
from . import style
from .graph_view import EdgeSpec, GraphView, NodeSpec, node_size
from .widgets import Verdict, label

MATH_FONT = ("'STIX Two Math', 'STIX Two Text', 'Cambria Math', 'Latin Modern Math', "
             "'Times New Roman', serif")
OPERATOR_NAMES = {"→": "sequence", "×": "exclusive choice", "∧": "parallel", "↺": "loop"}


# ---------------------------------------------------------------------------
# Small typesetting helpers (return HTML fragments)
# ---------------------------------------------------------------------------
def _var(name: str, sub: str = "L") -> str:
    """An italic maths symbol with a subscript, e.g. T_L."""
    return f"<i>{escape(name)}</i><sub>{escape(sub)}</sub>"


def _activity(name: str) -> str:
    """Activity names are words, set upright in the UI font."""
    return f"<span style='font-family: sans-serif'>{escape(name)}</span>"


def _set(items) -> str:
    items = sorted(items)
    if not items:
        return "∅"
    return "{" + ", ".join(_activity(i) for i in items) + "}"


def _pair(pair) -> str:
    a, b = pair
    return f"({_set(a)}, {_set(b)})"


def _place(pair) -> str:
    return f"<i>p</i><sub>{_pair(pair)}</sub>"


def _rich(html: str, size: int = 14) -> QLabel:
    t = style.tokens()
    widget = QLabel()
    widget.setTextFormat(Qt.RichText)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    widget.setText(f"<div style='font-family: {MATH_FONT}; font-size: {size}px; "
                   f"color: {t.text}'>{html}</div>")
    widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
    return widget


# ---------------------------------------------------------------------------
# α-algorithm
# ---------------------------------------------------------------------------
def alpha_view(result: AlphaResult) -> QWidget:
    t = style.tokens()
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for warning in result.warnings:
        layout.addWidget(Verdict("Limitation", "warning", warning))

    maximal = set(result.Y_L)
    x_lines = "<br>".join(
        (f"<b>{_pair(p)}</b>" if p in maximal else _pair(p)) for p in result.X_L) or "∅"
    y_lines = "<br>".join(_pair(p) for p in result.Y_L) or "∅"
    places = ", ".join([f"<i>i</i><sub>L</sub>"] + [_place(p) for p in result.Y_L]
                       + [f"<i>o</i><sub>L</sub>"])
    arcs = []
    for arc in result.net.arcs:
        arcs.append(f"({escape(result.net.node_name(arc.source))}, "
                    f"{escape(result.net.node_name(arc.target))})")
    steps = [
        ("1", "all activities", f"{_var('T')} = {_set(result.T_L)}"),
        ("2", "start activities", f"{_var('T', 'I')} = {_set(result.T_I)}"),
        ("3", "end activities", f"{_var('T', 'O')} = {_set(result.T_O)}"),
        ("4", "candidate pairs: A → B, members of A and of B pairwise #  "
              "(maximal ones in bold)", f"{_var('X')} = {{<br>{x_lines}<br>}}"),
        ("5", "maximal pairs", f"{_var('Y')} = {{<br>{y_lines}<br>}}"),
        ("6", "places: one per maximal pair, plus source and sink",
         f"{_var('P')} = {{ {places} }}"),
        ("7", f"flow relation: {len(result.net.arcs)} arcs",
         f"{_var('F')} = {{ " + ", ".join(escape(a) for a in arcs[:40])
         + (" …" if len(arcs) > 40 else "") + " }"),
        ("8", "the discovered net", f"α(<i>L</i>) = ({_var('P')}, {_var('T')}, {_var('F')})"),
    ]
    rows = []
    for number, caption, formula in steps:
        rows.append(
            f"<tr><td valign='top' style='padding: 6px 10px 6px 0; color: {t.text_muted}; "
            f"font-family: sans-serif; font-size: 12px'><b>{number}</b></td>"
            f"<td style='padding: 6px 0'>{formula}<br><span style='font-family: sans-serif; "
            f"font-size: 11px; color: {t.text_muted}'>{escape(caption)}</span></td></tr>")
    layout.addWidget(_rich("<table cellspacing='0' width='100%'>" + "".join(rows) + "</table>"))
    layout.addStretch(1)
    return host


# ---------------------------------------------------------------------------
# Process trees
# ---------------------------------------------------------------------------
def tree_formula(tree: ProcessTree) -> str:
    """The tree as a formula with coloured operators (HTML)."""
    accent = style.tokens().accent

    def render(node: ProcessTree) -> str:
        if node.is_leaf:
            return "<i>τ</i>" if node.label is None else _activity(node.label)
        inner = ", ".join(render(c) for c in node.children)
        return f"<b style='color: {accent}'>{node.operator.value}</b>({inner})"
    return render(tree)


#: Space between neighbouring subtrees, and between one level and the next.
TREE_SIBLING_GAP = 18.0
TREE_LEVEL_GAP = 56.0
#: The drawn tree's height on the Discover tab: at least, at most (pixels).
TREE_VIEW_HEIGHT = (160, 420)


def tree_specs(tree: ProcessTree) -> tuple[list[NodeSpec], list[EdgeSpec],
                                           dict[str, tuple[float, float]]]:
    """Nodes, edges and positions for drawing the tree with the graph canvas.

    Drawn the way a process tree is in the course: the root at the top, each
    operator's children in a row underneath it, in order (left to right is the
    order of a sequence), each parent centred over its children, with plain
    lines and τ as a leaf like any other (not the Petri net's black bar).
    """
    t = style.tokens()
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []
    #: Each node's children, and how wide its subtree is.
    children: dict[str, list[str]] = {}
    widths: dict[str, float] = {}
    sizes: dict[str, tuple[float, float]] = {}
    counter = [0]

    def visit(node: ProcessTree) -> str:
        counter[0] += 1
        node_id = f"n{counter[0]}"
        if node.is_leaf:
            if node.label is None:
                spec = NodeSpec(node_id, "transition", text="τ", tooltip="τ (silent step)")
            else:
                spec = NodeSpec(node_id, "transition", text=node.label,
                                tooltip=f"activity {node.label}")
        else:
            symbol = node.operator.value
            spec = NodeSpec(node_id, "operator", text=symbol, fill=t.accent_soft,
                            stroke=t.accent, tooltip=f"{symbol}  {OPERATOR_NAMES[symbol]}")
        nodes.append(spec)
        sizes[node_id] = node_size(spec)
        children[node_id] = []
        for child in node.children:
            child_id = visit(child)
            children[node_id].append(child_id)
            # A plain line: a tree's edge means "contains", not "goes to".
            edges.append(EdgeSpec(node_id, child_id, width=1.2, colour=t.text_muted,
                                  arrow=False))
        below = [widths[c] for c in children[node_id]]
        widths[node_id] = max(sizes[node_id][0],
                              sum(below) + TREE_SIBLING_GAP * max(0, len(below) - 1))
        return node_id

    root = visit(tree)
    # Each level is as tall as its tallest node, so rows line up.
    depth_of: dict[str, int] = {}
    heights: dict[int, float] = {}

    def depths(node_id: str, depth: int) -> None:
        depth_of[node_id] = depth
        heights[depth] = max(heights.get(depth, 0.0), sizes[node_id][1])
        for child in children[node_id]:
            depths(child, depth + 1)

    depths(root, 0)
    tops, y = {}, 0.0
    for depth in sorted(heights):
        tops[depth] = y
        y += heights[depth] + TREE_LEVEL_GAP
    positions: dict[str, tuple[float, float]] = {}

    def place(node_id: str, left: float) -> None:
        depth = depth_of[node_id]
        positions[node_id] = (left + widths[node_id] / 2, tops[depth] + heights[depth] / 2)
        below = children[node_id]
        span = sum(widths[c] for c in below) + TREE_SIBLING_GAP * max(0, len(below) - 1)
        x = left + (widths[node_id] - span) / 2
        for child in below:
            place(child, x)
            x += widths[child] + TREE_SIBLING_GAP

    place(root, 0.0)
    return nodes, edges, positions


class TreeView(GraphView):
    """The drawn process tree: as tall as the tree is at the width it gets.

    Stretched to fill the column instead, the canvas ran past the bottom of
    the card (taking its zoom bar with it), with the tree a sliver in the
    middle.
    """

    def fit_height(self) -> None:
        rect = self.graph.sceneRect()
        if rect.isEmpty():
            return
        scale = min(self.viewport().width() / rect.width(), 1.4) if self.width() > 1 else 1.0
        height = rect.height() * scale + 2 * self.frameWidth()
        height = int(min(max(height, TREE_VIEW_HEIGHT[0]), TREE_VIEW_HEIGHT[1]))
        if height != self.height():
            self.setFixedHeight(height)
            # The zoom bar is placed for the old height until the layout has
            # applied the new one: place it again once it has.
            QTimer.singleShot(0, self._place_controls)
        self.fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.fit_height()


# ---------------------------------------------------------------------------
# Rich-text rows in a tree widget
# ---------------------------------------------------------------------------
class HtmlDelegate(QStyledItemDelegate):
    """Paints an item's text as rich text (HTML) instead of plain text."""

    def _document(self, option, index) -> QTextDocument:
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setHtml(index.data(Qt.DisplayRole) or "")
        document.setTextWidth(max(option.rect.width(), 200))
        return document

    def paint(self, painter, option, index) -> None:
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        html = options.text
        options.text = ""
        style_ = options.widget.style() if options.widget else None
        if style_ is not None:
            style_.drawControl(QStyle.CE_ItemViewItem, options, painter, options.widget)
        document = QTextDocument()
        document.setDefaultFont(options.font)
        document.setHtml(html)
        document.setTextWidth(options.rect.width())
        painter.save()
        painter.translate(options.rect.topLeft())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.Text, QColor(style.tokens().text))
        document.documentLayout().draw(painter, context)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setHtml(index.data(Qt.DisplayRole) or "")
        # Wrap at the width the row will really get, or rows overlap.
        width = option.rect.width()
        widget = option.widget
        if widget is not None and hasattr(widget, "viewport"):
            indent = 0
            if hasattr(widget, "indentation"):
                depth, parent = 0, index.parent()
                while parent.isValid():
                    depth, parent = depth + 1, parent.parent()
                indent = widget.indentation() * (depth + 1)
            width = widget.viewport().width() - indent - 6
        document.setTextWidth(max(width, 200))
        return QSize(int(document.idealWidth()), int(document.size().height()) + 6)


def _step_html(step) -> str:
    t = style.tokens()
    accent, muted = t.accent, t.text_muted
    if step.kind == "cut":
        groups = " &nbsp;|&nbsp; ".join(_set(g) for g in step.groups or [])
        where = " <span style='color:%s'>(on the filtered DFG)</span>" % muted if step.filtered else ""
        # A sequence cut says when the strict step merged groups (Leemans 2017).
        detail = f"<br><span style='color:{muted}'>{escape(step.detail)}</span>" if step.detail else ""
        return (f"<b style='color:{accent}; font-size: 15px'>{step.operator}</b> "
                f"<b>{OPERATOR_NAMES.get(step.operator, '')} cut</b>{where}<br>"
                f"<span style='font-family:{MATH_FONT}'>{groups}</span>{detail}")
    if step.kind == "base":
        leaf = "<i>τ</i>" if step.result == "τ" else _activity(step.result or "")
        return f"<b>base case</b> → {leaf}"
    if step.kind == "filter":
        return f"<b>IMf filter</b> <span style='color:{muted}'>{escape(step.detail or step.text)}</span>"
    operator = f"<b style='color:{accent}'>{step.operator}</b> " if step.operator else ""
    return (f"{operator}<b>fall-through</b> <span style='color:{muted}'>"
            f"{escape(step.detail or step.text)}</span>")


def inductive_view(result: InductiveResult, draw_tree: bool = True) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    layout.addWidget(label("PROCESS TREE", "sectionLabel"))
    layout.addWidget(_rich(tree_formula(result.tree), 15))
    if draw_tree:
        view = TreeView()
        nodes, edges, positions = tree_specs(result.tree)
        view.graph.populate(nodes, edges, positions=positions)
        view.fit_height()
        layout.addWidget(view)

    layout.addWidget(label("RECURSION", "sectionLabel"))
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setItemDelegate(HtmlDelegate(tree))
    tree.setUniformRowHeights(False)
    tree.setFrameShape(QFrame.NoFrame)
    tree.setMinimumHeight(200)
    stack: list[tuple[int, QTreeWidgetItem | QTreeWidget]] = [(-1, tree)]
    for step in result.steps:
        while stack and stack[-1][0] >= step.depth:
            stack.pop()
        parent = stack[-1][1]
        item = QTreeWidgetItem(parent, [_step_html(step)]) if isinstance(parent, QTreeWidgetItem) \
            else QTreeWidgetItem(tree, [_step_html(step)])
        item.setToolTip(0, step.text)
        stack.append((step.depth, item))
    tree.expandAll()
    layout.addWidget(tree, 1)
    return host


def heuristics_view(result) -> QWidget:
    """The causal net behind a Heuristics Miner Petri net: each activity's
    input and output bindings, with how often the log used them."""
    from ...mining.discovery.heuristics import END, START
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    graph = result.graph
    layout.addWidget(label(
        f"1. Dependency graph: {len(graph.edges)} arcs a → b with a ⇒ b ≥ "
        f"{result.dependency_threshold:g}.  2. Bindings, from replaying the log: which "
        "predecessors each activity waited for (in) and which successors it enabled (out). "
        "A binding of several activities is an AND; different bindings are an XOR.  "
        "3. One place per arc, and a silent transition per binding where there is a choice.",
        "muted", wrap=True))
    layout.addWidget(label("BINDINGS", "sectionLabel"))

    def shown(binding) -> str:
        names = sorted({"start" if a == START else "end" if a == END else a for a in binding})
        return "{" + ", ".join(escape(n) for n in names) + "}"

    causal = result.causal
    activities = sorted((set(causal.inputs) | set(causal.outputs)) - {START, END})
    rows = []
    for activity in activities:
        ins = " or ".join(f"{shown(b)} ×{n}" for b, n in causal.inputs.get(activity, {}).most_common())
        outs = " or ".join(f"{shown(b)} ×{n}" for b, n in causal.outputs.get(activity, {}).most_common())
        rows.append(f"<b>{escape(activity)}</b><br>&nbsp;&nbsp;in: {ins or '–'}"
                    f"<br>&nbsp;&nbsp;out: {outs or '–'}")
    layout.addWidget(_rich("<br>".join(rows), 13))
    layout.addStretch(1)
    return host


def derivation_view(payload, draw_tree: bool = True) -> QWidget:
    from ...mining.discovery.heuristics import HeuristicsResult
    if isinstance(payload, AlphaResult):
        return alpha_view(payload)
    if isinstance(payload, InductiveResult):
        return inductive_view(payload, draw_tree)
    if isinstance(payload, HeuristicsResult):
        return heuristics_view(payload)
    from ...mining.discovery.state_regions import RegionResult
    if isinstance(payload, RegionResult):
        from .regions_view import RegionsPanel
        # The net is already drawn beside it (Discover tab, model page).
        return RegionsPanel(payload, show_net=False, flat=True)
    info = getattr(payload, "info", {}) or {}
    return label("\n".join(f"{k}: {v}" for k, v in info.items()) or "No details.", "muted",
                 wrap=True, selectable=True)
