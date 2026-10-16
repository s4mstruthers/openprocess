"""The model workspace: a Petri net with analysis and conformance checking.

Left: the canvas, with two views --

* **Model** -- the net itself.  With *Token game* on, enabled transitions
  are highlighted and clicking one fires it, so you can play out the firing
  rule by hand exactly as in the lectures;
* **State space** -- the reachability graph (or the coverability graph with
  ω when the net is unbounded).

Right: an inspector with three tabs --

* **Analysis** -- WF-net structure, soundness with counterexamples, and the
  classic behavioural properties;
* **Conformance** -- replay an open log: fitness (token replay and
  alignments), precision, generalisation, simplicity, per-variant alignments,
  and deviations painted onto the model;
* **Details** -- where the model came from, including the α-algorithm's
  derivation or the Inductive Miner's process tree.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QSlider, QGridLayout, QHeaderView, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget, QSplitter,
)

from ...mining.analysis import (analyse, check_soundness, coverability_graph, reachability_graph,
                               short_circuit)
from ...mining.conformance.alignments import align_log
from ...mining.conformance.quality import generalisation, precision, simplicity
from ...mining.conformance.token_replay import token_replay
from ...mining.petrinet import Marking
from ...mining.pnml import write_pnml
from .. import theme
from . import instances, style
from ..learn.concealment import ConcealsResults
from .documents import ModelDocument
from .graph_builders import petri_net_specs, state_graph_specs
from .graph_view import GraphView
from .log_page import _item, _table, derivation_widget
from .provenance import add_code
from .widgets import (
    suggested_path,
    Card, ElidedLabel, PageHeader, SegmentedControl, StatTile, Verdict, button, fitness_status, flow, hbox, label,
    paint_chips, scroll, status_for, vbox,
)
from .workers import run_in_background


def _tool(text: str, tooltip: str, checkable: bool = False) -> QToolButton:
    tool = QToolButton()
    tool.setObjectName("canvasTool")
    tool.setText(text)
    tool.setToolTip(tooltip)
    tool.setCheckable(checkable)
    tool.setCursor(Qt.PointingHandCursor)
    return tool


class AlignmentStrip(QWidget):
    """One alignment as a row of chips: sync, log move (a ≫), model move (≫ a)."""

    def __init__(self) -> None:
        super().__init__()
        self.moves = []
        self.setMinimumHeight(34)

    def set_moves(self, moves) -> None:
        self.moves = [m for m in moves if m.kind != "silent"]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = style.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        chips = []
        for move in self.moves:
            if move.kind == "sync":
                chips.append((move.log, t.surface_alt, t.border))
            elif move.kind == "log":
                chips.append((f"{move.log} ≫", style.categorical(1), None))
            else:
                chips.append((f"≫ {move.model}", style.categorical(6), None))
        font = painter.font()
        theme.set_px(font, 10.5)
        paint_chips(painter, QRectF(self.rect()).adjusted(0, 4, 0, -4), chips, font, 140)


class ModelPage(ConcealsResults, QWidget):
    status = Signal(str)
    #: A play-out produced a new event log (the window adds it to the sidebar).
    log_generated = Signal(object)
    #: Emitted after the model was exported as PNML; it now lives in that file.
    saved = Signal()
    #: "Edit" was pressed: open this net (a copy) in the Petri net editor.
    edit_requested = Signal(object)
    #: "Keep" was pressed: save this discovered model into the open folder.
    keep_requested = Signal()

    #: "Check against a log ›" (the studio's conformance tab): the window builds the analysis in Mine.
    check_requested = Signal()

    def __init__(self, document: ModelDocument, open_logs, parent=None, conformance: str = "inline") -> None:
        """``open_logs`` is a callable returning the currently open LogDocuments.
        ``conformance``: "inline" replays logs on this page (Learn, where the
        exercise is the context); "handoff" (the studio) sends it to an
        analysis in Mine, where every step is shown and recorded."""
        super().__init__(parent)
        self.document = document
        self.conformance_mode = conformance
        self.net = document.net
        self.open_logs = open_logs
        self.marking: Marking = self.net.initial_marking
        self.overlay_badges: dict = {}
        self.conformance = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(self.net.name, self._subtitle())
        # Shown by the window while a discovered model is not in the open folder yet.
        self.keep_button = button("Keep", self.keep_requested.emit, kind="primary",
                                  tooltip="Save this model into the folder as a PNML file")
        self.keep_button.setVisible(False)
        self.header.actions.addWidget(self.keep_button)
        self.header.actions.addWidget(button("✎ Edit a copy", lambda: self.edit_requested.emit(
            self.net), tooltip="Open this net in the Petri net editor to change it"))
        self.header.actions.addWidget(button("Export PNML…", self.export))
        self.header.actions.addWidget(button("Export image…", self._export_image))
        root.addWidget(self.header)

        splitter = QSplitter()
        splitter.setHandleWidth(14)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        splitter.addWidget(self._build_canvas())
        splitter.addWidget(self._build_inspector())
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([880, 430])
        wrapper = QWidget()
        wrapper.setLayout(vbox(splitter, margins=(20, 0, 20, 16)))
        root.addWidget(wrapper, 1)
        self.redraw(fit=True)

    def _subtitle(self) -> str:
        return f"{self.document.origin or 'Petri net'} · {self.net.summary()}"

    # ================================================================ canvas
    MODES = ["View", "Step through", "Simulate"]

    def _build_canvas(self) -> QWidget:
        card = Card()
        self.view_switch = SegmentedControl(["Model", "State space"])
        self.mode_switch = SegmentedControl(self.MODES)
        self.mode_switch.setToolTip("View: the model with analysis overlays\n"
                                    "Step through: fire transitions one at a time\n"
                                    "Simulate: let the model run by itself")
        self.place_names = _tool("Labels", "Show place names", checkable=True)
        self.place_names.setChecked(len(self.net.places) <= 40 and self.net.info.get(
            "algorithm", "").startswith("α"))
        self.sim_status = ElidedLabel("", "muted")
        top = hbox(self.view_switch, 12, self.mode_switch, 12, self.sim_status, self.place_names)
        top.setStretch(4, 1)
        card.add(top)

        # -- simulation bar (hidden in View mode) ------------------------------------
        self.back_button = _tool("◀ Back", "Undo the last firing")
        self.step_button = _tool("Step ▶", "Fire one random enabled transition")
        self.play_button = _tool("▶ Play", "Fire random enabled transitions automatically",
                                 checkable=True)
        self.reset_button = _tool("⟲ Reset", "Back to the initial marking")
        self.repeat_box = QCheckBox("Repeat")
        self.repeat_box.setToolTip("When a run ends, start a new one automatically")
        self.repeat_box.setChecked(True)
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 30)          # firings per second
        self.speed.setValue(4)
        self.speed.setFixedWidth(110)
        self.speed.setToolTip("Speed: firings per second")
        self.generate_button = _tool("Mine a simulated log ›", "Play the model out many times and "
                                     "add the resulting event log to the sidebar")
        self.sim_hint = label("", "muted", wrap=True)
        self.simulate_widgets = [self.play_button, self.speed, self.speed_label_widget(),
                                 self.repeat_box, self.generate_button]
        # A flow layout: on a narrow window the bar wraps instead of widening the page.
        bar = flow(self.reset_button, self.back_button, self.step_button, 12,
                   *self.simulate_widgets)
        self.sim_bar = bar
        card.add(bar)
        card.add(self.sim_hint)

        self.canvas_stack = QStackedWidget()
        self.view = GraphView()
        self.state_view = GraphView()
        self.state_info = label("", "muted", wrap=True)
        state_host = QWidget()
        state_host.setLayout(vbox(self.state_info, self.state_view))
        self.canvas_stack.addWidget(self.view)
        self.canvas_stack.addWidget(state_host)
        card.add(self.canvas_stack, 1)

        self.sequence_label = label("", "muted", wrap=True, selectable=True)
        card.add(self.sequence_label)

        # -- state of the simulation ------------------------------------------------------
        self.history: list[tuple[Marking, str]] = []     # (marking before, transition fired)
        self.runs_completed = 0
        self.last_fired: str | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        self.view.graph.node_clicked.connect(self._node_clicked)
        self.mode_switch.changed.connect(self._mode_changed)
        self.back_button.clicked.connect(self._back)
        self.step_button.clicked.connect(self._step_random)
        self.play_button.toggled.connect(self._play_toggled)
        self.reset_button.clicked.connect(self._reset_game)
        self.speed.valueChanged.connect(self._speed_changed)
        self.generate_button.clicked.connect(self._generate_log)
        self.place_names.toggled.connect(lambda _: self.redraw(fit=False))
        self.view_switch.changed.connect(self._switch_view)
        self._mode_changed(0)
        return card

    def speed_label_widget(self) -> QWidget:
        self.speed_label = label("4 / s", "muted")
        return self.speed_label

    @property
    def simulating(self) -> bool:
        return self.mode_switch.index() in (1, 2)

    # -- drawing ---------------------------------------------------------------------------
    def redraw(self, fit: bool = False) -> None:
        simulating = self.simulating
        enabled = set(self.net.enabled(self.marking)) if simulating else set()
        fills: dict[str, str] = {}
        if simulating and self.last_fired:
            fills[self.last_fired] = style.tokens().accent_soft
        nodes, edges = petri_net_specs(
            self.net, marking=self.marking, enabled=enabled,
            badges=self.overlay_badges if not simulating else {},
            fills=fills, show_place_names=self.place_names.isChecked())
        positions = None
        if all(p.position for p in self.net.places.values()) and \
                all(t.position for t in self.net.transitions.values()):
            positions = {p.id: p.position for p in self.net.places.values()}
            positions |= {t.id: t.position for t in self.net.transitions.values()}
        if fit or not self.view.graph.nodes:
            self.view.graph.populate(nodes, edges, positions, layer_gap=48)
            if fit:
                self.view.fit()
        else:
            for spec in nodes:
                self.view.graph.update_node(spec)
            for edge in self.view.graph.edges:
                edge.rebuild()
        self._update_sim_text(enabled)

    def _update_sim_text(self, enabled: set[str]) -> None:
        if not self.simulating:
            self.sequence_label.setText("")
            self.sim_status.setText("")
            return
        names = [self.net.transitions[t].name for _, t in self.history]
        shown = names[-30:]
        prefix = "… → " if len(names) > 30 else ""
        self.sequence_label.setText(
            f"Marking {self.marking.describe(self.net)}   ·   fired: "
            + (prefix + " → ".join(shown) if names else "nothing yet"))
        at_final = bool(self.net.final_marking) and self.marking == self.net.final_marking
        if at_final:
            state = "✓ final marking reached"
        elif not enabled:
            state = "✕ dead marking: nothing is enabled"
        else:
            state = f"{len(enabled)} enabled"
        runs = f" · {self.runs_completed} run(s) completed" if self.mode_switch.index() == 2 else ""
        self.sim_status.setText(f"Step {len(self.history)} · {state}{runs}")
        self.back_button.setEnabled(bool(self.history))
        self.step_button.setEnabled(bool(enabled))

    # -- modes ---------------------------------------------------------------------------------
    def _mode_changed(self, index: int) -> None:
        self.timer.stop()
        self.play_button.setChecked(False)
        self.sim_bar.setVisible(index != 0)
        for widget in self.simulate_widgets:
            widget.setVisible(index == 2)
        self.sim_hint.setVisible(index != 0)
        self.sim_hint.setText(
            "Green transitions are enabled. Click one to fire it, or press Step to fire a "
            "random one. Back undoes a firing." if index == 1 else
            "Press Play to let the model fire random enabled transitions by itself. The last "
            "transition that fired is shaded blue; with Repeat on, a new run starts at the "
            "end. Generate event log turns many runs into a log you can mine.")
        if index == 0:
            self.marking = self.net.initial_marking
            self.history.clear()
            self.last_fired = None
        if index != 0 and self.view_switch.index() != 0:
            self.view_switch.set_index(0)
        self.redraw()

    # -- firing ------------------------------------------------------------------------------------
    def _fire(self, transition_id: str) -> None:
        self.history.append((self.marking, transition_id))
        self.marking = self.net.fire(self.marking, transition_id)
        self.last_fired = transition_id
        self.redraw()

    def _step_random(self) -> bool:
        enabled = self.net.enabled(self.marking)
        if not enabled:
            return False
        import random as _random
        self._fire(_random.choice(enabled))
        return True

    def _back(self) -> None:
        if self.history:
            self.marking, _ = self.history.pop()
            self.last_fired = self.history[-1][1] if self.history else None
            self.redraw()

    def _reset_game(self) -> None:
        self.marking = self.net.initial_marking
        self.history.clear()
        self.last_fired = None
        self.redraw()

    def _node_clicked(self, node_id: str) -> None:
        if node_id not in self.net.transitions:
            return
        if not self.simulating:
            self.status.emit("Switch to Step through to fire transitions by clicking them.")
            return
        if self.net.is_enabled(self.marking, node_id):
            self._fire(node_id)
        else:
            self.status.emit(f"“{self.net.transitions[node_id].name}” is not enabled in "
                             f"{self.marking.describe(self.net)}")

    # -- automatic simulation ---------------------------------------------------------------
    def _speed_changed(self, value: int) -> None:
        self.speed_label.setText(f"{value} / s")
        self.timer.setInterval(int(1000 / value))

    def _play_toggled(self, on: bool) -> None:
        self.play_button.setText("⏸ Pause" if on else "▶ Play")
        if on:
            self.timer.start(int(1000 / self.speed.value()))
        else:
            self.timer.stop()

    def _tick(self) -> None:
        at_final = bool(self.net.final_marking) and self.marking == self.net.final_marking
        if not at_final and self._step_random():
            return
        # The run is over: at the final marking, or stuck.
        if at_final:
            self.runs_completed += 1
        if self.repeat_box.isChecked():
            self._reset_game()
        else:
            self.play_button.setChecked(False)
            self.redraw()

    def _generate_log(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        from ...mining.playout import play_out
        count, ok = QInputDialog.getInt(self, "Generate event log",
                                        "Number of cases to simulate:", 100, 1, 100_000, 50)
        if not ok:
            return
        net = self.net

        def done(result) -> None:
            self.status.emit(f"Generated {len(result.log):,} cases: {result.completed:,} "
                             f"completed, {result.deadlocked:,} deadlocked, "
                             f"{result.cut_off:,} cut off")
            self.log_generated.emit(result.log)

        run_in_background(lambda: play_out(net, traces=count, name=f"Play-out · {net.name}"),
                          done, lambda m: self.status.emit(f"Play-out failed: {m}"))

    def _switch_view(self, index: int) -> None:
        self.canvas_stack.setCurrentIndex(index)
        if index == 1 and not self.state_view.graph.nodes:
            self.state_info.setText("Computing state space…")
            net = self.net

            def compute():
                graph = reachability_graph(net, max_states=20_000)
                if graph.has_omega:
                    graph = coverability_graph(net, max_states=20_000)
                return graph

            def done(graph) -> None:
                nodes, edges = state_graph_specs(graph)
                self.state_view.graph.populate(nodes, edges, layer_gap=120)
                self.state_view.fit()
                kind = "Coverability graph (unbounded net: ω = any number)" \
                    if graph.kind == "coverability" else "Reachability graph"
                extra = ""
                if len(graph.states) > len(nodes):
                    extra = f" — showing the first {len(nodes)} of {len(graph.states)} states"
                if graph.truncated and not graph.has_omega:
                    extra += " (exploration stopped at the state limit)"
                dead = len(graph.dead_states())
                self.state_info.setText(
                    f"{kind}: {len(graph.states):,} states, {len(graph.edges):,} edges, "
                    f"{dead} dead state(s){extra}. Initial state is shaded; dead states "
                    "other than the final marking are outlined in red.")
            run_in_background(compute, done, lambda m: self.state_info.setText(m))

    # ============================================================= inspector
    def _build_inspector(self) -> QWidget:
        host = QWidget()
        host.setMinimumWidth(300)
        host.setMaximumWidth(560)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        tabs = SegmentedControl(["Analysis", "Conformance", "Details"])
        self.inspector_tabs = tabs
        layout.addWidget(tabs)
        stack = QStackedWidget()
        stack.addWidget(scroll(self._build_analysis()))
        stack.addWidget(scroll(self._build_conformance()))
        stack.addWidget(scroll(self._build_details()))
        tabs.changed.connect(stack.setCurrentIndex)
        layout.addWidget(stack, 1)
        return host

    # --- analysis -----------------------------------------------------------
    def _build_analysis(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        self.soundness_card = Card("Soundness", "Classical soundness of the WF-net: option "
                                   "to complete, proper completion, no dead transitions. "
                                   "Hover over a property (ⓘ) for its definition.")
        add_code(self.soundness_card, check_soundness, short_circuit, analyse)
        self.properties_card = Card("Behavioural properties")
        add_code(self.properties_card, analyse, reachability_graph, coverability_graph)
        check = button("Run analysis", self._run_analysis, kind="primary")
        layout.addWidget(check)
        layout.addWidget(self.soundness_card)
        layout.addWidget(self.properties_card)
        layout.addStretch(1)
        self.conceal_card(self.soundness_card, "soundness")
        self.conceal_card(self.properties_card, "properties")
        self._run_analysis()
        return page

    @staticmethod
    def _clear(card: Card) -> None:
        card.clear()

    def _run_analysis(self) -> None:
        net = self.net
        self._clear(self.soundness_card)
        self._clear(self.properties_card)
        self.soundness_card.add(label("Checking…", "muted"))

        def compute():
            soundness = check_soundness(net)
            workflow = soundness.workflow
            if workflow.is_workflow_net:
                # Soundness theorem: analyse the short-circuited net N̄ instead, where
                # liveness and boundedness together are equivalent to soundness.
                closed = short_circuit(net, workflow.source, workflow.sink)
                return soundness, analyse(closed, max_states=50_000), True
            return soundness, analyse(net, max_states=50_000), False

        def done(result) -> None:
            soundness, properties, closed = result
            self._clear(self.soundness_card)
            self._clear(self.properties_card)
            if closed:
                self.properties_card.add(label(
                    "Computed on the short-circuited net N̄ (this net plus a silent t* from "
                    "the sink back to the source, starting from [source]). Soundness "
                    "theorem: the WF-net is sound ⇔ N̄ is live and bounded.", "muted",
                    wrap=True))
            workflow = soundness.workflow
            self.soundness_card.add(Verdict(
                "WF-net structure", status_for(workflow.is_workflow_net),
                "one source, one sink, every node on a path between them"
                if workflow.is_workflow_net else "; ".join(workflow.problems),
                instance=instances.soundness(soundness, "wf_net")))
            if workflow.is_workflow_net:
                verdict = soundness.sound
                self.soundness_card.add(Verdict(
                    "Sound" if verdict else ("Not sound" if verdict is False else "Undecided"),
                    status_for(verdict),
                    "every case can complete properly and every transition can fire"
                    if verdict else "", instance=instances.soundness(soundness, "sound")))
                for name, key, value in (
                        ("(i) Option to complete", "option_to_complete",
                         soundness.option_to_complete),
                        ("(ii) Proper completion", "proper_completion",
                         soundness.proper_completion),
                        ("(iii) No dead transitions", "no_dead_transitions",
                         soundness.no_dead_transitions)):
                    self.soundness_card.add(Verdict(
                        name, status_for(value), "", definition=key,
                        instance=instances.soundness(soundness, key)))
                for finding in soundness.findings:
                    self.soundness_card.add(label("• " + finding, "muted", wrap=True, selectable=True))
            from ...mining.definitions import FOR_TITLE
            for name, value, detail in properties.lines():
                key = FOR_TITLE.get(name)
                if closed and key in ("bounded", "safe", "live", "deadlock_free"):
                    filled = instances.short_circuit(soundness, key)
                else:
                    filled = instances.properties(properties, key) if key else None
                self.properties_card.add(Verdict(name, status_for(value), detail,
                                                 instance=filled))
            self.properties_card.add(label(
                f"{len(properties.graph.states):,} reachable markings explored "
                f"({properties.graph.kind} graph).", "muted", wrap=True))

        run_in_background(compute, done, lambda m: self.soundness_card.add(
            Verdict("Analysis failed", "critical", m)))

    # --- conformance ----------------------------------------------------------
    def _build_conformance(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        if self.conformance_mode == "handoff":
            card = Card("Check against a log", "Conformance happens on the canvas, where you can see "
                        "what is done: Check fit's scores, how each case replayed, the code, and a "
                        "record that runs again. Pick a log; the analysis is built for you, with "
                        "this model already connected.")
            self.check_log_button = button("Check against a log  →", self.check_requested.emit, kind="primary",
                                           tooltip="A new analysis in Mine: Open net (this model), Open log, "
                                                   "Check fit, connected and run")
            card.add(hbox(self.check_log_button, None))
            card.add(label("Then add Token replay or Alignments for the per-case view, Soundness, or a "
                           "comparison with another model.", "muted", wrap=True))
            layout.addWidget(card)
            layout.addStretch(1)
            return page

        chooser = Card("Replay a log", "Measures how well this model and a log agree.")
        add_code(chooser, token_replay, align_log, precision, generalisation, simplicity)
        self.log_box = QComboBox()
        self.refresh_logs()
        self.run_conformance = button("Check conformance", self._check, kind="primary")
        chooser.add(self.log_box)
        chooser.add(hbox(self.run_conformance, None))
        layout.addWidget(chooser)

        self.metric_tiles = {
            "align": StatTile("Fitness · alignments"),
            "tbr": StatTile("Fitness · token replay"),
            "precision": StatTile("Precision"),
            "generalisation": StatTile("Generalisation"),
            "simplicity": StatTile("Simplicity"),
            "fitting": StatTile("Fitting cases"),
        }
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, tile in enumerate(self.metric_tiles.values()):
            grid.addWidget(tile, index // 2, index % 2)
        metrics = Card(padding=0)
        metrics.setObjectName("plain")
        metrics.add(grid)
        layout.addWidget(metrics)
        self.conceal_card(metrics, "conformance")

        overlay = Card("Show on model")
        self.overlay_switch = SegmentedControl(["Nothing", "Token replay", "Alignments"])
        self.overlay_switch.changed.connect(self._apply_overlay)
        overlay.add(self.overlay_switch)
        self.overlay_legend = label("", "muted", wrap=True)
        overlay.add(self.overlay_legend)
        layout.addWidget(overlay)

        variants = Card("Variants", "Select a variant to see its optimal alignment.")
        self.variants_card = variants
        self.variant_model = QStandardItemModel(0, 4)
        self.variant_model.setHorizontalHeaderLabels(["Cases", "Fitness", "Deviations", "Trace"])
        self.variant_table = _table(self.variant_model)
        self.variant_table.setMinimumHeight(220)
        header = self.variant_table.horizontalHeader()
        for c in range(3):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        variants.add(self.variant_table)
        self.alignment_strip = AlignmentStrip()
        variants.add(self.alignment_strip)
        legend = label("Chips: plain = synchronous move · “a ≫” = log move (in the log, "
                       "not allowed by the model) · “≫ a” = model move (required by the "
                       "model, missing in the log). Silent τ moves are hidden.", "muted", wrap=True)
        variants.add(legend)
        self.variant_table.selectionModel().currentRowChanged.connect(self._show_alignment)
        layout.addWidget(variants)
        self.conceal_card(overlay, "conformance")
        self.conceal_card(variants, "conformance")
        layout.addStretch(1)
        return page

    def refresh_logs(self) -> None:
        if not hasattr(self, "log_box"):              # the handoff tab has no log to choose
            return
        current = self.log_box.currentData() if self.log_box.count() else None
        self.log_box.clear()
        logs = self.open_logs()
        for document in logs:
            self.log_box.addItem(document.name, document)
        preferred = current or self.document.source_log
        if preferred is not None:
            index = next((i for i in range(self.log_box.count())
                          if self.log_box.itemData(i) is preferred), 0)
            self.log_box.setCurrentIndex(index)
        if hasattr(self, "run_conformance"):
            self.run_conformance.setEnabled(bool(logs))

    def _check(self) -> None:
        document = self.log_box.currentData()
        if document is None:
            return
        net = self.net
        simple = document.simple_log()
        self.run_conformance.setEnabled(False)
        self.run_conformance.setText("Checking…")
        for tile in self.metric_tiles.values():
            tile.set("…")

        def compute():
            replay = token_replay(net, simple)
            alignments = align_log(net, simple)
            return (replay, alignments, precision(net, simple), generalisation(replay),
                    simplicity(net))

        def done(result) -> None:
            self.run_conformance.setEnabled(True)
            self.run_conformance.setText("Check conformance")
            replay, alignments, prec, gen, simp = result
            self.conformance = (replay, alignments)
            tiles = self.metric_tiles
            tiles["align"].set(f"{alignments.average_fitness:.3f}",
                               f"log-level {alignments.log_fitness:.3f}")
            tiles["tbr"].set(f"{replay.fitness:.3f}",
                             f"m {replay.missing:,} · r {replay.remaining:,} · "
                             f"c {replay.consumed:,} · p {replay.produced:,}")
            tiles["precision"].set(f"{prec:.3f}", "escaping edges (ETC)")
            tiles["generalisation"].set(f"{gen:.3f}", "token-based")
            tiles["simplicity"].set(f"{simp:.3f}", "arc degree")
            total = alignments.trace_count
            tiles["fitting"].set(f"{alignments.fitting_traces:,} / {total:,}",
                                 f"{alignments.fitting_traces / max(total, 1):.0%} replay perfectly")
            self.variant_model.removeRows(0, self.variant_model.rowCount())
            for alignment in sorted(alignments.alignments, key=lambda a: (a.fitness, -a.count)):
                trace_item = _item(" → ".join(alignment.trace) or "⟨⟩", data=alignment)
                self.variant_model.appendRow([
                    _item(f"{alignment.count:,}", True), _item(f"{alignment.fitness:.3f}", True),
                    _item(alignment.cost if alignment.complete else "?", True), trace_item])
            if self.variant_model.rowCount():
                self.variant_table.selectRow(0)
            # A search that gave up (an unbounded net, say) says so where the "?" is.
            self.variants_card.caption_label.setText(
                " ".join(alignments.notes) if alignments.notes
                else "Select a variant to see its optimal alignment.")
            self.overlay_switch.set_index(2 if alignments.fitting_traces < total else 0)
            self.status.emit(f"Conformance of “{document.name}”: fitness "
                             f"{alignments.average_fitness:.3f}, precision {prec:.3f}")

        def failed(message: str) -> None:
            self.run_conformance.setEnabled(True)
            self.run_conformance.setText("Check conformance")
            self.status.emit(f"Conformance failed: {message}")

        run_in_background(compute, done, failed)

    def _show_alignment(self, current, _previous=None) -> None:
        if not current.isValid():
            return
        alignment = self.variant_model.item(current.row(), 3).data(Qt.UserRole)
        self.alignment_strip.set_moves(alignment.moves)

    def _apply_overlay(self, index: int) -> None:
        self.overlay_badges = {}
        self.overlay_legend.setText("")
        if self.conformance is not None and index == 1:
            replay, _ = self.conformance
            missing, remaining = replay.place_totals()
            for place in self.net.places:
                badges = []
                if missing[place]:
                    badges.append((f"−{missing[place]:,}", style.STATUS["critical"]))
                if remaining[place]:
                    badges.append((f"+{remaining[place]:,}", style.STATUS["serious"]))
                if badges:
                    self.overlay_badges[place] = badges
            self.overlay_legend.setText("“−n” on a place: n tokens were missing (the log did "
                                        "something the model did not allow). “+n”: n tokens "
                                        "were left behind (the model expected more).")
        elif self.conformance is not None and index == 2:
            _, alignments = self.conformance
            sync, log_moves, model_moves = alignments.move_statistics()
            for transition in self.net.transitions.values():
                if transition.label is None:
                    continue
                badges = []
                if model_moves.get(transition.label):
                    badges.append((f"≫ {model_moves[transition.label]:,}", style.categorical(6)))
                if log_moves.get(transition.label):
                    badges.append((f"{log_moves[transition.label]:,} ≫", style.categorical(1)))
                if badges:
                    self.overlay_badges[transition.id] = badges
            unknown = {a: n for a, n in log_moves.items() if a not in self.net.labels()}
            text = ("“≫ n”: the model had to do this n times without the log (model moves). "
                    "“n ≫”: the log did this n times where the model could not (log moves).")
            if unknown:
                text += " Activities missing from the model entirely: " + ", ".join(
                    f"{a} ({n:,})" for a, n in sorted(unknown.items()))
            self.overlay_legend.setText(text)
        self.redraw(fit=False)

    # --- details -----------------------------------------------------------
    def _build_details(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        info = Card("Model")
        rows = [("Origin", self.document.origin or "–"), ("Size", self.net.summary()),
                ("Initial marking", self.net.initial_marking.describe(self.net)),
                ("Final marking", self.net.final_marking.describe(self.net))]
        rows += [(k.capitalize(), v) for k, v in self.net.info.items() if k != "algorithm"]
        for key, value in rows:
            info.add(vbox(label(key, "sectionLabel"), label(value, wrap=True, selectable=True),
                          spacing=2))
        layout.addWidget(info)
        if self.document.derivation is not None:
            derivation = Card("Derivation")
            widget = derivation_widget(self.document.derivation)
            widget.setMinimumHeight(420)
            derivation.add(widget)
            layout.addWidget(derivation)
        layout.addStretch(1)
        return page

    # ================================================================ export
    def refresh_title(self) -> None:
        self.header.set_text(self.net.name, self._subtitle())

    def export(self) -> None:
        """Save as PNML (with the current layout).  The document then refers to
        that file, so it survives removal and is reopened at the next launch."""
        path, _ = QFileDialog.getSaveFileName(self, "Export Petri net", suggested_path(f"{self.net.name}.pnml"),
                                              "PNML (*.pnml)")
        if path:
            self.write_to(path)

    def write_to(self, path: str, quiet: bool = False) -> None:
        """Save as PNML at ``path``, with the current layout; it is then that file."""
        from .workspace import atomic_write
        positions = {node_id: (item.pos().x(), item.pos().y())
                     for node_id, item in self.view.graph.nodes.items()}
        atomic_write(path, lambda temporary: write_pnml(self.net, temporary, positions))
        self.document.path = path
        self.document.missing = False
        self.keep_button.setVisible(False)
        if not quiet:
            self.status.emit(f"Saved {path}")
        self.saved.emit()

    def _export_image(self) -> None:
        path, chosen = QFileDialog.getSaveFileName(self, "Export image", suggested_path(f"{self.net.name}.png"),
                                                   "PNG image (*.png);;SVG drawing (*.svg)")
        if not path:
            return
        # The picture is the model, not the session: no green for the
        # transitions enabled now, no overlay badges, no fill on the one that
        # fired last, no hover outline.  The tokens stay.
        nodes, _edges = petri_net_specs(self.net, marking=self.marking,
                                        show_place_names=self.place_names.isChecked())
        for spec in nodes:
            self.view.graph.update_node(spec)
        for item in self.view.graph.nodes.values():
            item.hovered = False
        try:
            if path.lower().endswith(".svg"):
                self.view.export_svg(path)
            else:
                self.view.export_png(path)
        finally:
            self.redraw(fit=False)
        self.status.emit(f"Saved {path}")
