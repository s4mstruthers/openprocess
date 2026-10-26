"""Exercise packs: folders of exercises, and the student's progress in them.

A *pack* is an ordinary folder::

    Process Mining — Week 3/
        pack.md                       optional: the pack's title and introduction
        1 Petri nets/                 optional: chapters are just subfolders
            Exercise 1.1 Order handling/
                question.md           the worksheet (see sheet.py)
                answer.pnml           files the answer blocks refer to
        2 Discovery/
            Exercise 2.1 The alpha-algorithm/
                question.md
                log.txt               a given log, in the course's notation

Any folder with a ``question.md`` (or ``question.pdf`` / ``.png``) is an
exercise.  Exercises and chapters are listed in name order, numbers sorted
as numbers (Exercise 2 before Exercise 10).

The student's work is stored next to the question: ``my answers.json``
(every typed answer and whether it was right), ``my answer.pnml`` for a
drawn net, ``my workflow.analysis`` for a built workflow, and ``my notes.md``
for scratch notes.  Delete those files to start an exercise again.

``pack.md`` may start with front matter (see :mod:`.sheet`): ``exam: yes``
makes the pack an exam (no hints, no *Show answer*, a ``time:`` in
minutes and a ``deadline:``), ``seed: student`` gives every student their
own variant, ``boxes:`` lists the boxes a workflow in the pack may use.
The student's name for variants is kept in the pack as ``my name.txt``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .sheet import Sheet, SheetError, Task, parse_sheet

#: The question, in the order the files are looked for.
QUESTION_FILES = ("question.md", "question.pdf", "question.png", "question.jpg",
                  "question.jpeg", "question.txt")
#: Files that hold the answer (left out of the sidebar in an exercise).
ANSWER_FILES = {"answer.pnml", "answer.md"}
#: The file OpenProcess creates for your own net.
MY_ANSWER = "my answer.pnml"
#: Your typed answers and how they did.
MY_ANSWERS = "my answers.json"
#: Your scratch notes (the Notes pane).
MY_NOTES = "my notes.md"
#: Your workflow, when an exercise asks for one.
MY_WORKFLOW = "my workflow.analysis"
#: The optional title page of a pack.
PACK_FILE = "pack.md"
#: The student's name, for variants (in the pack's folder).
MY_NAME = "my name.txt"
#: When an exam was started (in the pack's folder).
MY_EXAM = "my exam.json"

MAX_DEPTH = 3


def natural_key(text: str) -> list:
    """Sort "Exercise 2" before "Exercise 10"."""
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", text)]


def _question_file(names) -> str | None:
    lower = {name.lower(): name for name in names}
    for candidate in QUESTION_FILES:
        if candidate in lower:
            return lower[candidate]
    return None


@dataclass(frozen=True)
class ExerciseFiles:
    """The parts of an exercise folder (None: the folder has no such file).

    ===================  ================================================
    ``question``         ``question.md`` (or ``.pdf`` / ``.png``): the worksheet
    ``net``              ``net.pnml``: a given net
    ``log``              ``log.xes``, ``log.csv`` or ``log.txt`` (textbook notation)
    ``ts``               ``ts.txt``: a given transition system
    ``answer_net``       ``answer.pnml``: a model answer net
    ``answer_text``      ``answer.md``: the worked answer, hidden until asked for
    ``my_answer``        ``my answer.pnml``: your net (it may not exist yet)
    ===================  ================================================
    """

    folder: Path
    question: Path
    net: Path | None = None
    log: Path | None = None
    ts: Path | None = None
    answer_net: Path | None = None
    answer_text: Path | None = None

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def my_answer(self) -> Path:
        return self.folder / MY_ANSWER

    @property
    def needs_a_net(self) -> bool:
        """Is drawing (or editing) a net part of the exercise?"""
        return self.net is not None or self.answer_net is not None

    def file(self, name: str) -> Path | None:
        """A file of the exercise named in an answer block (None: not there)."""
        path = (self.folder / name).resolve()
        try:
            path.relative_to(self.folder.resolve())
        except ValueError:
            return None                          # only files inside the exercise
        return path if path.is_file() else None


def exercise_files(folder: str | Path) -> ExerciseFiles | None:
    """The exercise in ``folder``, or None when it has no ``question`` file."""
    folder = Path(folder)
    try:
        names = [entry.name for entry in os.scandir(folder) if entry.is_file()]
    except OSError:
        return None
    question = _question_file(names)
    if question is None:
        return None
    lower = {name.lower(): folder / name for name in names}

    def first(*candidates: str) -> Path | None:
        return next((lower[c] for c in candidates if c in lower), None)

    return ExerciseFiles(folder, folder / question, net=first("net.pnml"),
                         log=first("log.xes", "log.xes.gz", "log.csv", "log.txt"),
                         ts=first("ts.txt"), answer_net=first("answer.pnml"),
                         answer_text=first("answer.md", "answer.txt"))


# ---------------------------------------------------------------------------
# One exercise
# ---------------------------------------------------------------------------
class Exercise:
    """An exercise folder with its worksheet."""

    def __init__(self, files: ExerciseFiles, pack_settings: dict | None = None) -> None:
        self.files = files
        self.folder = files.folder
        self.error: str | None = None
        #: The pack's front matter (exam, seed, boxes…), for settings the sheet does not give.
        self.pack_settings: dict[str, str] = dict(pack_settings or {})
        self.sheet = self._read_sheet()

    def setting(self, key: str, default: str | None = None) -> str | None:
        """A setting of the exercise's front matter, else of the pack's."""
        value = self.sheet.settings.get(key)
        if value is None:
            value = self.pack_settings.get(key)
        return default if value is None else value

    def student(self) -> str | None:
        """The student's name (``my name.txt`` in the pack), for variants."""
        for folder in [self.folder, *self.folder.parents]:
            path = folder / MY_NAME
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8").strip()
                except OSError:
                    return None
            if (folder / PACK_FILE).is_file():
                break
        return None

    @property
    def points(self) -> float:
        """The points of the whole exercise (its blocks', or ``points:`` in front)."""
        front = self.sheet.settings.get("points")
        if front:
            try:
                return float(front)
            except ValueError:
                pass
        return self.sheet.points

    @classmethod
    def at(cls, folder: str | Path) -> "Exercise | None":
        files = exercise_files(folder)
        return cls(files) if files is not None else None

    @property
    def title(self) -> str:
        return self.sheet.title if self.sheet.title != "Exercise" else self.folder.name

    def _read_sheet(self) -> Sheet:
        question = self.files.question
        if question.suffix.lower() == ".md":
            try:
                sheet = parse_sheet(question.read_text(encoding="utf-8", errors="replace"))
            except SheetError as error:
                self.error = f"{question.name}: {error}"
                sheet = Sheet(self.folder.name, [question.read_text(
                    encoding="utf-8", errors="replace")])
        elif question.suffix.lower() == ".txt":
            sheet = Sheet(self.folder.name, ["```\n" + question.read_text(
                encoding="utf-8", errors="replace") + "\n```"])
        else:
            sheet = Sheet(self.folder.name, [])        # a PDF or picture: shown as it is
        if not sheet.tasks:
            parts = split_parts(sheet.blocks[0]) if len(sheet.blocks) == 1 else []
            if len(parts) >= 2:
                sheet.blocks = self._boxes_for_parts(parts)
            else:
                sheet.blocks += self._tasks_from_files()
        return sheet

    def _boxes_for_parts(self, parts: list[tuple[str, str]]) -> list:
        """An exercise written before answer blocks, in lettered parts (a., b., …):
        a box under each part.  The part that asks for a net to be drawn or
        changed gets the net editor; the others a text box, with that part of
        ``answer.md`` as its model answer."""
        files = self.files
        solutions = {}
        if files.answer_text is not None:
            text = files.answer_text.read_text(encoding="utf-8", errors="replace")
            solutions = {letter: body for letter, body in split_parts(text) if letter}
        has_net = files.net is not None or files.answer_net is not None
        drawing = [letter for letter, body in parts if letter and DRAWING.search(body)]
        net_part = drawing[-1] if has_net and drawing else None
        blocks: list = []
        for letter, body in parts:
            blocks.append(body)
            if not letter:
                continue
            if letter == net_part:
                settings = {"answer": files.answer_net.name} if files.answer_net else {}
                if letter in solutions:
                    settings["solution"] = solutions[letter]
                blocks.append(Task("net", letter, settings=settings))
            else:
                settings = {"solution": solutions[letter]} if letter in solutions else {}
                blocks.append(Task("open", letter, settings=settings))
        if has_net and net_part is None:
            blocks += self._tasks_from_files()[:1]
        return blocks

    def _tasks_from_files(self) -> list[Task]:
        """An exercise written before answer blocks: a net to draw if there is
        an answer net, else one box for your answer with ``answer.md`` behind it."""
        files = self.files
        if files.answer_net is not None:
            return [Task("net", "net", settings={"answer": files.answer_net.name})]
        if files.net is not None:
            return [Task("net", "net", settings={})]
        if files.answer_text is not None:
            return [Task("open", "answer", settings={
                "solution": files.answer_text.read_text(encoding="utf-8", errors="replace")})]
        return []

    # -- your work ---------------------------------------------------------------------------
    def net_tasks(self) -> list[Task]:
        return [t for t in self.sheet.tasks if t.type == "net"]

    @property
    def workflow_path(self) -> Path:
        """Where the workflow you build is saved."""
        return self.folder / MY_WORKFLOW

    def answer_net_path(self, task: Task) -> Path:
        """Where your net for ``task`` is saved: ``my answer.pnml``, and for a second
        net in the same exercise ``my answer (d).pnml``."""
        nets = self.net_tasks()
        if not nets or task is nets[0]:
            return self.folder / MY_ANSWER
        return self.folder / f"my answer ({task.id}).pnml"

    def start_net(self, task: Task) -> Path | None:
        """The net ``task`` starts from: its ``start:`` file, else the given net."""
        start = task.get("start")
        if start is not None:
            return None if start.lower() in ("empty", "") else self.files.file(start)
        return self.files.net

    @property
    def progress_file(self) -> Path:
        return self.folder / MY_ANSWERS

    def load_progress(self) -> dict:
        try:
            data = json.loads(self.progress_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        tasks = data.get("tasks", {}) if isinstance(data, dict) else {}
        return tasks if isinstance(tasks, dict) else {}

    def save_progress(self, tasks: dict) -> None:
        text = json.dumps({"version": 1, "exercise": self.title, "tasks": tasks},
                          ensure_ascii=False, indent=2)
        temporary = self.progress_file.with_name("." + MY_ANSWERS + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.progress_file)

    @property
    def notes_file(self) -> Path:
        return self.folder / MY_NOTES

    def load_notes(self) -> str:
        try:
            return self.notes_file.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save_notes(self, text: str) -> None:
        """Keep the notes (an empty pad leaves no file behind)."""
        if not text.strip():
            try:
                self.notes_file.unlink()
            except OSError:
                pass
            return
        temporary = self.notes_file.with_name("." + MY_NOTES + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.notes_file)

    def summary(self, progress: dict | None = None) -> "ProgressSummary":
        progress = self.load_progress() if progress is None else progress
        tasks = self.sheet.tasks
        done = [t for t in tasks if progress.get(t.id, {}).get("status")
                in ("correct", "done")]
        tried = [t for t in tasks if progress.get(t.id, {}).get("status")]
        earned = 0.0
        for task in tasks:
            entry = progress.get(task.id, {})
            if entry.get("status") == "correct":
                earned += task.points
            elif entry.get("status") == "done":
                earned += task.points
            elif entry.get("status") == "partial":
                earned += task.points * float(entry.get("share", 0) or 0)
        return ProgressSummary(len(tasks), len(done), len(tried), self.points, earned)

    def reset(self) -> None:
        """Start again: your answers, nets, workflow and notes go."""
        for path in [self.progress_file, self.notes_file, self.workflow_path] + \
                [self.answer_net_path(t) for t in self.net_tasks()]:
            try:
                path.unlink()
            except OSError:
                pass


@dataclass
class ProgressSummary:
    total: int
    done: int
    tried: int
    #: The exercise's points, and what the answers so far earn.
    points: float = 0.0
    earned: float = 0.0

    @property
    def state(self) -> str:
        """``"done"``, ``"started"`` or ``"new"``."""
        if self.total and self.done == self.total:
            return "done"
        return "started" if self.tried else "new"


# ---------------------------------------------------------------------------
# A pack
# ---------------------------------------------------------------------------
@dataclass
class Pack:
    root: Path
    title: str
    intro: str = ""
    exercises: list[Exercise] = field(default_factory=list)
    #: The pack's front matter: exam, time, deadline, seed, generate, boxes.
    settings: dict[str, str] = field(default_factory=dict)

    @property
    def is_exam(self) -> bool:
        return (self.settings.get("exam") or "").strip().lower() in ("yes", "true", "1", "on")

    @property
    def points(self) -> float:
        return sum(e.points for e in self.exercises)

    @property
    def name_file(self) -> Path:
        return self.root / MY_NAME

    def student(self) -> str | None:
        try:
            return self.name_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def set_student(self, name: str) -> None:
        self.name_file.write_text(name.strip() + "\n", encoding="utf-8")

    @property
    def wants_name(self) -> bool:
        """Does the pack vary per student (so it needs the student's name)?"""
        seed = (self.settings.get("seed") or "").strip().lower()
        return seed in ("student", "name", "per student") or any(
            (e.sheet.settings.get("seed") or "").strip().lower() in ("student", "name", "per student")
            for e in self.exercises)

    def chapter(self, exercise: Exercise) -> str:
        """The subfolder the exercise is in ("" at the top of the pack)."""
        relative = exercise.folder.resolve().relative_to(self.root.resolve())
        return " › ".join(relative.parts[:-1])

    def chapters(self) -> list[tuple[str, list[Exercise]]]:
        grouped: list[tuple[str, list[Exercise]]] = []
        for exercise in self.exercises:
            name = self.chapter(exercise)
            if not grouped or grouped[-1][0] != name:
                grouped.append((name, []))
            grouped[-1][1].append(exercise)
        return grouped

    def index(self, folder: str | Path) -> int | None:
        key = Path(folder).resolve()
        return next((i for i, e in enumerate(self.exercises) if e.folder.resolve() == key),
                    None)


#: The start of a lettered part: "a.", "a)", "**a.**", "(a)".
PART = re.compile(r"^\s*(?:\*\*)?\(?([a-h])[.)](?:\*\*)?\s+", re.M)
#: A part that asks for a net to be drawn or changed.
DRAWING = re.compile(r"\b(draw|change|repair|fix|model|construct)\b.*\bnet\b|"
                     r"\bnet\b.*\b(draw|change|repair|fix)\b", re.I | re.S)


def split_parts(text: str) -> list[tuple[str, str]]:
    """Markdown split at its lettered parts: ``[("", intro), ("a", "a. …"), …]``.
    A part starts a paragraph with its letter; the letters must run a, b, c…"""
    paragraphs = re.split(r"(\n\s*\n)", text)
    parts: list[tuple[str, str]] = [("", "")]
    expected = "a"
    for paragraph in paragraphs:
        match = PART.match(paragraph)
        if match and match.group(1) == expected:
            parts.append((expected, paragraph))
            expected = chr(ord(expected) + 1)
        else:
            letter, body = parts[-1]
            parts[-1] = (letter, body + paragraph)
    parts = [(letter, body.strip()) for letter, body in parts]
    if not parts[0][1]:
        parts = parts[1:]
    return parts if len(parts) > 1 or (parts and parts[0][0]) else []


def find_exercises(root: Path, depth: int = 0) -> list[Path]:
    if exercise_files(root) is not None:
        return [root]
    if depth >= MAX_DEPTH:
        return []
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()
                          and not p.name.startswith(".")), key=lambda p: natural_key(p.name))
    except OSError:
        return []
    found: list[Path] = []
    for folder in folders:
        found += find_exercises(folder, depth + 1)
    return found


def pack_root(folder: str | Path, boundary: str | Path | None = None) -> Path:
    """The pack an exercise belongs to: the nearest folder above it with a
    ``pack.md``; else ``boundary`` (the folder open in the app) when the
    exercise is inside it; else the exercise's chapter folder."""
    folder = Path(folder).resolve()
    for parent in [folder] + list(folder.parents):
        if (parent / PACK_FILE).is_file():
            return parent
        if boundary is not None and parent == Path(boundary).resolve():
            return parent
    return folder.parent if exercise_files(folder) is not None else folder


def load_pack(root: str | Path) -> Pack:
    from .sheet import parse_front_matter
    root = Path(root).resolve()
    title, intro, settings = root.name, "", {}
    for name in (PACK_FILE, "README.md"):
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                settings, text = parse_front_matter(text)
            except SheetError:
                settings = {}
            lines = text.strip().splitlines()
            if lines and lines[0].startswith("# "):
                title, intro = lines[0][2:].strip(), "\n".join(lines[1:]).strip()
            elif name == PACK_FILE:
                intro = text.strip()
            break
    title = settings.get("title") or title
    exercises = [Exercise(exercise_files(folder), settings) for folder in find_exercises(root)]
    return Pack(root, title, intro, exercises, settings)
