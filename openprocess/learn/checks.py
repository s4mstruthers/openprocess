"""Checking answers.

Each answer block is checked by :func:`check` against what the block says
is right.  The right answer can be written into the block (``answer: {a,
b}``) or *computed* from the exercise's own files (``compute: alpha.T_I``;
see :mod:`.computed` for every compute), so an exercise about a log does
not need its answers worked out by hand, and changing the log changes the
answers with it.

A check returns a :class:`Result`: right or not, a sentence that says why
without giving the answer away, and for a footprint, a matrix or a replay
table the cells that are wrong.  ``points`` are what the answer earns of
the block's ``points:`` (a partial answer earns a part).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import answers, notation
from .answers import AnswerSyntaxError
from .computed import COMPUTED, alignment_kind, compute, lookup
from .context import Context, TaskError
from .pack import Exercise
from .sheet import Task

CORRECT, INCORRECT, PARTIAL, UNKNOWN = "correct", "incorrect", "partial", "unknown"

YES = {"yes", "y", "true", "ja", "1"}
NO = {"no", "n", "false", "nee", "0"}

__all__ = ["CORRECT", "INCORRECT", "PARTIAL", "UNKNOWN", "Context", "Result", "TaskError",
           "check", "compute", "expected", "footprint_of", "model_answer_text", "validate",
           "COMPUTED"]


@dataclass
class Result:
    status: str
    message: str
    #: Footprint cells that are wrong, as (row activity, column activity).
    wrong_cells: list[tuple[str, str]] = field(default_factory=list)
    #: For a net: the comparison with the model answer (openprocess.mining.compare_nets).
    comparison: object | None = None
    #: The share of the block's points this answer earns (1 when right).
    share: float = 0.0
    #: Parts of a compound answer (a tuple's P, T, F, m0) with their own verdicts.
    parts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status == CORRECT and not self.share:
            self.share = 1.0

    @property
    def correct(self) -> bool:
        return self.status == CORRECT


def expected(context: Context, task: Task):
    """The right answer of a block, from ``answer`` or ``compute``."""
    if "compute" in task.settings:
        return compute(context, task)
    if "answer" not in task.settings:
        raise TaskError("the block has neither an answer nor a compute")
    return task.settings["answer"]


def _yes_no(value) -> bool:
    if isinstance(value, bool):
        return value
    text = answers.name(str(value))
    if text in YES:
        return True
    if text in NO:
        return False
    raise TaskError(f"“{value}” is not yes or no")


# ---------------------------------------------------------------------------
# The checks, one per type
# ---------------------------------------------------------------------------
def check(exercise: Exercise, task: Task, answer, context: Context | None = None) -> Result:
    """Is ``answer`` (as the answer box stores it) right for ``task``?"""
    context = context or Context(exercise)
    checker = CHECKERS.get(task.type)
    if checker is None:
        return Result(UNKNOWN, "Compare your answer with the model answer.")
    if task.type == "workflow":
        return checker(context, task, answer or "")     # the answer is a file beside the sheet
    if answer in (None, "", [], {}) or (isinstance(answer, dict) and not any(
            str(v).strip() for v in answer.values() if not isinstance(v, dict))
            and not any(isinstance(v, dict) and any(str(x).strip() for x in v.values())
                        for v in answer.values())):
        return Result(UNKNOWN, "Answer first, then check.")
    try:
        return checker(context, task, answer)
    except AnswerSyntaxError as error:
        return Result(UNKNOWN, f"Could not read your answer: {error}.")
    except TaskError as error:
        return Result(UNKNOWN, f"This answer cannot be checked: {error}.")


def _partial(hits: int, total: int, what: str = "item") -> tuple[str, float, str]:
    """``(status, share, text)`` for ``hits`` of ``total`` right."""
    if total and hits == total:
        return CORRECT, 1.0, f"Right: all {total} {what}{'s' * (total != 1)}."
    share = hits / total if total else 0.0
    return (PARTIAL if hits else INCORRECT), share, f"{hits} of {total} {what}s"


def _check_set(context, task, answer) -> Result:
    right = answers.normalised(expected(context, task))
    right = answers.as_set(right) if isinstance(right, str) else frozenset(right)
    options = answers.readings(str(answer))
    if right in options:
        return Result(CORRECT, f"Right: {len(right)} item{'s' * (len(right) != 1)}.")
    nested = all(isinstance(x, frozenset | tuple) for x in right)

    def fit(reading) -> tuple:
        same_kind = all(isinstance(x, frozenset | tuple) == nested for x in reading)
        return len(reading & right), same_kind
    mine = max(options, key=fit)
    hits, extra = len(mine & right), len(mine - right)
    missing = len(right - mine)
    parts = []
    if hits:
        parts.append(f"{hits} of your items {'is' if hits == 1 else 'are'} right")
    if extra:
        parts.append(f"{extra} {'does' if extra == 1 else 'do'} not belong")
    if missing:
        parts.append(f"{missing} {'is' if missing == 1 else 'are'} missing")
    share = max(0.0, (hits - extra) / len(right)) if right else 0.0
    return Result(PARTIAL if hits else INCORRECT, "Not yet: " + ", ".join(parts) + ".", share=share)


def _number_value(task, right) -> float:
    return float(right) if not isinstance(right, str) else answers.number(right)


def _check_number(context, task, answer) -> Result:
    right = _number_value(task, expected(context, task))
    try:
        mine = answers.number(str(answer))
    except (ValueError, ZeroDivisionError):
        return Result(UNKNOWN, "That is not a number.")
    tolerance = float(task.get("tolerance") or 0) or max(1e-9, abs(right) * 1e-6)
    if task.get("tolerance") is None and "compute" in task.settings:
        tolerance = 0.005                       # a computed figure: two decimals will do
    if right == float("inf"):
        return Result(CORRECT, "Right: unbounded.") if mine == float("inf") else \
            Result(INCORRECT, "Not right: there is no bound.")
    if abs(mine - right) <= tolerance:
        return Result(CORRECT, "Right.")
    return Result(INCORRECT, "Not yet: " + ("too high." if mine > right else "too low."))


def _check_yesno(context, task, answer) -> Result:
    right = _yes_no(expected(context, task))
    if _yes_no(answer) == right:
        return Result(CORRECT, "Right.")
    return Result(INCORRECT, "Not right.")


def _check_choice(context, task, answer) -> Result:
    chosen = set(answer if isinstance(answer, list) else [answer])
    right = {i for i, option in enumerate(task.options) if option.correct}
    if not right and task.get("compute"):
        # The right option is the one whose text matches the computed value.
        value = compute(context, task)
        value = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        wanted = answers.name(value)
        right = {i for i, option in enumerate(task.options) if answers.name(option.text) == wanted}
        if not right:                       # "yes, it is sound": the one option that contains it
            containing = {i for i, option in enumerate(task.options)
                          if re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", answers.name(option.text))}
            if len(containing) == 1:
                right = containing
    if chosen == right:
        return Result(CORRECT, "Right.")
    if len(right) > 1 and chosen & right:
        return Result(PARTIAL, f"Not yet: {len(chosen & right)} of the {len(right)} right "
                               "options, " + (f"and {len(chosen - right)} wrong one"
                                              f"{'s' * (len(chosen - right) != 1)}"
                                              if chosen - right else "but not all"),
                      share=max(0.0, (len(chosen & right) - len(chosen - right)) / len(right)))
    return Result(INCORRECT, "Not right.")


def _check_text(context, task, answer) -> Result:
    mine = answers.name(str(answer))
    pattern = task.get("pattern")
    if pattern and re.fullmatch(pattern, str(answer).strip(), re.I):
        return Result(CORRECT, "Right.")
    accepted = ([task.settings["answer"]] if "answer" in task.settings else []) \
        + task.alternatives
    if "compute" in task.settings:
        accepted.append(str(compute(context, task)))
    if any(answers.name(a) == mine for a in accepted):
        return Result(CORRECT, "Right.")
    return Result(INCORRECT, "Not right.")


def footprint_of(context: Context, task: Task):
    """The footprint the block asks for: of the log, or of a net (``of: net.pnml``)."""
    from ..mining.footprint import footprint_of_log, footprint_of_net
    source = task.get("of")
    if source and source.lower().endswith(".pnml"):
        return footprint_of_net(context.net(source))
    return footprint_of_log(context.simple_log(source if source and source != "log" else None))


def _footprint_pairs(task: Task, footprint) -> list[tuple[str, str]]:
    """The cells the block asks about: all of them, or those in ``pairs:``."""
    if not task.get("pairs"):
        return [(a, b) for a in footprint.activities for b in footprint.activities]
    known = {answers.name(x): x for x in footprint.activities}
    pairs = []
    for item in answers.parse(task.get("pairs")):
        if isinstance(item, tuple) and len(item) == 2 and all(x in known for x in item):
            pairs.append((known[item[0]], known[item[1]]))
        else:
            raise TaskError("“pairs:” must list pairs of activities, like (a, b), (b, c)")
    return pairs


def _check_footprint(context, task, answer) -> Result:
    footprint = footprint_of(context, task)
    wrong, blank = [], 0
    pairs = _footprint_pairs(task, footprint)
    for a, b in pairs:
        mine = answer.get(f"{a}\t{b}") if isinstance(answer, dict) else None
        if not mine:
            blank += 1
            wrong.append((a, b))
        elif mine != footprint.relation(a, b):
            wrong.append((a, b))
    cells = len(pairs)
    if not wrong:
        return Result(CORRECT, f"Right: all {cells} cells.")
    filled_wrong = len(wrong) - blank
    message = f"{cells - len(wrong)} of {cells} cells are right"
    if filled_wrong:
        message += f"; {filled_wrong} {'is' if filled_wrong == 1 else 'are'} wrong (marked)"
    if blank:
        message += f"; {blank} {'is' if blank == 1 else 'are'} still empty"
    return Result(PARTIAL if cells - len(wrong) else INCORRECT, message + ".",
                  wrong_cells=wrong, share=(cells - len(wrong)) / cells if cells else 0.0)


def _check_trace(context, task, answer) -> Result:
    """A firing sequence of the net that ends as ``ends:`` says."""
    from ..mining.analysis import reachability_graph
    from ..mining.compare_nets import replayable_prefix
    from .computed import _with_start
    steps = answers.trace(str(answer))
    target = task.get("fits")
    if target:
        net = _model_answer(context, target)
    else:
        net = context.net(task.get("net") or task.get("of"))
    net = _with_start(net)
    path, done = replayable_prefix(net, steps)
    if done < len(steps):
        return Result(INCORRECT, f"The net can do the first {done} step"
                      f"{'s' * (done != 1)}, but then “{steps[done]}” is not enabled.")
    marking = net.initial_marking
    for transition in path:
        marking = net.fire(marking, transition)
    ends = (task.get("ends") or "any").lower()
    final = net.final_marking or None
    if ends == "any":
        ok, why = True, "it can be fired"
    elif ends == "final":
        ok, why = final is not None and marking == final, "it ends in the final marking"
    elif ends == "deadlock":
        ok = not net.enabled(marking) and marking != final
        why = "nothing is enabled at its end and the case has not completed"
    elif ends in ("stuck", "no completion", "cannot complete"):
        graph = reachability_graph(net, marking, max_states=50_000)
        ok = final is not None and final not in graph.states and not graph.truncated
        why = "after it, the final marking can no longer be reached"
    elif ends in ("improper", "improper completion"):
        ok = final is not None and all(marking[p] >= n for p, n in final.items()) \
            and marking != final
        why = "it puts a token in the sink while other tokens are left behind"
    else:
        raise TaskError(f"unknown “ends: {ends}” (use any, final, deadlock, stuck or "
                        "improper)")
    if ok and task.get("unseen") and _yes_no(task.get("unseen")):
        log = context.simple_log(task.get("log"))
        seen = {tuple(answers.name(a) for a in t) for t in log}
        if tuple(answers.name(s) for s in steps) in seen:
            return Result(INCORRECT, "The net can fire it, but the log already shows this "
                                     "trace: find one the log never shows.")
        why += " and the log never shows it"
    if ok:
        return Result(CORRECT, f"Right: {why}.")
    return Result(INCORRECT, f"The net can fire it, but it is not what is asked: {why} "
                             "does not hold at its end.")


def _check_net(context, task, answer) -> Result:
    """``answer`` is the student's net (a PetriNet)."""
    from ..mining.compare_nets import compare_nets
    from .computed import _soundness, _with_start
    net = answer
    problems: list[str] = []
    comparison = None
    if task.get("sound") is not None and _yes_no(task.get("sound")):
        report = _soundness(net)
        if not report.workflow.is_workflow_net:
            problems.append("it is not a WF-net (" + "; ".join(report.workflow.problems[:1])
                            + ")")
        elif report.sound is False:
            problems.append("it is not sound: " + (report.findings[0] if report.findings
                                                   else "a soundness condition fails"))
        elif report.sound is None:
            problems.append("its soundness could not be decided (too many states)")
    if task.get("fits") is not None:
        from ..mining.conformance.token_replay import token_replay
        source = task.get("fits")
        log = context.simple_log(None if source.lower() in ("log", "yes", "") else source)
        replay = token_replay(_with_start(net), log)
        if replay.fitting_traces < replay.trace_count:
            problems.append(f"it replays {replay.fitting_traces} of the "
                            f"{replay.trace_count} traces of the log without problems")
    target = task.get("answer") or task.get("compute")
    if target:
        model = _model_answer(context, target, task)
        if task.get("exact") and _yes_no(task.get("exact")):
            same, why = _isomorphic(net, model)
            if not same:
                problems.append("it is not the same net as the model answer: " + why)
        else:
            comparison = compare_nets(net, model)
            if not comparison.equivalent:
                problems.append("it does not allow the same complete traces as the model answer")
    if not any(task.get(k) for k in ("sound", "fits", "answer", "compute")):
        return Result(UNKNOWN, "Nothing to check this net against: compare it with the "
                               "model answer yourself.")
    if problems:
        return Result(INCORRECT, "Not yet: " + "; ".join(problems) + ".",
                      comparison=comparison)
    good = []
    if target:
        good.append("the same net as the model answer" if task.get("exact")
                    else "same behaviour as the model answer")
    if task.get("sound"):
        good.append("sound")
    if task.get("fits"):
        good.append("replays every trace of the log")
    return Result(CORRECT, "Right: " + ", ".join(good) + ".", comparison=comparison)


