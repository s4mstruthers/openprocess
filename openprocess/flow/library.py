"""Where boxes come from: OpenProcess's own, a folder's ``boxes/``, and packages.

A :class:`Library` holds :class:`~.box.BoxSpec` objects by id.  The
standard library is OpenProcess's own boxes (``openprocess.flow.boxes``).  On top of
it, :meth:`Library.load_folder` reads every ``.py`` file in a folder (the
open folder's ``boxes/`` subfolder in the app) and
:meth:`Library.load_entry_points` every installed package that declares a
``openprocess.boxes`` entry point, so a research group can publish a box pack on
PyPI.

A file with a mistake does not stop the others: it is kept as a
:class:`BrokenBox` with the reason in plain words, and the app lists it
greyed out.  A box whose library is missing (``needs="pandas"``) is listed
too, greyed out, with "needs pandas".
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .box import Box, BoxError, BoxSpec

#: The modules of the standard library, in the order *+ Add box* shows their groups
#: (the core groups first).
STANDARD_MODULES = ("input", "filter", "discover", "check", "measure", "compare", "output",
                    "science", "predict", "cpn", "sweeps")
GROUP_ORDER = ("Input", "Filter", "Discover", "Check", "Measure", "Compare", "Output", "Science",
               "Predict", "Coloured nets", "Sweep", "Yours")


@dataclass
class BrokenBox:
    """A box file that could not be loaded, and why."""

    file: str
    reason: str
    name: str = ""

    @property
    def id(self) -> str:
        return f"broken:{self.file}"


class Library:
    def __init__(self) -> None:
        self.specs: dict[str, BoxSpec] = {}
        self.broken: list[BrokenBox] = []
        #: Where each custom box file came from, with its content hash (for the record).
        self.files: dict[str, str] = {}

    # -- lookup -------------------------------------------------------------------
    def get(self, box_id: str) -> BoxSpec:
        try:
            return self.specs[box_id]
        except KeyError:
            raise KeyError(f"No box {box_id!r}") from None

    def resolve(self, box) -> BoxSpec:
        """A :class:`Box`, a :class:`BoxSpec`, an id or a bare function name."""
        if isinstance(box, Box):
            if box.spec.id not in self.specs:
                self.register(box)
            return box.spec
        if isinstance(box, BoxSpec):
            if box.id not in self.specs:
                self.specs[box.id] = box
            return box
        if box in self.specs:
            return self.specs[box]
        matches = [s for s in self.specs.values() if s.function.__name__ == box or s.name == box]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"No box {box!r}")
        raise KeyError(f"Several boxes are called {box!r}: " + ", ".join(m.id for m in matches))

    def __contains__(self, box_id: str) -> bool:
        return box_id in self.specs

    def __iter__(self):
        return iter(self.specs.values())

    def __len__(self) -> int:
        return len(self.specs)

    def by_group(self) -> dict[str, list[BoxSpec]]:
        groups: dict[str, list[BoxSpec]] = {}
        for spec in self.specs.values():
            groups.setdefault(spec.group, []).append(spec)
        ordered = {g: groups[g] for g in GROUP_ORDER if g in groups}
        ordered.update({g: specs for g, specs in groups.items() if g not in ordered})
        return ordered

    # -- registering -----------------------------------------------------------------
    def register(self, *boxes: Box | BoxSpec, custom: bool | None = None) -> None:
        for item in boxes:
            spec = item.spec if isinstance(item, Box) else item
            if custom is not None:
                spec.custom = custom
            self.specs[spec.id] = spec

    def register_module(self, module, custom: bool = False) -> list[BoxSpec]:
        """Register the boxes *defined* in ``module`` (a box it merely imports,
        such as a standard box used by a saved group, stays as it is)."""
        from .workflow import Recorded
        own = module.__name__

        def defined_here(value) -> bool:
            fn = value.fn if isinstance(value, Recorded) else value.spec.function
            return getattr(fn, "__module__", own) == own

        found = [value for value in vars(module).values() if isinstance(value, Box) and defined_here(value)]
        for value in vars(module).values():
            if isinstance(value, Recorded) and defined_here(value):   # a @workflow function with typed parameters
                box = value.as_box()
                if box is not None:
                    box.spec.id = f"{module.__name__}.{value.__name__}"
                    found.append(box)
        found.sort(key=lambda b: b.spec.line)
        self.register(*found, custom=custom)
        return [b.spec for b in found]

    def load_standard(self) -> "Library":
        for name in STANDARD_MODULES:
            module = importlib.import_module(f"openprocess.flow.boxes.{name}")
            self.register_module(module)
        return self

    def load_file(self, path: str | Path) -> list[BoxSpec]:
        """Load one box file.  Raises nothing: a mistake becomes a BrokenBox."""
        path = Path(path)
        text = path.read_bytes()
        self.files[str(path)] = hashlib.sha256(text).hexdigest()
        module_name = "openprocess_boxes_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
        self.broken = [b for b in self.broken if b.file != str(path)]
        for spec_id in [s for s, spec in self.specs.items() if spec.file == str(path)]:
            del self.specs[spec_id]
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except BoxError as error:
            self.broken.append(BrokenBox(str(path), str(error), path.stem))
            return []
        except SyntaxError as error:
            self.broken.append(BrokenBox(str(path), f"line {error.lineno}: {error.msg}", path.stem))
            return []
        except Exception as error:   # noqa: BLE001 - whatever the file did
            detail = "".join(traceback.format_exception_only(type(error), error)).strip()
            self.broken.append(BrokenBox(str(path), detail, path.stem))
            return []
        specs = self.register_module(module, custom=True)
        for s in specs:
            s.file = str(path)
            s.group = s.group or "Yours"
        if not specs:
            self.broken.append(BrokenBox(str(path), "the file has no @box function", path.stem))
        return specs

    def load_folder(self, folder: str | Path) -> list[BoxSpec]:
        """Every ``.py`` file directly in ``folder`` (``boxes/`` in the app)."""
        folder = Path(folder)
        if not folder.is_dir():
            return []
        loaded = []
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            loaded.extend(self.load_file(path))
        return loaded

    def load_entry_points(self, group: str = "openprocess.boxes") -> list[BoxSpec]:
        """Boxes from installed packages that declare ``[project.entry-points."openprocess.boxes"]``."""
        loaded = []
        try:
            points = importlib.metadata.entry_points(group=group)
        except TypeError:                       # Python 3.9 style
            points = importlib.metadata.entry_points().get(group, [])
        for point in points:
            try:
                module = point.load()
            except Exception as error:   # noqa: BLE001
                self.broken.append(BrokenBox(point.value, f"package {point.name}: {error}", point.name))
                continue
            specs = self.register_module(module, custom=True)
            for s in specs:
                s.group = s.group or point.name
            loaded.extend(specs)
        return loaded


_standard: Library | None = None


def standard_library() -> Library:
    """OpenProcess's own boxes (loaded once)."""
    global _standard
    if _standard is None:
        _standard = Library().load_standard()
    return _standard


def library_for(folder: str | Path | None = None, packages: bool = True) -> Library:
    """The standard boxes plus a folder's ``boxes/`` and installed packages."""
    library = Library().load_standard()
    if packages:
        library.load_entry_points()
    if folder is not None:
        boxes = Path(folder) / "boxes" if (Path(folder) / "boxes").is_dir() else Path(folder)
        library.load_folder(boxes)
    return library


__all__ = ["BrokenBox", "GROUP_ORDER", "Library", "library_for", "standard_library"]
