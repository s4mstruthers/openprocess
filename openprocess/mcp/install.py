"""Connecting an assistant in one command: ``openprocess mcp install <client>``.

Each client keeps a JSON file (or, for Claude Code, a command) naming the
MCP servers it starts.  This module writes OpenProcess into it -- merging,
never replacing what is there -- and prints the configuration for clients
it does not know.  The server command is the ``openprocess-mcp`` script of
this Python (or the standalone app with ``--mcp``), so the assistant uses
the same OpenProcess as the user.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "openprocess"


def server_command() -> list[str]:
    """How to start the server from this install: the console script when it
    is beside this Python, else ``python -m openprocess.mcp``; the app itself
    when running as the standalone app."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp"]
    script = Path(sys.executable).parent / ("openprocess-mcp.exe" if sys.platform == "win32" else "openprocess-mcp")
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "openprocess.mcp"]


def server_entry() -> dict:
    command = server_command()
    return {"command": command[0], "args": command[1:]}


def config_path(client: str) -> Path | None:
    """Where the client keeps its MCP servers (None for clients set up by command)."""
    home = Path.home()
    if client == "claude-desktop":
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
        return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    return None


def snippet() -> str:
    """The JSON most clients take, to paste by hand."""
    return json.dumps({"mcpServers": {NAME: server_entry()}}, indent=2)


def _merge(path: Path) -> str:
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as error:
            raise ValueError(f"{path} is not valid JSON ({error}); fix it or add the server by hand:\n{snippet()}") \
                from None
        backup = path.with_suffix(path.suffix + ".before-openprocess")
        shutil.copy2(path, backup)
    servers = data.setdefault("mcpServers", {})
    servers[NAME] = server_entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(path)


def install(client: str) -> str:
    """Write the configuration for ``client``; returns what was done, in words."""
    client = client.lower().replace("_", "-").replace(" ", "-")
    if client in ("print", "json", "snippet"):
        return snippet()
    if client == "claude-code":
        command = server_command()
        claude = shutil.which("claude")
        if claude is None:
            return ("Claude Code's command-line tool (claude) was not found. In a terminal where it is, run:\n"
                    f"  claude mcp add --scope user {NAME} -- {' '.join(command)}")
        done = subprocess.run([claude, "mcp", "add", "--scope", "user", NAME, "--", *command],
                              capture_output=True, text=True)
        if done.returncode != 0:
            return f"claude mcp add failed:\n{done.stdout}{done.stderr}"
        return f"Added to Claude Code (user scope): {done.stdout.strip() or NAME}\nStart a new Claude Code session."
    path = config_path(client)
    if path is None:
        return (f"I do not know where {client} keeps its MCP servers. Most clients take this JSON "
                f"(a \"mcpServers\" entry):\n{snippet()}")
    written = _merge(path)
    restart = {"claude-desktop": "Quit and reopen Claude Desktop; OpenProcess then appears under its tools "
                                 "(the hammer / connectors icon).",
               "cursor": "Reload Cursor (or toggle the server in Settings ▸ MCP).",
               "windsurf": "Reload Windsurf."}.get(client, "Restart the client.")
    return f"Wrote {written}\n{restart}"


def status() -> dict:
    """Which known clients have OpenProcess configured."""
    out = {}
    for client in ("claude-desktop", "cursor", "windsurf"):
        path = config_path(client)
        configured = False
        if path is not None and path.is_file():
            try:
                configured = NAME in (json.loads(path.read_text(encoding="utf-8") or "{}").get("mcpServers") or {})
            except ValueError:
                configured = False
        out[client] = {"config": str(path) if path else None, "configured": configured}
    claude = shutil.which("claude")
    if claude:
        done = subprocess.run([claude, "mcp", "list"], capture_output=True, text=True)
        out["claude-code"] = {"configured": NAME in (done.stdout or ""), "config": "claude mcp list"}
    else:
        out["claude-code"] = {"configured": False, "config": None}
    return out


__all__ = ["NAME", "config_path", "install", "server_command", "server_entry", "snippet", "status"]