def _isomorphic(first, second) -> tuple[bool, str]:
    """Are two nets the same up to the names of places (transitions by label)?"""
    from ..mining.transition_system import TransitionSystem, isomorphic
    if len(first.places) != len(second.places):
        return False, f"it has {len(first.places)} places, the model answer {len(second.places)}"
    if len(first.transitions) != len(second.transitions):
        return False, (f"it has {len(first.transitions)} transitions, the model answer "
                       f"{len(second.transitions)}")
    if len(first.arcs) != len(second.arcs):
        return False, f"it has {len(first.arcs)} arcs, the model answer {len(second.arcs)}"
    mine = {answers.name(t.label or "") for t in first.transitions.values()}
    theirs = {answers.name(t.label or "") for t in second.transitions.values()}
    if mine != theirs:
        return False, "its transition labels differ from the model answer's"
    from ..mining.analysis import reachability_graph
    from .computed import _with_start
    graphs = []
    for net in (first, second):
        net = _with_start(net)
        graph = reachability_graph(net, max_states=5_000)
        if graph.truncated or graph.has_omega:
            return True, ""                       # too big to tell apart: accept the structure
        ts = TransitionSystem()
        for i in range(len(graph.states)):
            ts.add_state(f"s{i}")
        for source, transition, target in graph.edges:
            ts.add_transition(f"s{source}", answers.name(net.transitions[transition].label or "τ"),
                              f"s{target}")
        ts.initial = ["s0"]
        graphs.append(ts)
    if not isomorphic(graphs[0], graphs[1]):
        return False, "its reachability graph differs from the model answer's"
    return True, ""


