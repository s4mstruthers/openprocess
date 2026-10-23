"""A columnar store for large logs: one array per attribute, no objects.

A million-event log read as :class:`~.log.Trace` and :class:`~.log.Event`
objects costs a million dicts.  The :class:`ColumnStore` keeps one array
per attribute instead -- activities, resources and lifecycles as codes into
a table of strings, timestamps as seconds since the epoch -- and the
events of a case are a slice ``offsets[i]:offsets[i+1]``.

:class:`LazyTraces` makes a store look like the list of traces every view
in the app reads: ``log.traces[i]`` builds the ``Trace`` with its events on
first access and keeps it; ``len``, iteration and slicing work; and the
first *edit* (append, insert, delete) materialises everything, so the
editor keeps working.  The things discovery needs most -- the activity
sequences, the directly-follows counts, the event count -- are read from
the arrays without building any object (:meth:`LazyTraces.sequences`).
"""

from __future__ import annotations

from array import array
from collections.abc import MutableSequence
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

#: Standard keys kept in typed arrays; everything else goes to ``extra``.
KEY_NAME = "concept:name"
KEY_TIME = "time:timestamp"
KEY_LIFECYCLE = "lifecycle:transition"
KEY_RESOURCE = "org:resource"


class Codes:
    """Strings as small integers, with the table to read them back."""

    __slots__ = ("values", "_index", "codes")

    def __init__(self) -> None:
        self.values: list[str] = []
        self._index: dict[str, int] = {}
        self.codes = array("i")

    def add(self, value: str | None) -> None:
        if value is None:
            self.codes.append(-1)
            return
        code = self._index.get(value)
        if code is None:
            code = self._index[value] = len(self.values)
            self.values.append(value)
        self.codes.append(code)

    def get(self, index: int) -> str | None:
        code = self.codes[index]
        return None if code < 0 else self.values[code]

    def __len__(self) -> int:
        return len(self.codes)


