"""The Coloured Petri Net data model.

Structure
---------
A model (:class:`CPNet`) owns a declaration block and a list of
:class:`Page` objects.  Each page holds :class:`Place`, :class:`Transition` and
:class:`Arc` objects.  Objects are identified by an opaque string ``id`` that
is stable across save/load, because arcs refer to their endpoints by id and
CPN Tools' file format does the same.

Two representations of every inscription
----------------------------------------
Each inscription (initial marking, arc expression, guard, time delay) is kept
twice:

* as the **source text** the modeller typed -- this is what gets saved and what
  the editor shows;
* as a **parsed AST**, produced lazily by :meth:`CPNet.compile` -- this is what
  the simulator runs.

Keeping the text authoritative means a model with one broken inscription still
loads, still displays, and still lets you fix the broken bit, instead of
failing to open at all.  Compilation errors are collected in
:attr:`CPNet.errors` rather than raised.

Graphics
--------
Layout attributes (position, size, colours, fonts) are carried on
:class:`Graphics` so that the GUI has somewhere to read and write them, and so
that a file round-trips looking the same.  The reader additionally stashes the
original XML element on each object (``source_element``), which lets the writer
preserve attributes this implementation does not model.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from ..ml.ast_nodes import Expr
from ..ml.colorsets import ColourSet
from ..ml.errors import CPNMLError
from ..ml.evaluator import Environment, Evaluator
from ..ml.multiset import Multiset, TimedMultiset
from ..ml.parser import parse_arc_expression, parse_expression
from .declarations import DeclarationBlock

# ---------------------------------------------------------------------------
# Identifier generation
# ---------------------------------------------------------------------------
_ID_COUNTER = itertools.count(1)
#: Ids of the form we make: an element built with one (read from a file)
#: moves the counter past it.
_NUMBERED_ID = re.compile(r"ID(\d+)")


def new_id(prefix: str = "ID") -> str:
    """Generate a fresh object id.

    CPN Tools uses ids of the form ``ID1234567890``.  We only need uniqueness
    within a file, so a process-wide counter is enough; the reader preserves
    ids from imported files rather than renumbering them.
    """
    return f"{prefix}{next(_ID_COUNTER)}"


def _keep_unique(identifier: str) -> None:
    """An element was built with ``identifier``: new ids must not repeat it.

    The counter starts at 1 in every session, while a file saved in an
    earlier one already uses ``ID1``, ``ID2``, ...: a place added after
    opening it could get a transition's id, and the file could no longer be
    saved.
    """
    global _ID_COUNTER
    match = _NUMBERED_ID.fullmatch(identifier)
    if match is None:
        return
    number = int(match.group(1))
    upcoming = next(_ID_COUNTER)
    _ID_COUNTER = itertools.count(max(upcoming, number + 1))


# ---------------------------------------------------------------------------
# Graphical attributes
# ---------------------------------------------------------------------------
@dataclass
class Graphics:
    """Layout and appearance of one net element.

    Coordinates follow CPN Tools' convention: the origin is at the centre of
    the page and **y increases upwards**, which is the opposite of every screen
    coordinate system.  The GUI flips the sign when drawing; storing the file's
    convention here keeps the reader and writer trivial.
    """

    x: float = 0.0
    y: float = 0.0
    width: float = 60.0
    height: float = 40.0
    fill_colour: str = "White"
    line_colour: str = "Black"
    line_width: float = 1.0
    text_colour: str = "Black"
    font_size: int = 12
    #: Offsets of the annotation labels relative to the element's own position
    #: (for arcs: absolute positions, since an arc has no position of its own).
    #: Keys are the CPN Tools element names: type, initmark, cond, time, annot,
    #: and token / marking for the current-marking bubble and its text.
    label_offsets: dict[str, tuple[float, float]] = field(default_factory=dict)
    #: Places only: CPN Tools' "hide marking" flag.  When set, only the token
    #: count is drawn and the multiset itself appears on hover -- what keeps
    #: busy models readable.
    marking_hidden: bool = False


# ---------------------------------------------------------------------------
# Net elements
# ---------------------------------------------------------------------------
@dataclass
class Place:
    """A place: a typed container of tokens.

    ``colour_set_name`` names an entry in the model's colour set registry.  It
    is stored as a *name* rather than as a resolved object so that editing a
    colour set declaration updates every place that uses it without a rebind
    step.
    """

    id: str = field(default_factory=lambda: new_id("ID"))
    name: str = ""
    colour_set_name: str = "UNIT"
    initial_marking_text: str = ""
    graphics: Graphics = field(default_factory=Graphics)
    #: Set when this place is a port (interface to a superpage) in a
    #: hierarchical model: one of ``"In"``, ``"Out"``, ``"I/O"``, ``"General"``.
    port_type: str | None = None
    #: Name of the fusion set this place belongs to, if any.  Places in the
    #: same fusion set share a single marking.
    fusion_group: str | None = None
    #: The original XML element, kept for lossless re-serialisation.
    source_element: Any = None

    # Filled in by :meth:`CPNet.compile`.
    initial_marking_ast: Expr | None = None

    def __post_init__(self) -> None:
        _keep_unique(self.id)

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class Transition:
    """A transition: an action that consumes and produces tokens.

    ``guard_text`` holds the boolean condition (CPN Tools writes it in square
    brackets on the diagram, but stores it without them).  ``time_text`` is the
    transition-level delay ``@+ e`` applied to all output tokens; an output
    arc's own ``@+`` adds to it.
    """

    id: str = field(default_factory=lambda: new_id("ID"))
    name: str = ""
    guard_text: str = ""
    time_text: str = ""
    code_text: str = ""
    priority_text: str = ""
    graphics: Graphics = field(default_factory=Graphics)
    #: For a substitution transition: the id of the subpage it stands for.
    substitution_subpage: str | None = None
    #: Port/socket assignments for a substitution transition, ``socket -> port``.
    port_assignments: dict[str, str] = field(default_factory=dict)
    source_element: Any = None
    #: Plain nets only: a silent (τ) transition, drawn as a black bar and
    #: invisible in the traces it produces.
    silent: bool = False

    guard_ast: Expr | None = None
    time_ast: Expr | None = None

    @property
    def is_substitution(self) -> bool:
        return self.substitution_subpage is not None

    def __post_init__(self) -> None:
        _keep_unique(self.id)

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class Arc:
    """A directed arc between a place and a transition.

    ``orientation`` mirrors the file format:

    ``"PtoT"``
        place to transition -- an *input* arc; its inscription describes tokens
        that are **consumed**.
    ``"TtoP"``
        transition to place -- an *output* arc; tokens are **produced**.
    ``"BOTHDIR"``
        a double-headed arc, shorthand for one of each with the same
        inscription.  We expand it during simulation rather than in the model,
        so that the diagram keeps the single arc the modeller drew.
    """

    id: str = field(default_factory=lambda: new_id("ID"))
    place_id: str = ""
    transition_id: str = ""
    orientation: str = "PtoT"
    expression_text: str = ""
    graphics: Graphics = field(default_factory=Graphics)
    bendpoints: list[tuple[float, float]] = field(default_factory=list)
    source_element: Any = None

    expression_ast: Expr | None = None

    @property
    def is_input(self) -> bool:
        """Does this arc take tokens *out* of the place?"""
        return self.orientation in ("PtoT", "BOTHDIR")

    @property
    def is_output(self) -> bool:
        """Does this arc put tokens *into* the place?"""
        return self.orientation in ("TtoP", "BOTHDIR")

    def __post_init__(self) -> None:
        _keep_unique(self.id)

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class Page:
    """One diagram page.  A model is a forest of pages linked by substitution."""

    id: str = field(default_factory=lambda: new_id("ID"))
    name: str = "New Page"
    places: list[Place] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    source_element: Any = None

    def __post_init__(self) -> None:
        _keep_unique(self.id)

    # -- lookup helpers ------------------------------------------------------
    def place(self, place_id: str) -> Place | None:
        return next((p for p in self.places if p.id == place_id), None)

    def transition(self, transition_id: str) -> Transition | None:
        return next((t for t in self.transitions if t.id == transition_id), None)

    def arcs_of(self, transition: Transition) -> list[Arc]:
        return [a for a in self.arcs if a.transition_id == transition.id]

    def input_arcs(self, transition: Transition) -> list[Arc]:
        return [a for a in self.arcs_of(transition) if a.is_input]

    def output_arcs(self, transition: Transition) -> list[Arc]:
        return [a for a in self.arcs_of(transition) if a.is_output]


@dataclass
class CompileIssue:
    """One problem found while compiling inscriptions.

    Collected rather than raised, so that a model with errors still opens.
    """

    element_id: str
    element_name: str
    field_name: str
    message: str

    def __str__(self) -> str:
        target = f"{self.element_name or self.element_id}"
        return f"{target} ({self.field_name}): {self.message}"


class CPNet:
    """A complete CPN model: declarations plus pages.

    Typical use::

        net = read_cpn("model.cpn")      # or build one programmatically
        net.compile()                    # parse inscriptions, build colour sets
        marking = net.initial_marking()
        sim = Simulator(net, marking)
    """

    def __init__(self, name: str = "Untitled", seed: int | None = None) -> None:
        self.name = name
        #: Seed for the model's own random functions (``uniform``, ``discrete``
        #: ...).  Kept so that :meth:`compile` can seed the fresh evaluator.
        self.seed = seed
        self.declarations = DeclarationBlock()
        self.pages: list[Page] = []
        #: Drawn and edited as a plain Petri net (black tokens, arc weights;
        #: see :mod:`openprocess.model.plain`) rather than as a coloured net.
        self.plain = False
        #: Names written under places and transitions (and draggable) rather
        #: than inside them.
        self.names_outside = False
        #: Fusion sets: name -> list of place ids that share one marking.
        self.fusion_sets: dict[str, list[str]] = {}
        #: The evaluator holding the standard basis and the model's own
        #: declarations.  Recreated by :meth:`compile`.
        self.evaluator = Evaluator(seed=seed)
        self.errors: list[CompileIssue] = []
        #: What the model says that OpenProcess does not do (a transition's
        #: priority, its code segment): it still simulates, differently from
        #: CPN Tools.  Filled by :meth:`compile`.
        self.warnings: list[CompileIssue] = []
        #: place id -> the id under which its tokens are stored in a Marking.
        #: Identity for ordinary places; the group representative for places in
        #: a fusion set (see :meth:`marking_key`).
        self._fusion_representative: dict[str, str] = {}
        self._assigned_ports: set[str] = set()
        #: Preserved from the source file so that saving reproduces the header.
        self.source_tree: Any = None

    # -- traversal -----------------------------------------------------------
    def all_places(self) -> Iterator[Place]:
        for page in self.pages:
            yield from page.places

    def all_transitions(self) -> Iterator[Transition]:
        for page in self.pages:
            yield from page.transitions

    def all_arcs(self) -> Iterator[Arc]:
        for page in self.pages:
            yield from page.arcs

    def page_of(self, element: Place | Transition | Arc) -> Page | None:
        for page in self.pages:
            if element in page.places or element in page.transitions or element in page.arcs:
                return page
        return None

    def find_place(self, place_id: str) -> Place | None:
        return next((p for p in self.all_places() if p.id == place_id), None)

    def find_transition(self, transition_id: str) -> Transition | None:
        return next((t for t in self.all_transitions() if t.id == transition_id), None)

    def colour_set_of(self, place: Place) -> ColourSet | None:
        return self.declarations.colour_sets.get(place.colour_set_name)

    # -- compilation ---------------------------------------------------------
    def compile(self) -> list[CompileIssue]:
        """Build colour sets and parse every inscription.

        Returns the list of problems found (also stored on :attr:`errors`).  An
        empty list means the model is ready to simulate.
        """
        self.errors = []
        self.warnings = []
        self.evaluator = Evaluator(seed=self.seed)
        self._build_fusion_map()

        # 1. Declarations first: colour sets, variables, val/fun bindings.
        try:
            self.declarations.compile(self.evaluator)
        except CPNMLError as error:
            self.errors.append(CompileIssue("", self.name, "declarations", str(error)))
            # Fall back to just the standard colour sets so that places whose
            # colour set is INT/BOOL/... still work.
            from ..ml.colorsets import standard_colour_sets
            self.declarations.colour_sets = standard_colour_sets()

        # 2. Places: colour set existence and the initial marking expression.
        for place in self.all_places():
            place.initial_marking_ast = None
            if place.colour_set_name not in self.declarations.colour_sets:
                self._record(place.id, place.name, "colour set",
                             f"unknown colour set '{place.colour_set_name}'")
            text = place.initial_marking_text.strip()
            if text:
                try:
                    place.initial_marking_ast = parse_expression(text)
                except CPNMLError as error:
                    self._record(place.id, place.name, "initial marking", str(error))

        # 3. Transitions: guard and time delay.
        for transition in self.all_transitions():
            transition.guard_ast = None
            transition.time_ast = None
            guard = _strip_brackets(transition.guard_text)
            if guard:
                try:
                    transition.guard_ast = parse_guard(guard)
                except CPNMLError as error:
                    self._record(transition.id, transition.name, "guard", str(error))
            priority = transition.priority_text.strip()
            if priority and priority != "P_NORMAL":
                self.warnings.append(CompileIssue(
                    transition.id, transition.name, "priority",
                    f"the priority {priority} is not used: priorities are not implemented, so "
                    "this transition can fire whenever it is enabled, also when CPN Tools "
                    "would let another one go first. Simulation and the state space can "
                    "differ from CPN Tools."))
            if transition.code_text.strip():
                self.warnings.append(CompileIssue(
                    transition.id, transition.name, "code segment",
                    "the code segment is not run: code segments are not implemented, so its "
                    "action does not happen and the variables it would set stay unbound or "
                    "are bound freely. Simulation and the state space can differ from CPN Tools."))
            delay = transition.time_text.strip().lstrip("@+").strip()
            if delay:
                try:
                    transition.time_ast = parse_expression(delay)
                except CPNMLError as error:
                    self._record(transition.id, transition.name, "time delay", str(error))

        # 4. Arcs: the inscription, which may carry a `@+` delay.
        for arc in self.all_arcs():
            arc.expression_ast = None
            text = arc.expression_text.strip()
            if not text:
                # An empty inscription on an arc into or out of a UNIT place is
                # CPN Tools shorthand for a single black token.
                text = "1`()"
            try:
                arc.expression_ast = parse_arc_expression(text)
            except CPNMLError as error:
                self._record(arc.id, f"arc {arc.id}", "inscription", str(error))

        return self.errors

    def _record(self, element_id: str, name: str, field_name: str, message: str) -> None:
        self.errors.append(CompileIssue(element_id, name, field_name, message))

    # -- fusion sets ---------------------------------------------------------
    def _build_fusion_map(self) -> None:
        """Work out which places share a marking.

        Two constructs make several drawn places one place:

        * A **fusion set** -- places, possibly on different pages, that are
          one place drawn several times.
        * A **substitution transition** stands for a subpage.  Each *port*
          place on the subpage is assigned a *socket* place around the
          substitution transition, and is that same place: tokens put into
          the socket are in the port, and the subpage's transitions consume
          and produce them there.  This is how a hierarchical model runs as
          one flat net.

        Both are handled alike: every place of a group gets the same storage
        key in the marking (union-find, so a port of a port of a socket ends
        up with the socket).  The key is the socket for ports, and the first
        member for a fusion set, so the choice is deterministic.

        A subpage used by several substitution transitions would need one
        copy of its places per use; that is reported as a problem rather
        than simulated wrongly with the instances sharing their tokens.
        """
        self._fusion_representative = {}
        #: Port places assigned to a socket: their own initial marking is
        #: ignored, as in CPN Tools (the socket's counts).
        self._assigned_ports = set()
        parent: dict[str, str] = {}

        def find(place_id: str) -> str:
            root = place_id
            while parent.get(root, root) != root:
                root = parent[root]
            while parent.get(place_id, place_id) != root:       # path compression
                parent[place_id], place_id = root, parent[place_id]
            return root

        def join(keep: str, other: str) -> None:
            """Merge the groups; ``keep``'s representative stays."""
            keep_root, other_root = find(keep), find(other)
            parent.setdefault(keep_root, keep_root)
            if keep_root != other_root:
                parent[other_root] = keep_root

        groups: dict[str, list[str]] = {}
        # Places may declare their group inline (``fusioninfo``) or be listed
        # in a top-level ``<fusion>`` element; merge both sources.
        for place in self.all_places():
            if place.fusion_group:
                groups.setdefault(place.fusion_group, []).append(place.id)
        for group, members in self.fusion_sets.items():
            existing = groups.setdefault(group, [])
            for member in members:
                if member not in existing:
                    existing.append(member)
        for members in groups.values():
            for member in members:
                join(members[0], member)

        pages = {page.id: page for page in self.pages}
        uses: dict[str, list[Transition]] = {}
        for page in self.pages:
            for transition in page.transitions:
                subpage = pages.get(transition.substitution_subpage or "")
                if transition.is_substitution and subpage is None:
                    self._record(transition.id, transition.name, "subpage",
                                 f"its subpage ({transition.substitution_subpage}) is not "
                                 "in the model")
                if subpage is None:
                    continue
                uses.setdefault(subpage.id, []).append(transition)
                if len(uses[subpage.id]) > 1:
                    continue                  # reported below; do not merge instances
                own = {p.id for p in page.places}
                ports = {p.id for p in subpage.places}
                for first, second in transition.port_assignments.items():
                    # The file lists (socket, port) pairs; tell them apart by
                    # page rather than trusting the order.
                    if first in own and second in ports:
                        socket, port = first, second
                    elif second in own and first in ports:
                        socket, port = second, first
                    else:
                        self._record(transition.id, transition.name, "port assignment",
                                     f"{first} and {second} are not a socket on its page "
                                     "and a port on its subpage")
                        continue
                    join(socket, port)
                    self._assigned_ports.add(port)
        for page_id, transitions in uses.items():
            if len(transitions) > 1:
                names = ", ".join(f"'{t.name}'" for t in transitions)
                self._record(transitions[1].id, transitions[1].name, "subpage",
                             f"page '{pages[page_id].name}' is used by {len(transitions)} "
                             f"substitution transitions ({names}); OpenProcess runs each subpage "
                             "once, so give every use its own copy of the page")

        for place_id in parent:
            self._fusion_representative[place_id] = find(place_id)

    def marking_key(self, place_id: str) -> str:
        """The key under which ``place_id``'s tokens live in a :class:`Marking`."""
        return self._fusion_representative.get(place_id, place_id)

    # -- markings ------------------------------------------------------------
    def initial_marking(self) -> "Marking":
        """Evaluate every place's initial marking expression.

        Requires :meth:`compile` to have run.  Type-checks each token against
        the place's colour set, because an initial marking that does not fit is
        a modelling error worth catching immediately rather than three firings
        later.
        """
        marking = Marking()
        for place in self.all_places():
            colour_set = self.colour_set_of(place)
            timed = bool(colour_set and colour_set.timed)

            # (tokens, time stamp) groups: `1`x@5 +++ 1`y@0` stamps each term.
            groups: list[tuple[Multiset, Any]] = []
            if place.initial_marking_ast is not None:
                from ..ml.evaluator import expand_lists, to_multiset
                from ..ml.multiset import TimedTokens
                value = self.evaluator.evaluate(place.initial_marking_ast, self.evaluator.globals)
                if isinstance(value, TimedTokens):
                    groups = value.stamped(0)
                else:
                    groups = [(to_multiset(value), 0)]
                groups = [(expand_lists(part, colour_set), stamp) for part, stamp in groups]
                if colour_set is not None:
                    for part, _stamp in groups:
                        for token, _count in part.items():
                            if not colour_set.contains(token):
                                self._record(
                                    place.id, place.name, "initial marking",
                                    f"token {token} is not a member of '{colour_set.name}'",
                                )
            key = self.marking_key(place.id)
            if place.id in self._assigned_ports:
                # A port is its socket: the socket's initial marking counts.
                if key not in marking.place_ids():
                    marking.set(key, TimedMultiset.empty() if timed else Multiset.empty())
                continue
            if timed:
                stored = TimedMultiset.empty()
                for part, stamp in groups:
                    stored = stored.add(TimedMultiset.from_multiset(part, stamp))
            else:
                stored = Multiset.empty()
                for part, _stamp in groups:
                    stored = stored + part
            # Members of a fusion set share one marking. CPN Tools expects their
            # initial marking expressions to agree; if they do not, the first
            # non-empty one wins and the rest are ignored.
            already = marking.get(key) if key in marking.place_ids() else None
            if already is not None and already.size():
                continue
            marking.set(key, stored)
        return marking

    # -- construction helpers (used by the GUI and by tests) -----------------
    def add_page(self, name: str = "New Page") -> Page:
        page = Page(name=name)
        self.pages.append(page)
        return page

    def add_declaration(self, source: str) -> None:
        """Add one declaration, routing it to the right bucket by keyword."""
        from .declarations import classify_declaration, parse_variable_declaration
        kind = classify_declaration(source)
        if kind == "colset":
            # The name is the identifier right after `colset`.
            name = source.split()[1].split("=")[0].strip()
            self.declarations.colour_set_sources.append((name, source))
        elif kind == "var":
            for declaration in parse_variable_declaration(source):
                self.declarations.variable_sources.append(
                    (declaration.name, declaration.colour_set_name, source)
                )
        elif kind == "globref":
            body = source.strip()[len("globref"):].strip().rstrip(";")
            name, _, initial = body.partition("=")
            self.declarations.globref_sources.append((name.strip(), initial.strip()))
        else:
            self.declarations.ml_sources.append(source)


