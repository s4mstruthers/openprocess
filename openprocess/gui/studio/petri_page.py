"""The Petri net editor: draw a plain Petri net or WF-net and analyse it.

This is the editor for the nets of the course -- black tokens, arc weights,
silent τ transitions -- as opposed to coloured nets.  It is the coloured-net
page (:class:`~openprocess.gui.studio.cpn_page.CpnPage`) with a simpler element
form and, instead of the CPN state space tool, an **Analysis** tab:

* **Soundness**: is it a WF-net?  Option to complete, proper completion, no
  dead transitions -- and for every violation a counterexample, which
  *Show* replays on the net in the token game.
* **Short-circuited net N̄** (plus t* from o to i): the soundness theorem
  says the net is sound iff (N̄, [i]) is live and bounded.
* **Structure**: free-choice, well-structured, S-coverable, the start and
  end rule.
* **Invariants**: the incidence matrix, and the P- and T-invariants (of N̄
  for a WF-net), which prove boundedness or unsoundness without a state space.
* **Behavioural properties** of the net as drawn: bounded / safe, dead
  transitions, deadlocks, liveness, reversibility.
* **Footprint** of the net's behaviour (the → ← ‖ # matrix of the α
  chapter), to compare with a log's footprint.
* The **reachability graph**, and **conformance checking** against an open
  event log (the net opens as a model next to your logs).

Every property is defined in :mod:`openprocess.mining.definitions` (and
``docs/definitions.md``); hovering over one shows its definition filled in
for this net (:mod:`.instances`), clicking opens it.

The net is saved as PNML, the standard format ProM, WoPeD and PM4Py read.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QInputDialog, QLineEdit,
    QMessageBox, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ...mining.analysis import analyse, check_soundness, check_workflow_net, short_circuit
from ...mining.footprint import footprint_of_net
from ...mining.invariants import invariants
from ...mining.petrinet import Marking
from ...mining.pnml import write_pnml
from ...model.net import Arc, Place, Transition
from ...model.plain import to_petri_net, token_count, tokens_text, weight_of
from ..learn.concealment import ConcealsResults
from .cpn_page import CpnPage
from .documents import ModelDocument
from .workspace import atomic_write
from .widgets import (
    suggested_path,
    Card, Verdict, button, flow, footprint_table, hbox, label, status_for,
)
from . import instances
from .provenance import add_code
from .definition_view import attach_definition, show_reference
from .workers import run_in_background

# The notation of docs/definitions.md, as Qt rich text (labels detect the tags by themselves).
# N̄ with a combining macron renders badly in many fonts, so the bar is a real
# overline over an italic N.
NBAR = '<span style="text-decoration: overline"><i>N</i></span>'
TSTAR = "<i>t</i>*"
I, O = "<i>i</i>", "<i>o</i>"


def _math(name: str, status: str, detail: str, definition: str | None = None,
          instance: list[str] | None = None) -> "Verdict":
    """A Verdict whose detail is rich text (names in it must be escaped)."""
    return Verdict(name, status, f"<span>{detail}</span>", definition=definition,
                   instance=instance)


class PetriNetPage(ConcealsResults, CpnPage):
    """Draw, play and analyse a plain Petri net (see the module docstring)."""

    #: "Check against a log ›" was pressed: the window builds the analysis in Mine.
    check_requested = Signal()


    #: The net as a model for conformance checking / the model views.
    open_model = Signal(object)

    INSPECTOR = ["Simulation", "Element", "Analysis"]
    KIND = "Petri net"
    #: The coloured-net editor's tools plus τ: a silent transition in one click
    #: (the Element tab's *Silent* box turns an ordinary one into τ, and back).
    TOOLS = CpnPage.TOOLS[:3] + [
        ("silent", "τ", "Click on the canvas to add a silent (τ) transition: it moves tokens "
         "but leaves no event in a trace")] + CpnPage.TOOLS[3:]

    def __init__(self, document, parent=None) -> None:
        self._analysis_stale = True
        super().__init__(document, parent)
        # No colour sets, declarations or pages to manage.
        self.structure_toggle.hide()
        self._toggle_structure(False)
        self.values_button.hide()
        self.export_log_button.setText("Mine a simulated log ›")
        self.export_log_button.setToolTip("Play the net out many times (random choices), save the "
                                          "traces as an event log in the folder and open it in Mine")
        self.inspector_tabs.changed.connect(self._inspector_tab_changed)
        # Plain nets have no variables and no clock.
        self.bindings_card.title_label.setText("Enabled transitions")
        self.bindings_card.caption_label.setText("Double-click one to fire it (or click it on "
                                                 "the net).")
        self.binding_filter.hide()
        self.time_tile.hide()

    def _mode_changed(self, index: int) -> None:
        super()._mode_changed(index)
        if index == 1:
            self.sim_hint.setText("Green = enabled. Click a green transition to fire it; Back "
                                  "undoes, Reset returns to the initial marking. Trace lights up "
                                  "the path so far.")
        elif index == 2:
            self.sim_hint.setText("Play fires enabled transitions at random (blue halo = fired "
                                  "last). Generate event log (Simulation tab) plays many runs "
                                  "and opens them as a log.")

    def refresh_simulation(self, light: bool = False) -> None:
        super().refresh_simulation(light)
        count = len(getattr(self, "enabled_elements", []))
        self.enabled_tile.set(f"{count}", f"transition{'s' * (count != 1)}")

    def _fill_marking(self) -> None:
        """The marking as counts and dots, not CPN multisets."""
        from PySide6.QtWidgets import QTableWidgetItem
        rows = []
        for place in self.net.all_places():
            count = self._marking_for(place.id)[0]
            if count or not self.only_marked.isChecked():
                rows.append((place, count))
        self.marking_table.setRowCount(len(rows))
        for row, (place, count) in enumerate(rows):
            name = QTableWidgetItem(place.name)
            name.setData(Qt.UserRole, place.id)
            number = QTableWidgetItem(f"{count:,}")
            number.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            dots = QTableWidgetItem("●" * count if count <= 12 else f"{count} tokens")
            self.marking_table.setItem(row, 0, name)
            self.marking_table.setItem(row, 1, number)
            self.marking_table.setItem(row, 2, dots)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._toggle_structure(False)

    # =====================================================================
    # Inspector
    # =====================================================================
    def _build_inspector(self) -> QWidget:
        from PySide6.QtWidgets import QStackedWidget
        from .widgets import SegmentedControl, scroll
        host = QWidget()
        self.inspector_panel = host
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.inspector_tabs = SegmentedControl(self.INSPECTOR, compact=True)
        host.setMinimumWidth(max(300, self.inspector_tabs.sizeHint().width() + 24))
        layout.addLayout(hbox(self.inspector_tabs, None))
        self.inspector_stack = QStackedWidget()
        for build in (self._build_simulation_tab, self._build_element_tab,
                      self._build_analysis_tab):
            self.inspector_stack.addWidget(scroll(build()))
        self.inspector_tabs.changed.connect(self.inspector_stack.setCurrentIndex)
        layout.addWidget(self.inspector_stack, 1)
        # The coloured-net page's problems list is not needed here.
        self.problems_card = Card()
        return host

    # -- element form -------------------------------------------------------------------
    def _build_element_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        self.element_card = Card("Nothing selected")
        self.element_hint = label(
            "Click a place, transition or arc to edit it. Double-click a place or "
            "transition on the canvas to rename it.", "muted", wrap=True)
        self.element_card.add(self.element_hint)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._commit_element)
        self.tokens_spin = QSpinBox()
        self.tokens_spin.setRange(0, 999)
        self.tokens_spin.setToolTip("Tokens in this place in the initial marking")
        self.tokens_spin.valueChanged.connect(lambda _v: self._commit_element())
        self.silent_box = QCheckBox("Silent (τ) — invisible in traces")
        self.silent_box.toggled.connect(lambda _v: self._commit_element())
        self.weight_spin = QSpinBox()
        self.weight_spin.setRange(1, 99)
        self.weight_spin.setToolTip("How many tokens the arc moves at once")
        self.weight_spin.valueChanged.connect(lambda _v: self._commit_element())
        self.orientation_box = QComboBox()
        self.orientation_box.addItems(["Place → Transition", "Transition → Place",
                                       "Both directions"])
        self.orientation_box.activated.connect(lambda _i: self._commit_element())
        self.rows = {}
        for key, caption, widget in (("name", "Name", self.name_edit),
                                     ("tokens", "Tokens", self.tokens_spin),
                                     ("silent", "", self.silent_box),
                                     ("weight", "Weight", self.weight_spin),
                                     ("orientation", "Direction", self.orientation_box)):
            caption_label = label(caption, "muted")
            self.form.addRow(caption_label, widget)
            self.rows[key] = (caption_label, widget)
        self.element_card.add(self.form)
        self.element_info = label("", "muted", wrap=True, selectable=True)
        self.element_card.add(self.element_info)
        from PySide6.QtWidgets import QVBoxLayout as _V
        self.element_problems = _V()
        layout.addWidget(self.element_card)
        layout.addStretch(1)
        self._show_element(None)
        return page

    def _show_element(self, element) -> None:
        self.element = element
        self.element_info.setText("")
        self.element_hint.setVisible(element is None)
        widgets = (self.name_edit, self.tokens_spin, self.silent_box, self.weight_spin,
                   self.orientation_box)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            if element is None:
                self.element_card.title_label.setText("Nothing selected")
                self._set_rows(set())
            elif isinstance(element, Place):
                self.element_card.title_label.setText(f"Place · {element.name}")
                self.name_edit.setText(element.name)
                self.tokens_spin.setValue(token_count(element.initial_marking_text))
                self._set_rows({"name", "tokens"})
                now = self._marking_for(element.id)[0]
                self.element_info.setText(f"Now: {now} token(s).")
            elif isinstance(element, Transition):
                kind = "Silent transition" if element.silent else "Transition"
                self.element_card.title_label.setText(f"{kind} · {element.name or 'τ'}")
                self.name_edit.setText(element.name)
                self.silent_box.setChecked(element.silent)
                self._set_rows({"name", "silent"})
                enabled = bool(self.simulator.enabled_bindings(element))
                self.element_info.setText("Enabled now." if enabled else "Not enabled now.")
            elif isinstance(element, Arc):
                place = self.net.find_place(element.place_id)
                transition = self.net.find_transition(element.transition_id)
                self.element_card.title_label.setText("Arc")
                self.weight_spin.setValue(weight_of(element))
                self.orientation_box.setCurrentIndex(
                    {"PtoT": 0, "TtoP": 1, "BOTHDIR": 2}.get(element.orientation, 0))
                self._set_rows({"weight", "orientation"})
                self.element_info.setText(
                    f"Between place “{place.name if place else '?'}” and transition "
                    f"“{transition.name if transition else '?'}”.")
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _commit_element(self) -> None:
        element = self.element
        if element is None:
            return
        before = self._element_state(element)
        snapshot = self._snapshot()
        if isinstance(element, Place):
            element.name = self.name_edit.text().strip() or element.name
            element.initial_marking_text = tokens_text(self.tokens_spin.value())
        elif isinstance(element, Transition):
            element.name = self.name_edit.text().strip()
            element.silent = self.silent_box.isChecked()
        elif isinstance(element, Arc):
            weight = self.weight_spin.value()
            element.expression_text = "" if weight == 1 else tokens_text(weight)
            element.orientation = ["PtoT", "TtoP", "BOTHDIR"][self.orientation_box.currentIndex()]
        if self._element_state(element) != before:
            self._push_undo(snapshot)
            self._edited()
            self._show_element(element)

    @staticmethod
    def _element_state(element) -> tuple:
        return tuple(getattr(element, name, None) for name in (
            "name", "initial_marking_text", "silent", "expression_text", "orientation"))

    # -- analysis --------------------------------------------------------------------------
    def _build_analysis_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        self.analysis_button = button("Check again", self.run_analysis, kind="primary",
                                      tooltip="The analysis also runs by itself whenever "
                                              "you open this tab after an edit")
        definitions = button("Definitions…", lambda: show_reference(None, self),
                             kind="ghost", tooltip="Every property, defined mathematically")
        # The hint wraps, so the row never makes the inspector wider than it is.
        layout.addLayout(hbox(label("Hover over a property (ⓘ) for its definition.", "muted",
                                    wrap=True), definitions, self.analysis_button))
        # Conformance is Mine's job: one click builds the analysis there.
        self.check_log_button = button("Check against a log ›", self.check_requested.emit,
                                       tooltip="In Mine: a new analysis with this net, a log you "
                                               "choose and Check fit, connected and run")
        layout.addLayout(hbox(label("How well does it fit a log? Fitness, precision and more, "
                                    "with every step shown.", "muted", wrap=True), self.check_log_button))
        self.soundness_card = Card("Soundness", f"From one token in {I}: (i) every run can "
                                   f"still reach [{O}], (ii) [{O}] is the only marking with a "
                                   f"token in {O}, (iii) every transition can fire.")
        self.theorem_card = Card(f"Short-circuited net {NBAR}",
                                 f"{NBAR} is the net plus a transition {TSTAR} from {O} back "
                                 f"to {I}. Soundness theorem: the WF-net is sound iff "
                                 f"({NBAR}, [{I}]) is live and bounded.")
        self.structure_card = Card("Structure", "Properties of the drawing alone. They "
                                   "point at the construct behind a problem.")
        self.invariants_card = Card("Invariants", "Linear algebra on the incidence matrix: "
                                    "weighted token counts that never change (P-invariants), "
                                    "and firing counts that lead back to where they started "
                                    "(T-invariants).")
        self.properties_card = Card("Behavioural properties", "Of the net as drawn, from "
                                    "its initial marking.")
        self.footprint_card = Card("Footprint", "The ordering relations of the net's "
                                   "behaviour: which transition can directly follow which.")
        # The card captions explain the idea; hovering shows the maths; { } code opens the code.
        for card, key in ((self.soundness_card, "sound"), (self.theorem_card, "short_circuit"),
                          (self.invariants_card, "incidence_matrix"),
                          (self.footprint_card, "footprint")):
            attach_definition(card.caption_label, key)
        for card, functions in ((self.soundness_card, (check_soundness, check_workflow_net)),
                                (self.theorem_card, (short_circuit, analyse)),
                                (self.structure_card, (check_workflow_net,)),
                                (self.invariants_card, (invariants,)),
                                (self.properties_card, (analyse,)),
                                (self.footprint_card, (footprint_of_net,))):
            add_code(card, *functions)
        self.more_card = Card("More")
        self.more_card.add(label("Look at every reachable marking of the net.", "muted", wrap=True))
        self.more_card.add(flow(button("Reachability graph…", self.show_reachability_graph)))
        for card in (self.soundness_card, self.theorem_card, self.structure_card,
                     self.invariants_card, self.properties_card, self.footprint_card,
                     self.more_card):
            layout.addWidget(card)
        # Hidden in an exercise until revealed (see concealment.py).
        for card, key in ((self.soundness_card, "soundness"), (self.theorem_card, "theorem"),
                          (self.structure_card, "structure"),
                          (self.invariants_card, "invariants"),
                          (self.properties_card, "properties"),
                          (self.footprint_card, "footprint")):
            self.conceal_card(card, key)
        layout.addStretch(1)
        return page

    def set_concealment(self, concealment) -> None:
        super().set_concealment(concealment)
        if concealment is not None:
            concealment.changed.connect(self._update_sim_status)
        self._update_sim_status()

    def _inspector_tab_changed(self, index: int) -> None:
        if index == 2 and self._analysis_stale:
            self.run_analysis()

    def _edited(self) -> None:
        super()._edited()
        self._analysis_stale = True
        if getattr(self, "inspector_tabs", None) is not None and self.inspector_tabs.index() == 2:
            QTimer.singleShot(0, self.run_analysis)

    def petri_net(self):
        """The drawing as a :class:`~openprocess.mining.petrinet.PetriNet`."""
        return to_petri_net(self.net)

    def run_analysis(self) -> None:
        self._analysis_stale = False
        petri = self.petri_net()
        for card in self._result_cards():
            card.clear()
        self.soundness_card.add(label("Checking…", "muted"))

        def compute():
            soundness = check_soundness(petri, max_states=100_000)
            drawn = petri
            if not petri.initial_marking and soundness.workflow.is_workflow_net:
                # Nothing drawn in the net yet: analyse from [source].
                drawn = petri.copy()
                drawn.initial_marking = Marking({soundness.workflow.source: 1})
            properties = analyse(drawn, max_states=50_000)
            try:
                footprint = footprint_of_net(drawn)
                footprint_error = None
            except ValueError as error:
                footprint, footprint_error = None, str(error)
            workflow = soundness.workflow
            linear = (invariants(drawn), invariants(short_circuit(
                drawn, workflow.source, workflow.sink)) if workflow.is_workflow_net else None)
            return (soundness, properties, footprint, footprint_error, drawn is not petri,
                    linear)

        run_in_background(compute, self._show_analysis,
                          lambda message: self.soundness_card.add(
                              Verdict("Analysis failed", "critical", message)))

    def _result_cards(self) -> tuple:
        return (self.soundness_card, self.theorem_card, self.structure_card,
                self.invariants_card, self.properties_card, self.footprint_card)

    def _finding(self, card: Card, text: str, path: list[str] | None = None,
                 source: str | None = None) -> None:
        """A bullet with, when there is a firing sequence, a Show ▶ button."""
        row = QWidget()
        row_layout = hbox(spacing=6)
        row_layout.addWidget(label("• " + text, "muted", wrap=True, selectable=True), 1)
        if path:
            show = button("Show ▶", lambda _=False, p=path, s=source: self.replay(p, s),
                          kind="ghost", tooltip="Fire this sequence on the net in the "
                                                "token game")
            show.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            row_layout.addWidget(show, 0, Qt.AlignTop)
        row.setLayout(row_layout)
        card.add(row)

    def _show_analysis(self, result) -> None:
        soundness, properties, footprint, footprint_error, from_source, linear = result
        for card in self._result_cards():
            card.clear()
        workflow = soundness.workflow
        card = self.soundness_card
        card.add(Verdict("WF-net", status_for(workflow.is_workflow_net),
                         f"source i = {self._name(workflow.source)}, sink o = "
                         f"{self._name(workflow.sink)}" if workflow.is_workflow_net else
                         "needs one source place i, one sink place o, and every node on a "
                         "path from i to o",
                         instance=instances.soundness(soundness, "wf_net")))
        if workflow.is_workflow_net:
            verdict = soundness.sound
            card.add(Verdict("Sound" if verdict else ("Not sound" if verdict is False
                                                      else "Undecided"),
                             status_for(verdict),
                             "every case can complete properly and every transition can fire"
                             if verdict else "", instance=instances.soundness(soundness, "sound")))
            for name, key, value in (
                    ("(i) Option to complete", "option_to_complete",
                     soundness.option_to_complete),
                    ("(ii) Proper completion", "proper_completion", soundness.proper_completion),
                    ("(iii) No dead transitions", "no_dead_transitions",
                     soundness.no_dead_transitions)):
                card.add(Verdict(name, status_for(value), "", definition=key,
                                 instance=instances.soundness(soundness, key)))
        for finding, path in zip(soundness.findings, soundness.paths):
            self._finding(card, finding, path, workflow.source)
        self._show_theorem(soundness)
        self._show_structure(soundness)
        self._show_invariants(*linear)

        if from_source:
            self.properties_card.add(label("The net has no tokens yet, so this is from one "
                                           "token in the source place.", "muted", wrap=True))
        final = Marking({workflow.sink: 1}) if workflow.is_workflow_net else None
        only_final = final is not None and properties.dead_markings and all(
            properties.graph.states[s] == final for s in properties.dead_markings)
        from ...mining.definitions import FOR_TITLE
        for name, value, detail in properties.lines():
            key = FOR_TITLE.get(name)
            filled = instances.properties(properties, key) if key else None
            if name == "Deadlock-free" and only_final:
                # A WF-net always stops in [o]: that is the case finishing.
                self.properties_card.add(Verdict(
                    name, "info", "the only dead marking is "
                    f"[{escape(self._plain(workflow.sink))}]: the case has finished. Every "
                    f"WF-net stops there, so this is expected; {NBAR} above shows whether it "
                    "can get stuck anywhere else.", instance=filled))
                continue
            self.properties_card.add(Verdict(name, status_for(value), detail,
                                             instance=filled))
        self.properties_card.add(label(
            f"{len(properties.graph.states):,} reachable markings "
            f"({properties.graph.kind} graph).", "muted", wrap=True))

        if footprint is None:
            self.footprint_card.add(label(footprint_error or "", "muted", wrap=True))
        elif not footprint.activities:
            self.footprint_card.add(label("No visible transitions yet.", "muted"))
        else:
            table = footprint_table(footprint, compact=True)
            rows = len(footprint.activities)
            table.setMinimumHeight(min(440, 36 + 30 * rows))
            self.footprint_card.add(table)
            abbreviated = any(len(a) > 3 for a in footprint.activities)
            self.footprint_card.add(label(
                ("Columns use initials (hover for the name). " if abbreviated else "")
                + "→ causality  ← inverse  ‖ parallel  # choice. Start: "
                f"{', '.join(sorted(footprint.start)) or '–'} · end: "
                f"{', '.join(sorted(footprint.end)) or '–'}", "muted", wrap=True))

    def _show_invariants(self, drawn, closed) -> None:
        """P-invariants of the net; T-invariants of N̄ for a WF-net (a WF-net
        itself has none: nothing leads back from [o])."""
        card = self.invariants_card
        self._invariants = drawn

        def names(ids) -> str:
            return ", ".join(escape(drawn.net.node_name(node)) for node in ids)

        covered = drawn.covered_by_p_invariants
        uncovered = drawn.uncovered_places()
        card.add(Verdict(
            "Covered by P-invariants", status_for(covered),
            "too many invariants to list" if covered is None else
            "every place is in one, so the net is bounded from any initial marking"
            if covered else f"in no P-invariant: {names(uncovered)}",
            definition="p_invariant"))
        for invariant in drawn.p_invariants[:12]:
            card.add(label("• " + escape(drawn.describe(invariant, with_value=True)), "muted",
                           wrap=True, selectable=True))
        if len(drawn.p_invariants) > 12:
            card.add(label(f"… and {len(drawn.p_invariants) - 12} more", "muted"))

        target, where = (closed, NBAR) if closed is not None else (drawn, "the net")
        covered = target.covered_by_t_invariants
        missing = [t for t in target.uncovered_transitions()]
        if covered is None:
            detail = "too many invariants to list"
        elif covered:
            detail = (f"every transition of {where} is in one" + (
                f": a sound WF-net needs this ({NBAR} is live and bounded), but it does not "
                "prove soundness" if closed is not None else ""))
        else:
            detail = f"in no T-invariant of {where}: {names(missing)}" + (
                f", so {NBAR} is not live and bounded and the WF-net is not sound"
                if closed is not None else ", so the net is not both live and bounded")
        card.add(Verdict("Covered by T-invariants", status_for(covered),
                         f"<span>{detail}</span>", definition="t_invariant"))
        for invariant in target.t_invariants[:12]:
            card.add(label("• " + escape(target.describe(invariant)), "muted", wrap=True,
                           selectable=True))
        if len(target.t_invariants) > 12:
            card.add(label(f"… and {len(target.t_invariants) - 12} more", "muted"))
        card.add(flow(button("Incidence matrix…", self.show_incidence_matrix,
                             tooltip="What firing each transition does to each place")))

    def show_incidence_matrix(self) -> None:
        """The incidence matrix C (places × transitions) in a dialog."""
        from PySide6.QtWidgets import QAbstractItemView, QDialogButtonBox, QTableWidget, \
            QTableWidgetItem
        found = getattr(self, "_invariants", None) or invariants(self.petri_net())
        net = found.net
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Incidence matrix of {self.net.name}")
        table = QTableWidget(len(found.places), len(found.transitions))
        table.setHorizontalHeaderLabels([net.node_name(t) for t in found.transitions])
        table.setVerticalHeaderLabels([net.node_name(p) for p in found.places])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for i, row in enumerate(found.incidence):
            for j, value in enumerate(row):
                cell = QTableWidgetItem("0" if not value else f"{value:+d}")
                cell.setTextAlignment(Qt.AlignCenter)
                table.setItem(i, j, cell)
        table.resizeColumnsToContents()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("C(p, t) = tokens t puts into p − tokens t takes from p. "
                               "Firing t adds column t to the marking.", "muted", wrap=True))
        layout.addWidget(table)
        layout.addWidget(buttons)
        dialog.resize(min(900, 140 + 80 * len(found.transitions)),
                      min(640, 140 + 32 * len(found.places)))
        dialog.show()
        self._matrix_dialog = dialog

    def _name(self, node_id: str | None) -> str:
        if node_id is None:
            return "?"
        node = self.net.find_place(node_id) or self.net.find_transition(node_id)
        return f"“{node.name}”" if node is not None else node_id

    def _plain(self, node_id: str | None) -> str:
        node = node_id and (self.net.find_place(node_id) or self.net.find_transition(node_id))
        return node.name if node else (node_id or "?")

    def _show_theorem(self, soundness) -> None:
        """The soundness theorem: (N̄, [i]) live and bounded iff sound."""
        card = self.theorem_card
        closed = soundness.short_circuit
        if closed is None:
            card.add(label("Only for WF-nets.", "muted"))
            return
        graph = closed.properties.graph
        net = closed.net
        live = closed.live
        not_live = [TSTAR if t == "t_star" else escape(net.node_name(t))
                    for t in closed.not_live]
        filled = lambda key: instances.short_circuit(soundness, key)  # noqa: E731
        card.add(_math("Live", status_for(live),
                         f"from every reachable marking, every transition ({TSTAR} too) can "
                         "fire again" if live else
                         (f"not decided: {NBAR} is unbounded or too big" if live is None else
                          "can never fire again at some point: " + ", ".join(not_live)),
                       instance=filled("live")))
        card.add(_math("Bounded", status_for(closed.bounded),
                         f"at most {int(closed.properties.bound)} token(s) per place"
                         if closed.bounded else
                         "tokens can pile up: a case can finish with tokens left behind, "
                         f"and {TSTAR} then starts the next case on top of them",
                       instance=filled("bounded")))
        theorem = closed.sound
        card.add(_math("Live and bounded ⇒ sound" if theorem else
                         ("Unbounded ⇒ not sound" if not closed.bounded else
                          "Not live ⇒ not sound" if theorem is False else "Undecided"),
                         status_for(theorem),
                         "the same verdict as the soundness check above" if theorem ==
                         soundness.sound else "differs from the soundness check above (a "
                         "state-space limit was hit)", definition="soundness_theorem",
                       instance=filled("soundness_theorem")))
        card.add(_math("Safe", "good" if closed.safe else "info",
                         "at most one token per place (a sound free-choice or "
                         "well-structured net is always safe)" if closed.safe else
                         "some place can hold two or more tokens", instance=filled("safe")))
        dead = closed.properties.dead_markings
        card.add(_math("Deadlock-free", "good" if not dead else "info",
                         "no marking where nothing can fire. Weaker than live: a net can "
                         "keep going while some transition is dead, so the soundness "
                         "theorem asks for live" if not dead else
                         f"stuck in {escape(graph.states[dead[0]].describe(net))}; a deadlock is "
                         "never live, so this alone already means not sound",
                       instance=filled("deadlock_free")))
        for transition, state in list(closed.not_live.items())[:4]:
            path = graph.path_to(state)
            usable = "t_star" not in path
            who = TSTAR if transition == "t_star" else escape(net.node_name(transition))
            self._finding(card, f"<span>{who} can never fire again after "
                          f"{escape(graph.describe_path(state))} (marking "
                          f"{escape(graph.states[state].describe(net))}).</span>",
                          path if usable else None, soundness.workflow.source)
        card.add(flow(button("Open the short-circuited net", self._open_short_circuited,
                             tooltip="Draw the net with t* added, in a new tab")))

    def _open_short_circuited(self) -> None:
        from ...mining.analysis import short_circuit
        from ...model.plain import from_petri_net
        from .documents import CpnDocument
        petri = self.petri_net()
        workflow = check_workflow_net(petri)
        if not workflow.is_workflow_net:
            return
        closed = short_circuit(petri, workflow.source, workflow.sink)
        closed.name = f"{self.net.name} — short-circuited"
        self.open_model.emit(CpnDocument(from_petri_net(closed)))

    def _show_structure(self, soundness) -> None:
        """Free-choice, well-structured, S-coverable, and the start and end rule."""
        card = self.structure_card
        report = soundness.structure
        if report is None:
            card.add(label("Only for WF-nets.", "muted"))
            return
        name = self._plain

        def node(prefixed: str) -> str:
            return TSTAR if prefixed == "t:t_star" else escape(name(prefixed[2:]))

        if report.is_free_choice:
            detail = ("transitions that share an input place have the same input places, "
                      "so every choice is free")
        else:
            t1, t2, p = report.free_choice[0]
            detail = (f"{escape(name(t1))} and {escape(name(t2))} share {escape(name(p))} but "
                      "need different input "
                      "places, so the choice between them depends on what happened "
                      "elsewhere")
        net = self.petri_net()
        card.add(_math("Free-choice", "good" if report.is_free_choice else "info", detail,
                       instance=instances.structure(soundness, net, "free_choice")))

        well = report.well_structured
        if well is None:
            detail = "not checked (the net is very large)"
        elif well:
            detail = (f"{NBAR} has no handles: every AND-split is closed by an AND-join and every "
                      "OR-split by an OR-join")
        else:
            handle = report.handles[0]
            kind = ("an OR-split (place) later synchronised by an AND-join (transition)"
                    if handle.kind == "PT" else
                    "an AND-split (transition) later merged by an OR-join (place)")
            detail = (f"{handle.kind}-handle from {node(handle.start)} to "
                      f"{node(handle.end)}: {kind}. Paths "
                      + " and ".join("⟨" + ", ".join(node(n) for n in path) + "⟩"
                                     for path in handle.paths))
        card.add(_math("Well-structured", "unknown" if well is None else
                         ("good" if well else "info"), detail,
                       instance=instances.structure(soundness, net, "well_structured")))

        cover = report.s_coverable
        if cover is None:
            detail = "not decided (search limit reached)"
        elif cover:
            detail = (f"{len(report.coverage.components)} S-component(s) cover {NBAR}: each is "
                      "a 'thread' that is always in exactly one place")
        else:
            missing = ", ".join(TSTAR if n == "t_star" else escape(name(n))
                                for n in report.coverage.uncovered[:6])
            detail = (f"no S-component contains {missing}. Unsound nets are often not "
                      "S-coverable, so this is worth a close look")
        card.add(_math("S-coverable", "unknown" if cover is None else
                         ("good" if cover else "warning"), detail,
                       instance=instances.structure(soundness, net, "s_coverable")))
        for transition in report.lemma4:
            self._finding(card, f"<span>Start and end rule: {escape(name(transition))} needs {I} or "
                          f"{O} together with another place, which never happens in a sound "
                          "net — it is dead.</span>")
        if soundness.sound and (report.is_free_choice or well):
            self._finding(card, "Sound and " + ("free-choice" if report.is_free_choice
                                                else "well-structured")
                          + ", so the net is safe.")

    # -- counterexamples in the token game --------------------------------------------------
    def replay(self, path: list[str], source: str | None = None) -> None:
        """Fire ``path`` (transition ids) from the start, in Step through mode."""
        if source is not None:
            place = self.net.find_place(source)
            wanted = {p.id: (1 if p.id == source else 0) for p in self.net.all_places()}
            drawn = {p.id: token_count(p.initial_marking_text) for p in self.net.all_places()}
            if place is not None and drawn != wanted:
                # Soundness is about one token in the source: start there.
                self._push_undo(self._snapshot())
                for other in self.net.all_places():
                    other.initial_marking_text = tokens_text(wanted[other.id])
                self._edited()
                self.status.emit(f"Initial marking set to one token in {place.name} to "
                                 f"replay the counterexample (undo brings yours back)")
        self.mode_switch.set_index(1)
        self.simulator.reset()
        names = []
        for transition_id in path:
            transition = self.net.find_transition(transition_id)
            elements = self.simulator.enabled_bindings(transition) if transition else []
            if not elements:
                break
            self.simulator.step(elements[0])
            names.append(transition.name or "τ")
        self.refresh_simulation()
        self.inspector_tabs.set_index(0)
        self.status.emit("Counterexample: " + (" → ".join(names) or "(initial marking)")
                         + " — this is the problem marking")

    # -- more ------------------------------------------------------------------------------
    def show_reachability_graph(self) -> None:
        from ...mining.analysis import coverability_graph, reachability_graph
        from .graph_builders import state_graph_specs
        from .graph_view import GraphView
        petri = self.petri_net()
        if not petri.initial_marking:
            workflow = check_workflow_net(petri)
            if workflow.is_workflow_net:
                petri.initial_marking = Marking({workflow.source: 1})
        graph = reachability_graph(petri, max_states=5_000)
        if graph.has_omega:
            graph = coverability_graph(petri, max_states=5_000)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Reachability graph — {self.net.name}")
        dialog.resize(900, 640)
        layout = QVBoxLayout(dialog)
        kind = "Coverability graph (ω = unbounded)" if graph.kind == "coverability" \
            else "Reachability graph"
        layout.addWidget(label(f"{kind}: {len(graph.states):,} markings, "
                               f"{len(graph.edges):,} steps"
                               + (" (stopped at 5,000)" if graph.truncated else "")
                               + ". The initial marking is shaded; red = dead.", "muted",
                               wrap=True))
        view = GraphView()
        nodes, edges = state_graph_specs(graph)
        view.graph.populate(nodes, edges, layer_gap=120)
        layout.addWidget(view, 1)
        QTimer.singleShot(0, view.fit)
        dialog.exec()

    def open_as_model(self) -> None:
        """Open the net next to the logs, where conformance checking lives."""
        petri = self.petri_net()
        self.open_model.emit(ModelDocument(petri, origin=f"Drawn in {self.net.name}"))

    def _export_log(self) -> None:
        """Play the net out: each run from the initial marking is one case."""
        from ...mining.playout import play_out
        count, ok = QInputDialog.getInt(self, "Generate event log",
                                        "Number of cases to simulate:", 100, 1, 100_000, 50)
        if not ok:
            return
        petri = self.petri_net()
        if not petri.initial_marking:
            workflow = check_workflow_net(petri)
            if workflow.is_workflow_net:
                petri.initial_marking = Marking({workflow.source: 1})

        def done(result) -> None:
            self.status.emit(f"Generated {len(result.log):,} cases: {result.completed:,} "
                             f"completed, {result.deadlocked:,} deadlocked")
            self.log_generated.emit(result.log)

        run_in_background(lambda: play_out(petri, traces=count,
                                           name=f"Play-out · {self.net.name}"),
                          done, lambda m: self.status.emit(f"Play-out failed: {m}"))

    # =====================================================================
    # Status, titles, files
    # =====================================================================
    def _fill_problems(self) -> None:
        pass

    def _update_sim_status(self) -> None:
        if not self.simulating:
            if self.results_hidden("soundness"):
                self.sim_status.setText("")          # an exercise may ask exactly this
                return
            workflow = check_workflow_net(self.petri_net())
            self.sim_status.setText("WF-net ✓" if workflow.is_workflow_net else
                                    "not (yet) a WF-net")
            return
        enabled = len(getattr(self, "enabled_elements", []))
        state = f"{enabled} enabled" if enabled else "✕ dead: nothing is enabled"
        self.sim_status.setText(f"Step {self.simulator.step_count:,} · {state}")
        self.back_button.setEnabled(bool(self.simulator.log))
        self.step_button.setEnabled(bool(enabled))

    def _subtitle(self) -> str:
        places = sum(1 for _ in self.net.all_places())
        transitions = sum(1 for _ in self.net.all_transitions())
        where = Path(self.document.path).name if self.document.path else "not saved yet"
        state = " · edited" if self.document.dirty and not self.document.autosave else ""
        return (f"Petri net · {places} places · {transitions} transitions · "
                f"{where}{state}")

    def export(self) -> bool:
        suggested = self.document.path or suggested_path(f"{self.net.name}.pnml")
        if suggested.lower().endswith(".cpn"):
            suggested = suggested[:-4] + ".pnml"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save Petri net", suggested,
            "PNML Petri net (*.pnml);;CPN Tools model (*.cpn)")
        if not path:
            return False
        if not path.lower().endswith((".pnml", ".cpn")):
            path += ".cpn" if "cpn" in chosen.lower() and "pnml" not in chosen.lower() \
                else ".pnml"
        self._take_file_name(Path(path))
        return self._write(Path(path))

    def _write(self, path: Path, quiet: bool = False) -> bool:
        if path.suffix.lower() == ".cpn":
            return super()._write(path, quiet)
        try:
            net = self.petri_net()
            atomic_write(path, lambda temporary: write_pnml(net, temporary))
        except Exception as error:  # noqa: BLE001
            return self._save_failed(error, quiet)
        self._written(path, quiet)
        return True
