"""Computed answers: the right answer worked out from the exercise's own files.

An answer block says ``compute: alpha.T_I`` instead of writing the answer
down, so an exercise about a log does not need its answers worked out by
hand, and changing the log changes the answers with it.  Every compute is
one function here, registered with :func:`computed` the way a box is
registered with ``@box``::

    @computed("deadlocks", of="net")
    def deadlocks(context: Context, argument: str, task: Task) -> int:
        \"\"\"How many dead markings the net reaches (none for a sound WF-net).\"\"\"
        ...

The name is what the author writes; ``argument`` is what is between the
parentheses (``dead(t)``, ``fire(a, [p1])``); ``task`` gives the block's
other keys (``of: other.pnml``).  ``openprocess exercises check`` lists every
compute with its docstring, and tells an author when a block refers to one
that does not exist.

Of the log
    ``activities``, ``start activities``, ``end activities``, ``variants``,
    ``cases``, ``events``, ``alpha.T_L``, ``alpha.T_I``, ``alpha.T_O``,
    ``alpha.X_L``, ``alpha.Y_L``, ``alpha.P_L``, ``alpha.place(p)``,
    ``dfg``, ``prefixes``, ``language.M``, ``language.M'``, ``im.cut``,
    ``im.split(2)``, ``im.log(2.1)``, ``im.tree``, ``im.dfg(2)``,
    ``fitness``, ``replay``, ``alignment fitness(⟨a, b⟩)``,
    ``alignment.kind``, ``batches(activity)``, ``weekends``,
    ``timeout(14 days)``, ``arrival rate``.
Of the net
    ``wf-net``, ``sound``, ``option to complete``, ``proper completion``,
    ``no dead transitions``, ``dead transitions``, ``bounded``, ``safe``,
    ``live``, ``deadlock-free``, ``reversible``, ``free-choice``,
    ``well-structured``, ``s-coverable``, ``terminating``, ``dead(t)``,
    ``bound``, ``bound(p)``, ``deadlocks``, ``net.P``, ``net.T``, ``net.F``,
    ``net.m0``, ``pre(t)``, ``post(t)``, ``enabled([p1])``,
    ``fire(t, [p1])``, ``reachable``, ``terminal``, ``reachability graph``,
    ``incidence``, ``parikh(⟨a, b⟩)``, ``state equation(⟨a, b⟩)``,
    ``synchronous product(⟨a, b⟩)``.
Of the transition system
    ``regions``, ``minimal regions``, ``ger(e)``, ``pre-regions(e)``,
    ``post-regions(e)``, ``region({s1, s3})``, ``elementary``,
    ``state separation``, ``forward closure``.
Of a box or a workflow
    ``box(alpha_miner).net.transitions``: the box run on the exercise's
    log, then the attribute path; ``workflow(file).node.path`` likewise for
    a saved workflow.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from . import answers, notation
from .context import Context, TaskError
from .sheet import Task


@dataclass
class Compute:
    name: str
    function: Callable
    #: What it needs: "log", "net", "ts", "box", "" (the block says).
    of: str
    help: str

    def __call__(self, context: Context, argument: str, task: Task):
        return self.function(context, argument, task)


#: Every compute by its (case-folded) name.
COMPUTED: dict[str, Compute] = {}


def computed(name: str, *aliases: str, of: str = ""):
    """Register a compute under ``name`` (and ``aliases``)."""
    def decorate(function: Callable) -> Callable:
        text = (function.__doc__ or "").strip().split("\n\n")[0]
        text = " ".join(line.strip() for line in text.splitlines())
        for key in (name, *aliases):
            COMPUTED[" ".join(key.split()).casefold()] = Compute(key, function, of, text)
        return function
    return decorate


_CALL = re.compile(r"^([\w.'\-\s]+?)\s*\((.*)\)\s*$", re.S)


def split_expression(expression: str) -> tuple[str, str]:
    """``"fire(a, [p1])"`` → ``("fire", "a, [p1]")``; a trailing attribute path
    after the call (``box(alpha_miner).net.transitions``) stays with the name."""
    expression = expression.strip()
    match = re.match(r"^([\w.'\-\s]+?)\s*\((.*?)\)((?:\.[\w\s]+)*)\s*$", expression, re.S)
    if match:
        key = match.group(1) + (match.group(3) or "")
        return key, match.group(2)
    return expression, ""


def lookup(expression: str) -> tuple[Compute, str]:
    """The compute an expression names, and its argument."""
    written, argument = split_expression(expression)
    written = " ".join(written.split())
    key = written.casefold()
    if key in COMPUTED:
        return COMPUTED[key], argument
    # box(id).path and workflow(file).path keep the path in the key -- as
    # written, since an attribute path is case-sensitive (net.T, net.P).
    for prefix in ("box", "workflow"):
        if key.startswith(prefix) and prefix in COMPUTED:
            path = written[len(prefix):] if key.startswith(prefix + "(") else written.partition(".")[2]
            return COMPUTED[prefix], argument + "|" + path.strip(". ")
    raise TaskError(f"unknown compute “{expression}” (known: " + ", ".join(sorted(COMPUTED)) + ")")


def compute(context: Context, task: Task, expression: str | None = None):
    """The value of the block's ``compute:`` (or of ``expression``)."""
    expression = (expression if expression is not None else task.get("compute", "")).strip()
    found, argument = lookup(expression)
    value = found(context, argument, task)
    if value is None:
        raise TaskError(f"“{expression}” could not be decided for this exercise "
                        "(the state space is too large)")
    return value


