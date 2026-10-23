"""The key figure of a result: one number and a line, for a tile or a report.

Shared by the Summary (:mod:`openprocess.gui.flow.summary`) and the HTML
report (:mod:`openprocess.flow.report`), so both say the same thing about a
result: a score's first metric, a table's size, a log's cases, a net's
places and transitions, a map's activities and paths.
"""

from __future__ import annotations

from .types import (DFG, OCDFG, OCEL, AlignmentResult, EventLog, Figure, PetriNet, ReplayResult, Scores,
                    Table, Text)


def number(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def tile_parts(value) -> tuple[str, str] | None:
    """(the big figure, the line under it) for a result, or None for a result
    that has no figure to show (a trace, a prediction model, …)."""
    if isinstance(value, Scores):
        items = [(k, v) for k, v in value.metrics.items() if not isinstance(v, (dict, list))]
        if not items:
            return None
        first, rest = items[0], items[1:4]
        return (f"{number(first[1])} {first[0]}".strip(),
                " · ".join(f"{k} {number(v)}" for k, v in rest) or value.model)
    if isinstance(value, Table):
        return f"{len(value.rows):,} × {len(value.columns)}", f"rows × columns · {value.name}"
    if isinstance(value, Figure):
        return "figure", value.caption or value.name
    if isinstance(value, EventLog):
        return f"{len(value):,} cases", f"{value.event_count:,} events"
    if isinstance(value, PetriNet):
        return f"{len(value.places)} · {len(value.transitions)}", "places · transitions"
    if isinstance(value, DFG):
        return f"{len(value.activities)} · {len(value.all_edges())}", "activities · paths"
    if isinstance(value, OCEL):
        return f"{len(value):,} events", f"{len(value.objects):,} objects of {len(value.object_types)} types"
    if isinstance(value, OCDFG):
        return f"{len(value.activities)} · {len(value.object_types)}", "activities · object types"
    if isinstance(value, ReplayResult):
        return f"{value.fitness:.2f} fitness", f"{value.fitting_traces:,} of {value.trace_count:,} cases fit"
    if isinstance(value, AlignmentResult):
        return (f"{value.average_fitness:.2f} fitness",
                f"{value.fitting_traces:,} of {value.trace_count:,} cases align")
    if isinstance(value, Text):
        first = str(value).strip().splitlines()[0] if str(value).strip() else ""
        return first[:40], value.name
    import dataclasses
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # A box with several outputs: the key figure of its first output that has one.
        for item in dataclasses.fields(value):
            parts = tile_parts(getattr(value, item.name))
            if parts is not None:
                return parts[0], f"{item.name.replace('_', ' ')} · {parts[1]}"
    return None


__all__ = ["number", "tile_parts"]
