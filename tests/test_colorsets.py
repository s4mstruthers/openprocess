"""Colour sets: membership and enumeration."""

import pytest

from openprocess.ml.colorsets import (
    BoolColourSet, EnumColourSet, IndexColourSet, InfiniteColourSetError,
    IntColourSet, ListColourSet, ProductColourSet, RecordColourSet,
    UnionColourSet, standard_colour_sets,
)
from openprocess.ml.values import Constructor
from openprocess.model.declarations import parse_colour_set_declaration


def test_bool_is_not_a_member_of_int():
    # Python's bool subclasses int, so this needs an explicit guard.
    assert not IntColourSet("INT").contains(True)
    assert BoolColourSet("BOOL").contains(True)


def test_ranged_int_is_finite_and_enumerable():
    colour_set = IntColourSet("SMALL", low=1, high=3)
    assert colour_set.is_finite()
    assert list(colour_set.members()) == [1, 2, 3]


def test_unranged_int_refuses_enumeration():
    with pytest.raises(InfiniteColourSetError):
        list(IntColourSet("INT").members())


def test_product_enumerates_the_cartesian_product():
    colours = EnumColourSet("E", ["a", "b"])
    numbers = IntColourSet("N", low=1, high=2)
    product = ProductColourSet("P", [colours, numbers])
    assert len(list(product.members())) == 4


def test_list_is_infinite_without_a_length_bound():
    element = IntColourSet("N", low=1, high=2)
    assert not ListColourSet("L", element).is_finite()
    assert ListColourSet("L", element, length_high=2).is_finite()


# -- declaration parsing ----------------------------------------------------
@pytest.mark.parametrize(
    "source, kind, finite",
    [
        ("colset E = with red | green;", EnumColourSet, True),
        ("colset N = int with 1..4;", IntColourSet, True),
        ("colset X = index ph with 1..3;", IndexColourSet, True),
        ("colset P = product INT * INT;", ProductColourSet, False),
        ("colset R = record a:INT * b:INT;", RecordColourSet, False),
        ("colset U = union Car:INT + Walk;", UnionColourSet, False),
    ],
)
def test_declaration_forms(source, kind, finite):
    registry = standard_colour_sets()
    colour_set = parse_colour_set_declaration(source, registry)
    assert isinstance(colour_set, kind)
    assert colour_set.is_finite() is finite


def test_timed_keyword_is_recognised():
    registry = standard_colour_sets()
    colour_set = parse_colour_set_declaration("colset T = int timed;", registry)
    assert colour_set.timed


def test_union_membership_checks_the_payload():
    registry = standard_colour_sets()
    union = parse_colour_set_declaration("colset U = union Car:INT + Walk;", registry)
    assert union.contains(Constructor("Car", 3))
    assert union.contains(Constructor("Walk"))
    assert not union.contains(Constructor("Car", "three"))
    assert not union.contains(Constructor("Bike", 3))


# -- declarations that depend on values and functions -------------------------
def _compiled(*declarations):
    from openprocess.model.net import CPNet
    net = CPNet("Declarations")
    for declaration in declarations:
        net.add_declaration(declaration)
    assert net.compile() == []
    return net


def test_a_range_may_use_a_declared_value():
    # Declared in either order: the value is found once it exists.
    net = _compiled("colset N = int with 1..n;", "val n = 4;",
                    "colset M = int with ~n..n * 2;")
    assert list(net.declarations.colour_sets["N"].members()) == [1, 2, 3, 4]
    assert list(net.declarations.colour_sets["M"].members())[0] == -4


def test_subset_by_a_predicate_keeps_only_matching_values():
    net = _compiled("colset N = int with 1..6;", "fun even x = x mod 2 = 0;",
                    "colset E = subset N by even;")
    evens = net.declarations.colour_sets["E"]
    assert list(evens.members()) == [2, 4, 6]
    assert not evens.contains(3)


def test_subset_with_a_list_is_finite_even_over_int():
    net = _compiled("colset L = subset INT with [3, 5, 7];")
    listed = net.declarations.colour_sets["L"]
    assert listed.is_finite()
    assert list(listed.members()) == [3, 5, 7]
    assert not listed.contains(4)


def test_colour_set_functions():
    from openprocess.ml.parser import parse_expression
    net = _compiled("colset PH = index ph with 1..3;")

    def run(source):
        return net.evaluator.evaluate(parse_expression(source))

    assert run("PH.size ()") == 3
    assert run("PH.all ()").size() == 3
    assert run("PH.ord (ph 2)") == 1
    assert run("PH.col 0") == Constructor("ph", 1)
    assert run("PH.legal (ph 3)") is True
    assert run("PH.ran ()") in list(net.declarations.colour_sets["PH"].members())


def test_an_unknown_bound_names_the_colour_set():
    from openprocess.model.net import CPNet
    net = CPNet("Broken")
    net.add_declaration("colset N = int with 1..missing;")
    problems = net.compile()
    assert len(problems) == 1
    assert "colset N" in problems[0].message and "missing" in problems[0].message
