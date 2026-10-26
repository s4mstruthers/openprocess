"""Entry point of the standalone OpenProcess Studio app (see ``packaging/openprocess.spec``).

PyInstaller freezes this script into the app's program.  The same program is
started in three ways:

``OpenProcess [files…]``
    The normal case: open OpenProcess Studio, optionally with files.
``OpenProcess --openprocess-state-space-worker MODEL MAX_NODES OUT``
    Started by the app itself to compute a state space in a separate process
    (see :mod:`openprocess.analysis.state_space_process`).  A frozen app has no
    separate Python to run, so it runs itself with this flag.  No window opens.
``OpenProcess --mcp``
    Started by an AI assistant (Claude Desktop, Claude Code, Cursor…) as
    OpenProcess's MCP server, on stdin and stdout; no window opens.  *Help ▸
    Connect an AI Assistant…* writes the assistant's settings to start it
    this way, so the downloaded app needs no ``openprocess`` command.
``OpenProcess --openprocess-self-test [MODEL.cpn]``
    Used by the build script: build the main window without showing it,
    check the update check has certificates to trust and, given a model,
    compute its state space the way the app does (in a worker process); print
    ``ok`` and exit.  Proves the bundle contains everything the GUI imports,
    can reach GitHub securely and can start its own workers, also on machines
    without a screen (with QT_QPA_PLATFORM=offscreen).
"""

from __future__ import annotations

import sys

SELF_TEST_FLAG = "--openprocess-self-test"
MCP_FLAG = "--mcp"                    # the same as openprocess.mcp.install.MCP_FLAG


def attach_stdio() -> None:
    """Give a windowed app's Python its standard streams back.

    On Windows a program built without a console has ``sys.stdin`` and
    ``sys.stdout`` set to None, although the process that started it (an
    assistant running the MCP server) passed it pipes: the pipes are there as
    the process's standard handles, so they are opened as files again.
    Elsewhere the streams are already there and nothing changes."""
    import io
    import os
    if sys.stdin is not None and sys.stdout is not None:
        return
    if sys.platform != "win32":
        if sys.stdin is None:
            sys.stdin = io.TextIOWrapper(io.FileIO(0, "rb", closefd=False), encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = io.TextIOWrapper(io.FileIO(1, "wb", closefd=False), encoding="utf-8",
                                          line_buffering=True)
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        return
    import ctypes
    import msvcrt
    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p

    def stream(number: int, reading: bool):
        handle = kernel32.GetStdHandle(number)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return None
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDONLY if reading else 0))
        except OSError:
            return None
        raw = io.FileIO(descriptor, "rb" if reading else "wb", closefd=False)
        if reading:
            return io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8")
        return io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8", line_buffering=True)
    if sys.stdin is None:
        sys.stdin = stream(-10, True)              # STD_INPUT_HANDLE
    if sys.stdout is None:
        sys.stdout = stream(-11, False)            # STD_OUTPUT_HANDLE
    if sys.stderr is None:
        sys.stderr = stream(-12, False) or open(os.devnull, "w", encoding="utf-8")


