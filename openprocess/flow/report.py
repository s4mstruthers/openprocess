"""An analysis as one HTML page: for a supervisor, a reviewer, a hand-in.

:func:`html_report` writes everything a reader needs and nothing they have
to install: the key figure of every box, each box's result (a drawing when
the app made one, a table, a score), how it got there (the box's own
report), the code it ran and the papers it follows, what it read (with
fingerprints), whether it is reproducible, and how to cite.  One file, no
external assets, readable in any browser and printable.

The page is built from the workflow and its run alone; drawings (SVG) are
passed in by the app, which has the canvas to render them.  So the same
report can be made without the app (``openprocess run analysis.cpnflow
--report report.html``), minus the pictures.
"""

from __future__ import annotations

import base64
import datetime as _dt
from html import escape
from pathlib import Path

from .. import citation
from .box import algorithm_calls
from .figures import number, tile_parts
from .record import Record, make_record
from .runner import DONE, Run
from .types import DFG, OCDFG, OCEL, EventLog, Figure, Footprint, PetriNet, ProcessTree, Scores, Table, Text
from .workflow import Workflow

CSS = """
:root { --ink: #1d1d1f; --muted: #6e6e73; --line: #e1e1e6; --soft: #f5f5f7; --accent: #0a84ff;
        --good: #0ca30c; --warn: #c77800; --bad: #d12b2b; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink); font: 15px/1.55 -apple-system, "Helvetica Neue",
       Helvetica, Arial, sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 36px 24px 72px; }
h1 { font-size: 28px; margin: 0 0 4px; } h2 { font-size: 20px; margin: 40px 0 8px; }
h3 { font-size: 16px; margin: 24px 0 6px; } p { margin: 6px 0; } .muted { color: var(--muted); }
.caption { color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }
.badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 10px;
         color: #fff; background: var(--muted); vertical-align: middle; }
.badge.good { background: var(--good); } .badge.warning { background: var(--warn); color: #1d1d1f; }
.badge.critical { background: var(--bad); }
.tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0 4px; }
.tile { border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; min-width: 160px; }
.tile .big { font-size: 22px; font-weight: 600; }
.box { border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; margin: 16px 0; }
.box h3 { margin-top: 0; } .eyebrow { color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .07em; }
table { border-collapse: collapse; font-size: 13px; margin: 8px 0; max-width: 100%; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
pre { background: var(--soft); border-radius: 8px; padding: 12px 14px; overflow-x: auto; font-size: 12px;
      line-height: 1.45; }
code { font-size: 12.5px; background: var(--soft); padding: 1px 4px; border-radius: 4px; }
svg { max-width: 100%; height: auto; }
details { margin: 10px 0; } summary { cursor: pointer; color: var(--accent); font-weight: 500; }
.steps td:first-child { white-space: nowrap; color: var(--muted); font-weight: 600; }
.mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
@media print { main { padding: 0; } details { break-inside: avoid; } .box { break-inside: avoid; } }
"""


