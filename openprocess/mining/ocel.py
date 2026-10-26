"""Object-centric event logs (OCEL): events that touch several objects at once.

A classical event log has one case per trace.  In an order-to-cash process an
event such as *pack items* belongs to one order, three items and one
package at the same time; forcing a case id flattens that and invents
convergence and divergence.  OCEL keeps the objects: every event names the
objects it involves, by type.  This module reads the two JSON forms of the
standard (OCEL 2.0, and the older JSON-OCEL 1.0), flattens a log onto one
object type when a classical algorithm needs a case, and builds the
**object-centric directly-follows graph**: one directly-follows relation per
object type, drawn in that type's colour on the shared activities.

Follows: W.M.P. van der Aalst and A. Berti, *Discovering Object-Centric Petri
Nets* (Fundamenta Informaticae 175, 2020) and the OCEL 2.0 specification
(ocel-standard.org).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .log import KEY_NAME, KEY_TIME, Event, EventLog, Trace


@dataclass
class OCEvent:
    id: str
    activity: str
    time: datetime | None
    #: object type -> the ids of the objects of that type this event involves
    objects: dict[str, list[str]] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def object_ids(self) -> list[str]:
        return [o for ids in self.objects.values() for o in ids]


@dataclass
class OCObject:
    id: str
    type: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class OCEL:
    """An object-centric event log: events in time order, and the objects they touch."""

    name: str = "Object-centric log"
    events: list[OCEvent] = field(default_factory=list)
    objects: dict[str, OCObject] = field(default_factory=dict)
    object_types: list[str] = field(default_factory=list)
    source_path: str | None = None

    def __len__(self) -> int:
        return len(self.events)

    @property
    def activities(self) -> Counter:
        return Counter(e.activity for e in self.events)

    def objects_of_type(self, object_type: str) -> list[OCObject]:
        return [o for o in self.objects.values() if o.type == object_type]

    def counts_by_type(self) -> dict[str, int]:
        return {t: sum(1 for o in self.objects.values() if o.type == t) for t in self.object_types}

    def events_of(self, object_id: str) -> list[OCEvent]:
        return [e for e in self.events if object_id in e.object_ids]

    def summary(self) -> str:
        types = ", ".join(f"{n} {t}" for t, n in self.counts_by_type().items())
        return (f"{len(self.events):,} events · {len(self.activities)} activities · "
                f"{len(self.objects):,} objects ({types})")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _time(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def read_ocel(path: str | Path) -> OCEL:
    """Read an OCEL 2.0 JSON file (``.jsonocel`` or ``.json``), or a JSON-OCEL 1.0 one."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "objectTypes" in data or "eventTypes" in data:
        log = _read_ocel2(data)
    elif "ocel:events" in data:
        log = _read_ocel1(data)
    else:
        raise ValueError(f"{path.name} is not an object-centric event log (no objectTypes, no ocel:events)")
    log.name = path.name.split(".")[0]
    log.source_path = str(path)
    return log


def _read_ocel2(data: dict) -> OCEL:
    log = OCEL()
    log.object_types = [t["name"] for t in data.get("objectTypes", []) if isinstance(t, dict) and t.get("name")]
    for item in data.get("objects", []):
        object_id, object_type = str(item.get("id")), str(item.get("type", ""))
        attributes = {}
        for attribute in item.get("attributes", []) or []:
            if isinstance(attribute, dict) and "name" in attribute:
                attributes[attribute["name"]] = attribute.get("value")       # the latest value wins
        log.objects[object_id] = OCObject(object_id, object_type, attributes)
        if object_type and object_type not in log.object_types:
            log.object_types.append(object_type)
    for item in data.get("events", []):
        objects: dict[str, list[str]] = defaultdict(list)
        for relation in item.get("relationships", []) or []:
            object_id = str(relation.get("objectId"))
            kind = log.objects[object_id].type if object_id in log.objects else "object"
            objects[kind].append(object_id)
        attributes = {}
        for attribute in item.get("attributes", []) or []:
            if isinstance(attribute, dict) and "name" in attribute:
                attributes[attribute["name"]] = attribute.get("value")
        log.events.append(OCEvent(str(item.get("id")), str(item.get("type", "")), _time(item.get("time")),
                                  dict(objects), attributes))
    _finish(log)
    return log


