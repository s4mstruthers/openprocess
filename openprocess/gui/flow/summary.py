"""The *Summary* of an analysis: the key figure of every box, in one place.

Nothing here is computed on its own.  A tile is a box's result, as on the
canvas: a score's first metric, a table's size, a log's cases, a net's
places and transitions, in the order the boxes run, named after the box.  A
click on a tile opens that box's Result on the canvas.  The process map
under the tiles is the result of a *Directly-follows graph* box when the
analysis has one; when it has none, one click adds the box, fed by the
first log, so the map too is something you can click, read and re-run.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSlider, QSizePolicy, QVBoxLayout, QWidget

from ...flow.figures import tile_parts
from ...flow.types import DFG, EventLog
from ..studio import style
from ..studio.graph_builders import dfg_specs
from ..studio.graph_view import GraphView
from ..studio.widgets import Card, ElidedLabel, Legend, LegendSwatch, button, flow, hbox, label

#: How many tiles at most (an analysis with a sweep can have hundreds of results).
TILE_LIMIT = 24


class SummaryTile(QFrame):
    """One box's figure: the box (group and name), the figure, a line under it.  Clickable."""

    clicked = Signal(str)

    def __init__(self, node_id: str, group: str, caption: str, value: str, sub: str) -> None:
        super().__init__()
        self.node_id = node_id
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(170)
        self.setMaximumWidth(260)
        self.setToolTip(f"{caption}: open its Result on the canvas")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 10, 14, 12)
        column.setSpacing(2)
        column.addWidget(ElidedLabel(group.upper(), "sectionLabel"))
        column.addWidget(ElidedLabel(caption, "muted"))
        big = ElidedLabel(value)
        big.setObjectName("summaryValue")
        column.addWidget(big)
        column.addWidget(ElidedLabel(sub, "muted"))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.node_id)
            event.accept()
            return
        super().mousePressEvent(event)


class SummaryWidget(QWidget):
    """The Summary view of a workflow page; :meth:`refresh` rebuilds it from the run."""

    tile_clicked = Signal(str)

    def __init__(self, page, parent=None) -> None:
        super().__init__(parent)
        self.page = page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        intro = label("Every box's key figure, in run order. Click a tile to open its box.", "muted", wrap=True)
        intro.setToolTip("Nothing is computed here: a tile is the box's result, as on the canvas.")
        layout.addWidget(intro)
        self.tiles_host = QWidget()
        self.tiles_host.setObjectName("plain")
        self.tiles_layout = QVBoxLayout(self.tiles_host)
        self.tiles_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tiles_host)
        # The process map: a Directly-follows graph box's result.
        self.map_card = Card("Process map", "")
        self.map_info = label("", "muted")
        self.detail = QSlider(Qt.Horizontal)
        self.detail.setRange(0, 100)
        self.detail.setValue(100)
        self.detail.setMinimumWidth(90)
        self.detail.setMaximumWidth(220)
        self.detail.setToolTip("A view setting: hides the rarer activities and paths of the drawing. "
                               "The box's result is unchanged.")
        self.detail_label = label("100%", "muted")
        self.map_view = GraphView()
        self.map_view.setMinimumHeight(260)
        self.map_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.legend = Legend()
        self.map_card.add(hbox(label("Shown", "muted"), self.detail, self.detail_label, 16, self.map_info, None))
        self.map_card.add(self.map_view, 1)
        self.map_card.add(self.legend)
        self.map_card.setVisible(False)
        layout.addWidget(self.map_card, 1)
        # No such box yet: say so, and offer to add it.
        self.no_map_card = Card("Process map", "The process map is the result of a Directly-follows graph "
                                "box (Discover ▸), so it too can be clicked, read and re-run. This "
                                "analysis has none yet.")
        self.add_map_button = button("Add a Directly-follows graph box", self._add_map_box,
                                     tooltip="Adds the box, fed by the first log, and runs it")
        self.no_map_card.add(hbox(self.add_map_button, None))
        self.no_map_card.setVisible(False)
        layout.addWidget(self.no_map_card)
        layout.addStretch(0)
        self.detail.valueChanged.connect(lambda _v: self._redraw_map())
        self._dfg: DFG | None = None
        self.tiles: list[SummaryTile] = []

    # -- building ----------------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the tiles and the map from the page's current run."""
        page = self.page
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.tiles = []
        run = page.run
        tiles = []
        dfg = None
        has_log = False
        for node in page.workflow.order():
            result = run.result(node) if run is not None else None
            if result is None or result.status != "done":
                continue
            value = result.value
            if isinstance(value, EventLog):
                has_log = True
            if dfg is None and isinstance(value, DFG):
                dfg = (node, value)
            parts = tile_parts(value)
            if parts is None:
                continue
            tile = SummaryTile(node.id, page.workflow.spec(node).group, page.workflow.title(node.id), *parts)
            tile.clicked.connect(self.tile_clicked.emit)
            tiles.append(tile)
            if len(tiles) >= TILE_LIMIT:
                break
        self.tiles = tiles
        if tiles:
            self.tiles_layout.addWidget(flow(*tiles, spacing=10))
        else:
            hint = QLabel("No results yet. Add boxes (a log, a miner, a check) and run: each one's key "
                          "figure appears here.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            self.tiles_layout.addWidget(hint)
        self._dfg = None if dfg is None else dfg[1]
        self.map_card.setVisible(dfg is not None)
        self.no_map_card.setVisible(dfg is None and has_log)
        self.add_map_button.setEnabled(self._map_box_id() is not None)
        if dfg is not None:
            node, _value = dfg
            self.map_card.title_label.setText(f"Process map · the result of {page.workflow.title(node.id)}")
            self._redraw_map(fit=True)

    def _map_box_id(self) -> str | None:
        from .picker import input_box_for  # noqa: F401 - the same lookup style, for one more box
        return next((s.id for s in self.page.library.specs.values() if s.id.endswith(".directly_follows")), None)

    def _add_map_box(self) -> None:
        """Add a Directly-follows graph box, fed by the first log, and run it."""
        box_id = self._map_box_id()
        if box_id is None:
            return
        page = self.page
        source = next((n for n in page.workflow.order()
                       if page.run is not None and page.run.result(n) is not None
                       and isinstance(page.run.result(n).value, EventLog)), None)
        page.add_box_fed_by(box_id, source.id if source is not None else None)

    def _redraw_map(self, fit: bool = False) -> None:
        dfg = self._dfg
        if dfg is None:
            return
        detail = self.detail.value() / 100
        simplified = dfg.simplified(max(0.25, detail), detail)
        self.detail_label.setText(f"{self.detail.value()}%")
        nodes, edges = dfg_specs(simplified, "frequency")
        self.map_view.graph.populate(nodes, edges, layer_gap=64)
        self.map_info.setText(f"{len(simplified.activities)} of {len(dfg.activities)} activities · "
                              f"{len(simplified.all_edges())} of {len(dfg.all_edges())} paths")
        self.legend.set_items([
            (LegendSwatch("ramp"), "activity: darker = more events (count inside)"),
            (LegendSwatch("widths"), "thicker = path taken more often (label = times)"),
            (LegendSwatch("line", style.tokens().text_muted), "grey = rare path"),
            (LegendSwatch("dashed"), "case start / end"),
        ])
        if fit:
            QTimer.singleShot(0, self.map_view.fit)
        else:
            self.map_view.fit()


__all__ = ["SummaryWidget", "SummaryTile", "tile_parts"]
