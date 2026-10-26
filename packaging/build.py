"""Build the standalone OpenProcess Studio app for the system this runs on.

Usage, from the project folder, in an environment with OpenProcess and its GUI
installed (``pip install -e ".[app]"``, or the conda environment)::

    pip install pyinstaller
    python packaging/build.py

Result, in ``dist/``:

=========  ==========================================================  ==================================
system     app                                                         file to share
=========  ==========================================================  ==================================
macOS      ``OpenProcess.app``                                               ``OpenProcess-<version>-macOS-<arch>.dmg``
Windows    ``OpenProcess\\OpenProcess.exe`` (with its folder)                      ``OpenProcess-<version>-Windows-<arch>.zip``
Linux      ``OpenProcess/OpenProcess`` (with its folder)                           ``OpenProcess-<version>-Linux-<arch>.tar.gz``
=========  ==========================================================  ==================================

The steps:

1. run PyInstaller on ``packaging/openprocess.spec``;
2. smoke-test the result: start it with ``--openprocess-self-test`` (builds the
   window off-screen and computes a state space through a worker process, as
   the app does), then run the worker on its own, then start it as the MCP
   server (``--mcp``) and do an assistant's handshake;
3. pack it into the file people download.

``--skip-tests`` skips step 2; ``--no-package`` skips step 3.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
ROOT = PACKAGING.parent
DIST = ROOT / "dist"


def fail(title: str, detail: str) -> None:
    """Stop with an error; on GitHub Actions also show it on the run's page."""
    print(f"!! {title}\n{detail}", file=sys.stderr, flush=True)
    if os.environ.get("GITHUB_ACTIONS"):
        # Annotations survive where full logs are hard to reach; keep the tail.
        tail = "\n".join(detail.strip().splitlines()[-40:])
        encoded = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={title}::{encoded}", flush=True)
    sys.exit(1)


def run(command: list[str], title: str, attempts: int = 1, **options) -> None:
    """Run a build command, showing its output; report its last lines if it fails."""
    import time
    for attempt in range(1, attempts + 1):
        result = subprocess.run(command, capture_output=True, text=True, **options)
        print(result.stdout + result.stderr, end="", flush=True)
        if result.returncode == 0:
            return
        if attempt < attempts:
            print(f"   ({title} failed, retrying in 10 s)", flush=True)
            time.sleep(10)
    fail(title, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}")


def version() -> str:
    """The version number, from its one home: ``__version__`` in openprocess/__init__.py."""
    text = (ROOT / "openprocess" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M).group(1)


def system_name() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


def architecture() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def executable() -> Path:
    """The program inside the build that a user would start."""
    if sys.platform == "darwin":
        return DIST / "OpenProcess.app" / "Contents" / "MacOS" / "OpenProcess"
    if sys.platform == "win32":
        return DIST / "OpenProcess" / "OpenProcess.exe"
    return DIST / "OpenProcess" / "OpenProcess"


# ---------------------------------------------------------------------------
# 1. PyInstaller
# ---------------------------------------------------------------------------
def run_pyinstaller() -> None:
    print("== PyInstaller", flush=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
         str(PACKAGING / "openprocess.spec")], "PyInstaller failed", cwd=ROOT)