def _model_answer(context: Context, target: str, task: Task | None = None):
    """``answer.pnml``, or a net discovered from the log (``alpha``,
    ``inductive``), or a compute that gives a net (``synchronous product(⟨a, b⟩)``,
    ``box(heuristics_miner).net``)."""
    kind = target.strip().casefold()
    if kind in ("alpha", "alpha(log)", "α"):
        return context.alpha.net
    if kind in ("inductive", "inductive(log)", "im"):
        from ..mining.discovery.inductive import inductive_miner
        return inductive_miner(context.simple_log()).net
    if context.files.file(target) is not None or target.lower().endswith(".pnml"):
        return context.net(target)
    from ..mining.petrinet import PetriNet
    from ..mining.processtree import ProcessTree, to_petri_net
    value = compute(context, task or Task("net", "net"), target)
    if isinstance(value, ProcessTree):
        return to_petri_net(value)
    if isinstance(value, notation.Tree):
        raise TaskError("a tree compute does not give a net; use im.tree with a tree block")
    if hasattr(value, "net") and isinstance(value.net, PetriNet):
        return value.net
    if not isinstance(value, PetriNet):
        raise TaskError(f"“{target}” does not give a net")
    return value


# -- the new blocks -------------------------------------------------------------------------
def _check_marking(context, task, answer) -> Result:
    right = expected(context, task)
    right = notation.marking(right) if isinstance(right, str) else Counter(
        {answers.name(k): v for k, v in dict(right).items()})
    mine = notation.marking(str(answer))
    if mine == right:
        return Result(CORRECT, "Right.")
    missing = +Counter(right) - Counter(mine)
    extra = +Counter(mine) - Counter(right)
    parts = []
    if extra:
        parts.append(f"{sum(extra.values())} token{'s' * (sum(extra.values()) != 1)} too many")
    if missing:
        parts.append(f"{sum(missing.values())} missing")
    total = sum(right.values()) or 1
    hits = sum((Counter(mine) & Counter(right)).values())
    partial = bool(hits) and not extra
    return Result(PARTIAL if partial else INCORRECT, "Not yet: " + ", ".join(parts) + ".",
                  share=hits / total if partial else 0.0)


