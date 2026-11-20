"""Tokeniser for the CPN ML subset.

CPN ML is a dialect of Standard ML, so the lexical rules are ML's rules.  The
three that trip people up, and that this lexer gets right, are:

1. **Negation is written ``~``, not ``-``.**  ``~3`` is negative three;
   ``3 - 1`` is subtraction.  This removes the usual unary/binary minus
   ambiguity entirely, so we can tokenise ``~`` as its own operator.

2. **Comments are ``(* ... *)`` and they nest.**  ``(* a (* b *) c *)`` is one
   comment.  A non-nesting scanner would stop at the first ``*)`` and then
   choke on ``c *)``.

3. **Several operators share a prefix.**  ``+`` / ``++``, ``-`` / ``--`` /
   ``->``, ``@`` / ``@+``, ``<`` / ``<=`` / ``<>``, ``=`` / ``=>``, ``^`` /
   ``^^``.  We therefore try the longest spellings first.

CPN-specific additions to plain SML:

* ``` ` ``` (backquote) is the multiset coefficient operator: ``3`x`` means
  three copies of ``x``.
* ``++`` and ``--`` are multiset union and difference.
* ``@+`` introduces a time delay on an arc inscription (``e @+ 5``).
* ``empty`` is the empty multiset literal.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import LexError


# Words that are reserved and therefore never parsed as identifiers.
KEYWORDS = frozenset(
    {
        "if", "then", "else",
        "let", "in", "end",
        "val", "fun", "fn",
        "case", "of",
        "andalso", "orelse", "not",
        "div", "mod",
        "true", "false", "nil", "empty",
        "as", "op", "raise", "handle",
    }
)

# Multi-character operators, longest first.  Order matters: the scanner takes
# the first entry that matches, so ``++`` must precede ``+``.
_MULTI_CHAR_OPERATORS = (
    "@+",   # time delay (CPN extension)
    "+++",  # timed multiset union
    "---",  # timed multiset difference
    "++",   # multiset union
    "--",   # multiset difference
    "^^",   # list append (CPN Tools spelling)
    "::",   # list cons
    "<=", ">=", "<>",
    "=>",   # match arrow (fn / case)
    "->",   # type arrow
    "..",   # range, used in `int with 1..10`
)

_SINGLE_CHAR_OPERATORS = frozenset("+-*/=<>^@`~(),;:|[]{}#_.")

# Characters that may appear inside an identifier after the first character,
# in addition to letters and digits. A frozenset (not a string) on purpose:
# `"" in "_'"` is True in Python, which would make the scan loops run forever
# at end of input.
_IDENT_EXTRA_CHARS = frozenset("_'")


@dataclass(frozen=True)
class Token:
    """One lexical unit.

    ``kind`` is one of ``ID``, ``KEYWORD``, ``INT``, ``REAL``, ``STRING``,
    ``CHAR``, ``OP`` or ``EOF``.  ``value`` carries the already-decoded payload
    for literals (a Python ``int``/``float``/``str``) and the raw text for
    everything else.  ``position`` is the character offset of the token's first
    character, used to point at the right place in error messages.
    """

    kind: str
    value: object
    position: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.kind}({self.value!r})@{self.position}"


class Lexer:
    """Turns an inscription string into a list of :class:`Token`."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.index = 0

    # -- low-level cursor helpers -------------------------------------------
    def _peek(self, offset: int = 0) -> str:
        position = self.index + offset
        return self.source[position] if position < len(self.source) else ""

    def _at_end(self) -> bool:
        return self.index >= len(self.source)

    # -- skipping ------------------------------------------------------------
    def _skip_whitespace_and_comments(self) -> None:
        """Advance past any run of whitespace and (nesting) comments."""
        while not self._at_end():
            char = self._peek()
            if char.isspace():
                self.index += 1
                continue
            if char == "(" and self._peek(1) == "*":
                self._skip_comment()
                continue
            break

    def _skip_comment(self) -> None:
        start = self.index
        depth = 0
        while not self._at_end():
            if self._peek() == "(" and self._peek(1) == "*":
                depth += 1
                self.index += 2
            elif self._peek() == "*" and self._peek(1) == ")":
                depth -= 1
                self.index += 2
                if depth == 0:
                    return
            else:
                self.index += 1
        raise LexError("unterminated comment: '(*' without a matching '*)'", start)

    # -- literal scanners ----------------------------------------------------
    def _scan_number(self) -> Token:
        """Scan an integer or real literal, honouring ML's ``~`` for negation.

        ``~`` is only treated as part of the literal when it is immediately
        followed by a digit; otherwise it is a standalone negation operator
        applied to an expression, and the parser deals with it.
        """
        start = self.index
        if self._peek() == "~":
            self.index += 1
        while self._peek().isdigit():
            self.index += 1

        is_real = False
        # A '.' only starts a fractional part if a digit follows it, so that
        # `#1.x` style selections are not mis-scanned.
        if self._peek() == "." and self._peek(1).isdigit():
            is_real = True
            self.index += 1
            while self._peek().isdigit():
                self.index += 1
        # Exponent, e.g. 1.5E~3
        if self._peek() in ("e", "E") and (
            self._peek(1).isdigit() or (self._peek(1) == "~" and self._peek(2).isdigit())
        ):
            is_real = True
            self.index += 1
            if self._peek() == "~":
                self.index += 1
            while self._peek().isdigit():
                self.index += 1

        text = self.source[start:self.index]
        # ML writes negation as '~'; Python needs '-'.
        python_text = text.replace("~", "-")
        if is_real:
            return Token("REAL", float(python_text), start)
        return Token("INT", int(python_text), start)

    def _scan_string(self) -> Token:
        """Scan a double-quoted string with the ML escape sequences."""
        start = self.index
        self.index += 1  # consume the opening quote
        pieces: list[str] = []
        while True:
            if self._at_end():
                raise LexError("unterminated string literal", start)
            char = self._peek()
            if char == '"':
                self.index += 1
                return Token("STRING", "".join(pieces), start)
            if char == "\\":
                self.index += 1
                escape = self._peek()
                self.index += 1
                pieces.append(
                    {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}.get(escape, escape)
                )
                continue
            pieces.append(char)
            self.index += 1

    def _scan_identifier(self) -> Token:
        """Scan an alphanumeric identifier or keyword.

        ML identifiers may contain letters, digits, underscores and primes
        (``x'``), and may be qualified with a structure name (``Int.toString``),
        which we keep as one dotted identifier so the evaluator can look it up
        in the builtin table.
        """
        start = self.index
        while self._peek().isalnum() or self._peek() in _IDENT_EXTRA_CHARS:
            self.index += 1
        # Qualified name: swallow `.name` runs, but only when a letter follows,
        # so that `1.` or a record selector cannot be absorbed by accident.
        while self._peek() == "." and (self._peek(1).isalpha() or self._peek(1) == "_"):
            self.index += 1
            while self._peek().isalnum() or self._peek() in _IDENT_EXTRA_CHARS:
                self.index += 1
        text = self.source[start:self.index]
        if text in KEYWORDS:
            return Token("KEYWORD", text, start)
        return Token("ID", text, start)

    # -- the main loop -------------------------------------------------------
    def tokens(self) -> list[Token]:
        """Scan the whole input and return the token list, ending with EOF."""
        result: list[Token] = []
        while True:
            self._skip_whitespace_and_comments()
            if self._at_end():
                result.append(Token("EOF", None, self.index))
                return result

            start = self.index
            char = self._peek()

            # Character literal: #"a"
            if char == "#" and self._peek(1) == '"':
                self.index += 1
                string_token = self._scan_string()
                text = str(string_token.value)
                if len(text) != 1:
                    raise LexError("character literal must contain exactly one character", start)
                result.append(Token("CHAR", text, start))
                continue

            if char.isdigit() or (char == "~" and self._peek(1).isdigit()):
                result.append(self._scan_number())
                continue

            if char == '"':
                result.append(self._scan_string())
                continue

            if char.isalpha() or char == "_" and (self._peek(1).isalnum() or self._peek(1) == "_"):
                result.append(self._scan_identifier())
                continue

            matched = False
            for operator in _MULTI_CHAR_OPERATORS:
                if self.source.startswith(operator, self.index):
                    self.index += len(operator)
                    result.append(Token("OP", operator, start))
                    matched = True
                    break
            if matched:
                continue

            if char in _SINGLE_CHAR_OPERATORS:
                self.index += 1
                result.append(Token("OP", char, start))
                continue

            raise LexError(f"unexpected character {char!r}", start)


def tokenise(source: str) -> list[Token]:
    """Convenience wrapper: ``tokenise("1 ++ 2")``."""
    return Lexer(source).tokens()
