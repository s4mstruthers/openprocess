"""``@box``: a Python function as a box on the workflow canvas.

Writing a box is writing a function with type hints, nothing more::

    from openprocess.flow import box, EventLog, TransitionSystem

    @box(group="Discover")
    def learned_states(log: EventLog, clusters: int = 8, seed: int = 0) -> TransitionSystem:
        \"\"\"Builds a transition system by clustering prefix embeddings.\"\"\"
        ...

What the decorator reads, and nothing else:

====================================================  ========================================================
parameters whose type is an OpenProcess type          input connection points, named after the parameter
``list[Type]``                                        an input that takes any number of connections
``Type | None`` (or a default of ``None``)            an optional input
the return type                                       the output; a dataclass of typed fields gives several
``int``, ``float``, ``bool``, ``str`` with a default  a setting, with the default as its value
``Literal["a", "b"]``                                 a setting that is a choice
``Column``                                            a setting that is a column of a connected table
``Path``                                              a setting that is a file in the folder
the docstring                                         the help text; a ``name: text`` line is a setting's help
``inspect.getsource``                                 the Code tab
====================================================  ========================================================

A box has no base class and no registration call: the decorator returns a
:class:`Box`, which is still the function (call it from a script or a
notebook and it runs the same way) with a :class:`BoxSpec` attached.  A
mistake in the hints (an unknown type, a setting without a default) raises
:class:`BoxError` with a plain message at import time, which the loader
turns into a greyed-out box with the reason.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import os
import importlib.util
import re
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from .types import Column, TypeInfo, is_known, type_info

SETTING_TYPES = (int, float, bool, str)


class BoxError(ValueError):
    """The function cannot be a box; the message says why in plain words."""


@dataclass(frozen=True)
class Port:
    """A connection point: an input (a parameter) or an output."""

    name: str
    type: type | None                # None for typing.Any
    many: bool = False               # takes any number of connections (a list)
    optional: bool = False
    label: str = ""

    @property
    def info(self) -> TypeInfo | None:
        return type_info(self.type) if self.type is not None else None

    @property
    def key(self) -> str:
        info = self.info
        return info.key if info else "any"

    @property
    def type_name(self) -> str:
        info = self.info
        if info:
            return info.name
        return "Anything" if self.type is None else getattr(self.type, "__name__", str(self.type))

    def accepts(self, other: "Port") -> bool:
        """Can an output ``other`` be connected to this input?  The same type
        or a subclass of it; a ``typing.Any`` point only to another one."""
        if self.type is None or other.type is None:
            return self.type is other.type
        return issubclass(other.type, self.type)


def clean_path(value: Any) -> str:
    """A path as typed or pasted, cleaned up: surrounding quotes and spaces
    dropped, shell escapes like ``Week\\ 2`` undone, ``~`` expanded.  A path
    dragged from a terminal or copied from a shell then works as it is."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    text = text.replace("\\ ", " ")
    if text.startswith("~"):
        text = os.path.expanduser(text)
    return text


@dataclass(frozen=True)
class Setting:
    """A parameter that is not an input: shown as a control in the side panel."""

    name: str
    kind: str                        # "int", "float", "bool", "str", "choice", "path", "column"
    default: Any
    choices: tuple = ()
    help: str = ""

    def coerce(self, value: Any) -> Any:
        """A value as the GUI or a file gives it, in the setting's type.
        Raises ``ValueError`` for a value that is not one, rather than
        rounding it: ``2.5`` is not an int setting's value, ``"2"`` and
        ``2.0`` are."""
        if value is None and self.default is None and self.kind != "bool":
            return None                                  # "no value" stays no value (str | None = None)
        if self.kind == "int":
            if isinstance(value, (bool, int)):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    value = float(value)
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{self.name!r} must be a whole number, not {value!r}")
            return int(value)
        if self.kind == "float":
            return float(value)
        if self.kind == "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        if self.kind == "choice":
            if self.choices and value not in self.choices:
                for choice in self.choices:
                    if str(choice) == str(value):
                        return choice
                raise ValueError(f"{self.name!r} must be one of {', '.join(map(str, self.choices))}")
            return value
        if self.kind == "path":
            if isinstance(value, Path):
                return value
            text = clean_path(value)
            return Path(text) if text else None
        return str(value)