def _check_markings(context, task, answer) -> Result:
    right = expected(context, task)
    right = notation.markings(right) if isinstance(right, str) else frozenset(right)
    mine = notation.markings(str(answer))
    if mine == right:
        return Result(CORRECT, f"Right: {len(right)} marking{'s' * (len(right) != 1)}.")
    hits, extra, missing = len(mine & right), len(mine - right), len(right - mine)
    parts = []
    if hits:
        parts.append(f"{hits} of your markings {'is' if hits == 1 else 'are'} right")
    if extra:
        parts.append(f"{extra} {'is' if extra == 1 else 'are'} not reachable")
    if missing:
        parts.append(f"{missing} {'is' if missing == 1 else 'are'} missing")
    return Result(PARTIAL if hits else INCORRECT, "Not yet: " + ", ".join(parts) + ".",
                  share=max(0.0, (hits - extra) / len(right)) if right else 0.0)


def _check_tuple(context, task, answer) -> Result:
    """(P, T, F, m0): each part against ``net.P``, ``net.T``, ``net.F``, ``net.m0``."""
    mine = notation.net_tuple(answer)
    context.net(task.get("of") or task.get("net"))          # a clear error when it is missing
    from .computed import COMPUTED as C
    right = {"P": answers.normalised(C["net.p"](context, "", task)),
             "T": answers.normalised(C["net.t"](context, "", task)),
             "F": answers.normalised(C["net.f"](context, "", task)),
             "m0": notation.marking_key(C["net.m0"](context, "", task))}
    parts, verdicts, hits = [], {}, 0
    for key in notation.TUPLE_FIELDS:
        ok = mine[key] == right[key]
        verdicts[key] = CORRECT if ok else (INCORRECT if not mine[key] else
                                            (PARTIAL if (mine[key] & right[key]) else INCORRECT))
        hits += ok
        if not ok:
            label = {"P": "P", "T": "T", "F": "F", "m0": "m₀"}[key]
            if not mine[key]:
                parts.append(f"{label} is empty")
            elif key == "m0":
                parts.append(f"{label} is not the initial marking")
            else:
                extra, missing = len(mine[key] - right[key]), len(right[key] - mine[key])
                parts.append(f"{label}: " + ", ".join(x for x in (
                    f"{extra} too many" if extra else "", f"{missing} missing" if missing else "") if x))
    if hits == 4:
        return Result(CORRECT, "Right: P, T, F and m₀ all agree with the net.", parts=verdicts)
    return Result(PARTIAL if hits else INCORRECT, f"Not yet ({hits} of 4 parts right): "
                  + "; ".join(parts) + ".", share=hits / 4, parts=verdicts)


def _check_ts(context, task, answer) -> Result:
    from ..mining.transition_system import isomorphic, parse_transition_system
    right = expected(context, task)
    if isinstance(right, str):
        try:
            right = parse_transition_system(right)
        except ValueError as error:
            raise TaskError(f"the block's answer is not a transition system: {error}") from None
    try:
        mine = parse_transition_system(str(answer))
    except ValueError as error:
        return Result(UNKNOWN, f"Could not read your transition system: {error}")
    for ts in (mine, right):
        ts.transitions = [(s, answers.name(e), t) for s, e, t in ts.transitions]
    if isomorphic(mine, right):
        return Result(CORRECT, f"Right: {len(right.reachable())} states and "
                               f"{len(right.transitions)} transitions, the same up to names.")
    states, arcs = len(mine.reachable()), len(mine.transitions)
    wanted_states, wanted_arcs = len(right.reachable()), len(right.transitions)
    hints = []
    if states != wanted_states:
        hints.append(f"you have {states} reachable state{'s' * (states != 1)}, it should be {wanted_states}")
    if arcs != wanted_arcs:
        hints.append(f"{arcs} transition{'s' * (arcs != 1)} instead of {wanted_arcs}")
    if not hints:
        hints.append("the numbers agree, but the shape does not (look at which events leave which state)")
    close = states == wanted_states or arcs == wanted_arcs
    return Result(PARTIAL if close else INCORRECT, "Not yet: " + "; ".join(hints) + ".",
                  share=0.5 if close else 0.0)