# ---------------------------------------------------------------------------
# 2. Smoke tests
# ---------------------------------------------------------------------------
def smoke_test() -> None:
    program = executable()
    print(f"== Smoke test of {program.relative_to(ROOT)}", flush=True)
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    # a) The GUI, plus a state space computed the way the app computes it:
    #    the frozen app starts a copy of itself as the worker process.
    model = ROOT / "examples" / "dining_philosophers.cpn"
    result = subprocess.run([str(program), "--openprocess-self-test", str(model)], capture_output=True,
                            text=True, timeout=300, env=environment)
    if result.returncode != 0 or not result.stdout.rstrip().endswith("ok"):
        fail("Self-test failed", f"exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    print("   window and state space job: ok")

    # b) The worker on its own, with its messages visible if something breaks.
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "result.pickle"
        result = subprocess.run(
            [str(program), "--openprocess-state-space-worker", str(model), "1000", str(output)],
            input="", capture_output=True, text=True, timeout=180, env=environment)
        if "done" not in result.stdout or not output.exists():
            fail("State space worker failed",
                 f"exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    print("   state space worker: ok")

    # c) The MCP server, as an AI assistant starts it (Help ▸ Connect an AI
    #    Assistant writes exactly this command): `OpenProcess --mcp` on pipes,
    #    the initialize handshake, and the list of tools.
    import json
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "build-smoke-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    result = subprocess.run([str(program), "--mcp"], input="".join(json.dumps(m) + "\n" for m in messages),
                            capture_output=True, text=True, encoding="utf-8", timeout=180, env=environment)
    answers = {}
    for line in result.stdout.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and "id" in message:
            answers[message["id"]] = message
    info = (answers.get(1, {}).get("result") or {}).get("serverInfo") or {}
    tools = (answers.get(2, {}).get("result") or {}).get("tools") or []
    if info.get("name") != "OpenProcess" or len(tools) < 25:
        fail("The MCP server did not answer as an assistant expects",
             f"exit {result.returncode}\nserverInfo {info}, {len(tools)} tools\n"
             f"{result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    print(f"   MCP server: {info.get('name')} {info.get('version')}, {len(tools)} tools")


# ---------------------------------------------------------------------------
# 3. The file to share
# ---------------------------------------------------------------------------
def package() -> Path:
    name = f"OpenProcess-{version()}-{system_name()}-{architecture()}"
    print(f"== Packing {name}", flush=True)
    if sys.platform == "darwin":
        return _dmg(name)
    if sys.platform == "win32":
        return _zip(name)
    return _tarball(name)


def _dmg(name: str) -> Path:
    """A disk image showing OpenProcess.app next to a shortcut to /Applications."""
    staging = DIST / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    # ditto copies an app bundle faithfully (symlinks, permissions, signatures).
    run(["ditto", str(DIST / "OpenProcess.app"), str(staging / "OpenProcess.app")], "Copying the app failed")
    (staging / "Applications").symlink_to("/Applications")
    target = DIST / f"{name}.dmg"
    target.unlink(missing_ok=True)
    # hdiutil sometimes fails with "Resource busy" on CI machines; retry it.
    run(["hdiutil", "create", "-volname", "OpenProcess", "-srcfolder", str(staging),
         "-ov", "-format", "UDZO", str(target)], "Making the disk image failed", attempts=3)
    shutil.rmtree(staging)
    return target


def _zip(name: str) -> Path:
    target = DIST / f"{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((DIST / "OpenProcess").rglob("*")):
            archive.write(path, Path("OpenProcess") / path.relative_to(DIST / "OpenProcess"))
    return target


def _tarball(name: str) -> Path:
    """The app folder plus what Linux needs to show it in the applications menu."""
    folder = DIST / "OpenProcess"
    shutil.copy(PACKAGING / "icons" / "OpenProcess.png", folder / "OpenProcess.png")
    installer = folder / "install-desktop-entry.sh"
    shutil.copy(PACKAGING / "linux" / "install-desktop-entry.sh", installer)
    installer.chmod(0o755)
    target = DIST / f"{name}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.add(folder, arcname="OpenProcess")
    return target


def main() -> None:
    try:
        build()
    except subprocess.TimeoutExpired as error:
        fail("Timed out", f"{' '.join(map(str, error.cmd))}\n{error.stdout or ''}\n{error.stderr or ''}")


def build() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-tests", action="store_true", help="do not smoke-test the build")
    parser.add_argument("--no-package", action="store_true", help="do not make the .dmg/.zip/.tar.gz")
    options = parser.parse_args()

    run_pyinstaller()
    if not options.skip_tests:
        smoke_test()
    if not options.no_package:
        target = package()
        size = target.stat().st_size / 1_000_000
        print(f"== Done: {target.relative_to(ROOT)} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
