"""Write the parts of the documentation that are read from the code.

    python docs/make_docs.py            # rewrite them
    python docs/make_docs.py --check    # exit 1 if any is out of date (the test suite runs this)

* ``docs/architecture.md``: between its ``<!-- generated: ... -->`` markers,
  the map of the packages drawn from their actual imports, and every module
  with the first line of its docstring (no line counts: the check should
  fail when the structure changes, not on every edit);
* ``docs/boxes.md``: every standard box, read from its signature.

Nothing else in those files is touched.  A release cannot carry a stale map
or catalogue: the test suite fails until this has been run (see
"Publishing a release" in the README).
"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "openprocess"

#: The layers of the code, innermost first, and the packages in each.
#: ``layer_violations`` checks that a layer only imports its own and inner ones.
LAYERS = [
    ("Engines", "pure Python, no dependencies",
     ["mining", "mining.discovery", "mining.conformance", "ml", "model", "sim", "analysis", "io", "core"]),
    ("Framework", "workflows of boxes; no Qt", ["flow", "flow.boxes"]),
    ("Teaching", "exercise packs and checks; no Qt", ["learn", "teaching"]),
    ("App", "PySide6", ["gui", "gui.studio", "gui.flow", "gui.learn", "cli"]),
]
UNIT_NAMES = {
    "core": "top level: citation, references, packages",
    "mining": "logs, nets, analysis",
    "mining.discovery": "α, Inductive, Heuristics, regions",
    "mining.conformance": "replay, alignments, quality",
    "ml": "CPN ML language",
    "model": "coloured nets",
    "sim": "CPN simulator",
    "analysis": "CPN state space",
    "io": ".cpn files",
    "flow": "boxes, workflows, runner, record",
    "flow.boxes": "the standard boxes",
    "learn": "exercise packs",
    "teaching": "old name of learn",
    "gui": "net editor, canvas",
    "gui.studio": "the Studio window and pages",
    "gui.flow": "the workflow canvas",
    "gui.learn": "Learn mode",
    "cli": "the openprocess command",
}


# ---------------------------------------------------------------------------
# Reading the code
# ---------------------------------------------------------------------------
def modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts and "exercises" not in p.parts)


def dotted(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def unit_of(module: str) -> str:
    """The package a module belongs to on the map (``openprocess.mining.xes`` → ``mining``)."""
    parts = module.split(".")[1:]
    if not parts or parts[0] in ("citation", "references", "packages", "__init__"):
        return "core"
    if parts[0] == "cli":
        return "cli"
    if parts[0] in ("mining", "flow", "gui") and len(parts) > 1 and (PACKAGE / parts[0] / parts[1]).is_dir():
        return f"{parts[0]}.{parts[1]}"
    return parts[0]


def imports_of(path: Path) -> set[str]:
    """The ``openprocess`` modules ``path`` imports (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    here = dotted(path).split(".")
    if path.name != "__init__.py":
        here = here[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("openprocess"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[:len(here) - node.level + 1]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if target.startswith("openprocess"):
                found.add(target)
                found.update(f"{target}.{alias.name}" for alias in node.names
                             if (PACKAGE.parent / Path(*f"{target}.{alias.name}".split("."))).with_suffix(".py").exists()
                             or (PACKAGE.parent / Path(*f"{target}.{alias.name}".split("."))).is_dir())
    return found


def first_line(path: Path) -> str:
    doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    line = doc.strip().split("\n\n")[0].replace("\n", " ")
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= 140 else line[:137].rstrip() + "…"


def unit_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in modules():
        source = unit_of(dotted(path))
        for target in imports_of(path):
            if target == "openprocess":
                continue
            unit = unit_of(target)
            if unit != source:
                graph[source].add(unit)
    return graph


def layer_of(unit: str) -> int:
    for index, (_name, _what, units) in enumerate(LAYERS):
        if unit in units:
            return index
    raise KeyError(unit)


def layer_violations() -> list[str]:
    """Imports that go outwards (an engine importing the framework, the
    framework importing the app): what the design rules out."""
    problems = []
    for path in modules():
        source = unit_of(dotted(path))
        for target in sorted(imports_of(path)):
            if target == "openprocess":
                continue
            unit = unit_of(target)
            if layer_of(unit) > layer_of(source):
                problems.append(f"{dotted(path)} imports {target} ({LAYERS[layer_of(source)][0]} → "
                                f"{LAYERS[layer_of(unit)][0]})")
    return problems


# ---------------------------------------------------------------------------
# Writing it out
# ---------------------------------------------------------------------------
def _node(unit: str) -> str:
    return unit.replace(".", "_")


def package_map() -> str:
    graph = unit_graph()
    lines = ["```mermaid", "flowchart BT"]
    for name, what, units in LAYERS:
        lines.append(f'  subgraph {name.lower()}["{name} · {what}"]')
        for unit in units:
            lines.append(f'    {_node(unit)}["<b>{unit}</b><br/>{UNIT_NAMES[unit]}"]')
        lines.append("  end")
    for source in sorted(graph):
        for target in sorted(graph[source]):
            lines.append(f"  {_node(source)} --> {_node(target)}")
    lines.append("```")
    return "\n".join(lines)


def module_table() -> str:
    by_unit: dict[str, list[Path]] = defaultdict(list)
    for path in modules():
        by_unit[unit_of(dotted(path))].append(path)
    out = []
    for name, _what, units in LAYERS:
        for unit in units:
            if unit not in by_unit:
                continue
            out += [f"### `{unit}` · {UNIT_NAMES[unit]}", "", "| Module | What it is |", "|---|---|"]
            for path in by_unit[unit]:
                relative = path.relative_to(ROOT).as_posix()
                summary = first_line(path).replace("|", "\\|")
                out.append(f"| [`{relative.removeprefix('openprocess/')}`](../{relative}) | {summary} |")
            out.append("")
    out.append(f"{len(modules())} modules.")
    return "\n".join(out)


def box_catalogue() -> str:
    sys.path.insert(0, str(ROOT))
    from openprocess.flow.library import GROUP_ORDER, Library
    library = Library().load_standard()
    lines = ["# The boxes", "",
             "Every box OpenProcess comes with, read from its signature by `docs/make_docs.py` (do not edit",
             "by hand). A box is a Python function; *Code* in the app shows it and the algorithm it calls.",
             "How to write your own: [workflows.md](workflows.md#writing-a-box); what a box *is*, formally:",
             "[framework.md](framework.md).", ""]
    groups = library.by_group()
    lines.append(" · ".join(f"[{g}](#{g.lower().replace(' ', '-')})" for g in groups if g in GROUP_ORDER))
    lines.append("")
    lines.append(f"{len(library)} boxes.")
    for group, specs in groups.items():
        lines += ["", f"## {group}", "", "| Box | Takes | Gives | Settings | Needs |", "|---|---|---|---|---|"]
        for spec in specs:
            takes = ", ".join(f"{p.name}: {p.type_name}{' (any number)' if p.many else ''}"
                              f"{' (optional)' if p.optional and not p.many else ''}" for p in spec.inputs) or "–"
            gives = ", ".join(f"{p.name}: {p.type_name}" if len(spec.outputs) > 1 else p.type_name
                              for p in spec.outputs) or "–"
            settings = ", ".join(f"`{s.name}`" + (f" ∈ {{{', '.join(map(str, s.choices))}}}" if s.choices
                                                   else f" = {s.default!r}") for s in spec.settings) or "–"
            summary = spec.description.split("\n\n")[0].replace("\n", " ")
            summary = summary if len(summary) <= 160 else summary[:157].rstrip() + "…"
            where = Path(spec.file).relative_to(ROOT).as_posix() if spec.file else ""
            summary, settings = summary.replace("|", "\\|"), settings.replace("|", "\\|")
            needs = ", ".join(spec.needs) or "–"
            lines.append(f"| **[{spec.name}](../{where}#L{spec.line})**<br/>{summary} | "
                         f"{takes} | {gives} | {settings} | {needs} |")
    return "\n".join(lines) + "\n"


def fill(text: str, name: str, content: str) -> str:
    """Put ``content`` between ``<!-- generated: name -->`` and ``<!-- /generated -->``."""
    pattern = re.compile(rf"(<!-- generated: {name} -->\n).*?(<!-- /generated -->)", re.S)
    if not pattern.search(text):
        raise ValueError(f"no generated section {name!r}")
    return pattern.sub(lambda m: m.group(1) + content.rstrip("\n") + "\n" + m.group(2), text)


def outputs() -> dict[Path, str]:
    architecture = ROOT / "docs" / "architecture.md"
    text = architecture.read_text(encoding="utf-8")
    text = fill(text, "package-map", package_map())
    text = fill(text, "modules", module_table())
    return {architecture: text, ROOT / "docs" / "boxes.md": box_catalogue()}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for path, text in outputs().items():
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        if check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(text, encoding="utf-8")
            print(f"Wrote {path.relative_to(ROOT)}")
    problems = layer_violations()
    for problem in problems:
        print(f"layering: {problem}", file=sys.stderr)
    if stale:
        print("Out of date: " + ", ".join(stale) + ". Run: python docs/make_docs.py", file=sys.stderr)
    return 1 if (stale or problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
