"""Recursive-descent parser for the CPN ML subset.

Grammar and precedence
----------------------
The infix precedence table follows Standard ML, with the two CPN additions
(``++``/``--`` for multisets and the backquote coefficient) slotted in where
CPN Tools puts them.  Lowest binding power first:

    ==== ======================== ==============
    Lvl  Operators                Associativity
    ==== ======================== ==============
    1    ``orelse``               left
    2    ``andalso``              left
    3    ``= <> < > <= >=``       left
    4    ``+++ ---`` (timed)      left
    5    ``@+`` (time delay)      non-associative
    6    ``:: @ ^^``              right
    7    ``+ - ^ ++ --``          left
    8    ``* / div mod``          left
    9    ``~``  ``not`` (prefix)  --
    10   `` ` `` (coefficient)    non-associative
    11   application ``f x``      left
    12   atoms                    --
    ==== ======================== ==============

The timed levels follow CPN Tools' timed multisets:
``1`x@+5 +++ 1`y@+3`` delays two tokens differently, ``(1`x ++ 1`y)@+5``
and ``1`x ++ 1`y @+ 5`` delay both, and ``1`3@5 +++ 1`4@0`` gives an
initial marking with time stamps (``@`` with a number on the right is a time
stamp; between two lists it is list append).

Two consequences are worth internalising, because they explain how ordinary
CPN inscriptions parse:

* ``2`x ++ 3`y`` groups as ``(2`x) ++ (3`y)`` -- the coefficient binds tighter
  than multiset union, so the familiar multiset literal works without brackets.
* ``1`f(a)`` groups as ``1`(f(a))`` -- application binds tighter still, so a
  computed token value needs no brackets either.

Everything else is plain ML.  Where ML is ambiguous (a ``case`` nested inside
another ``case``) we resolve greedily, as every ML compiler does, meaning the
inner ``case`` swallows following ``|`` rules and you must parenthesise to get
the other reading.

Entry points
------------
``parse_expression``
    A general expression -- guards, initial markings, ``let`` bodies.
``parse_arc_expression``
    Same, but additionally accepts a trailing ``@+ delay`` (only meaningful on
    an arc, hence the separate entry point).
``parse_declarations``
    A sequence of ``val`` / ``fun`` declarations, as found in a model's
    declaration block.
``expression_to_pattern``
    Reinterprets an already-parsed expression as a pattern.  Input arc
    inscriptions are written as expressions but *used* as patterns during
    binding, and this is the bridge between the two.
"""

from __future__ import annotations

from typing import Callable, Sequence

from .ast_nodes import (
    App, BinOp, CaseExpr, Coefficient, Decl, Delay, EmptyMultiset, Expr,
    FnExpr, FunDecl, IfExpr, LetExpr, ListExpr, Literal, PAs, PCons,
    PConstructor, PList, PLiteral, PRecord, PTuple, PVar, PWildcard, Pattern,
    RecordExpr, Selector, TupleExpr, UnOp, ValDecl, Var,
)
from .errors import ParseError
from .lexer import Token, tokenise
from .values import UNIT

# Binary operator levels.  Each entry maps a level number to the set of
# operator spellings at that level; ``_RIGHT_ASSOCIATIVE`` lists the levels that
# fold to the right instead of the left.
_BINARY_LEVELS: dict[int, frozenset[str]] = {
    1: frozenset({"orelse"}),
    2: frozenset({"andalso"}),
    3: frozenset({"=", "<>", "<", ">", "<=", ">="}),
    4: frozenset({"+++", "---"}),
    5: frozenset({"@+"}),
    6: frozenset({"::", "@", "^^"}),
    7: frozenset({"+", "-", "^", "++", "--"}),
    8: frozenset({"*", "/", "div", "mod"}),
}
_RIGHT_ASSOCIATIVE = frozenset({6})
_DELAY_LEVEL = 5
_LOWEST_LEVEL = 1
_HIGHEST_BINARY_LEVEL = 8

# Tokens that can begin an atom.  The application parser uses this to decide
# whether the next token continues an application (``f x``) or ends it.
_ATOM_START_KEYWORDS = frozenset({"true", "false", "nil", "empty", "op"})

# Infix operators that ``op`` turns into ordinary two-argument functions:
# ``op +`` is ``fn (a, b) => a + b``, as in Standard ML.
_OP_SECTIONS = frozenset(
    spelling for level in (3, 4, 6, 7, 8) for spelling in _BINARY_LEVELS[level]
)
_ATOM_START_OPERATORS = frozenset({"(", "[", "{", "#"})