def describe_all() -> list[tuple[str, str, str]]:
    """``(name, of, help)`` for every compute, for the author's listing."""
    seen, rows = set(), []
    for item in COMPUTED.values():
        if item.name in seen:
            continue
        seen.add(item.name)
        rows.append((item.name, item.of, item.help))
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _net_of(context: Context, task: Task):
    return context.net(task.get("of") or task.get("net"))


def _log_of(context: Context, task: Task):
    source = task.get("log") or task.get("of")
    if source and source.lower().endswith(".pnml"):
        source = None
    return context.simple_log(None if source in (None, "", "log") else source)


def _with_start(net):
    from ..mining.analysis import check_workflow_net
    from ..mining.petrinet import Marking
    if net.initial_marking:
        return net
    workflow = check_workflow_net(net)
    if workflow.is_workflow_net:
        net = net.copy()
        net.initial_marking = Marking({workflow.source: 1})
        net.final_marking = net.final_marking or Marking({workflow.sink: 1})
    return net


def _soundness(net):
    from ..mining.analysis import check_soundness
    return check_soundness(net, max_states=50_000)


def _structure(net):
    from ..mining.analysis import check_workflow_net
    from ..mining.structure import check_structure
    workflow = check_workflow_net(net)
    if not workflow.is_workflow_net:
        raise TaskError("the net is not a WF-net, so this structural property is not defined")
    return check_structure(net, workflow.source, workflow.sink)


def _properties(net):
    from ..mining.analysis import analyse
    return analyse(_with_start(net), max_states=50_000)


def _graph(net):
    from ..mining.analysis import reachability_graph
    net = _with_start(net)
    graph = reachability_graph(net, max_states=50_000)
    if graph.has_omega:
        raise TaskError("the net is unbounded: its markings cannot be listed")
    if graph.truncated:
        raise TaskError("the net has too many markings to list")
    return net, graph


def _place_name(net, place_id: str) -> str:
    return net.places[place_id].name or place_id


def _transition_name(net, transition_id: str) -> str:
    t = net.transitions[transition_id]
    return t.label or t.name or transition_id


def _labels(net, ids) -> frozenset:
    return frozenset(_transition_name(net, t) for t in ids)


def _find_transition(net, text: str) -> str:
    wanted = answers.name(text)
    for transition_id, t in net.transitions.items():
        if wanted in {answers.name(x) for x in (t.label or "", t.name or "", transition_id) if x}:
            return transition_id
    raise TaskError(f"the net has no transition “{text}”")


def _find_place(net, text: str) -> str:
    wanted = answers.name(text)
    for place_id, p in net.places.items():
        if wanted in {answers.name(x) for x in (p.name or "", place_id) if x}:
            return place_id
    raise TaskError(f"the net has no place “{text}”")


def _marking_of(net, text: str):
    """A typed marking as a :class:`Marking` of ``net`` (by place names)."""
    from ..mining.petrinet import Marking
    if not text.strip():
        return _with_start(net).initial_marking
    counts = notation.marking(text)
    return Marking({_find_place(net, name): n for name, n in counts.items()})


def _marking_counter(net, marking) -> Counter:
    return Counter({_place_name(net, p): n for p, n in marking.items() if n})


def _trace_argument(argument: str) -> tuple[str, ...]:
    try:
        return tuple(answers.trace(argument))
    except answers.AnswerSyntaxError as error:
        raise TaskError(f"could not read the trace “{argument}”: {error}") from None


# ---------------------------------------------------------------------------
# Of the log
# ---------------------------------------------------------------------------
@computed("activities", "alpha.T_L", of="log")
def _activities(context, argument, task):
    """The activities of the log, T_L."""
    return frozenset(context.alpha.T_L) if not task.get("of") else \
        frozenset(a for trace in _log_of(context, task) for a in trace)


@computed("start activities", "alpha.T_I", of="log")
def _start(context, argument, task):
    """The activities a trace of the log begins with, T_I."""
    return frozenset(t[0] for t in _log_of(context, task) if t)


@computed("end activities", "alpha.T_O", of="log")
def _end(context, argument, task):
    """The activities a trace of the log ends with, T_O."""
    return frozenset(t[-1] for t in _log_of(context, task) if t)


@computed("alpha.X_L", of="log")
def _x_l(context, argument, task):
    """The candidate pairs (A, B) of the α-algorithm, X_L."""
    return frozenset((frozenset(a), frozenset(b)) for a, b in context.alpha.X_L)


@computed("alpha.Y_L", of="log")
def _y_l(context, argument, task):
    """The maximal pairs of the α-algorithm, Y_L."""
    return frozenset((frozenset(a), frozenset(b)) for a, b in context.alpha.Y_L)


@computed("alpha.P_L", of="log")
def _p_l(context, argument, task):
    """The places of α(L): i_L, o_L and one p(A, B) per pair of Y_L (typed as
    ``p({a},{b,e})``; shown as ``p〈a | b·e〉``)."""
    return frozenset({"i_L", "o_L"} | {answers.place_atom(a, b) for a, b in context.alpha.Y_L})


