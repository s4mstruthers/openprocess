"""Installing optional packages from the app: where, how, and in the downloaded app."""

from __future__ import annotations

import sys
from pathlib import Path

from openprocess import packages


def test_the_user_site_is_per_platform_and_python(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    site = packages.user_site()
    assert site == tmp_path / "Library" / "Application Support" / "OpenProcess" / "packages" / \
        f"py{sys.version_info.major}.{sys.version_info.minor}"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert packages.user_site().parts[-4:-2] == ("Local", "OpenProcess")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert packages.user_site() == tmp_path / "xdg" / "openprocess" / "packages" / \
        f"py{sys.version_info.major}.{sys.version_info.minor}"


def test_the_command_depends_on_how_the_app_runs(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert packages.install_command("pandas>=2.0") == [sys.executable, "-m", "pip", "install", "pandas>=2.0"]
    assert packages.where_text() == "into this app's Python"
    assert packages.installable("pm4py") and packages.installable("numpy>=1.24")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(packages, "user_site", lambda: tmp_path / "site")
    command = packages.install_command("pandas>=2.0")
    assert command[:2] == [sys.executable, "--pip"] and "--target" in command and str(tmp_path / "site") in command
    assert "--only-binary" in command                       # pip must never start a Python: it would open the app
    assert packages.where_text() == "into the app's own packages folder"
    assert packages.installable("numpy>=1.24") and not packages.installable("pm4py")
    assert packages.name_of("matplotlib>=3.7") == "matplotlib" and packages.name_of("pm4py[extra]") == "pm4py"


def test_activate_puts_the_site_first_once(monkeypatch, tmp_path):
    site = tmp_path / "site"
    monkeypatch.setattr(packages, "user_site", lambda: site)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert not packages.activate()                           # no folder yet: nothing to add
    site.mkdir()
    assert packages.activate() and sys.path[-1] == str(site)   # after the app's own modules
    assert not packages.activate() and sys.path.count(str(site)) == 1


def test_run_pip_hands_the_arguments_to_pip(monkeypatch, tmp_path):
    import types
    calls = []
    fake = types.ModuleType("pip._internal.cli.main")
    fake.main = lambda args: calls.append(list(args)) or 0
    monkeypatch.setitem(sys.modules, "pip._internal.cli.main", fake)
    monkeypatch.setattr(packages, "user_site", lambda: tmp_path / "site")
    assert packages.run_pip(["install", "--target", str(tmp_path / "site"), "numpy"]) == 0
    assert calls == [["install", "--target", str(tmp_path / "site"), "numpy"]]
    assert (tmp_path / "site").is_dir()                      # made for pip, so --target has somewhere to go
    assert packages.can_install() == (__import__("importlib").util.find_spec("pip") is not None)


def test_the_whole_standard_library_is_listed_for_the_downloaded_app():
    names = packages.stdlib_modules()
    assert {"unittest", "unittest.mock", "json", "xml.etree.ElementTree", "sqlite3", "decimal"} <= set(names)
    assert not any(n == "test" or n.startswith("test.") for n in names)          # CPython's own tests
    assert "tkinter" not in names and "idlelib" not in names and "asyncio.__main__" not in names
    assert len(names) == len(set(names))
