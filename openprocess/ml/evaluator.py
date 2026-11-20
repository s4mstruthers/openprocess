"""The CPN ML evaluator: turns an AST plus an environment into a value.

Scope and lookup
----------------
Environments form a chain (:class:`Environment`), so a ``let`` or a function
call pushes a small frame rather than copying the whole global table.  Lookup
walks the chain outwards.  The outermost frame holds the standard basis from
:mod:`openprocess.ml.builtins` plus everything the model declares.

Functions
---------
User functions are :class:`Closure` objects capturing the environment in which
they were declared.  ``fun`` is made recursive by binding the function's own
name inside the closure's environment *before* the body ever runs, which is the
standard trick and avoids a separate ``letrec`` construct.

Identifier resolution
---------------------
CPN ML has no syntactic marker distinguishing a variable from a nullary
constructor: ``red`` could be either.  We resolve at lookup time -- an
identifier bound in the environment is whatever it is bound to, and an unbound
capitalised identifier is treated as a nullary constructor.  This matches how
declarations work in practice, because enumeration constants are injected into
the global environment when a colour set is compiled.

Errors
------
Anything the modeller can get wrong raises a subclass of
:class:`~openprocess.ml.errors.CPNMLError` carrying the source position, so the editor
can point at the failing token.  Pattern-match failure raises
:class:`~openprocess.ml.errors.MatchError` specifically, because the binder catches
that one and treats it as "this token does not fit", not as an error.
"""

from __future__ import annotations

import random
from typing import Any, Iterable, Mapping, Sequence

from .ast_nodes import (
    App, BinOp, CaseExpr, Coefficient, Decl, Delay, EmptyMultiset, Expr,
    FnExpr, FunDecl, IfExpr, LetExpr, ListExpr, Literal, PAs, PCons,
    PConstructor, PList, PLiteral, PRecord, PTuple, PVar, PWildcard, Pattern,
    RecordExpr, Selector, TupleExpr, UnOp, ValDecl, Var,
)
import sys

from .builtins import Builtin, PartialApplication, make_builtins

# The evaluator is recursive, and ML code recurses over lists: a function such
# as ``insertB2F`` walking a 120-passenger queue nests a few thousand Python
# frames.  Python's default limit (1000) made that fail with "maximum
# recursion depth exceeded", so raise it to a level real models need.
if sys.getrecursionlimit() < 20_000:
    sys.setrecursionlimit(20_000)
from .errors import EvalError, MatchError
from .multiset import Multiset, TimedTokens
from .values import Constructor, MLList, Record, format_value, sort_key


class Environment:
    """A lexical scope: a small dict plus a pointer to the enclosing scope."""

    __slots__ = ("bindings", "parent")

    def __init__(self, bindings: Mapping[str, Any] | None = None,
                 parent: "Environment | None" = None) -> None:
        self.bindings: dict[str, Any] = dict(bindings or {})
        self.parent = parent

    def lookup(self, name: str) -> Any:
        scope: Environment | None = self
        while scope is not None:
            if name in scope.bindings:
                return scope.bindings[name]
            scope = scope.parent
        raise KeyError(name)

    def contains(self, name: str) -> bool:
        scope: Environment | None = self
        while scope is not None:
            if name in scope.bindings:
                return True
            scope = scope.parent
        return False

    def define(self, name: str, value: Any) -> None:
        self.bindings[name] = value

    def child(self, bindings: Mapping[str, Any] | None = None) -> "Environment":
        return Environment(bindings, self)


class Closure:
    """A user-defined function value.

    ``rules`` is the list of ``(pattern, body)`` alternatives.  A curried
    ``fun f x y = ...`` is desugared by the evaluator into nested closures, so
    every closure here is unary, matching ML's actual semantics.
    """

    __slots__ = ("rules", "environment", "name")

    def __init__(self, rules: Sequence[tuple[Pattern, Expr]],
                 environment: Environment, name: str | None = None) -> None:
        self.rules = tuple(rules)
        self.environment = environment
        self.name = name

    def __repr__(self) -> str:
        return f"fn {self.name}" if self.name else "fn"