@computed("alpha.place", of="log")
def _alpha_place(context, argument, task):
    """Could the α-algorithm have produced the place p(A, B)? ``yes``, or why
    not: ``a relation forbids it`` (some a → b fails, or A or B is not
    independent) or ``not maximal`` (a bigger pair contains it).  Write the
    place as ``({a}, {b, c})``."""
    items = answers.parse(argument)
    if len(items) == 1 and isinstance(items[0], tuple) and len(items[0]) == 2:
        a_set, b_set = items[0]
    elif len(items) == 2:
        a_set, b_set = items
    else:
        raise TaskError("write the place as ({a}, {b, c})")
    a_set = frozenset(a_set) if isinstance(a_set, frozenset) else frozenset([a_set])
    b_set = frozenset(b_set) if isinstance(b_set, frozenset) else frozenset([b_set])
    footprint = context.alpha.footprint
    known = {answers.name(x): x for x in footprint.activities}
    try:
        a_real = frozenset(known[x] for x in a_set)
        b_real = frozenset(known[x] for x in b_set)
    except KeyError as missing:
        raise TaskError(f"“{missing.args[0]}” is not an activity of the log") from None
    for a in a_real:
        for b in b_real:
            if footprint.relation(a, b) != "→":
                return "a relation forbids it"
    for group in (a_real, b_real):
        for x in group:
            for y in group:
                if footprint.relation(x, y) != "#":
                    return "a relation forbids it"
    y_l = {(frozenset(a), frozenset(b)) for a, b in context.alpha.Y_L}
    if (a_real, b_real) in y_l:
        return "yes"
    return "not maximal"


@computed("variants", of="log")
def _variants(context, argument, task):
    """How many different traces the log has."""
    return len(_log_of(context, task))


@computed("cases", "traces", of="log")
def _cases(context, argument, task):
    """How many cases (traces) the log has."""
    return sum(_log_of(context, task).values())


@computed("events", of="log")
def _events(context, argument, task):
    """How many events the log has."""
    return sum(len(t) * n for t, n in _log_of(context, task).items())


@computed("dfg", "directly follows", of="log")
def _dfg(context, argument, task):
    """The directly-follows relation of the log, as pairs (a, b)."""
    from ..mining.dfg import dfg_from_simple_log
    return frozenset(dfg_from_simple_log(_log_of(context, task)).edges)


@computed("prefixes", "prefix-closed language", of="log")
def _prefixes(context, argument, task):
    """The prefix-closed language of the log: every prefix of every trace,
    the empty sequence included."""
    found = set()
    for trace in _log_of(context, task):
        for n in range(len(trace) + 1):
            found.add(answers.Sequence(trace[:n]))
    return frozenset(found)


def _language_matrix(context, task, drop_last: bool) -> notation.Matrix:
    log = _log_of(context, task)
    labels = sorted({a for t in log for a in t})
    prefixes = sorted({t[:n] for t in log for n in range(1, len(t) + 1)}, key=lambda p: (len(p), p))
    rows, cells = [], {}
    for prefix in prefixes:
        name = "⟨" + ", ".join(prefix) + "⟩"
        rows.append(name)
        counted = Counter(prefix[:-1] if drop_last else prefix)
        for label in labels:
            cells[(name, label)] = float(counted[label])
    return notation.Matrix(rows, labels, cells)


@computed("language.M", "regions.M", of="log")
def _matrix_m(context, argument, task):
    """The matrix M of language-based regions: one row per non-empty prefix of
    the log (shortest first, then alphabetical), one column per activity
    (alphabetical), the number of times the activity occurs in the prefix."""
    return _language_matrix(context, task, drop_last=False)


@computed("language.M'", "regions.M'", "language.M′", "regions.M′", of="log")
def _matrix_m_prime(context, argument, task):
    """The matrix M′: like M, but each prefix without its last event."""
    return _language_matrix(context, task, drop_last=True)


# -- the Inductive Miner, step by step --------------------------------------------------
def _im_path(argument: str) -> list[int]:
    path = argument.strip().strip("()")
    if not path:
        return []
    try:
        return [int(x) - 1 for x in re.split(r"[.,\s/]+", path) if x]
    except ValueError:
        raise TaskError(f"“{argument}” is not a position like 2 or 2.1") from None


def _im_sublog(context, task, path: list[int]):
    """The sublog at ``path`` (1-based positions down the cuts), and the cut it
    gets (None at a base case)."""
    from ..mining.dfg import dfg_from_simple_log
    from ..mining.discovery.inductive import _Miner, split_log
    miner = _Miner(0.0)
    log = Counter(_log_of(context, task))
    for position in path:
        dfg = dfg_from_simple_log(log)
        found = miner.find_cut(dfg)
        if found is None:
            raise TaskError("the Inductive Miner finds no cut here (a base case or a fall-through)")
        operator, groups = found
        sublogs = split_log(log, operator, groups, dfg)
        if not 0 <= position < len(sublogs):
            raise TaskError(f"the cut has {len(sublogs)} parts, not {position + 1}")
        log = sublogs[position]
    dfg = dfg_from_simple_log(log)
    return log, miner.find_cut(dfg)


@computed("im.cut", of="log")
def _im_cut(context, argument, task):
    """The cut the Inductive Miner finds: on the whole log, or on the sublog at a
    position (``im.cut(2)``: the second part of the first cut; ``im.cut(2.1)``:
    the first part of that)."""
    _, found = _im_sublog(context, task, _im_path(argument))
    if found is None:
        raise TaskError("the Inductive Miner finds no cut here (a base case or a fall-through)")
    operator, groups = found
    return operator.value, tuple(frozenset(g) for g in groups)


@computed("im.split", "im.log", of="log")
def _im_split(context, argument, task):
    """The sublog at a position after the cuts (``im.split(2)``: the second
    part of the first cut)."""
    log, _ = _im_sublog(context, task, _im_path(argument))
    return Counter(log)


@computed("im.dfg", of="log")
def _im_dfg(context, argument, task):
    """The directly-follows relation of the sublog at a position."""
    from ..mining.dfg import dfg_from_simple_log
    log, _ = _im_sublog(context, task, _im_path(argument))
    return frozenset(dfg_from_simple_log(log).edges)


@computed("im.tree", "inductive.tree", "process tree", of="log")
def _im_tree(context, argument, task):
    """The process tree the Inductive Miner discovers (of the sublog at a
    position, when one is given)."""
    from ..mining.discovery.inductive import inductive_miner
    log, _ = _im_sublog(context, task, _im_path(argument))
    return notation.tree_from_engine(inductive_miner(log).tree)


