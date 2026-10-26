"""Performance analysis from the simulator (``openprocess.sim.performance``) and
the *Performance* box: throughput, utilisation, waiting times and token counts
checked against hand calculations on deterministic models, reproducibility
from the seed, the warm-up, and an untimed net."""

from __future__ import annotations

import pytest

from models import _connect, dining_philosophers, simple_transfer, timed_conveyor

from openprocess.flow.explain import explaining
from openprocess.model.net import CPNet, Place, Transition
from openprocess.sim.performance import COLUMNS, analyse_performance


def near(value):
    """Within the four decimals the rows are rounded to."""
    return pytest.approx(value, abs=5e-5)


def row(result, kind, name):
    """The row of ``result.rows()`` for one element, as a dict by column."""
    for values in result.rows():
        if values[0] == kind and values[1] == name:
            return dict(zip(COLUMNS, values))
    raise AssertionError(f"no row for {kind} {name}")


def single_server_queue() -> CPNet:
    """Three items queue for one server that takes 4 time units each.

    Firings at 0, 4 and 8; the clock then jumps to 12, where nothing is
    enabled: dead.  Every number below follows by hand.
    """
    net = CPNet("Queue")
    net.add_declaration("colset ITEM = int timed;")
    net.add_declaration("colset U = unit timed;")
    net.add_declaration("var i : ITEM;")
    page = net.add_page("Top")
    queue = Place(name="Queue", colour_set_name="ITEM", initial_marking_text="1`1 ++ 1`2 ++ 1`3")
    server = Place(name="Server", colour_set_name="U", initial_marking_text="1`()")
    done = Place(name="Done", colour_set_name="ITEM")
    serve = Transition(name="serve")
    page.places += [queue, server, done]
    page.transitions.append(serve)
    _connect(page, queue, serve, "PtoT", "i")
    _connect(page, server, serve, "PtoT", "()")
    _connect(page, server, serve, "TtoP", "()@+4")
    _connect(page, done, serve, "TtoP", "i@+4")
    assert net.compile() == [], net.errors
    return net


def clock_loop() -> CPNet:
    """One token round a loop with a delay of 2: a firing every 2 time units, for ever."""
    net = CPNet("Loop")
    net.add_declaration("colset ITEM = int timed;")
    net.add_declaration("var i : ITEM;")
    page = net.add_page("Top")
    place = Place(name="P", colour_set_name="ITEM", initial_marking_text="1`1")
    tick = Transition(name="tick")
    page.places.append(place)
    page.transitions.append(tick)
    _connect(page, place, tick, "PtoT", "i")
    _connect(page, place, tick, "TtoP", "i@+2")
    assert net.compile() == [], net.errors
    return net


# -- hand calculations -------------------------------------------------------
def test_single_server_queue_matches_the_hand_calculation():
    result = analyse_performance(single_server_queue(), runs=1, steps=100)
    (run,) = result.runs
    assert result.timed and run.dead and run.firings == 3 and run.end == 12 and run.span == 12
    serve = row(result, "transition", "serve")
    assert serve["firings"] == 3 and serve["throughput"] == near(3 / 12)
    assert serve["utilisation"] == 1.0                      # busy [0,4] ∪ [4,8] ∪ [8,12] = the whole span
    queue = row(result, "place", "Queue")
    # Items 1, 2 and 3 are stamped 0 and taken at 0, 4 and 8.
    assert queue["consumed"] == 3
    assert (queue["waiting mean"], queue["waiting median"], queue["waiting max"]) == (4.0, 4.0, 8.0)
    # Two items left over [0,4), one over [4,8), none over [8,12).
    assert queue["mean tokens"] == near(12 / 12) and queue["occupancy"] == near(8 / 12)
    server = row(result, "place", "Server")
    # The server token is taken the moment it is free: it never waits, and is always in the place.
    assert (server["waiting mean"], server["waiting max"]) == (0.0, 0.0)
    assert server["mean tokens"] == 1.0 and server["occupancy"] == 1.0
    done = row(result, "place", "Done")
    assert done["consumed"] == 0 and done["waiting mean"] is None
    assert done["mean tokens"] == near((4 + 8 + 12) / 12)   # 1, then 2, then 3 tokens, 4 units each
    model = row(result, "model", "Queue")
    assert model["firings"] == 3 and model["throughput"] == near(0.25)
    assert model["waiting mean"] == near(12 / 6)      # all six consumed tokens pooled
    assert result.samples() == [near(0.25)] and result.samples("waiting time") == [near(2.0)]


def test_timed_conveyor_fires_three_times_at_once():
    result = analyse_performance(timed_conveyor(), runs=3, steps=50, seed=4)
    assert [run.seed for run in result.runs] == [4, 5, 6]
    assert all(run.dead and run.firings == 3 and run.end == 3 for run in result.runs)
    process = row(result, "transition", "process")
    assert process["firings"] == 3 and process["throughput"] == 1.0 and process["utilisation"] == 1.0
    waiting = row(result, "place", "Waiting")
    assert (waiting["waiting mean"], waiting["waiting max"], waiting["mean tokens"]) == (0.0, 0.0, 0.0)
    assert row(result, "place", "Done")["mean tokens"] == 3.0
    assert [values[0] for values in result.rows()] == ["model", "transition", "place", "place"]


