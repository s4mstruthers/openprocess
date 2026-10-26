"""Connecting an assistant: one click in the app, or ``openprocess mcp install <client>``.

Each client keeps a JSON file (or, for Claude Code, a command) naming the
MCP servers it starts.  This module writes OpenProcess into it -- merging,
never replacing what is there -- and gives the configuration for clients it
does not know.  The server command is this install's own: the standalone
app started with ``--mcp`` (the app downloaded from the releases, which has
no ``openprocess`` command), or the ``openprocess-mcp`` script of this
Python.  *Help ▸ Connect an AI Assistant…* in the app calls the same
functions, so nobody needs a terminal.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "openprocess"

#: The flag that starts the standalone app as the MCP server (no window).
MCP_FLAG = "--mcp"

#: Clients that can be connected in one step, and how to call them.
CLIENTS = {"claude-desktop": "Claude Desktop", "claude-code": "Claude Code", "cursor": "Cursor",
           "windsurf": "Windsurf"}


def server_command() -> list[str]:
    """How to start the server from this install: the console script when it
    is beside this Python, else ``python -m openprocess.mcp``; the app itself
    when running as the standalone app."""
    if getattr(sys, "frozen", False):
        return [sys.executable, MCP_FLAG]
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


def claude_cli() -> str | None:
    """Claude Code's ``claude`` command.  An app started from the Dock or the
    Start menu does not have the user's shell PATH, so the usual places are
    looked at too."""
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [home / ".claude" / "local" / "claude", home / ".local" / "bin" / "claude",
                  Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"),
                  home / ".npm-global" / "bin" / "claude", home / ".bun" / "bin" / "claude"]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        candidates += [appdata / "npm" / "claude.cmd", home / ".local" / "bin" / "claude.exe"]
    return next((str(c) for c in candidates if c.is_file()), None)


def claude_code_command() -> str:
    """The command that adds OpenProcess to Claude Code, to run by hand."""
    command = server_command()
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
    return f"claude mcp add --scope user {NAME} -- {quoted}"


def server_available() -> tuple[bool, str]:
    """Can this install run the server?  (False and why when the MCP SDK is missing.)"""
    try:
        import mcp.server.mcpserver  # noqa: F401
    except ImportError:
        if getattr(sys, "frozen", False):
            return False, "This copy of the app was built without the connector."
        return False, 'The connector needs the MCP Python SDK: pip install "openprocess[mcp]"'
    return True, ""


def check_server(timeout: float = 60.0) -> dict:
    """Start the server as an assistant would and do the MCP handshake:
    ``{"ok": True, "name", "version", "tools"}``, or ``{"ok": False, "error"}``.

    The same check the release build runs on every system, so a connector
    that cannot start is noticed before an assistant tries it."""
    return handshake(server_command(), timeout)


def handshake(command: list[str], timeout: float = 60.0, environment: dict | None = None) -> dict:
    """Start ``command`` as an MCP server on pipes and talk to it as a client
    does: ``initialize``, wait for the answer, ``notifications/initialized``,
    ``tools/list``, wait for the answer, then close its input.  (Writing all of
    it at once and closing the input lets a server see the end of its input
    and stop before it answers, on a busy machine.)"""
    import json as _json
    import threading
    import time
    environment = dict(environment or os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment)
    except OSError as error:
        return {"ok": False, "error": f"could not start {command[0]}: {error}"}
    answers: dict = {}
    errors: list[str] = []
    arrived = threading.Condition()

    def read_answers() -> None:
        for raw in process.stdout:
            try:
                message = _json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(message, dict) and "id" in message:
                with arrived:
                    answers[message["id"]] = message
                    arrived.notify_all()
        with arrived:
            answers["closed"] = True
            arrived.notify_all()

    def read_errors() -> None:
        for raw in process.stderr:
            errors.append(raw.decode("utf-8", "replace"))
            del errors[:-20]
    for target in (read_answers, read_errors):
        threading.Thread(target=target, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(message: dict) -> None:
        try:
            process.stdin.write((_json.dumps(message) + "\n").encode("utf-8"))
            process.stdin.flush()
        except OSError:
            pass

    def wait_for(key) -> None:
        with arrived:
            arrived.wait_for(lambda: key in answers or "closed" in answers,
                             timeout=max(0.0, deadline - time.monotonic()))
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "openprocess-check", "version": "1"}}})
    wait_for(1)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    wait_for(2)
    try:
        process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    info = (answers.get(1, {}).get("result") or {}).get("serverInfo") or {}
    tools = (answers.get(2, {}).get("result") or {}).get("tools")
    if not info or tools is None:
        tail = "".join(errors).strip().splitlines()[-6:]
        return {"ok": False, "error": "the server did not answer the handshake"
                + (": " + " / ".join(tail) if tail else "")}
    return {"ok": True, "name": info.get("name"), "version": info.get("version"), "tools": len(tools)}


def install(client: str) -> str:
    """Write the configuration for ``client``; returns what was done, in words."""
    client = client.lower().replace("_", "-").replace(" ", "-")
    if client in ("print", "json", "snippet"):
        return snippet()
    if client == "claude-code":
        command = server_command()
        claude = claude_cli()
        if claude is None:
            return ("Claude Code's command-line tool (claude) was not found. In a terminal where it is, run:\n"
                    f"  {claude_code_command()}")
        # Replace an earlier entry (an older copy of the app, say) rather than fail on the name.
        subprocess.run([claude, "mcp", "remove", "--scope", "user", NAME], capture_output=True, text=True)
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


def status(include_claude_code: bool = True) -> dict:
    """Which known clients have OpenProcess configured: ``configured``, and
    ``current`` when the entry starts this install (not an older copy of the
    app, or another Python)."""
    out = {}
    wanted = server_entry()
    for client in ("claude-desktop", "cursor", "windsurf"):
        path = config_path(client)
        entry = None
        if path is not None and path.is_file():
            try:
                entry = (json.loads(path.read_text(encoding="utf-8") or "{}").get("mcpServers") or {}).get(NAME)
            except ValueError:
                entry = None
        installed = client != "claude-desktop" or path is None or path.parent.is_dir()
        out[client] = {"config": str(path) if path else None, "configured": entry is not None,
                       "current": entry is not None and entry.get("command") == wanted["command"]
                       and list(entry.get("args") or []) == wanted["args"], "installed": installed}
    claude = claude_cli() if include_claude_code else None
    if claude:
        try:
            done = subprocess.run([claude, "mcp", "get", NAME], capture_output=True, text=True, timeout=20)
            text = (done.stdout or "") + (done.stderr or "")
            configured = done.returncode == 0
            out["claude-code"] = {"configured": configured, "current": configured and server_command()[0] in text,
                                  "config": "claude mcp get openprocess", "installed": True}
        except (OSError, subprocess.TimeoutExpired):
            out["claude-code"] = {"configured": False, "current": False, "config": None, "installed": True}
    else:
        out["claude-code"] = {"configured": False, "current": False, "config": None,
                              "installed": claude is not None}
    return out


__all__ = ["CLIENTS", "MCP_FLAG", "NAME", "check_server", "claude_cli", "claude_code_command", "config_path",
           "handshake", "install", "server_available", "server_command", "server_entry", "snippet", "status"]
