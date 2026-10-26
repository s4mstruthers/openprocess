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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import numpy as np

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

    # -- as numpy arrays (views of the same memory; nothing is copied) --------------
    @property
    def micros_array(self) -> np.ndarray:
        return as_array(self.micros, np.int64)

    @property
    def offsets_array(self) -> np.ndarray:
        return as_array(self.offsets, np.int64)

    def case_of_event(self) -> np.ndarray:
        """Each event's case index."""
        offsets = self.offsets_array
        return np.repeat(np.arange(self.case_count), np.diff(offsets))

    def case_spans(self) -> tuple[np.ndarray, np.ndarray]:
        """Per case, the event indices of its earliest and latest timestamp
        (-1 where it has none): one pass over the arrays, kept."""
        spans = getattr(self, "_spans", None)
        if spans is None or len(spans[0]) != self.case_count:
            micros, offsets = self.micros_array, self.offsets_array
            count, n = self.case_count, len(micros)
            low = np.full(count, -1, dtype=np.int64)
            high = np.full(count, -1, dtype=np.int64)
            if n and count:
                has_time = micros != NO_TIME
                starts = offsets[:-1]
                nonempty = np.diff(offsets) > 0
                # The earliest: the smallest stamp per case (NO_TIME masked out), then
                # the first event carrying it; the latest likewise.
                big = np.iinfo(np.int64).max
                for target, values, pick in ((low, np.where(has_time, micros, big), np.minimum),
                                             (high, micros, np.maximum)):
                    best = np.full(count, big if pick is np.minimum else NO_TIME, dtype=np.int64)
                    best[nonempty] = pick.reduceat(values, starts[nonempty])
                    hit = (values == np.repeat(best, np.diff(offsets))) & has_time
                    where = np.where(hit, np.arange(n), n)
                    first = np.full(count, n, dtype=np.int64)
                    first[nonempty] = np.minimum.reduceat(where, starts[nonempty])
                    target[first < n] = first[first < n]
            spans = self._spans = (low, high)
        return spans

    def span(self, case: int) -> tuple[int, int] | None:
        """The event indices of a case's earliest and latest timestamp (None:
        it has none), read from the array without building an event."""
        low, high = self.case_spans()
        return None if low[case] < 0 else (int(low[case]), int(high[case]))

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

    def present_keys(self) -> set[str]:
        """The event attribute keys some event has, from the arrays."""
        keys = {key for key, column in self.extra.items() if any(v is not None for v in column)}
        for key, codes in ((KEY_NAME, self.activity), (KEY_LIFECYCLE, self.lifecycle),
                           (KEY_RESOURCE, self.resource)):
            if codes.values and max(codes.codes, default=-1) >= 0:
                keys.add(key)
        if self.micros and max(self.micros) != NO_TIME:
            keys.add(KEY_TIME)
        return keys

    def first_offset(self) -> int | None:
        """The UTC offset (minutes) of the first event with a timestamp, or None."""
        for index, value in enumerate(self.micros):
            if value != NO_TIME:
                return self.offset_minutes[index]
        return None

    def is_plain(self) -> bool:
        """Nothing but activity names (and case ids): no timestamps, no other attributes."""
        if any(key != KEY_NAME for keys in set(self.case_keys) for key in keys):
            return False
        return self.present_keys() <= {KEY_NAME}

    def event_labels(self, keys: tuple[str, ...]) -> list[str]:
        """Every event's label under ``keys`` (the classifier's; lifecycles not
        applied), straight from the arrays."""
        if len(keys) == 1 and keys[0] == KEY_NAME:
            values = self.activity.values
            return ["" if code < 0 else values[code] for code in self.activity.codes]
        columns = []
        for key in keys:
            codes = {KEY_NAME: self.activity, KEY_LIFECYCLE: self.lifecycle,
                     KEY_RESOURCE: self.resource}.get(key)
            if codes is not None:
                values = codes.values
                columns.append(["" if code < 0 else values[code] for code in codes.codes])
            else:
                extra = self.extra.get(key)
                columns.append(["" if v is None else str(v) for v in extra] if extra is not None
                               else [""] * self.event_count)
        return ["+".join(parts) for parts in zip(*columns)] if columns else [""] * self.event_count

    def accepted(self, lifecycles: frozenset[str] | None) -> list[bool] | None:
        """Per event, whether a classifier keeping ``lifecycles`` keeps it (None: all)."""
        if lifecycles is None:
            return None
        wanted = {v.lower() for v in lifecycles}
        by_code = [v.lower() in wanted for v in self.lifecycle.values]
        return [code < 0 or by_code[code] for code in self.lifecycle.codes]

    def select_events(self, kept: list[tuple[int, list[int]]]) -> "ColumnStore":
        """A store with, per ``(case, event indices)`` in ``kept``, only those
        events: the "keep events" filter of a large log, still in columns."""
        from operator import itemgetter
        new = ColumnStore()
        new.key_order, new._known = list(self.key_order), set(self._known)
        new._key_tuples, new._strings = self._key_tuples, self._strings
        for codes, target in ((self.activity, new.activity), (self.lifecycle, new.lifecycle),
                              (self.resource, new.resource)):
            target.values, target._index = list(codes.values), dict(codes._index)
        indices: list[int] = []
        for case, events in kept:
            new.case_ids.append(self.case_ids[case])
            new.case_keys.append(self.case_keys[case])
            new.case_values.append(self.case_values[case])
            indices.extend(events)
            new.offsets.append(len(indices))
        if indices:
            gather = itemgetter(*indices)
            pick = (lambda column: (gather(column),)) if len(indices) == 1 else gather
            new.activity.codes.extend(pick(self.activity.codes))
            new.lifecycle.codes.extend(pick(self.lifecycle.codes))
            new.resource.codes.extend(pick(self.resource.codes))
            new.micros.extend(pick(self.micros))
            new.offset_minutes.extend(pick(self.offset_minutes))
            new.extra = {key: list(pick(column)) for key, column in self.extra.items()}
        else:
            new.extra = {key: [] for key in self.extra}
        return new

    def accepted_mask(self, lifecycles: frozenset[str] | None) -> np.ndarray | None:
        """Per event, whether a classifier keeping ``lifecycles`` keeps it (None: all)."""
        if lifecycles is None:
            return None
        wanted = {v.lower() for v in lifecycles}
        by_code = np.array([v.lower() in wanted for v in self.lifecycle.values] + [True], dtype=bool)
        codes = as_array(self.lifecycle.codes, np.int32)
        return by_code[np.where(codes < 0, len(by_code) - 1, codes)]

    def label_codes(self, keys: tuple[str, ...]) -> tuple[np.ndarray, list[str]]:
        """Every event's label under ``keys`` as a code into the returned table
        (lifecycles not applied): the labels as small integers."""
        codes = {KEY_NAME: self.activity, KEY_LIFECYCLE: self.lifecycle,
                 KEY_RESOURCE: self.resource}.get(keys[0]) if len(keys) == 1 else None
        if codes is not None:
            table = list(codes.values) + [""]
            raw = as_array(codes.codes, np.int32)
            return np.where(raw < 0, len(table) - 1, raw).astype(np.int64), table
        labels = self.event_labels(keys)
        index: dict[str, int] = {}
        out = np.fromiter((index.setdefault(label, len(index)) for label in labels),
                          dtype=np.int64, count=len(labels))
        return out, list(index)

    def sequences(self, keys: tuple[str, ...], lifecycles: frozenset[str] | None) -> list[tuple[str, ...]]:
        """Activity sequences under a classifier, straight from the arrays."""
        if len(keys) == 1 and keys[0] in (KEY_NAME, KEY_LIFECYCLE, KEY_RESOURCE):
            codes, table = self.label_codes(keys)
            offsets = self.offsets_array
            mask = self.accepted_mask(lifecycles)
            if mask is not None:
                codes = codes[mask]
                kept = np.zeros(self.case_count, dtype=np.int64)
                nonempty = np.diff(offsets) > 0
                if len(mask):
                    kept[nonempty] = np.add.reduceat(mask.astype(np.int64), offsets[:-1][nonempty])
                offsets = np.concatenate(([0], np.cumsum(kept)))
            labels = np.asarray(table, dtype=object)[codes].tolist()
            bounds = offsets.tolist()
            return [tuple(labels[a:b]) for a, b in zip(bounds, bounds[1:])]
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