# -- conformance --------------------------------------------------------------------------
@computed("fitness", "token fitness", of="net")
def _fitness(context, argument, task):
    """Token-based replay fitness of the log on the net (``of: net.pnml``)."""
    from ..mining.conformance.token_replay import token_replay
    return token_replay(_with_start(_net_of(context, task)), _log_of(context, task)).fitness


@computed("replay", of="net")
def _replay(context, argument, task):
    """Token replay per trace: produced, consumed, missing and remaining tokens
    and the fitness, for every variant of the log (or the one in ``trace:``)."""
    from ..mining.conformance.token_replay import replay_trace
    net = _with_start(_net_of(context, task))
    log = _log_of(context, task)
    traces = [_trace_argument(task.get("trace"))] if task.get("trace") else sorted(log)
    rows = {}
    for trace in traces:
        result = replay_trace(net, tuple(trace), 1)
        rows[tuple(answers.name(a) for a in trace)] = {
            "p": float(result.produced), "c": float(result.consumed),
            "m": float(result.missing), "r": float(result.remaining),
            "fitness": result.fitness}
    return rows


@computed("alignment fitness", "alignment.fitness", of="net")
def _alignment_fitness(context, argument, task):
    """The fitness of an optimal alignment of the trace (``alignment fitness(⟨a, b⟩)``,
    or of the block's ``trace:``) on the net: 1 − cost / worst case."""
    from ..mining.conformance.alignments import align_trace
    net = _with_start(_net_of(context, task))
    trace = _trace_argument(argument or task.get("trace") or "")
    empty = align_trace(net, ())
    best = align_trace(net, trace)
    if not best.complete:
        raise TaskError("the alignment search gave up (too many states)")
    worst = len(trace) + empty.cost
    return 1 - best.cost / worst if worst else 1.0


@computed("alignment cost", "alignment.cost", of="net")
def _alignment_cost(context, argument, task):
    """The cost of an optimal alignment of the trace on the net (one per log
    move or model move; silent moves are free)."""
    from ..mining.conformance.alignments import align_trace
    net = _with_start(_net_of(context, task))
    trace = _trace_argument(argument or task.get("trace") or "")
    best = align_trace(net, trace)
    if not best.complete:
        raise TaskError("the alignment search gave up (too many states)")
    return best.cost


def alignment_kind(net, moves: tuple[list[str], list[str]], trace=None) -> tuple[str, str]:
    """``(kind, why)`` for a typed alignment: ``optimal``, ``sub-optimal`` or
    ``not an alignment``."""
    from ..mining.conformance.alignments import align_trace
    net = _with_start(net)
    top, bottom = moves
    log_row = [m for m in top if m != notation.SKIP]
    if trace is not None and tuple(answers.name(a) for a in trace) != tuple(log_row):
        return "not an alignment", "the log moves do not spell the trace"
    labels = {}
    for transition_id, t in net.transitions.items():
        for key in (t.label, t.name, transition_id):
            if key:
                labels.setdefault(answers.name(key), []).append(transition_id)
    marking = net.initial_marking
    cost = 0
    for a, b in zip(top, bottom):
        if b == notation.SKIP:
            cost += 1
            continue
        if a != notation.SKIP and a != b:
            return "not an alignment", f"the moves {a} and {b} in one column do not agree"
        if b in ("τ", "tau"):
            candidates = [t for t in net.transitions if net.transitions[t].label is None
                          and net.is_enabled(marking, t)]
        else:
            candidates = [t for t in labels.get(b, []) if net.is_enabled(marking, t)]
        if not candidates:
            return "not an alignment", f"“{b}” is not enabled at that point in the model"
        marking = net.fire(marking, candidates[0])
        if a == notation.SKIP and b not in ("τ", "tau"):
            cost += 1
    if net.final_marking and marking != net.final_marking:
        return "not an alignment", "the model moves do not reach the final marking"
    best = align_trace(net, tuple(log_row))
    if best.complete and cost > best.cost:
        return "sub-optimal", f"it costs {cost}; an optimal alignment costs {best.cost}"
    return "optimal", f"it costs {cost}, as little as any alignment of this trace"


@computed("alignment.kind", "alignment kind", of="net")
def _alignment_kind(context, argument, task):
    """Is the alignment written in the block's ``alignment:`` (two rows) optimal,
    sub-optimal or not an alignment of ``trace:`` on the net?"""
    text = task.get("alignment")
    if not text:
        raise TaskError("the block needs an “alignment:” with the two rows")
    moves = notation.alignment(text)
    trace = _trace_argument(task.get("trace")) if task.get("trace") else None
    return alignment_kind(_net_of(context, task), moves, trace)[0]


