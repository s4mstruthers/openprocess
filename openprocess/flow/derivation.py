"""A box's derivation (the *How* tab) as Markdown or LaTeX, for papers and notes.

The How tab is an :class:`~.explain.Explanation`: notes, derivation steps
and intermediate values, in the order the box reported them.  The app shows
it, the HTML report renders it (:mod:`.report`); this module writes the same
content as

* **Markdown** (:func:`to_markdown`): a table of steps, bullet notes, and the
  values as Markdown tables -- for lecture notes, a README, a chat; and
* **LaTeX** (:func:`to_latex`): ``tabular`` environments and ``itemize``
  lists, with the symbols the steps use (→ × ∧ ↺ τ ≫ …) as maths commands,
  for a paper.

:func:`value_markdown` and :func:`value_latex` render one result on their
own (a footprint, a table, the scores of a model), and :func:`derivation`
gives the whole thing with a title and, when ``spec`` is given, the sources
the algorithm follows, so a pasted derivation carries its citation.
"""

from __future__ import annotations

from typing import Any

from .explain import Explanation
from .figures import number
from .types import (DFG, OCDFG, OCEL, AlignmentResult, EventLog, Figure, Footprint, PetriNet, ProcessTree,
                    ReplayResult, Scores, Table, Text, TransitionSystem)

#: The symbols derivations use, and how LaTeX writes them (in maths mode).
LATEX_SYMBOLS = {
    "→": r"\rightarrow", "←": r"\leftarrow", "‖": r"\parallel", "#": r"\#", "×": r"\times",
    "∧": r"\wedge", "↺": r"\circlearrowleft", "τ": r"\tau", "≫": r"\gg", "⟨": r"\langle ",
    "⟩": r"\rangle", "∅": r"\emptyset", "α": r"\alpha", "∈": r"\in", "⇒": r"\Rightarrow",
    "·": r"\cdot", "ω": r"\omega", "∞": r"\infty", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
}
_TEXT_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                 "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _md_cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_table(columns: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(_md_cell(c) for c in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(_md_cell(number(c) if isinstance(c, (int, float)) else c) for c in row) + " |"
            for row in rows]
    return "\n".join([head, rule, *body])


def value_markdown(value: Any, limit: int = 50) -> str:
    """One result as Markdown: a table where it is tabular, a line otherwise."""
    if isinstance(value, Scores):
        rows = [[k, v] for k, v in value.metrics.items() if not isinstance(v, (dict, list))]
        text = _md_table([value.model, ""], rows)
        return text + (f"\n\n*{value.note}*" if value.note else "")
    if isinstance(value, Table):
        text = _md_table([str(c) for c in value.columns], value.rows[:limit])
        if len(value.rows) > limit:
            text += f"\n\n*… {len(value.rows) - limit:,} more rows.*"
        return text
    if isinstance(value, Footprint):
        return _md_table(["", *value.activities],
                         [[a, *row] for a, row in zip(value.activities, value.matrix())])
    if isinstance(value, EventLog):
        from ..mining.stats import summarise
        summary = summarise(value)
        rows = [[f"⟨{', '.join(v.sequence)}⟩", v.count] for v in summary.variants[:limit]]
        text = (f"{summary.case_count:,} cases · {summary.event_count:,} events · "
                f"{summary.variant_count:,} variants · {summary.activity_count} activities\n\n"
                + _md_table(["Variant", "Cases"], rows))
        if summary.variant_count > limit:
            text += f"\n\n*… {summary.variant_count - limit:,} more variants.*"
        return text
    if isinstance(value, PetriNet):
        return value.summary()
    if isinstance(value, ProcessTree):
        return f"`{value}`"
    if isinstance(value, DFG):
        rows = [[a, b, n] for a, b, n in sorted(value.all_edges(), key=lambda e: -e[2])[:limit]]
        return (f"{len(value.activities)} activities · {len(value.all_edges())} paths\n\n"
                + _md_table(["From", "To", "Times"], rows))
    if isinstance(value, TransitionSystem):
        return f"{value.summary()}\n\n```\n{value.to_text()}\n```"
    if isinstance(value, ReplayResult):
        rows = [[f"⟨{', '.join(t.trace)}⟩", t.count, t.produced, t.consumed, t.missing, t.remaining]
                for t in value.traces[:limit]]
        return (f"Fitness {value.fitness:.4f} (p = {value.produced}, c = {value.consumed}, "
                f"m = {value.missing}, r = {value.remaining})\n\n"
                + _md_table(["Variant", "Cases", "p", "c", "m", "r"], rows))
    if isinstance(value, AlignmentResult):
        rows = [[f"⟨{', '.join(a.trace)}⟩", a.count, a.cost, f"{a.fitness:.3f}"] for a in value.alignments[:limit]]
        return (f"Fitness {value.average_fitness:.4f}; {value.fitting_traces:,} of {value.trace_count:,} cases "
                f"align without a deviation\n\n" + _md_table(["Variant", "Cases", "Cost", "Fitness"], rows))
    if isinstance(value, OCEL):
        return value.summary()
    if isinstance(value, OCDFG):
        return (f"{len(value.activities)} activities · {len(value.object_types)} object types: "
                + ", ".join(value.object_types))
    if isinstance(value, Figure):
        return f"*Figure: {value.caption or value.name}*"
    if isinstance(value, Text):
        return str(value)
    if value is None:
        return "*Nothing (the box writes a file, or has no output).*"
    summary = getattr(value, "summary", None)
    if callable(summary):
        try:
            return str(summary())
        except Exception:  # noqa: BLE001 - a result's own method
            pass
    return f"*{type(value).__name__}*"