def _check_matrix(context, task, answer) -> Result:
    right = expected(context, task)
    right = (notation.matrix(right) if isinstance(right, str) else right).normalised()
    mine = (notation.matrix(answer) if isinstance(answer, str) else _matrix_from_grid(answer)).normalised()
    if set(mine.rows) != set(right.rows) or set(mine.columns) != set(right.columns):
        missing_rows = sorted(set(right.rows) - set(mine.rows))
        missing_cols = sorted(set(right.columns) - set(mine.columns))
        extra = sorted((set(mine.rows) - set(right.rows)) | (set(mine.columns) - set(right.columns)))
        parts = [f"rows missing: {', '.join(missing_rows)}" if missing_rows else "",
                 f"columns missing: {', '.join(missing_cols)}" if missing_cols else "",
                 f"not in the matrix: {', '.join(extra)}" if extra else ""]
        return Result(INCORRECT, "Not yet: " + "; ".join(p for p in parts if p) + ".")
    wrong = [(r, c) for r in right.rows for c in right.columns
             if abs((mine.cells.get((r, c)) or 0) - (right.cells.get((r, c)) or 0)) > 1e-9]
    cells = len(right.rows) * len(right.columns)
    if not wrong:
        return Result(CORRECT, f"Right: all {cells} cells.")
    return Result(PARTIAL if len(wrong) < cells else INCORRECT,
                  f"{cells - len(wrong)} of {cells} cells are right; {len(wrong)} "
                  f"{'is' if len(wrong) == 1 else 'are'} wrong (marked).",
                  wrong_cells=wrong, share=(cells - len(wrong)) / cells)


def _matrix_from_grid(value: dict) -> notation.Matrix:
    """The matrix editor's value: ``{"rows": [...], "columns": [...], "cells": {"r\\tc": v}}``."""
    rows, columns = list(value.get("rows", [])), list(value.get("columns", []))
    cells = {}
    for key, cell in (value.get("cells") or {}).items():
        r, _, c = key.partition("\t")
        if str(cell).strip() == "":
            continue
        try:
            cells[(r, c)] = answers.number(str(cell))
        except (ValueError, ZeroDivisionError):
            raise AnswerSyntaxError(f"“{cell}” is not a number") from None
    return notation.Matrix(rows, columns, cells)


def _check_cut(context, task, answer) -> Result:
    right = expected(context, task)
    right = notation.cut(right) if isinstance(right, str) else right
    operator, groups = right
    groups = tuple(frozenset(answers.name(a) for a in g) for g in groups)
    mine_operator, mine_groups = notation.cut(str(answer))
    ordered = operator in ("→", "↺")
    same = (mine_groups == groups) if ordered else (set(mine_groups) == set(groups))
    if mine_operator == operator and same:
        return Result(CORRECT, f"Right: a {operator} cut into {len(groups)} parts.")
    parts = []
    if mine_operator != operator:
        parts.append("the operator is not the one the Inductive Miner finds first")
    if set(mine_groups) == set(groups) and ordered:
        parts.append("the groups are right but not in this order")
    elif not same:
        hits = len(set(mine_groups) & set(groups))
        parts.append(f"{hits} of the {len(groups)} groups {'is' if hits == 1 else 'are'} right")
    share = 0.5 if (mine_operator == operator or same) else 0.0
    return Result(PARTIAL if share else INCORRECT, "Not yet: " + "; ".join(parts) + ".", share=share)


def _check_log(context, task, answer) -> Result:
    right = expected(context, task)
    right = notation.log(right) if isinstance(right, str) else Counter(
        {tuple(answers.name(a) for a in t): n for t, n in dict(right).items()})
    mine = notation.log(str(answer))
    if mine == right:
        return Result(CORRECT, f"Right: {len(right)} variant{'s' * (len(right) != 1)}, "
                               f"{sum(right.values())} traces.")
    hits = sum((Counter(mine) & Counter(right)).values())
    total = sum(right.values())
    parts = []
    extra = sum((+(Counter(mine) - Counter(right))).values())
    missing = sum((+(Counter(right) - Counter(mine))).values())
    if extra:
        parts.append(f"{extra} trace{'s' * (extra != 1)} should not be there")
    if missing:
        parts.append(f"{missing} {'is' if missing == 1 else 'are'} missing")
    return Result(PARTIAL if hits else INCORRECT, "Not yet: " + ", ".join(parts) + ".",
                  share=max(0.0, (hits - extra) / total) if total else 0.0)


def _check_tree(context, task, answer) -> Result:
    right = expected(context, task)
    right = notation.tree(right) if isinstance(right, str) else right
    mine = notation.tree(str(answer))
    if mine.key() == right.key():
        return Result(CORRECT, "Right: the same tree.")
    if mine.operator == right.operator and len(mine.children) == len(right.children):
        return Result(PARTIAL, "Not yet: the root and its number of children are right, "
                               "but something below differs.", share=0.5)
    if {a for a in _leaves(mine)} != {a for a in _leaves(right)}:
        return Result(INCORRECT, "Not yet: the leaves are not the activities of the log.")
    return Result(INCORRECT, "Not yet: the tree differs from the one the miner finds.")


def _leaves(tree: notation.Tree) -> list:
    if tree.operator is None:
        return [tree.label]
    return [leaf for child in tree.children for leaf in _leaves(child)]


