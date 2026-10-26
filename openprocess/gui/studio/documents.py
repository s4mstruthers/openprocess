"""Open documents: the objects the sidebar lists.

A *document* wraps one thing the user opened or produced -- an event log or
a Petri net -- together with the derived data the views need (statistics,
activity colours) so that switching between pages never recomputes them.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ...mining.log import Classifier, EventLog
from ...mining.petrinet import PetriNet
from ...mining.stats import LogSummary, summarise
from .style import ActivityColours

_ids = itertools.count(1)


@dataclass(eq=False)
class LogDocument:
    log: EventLog
    path: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    #: Its file was deleted or moved away while it was open.
    missing: bool = False
    #: The text the log was written in, for a log typed in the course's
    #: notation or read from a ``….log.txt`` file (None for XES and CSV logs).
    notation: str | None = None
    #: The document (a workflow) this one was opened from, for the way back; not saved.
    opened_from: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.classifier: Classifier = self.log.default_classifier()
        #: The summary and the colours under each classifier looked at so far
        #: (by its key, see :func:`~openprocess.flow.loading.summary_key`):
        #: switching back to a classifier costs nothing.
        self._summaries: dict[tuple, LogSummary] = {}
        self._colours: dict[tuple, ActivityColours] = {}

    @property
    def name(self) -> str:
        return self.log.name

    def replace_log(self, log: EventLog) -> None:
        """The file was read again: show the new log (same classifier if it still applies)."""
        classifier = self.classifier
        self.log = log
        self.classifier = (classifier if classifier in log.available_classifiers()
                           else log.default_classifier())
        self._summaries.clear()
        self._colours.clear()

    def set_classifier(self, classifier: Classifier) -> None:
        self.classifier = classifier

    @property
    def summary(self) -> LogSummary:
        from ...flow.loading import summary_key
        key = summary_key(self.classifier)
        summary = self._summaries.get(key)
        if summary is None:
            # A large log read by a worker process comes with its summary worked out
            # there (by the same summarise(), for its default classifier).
            ready = (getattr(self.log, "precomputed_summaries", None) or {}).get(key)
            summary = ready if ready is not None else summarise(self.log, self.classifier)
            self._summaries[key] = summary
        return summary

    @property
    def colours(self) -> ActivityColours:
        from ...flow.loading import summary_key
        key = summary_key(self.classifier)
        colours = self._colours.get(key)
        if colours is None:
            colours = self._colours[key] = ActivityColours([a.name for a in self.summary.activities])
        return colours

    def simple_log(self):
        return self.log.simple_log(self.classifier)


@dataclass(eq=False)
class ModelDocument:
    net: PetriNet
    path: str | None = None
    #: Where it came from: "α-algorithm on <log>", "PNML file", ...
    origin: str = ""
    #: Algorithm-specific extras (AlphaResult, InductiveResult, ...)
    derivation: object | None = None
    source_log: LogDocument | None = None
    id: int = field(default_factory=lambda: next(_ids))
    #: Its file was deleted or moved away while it was open.
    missing: bool = False
    #: The document (a workflow) this one was opened from, for the way back; not saved.
    opened_from: object | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.net.name


@dataclass(eq=False)
class CpnDocument:
    """A coloured Petri net (CPN Tools model) being edited and simulated."""

    net: object                      # openprocess.model.net.CPNet
    path: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    #: Edited since it was opened or last saved.
    dirty: bool = False
    #: For a copy: where it came from ("from Inductive Miner in Discover and check").
    origin: str = ""
    #: Edits are saved by themselves (a file in the open folder): no "edited" mark.
    autosave: bool = False
    #: Its file was deleted or moved away while it was open.
    missing: bool = False
    #: The document (a workflow) this one was opened from, for the way back; not saved.
    opened_from: object | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.net.name


@dataclass(eq=False)
class ComparisonDocument:
    """Two or more open logs shown side by side (nothing is stored on disk)."""

    logs: list
    id: int = field(default_factory=lambda: next(_ids))
    path: str | None = None

    @property
    def name(self) -> str:
        if len(self.logs) == 2:
            return f"{self.logs[0].name} vs {self.logs[1].name}"
        return f"{len(self.logs)} logs compared"


@dataclass(eq=False)
class WorkflowDocument:
    """A workflow of boxes (an ``.analysis`` file), with its versions and the record of the latest."""

    workflow: object                 # openprocess.flow.workflow.Workflow
    path: str | None = None
    record: object | None = None     # openprocess.flow.record.Record
    versions: object | None = None   # openprocess.flow.versions.Versions
    id: int = field(default_factory=lambda: next(_ids))
    dirty: bool = False
    autosave: bool = False
    missing: bool = False

    @property
    def name(self) -> str:
        return self.workflow.name


@dataclass(eq=False)
class TransitionSystemDocument:
    """A transition system, typed or from a ``ts.txt`` file, to study its regions."""

    ts: object                       # openprocess.mining.transition_system.TransitionSystem
    path: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    #: Its file was deleted or moved away while it was open.
    missing: bool = False
    #: The document (a workflow) this one was opened from, for the way back; not saved.
    opened_from: object | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.ts.name
