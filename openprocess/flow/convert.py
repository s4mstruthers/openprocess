"""Converters between OpenProcess's types and other libraries' objects.

A box that wraps a pandas, PM4Py or matplotlib function asks for the
conversion in one line::

    df = convert(log, "pandas.DataFrame")       # one row per event
    return convert(fig, Figure)                  # a matplotlib figure as SVG

Foreign types are named by their dotted path, so this module imports none
of them until a conversion is asked for.  Register your own with
:func:`register`: a function from one type to another, keyed by the source
class and the target (a class, or a dotted name).
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Callable

from ..mining.log import KEY_NAME, KEY_RESOURCE, KEY_TIME, EventLog, SimpleLog
from ..mining.petrinet import PetriNet
from .types import Figure, Table

_converters: dict[tuple[str, str], Callable] = {}


def _name(target) -> str:
    """The name a type is registered under: ``module.qualname`` for a class,
    a dotted string as given.  Nothing is imported here, so registering a
    converter for ``"pandas.core.frame.DataFrame"`` costs nothing when pandas
    is absent, and importing this module never imports pandas."""
    if isinstance(target, str):
        return target
    return f"{target.__module__}.{target.__qualname__}"


def _resolve(dotted: str):
    """The class a dotted name points at, or None when it cannot be found.

    Tries the longest module prefix first (``a.b.c.D`` as module ``a.b.c``,
    attribute ``D``), then shorter ones, so both ``pandas.DataFrame`` and
    ``pandas.core.frame.DataFrame`` resolve to the same class.  A name whose
    top-level package is not installed resolves to None without importing
    anything.
    """
    parts = dotted.split(".")
    if len(parts) < 2 or importlib.util.find_spec(parts[0]) is None:
        return None
    for split in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            obj = getattr(obj, attribute, None)
            if obj is None:
                break
        else:
            if isinstance(obj, type):
                return obj
    return None


def _canonical(name: str) -> str:
    """``name`` as the class it points at is defined (``module.qualname``),
    or ``name`` itself when it points at nothing importable."""
    resolved = _resolve(name)
    return name if resolved is None else _name(resolved)


def register(source, target, function: Callable) -> None:
    """``function(value) -> converted`` for a value of class ``source``."""
    _converters[(_name(source), _name(target))] = function


def _class_names(value) -> list[str]:
    return [f"{cls.__module__}.{cls.__qualname__}" for cls in type(value).__mro__]


def _find(value: Any, target) -> Callable | None:
    """The registered converter from ``value``'s class (or a base class) to
    ``target``, or None.

    A class may be named by where it is imported from (``"pandas.DataFrame"``)
    or where it is defined (``"pandas.core.frame.DataFrame"``, which pandas
    2 reports as the class's module, while pandas 3 reports ``"pandas"``).
    Both the source and the target are matched by resolving the names to the
    class.  Only registered names in the same top-level package as the value
    or the target are resolved, so looking up a pandas conversion imports
    pandas and nothing else, and only once a conversion is asked for.
    """
    wanted = _name(target)
    wanted_names = {wanted, _canonical(wanted)}
    target_package = wanted.split(".")[0]
    value_names = set(_class_names(value))
    value_package = type(value).__module__.split(".")[0]

    def matches(registered: str, names: set[str], package: str) -> bool:
        if registered in names:
            return True
        return registered.split(".")[0] == package and _canonical(registered) in names

    for (source, registered), function in _converters.items():
        if matches(source, value_names, value_package) and \
                matches(registered, wanted_names, target_package):
            return function
    return None


def can_convert(value: Any, target) -> bool:
    return _name(target) in _class_names(value) or _find(value, target) is not None


def convert(value: Any, target):
    """Convert ``value`` to ``target`` (a class or a dotted name)."""
    wanted = _name(target)
    if wanted in _class_names(value):
        return value
    function = _find(value, target)
    if function is None:
        raise TypeError(f"No converter from {type(value).__name__} to {wanted}")
    return function(value)


def converters() -> list[tuple[str, str]]:
    return sorted(_converters)


# ---------------------------------------------------------------------------
# Built-in converters
# ---------------------------------------------------------------------------
def _simple_to_log(simple: SimpleLog) -> EventLog:
    return EventLog.from_simple_log(simple)


def _log_to_simple(log: EventLog) -> SimpleLog:
    return log.simple_log()


def _log_to_dataframe(log: EventLog):
    pd = importlib.import_module("pandas")
    rows = []
    for trace in log:
        case = trace.case_id
        for event in trace:
            row = {"case:concept:name": case}
            row.update(event.attributes)
            rows.append(row)
    frame = pd.DataFrame(rows)
    if KEY_TIME in frame.columns:
        frame[KEY_TIME] = pd.to_datetime(frame[KEY_TIME], utc=True, errors="coerce")
    return frame


def _dataframe_to_log(frame) -> EventLog:
    from ..mining.log import Event, Trace
    case_column = "case:concept:name" if "case:concept:name" in frame.columns else frame.columns[0]
    log = EventLog(attributes={KEY_NAME: "DataFrame"})
    for case, group in frame.groupby(case_column, sort=False):
        trace = Trace({KEY_NAME: str(case)})
        for record in group.drop(columns=[case_column]).to_dict("records"):
            attributes = {k: (v.to_pydatetime() if hasattr(v, "to_pydatetime") else v)
                          for k, v in record.items() if v == v}       # drop NaN
            trace.events.append(Event(attributes))
        log.traces.append(trace)
    return log


def _table_to_dataframe(table: Table):
    pd = importlib.import_module("pandas")
    return pd.DataFrame(table.rows, columns=table.columns)


def _dataframe_to_table(frame) -> Table:
    columns = [str(c) for c in frame.columns]
    rows = [[_plain(v) for v in row] for row in frame.itertuples(index=False, name=None)]
    return Table("DataFrame", columns, rows)


def _series_to_table(series) -> Table:
    name = str(series.name or "value")
    return Table(name, ["index", name], [[_plain(k), _plain(v)] for k, v in series.items()])


def _plain(value):
    item = getattr(value, "item", None)          # NumPy scalars -> Python
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):
            return value
    return value


def _ndarray_to_table(array) -> Table:
    rows = array.tolist()
    if rows and not isinstance(rows[0], list):
        rows = [[r] for r in rows]
    width = len(rows[0]) if rows else 0
    return Table("array", [f"c{i}" for i in range(width)], rows)


def _mpl_figure_to_figure(fig) -> Figure:
    import io
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg", bbox_inches="tight")
    svg = buffer.getvalue()
    png = io.BytesIO()
    try:
        fig.savefig(png, format="png", dpi=160, bbox_inches="tight")
        png_bytes = png.getvalue()
    except Exception:    # noqa: BLE001 - no PNG backend
        png_bytes = None
    title = ""
    try:
        title = fig.axes[0].get_title() if fig.axes else ""
    except Exception:    # noqa: BLE001
        pass
    return Figure(title or "Figure", svg=svg, png=png_bytes)


def _petri_to_pm4py(net: PetriNet):
    import os
    import tempfile
    pm4py = importlib.import_module("pm4py")
    from ..mining.pnml import write_pnml
    handle, path = tempfile.mkstemp(suffix=".pnml")
    os.close(handle)
    try:
        write_pnml(net, path)
        return pm4py.read_pnml(path)
    finally:
        os.unlink(path)


def _pm4py_to_petri(triple) -> PetriNet:
    from ..mining.pm4py_bridge import _to_native
    net, im, fm = triple
    return _to_native(net, im, fm, getattr(net, "name", None) or "PM4Py net")


register(SimpleLog, EventLog, _simple_to_log)
register(EventLog, SimpleLog, _log_to_simple)
register(EventLog, "pandas.core.frame.DataFrame", _log_to_dataframe)
register("pandas.core.frame.DataFrame", EventLog, _dataframe_to_log)
register(Table, "pandas.core.frame.DataFrame", _table_to_dataframe)
register("pandas.core.frame.DataFrame", Table, _dataframe_to_table)
register("pandas.core.series.Series", Table, _series_to_table)
register("numpy.ndarray", Table, _ndarray_to_table)
register("matplotlib.figure.Figure", Figure, _mpl_figure_to_figure)
register(PetriNet, "pm4py", _petri_to_pm4py)
register(tuple, "openprocess.mining.petrinet.PetriNet", _pm4py_to_petri)

__all__ = ["can_convert", "convert", "converters", "register"]