def _check_replay(context, task, answer) -> Result:
    right = expected(context, task)
    if isinstance(right, str):
        right = notation.replay_table(right)
    mine = notation.replay_table(answer)
    wrong, cells = [], 0
    for trace, row in right.items():
        for column in notation.REPLAY_COLUMNS:
            cells += 1
            value = mine.get(trace, {}).get(column)
            if value is None or abs(value - row[column]) > 1e-9:
                wrong.append(("⟨" + ", ".join(trace) + "⟩", column))
    if not cells:
        raise TaskError("the replay has no traces")
    if not wrong:
        return Result(CORRECT, f"Right: all {cells} figures.")
    return Result(PARTIAL if len(wrong) < cells else INCORRECT,
                  f"{cells - len(wrong)} of {cells} figures are right; {len(wrong)} "
                  f"{'is' if len(wrong) == 1 else 'are'} wrong or missing (marked).",
                  wrong_cells=wrong, share=(cells - len(wrong)) / cells)


def _check_alignment(context, task, answer) -> Result:
    moves = notation.alignment(str(answer))
    net = context.net(task.get("of") or task.get("net"))
    trace = tuple(answers.trace(task.get("trace"))) if task.get("trace") else None
    kind, why = alignment_kind(net, moves, trace)
    wanted = (task.get("answer") or "optimal").strip().casefold()
    if kind == wanted:
        return Result(CORRECT, f"Right: {why}.")
    if kind == "sub-optimal" and wanted == "optimal":
        return Result(PARTIAL, f"It is an alignment, but not an optimal one: {why}.", share=0.5)
    return Result(INCORRECT, f"Not yet: it is {kind} ({why}).")


def _check_ranking(context, task, answer) -> Result:
    names = task.get("over")
    if not names:
        raise TaskError("a ranking needs “over:” with the names to rank")
    candidates = [n.strip() for n in names.split(",") if n.strip()]
    mine = notation.ranking(answer)
    keys = {answers.name(c.replace(".pnml", "")): c for c in candidates}
    unknown = [m for m in mine if m not in keys]
    if unknown:
        return Result(UNKNOWN, "Not among the names to rank: " + ", ".join(unknown) + ".")
    if len(mine) != len(candidates):
        return Result(UNKNOWN, f"Rank all {len(candidates)} of them.")
    scores = _scores(context, task, candidates)
    by = {answers.name(c.replace(".pnml", "")): scores[c] for c in candidates}
    higher_is_better = (task.get("order") or "high").strip().lower() not in ("low", "ascending", "lowest first")
    for a, b in zip(mine, mine[1:]):
        bad = by[a] < by[b] if higher_is_better else by[a] > by[b]
        if bad:
            right_count = sum(1 for x, y in zip(mine, mine[1:])
                              if not (by[x] < by[y] if higher_is_better else by[x] > by[y]))
            return Result(INCORRECT if right_count == 0 else PARTIAL,
                          f"Not yet: {keys[a]} is ranked above {keys[b]}, but scores "
                          f"{'lower' if higher_is_better else 'higher'}.",
                          share=right_count / max(1, len(mine) - 1))
    return Result(CORRECT, "Right: " + " ≥ ".join(f"{keys[m]} ({by[m]:.3g})" for m in mine) + ".")


def _scores(context, task, candidates: list[str]) -> dict[str, float]:
    """``by: fitness`` (the default) of every candidate net on the log."""
    by = (task.get("by") or "fitness").strip()
    found, argument = lookup(by)
    scores = {}
    for candidate in candidates:
        scoped = Task(task.type, task.id, settings={**task.settings, "of": candidate})
        scores[candidate] = float(found(context, argument, scoped))
    return scores


def _check_predict(context, task, answer) -> Result:
    """``predict``: a set, number, yes/no or text answer whose right value is a
    box's result (``box: alpha_miner``, ``value: net.transitions``)."""
    box_id = task.get("box")
    if not box_id:
        raise TaskError("a predict block needs “box:” (the box to predict)")
    expression = f"box({box_id})" + ("." + task.get("value") if task.get("value") else "")
    scoped = Task(_predict_kind(task), task.id, settings={**task.settings, "compute": expression})
    scoped.options = task.options
    return CHECKERS[scoped.type](context, scoped, answer)


def _predict_kind(task: Task) -> str:
    kind = (task.get("as") or "").strip().lower()
    if kind in CHECKERS and kind not in ("predict", "workflow"):
        return kind
    return "set"