@dataclass
class BoxSpec:
    """Everything the app knows about a box, read from the function."""

    id: str                          # "module.function", unique in a library
    name: str                        # plain name shown on the canvas
    group: str
    function: Callable
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    settings: list[Setting] = field(default_factory=list)
    help: str = ""
    source: str = ""
    file: str = ""
    line: int = 0
    module: str = ""
    needs: tuple[str, ...] = ()      # modules that must be importable
    heavy: bool = False              # run it where it can be stopped (a process)
    custom: bool = False             # from the folder's boxes/ or a package, not OpenProcess's own
    result_type: type | None = None  # a dataclass of outputs, when there are several

    def setting(self, name: str) -> Setting | None:
        return next((s for s in self.settings if s.name == name), None)

    def input(self, name: str) -> Port | None:
        return next((p for p in self.inputs if p.name == name), None)

    def output(self, name: str = "out") -> Port | None:
        return next((p for p in self.outputs if p.name == name), None)

    def defaults(self) -> dict[str, Any]:
        return {s.name: s.default for s in self.settings}

    @property
    def description(self) -> str:
        """The docstring's prose: :attr:`help` without the ``name: text``
        lines of its settings (they are shown next to each setting) and
        without an ``Args:`` heading left with nothing under it."""
        names = {s.name for s in self.settings} | {p.name for p in self.inputs}
        lines = (self.help or "").splitlines()
        dropped = {number for key, _text, first, last in _hint_blocks(self.help) if key in names
                   for number in range(first, last + 1)}
        text = "\n".join(line for number, line in enumerate(lines) if number not in dropped)
        for heading in ("Args:", "Arguments:", "Parameters:"):
            text = re.sub(rf"(?m)^\s*{heading}\s*$\n?(?=\s*(\n|$))", "", text)
        return text.strip()

    @property
    def fingerprint(self) -> str:
        """Changes when the code changes: part of every cache key."""
        return hashlib.sha256((self.source or self.id).encode("utf-8")).hexdigest()

    def missing(self) -> list[str]:
        """The modules in ``needs`` that are not installed."""
        return [m for m in self.needs if importlib.util.find_spec(m.split(".")[0]) is None]

    @property
    def available(self) -> bool:
        return not self.missing()

    @property
    def unavailable_reason(self) -> str:
        missing = self.missing()
        return f"needs {', '.join(missing)}" if missing else ""

    def describe(self) -> str:
        """One paragraph for the command line."""
        ins = ", ".join(f"{p.name}: {p.type_name}{' (any number)' if p.many else ''}" for p in self.inputs) or "nothing"
        outs = ", ".join(p.type_name for p in self.outputs) or "nothing"
        settings = ", ".join(f"{s.name}={s.default!r}" for s in self.settings)
        first = (self.help or "").strip().split("\n")[0]
        lines = [f"{self.name}  [{self.group}]  {self.id}", f"  takes {ins}; gives {outs}"]
        if settings:
            lines.append(f"  settings: {settings}")
        if first:
            lines.append(f"  {first}")
        if not self.available:
            lines.append(f"  unavailable: {self.unavailable_reason}")
        return "\n".join(lines)


