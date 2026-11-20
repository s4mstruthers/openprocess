"""The standard basis available to every inscription.

CPN ML inherits most of the Standard ML Basis Library.  Reimplementing all of
it is neither possible nor useful, so this module provides the subset that real
CPN models actually use, in three groups:

1. **Standard ML basis** -- arithmetic, strings, and the ``List`` structure.
2. **CPN'MS multiset functions** -- ``size``, ``cf``, ``ms_to_col`` and friends,
   which CPN Tools defines in its multiset structure.
3. **Random distributions** -- ``discrete``, ``uniform``, ``exponential``,
   ``normal``, ``poisson`` and so on.  These are what timed models use to draw
   delays, and they are the reason a CPN simulation is stochastic.

Calling convention
------------------
ML functions take exactly one argument.  A function that appears to take two
is either *tupled* (``min (a, b)`` receives one tuple) or *curried*
(``List.map f xs`` is ``(List.map f) xs``).  Both shapes are supported:
:class:`Builtin` with ``arity=1`` for tupled functions, and ``arity>1`` for
curried ones, where applying too few arguments yields a
:class:`PartialApplication` that waits for the rest.

Reproducibility
---------------
All randomness goes through a single :class:`random.Random` instance owned by
the evaluation environment, so seeding it makes a whole simulation replayable.
That is essential for debugging a model: without it, "it deadlocked once" is
not something you can investigate.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

from .errors import EvalError
from .multiset import Multiset
from .values import UNIT, Constructor, MLList, format_value


class Builtin:
    """A primitive function implemented in Python.

    ``arity`` is the number of *curried* arguments.  ``function`` receives them
    as a Python tuple once they have all arrived.
    """

    __slots__ = ("name", "function", "arity")

    def __init__(self, name: str, function: Callable[..., Any], arity: int = 1) -> None:
        self.name = name
        self.function = function
        self.arity = arity

    def __repr__(self) -> str:
        return f"fn {self.name}"


class PartialApplication:
    """A builtin that has received some, but not all, of its curried arguments."""

    __slots__ = ("builtin", "arguments")

    def __init__(self, builtin: Builtin, arguments: tuple[Any, ...]) -> None:
        self.builtin = builtin
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"fn {self.builtin.name} (partially applied)"


# ---------------------------------------------------------------------------
# Small helpers shared by many builtins
# ---------------------------------------------------------------------------
def _expect_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalError(f"{where} expects an integer, got {format_value(value)}")
    return value


def _expect_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"{where} expects a number, got {format_value(value)}")
    return float(value)


def _expect_list(value: Any, where: str) -> MLList:
    if not isinstance(value, MLList):
        raise EvalError(f"{where} expects a list, got {format_value(value)}")
    return value


def _expect_pair(value: Any, where: str) -> tuple[Any, Any]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise EvalError(f"{where} expects a pair, got {format_value(value)}")
    return value[0], value[1]


def _expect_multiset(value: Any, where: str) -> Multiset:
    if isinstance(value, Multiset):
        return value
    raise EvalError(f"{where} expects a multiset, got {format_value(value)}")


def _raise(message: str) -> Any:
    raise EvalError(message)


def make_builtins(rng: random.Random, apply_function: Callable[[Any, Any], Any]) -> dict[str, Any]:
    """Build the standard environment.

    ``apply_function`` is injected by the evaluator so that higher-order
    builtins such as ``List.map`` can call back into user-defined closures
    without this module importing the evaluator (which would be circular).
    """
    table: dict[str, Any] = {}

    def register(name: str, function: Callable[..., Any], arity: int = 1, *aliases: str) -> None:
        builtin = Builtin(name, function, arity)
        table[name] = builtin
        for alias in aliases:
            table[alias] = builtin

    # -- arithmetic ---------------------------------------------------------
    register("abs", lambda v: abs(_expect_number(v, "abs")) if isinstance(v, float) else abs(_expect_int(v, "abs")),
             1, "Int.abs", "Real.abs")
    register("min", lambda v: min(_expect_pair(v, "min")[0], _expect_pair(v, "min")[1]), 1, "Int.min")
    register("max", lambda v: max(_expect_pair(v, "max")[0], _expect_pair(v, "max")[1]), 1, "Int.max")
    register("real", lambda v: float(_expect_int(v, "real")), 1, "Real.fromInt")
    register("floor", lambda v: math.floor(_expect_number(v, "floor")), 1, "Real.floor")
    register("ceil", lambda v: math.ceil(_expect_number(v, "ceil")), 1, "Real.ceil")
    register("trunc", lambda v: math.trunc(_expect_number(v, "trunc")), 1, "Real.trunc")
    # ML's `round` is round-half-to-even, which is also Python's rule.
    register("round", lambda v: round(_expect_number(v, "round")), 1, "Real.round")
    register("Math.sqrt", lambda v: math.sqrt(_expect_number(v, "sqrt")), 1, "sqrt")
    register("Math.ln", lambda v: math.log(_expect_number(v, "ln")), 1, "ln")
    register("Math.exp", lambda v: math.exp(_expect_number(v, "exp")), 1, "exp")
    register("Math.pow", lambda v: math.pow(*(_expect_pair(v, "pow"))), 1, "pow")

    # -- conversions to and from strings ------------------------------------
    def _int_to_string(value: Any) -> str:
        number = _expect_int(value, "Int.toString")
        # ML prints negative integers with a leading tilde.
        return f"~{-number}" if number < 0 else str(number)

    register("Int.toString", _int_to_string)
    register("Real.toString", lambda v: (lambda x: f"~{-x!r}" if x < 0 else repr(x))(_expect_number(v, "Real.toString")))
    register("Bool.toString", lambda v: "true" if v else "false")
    register("str", lambda v: v if isinstance(v, str) else format_value(v))
    def _from_string(pattern: str, convert: Callable[[str], Any]) -> Callable[[Any], Any]:
        """``Int.fromString "12"`` is ``SOME 12``, and ``NONE`` for "abc".

        Like Standard ML: leading blanks are skipped, ``~`` or ``-`` makes the
        number negative, and whatever follows the number is ignored.
        """
        import re
        compiled = re.compile(r"\s*([~+-]?)(" + pattern + ")")

        def parse(value: Any) -> Any:
            if not isinstance(value, str):
                raise EvalError(f"fromString expects a string, got {format_value(value)}")
            found = compiled.match(value)
            if not found:
                return Constructor("NONE")
            sign = "-" if found.group(1) in ("~", "-") else ""
            return Constructor("SOME", convert(sign + found.group(2)))
        return parse

    register("Int.fromString", _from_string(r"\d+", int))
    register("Real.fromString",
             _from_string(r"\d+(?:\.\d+)?(?:[eE][~+-]?\d+)?|\.\d+",
                          lambda text: float(text.replace("~", "-"))))

    # -- options (SOME x / NONE) ----------------------------------------------
    def _is_some(value: Any) -> bool:
        if isinstance(value, Constructor) and value.name in ("SOME", "NONE"):
            return value.name == "SOME"
        raise EvalError(f"expected an option (SOME x or NONE), got {format_value(value)}")

    def _value_of(value: Any) -> Any:
        if not _is_some(value):
            raise EvalError("valOf applied to NONE (exception Option)")
        return value.argument

    register("isSome", _is_some, 1, "Option.isSome")
    register("valOf", _value_of, 1, "Option.valOf")
    register("getOpt", lambda v: (lambda o, d: o.argument if _is_some(o) else d)(*_expect_pair(v, "getOpt")),
             1, "Option.getOpt")

    # -- strings ------------------------------------------------------------
    register("size", lambda v: len(v) if isinstance(v, str) else _multiset_size(v), 1, "String.size")
    register("String.concat", lambda v: "".join(_expect_list(v, "String.concat")))
    register("String.str", lambda v: str(v))
    register("explode", lambda v: MLList(tuple(str(v))), 1, "String.explode")
    register("implode", lambda v: "".join(_expect_list(v, "implode")), 1, "String.implode")
    register("concat", lambda v: "".join(_expect_list(v, "concat")))

    def _string_sub(value: Any) -> str:
        text, index = _expect_pair(value, "String.sub")
        index = _expect_int(index, "String.sub")
        if not isinstance(text, str) or not 0 <= index < len(text):
            raise EvalError(f"String.sub: index {index} is outside the string (exception Subscript)")
        return text[index]

    register("String.sub", _string_sub)
    # Characters are one-letter strings in this implementation.
    register("ord", lambda v: ord(v) if isinstance(v, str) and len(v) == 1 else
             _raise(f"ord expects a character, got {format_value(v)}"), 1, "Char.ord")
    register("chr", lambda v: chr(_expect_int(v, "chr")), 1, "Char.chr")
    register(
        "substring",
        lambda v: str(v[0])[_expect_int(v[1], "substring"): _expect_int(v[1], "substring") + _expect_int(v[2], "substring")],
        1,
        "String.substring",
    )

    # -- lists --------------------------------------------------------------
    def _head(value: Any) -> Any:
        items = _expect_list(value, "hd")
        if not len(items):
            raise EvalError("hd applied to the empty list")
        return items[0]

    def _tail(value: Any) -> Any:
        items = _expect_list(value, "tl")
        if not len(items):
            raise EvalError("tl applied to the empty list")
        return MLList(items.items[1:])

    register("hd", _head, 1, "List.hd")
    register("tl", _tail, 1, "List.tl")
    register("null", lambda v: len(_expect_list(v, "null")) == 0, 1, "List.null")
    register("length", lambda v: len(_expect_list(v, "length")), 1, "List.length")
    register("rev", lambda v: MLList(reversed(_expect_list(v, "rev").items)), 1, "List.rev")
    register("List.last", lambda v: _expect_list(v, "List.last")[-1])
    register("List.concat",
             lambda v: MLList(item for inner in _expect_list(v, "List.concat")
                              for item in _expect_list(inner, "List.concat")))
    register(
        "List.find",
        lambda f, xs: next((Constructor("SOME", x) for x in _expect_list(xs, "List.find")
                            if apply_function(f, x)), Constructor("NONE")),
        2,
    )
    register(
        "List.partition",
        lambda f, xs: (lambda items: (MLList(x for x in items if apply_function(f, x)),
                                      MLList(x for x in items if not apply_function(f, x))))(
            _expect_list(xs, "List.partition").items),
        2,
    )
    register(
        "List.nth",
        lambda v: _expect_list(v[0], "List.nth")[_expect_int(v[1], "List.nth")],
    )
    register(
        "List.take",
        lambda v: MLList(_expect_list(v[0], "List.take").items[: _expect_int(v[1], "List.take")]),
    )
    register(
        "List.drop",
        lambda v: MLList(_expect_list(v[0], "List.drop").items[_expect_int(v[1], "List.drop"):]),
    )

    # Higher-order list functions.  Curried, hence ``arity=2``/``3``.
    register(
        "List.map",
        lambda f, xs: MLList(apply_function(f, x) for x in _expect_list(xs, "List.map")),
        2,
        "map",
    )
    register(
        "List.filter",
        lambda f, xs: MLList(x for x in _expect_list(xs, "List.filter") if apply_function(f, x)),
        2,
    )
    register(
        "List.exists",
        lambda f, xs: any(bool(apply_function(f, x)) for x in _expect_list(xs, "List.exists")),
        2,
    )
    register(
        "List.all",
        lambda f, xs: all(bool(apply_function(f, x)) for x in _expect_list(xs, "List.all")),
        2,
    )
    register(
        "List.app",
        lambda f, xs: ([apply_function(f, x) for x in _expect_list(xs, "List.app")], UNIT)[1],
        2,
        "app",
    )

    def _foldl(f: Any, initial: Any, xs: Any) -> Any:
        # ML's foldl passes (element, accumulator) as a pair.
        accumulator = initial
        for item in _expect_list(xs, "List.foldl"):
            accumulator = apply_function(f, (item, accumulator))
        return accumulator

    def _foldr(f: Any, initial: Any, xs: Any) -> Any:
        accumulator = initial
        for item in reversed(_expect_list(xs, "List.foldr").items):
            accumulator = apply_function(f, (item, accumulator))
        return accumulator

    register("List.foldl", _foldl, 3, "foldl")
    register("List.foldr", _foldr, 3, "foldr")
    register(
        "List.tabulate",
        lambda v: MLList(apply_function(v[1], i) for i in range(_expect_int(v[0], "List.tabulate"))),
    )

    # -- multisets (CPN'MS) --------------------------------------------------
    def _multiset_size(value: Any) -> int:
        return _expect_multiset(value, "size").size()

    def _ms_to_col(value: Any) -> Any:
        """The one colour of a multiset of size 1 (CPN Tools' definition)."""
        tokens = _expect_multiset(value, "ms_to_col")
        if tokens.size() != 1:
            raise EvalError(f"ms_to_col needs a multiset of size 1, got {tokens}")
        return next(tokens.expand())

    register("ms_to_col", _ms_to_col)
    register("ms_to_list", lambda v: MLList(_expect_multiset(v, "ms_to_list").expand()))
    register("list_to_ms", lambda v: Multiset.from_values(_expect_list(v, "list_to_ms")))
    register("cf", lambda v: _expect_multiset(v[1], "cf").count(v[0]))
    register("empty_ms", lambda _v: Multiset.empty())
    register("List.length_ms", _multiset_size)

    # -- random distributions ------------------------------------------------
    # Each returns a single draw.  ``discrete`` and ``uniform`` take an
    # inclusive pair; the others take their usual parameters.
    register("discrete", lambda v: rng.randint(*(int(x) for x in _expect_pair(v, "discrete"))))
    register("uniform", lambda v: rng.uniform(*(float(x) for x in _expect_pair(v, "uniform"))))
    register("exponential", lambda v: rng.expovariate(_expect_number(v, "exponential")))
    register("normal", lambda v: rng.gauss(*(float(x) for x in _expect_pair(v, "normal"))))
    register("erlang", lambda v: sum(rng.expovariate(float(v[1])) for _ in range(int(v[0]))))
    register("bernoulli", lambda v: 1 if rng.random() < _expect_number(v, "bernoulli") else 0, 1, "Bernoulli")
    register(
        "binomial",
        lambda v: sum(1 for _ in range(int(v[0])) if rng.random() < float(v[1])),
    )

    def _poisson(value: Any) -> int:
        """Knuth's algorithm -- adequate for the small means used in models."""
        mean = _expect_number(value, "poisson")
        limit = math.exp(-mean)
        count, product = 0, 1.0
        while True:
            product *= rng.random()
            if product <= limit:
                return count
            count += 1

    register("poisson", _poisson)

    # -- miscellaneous -------------------------------------------------------
    register("ignore", lambda _v: UNIT)
    register("not", lambda v: not v)

    return table
