"""Where a result came from, carried with it and written into the files it becomes.

The runner stamps every value a box gives with a :class:`Provenance`: the
analysis, the box and its settings, and the result's key (the fingerprint of
the box's code, its settings and its inputs, which is also the cache key).
A figure saved as SVG carries it in a ``<metadata>`` block; a table saved as
CSV, or a figure as PNG, gets a ``.provenance.json`` beside it.  A figure in
a paper then points back at the analysis that made it.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.sax.saxutils import escape


@dataclass
class Provenance:
    workflow: str
    box: str
    title: str
    settings: dict
    key: str
    openprocess: str
    when: str

    def as_dict(self) -> dict:
        return asdict(self)

    def json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False, default=str)

    def line(self) -> str:
        return (f"{self.title} ({self.box}) in “{self.workflow}”, OpenProcess {self.openprocess}, "
                f"{self.when}; key {self.key[:12]}")

    def svg_metadata(self) -> str:
        """A Dublin Core block for an SVG's ``<metadata>``."""
        return (
            '<metadata id="openprocess-provenance">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Description>'
            f"<dc:creator>OpenProcess {escape(self.openprocess)}</dc:creator>"
            f"<dc:source>{escape(self.line())}</dc:source>"
            f"<dc:date>{escape(self.when)}</dc:date>"
            f"<dc:description>{escape(self.json())}</dc:description>"
            "</rdf:Description></rdf:RDF></metadata>"
        )


def make(workflow, node, spec, settings: dict, key: str) -> Provenance:
    from .. import __version__
    plain = {}
    for name, value in settings.items():
        if hasattr(value, "describe"):             # a sweep
            value = value.describe()
        plain[name] = str(value) if isinstance(value, Path) else value
    return Provenance(workflow.name, spec.id, workflow.title(node), plain, key, __version__,
                      _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))


def stamp(value, provenance: Provenance) -> bool:
    """Attach ``provenance`` to ``value`` when it can carry one (a dataclass
    can; a number or a string cannot).  Returns whether it did."""
    if value is None or isinstance(value, (bool, int, float, str, bytes, list, tuple, dict, set, frozenset)):
        return False
    try:
        object.__setattr__(value, "provenance", provenance)
    except (AttributeError, TypeError):
        return False
    return True


def of(value) -> Provenance | None:
    found = getattr(value, "provenance", None)
    return found if isinstance(found, Provenance) else None


_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.S)


def with_metadata(svg: str, provenance: Provenance | None) -> str:
    """The SVG text with the provenance in a ``<metadata>`` block after the opening tag."""
    if provenance is None:
        return svg
    match = _SVG_OPEN.search(svg)
    if match is None:
        return svg
    return svg[:match.end()] + "\n" + provenance.svg_metadata() + svg[match.end():]


def sidecar_path(path) -> Path:
    """``table.csv`` → ``table.provenance.json`` beside it."""
    path = Path(path)
    return path.with_name(path.stem + ".provenance.json")


def write_sidecar(path, provenance: Provenance | None) -> Path | None:
    if provenance is None:
        return None
    target = sidecar_path(path)
    target.write_text(provenance.json() + "\n", encoding="utf-8")
    return target


__all__ = ["Provenance", "make", "of", "sidecar_path", "stamp", "with_metadata", "write_sidecar"]