# -- reproducibility -----------------------------------------------------------
def test_the_same_seed_gives_the_same_table():
    net = dining_philosophers(4)
    first = analyse_performance(net, runs=3, steps=60, seed=7)
    second = analyse_performance(net, runs=3, steps=60, seed=7)
    assert first.rows() == second.rows() and first.samples() == second.samples()
    assert [run.seed for run in first.runs] == [7, 8, 9]
    # Run k with seed s is the run Simulate CPN makes with seed s + k.
    from openprocess.sim.simulator import Simulator
    simulator = Simulator(net, seed=8)
    simulator.run(60)
    take = [r for r in simulator.log if net.find_transition(r.binding.transition_id).name == "take"]
    assert first.runs[1].transitions[take[0].binding.transition_id].firings == len(take)


# -- warm-up -------------------------------------------------------------------
def test_warm_up_leaves_out_the_early_firings():
    whole = analyse_performance(clock_loop(), runs=1, steps=10)
    (run,) = whole.runs
    assert run.end == 18 and not run.dead                     # firings at 0, 2, ..., 18
    tick = row(whole, "transition", "tick")
    assert tick["firings"] == 10 and tick["throughput"] == near(10 / 18)
    assert tick["utilisation"] == 1.0 and row(whole, "place", "P")["mean tokens"] == 1.0

    warmed = analyse_performance(clock_loop(), runs=1, steps=10, warm_up=5)
    (run,) = warmed.runs
    assert run.firings == 10 and run.span == 13               # the run is as long; the statistics are not
    tick = row(warmed, "transition", "tick")
    assert tick["firings"] == 7 and tick["throughput"] == near(7 / 13)   # at 6, 8, ..., 18
    assert tick["utilisation"] == 1.0                         # [4,6] is clipped to [5,6]; busy throughout
    assert row(warmed, "place", "P")["consumed"] == 7

    nothing = analyse_performance(clock_loop(), runs=1, steps=10, warm_up=18)
    assert row(nothing, "transition", "tick")["throughput"] is None and nothing.notes


def test_an_untimed_net_is_measured_in_steps():
    result = analyse_performance(simple_transfer(), runs=1, steps=10)
    assert not result.timed and "untimed" in result.notes[0] and result.unit == "step"
    (run,) = result.runs
    assert run.dead and run.firings == 3 and run.end == 3    # 1`red ++ 2`green move over, one per step
    move = row(result, "transition", "move")
    assert move["firings"] == 3 and move["throughput"] == 1.0 and move["utilisation"] is None
    a = row(result, "place", "A")
    # Every token arrived at step 0 and left at steps 0, 1 and 2.
    assert (a["waiting mean"], a["waiting median"], a["waiting max"]) == (1.0, 1.0, 2.0)
    assert a["mean tokens"] == near((2 + 1 + 0) / 3) and a["occupancy"] == near(2 / 3)
    assert row(result, "place", "B")["mean tokens"] == near((1 + 2 + 3) / 3)

    philosophers = analyse_performance(dining_philosophers(3), runs=2, steps=30, warm_up=10)
    take, put = row(philosophers, "transition", "take"), row(philosophers, "transition", "put")
    assert take["firings"] + put["firings"] == 20 and take["throughput"] + put["throughput"] == near(1.0)


def test_settings_are_checked():
    with pytest.raises(ValueError, match="runs"):
        analyse_performance(timed_conveyor(), runs=0)
    with pytest.raises(ValueError, match="warm_up"):
        analyse_performance(timed_conveyor(), warm_up=-1)
    with pytest.raises(ValueError, match="measure"):
        analyse_performance(timed_conveyor(), runs=1).samples("firings")


# -- the box -------------------------------------------------------------------
def test_performance_box_gives_a_table_with_samples_and_notes():
    from openprocess.flow.boxes.cpn import performance
    net = single_server_queue()
    with explaining() as explanation:
        table = performance(net, runs=4, steps=50, seed=2)
    assert table.columns == COLUMNS and table.name == "Performance of Queue"
    assert [values[0] for values in table.rows] == ["model", "transition", "place", "place", "place"]
    assert table.samples == [0.25] * 4                         # the per-run throughput, for Compare samples and Plot
    assert "seeds 2 to 5" in table.note and "4 of them ended in a dead marking" in table.note
    assert sum("run 3 (seed 5)" in note for note in explanation.notes) == 1
    assert [label for label, _value in explanation.shows] == ["Per run"]
    (_label, per_run) = explanation.shows[0]
    assert per_run.columns[:3] == ["run", "seed", "firings"] and len(per_run.rows) == 4

    waiting = performance(net, runs=2, steps=50, distribution="waiting time")
    assert waiting.samples == [2.0, 2.0] and "per-run waiting time" in waiting.note

    untimed = performance(dining_philosophers(3), runs=2, steps=20)
    assert untimed.samples == [1.0, 1.0] and "firings per step" in untimed.note


def test_performance_box_is_in_the_standard_library():
    from openprocess.flow.library import standard_library
    spec = standard_library().get("openprocess.flow.boxes.cpn.performance")
    assert spec.name == "Performance" and spec.group == "Coloured nets"
    assert [(s.name, s.default) for s in spec.settings] == [
        ("runs", 10), ("steps", 1000), ("warm_up", 0.0), ("seed", 0), ("distribution", "throughput")]
    assert spec.setting("seed").help.startswith("the first run's seed")
