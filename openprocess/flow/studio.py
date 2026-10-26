"""Talking to a running OpenProcess Studio from Python: open a file, publish a box.

The app listens on a local socket while it runs (``gui/studio/remote.py``):
a Unix socket in the user's runtime folder on macOS and Linux, a named pipe
on Windows.  This module is the client side, with no Qt in it, so a
notebook, a script or the MCP server can hand things to the app::

    from openprocess.flow.studio import open_in_studio, publish_box

    open_in_studio("Week 3/analysis.cpnflow")      # the app opens it (and starts if it is not running)
    publish_box(my_box)                            # a @box defined in the notebook appears under + Add box

A :class:`~.workflow.Workflow` has ``open_in_studio()`` and a
:class:`~.box.Box` has ``publish()`` that call these.  The protocol is one
JSON object per line, answered by one JSON object per line::

    {"open": ["/path/a.cpnflow"], "folder": "/path"}     -> {"ok": true, "opened": [...]}
    {"publish_box": {"name": "last_two", "source": "..."}} -> {"ok": true, "file": ".../boxes/last_two.py"}
    {"ping": true}                                       -> {"ok": true, "version": "0.13.0", "folder": "..."}
    {"changed": ["/path/net.pnml"]}                      -> {"ok": true}   (the journal has new entries)

The app only ever opens files and writes box files into the open folder's
``boxes/``; it never runs anything it is sent.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: The socket's name; the app and this module agree on it.
NAME = "openprocess-studio"


def socket_path() -> str:
    """Where the app listens: a named pipe on Windows, a Unix socket elsewhere
    (in ``$XDG_RUNTIME_DIR`` or the temp folder, with the user's id, as Qt's
    ``QLocalServer`` names it)."""
    if sys.platform == "win32":
        return rf"\\.\pipe\{NAME}"
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return str(Path(base) / f"{NAME}-{os.getuid()}")


def send(message: dict, timeout: float = 5.0) -> dict:
    """Send one message to the running app and return its answer.

    Raises ``ConnectionError`` when no app is listening."""
    data = (json.dumps(message, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    if sys.platform == "win32":
        try:
            with open(socket_path(), "r+b", buffering=0) as pipe:
                pipe.write(data)
                line = b""
                while not line.endswith(b"\n"):
                    chunk = pipe.read(1)
                    if not chunk:
                        break
                    line += chunk
        except OSError as error:
            raise ConnectionError("OpenProcess Studio is not running") from error
    else:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(socket_path())
                client.sendall(data)
                line = b""
                while not line.endswith(b"\n"):
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    line += chunk
        except OSError as error:
            raise ConnectionError("OpenProcess Studio is not running") from error
    if not line.strip():
        raise ConnectionError("OpenProcess Studio did not answer")
    return json.loads(line.decode("utf-8"))


def is_running() -> dict | None:
    """The running app's ``{"version", "folder"}``, or None."""
    try:
        answer = send({"ping": True}, timeout=1.5)
    except (ConnectionError, ValueError):
        return None
    return answer if answer.get("ok") else None


def launch(paths: list[str] | None = None, wait: float = 20.0) -> dict | None:
    """Start OpenProcess Studio (with ``paths`` to open) and wait until it listens."""
    command = [sys.executable, "-m", "openprocess.gui.studio.app", *(paths or [])]
    if getattr(sys, "frozen", False):                   # the standalone app: its own executable
        command = [sys.executable, *(paths or [])]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    end = time.time() + wait
    while time.time() < end:
        found = is_running()
        if found:
            return found
        time.sleep(0.3)
    return None


def open_in_studio(*paths: str | Path, folder: str | Path | None = None, start: bool = True) -> dict:
    """Have the running app open the files (a workflow, a net, a log, an
    exercise folder…); start the app when ``start`` and it is not running.

    Returns the app's answer: ``{"ok": True, "opened": [...]}``."""
    files = [str(Path(p).resolve()) for p in paths]
    message: dict[str, Any] = {"open": files}
    if folder is not None:
        message["folder"] = str(Path(folder).resolve())
    try:
        return send(message)
    except ConnectionError:
        if not start:
            raise
        if launch(files) is None:
            raise ConnectionError("OpenProcess Studio did not start") from None
        # The files were given on the command line; still tell the app the folder.
        if folder is not None:
            try:
                return send({"folder": message["folder"]})
            except ConnectionError:
                pass
        return {"ok": True, "opened": files, "started": True}


def publish_box(box, name: str | None = None) -> dict:
    """Make a ``@box`` function defined in a notebook or a script a box of the
    open folder: its source is written to the folder's ``boxes/<name>.py`` by
    the app, which lists it under *Yours* and records its hash with every
    analysis that uses it (so the analysis stays reproducible)."""
    import inspect
    function = getattr(box, "function", box)
    source = inspect.getsource(function)
    try:                                             # the imports the function's module has
        module = inspect.getmodule(function)
        header = ""
        if module is not None and module.__name__ != "__main__" and getattr(module, "__file__", None):
            text = Path(module.__file__).read_text(encoding="utf-8")
            header = "\n".join(line for line in text.splitlines()
                               if line.startswith(("import ", "from ")) and "openprocess" in line) + "\n\n"
        if not header.strip():
            header = "from openprocess.flow import *  # noqa: F401,F403 - the types and @box\n\n"
    except OSError:
        header = "from openprocess.flow import *  # noqa: F401,F403\n\n"
    return send({"publish_box": {"name": name or function.__name__, "source": header + source}})


def notify_changes(paths: list[str | Path]) -> None:
    """Tell the app that files changed (the journal has new entries); quiet when it is not running."""
    try:
        send({"changed": [str(Path(p).resolve()) for p in paths]}, timeout=1.5)
    except (ConnectionError, ValueError):
        pass


__all__ = ["NAME", "is_running", "launch", "notify_changes", "open_in_studio", "publish_box", "send",
           "socket_path"]
