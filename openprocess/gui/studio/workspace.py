"""Working in a folder such as "Week 2".

Opening a folder (File ▸ Open Folder…) makes the sidebar list every event
log and Petri net in it, opened or not, and keeps the folder and the app in
step.  Internally the open folder is a :class:`Workspace`; the app only ever
calls it a folder.  This module is the part that does not need Qt:

* :meth:`Workspace.tree` / :meth:`Workspace.files` -- the subfolders and the
  files in the folder the app can open;
* :meth:`Workspace.load_state` / :meth:`Workspace.save_state` -- which of
  them were open, so the folder comes back as you left it;
* :meth:`Workspace.settings` / :meth:`Workspace.update_settings` -- choices
  made for this folder (the sidebar's view, expanded subfolders, what to do
  with files opened from elsewhere);
* :func:`unique_path` and :func:`atomic_write` -- naming and writing the
  files the app creates in the folder;
* :func:`exercise_files` -- whether a folder is an *exercise* (it has a
  ``question`` file) and which of its files play which part.

The state lives in a small hidden JSON file, ``.openprocess``, inside the folder;
the folder's scratch notes (✎ Notes) in another, ``.openprocess notes.md``.
Paths in it are relative to the folder, so the workspace still works after
the folder is moved, renamed or synced to another computer (iCloud, a USB
stick, a Git repository).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

#: What the app can open, by extension, and the sidebar section it goes in.
KINDS = {
    ".xes": "log",
    ".xes.gz": "log",
    ".csv": "log",
    ".pnml": "petri",
    ".bpmn": "bpmn",
    ".cpn": "cpn",
    ".cpnflow": "workflow",
    ".jsonocel": "ocel",
    ".json": "ocel",
}

#: Text files the app opens, by the end of their name: a log in textbook
#: notation (``log.txt``, ``L1.log.txt``) and a transition system (``ts.txt``,
#: ``exam.ts.txt``).  Other ``.txt`` files are not listed.
TEXT_KINDS = {"log.txt": "log", "ts.txt": "ts"}

#: Extensions of more than one part, longest first.
COMPOUND_SUFFIXES = (".xes.gz", ".log.txt", ".ts.txt")

#: The hidden file holding the workspace's state.
STATE_FILE = ".openprocess"

#: The hidden file holding the folder's scratch notes (the ✎ Notes overlay).
NOTES_FILE = ".openprocess notes.md"

#: The same files as CPNpy (before 0.7) wrote them: renamed when the folder is opened.
OLD_FILES = {".cpnpy": STATE_FILE, ".cpnpy notes.md": NOTES_FILE}

#: How deep into subfolders to look ("Week 2/logs/boarding.xes" is depth 1).
MAX_DEPTH = 3

#: Stop listing after this many files, so opening a huge folder by mistake
#: (your home folder, say) does not freeze the window.
MAX_FILES = 500

#: At most this many subfolders are listed (and watched for changes).
MAX_FOLDERS = 200

#: Folders never looked into: tools' caches and the like.
SKIPPED_FOLDERS = {"__pycache__", "node_modules", "build", "dist", "venv", ".venv"}

#: iCloud Drive's "Optimise Mac Storage" replaces a file that is only in
#: iCloud by a hidden placeholder: ``Week 2.xes`` becomes ``.Week 2.xes.icloud``.
ICLOUD_SUFFIX = ".icloud"

#: Characters macOS, Windows or Linux do not allow in a file name.
FORBIDDEN_CHARACTERS = set('/\\:*?"<>|')


def file_kind(path: Path) -> str | None:
    """``"log"``, ``"petri"``, ``"bpmn"``, ``"cpn"``, ``"ts"`` or ``None`` for a file the app cannot open."""
    name = path.name.lower()
    if name.endswith(".xes.gz"):
        return KINDS[".xes.gz"]
    for ending, kind in TEXT_KINDS.items():
        if name == ending or name.endswith("." + ending):
            return kind
    return KINDS.get(path.suffix.lower())


def file_suffix(name: str) -> str:
    """The whole extension: ``log.xes.gz`` gives ``.xes.gz`` (not just ``.gz``),
    ``L1.log.txt`` gives ``.log.txt``."""
    lower = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[-len(suffix):]
    return os.path.splitext(name)[1]


def file_stem(name: str) -> str:
    """The file name without :func:`file_suffix`."""
    return name[: len(name) - len(file_suffix(name))]


def safe_file_name(name: str) -> str:
    """``name`` usable as a file name: forbidden characters become "-"."""
    cleaned = "".join("-" if c in FORBIDDEN_CHARACTERS or ord(c) < 32 else c for c in name)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "Untitled"


def unique_path(folder: str | Path, file_name: str) -> Path:
    """``folder/file_name``, or the first free ``"name 2.ext"``, ``"name 3.ext"``, …

    The numbering is Finder's ("Keep both"), so ``Wilma 50.xes`` becomes
    ``Wilma 50 2.xes``.  An iCloud placeholder counts as taken.
    """
    folder = Path(folder)
    stem, suffix = file_stem(file_name), file_suffix(file_name)

    def taken(name: str) -> bool:
        return (folder / name).exists() or (folder / f".{name}{ICLOUD_SUFFIX}").exists()

    candidate, number = file_name, 2
    while taken(candidate):
        candidate = f"{stem} {number}{suffix}"
        number += 1
    return folder / candidate


def atomic_write(path: str | Path, write: Callable[[Path], Any]) -> None:
    """Write a file so that a crash never leaves it half-written.

    ``write`` writes to a hidden temporary file next to ``path`` (with the
    same extension, so writers that look at it still work), which then
    replaces ``path`` in one step.
    """
    path = Path(path)
    temporary = path.with_name(f".~{path.name}")
    try:
        write(temporary)
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def made_by_openprocess(path: str | Path) -> bool:
    """True when a ``.cpn`` file was written by OpenProcess (not by CPN Tools, say)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return b'tool="OpenProcess"' in head