@computed("synchronous product", of="net")
def _synchronous_product(context, argument, task):
    """The synchronous product of the net and the trace (``synchronous
    product(⟨a, b, c⟩)``): the trace as a sequence of places and transitions,
    beside the net, with a synchronous transition for every pair of equal labels."""
    from ..mining.petrinet import Marking, PetriNet
    net = _with_start(_net_of(context, task))
    trace = _trace_argument(argument or task.get("trace") or "")
    product = PetriNet(name="Synchronous product")
    places = {}
    for place_id, place in net.places.items():
        places[place_id] = product.add_place(place.name, id=f"m_{place_id}")
    model = {}
    for transition_id, t in net.transitions.items():
        model[transition_id] = product.add_transition(t.label, name=f"({t.label or 'τ'}, ≫)",
                                                      id=f"m_{transition_id}")
    for arc in net.arcs:
        if arc.source in net.places:
            product.add_arc(f"m_{arc.source}", f"m_{arc.target}", arc.weight)
        else:
            product.add_arc(f"m_{arc.source}", f"m_{arc.target}", arc.weight)
    trace_places = [product.add_place(f"q{i}", id=f"q{i}") for i in range(len(trace) + 1)]
    for i, activity in enumerate(trace):
        log_move = product.add_transition(activity, name=f"(≫, {activity})", id=f"l_{i}")
        product.add_arc(trace_places[i].id, log_move.id)
        product.add_arc(log_move.id, trace_places[i + 1].id)
        for transition_id, t in net.transitions.items():
            if t.label == activity:
                sync = product.add_transition(activity, name=f"({activity}, {activity})",
                                              id=f"s_{i}_{transition_id}")
                product.add_arc(trace_places[i].id, sync.id)
                product.add_arc(sync.id, trace_places[i + 1].id)
                for arc in net.arcs:
                    if arc.target == transition_id:
                        product.add_arc(f"m_{arc.source}", sync.id, arc.weight)
                    elif arc.source == transition_id:
                        product.add_arc(sync.id, f"m_{arc.target}", arc.weight)
    initial = {f"m_{p}": n for p, n in net.initial_marking.items()}
    initial[trace_places[0].id] = 1
    product.initial_marking = Marking(initial)
    if net.final_marking:
        final = {f"m_{p}": n for p, n in net.final_marking.items()}
        final[trace_places[-1].id] = 1
        product.final_marking = Marking(final)
    return product


# -- the dotted chart: time ---------------------------------------------------------------
def _timed_events(context, task):
    log = context.event_log(task.get("log") or task.get("of"))
    events = []
    for trace in log:
        case = trace.case_id
        for event in trace:
            when = event.timestamp
            if when is not None:
                events.append((when, event.activity or "", case))
    if not events:
        raise TaskError("the log has no timestamps")
    return events


@computed("batches", of="log")
def _batches(context, argument, task):
    """How many batches an activity is done in: runs of that activity, each
    within an hour of the previous one, separated by more than an hour
    (``batches(send invoices)``; ``batches(send invoices, 2 hours)`` for
    another gap)."""
    from datetime import timedelta
    parts = [p.strip() for p in argument.split(",")]
    wanted = answers.name(parts[0]) if parts and parts[0] else None
    gap = _duration(parts[1]) if len(parts) > 1 else timedelta(hours=1)
    times = sorted(when for when, activity, _ in _timed_events(context, task)
                   if wanted is None or answers.name(activity) == wanted)
    if not times:
        raise TaskError(f"the log has no events of “{parts[0]}”")
    count, previous = 0, None
    for when in times:
        if previous is None or when - previous > gap:
            count += 1
        previous = when
    return count


@computed("weekends", "weekend events", of="log")
def _weekends(context, argument, task):
    """How many events happen on a Saturday or a Sunday."""
    return sum(1 for when, _, _ in _timed_events(context, task) if when.weekday() >= 5)


def _duration(text: str):
    from datetime import timedelta
    match = re.match(r"^\s*([\d.]+)\s*(day|days|d|hour|hours|h|minute|minutes|min|m|week|weeks|w)?\s*$",
                     text, re.I)
    if not match:
        raise TaskError(f"“{text}” is not a duration like 14 days")
    amount = float(match.group(1))
    unit = (match.group(2) or "days").lower()[0]
    return timedelta(**{{"d": "days", "h": "hours", "m": "minutes", "w": "weeks"}[unit]: amount})


@computed("timeout", "timeouts", of="log")
def _timeout(context, argument, task):
    """How many cases have a gap longer than the duration between two of
    their events (``timeout(14 days)``): the cases that timed out."""
    gap = _duration(argument or "14 days")
    by_case: dict[str, list] = {}
    for when, _, case in _timed_events(context, task):
        by_case.setdefault(case, []).append(when)
    count = 0
    for times in by_case.values():
        times.sort()
        if any(b - a > gap for a, b in zip(times, times[1:])):
            count += 1
    return count


@computed("arrival rate", of="log")
def _arrival_rate(context, argument, task):
    """Cases per day: the number of cases divided by the days between the
    first and the last case start (at least one day)."""
    starts: dict[str, object] = {}
    for when, _, case in _timed_events(context, task):
        if case not in starts or when < starts[case]:
            starts[case] = when
    times = sorted(starts.values())
    days = max((times[-1] - times[0]).total_seconds() / 86400, 1.0)
    return len(times) / days


# ---------------------------------------------------------------------------
# Of the net
# ---------------------------------------------------------------------------
@computed("wf-net", "workflow net", of="net")
def _wf(context, argument, task):
    """Is the net a WF-net (one source, one sink, every node on a path between them)?"""
    from ..mining.analysis import check_workflow_net
    return check_workflow_net(_net_of(context, task)).is_workflow_net


@computed("sound", of="net")
def _sound(context, argument, task):
    """Is the WF-net sound?"""
    return _soundness(_net_of(context, task)).sound


@computed("option to complete", of="net")
def _option(context, argument, task):
    """Can every reachable marking still reach the final marking?"""
    return _soundness(_net_of(context, task)).option_to_complete


@computed("proper completion", of="net")
def _proper(context, argument, task):
    """Is the sink the only place marked when it is marked?"""
    return _soundness(_net_of(context, task)).proper_completion


@computed("no dead transitions", of="net")
def _no_dead(context, argument, task):
    """Can every transition fire in some reachable marking?"""
    return _soundness(_net_of(context, task)).no_dead_transitions


