"""The ``.cpnflow`` file: a workflow, and the record that makes it reproducible.

A workflow file is JSON: the boxes, their settings and positions, the
connections, the groups, and a **record**:

* the versions of OpenProcess, Python and the optional libraries;
* the environment: every installed package and its version (the lock);
* a fingerprint (SHA-256) of every input file and every custom box file;
* every seed;
* a fingerprint of every box's result from the last run.

**Re-run** compares the record with what is on disk now and says exactly
what differs before running.  ``openprocess run --check`` re-runs headless and
fails if a result's fingerprint differs from the recorded one: run it in a
paper's CI and the passing badge is the reproducibility claim.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import importlib.metadata
import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..mining.log import EventLog, SimpleLog, format_simple_log
from ..mining.petrinet import PetriNet
from .runner import DONE, Run
from .types import Dataset, Figure, Predictions, Scores, Table, Text
from .workflow import Workflow

FORMAT = "cpnflow/1"


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


#: (resolved path, size, modification time) -> SHA-256: a file is hashed again only when it changed.
_file_hashes: dict[tuple[str, int, int], str] = {}


def sha256_file(path: str | Path) -> str:
    """The file's SHA-256.  Remembered while its size and modification time
    stay the same, so the badge can check a 700 MB input after every run
    without reading it again."""
    stat = Path(path).stat()
    key = (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)
    known = _file_hashes.get(key)
    if known is not None:
        return known
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    _file_hashes[key] = digest.hexdigest()
    return _file_hashes[key]


#: How results are fingerprinted.  Scheme 1 (up to 0.11) hashed some types
#: by ``repr``, which follows the order of a set and so changed from one
#: Python session to the next, and hashed a replay or an alignment by its net
#: alone.  Scheme 2 hashes a canonical form of the whole content.  A record
#: says which scheme it was made with, and is checked with that one.
FINGERPRINT = 2


def fingerprint(value: Any, scheme: int = FINGERPRINT, remember: bool = False) -> str:
    """A code that changes if and only if the value's content changes.

    Scheme 2 (the default) hashes :func:`canonical`: the value as plain
    JSON, with sets and dictionaries sorted, floats rounded to 9 digits and
    drawing-only details (positions, label offsets) left out, so the same
    content gives the same code in every session and on every machine.

    ``remember``: for a run's results, which do not change after their box
    returned them (§9 of docs/framework.md), the code is kept for as long as
    the value lives, so a snapshot or a save after every run does not hash a
    million-event log again.  Never for a value that may still change (strict
    mode compares a box's inputs before and after it ran)."""
    if scheme == 1:
        return _fingerprint_v1(value)
    if remember:
        known = _remembered.get(id(value))
        if known is not None and known[0]() is value:
            return known[1]
    digest = _log_fingerprint(value) if isinstance(value, EventLog) else sha256_text(_dumps(canonical(value)))
    if remember:
        _remember(value, digest)
    return digest


#: id(value) -> (weak reference to it, its scheme-2 fingerprint), for
#: ``fingerprint(..., remember=True)``; the entry goes when the value does.
_remembered: dict[int, tuple] = {}


def _remember(value, digest: str) -> None:
    import weakref
    try:
        reference = weakref.ref(value, lambda _ref, key=id(value): _remembered.pop(key, None))
    except TypeError:                               # an int, a str, a tuple: cheap to hash again
        return
    _remembered[id(value)] = (reference, digest)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _log_fingerprint(log: EventLog) -> str:
    """Every case and every event attribute, in log order, streamed (a log
    can have a million events)."""
    if log.columnar:
        return _columnar_log_fingerprint(log)
    digest = hashlib.sha256()
    digest.update(_dumps(["EventLog", canonical(log.attributes)]).encode("utf-8"))
    for trace in log:
        digest.update(b"\n" + _dumps(canonical(trace.attributes)).encode("utf-8"))
        for event in trace:
            digest.update(b"|" + _dumps(canonical(event.attributes)).encode("utf-8"))
    return digest.hexdigest()


def _columnar_log_fingerprint(log: EventLog) -> str:
    """:func:`_log_fingerprint` of a columnar log, read from its arrays: the
    same bytes are hashed, so the same digest comes out, without building a
    trace or an event (on a million-event log, seconds instead of half a
    minute, and no gigabyte of objects kept).  The JSON of every distinct
    value and key is made once, and a map's pairs are put in the order of
    their keys' JSON, which is the order :func:`canonical` sorts them in
    (keys of one map are distinct, and no key's JSON is a prefix of another's)."""
    from datetime import timedelta, timezone
    from ..mining.columns import KEY_LIFECYCLE, KEY_NAME, KEY_RESOURCE, KEY_TIME, NO_TIME, _EPOCH
    store = log.traces.store
    texts: dict[tuple[type, Any], str] = {}

    def text(value) -> str:
        if type(value) in (str, int, float, bool) or value is None:
            key = (type(value), repr(value) if type(value) is float else value)     # -0.0 is not 0.0
            found = texts.get(key)
            if found is None:
                found = texts[key] = _dumps(canonical(value))
            return found
        return _dumps(canonical(value))

    def as_map(pairs: list[tuple[str, str]]) -> bytes:
        pairs.sort()
        return ('{"map":[' + ",".join(f"[{k},{v}]" for k, v in pairs) + "]}").encode("utf-8")

    standard = [(KEY_NAME, store.activity), (KEY_LIFECYCLE, store.lifecycle), (KEY_RESOURCE, store.resource)]
    standard = [(text(key), codes.codes, [text(v) for v in codes.values]) for key, codes in standard]
    time_key = text(KEY_TIME)
    extras = [(text(key), column) for key, column in store.extra.items()]
    zones: dict[int, Any] = {}
    digest = hashlib.sha256()
    digest.update(_dumps(["EventLog", canonical(log.attributes)]).encode("utf-8"))
    offsets, micros, minutes = store.offsets, store.micros, store.offset_minutes
    for case in range(store.case_count):
        attributes = zip(store.case_keys[case], store.case_values[case])
        digest.update(b"\n" + as_map([(text(k), text(v)) for k, v in dict(attributes).items()]))
        for i in range(offsets[case], offsets[case + 1]):
            pairs = []
            for key, codes, values in standard:
                code = codes[i]
                if code >= 0:
                    pairs.append((key, values[code]))
            if micros[i] != NO_TIME:
                zone = zones.get(minutes[i])
                if zone is None:
                    zone = zones[minutes[i]] = _EPOCH.astimezone(timezone(timedelta(minutes=minutes[i])))
                pairs.append((time_key, '"' + (zone + timedelta(microseconds=micros[i])).isoformat() + '"'))
            for key, column in extras:
                value = column[i]
                if value is not None:
                    pairs.append((key, text(value)))
            digest.update(b"|" + as_map(pairs))
    return digest.hexdigest()


#: Attributes that say how a thing is drawn or where it came from, not what it is.
_NOT_CONTENT = {"provenance", "position", "name_offset", "points", "info", "source_path"}


def canonical(value: Any, _active: set[int] | None = None) -> Any:
    """``value`` as plain JSON whose text does not depend on the order of
    sets, on memory addresses or on how the value is drawn: what
    :func:`fingerprint` hashes."""
    import dataclasses
    import datetime
    import math
    import re
    from enum import Enum
    from collections.abc import Mapping
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        return {"bytes": sha256_bytes(value)}
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return {"seconds": round(value.total_seconds(), 6)}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if type(value).__module__.split(".")[0] == "numpy":
        to_list = getattr(value, "tolist", None)
        if callable(to_list):
            return canonical(to_list(), _active)
    _active = _active if _active is not None else set()
    if id(value) in _active:                        # a value that contains itself
        return "<cycle>"
    _active.add(id(value))
    try:
        if isinstance(value, PetriNet):
            return {"type": "PetriNet", "name": value.name,
                    "places": sorted([p.id, p.name] for p in value.places.values()),
                    "transitions": sorted([t.id, t.name, t.label] for t in value.transitions.values()),
                    "arcs": sorted([a.source, a.target, a.weight] for a in value.arcs),
                    "initial": sorted(value.initial_marking.items()),
                    "final": sorted(value.final_marking.items())}
        if isinstance(value, Mapping):
            items = [[canonical(k, _active), canonical(v, _active)] for k, v in value.items()]
            return {"map": sorted(items, key=_dumps)}
        if isinstance(value, (set, frozenset)):
            return {"set": sorted((canonical(v, _active) for v in value), key=_dumps)}
        if isinstance(value, (list, tuple)):
            return [canonical(v, _active) for v in value]
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            fields = {f.name: canonical(getattr(value, f.name, None), _active)
                      for f in dataclasses.fields(value) if f.name not in _NOT_CONTENT}
            return {"type": type(value).__name__, **fields}
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict):
            return {"type": type(value).__name__,
                    **{k: canonical(v, _active) for k, v in sorted(attributes.items())
                       if not k.startswith("_") and k not in _NOT_CONTENT}}
        # No content to read: the text, without the memory addresses Python puts in it.
        return {"type": type(value).__name__, "repr": re.sub(r"0x[0-9a-fA-F]+", "0x…", repr(value))}
    finally:
        _active.discard(id(value))