def _check_workflow(context, task, answer) -> Result:
    """``workflow``: the student's workflow (saved beside the question) must
    have the boxes in ``needs:`` and, with ``result:``, a box's value must be
    ``answer:`` / ``compute:``."""
    from ..flow.record import load
    from ..flow.runner import Cache, Runner
    path = context.exercise.folder / (answer if isinstance(answer, str) and answer else "my workflow.cpnflow")
    if not path.exists():
        return Result(UNKNOWN, "Build the workflow in the Workflow tab first (it is saved beside "
                               "the question).")
    wf, _record = load(path, context.library)
    problems, share, checks = [], 0.0, 0
    needs = [n.strip() for n in (task.get("needs") or "").split(",") if n.strip()]
    if needs:
        checks += 1
        present = {n.box for n in wf.nodes.values()} | {n.box.rsplit(".", 1)[-1] for n in wf.nodes.values()}
        present |= {answers.name(wf.spec(n.id).name) for n in wf.nodes.values()}
        missing = [n for n in needs if n not in present and answers.name(n) not in present]
        if missing:
            problems.append("missing: " + ", ".join(missing))
        else:
            share += 1
    if task.get("result"):
        checks += 1
        run = Runner(context.library, Cache(), folder=context.exercise.folder).run(wf)
        node_name, _, rest = task.get("result").partition(".")
        node = next((n for n in wf.nodes.values()
                     if answers.name(wf.title(n.id)) == answers.name(node_name)
                     or n.box.rsplit(".", 1)[-1] == node_name or n.id == node_name), None)
        if node is None:
            problems.append(f"no box called “{node_name}” in your workflow")
        else:
            result = run.result(node)
            if result is None or result.status != "done":
                problems.append(f"“{wf.title(node.id)}” did not run: " + (result.error if result else ""))
            else:
                from .computed import follow_path
                value = follow_path(result.value, rest)
                scoped = Task(_result_kind(value), task.id, settings={k: v for k, v in task.settings.items()
                                                                     if k in ("answer", "compute", "tolerance", "of")})
                verdict = CHECKERS[scoped.type](context, scoped, _as_text(value))
                if verdict.correct:
                    share += 1
                else:
                    problems.append(f"the result of “{wf.title(node.id)}” is not right ({verdict.message.rstrip('.')})")
    if not checks:
        return Result(UNKNOWN, "Nothing to check this workflow against: compare it with the "
                               "model answer yourself.")
    if problems:
        return Result(PARTIAL if share else INCORRECT, "Not yet: " + "; ".join(problems) + ".",
                      share=share / checks)
    return Result(CORRECT, "Right: the workflow has what it needs and its result agrees.")


def _result_kind(value) -> str:
    if isinstance(value, bool):
        return "yesno"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (set, frozenset, list)):
        return "set"
    return "text"


def _as_text(value) -> str:
    if isinstance(value, (set, frozenset, list)):
        return answers.show(frozenset(answers.normalised(v) for v in value))
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


CHECKERS = {"set": _check_set, "number": _check_number, "yesno": _check_yesno,
            "choice": _check_choice, "text": _check_text, "footprint": _check_footprint,
            "trace": _check_trace, "net": _check_net, "marking": _check_marking,
            "markings": _check_markings, "tuple": _check_tuple, "ts": _check_ts,
            "matrix": _check_matrix, "cut": _check_cut, "log": _check_log, "tree": _check_tree,
            "replay": _check_replay, "alignment": _check_alignment, "ranking": _check_ranking,
            "predict": _check_predict, "workflow": _check_workflow}


# ---------------------------------------------------------------------------
# The right answer in words
# ---------------------------------------------------------------------------
def show_value(value) -> str:
    """A computed value in the notation of the course."""
    from ..mining.transition_system import TransitionSystem
    if isinstance(value, bool):
        return "Yes." if value else "No."
    if isinstance(value, float) and value == float("inf"):
        return "∞ (unbounded)"
    if isinstance(value, (int, float)):
        return f"{float(value):.4g}"
    if isinstance(value, notation.Matrix):
        return notation.show_matrix(value)
    if isinstance(value, notation.Tree):
        return str(value)
    if isinstance(value, TransitionSystem):
        return value.to_text()
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and value[0] in "→×∧↺":
        return notation.show_cut(value)
    if isinstance(value, Counter):
        if value and all(isinstance(k, tuple) for k in value):
            return notation.show_log(value)
        return notation.show_marking(value)
    if isinstance(value, dict):
        rows = []
        for key, row in value.items():
            if isinstance(row, dict):
                rows.append("⟨" + ", ".join(key) + "⟩: " + "  ".join(
                    f"{c} = {row[c]:g}" for c in notation.REPLAY_COLUMNS if c in row))
            else:
                rows.append(f"{key}: {row}")
        return "\n".join(rows)
    if isinstance(value, frozenset) and value and all(isinstance(v, frozenset) and all(
            isinstance(x, tuple) and len(x) == 2 and isinstance(x[1], int) for x in v) for v in value):
        return notation.show_markings(value)
    if isinstance(value, (frozenset, set)):
        value = answers.normalised(value)
        return answers.show_set(value)
    if isinstance(value, str):
        value = answers.normalised(value)
        return answers.show_set(answers.as_set(value)) if value.startswith("{") else value
    return str(value)


def model_answer_text(exercise: Exercise, task: Task, context: Context | None = None) -> str:
    """The right answer in words, for *Show answer* (the block's ``solution`` first)."""
    if task.solution:
        return task.solution
    return computed_answer_text(exercise, task, context)