@computed("dead transitions", of="net")
def _dead_transitions(context, argument, task):
    """The transitions that never fire from the initial marking (a WF-net: from [i])."""
    net = _net_of(context, task)
    return _labels(net, _properties(net).dead_transitions)


@computed("dead", of="net")
def _dead(context, argument, task):
    """Is the transition dead: never enabled in any reachable marking (``dead(t)``)?"""
    net = _net_of(context, task)
    return _find_transition(net, argument) in _properties(net).dead_transitions


@computed("bounded", of="net")
def _bounded(context, argument, task):
    """Is the net bounded?"""
    return _properties(_net_of(context, task)).bounded


@computed("bound", "k", of="net")
def _bound(context, argument, task):
    """The smallest k for which the net is k-bounded (``bound(p)``: of one place);
    inf for an unbounded net."""
    net = _net_of(context, task)
    report = _properties(net)
    if not report.bounded:
        return math.inf
    if argument.strip():
        return float(report.place_bounds[_find_place(net, argument)])
    return float(report.bound)


@computed("safe", of="net")
def _safe(context, argument, task):
    """Is the net safe (1-bounded)?"""
    return _properties(_net_of(context, task)).safe


@computed("deadlock-free", "deadlock free", of="net")
def _deadlock_free(context, argument, task):
    """Does every reachable marking enable a transition?"""
    return _properties(_net_of(context, task)).deadlock_free


@computed("deadlocks", "dead markings", of="net")
def _deadlocks(context, argument, task):
    """How many reachable markings enable nothing."""
    report = _properties(_net_of(context, task))
    return len(report.dead_markings)


@computed("terminating", of="net")
def _terminating(context, argument, task):
    """Does every run end: is the reachability graph finite and without cycles?"""
    net, graph = _graph(_net_of(context, task))
    from ..mining.analysis import strongly_connected_components
    sccs = strongly_connected_components(len(graph.states), lambda s: [t for _, t in graph.successors(s)])
    for component in sccs:
        if len(component) > 1:
            return False
        state = next(iter(component))
        if any(target == state for _, target in graph.successors(state)):
            return False
    return True


@computed("live", of="net")
def _live(context, argument, task):
    """Is the net live: from every reachable marking, every transition can fire again?"""
    report = _properties(_net_of(context, task))
    if report.live_transitions is None:
        return None
    return set(report.live_transitions) == set(_net_of(context, task).transitions)


@computed("reversible", of="net")
def _reversible(context, argument, task):
    """Can the initial marking be reached again from every reachable marking?"""
    return _properties(_net_of(context, task)).reversible


@computed("free-choice", "free choice", of="net")
def _free_choice(context, argument, task):
    """Is the net free-choice (places that share an output transition share all)?"""
    from ..mining.structure import free_choice_violations
    return not free_choice_violations(_net_of(context, task))


@computed("well-structured", of="net")
def _well_structured(context, argument, task):
    """Is the WF-net well-structured?"""
    return _structure(_net_of(context, task)).well_structured


@computed("s-coverable", of="net")
def _s_coverable(context, argument, task):
    """Is the WF-net S-coverable?"""
    return _structure(_net_of(context, task)).s_coverable


@computed("net.P", "places", of="net")
def _net_places(context, argument, task):
    """The places of the net, P."""
    net = _net_of(context, task)
    return frozenset(_place_name(net, p) for p in net.places)


@computed("net.T", "transitions", of="net")
def _net_transitions(context, argument, task):
    """The transitions of the net, T."""
    net = _net_of(context, task)
    return frozenset(_transition_name(net, t) for t in net.transitions)


@computed("net.F", "arcs", "flow", of="net")
def _net_arcs(context, argument, task):
    """The arcs of the net, F, as pairs (p, t) and (t, p)."""
    net = _net_of(context, task)

    def name(node_id: str) -> str:
        return _place_name(net, node_id) if node_id in net.places else _transition_name(net, node_id)
    return frozenset((name(arc.source), name(arc.target)) for arc in net.arcs)


@computed("net.m0", "initial marking", "m0", of="net")
def _net_m0(context, argument, task):
    """The initial marking of the net (a WF-net without one: [i])."""
    net = _net_of(context, task)
    return _marking_counter(net, _with_start(net).initial_marking)


@computed("pre", "preset", of="net")
def _pre(context, argument, task):
    """The preset of a transition or place (``pre(t)``): the nodes with an arc to it."""
    net = _net_of(context, task)
    wanted = answers.name(argument)
    for node_id in [*net.places, *net.transitions]:
        label = _place_name(net, node_id) if node_id in net.places else _transition_name(net, node_id)
        if answers.name(label) == wanted or answers.name(node_id) == wanted:
            return frozenset(_place_name(net, n) if n in net.places else _transition_name(net, n)
                             for n in net.preset(node_id))
    raise TaskError(f"the net has no node “{argument}”")


@computed("post", "postset", of="net")
def _post(context, argument, task):
    """The postset of a transition or place (``post(t)``): the nodes it has an arc to."""
    net = _net_of(context, task)
    wanted = answers.name(argument)
    for node_id in [*net.places, *net.transitions]:
        label = _place_name(net, node_id) if node_id in net.places else _transition_name(net, node_id)
        if answers.name(label) == wanted or answers.name(node_id) == wanted:
            return frozenset(_place_name(net, n) if n in net.places else _transition_name(net, n)
                             for n in net.postset(node_id))
    raise TaskError(f"the net has no node “{argument}”")


@computed("enabled", of="net")
def _enabled(context, argument, task):
    """The transitions enabled in a marking (``enabled([p1, p2])``; empty: the
    initial marking)."""
    net = _net_of(context, task)
    marking = _marking_of(net, argument)
    return _labels(net, net.enabled(marking))


