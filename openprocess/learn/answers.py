"""Reading what a student typed: sets, pairs, traces, numbers.

Answers are typed the way they are written on paper, and read leniently:

==========================  =========================================
typed                       read as
==========================  =========================================
``a, b, c``                 the set {a, b, c}
``{a, b}``                  the set {a, b} (the braces are optional)
``{s1,s3}, {s2,s3}``        a set of two sets
``({a},{b,d}), ({b},{e})``  a set of two pairs (as in X_L and Y_L)
``<a, b, c>`` or ``⟨a,b⟩``  a sequence (a trace)
``∅`` or ``{}``             the empty set
==========================  =========================================

Names are compared without regard to case or spacing, and ``s_1``,
``s_{1}`` and ``s1`` are the same name (subscripts as typed in LaTeX).  The
same reading is applied to the expected answer, so a professor can write it
in any of these forms too.
"""

from __future__ import annotations

import re


class Sequence(tuple):
    """``<a, b, c>``: kept apart from a pair ``(a, b)``."""

    def __repr__(self) -> str:
        return "Sequence" + super().__repr__()


_SUBSCRIPT = re.compile(r"_\{([^{}]*)\}|_(\w)")
_LATEX = {"\\{": "{", "\\}": "}", "\\langle": "⟨", "\\rangle": "⟩", "\\emptyset": "∅",
          "\\varnothing": "∅", "\\;": " ", "\\,": " ", "\\ ": " "}
_OPEN = {"{": "}", "(": ")", "<": ">", "⟨": "⟩", "[": "]"}
_CLOSE = set(_OPEN.values())

#: An α-place as typed, ``p({a},{b,e})`` or ``p({a}, {b, e})``: one name, not a pair.
_ALPHA_PLACE = re.compile(r"\bp\s*\(\s*\{([^{}]*)\}\s*,\s*\{([^{}]*)\}\s*\)")


def place_atom(inputs, outputs) -> str:
    """The α-algorithm's place p(A, B) as one name, members sorted, with no
    character the set reader splits on: ``p〈a | b·e〉``.  Both the computed
    answer (``alpha.P_L``) and what a student types, ``p({a},{b,e})``, become
    this, so they compare equal whatever the order or spacing."""
    def side(items) -> str:
        return "·".join(sorted(name(str(i)) for i in items if str(i).strip()))
    return f"p〈{side(inputs)} | {side(outputs)}〉"


def _alpha_places(text: str) -> str:
    return _ALPHA_PLACE.sub(lambda m: place_atom(m.group(1).split(","), m.group(2).split(",")), text)


def name(text: str) -> str:
    """One name, as compared: ``" S_1 "`` and ``"s1"`` are the same."""
    text = _SUBSCRIPT.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text)
    return " ".join(text.replace("$", "").split()).casefold()


def _tokens(text: str) -> list[str]:
    # s_{1} first: its braces are not a set.
    text = _SUBSCRIPT.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text)
    for latex, plain in _LATEX.items():
        text = text.replace(latex, plain)
    text = _alpha_places(text.replace("$", ""))
    tokens, current = [], []
    for char in text:
        if char in _OPEN or char in _CLOSE or char in ",;":
            if "".join(current).strip():
                tokens.append("".join(current).strip())
            current = []
            tokens.append("," if char == ";" else char)
        else:
            current.append(char)
    if "".join(current).strip():
        tokens.append("".join(current).strip())
    return tokens


class AnswerSyntaxError(ValueError):
    pass


def _parse_items(tokens: list[str], position: int, closing: str | None):
    items, expecting_item = [], True
    while position < len(tokens):
        token = tokens[position]
        if token == closing:
            return items, position + 1
        if token in _CLOSE:
            raise AnswerSyntaxError(f"“{token}” does not close anything here")
        if token == ",":
            expecting_item = True
            position += 1
            continue
        if not expecting_item:
            raise AnswerSyntaxError("a comma is missing between two items")
        if token in _OPEN:
            inner, position = _parse_items(tokens, position + 1, _OPEN[token])
            if token == "{":
                item = frozenset(inner)
            elif token in ("<", "⟨"):
                item = Sequence(inner)
            elif token == "[":
                item = tuple(inner)                  # a multiset/list: order kept
            else:
                item = tuple(inner)
            items.append(item)
        elif token == "∅":
            items.append(frozenset())
            position += 1
        else:
            items.append(name(token))
            position += 1
        expecting_item = False
    if closing is not None:
        raise AnswerSyntaxError(f"“{closing}” is missing")
    return items, position


def parse(text: str) -> list:
    """The top-level items of an answer (each a name, set, pair or sequence)."""
    items, _ = _parse_items(_tokens(text), 0, None)
    return items


def readings(text: str) -> list:
    """The ways ``text`` can be read as a set: the items as a set, and, when it
    is one braced set, that set itself (``{a, b}`` and ``a, b`` agree, and
    ``{{s1}}`` is the same as ``{s1}`` written with outer braces)."""
    items = parse(text)
    result = [frozenset(items)]
    if len(items) == 1 and isinstance(items[0], frozenset):
        result.append(items[0])
    return result


def as_set(value) -> frozenset:
    """An expected value (from a compute or a parsed answer) as a frozenset."""
    if isinstance(value, str):
        options = readings(value)
        return options[-1] if len(options) > 1 else options[0]
    return frozenset(normalised(v) for v in value)


def normalised(value):
    """Names inside a computed value, compared as typed answers are."""
    if isinstance(value, str):
        return name(value)
    if isinstance(value, frozenset | set):
        return frozenset(normalised(v) for v in value)
    if isinstance(value, Sequence):
        return Sequence(normalised(v) for v in value)
    if isinstance(value, tuple | list):
        return tuple(normalised(v) for v in value)
    return value


def show(value) -> str:
    """A value written back in the notation of the course."""
    if isinstance(value, str):
        return value
    if isinstance(value, frozenset | set):
        if not value:
            return "∅"
        return "{" + ", ".join(sorted(show(v) for v in value)) + "}"
    if isinstance(value, Sequence):
        return "⟨" + ", ".join(show(v) for v in value) + "⟩"
    if isinstance(value, tuple | list):
        return "(" + ", ".join(show(v) for v in value) + ")"
    return str(value)


def show_set(value) -> str:
    """A set of items without its outer braces when they are themselves sets or
    pairs: ``{a}, {b}`` rather than ``{{a}, {b}}``."""
    if isinstance(value, frozenset | set) and value and all(
            isinstance(v, frozenset | tuple) for v in value):
        return ", ".join(sorted(show(v) for v in value))
    return show(value)


def number(text: str) -> float:
    """``0.75``, ``0,75``, ``3/4`` or ``75%``; also ``−1`` and ``∞``."""
    text = text.strip().replace(" ", "").replace("−", "-").replace("–", "-")
    if text.casefold() in ("∞", "inf", "infinity", "unbounded"):
        return float("inf")
    if text.endswith("%"):
        return float(text[:-1].replace(",", ".")) / 100
    if "/" in text:
        top, bottom = text.split("/", 1)
        return float(top.replace(",", ".")) / float(bottom.replace(",", "."))
    return float(text.replace(",", "."))


def trace(text: str) -> list[str]:
    """A firing sequence: ``register, send letter`` or ``⟨register, send letter⟩``."""
    items = parse(text)
    if len(items) == 1 and isinstance(items[0], Sequence | tuple):
        items = list(items[0])
    if not all(isinstance(item, str) for item in items):
        raise AnswerSyntaxError("write the steps as names separated by commas")
    return items