def computed_answer_text(exercise: Exercise, task: Task, context: Context | None = None) -> str:
    """The right answer as the app works it out, whatever ``solution`` says
    (for authors: ``openprocess exercises check --answers``)."""
    context = context or Context(exercise)
    try:
        if task.type == "choice":
            if any(o.correct for o in task.options):
                return "; ".join(o.text for o in task.options if o.correct) + "."
            return show_value(compute(context, task)) if task.get("compute") else ""
        if task.type == "text":
            return task.settings.get("answer") or (task.alternatives[0] if task.alternatives
                                                   else (show_value(compute(context, task))
                                                         if task.get("compute") else ""))
        if task.type == "predict":
            value = compute(context, task, f"box({task.get('box')})" +
                            ("." + task.get("value") if task.get("value") else ""))
            return show_value(value)
        if task.type == "tuple":
            from .computed import COMPUTED as C
            return "\n".join(f"{label} = {show_value(C[key](context, '', task))}" for label, key in
                             (("P", "net.p"), ("T", "net.t"), ("F", "net.f"), ("m₀", "net.m0")))
        if task.type == "ranking":
            candidates = [n.strip() for n in (task.get("over") or "").split(",") if n.strip()]
            scores = _scores(context, task, candidates)
            order = sorted(candidates, key=lambda c: -scores[c])
            return " > ".join(f"{c} ({scores[c]:.3g})" for c in order)
        if task.type == "alignment":
            from ..mining.conformance.alignments import align_trace
            from .computed import _with_start
            net = _with_start(context.net(task.get("of") or task.get("net")))
            trace = tuple(answers.trace(task.get("trace"))) if task.get("trace") else ()
            best = align_trace(net, trace)
            return notation.show_alignment((m.log, m.model) for m in best.moves) + \
                f"\ncost {best.cost}"
        if task.type in ("set", "number", "yesno", "marking", "markings", "ts", "matrix", "cut",
                         "log", "tree", "replay"):
            value = expected(context, task)
            if isinstance(value, str) and task.type == "set":
                value = answers.as_set(answers.normalised(value))
            if isinstance(value, str) and task.type == "yesno":
                value = _yes_no(value)
            if isinstance(value, str) and task.type == "number":
                return value
            return show_value(value)
    except TaskError as error:
        return f"(The answer could not be worked out: {error}.)"
    return ""


# ---------------------------------------------------------------------------
# For authors: is the pack consistent?
# ---------------------------------------------------------------------------
def validate(exercise: Exercise) -> list[str]:
    """Problems with the exercise's answer blocks (for authors: ``openprocess exercises check``)."""
    problems = []
    if exercise.error:
        problems.append(exercise.error)
    context = Context(exercise, student=exercise.student() or "the author")
    from .sheet import FRONT_KEYS
    for key in getattr(exercise.sheet, "settings", {}) or {}:
        if key not in FRONT_KEYS:
            problems.append(f"front matter: unknown key “{key}” (known: {', '.join(sorted(FRONT_KEYS))})")
    for task in exercise.sheet.tasks:
        where = f"answer block “{task.id}” (line {task.line})" if task.line else \
            f"answer “{task.id}”"
        try:
            if task.get("compute"):
                lookup(task.get("compute"))
            if task.type in ("set", "number", "yesno", "marking", "markings", "ts", "matrix",
                             "cut", "log", "tree", "replay") and task.checkable:
                value = expected(context, task)
                if task.type == "yesno":
                    _yes_no(value)
                if task.type == "number" and isinstance(value, str):
                    answers.number(value)
                if isinstance(value, str):
                    reader = {"marking": notation.marking, "markings": notation.markings,
                              "matrix": notation.matrix, "cut": notation.cut, "log": notation.log,
                              "tree": notation.tree, "replay": notation.replay_table}.get(task.type)
                    if reader is not None:
                        reader(value)
            elif task.type == "footprint":
                _footprint_pairs(task, footprint_of(context, task))
            elif task.type == "trace":
                ends = (task.get("ends") or "any").lower()
                if ends not in ("any", "final", "deadlock", "stuck", "no completion", "cannot complete",
                                "improper", "improper completion"):
                    raise TaskError(f"unknown “ends: {ends}” (use any, final, deadlock, stuck or improper)")
                if task.get("fits"):
                    _model_answer(context, task.get("fits"), task)
                else:
                    context.net(task.get("net") or task.get("of"))
            elif task.type == "net":
                if task.get("answer") or task.get("compute"):
                    _model_answer(context, task.get("answer") or task.get("compute"), task)
                if task.get("start") and task.get("start").lower() != "empty" \
                        and exercise.files.file(task.get("start")) is None:
                    raise TaskError(f"the exercise has no file “{task.get('start')}”")
            elif task.type == "tuple":
                context.net(task.get("of") or task.get("net"))
            elif task.type == "alignment":
                context.net(task.get("of") or task.get("net"))
                wanted = (task.get("answer") or "optimal").strip().casefold()
                if wanted not in ("optimal", "sub-optimal", "not an alignment"):
                    raise TaskError(f"“answer: {wanted}” is not one of optimal, sub-optimal, not an alignment")
                if task.get("trace"):
                    answers.trace(task.get("trace"))
                if task.get("alignment"):
                    notation.alignment(task.get("alignment"))
            elif task.type == "ranking":
                candidates = [n.strip() for n in (task.get("over") or "").split(",") if n.strip()]
                if len(candidates) < 2:
                    raise TaskError("“over:” must name at least two things to rank")
                _scores(context, task, candidates)
            elif task.type == "predict":
                if not task.get("box"):
                    raise TaskError("a predict block needs “box:”")
                kind = (task.get("as") or "").strip().lower()
                if kind and kind not in ("set", "number", "yesno", "text"):
                    raise TaskError(f"“as: {kind}” is not one of set, number, yesno, text")
                compute(context, task, f"box({task.get('box')})" +
                        ("." + task.get("value") if task.get("value") else ""))
            elif task.type == "workflow":
                if not task.get("needs") and not task.get("result"):
                    raise TaskError("a workflow block needs “needs:” or “result:”")
            elif task.type == "choice" and task.get("compute"):
                compute(context, task)
            if task.get("points"):
                float(task.get("points"))
            if not task.checkable and task.type not in ("open",) and not task.solution:
                problems.append(f"{where}: nothing to check against and no solution")
        except (TaskError, AnswerSyntaxError, ValueError) as error:
            problems.append(f"{where}: {error}")
        except Exception as error:  # noqa: BLE001 - report, do not crash the listing
            problems.append(f"{where}: {type(error).__name__}: {error}")
    return problems