class Parser:
    """A cursor over a token list plus one method per grammar production."""

    def __init__(self, tokens: Sequence[Token], source: str = "") -> None:
        self.tokens = list(tokens)
        self.index = 0
        self.source = source

    # -- cursor helpers ------------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        position = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[position]

    def next_token(self) -> Token:
        token = self.peek()
        self.index += 1
        return token

    def at_operator(self, *spellings: str) -> bool:
        token = self.peek()
        return token.kind == "OP" and token.value in spellings

    def at_keyword(self, *words: str) -> bool:
        token = self.peek()
        return token.kind == "KEYWORD" and token.value in words

    def at_end(self) -> bool:
        return self.peek().kind == "EOF"

    def expect_operator(self, spelling: str) -> Token:
        if not self.at_operator(spelling):
            raise ParseError(
                f"expected {spelling!r} but found {self._describe(self.peek())}",
                self.peek().position,
            )
        return self.next_token()

    def expect_keyword(self, word: str) -> Token:
        if not self.at_keyword(word):
            raise ParseError(
                f"expected {word!r} but found {self._describe(self.peek())}",
                self.peek().position,
            )
        return self.next_token()

    def expect_identifier(self) -> str:
        if self.peek().kind != "ID":
            raise ParseError(
                f"expected an identifier but found {self._describe(self.peek())}",
                self.peek().position,
            )
        return str(self.next_token().value)

    @staticmethod
    def _describe(token: Token) -> str:
        if token.kind == "EOF":
            return "end of input"
        return f"{token.value!r}"

    # =======================================================================
    # Expressions
    # =======================================================================
    def parse_expression(self) -> Expr:
        """Top of the expression grammar."""
        token = self.peek()

        if token.kind == "KEYWORD":
            if token.value == "if":
                return self._parse_if()
            if token.value == "let":
                return self._parse_let()
            if token.value == "case":
                return self._parse_case()
            if token.value == "fn":
                return self._parse_fn()
            if token.value == "raise":
                # `raise Exn` -- we parse and discard the exception name so that
                # models using it load; evaluating one is a runtime error.
                start = self.next_token().position
                self.parse_expression()
                return App(Var("raise", start), Literal(UNIT, start), start)

        return self._parse_binary(_LOWEST_LEVEL)

    def _parse_if(self) -> Expr:
        start = self.expect_keyword("if").position
        condition = self.parse_expression()
        self.expect_keyword("then")
        then_branch = self.parse_expression()
        self.expect_keyword("else")
        else_branch = self.parse_expression()
        return IfExpr(condition, then_branch, else_branch, start)

    def _parse_let(self) -> Expr:
        start = self.expect_keyword("let").position
        declarations: list[Decl] = []
        while not self.at_keyword("in"):
            if self.at_end():
                raise ParseError("unterminated 'let': expected 'in'", start)
            declarations.append(self.parse_declaration())
            # Semicolons between declarations are optional in ML.
            while self.at_operator(";"):
                self.next_token()
        self.expect_keyword("in")
        body = self.parse_expression()
        self.expect_keyword("end")
        return LetExpr(tuple(declarations), body, start)

    def _parse_case(self) -> Expr:
        start = self.expect_keyword("case").position
        scrutinee = self.parse_expression()
        self.expect_keyword("of")
        return CaseExpr(scrutinee, self._parse_match_rules(), start)

    def _parse_fn(self) -> Expr:
        start = self.expect_keyword("fn").position
        return FnExpr(self._parse_match_rules(), start)

    def _parse_match_rules(self) -> tuple[tuple[Pattern, Expr], ...]:
        """Parse ``p => e | p => e | ...`` -- shared by ``fn`` and ``case``."""
        rules: list[tuple[Pattern, Expr]] = []
        while True:
            pattern = self.parse_pattern()
            self.expect_operator("=>")
            body = self.parse_expression()
            rules.append((pattern, body))
            if self.at_operator("|"):
                self.next_token()
                continue
            return tuple(rules)

    # -- infix operators -----------------------------------------------------
    def _parse_binary(self, level: int) -> Expr:
        """Precedence-climbing over :data:`_BINARY_LEVELS`."""
        if level > _HIGHEST_BINARY_LEVEL:
            return self._parse_unary()

        operators = _BINARY_LEVELS[level]
        left = self._parse_binary(level + 1)

        if level == _DELAY_LEVEL:
            # `tokens @+ delay`: at most once, the delay being an ordinary
            # arithmetic expression (`x @+ d + 1` delays by d + 1).
            if self.at_operator("@+"):
                token = self.next_token()
                return Delay(left, self._parse_binary(level + 1), token.position)
            return left

        while True:
            token = self.peek()
            # `div` and `mod` are keywords, every other operator is an OP token.
            spelling = token.value if token.kind in ("OP", "KEYWORD") else None
            if spelling not in operators:
                return left
            self.next_token()
            if level in _RIGHT_ASSOCIATIVE:
                # Right associative: recurse at the *same* level so that
                # `a :: b :: c` becomes `a :: (b :: c)`.
                right = self._parse_binary(level)
                return BinOp(str(spelling), left, right, token.position)
            right = self._parse_binary(level + 1)
            left = BinOp(str(spelling), left, right, token.position)

    def _parse_unary(self) -> Expr:
        """Prefix ``~`` (arithmetic negation) and ``not`` (boolean negation)."""
        if self.at_operator("~"):
            token = self.next_token()
            return UnOp("~", self._parse_unary(), token.position)
        if self.at_keyword("not"):
            token = self.next_token()
            return UnOp("not", self._parse_unary(), token.position)
        return self._parse_coefficient()

    def _parse_coefficient(self) -> Expr:
        """``n`v`` -- multiset coefficient, binding tighter than ``++``."""
        left = self._parse_application()
        if self.at_operator("`"):
            token = self.next_token()
            value = self._parse_application()
            return Coefficient(left, value, token.position)
        return left

    def _parse_application(self) -> Expr:
        """Juxtaposition: ``f x y`` is ``(f x) y``."""
        result = self._parse_atom()
        while self._starts_atom():
            argument = self._parse_atom()
            result = App(result, argument, result.position)
        return result

    def _starts_atom(self) -> bool:
        token = self.peek()
        if token.kind in ("ID", "INT", "REAL", "STRING", "CHAR"):
            return True
        if token.kind == "KEYWORD":
            return token.value in _ATOM_START_KEYWORDS
        if token.kind == "OP":
            return token.value in _ATOM_START_OPERATORS
        return False

    # -- atoms ---------------------------------------------------------------
    def _parse_atom(self) -> Expr:
        token = self.peek()

        if token.kind in ("INT", "REAL", "STRING", "CHAR"):
            self.next_token()
            return Literal(token.value, token.position)

        if token.kind == "ID":
            self.next_token()
            return Var(str(token.value), token.position)

        if token.kind == "KEYWORD":
            if token.value in ("true", "false"):
                self.next_token()
                return Literal(token.value == "true", token.position)
            if token.value == "nil":
                self.next_token()
                return ListExpr((), token.position)
            if token.value == "empty":
                self.next_token()
                return EmptyMultiset(token.position)
            if token.value == "op":
                return self._parse_op_section()
            # `if`/`let`/`case`/`fn` in argument position must be bracketed in
            # ML, so reaching here means the input really is malformed.
            raise ParseError(
                f"unexpected keyword {token.value!r} in expression", token.position
            )

        if token.kind == "OP":
            if token.value == "(":
                return self._parse_parenthesised()
            if token.value == "[":
                return self._parse_list()
            if token.value == "{":
                return self._parse_record()
            if token.value == "#":
                return self._parse_selector()

        raise ParseError(
            f"unexpected {self._describe(token)} where an expression was expected",
            token.position,
        )

    def _parse_op_section(self) -> Expr:
        """``op +``: an infix operator as a function on pairs."""
        start = self.next_token().position                 # the `op` keyword
        token = self.next_token()
        spelling = token.value if token.kind in ("OP", "KEYWORD") else None
        if spelling not in _OP_SECTIONS:
            raise ParseError(
                f"'op' must be followed by an infix operator, not {self._describe(token)}",
                token.position,
            )
        left, right = "op'left", "op'right"                # cannot clash with ML names
        pattern = PTuple((PVar(left, start), PVar(right, start)), start)
        body = BinOp(str(spelling), Var(left, start), Var(right, start), start)
        return FnExpr(((pattern, body),), start)

    def _parse_parenthesised(self) -> Expr:
        """``()``, ``(e)`` or ``(e1, e2, ...)`` -- unit, grouping, or a tuple."""
        start = self.expect_operator("(").position
        if self.at_operator(")"):
            self.next_token()
            return Literal(UNIT, start)
        items = [self.parse_expression()]
        while self.at_operator(","):
            self.next_token()
            items.append(self.parse_expression())
        self.expect_operator(")")
        if len(items) == 1:
            return items[0]
        return TupleExpr(tuple(items), start)

    def _parse_list(self) -> Expr:
        start = self.expect_operator("[").position
        items: list[Expr] = []
        if not self.at_operator("]"):
            items.append(self.parse_expression())
            while self.at_operator(","):
                self.next_token()
                items.append(self.parse_expression())
        self.expect_operator("]")
        return ListExpr(tuple(items), start)

    def _parse_record(self) -> Expr:
        start = self.expect_operator("{").position
        fields: list[tuple[str, Expr]] = []
        if not self.at_operator("}"):
            while True:
                name = self.expect_identifier()
                self.expect_operator("=")
                fields.append((name, self.parse_expression()))
                if self.at_operator(","):
                    self.next_token()
                    continue
                break
        self.expect_operator("}")
        return RecordExpr(tuple(fields), start)

    def _parse_selector(self) -> Expr:
        """``#field`` (record projection) or ``#2`` (tuple projection)."""
        start = self.expect_operator("#").position
        token = self.next_token()
        if token.kind == "ID":
            return Selector(str(token.value), start)
        if token.kind == "INT":
            return Selector(str(token.value), start)
        raise ParseError("'#' must be followed by a field name or a number", start)

    # =======================================================================
    # Patterns
    # =======================================================================
    def parse_pattern(self) -> Pattern:
        """Patterns support ``as`` at the outermost level and ``::`` inside."""
        # `x as p`
        if self.peek().kind == "ID" and self.peek(1).kind == "KEYWORD" and self.peek(1).value == "as":
            name_token = self.next_token()
            self.next_token()  # consume `as`
            return PAs(str(name_token.value), self.parse_pattern(), name_token.position)
        return self._parse_cons_pattern()

    def _parse_cons_pattern(self) -> Pattern:
        """``h :: t`` -- right associative, like the expression form."""
        head = self._parse_applied_pattern()
        if self.at_operator("::"):
            token = self.next_token()
            return PCons(head, self._parse_cons_pattern(), token.position)
        return head

    def _parse_applied_pattern(self) -> Pattern:
        """A constructor applied to an argument: ``Car (a, b)``, ``ph(i)``.

        This level exists because ML distinguishes two pattern positions.  In
        ``fun f p1 p2 = e`` the parameters are *atomic* patterns, so ``f x y``
        means two parameters rather than one constructor application.  Anywhere
        else -- inside brackets, in a ``case`` rule, on the left of ``=>`` --
        juxtaposition **is** constructor application.  :meth:`_parse_fun`
        therefore calls :meth:`_parse_atomic_pattern` directly and everything
        else comes through here.

        Whether the head really names a constructor is not decided now: the
        matcher resolves it against the environment, so ``ph(i)`` works whether
        ``ph`` is an index tag or a union constructor.
        """
        head = self._parse_atomic_pattern()
        if isinstance(head, PConstructor) and head.argument is None and self._starts_atomic_pattern():
            return PConstructor(head.name, self._parse_atomic_pattern(), head.position)
        if isinstance(head, PVar) and self._starts_atomic_pattern():
            return PConstructor(head.name, self._parse_atomic_pattern(), head.position)
        return head

    def _parse_atomic_pattern(self) -> Pattern:
        token = self.peek()

        if token.kind in ("INT", "REAL", "STRING", "CHAR"):
            self.next_token()
            return PLiteral(token.value, token.position)

        if token.kind == "KEYWORD":
            if token.value in ("true", "false"):
                self.next_token()
                return PLiteral(token.value == "true", token.position)
            if token.value == "nil":
                self.next_token()
                return PList((), token.position)
            raise ParseError(f"unexpected keyword {token.value!r} in pattern", token.position)

        if token.kind == "ID":
            self.next_token()
            name = str(token.value)
            # A capitalised identifier is *probably* a nullary constructor, but
            # it could equally be a variable the modeller chose to capitalise,
            # so we emit PConstructor and let the matcher resolve it against
            # the environment.  Constructor *application* is handled one level
            # up, in :meth:`_parse_applied_pattern`.
            if name[:1].isupper():
                return PConstructor(name, None, token.position)
            return PVar(name, token.position)

        if token.kind == "OP":
            if token.value == "_":
                self.next_token()
                return PWildcard(token.position)
            if token.value == "(":
                return self._parse_parenthesised_pattern()
            if token.value == "[":
                return self._parse_list_pattern()
            if token.value == "{":
                return self._parse_record_pattern()
            if token.value == "~" and self.peek(1).kind in ("INT", "REAL"):
                # `~1` reaches here only if the lexer split it, which it does
                # not, but handle it defensively.
                self.next_token()
                number = self.next_token()
                return PLiteral(-number.value, token.position)  # type: ignore[operator]

        raise ParseError(
            f"unexpected {self._describe(token)} where a pattern was expected",
            token.position,
        )

    def _starts_atomic_pattern(self) -> bool:
        token = self.peek()
        if token.kind in ("ID", "INT", "REAL", "STRING", "CHAR"):
            return True
        if token.kind == "KEYWORD":
            return token.value in ("true", "false", "nil")
        if token.kind == "OP":
            return token.value in ("(", "[", "{", "_")
        return False

    def _parse_parenthesised_pattern(self) -> Pattern:
        start = self.expect_operator("(").position
        if self.at_operator(")"):
            self.next_token()
            return PLiteral(UNIT, start)
        items = [self.parse_pattern()]
        while self.at_operator(","):
            self.next_token()
            items.append(self.parse_pattern())
        self.expect_operator(")")
        if len(items) == 1:
            return items[0]
        return PTuple(tuple(items), start)

    def _parse_list_pattern(self) -> Pattern:
        start = self.expect_operator("[").position
        items: list[Pattern] = []
        if not self.at_operator("]"):
            items.append(self.parse_pattern())
            while self.at_operator(","):
                self.next_token()
                items.append(self.parse_pattern())
        self.expect_operator("]")
        return PList(tuple(items), start)

    def _parse_record_pattern(self) -> Pattern:
        start = self.expect_operator("{").position
        fields: list[tuple[str, Pattern]] = []
        open_ended = False
        if not self.at_operator("}"):
            while True:
                if self.at_operator("."):
                    # `...` ellipsis: three separate '.' tokens, or one '..'
                    # plus a '.', depending on how the lexer split them.
                    while self.at_operator(".") or self.at_operator(".."):
                        self.next_token()
                    open_ended = True
                    break
                name = self.expect_identifier()
                if self.at_operator("="):
                    self.next_token()
                    fields.append((name, self.parse_pattern()))
                else:
                    # ML shorthand: `{name}` binds the field to a variable of
                    # the same name.
                    fields.append((name, PVar(name, start)))
                if self.at_operator(","):
                    self.next_token()
                    continue
                break
        self.expect_operator("}")
        return PRecord(tuple(fields), open_ended, start)

    # =======================================================================
    # Declarations
    # =======================================================================
    def parse_declaration(self) -> Decl:
        token = self.peek()
        if self.at_keyword("val"):
            start = self.next_token().position
            pattern = self.parse_pattern()
            self.expect_operator("=")
            return ValDecl(pattern, self.parse_expression(), start)
        if self.at_keyword("fun"):
            return self._parse_fun()
        raise ParseError(
            f"expected 'val' or 'fun' but found {self._describe(token)}", token.position
        )

    def _parse_fun(self) -> Decl:
        """``fun f p1 p2 = e | f q1 q2 = e2`` -- curried, multi-clause."""
        start = self.expect_keyword("fun").position
        name = self.expect_identifier()
        clauses: list[tuple[tuple[Pattern, ...], Expr]] = []
        while True:
            parameters: list[Pattern] = []
            while not self.at_operator("="):
                parameters.append(self._parse_atomic_pattern())
            if not parameters:
                raise ParseError(f"function '{name}' has no parameters", start)
            self.expect_operator("=")
            clauses.append((tuple(parameters), self.parse_expression()))
            # Another clause? It starts with `|` followed by the same name.
            if self.at_operator("|"):
                save = self.index
                self.next_token()
                if self.peek().kind == "ID" and self.peek().value == name:
                    self.next_token()
                    continue
                self.index = save
            return FunDecl(name, tuple(clauses), start)


