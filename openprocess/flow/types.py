"""The types boxes exchange.

A box's connection points are typed, and the types are the ones OpenProcess
already uses: an event log, a Petri net, a transition system, ...  This
module re-exports them so a box author imports from one place, adds the few
small types that only workflows need (scores, tables, figures, datasets),
and keeps the registry the app uses to draw a connection point: its short
key, its plain name and its colour.

Registering a type of your own::

    from openprocess.flow.types import register_type

    class Embedding: ...
    register_type(Embedding, "embedding", "Embedding")

A box may also take or give ``typing.Any``: such a connection point is grey,
connects only to a point of the same declared class, and is shown with a
generic viewer.  That is how a *Train encoder* box feeds an *Embed prefixes*
box without OpenProcess knowing what an encoder is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..mining.conformance.alignments import AlignmentResult
from ..mining.conformance.token_replay import ReplayResult
from ..mining.dfg import DFG
from ..mining.ocel import OCDFG, OCEL
from ..mining.footprint import Footprint
from ..mining.log import EventLog, SimpleLog
from ..mining.petrinet import Marking, PetriNet
from ..mining.processtree import ProcessTree
from ..mining.regions import RegionAnalysis as Regions
from ..mining.transition_system import TransitionSystem
from ..model.net import CPNet


# ---------------------------------------------------------------------------
# Small types that only workflows need
# ---------------------------------------------------------------------------
@dataclass
class Scores:
    """Named numbers about one model: the row of a Compare table.

    ``metrics`` keeps insertion order; a value may be a number, a string
    (``"Sound"``) or a boolean.  ``context`` holds the settings a sweep gave
    the boxes that produced it (``{"noise": 0.2}``), so stacked scores can be
    told apart.
    """

    model: str
    metrics: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, name: str) -> Any:
        return self.metrics[name]

    def items(self):
        return self.metrics.items()


@dataclass
class Table:
    """Anything tabular: rows of values under column names.

    ``samples`` is set by boxes that produce a distribution (the bootstrap):
    the raw numbers behind the summary rows, for tests and histograms.
    """

    name: str
    columns: list[str]
    rows: list[list[Any]] = field(default_factory=list)
    samples: list[float] | None = None
    note: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_scores(cls, scores: Iterable[Scores], name: str = "Scores") -> "Table":
        """One row per :class:`Scores`, the columns being every metric seen
        (and every context key, so a sweep's values become columns)."""
        scores = list(scores)
        context_keys: list[str] = []
        metric_keys: list[str] = []
        for s in scores:
            for key in s.context:
                if key not in context_keys:
                    context_keys.append(key)
            for key in s.metrics:
                if key not in metric_keys:
                    metric_keys.append(key)
        columns = ["model", *context_keys, *metric_keys]
        rows = [[s.model, *[s.context.get(k) for k in context_keys],
                 *[s.metrics.get(k) for k in metric_keys]] for s in scores]
        return cls(name, columns, rows)

    def column(self, name: str) -> list[Any]:
        index = self.columns.index(name)
        return [row[index] for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)

    def to_csv(self, path) -> None:
        """Write the table as CSV, with where it came from in a
        ``.provenance.json`` beside it when the table knows."""
        import csv
        from .provenance import of, write_sidecar
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.columns)
            writer.writerows(self.rows)
        write_sidecar(path, of(self))



class Column(str):
    """The name of a column of a table connected to the box.

    A setting typed ``Column`` (``x: Column = ""``) is a plain string to the
    function.  In the app it is a list of the connected table's columns to
    pick from, once the box before it has run; a name can still be typed.
    It is a setting, not an input: the table is the input.
    """


@dataclass
class Figure:
    """A picture: SVG text, or PNG bytes, as a box made it."""

    name: str = "Figure"
    svg: str | None = None
    png: bytes | None = None
    caption: str = ""

    def save(self, path) -> None:
        """Write the figure; where it came from goes with it (SVG: a
        ``<metadata>`` block; PNG: a ``.provenance.json`` beside it)."""
        from .provenance import of, with_metadata, write_sidecar
        path = str(path)
        info = of(self)
        if self.svg is not None and path.lower().endswith(".svg"):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(with_metadata(self.svg, info))
        elif self.png is not None:
            with open(path, "wb") as handle:
                handle.write(self.png)
            write_sidecar(path, info)
        else:
            raise ValueError(f"This figure has no {path.rsplit('.', 1)[-1].upper()} form.")


@dataclass
class Text:
    """A report or any other text a box produces."""

    text: str
    name: str = "Text"

    def __str__(self) -> str:
        return self.text


