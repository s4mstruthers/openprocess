"""Multiset algebra -- the foundation the firing rule is stated in.

If any of these fail, every simulation result is suspect, so they are checked
against hand-computed values rather than against the implementation.
"""


from openprocess.ml.multiset import Multiset, TimedMultiset


def test_construction_drops_zero_counts():
    # The class invariant: a value with count zero is simply absent.
    multiset = Multiset({"a": 2, "b": 0})
    assert multiset.support() == ["a"]
    assert multiset.count("b") == 0


def test_union_sums_coefficients():
    assert (Multiset({"a": 2}) + Multiset({"a": 1, "b": 3})) == Multiset({"a": 3, "b": 3})


def test_difference_truncates_at_zero():
    # CPN Tools defines `--` as max(0, m1 - m2), not as a signed difference.
    assert (Multiset({"a": 1}) - Multiset({"a": 3})) == Multiset.empty()


def test_inclusion_is_the_enabling_test():
    small = Multiset({"a": 1, "b": 1})
    large = Multiset({"a": 2, "b": 1, "c": 5})
    assert small <= large
    assert not (large <= small)


def test_inclusion_is_partial_not_total():
    # Neither includes the other, so both comparisons are false.  A total
    # order would wrongly report one of them true.
    left, right = Multiset({"a": 1}), Multiset({"b": 1})
    assert not (left <= right)
    assert not (right <= left)


def test_scalar_multiplication():
    assert Multiset({"a": 2}) * 3 == Multiset({"a": 6})
    assert Multiset({"a": 2}) * 0 == Multiset.empty()


def test_equal_multisets_hash_equally():
    # Markings are dictionary keys during state space exploration.
    assert hash(Multiset({"a": 1, "b": 2})) == hash(Multiset({"b": 2, "a": 1}))


def test_printing_uses_cpn_syntax():
    assert repr(Multiset({"a": 2})) == '2`"a"'
    assert repr(Multiset.empty()) == "empty"


# -- timed ------------------------------------------------------------------
def test_only_reached_timestamps_are_available():
    tokens = TimedMultiset.from_multiset(Multiset({"x": 1}), 0).add(
        TimedMultiset.from_multiset(Multiset({"x": 1}), 5)
    )
    assert tokens.available_at(0) == Multiset({"x": 1})
    assert tokens.available_at(5) == Multiset({"x": 2})


def test_next_time_after_finds_the_clock_jump():
    tokens = TimedMultiset.from_multiset(Multiset({"x": 1}), 7)
    assert tokens.next_time_after(0) == 7
    assert tokens.next_time_after(7) is None


def test_removal_takes_the_oldest_tokens_first():
    tokens = TimedMultiset.from_multiset(Multiset({"x": 1}), 0).add(
        TimedMultiset.from_multiset(Multiset({"x": 1}), 2)
    )
    remaining = tokens.remove_available(Multiset({"x": 1}), clock=5)
    # The token stamped 0 goes; the one stamped 2 stays.
    assert list(remaining.items()) == [("x", 2, 1)]