def serve_mcp(arguments: list[str]) -> int:
    """``OpenProcess --mcp``: the MCP server on stdio, without Qt's window."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")    # diagrams render as PNG without a window
    attach_stdio()
    from openprocess.mcp.server import main as serve
    return serve(arguments)


def self_test(model_path: str | None = None) -> int:
    """Create the Studio window off-screen, optionally run a state space job."""
    from PySide6.QtWidgets import QApplication

    from openprocess.gui.studio.app import StudioWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = StudioWindow(persist=False)    # persist=False: leave the user's settings alone
    application.processEvents()
    window.close()

    # The update check needs certificates to reach GitHub; a bundle without
    # them fails with CERTIFICATE_VERIFY_FAILED on users' machines (0.3.0).
    from openprocess.gui.studio.updates import ssl_context
    certificates = ssl_context().cert_store_stats()["x509_ca"]
    if certificates == 0:
        print("update check: no trusted certificates in the app", flush=True)
        return 1
    print(f"update check: {certificates} trusted certificates", flush=True)

    # Workflows: the standard boxes are imported by name, so a bundle can miss
    # them (0.7.0 did); the Code tab needs the sources.  Build and run the
    # ready-made workflow, and read a box's code, as the app would.
    from openprocess.flow import Runner, standard_library
    from openprocess.flow.box import algorithm_calls
    from openprocess.gui.flow.templates import TEMPLATES
    library = standard_library()
    if len(library) < 40:
        print(f"workflows: only {len(library)} boxes in the app", flush=True)
        return 1
    workflow = TEMPLATES[0][1](library, None)
    run = Runner(library).run(workflow)
    failed = [n.id for n in workflow.nodes.values() if run.status(n) != "done"]
    if failed:
        print(f"workflows: boxes did not run: {failed}", flush=True)
        return 1
    alpha = library.get("openprocess.flow.boxes.discover.alpha_miner")
    if "def alpha_miner" not in alpha.source or not algorithm_calls(alpha):
        print("workflows: the Code tab has no source in the app", flush=True)
        return 1
    print(f"workflows: {len(library)} boxes, the ready-made workflow runs, the code is there",
          flush=True)

    # Installing a tool from Connections: the app runs pip in a copy of itself,
    # and whatever the package then imports from the standard library must be
    # in the app (0.10.6's first build had no pip and no unittest, so Plot
    # failed after installing matplotlib).
    import importlib
    import subprocess

    from openprocess import packages
    if getattr(sys, "frozen", False):
        pip = subprocess.run([sys.executable, packages.PIP_FLAG, "--version"], capture_output=True, text=True,
                             timeout=180)
        if pip.returncode != 0 or "pip" not in pip.stdout:
            print(f"install: the app cannot run pip (exit {pip.returncode}): {pip.stderr.strip()[-400:]}", flush=True)
            return 1
        print(f"install: {pip.stdout.strip()}", flush=True)
    # Windows has no curses or readline, and it ships pty.py and tty.py, which
    # want termios: those fail in any Python on Windows, not just in the app.
    tolerated = {"curses", "readline"} if sys.platform == "win32" else set()
    posix_only = {"termios", "fcntl", "grp", "pwd", "posix", "resource", "syslog"}
    missing = []
    checked = 0
    for name in packages.stdlib_modules():
        if "." in name or name in tolerated:
            continue
        checked += 1
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as error:
            if sys.platform == "win32" and error.name in posix_only:
                continue
            missing.append(f"{name} ({type(error).__name__}: {error})")
        except Exception as error:  # noqa: BLE001 - whatever stops the import
            missing.append(f"{name} ({type(error).__name__}: {error})")
    if missing:
        print(f"install: standard library modules the app lacks: {'; '.join(missing[:12])}", flush=True)
        return 1
    print(f"install: the standard library is there ({checked} modules import)", flush=True)

    # The MCP server (Help ▸ Connect an AI Assistant): the SDK and every tool
    # must be in the app; build.py then starts `OpenProcess --mcp` and does
    # the handshake an assistant does.
    try:
        import asyncio

        from openprocess.mcp.server import build_server
        tools = asyncio.run(build_server().list_tools())
    except Exception as error:  # noqa: BLE001 - whatever the bundle lacks
        print(f"mcp: the connector does not load in the app: {type(error).__name__}: {error}", flush=True)
        return 1
    if len(tools) < 25:
        print(f"mcp: only {len(tools)} tools in the app", flush=True)
        return 1
    print(f"mcp: the connector loads, {len(tools)} tools", flush=True)

    if model_path:
        import time

        from openprocess.analysis.state_space_process import StateSpaceJob
        from openprocess.io.cpn_reader import read_cpn

        job = StateSpaceJob(read_cpn(model_path), max_nodes=1000)
        job.start()
        deadline = time.monotonic() + 120
        while (outcome := job.poll()) is None:
            if time.monotonic() > deadline:
                job.kill()
                print("state space: timed out", flush=True)
                return 1
            time.sleep(0.05)
        if outcome[0] != "done":
            print(f"state space: {outcome[1]}", flush=True)
            return 1
        print(f"state space: {outcome[1]['nodes']} nodes", flush=True)

    print("ok", flush=True)
    return 0


def main() -> int:
    # Decide what this run is before importing Qt: a state space worker must
    # stay light and must never open a window.
    from openprocess.analysis.state_space_process import WORKER_FLAG, run_worker
    from openprocess.packages import PIP_FLAG, activate, run_pip

    if len(sys.argv) > 1 and sys.argv[1] == WORKER_FLAG:
        return run_worker(sys.argv[2:5])
    if len(sys.argv) > 1 and sys.argv[1] == PIP_FLAG:
        return run_pip(sys.argv[2:])            # Install… from Connections, in a copy of the app
    activate()                                  # packages installed that way, on the import path
    if len(sys.argv) > 1 and sys.argv[1] == MCP_FLAG:
        return serve_mcp(sys.argv[2:])          # an AI assistant started the app as its MCP server
    if len(sys.argv) > 1 and sys.argv[1] == SELF_TEST_FLAG:
        return self_test(sys.argv[2] if len(sys.argv) > 2 else None)

    from openprocess.gui.studio.app import main as studio_main
    return studio_main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