def _fingerprint_v1(value: Any) -> str:
    """Scheme 1, kept unchanged so records made before 0.12 are checked as they were made."""
    if isinstance(value, EventLog):
        digest = hashlib.sha256()
        for trace in value:
            digest.update(trace.case_id.encode("utf-8", "replace"))
            for event in trace:
                stamp = event.timestamp
                digest.update(f"|{event.activity}|{stamp.isoformat() if stamp else ''}".encode("utf-8", "replace"))
            digest.update(b"\n")
        return digest.hexdigest()
    if isinstance(value, SimpleLog):
        return sha256_text(format_simple_log(value))
    if isinstance(value, PetriNet):
        from ..mining.pnml import pnml_string
        return sha256_text(pnml_string(value))
    if isinstance(value, Scores):
        return sha256_text(json.dumps([value.model, _rounded(value.metrics)], sort_keys=True, default=str))
    if isinstance(value, Table):
        return sha256_text(json.dumps([value.columns, _rounded(value.rows)], sort_keys=True, default=str))
    if isinstance(value, Figure):
        return sha256_text(value.svg or "") if value.svg else sha256_bytes(value.png or b"")
    if isinstance(value, Text):
        return sha256_text(value.text)
    if isinstance(value, Dataset):
        return sha256_text(json.dumps([_rounded(_rows(value.X)), [str(y) for y in value.y]], default=str))
    if isinstance(value, Predictions):
        return sha256_text(json.dumps([str(v) for v in value.values]))
    if isinstance(value, (int, float, str, bool, list, dict, tuple)) or value is None:
        return sha256_text(json.dumps(_rounded(value), sort_keys=True, default=str))
    steps = getattr(value, "steps", None)
    if callable(steps):
        try:
            return sha256_text(json.dumps(steps(), default=str))
        except Exception:   # noqa: BLE001
            pass
    for attribute in ("net", "tree", "ts"):
        inner = getattr(value, attribute, None)
        if inner is not None and inner is not value:
            return _fingerprint_v1(inner)
    return sha256_text(repr(value))


