"""The *Connections* view: what flows into OpenProcess, what flows out, and
the tools that plug in, drawn as a hub.

Three bands.  **In**: the file formats and sources the input boxes read.
**OpenProcess**: the boxes, by group, and the folder's own.  **Out**: what the
output boxes and the exports hand to other tools.  Beside them, **Tools**:
the optional packages boxes need (``needs="pandas"``), each with whether it
is installed, how to install it, and the boxes it brings.  Nothing here is
declared twice: the cards are read from the box library.
"""

from __future__ import annotations

import sys
from importlib.util import find_spec

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ...flow.library import Library
from .widgets import Card, Chip, PageHeader, button, flow, hbox, label

#: (title, what it is, the input boxes' function names)
INPUTS = [
    ("Event logs", "XES (also gzipped), CSV, or a .txt in the course notation", ("open_log",)),
    ("Object-centric logs", "OCEL 2.0 JSON (.jsonocel), or the older JSON-OCEL", ("open_ocel", "flatten_ocel")),
    ("Petri nets", "PNML files, from any tool", ("open_net",)),
    ("Coloured nets", "CPN Tools .cpn files", ("open_cpn",)),
    ("Transition systems", "ts.txt, typed as s0 -a-> s1", ("open_transition_system",)),
    ("Typed logs", "the course notation, [<a,b,c>^3, <a,c>]", ("typed_log",)),
    ("Public datasets", "the BPI Challenge logs, from the cache folder", ("open_dataset",)),
    ("Simulations", "a model played out, as a log", ("simulate_log",)),
]
#: (title, what it is, the output boxes' function names)
OUTPUTS = [
    ("PNML", "Petri nets, for ProM, PM4Py and others", ("save_pnml",)),
    ("Event logs", "XES, for any mining tool", ("save_log",)),
    ("Tables", "CSV, for pandas, R or a spreadsheet", ("save_table",)),
    ("Figures", "SVG or PNG, for a paper", ("save_figure",)),
    ("Experiment", "a zip of everything, from ⋯ ▸ Export experiment", ()),
    ("Python", "every box is a function: call it from a script or a notebook", ()),
]
#: What pip installs for each tool (the package itself, pinned as the app's extras pin it).
PACKAGES = {"pandas": "pandas>=2.0", "numpy": "numpy>=1.24", "scipy": "scipy>=1.10",
            "matplotlib": "matplotlib>=3.7", "pm4py": "pm4py"}


def package_for(tool: str) -> str:
    return PACKAGES.get(tool, tool)


def can_install() -> bool:
    """Whether Install… can work here (see :mod:`openprocess.packages`: from a
    Python install pip runs in that Python; the downloaded app carries pip and
    installs into a folder of the user's own)."""
    from ... import packages
    return packages.can_install()


def installable(tool: str) -> bool:
    """Whether Install… works for this tool here (pip, and a package this build can install)."""
    from ... import packages
    return can_install() and packages.installable(package_for(tool))


def _by_function(library: Library) -> dict[str, object]:
    return {spec.id.rsplit(".", 1)[-1]: spec for spec in library.specs.values()}


def tools(library: Library) -> list[dict]:
    """The optional packages boxes need: name, installed?, how to install, the boxes."""
    found: dict[str, list[str]] = {}
    for spec in library.specs.values():
        for need in getattr(spec, "needs", ()) or ():
            found.setdefault(need, []).append(spec.name)
    return [{"name": name, "installed": find_spec(name.split(".")[0]) is not None,
             "install": f"pip install {package_for(name)}", "boxes": sorted(set(boxes))}
            for name, boxes in sorted(found.items())]


