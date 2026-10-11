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


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: Any) -> str:
    """A short code that changes if the value changes (as far as it can be
    told from the value's content, not its identity)."""
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
            return fingerprint(inner)
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

    def to_dict(self) -> dict:
        return {"versions": self.versions, "environment": self.environment, "inputs": self.inputs,
                "custom_boxes": self.custom_boxes, "seeds": self.seeds, "results": self.results,
                "saved": self.saved, "history": self.history}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Record":
        data = data or {}
        return cls(data.get("versions", {}), data.get("environment", {}), data.get("inputs", []),
                   data.get("custom_boxes", []), data.get("seeds", {}), data.get("results", {}),
                   data.get("saved", ""), [d for d in data.get("history", []) if isinstance(d, dict)])


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
                record.results[node_id] = fingerprint(result.value)
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


def check(workflow: Workflow, record: Record, run: Run) -> list[str]:
    """After re-running: which results differ from the recorded ones."""
    out = []
    for node_id, then in record.results.items():
        result = run.result(node_id, 0)
        if result is None:
            continue
        if result.status != DONE:
            out.append(f"“{workflow.title(node_id)}” did not finish: {result.error or result.message}")
        elif fingerprint(result.value) != then:
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
    data = {"format": FORMAT, **workflow.to_dict(), "record": record.to_dict()}
    name = workflow.name or "workflow"
    lines = [f"# {name}", "", f"Exported {record.saved} with OpenProcess {record.versions.get('openprocess', '')}, "
             f"Python {record.versions.get('python', '')}.", "",
             "Run it again with `openprocess run " + f"{name}.cpnflow --check`: it fails if any result differs "
             "from the recorded one. `requirements.lock` is the environment it ran in. `run.py` is the same "
             "analysis as a plain Python script (`python run.py`). Or drop this zip on OpenProcess: it unpacks "
             "it into a folder and runs it.", "", "## Boxes", ""]
    for node in workflow.order():
        spec = workflow.spec(node)
        settings = ", ".join(f"{k} = {v}" for k, v in node.settings.items()) or "no settings"
        fed = ", ".join(f"{p} ← {workflow.title(src)}" for p, sources in workflow.inputs_of(node).items()
                        for src, _ in sources) or "no inputs"
        result = run.result(node) if run else None
        verdict = ""
        if result is not None and result.status == DONE:
            verdict = " → " + _describe(result.value)
        lines.append(f"- **{workflow.title(node)}** (`{spec.id}`): {settings}; {fed}{verdict}")
    lines += ["", "## Inputs", ""] + [f"- `{i['file']}` sha256 `{i['sha256']}`" for i in record.inputs] +         ["", "## Your boxes", ""] + [f"- `{b['file']}` sha256 `{b['sha256']}`" for b in record.custom_boxes]
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
        zipped.writestr("run.py", script_text(workflow, "run.py"))
        zipped.writestr("CITATIONS.bib", "\n\n".join(e.bibtex() for e in entries) + "\n")
        zipped.writestr("README.md", "\n".join(lines) + "\n")
        for item in record.inputs:
            path = _resolve(item["file"], folder)
            if path.is_file():
                zipped.write(path, f"inputs/{Path(item['file']).name}")
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