def _rows(x):
    try:
        return x.tolist()
    except AttributeError:
        return [list(row) for row in x]


def _rounded(value):
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------
def versions() -> dict[str, str]:
    out = {"openprocess": __version__, "python": platform.python_version()}
    for name in ("pm4py", "numpy", "pandas", "scipy", "matplotlib", "sklearn", "torch"):
        try:
            out[name] = importlib.metadata.version("scikit-learn" if name == "sklearn" else name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


def environment() -> dict[str, Any]:
    """Everything installed, for the lock: ``{"packages": {"numpy": "1.26.4", ...}}``."""
    packages = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            packages[name.lower()] = dist.version
    return {"platform": platform.platform(), "python": sys.version.split()[0],
            "packages": dict(sorted(packages.items()))}


def requirements_lock(record: "Record") -> str:
    """A ``requirements.lock`` from a record's environment."""
    packages = record.environment.get("packages", {})
    return "\n".join(f"{name}=={version}" for name, version in sorted(packages.items())) + "\n"


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
@dataclass
class Record:
    versions: dict[str, str] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    #: ``{"node": ..., "setting": ..., "file": ..., "sha256": ...}`` per file setting.
    inputs: list[dict[str, str]] = field(default_factory=list)
    custom_boxes: list[dict[str, str]] = field(default_factory=list)
    seeds: dict[str, Any] = field(default_factory=dict)
    #: ``node id`` -> result fingerprint (variant 0), from the last run.
    results: dict[str, str] = field(default_factory=dict)
    saved: str = ""
    #: The last runs, as :mod:`openprocess.flow.snapshots` keeps them (dicts).
    history: list[dict] = field(default_factory=list)
    #: The :func:`fingerprint` scheme of :attr:`results` (1 for files written before 0.12).
    scheme: int = FINGERPRINT

    def to_dict(self) -> dict:
        return {"versions": self.versions, "environment": self.environment, "inputs": self.inputs,
                "custom_boxes": self.custom_boxes, "seeds": self.seeds, "results": self.results,
                "fingerprint": self.scheme, "saved": self.saved, "history": self.history}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Record":
        data = data or {}
        return cls(data.get("versions", {}), data.get("environment", {}), data.get("inputs", []),
                   data.get("custom_boxes", []), data.get("seeds", {}), data.get("results", {}),
                   data.get("saved", ""), [d for d in data.get("history", []) if isinstance(d, dict)],
                   int(data.get("fingerprint", 1)))


def make_record(workflow: Workflow, run: Run | None = None, folder: str | Path | None = None,
                lock: bool = True) -> Record:
    """What the workflow depends on right now."""
    record = Record(versions=versions(), environment=environment() if lock else {},
                    saved=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    for node in workflow.nodes.values():
        spec = workflow.spec(node)
        for setting in spec.settings:
            value = node.settings.get(setting.name)
            if setting.kind == "path" and value:
                path = _resolve(value, folder)
                record.inputs.append({"node": node.id, "setting": setting.name,
                                      "file": _relative(path, folder),
                                      "sha256": sha256_file(path) if path.is_file() else "missing"})
            if setting.name == "seed" and value is not None:
                record.seeds[node.id] = value if not hasattr(value, "values") else list(value.values)
        if spec.custom and spec.file:
            file = Path(spec.file)
            entry = {"file": _relative(file, folder), "sha256": sha256_file(file) if file.is_file() else "missing"}
            if entry not in record.custom_boxes:
                record.custom_boxes.append(entry)
    if run is not None:
        for (node_id, variant), result in run.results.items():
            if variant == 0 and result.status == DONE:
                record.results[node_id] = fingerprint(result.value, remember=True)
    return record


def _resolve(value, folder) -> Path:
    path = Path(value)
    if not path.is_absolute() and folder is not None:
        path = Path(folder) / path
    return path


def _relative(path: Path, folder) -> str:
    if folder is not None:
        try:
            return str(path.resolve().relative_to(Path(folder).resolve()))
        except ValueError:
            pass
    return str(path)


def differences(record: Record, workflow: Workflow, folder: str | Path | None = None) -> list[str]:
    """What differs between the record and the world now, in plain words."""
    now = make_record(workflow, None, folder, lock=False)
    out = []
    for then in record.inputs:
        current = next((i for i in now.inputs if i["node"] == then["node"] and i["setting"] == then["setting"]), None)
        if current is None:
            out.append(f"“{then['file']}” is no longer read by the workflow.")
        elif current["sha256"] == "missing":
            out.append(f"“{current['file']}” is missing.")
        elif current["sha256"] != then["sha256"]:
            out.append(f"“{current['file']}” has changed since the workflow was saved "
                       f"(fingerprint {then['sha256'][:8]}… is now {current['sha256'][:8]}…).")
    for then in record.custom_boxes:
        current = next((c for c in now.custom_boxes if c["file"] == then["file"]), None)
        if current is None:
            out.append(f"The box file “{then['file']}” is not used any more.")
        elif current["sha256"] != then["sha256"]:
            out.append(f"The code in “{then['file']}” has changed.")
    for node_id, seed in record.seeds.items():
        if node_id in now.seeds and now.seeds[node_id] != seed:
            out.append(f"The seed of “{workflow.title(node_id)}” is {now.seeds[node_id]}, it was {seed}.")
    for name in ("openprocess", "python"):
        then, current = record.versions.get(name), now.versions.get(name)
        if then and current and then != current:
            out.append(f"{name} is {current}; the workflow was saved with {then}.")
    return out


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def save(workflow: Workflow, path: str | Path, run: Run | None = None,
         folder: str | Path | None = None, lock: bool = True, history: list[dict] | None = None) -> Record:
    """Write the workflow and its record to ``path``; returns the record.
    ``history``: the analysis's snapshots (see :mod:`.snapshots`), kept in the file."""
    path = Path(path)
    folder = folder if folder is not None else path.parent
    record = make_record(workflow, run, folder, lock)
    record.history = list(history or [])
    data = {"format": FORMAT, **workflow.to_dict(), "record": record.to_dict()}
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return record


def load(path: str | Path, library=None) -> tuple[Workflow, Record]:
    """Read a ``.cpnflow`` file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format", FORMAT).split("/")[0] != "cpnflow":
        raise ValueError(f"{path} is not a workflow file")
    workflow = Workflow.from_dict(data, library)
    return workflow, Record.from_dict(data.get("record"))


def check(workflow: Workflow, record: Record, run: Run, complete: bool = False) -> list[str]:
    """After re-running: which results differ from the recorded ones, in
    plain words (empty: every recorded result was reproduced).  Each result
    is fingerprinted with the scheme the record was made with.

    ``complete`` (what ``openprocess run --check`` asks for): a recorded box
    that is gone from the workflow, or did not run, is a difference too;
    otherwise only the boxes that ran are compared (the app checks a run
    that may still be going)."""
    out = []
    for node_id, then in record.results.items():
        if node_id not in workflow.nodes:
            if complete:
                out.append(f"The box {node_id} has a recorded result but is no longer in the workflow.")
            continue
        result = run.result(node_id, 0)
        if result is None:
            if complete:
                out.append(f"“{workflow.title(node_id)}” did not run.")
            continue
        elif result.status != DONE:
            out.append(f"“{workflow.title(node_id)}” did not finish: {result.error or result.message}")
        elif fingerprint(result.value, record.scheme, remember=True) != then:
            out.append(f"“{workflow.title(node_id)}” gives a different result than recorded.")
    return out


# ---------------------------------------------------------------------------
# Export experiment: everything in one zip, for a paper's supplementary material
# ---------------------------------------------------------------------------
def export_experiment(workflow: Workflow, run: Run | None, target: str | Path,
                      folder: str | Path | None = None, library=None) -> Path:
    """A zip with the workflow file and its record, the input files, the
    custom box files, every result as a file (CSV, PNML, SVG, XES), a
    ``requirements.lock`` and a ``README.md`` that says what was run."""
    import io
    import zipfile
    target = Path(target)
    folder = Path(folder) if folder is not None else target.parent
    record = make_record(workflow, run, folder)
    # The inputs travel in inputs/, so the workflow in the zip reads them from
    # there: `openprocess run --check` and `python run.py` work in the
    # unpacked folder as they are.
    packed: dict[str, str] = {}                     # source file -> its name in the zip
    moved: dict[tuple[str, str], str] = {}          # (node, setting) -> inputs/...
    for item in record.inputs:
        source = _resolve(item["file"], folder)
        if not source.is_file():
            continue
        key = str(source.resolve())
        if key not in packed:
            stem, suffix, number = Path(item["file"]).name, "", 2
            candidate = f"inputs/{stem}"
            while candidate in packed.values():
                candidate = f"inputs/{Path(stem).stem} {number}{''.join(Path(stem).suffixes)}"
                number += 1
            packed[key] = candidate
        moved[(item["node"], item["setting"])] = item["file"] = packed[key]
    data = {"format": FORMAT, **workflow.to_dict(), "record": record.to_dict()}
    for box in data["boxes"]:
        for setting in box["settings"]:
            if (box["id"], setting) in moved:
                box["settings"][setting] = moved[(box["id"], setting)]
    packed_workflow = Workflow.from_dict(data, library or workflow.library) if moved else workflow
    name = workflow.name or "workflow"
    lines = [f"# {name}", "", f"Exported {record.saved} with OpenProcess {record.versions.get('openprocess', '')}, "
             f"Python {record.versions.get('python', '')}.", "",
             "Run it again with `openprocess run " + f"{name}.cpnflow --check`: it fails if any result differs "
             "from the recorded one. `requirements.lock` is the environment it ran in. `run.py` is the same "
             "analysis as a plain Python script (`python run.py`). Or drop this zip on OpenProcess: it unpacks "
             "it into a folder and runs it.", "", "## Boxes", ""]
    for node in workflow.order():
        spec = workflow.spec(node)
        settings = ", ".join(f"{k} = {v}" for k, v in packed_workflow.nodes[node.id].settings.items()) \
            or "no settings"
        fed = ", ".join(f"{p} ← {workflow.title(src)}" for p, sources in workflow.inputs_of(node).items()
                        for src, _ in sources) or "no inputs"
        result = run.result(node) if run else None
        verdict = ""
        if result is not None and result.status == DONE:
            verdict = " → " + _describe(result.value)
        lines.append(f"- **{workflow.title(node)}** (`{spec.id}`): {settings}; {fed}{verdict}")
    lines += ["", "## Inputs", ""] + [f"- `{i['file']}` sha256 `{i['sha256']}`" for i in record.inputs]
    lines += ["", "## Your boxes", ""] + [f"- `{b['file']}` sha256 `{b['sha256']}`" for b in record.custom_boxes]
    from .. import citation
    from .box import algorithm_calls
    entries = [citation.app_entry()]
    for node in workflow.order():
        for called in algorithm_calls(workflow.spec(node)):
            for item in citation.entries_for_module(called.module):
                if all(item.key != e.key for e in entries):
                    entries.append(item)
    lines += ["", "## How to cite", "", citation.app_text(), "",
              "`CITATIONS.bib` has this entry and the papers the algorithms follow, as BibTeX."]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(f"{name}.cpnflow", json.dumps(data, indent=2, ensure_ascii=False, default=str))
        zipped.writestr("requirements.lock", requirements_lock(record))
        from .script import script_text
        zipped.writestr("run.py", script_text(packed_workflow, "run.py"))
        zipped.writestr("CITATIONS.bib", "\n\n".join(e.bibtex() for e in entries) + "\n")
        zipped.writestr("README.md", "\n".join(lines) + "\n")
        for source, packed_name in packed.items():
            zipped.write(source, packed_name)
        for item in record.custom_boxes:
            path = _resolve(item["file"], folder)
            if path.is_file():
                zipped.write(path, f"boxes/{path.name}")
        if run is not None:
            for node in workflow.order():
                value = run.value(node)
                stem = "results/" + f"{node.id} {workflow.title(node)}".replace("/", "-")
                payload = _as_file(value, stem)
                if payload is not None:
                    file_name, content = payload
                    zipped.writestr(file_name, content)
    return target


def _describe(value) -> str:
    if isinstance(value, Scores):
        return ", ".join(f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in value.metrics.items())
    if isinstance(value, Table):
        return f"table {len(value.rows)} × {len(value.columns)}"
    if isinstance(value, EventLog):
        return f"{len(value)} cases, {value.event_count} events"
    if isinstance(value, PetriNet):
        return value.summary()
    return type(value).__name__ if value is not None else "done"


def is_experiment(path) -> bool:
    """Whether ``path`` is a zip :func:`export_experiment` wrote: one ``.cpnflow`` at the top."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as zipped:
            return sum(1 for n in zipped.namelist() if n.endswith(".cpnflow") and "/" not in n) == 1
    except (OSError, zipfile.BadZipFile):
        return False


def import_experiment(archive, into) -> Path:
    """Unpack an exported experiment into a new folder under ``into`` (named
    after the zip) and give back its workflow file, ready to open: the file
    settings point at ``inputs/``, the folder's ``boxes/`` holds the custom
    boxes (a box is found by its function name there), and the recorded
    results are kept for the reproducibility check."""
    import zipfile
    archive = Path(archive)
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        flows = [n for n in names if n.endswith(".cpnflow") and "/" not in n]
        if len(flows) != 1:
            raise ValueError(f"{archive.name} is not an exported experiment (one .cpnflow at the top is needed)")
        folder = Path(into) / archive.stem
        counter = 2
        while folder.exists():
            folder = Path(into) / f"{archive.stem} {counter}"
            counter += 1
        folder.mkdir(parents=True)
        for name in names:
            parts = Path(name).parts
            if name.endswith("/") or not parts or ".." in parts or Path(name).is_absolute():
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zipped.read(name))
    flow_file = folder / flows[0]
    data = json.loads(flow_file.read_text(encoding="utf-8"))
    moved: dict[tuple[str, str], str] = {}
    for item in data.get("record", {}).get("inputs", []):
        new = f"inputs/{Path(item['file']).name}"
        if (folder / new).is_file():
            moved[(item["node"], item["setting"])] = new
            item["file"] = new
    for item in data.get("record", {}).get("custom_boxes", []):
        new = f"boxes/{Path(item['file']).name}"
        if (folder / new).is_file():
            item["file"] = new
    for box in data.get("boxes", []):
        for setting in list(box.get("settings", {})):
            if (box["id"], setting) in moved:
                box["settings"][setting] = moved[(box["id"], setting)]
    flow_file.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return flow_file


def _as_file(value, stem: str):
    """(name, bytes or text) for a result worth a file of its own."""
    if isinstance(value, Table):
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(value.columns)
        writer.writerows(value.rows)
        return f"{stem}.csv", buffer.getvalue()
    if isinstance(value, Scores):
        return _as_file(Table.from_scores([value]), stem)
    if isinstance(value, PetriNet):
        from ..mining.pnml import pnml_string
        return f"{stem}.pnml", pnml_string(value)
    if isinstance(value, Figure):
        if value.svg:
            return f"{stem}.svg", value.svg
        if value.png:
            return f"{stem}.png", value.png
    if isinstance(value, Text):
        return f"{stem}.txt", value.text
    if isinstance(value, EventLog) and len(value) <= 20_000:
        from ..mining.xes import xes_string
        return f"{stem}.xes", xes_string(value)
    return None


#: (name, bytes or text) for a result worth a file of its own; the script an experiment carries uses it.
as_file = _as_file

__all__ = ["FORMAT", "Record", "as_file", "check", "differences", "environment", "export_experiment",
           "fingerprint", "import_experiment", "is_experiment", "load", "make_record", "requirements_lock",
           "save", "sha256_file", "sha256_text", "versions"]