class CurriedFunction:
    """A multi-argument ``fun`` with several clauses, e.g.

    ``fun insert x [] = [x] | insert x (h::t) = ...``

    ML matches the *whole argument sequence* against each clause in turn, so
    the clause cannot be chosen from the first argument alone -- both clauses
    above accept any ``x``.  This value therefore collects arguments one at a
    time (it is still curried: ``insert x`` is a valid partial application),
    and only when all of them have arrived does it try the clauses.
    """

    __slots__ = ("clauses", "environment", "name", "arity", "arguments")

    def __init__(self, clauses, environment: "Environment", name: str | None,
                 arity: int, arguments: tuple = ()) -> None:
        self.clauses = clauses
        self.environment = environment
        self.name = name
        self.arity = arity
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"fn {self.name}" if self.name else "fn"


class ConstructorFunction:
    """The value of a union constructor that expects an argument.

    ``colset U = union Car:CARS + ...`` makes ``Car`` a function from a ``CARS``
    value to a ``U`` value.  Applying it produces a
    :class:`~openprocess.ml.values.Constructor`.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"fn {self.name}"


class Evaluator:
    """Evaluates CPN ML expressions.

    One evaluator instance serves a whole model.  It owns the global
    environment (standard basis + model declarations) and the random number
    generator, so seeding it makes a simulation reproducible.
    """

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        # ``make_builtins`` needs to call back into ``apply`` for higher-order
        # functions like List.map, hence passing the bound method.
        self.globals = Environment(make_builtins(self.rng, self.apply))
        # Set by the simulator so that inscriptions can call ``intTime()``.
        self._model_time: int = 0
        self.globals.define("intTime", Builtin("intTime", lambda _v: self._model_time))
        self.globals.define("time", Builtin("time", lambda _v: self._model_time))
        # The option type: `SOME x` / `NONE` (what Int.fromString returns).
        self.globals.define("SOME", ConstructorFunction("SOME"))
        self.globals.define("NONE", Constructor("NONE"))

    # -- integration points --------------------------------------------------
    def set_model_time(self, value: int) -> None:
        """Tell inscriptions what the simulator's clock currently reads."""
        self._model_time = value

    # =======================================================================
    # Expression evaluation
    # =======================================================================
    def evaluate(self, expression: Expr, environment: Environment | None = None) -> Any:
        """Evaluate ``expression`` in ``environment`` (globals if omitted)."""
        env = environment if environment is not None else self.globals

        if isinstance(expression, Literal):
            return expression.value

        if isinstance(expression, Var):
            return self._lookup_identifier(expression, env)

        if isinstance(expression, TupleExpr):
            return tuple(self.evaluate(item, env) for item in expression.items)

        if isinstance(expression, ListExpr):
            return MLList(self.evaluate(item, env) for item in expression.items)

        if isinstance(expression, RecordExpr):
            return Record(tuple((name, self.evaluate(value, env)) for name, value in expression.fields))

        if isinstance(expression, Selector):
            return Builtin(f"#{expression.field_name}", lambda v, f=expression.field_name: _project(v, f))

        if isinstance(expression, EmptyMultiset):
            return Multiset.empty()

        if isinstance(expression, Coefficient):
            return self._evaluate_coefficient(expression, env)

        if isinstance(expression, Delay):
            # `tokens @+ delay`: tokens that become available `delay` time
            # units after the transition fires (see evaluate_timed_arc).
            base = self.evaluate(expression.base, env)
            delay = _expect_time(self.evaluate(expression.delay, env), expression.position)
            if isinstance(base, TimedTokens):
                return base.delayed(delay)
            return TimedTokens([(to_multiset(base), delay, False)])

        if isinstance(expression, UnOp):
            return self._evaluate_unary(expression, env)

        if isinstance(expression, BinOp):
            return self._evaluate_binary(expression, env)

        if isinstance(expression, IfExpr):
            condition = self.evaluate(expression.condition, env)
            if not isinstance(condition, bool):
                raise EvalError(
                    f"'if' condition must be a boolean, got {format_value(condition)}",
                    expression.position,
                )
            branch = expression.then_branch if condition else expression.else_branch
            return self.evaluate(branch, env)

        if isinstance(expression, LetExpr):
            local = env.child()
            for declaration in expression.declarations:
                self.execute_declaration(declaration, local)
            return self.evaluate(expression.body, local)

        if isinstance(expression, CaseExpr):
            subject = self.evaluate(expression.scrutinee, env)
            for pattern, body in expression.rules:
                bindings: dict[str, Any] = {}
                if self.match(pattern, subject, bindings, env):
                    return self.evaluate(body, env.child(bindings))
            raise MatchError(
                f"no case alternative matches {format_value(subject)}", expression.position
            )

        if isinstance(expression, FnExpr):
            return Closure(expression.rules, env)

        if isinstance(expression, App):
            function = self.evaluate(expression.function, env)
            argument = self.evaluate(expression.argument, env)
            return self.apply(function, argument)

        raise AssertionError(f"unhandled expression node {type(expression).__name__}")

    def evaluate_arc(self, expression: Expr, environment: Environment,
                     clock: int = 0) -> tuple[Multiset, int]:
        """Evaluate an arc inscription to ``(tokens, timestamp)``.

        The result is coerced to a multiset, so a bare ``x`` means ``1`x``
        exactly as CPN Tools reads it.  The time stamp is that of the first
        timed term (``x @+ d`` gives ``clock + d``), or the clock itself;
        :meth:`evaluate_timed_arc` keeps the time of every term.
        """
        groups = self.evaluate_timed_arc(expression, environment, clock)
        tokens = Multiset.empty()
        for part, _stamp in groups:
            tokens = tokens + part
        return tokens, (groups[0][1] if groups else clock)

    def evaluate_timed_arc(self, expression: Expr, environment: Environment,
                           clock: Any = 0) -> list[tuple[Multiset, Any]]:
        """Evaluate an arc inscription to ``[(tokens, time stamp), ...]``.

        ``1`x@+5 +++ 1`y@+3`` gives two groups, stamped ``clock + 5`` and
        ``clock + 3``; an untimed inscription gives one group stamped
        ``clock``, meaning the tokens are available immediately.  Delays may
        be real (``normal(5.0, 1.0)``); model time is kept as a number of
        either kind, like CPN Tools' real-time setting.
        """
        value = self.evaluate(expression, environment)
        if isinstance(value, TimedTokens):
            return value.stamped(clock)
        return [(to_multiset(value), clock)]

    # -- identifier resolution ----------------------------------------------
    def _lookup_identifier(self, node: Var, environment: Environment) -> Any:
        try:
            return environment.lookup(node.name)
        except KeyError:
            pass
        # An unbound capitalised identifier is a nullary constructor.  This
        # keeps models loading even when a colour set was declared in a block
        # we could not fully compile.
        if node.name[:1].isupper():
            return Constructor(node.name)
        raise EvalError(f"unbound identifier '{node.name}'", node.position)

    # -- operators -----------------------------------------------------------
    def _evaluate_coefficient(self, node: Coefficient, environment: Environment) -> Multiset:
        count = self.evaluate(node.count, environment)
        if isinstance(count, bool) or not isinstance(count, int):
            raise EvalError(
                f"multiset coefficient must be an integer, got {format_value(count)}",
                node.position,
            )
        if count < 0:
            raise EvalError("multiset coefficient must not be negative", node.position)
        value = self.evaluate(node.value, environment)
        if isinstance(value, Multiset):
            # `2`(1`x ++ 1`y)` scales an existing multiset.
            return value * count
        if isinstance(value, TimedTokens):
            return value.scaled(count)
        return Multiset.singleton(value, count)

    def _evaluate_unary(self, node: UnOp, environment: Environment) -> Any:
        operand = self.evaluate(node.operand, environment)
        if node.operator == "~":
            if isinstance(operand, bool) or not isinstance(operand, (int, float)):
                raise EvalError(f"'~' expects a number, got {format_value(operand)}", node.position)
            return -operand
        if node.operator == "not":
            if not isinstance(operand, bool):
                raise EvalError(f"'not' expects a boolean, got {format_value(operand)}", node.position)
            return not operand
        raise AssertionError(f"unhandled unary operator {node.operator!r}")

    def _evaluate_binary(self, node: BinOp, environment: Environment) -> Any:
        operator = node.operator

        # Short-circuit operators must not evaluate the right side eagerly.
        if operator in ("andalso", "orelse"):
            left = self.evaluate(node.left, environment)
            if not isinstance(left, bool):
                raise EvalError(
                    f"'{operator}' expects booleans, got {format_value(left)}", node.position
                )
            if operator == "andalso" and not left:
                return False
            if operator == "orelse" and left:
                return True
            right = self.evaluate(node.right, environment)
            if not isinstance(right, bool):
                raise EvalError(
                    f"'{operator}' expects booleans, got {format_value(right)}", node.position
                )
            return right

        left = self.evaluate(node.left, environment)
        right = self.evaluate(node.right, environment)
        return self._apply_binary(operator, left, right, node.position)

    def _apply_binary(self, operator: str, left: Any, right: Any, position: int) -> Any:
        # -- equality and ordering ------------------------------------------
        if operator == "=":
            return left == right
        if operator == "<>":
            return left != right
        if operator in ("<", ">", "<=", ">="):
            if isinstance(left, Multiset) and isinstance(right, Multiset):
                # On multisets these are the inclusion orders, which is what a
                # guard like `m <= M(p)` means.
                return {"<": left < right, ">": left > right,
                        "<=": left <= right, ">=": left >= right}[operator]
            try:
                left_key, right_key = sort_key(left), sort_key(right)
            except AssertionError:
                raise EvalError(f"cannot compare {format_value(left)} and {format_value(right)}", position)
            return {"<": left_key < right_key, ">": left_key > right_key,
                    "<=": left_key <= right_key, ">=": left_key >= right_key}[operator]

        # -- timed multisets --------------------------------------------------
        if operator == "+++" or (operator == "++" and (isinstance(left, TimedTokens)
                                                       or isinstance(right, TimedTokens))):
            return _as_timed(left) + _as_timed(right)
        if operator == "---":
            # Removing timed tokens: like `--`, regardless of their times.
            return to_multiset(left) - to_multiset(right)

        # -- multiset algebra ------------------------------------------------
        if operator in ("++", "--"):
            # In CPN ML a multiset *is* a list ('a ms = 'a list), so `++` on
            # two lists is list concatenation and the result is one list
            # value: `lp ++ [p]` appends p to the queue lp.
            if operator == "++" and isinstance(left, MLList) and isinstance(right, MLList):
                return left.append(right)
            # Otherwise coerce bare values to singletons: writing `x ++ y` on
            # an arc is common shorthand for `1`x ++ 1`y`.
            left_ms, right_ms = to_multiset(left), to_multiset(right)
            return left_ms + right_ms if operator == "++" else left_ms - right_ms

        # -- lists -----------------------------------------------------------
        if operator == "::":
            if not isinstance(right, MLList):
                raise EvalError(f"'::' expects a list on the right, got {format_value(right)}", position)
            return right.cons(left)
        if operator == "@" and not isinstance(right, bool) and isinstance(right, (int, float)):
            # `1`x@5`: tokens with the time stamp 5 (timed initial markings).
            if isinstance(left, TimedTokens):
                return TimedTokens((tokens, right, True) for tokens, _t, _a in left.groups)
            return TimedTokens([(to_multiset(left), right, True)])
        if operator in ("@", "^^"):
            if isinstance(left, MLList) and isinstance(right, MLList):
                return left.append(right)
            if isinstance(left, str) and isinstance(right, str):
                # CPN Tools also uses ^^ for string concatenation in places.
                return left + right
            hint = (" (join timed tokens with +++, e.g. 1`x@0 +++ 1`y@5)"
                    if operator == "@" and isinstance(right, Multiset) else "")
            raise EvalError(f"'{operator}' expects two lists, or a time stamp after '@'{hint}",
                            position)

        # -- strings ----------------------------------------------------------
        if operator == "^":
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            raise EvalError(f"'^' expects two strings, got {format_value(left)} and {format_value(right)}", position)

        # -- arithmetic --------------------------------------------------------
        if operator in ("+", "-", "*", "/", "div", "mod"):
            return self._arithmetic(operator, left, right, position)

        raise AssertionError(f"unhandled binary operator {operator!r}")

    @staticmethod
    def _arithmetic(operator: str, left: Any, right: Any, position: int) -> Any:
        for operand in (left, right):
            if isinstance(operand, bool) or not isinstance(operand, (int, float)):
                raise EvalError(
                    f"'{operator}' expects numbers, got {format_value(left)} and {format_value(right)}",
                    position,
                )
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator == "/":
            if float(right) == 0.0:
                raise EvalError("division by zero", position)
            return float(left) / float(right)
        if right == 0:
            raise EvalError(f"'{operator}' by zero", position)
        if operator == "div":
            # ML's `div` truncates towards negative infinity, like Python's //.
            return left // right
        return left % right

    # -- application ---------------------------------------------------------
    def apply(self, function: Any, argument: Any) -> Any:
        """Apply a function value to one argument."""
        if isinstance(function, Closure):
            for pattern, body in function.rules:
                bindings: dict[str, Any] = {}
                if self.match(pattern, argument, bindings, function.environment):
                    return self.evaluate(body, function.environment.child(bindings))
            raise MatchError(
                f"no clause of {function.name or 'this function'} matches "
                f"{format_value(argument)}"
            )

        if isinstance(function, CurriedFunction):
            arguments = function.arguments + (argument,)
            if len(arguments) < function.arity:
                return CurriedFunction(function.clauses, function.environment, function.name,
                                       function.arity, arguments)
            for parameters, body in function.clauses:
                bindings: dict[str, Any] = {}
                if all(self.match(pattern, value, bindings, function.environment)
                       for pattern, value in zip(parameters, arguments)):
                    return self.evaluate(body, function.environment.child(bindings))
            shown = " ".join(format_value(a) for a in arguments)
            raise MatchError(f"no clause of {function.name or 'this function'} matches {shown}")

        if isinstance(function, ConstructorFunction):
            return Constructor(function.name, argument)

        if isinstance(function, Builtin):
            if function.arity == 1:
                return function.function(argument)
            return self.apply(PartialApplication(function, ()), argument)

        if isinstance(function, PartialApplication):
            arguments = function.arguments + (argument,)
            if len(arguments) == function.builtin.arity:
                return function.builtin.function(*arguments)
            return PartialApplication(function.builtin, arguments)

        raise EvalError(f"{format_value(function) if not callable(function) else function} is not a function")

    # =======================================================================
    # Pattern matching
    # =======================================================================
    def match(self, pattern: Pattern, value: Any, bindings: dict[str, Any],
              environment: Environment) -> bool:
        """Try to match ``value`` against ``pattern``.

        On success, fills ``bindings`` with the variables the pattern binds and
        returns ``True``.  On failure returns ``False`` -- ``bindings`` may have
        been partially written, so callers must not reuse a failed dictionary.

        A variable that is *already* in ``bindings`` acts as an equality test
        rather than a fresh binding.  This is what makes a non-linear arc
        inscription such as ``(x, x)`` behave correctly, and it is also how the
        binder enforces consistency of one variable across several input arcs.
        """
        if isinstance(pattern, PWildcard):
            return True

        if isinstance(pattern, PVar):
            existing = bindings.get(pattern.name, _MISSING)
            if existing is _MISSING:
                bindings[pattern.name] = value
                return True
            return bool(existing == value)

        if isinstance(pattern, PLiteral):
            return bool(pattern.value == value)

        if isinstance(pattern, PAs):
            if not self.match(pattern.pattern, value, bindings, environment):
                return False
            existing = bindings.get(pattern.name, _MISSING)
            if existing is _MISSING:
                bindings[pattern.name] = value
                return True
            return bool(existing == value)

        if isinstance(pattern, PTuple):
            if not isinstance(value, tuple) or len(value) != len(pattern.items):
                return False
            return all(
                self.match(sub, item, bindings, environment)
                for sub, item in zip(pattern.items, value)
            )

        if isinstance(pattern, PList):
            if not isinstance(value, MLList) or len(value) != len(pattern.items):
                return False
            return all(
                self.match(sub, item, bindings, environment)
                for sub, item in zip(pattern.items, value)
            )

        if isinstance(pattern, PCons):
            if not isinstance(value, MLList) or len(value) == 0:
                return False
            if not self.match(pattern.head, value[0], bindings, environment):
                return False
            return self.match(pattern.tail, MLList(value.items[1:]), bindings, environment)

        if isinstance(pattern, PRecord):
            if not isinstance(value, Record):
                return False
            if not pattern.open_ended and len(pattern.fields) != len(value.fields):
                return False
            for name, sub in pattern.fields:
                try:
                    field_value = value.get(name)
                except KeyError:
                    return False
                if not self.match(sub, field_value, bindings, environment):
                    return False
            return True

        if isinstance(pattern, PConstructor):
            return self._match_constructor(pattern, value, bindings, environment)

        raise AssertionError(f"unhandled pattern node {type(pattern).__name__}")

    def _match_constructor(self, pattern: PConstructor, value: Any,
                           bindings: dict[str, Any], environment: Environment) -> bool:
        """Match ``Tag`` or ``Tag p``.

        A bare capitalised identifier is genuinely ambiguous in CPN ML: it can
        be a nullary constructor *or* a variable that the modeller chose to
        capitalise.  We resolve it the only way that is always safe: if the
        name is bound in the environment to a constructor, treat it as a
        constant; otherwise treat it as a variable binding.
        """
        if pattern.argument is None:
            if environment.contains(pattern.name):
                bound = environment.lookup(pattern.name)
                if isinstance(bound, Constructor):
                    return bool(bound == value)
            if isinstance(value, Constructor) and not value.has_argument \
                    and value.name == pattern.name:
                return True
            # Fall back to variable semantics.
            existing = bindings.get(pattern.name, _MISSING)
            if existing is _MISSING:
                bindings[pattern.name] = value
                return True
            return bool(existing == value)

        if not isinstance(value, Constructor) or value.name != pattern.name:
            return False
        if not value.has_argument:
            return False
        return self.match(pattern.argument, value.argument, bindings, environment)

    # =======================================================================
    # Declarations
    # =======================================================================
    def execute_declaration(self, declaration: Decl, environment: Environment) -> None:
        """Run a ``val`` or ``fun`` declaration, updating ``environment``."""
        if isinstance(declaration, ValDecl):
            value = self.evaluate(declaration.expression, environment)
            bindings: dict[str, Any] = {}
            if not self.match(declaration.pattern, value, bindings, environment):
                raise MatchError(
                    f"value {format_value(value)} does not match the pattern in this "
                    "'val' declaration",
                    declaration.position,
                )
            environment.bindings.update(bindings)
            return

        if isinstance(declaration, FunDecl):
            self._define_function(declaration, environment)
            return

        raise AssertionError(f"unhandled declaration node {type(declaration).__name__}")

    def _define_function(self, declaration: FunDecl, environment: Environment) -> None:
        """Bind a ``fun`` declaration, desugaring currying and enabling recursion.

        ``fun f x y = body`` becomes a closure over ``x`` whose body is another
        closure over ``y``.  We build the outer closure first, bind the name,
        then let the closure's captured environment see that binding -- which
        is what makes ``f`` visible inside its own body.
        """
        arity = len(declaration.clauses[0][0])
        if any(len(parameters) != arity for parameters, _ in declaration.clauses):
            raise EvalError(
                f"all clauses of '{declaration.name}' must take the same number of arguments",
                declaration.position,
            )

        if arity == 1:
            rules = tuple((parameters[0], body) for parameters, body in declaration.clauses)
            closure = Closure(rules, environment, declaration.name)
        elif len(declaration.clauses) == 1:
            # A single clause: nested ``fn``s are exact and cheapest.
            parameters, body = declaration.clauses[0]
            closure = Closure(((parameters[0], _nest_fn(parameters[1:], body)),),
                              environment, declaration.name)
        else:
            # Several clauses: match all arguments together (see CurriedFunction).
            closure = CurriedFunction(tuple(declaration.clauses), environment,
                                      declaration.name, arity)

        # Bind before evaluation so the closure can call itself.
        environment.define(declaration.name, closure)

    # =======================================================================
    # Convenience
    # =======================================================================
    def run_declarations(self, declarations: Iterable[Decl],
                         environment: Environment | None = None) -> None:
        env = environment if environment is not None else self.globals
        for declaration in declarations:
            self.execute_declaration(declaration, env)


