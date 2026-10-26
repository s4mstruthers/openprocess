"""Output boxes: save a result into the folder, next to the workflow."""

from __future__ import annotations

from pathlib import Path

from ... import flow
from ...mining.bpmn import BPMN
from ...mining.log import EventLog
from ...mining.petrinet import PetriNet
from ..box import box
from ..types import Figure, Table


def _target(file: str) -> Path:
    path = Path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@box(name="Save as PNML", group="Output")
def save_pnml(model: PetriNet, file: str = "model.pnml") -> None:
    """Writes the model as PNML (ProM, WoPeD and PM4Py read it)."""
    from ...mining.pnml import write_pnml
    write_pnml(model, _target(file))
    flow.note(f"Wrote {file}")


@box(name="Save as BPMN", group="Output")
def save_bpmn(model: BPMN, file: str = "model.bpmn") -> None:
    """Writes the model as BPMN 2.0 XML with a diagram (Camunda Modeler,
    bpmn.io and Signavio open it)."""
    from ...mining.bpmn import write_bpmn
    write_bpmn(model, _target(file))
    flow.note(f"Wrote {file}")


@box(name="Save log", group="Output")
def save_log(log: EventLog, file: str = "log.xes") -> None:
    """Writes the log as XES (``.xes`` or ``.xes.gz``) or CSV (``.csv``)."""
    path = _target(file)
    if path.suffix.lower() == ".csv":
        from ...mining.csv_import import write_csv
        write_csv(log, path)
    else:
        from ...mining.xes import write_xes
        write_xes(log, path)
    flow.note(f"Wrote {file}: {len(log)} cases")


@box(name="Save table", group="Output")
def save_table(table: Table, file: str = "table.csv") -> None:
    """Writes a table as CSV."""
    table.to_csv(_target(file))
    flow.note(f"Wrote {file}: {len(table)} rows")


@box(name="Save figure", group="Output")
def save_figure(figure: Figure, file: str = "figure.svg") -> None:
    """Writes a figure as SVG or PNG, by the file's extension."""
    figure.save(_target(file))
    flow.note(f"Wrote {file}")