class ConnectionsPage(QWidget):
    """The hub picture, rebuilt by :meth:`refresh` from the window's library."""

    #: *Install…* on a tool's card: the tool's name (the window runs pip).
    install_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.installing: set[str] = set()
        self.install_buttons: dict[str, object] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = PageHeader("Connections", "What flows in, what flows out, and the tools that plug in")
        root.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName("plain")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 4, 20, 24)
        self.body_layout.setSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.body)
        root.addWidget(scroll, 1)
        self.cards: dict[str, Card] = {}

    def refresh(self, library: Library, folder=None) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = {}
        functions = _by_function(library)

        def band(caption: str, text: str, cards: list[Card]) -> None:
            self.body_layout.addWidget(label(caption, "sectionLabel"))
            if text:
                self.body_layout.addWidget(label(text, "muted", wrap=True))
            self.body_layout.addWidget(flow(*cards, spacing=10))

        def card(title: str, text: str, boxes: list[str], status: str = "", tone: str = "good") -> Card:
            item = Card(title, text)
            item.setMinimumWidth(200)
            item.setMaximumWidth(300)
            if status:
                item.header.addWidget(Chip(status, fill=_tone(tone), ink="white"))
            if boxes:
                item.add(label("Boxes: " + ", ".join(boxes), "muted", wrap=True))
            self.cards[title] = item
            return item

        inputs = []
        for title, text, names in INPUTS:
            specs = [functions[n] for n in names if n in functions]
            inputs.append(card(title, text, [s.name for s in specs],
                               "" if specs else "no box", "good" if specs else "muted"))
        band("IN", "What the input boxes read. Drop any of these on the canvas, or pick them in + Add box.",
             inputs)

        groups = library.by_group()
        custom = [s for s in library.specs.values() if getattr(s, "custom", False)]
        middle = card("OpenProcess", f"{len(library)} boxes in {len(groups)} groups: "
                      + ", ".join(groups) + ".", [])
        own = card("Your own boxes", "A Python function in the folder's boxes/ subfolder is a box: "
                   "type hints make its inputs and settings, its docstring its help.",
                   [s.name for s in custom][:12],
                   f"{len(custom)} in this folder" if custom else ("none yet" if folder else "open a folder"),
                   "good" if custom else "muted")
        band("OPENPROCESS", "", [middle, own])

        tool_cards = []
        self.install_buttons = {}
        for tool in tools(library):
            name = tool["name"]
            if tool["installed"]:
                text, status, tone = "Installed.", "ready", "good"
            elif name in self.installing:
                text, status, tone = "Installing… (a minute or two; the app stays usable)", "installing", "muted"
            elif installable(name):
                from ... import packages
                text, status, tone = (f"Not installed. Install… puts it {packages.where_text()}; the boxes "
                                      "that need it come alive without a restart."), "not installed", "warning"
            elif can_install():
                text, status, tone = (f"Not installed. The downloaded app installs only packages that come "
                                      f"ready-built, and this one needs building: in a Python install, run "
                                      f"{tool['install']}."), "not installed", "warning"
            else:
                text, status, tone = (f"Not installed, and this build has no pip: in a Python install, run "
                                      f"{tool['install']}."), "not installed", "warning"
            item = card(name, text, tool["boxes"], status, tone)
            if not tool["installed"] and installable(name) and name not in self.installing:
                install = button("Install…", lambda _checked=False, n=name: self.install_requested.emit(n),
                                 kind="primary", tooltip=f"Runs {tool['install']} in the background")
                self.install_buttons[name] = install
                item.add(hbox(install, None))
            tool_cards.append(item)
        band("TOOLS", "Optional packages that boxes need. A box whose package is missing is listed "
             "greyed out until it is installed.", tool_cards or [card("None needed", "Every box runs "
                                                                       "with what is installed.", [])])

        outputs = []
        for title, text, names in OUTPUTS:
            specs = [functions[n] for n in names if n in functions]
            outputs.append(card(title, text, [s.name for s in specs]))
        band("OUT", "What leaves OpenProcess for other tools.", outputs)
        self.body_layout.addStretch(1)


def _tone(tone: str) -> str:
    from . import style
    return {"good": style.STATUS["good"], "warning": style.STATUS["warning"],
            "muted": style.tokens().text_muted}.get(tone, style.tokens().text_muted)


__all__ = ["ConnectionsPage", "tools", "can_install", "package_for", "INPUTS", "OUTPUTS"]
