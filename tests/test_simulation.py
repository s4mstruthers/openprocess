"""End-to-end behaviour: binding, firing, guards, time, and fusion sets."""

import pytest

from models import (
    dining_philosophers, guarded_choice, simple_transfer, timed_conveyor,
)

from openprocess.ml.multiset import Multiset
from openprocess.model.net import Arc, CPNet, Place, Transition
from openprocess.sim.simulator import DeadMarkingError, Simulator


def marking_of(net, simulator, place_name):
    place = next(p for p in net.all_places() if p.name == place_name)
    return simulator.marking.get(net.marking_key(place.id))


# -- basic firing -----------------------------------------------------------
def test_example_models_compile_without_errors():
    for builder in (simple_transfer, guarded_choice, timed_conveyor,
                    lambda: dining_philosophers(3)):
        net = builder()
        assert net.errors == [], f"{net.name}: {net.errors}"


def test_one_binding_per_distinct_token():
    net = simple_transfer()
    simulator = Simulator(net, seed=0)
    # A holds 1`red ++ 2`green, so `move` has two bindings, not three: the two
    # green tokens are indistinguishable and give the same binding element.
    assert len(simulator.all_enabled()) == 2


def test_firing_moves_exactly_the_bound_token():
    net = simple_transfer()
    simulator = Simulator(net, seed=0)
    red = next(b for b in simulator.all_enabled() if "red" in b.describe(net))
    simulator.step(red)
    assert marking_of(net, simulator, "A") == Multiset({_green(net): 2})
    assert marking_of(net, simulator, "B").size() == 1


def _green(net):
    from openprocess.ml.values import Constructor
    return Constructor("green")


def test_run_reaches_the_expected_final_marking():
    net = simple_transfer()
    simulator = Simulator(net, seed=1)
    simulator.run(50)
    assert marking_of(net, simulator, "A").is_empty()
    assert marking_of(net, simulator, "B").size() == 3


def test_dead_marking_stops_the_run():
    net = simple_transfer()
    simulator = Simulator(net, seed=1)
    simulator.run(50)
    with pytest.raises(DeadMarkingError):
        simulator.step()


# -- guards -----------------------------------------------------------------
def test_guard_filters_bindings():
    net = guarded_choice()
    simulator = Simulator(net, seed=0)
    to_big = next(t for t in net.all_transitions() if t.name == "toBig")
    values = {b.as_dict()["n"] for b in simulator.enabled_bindings(to_big)}
    assert values == {4, 5}


# -- time -------------------------------------------------------------------
def test_produced_tokens_carry_the_arc_delay():
    net = timed_conveyor()
    simulator = Simulator(net, seed=0)
    simulator.step()
    done = marking_of(net, simulator, "Done")
    # One token, stamped three units in the future.
    assert [stamp for _value, stamp, _count in done.items()] == [3]


def test_clock_jumps_to_the_next_timestamp():
    net = timed_conveyor()
    simulator = Simulator(net, seed=0)
    simulator.run(10)
    assert simulator.clock == 3


# -- dining philosophers ----------------------------------------------------
def test_taking_chopsticks_consumes_both_neighbours():
    net = dining_philosophers(3)
    simulator = Simulator(net, seed=0)
    take = next(t for t in net.all_transitions() if t.name == "take")
    element = simulator.enabled_bindings(take)[0]
    simulator.step(element)
    # One philosopher eating, one chopstick left of three.
    assert marking_of(net, simulator, "Eat").size() == 1
    assert marking_of(net, simulator, "Unused").size() == 1


def test_only_one_philosopher_can_eat_at_a_time_with_three_seats():
    net = dining_philosophers(3)
    simulator = Simulator(net, seed=0)
    take = next(t for t in net.all_transitions() if t.name == "take")
    simulator.step(simulator.enabled_bindings(take)[0])
    assert simulator.enabled_bindings(take) == []


# -- fusion sets ------------------------------------------------------------
def test_places_in_a_fusion_set_share_one_marking():
    net = CPNet("Fusion")
    net.add_declaration("colset U = unit;")
    page = net.add_page("Top")

    # Two drawings of the same place, joined by a fusion group.
    left = Place(name="Shared A", colour_set_name="U",
                 initial_marking_text="1`()", fusion_group="F")
    right = Place(name="Shared B", colour_set_name="U", fusion_group="F")
    sink = Place(name="Sink", colour_set_name="U")
    drain = Transition(name="drain")
    page.places += [left, right, sink]
    page.transitions.append(drain)
    # Consume from the *other* drawing of the fused place.
    page.arcs += [
        Arc(place_id=right.id, transition_id=drain.id, orientation="PtoT",
            expression_text="1`()"),
        Arc(place_id=sink.id, transition_id=drain.id, orientation="TtoP",
            expression_text="1`()"),
    ]
    assert net.compile() == []

    simulator = Simulator(net, seed=0)
    # The token was declared on `left` but must be visible through `right`.
    assert simulator.is_enabled(drain)
    simulator.step()
    assert marking_of(net, simulator, "Shared A").is_empty()
    assert marking_of(net, simulator, "Sink").size() == 1


