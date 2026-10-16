"""The coloured-Petri-net workspace: edit, simulate and analyse a CPN model.

This is the CPN Tools / CPN IDE half of the application, in the same visual
language as the process-mining pages::

    ┌ header: model name · summary ·············· Save  Save As…  Export image… ┐
    │ ┌ Model ──────┐ ┌ canvas ─────────────────────────┐ ┌ inspector ─────────┐ │
    │ │ Pages │ Decl│ │ Edit | Step through | Simulate  │ │ Simulation         │ │
    │ │ • Top       │ │ tools / simulation bar          │ │ Element            │ │
    │ │ • Sub       │ │                                 │ │ Problems           │ │
    │ │             │ │        the net                  │ │ State space        │ │
    │ └─────────────┘ └─────────────────── − 100% + Fit ┘ └────────────────────┘ │
    └──────────────────────────────────────────────────────────────────────────────┘

Modes (the same three as for Petri nets in the mining half):

* **Edit** -- draw places, transitions and arcs; select one to edit its
  inscriptions in the *Element* tab.  Clicking never fires anything.
* **Step through** -- enabled transitions are green; click one to fire a
  random enabled binding of it, pick a specific binding in the inspector,
  press Step for a random one, or Back to undo.
* **Simulate** -- Play fires by itself at the chosen speed; Fast-forward
  runs many steps as fast as possible.  The firing history can be turned
  into an event log (*Export as event log…*) and mined straight away.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHeaderView, QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStyle,
    QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)
from html import escape

from ...io.cpn_writer import write_cpn
from ...ml.values import format_value
from ...model.net import Arc, CPNet, Place, Transition
from ...sim.simulator import DeadMarkingError, Simulator
from .. import theme
from ..canvas import NetScene, NetView
from . import style
from .derivation_view import HtmlDelegate
from .documents import CpnDocument
from .graph_view import EdgeSpec, GraphView, NodeSpec
from .workspace import atomic_write
from .ml_highlighter import MlHighlighter
from .widgets import (
    suggested_path,
    Card, ElidedLabel, PageHeader, SegmentedControl, StatTile, Verdict, button, flow, hbox, label,
    scroll, shortcut_text, vbox,
)


def _tool(text: str, tooltip: str, checkable: bool = False) -> QToolButton:
    tool = QToolButton()
    tool.setObjectName("canvasTool")
    tool.setText(text)
    tool.setToolTip(tooltip)
    tool.setCheckable(checkable)
    tool.setCursor(Qt.PointingHandCursor)
    return tool


def format_time(value) -> str:
    """Model time without float noise: 12, 12.5, 3.142."""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    return str(value)


def _mono() -> str:
    # The first one installed wins: macOS, Windows, Linux, then any.
    return ("'SF Mono', Menlo, Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', "
            "'Liberation Mono', 'Courier New', monospace")


def binding_html(net: CPNet, element) -> str:
    """``Check in  i = 1, r = 0`` with the name in bold and values in mono."""
    transition = net.find_transition(element.transition_id)
    name = escape(transition.name if transition else element.transition_id)
    muted = style.tokens().text_muted
    def value(v) -> str:
        text = format_value(v)
        return text if len(text) <= 48 else text[:46] + " …"
    parts = [f"<span style='color:{muted}'>{escape(k)}</span>&nbsp;=&nbsp;"
             f"<span style='font-family:{_mono()}'>{escape(value(v))}</span>"
             for k, v in element.assignments]
    return f"<b>{name}</b>&nbsp;&nbsp;" + ", ".join(parts)


class _TwoLineDelegate(QStyledItemDelegate):
    """List rows with a title and a muted second line (UserRole + 1)."""

    def paint(self, painter: QPainter, option, index) -> None:
        t = style.tokens()
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        rect = option.rect.adjusted(4, 2, -4, -2)
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(rect, 7, 7)
        elif option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            hover = QColor(t.text)
            hover.setAlphaF(0.06)
            painter.setBrush(hover)
            painter.drawRoundedRect(rect, 7, 7)
        font = QFont(option.font)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(t.accent_text if selected else t.text))
        text_rect = rect.adjusted(10, 5, -8, 0)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, index.data(Qt.DisplayRole) or "")
        font.setWeight(QFont.Normal)
        theme.set_px(font, theme.px(font) * 0.88)
        painter.setFont(font)
        painter.setPen(QColor(t.accent_text if selected else t.text_muted))
        painter.drawText(text_rect.adjusted(0, 18, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                         index.data(Qt.UserRole + 1) or "")
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(160, 46)


class CaseVariableDialog(QDialog):
    """Options for turning the firing history into an event log."""

    def __init__(self, variables: list[str], preferred: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export simulation as event log")
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label(
            "Every firing becomes an event: the activity is the transition's name and the "
            "timestamp is the model time. Choose the variable whose value identifies the case "
            "(e.g. the passenger id); firings that do not bind it are left out.", "muted",
            wrap=True))
        form = QFormLayout()
        self.variable = QComboBox()
        self.variable.addItems(variables)
        if preferred in variables:
            self.variable.setCurrentText(preferred)
        self.unit = QComboBox()
        self.unit.addItems(["seconds", "minutes", "hours", "days"])
        self.unit.setCurrentText("minutes")
        form.addRow("Case identifier", self.variable)
        form.addRow("One time unit is", self.unit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CpnPage(QWidget):
    status = Signal(str)
    saved = Signal()
    #: The model was edited (True) or saved (False).
    dirty_changed = Signal(bool)
    #: Any edit at all (the window's autosave timer restarts on each one).
    edited = Signal()
    #: A simulation run was exported as an event log.
    log_generated = Signal(object)
    #: The Snap to grid box was ticked or unticked (one setting for every editor).
    snap_changed = Signal(bool)

    MODES = ["Edit", "Step through", "Simulate"]
    #: Highlight the fired path while stepping (the Trace box; shared by all nets)
    show_trace = True
    TOOLS = [("select", "Select", "Select and move things (drag on empty canvas to select "
              "several); double-click a place or transition to rename it"),
             ("place", "Place", "Click on the canvas to add a place"),
             ("transition", "Transition", "Click on the canvas to add a transition"),
             ("arc", "Arc", "Drag from a place to a transition (or the other way) to "
              "connect them")]
    INSPECTOR = ["Simulation", "Element", "Problems", "State space"]
    MAX_BINDINGS_SHOWN = 400

    def __init__(self, document: CpnDocument, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.net: CPNet = document.net
        self.net.compile()
        self.simulator = Simulator(self.net)
        self.element = None
        self.busy = False                      # a state space is being computed
        self.fast_forward_left = 0
        self.runs_completed = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader(self.net.name, "")
        self.save_button = button("Save", self.save, tooltip=f"Save the model ({shortcut_text('Ctrl+S')})")
        self.header.actions.addWidget(self.save_button)
        self.header.actions.addWidget(button("Save As…", self.export))
        self.header.actions.addWidget(button("Export image…", self._export_image))
        root.addWidget(self.header)

        splitter = QSplitter()
        splitter.setHandleWidth(14)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        splitter.addWidget(self._build_structure())
        splitter.addWidget(self._build_canvas())
        splitter.addWidget(self._build_inspector())
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(1, False)
        splitter.setSizes([250, 780, 400])
        self.splitter = splitter
        wrapper = QWidget()
        wrapper.setLayout(vbox(splitter, margins=(20, 0, 20, 16)))
        root.addWidget(wrapper, 1)

        self.scene.show_page(self.net.pages[0] if self.net.pages else None)
        self._mode_changed(0)
        self.refresh_all()
        QTimer.singleShot(0, self.view.zoom_to_fit)

    # =====================================================================
    # Construction
    # =====================================================================
    def _build_structure(self) -> QWidget:
        card = Card()
        self.structure_panel = card
        self.structure_switch = SegmentedControl(["Pages", "Declarations"], compact=True)
        card.setMinimumWidth(max(200, self.structure_switch.sizeHint().width() + 36))
        card.add(hbox(self.structure_switch, None))
        self.structure_stack = QStackedWidget()

        # -- pages
        pages = QWidget()
        self.page_list = QListWidget()
        self.page_list.setObjectName("sourceList")
        self.page_list.setItemDelegate(_TwoLineDelegate(self.page_list))
        self.page_list.setMouseTracking(True)
        self.page_list.setFrameShape(QListWidget.NoFrame)
        self.page_list.currentRowChanged.connect(self._page_selected)
        self.page_list.itemDoubleClicked.connect(lambda _item: self._rename_page())
        self.page_list.setToolTip("Double-click a page to rename it")
        pages.setLayout(vbox(self.page_list,
                             hbox(button("＋ Add page", self._add_page, kind="ghost"), None),
                             spacing=4))
        self.structure_stack.addWidget(pages)

        # -- declarations
        declarations = QWidget()
        self.declarations_editor = QPlainTextEdit()
        self.declarations_editor.setFont(theme.mono_font(11))
        self.declarations_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.declarations_editor.setPlaceholderText(
            "colset NO = int;\nvar n : NO;\nfun next n = n + 1;")
        self.highlighter = MlHighlighter(self.declarations_editor.document())
        self.declarations_status = label("", "muted", wrap=True)
        self.apply_button = button("Apply", self._apply_declarations, kind="primary",
                                   tooltip="Re-read the declarations and recompile the model")
        self.revert_button = button("Revert", self._reload_declarations)
        self.declarations_editor.textChanged.connect(self._declarations_edited)
        declarations.setLayout(vbox(
            label("CPN ML: colset, var, val, fun, globref — each ending in ;", "muted",
                  wrap=True),
            self.declarations_editor, self.declarations_status,
            hbox(None, self.revert_button, self.apply_button), spacing=6))
        self.structure_stack.addWidget(declarations)
        self.structure_switch.changed.connect(self._structure_tab)
        card.add(self.structure_stack, 1)
        self._reload_declarations()
        return card

    def _values_mode(self, index: int) -> None:
        for i, action in enumerate(self.values_actions):
            action.setChecked(i == index)
        self.scene.show_token_values = (None, True, False)[index]
        self.scene.update_markings(self._marking_for)

    def _toggle_inspector(self) -> None:
        show = not self.inspector_panel.isVisible()
        self.inspector_panel.setVisible(show)
        self.inspector_toggle.setText("Inspector ⇥" if show else "⇤ Inspector")
        if self.view.auto_fit:
            QTimer.singleShot(0, self.view.zoom_to_fit)

    def _toggle_structure(self, show: bool) -> None:
        splitter = getattr(self, "splitter", None)
        inspector = splitter.sizes()[2] if splitter is not None else 0
        self.structure_panel.setVisible(show)
        if splitter is not None and self.isVisible():
            self._canvas_takes_the_rest(inspector)

    def _canvas_takes_the_rest(self, inspector: int | None = None) -> None:
        """Side panels keep their width; the canvas gets whatever is left.

        (QSplitter would share freed space out in proportion, widening the
        inspector and squeezing the canvas's toolbar onto two lines.)
        """
        splitter = self.splitter
        structure = 250 if self.structure_panel.isVisible() else 0
        shown = 1 + self.structure_panel.isVisible() + self.inspector_panel.isVisible()
        total = splitter.width() - splitter.handleWidth() * (shown - 1)
        if inspector is None or inspector <= 0:
            # A little more than the minimum, unless the canvas needs it.
            minimum = self.inspector_panel.minimumWidth()
            inspector = minimum + 40 if total - structure - minimum - 40 >= 800 else minimum
        inspector = inspector if self.inspector_panel.isVisible() else 0
        splitter.setSizes([structure, max(300, total - structure - inspector), inspector])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            # On a small screen start with the canvas as large as possible
            # (single-page models rarely need the pages list).  Measured
            # against the space the window gives the page -- the page's own
            # width already includes the panel.
            from PySide6.QtWidgets import QScrollArea
            available = self.window().width()
            holder = self.parentWidget()
            while holder is not None and not isinstance(holder, QScrollArea):
                holder = holder.parentWidget()
            if holder is not None:
                available = holder.viewport().width()
            if len(self.net.pages) == 1 and (available < 1500 or
                                             self.minimumSizeHint().width() > available):
                self.structure_panel.setVisible(False)
            QTimer.singleShot(0, self._canvas_takes_the_rest)

    def _structure_tab(self, index: int) -> None:
        """Declarations are long lines of code: give them room while shown."""
        self.structure_stack.setCurrentIndex(index)
        splitter = getattr(self, "splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        wanted = 440 if index == 1 else 250
        if index == 1 and sizes[0] >= wanted:
            return
        spare = sizes[0] - wanted
        splitter.setSizes([wanted, max(300, sizes[1] + spare), sizes[2]])

    def _build_canvas(self) -> QWidget:
        card = Card()
        self.mode_switch = SegmentedControl(self.MODES)
        self.mode_switch.setToolTip("Edit: draw and change the model\n"
                                    "Step through: fire transitions one at a time\n"
                                    "Simulate: let the model run by itself")
        self.tool_switch = SegmentedControl([text for _, text, _ in self.TOOLS], compact=True)
        from .tool_icons import tool_icon
        for tool_button, (kind, _, tip) in zip(self.tool_switch.buttons, self.TOOLS):
            tool_button.setToolTip(tip)
            tool_button.setIcon(tool_icon(kind))
        self.sim_status = ElidedLabel("", "muted")
        self.structure_toggle = _tool("☰ Model", "Show or hide the pages and declarations "
                                      "panel")
        # What to write next to the green token counts (a compact menu button).
        self.values_button = _tool("Tokens", "What to write next to the green token "
                                   "counts. Hidden values still show on hover and in the "
                                   "Marking table.")
        self.values_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.values_button)
        self.values_actions = []
        for index, text in enumerate(["Values as in the model (CPN Tools’ “hide marking”)",
                                      "All values", "Counts only"]):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(index == 0)
            action.triggered.connect(lambda _=False, i=index: self._values_mode(i))
            self.values_actions.append(action)
        self.values_button.setMenu(menu)
        self.inspector_toggle = _tool("Inspector ⇥", "Show or hide the inspector, to give "
                                      "the canvas the full width")
        self.undo_button = _tool("↶", f"Undo the last change to the model "
                                  f"({shortcut_text('Ctrl+Z')})")
        self.redo_button = _tool("↷", f"Redo ({shortcut_text('Ctrl+Shift+Z')})")
        # A flow layout, like the simulation bar: when the model panel and the
        # inspector leave little room, the row wraps instead of widening the page.
        # The status goes last and takes its natural width (up to a limit), so
        # a long one moves to a line of its own rather than pushing buttons.
        self.sim_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.sim_status.setMaximumWidth(460)
        card.add(flow(self.structure_toggle, self.undo_button, self.redo_button, 10,
                      self.mode_switch, 10, self.values_button, self.inspector_toggle, 10,
                      self.sim_status))
        self.tool_hint = ElidedLabel("", "muted")
        self.names_box = QCheckBox("Names outside")
        self.names_box.setToolTip("Write the names of places and transitions next to them "
                                  "instead of inside; you can then drag each name where you "
                                  "want it")
        self.names_box.setChecked(getattr(self.net, "names_outside", False))
        self.names_box.toggled.connect(self._names_outside_toggled)
        # Neat nets: new and moved things on the grid, and tidying in one go.
        self.grid_box = QCheckBox("Snap to grid")
        self.grid_box.setToolTip("Put new and moved places, transitions and arc bends on "
                                 "the canvas's dots, so they line up neatly")
        self.grid_box.setChecked(NetScene.snap_to_grid)
        self.grid_box.toggled.connect(self._snap_toggled)
        self.tidy_button = _tool("Snap All to Grid", "Keep the layout, but move every "
                                 "place, transition and arc bend on this page to the "
                                 "nearest dot (undo puts them back)")
        self.tidy_button.clicked.connect(self.snap_everything)
        # Wraps like the row above: a fixed row made this the widest thing on
        # the page, so a narrow window pinned the splitter handles in place.
        self.tool_hint.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.tool_hint.setMaximumWidth(460)
        self.tool_row = flow(self.tool_switch, 12, self.names_box, 4, self.grid_box, 4,
                             self.tidy_button, 12, self.tool_hint)
        card.add(self.tool_row)

        # -- simulation bar ---------------------------------------------------
        self.reset_button = _tool("⟲ Reset", "Back to the initial marking at time 0")
        self.back_button = _tool("◀ Back", "Undo the last firing")
        self.step_button = _tool("Step ▶", f"Fire one random enabled binding "
                                 f"({shortcut_text('Ctrl+.')})")
        self.play_button = _tool("▶ Play", "Fire random enabled bindings automatically",
                                 checkable=True)
        self.speed = QSpinBox()
        self.speed.setRange(1, 60)
        self.speed.setValue(5)
        self.speed.setSuffix(" / s")
        self.speed.setToolTip("Play speed: firings per second")
        self.forward_steps = QSpinBox()
        self.forward_steps.setRange(1, 1_000_000)
        self.forward_steps.setValue(1000)
        self.forward_steps.setGroupSeparatorShown(True)
        self.forward_steps.setToolTip("How many steps Fast-forward fires (it stops earlier at "
                                      "a dead marking)")
        self.forward_button = _tool("⏭ Fast-forward", "Fire many steps as fast as possible "
                                    "and only show the result", checkable=True)
        self.repeat_box = QCheckBox("Repeat")
        self.repeat_box.setToolTip("When a run reaches a dead marking, start again")
        self.trace_box = QCheckBox("Trace")
        self.trace_box.setToolTip("Highlight the path the tokens took: every fired "
                                  "transition gets its step numbers and its arcs light up, "
                                  "the latest step strongest")
        self.trace_box.setChecked(CpnPage.show_trace)
        self.trace_box.toggled.connect(self._trace_toggled)
        for box in (self.repeat_box, self.trace_box):
            # As tall as the buttons, so the flow layout lines them up.
            box.setFixedHeight(self.reset_button.sizeHint().height())
        self.speed.setFixedWidth(84)
        self.forward_steps.setFixedWidth(104)
        steps_label = label("steps", "muted")
        # A flow layout: on a narrow window the bar wraps instead of widening the page.
        self.sim_bar = flow(self.reset_button, self.back_button, self.step_button, 12,
                            self.play_button, self.speed, 12, self.forward_button,
                            self.forward_steps, steps_label, 12, self.repeat_box, 12,
                            self.trace_box)
        card.add(self.sim_bar)
        self.simulate_widgets = [self.play_button, self.speed, self.forward_button,
                                 self.forward_steps, steps_label, self.repeat_box]
        self.sim_hint = label("", "muted", wrap=True)
        card.add(self.sim_hint)

        # -- canvas -------------------------------------------------------------
        self.scene = NetScene(self.net)
        self.view = NetView(self.scene)
        self.view.setMinimumSize(300, 240)
        card.add(self.view, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.scene.selection_changed.connect(self._selection_changed)
        self.scene.model_changed.connect(self._model_changed)
        # Undo: remember the model before every change the canvas makes.
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._pending_move: dict | None = None
        self.scene.about_to_change.connect(self._checkpoint)
        self.scene.drag_started.connect(self._drag_started)
        self.scene.moved.connect(self._moved)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        for keys, slot in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                           (QKeySequence("Ctrl+Shift+Z"), self.redo)):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self._update_undo_buttons()
        self.scene.transition_activated.connect(self._fire_transition)
        self.scene.created.connect(self._element_created)
        self.scene.rename_requested.connect(lambda element: self.start_rename(element.id))
        self.scene.message.connect(self.status.emit)
        self.mode_switch.changed.connect(self._mode_changed)
        self.tool_switch.changed.connect(self._tool_changed)
        self.reset_button.clicked.connect(self._reset)
        self.back_button.clicked.connect(self._back)
        self.step_button.clicked.connect(lambda: self._step(None))
        self.play_button.toggled.connect(self._play_toggled)
        self.speed.valueChanged.connect(
            lambda v: self.timer.setInterval(int(1000 / v)))
        self.forward_button.toggled.connect(self._forward_toggled)
        self.inspector_toggle.clicked.connect(self._toggle_inspector)
        self.structure_toggle.clicked.connect(
            lambda: self._toggle_structure(not self.structure_panel.isVisible()))
        for key in (QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)):
            shortcut = QShortcut(key, self.view)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(self._delete_selection)
        QShortcut(QKeySequence("Ctrl+."), self, lambda: self._step(None))
        return card

    def _build_inspector(self) -> QWidget:
        host = QWidget()
        self.inspector_panel = host
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.inspector_tabs = SegmentedControl(self.INSPECTOR, compact=True)
        # Room for the tabs (the segmented control grows for "Problems (12)"),
        # but no more: the page must fit a 13" laptop's window.
        host.setMinimumWidth(max(320, self.inspector_tabs.sizeHint().width() + 12))
        layout.addLayout(hbox(self.inspector_tabs, None))
        self.inspector_stack = QStackedWidget()
        for build in (self._build_simulation_tab, self._build_element_tab,
                      self._build_problems_tab, self._build_state_space_tab):
            self.inspector_stack.addWidget(scroll(build()))
        self.inspector_tabs.changed.connect(self.inspector_stack.setCurrentIndex)
        layout.addWidget(self.inspector_stack, 1)
        return host

    # -- inspector: simulation ------------------------------------------------------
    def _build_simulation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)
        self.step_tile = StatTile("Step", "0")
        self.time_tile = StatTile("Model time", "0")
        self.enabled_tile = StatTile("Enabled", "–")
        layout.addLayout(hbox(self.step_tile, self.time_tile, self.enabled_tile))

        bindings = Card("Enabled bindings", "Each is a transition with values for its "
                        "variables. Double-click one to fire exactly that binding.")
        self.bindings_card = bindings
        self.binding_filter = QLineEdit()
        self.binding_filter.setPlaceholderText("Filter…")
        self.binding_filter.setClearButtonEnabled(True)
        self.binding_filter.textChanged.connect(lambda _: self._fill_bindings())
        self.binding_list = QListWidget()
        self.binding_list.setFont(theme.ui_font(12))
        self.binding_list.setItemDelegate(HtmlDelegate(self.binding_list))
        self.binding_list.setResizeMode(QListWidget.Adjust)      # re-wrap rows on resize
        self.binding_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.binding_list.setMinimumHeight(150)
        self.binding_list.setMaximumHeight(260)
        self.binding_list.itemDoubleClicked.connect(
            lambda item: self._step(item.data(Qt.UserRole)))
        self.fire_selected = button("Fire selected", lambda: self._step(
            self.binding_list.currentItem().data(Qt.UserRole)
            if self.binding_list.currentItem() else None))
        self.binding_note = label("", "muted", wrap=True)
        bindings.add(self.binding_filter)
        bindings.add(self.binding_list)
        bindings.add(hbox(self.binding_note, None, self.fire_selected))
        layout.addWidget(bindings)

        marking = Card("Marking", "Tokens on each place. Click a row to find the place.")
        self.marking_card = marking
        self.only_marked = QCheckBox("Only places with tokens")
        self.only_marked.setChecked(True)
        self.only_marked.toggled.connect(lambda _: self._fill_marking())
        marking.add(self.only_marked)
        self.marking_table = QTableWidget(0, 3)
        self.marking_table.setHorizontalHeaderLabels(["Place", "#", "Tokens"])
        self.marking_table.verticalHeader().hide()
        self.marking_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.marking_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.marking_table.setAlternatingRowColors(True)
        self.marking_table.setShowGrid(False)
        self.marking_table.setWordWrap(False)
        header = self.marking_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.marking_table.setMinimumHeight(180)
        self.marking_table.cellClicked.connect(self._marking_row_clicked)
        marking.add(self.marking_table)
        layout.addWidget(marking)

        history = Card("History", "Firings so far, newest first. Double-click one to go "
                       "back to the state right after it.")
        self.history_list = QListWidget()
        self.history_list.setFont(theme.ui_font(12))
        self.history_list.setItemDelegate(HtmlDelegate(self.history_list))
        self.history_list.setResizeMode(QListWidget.Adjust)      # re-wrap rows on resize
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.setMinimumHeight(140)
        self.history_list.setMaximumHeight(240)
        self.history_list.itemDoubleClicked.connect(self._rewind_to_item)
        history.add(self.history_list)
        self.export_log_button = button("Mine the simulated log ›", self._export_log,
                                        tooltip="Turn the firings so far into an event log, save "
                                                "it in the folder and open it in Mine (dotted chart, "
                                                "discovery, conformance, …)")
        history.add(hbox(None, self.export_log_button))
        layout.addWidget(history)
        layout.addStretch(1)
        return page

    # -- inspector: element -----------------------------------------------------------
    def _build_element_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        self.element_card = Card("Nothing selected")
        self.element_hint = label("Click a place, transition or arc on the canvas to see and "
                                  "edit its inscriptions. Changes apply when you press Return "
                                  "or leave the field.", "muted", wrap=True)
        self.element_card.add(self.element_hint)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self.form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.name_edit = self._line_edit(mono=False)
        self.colour_set_box = QComboBox()
        self.colour_set_box.setEditable(True)
        self.colour_set_box.activated.connect(self._commit_element)
        self.colour_set_box.lineEdit().editingFinished.connect(self._commit_element)
        self.initial_edit = self._line_edit()
        self.guard_edit = self._line_edit()
        self.time_edit = self._line_edit()
        self.expression_edit = self._line_edit()
        self.orientation_box = QComboBox()
        self.orientation_box.addItems(["Place → Transition (input)",
                                       "Transition → Place (output)",
                                       "Both directions"])
        self.orientation_box.activated.connect(self._commit_element)
        self.rows: dict[str, tuple[QWidget, QWidget]] = {}
        for key, caption, widget in (
                ("name", "Name", self.name_edit),
                ("colour_set", "Colour set", self.colour_set_box),
                ("initial", "Initial marking", self.initial_edit),
                ("guard", "Guard", self.guard_edit),
                ("time", "Time delay  @+", self.time_edit),
                ("expression", "Inscription", self.expression_edit),
                ("orientation", "Direction", self.orientation_box)):
            caption_label = label(caption, "muted")
            self.form.addRow(caption_label, widget)
            self.rows[key] = (caption_label, widget)
        self.element_card.add(self.form)
        self.element_info = label("", "muted", wrap=True, selectable=True)
        self.element_card.add(self.element_info)
        # A substitution transition: go to the page it stands for.
        self.subpage_button = button("Open subpage", self._open_subpage, kind="ghost",
                                     tooltip="Show the page this substitution transition "
                                             "stands for")
        self.element_card.add(hbox(self.subpage_button, None))
        self.subpage_button.hide()
        self.element_problems = QVBoxLayout()
        self.element_card.add(self.element_problems)
        layout.addWidget(self.element_card)
        layout.addStretch(1)
        self._show_element(None)
        return page

    def _line_edit(self, mono: bool = True) -> QLineEdit:
        edit = QLineEdit()
        if mono:
            edit.setFont(theme.mono_font(12))
        edit.editingFinished.connect(self._commit_element)
        return edit

    # -- inspector: problems ------------------------------------------------------------
    def _build_problems_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        self.problems_card = Card("Problems", "Inscriptions and declarations that did not "
                                  "compile. Click Show to jump to the element.")
        layout.addWidget(self.problems_card)
        layout.addStretch(1)
        return page

    # -- inspector: state space ----------------------------------------------------------
    def _build_state_space_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)
        card = Card("State space", "Every reachable marking (with its model time) and the "
                    "binding elements between them, explored breadth-first from the initial "
                    "marking — the analysis CPN Tools calls the state space tool.")
        from ...analysis.state_space import StateSpace
        from .provenance import add_code
        add_code(card, StateSpace)
        self.max_nodes = QSpinBox()
        self.max_nodes.setRange(10, 1_000_000)
        self.max_nodes.setValue(20_000)
        self.max_nodes.setGroupSeparatorShown(True)
        self.max_nodes.setToolTip("Stop exploring after this many nodes (the report then "
                                  "says PARTIAL)")
        self.space_button = button("Calculate", self._calculate_state_space, kind="primary")
        # A flow layout: in a narrow inspector the button wraps below instead
        # of being cut off.
        card.add(flow(label("Node limit", "muted"), self.max_nodes, 12, self.space_button))
        self.space_status = label("", "muted", wrap=True)
        card.add(self.space_status)
        layout.addWidget(card)
        self.space_results = Card()
        self.space_results.setVisible(False)
        layout.addWidget(self.space_results)
        layout.addStretch(1)
        return page

    # =====================================================================
    # Refreshing
    # =====================================================================
    def _subtitle(self) -> str:
        places = sum(1 for _ in self.net.all_places())
        transitions = sum(1 for _ in self.net.all_transitions())
        pages = len(self.net.pages)
        where = Path(self.document.path).name if self.document.path else "not saved yet"
        state = " · edited" if self.document.dirty and not self.document.autosave else ""
        return (f"Coloured Petri net · {pages} page{'s' * (pages != 1)} · {places} places · "
                f"{transitions} transitions · {where}{state}")

    def restyle(self) -> None:
        """Colours changed (light/dark): redraw the canvas items."""
        self.scene.rebuild()
        self.refresh_all()
        self.highlighter.rehighlight()

    def refresh_title(self) -> None:
        self.header.set_text(self.net.name, self._subtitle())

    def refresh_all(self) -> None:
        self.refresh_title()
        self._fill_pages()
        self._fill_problems()
        self.scene.refresh_labels()
        self.refresh_simulation()

    def _fill_pages(self) -> None:
        self.page_list.blockSignals(True)
        self.page_list.clear()
        parents = {t.substitution_subpage: t for t in self.net.all_transitions()
                   if t.is_substitution}
        for page in self.net.pages:
            item = QListWidgetItem(page.name)
            owner = parents.get(page.id)
            item.setData(Qt.UserRole + 1, f"{len(page.places)} places · "
                                          f"{len(page.transitions)} transitions"
                         + (f" · subpage of “{owner.name}”" if owner else ""))
            self.page_list.addItem(item)
        if self.scene.page in self.net.pages:
            self.page_list.setCurrentRow(self.net.pages.index(self.scene.page))
        self.page_list.blockSignals(False)

    def refresh_simulation(self, light: bool = False) -> None:
        """Enabling, markings, tiles, bindings and history.

        ``light`` skips the lists (used while playing fast): the canvas and
        the tiles are what one watches then.
        """
        counts: dict[str, int] = {}
        self.enabled_elements = []
        try:
            for transition in self.net.all_transitions():
                elements = self.simulator.enabled_bindings(transition)
                if elements:
                    counts[transition.id] = len(elements)
                    self.enabled_elements.extend(elements)
        except Exception as error:  # noqa: BLE001 - a model error, surfaced not swallowed
            self.status.emit(f"Enabling could not be computed: {error}")
        self.scene.update_enabling(counts)
        self.scene.update_markings(self._marking_for)
        last = self.simulator.log[-1].binding.transition_id if self.simulator.log else None
        for transition_id, item in self.scene.transition_items.items():
            item.set_halo(self.simulating and transition_id == last)
        self._update_trace()

        self.step_tile.set(f"{self.simulator.step_count:,}")
        self.time_tile.set(format_time(self.simulator.clock))
        transitions = len(counts)
        self.enabled_tile.set(f"{len(self.enabled_elements):,}",
                              f"binding{'s' * (len(self.enabled_elements) != 1)} of "
                              f"{transitions} transition{'s' * (transitions != 1)}")
        self._update_sim_status()
        if not light:
            self._fill_bindings()
            self._fill_marking()
            self._fill_history()

    def _update_trace(self) -> None:
        if self.simulating and self.trace_box.isChecked():
            log = self.simulator.log[-200:]
            self.scene.set_trace([(record.step, record.binding.transition_id)
                                  for record in log])
        else:
            self.scene.set_trace(None)

    def _trace_toggled(self, on: bool) -> None:
        CpnPage.show_trace = on            # the next net opens the same way
        self._update_trace()

    def _marking_for(self, place_id: str) -> tuple[int, str]:
        tokens = self.simulator.marking.get(self.net.marking_key(place_id))
        count = tokens.size()
        return (0, "") if not count else (count, str(tokens))

    def _dead(self) -> bool:
        """Nothing enabled now, and waiting (advancing time) cannot help."""
        if self.enabled_elements:
            return False
        from ...ml.multiset import TimedMultiset
        for place in self.net.all_places():
            tokens = self.simulator.marking.get(self.net.marking_key(place.id))
            if isinstance(tokens, TimedMultiset) and \
                    tokens.next_time_after(self.simulator.clock) is not None:
                return False
        return True

    def _update_sim_status(self) -> None:
        if not self.simulating:
            problems = len(self.net.errors)
            self.sim_status.setText("✓ compiles" if not problems else
                                    f"⚠ {problems} problem{'s' * (problems != 1)} — see "
                                    "the Problems tab")
            return
        if self.enabled_elements:
            state = f"{len(self.enabled_elements):,} enabled"
        elif self._dead():
            state = "✕ dead marking: nothing can happen any more"
        else:
            state = "waiting: tokens become available later (Step advances time)"
        runs = f" · {self.runs_completed} run(s)" if self.mode_switch.index() == 2 and \
            self.runs_completed else ""
        self.sim_status.setText(f"Step {self.simulator.step_count:,} · time "
                                f"{format_time(self.simulator.clock)} · {state}{runs}")
        self.back_button.setEnabled(bool(self.simulator.log))
        self.step_button.setEnabled(not self._dead())

    def _fill_bindings(self) -> None:
        wanted = self.binding_filter.text().strip().lower()
        self.binding_list.clear()
        shown = 0
        matching = 0
        for element in getattr(self, "enabled_elements", []):
            text = element.describe(self.net)
            if wanted and wanted not in text.lower():
                continue
            matching += 1
            if shown >= self.MAX_BINDINGS_SHOWN:
                continue
            item = QListWidgetItem(binding_html(self.net, element))
            item.setData(Qt.UserRole, element)
            item.setToolTip(text)
            self.binding_list.addItem(item)
            shown += 1
        total = len(getattr(self, "enabled_elements", []))
        if not total:
            self.binding_note.setText("Nothing is enabled." + ("" if self._dead() else
                                      " Step advances the clock to the next token."))
        elif shown < matching:
            self.binding_note.setText(f"Showing {shown:,} of {matching:,}; filter to narrow.")
        elif wanted:
            self.binding_note.setText(f"{matching:,} of {total:,} match")
        else:
            self.binding_note.setText(f"{total:,} enabled")
        self.fire_selected.setEnabled(bool(shown))

    def _fill_marking(self) -> None:
        rows = []
        for place in self.net.all_places():
            tokens = self.simulator.marking.get(self.net.marking_key(place.id))
            count = tokens.size()
            if count or not self.only_marked.isChecked():
                rows.append((place, count, str(tokens) if count else "empty"))
        self.marking_table.setRowCount(len(rows))
        mono = theme.mono_font(11)
        for row, (place, count, text) in enumerate(rows):
            name = QTableWidgetItem(place.name)
            name.setData(Qt.UserRole, place.id)
            number = QTableWidgetItem(f"{count:,}")
            number.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tokens = QTableWidgetItem(text)
            tokens.setFont(mono)
            tokens.setToolTip(text if len(text) < 2000 else text[:2000] + " …")
            if not count:
                tokens.setForeground(QColor(style.tokens().text_muted))
            self.marking_table.setItem(row, 0, name)
            self.marking_table.setItem(row, 1, number)
            self.marking_table.setItem(row, 2, tokens)

    def _fill_history(self) -> None:
        self.history_list.clear()
        muted = style.tokens().text_muted
        for record in reversed(self.simulator.log[-300:]):
            item = QListWidgetItem(
                f"<span style='color:{muted}'>{record.step:,} · t = "
                f"{escape(format_time(record.time))}</span>&nbsp;&nbsp;"
                + binding_html(self.net, record.binding))
            item.setData(Qt.UserRole, record.step)
            self.history_list.addItem(item)
        if not self.simulator.log:
            item = QListWidgetItem(f"<span style='color:{muted}'>Nothing has fired yet.</span>")
            item.setFlags(Qt.NoItemFlags)
            self.history_list.addItem(item)

    def _fill_problems(self) -> None:
        errors = list(self.net.errors)
        count = len(errors)
        self.inspector_tabs.buttons[2].setText(f"Problems ({count})" if count else "Problems")
        card = self.problems_card
        card.clear()
        if not errors:
            card.add(Verdict("Model compiles", "good", "Every declaration and inscription "
                             "parsed. The model is ready to simulate."))
            return
        for issue in errors[:200]:
            row = QWidget()
            title = f"{issue.element_name or 'Declarations'} — {issue.field_name}"
            layout = hbox(Verdict(title, "critical", issue.message), spacing=6)
            if issue.element_id:
                layout.addWidget(button("Show", lambda _=False, i=issue.element_id:
                                        self.reveal_element(i), kind="ghost"), 0, Qt.AlignTop)
            elif issue.field_name == "declarations":
                layout.addWidget(button("Show", lambda: self.structure_switch.set_index(1),
                                        kind="ghost"), 0, Qt.AlignTop)
            row.setLayout(layout)
            card.add(row)

    # =====================================================================
    # Modes and tools
    # =====================================================================
    @property
    def simulating(self) -> bool:
        return self.mode_switch.index() in (1, 2)

    def _mode_changed(self, index: int) -> None:
        self._stop_running()
        editing = index == 0
        self.tool_row.setVisible(editing)
        self.sim_bar.setVisible(not editing)
        for widget in self.simulate_widgets:
            widget.setVisible(index == 2)
        self.sim_hint.setText(
            "" if editing else
            "Green = enabled. Click a green transition to fire it, or double-click a binding "
            "in the inspector to choose the values. Back undoes. Trace lights up the path "
            "so far." if index == 1 else
            "Play fires random bindings by itself (blue halo = fired last). Fast-forward runs "
            "many steps at once. History ▸ Export as event log makes a log you can mine.")
        self.sim_hint.setVisible(not editing)
        self.scene.fire_on_click = not editing
        self.scene.set_editable(editing)
        if not editing:
            self.tool_switch.set_index(0)
            if self.net.errors:
                self.status.emit("The model has problems; fix them (Problems tab) for "
                                 "reliable simulation.")
        self.refresh_simulation()

    def _tool_changed(self, index: int) -> None:
        from .tool_icons import tool_cursor
        tool = self.TOOLS[index][0]
        self.scene._cancel_connection()
        self.scene.hide_connect_handle()
        self.scene.tool = tool
        delete = shortcut_text("Backspace")
        self.tool_hint.setText({
            "select": f"Drag to move · hover a node and drag its arrow to connect, or "
                      f"to empty space to add the next node · double-click to rename · "
                      f"{delete} deletes",
            "place": "Click on the canvas to add a place",
            "transition": "Click on the canvas to add a transition",
            "silent": "Click on the canvas to add a silent (τ) transition: a black bar that "
                      "moves tokens without an event in the trace",
            "arc": "Drag from a place to a transition (or back), or to empty space to add "
                   "one — or click one node, then the other",
        }[tool])
        self.scene.set_editable(self.scene.editable)      # handles only with the select tool
        self.view.setDragMode(NetView.RubberBandDrag if tool == "select" else NetView.NoDrag)
        self.view.viewport().setCursor(tool_cursor(tool))

    # -- naming things on the canvas ---------------------------------------------------
    # -- neat nets ----------------------------------------------------------------
    def _snap_toggled(self, on: bool) -> None:
        NetScene.snap_to_grid = on
        self.snap_changed.emit(on)

    def snap_everything(self) -> None:
        """Snap All to Grid: every node and arc bend of this page, layout kept."""
        from ..canvas import GRID_STEP
        from ..tidy import snap_page
        if self.scene.page is None:
            return
        self._checkpoint()
        moved = snap_page(self.scene.page, GRID_STEP)
        self._edited()
        self.status.emit(f"Snapped to the grid ({moved} node{'s' * (moved != 1)} moved) — "
                         f"{shortcut_text('Ctrl+Z')} puts them back")

    def _names_outside_toggled(self, outside: bool) -> None:
        if getattr(self.net, "names_outside", False) == outside:
            return
        self._push_undo(self._snapshot())
        self.net.names_outside = outside
        self.scene.rebuild()
        self.refresh_simulation()
        self.set_dirty(True)

    def _element_created(self, element) -> None:
        """A new place or transition: back to Select (from the Place, Transition
        and τ tools) and type its name (a silent transition has none to type)."""
        if self.scene.tool in ("place", "transition", "silent"):
            self.tool_switch.set_index(0)
        if not getattr(element, "silent", False):
            QTimer.singleShot(0, lambda: self.start_rename(element.id))

    def start_rename(self, element_id: str) -> None:
        """Edit a place's or transition's name right on the canvas."""
        item = self.scene.place_items.get(element_id) or \
            self.scene.transition_items.get(element_id)
        if item is None or self.mode_switch.index() != 0:
            return
        element = getattr(item, "place", None) or item.transition
        self.scene.clearSelection()
        item.setSelected(True)

        def done(text: str) -> None:
            text = text.strip()
            current = self.net.find_place(element_id) or self.net.find_transition(element_id)
            if current is None or not text or text == current.name:
                return
            self._push_undo(self._snapshot())
            current.name = text
            self._edited()
            self.reveal_element(element_id)

        def anchor():
            # Looked up afresh each time: the node may be redrawn (a new
            # item) or moved while the box is open, or deleted.
            current = self.scene.place_items.get(element_id) or \
                self.scene.transition_items.get(element_id)
            return current.name_anchor() if current is not None else None

        self.view.edit_text(anchor, element.name, done)

    # =====================================================================
    # Simulation
    # =====================================================================
    def _step(self, element=None) -> bool:
        if self.busy:
            return False
        if not self.simulating:
            self.mode_switch.set_index(1)
        try:
            fired = self.simulator.step(element)
        except DeadMarkingError:
            self.status.emit(f"Dead marking at time {format_time(self.simulator.clock)} "
                             f"after {self.simulator.step_count:,} steps")
            self.refresh_simulation()
            return False
        except Exception as error:  # noqa: BLE001
            self._stop_running()
            QMessageBox.warning(self, "Simulation error", str(error))
            return False
        text = fired.describe(self.net)
        self.status.emit(f"Fired {text if len(text) < 140 else text[:138] + ' …'}")
        self.refresh_simulation(light=self.timer.isActive() and self.speed.value() > 12)
        return True

    def _fire_transition(self, transition: Transition) -> None:
        """Clicking an enabled transition fires one of its bindings."""
        elements = self.simulator.enabled_bindings(transition)
        if elements:
            self._step(self.simulator.choice_rng.choice(elements))

    def _back(self) -> None:
        if self.simulator.log:
            self.simulator.rewind_to(self.simulator.step_count - 1)
            self.refresh_simulation()

    def _reset(self) -> None:
        self._stop_running()
        self.simulator.reset()
        self.runs_completed = 0
        self.status.emit("Back at the initial marking")
        self.refresh_simulation()

    def _rewind_to_item(self, item: QListWidgetItem) -> None:
        step = item.data(Qt.UserRole)
        if step is not None:
            self._stop_running()
            self.simulator.rewind_to(step)
            self.status.emit(f"Went back to the state after step {step:,}")
            self.refresh_simulation()

    def _play_toggled(self, on: bool) -> None:
        self.play_button.setText("⏸ Pause" if on else "▶ Play")
        if on:
            self.forward_button.setChecked(False)
            self.timer.start(int(1000 / self.speed.value()))
        else:
            self.timer.stop()
            self.refresh_simulation()

    def _tick(self) -> None:
        if self._step(None):
            return
        # A dead marking: the run is over.
        self.runs_completed += 1
        if self.repeat_box.isChecked():
            self.simulator.reset()
            self.refresh_simulation(light=True)
        else:
            self.play_button.setChecked(False)

    def _forward_toggled(self, on: bool) -> None:
        self.forward_button.setText("■ Stop" if on else "⏭ Fast-forward")
        if on:
            self.play_button.setChecked(False)
            self.fast_forward_left = self.forward_steps.value()
            QTimer.singleShot(0, self._forward_chunk)
        else:
            self.fast_forward_left = 0

    def _forward_chunk(self) -> None:
        """Fire a chunk of steps, then yield to the event loop (keeps the UI alive)."""
        if self.fast_forward_left <= 0:
            self.forward_button.setChecked(False)
            self.refresh_simulation()
            return
        chunk = min(200, self.fast_forward_left)
        taken = self.simulator.run(chunk)
        self.fast_forward_left -= chunk
        if taken < chunk:                       # dead marking
            self.runs_completed += 1
            if self.repeat_box.isChecked() and self.fast_forward_left > 0:
                self.simulator.reset()
            else:
                self.fast_forward_left = 0
        self.refresh_simulation(light=True)
        self.status.emit(f"Fast-forward: step {self.simulator.step_count:,}, time "
                         f"{format_time(self.simulator.clock)}")
        QTimer.singleShot(0, self._forward_chunk)

    def _stop_running(self) -> None:
        if hasattr(self, "play_button"):
            self.play_button.setChecked(False)
            self.forward_button.setChecked(False)
        if hasattr(self, "timer"):
            self.timer.stop()

    def _export_log(self) -> None:
        records = list(self.simulator.log)
        if not records:
            self.status.emit("Nothing has fired yet: simulate first, then export.")
            return
        counts: dict[str, int] = {}
        for record in records:
            for name, _value in record.binding.assignments:
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            QMessageBox.information(self, "Export as event log",
                                    "The transitions that fired have no variables, so there "
                                    "is nothing to identify cases by.")
            return
        variables = sorted(counts, key=lambda n: (-counts[n], n))
        preferred = next((v for v in variables if v.lower() in ("id", "case", "caseid", "cid",
                                                                "pid", "passenger")), variables[0])
        dialog = CaseVariableDialog(variables, preferred, self)
        if dialog.exec() != QDialog.Accepted:
            return
        log = simulation_to_log(self.net, records, dialog.variable.currentText(),
                                dialog.unit.currentText())
        self.status.emit(f"Exported {len(log):,} cases from {len(records):,} firings")
        self.log_generated.emit(log)

    # =====================================================================
    # Selection and editing
    # =====================================================================
    def _selection_changed(self, element) -> None:
        self._show_element(element)
        # While editing, selecting something opens its properties.  While
        # simulating, clicks fire transitions: stay on the Simulation tab.
        if element is not None and self.mode_switch.index() == 0:
            self.inspector_tabs.set_index(1)
            if not self.inspector_panel.isVisible():
                self._toggle_inspector()

    def reveal_element(self, element_id: str) -> None:
        """Switch to the element's page, select it and centre on it."""
        element = self.net.find_place(element_id) or self.net.find_transition(element_id) or \
            next((a for a in self.net.all_arcs() if a.id == element_id), None)
        if element is None:
            return
        page = self.net.page_of(element)
        if page is not None and page is not self.scene.page:
            self.scene.show_page(page)
            self._fill_pages()
            self.refresh_simulation()
        item = (self.scene.place_items.get(element_id) or
                self.scene.transition_items.get(element_id) or
                self.scene.arc_items.get(element_id))
        if item is not None:
            self.scene.clearSelection()
            item.setSelected(True)
            # Scroll only if it is out of sight: a jump under the mouse while
            # drawing (every edit reveals the selection) loses your place.
            shown = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            if not shown.contains(item.sceneBoundingRect()):
                self.view.centerOn(item)

    def _marking_row_clicked(self, row: int, _column: int) -> None:
        item = self.marking_table.item(row, 0)
        if item is not None:
            tab = self.inspector_tabs.index()
            self.reveal_element(item.data(Qt.UserRole))
            self.inspector_tabs.set_index(tab)          # stay on the marking

    def _set_rows(self, keys: set[str]) -> None:
        for key, (caption, widget) in self.rows.items():
            caption.setVisible(key in keys)
            widget.setVisible(key in keys)

    def _show_element(self, element) -> None:
        self.element = element
        card = self.element_card
        while self.element_problems.count():
            item = self.element_problems.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.element_info.setText("")
        self.subpage_button.hide()
        self.element_hint.setVisible(element is None)
        if element is None:
            card.title_label.setText("Nothing selected")
            self._set_rows(set())
            return
        if isinstance(element, Place):
            card.title_label.setText(f"Place · {element.name}")
            self.colour_set_box.blockSignals(True)
            self.colour_set_box.clear()
            self.colour_set_box.addItems(sorted(self.net.declarations.colour_sets))
            self.colour_set_box.setCurrentText(element.colour_set_name)
            self.colour_set_box.blockSignals(False)
            self.name_edit.setText(element.name)
            self.initial_edit.setText(element.initial_marking_text)
            self._set_rows({"name", "colour_set", "initial"})
            tokens = self.simulator.marking.get(self.net.marking_key(element.id))
            self.element_info.setText(f"Current marking: {tokens.size():,} token(s)"
                                      + (f"\n{tokens}" if tokens.size() else ""))
        elif isinstance(element, Transition):
            kind = "Substitution transition" if element.is_substitution else "Transition"
            card.title_label.setText(f"{kind} · {element.name}")
            self.name_edit.setText(element.name)
            self.guard_edit.setText(element.guard_text)
            self.time_edit.setText(element.time_text)
            self._set_rows({"name", "guard", "time"})
            subpage = self._subpage_of(element)
            if element.is_substitution:
                self.element_info.setText(
                    f"Stands for the page “{subpage.name if subpage else '?'}”: its port "
                    "places are the socket places around this transition, and its own "
                    "transitions do the work.")
                self.subpage_button.setVisible(subpage is not None)
            else:
                enabled = len(self.simulator.enabled_bindings(element))
                self.element_info.setText(
                    f"{enabled:,} enabled binding(s) in the current marking.")
        elif isinstance(element, Arc):
            place = self.net.find_place(element.place_id)
            transition = self.net.find_transition(element.transition_id)
            card.title_label.setText("Arc")
            self.expression_edit.setText(element.expression_text)
            self.orientation_box.setCurrentIndex(
                {"PtoT": 0, "TtoP": 1, "BOTHDIR": 2}.get(element.orientation, 0))
            self._set_rows({"expression", "orientation"})
            self.element_info.setText(f"Between place “{place.name if place else '?'}” and "
                                      f"transition “{transition.name if transition else '?'}”.")
        for issue in self.net.errors:
            if issue.element_id == getattr(element, "id", None):
                self.element_problems.addWidget(Verdict(issue.field_name, "critical",
                                                        issue.message))

    def _commit_element(self) -> None:
        element = self.element
        if element is None:
            return
        before = self._element_state(element)
        snapshot = self._snapshot()
        if isinstance(element, Place):
            element.name = self.name_edit.text().strip() or element.name
            element.colour_set_name = self.colour_set_box.currentText().strip()
            element.initial_marking_text = self.initial_edit.text()
        elif isinstance(element, Transition):
            element.name = self.name_edit.text().strip() or element.name
            element.guard_text = self.guard_edit.text()
            element.time_text = self.time_edit.text()
        elif isinstance(element, Arc):
            element.expression_text = self.expression_edit.text()
            element.orientation = ["PtoT", "TtoP", "BOTHDIR"][self.orientation_box.currentIndex()]
        if self._element_state(element) != before:
            self._push_undo(snapshot)
            self._edited()
            self._show_element(element)

    @staticmethod
    def _element_state(element) -> tuple:
        return tuple(getattr(element, name, None) for name in (
            "name", "colour_set_name", "initial_marking_text", "guard_text", "time_text",
            "expression_text", "orientation"))

    def _model_changed(self) -> None:
        self._edited()

    # -- undo / redo --------------------------------------------------------------------
    UNDO_LIMIT = 100

    def _snapshot(self) -> dict:
        """A copy of everything an edit can change (not the simulation)."""
        block = self.net.declarations
        page = self.scene.page
        return {
            "pages": copy_pages(self.net.pages),
            "declarations": (list(block.colour_set_sources), list(block.variable_sources),
                             list(block.ml_sources), list(block.globref_sources)),
            "name": self.net.name,
            "names_outside": getattr(self.net, "names_outside", False),
            "fusion": {k: list(v) for k, v in self.net.fusion_sets.items()},
            "page": self.net.pages.index(page) if page in self.net.pages else 0,
        }

    def _push_undo(self, snapshot: dict) -> None:
        self.undo_stack.append(snapshot)
        del self.undo_stack[:-self.UNDO_LIMIT]
        self.redo_stack.clear()
        self._update_undo_buttons()

    def _checkpoint(self) -> None:
        """Called just before a change: remember how things were."""
        self._pending_move = None
        self._push_undo(self._snapshot())

    def _drag_started(self) -> None:
        self._pending_move = self._snapshot()

    def _moved(self) -> None:
        if self._pending_move is not None:
            self._push_undo(self._pending_move)
            self._pending_move = None
        self.set_dirty(True)

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())
            self.status.emit("Undone")

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())
            self.status.emit("Redone")

    def _restore(self, snapshot: dict) -> None:
        self.net.pages = copy_pages(snapshot["pages"])      # the stack keeps its own copy
        block = self.net.declarations
        (block.colour_set_sources, block.variable_sources, block.ml_sources,
         block.globref_sources) = (list(x) for x in snapshot["declarations"])
        self.net.name = snapshot["name"]
        self.net.names_outside = snapshot.get("names_outside", False)
        self.names_box.blockSignals(True)
        self.names_box.setChecked(self.net.names_outside)
        self.names_box.blockSignals(False)
        self.net.fusion_sets = {k: list(v) for k, v in snapshot["fusion"].items()}
        index = min(snapshot["page"], len(self.net.pages) - 1)
        self.scene.page = self.net.pages[index] if self.net.pages else None
        self.element = None
        self._show_element(None)
        self._edited()
        self._reload_declarations()
        self._update_undo_buttons()
        self.dirty_changed.emit(True)        # the sidebar re-reads the (maybe restored) name

    def _update_undo_buttons(self) -> None:
        self.undo_button.setEnabled(bool(self.undo_stack))
        self.redo_button.setEnabled(bool(self.redo_stack))

    def _delete_selection(self) -> None:
        if self.mode_switch.index() == 0:
            self.scene.delete_selected()

    def _edited(self) -> None:
        """Something that can change behaviour was edited: recompile and restart."""
        self._stop_running()
        self.net.compile()
        self.simulator = Simulator(self.net)
        self.scene.rebuild()
        self.set_dirty(True)
        self.refresh_all()
        if self.element is not None:
            self.reveal_element(self.element.id)

    def set_dirty(self, dirty: bool) -> None:
        if self.document.dirty != dirty:
            self.document.dirty = dirty
            self.dirty_changed.emit(dirty)
        if dirty:
            self.edited.emit()
        self.refresh_title()

    # -- pages --------------------------------------------------------------------------
    def _subpage_of(self, transition):
        return next((page for page in self.net.pages
                     if page.id == transition.substitution_subpage), None)

    def _open_subpage(self) -> None:
        """Show the subpage of the selected substitution transition."""
        element = getattr(self, "element", None)
        subpage = self._subpage_of(element) if isinstance(element, Transition) else None
        if subpage is None:
            return
        self._toggle_structure(True)
        self.page_list.setCurrentRow(self.net.pages.index(subpage))

    def _page_selected(self, row: int) -> None:
        if 0 <= row < len(self.net.pages):
            self.scene.show_page(self.net.pages[row])
            self.refresh_simulation()
            self.view.zoom_to_fit()

    def _add_page(self) -> None:
        name, ok = QInputDialog.getText(self, "Add page", "Page name:",
                                        text=f"Page {len(self.net.pages) + 1}")
        if not ok or not name.strip():
            return
        self._checkpoint()
        page = self.net.add_page(name.strip())
        self.scene.show_page(page)
        self._edited()
        self.mode_switch.set_index(0)

    def _rename_page(self) -> None:
        row = self.page_list.currentRow()
        if not 0 <= row < len(self.net.pages):
            return
        page = self.net.pages[row]
        name, ok = QInputDialog.getText(self, "Rename page", "Page name:", text=page.name)
        if ok and name.strip() and name.strip() != page.name:
            self._checkpoint()
            page.name = name.strip()
            self.set_dirty(True)
            self._fill_pages()

    # -- declarations -------------------------------------------------------------------
    def _declarations_text(self) -> str:
        block = self.net.declarations
        lines: list[str] = [source.strip() for _name, source in block.colour_set_sources]
        seen: set[str] = set()
        for _name, _colour_set, source in block.variable_sources:
            if source not in seen:
                seen.add(source)
                lines.append(source.strip())
        lines += [source.strip() for source in block.ml_sources]
        lines += [f"globref {name} = {initial};" for name, initial in block.globref_sources]
        return "\n".join(lines)

    def _reload_declarations(self) -> None:
        self.declarations_editor.blockSignals(True)
        self.declarations_editor.setPlainText(self._declarations_text())
        self.declarations_editor.blockSignals(False)
        self._declarations_edited()

    def _declarations_edited(self) -> None:
        changed = self.declarations_editor.toPlainText().strip() != \
            self._declarations_text().strip()
        self.apply_button.setEnabled(changed)
        self.revert_button.setEnabled(changed)
        errors = [e for e in self.net.errors if e.field_name == "declarations"]
        t = style.tokens()
        if changed:
            self.declarations_status.setText("Edited — press Apply to recompile.")
            self.declarations_status.setStyleSheet(f"color: {t.text_muted}")
        elif errors:
            self.declarations_status.setText("✕ " + errors[0].message)
            self.declarations_status.setStyleSheet(f"color: {style.STATUS['critical']}")
        else:
            count = len(self.net.declarations.colour_sets)
            self.declarations_status.setText(f"✓ compiled · {count} colour sets available")
            self.declarations_status.setStyleSheet(f"color: {t.text_muted}")

    def _apply_declarations(self) -> None:
        """Re-read the text: declarations end with ``;`` at the end of a line."""
        self._checkpoint()
        block = self.net.declarations
        block.colour_set_sources.clear()
        block.variable_sources.clear()
        block.ml_sources.clear()
        block.globref_sources.clear()
        buffer: list[str] = []
        for line in self.declarations_editor.toPlainText().splitlines():
            stripped = line.rstrip()
            if not stripped.strip():
                continue
            buffer.append(stripped)
            if stripped.endswith(";"):
                self.net.add_declaration("\n".join(buffer))
                buffer = []
        if buffer:
            self.net.add_declaration("\n".join(buffer))
        self._edited()
        self._reload_declarations()

    # =====================================================================
    # State space
    # =====================================================================
    def _calculate_state_space(self) -> None:
        """Explore in a separate process; the button turns into Cancel meanwhile.

        A process, not a thread: exploration is pure Python, and in a thread
        it would hold the interpreter lock and make the whole window stutter.
        """
        if self.busy:                                   # the button says "Stop"
            self._space_job.cancel()
            self._space_cancel_at = time.monotonic()
            self.space_button.setEnabled(False)
            self.space_status.setText("Stopping… (keeping what was found so far)")
            return
        if self.net.errors:
            self.space_status.setText("Fix the problems in the Problems tab first: the state "
                                      "space of a model that does not compile is meaningless.")
            return
        from ...analysis.state_space_process import StateSpaceJob
        try:
            job = StateSpaceJob(self.net, self.max_nodes.value())
            job.start()
        except Exception as error:  # noqa: BLE001
            self.space_status.setText(f"Could not start the state space tool: {error}")
            return
        self._space_job = job
        self._space_cancel_at = None
        self.busy = True
        self._set_space_button(running=True)
        self.space_status.setText("Starting…")
        if not hasattr(self, "space_timer"):
            self.space_timer = QTimer(self)
            self.space_timer.timeout.connect(self._space_tick)
        self.space_timer.start(200)

    def _set_space_button(self, running: bool) -> None:
        button_ = self.space_button
        button_.setText("■ Stop" if running else "Calculate")
        button_.setToolTip("Stop exploring and show what was found so far" if running else "")
        button_.setObjectName("" if running else "primary")
        button_.style().unpolish(button_)
        button_.style().polish(button_)
        button_.setEnabled(True)

    def shutdown(self) -> None:
        """The page is being closed: stop background work and timers."""
        self._stop_running()
        if self.busy and getattr(self, "_space_job", None) is not None:
            self._space_job.kill()
            self.busy = False
        if hasattr(self, "space_timer"):
            self.space_timer.stop()

    def _space_tick(self) -> None:
        job = self._space_job
        outcome = job.poll()
        if outcome is None:
            # A stop request normally takes effect within a second or two;
            # if the worker is stuck in one huge step, end it outright.
            if self._space_cancel_at is not None and \
                    time.monotonic() - self._space_cancel_at > 8:
                job.kill()
                outcome = ("error", "stopped")
            else:
                nodes, arcs, phase = job.progress()
                if self._space_cancel_at is not None and phase == "Exploring":
                    phase = "Stopping"
                self.space_status.setText(
                    f"{phase}… {nodes:,} nodes, {arcs:,} arcs so far. It runs separately, "
                    "so you can keep working; Stop keeps what was found.")
                return
        self.space_timer.stop()
        self.busy = False
        self._set_space_button(running=False)
        kind, payload = outcome
        if kind == "done":
            self._show_state_space(payload)
        elif payload == "stopped":
            self.space_status.setText("Stopped.")
        else:
            self.space_status.setText(f"State space failed: {payload}")

    def _show_state_space(self, results: dict) -> None:
        nodes_found = results["nodes"]
        if results["cancelled"]:
            text = (f"Stopped by you after {nodes_found:,} nodes: the properties below "
                    "describe only the explored part.")
        elif results["partial"]:
            text = (f"Partial: the node limit ({nodes_found:,}) was reached, so the "
                    "properties below describe only the explored part. Raise the limit to "
                    "explore further.")
        else:
            text = "Full state space: the properties below are proofs about every reachable " \
                   "marking."
        self.space_status.setText(text)
        card = self.space_results
        card.clear()
        card.setVisible(True)
        dead = results["dead"]
        home = results["home"]
        partial = results["partial"]
        card.add(hbox(StatTile("Nodes", f"{nodes_found:,}",
                               f"{results['unexplored']:,} not yet explored" if partial
                               else "full"),
                      StatTile("Arcs", f"{results['arcs']:,}"),
                      StatTile("SCCs", f"{results['sccs']:,}")))

        def listed(nodes: list[int]) -> str:
            shown = ", ".join(str(n) for n in nodes[:12])
            return shown + (f" … ({len(nodes):,} in total)" if len(nodes) > 12 else "")

        # In a partial state space only some statements are meaningful: a
        # dead marking found is real, but "no home marking" or "transition
        # never occurs" may just mean exploration had not got there yet.
        where = " among the explored nodes" if partial else ""
        if dead:
            card.add(Verdict(f"{len(dead):,} dead marking(s){where}", "warning",
                             f"Nodes {listed(dead)}. A dead marking has no enabled binding; "
                             "for a model that should terminate these are its end states."))
        else:
            card.add(Verdict(f"No dead markings{where}", "good" if not partial else "unknown",
                             "Something can always happen." if not partial else
                             "Unexplored nodes are not counted: they may still hide one."))
        if home is None:
            card.add(Verdict("Home markings: not determined", "unknown",
                             "Needs the full state space."))
        elif not home:
            card.add(Verdict("No home marking", "warning",
                             "No marking can be reached from every other marking."))
        elif len(home) == nodes_found:
            card.add(Verdict("Every marking is a home marking", "good",
                             "The initial marking can always be reached again."))
        else:
            card.add(Verdict(f"{len(home):,} home marking(s)", "good", f"Nodes {listed(home)}."))
        dead_transitions = results["dead_transitions"]
        names = ", ".join(dead_transitions)
        if partial:
            card.add(Verdict("Transitions not seen yet" if dead_transitions
                             else "Every transition occurred", "unknown" if dead_transitions
                             else "good",
                             f"{names} did not occur in the explored part (not proof that "
                             "they are dead)." if dead_transitions else
                             "So none of them is dead."))
        else:
            card.add(Verdict("Dead transitions" if dead_transitions else "No dead transitions",
                             "critical" if dead_transitions else "good",
                             names if dead_transitions
                             else "Every transition occurs somewhere in the state space."))
        live = results["live"]
        if live is None:
            card.add(Verdict("Live transitions: not determined", "unknown",
                             "Needs the full state space."))
        else:
            card.add(Verdict(f"{len(live)} live transition(s)", "good" if live else "unknown",
                             ", ".join(live) if live else
                             "No transition can always fire again (usual for a model that "
                             "ends)."))

        card.add(label("BOUNDS", "sectionLabel"))
        bounds = results["bounds"]
        multiset = results["multiset"]
        table = QTableWidget(len(bounds), 3)
        table.setHorizontalHeaderLabels(["Place", "Lower", "Upper"])
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        for row, (place_id, place_name, lower, upper) in enumerate(bounds):
            name = QTableWidgetItem(place_name)
            tip = multiset.get(place_id, "")
            name.setToolTip(f"Upper multiset bound:\n"
                            f"{tip if len(tip) < 1500 else tip[:1500] + ' …'}")
            table.setItem(row, 0, name)
            for column, value in ((1, lower), (2, upper)):
                cell = QTableWidgetItem(f"{value:,}")
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, column, cell)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.setMinimumHeight(min(360, 34 + 28 * len(bounds)))
        card.add(table)

        if results["graph"] is not None:
            card.add(label("GRAPH", "sectionLabel"))
            view = GraphView()
            view.setMinimumHeight(320)
            nodes, edges = state_space_specs(*results["graph"])
            view.graph.populate(nodes, edges, layer_gap=70)
            view.fit()
            card.add(view)

        card.add(label("REPORT", "sectionLabel"))
        report = QPlainTextEdit(results["report"])
        report.setReadOnly(True)
        report.setFont(theme.mono_font(11))
        report.setLineWrapMode(QPlainTextEdit.NoWrap)
        report.setMinimumHeight(260)
        card.add(report)
        self.status.emit(f"State space: {nodes_found:,} nodes, {results['arcs']:,} arcs")

    # =====================================================================
    # Files
    # =====================================================================
    def save(self) -> bool:
        if not self.document.path:
            return self.export()
        return self._write(Path(self.document.path))

    def export(self) -> bool:
        suggested = self.document.path or suggested_path(f"{self.net.name}.cpn")
        path, _ = QFileDialog.getSaveFileName(self, "Save CPN model", suggested,
                                              "CPN Tools model (*.cpn)")
        if not path:
            return False
        if not path.lower().endswith(".cpn"):
            path += ".cpn"
        self._take_file_name(Path(path))
        return self._write(Path(path))

    def _take_file_name(self, path: Path) -> None:
        """Save As names the net after its new file ("Untitled 1" → "order").

        Opening a .cpn or .pnml file names the net after the file too, so the
        name you see stays the same when the file is opened again later.  The
        name is set before writing, so the file itself carries it as well.
        """
        if self.net.name != path.stem:
            self.net.name = path.stem
            self.refresh_title()

    def _write(self, path: Path, quiet: bool = False) -> bool:
        """Save to ``path``.  ``quiet``: an autosave (no status message)."""
        try:
            atomic_write(path, lambda temporary: write_cpn(self.net, temporary))
        except Exception as error:  # noqa: BLE001
            return self._save_failed(error, quiet)
        self._written(path, quiet)
        return True

    def _save_failed(self, error: Exception, quiet: bool) -> bool:
        """Report a failed save: a dialog, or (an autosave) just remember why --
        the window says so once, rather than a dialog after every edit."""
        self.save_error = str(error)
        if not quiet:
            QMessageBox.critical(self, "Could not save", str(error))
        return False

    def _written(self, path: Path, quiet: bool) -> None:
        self.document.path = str(path)
        self.document.missing = False
        self.set_dirty(False)
        if not quiet:
            self.status.emit(f"Saved {path.name}")
        self.saved.emit()

    def autosave(self) -> bool:
        """Save to the document's file without a word (the window decides when)."""
        if not self.document.path or not self.document.dirty:
            return True
        return self._write(Path(self.document.path), quiet=True)

    def _export_image(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export image",
                                              suggested_path(f"{self.net.name}.png"),
                                              "PNG (*.png);;SVG (*.svg)")
        if not path:
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage
        selected = self.scene.selectedItems()
        self._plain_drawing(True)
        try:
            rect = self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
            if path.lower().endswith(".svg"):
                from PySide6.QtSvg import QSvgGenerator
                generator = QSvgGenerator()
                generator.setFileName(path)
                generator.setSize(rect.size().toSize())
                generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
                painter = QPainter(generator)
                self.scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
                painter.end()
            else:
                image = QImage(int(rect.width() * 2), int(rect.height() * 2), QImage.Format_ARGB32)
                image.fill(theme.palette().canvas)
                painter = QPainter(image)
                painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
                self.scene.render(painter, QRectF(image.rect()), rect)
                painter.end()
                image.save(path)
        finally:
            self._plain_drawing(False)
            for item in selected:
                item.setSelected(True)
        self.status.emit(f"Exported {Path(path).name}")

    def _plain_drawing(self, on: bool) -> None:
        """Strip the canvas to the net itself for an export (``on``), or put the
        working view back (``off``).

        An exported picture is the model, not the session: no selection ring,
        no green for the transitions enabled now, no halo on the one that
        fired last, no trace badges, no arrow for drawing arcs, no hover
        handles on arcs.  The tokens stay: the marking is part of the model.
        """
        scene = self.scene
        if on:
            scene.clearSelection()
            scene.hide_connect_handle()
            scene.update_enabling({})
            scene.set_trace(None)
            for item in scene.transition_items.values():
                item.set_halo(False)
            for item in scene.arc_items.values():
                if item.hovered:
                    item.hovered = False
                    item.update()
        else:
            self.refresh_simulation(light=True)         # enabling, halo and trace as they were


