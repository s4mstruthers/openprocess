"""*Connect an AI Assistant…*: OpenProcess as a connector for Claude and others, in one click.

The downloaded app has no ``openprocess`` command, so connecting cannot ask
for a terminal.  This dialog does what ``openprocess mcp install`` does: it
writes the assistant's settings (Claude Desktop's, Cursor's…) to start this
very app with ``--mcp`` as its MCP server, keeping whatever else is there.
*Test the connector* starts the server the way an assistant will and does
the handshake, so a connector that cannot start says so here.

The work is :mod:`openprocess.mcp.install`; this is its face.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea, QVBoxLayout, QWidget

from ...mcp import install
from . import style
from .widgets import Card, Chip, button, hbox, label
from .workers import run_in_background

#: (client key, what to say once it is connected)
_AFTER = {
    "claude-desktop": "Quit Claude Desktop and open it again: OpenProcess is then listed under its connectors "
                      "(the tools menu in the message box).",
    "claude-code": "Start a new Claude Code session; /mcp lists OpenProcess.",
    "cursor": "Reload Cursor, or turn the server on in Settings ▸ MCP.",
    "windsurf": "Reload Windsurf.",
}


class ConnectDialog(QDialog):
    """One card per assistant: whether it has OpenProcess, and Connect."""

    def __init__(self, window=None, parent=None) -> None:
        super().__init__(parent or window)
        self.window_ = window
        self.setWindowTitle("Connect an AI Assistant")
        self.resize(620, 640)
        self.cards: dict[str, Card] = {}
        self.buttons: dict[str, object] = {}
        self.chips: dict[str, Chip] = {}
        self.notes: dict[str, object] = {}
        self.states: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body.setObjectName("plain")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        title = label("Connect an AI assistant", "pageTitle")
        layout.addWidget(title)
        layout.addWidget(label(
            "Let Claude, or another assistant that speaks MCP, use OpenProcess: summarise and filter logs, "
            "discover models and check their fit, analyse nets, draw diagrams, build analyses you can rerun, "
            "and write exercises for Learn. It is told to cite the work every algorithm follows, to keep "
            "results reproducible, and to show you what it made here. Every file it writes can be reviewed "
            "and undone (Help ▸ Review AI Changes…).", "muted", wrap=True))

        available, why = install.server_available()
        self.available = available
        if not available:
            warning = label(why, wrap=True, selectable=True)
            warning.setStyleSheet(f"color: {style.STATUS['warning']};")
            layout.addWidget(warning)

        for key, name in install.CLIENTS.items():
            card = Card(name)
            chip = Chip("checking…")
            chip.set_colours(style.tokens().text_muted, "white")
            card.header.addWidget(chip)
            note = label("", "muted", wrap=True, selectable=True)
            connect = button("Connect", lambda _checked=False, k=key: self.connect_client(k), kind="primary",
                             tooltip=f"Add OpenProcess to {name}'s MCP servers (everything else stays)")
            connect.setEnabled(False)
            card.header.addSpacing(6)
            card.header.addWidget(connect)
            note.hide()
            card.add(note)
            layout.addWidget(card)
            self.cards[key], self.chips[key], self.buttons[key], self.notes[key] = card, chip, connect, note

        other = Card("Another assistant")
        other.add(label("Most assistants that support MCP (VS Code with Copilot, Gemini CLI, Zed, …) take the "
                        "same settings: copy them and paste them into the assistant's MCP settings.",
                        "muted", wrap=True))
        self.copy_button = button("Copy settings", self.copy_settings,
                                  tooltip="The JSON most assistants take, with the command that starts this app")
        other.add(hbox(self.copy_button, None))
        layout.addWidget(other)

        self.test_result = label("", "muted", wrap=True, selectable=True)
        self.test_button = button("Test the connector", self.test_connector,
                                  tooltip="Start OpenProcess as an assistant will and check that it answers")
        self.test_button.setEnabled(available)
        guide = button("Read the guide", self._guide, kind="ghost")
        layout.addLayout(hbox(self.test_button, guide, None))
        layout.addWidget(self.test_result)
        command = " ".join(f'"{p}"' if " " in p else p for p in install.server_command())
        layout.addWidget(label(f"The assistant starts: {command}", "muted", wrap=True, selectable=True))
        layout.addStretch(1)
        self.refresh()

    # -- state ------------------------------------------------------------------------
    def refresh(self) -> None:
        """Read which assistants have OpenProcess (Claude Code's answer takes a moment)."""
        self.apply(install.status(include_claude_code=False), partial=True)
        run_in_background(install.status, lambda states: self.apply(states),
                          lambda message: None)

    def apply(self, states: dict, partial: bool = False) -> None:
        self.states = states
        for key, name in install.CLIENTS.items():
            if partial and key == "claude-code":
                continue
            state = states.get(key) or {}
            chip, note, connect = self.chips[key], self.notes[key], self.buttons[key]
            if state.get("current"):
                chip.setText("connected")
                chip.set_colours(style.STATUS["good"], "white")
                note.setText(_AFTER.get(key, ""))
                connect.setText("Connect again")
            elif state.get("configured"):
                chip.setText("another copy")
                chip.set_colours(style.STATUS["warning"], "white")
                note.setText(f"{name} starts OpenProcess from another place (an older copy of the app, or "
                             "another Python). Connect to point it at this app.")
                connect.setText("Connect")
            elif not state.get("installed", True):
                chip.setText("not found")
                chip.set_colours(style.tokens().text_muted, "white")
                note.setText(f"{name} was not found on this computer."
                             + (f" To add OpenProcess by hand: {install.claude_code_command()}"
                                if key == "claude-code" else ""))
                connect.setText("Connect")
            else:
                chip.setText("not connected")
                chip.set_colours(style.tokens().text_muted, "white")
                note.setText("")
                connect.setText("Connect")
            connect.setEnabled(self.available and (state.get("installed", True) or key != "claude-code"))
            note.setVisible(bool(note.text()))

    # -- actions ----------------------------------------------------------------------
    def connect_client(self, key: str) -> None:
        name = install.CLIENTS[key]
        self.buttons[key].setEnabled(False)
        self.notes[key].setText(f"Connecting {name}…")
        self.notes[key].show()

        def done(message: str) -> None:
            self.notes[key].setText(message.replace("\n", " "))
            self.notes[key].show()
            self.refresh()
            if self.window_ is not None and hasattr(self.window_, "statusBar"):
                self.window_.statusBar().showMessage(f"OpenProcess added to {name}", 8000)

        def failed(message: str) -> None:
            self.notes[key].setText(f"Could not connect {name}: {message}")
            self.buttons[key].setEnabled(True)
        run_in_background(lambda: install.install(key), done, failed)

    def copy_settings(self) -> None:
        QApplication.clipboard().setText(install.snippet())
        self.copy_button.setText("Copied ✓")

    def test_connector(self) -> None:
        self.test_button.setEnabled(False)
        self.test_result.setText("Starting OpenProcess as an assistant would…")

        def done(result: dict) -> None:
            self.test_button.setEnabled(True)
            if result.get("ok"):
                self.test_result.setText(f"It works: {result['name']} {result['version']} answers with "
                                         f"{result['tools']} tools.")
                self.test_result.setStyleSheet(f"color: {style.STATUS['good']};")
            else:
                self.test_result.setText(f"The connector did not start: {result.get('error')}")
                self.test_result.setStyleSheet(f"color: {style.STATUS['critical']};")

        def failed(message: str) -> None:
            self.test_button.setEnabled(True)
            self.test_result.setText(f"The test failed: {message}")
        run_in_background(install.check_server, done, failed)

    def _guide(self) -> None:
        from .definition_view import show_guide
        show_guide("mcp", self.window_ or self)


def show_connect(window) -> ConnectDialog:
    """Open (or raise) the window's Connect dialog."""
    dialog = getattr(window, "_connect_dialog", None)
    if dialog is None or not dialog.isVisible():
        dialog = ConnectDialog(window)
        window._connect_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


__all__ = ["ConnectDialog", "show_connect"]