def html_report(workflow: Workflow, run: Run | None, folder=None, drawings: dict[str, str] | None = None,
                record: Record | None = None, status=None, title: str | None = None) -> str:
    """The analysis as a self-contained HTML page.

    ``drawings``: SVG text per box id (the app renders nets, maps and trees);
    ``status``: a :class:`~openprocess.flow.reproducibility.Status`, when known.
    """
    drawings = drawings or {}
    record = record or make_record(workflow, run, folder, lock=False)
    name = title or workflow.name or "Analysis"
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<title>{escape(name)} — OpenProcess report</title><style>{CSS}</style></head><body><main>",
             f"<div class='caption'>OpenProcess report</div><h1>{escape(name)}</h1>",
             f"<p class='muted'>{len(workflow.nodes)} box{'es' if len(workflow.nodes) != 1 else ''} · "
             f"made {escape(when)} with OpenProcess {escape(record.versions.get('openprocess', ''))}, "
             f"Python {escape(record.versions.get('python', ''))}.</p>"]
    if status is not None:
        parts.append(f"<p><span class='badge {escape(status.tone)}'>{escape(status.headline)}</span> "
                     f"<span class='muted'>{escape(' '.join(status.details))}</span></p>")
    parts.append(_summary(workflow, run))
    parts.append("<h2>The boxes, in the order they run</h2>")
    for node in workflow.order():
        parts.append(_box_section(workflow, run, node, drawings.get(node.id)))
    parts.append(_inputs_section(record))
    parts.append(_cite_section(workflow))
    parts.append("</main></body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _summary(workflow: Workflow, run: Run | None) -> str:
    tiles = []
    for node in workflow.order():
        result = run.result(node) if run is not None else None
        if result is None or result.status != DONE:
            continue
        parts = tile_parts(result.value)
        if parts is None:
            continue
        big, sub = parts
        tiles.append(f"<div class='tile'><div class='eyebrow'>{escape(workflow.spec(node).group.upper())}</div>"
                     f"<div class='muted'>{escape(workflow.title(node.id))}</div><div class='big'>{escape(big)}</div>"
                     f"<div class='muted'>{escape(sub)}</div></div>")
    if not tiles:
        return "<p class='muted'>No results: the analysis has not run.</p>"
    return ("<h2>Summary</h2><p class='muted'>The key figure of every box, as the box reported it.</p>"
            f"<div class='tiles'>{''.join(tiles)}</div>")


def _box_section(workflow: Workflow, run: Run | None, node, drawing: str | None) -> str:
    spec = workflow.spec(node)
    result = run.result(node) if run is not None else None
    settings = ", ".join(f"{k} = {escape(str(v))}" for k, v in node.settings.items()) or "no settings"
    fed = ", ".join(f"{escape(port)} ← {escape(workflow.title(src))}"
                    for port, sources in workflow.inputs_of(node).items() for src, _ in sources) or "no inputs"
    out = [f"<section class='box' id='{escape(node.id)}'>",
           f"<div class='eyebrow'>{escape(spec.group.upper())}</div><h3>{escape(workflow.title(node.id))}</h3>",
           f"<p class='muted'>{escape(spec.help.split(chr(10))[0]) if spec.help else ''}</p>",
           f"<p><b>Settings:</b> {settings}. <b>Inputs:</b> {fed}.</p>"]
    if result is None:
        out.append("<p class='muted'>Not run.</p>")
    elif result.status != DONE:
        out.append(f"<p><span class='badge critical'>{escape(result.status)}</span> "
                   f"{escape(result.error or result.message or '')}</p>")
    else:
        out.append("<div class='caption'>Result</div>")
        if drawing:
            out.append(drawing)
        out.append(_value_html(result.value))
        explanation = getattr(result, "explanation", None)
        if explanation is not None and not explanation.empty:
            out.append("<details><summary>How it got there</summary>" + _explanation_html(explanation) + "</details>")
    out.append("<details><summary>The code it ran</summary>")
    out.append(f"<p class='muted'>The box: <code>{escape(spec.id)}</code></p><pre>{escape(spec.source or '')}</pre>")
    for called in algorithm_calls(spec):
        out.append(f"<p class='muted'>The algorithm: <code>{escape(called.module)}.{escape(called.name)}</code> "
                   f"({escape(called.where)})</p><pre>{escape(called.source or '')}</pre>")
        entries = citation.entries_for_module(called.module)
        if entries:
            out.append("<p class='muted'>Follows: " + "; ".join(
                escape(_one_line(e)) for e in entries) + "</p>")
    out.append("</details></section>")
    return "\n".join(out)


def _one_line(entry: citation.Entry) -> str:
    f = entry.fields
    who = f.get("author") or f.get("organization") or ""
    venue = f.get("journal") or f.get("booktitle") or f.get("publisher") or f.get("school") or f.get("institution") or ""
    return f"{who}. {f.get('title', '')}. {venue}, {f.get('year', '')}.".replace(" .", ".").replace(", .", ".")


def _value_html(value) -> str:
    if isinstance(value, Scores):
        rows = "".join(f"<tr><td>{escape(str(k))}</td><td>{escape(number(v))}</td></tr>"
                       for k, v in value.metrics.items() if not isinstance(v, (dict, list)))
        note = f"<p class='muted'>{escape(value.note)}</p>" if value.note else ""
        return f"<table><tr><th>{escape(value.model)}</th><th></th></tr>{rows}</table>{note}"
    if isinstance(value, Table):
        head = "".join(f"<th>{escape(str(c))}</th>" for c in value.columns)
        body = "".join("<tr>" + "".join(f"<td>{escape(number(cell) if isinstance(cell, (int, float)) else str(cell))}</td>"
                                        for cell in row) + "</tr>" for row in value.rows[:200])
        more = f"<p class='muted'>… {len(value.rows) - 200:,} more rows.</p>" if len(value.rows) > 200 else ""
        return f"<table><tr>{head}</tr>{body}</table>{more}"
    if isinstance(value, Figure):
        if value.svg:
            return value.svg + (f"<p class='muted'>{escape(value.caption)}</p>" if value.caption else "")
        if value.png:
            data = base64.b64encode(value.png).decode("ascii")
            return f"<img alt='{escape(value.name)}' src='data:image/png;base64,{data}'>"
        return ""
    if isinstance(value, EventLog):
        from ..mining.stats import summarise
        summary = summarise(value)
        rows = "".join(f"<tr><td class='mono'>⟨{escape(', '.join(v.sequence))}⟩</td><td>{v.count:,}</td></tr>"
                       for v in summary.variants[:20])
        more = (f"<p class='muted'>… {summary.variant_count - 20:,} more variants.</p>"
                if summary.variant_count > 20 else "")
        return (f"<p>{summary.case_count:,} cases · {summary.event_count:,} events · "
                f"{summary.variant_count:,} variants · {summary.activity_count} activities</p>"
                f"<table><tr><th>Variant</th><th>Cases</th></tr>{rows}</table>{more}")
    if isinstance(value, PetriNet):
        return f"<p>{escape(value.summary())}</p>"
    if isinstance(value, ProcessTree):
        return f"<p class='mono'>{escape(str(value))}</p>"
    if isinstance(value, DFG):
        return f"<p>{len(value.activities)} activities · {len(value.all_edges())} paths</p>"
    if isinstance(value, OCEL):
        rows = "".join(f"<tr><td>{escape(t)}</td><td>{n:,}</td></tr>" for t, n in value.counts_by_type().items())
        return f"<p>{escape(value.summary())}</p><table><tr><th>Object type</th><th>Objects</th></tr>{rows}</table>"
    if isinstance(value, OCDFG):
        rows = "".join(f"<tr><td>{escape(t)}</td><td>{escape(a)}</td><td>{escape(b)}</td><td>{n:,}</td></tr>"
                       for t in value.object_types for (a, b), n in value.edges[t].most_common())
        return (f"<p>{escape(value.summary())}</p><table><tr><th>Object type</th><th>From</th><th>To</th>"
                f"<th>Objects</th></tr>{rows}</table>")
    if isinstance(value, Footprint):
        matrix = value.matrix()
        head = "".join(f"<th>{escape(a)}</th>" for a in value.activities)
        body = "".join(f"<tr><th>{escape(a)}</th>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
                       for a, row in zip(value.activities, matrix))
        return f"<table><tr><th></th>{head}</tr>{body}</table>"
    if isinstance(value, Text):
        return f"<pre>{escape(str(value))}</pre>"
    if value is None:
        return "<p class='muted'>Nothing (the box writes a file, or has no output).</p>"
    return f"<p class='muted'>{escape(type(value).__name__)}</p>"


def _explanation_html(explanation) -> str:
    out = []
    for entry in explanation.entries:
        if entry[0] == "note":
            out.append(f"<p class='muted'>· {escape(entry[1])}</p>")
        elif entry[0] == "steps":
            rows = "".join(f"<tr><td>{escape(title)}</td><td class='mono'>{escape(content)}</td></tr>"
                           for title, content in entry[1])
            out.append(f"<table class='steps'>{rows}</table>")
        elif entry[0] == "show":
            if entry[1]:
                out.append(f"<div class='caption'>{escape(str(entry[1]))}</div>")
            out.append(_value_html(entry[2]))
    return "\n".join(out)


def _inputs_section(record: Record) -> str:
    out = ["<h2>What it read</h2>"]
    if record.inputs:
        rows = "".join(f"<tr><td>{escape(i['file'])}</td><td class='mono'>{escape(i['sha256'][:16])}…</td></tr>"
                       for i in record.inputs)
        out.append(f"<table><tr><th>File</th><th>sha256</th></tr>{rows}</table>")
    else:
        out.append("<p class='muted'>No files: every input was typed or simulated.</p>")
    if record.custom_boxes:
        rows = "".join(f"<tr><td>{escape(b['file'])}</td><td class='mono'>{escape(b['sha256'][:16])}…</td></tr>"
                       for b in record.custom_boxes)
        out.append(f"<p>Your own boxes:</p><table><tr><th>File</th><th>sha256</th></tr>{rows}</table>")
    if record.seeds:
        out.append("<p class='muted'>Seeds: " + ", ".join(f"{escape(str(k))} = {escape(str(v))}"
                                                           for k, v in record.seeds.items()) + "</p>")
    return "\n".join(out)


def _cite_section(workflow: Workflow) -> str:
    entries = [citation.app_entry()]
    for node in workflow.order():
        for called in algorithm_calls(workflow.spec(node)):
            for item in citation.entries_for_module(called.module):
                if all(item.key != e.key for e in entries):
                    entries.append(item)
    bib = "\n\n".join(e.bibtex() for e in entries)
    return ("<h2>How to cite</h2>"
            f"<p>{escape(citation.app_text())}</p>"
            f"<details><summary>BibTeX, with the papers the algorithms follow</summary><pre>{escape(bib)}</pre></details>")


def write_report(path: str | Path, **kwargs) -> Path:
    """Write :func:`html_report` to ``path``."""
    path = Path(path)
    path.write_text(html_report(**kwargs), encoding="utf-8")
    return path


__all__ = ["html_report", "write_report", "CSS"]
