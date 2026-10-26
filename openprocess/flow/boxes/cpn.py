"""Coloured net boxes: play a CPN out many times, and compute its state space."""

from __future__ import annotations

from typing import Literal

from ... import flow
from ...analysis.state_space import StateSpace
from ...mining.log import EventLog
from ...model.net import CPNet
from ...sim.export import case_variables, simulation_to_log
from ...sim.performance import analyse_performance
from ...sim.simulator import DeadMarkingError, Simulator
from ..box import box
from ..types import Scores, Table, Text


@box(name="Simulate CPN", group="Coloured nets")
def simulate_cpn(net: CPNet, steps: int = 500, case_variable: str = "", seed: int = 0,
                 unit: Literal["seconds", "minutes", "hours", "days"] = "minutes") -> EventLog:
    """Runs the simulator for *steps* firings and turns the firing history
    into an event log: one case per value of *case_variable* (the variable
    that identifies a case in the inscriptions, e.g. ``p`` or ``order``),
    one event per firing, the model time as the timestamp.

    case_variable: the variable whose value is the case id (empty: the first one found)
    """
    simulator = Simulator(net, seed=seed)
    try:
        fired = simulator.run(steps)
    except DeadMarkingError:
        fired = simulator.step_count
    variable = case_variable or next(iter(case_variables(net)), "")
    if not variable:
        raise ValueError("No variable to use as the case id: set case_variable")
    log = simulation_to_log(net, simulator.log, variable, unit)
    flow.note(f"{fired} firings, model time {simulator.clock}, case variable {variable}: "
              f"{len(log)} cases, {log.event_count} events")
    return log


@box(name="Performance", group="Coloured nets")
def performance(net: CPNet, runs: int = 10, steps: int = 1000, warm_up: float = 0.0, seed: int = 0,
                distribution: Literal["throughput", "waiting time"] = "throughput") -> Table:
    """Runs a timed coloured net *runs* times (seeds *seed*, *seed* + 1, …),
    each for *steps* firings or until it is dead, and reports one row per
    transition (firings, throughput in firings per time unit, utilisation:
    the share of the model time during which one of its firings was still in
    progress) and one per place (the waiting time of its tokens from their
    time stamp to their consumption, the time-averaged number of tokens, and
    occupancy: the share of the time it was not empty), after the first
    *warm_up* time units of every run, averaged over the runs. An untimed
    net is measured in steps instead. The samples behind the table are one
    number per run, for Compare samples and Plot.

    runs: how many seeded runs to average over
    steps: firings per run (a run stops earlier when the model is dead)
    warm_up: model time left out at the start of every run (firings, for an untimed net)
    seed: the first run's seed; run k uses seed + k
    distribution: what the samples hold per run: the model's throughput, or its mean waiting time
    """
    result = analyse_performance(net, runs=runs, steps=steps, warm_up=warm_up, seed=seed)
    for note in result.notes:
        flow.note(note)
    for run in result.runs:
        flow.note(result.summary(run))
    flow.show(Table("Per run", ["run", "seed", "firings", "end", "stopped", "throughput", "mean waiting"],
                    [[k, run.seed, run.firings, run.end, "dead" if run.dead else "steps",
                      None if run.throughput is None else round(run.throughput, 4),
                      None if run.waiting_mean is None else round(run.waiting_mean, 4)]
                     for k, run in enumerate(result.runs)]), "Per run")
    samples = result.samples(distribution)
    dead = sum(run.dead for run in result.runs)
    note = (f"{runs} runs of up to {steps} firings, seeds {seed} to {seed + runs - 1}, the first {warm_up:g} "
            f"{'time units' if result.timed else 'firings'} of each left out"
            f"{f'; {dead} of them ended in a dead marking' if dead else ''}. "
            f"Throughput is firings per {result.unit}; waiting time runs from a token's time stamp to its "
            f"consumption; utilisation is the share of the time a firing of the transition was in progress; "
            f"occupancy the share of the time the place was not empty. "
            f"The samples are the per-run {distribution} of the whole model.")
    return Table(f"Performance of {net.name}", list(result.columns), result.rows(),
                 samples=samples or None, note=note)


@box(name="State space", group="Coloured nets", heavy=True)
def state_space(net: CPNet, max_nodes: int = 10000) -> Scores:
    """The reachability graph of a coloured net and its report: dead and
    home markings, bounds, dead and live transitions (as CPN Tools reports
    them). The full report is in the notes."""
    space = StateSpace(net).generate(max_nodes=max_nodes)
    report = space.report()
    flow.show(Text(report, "State space report"), "Report")
    dead = space.dead_markings()
    return Scores(net.name, {"nodes": space.node_count, "arcs": space.arc_count,
                             "dead markings": len(dead), "home markings": len(space.home_markings()),
                             "dead transitions": len(space.dead_transitions()),
                             "complete": "No (stopped at the limit)" if space.unexplored_count else "Yes"})