def display_name(relative: str) -> str:
    """The name shown in the sidebar: the path inside the folder, without extension.

    ``"order handling.pnml"`` → ``"order handling"``;
    ``"logs/boarding.xes.gz"`` → ``"logs/boarding"``.
    """
    lower = relative.lower()
    base = lower.rpartition("/")[2]
    if base in TEXT_KINDS:                                      # "log.txt" → "log"
        return relative[:-4]
    extensions = list(KINDS) + list(COMPOUND_SUFFIXES)
    for extension in sorted(extensions, key=len, reverse=True):     # ".xes.gz" before ".gz"
        if lower.endswith(extension):
            return relative[: len(relative) - len(extension)]
    return relative


@dataclass(frozen=True)
class WorkspaceFile:
    """One file in the workspace that the app can open."""

    path: Path          # absolute
    relative: str       # inside the folder, with "/" separators
    kind: str           # "log", "petri", "cpn" or "ts"
    #: Only in iCloud for now ("Optimise Mac Storage"): ``path`` does not exist
    #: yet, a placeholder (:attr:`cloud_placeholder`) stands in for it.
    in_cloud: bool = False

    @property
    def name(self) -> str:
        return display_name(self.relative)

    @property
    def cloud_placeholder(self) -> Path:
        return self.path.with_name(f".{self.path.name}{ICLOUD_SUFFIX}")


@dataclass
class WorkspaceFolder:
    """A folder in the workspace (the workspace's own folder too), with its contents."""

    path: Path
    relative: str                       # "" for the workspace's own folder
    folders: list["WorkspaceFolder"] = field(default_factory=list)
    files: list[WorkspaceFile] = field(default_factory=list)
    #: True (on the top folder) when :data:`MAX_FILES` or :data:`MAX_FOLDERS`
    #: cut the listing short.
    truncated: bool = False
    #: The folder is an exercise (see :func:`exercise_files`).
    exercise: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    def walk(self) -> Iterator["WorkspaceFolder"]:
        """This folder, then every subfolder (depth first, in order)."""
        yield self
        for folder in self.folders:
            yield from folder.walk()