# ===========================================================================
# Public entry points
# ===========================================================================
def parse_expression(source: str) -> Expr:
    """Parse a complete expression; error if anything is left over."""
    parser = Parser(tokenise(source), source)
    expression = parser.parse_expression()
    if not parser.at_end():
        raise ParseError(
            f"unexpected {Parser._describe(parser.peek())} after the expression",
            parser.peek().position,
        )
    return expression


def parse_arc_expression(source: str) -> Expr:
    """Parse an arc inscription, allowing a trailing ``@+ delay``."""
    parser = Parser(tokenise(source), source)
    expression = parser.parse_expression()
    if parser.at_operator("@+"):
        token = parser.next_token()
        expression = Delay(expression, parser.parse_expression(), token.position)
    if not parser.at_end():
        raise ParseError(
            f"unexpected {Parser._describe(parser.peek())} after the arc expression",
            parser.peek().position,
        )
    return expression


def parse_declarations(source: str) -> list[Decl]:
    """Parse a whole declaration block (a sequence of ``val`` / ``fun``)."""
    parser = Parser(tokenise(source), source)
    declarations: list[Decl] = []
    while not parser.at_end():
        if parser.at_operator(";"):
            parser.next_token()
            continue
        declarations.append(parser.parse_declaration())
    return declarations


