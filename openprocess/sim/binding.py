"""Binding search: deciding which transitions can fire, and with what values.

The problem
-----------
A transition's arc inscriptions contain free variables.  ``(x, n)`` on an input
arc does not say *which* token to take -- it says "take any token whose value
fits this shape, and let ``x`` and ``n`` be its parts".  Before we can fire
anything we must find every assignment of values to variables such that the
consumed tokens are actually present and the guard holds.  Such an assignment
is a **binding**, and a transition plus a binding is a **binding element**.

The naive method -- enumerate the Cartesian product of every variable's colour
set and test each combination -- is correct but hopeless: three variables over
a 100-element colour set is a million candidates per transition per step.

The method used here
--------------------
**Pattern-directed search with backtracking.**  We read each input arc
inscription as a *pattern* and match it against the tokens that are really in
the place, which yields the variable values directly instead of guessing them.

Concretely, an arc inscription is first decomposed into terms::

    2`(x, n) ++ 1`y      ->   [ (2, (x,n)), (1, y) ]

Each term becomes one *demand*: "take this many tokens of this shape from this
place".  Demands are then satisfied one at a time, depth first:

1. Pick the next unsatisfied demand.
2. If its value expression is a pattern, try each distinct value still
   available in that place, matching to extend the binding; recurse.
3. If it is not a pattern (``1`(n+1)``, say), it can only be *evaluated*, so we
   defer it until its variables are bound by other demands, then check that
   enough copies are present.
4. When every demand is satisfied, enumerate any variables that are still free
   -- ones appearing only in the guard, the output arcs, or the time
   expression -- over their declared colour sets.
5. Evaluate the guard.  If it holds, we have a binding element.

Backtracking happens naturally: if a choice at step 2 leads to a dead end
further down, the loop simply tries the next candidate value.

Consumption bookkeeping
-----------------------
Two demands may draw on the same place -- either two terms of one inscription,
or two arcs from the same place to the same transition.  A ``remaining``
multiset per place is threaded through the search so that a token cannot be
counted twice.

Limits, stated honestly
-----------------------
Step 4 needs a *finite* colour set.  A variable that appears only on an output
arc and is typed ``INT`` cannot be enumerated, and we raise a clear error
naming the variable rather than looping forever.  In practice such variables
are either bound by an input arc or declared over a finite colour set, which is
also what CPN Tools requires.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Sequence

from ..ml.ast_nodes import (
    App, BinOp, CaseExpr, Coefficient, Delay, EmptyMultiset, Expr, FnExpr,
    IfExpr, LetExpr, ListExpr, Literal, RecordExpr, Selector, TupleExpr, UnOp,
    Var,
)
from ..ml.colorsets import InfiniteColourSetError
from ..ml.errors import CPNMLError, EvalError
from ..ml.evaluator import ConstructorFunction, Environment, Evaluator, to_multiset
from ..ml.multiset import Multiset, TimedMultiset
from ..ml.parser import expression_to_pattern
from ..ml.values import Constructor, format_value
from ..model.net import Arc, CPNet, Marking, Place, Transition


# ---------------------------------------------------------------------------
# Free variable collection
# ---------------------------------------------------------------------------
def free_variables(expression: Expr | None, declared: set[str]) -> set[str]:
    """Names from ``declared`` that occur in ``expression``.

    We intersect with the model's declared variables rather than collecting
    every identifier, because an inscription also mentions function names,
    constructors and constants, none of which are variables to bind.  CPN
    requires variables to be declared, so this is exact rather than heuristic.
    """
    if expression is None:
        return set()

    found: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Var):
            if node.name in declared:
                found.add(node.name)
            return
        if isinstance(node, (Literal, EmptyMultiset, Selector)):
            return
        if isinstance(node, (TupleExpr, ListExpr)):
            for item in node.items:
                visit(item)
            return
        if isinstance(node, RecordExpr):
            for _name, value in node.fields:
                visit(value)
            return
        if isinstance(node, Coefficient):
            visit(node.count)
            visit(node.value)
            return
        if isinstance(node, Delay):
            visit(node.base)
            visit(node.delay)
            return
        if isinstance(node, App):
            visit(node.function)
            visit(node.argument)
            return
        if isinstance(node, BinOp):
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, UnOp):
            visit(node.operand)
            return
        if isinstance(node, IfExpr):
            visit(node.condition)
            visit(node.then_branch)
            visit(node.else_branch)
            return
        if isinstance(node, LetExpr):
            for declaration in node.declarations:
                visit(getattr(declaration, "expression", None))
                for _patterns, body in getattr(declaration, "clauses", ()):
                    visit(body)
            visit(node.body)
            return
        if isinstance(node, CaseExpr):
            visit(node.scrutinee)
            for _pattern, body in node.rules:
                visit(body)
            return
        if isinstance(node, FnExpr):
            for _pattern, body in node.rules:
                visit(body)
            return

    visit(expression)
    return found


# ---------------------------------------------------------------------------
# Decomposing an arc inscription into demands
# ---------------------------------------------------------------------------
@dataclass
class Demand:
    """One "take *count* tokens shaped like *value* out of *place*" obligation."""

    place_id: str
    count: Expr
    value: Expr
    #: The pattern form of ``value``, or ``None`` if it is not a pattern.
    pattern: Any = None

    @property
    def is_pattern(self) -> bool:
        return self.pattern is not None


def split_terms(expression: Expr) -> list[tuple[Expr, Expr]] | None:
    """Split ``2`a ++ 1`b`` into ``[(2, a), (1, b)]``.

    Returns ``None`` when the inscription is not a plain sum of coefficient
    terms -- for example when it contains ``--``, an ``if``, or a function call
    producing a multiset.  Such an inscription cannot be turned into demands and
    is handled by the fallback path (evaluate, then check inclusion).
    """
    terms: list[tuple[Expr, Expr]] = []

    def visit(node: Expr) -> bool:
        if isinstance(node, BinOp) and node.operator == "++":
            return visit(node.left) and visit(node.right)
        if isinstance(node, Coefficient):
            terms.append((node.count, node.value))
            return True
        if isinstance(node, EmptyMultiset):
            return True
        # A bare value is one token: `x` means `1`x`.
        if isinstance(node, (Var, Literal, TupleExpr, ListExpr, RecordExpr, App)):
            terms.append((Literal(1, node.position), node))
            return True
        # `h :: t` (take a list token apart) is one token as well.
        if isinstance(node, BinOp) and node.operator == "::":
            terms.append((Literal(1, node.position), node))
            return True
        return False

    return terms if visit(expression) else None


# ---------------------------------------------------------------------------
# Binding elements
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BindingElement:
    """A transition together with a concrete assignment to its variables.

    This is the unit the simulator fires and the unit a state space arc is
    labelled with, so it must be hashable -- hence the tuple of sorted pairs
    rather than a dict.
    """

    transition_id: str
    assignments: tuple[tuple[str, Any], ...]

    @staticmethod
    def create(transition: Transition, bindings: dict[str, Any]) -> "BindingElement":
        return BindingElement(
            transition.id, tuple(sorted(bindings.items(), key=lambda pair: pair[0]))
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.assignments)

    def describe(self, net: CPNet) -> str:
        """``T1 <x = 3, y = "a">`` -- CPN Tools' notation for a binding element."""
        transition = net.find_transition(self.transition_id)
        name = transition.name if transition else self.transition_id
        if not self.assignments:
            return name
        inner = ", ".join(f"{k} = {format_value(v)}" for k, v in self.assignments)
        return f"{name} <{inner}>"

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={format_value(v)}" for k, v in self.assignments)
        return f"<{self.transition_id} {inner}>"


