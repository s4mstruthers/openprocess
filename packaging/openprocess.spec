# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the standalone OpenProcess Studio app.

Build with ``python packaging/build.py`` (which runs this file and then packs
the result), or directly with ``pyinstaller packaging/openprocess.spec``.

PyInstaller collects a Python interpreter, the openprocess package and the parts of
Qt the app imports into one folder, ``dist/OpenProcess/``.  On macOS that folder is
then wrapped as ``dist/OpenProcess.app``.  PyInstaller cannot cross-compile: each
system builds its own app, which is why the GitHub workflow runs this on a
macOS, a Windows and a Linux machine.

This file is Python, run by PyInstaller with a few names predefined:
``SPECPATH`` (this folder), ``Analysis``, ``PYZ``, ``EXE``, ``COLLECT`` and
``BUNDLE``.
"""

import pathlib
import re
import sys
from pathlib import Path

PACKAGING = Path(SPECPATH)
ROOT = PACKAGING.parent
ICONS = PACKAGING / "icons"

# One version number for everything: __version__ in openprocess/__init__.py.
VERSION = re.search(r'^__version__\s*=\s*"([^"]+)"',
                    (ROOT / "openprocess" / "__init__.py").read_text(encoding="utf-8"), re.M).group(1)

if sys.platform == "darwin":
    ICON = str(ICONS / "OpenProcess.icns")
elif sys.platform == "win32":
    ICON = str(ICONS / "OpenProcess.ico")
else:
    ICON = None                         # Linux: the icon goes in the .desktop file

from PyInstaller.utils.hooks import collect_all, collect_submodules

# pip travels inside the app, so Connections ▸ Install… can add pandas, numpy,
# scipy or matplotlib to a folder of the user's own (openprocess.packages).
PIP_DATAS, PIP_BINARIES, PIP_HIDDEN = collect_all("pip")

# A package installed that way imports what it likes from the standard library
# (matplotlib wants unittest), and PyInstaller packs only what the app imports:
# so the whole library is listed (openprocess.packages.stdlib_modules).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from openprocess.packages import stdlib_modules
STDLIB = stdlib_modules()

# The Code tab reads a box's source with inspect.getsource, and the algorithm
# it calls likewise: the bundle carries every .py file of the package next to
# its compiled form, so the app shows the code it runs (0.7.1).
SOURCES = [(str(path), str(pathlib.Path("openprocess") / path.relative_to(ROOT / "openprocess").parent))
           for path in (ROOT / "openprocess").rglob("*.py")]

analysis = Analysis(
    [str(PACKAGING / "openprocess_studio.py")],
    pathex=[str(ROOT)],
    binaries=PIP_BINARIES,
    # Files the code opens by path at run time (the window icon, and the demo
    # exercises that File ▸ Open Demo Exercises copies to Documents).
    datas=PIP_DATAS + [(str(ROOT / "openprocess" / "gui" / "resources" / "openprocess-icon.png"),
            "openprocess/gui/resources"),
           (str(ROOT / "openprocess" / "exercises"), "openprocess/exercises"),
           # Help ▸ Writing Exercise Packs.
           (str(ROOT / "openprocess" / "learn" / "exercise-packs.md"), "openprocess/learn")]
          + SOURCES,
    # Imported inside functions, or by name (the standard boxes: the library
    # imports openprocess.flow.boxes.<group> at run time, so PyInstaller
    # cannot see them, and 0.7.0 shipped without them: New Workflow failed);
    # listed so they are never missed.  certifi brings its certificate file
    # (PyInstaller's hook), which the update check needs to reach GitHub: the
    # bundled Python cannot use the system's.
    hiddenimports=["openprocess.analysis.state_space_process", "certifi", "openprocess.packages"]
                  + collect_submodules("openprocess.flow.boxes") + PIP_HIDDEN + STDLIB,
    # Not needed by the app.  PM4Py is an optional extra (AGPL-3.0) and is
    # never bundled; the app hides its PM4Py options when it is missing.
    excludes=["pm4py", "tkinter", "pytest", "matplotlib", "numpy", "pandas", "scipy"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,              # "one folder" build: starts faster than one file
    name="OpenProcess",
    console=False,                      # a GUI app: no terminal window on Windows
    icon=ICON,
    argv_emulation=False,
    upx=False,
)

folder = COLLECT(executable, analysis.binaries, analysis.datas, name="OpenProcess", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        folder,
        name="OpenProcess.app",
        icon=ICON,
        bundle_identifier="io.github.s4mstruthers.openprocess",
        version=VERSION,
        info_plist={
            "CFBundleName": "OpenProcess",
            "CFBundleDisplayName": "OpenProcess Studio",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # Follow the system's light / dark appearance, as the app does from source.
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.education",
            "NSHumanReadableCopyright": "MIT licence",
        },
    )