def to_markdown(explanation: Explanation, value: Any = None, title: str = "") -> str:
    """The How tab as Markdown: steps as a two-column table, notes as bullets,
    intermediate values as tables; ``value`` (the box's result) at the end."""
    out: list[str] = []
    if title:
        out.append(f"## {title}")
    for entry in explanation.entries:
        if entry[0] == "note":
            out.append(f"- {entry[1]}")
        elif entry[0] == "steps":
            out.append(_md_table(["Step", ""], [[t, f"`{c}`" if c else ""] for t, c in entry[1]]))
        elif entry[0] == "show":
            if entry[1]:
                out.append(f"**{entry[1]}**")
            out.append(value_markdown(entry[2]))
    if value is not None:
        out.append("**Result**")
        out.append(value_markdown(value))
    return "\n\n".join(part for part in out if part) + "\n"


# ---------------------------------------------------------------------------
# LaTeX
# ---------------------------------------------------------------------------
def latex_text(text: Any) -> str:
    """Plain text for a LaTeX document: special characters escaped, the
    derivation's symbols as maths."""
    out = []
    for char in str(text):
        if char in LATEX_SYMBOLS:
            out.append(f"${LATEX_SYMBOLS[char]}$")
        elif char in _TEXT_ESCAPES:
            out.append(_TEXT_ESCAPES[char])
        else:
            out.append(char)
    return "".join(out).replace("$$", "")