@computed("fire", of="net")
def _fire(context, argument, task):
    """The marking after firing a transition in a marking (``fire(t, [p1])``; a
    sequence works too: ``fire(⟨a, b⟩, [p1])``)."""
    net = _net_of(context, task)
    match = re.match(r"^\s*(.+?)\s*,\s*(\[.*\]|\{.*\}|∅)\s*$", argument, re.S)
    if match:
        steps_text, marking_text = match.group(1), match.group(2)
    else:
        steps_text, marking_text = argument, ""
    marking = _marking_of(net, marking_text)
    steps = answers.trace(steps_text)
    for step in steps:
        transition = _find_transition(net, step)
        if not net.is_enabled(marking, transition):
            raise TaskError(f"“{step}” is not enabled in {notation.show_marking(_marking_counter(net, marking))}")
        marking = net.fire(marking, transition)
    return _marking_counter(net, marking)


@computed("reachable", "reachable markings", of="net")
def _reachable(context, argument, task):
    """Every reachable marking of the net (the initial one included)."""
    net, graph = _graph(_net_of(context, task))
    return frozenset(notation.marking_key(_marking_counter(net, m)) for m in graph.states)


@computed("terminal", "terminal markings", "dead markings set", of="net")
def _terminal(context, argument, task):
    """The reachable markings that enable nothing."""
    net, graph = _graph(_net_of(context, task))
    return frozenset(notation.marking_key(_marking_counter(net, graph.states[i]))
                     for i in graph.dead_states())


@computed("reachability graph", "marking graph", "ts", of="net")
def _reachability_graph(context, argument, task):
    """The reachability graph as a transition system: the markings as states,
    the transition labels as events."""
    from ..mining.transition_system import TransitionSystem
    net, graph = _graph(_net_of(context, task))
    ts = TransitionSystem(name="Reachability graph")
    names = [notation.show_marking(_marking_counter(net, m)) for m in graph.states]
    for name in names:
        ts.add_state(name)
    for source, transition, target in graph.edges:
        ts.add_transition(names[source], _transition_name(net, transition), names[target])
    ts.initial = [names[0]]
    ts.final = [names[i] for i in graph.dead_states()]
    return ts


@computed("incidence", "incidence matrix", of="net")
def _incidence(context, argument, task):
    """The incidence matrix: a row per place, a column per transition, the
    tokens the transition adds to the place (produced minus consumed)."""
    from ..mining.invariants import incidence_matrix
    net = _net_of(context, task)
    places, transitions, matrix = incidence_matrix(net)
    rows = [_place_name(net, p) for p in places]
    columns = [_transition_name(net, t) for t in transitions]
    cells = {(rows[i], columns[j]): float(matrix[i][j]) for i in range(len(rows))
             for j in range(len(columns))}
    return notation.Matrix(rows, columns, cells)


@computed("parikh", "parikh vector", of="net")
def _parikh(context, argument, task):
    """The Parikh vector of a firing sequence (``parikh(⟨a, b, a⟩)``): how often
    each transition fires, as a one-row matrix."""
    net = _net_of(context, task)
    steps = answers.trace(argument)
    counts = Counter(_transition_name(net, _find_transition(net, s)) for s in steps)
    columns = [_transition_name(net, t) for t in net.transitions]
    return notation.Matrix(["x"], columns, {("x", c): float(counts[c]) for c in columns})


@computed("state equation", "marking equation", of="net")
def _state_equation(context, argument, task):
    """The marking the state equation gives for a firing sequence,
    m = m₀ + C·x (``state equation(⟨a, b⟩)``; also ``state equation(⟨a, b⟩, [p1])``
    from another marking)."""
    from ..mining.invariants import incidence_matrix
    net = _net_of(context, task)
    match = re.match(r"^\s*(.+?)\s*,\s*(\[.*\]|\{.*\}|∅)\s*$", argument, re.S)
    steps_text, marking_text = (match.group(1), match.group(2)) if match else (argument, "")
    marking = _marking_of(net, marking_text)
    counts = Counter(_find_transition(net, s) for s in answers.trace(steps_text))
    places, transitions, matrix = incidence_matrix(net)
    result = Counter()
    for i, place in enumerate(places):
        value = marking[place] + sum(matrix[i][j] * counts[t] for j, t in enumerate(transitions))
        if value:
            result[_place_name(net, place)] = value
    return result


# ---------------------------------------------------------------------------
# Of the transition system
# ---------------------------------------------------------------------------
def _regions(ts):
    from ..mining.regions import analyse_regions
    return analyse_regions(ts)


def _event_regions(ts, event: str):
    analysis = _regions(ts)
    for key, regions in analysis.by_event.items():
        if answers.name(key) == answers.name(event):
            return regions
    raise TaskError(f"the transition system has no event “{event}”")


def _ts_of(context, task):
    return context.ts(task.get("of") or task.get("ts"))


@computed("regions", of="ts")
def _regions_all(context, argument, task):
    """Every region of the transition system (sets of states)."""
    return frozenset(_regions(_ts_of(context, task)).regions)


@computed("minimal regions", of="ts")
def _minimal_regions(context, argument, task):
    """The minimal regions."""
    return frozenset(_regions(_ts_of(context, task)).minimal)


@computed("ger", of="ts")
def _ger(context, argument, task):
    """The generalised excitation region of an event (``ger(e)``)."""
    return _event_regions(_ts_of(context, task), argument).ger


@computed("pre-regions", of="ts")
def _pre_regions(context, argument, task):
    """The minimal pre-regions of an event (``pre-regions(e)``)."""
    return frozenset(_event_regions(_ts_of(context, task), argument).minimal_pre)