# ---------------------------------------------------------------------------
# Helpers that do not need the page
# ---------------------------------------------------------------------------
def copy_pages(pages: list) -> list:
    """Deep copy of a model's pages, for undo.

    The parsed inscriptions (rebuilt by every compile) and the XML elements
    kept from the source file (never modified) are shared, not copied, which
    keeps a snapshot cheap.
    """
    import copy
    memo: dict[int, object] = {}
    for page in pages:
        if page.source_element is not None:
            memo[id(page.source_element)] = page.source_element
        for element in (*page.places, *page.transitions, *page.arcs):
            for name in ("source_element", "initial_marking_ast", "guard_ast", "time_ast",
                         "expression_ast"):
                value = getattr(element, name, None)
                if value is not None:
                    memo[id(value)] = value
    return copy.deepcopy(pages, memo)


def state_space_specs(nodes: list, arcs: list) -> tuple[list[NodeSpec], list[EdgeSpec]]:
    """Graph drawing for a small state space.

    ``nodes`` holds ``(index, description, is_dead)`` and ``arcs`` holds
    ``(source, target, binding description)``, as produced by
    :func:`openprocess.analysis.state_space_process.summarise`.
    """
    t = style.tokens()
    specs = [NodeSpec(str(index), "state", text=str(index), tooltip=description,
                      emphasis=index == 0, fill=t.accent_soft if index == 0 else None,
                      stroke=style.STATUS["critical"] if dead else None)
             for index, description, dead in nodes]
    labels: dict[tuple[int, int], list[str]] = {}
    for source, target, binding in arcs:
        labels.setdefault((source, target), []).append(binding)
    edges = [EdgeSpec(str(s), str(d), width=1.2,
                      text=names[0].split(" <")[0] + (f" +{len(names) - 1}" if len(names) > 1
                                                      else ""),
                      tooltip="\n".join(names))
             for (s, d), names in labels.items()]
    return specs, edges


from ...sim.export import simulation_to_log  # noqa: E402 - the export lives with the engine now