class Binder:
    """Computes the enabled binding elements of a transition in a marking."""

    def __init__(self, net: CPNet, max_bindings_per_transition: int = 5000) -> None:
        self.net = net
        self.evaluator: Evaluator = net.evaluator
        self.declared_variables = set(net.declarations.variables)
        #: Safety valve.  A model with a badly constrained variable can produce
        #: an enormous number of bindings; we stop rather than hang, and the
        #: simulator surfaces this as a warning.
        self.max_bindings = max_bindings_per_transition
        self.truncated = False
        #: transition id -> (demand templates, opaque arcs); see bindings()
        self._analysis: dict[str, tuple] = {}
        #: cache for variables_in(): arc/guard expression id -> variable names
        self._variables: dict[int, set[str]] = {}
        self._keep_alive: list = []

    def identifier_role(self, name: str) -> str:
        """Classify an identifier for pattern conversion.

        Order matters.  A declared ``var`` is a variable even if capitalised.
        Otherwise, an identifier bound in the global environment to a
        constructor value or a constructor function is a constructor; anything
        else bound there is an ML value or function, which cannot appear in a
        pattern.  An unbound identifier is treated as a variable, which keeps
        partially-declared models usable in the editor.
        """
        if name in self.declared_variables:
            return "variable"
        if self.evaluator.globals.contains(name):
            bound = self.evaluator.globals.lookup(name)
            if isinstance(bound, (Constructor, ConstructorFunction)):
                return "constructor"
            return "other"
        return "variable"

    def _free(self, expression) -> set[str]:
        """Cached :func:`free_variables` (expressions are immutable)."""
        if expression is None:
            return set()
        key = id(expression)
        found = self._variables.get(key)
        if found is None:
            found = free_variables(expression, self.declared_variables)
            self._variables[key] = found
            self._keep_alive.append(expression)   # ids stay valid while cached
        return found

    def _analyse_inputs(self, input_arcs):
        demands: list[Demand] = []
        opaque_arcs: list[Arc] = []
        for arc in input_arcs:
            if arc.expression_ast is None:
                continue
            terms = split_terms(arc.expression_ast)
            if terms is None:
                opaque_arcs.append(arc)
                continue
            # Demands are keyed by the marking key, not the raw place id, so
            # that two places in the same fusion set draw on one shared pool.
            place_key = self.net.marking_key(arc.place_id)
            for count_expression, value_expression in terms:
                pattern = None
                try:
                    pattern = expression_to_pattern(self.inline_constants(value_expression),
                                                    self.identifier_role)
                except CPNMLError:
                    pattern = None
                demands.append(Demand(place_key, count_expression, value_expression, pattern))
        return tuple(demands), tuple(opaque_arcs)

    def inline_constants(self, node: Expr) -> Expr:
        """Replace named ML *values* (``val PNONE = (~1,~1,~1)``) by literals.

        An input inscription such as ``(nr, false, PNONE)`` is a perfectly
        good pattern -- PNONE is just a fixed value -- but a bare name in a
        pattern would otherwise be taken as a function and make the whole
        arc un-matchable, leaving ``nr`` to be enumerated over an infinite
        colour set.  Functions and constructors are left untouched.
        """
        from dataclasses import replace
        from ..ml.builtins import Builtin, PartialApplication
        from ..ml.evaluator import Closure, CurriedFunction

        if isinstance(node, Var):
            if node.name in self.declared_variables or not self.evaluator.globals.contains(node.name):
                return node
            value = self.evaluator.globals.lookup(node.name)
            if isinstance(value, (Closure, CurriedFunction, Builtin, PartialApplication, Constructor,
                                  ConstructorFunction)) or callable(value):
                return node
            return Literal(value, node.position)
        if isinstance(node, (TupleExpr, ListExpr)):
            return replace(node, items=tuple(self.inline_constants(i) for i in node.items))
        if isinstance(node, BinOp) and node.operator == "::":
            return replace(node, left=self.inline_constants(node.left),
                           right=self.inline_constants(node.right))
        if isinstance(node, RecordExpr):
            return replace(node, fields=tuple((n, self.inline_constants(v)) for n, v in node.fields))
        if isinstance(node, App):
            return replace(node, argument=self.inline_constants(node.argument))
        return node

    # -- available tokens ----------------------------------------------------
    def available(self, place: Place, marking: Marking, clock: int) -> Multiset:
        """Tokens of ``place`` that may be consumed at time ``clock``.

        For an untimed place this is simply its marking.  For a timed place it
        is the subset whose time stamp has been reached -- tokens stamped for
        the future are present but not yet usable, which is exactly what makes
        a timed net advance its clock instead of deadlocking.
        """
        tokens = marking.get(self.net.marking_key(place.id))
        if isinstance(tokens, TimedMultiset):
            return tokens.available_at(clock)
        return tokens

    # -- the search ----------------------------------------------------------
    def bindings(self, transition: Transition, marking: Marking,
                 clock: int = 0) -> list[BindingElement]:
        """All enabled binding elements for ``transition``, in canonical order."""
        page = self.net.page_of(transition)
        if page is None:
            return []

        input_arcs = [a for a in page.arcs_of(transition) if a.is_input]

        # Split the input arcs into ones we can turn into demands and ones we
        # can only evaluate once the variables are known.  This analysis
        # depends only on the inscriptions, not on the marking, so it is done
        # once per transition and cached (the Binder is rebuilt whenever the
        # model is recompiled).
        cached = self._analysis.get(transition.id)
        if cached is None:
            cached = self._analyse_inputs(input_arcs)
            self._analysis[transition.id] = cached
        template, opaque_arcs = cached
        demands = list(template)

        # Order demands so that the most constrained come first: fewer distinct
        # candidate values means less backtracking.  Non-pattern demands go
        # last, since they need other demands to have bound their variables.
        def demand_cost(demand: Demand) -> tuple[int, int]:
            place = self.net.find_place(demand.place_id)
            distinct = len(self.available(place, marking, clock).support()) if place else 0
            return (0 if demand.is_pattern else 1, distinct)

        demands.sort(key=demand_cost)

        remaining: dict[str, Multiset] = {}
        for demand in demands:
            if demand.place_id not in remaining:
                place = self.net.find_place(demand.place_id)
                remaining[demand.place_id] = (
                    self.available(place, marking, clock) if place else Multiset.empty()
                )
        for arc in opaque_arcs:
            key = self.net.marking_key(arc.place_id)
            if key not in remaining:
                place = self.net.find_place(arc.place_id)
                remaining[key] = (
                    self.available(place, marking, clock) if place else Multiset.empty()
                )

        results: list[BindingElement] = []
        self.truncated = False
        self.evaluator.set_model_time(clock)
        self._search(transition, demands, 0, {}, remaining, opaque_arcs, marking, clock, results)
        return results

    def _search(self, transition: Transition, demands: Sequence[Demand], index: int,
                bindings: dict[str, Any], remaining: dict[str, Multiset],
                opaque_arcs: Sequence[Arc], marking: Marking, clock: int,
                results: list[BindingElement], stalled: int = 0) -> None:
        """Depth-first satisfaction of the demand list.

        ``stalled`` counts how many demands in a row have been postponed
        because they could not be evaluated yet (see :meth:`_postpone`).
        """
        if len(results) >= self.max_bindings:
            self.truncated = True
            return

        if index == len(demands):
            self._finish(transition, bindings, remaining, opaque_arcs, marking, clock, results)
            return

        demand = demands[index]
        pool = remaining.get(demand.place_id, Multiset.empty())

        # How many tokens does this term want?  The coefficient may itself
        # mention variables, in which case it must already be bound.
        try:
            count = self._evaluate(demand.count, bindings)
        except CPNMLError:
            # Cannot evaluate yet -- postpone by moving this demand to the end.
            self._postpone(transition, demands, index, bindings, remaining,
                           opaque_arcs, marking, clock, results, stalled)
            return

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return
        if count == 0:
            self._search(transition, demands, index + 1, bindings, remaining,
                         opaque_arcs, marking, clock, results)
            return

        if demand.is_pattern:
            free = self._free(demand.value) - set(bindings)
            if not free:
                # Fully determined: evaluate it and check availability directly.
                self._consume_concrete(transition, demands, index, bindings, remaining,
                                       opaque_arcs, marking, clock, results,
                                       demand, count)
                return
            # Try every distinct value with enough copies left.
            for candidate, available_count in pool.items():
                if available_count < count:
                    continue
                trial = dict(bindings)
                if not self.evaluator.match(demand.pattern, candidate, trial,
                                            self.evaluator.globals):
                    continue
                updated = dict(remaining)
                updated[demand.place_id] = pool - Multiset.singleton(candidate, count)
                self._search(transition, demands, index + 1, trial, updated,
                             opaque_arcs, marking, clock, results)
            return

        # Not a pattern: it can only be evaluated.
        self._consume_concrete(transition, demands, index, bindings, remaining,
                               opaque_arcs, marking, clock, results, demand, count,
                               stalled)

    def _postpone(self, transition: Transition, demands: Sequence[Demand], index: int,
                  bindings: dict[str, Any], remaining: dict[str, Multiset],
                  opaque_arcs: Sequence[Arc], marking: Marking, clock: int,
                  results: list[BindingElement], stalled: int) -> None:
        """Move an unevaluable demand to the end, or unblock the search.

        A demand such as ``1`(n+1)`` cannot be evaluated until ``n`` is bound.
        Usually a later demand binds it, so the demand is rotated to the back.
        When *every* remaining demand has been rotated without progress, no
        demand will ever bind the missing variables (they occur only in these
        expressions, a guard or an output arc).  Then, as CPN Tools does, the
        missing variables are enumerated over their colour sets and the search
        continues with them bound.  Rotating for ever instead used to recurse
        until Python gave up.
        """
        if stalled + 1 < len(demands) - index:
            reordered = list(demands)
            reordered.append(reordered.pop(index))
            self._search(transition, reordered, index, bindings, remaining,
                         opaque_arcs, marking, clock, results, stalled + 1)
            return
        missing: set[str] = set()
        for demand in demands[index:]:
            missing |= self._free(demand.count) | self._free(demand.value)
        unbound = sorted(missing - set(bindings))
        if not unbound:
            return      # stuck on an evaluation error, not on a variable
        for combination in itertools.product(*self._domains(transition, unbound)):
            if len(results) >= self.max_bindings:
                self.truncated = True
                return
            trial = dict(bindings)
            trial.update(zip(unbound, combination))
            self._search(transition, demands, index, trial, remaining,
                         opaque_arcs, marking, clock, results)

    def _domains(self, transition: Transition, names: Sequence[str]) -> list[list[Any]]:
        """The values each of ``names`` ranges over, from its colour set."""
        domains: list[list[Any]] = []
        for name in names:
            colour_set = self.net.declarations.variable_colour_set(name)
            if colour_set is None:
                raise EvalError(
                    f"variable '{name}' on transition '{transition.name}' is not declared"
                )
            try:
                domains.append(list(colour_set.members()))
            except InfiniteColourSetError:
                raise EvalError(
                    f"variable '{name}' on transition '{transition.name}' is not "
                    f"determined by any input arc, and its colour set "
                    f"'{colour_set.name}' cannot be enumerated. Bind it on an input "
                    f"arc, or give it a finite colour set."
                )
        return domains

    def _consume_concrete(self, transition: Transition, demands: Sequence[Demand],
                          index: int, bindings: dict[str, Any],
                          remaining: dict[str, Multiset], opaque_arcs: Sequence[Arc],
                          marking: Marking, clock: int, results: list[BindingElement],
                          demand: Demand, count: int, stalled: int = 0) -> None:
        """Handle a demand whose value expression is fully determined."""
        try:
            value = self._evaluate(demand.value, bindings)
        except CPNMLError:
            # Still has unbound variables and is not a pattern: defer it.
            self._postpone(transition, demands, index, bindings, remaining,
                           opaque_arcs, marking, clock, results, stalled)
            return

        pool = remaining.get(demand.place_id, Multiset.empty())
        wanted = value * count if isinstance(value, Multiset) else Multiset.singleton(value, count)
        if not (wanted <= pool):
            return
        updated = dict(remaining)
        updated[demand.place_id] = pool - wanted
        self._search(transition, demands, index + 1, bindings, updated,
                     opaque_arcs, marking, clock, results)

    def _finish(self, transition: Transition, bindings: dict[str, Any],
                remaining: dict[str, Multiset], opaque_arcs: Sequence[Arc],
                marking: Marking, clock: int, results: list[BindingElement]) -> None:
        """All demands satisfied: bind leftover variables, then check the guard."""
        page = self.net.page_of(transition)
        assert page is not None

        # Variables that appear anywhere on this transition but are still free.
        needed: set[str] = set()
        for arc in page.arcs_of(transition):
            needed |= self._free(arc.expression_ast)
        needed |= self._free(transition.guard_ast)
        needed |= self._free(transition.time_ast)
        unbound = sorted(needed - set(bindings))

        if not unbound:
            self._check_and_emit(transition, bindings, remaining, opaque_arcs,
                                 marking, clock, results)
            return

        # Enumerate the remaining variables over their colour sets.
        for combination in itertools.product(*self._domains(transition, unbound)):
            if len(results) >= self.max_bindings:
                self.truncated = True
                return
            trial = dict(bindings)
            trial.update(zip(unbound, combination))
            self._check_and_emit(transition, trial, remaining, opaque_arcs,
                                 marking, clock, results)

    def _check_and_emit(self, transition: Transition, bindings: dict[str, Any],
                        remaining: dict[str, Multiset], opaque_arcs: Sequence[Arc],
                        marking: Marking, clock: int, results: list[BindingElement]) -> None:
        """Final validation: opaque input arcs, then the guard."""
        # Any arc we could not decompose is now fully evaluable.
        for arc in opaque_arcs:
            try:
                wanted = to_multiset(self._evaluate(arc.expression_ast, bindings))
            except CPNMLError:
                return
            key = self.net.marking_key(arc.place_id)
            pool = remaining.get(key, Multiset.empty())
            if not (wanted <= pool):
                return
            remaining = dict(remaining)
            remaining[key] = pool - wanted

        if transition.guard_ast is not None:
            try:
                guard = self._evaluate(transition.guard_ast, bindings)
            except CPNMLError:
                return
            if guard is not True:
                return

        element = BindingElement.create(transition, bindings)
        if element not in results:
            results.append(element)

    # -- evaluation helper ---------------------------------------------------
    def _evaluate(self, expression: Expr | None, bindings: dict[str, Any]) -> Any:
        if expression is None:
            return None
        environment = Environment(bindings, self.evaluator.globals)
        return self.evaluator.evaluate(expression, environment)
