"""Optional packages, installed from inside the app.

Some boxes need a package the app does not ship (pandas, numpy, scipy,
matplotlib).  From a Python install, the app runs ``pip`` in that Python.
The downloaded app has a Python of its own with no pip on any path and no
writable site-packages, so it carries pip inside, runs it in a copy of
itself (``OpenProcess --pip install …``), and installs into a folder of
the user's own (:func:`user_site`), which :func:`activate` puts on the import
path at start-up and right after an install.  Either way the boxes that
need the package come alive without a restart.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from importlib.util import find_spec
from pathlib import Path

#: The flag that makes the downloaded app run pip instead of opening a window.
PIP_FLAG = "--pip"
#: Packages that need building from source (theirs or a dependency's), which
#: the downloaded app cannot do: it installs ready-built wheels only, so that
#: pip never has to start a Python (the app's own executable would open a window).
SOURCE_ONLY = ("pm4py",)
#: Standard library modules the downloaded app leaves out: test suites, IDEs
#: and toys, and the "dead batteries" Python 3.13 removed.
STDLIB_SKIP = frozenset({
    "test", "idlelib", "tkinter", "turtle", "turtledemo", "lib2to3", "pydoc_data", "ensurepip", "venv",
    "antigravity", "this", "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap", "msilib",
    "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib"})


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_site() -> Path:
    """Where the downloaded app installs packages: the user's own application
    data, per Python version (compiled packages are built for one)."""
    tag = f"py{sys.version_info.major}.{sys.version_info.minor}"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "OpenProcess"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "OpenProcess"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "openprocess"
    return base / "packages" / tag


def activate() -> bool:
    """Put :func:`user_site` on the import path when it exists (after the
    app's own modules, which stay as shipped).  Returns whether it was added
    (False when absent or already there)."""
    site = user_site()
    if not site.is_dir():
        return False
    text = str(site)
    if text in sys.path:
        return False
    sys.path.append(text)
    importlib.invalidate_caches()
    return True


def can_install() -> bool:
    """Whether Install… can work here: pip is importable (bundled, in the
    downloaded app) and, from a Python install, this Python can run it."""
    return find_spec("pip") is not None


def name_of(requirement: str) -> str:
    """``"pandas>=2.0"`` → ``"pandas"``."""
    return re.split(r"[<>=!~\[; ]", requirement.strip(), maxsplit=1)[0].lower()


def installable(requirement: str) -> bool:
    """Whether this build can install ``requirement``: any package from a
    Python install; in the downloaded app, one that comes as wheels."""
    return not (frozen() and name_of(requirement) in SOURCE_ONLY)


def install_command(requirement: str) -> list[str]:
    """The command that installs ``requirement`` for this app."""
    if frozen():
        return [sys.executable, PIP_FLAG, "install", "--upgrade", "--only-binary", ":all:",
                "--target", str(user_site()), requirement]
    return [sys.executable, "-m", "pip", "install", requirement]


def where_text() -> str:
    """A few words on where Install… puts a package, for the Connections cards."""
    return "into the app's own packages folder" if frozen() else "into this app's Python"


def stdlib_modules() -> list[str]:
    """Every standard library module, for the downloaded app to carry.

    PyInstaller packs only what the app imports, but a package installed from
    Connections imports what it likes (matplotlib wants ``unittest``), so the
    build lists the whole library: the top-level modules this Python has, and
    every submodule of its packages, found without importing the packages.
    """
    import pkgutil
    names: list[str] = []
    for name in sorted(getattr(sys, "stdlib_module_names", ())):
        if name.startswith("_") or name in STDLIB_SKIP:
            continue
        try:
            spec = find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue
        names.append(name)
        if spec.submodule_search_locations:
            for info in pkgutil.walk_packages(spec.submodule_search_locations, name + ".", onerror=lambda _n: None):
                if not info.name.endswith(".__main__"):
                    names.append(info.name)
    return names


def run_pip(arguments: list[str]) -> int:
    """Run pip in this process (the downloaded app, launched with ``--pip``)."""
    try:
        from pip._internal.cli.main import main as pip_main
    except ImportError:
        print("pip is not available in this build", file=sys.stderr)
        return 1
    target = user_site()
    if "--target" in arguments:
        target.mkdir(parents=True, exist_ok=True)
    return int(pip_main(list(arguments)) or 0)


__all__ = ["PIP_FLAG", "SOURCE_ONLY", "STDLIB_SKIP", "activate", "can_install", "frozen", "install_command",
           "installable", "name_of", "run_pip", "stdlib_modules", "user_site", "where_text"]