# Sentinel for "no such binding", so that ``None`` remains a legal bound value.
_MISSING = object()


def _nest_fn(parameters: Sequence[Pattern], body: Expr) -> Expr:
    """Build ``fn p1 => fn p2 => ... => body`` for a curried function."""
    result = body
    for pattern in reversed(parameters):
        result = FnExpr(((pattern, result),), getattr(pattern, "position", 0))
    return result


def _project(value: Any, field_name: str) -> Any:
    """Implement ``#field`` / ``#n`` projection for records and tuples."""
    if isinstance(value, Record):
        try:
            return value.get(field_name)
        except KeyError:
            raise EvalError(f"record has no field '{field_name}'")
    if isinstance(value, tuple) and field_name.isdigit():
        index = int(field_name) - 1  # ML tuple components are 1-based.
        if 0 <= index < len(value):
            return value[index]
        raise EvalError(f"tuple has no component #{field_name}")
    raise EvalError(f"cannot select #{field_name} from {format_value(value)}")


def expand_lists(tokens: Multiset, colour_set) -> Multiset:
    """CPN Tools' list-to-multiset coercion.

    When an initial marking or arc expression evaluates to a *list* but the
    place's colour set is not a list colour set, CPN Tools reads the list as
    a multiset: one token per element.  ``PLANE_INIT`` (a list of seats) on a
    ``SEAT`` place therefore means "one token per seat", not one list token.
    Tokens that already belong to the colour set are left alone.
    """
    if colour_set is None:
        return tokens
    from .values import MLList
    result = Multiset.empty()
    changed = False
    for token, count in tokens.items():
        if isinstance(token, MLList) and not colour_set.contains(token):
            for item in token.items:
                result = result + Multiset.singleton(item, count)
            changed = True
        else:
            result = result + Multiset.singleton(token, count)
    return result if changed else tokens


def to_multiset(value: Any) -> Multiset:
    """Coerce an arc-expression result to a multiset.

    CPN Tools treats a bare colour expression on an arc as one token of that
    colour, so ``x`` and ``1`x`` mean the same thing.  We implement that
    coercion here, in one place, rather than scattering it through the
    simulator.  Timed tokens lose their times (an input arc, for example,
    takes tokens regardless of their stamps).
    """
    if isinstance(value, Multiset):
        return value
    if isinstance(value, TimedTokens):
        return value.untimed()
    return Multiset.singleton(value, 1)


def _as_timed(value: Any) -> TimedTokens:
    """An operand of ``+++``: untimed tokens are available at once (``@+0``)."""
    if isinstance(value, TimedTokens):
        return value
    return TimedTokens([(to_multiset(value), 0, False)])


def _expect_time(value: Any, position: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"time delay must be a number, got {format_value(value)}", position)
    return value
