"""Event logs: the data every process mining technique starts from.

Vocabulary (as in van der Aalst, *Process Mining*, ch. 2 and 5)
---------------------------------------------------------------
event
    One recorded occurrence of an activity, carrying attributes such as the
    activity name (``concept:name``), a timestamp (``time:timestamp``), a
    resource (``org:resource``) and a lifecycle transition
    (``lifecycle:transition``: *start*, *complete*, ...).
trace (case)
    The ordered list of events belonging to one process instance, e.g. one
    passenger boarding a plane.  A trace also has attributes of its own; its
    ``concept:name`` is the *case id*.
event log
    A collection of traces.

Two views of the same log
-------------------------
The *rich* view (:class:`EventLog`) keeps every attribute, because the dotted
chart, performance analysis and the case browser need timestamps and
resources.

Most discovery algorithms, however, only look at the *control-flow* view: each
trace reduced to its sequence of activity names, and the log reduced to a
**multiset of such sequences**, written in the course as::

    L = [ <a,b,c,d>^3, <a,c,b,d>^2, <a,e,d> ]

A multiset (not a set) because the same sequence can occur many times, and
frequency matters for filtering and for the quality measures.  In Python a
multiset is a :class:`collections.Counter`; we call it :data:`SimpleLog`.

Large logs
----------
A log read with ``read_xes(path, columnar=True)`` (the default above a
size threshold) keeps its events in a :class:`~.columns.ColumnStore`, one
array per attribute, and ``traces`` is a :class:`~.columns.LazyTraces`
that builds each ``Trace`` on first access.  The API is the same; the
sequences, the event count and the variants come straight from the
arrays, so discovery on a million events does not create a million
objects.

Classifiers
-----------
Which attribute(s) turn an event into an *activity label* is a choice, called
the *event classifier* in XES.  The default is ``concept:name``.  A log that
records both *start* and *complete* events (like the plane-boarding logs in
the course) would, with the default classifier, show every activity twice in a
row.  Two standard remedies are offered as ready-made classifiers:

* keep only *complete* events and label them by name, or
* label every event by ``name + lifecycle`` (so ``Move in+start`` and
  ``Move in+complete`` become different activities).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator

#: The control-flow abstraction of a log: a multiset of activity sequences.
SimpleLog = Counter  # Counter[tuple[str, ...]]

# Standard XES attribute keys, named once so a typo is a NameError rather
# than a silently empty column.
KEY_NAME = "concept:name"
KEY_TIME = "time:timestamp"
KEY_LIFECYCLE = "lifecycle:transition"
KEY_RESOURCE = "org:resource"


# ---------------------------------------------------------------------------
# Events and traces
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Event:
    """One event: a bag of typed attributes.

    Attribute values are already converted to Python types by the reader:
    ``str``, ``int``, ``float``, ``bool``, :class:`datetime`, or ``list`` for
    XES list attributes.
    """

    attributes: dict[str, Any] = field(default_factory=dict)

    # Convenience accessors for the standard extensions --------------------
    @property
    def activity(self) -> str | None:
        return self.attributes.get(KEY_NAME)

    @property
    def timestamp(self) -> datetime | None:
        value = self.attributes.get(KEY_TIME)
        return value if isinstance(value, datetime) else None

    @property
    def lifecycle(self) -> str | None:
        return self.attributes.get(KEY_LIFECYCLE)

    @property
    def resource(self) -> str | None:
        return self.attributes.get(KEY_RESOURCE)

    def get(self, key: str, default: Any = None) -> Any:
        return self.attributes.get(key, default)


@dataclass(slots=True)
class Trace:
    """One case: trace-level attributes plus the ordered list of events."""

    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    @property
    def case_id(self) -> str:
        value = self.attributes.get(KEY_NAME)
        return "" if value is None else str(value)

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __getitem__(self, index: int) -> Event:
        return self.events[index]


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Classifier:
    """Maps an event to an activity label, optionally dropping some events.

    Parameters
    ----------
    name:
        Shown in the user interface.
    keys:
        Attribute keys whose values are joined with ``+`` to form the label.
    lifecycles:
        If given, only events whose ``lifecycle:transition`` is in this set
        are kept (case-insensitive).  Events *without* a lifecycle attribute
        are always kept: a log that never recorded lifecycles should not come
        out empty.
    """

    name: str
    keys: tuple[str, ...] = (KEY_NAME,)
    lifecycles: frozenset[str] | None = None

    def accepts(self, event: Event) -> bool:
        if self.lifecycles is None:
            return True
        lifecycle = event.attributes.get(KEY_LIFECYCLE)
        return lifecycle is None or str(lifecycle).lower() in self.lifecycles

    def label(self, event: Event) -> str:
        parts = [event.attributes.get(key) for key in self.keys]
        return "+".join("" if part is None else str(part) for part in parts)


#: Label by activity name, keep every event.
BY_NAME = Classifier("Activity")
#: Label by activity name, keep only *complete* events.
BY_NAME_COMPLETE = Classifier("Activity (complete events)",
                              lifecycles=frozenset({"complete"}))
#: Label by activity name and lifecycle transition.
BY_NAME_LIFECYCLE = Classifier("Activity + lifecycle", keys=(KEY_NAME, KEY_LIFECYCLE))


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------
@dataclass
class EventLog:
    """A list of traces plus log-level metadata.

    ``classifiers`` holds the classifiers declared in the XES file (if any) in
    addition to the built-in ones; the GUI offers all of them.
    """

    traces: list[Trace] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    extensions: list[dict[str, str]] = field(default_factory=list)
    global_trace_attributes: dict[str, Any] = field(default_factory=dict)
    global_event_attributes: dict[str, Any] = field(default_factory=dict)
    declared_classifiers: list[Classifier] = field(default_factory=list)
    source_path: str | None = None

    # -- container protocol --------------------------------------------------
    def __len__(self) -> int:
        return len(self.traces)

    def __iter__(self) -> Iterator[Trace]:
        return iter(self.traces)

    def __getitem__(self, index: int) -> Trace:
        return self.traces[index]

    @property
    def name(self) -> str:
        value = self.attributes.get(KEY_NAME)
        if value:
            return str(value)
        if self.source_path:
            from pathlib import Path
            return Path(self.source_path).stem
        return "Untitled log"

    @property
    def event_count(self) -> int:
        lazy = getattr(self.traces, "event_count", None)
        return lazy if lazy is not None else sum(len(trace) for trace in self.traces)

    @property
    def columnar(self) -> bool:
        """Are the events kept in columns (a large log, read lazily)?"""
        return hasattr(self.traces, "store") and not getattr(self.traces, "modified", True)

    @classmethod
    def from_columns(cls, store, **fields) -> "EventLog":
        from .columns import LazyTraces
        return cls(traces=LazyTraces(store), **fields)

    # -- classifiers ------------------------------------------------------
    def has_lifecycle_pairs(self) -> bool:
        """Does the log record both *start* and *complete* events?"""
        if self.columnar:
            return self.traces.store.has_lifecycle_pairs()
        seen: set[str] = set()
        for trace in self.traces:
            for event in trace:
                lifecycle = event.attributes.get(KEY_LIFECYCLE)
                if lifecycle is not None:
                    seen.add(str(lifecycle).lower())
                    if {"start", "complete"} <= seen:
                        return True
        return False

    def default_classifier(self) -> Classifier:
        """The most sensible classifier for control-flow discovery.

        With start/complete pairs, the plain name classifier would produce a
        self-loop on every activity, which is an artefact of logging, not of
        the process.  Keeping complete events avoids that.
        """
        return BY_NAME_COMPLETE if self.has_lifecycle_pairs() else BY_NAME

    def available_classifiers(self) -> list[Classifier]:
        built_in = [BY_NAME, BY_NAME_COMPLETE, BY_NAME_LIFECYCLE]
        names = {c.name for c in built_in}
        extra = [c for c in self.declared_classifiers if c.name not in names]
        return built_in + extra

    # -- control-flow projections ----------------------------------------
    def sequence(self, trace: Trace, classifier: Classifier | None = None) -> tuple[str, ...]:
        """The activity sequence of one trace under ``classifier``."""
        classifier = classifier or self.default_classifier()
        return tuple(classifier.label(e) for e in trace.events if classifier.accepts(e))

    def sequences(self, classifier: Classifier | None = None) -> list[tuple[str, ...]]:
        """Activity sequences for every trace, in log order."""
        classifier = classifier or self.default_classifier()
        if self.columnar:
            fast = self.traces.sequences(classifier.keys, classifier.lifecycles)
            if fast is not None:
                return fast
        return [self.sequence(trace, classifier) for trace in self.traces]

    def simple_log(self, classifier: Classifier | None = None) -> SimpleLog:
        """The multiset of activity sequences: the input to discovery."""
        return Counter(self.sequences(classifier))

    def filtered(self, keep: Iterable[int]) -> "EventLog":
        """A new log containing only the traces at the given indices.  A
        columnar log stays columnar (see :meth:`.columns.ColumnStore.subset`)."""
        indices = list(keep)
        if self.columnar:
            return EventLog.from_columns(
                self.traces.store.subset(indices), attributes=dict(self.attributes),
                extensions=list(self.extensions), global_trace_attributes=dict(self.global_trace_attributes),
                global_event_attributes=dict(self.global_event_attributes),
                declared_classifiers=list(self.declared_classifiers), source_path=self.source_path)
        return EventLog(
            traces=[self.traces[i] for i in indices],
            attributes=dict(self.attributes),
            extensions=list(self.extensions),
            global_trace_attributes=dict(self.global_trace_attributes),
            global_event_attributes=dict(self.global_event_attributes),
            declared_classifiers=list(self.declared_classifiers),
            source_path=self.source_path,
        )

    # -- construction from a simple log ----------------------------------
    @classmethod
    def from_simple_log(cls, simple: SimpleLog, name: str = "Log") -> "EventLog":
        """Expand a multiset of sequences into a full log (no timestamps).

        Handy for textbook exercises: the course writes logs as multisets, and
        this turns one into something every view in the app can display.
        """
        log = cls(attributes={KEY_NAME: name})
        case = 0
        for sequence, count in simple.items():
            for _ in range(count):
                case += 1
                log.traces.append(Trace(
                    attributes={KEY_NAME: f"case {case}"},
                    events=[Event({KEY_NAME: activity}) for activity in sequence],
                ))
        return log


# ---------------------------------------------------------------------------
# Textbook notation
# ---------------------------------------------------------------------------
_TRACE_PATTERN = re.compile(r"<([^<>]*)>\s*(?:\^\s*(\d+))?")

# The textbook and the slides typeset traces as ⟨a,b,c⟩³: angle brackets and
# superscript counts.  Text copied from a PDF therefore arrives in that form.
_TYPESET = str.maketrans({"⟨": "<", "⟩": ">", "〈": "<", "〉": ">", "‹": "<", "›": ">"})
_SUPERSCRIPTS = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def parse_simple_log(text: str) -> SimpleLog:
    """Parse the notation used in the course and the textbook.

    Accepted forms (whitespace is free, the outer brackets are optional)::

        [<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]
        [⟨a,b,c,d⟩³, ⟨a,c,b,d⟩², ⟨a,e,d⟩]    # as typeset in the book
        [<>^2, <a>]                         # the empty trace is allowed

    Activity names may contain spaces (``<register request, pay>``).  A
    multiplicity defaults to 1.  Repeated sequences are added together, so
    ``<a>, <a>`` is the same as ``<a>^2``; a multiplicity of 0 leaves the
    trace out.

    Raises ``ValueError`` if nothing trace-like is found, because silently
    returning an empty log would make a typo look like a finding.
    """
    text = text.translate(_TYPESET)
    text = _SUPERSCRIPTS.sub(lambda m: "^" + m.group().translate(_SUPERSCRIPT_DIGITS), text)
    log: SimpleLog = Counter()
    for match in _TRACE_PATTERN.finditer(text):
        body, count = match.group(1), match.group(2)
        activities = tuple(part.strip() for part in body.split(",") if part.strip())
        log[activities] += int(count) if count else 1
    log = Counter({trace: count for trace, count in log.items() if count > 0})
    if not log:
        raise ValueError("No traces found. Write traces as <a,b,c>^n, e.g. [<a,b,c>^3, <a,c>]")
    return log


def format_simple_log(log: SimpleLog, max_traces: int | None = None) -> str:
    """The inverse of :func:`parse_simple_log`, most frequent first."""
    items = sorted(log.items(), key=lambda item: (-item[1], item[0]))
    if max_traces is not None:
        items = items[:max_traces]
    parts = []
    for sequence, count in items:
        body = ",".join(sequence)
        parts.append(f"<{body}>" + (f"^{count}" if count != 1 else ""))
    return "[" + ", ".join(parts) + "]"
