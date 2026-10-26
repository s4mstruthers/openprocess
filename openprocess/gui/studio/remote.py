"""The app's command port: a local socket other programs hand things to.

A notebook (``workflow.open_in_studio()``), the command line, or an AI
assistant through the MCP server asks the running app to open a file, to
switch to a folder, or to add a box; see :mod:`openprocess.flow.studio` for
the client and the protocol (one JSON object per line, one answer per line).

What the app will do on request, and nothing else:

* ``ping``: say its version and the open folder;
* ``open``: open files or folders, as *File ▸ Open…* would (an exercise
  folder opens in Learn; a folder with an ``.openprocess`` state or without a
  ``question.md`` opens as the workspace);
* ``folder``: open that folder as the workspace;
* ``publish_box``: write the given Python source as ``boxes/<name>.py`` in
  the open folder (journaled like every assistant's change), where the
  folder's box loader picks it up -- the source is written, never executed
  here; the box runs only when the user runs a workflow with it, under the
  same rule as any folder box;
* ``changed``: refresh the *AI changes* pill (the journal has new entries).

The socket is the user's own (their runtime folder, their pipe), so only
programs running as the user can reach it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ...flow import studio as _studio


class RemoteServer(QObject):
    """Listens while the window lives; one :class:`QLocalSocket` per request."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self._buffers: dict[int, bytes] = {}
        name = _studio.NAME if sys.platform == "win32" else _studio.socket_path()
        QLocalServer.removeServer(name)                  # a socket left by a crash
        self.listening = self.server.listen(name)
        if self.listening:
            self.server.newConnection.connect(self._accept)

    @property
    def address(self) -> str:
        return self.server.fullServerName() if self.listening else ""

    def close(self) -> None:
        if self.listening:
            self.server.close()
            self.listening = False

    # -- connections ------------------------------------------------------------------
    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                return
            key = id(socket)
            self._buffers[key] = b""
            socket.readyRead.connect(lambda s=socket, k=key: self._read(s, k))
            socket.disconnected.connect(lambda k=key: self._buffers.pop(k, None))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket, key)

    def _read(self, socket: QLocalSocket, key: int) -> None:
        self._buffers[key] = self._buffers.get(key, b"") + bytes(socket.readAll().data())
        if b"\n" not in self._buffers[key]:
            return
        line, _, rest = self._buffers[key].partition(b"\n")
        self._buffers[key] = rest
        try:
            message = json.loads(line.decode("utf-8"))
            answer = self.handle(message)
        except Exception as error:  # noqa: BLE001 - whatever the client sent: answer, never crash the app
            answer = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        socket.write((json.dumps(answer, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        socket.flush()
        socket.disconnectFromServer()

    # -- the requests -----------------------------------------------------------------
    def handle(self, message: dict) -> dict:
        window = self.window
        folder = str(window.workspace.folder) if getattr(window, "workspace", None) is not None else None
        if not isinstance(message, dict):
            return {"ok": False, "error": "a message is a JSON object"}
        if message.get("ping"):
            from ... import __version__
            return {"ok": True, "version": __version__, "folder": folder, "app": "OpenProcess Studio"}
        if "folder" in message and "open" not in message:
            return self._open_folder(message["folder"])
        if "open" in message:
            opened, problems = [], []
            wanted_folder = message.get("folder")
            if wanted_folder and (folder is None or Path(wanted_folder).resolve() != Path(folder).resolve()):
                answer = self._open_folder(wanted_folder)
                if not answer.get("ok"):
                    return answer
            for path in message["open"] or []:
                target = Path(path)
                if not target.exists():
                    problems.append(f"no such file: {path}")
                    continue
                if target.is_dir():
                    if (target / "question.md").exists() or any(target.glob("*/question.md")) \
                            or any(target.glob("*/*/question.md")):
                        window.open_exercise(target)
                    else:
                        answer = self._open_folder(str(target))
                        if not answer.get("ok"):
                            problems.append(answer.get("error", path))
                            continue
                else:
                    window.open_files([str(target)])
                opened.append(str(target))
            window.raise_()
            window.activateWindow()
            return {"ok": not problems, "opened": opened, "problems": problems}
        if "publish_box" in message:
            return self._publish_box(message["publish_box"])
        if "changed" in message:
            refresh = getattr(window, "refresh_review", None)
            if callable(refresh):
                refresh()
            return {"ok": True}
        return {"ok": False, "error": "unknown request: " + ", ".join(sorted(message))}

    def _open_folder(self, path: str) -> dict:
        target = Path(path)
        if not target.is_dir():
            return {"ok": False, "error": f"not a folder: {path}"}
        opener = getattr(self.window, "open_workspace", None)
        if not callable(opener):
            return {"ok": False, "error": "this window cannot open folders"}
        done = opener(str(target))
        return {"ok": done is not False, "folder": str(target)}

    def _publish_box(self, box: dict) -> dict:
        from ...flow import changes
        window = self.window
        if getattr(window, "workspace", None) is None:
            return {"ok": False, "error": "no folder is open in OpenProcess: open one first (File ▸ Open Folder…)"}
        name = str(box.get("name") or "").strip()
        source = box.get("source")
        if not name.isidentifier() or not isinstance(source, str) or not source.strip():
            return {"ok": False, "error": "publish_box needs a Python identifier as name and the source text"}
        if "@box" not in source:
            return {"ok": False, "error": "the source has no @box function"}
        target = Path(window.workspace.folder) / "boxes" / f"{name}.py"
        change = changes.record(target, source, "publish_box", f"Box {name} from a notebook or an assistant",
                                client=str(box.get("client") or "notebook"), folder=window.workspace.folder)
        window.statusBar().showMessage(f"Box {name} written to boxes/{name}.py; it is listed under Yours", 8000)
        refresh = getattr(window, "refresh_review", None)
        if callable(refresh):
            refresh()
        return {"ok": True, "file": str(target), "change": change.id}


__all__ = ["RemoteServer"]
