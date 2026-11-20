"""Transition systems and their regions on screen.

:class:`RegionsPanel` shows one transition system with everything region
theory says about it, in the order an exam question asks:

1. the **transition system** itself, drawn with the state-graph canvas
   (states labelled by their abstraction, e.g. ``{a,d}``), with a menu to
   highlight the states one trace of the log passes through;
2. **Is this a region?** -- type a set of states, or click states on the
   drawing, and get yes or no; no names the event that breaks it and the two
   transitions that cross the set differently;
3. **Elementary?** -- state separation and forward closure, with the states
   or the event that break them;
4. **Events** -- for each event its GER, minimal pre-regions, the
   intersection of its pre-regions and its minimal post-regions (the table
   exam answers are written in);
5. the **regions**, the minimal ones numbered r1, r2, … as the places of
   6. the **synthesised net**, and whether its reachability graph is
   isomorphic to the transition system.

It is used in the Discover tab (*State-based regions*), in a discovered
model's Details, and on the page of a typed transition system
(:class:`TransitionSystemPage`).  Every property has its definition on hover
(:mod:`.definition_view`), filled in for this transition system
(:func:`.instances.regions`).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QLineEdit, QPlainTextEdit,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...mining.discovery.state_regions import RegionResult, region_result
from ...mining.regions import analyse_regions, check_region, format_states
from ...mining.transition_system import (
    TransitionSystem, parse_states, parse_transition_system,
)
from . import instances, style
from ..learn.concealment import ConcealsResults
from .definition_view import attach_definition
from .provenance import add_code
from .graph_view import EdgeSpec, GraphView, NodeSpec
from .widgets import Card, PageHeader, Verdict, button, hbox, label, scroll, status_for, \
    suggested_path


# ---------------------------------------------------------------------------
# Drawing a transition system
# ---------------------------------------------------------------------------
def ts_specs(ts: TransitionSystem) -> tuple[list[NodeSpec], list[EdgeSpec], dict[str, str]]:
    """Nodes and edges for the canvas, and the node id of each state."""
    t = style.tokens()
    ids = {state: f"s{number}" for number, state in enumerate(ts.states)}
    nodes = []
    for state in ts.states:
        roles = []
        if state in ts.initial:
            roles.append("initial")
        if state in ts.final:
            roles.append("final")
        nodes.append(NodeSpec(ids[state], "state", text=state,
                              tooltip=f"State {state}" + (f" ({', '.join(roles)})" if roles
                                                          else "") + "\nClick to add it to "
                              "the set to check", fill=t.accent_soft if state in ts.initial
                              else None, emphasis=state in ts.final))
    labels: dict[tuple[str, str], list[str]] = {}
    for source, event, target in ts.transitions:
        labels.setdefault((ids[source], ids[target]), []).append(event)
    edges = [EdgeSpec(s, d, text=", ".join(events), width=1.3, tooltip=", ".join(events))
             for (s, d), events in labels.items()]
    return nodes, edges, ids


class TransitionSystemView(QWidget):
    """The drawing, a trace highlighter, and click-to-select states."""

    #: The set of selected states changed (by clicking).
    selection_changed = Signal(list)

    def __init__(self, ts: TransitionSystem, parent=None) -> None:
        super().__init__(parent)
        self.ts = ts
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.view = GraphView()
        self.view.setMinimumHeight(260)
        self.nodes, edges, self.ids = ts_specs(ts)
        self.state_of = {node_id: state for state, node_id in self.ids.items()}
        self.view.graph.populate(self.nodes, edges, layer_gap=90)
        self.view.graph.node_clicked.connect(self._clicked)
        self.selected: list[str] = []
        self.trace_box = QComboBox()
        self.trace_box.addItem("Highlight a trace…", None)
        for sequence, count, walk in ts.traces:
            text = "⟨" + ", ".join(sequence) + "⟩" + (f"  ×{count}" if count > 1 else "")
            self.trace_box.addItem(text, walk)
        self.trace_box.currentIndexChanged.connect(lambda _i: self.repaint_states())
        self.trace_box.setVisible(bool(ts.traces))
        self.trace_box.setToolTip("Light up the states and transitions one trace passes "
                                  "through")
        self.trace_info = label("", "muted", wrap=True)
        layout.addWidget(self.view, 1)
        layout.addLayout(hbox(self.trace_box, self.trace_info, stretch_last=True))
        QTimer.singleShot(0, self.view.fit)

    def _clicked(self, node_id: str) -> None:
        state = self.state_of.get(node_id)
        if state is None:
            return
        if state in self.selected:
            self.selected.remove(state)
        else:
            self.selected.append(state)
        self.repaint_states()
        self.selection_changed.emit(list(self.selected))

    def set_selected(self, states: list[str]) -> None:
        self.selected = [s for s in states if s in self.ids]
        self.repaint_states()

    def repaint_states(self) -> None:
        t = style.tokens()
        walk = self.trace_box.currentData() or []
        on_path = set(walk)
        steps = {(self.ids[a], self.ids[b]) for a, b in zip(walk, walk[1:])}
        trace_colour = style.categorical(1)
        selected_fill = style.categorical(0)
        for spec in self.nodes:
            state = self.state_of[spec.id]
            fill = selected_fill if state in self.selected else (
                t.accent_soft if state in self.ts.initial else None)
            stroke = trace_colour if state in on_path else None
            spec_copy = NodeSpec(**{**spec.__dict__, "fill": fill, "stroke": stroke,
                                    "emphasis": spec.emphasis or state in on_path})
            self.view.graph.update_node(spec_copy)
        for edge in self.view.graph.edges:
            on = (edge.spec.source, edge.spec.target) in steps
            edge.spec.colour = trace_colour if on else None
            edge.spec.width = 2.6 if on else 1.3
            edge.prepareGeometryChange()
            edge.update()
        if walk:
            self.trace_info.setText(" → ".join(walk))
        else:
            self.trace_info.setText("")


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------
def _table(rows: list[list[str]], headers: list[str]) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setWordWrap(True)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            table.setItem(r, c, item)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.resizeRowsToContents()
    height = table.horizontalHeader().sizeHint().height() + sum(
        table.rowHeight(r) for r in range(len(rows))) + 2 * table.frameWidth() + 4
    table.setMinimumHeight(min(height, 360))
    table.setMaximumHeight(height + 20)
    return table


class RegionsPanel(ConcealsResults, QWidget):
    """Everything about one transition system's regions (see the module docstring)."""

    #: "Open as model" on the synthesised net: the window opens it.
    open_net = Signal(object)

    def __init__(self, result: RegionResult, show_net: bool = True, flat: bool = False,
                 parent=None) -> None:
        """``flat``: inside a card already (a derivation), so the sections get
        no frame of their own."""
        super().__init__(parent)
        self.result = result
        ts, analysis = result.ts, result.analysis
        self.ts = ts
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 1. the transition system
        caption = ts.summary() + (f" · {ts.abstraction}" if ts.abstraction else "") + \
            ". The initial state is shaded, final states have a thick outline."
        system = Card("Transition system", caption)
        attach_definition(system.caption_label, "transition_system",
                          instances.regions(analysis, "transition_system"))
        self.system_view = TransitionSystemView(ts)
        system.add(self.system_view, 1)
        layout.addWidget(system, 1)

        # 2. is this a region?
        checker = Card("Is this a region?", "Type states separated by commas, or click "
                       "them on the drawing.")
        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText(", ".join(ts.states[:3]) or "s0, s1")
        self.region_edit.returnPressed.connect(self.check_region)
        self.check_button = button("Check", self.check_region, kind="primary")
        checker.add(hbox(self.region_edit, self.check_button))
        self.check_host = QVBoxLayout()
        checker.add(self.check_host)
        self.system_view.selection_changed.connect(self._selection_changed)
        layout.addWidget(checker)

        if analysis.limit_message:
            layout.addWidget(Verdict("Limit reached", "warning", analysis.limit_message))

        # 3. elementary?
        elementary = Card("Elementary?", "A Petri net can mimic the transition system "
                          "exactly when any two states are separated by a region and every "
                          "event's pre-regions meet exactly where it is enabled.")
        self._fill_elementary(elementary)
        self.conceal_card(elementary, "regions")
        layout.addWidget(elementary)

        # 4. events
        events = Card("Events", "For each event: where it is enabled (GER), its minimal "
                      "pre-regions, the intersection of all its pre-regions, and its minimal "
                      "post-regions.")
        attach_definition(events.caption_label, "pre_region",
                          instances.regions(analysis, "pre_region"))
        order = ts.states
        rows = []
        for item in analysis.by_event.values():
            rows.append([item.event, format_states(item.ger, order),
                         ", ".join(format_states(r, order) for r in item.minimal_pre) or "–",
                         format_states(item.intersection, order)
                         + ("  ✓" if item.forward_closed else "  ✗ ≠ GER"),
                         ", ".join(format_states(r, order) for r in item.minimal_post) or "–"])
        events.add(_table(rows, ["Event", "GER", "Minimal pre-regions", "⋂ pre-regions",
                                 "Minimal post-regions"]))
        self.conceal_card(events, "regions")
        layout.addWidget(events)

        # 5. regions
        regions = Card("Regions", f"{len(analysis.regions)} non-trivial region(s), "
                       f"{len(analysis.minimal)} minimal. Each minimal region becomes a "
                       "place of the net.")
        add_code(regions, analyse_regions, check_region)
        attach_definition(regions.caption_label, "minimal_region",
                          instances.regions(analysis, "minimal_region"))
        lines = [f"r{number} = {format_states(region, order)}"
                 for number, region in enumerate(analysis.minimal, 1)]
        regions.add(label("\n".join(lines) or "No non-trivial regions.", wrap=True,
                          selectable=True))
        others = [r for r in analysis.regions if r not in set(analysis.minimal)]
        if others:
            shown = others[:60]
            text = "\n".join(format_states(r, order) for r in shown)
            if len(others) > len(shown):
                text += f"\n… and {len(others) - len(shown)} more"
            more = label("Other regions (unions of minimal ones and more):\n" + text, "muted",
                         wrap=True, selectable=True)
            regions.add(more)
        self.conceal_card(regions, "regions")
        layout.addWidget(regions)

        # 6. the net
        synthesis = result.synthesis
        net_card = Card("Synthesised net", "One place per minimal region, an arc from each "
                        "pre-region and to each post-region, a token in every minimal region "
                        "holding the initial state.")
        add_code(net_card, region_result)
        attach_definition(net_card.caption_label, "region_synthesis",
                          instances.regions(analysis, "region_synthesis", synthesis))
        for warning in result.warnings:
            net_card.add(Verdict("Note", "warning", warning))
        if synthesis.net is not None:
            if synthesis.isomorphic is not None:
                net_card.add(Verdict(
                    "Reachability graph ≅ transition system" if synthesis.isomorphic else
                    "Reachability graph ≇ transition system",
                    status_for(synthesis.isomorphic),
                    "the net behaves exactly like the transition system"
                    if synthesis.isomorphic else
                    "the net's reachability graph is not the transition system (it is not "
                    "elementary, or has self-loops)",
                    instance=instances.regions(analysis, "region_synthesis", synthesis)))
            if show_net:
                from .graph_builders import petri_net_specs
                preview = GraphView()
                preview.setMinimumHeight(220)
                nodes, edges = petri_net_specs(synthesis.net)
                preview.graph.populate(nodes, edges, layer_gap=48)
                net_card.add(preview)
                QTimer.singleShot(0, preview.fit)
                places = "\n".join(f"{p} = {format_states(region, order)}"
                                   for p, region in synthesis.places.items())
                net_card.add(label(places, "muted", wrap=True, selectable=True))
                net_card.add(hbox(button("Open as model  →", lambda: self.open_net.emit(
                    synthesis.net), kind="primary",
                    tooltip="Open the net next to your logs (conformance, soundness, "
                            "token game)"), None))
        self.conceal_card(net_card, "regions")
        layout.addWidget(net_card)
        layout.addStretch(0)
        if flat:
            layout.setContentsMargins(0, 0, 12, 0)       # clear of the scroll bar
            for card in self.findChildren(Card):
                card.setObjectName("flatCard")
                card.body.setContentsMargins(0, 4, 0, 4)

    def _fill_elementary(self, card: Card) -> None:
        analysis = self.result.analysis
        card.add(Verdict("State separation", status_for(analysis.state_separation),
                         "every two states are told apart by some region"
                         if analysis.state_separation else
                         "undecided (search limit)" if analysis.state_separation is None else "",
                         instance=instances.regions(analysis, "state_separation")))
        for pair in analysis.inseparable[:5]:
            card.add(label("• " + pair.explanation(), "muted", wrap=True, selectable=True))
        if len(analysis.inseparable) > 5:
            card.add(label(f"… and {len(analysis.inseparable) - 5} more pairs", "muted"))
        card.add(Verdict("Forward closure", status_for(analysis.forward_closure),
                         "for every event, its pre-regions meet exactly in GER(e)"
                         if analysis.forward_closure else
                         "undecided (search limit)" if analysis.forward_closure is None else "",
                         instance=instances.regions(analysis, "forward_closure")))
        order = self.ts.states
        for event, extra in analysis.closure_failures()[:5]:
            item = analysis.by_event[event]
            card.add(label(f"• {event}: ⋂ pre-regions = {format_states(item.intersection, order)}"
                           f" but GER({event}) = {format_states(item.ger, order)}; the net "
                           f"would also enable {event} in {format_states(extra, order)}.",
                           "muted", wrap=True, selectable=True))
        verdict = analysis.elementary
        card.add(Verdict("Elementary" if verdict or verdict is None else "Not elementary",
                         status_for(verdict),
                         "state separation and forward closure both hold" if verdict else "",
                         definition="elementary_ts",
                         instance=instances.regions(analysis, "elementary_ts")))

    # -- is this a region? ----------------------------------------------------------------
    def _selection_changed(self, states: list[str]) -> None:
        self.region_edit.setText(", ".join(states))
        if states:
            self.check_region()
        else:
            self._clear_check()

    def _clear_check(self) -> None:
        while self.check_host.count():
            item = self.check_host.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def check_region(self) -> None:
        states = parse_states(self.region_edit.text())
        self.system_view.set_selected(states)
        self._clear_check()
        result = check_region(self.ts, states)
        self.last_check = result
        if result.unknown:
            verdict = Verdict("Not a region", "critical", result.explanation(self.ts.states))
        else:
            verdict = Verdict("Region" if result.is_region else "Not a region",
                              status_for(result.is_region), result.explanation(self.ts.states),
                              instance=instances.region_check(result, self.ts))
        self.check_host.addWidget(verdict)