def _read_ocel1(data: dict) -> OCEL:
    log = OCEL()
    globals_ = data.get("ocel:global-log", {}) or {}
    log.object_types = list(globals_.get("ocel:object-types", []) or [])
    for object_id, item in (data.get("ocel:objects", {}) or {}).items():
        object_type = str(item.get("ocel:type", ""))
        log.objects[str(object_id)] = OCObject(str(object_id), object_type, dict(item.get("ocel:ovmap", {}) or {}))
        if object_type and object_type not in log.object_types:
            log.object_types.append(object_type)
    for event_id, item in (data.get("ocel:events", {}) or {}).items():
        objects: dict[str, list[str]] = defaultdict(list)
        for object_id in item.get("ocel:omap", []) or []:
            kind = log.objects[str(object_id)].type if str(object_id) in log.objects else "object"
            objects[kind].append(str(object_id))
        log.events.append(OCEvent(str(event_id), str(item.get("ocel:activity", "")),
                                  _time(item.get("ocel:timestamp")), dict(objects),
                                  dict(item.get("ocel:vmap", {}) or {})))
    _finish(log)
    return log


def _finish(log: OCEL) -> None:
    """Events in time order (ties keep the file's order); object types in a stable order."""
    log.events.sort(key=lambda e: (e.time is None, e.time or datetime.min.replace(tzinfo=None)) if e.time is None
                    or e.time.tzinfo is None else (False, e.time))
    seen = set()
    types = []
    for t in log.object_types + [o.type for o in log.objects.values()]:
        if t and t not in seen:
            seen.add(t)
            types.append(t)
    log.object_types = types


# ---------------------------------------------------------------------------
# Flattening: one object type becomes the case notion
# ---------------------------------------------------------------------------
def flatten(log: OCEL, object_type: str | None = None) -> EventLog:
    """A classical event log with one case per object of ``object_type`` (the
    type with most objects when None).  An event that involves several
    objects of the type is copied into each of their cases: that is what
    flattening does, and why the object-centric map exists."""
    object_type = object_type or max(log.counts_by_type().items(), key=lambda kv: kv[1], default=("", 0))[0]
    if object_type not in log.object_types:
        raise ValueError(f"No object type {object_type!r}: this log has {', '.join(log.object_types) or 'none'}")
    traces = []
    for obj in log.objects_of_type(object_type):
        events = [e for e in log.events if obj.id in e.objects.get(object_type, ())]
        if not events:
            continue
        trace = Trace({KEY_NAME: obj.id, "ocel:type": object_type, **obj.attributes})
        for event in events:
            attributes = {KEY_NAME: event.activity, "ocel:event": event.id, **event.attributes}
            if event.time is not None:
                attributes[KEY_TIME] = event.time
            for other_type, ids in event.objects.items():
                if other_type != object_type:
                    attributes[f"ocel:{other_type}"] = ", ".join(ids)
            trace.events.append(Event(attributes))
        traces.append(trace)
    return EventLog(traces=traces, attributes={KEY_NAME: f"{log.name} · {object_type}"}, source_path=log.source_path)


# ---------------------------------------------------------------------------
# The object-centric directly-follows graph
# ---------------------------------------------------------------------------
@dataclass
class OCDFG:
    """One directly-follows relation per object type, over shared activities."""

    activities: Counter = field(default_factory=Counter)            # activity -> events
    object_types: list[str] = field(default_factory=list)
    #: object type -> Counter of (a, b): how many objects of the type went a then b
    edges: dict[str, Counter] = field(default_factory=dict)
    starts: dict[str, Counter] = field(default_factory=dict)        # type -> first activities
    ends: dict[str, Counter] = field(default_factory=dict)          # type -> last activities
    #: (activity, type) -> how many objects of the type the activity's events involve
    objects_at: dict[tuple[str, str], int] = field(default_factory=dict)
    name: str = "Object-centric map"

    def summary(self) -> str:
        paths = sum(len(c) for c in self.edges.values())
        return f"{len(self.activities)} activities · {paths} paths over {len(self.object_types)} object types"


def object_centric_dfg(log: OCEL) -> OCDFG:
    """Follow every object through its events: each type gets its own
    directly-follows counts, drawn later in its own colour."""
    graph = OCDFG(activities=log.activities, object_types=list(log.object_types), name=f"Map of {log.name}")
    per_object: dict[str, list[OCEvent]] = defaultdict(list)
    for event in log.events:
        for ids in event.objects.values():
            for object_id in ids:
                per_object[object_id].append(event)
    objects_at: Counter = Counter()
    for object_type in log.object_types:
        edges: Counter = Counter()
        starts: Counter = Counter()
        ends: Counter = Counter()
        for obj in log.objects_of_type(object_type):
            events = per_object.get(obj.id, [])
            if not events:
                continue
            starts[events[0].activity] += 1
            ends[events[-1].activity] += 1
            for a, b in zip(events, events[1:]):
                edges[(a.activity, b.activity)] += 1
            for event in events:
                objects_at[(event.activity, object_type)] += 1
        graph.edges[object_type], graph.starts[object_type], graph.ends[object_type] = edges, starts, ends
    graph.objects_at = dict(objects_at)
    return graph


__all__ = ["OCEL", "OCDFG", "OCEvent", "OCObject", "read_ocel", "flatten", "object_centric_dfg"]