# -- review fixes ---------------------------------------------------------------
def _counter_net(input_inscription: str) -> tuple[CPNet, Transition]:
    """A holds 2, 3, 4; `t` takes `input_inscription` from A for n < 5."""
    net = CPNet("Counter")
    net.add_declaration("colset N = int with 1..5;")
    net.add_declaration("var n : N;")
    page = net.add_page("Top")
    a = Place(name="A", colour_set_name="N", initial_marking_text="1`2 ++ 1`3 ++ 1`4")
    b = Place(name="B", colour_set_name="N")
    t = Transition(name="t", guard_text="[n < 5]")
    page.places += [a, b]
    page.transitions.append(t)
    page.arcs += [
        Arc(place_id=a.id, transition_id=t.id, orientation="PtoT",
            expression_text=input_inscription),
        Arc(place_id=b.id, transition_id=t.id, orientation="TtoP", expression_text="n"),
    ]
    assert net.compile() == []
    return net, t


def test_variable_only_in_a_computed_input_is_enumerated():
    # `n` is bound by no pattern, so it must be tried over N = 1..5: n+1 must
    # be in A, i.e. n = 1, 2, 3 (as CPN Tools does).  Used to find nothing.
    net, t = _counter_net("1`(n+1)")
    bindings = Simulator(net).enabled_bindings(t)
    assert [b.as_dict()["n"] for b in bindings] == [1, 2, 3]


def test_two_computed_input_terms_do_not_recurse_forever():
    # Used to rotate the two unevaluable terms until RecursionError.
    net, t = _counter_net("1`(n+1) ++ 1`(n+2)")
    bindings = Simulator(net).enabled_bindings(t)
    assert [b.as_dict()["n"] for b in bindings] == [1, 2]


def test_the_seed_also_fixes_the_models_own_random_draws():
    net = CPNet("Random")
    net.add_declaration("colset N = int;")
    page = net.add_page("Top")
    source = Place(name="Source", colour_set_name="UNIT", initial_marking_text="5`()")
    drawn = Place(name="Drawn", colour_set_name="N")
    draw = Transition(name="draw")
    page.places += [source, drawn]
    page.transitions.append(draw)
    page.arcs += [
        Arc(place_id=source.id, transition_id=draw.id, orientation="PtoT", expression_text="()"),
        Arc(place_id=drawn.id, transition_id=draw.id, orientation="TtoP",
            expression_text="discrete(1, 1000000)"),
    ]
    assert net.compile() == []

    def run() -> Multiset:
        simulator = Simulator(net, seed=42)
        simulator.run(10)
        return marking_of(net, simulator, "Drawn")

    assert run() == run()


def _timed_net(output_inscription: str, transition_delay: str = "") -> tuple[CPNet, Simulator]:
    net = CPNet("Timed")
    net.add_declaration("colset T = int timed;")
    net.add_declaration("var n : T;")
    page = net.add_page("Top")
    a = Place(name="A", colour_set_name="T", initial_marking_text="1`1")
    b = Place(name="B", colour_set_name="T")
    t = Transition(name="t", time_text=transition_delay)
    page.places += [a, b]
    page.transitions.append(t)
    page.arcs += [
        Arc(place_id=a.id, transition_id=t.id, orientation="PtoT", expression_text="n"),
        Arc(place_id=b.id, transition_id=t.id, orientation="TtoP",
            expression_text=output_inscription),
    ]
    assert net.compile() == []
    simulator = Simulator(net, seed=0)
    simulator.step()
    return net, simulator


def _stamps(net, simulator, place_name):
    return sorted((value, stamp) for value, stamp, _count in
                  marking_of(net, simulator, place_name).items())


def test_transition_and_arc_delays_add_up():
    # CPN Tools: time stamp = model time + transition delay + arc delay.
    net, simulator = _timed_net("n @+ 3", transition_delay="@+5")
    assert _stamps(net, simulator, "B") == [(1, 8)]


def test_each_timed_term_keeps_its_own_delay():
    net, simulator = _timed_net("1`n@+2 +++ 1`(n+10)@+7")
    assert _stamps(net, simulator, "B") == [(1, 2), (11, 7)]


def test_a_delay_inside_an_if_branch():
    net, simulator = _timed_net("if n = 1 then 1`n@+4 else empty")
    assert _stamps(net, simulator, "B") == [(1, 4)]


def test_a_trailing_delay_applies_to_the_whole_inscription():
    net, simulator = _timed_net("1`n ++ 1`(n+1) @+ 6")
    assert _stamps(net, simulator, "B") == [(1, 6), (2, 6)]


def test_timed_initial_marking_with_time_stamps():
    net = CPNet("Stamps")
    net.add_declaration("colset T = int timed;")
    page = net.add_page("Top")
    page.places.append(Place(name="P", colour_set_name="T",
                             initial_marking_text="1`3@5 +++ 2`4@0"))
    assert net.compile() == []
    tokens = net.initial_marking().get(page.places[0].id)
    assert sorted(tokens.items()) == [(3, 5, 1), (4, 0, 2)]
