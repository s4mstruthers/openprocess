"""The notations of the course, read from what a student types and written back.

Every answer box beyond a set or a number has a notation the course already
uses on paper.  This module reads each one leniently and shows it back in
its canonical form, so a check can compare what was typed with what the
engine computed:

===========  ===========================================  ===================
block type   typed                                        read as
===========  ===========================================  ===================
marking      ``[p1, p4²]``, ``[p1, p4^2]``, ``p1 + 2p4``   a multiset of places
markings     ``[p1], [p2, p3], [p4]``                     a set of markings
tuple        four fields: P, T, F, m₀                     sets, pairs, a marking
ts           ``s0 -a-> s1`` lines (see transition_system)  a TransitionSystem
matrix       rows of numbers with row and column headers  a named matrix
cut          ``→ {a} {b, c, e} {d}``                      an operator and groups
log          ``[<a, b>^2, <a, c>]``                       a multiset of traces
tree         ``→(a, ×(∧(b, c), e), d)``                   a process tree
alignment    two rows, ``a b ≫ c`` over ``a ≫ d c``       moves (log, model)
replay       a table: p, c, m, r per trace                numbers per trace
ranking      ``m2 > m1 > m3`` or one name per line        an order of names
===========  ===========================================  ===================

Nothing here needs Qt: the same readers serve the app, the command line and
the tests.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import answers
from .answers import AnswerSyntaxError

SKIP = "≫"
#: What may stand for ≫ (the "no move") in a typed alignment.
SKIP_WORDS = {"≫", ">>", "»", "-", "–", "—", "_", "skip", "nomove", "no move"}


# ---------------------------------------------------------------------------
# Markings
# ---------------------------------------------------------------------------
_COUNT_BEFORE = re.compile(r"^(\d+)\s*[·*x×]?\s*(.+)$")
_COUNT_AFTER = re.compile(r"^(.+?)\s*(?:\^\s*(\d+)|([²³⁴⁵⁶⁷⁸⁹⁰¹]+)|:\s*(\d+))$")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _strip_brackets(text: str) -> str:
    text = text.strip()
    for latex, plain in (("\\{", "{"), ("\\}", "}"), ("\\emptyset", "∅"), ("$", "")):
        text = text.replace(latex, plain)
    text = text.strip()
    if len(text) >= 2 and text[0] in "[{(" and text[-1] in "]})":
        return text[1:-1].strip()
    return text


def marking(text: str) -> Counter:
    """A marking as typed: ``[p1, p4²]``, ``[p1, p4^2]``, ``[p1, 2p4]``,
    ``p1 + 2·p4``, ``{p1: 1, p4: 2}``, ``[]`` or ``∅``.  Names are compared
    like other answers (case and spaces do not matter)."""
    body = _strip_brackets(text)
    result: Counter = Counter()
    if body in ("", "∅", "-"):
        return result
    for item in re.split(r"[,+;]", body):
        item = item.strip()
        if not item:
            continue
        count = 1
        after = _COUNT_AFTER.match(item)
        if after and (after.group(2) or after.group(3) or after.group(4)):
            item = after.group(1)
            count = int((after.group(2) or after.group(4) or after.group(3).translate(_SUPERSCRIPTS)))
        else:
            before = _COUNT_BEFORE.match(item)
            if before:
                count, item = int(before.group(1)), before.group(2)
        name = answers.name(item)
        if not name:
            raise AnswerSyntaxError(f"“{item}” is not a place")
        if count:
            result[name] += count
    return result


def show_marking(counts) -> str:
    """``[p1, p4^2]`` (the textbook's notation); ``[]`` when empty."""
    parts = []
    for name in sorted(counts, key=answers.name if all(isinstance(k, str) for k in counts) else str):
        n = counts[name]
        if n:
            parts.append(f"{name}^{n}" if n != 1 else str(name))
    return "[" + ", ".join(parts) + "]"


def marking_key(counts) -> frozenset:
    """A marking as something hashable, for sets of markings."""
    return frozenset((answers.name(k), v) for k, v in counts.items() if v)


def markings(text: str) -> frozenset:
    """A set of markings: ``[p1], [p2, p3], [p4]`` (also inside ``{ }``)."""
    text = text.strip()
    for latex, plain in (("\\{", "{"), ("\\}", "}"), ("$", "")):
        text = text.replace(latex, plain)
    if text.startswith("{") and text.endswith("}") and "[" in text:
        text = text[1:-1]
    found = re.findall(r"\[[^\[\]]*\]", text)
    if not found:
        if text.strip() in ("", "∅", "{}"):
            return frozenset()
        raise AnswerSyntaxError("write each marking in square brackets, like [p1, p2^2]")
    return frozenset(marking_key(marking(m)) for m in found)


def show_markings(keys) -> str:
    shown = sorted(show_marking(dict(k)) for k in keys)
    return ", ".join(shown) if shown else "∅"


# ---------------------------------------------------------------------------
# Cuts and process trees
# ---------------------------------------------------------------------------
OPERATORS = {"→": "→", "->": "→", "seq": "→", "sequence": "→", "x": "×", "×": "×", "xor": "×",
             "choice": "×", "exclusive": "×", "^": "∧", "∧": "∧", "and": "∧", "parallel": "∧",
             "par": "∧", "concurrent": "∧", "↺": "↺", "loop": "↺", "*": "↺", "redo": "↺",
             # LaTeX, as the guide promises it does not matter
             "\\rightarrow": "→", "\\to": "→", "\\times": "×", "\\wedge": "∧", "\\land": "∧",
             "\\circlearrowleft": "↺", "\\curvearrowleft": "↺", "\\circlearrowright": "↺"}


def _operator(token: str) -> str:
    key = token.strip().casefold()
    if key in OPERATORS:
        return OPERATORS[key]
    raise AnswerSyntaxError(f"“{token}” is not an operator (use →, ×, ∧ or ↺, or write "
                            "sequence, xor, and, loop)")


def cut(text: str) -> tuple[str, tuple[frozenset, ...]]:
    """A cut as typed: ``→ {a} {b, c, e} {d}``, ``sequence: {a}, {b,c,e}, {d}`` or
    ``→({a}, {b, c, e}, {d})``: the operator and its groups, in order."""
    text = text.strip().replace("\\{", "{").replace("\\}", "}").replace("$", "")
    match = re.match(r"^\s*([^\s{(:]+)\s*[:(]?\s*(.*?)\)?\s*$", text, re.S)
    if not match:
        raise AnswerSyntaxError("write the operator first, then the groups in braces")
    operator = _operator(match.group(1))
    groups = re.findall(r"\{[^{}]*\}", match.group(2))
    if not groups:
        raise AnswerSyntaxError("write each group of the cut in braces, like {b, c}")
    parsed = tuple(frozenset(answers.name(n) for n in _strip_brackets(g).split(",") if n.strip())
                   for g in groups)
    if any(not g for g in parsed):
        raise AnswerSyntaxError("a group of the cut is empty")
    return operator, parsed


def show_cut(value) -> str:
    operator, groups = value
    return operator + " " + " ".join("{" + ", ".join(sorted(g)) + "}" for g in groups)


@dataclass
class Tree:
    """A process tree as typed (compared with the engine's, order of the
    children of × and ∧ ignored)."""
    operator: str | None = None
    children: list["Tree"] = field(default_factory=list)
    label: str | None = None

    def key(self):
        if self.operator is None:
            return ("leaf", self.label)
        inner = [c.key() for c in self.children]
        if self.operator in ("×", "∧"):
            inner = sorted(inner, key=repr)
        return (self.operator, tuple(inner))

    def __str__(self) -> str:
        if self.operator is None:
            return "τ" if self.label is None else self.label
        return f"{self.operator}(" + ", ".join(map(str, self.children)) + ")"


_TAU = {"τ", "tau", "t", "silent", "skip"}


def tree(text: str) -> Tree:
    """``→(a, ×(∧(b, c), e), d)``; also ``seq(a, xor(and(b, c), e), d)``."""
    text = text.strip().replace("$", "").replace("\\tau", "τ")
    tokens = [t for t in re.findall(r"[(),]|[^(),\s][^(),]*", text)]
    tokens = [t.strip() for t in tokens if t.strip()]
    position = 0

    def parse() -> Tree:
        nonlocal position
        if position >= len(tokens):
            raise AnswerSyntaxError("the tree ends too soon")
        token = tokens[position]
        position += 1
        if position < len(tokens) and tokens[position] == "(":
            operator = _operator(token)
            position += 1
            children = []
            while True:
                children.append(parse())
                if position >= len(tokens):
                    raise AnswerSyntaxError("a “)” is missing")
                if tokens[position] == ",":
                    position += 1
                    continue
                if tokens[position] == ")":
                    position += 1
                    break
                raise AnswerSyntaxError("a comma is missing between two children")
            return Tree(operator, children)
        if token in "(),":
            raise AnswerSyntaxError(f"unexpected “{token}”")
        name = answers.name(token)
        return Tree(label=None if name in _TAU else name)

    result = parse()
    if position < len(tokens):
        raise AnswerSyntaxError(f"unexpected “{tokens[position]}” after the tree")
    return result


def tree_from_engine(node) -> Tree:
    """A :class:`openprocess.mining.processtree.ProcessTree` as a :class:`Tree`."""
    if node.is_leaf:
        return Tree(label=None if node.label is None else answers.name(node.label))
    return Tree(node.operator.value, [tree_from_engine(c) for c in node.children])


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def log(text: str) -> Counter:
    """A log in the course's notation, names normalised: ``[<a,b>^2, <a,c>]``."""
    from ..mining.log import parse_simple_log
    try:
        parsed = parse_simple_log(text)
    except ValueError as error:
        raise AnswerSyntaxError(str(error)) from None
    return Counter({tuple(answers.name(a) for a in trace): n for trace, n in parsed.items() if n})


def show_log(counts) -> str:
    from ..mining.log import format_simple_log
    return format_simple_log(Counter(counts))


# ---------------------------------------------------------------------------
# Matrices
# ---------------------------------------------------------------------------
@dataclass
class Matrix:
    """Rows and columns by name, with a value in every cell."""
    rows: list[str]
    columns: list[str]
    cells: dict[tuple[str, str], float]

    def get(self, row: str, column: str):
        return self.cells.get((answers.name(row), answers.name(column)))

    def normalised(self) -> "Matrix":
        return Matrix([answers.name(r) for r in self.rows], [answers.name(c) for c in self.columns],
                      {(answers.name(r), answers.name(c)): v for (r, c), v in self.cells.items()})


def _number(token: str) -> float:
    token = token.strip().replace("−", "-").replace("–", "-")
    if token in ("", ".", "·"):
        return 0.0
    return answers.number(token)


def matrix(text: str) -> Matrix:
    """A matrix typed as rows of numbers with headers::

        .    a   b   c
        p1  -1   1   0
        p2   0  -1   1

    The first line names the columns (its first cell may be empty or a dot);
    each other line starts with its row's name.  Cells are separated by
    spaces, tabs, commas or ``|`` (a Markdown table works too)."""
    lines = [ln.strip().strip("|") for ln in text.strip().splitlines()]
    lines = [ln for ln in lines if ln.strip() and not re.fullmatch(r"[\s|:\-]+", ln)]
    if len(lines) < 2:
        raise AnswerSyntaxError("write the column names on the first line and one row per line")

    def split(line: str) -> list[str]:
        if "|" in line:
            return [c.strip() for c in line.split("|")]
        if "\t" in line:
            return [c.strip() for c in line.split("\t")]
        if "," in line:
            return [c.strip() for c in line.split(",")]
        return line.split()

    header = split(lines[0])
    if header and header[0] in ("", ".", "·", "-", "x", "\\"):
        header = header[1:]
    columns = [answers.name(c) for c in header]
    rows, cells = [], {}
    for line in lines[1:]:
        parts = split(line)
        if len(parts) == len(columns) + 1:
            name, values = answers.name(parts[0]), parts[1:]
        elif len(parts) == len(columns):
            name, values = f"row {len(rows) + 1}", parts
        else:
            raise AnswerSyntaxError(f"the row “{line}” has {len(parts) - 1} values for "
                                    f"{len(columns)} columns")
        rows.append(name)
        for column, value in zip(columns, values):
            try:
                cells[(name, column)] = _number(value)
            except (ValueError, ZeroDivisionError):
                raise AnswerSyntaxError(f"“{value}” in row {name} is not a number") from None
    return Matrix(rows, columns, cells)


def show_matrix(value: Matrix) -> str:
    rows, columns = value.rows, value.columns
    width = max([len(str(c)) for c in columns] + [len(str(r)) for r in rows] + [3])

    def fmt(x) -> str:
        return f"{x:g}" if isinstance(x, float) else str(x)
    lines = [" " * (width + 1) + " ".join(str(c).rjust(width) for c in columns)]
    for row in rows:
        lines.append(str(row).ljust(width) + " " +
                     " ".join(fmt(value.cells.get((row, c), 0)).rjust(width) for c in columns))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alignments and replay tables
# ---------------------------------------------------------------------------
def alignment(text: str) -> tuple[list[str], list[str]]:
    """Two rows of moves: the log row over the model row.  Cells are
    separated by spaces, ``|`` or commas; ``≫`` (also ``>>`` or ``-``) is no
    move.  Returns ``(log moves, model moves)`` with names normalised and
    ``≫`` for no move."""
    text = text.replace("$", "").replace("\\gg", "≫")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()
             and not re.fullmatch(r"[\s|:\-]+", ln)]
    if len(lines) != 2:
        raise AnswerSyntaxError("write the alignment as two lines: the log moves, then the "
                                "model moves")

    def cells(line: str) -> list[str]:
        line = line.strip().strip("|")
        parts = [c.strip() for c in re.split(r"\s*\|\s*|,", line)] if ("|" in line or "," in line) \
            else line.split()
        parts = [p for p in parts if p != ""]
        # A row label ("log:", "model |", "L:") is dropped; a single letter only
        # when a colon or bar marks it as a label, since l and m are activities too.
        if parts and (answers.name(parts[0]).rstrip(":") in ("log", "model", "trace", "net")
                      or (answers.name(parts[0]).rstrip(":") in ("l", "m")
                          and re.match(r"^\s*\|?\s*[lmLM]\s*[:|]", line))):
            parts = parts[1:]
        return [SKIP if p.casefold() in SKIP_WORDS else answers.name(p) for p in parts]
    top, bottom = cells(lines[0]), cells(lines[1])
    if len(top) != len(bottom):
        raise AnswerSyntaxError(f"the rows have different lengths ({len(top)} and {len(bottom)} "
                                "moves): write ≫ where there is no move")
    if any(a == SKIP and b == SKIP for a, b in zip(top, bottom)):
        raise AnswerSyntaxError("a column with ≫ in both rows is not a move")
    return top, bottom