#: How :func:`expression_to_pattern` should read a bare identifier.
#: ``"constructor"`` -- a union/enumeration tag, matched as a constant;
#: ``"variable"``    -- a net variable, bound by the match;
#: ``"other"``       -- anything else (a function, an ML constant), which means
#: the expression is *not* a pattern and must be evaluated instead.
IdentifierRole = str


def default_identifier_role(name: str) -> IdentifierRole:
    """Fallback classifier: ML's capitalisation convention.

    Used when no environment is available (tests, tooling).  The binder always
    supplies a real classifier built from the model's declarations, because
    the convention is only a convention -- plenty of CPN models capitalise
    their function names.
    """
    return "constructor" if name[:1].isupper() else "variable"


def expression_to_pattern(expression: Expr,
                          identifier_role: Callable[[str], IdentifierRole] | None = None) -> Pattern:
    """Reinterpret an expression as a pattern.

    Why this exists
    ---------------
    An input arc carries an inscription such as ``(x, n)`` or ``1`p``.  CPN
    semantics say the transition is enabled when the inscription *evaluates* to
    a sub-multiset of the place's marking -- but the variables in it are
    unbound, so we cannot evaluate it yet.  The practical algorithm (the same
    one CPN Tools uses for the common case) is to treat the inscription as a
    pattern and match it against the tokens that are actually there, which
    determines the variable values directly instead of guessing them.

    Not every expression is a pattern: ``x + 1`` is not, because matching it
    would require inverting addition.  When that happens we raise
    :class:`ParseError`, and the binder falls back to enumerating the
    variable's colour set and *evaluating* the expression instead.  So this
    function failing is a performance question, not a correctness one.
    """
    role = identifier_role or default_identifier_role

    def convert(node: Expr) -> Pattern:
        if isinstance(node, Literal):
            return PLiteral(node.value, node.position)

        if isinstance(node, Var):
            kind = role(node.name)
            if kind == "constructor":
                return PConstructor(node.name, None, node.position)
            if kind == "variable":
                return PVar(node.name, node.position)
            # An ML constant or a function name: not something we can match
            # against, so the whole inscription must be evaluated instead.
            raise ParseError(
                f"'{node.name}' is not a variable or a constructor, so this "
                "expression cannot be used as a pattern",
                node.position,
            )

        if isinstance(node, TupleExpr):
            return PTuple(tuple(convert(i) for i in node.items), node.position)

        if isinstance(node, ListExpr):
            return PList(tuple(convert(i) for i in node.items), node.position)

        if isinstance(node, RecordExpr):
            return PRecord(
                tuple((n, convert(v)) for n, v in node.fields), False, node.position
            )

        if isinstance(node, BinOp) and node.operator == "::":
            return PCons(convert(node.left), convert(node.right), node.position)

        if isinstance(node, App) and isinstance(node.function, Var):
            # `Car (a, b)` -- but only if `Car` really is a constructor.  A
            # capitalised *function* such as `Chopsticks(p)` must not be read
            # as a pattern, or we would try to match tokens against it.
            if role(node.function.name) == "constructor":
                return PConstructor(
                    node.function.name, convert(node.argument), node.position
                )

        raise ParseError("this expression cannot be used as a pattern", node.position)

    return convert(expression)
