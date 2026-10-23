"""Automatic left-to-right layout of process graphs (Sugiyama's method).

Discovered models have no coordinates, so the app must place every node
itself.  ProM and PM4Py hand this to Graphviz; we do it in pure Python so the
app needs no external binary.  The method is the classic *layered* drawing
of Sugiyama, Tagawa & Toda (1981), which is also what Graphviz ``dot`` uses:

1. **Break cycles.**  Layering needs a DAG, so a depth-first search reverses
   every back edge (loops in the process).  Reversed edges are drawn in
   their original direction afterwards.
2. **Assign layers** (columns).  Longest-path layering from the sources, so
   the start of the process is on the left.
3. **Insert dummy nodes** on edges spanning several layers, so every edge
   connects adjacent layers and can be routed around other nodes.
4. **Reduce crossings.**  Reorder nodes within each layer by the barycentre
   (average position) of their neighbours, sweeping left-to-right and back;
   keep the best ordering seen.
5. **Assign coordinates.**  x from the layer, y by repeatedly pulling each
   node towards the median of its neighbours while keeping a minimum gap.

The result gives a centre point per node and a polyline per edge (through
its dummy nodes), which the canvas turns into smooth curves.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


#: Above this many nodes (dummies included), fewer refinement passes; see :func:`layered_layout`.
LARGE = 20_000


def count_inversions(ends: list[int]) -> int:
    """How many pairs of edges between two neighbouring layers cross: with the
    edges sorted by their left end (then right end), ``ends`` are their right
    ends, and a crossing is a pair whose right ends are in the opposite order.
    Counted with a Fenwick tree in O(E log E); a large discovered net has tens
    of thousands of edges, and comparing every pair of them took minutes."""
    size = max(ends, default=0) + 2
    tree = [0] * (size + 1)
    total = 0
    for seen, end in enumerate(ends):
        k, not_above = end + 1, 0                      # earlier edges ending at or above this one
        while k > 0:
            not_above += tree[k]
            k -= k & -k
        total += seen - not_above                      # earlier edges ending strictly below: a crossing
        k = end + 1
        while k <= size:
            tree[k] += 1
            k += k & -k
    return total


@dataclass
class LayoutResult:
    positions: dict[str, tuple[float, float]]                   # node -> centre
    routes: dict[int, list[tuple[float, float]]] = field(default_factory=dict)  # edge index -> points
    width: float = 0.0
    height: float = 0.0


def layered_layout(nodes: dict[str, tuple[float, float]], edges: list[tuple[str, str]],
                   layer_gap: float = 70.0, node_gap: float = 28.0,
                   sweeps: int = 16, soft_edges: frozenset[int] | set[int] = frozenset()
                   ) -> LayoutResult:
    """Lay out ``nodes`` (id -> (width, height)) connected by ``edges``.

    Self-loops are ignored for layering (the canvas draws them as small
    arcs).  Returns centre positions and a route per edge index.

    ``soft_edges`` (indices into ``edges``) do not decide the layers.  A node
    reached *only* through soft edges -- a resource place that a transition
    reads and writes back, say -- is put in the layer of its neighbours,
    right next to them, instead of dragging the whole drawing out of shape.
    """
    ids = list(nodes)
    if not ids:
        return LayoutResult({})
    hard = [(a, b) for index, (a, b) in enumerate(edges) if index not in soft_edges]

    # -- 1. break cycles ----------------------------------------------------
    successors: dict[str, list[str]] = defaultdict(list)
    for a, b in hard:
        if a != b:
            successors[a].append(b)
    order = {node: i for i, node in enumerate(ids)}
    # Start the DFS from nodes without predecessors (process starts) so that
    # the natural direction of the process is kept and loops get reversed.
    has_pred = {b for a, b in hard if a != b}
    roots = [n for n in ids if n not in has_pred] + [n for n in ids if n in has_pred]
    state: dict[str, int] = {}          # 1 = on stack, 2 = done
    back_edges: set[tuple[str, str]] = set()
    for root in roots:
        if root in state:
            continue
        stack = [(root, iter(sorted(successors[root], key=order.get)))]
        state[root] = 1
        while stack:
            node, children = stack[-1]
            for child in children:
                if state.get(child) == 1:
                    back_edges.add((node, child))
                elif child not in state:
                    state[child] = 1
                    stack.append((child, iter(sorted(successors[child], key=order.get))))
                    break
            else:
                state[node] = 2
                stack.pop()

    dag: list[tuple[str, str]] = []
    for a, b in hard:
        if a == b:
            continue
        dag.append((b, a) if (a, b) in back_edges else (a, b))

    # -- 2. layers: longest path from sources ------------------------------
    preds: dict[str, set[str]] = defaultdict(set)
    succs: dict[str, set[str]] = defaultdict(set)
    for a, b in dag:
        preds[b].add(a)
        succs[a].add(b)
    layer: dict[str, int] = {}
    pending = {n: len(preds[n]) for n in ids}
    ready = [n for n in ids if pending[n] == 0]
    while ready:
        node = ready.pop(0)
        layer[node] = max((layer[p] + 1 for p in preds[node]), default=0)
        for child in sorted(succs[node], key=order.get):
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    for n in ids:  # safety net; cannot happen after cycle breaking
        layer.setdefault(n, 0)

    # Nodes attached by soft edges only join the layer of their neighbours.
    in_hard = {n for edge in hard for n in edge if edge[0] != edge[1]}
    free = [n for n in ids if n not in in_hard]
    soft_neighbours: dict[str, list[str]] = defaultdict(list)
    for index in soft_edges:
        a, b = edges[index]
        soft_neighbours[a].append(b)
        soft_neighbours[b].append(a)
    placed = set(in_hard)
    for _ in range(3):                  # a few rounds settle chains of free nodes
        for n in free:
            known = sorted(layer[x] for x in soft_neighbours[n] if x in placed)
            if known:
                layer[n] = known[len(known) // 2]
                placed.add(n)
    # Soft edges between nodes in different layers are drawn like any other;
    # within one layer they are "side" edges: no dummies, but they still pull
    # the two nodes next to each other.
    back_edges |= {(a, b) for index, (a, b) in enumerate(edges)
                   if index in soft_edges and layer[a] > layer[b]}
    side: dict[str, list[str]] = defaultdict(list)
    same_layer: set[int] = set()
    for index, (a, b) in enumerate(edges):
        if a != b and layer[a] == layer[b]:
            same_layer.add(index)
            side[a].append(b)
            side[b].append(a)

    # -- 3. dummy nodes -------------------------------------------------------
    chain_of: dict[int, list[str]] = {}
    adjacency: list[tuple[str, str]] = []
    dummy_count = 0
    all_nodes = list(ids)
    for index, (a, b) in enumerate(edges):
        if a == b or index in same_layer:
            continue
        u, v = (b, a) if (a, b) in back_edges else (a, b)
        chain = [u]
        for level in range(layer[u] + 1, layer[v]):
            dummy = f"\x00d{dummy_count}"
            dummy_count += 1
            layer[dummy] = level
            all_nodes.append(dummy)
            chain.append(dummy)
        chain.append(v)
        for x, y in zip(chain, chain[1:]):
            adjacency.append((x, y))
        chain_of[index] = chain if (a, b) not in back_edges else chain[::-1]

    layers: list[list[str]] = [[] for _ in range(max(layer.values()) + 1)]
    for node in all_nodes:
        layers[layer[node]].append(node)

    up: dict[str, list[str]] = defaultdict(list)
    down: dict[str, list[str]] = defaultdict(list)
    for x, y in adjacency:
        down[x].append(y)
        up[y].append(x)

    # A huge graph (a Heuristics net of a big log: thousands of τ bindings,
    # hundreds of thousands of route points) gets fewer refinement passes,
    # so it is drawn in seconds; a graph a person can read gets them all.
    work = len(all_nodes)
    if work > LARGE:
        sweeps = min(sweeps, 4 if work <= 10 * LARGE else 2)
    refinements = 24 if work <= LARGE else 8 if work <= 10 * LARGE else 4

    # -- 4. crossing reduction --------------------------------------------
    def position_map(current: list[list[str]]) -> dict[str, int]:
        return {n: i for level in current for i, n in enumerate(level)}

    def crossings(current: list[list[str]]) -> int:
        pos = position_map(current)
        return sum(count_inversions([b for _a, b in sorted((pos[x], pos[y]) for x in level for y in down[x])])
                   for level in current[:-1])

    best = [list(level) for level in layers]
    best_score = crossings(best)
    current = [list(level) for level in layers]
    for sweep in range(sweeps):
        pos = position_map(current)
        rng = range(1, len(current)) if sweep % 2 == 0 else range(len(current) - 2, -1, -1)
        for i in rng:
            neighbours = up if sweep % 2 == 0 else down
            def barycentre(node: str, i=i) -> float:
                linked = [pos[x] for x in neighbours[node]]
                # Same-layer partners count too, so a node sits beside them.
                linked += [pos[x] + 0.5 for x in side[node]]
                if not linked:
                    return pos[node]
                return sum(linked) / len(linked)
            current[i].sort(key=barycentre)
            for k, node in enumerate(current[i]):
                pos[node] = k
        score = crossings(current)
        if score < best_score:
            best, best_score = [list(level) for level in current], score
            if score == 0:
                break
    layers = best

    # -- 5. coordinates -----------------------------------------------------
    size = {n: nodes.get(n, (0.0, 0.0)) for n in all_nodes}
    widths = [max((size[n][0] for n in level), default=0) for level in layers]
    xs: list[float] = []
    cursor = 0.0
    for w in widths:
        xs.append(cursor + w / 2)
        cursor += w + layer_gap

    half = {n: size[n][1] / 2 for n in all_nodes}
    dummy = {n: n.startswith("\x00") for n in all_nodes}
    pads = {(True, True): node_gap * 0.45, (True, False): node_gap * 0.7, (False, True): node_gap * 0.7,
            (False, False): node_gap}

    def gap_between(a: str, b: str) -> float:
        return half[a] + half[b] + pads[(dummy[a], dummy[b])]

    y: dict[str, float] = {}
    for level in layers:
        cursor = 0.0
        for k, node in enumerate(level):
            if k:
                cursor += gap_between(level[k - 1], node)
            y[node] = cursor

    def pack(level: list[str], wanted: dict[str, float]) -> None:
        """Place nodes near their wanted y without overlap, preserving order."""
        placed: list[float] = []
        for k, node in enumerate(level):
            value = wanted[node]
            if k:
                value = max(value, placed[-1] + gap_between(level[k - 1], node))
            placed.append(value)
        # shift the block so that its mean displacement is zero
        shift = sum(wanted[n] - p for n, p in zip(level, placed)) / len(level)
        for k in range(len(level) - 1, -1, -1):
            candidate = placed[k] + shift
            if k < len(level) - 1:
                candidate = min(candidate, placed[k + 1] - gap_between(level[k], level[k + 1]))
            placed[k] = candidate
        for node, value in zip(level, placed):
            y[node] = value

    for iteration in range(refinements):
        forward = iteration % 2 == 0
        sequence = layers if forward else layers[::-1]
        for level in sequence:
            wanted = {}
            for node in level:
                linked = (up[node] if forward else down[node]) + \
                         (down[node] if forward else up[node]) + side[node]
                if linked:
                    values = sorted(y[x] for x in linked)
                    mid = len(values) // 2
                    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
                    wanted[node] = 0.5 * y[node] + 0.5 * median
                else:
                    wanted[node] = y[node]
            pack(level, wanted)

    top = min(y[n] - size[n][1] / 2 for n in all_nodes)
    positions = {n: (xs[layer[n]], y[n] - top) for n in ids}
    routes: dict[int, list[tuple[float, float]]] = {}
    for index, chain in chain_of.items():
        routes[index] = [(xs[layer[n]], y[n] - top) for n in chain]
    for index in same_layer:
        a, b = edges[index]
        routes[index] = [(xs[layer[a]], y[a] - top), (xs[layer[b]], y[b] - top)]
    height = max(y[n] - top + size[n][1] / 2 for n in all_nodes)
    return LayoutResult(positions, routes, xs[-1] + widths[-1] / 2, height)