def show_alignment(moves) -> str:
    """Two aligned rows from ``(log, model)`` pairs."""
    pairs = list(moves)
    width = [max(len(str(a)), len(str(b)), 1) for a, b in pairs]
    top = "  ".join(str(a).ljust(w) for (a, _), w in zip(pairs, width))
    bottom = "  ".join(str(b).ljust(w) for (_, b), w in zip(pairs, width))
    return top.rstrip() + "\n" + bottom.rstrip()


REPLAY_COLUMNS = ("p", "c", "m", "r")


def replay_table(value) -> dict[str, dict[str, float]]:
    """A replay table as the editor stores it: ``{trace text: {p, c, m, r}}``
    with the trace keys normalised.  A string is read as lines of
    ``<a, b, c>: p c m r`` (or ``<a, b, c> | p | c | m | r``)."""
    if isinstance(value, dict):
        result = {}
        for key, row in value.items():
            if not isinstance(row, dict):
                continue
            trace = tuple(answers.trace(str(key)))
            result[trace] = {}
            for column in REPLAY_COLUMNS:
                cell = row.get(column, "")
                if cell not in ("", None):
                    try:
                        result[trace][column] = answers.number(str(cell))
                    except (ValueError, ZeroDivisionError):
                        raise AnswerSyntaxError(f"“{cell}” is not a number") from None
        return result
    result = {}
    for line in str(value).strip().splitlines():
        if not line.strip():
            continue
        match = re.match(r"^\s*([<⟨].*?[>⟩])\s*[:|]?\s*(.*)$", line)
        if not match:
            raise AnswerSyntaxError(f"start each line with the trace in ⟨ ⟩: “{line}”")
        numbers = [n for n in re.split(r"[\s|,]+", match.group(2).strip()) if n]
        trace = tuple(answers.trace(match.group(1)))
        result[trace] = {}
        for column, cell in zip(REPLAY_COLUMNS, numbers):
            result[trace][column] = answers.number(cell)
    return result


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
def ranking(value) -> list[str]:
    """An order of names, best first: a list, ``m2 > m1 > m3``, ``m2, m1, m3``
    or one name per line (``1. m2``)."""
    if isinstance(value, list):
        names = [str(v) for v in value]
    else:
        text = str(value).strip()
        if ">" in text:
            names = text.split(">")
        elif "\n" in text:
            names = text.splitlines()
        else:
            names = text.split(",")
        names = [re.sub(r"^\s*\d+[.)]\s*", "", n) for n in names]
    names = [answers.name(n.replace(".pnml", "")) for n in names if n.strip()]
    if len(set(names)) != len(names):
        raise AnswerSyntaxError("a name appears twice in the ranking")
    return names