def as_array(values: array, dtype) -> np.ndarray:
    """A numpy view of an ``array.array`` (no copy; empty arrays have no buffer
    to view, so those become an empty numpy array)."""
    if len(values) == 0:
        return np.zeros(0, dtype=dtype)
    return np.frombuffer(values, dtype=dtype)


def to_array(values: np.ndarray, typecode: str) -> array:
    """An ``array.array`` holding a numpy array's values (one copy, in C)."""
    out = array(typecode)
    out.frombytes(np.ascontiguousarray(values, dtype=_DTYPES[typecode]).tobytes())
    return out


_DTYPES = {"i": np.int32, "q": np.int64}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_EPOCH_ORDINAL = _EPOCH.toordinal()
_DAY = 86_400_000_000


class IsoFormatter:
    """ISO 8601 text of a stored timestamp, as ``datetime.isoformat`` writes
    it, without building the datetime: a writer formats a million of them.

    ``timespec`` is ``"auto"`` (microseconds only when there are any) or
    ``"milliseconds"``; the days and the offsets seen are remembered.
    """

    def __init__(self, timespec: str = "auto") -> None:
        self.timespec = timespec
        self._days: dict[int, str] = {}
        self._offsets: dict[int, str] = {}

    def __call__(self, micros: int, offset_minutes: int) -> str:
        days, rest = divmod(micros + offset_minutes * 60_000_000, _DAY)
        day = self._days.get(days)
        if day is None:
            day = self._days[days] = date.fromordinal(_EPOCH_ORDINAL + days).isoformat()
        offset = self._offsets.get(offset_minutes)
        if offset is None:
            sign, minutes = ("-", -offset_minutes) if offset_minutes < 0 else ("+", offset_minutes)
            offset = self._offsets[offset_minutes] = f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"
        seconds, fraction = divmod(rest, 1_000_000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        if self.timespec == "milliseconds":
            tail = f".{fraction // 1000:03d}"
        else:
            tail = f".{fraction:06d}" if fraction else ""
        return f"{day}T{hours:02d}:{minutes:02d}:{seconds:02d}{tail}{offset}"


def _micros(stamp: datetime) -> int:
    if stamp.tzinfo is None:                      # a naive stamp counts as UTC, as the XES reader does
        stamp = stamp.replace(tzinfo=timezone.utc)
    delta = stamp - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _text(value) -> str | None:
    return None if value is None else str(value)


class LazyTraces(MutableSequence):
    """The traces of a log, built from a :class:`ColumnStore` on demand."""

    def __init__(self, store: ColumnStore) -> None:
        self.store = store
        self._built: list = [None] * store.case_count
        self._list: list | None = None          # set once a case was added or removed
        self._modified = False                  # set once a built trace was edited

    # -- the fast paths -------------------------------------------------------------
    @property
    def modified(self) -> bool:
        """Was the log edited?  Then the arrays are stale and every reader
        takes the trace objects (built on demand, so nothing is paid up front)."""
        return self._list is not None or self._modified

    def mark_modified(self) -> None:
        """A built trace or event was changed in place (the editor's cell
        edits, renames): say so, or the arrays would still answer for it."""
        self._modified = True

    @property
    def event_count(self) -> int:
        if self.modified:
            return sum(len(t) for t in self)
        return self.store.event_count

    def sequences(self, keys: tuple[str, ...], lifecycles: frozenset[str] | None):
        return None if self.modified else self.store.sequences(keys, lifecycles)

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