@computed("post-regions", of="ts")
def _post_regions(context, argument, task):
    """The minimal post-regions of an event (``post-regions(e)``)."""
    return frozenset(_event_regions(_ts_of(context, task), argument).minimal_post)


@computed("region", of="ts")
def _is_region(context, argument, task):
    """Is a set of states a region (``region({s1, s3})``)?"""
    from ..mining.regions import check_region
    ts = _ts_of(context, task)
    states = answers.readings(argument)[-1]
    known = {answers.name(s): s for s in ts.states}
    unknown = [s for s in states if s not in known]
    if unknown:
        raise TaskError("not states of the transition system: " + ", ".join(sorted(unknown)))
    return check_region(ts, {known[s] for s in states}).is_region


@computed("elementary", of="ts")
def _elementary(context, argument, task):
    """Is the transition system elementary (state separation and forward closure)?"""
    return _regions(_ts_of(context, task)).elementary


@computed("state separation", of="ts")
def _state_separation(context, argument, task):
    """Does the state separation property hold?"""
    return _regions(_ts_of(context, task)).state_separation


@computed("forward closure", of="ts")
def _forward_closure(context, argument, task):
    """Does the forward closure property hold?"""
    return _regions(_ts_of(context, task)).forward_closure


# ---------------------------------------------------------------------------
# Of a box or a workflow
# ---------------------------------------------------------------------------
def follow_path(value, path: str):
    """``value.net.transitions`` → the attribute path followed, with nets, logs
    and tables turned into things an answer can be compared with."""
    from ..mining.petrinet import PetriNet
    for part in [p for p in path.split(".") if p.strip()]:
        part = part.strip()
        if isinstance(value, PetriNet) and part in ("net", "model"):
            continue                                   # the box gave the net itself
        if isinstance(value, PetriNet):
            if part in ("P", "places"):
                value = frozenset(_place_name(value, p) for p in value.places)
                continue
            if part in ("T", "transitions"):
                value = frozenset(_transition_name(value, t) for t in value.transitions)
                continue
            if part in ("F", "arcs"):
                value = len(value.arcs)
                continue
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif hasattr(value, part):
            value = getattr(value, part)
            if callable(value) and not isinstance(value, type):
                value = value()
        elif hasattr(value, "values") and isinstance(getattr(value, "values", None), dict) \
                and part in value.values:
            value = value.values[part]
        else:
            raise TaskError(f"the result has no “{part}”")
    if isinstance(value, (set, list)) and all(isinstance(v, str) for v in value):
        return frozenset(value)
    if isinstance(value, dict) and all(isinstance(v, (int, float)) for v in value.values()):
        return value
    return value


def run_box(context: Context, box_id: str, task: Task, settings: dict | None = None):
    """Run one box of the library on the exercise's log (or net), as a one-box
    workflow: its result value."""
    from ..flow.runner import Cache, Runner
    from ..flow.workflow import Workflow
    library = context.library
    spec = None
    for candidate in library:
        if candidate.id == box_id or candidate.function.__name__ == box_id or \
                answers.name(candidate.name) == answers.name(box_id):
            spec = candidate
            break
    if spec is None:
        raise TaskError(f"no box called “{box_id}” (see + Add box on a workflow)")
    wf = Workflow("Exercise", library)
    inputs = []
    for port in spec.inputs:
        if port.type_name in ("EventLog", "Log") or port.name == "log":
            inputs.append(wf.add("typed_log", {"text": notation.show_log(_log_of(context, task))}))
        elif port.type_name in ("PetriNet", "Net", "Model") or port.name in ("net", "model"):
            if context.files.net is None and not task.get("of"):
                raise TaskError(f"“{spec.name}” needs a net and the exercise has none")
            inputs.append(wf.add("open_net", {"file": str(context.path(task.get("of"), context.files.net, "net"))}))
        else:
            inputs.append(None)
    node = wf.add(spec.id, settings or {})
    for port, source in zip(spec.inputs, inputs):
        if source is not None:
            wf.connect(source, node, port.name)
    run = Runner(library, Cache(), folder=context.exercise.folder).run(wf)
    result = run.result(node)
    if result is None or result.status != "done":
        raise TaskError(f"“{spec.name}” did not run: " + (result.error if result else "no result"))
    return result.value


@computed("box", of="box")
def _box(context, argument, task):
    """A box of the library run on the exercise's log, then an attribute of its
    result: ``box(alpha_miner).net.transitions``, ``box(check_fit).metrics.fitness``."""
    box_id, _, path = argument.partition("|")
    settings = None
    if task.get("settings"):
        import json
        try:
            settings = json.loads(task.get("settings"))
        except ValueError:
            raise TaskError("“settings:” must be JSON, like {\"k\": 5}") from None
    value = run_box(context, box_id.strip(), task, settings)
    return follow_path(value, path)


@computed("workflow", of="box")
def _workflow(context, argument, task):
    """A saved workflow of the exercise run, then a box's result by the box's
    title: ``workflow(orders.analysis).Check fit.fitness``."""
    from ..flow.record import load
    from ..flow.runner import Cache, Runner
    file_name, _, path = argument.partition("|")
    wf_path = context.path(file_name.strip(), None, "workflow")
    wf, _record = load(wf_path, context.library)
    run = Runner(context.library, Cache(), folder=context.exercise.folder).run(wf)
    node_name, _, rest = path.partition(".")
    wanted = answers.name(node_name)
    for node in wf.nodes.values():
        if answers.name(wf.title(node.id)) == wanted or node.id == node_name:
            result = run.result(node)
            if result is None or result.status != "done":
                raise TaskError(f"“{wf.title(node.id)}” did not run")
            return follow_path(result.value, rest)
    raise TaskError(f"the workflow has no box called “{node_name}”")