class Workspace:
    """A folder opened as a workspace."""

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder).expanduser().resolve()
        self._carry_over_old_files()

    @property
    def name(self) -> str:
        return self.folder.name or str(self.folder)

    def exists(self) -> bool:
        return self.folder.is_dir()

    # -- the files ---------------------------------------------------------------
    def tree(self) -> WorkspaceFolder:
        """The folder's subfolders and the files the app can open in them.

        Folders come before files, both sorted by name (as Finder's list
        view does), and empty folders are included, so a folder just made
        shows up before anything is put in it.  Hidden files and folders
        (starting with ".") are skipped, as Finder does, and so are the folders
        in :data:`SKIPPED_FOLDERS` and anything deeper than :data:`MAX_DEPTH`.
        Files that are only in iCloud are listed with ``in_cloud=True``.
        """
        root = WorkspaceFolder(self.folder, "")
        budget = {"files": MAX_FILES, "folders": MAX_FOLDERS}

        def fill(folder: WorkspaceFolder, depth: int) -> None:
            try:
                entries = sorted(os.scandir(folder.path), key=lambda e: e.name.casefold())
            except OSError:
                return
            names = {entry.name for entry in entries}
            folder.exercise = _question_file(names) is not None
            subfolders = []
            for entry in entries:
                name = entry.name
                try:
                    is_folder = entry.is_dir()
                except OSError:
                    continue
                if is_folder:
                    if not name.startswith(".") and name not in SKIPPED_FOLDERS \
                            and depth < MAX_DEPTH:
                        subfolders.append(entry)
                    continue
                in_cloud = False
                if name.startswith("."):
                    # ".Week 2.xes.icloud" stands for "Week 2.xes", still in iCloud.
                    if not name.endswith(ICLOUD_SUFFIX) or name[1:-len(ICLOUD_SUFFIX)] in names:
                        continue
                    name, in_cloud = name[1:-len(ICLOUD_SUFFIX)], True
                    if not name or name.startswith("."):
                        continue
                path = folder.path / name
                kind = file_kind(path)
                if kind is None:
                    continue
                if folder.exercise and name.lower() in ANSWER_FILES:
                    continue            # the model answer is not a file to open by accident
                if budget["files"] <= 0:
                    root.truncated = True
                    return
                budget["files"] -= 1
                relative = path.relative_to(self.folder).as_posix()
                folder.files.append(WorkspaceFile(path, relative, kind, in_cloud))
            # A placeholder's own name (".Week 2.xes.icloud") sorted it first.
            folder.files.sort(key=lambda f: f.path.name.casefold())
            for entry in subfolders:
                if budget["folders"] <= 0:
                    root.truncated = True
                    return
                budget["folders"] -= 1
                path = Path(entry.path)
                child = WorkspaceFolder(path, path.relative_to(self.folder).as_posix())
                folder.folders.append(child)
                fill(child, depth + 1)

        fill(root, 0)
        return root

    def files(self, tree: WorkspaceFolder | None = None) -> list[WorkspaceFile]:
        """Every file the app can open, by path (a folder's files before its subfolders).

        iCloud placeholders are left out; see :meth:`tree` for those.
        """
        tree = tree or self.tree()
        return [f for folder in tree.walk() for f in folder.files if not f.in_cloud]

    def directories(self, tree: WorkspaceFolder | None = None) -> list[Path]:
        """The folders whose changes matter (for a file system watcher)."""
        return [folder.path for folder in (tree or self.tree()).walk()]

    def contains(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.folder)
        except (ValueError, OSError):
            return False
        return True

    def relative(self, path: str | Path) -> str:
        """``path`` relative to the folder if it is inside it, else absolute."""
        resolved = Path(path).resolve()
        try:
            return resolved.relative_to(self.folder).as_posix()
        except ValueError:
            return str(resolved)

    def absolute(self, stored: str) -> Path:
        """The inverse of :meth:`relative`."""
        path = Path(stored)
        return path if path.is_absolute() else self.folder / path

    def _carry_over_old_files(self) -> None:
        """A folder last opened by CPNpy keeps its state and notes under the new names."""
        for old, new in OLD_FILES.items():
            source, target = self.folder / old, self.folder / new
            if source.is_file() and not target.exists():
                try:
                    os.replace(source, target)
                except OSError:
                    pass

    # -- what was open -----------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.folder / STATE_FILE

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any], create: bool = True) -> None:
        """Write the state file, unless it would not change.  Never fails."""
        data = {"about": "OpenProcess: the files that were open in this folder. Safe to delete.",
                "version": 1, **{k: v for k, v in data.items() if k not in ("about", "version")}}
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        try:
            if self.state_path.exists():
                if self.state_path.read_text(encoding="utf-8") == text:
                    return                  # unchanged: do not touch the file (or its sync)
            elif not create:
                return                      # nothing worth a file yet
            self.state_path.write_text(text, encoding="utf-8")
        except OSError:
            pass                            # a read-only folder is just not written

    def load_state(self) -> dict[str, Any]:
        """``{"open": [{"path": …, …options}], "selected": path | None}``; empty if none."""
        data = self._read()
        entries = [e for e in data.get("open", []) if isinstance(e, dict) and e.get("path")]
        for entry in entries:
            entry["path"] = str(self.absolute(entry["path"]))
        selected = data.get("selected")
        return {"open": entries,
                "selected": str(self.absolute(selected)) if selected else None}

    def save_state(self, entries: list[dict[str, Any]], selected: str | None) -> None:
        """Remember the open files.  Never fails: a read-only folder just is not written."""
        data = self._read()
        data["open"] = [dict(entry, path=self.relative(entry["path"])) for entry in entries]
        data["selected"] = self.relative(selected) if selected else None
        self._write(data, create=bool(entries))

    # -- choices made for this folder ----------------------------------------------
    def settings(self) -> dict[str, Any]:
        """``{"view": "folder" | "kind", "expanded": [relative, …], "import": …}``."""
        settings = self._read().get("settings", {})
        return settings if isinstance(settings, dict) else {}

    def update_settings(self, **changes: Any) -> None:
        """Change some settings (``None`` removes one)."""
        data = self._read()
        settings = dict(data.get("settings") or {})
        for key, value in changes.items():
            if value is None:
                settings.pop(key, None)
            else:
                settings[key] = value
        if settings == data.get("settings"):
            return
        data["settings"] = settings
        data.setdefault("open", [])
        data.setdefault("selected", None)
        self._write(data)

    # -- scratch notes -------------------------------------------------------------
    @property
    def notes_path(self) -> Path:
        return self.folder / NOTES_FILE

    def load_notes(self) -> str:
        try:
            return self.notes_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save_notes(self, text: str) -> None:
        """Keep the notes (an empty pad leaves no file behind).  Raises OSError."""
        if not text.strip():
            try:
                self.notes_path.unlink()
            except FileNotFoundError:
                pass
            return
        atomic_write(self.notes_path, lambda path: path.write_text(text, encoding="utf-8"))


# ---------------------------------------------------------------------------
# Exercises
# ---------------------------------------------------------------------------
# An exercise is a folder with a question file; the details live with the
# rest of the exercise code (no Qt there, so the command line can use it).
from ...learn.pack import (  # noqa: E402,F401 - re-exported
    ANSWER_FILES, MY_ANSWER, MY_ANSWERS, QUESTION_FILES, ExerciseFiles, _question_file,
    exercise_files,
)