class Box:
    """The function, with its :class:`BoxSpec`.  Calling it calls the
    function; inside a ``@workflow`` being recorded it adds a node instead."""

    def __init__(self, function: Callable, spec: BoxSpec) -> None:
        self.function = function
        self.spec = spec
        self.__wrapped__ = function
        self.__name__ = function.__name__
        self.__qualname__ = getattr(function, "__qualname__", function.__name__)
        self.__doc__ = function.__doc__
        self.__module__ = function.__module__

    def __call__(self, *args, **kwargs):
        from .workflow import recording
        builder = recording()
        if builder is not None:
            return builder.call(self, args, kwargs)
        return self.function(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<box {self.spec.id}>"


# ---------------------------------------------------------------------------
# Reading the signature
# ---------------------------------------------------------------------------
def _unwrap_optional(annotation):
    """``X | None`` -> (X, True); anything else -> (annotation, False)."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1 and len(get_args(annotation)) == 2:
            return args[0], True
    return annotation, False


def _port_from(name: str, annotation, default, has_default: bool) -> Port | None:
    """An input port for a parameter, or None when it is a setting."""
    annotation, optional = _unwrap_optional(annotation)
    if has_default and default is None:
        optional = True
    origin = get_origin(annotation)
    if origin in (list, tuple, typing.List):
        args = get_args(annotation)
        inner = args[0] if args else None
        if inner is not None and (is_known(inner) or inner is Any):
            return Port(name, None if inner is Any else inner, many=True,
                        optional=optional or has_default, label=name.replace("_", " "))
        return None
    if annotation is Any:
        return Port(name, None, optional=optional, label=name.replace("_", " "))
    if isinstance(annotation, type) and is_known(annotation):
        return Port(name, annotation, optional=optional, label=name.replace("_", " "))
    return None


def _setting_from(name: str, annotation, default, has_default: bool, doc_hints: dict) -> Setting:
    annotation, _ = _unwrap_optional(annotation)
    if get_origin(annotation) is Literal:
        choices = get_args(annotation)
        if not has_default:
            default = choices[0]
        return Setting(name, "choice", default, tuple(choices), doc_hints.get(name, ""))
    if annotation is Path or annotation == "Path":
        return Setting(name, "path", default if has_default else None, help=doc_hints.get(name, ""))
    if annotation is bool:
        kind = "bool"
    elif annotation is int:
        kind = "int"
    elif annotation is float:
        kind = "float"
    elif annotation is Column:
        kind = "column"
    elif annotation is str:
        kind = "str"
    else:
        raise BoxError(
            f"parameter {name!r} has type {_type_text(annotation)}, which is neither an OpenProcess "
            f"type (an input) nor int, float, bool, str, Literal[...], Column or Path (a setting)")
    if not has_default:
        raise BoxError(f"setting {name!r} needs a default value (e.g. {name}: {kind} = ...)")
    if kind == "float" and isinstance(default, int) and not isinstance(default, bool):
        default = float(default)
    return Setting(name, kind, default, help=doc_hints.get(name, ""))


def _type_text(annotation) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def _outputs_from(annotation) -> tuple[list[Port], type | None]:
    if annotation is inspect.Signature.empty:
        raise BoxError("the function needs a return type hint (-> PetriNet), or -> None")
    if annotation is None or annotation is type(None):
        return [], None
    annotation, optional = _unwrap_optional(annotation)
    if annotation is Any:
        return [Port("out", None)], None
    if isinstance(annotation, type) and is_known(annotation):
        return [Port("out", annotation, optional=optional)], None
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        ports = []
        hints = get_type_hints(annotation)
        for f in dataclasses.fields(annotation):
            inner, optional = _unwrap_optional(hints.get(f.name, f.type))
            if not (isinstance(inner, type) and is_known(inner)) and inner is not Any:
                raise BoxError(f"result field {f.name!r} has type {_type_text(inner)}, "
                               f"which is not an OpenProcess type")
            ports.append(Port(f.name, None if inner is Any else inner, optional=optional,
                              label=f.name.replace("_", " ")))
        if not ports:
            raise BoxError(f"result class {annotation.__name__} has no fields")
        return ports, annotation
    raise BoxError(f"the return type {_type_text(annotation)} is not an OpenProcess type "
                   f"(nor a dataclass of them)")


def _doc_hints(doc: str) -> dict[str, str]:
    """``name: text`` lines of a docstring, as help for settings.  The
    Google style ``name (float): text`` is read the same way, so a function
    documented for Sphinx needs no second docstring to be a box."""
    hints: dict[str, str] = {}
    for key, text, _first, _last in _hint_blocks(doc):
        hints.setdefault(key, text)
    return hints


def _hint_blocks(doc: str):
    """``(name, help, first line, last line)`` for each ``name: text`` block of a
    docstring: the line and the lines under it until a blank line or the next
    ``name:``, so a help that goes on over several lines stays one help."""
    lines = (doc or "").splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        key, _, text = stripped.partition(":")
        key = key.split("(", 1)[0].strip()                 # "noise (float)" -> "noise"
        if ":" in stripped and not stripped.startswith(":") and key.isidentifier() and text.strip():
            parts, end = [text.strip()], index + 1
            while end < len(lines) and lines[end].strip() and not _starts_hint(lines[end]):
                parts.append(lines[end].strip())
                end += 1
            yield key, " ".join(parts), index, end - 1
            index = end
        else:
            index += 1


def _starts_hint(line: str) -> bool:
    stripped = line.strip()
    key, _, text = stripped.partition(":")
    return ":" in stripped and key.split("(", 1)[0].strip().isidentifier() and bool(text.strip())


def _plain_name(function_name: str) -> str:
    words = function_name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def make_spec(function: Callable, name: str | None = None, group: str = "Other",
              needs: str | tuple[str, ...] | None = None, heavy: bool = False,
              custom: bool = False, id: str | None = None, localns: dict | None = None) -> BoxSpec:
    """Read a function's signature into a :class:`BoxSpec` (raises :class:`BoxError`).

    ``localns`` resolves names in string annotations (``from __future__ import
    annotations``) that are local to where the function was defined."""
    try:
        hints = get_type_hints(function, localns=localns)
    except Exception as error:   # noqa: BLE001 - a bad annotation of any kind
        raise BoxError(f"could not read the type hints: {error}") from error
    signature = inspect.signature(function)
    doc = inspect.getdoc(function) or ""
    doc_hints = _doc_hints(doc)
    inputs: list[Port] = []
    settings: list[Setting] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise BoxError(f"*{parameter.name} is not allowed: every parameter must be named")
        if parameter.name not in hints:
            raise BoxError(f"parameter {parameter.name!r} needs a type hint")
        has_default = parameter.default is not parameter.empty
        port = _port_from(parameter.name, hints[parameter.name], parameter.default, has_default)
        if port is not None:
            inputs.append(port)
        else:
            settings.append(_setting_from(parameter.name, hints[parameter.name],
                                          parameter.default, has_default, doc_hints))
    outputs, result_type = _outputs_from(hints.get("return", signature.return_annotation))
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = ""
    try:
        file = inspect.getsourcefile(function) or ""
        line = inspect.getsourcelines(function)[1]
    except (OSError, TypeError):
        file, line = "", 0
    if isinstance(needs, str):
        needs = (needs,)
    module = function.__module__ or ""
    return BoxSpec(id=id or f"{module}.{function.__name__}", name=name or _plain_name(function.__name__),
                   group=group, function=function, inputs=inputs, outputs=outputs, settings=settings,
                   help=doc, source=source, file=file, line=line, module=module,
                   needs=tuple(needs or ()), heavy=heavy, custom=custom, result_type=result_type)


@dataclass(frozen=True)
class Called:
    """A function a box calls that holds the actual work: the algorithm the
    box is a thin wrapper around (see :func:`algorithm_calls`)."""

    name: str                        # "alpha_miner"
    module: str                      # "openprocess.mining.discovery.alpha"
    file: str
    line: int
    source: str

    @property
    def where(self) -> str:
        """The file as shown: ``openprocess/mining/discovery/alpha.py:86`` for the
        package's own code (whatever the folder it was cloned into is called),
        else the file's name."""
        path = Path(self.file)
        root = Path(__file__).resolve().parents[2]          # the folder holding the package
        try:
            shown = path.resolve().relative_to(root).as_posix()
        except ValueError:
            shown = path.name
        return shown + (f":{self.line}" if self.line else "")


def _resolve(expression, namespace: dict):
    """The object a call's target names, through the box's globals: ``name``
    or ``module.attribute``; None when it is not a global (a parameter, a
    result's method)."""
    import ast
    if isinstance(expression, ast.Name):
        return namespace.get(expression.id)
    if isinstance(expression, ast.Attribute):
        base = _resolve(expression.value, namespace)
        if isinstance(base, (types.ModuleType, type)):
            return getattr(base, expression.attr, None)
    return None


def algorithm_calls(spec: BoxSpec) -> list[Called]:
    """The functions the box's code calls that are defined outside the
    workflow framework and the standard library, in the order they are
    called: the algorithms the box delegates to.  The α-algorithm box, for
    example, calls ``openprocess.mining.discovery.alpha.alpha_miner``; that is
    the code to read when assessing correctness, so the Code tab shows it
    under the box's own few lines."""
    import ast
    import sysconfig
    import textwrap
    if not spec.source:
        return []
    try:
        tree = ast.parse(textwrap.dedent(spec.source))
    except SyntaxError:
        return []
    namespace = getattr(spec.function, "__globals__", {})
    stdlib = sysconfig.get_paths().get("stdlib", "")
    calls: list[tuple[int, int, Called]] = []
    seen: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve(node.func, namespace)
        if target is None:
            continue
        target = inspect.unwrap(target)
        if not inspect.isfunction(target):
            continue                                   # classes, builtins and C code are not algorithms
        module = getattr(target, "__module__", "") or ""
        if not module or module.startswith("openprocess.flow") or module == "builtins":
            continue
        key = (module, target.__qualname__)
        if key in seen:
            continue
        try:
            file = inspect.getsourcefile(target) or ""
            lines, line = inspect.getsourcelines(target)
        except (OSError, TypeError):
            continue
        if stdlib and file.startswith(stdlib):
            continue
        seen.add(key)
        calls.append((node.lineno, node.col_offset, Called(target.__name__, module, file, line, "".join(lines))))
    return [called for _line, _column, called in sorted(calls, key=lambda item: item[:2])]


def box(function: Callable | None = None, *, name: str | None = None, group: str = "Other",
        needs: str | tuple[str, ...] | None = None, heavy: bool = False):
    """Turn a function into a box.  Use it bare (``@box``) or with options
    (``@box(name="α-algorithm", group="Discover", needs="pandas")``).

    ``needs`` names modules the box imports; without them the box is listed
    greyed out with "needs pandas" instead of failing at run time.  ``heavy``
    marks a box that may take minutes, so the app runs it where it can be
    stopped.
    """
    import sys
    # Names local to the defining scope (a result dataclass defined in a function).
    frame = sys._getframe(1)
    localns = dict(frame.f_locals) if frame.f_code.co_name != "<module>" else None

    def decorate(fn: Callable) -> Box:
        spec = make_spec(fn, name=name, group=group, needs=needs, heavy=heavy, localns=localns)
        return Box(fn, spec)
    return decorate(function) if function is not None else decorate


__all__ = ["Box", "BoxError", "BoxSpec", "Port", "Setting", "box", "make_spec"]
