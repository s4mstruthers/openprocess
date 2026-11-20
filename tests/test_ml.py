"""Lexer, parser and evaluator for the CPN ML subset."""

import pytest

from openprocess.ml.errors import CPNMLError, EvalError, ParseError
from openprocess.ml.evaluator import Environment, Evaluator
from openprocess.ml.lexer import tokenise
from openprocess.ml.multiset import Multiset
from openprocess.ml.parser import (
    expression_to_pattern, parse_arc_expression, parse_declarations,
    parse_expression,
)
from openprocess.ml.values import Constructor, MLList


@pytest.fixture
def evaluator() -> Evaluator:
    return Evaluator(seed=0)


def run(evaluator: Evaluator, source: str, **bindings):
    environment = Environment(bindings, evaluator.globals)
    return evaluator.evaluate(parse_expression(source), environment)


# -- lexer ------------------------------------------------------------------
def test_tilde_is_negation_not_subtraction():
    assert [t.value for t in tokenise("~3")][:1] == [-3]
    assert [t.value for t in tokenise("4 - 3")] == [4, "-", 3, None]


def test_comments_nest():
    # A non-nesting scanner would stop at the first `*)` and then fail.
    assert [t.kind for t in tokenise("1 (* a (* b *) c *) + 2")] == \
        ["INT", "OP", "INT", "EOF"]


def test_longest_operator_wins():
    operators = [t.value for t in tokenise("a ++ b -- c @+ d") if t.kind == "OP"]
    assert operators == ["++", "--", "@+"]


def test_identifier_scan_terminates_at_end_of_input():
    # Regression: an end-of-input check that used `"" in "_'"` looped forever.
    assert [t.kind for t in tokenise("abc")] == ["ID", "EOF"]


# -- parser -----------------------------------------------------------------
def test_coefficient_binds_tighter_than_multiset_union():
    # `2`x ++ 3`y` must group as `(2`x) ++ (3`y)`.
    tree = parse_expression("2`x ++ 3`y")
    assert tree.operator == "++"
    assert tree.left.__class__.__name__ == "Coefficient"


def test_application_binds_tighter_than_the_coefficient():
    tree = parse_expression("1`f x")
    assert tree.__class__.__name__ == "Coefficient"
    assert tree.value.__class__.__name__ == "App"


def test_cons_is_right_associative():
    tree = parse_expression("a :: b :: c")
    assert tree.right.operator == "::"


def test_function_parameters_are_atomic_patterns():
    # `fun add x y` is two parameters, not `add (x y)`.
    declaration = parse_declarations("fun add x y = x + y")[0]
    assert len(declaration.clauses[0][0]) == 2


def test_constructor_application_in_a_bracketed_pattern():
    # `ph(i)` inside brackets is a constructor application even though `ph`
    # is lower case.
    declaration = parse_declarations("fun f (ph(i)) = i")[0]
    pattern = declaration.clauses[0][0][0]
    assert pattern.__class__.__name__ == "PConstructor"
    assert pattern.name == "ph"


# -- evaluator --------------------------------------------------------------
@pytest.mark.parametrize(
    "source, expected",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("~3 + 4", 1),
        ("7 div 2", 3),
        ("7 mod 2", 1),
        ('"a" ^ "b"', "ab"),
        ("if 1 < 2 then 10 else 20", 10),
        ("let val x = 5 in x * x end", 25),
        ("[1,2] ^^ [3]", MLList([1, 2, 3])),
        ("1 :: [2]", MLList([1, 2])),
        ("#2 (1, 2)", 2),
        ("not false", True),
        ("true andalso false", False),
    ],
)
def test_expression_results(evaluator, source, expected):
    assert run(evaluator, source) == expected


def test_short_circuit_avoids_evaluating_the_right_operand(evaluator):
    # If `andalso` were strict this would raise "unbound identifier".
    assert run(evaluator, "false andalso undefined_thing") is False