# ---------------------------------------------------------------------------
# The tuple (P, T, F, m0)
# ---------------------------------------------------------------------------
TUPLE_FIELDS = ("P", "T", "F", "m0")


def net_tuple(value) -> dict:
    """The four parts of ``(P, T, F, m0)`` as the editor stores them
    (``{"P": "...", "T": "...", "F": "...", "m0": "..."}``), read: ``P`` and
    ``T`` as sets of names, ``F`` as a set of pairs, ``m0`` as a marking."""
    if isinstance(value, str):
        value = tuple_text(value)
    parts = {k: (value.get(k) or "") for k in TUPLE_FIELDS}
    read = {}
    read["P"] = answers.as_set(parts["P"]) if parts["P"].strip() else frozenset()
    read["T"] = answers.as_set(parts["T"]) if parts["T"].strip() else frozenset()
    arcs = answers.as_set(parts["F"]) if parts["F"].strip() else frozenset()
    pairs = set()
    for item in arcs:
        if isinstance(item, tuple) and len(item) == 2 and all(isinstance(x, str) for x in item):
            pairs.add(item)
        else:
            raise AnswerSyntaxError("write F as pairs, like (p1, a), (a, p2)")
    read["F"] = frozenset(pairs)
    read["m0"] = marking_key(marking(parts["m0"])) if parts["m0"].strip() else frozenset()
    return read


def tuple_text(text: str) -> dict:
    """``P = {p1, p2}; T = {a, b}; F = {(p1, a), …}; m0 = [p1]`` on one or more lines."""
    found = {}
    for key in TUPLE_FIELDS:
        pattern = rf"(?:^|[;\n])\s*{re.escape(key)}\s*[=:]\s*(.*?)(?=(?:[;\n]\s*(?:P|T|F|m0)\s*[=:])|$)"
        match = re.search(pattern, text.replace("m_0", "m0").replace("M0", "m0"), re.S | re.I)
        found[key] = match.group(1).strip() if match else ""
    return found