def _latex_table(columns: list[str], rows: list[list[Any]], caption: str = "") -> str:
    spec = "l" * len(columns)
    lines = [r"\begin{tabular}{" + spec + "}", r"\toprule",
             " & ".join(f"\\textbf{{{latex_text(c)}}}" for c in columns) + r" \\", r"\midrule"]
    for row in rows:
        lines.append(" & ".join(latex_text(number(c) if isinstance(c, (int, float)) else c) for c in row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    if caption:
        lines.append(f"\\par\\noindent\\textit{{{latex_text(caption)}}}")
    return "\n".join(lines)


def value_latex(value: Any, limit: int = 50) -> str:
    """One result as LaTeX (``booktabs`` tables, verbatim for text)."""
    if isinstance(value, Scores):
        rows = [[k, v] for k, v in value.metrics.items() if not isinstance(v, (dict, list))]
        return _latex_table([value.model, ""], rows, value.note)
    if isinstance(value, Table):
        more = f"… {len(value.rows) - limit:,} more rows." if len(value.rows) > limit else ""
        return _latex_table([str(c) for c in value.columns], value.rows[:limit], more)
    if isinstance(value, Footprint):
        return _latex_table(["", *value.activities],
                            [[a, *row] for a, row in zip(value.activities, value.matrix())])
    if isinstance(value, EventLog):
        from ..mining.stats import summarise
        summary = summarise(value)
        rows = [[f"⟨{', '.join(v.sequence)}⟩", v.count] for v in summary.variants[:limit]]
        return _latex_table(["Variant", "Cases"], rows,
                            f"{summary.case_count:,} cases, {summary.event_count:,} events, "
                            f"{summary.variant_count:,} variants, {summary.activity_count} activities")
    if isinstance(value, ProcessTree):
        return "$" + "".join(LATEX_SYMBOLS.get(c, c) if c in LATEX_SYMBOLS else _TEXT_ESCAPES.get(c, c)
                             for c in str(value)) + "$"
    if isinstance(value, DFG):
        rows = [[a, b, n] for a, b, n in sorted(value.all_edges(), key=lambda e: -e[2])[:limit]]
        return _latex_table(["From", "To", "Times"], rows)
    if isinstance(value, ReplayResult):
        rows = [[f"⟨{', '.join(t.trace)}⟩", t.count, t.produced, t.consumed, t.missing, t.remaining]
                for t in value.traces[:limit]]
        return _latex_table(["Variant", "Cases", "$p$", "$c$", "$m$", "$r$"], rows,
                            f"Fitness {value.fitness:.4f}")
    if isinstance(value, AlignmentResult):
        rows = [[f"⟨{', '.join(a.trace)}⟩", a.count, a.cost, f"{a.fitness:.3f}"] for a in value.alignments[:limit]]
        return _latex_table(["Variant", "Cases", "Cost", "Fitness"], rows, f"Fitness {value.average_fitness:.4f}")
    if isinstance(value, TransitionSystem):
        return "\\begin{verbatim}\n" + value.to_text() + "\n\\end{verbatim}"
    if isinstance(value, Text):
        return "\\begin{verbatim}\n" + str(value) + "\n\\end{verbatim}"
    return latex_text(value_markdown(value, limit).replace("*", ""))


def to_latex(explanation: Explanation, value: Any = None, title: str = "") -> str:
    """The How tab as LaTeX: steps in a ``tabular`` (needs ``booktabs``), notes
    in an ``itemize``, values as tables."""
    out: list[str] = ["% Derivation exported from OpenProcess; \\usepackage{booktabs} for the tables."]
    if title:
        out.append(f"\\subsection*{{{latex_text(title)}}}")
    notes: list[str] = []

    def flush_notes() -> None:
        if notes:
            out.append("\\begin{itemize}\n" + "\n".join(f"  \\item {latex_text(n)}" for n in notes)
                       + "\n\\end{itemize}")
            notes.clear()
    for entry in explanation.entries:
        if entry[0] == "note":
            notes.append(entry[1])
            continue
        flush_notes()
        if entry[0] == "steps":
            out.append(_latex_table(["Step", ""], [[t, c] for t, c in entry[1]]))
        elif entry[0] == "show":
            if entry[1]:
                out.append(f"\\paragraph{{{latex_text(entry[1])}}}")
            out.append(value_latex(entry[2]))
    flush_notes()
    if value is not None:
        out.append("\\paragraph{Result}")
        out.append(value_latex(value))
    return "\n\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# With the citation
# ---------------------------------------------------------------------------
def sources_of(spec) -> list:
    """The works the box's algorithms follow (:class:`~openprocess.references.Reference`
    objects), from the calls the box makes into the engine."""
    from .. import references
    from .box import algorithm_calls
    found: list = []
    try:
        called = algorithm_calls(spec)
    except Exception:  # noqa: BLE001 - a box whose source cannot be read
        called = []
    modules = {c.module for c in called} | {spec.module}
    for module in sorted(modules):
        for topic in references.topics_for_module(module):
            for key in topic.sources:
                reference = references.reference(key)
                if reference is not None and all(reference.key != r.key for r in found):
                    found.append(reference)
    return found


def derivation(explanation: Explanation, value: Any = None, title: str = "", spec=None,
               fmt: str = "markdown") -> str:
    """The derivation in ``fmt`` (``markdown`` or ``latex``) with, when the
    box's ``spec`` is given, the sources its algorithms follow -- so what is
    pasted into notes or a paper carries its citation."""
    render = to_latex if fmt.lower() in ("latex", "tex") else to_markdown
    text = render(explanation, value, title)
    if spec is not None:
        found = sources_of(spec)
        if found:
            if render is to_latex:
                from .. import citation
                text += "\n% Sources the algorithm follows (BibTeX):\n" + "\n".join(
                    "% " + line for r in found for line in citation.entry(r).bibtex().splitlines()) + "\n"
            else:
                text += "\n**Sources**\n\n" + "\n".join(
                    f"- {r.citation}" + (f" <{r.url}>" if r.url else "") for r in found) + "\n"
    return text


__all__ = ["LATEX_SYMBOLS", "derivation", "latex_text", "sources_of", "to_latex", "to_markdown",
           "value_latex", "value_markdown"]