def test_multiset_expressions(evaluator):
    assert run(evaluator, "2`1 ++ 1`2") == Multiset({1: 2, 2: 1})
    assert run(evaluator, "empty") == Multiset.empty()


def test_recursive_function(evaluator):
    evaluator.run_declarations(parse_declarations("fun fact 0 = 1 | fact n = n * fact(n-1)"))
    assert run(evaluator, "fact 5") == 120


def test_curried_function(evaluator):
    evaluator.run_declarations(parse_declarations("fun add x y = x + y"))
    assert run(evaluator, "add 2 3") == 5


def test_higher_order_builtin(evaluator):
    assert run(evaluator, "List.map (fn x => x * 2) [1,2,3]") == MLList([2, 4, 6])


def test_records_and_selection(evaluator):
    assert run(evaluator, "#name {name = 1, age = 2}") == 1


def test_case_matches_top_to_bottom(evaluator):
    assert run(evaluator, "case [1,2] of [] => 0 | h :: _ => h") == 1


def test_unbound_identifier_is_a_model_error(evaluator):
    with pytest.raises(CPNMLError):
        run(evaluator, "nosuchthing + 1")


def test_division_by_zero_is_a_model_error(evaluator):
    with pytest.raises(EvalError):
        run(evaluator, "1 div 0")


# -- arc expressions and patterns -------------------------------------------
def test_bare_value_on_an_arc_means_one_token(evaluator):
    tokens, stamp = evaluator.evaluate_arc(
        parse_arc_expression("x"), Environment({"x": 7}, evaluator.globals), clock=3
    )
    assert tokens == Multiset({7: 1}) and stamp == 3


def test_delay_adds_to_the_clock(evaluator):
    tokens, stamp = evaluator.evaluate_arc(
        parse_arc_expression("1`x @+ 5"), Environment({"x": 7}, evaluator.globals), clock=3
    )
    assert tokens == Multiset({7: 1}) and stamp == 8


def test_repeated_variable_in_a_pattern_forces_equality(evaluator):
    pattern = expression_to_pattern(parse_expression("(x, x)"))
    assert evaluator.match(pattern, (1, 1), {}, evaluator.globals)
    assert not evaluator.match(pattern, (1, 2), {}, evaluator.globals)


def test_non_pattern_expression_is_rejected():
    # `x + 1` cannot be matched -- that would require inverting addition.
    with pytest.raises(ParseError):
        expression_to_pattern(parse_expression("x + 1"))


# -- basis additions ----------------------------------------------------------
def test_op_turns_an_operator_into_a_function(evaluator):
    assert run(evaluator, "List.foldl (op +) 0 [1, 2, 3]") == 6
    assert run(evaluator, "List.foldr (op ::) [] [1, 2]") == MLList((1, 2))


def test_options(evaluator):
    assert run(evaluator, "Int.fromString \"12\"") == Constructor("SOME", 12)
    assert run(evaluator, "Int.fromString \"~7\"") == Constructor("SOME", -7)
    assert run(evaluator, "Int.fromString \"x\"") == Constructor("NONE")
    assert run(evaluator, "case Int.fromString \"3\" of SOME n => n | NONE => 0") == 3
    assert run(evaluator, "valOf (SOME 4) + getOpt (NONE, 1)") == 5
    assert run(evaluator, "isSome NONE") is False


def test_ms_to_col_returns_the_single_colour(evaluator):
    assert run(evaluator, "ms_to_col (1`5)") == 5
    with pytest.raises(EvalError):
        run(evaluator, "ms_to_col (2`5)")


def test_more_of_the_basis(evaluator):
    assert run(evaluator, "List.concat [[1], [2, 3]]") == MLList((1, 2, 3))
    assert run(evaluator, "String.sub (\"abc\", 1)") == "b"
    assert run(evaluator, "Char.ord #\"a\"") == 97
    assert run(evaluator, "Int.abs ~2") == 2
    assert run(evaluator, "List.find (fn x => x > 1) [1, 2, 3]") == Constructor("SOME", 2)