class ColumnStore:
    """The columns of a log.  Fill it event by event, case by case."""

    def __init__(self) -> None:
        self.case_ids: list[str] = []
        #: Case attributes, compactly: the keys of each case (one shared tuple
        #: for every case with the same keys) and its values as a tuple.  A
        #: quarter of a million cases with 16 attributes each costs a fraction
        #: of a dict per case.  :meth:`case_attributes` gives one case's dict.
        self.case_keys: list[tuple[str, ...]] = []
        self.case_values: list[tuple] = []
        self._key_tuples: dict[tuple[str, ...], tuple[str, ...]] = {}
        #: One string object per distinct text, so a value repeated on every
        #: event ("batch_00", a vendor's name) is stored once.
        self._strings: dict[str, str] = {}
        self.offsets = array("q", [0])
        self.activity = Codes()
        self.lifecycle = Codes()
        self.resource = Codes()
        self.micros = array("q")          # microseconds since the epoch; NO_TIME when absent
        self.offset_minutes = array("i")  # the timestamp's UTC offset
        #: Other event attributes: key -> list of values (None where absent).
        self.extra: dict[str, list[Any]] = {}
        #: Event attribute keys in the order they first appeared, so a trace
        #: built from the columns writes its attributes in the file's order.
        self.key_order: list[str] = []
        self._known: set[str] = set()

    # -- filling -------------------------------------------------------------------
    def add_event(self, attributes: dict[str, Any]) -> None:
        """One event from its attribute dict (the reader has a faster path)."""
        self.add(attributes.get(KEY_NAME), attributes.get(KEY_TIME), attributes.get(KEY_LIFECYCLE),
                 attributes.get(KEY_RESOURCE),
                 [(k, v) for k, v in attributes.items() if k not in _STANDARD])

    def add(self, activity, stamp, lifecycle, resource, extras: list[tuple[str, Any]],
            order: list[str] | None = None) -> None:
        if order is not None:
            for key in order:
                if key not in self._known:
                    self._known.add(key)
                    self.key_order.append(key)
        self.activity.add(_text(activity))
        self.lifecycle.add(_text(lifecycle))
        self.resource.add(_text(resource))
        if isinstance(stamp, datetime):
            self.micros.append(_micros(stamp))
            offset = stamp.utcoffset()
            self.offset_minutes.append(int(offset.total_seconds() // 60) if offset is not None else 0)
        else:
            self.micros.append(NO_TIME)
            self.offset_minutes.append(0)
        position = len(self.micros) - 1
        if extras:
            strings = self._strings
            for key, value in extras:
                if type(value) is str:
                    value = strings.setdefault(value, value)
                column = self.extra.get(key)
                if column is None:
                    column = self.extra[key] = [None] * position
                column.append(value)
        if len(self.extra) != len(extras):
            for column in self.extra.values():
                if len(column) <= position:
                    column.append(None)

    def end_case(self, attributes: dict[str, Any]) -> None:
        keys = tuple(attributes)
        strings = self._strings
        self.case_keys.append(self._key_tuples.setdefault(keys, keys))
        self.case_values.append(tuple(strings.setdefault(v, v) if type(v) is str else v
                                      for v in attributes.values()))
        self.case_ids.append(_text(attributes.get(KEY_NAME)) or "")
        self.offsets.append(len(self.micros))

    def case_attributes(self, case: int) -> dict[str, Any]:
        """The attributes of one case, as the object reader would give them."""
        return dict(zip(self.case_keys[case], self.case_values[case]))

    def subset(self, cases) -> "ColumnStore":
        """A store with only ``cases`` (indices, in that order): what a filter
        keeps of a large log stays in columns, its strings shared."""
        new = ColumnStore()
        new.key_order, new._known = list(self.key_order), set(self._known)
        new._key_tuples, new._strings = self._key_tuples, self._strings
        for codes, target in ((self.activity, new.activity), (self.lifecycle, new.lifecycle),
                              (self.resource, new.resource)):
            target.values, target._index = list(codes.values), dict(codes._index)
        extra = {key: [] for key in self.extra}
        for case in cases:
            start, stop = self.offsets[case], self.offsets[case + 1]
            new.case_ids.append(self.case_ids[case])
            new.case_keys.append(self.case_keys[case])
            new.case_values.append(self.case_values[case])
            new.activity.codes.extend(self.activity.codes[start:stop])
            new.lifecycle.codes.extend(self.lifecycle.codes[start:stop])
            new.resource.codes.extend(self.resource.codes[start:stop])
            new.micros.extend(self.micros[start:stop])
            new.offset_minutes.extend(self.offset_minutes[start:stop])
            for key, column in self.extra.items():
                extra[key].extend(column[start:stop])
            new.offsets.append(len(new.micros))
        new.extra = extra
        return new

    # -- reading -------------------------------------------------------------------
    @property
    def case_count(self) -> int:
        return len(self.case_ids)

    @property
    def event_count(self) -> int:
        return len(self.micros)

    def timestamp(self, index: int) -> datetime | None:
        micros = self.micros[index]
        if micros == NO_TIME:
            return None
        zone = timezone(timedelta(minutes=self.offset_minutes[index]))
        return _EPOCH.astimezone(zone) + timedelta(microseconds=micros)

    def event_attributes(self, index: int) -> dict[str, Any]:
        """The attributes of one event, as the object reader would give them."""
        attributes: dict[str, Any] = {}
        activity = self.activity.get(index)
        if activity is not None:
            attributes[KEY_NAME] = activity
        stamp = self.timestamp(index)
        if stamp is not None:
            attributes[KEY_TIME] = stamp
        lifecycle = self.lifecycle.get(index)
        if lifecycle is not None:
            attributes[KEY_LIFECYCLE] = lifecycle
        resource = self.resource.get(index)
        if resource is not None:
            attributes[KEY_RESOURCE] = resource
        for key, column in self.extra.items():
            value = column[index]
            if value is not None:
                attributes[key] = value
        if self.key_order:
            ordered = {key: attributes[key] for key in self.key_order if key in attributes}
            ordered.update({k: v for k, v in attributes.items() if k not in ordered})
            return ordered
        return attributes

    def trace(self, case: int):
        from .log import Event, Trace
        start, end = self.offsets[case], self.offsets[case + 1]
        return Trace(self.case_attributes(case),
                     [Event(self.event_attributes(i)) for i in range(start, end)])

    def span(self, case: int) -> tuple[int, int] | None:
        """The event indices of a case's earliest and latest timestamp (None:
        it has none), read from the array without building an event."""
        micros, start, end = self.micros, self.offsets[case], self.offsets[case + 1]
        low = high = None
        for i in range(start, end):
            value = micros[i]
            if value == NO_TIME:
                continue
            if low is None or value < micros[low]:
                low = i
            if high is None or value > micros[high]:
                high = i
        return None if low is None else (low, high)

    def column_text(self, key: str) -> list[str | None]:
        """Every event's value of one attribute as text (None where absent),
        straight from the arrays: what a view that colours or groups events
        by an attribute needs, without building the events."""
        for standard, codes in ((KEY_NAME, self.activity), (KEY_LIFECYCLE, self.lifecycle),
                                (KEY_RESOURCE, self.resource)):
            if key == standard:
                values = codes.values
                return [None if code < 0 else values[code] for code in codes.codes]
        column = self.extra.get(key)
        if column is None:
            return [None] * self.event_count
        return [None if value is None else str(value) for value in column]

    def has_lifecycle_pairs(self) -> bool:
        seen = {v.lower() for v in self.lifecycle.values}
        return {"start", "complete"} <= seen

    def sequences(self, keys: tuple[str, ...], lifecycles: frozenset[str] | None) -> list[tuple[str, ...]]:
        """Activity sequences under a classifier, straight from the arrays."""
        activity, lifecycle, resource = self.activity, self.lifecycle, self.resource
        columns = []
        for key in keys:
            if key == KEY_NAME:
                columns.append((activity.codes, activity.values))
            elif key == KEY_LIFECYCLE:
                columns.append((lifecycle.codes, lifecycle.values))
            elif key == KEY_RESOURCE:
                columns.append((resource.codes, resource.values))
            else:
                extra = self.extra.get(key)
                columns.append((None, extra))
        wanted = None if lifecycles is None else {v.lower() for v in lifecycles}
        out = []
        offsets = self.offsets
        for case in range(len(self.case_ids)):
            labels = []
            for i in range(offsets[case], offsets[case + 1]):
                if wanted is not None:
                    code = lifecycle.codes[i]
                    if code >= 0 and lifecycle.values[code].lower() not in wanted:
                        continue
                parts = []
                for codes, values in columns:
                    if codes is None:
                        value = values[i] if values is not None else None
                        parts.append("" if value is None else str(value))
                    else:
                        code = codes[i]
                        parts.append("" if code < 0 else values[code])
                labels.append("+".join(parts))
            out.append(tuple(labels))
        return out


_STANDARD = frozenset((KEY_NAME, KEY_TIME, KEY_LIFECYCLE, KEY_RESOURCE))
NO_TIME = -(1 << 62)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _micros(stamp: datetime) -> int:
    delta = stamp - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _text(value) -> str | None:
    return None if value is None else str(value)


class LazyTraces(MutableSequence):
    """The traces of a log, built from a :class:`ColumnStore` on demand."""

    def __init__(self, store: ColumnStore) -> None:
        self.store = store
        self._built: list = [None] * store.case_count
        self._list: list | None = None          # set once the log was edited

    # -- the fast paths -------------------------------------------------------------
    @property
    def modified(self) -> bool:
        return self._list is not None

    @property
    def event_count(self) -> int:
        if self._list is not None:
            return sum(len(t) for t in self._list)
        return self.store.event_count

    def sequences(self, keys: tuple[str, ...], lifecycles: frozenset[str] | None):
        return self.store.sequences(keys, lifecycles) if self._list is None else None

    def materialise(self) -> list:
        """Every trace as an object; from then on this is a plain list."""
        if self._list is None:
            self._list = [self[i] for i in range(len(self))]
            self._built = []
        return self._list

    # -- the sequence protocol ------------------------------------------------------
    def __len__(self) -> int:
        return len(self._list) if self._list is not None else self.store.case_count

    def __getitem__(self, index):
        if self._list is not None:
            return self._list[index]
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        trace = self._built[index]
        if trace is None:
            trace = self._built[index] = self.store.trace(index)
        return trace

    def __iter__(self) -> Iterator:
        if self._list is not None:
            return iter(self._list)
        return (self[i] for i in range(len(self)))

    def __setitem__(self, index, value) -> None:
        self.materialise()[index] = value

    def __delitem__(self, index) -> None:
        del self.materialise()[index]

    def insert(self, index: int, value) -> None:
        self.materialise().insert(index, value)

    def __repr__(self) -> str:
        return f"<LazyTraces {len(self)} cases, {self.event_count} events>"