@dataclass
class Dataset:
    """Prefixes of a log encoded for prediction: one row per prefix.

    ``X`` holds one feature vector per row (plain lists, or a NumPy array if
    the box that made it used NumPy), ``y`` the label of each row.  ``kind``
    is ``"classification"`` (next activity, outcome) or ``"regression"``
    (remaining time, in hours).  ``case_ids`` and ``prefix_lengths`` say
    where each row came from, so evaluation can be shown per prefix length
    and a split can be made by case.
    """

    X: Any
    y: list[Any]
    feature_names: list[str]
    label: str
    kind: str = "classification"
    case_ids: list[str] = field(default_factory=list)
    prefix_lengths: list[int] = field(default_factory=list)
    case_starts: list[Any] = field(default_factory=list)
    activities: list[str] = field(default_factory=list)
    classes: list[str] | None = None
    name: str = "Dataset"

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, indices: Iterable[int], name: str | None = None) -> "Dataset":
        indices = list(indices)
        X = self.X
        rows = [X[i] for i in indices]
        try:                                           # keep a NumPy array a NumPy array
            import numpy as np
            if isinstance(X, np.ndarray):
                rows = X[indices]
        except ImportError:
            pass
        return Dataset(rows, [self.y[i] for i in indices], list(self.feature_names), self.label,
                       self.kind, [self.case_ids[i] for i in indices] if self.case_ids else [],
                       [self.prefix_lengths[i] for i in indices] if self.prefix_lengths else [],
                       [self.case_starts[i] for i in indices] if self.case_starts else [],
                       list(self.activities), self.classes, name or self.name)


@dataclass
class Predictions:
    """What a model predicted for every row of a dataset."""

    values: list[Any]
    dataset: Dataset
    name: str = "Predictions"

    def __len__(self) -> int:
        return len(self.values)


class Predictor:
    """What a prediction model must be able to do to sit between *Prefixes*
    and *Evaluate predictions*: ``fit`` on a dataset and ``predict`` the
    labels of another.  Subclass it, or wrap any object that has the two
    methods (scikit-learn estimators fit the shape already)."""

    name = "Predictor"

    def fit(self, dataset: Dataset) -> "Predictor":
        raise NotImplementedError

    def predict(self, dataset: Dataset) -> list[Any]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TypeInfo:
    cls: type
    key: str          # short id used in files and the GUI, e.g. "log"
    name: str         # plain name, e.g. "Log"
    colour: str       # a hex colour for the connection point
    python: str       # the name a box author writes, e.g. "EventLog"


#: Every type a connection point can have, by class.
TYPES: dict[type, TypeInfo] = {}
_BY_KEY: dict[str, TypeInfo] = {}


def register_type(cls: type, key: str, name: str, colour: str = "#86868b",
                  python: str | None = None) -> TypeInfo:
    """Make ``cls`` usable as a connection point type."""
    info = TypeInfo(cls, key, name, colour, python or cls.__name__)
    TYPES[cls] = info
    _BY_KEY[key] = info
    return info


def type_info(cls) -> TypeInfo | None:
    """The registry entry for ``cls`` (or a subclass of a registered type)."""
    if cls in TYPES:
        return TYPES[cls]
    if isinstance(cls, type):
        for known, info in TYPES.items():
            if issubclass(cls, known):
                return info
    return None


def type_by_key(key: str) -> TypeInfo | None:
    return _BY_KEY.get(key)


def is_known(cls) -> bool:
    return type_info(cls) is not None or cls is Any


for _cls, _key, _name, _colour in (
    (EventLog, "log", "Log", "#0a84ff"),
    (SimpleLog, "simple_log", "Simple log", "#0a84ff"),
    (PetriNet, "net", "Petri net", "#2e9e4f"),
    (CPNet, "cpn", "Coloured Petri net", "#2e9e4f"),
    (Marking, "marking", "Marking", "#2e9e4f"),
    (ProcessTree, "tree", "Process tree", "#2e9e4f"),
    (TransitionSystem, "ts", "Transition system", "#b0430f"),
    (DFG, "dfg", "Directly-follows graph", "#b0430f"),
    (OCEL, "ocel", "Object-centric log", "#0f6e9e"),
    (OCDFG, "ocdfg", "Object-centric map", "#b0430f"),
    (Footprint, "footprint", "Footprint", "#b0430f"),
    (ReplayResult, "replay", "Replay", "#8250c8"),
    (AlignmentResult, "alignments", "Alignments", "#8250c8"),
    (Regions, "regions", "Regions", "#b0430f"),
    (Scores, "scores", "Scores", "#8250c8"),
    (Table, "table", "Table", "#5a7d8c"),
    (Figure, "figure", "Figure", "#c9871b"),
    (Text, "text", "Text", "#5a7d8c"),
    (Dataset, "dataset", "Dataset", "#5a7d8c"),
    (Predictions, "predictions", "Predictions", "#5a7d8c"),
    (Predictor, "predictor", "Prediction model", "#86868b"),
):
    register_type(_cls, _key, _name, _colour)

__all__ = [
    "Column",
    "AlignmentResult", "Any", "CPNet", "DFG", "Dataset", "EventLog", "Figure", "Footprint", "OCDFG", "OCEL",
    "Marking", "PetriNet", "Predictions", "Predictor", "ProcessTree", "Regions", "ReplayResult",
    "Scores", "SimpleLog", "Table", "Text", "TransitionSystem", "TypeInfo", "TYPES",
    "is_known", "register_type", "type_by_key", "type_info",
]