# ---------------------------------------------------------------------------
# A typed transition system as a page of its own
# ---------------------------------------------------------------------------
class TransitionSystemPage(ConcealsResults, QWidget):
    """Type a transition system (as in an exercise) and study its regions."""

    status = Signal(str)
    saved = Signal()
    #: A model to open (the synthesised net).
    open_model = Signal(object)

    def __init__(self, document, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.panel: RegionsPanel | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(document.name, "")
        self.header.actions.addWidget(button("Export…", self.export,
                                             tooltip="Save the transition system as a text "
                                                     "file"))
        root.addWidget(self.header)

        editor = Card("Transitions", "One per line, or separated by commas: s0 -a-> s1. "
                      "Optional lines: initial: s0, final: s7.")
        self.editor = QPlainTextEdit(document.ts.to_text())
        font = self.editor.font()
        font.setFamily("Menlo, Consolas, monospace")
        self.editor.setFont(font)
        self.editor.setMinimumHeight(160)
        editor.add(self.editor, 1)
        self.apply_button = button("Apply", self.apply, kind="primary",
                                   tooltip="Read the transitions again and recompute the "
                                           "regions")
        self.error = label("", "muted", wrap=True)
        editor.add(hbox(self.apply_button, self.error, stretch_last=True))
        editor.setMinimumWidth(240)

        self.panel_host = QWidget()
        self.panel_layout = QVBoxLayout(self.panel_host)
        self.panel_layout.setContentsMargins(0, 0, 4, 0)
        splitter = QSplitter()
        splitter.setHandleWidth(14)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        splitter.addWidget(editor)
        splitter.addWidget(scroll(self.panel_host))
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 900])
        body = QWidget()
        body.setLayout(hbox(splitter, margins=(20, 0, 20, 16)))
        root.addWidget(body, 1)
        self.refresh_title()
        self._compute()

    def refresh_title(self) -> None:
        where = Path(self.document.path).name if self.document.path else "not saved to a file"
        self.header.set_text(self.document.name,
                             f"Transition system · {self.document.ts.summary()} · {where}")

    def _compute(self) -> None:
        from .workers import run_in_background
        ts = self.document.ts
        self._show_panel(None)
        self.panel_layout.addWidget(label("Finding the regions…", "muted"))
        run_in_background(lambda: region_result(ts), self._show_panel,
                          lambda message: self.error.setText(message))

    def _show_panel(self, result) -> None:
        while self.panel_layout.count():
            item = self.panel_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._concealed_cards = []
        self.panel = None
        if result is None:
            return
        self.panel = RegionsPanel(result)
        self.panel.open_net.connect(self._open_net)
        self.panel.set_concealment(getattr(self, "concealment", None))
        self.panel_layout.addWidget(self.panel)

    def set_concealment(self, concealment) -> None:
        super().set_concealment(concealment)
        if self.panel is not None:
            self.panel.set_concealment(concealment)

    def _open_net(self, net) -> None:
        from .documents import ModelDocument
        net.name = f"Regions · {self.document.name}"
        self.open_model.emit(ModelDocument(net, origin=f"Regions of “{self.document.name}”",
                                           derivation=self.panel.result if self.panel else
                                           None))

    def apply(self) -> None:
        try:
            ts = parse_transition_system(self.editor.toPlainText(), self.document.name)
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.error.setText("")
        self.document.ts = ts
        self.refresh_title()
        self._compute()
        if self.document.path:
            self.write_to(self.document.path, quiet=True)

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export transition system",
            self.document.path or suggested_path(f"{self.document.name}.ts.txt"),
            "Transition system (*.txt)")
        if path:
            if not path.lower().endswith(".txt"):
                path += ".ts.txt"
            self.write_to(path)

    def write_to(self, path: str, quiet: bool = False) -> None:
        from .workspace import atomic_write
        text = self.document.ts.to_text() + "\n"
        atomic_write(path, lambda temporary: Path(temporary).write_text(text, encoding="utf-8"))
        self.document.path = path
        self.document.missing = False
        self.refresh_title()
        if not quiet:
            self.status.emit(f"Saved {Path(path).name}")
        self.saved.emit()