class Marking:
    """A marking: what every place holds right now.

    Maps place id to a :class:`~openprocess.ml.multiset.Multiset` (untimed place) or a
    :class:`~openprocess.ml.multiset.TimedMultiset` (timed place).  Markings are
    treated as immutable values: :meth:`set` returns nothing but is only used
    while building one, and the simulator always constructs a *new* marking for
    the successor state rather than mutating the current one.  That is what
    makes state space exploration safe.
    """

    __slots__ = ("_places", "_hash")

    def __init__(self, places: dict[str, Any] | None = None) -> None:
        self._places: dict[str, Any] = dict(places or {})
        self._hash: int | None = None

    def get(self, place_id: str) -> Any:
        return self._places.get(place_id, Multiset.empty())

    def set(self, place_id: str, tokens: Any) -> None:
        self._places[place_id] = tokens
        self._hash = None

    def copy(self) -> "Marking":
        return Marking(self._places)

    def place_ids(self) -> Iterable[str]:
        return self._places.keys()

    def items(self) -> Iterable[tuple[str, Any]]:
        return sorted(self._places.items())

    def total_tokens(self) -> int:
        return sum(tokens.size() for tokens in self._places.values())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Marking) and self._places == other._places

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(frozenset((k, v) for k, v in self._places.items()))
        return self._hash

    def describe(self, net: CPNet) -> str:
        """Human-readable dump, using place names rather than ids."""
        lines = []
        for place in net.all_places():
            tokens = self.get(net.marking_key(place.id))
            if tokens.size():
                lines.append(f"{place.name}: {tokens}")
        return "\n".join(lines) if lines else "(all places empty)"

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in self.items() if v.size())
        return f"Marking({inner})"


def parse_guard(text: str):
    """Parse a guard, including CPN Tools' *list* form.

    A guard may be a single boolean expression, ``[x > 0]``, or a comma
    separated list, ``[r = cr + 1, c = cr]``, which means *all* conditions
    must hold.  We parse the list form as an ML list and fold it into a chain
    of ``andalso``, so the simulator only ever sees one boolean expression.
    """
    from ..ml.ast_nodes import BinOp, ListExpr
    try:
        return parse_expression(text)
    except CPNMLError as first_error:
        try:
            as_list = parse_expression("[" + text + "]")
        except CPNMLError:
            raise first_error from None
        if not isinstance(as_list, ListExpr) or not as_list.items:
            raise first_error
        guard = as_list.items[0]
        for condition in as_list.items[1:]:
            guard = BinOp("andalso", guard, condition)
        return guard


def _strip_brackets(text: str) -> str:
    """Guards are shown as ``[g]`` on the diagram; the brackets are not part
    of the expression.  CPN Tools stores them both ways depending on version,
    so we tolerate either."""
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return stripped
