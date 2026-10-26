"""Discovery with state-based regions: log → transition system → Petri net.

The standard example of *two-phase* discovery (van der Aalst, *Process
Mining*, Section 7.4):

1. build a **transition system** from the log with a state function
   (:func:`~openprocess.mining.transition_system.transition_system_from_log`):
   the prefix, postfix or both of every event, as a sequence, multiset or
   set, over a horizon of the last ``k`` events or all of them;
2. find its **regions** and **minimal regions**
   (:func:`~openprocess.mining.regions.analyse_regions`), and check whether it is
   **elementary** (state separation and forward closure);
3. **synthesise** a Petri net with one place per minimal region
   (:func:`~openprocess.mining.regions.synthesise`).  For an elementary
   transition system the net's reachability graph is isomorphic to it.

The abstraction is the knob: a coarser one (a set, a short horizon) merges
states and so generalises, a finer one (the full sequence) only allows the
log.  The result keeps every step so the app can show the derivation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..log import SimpleLog
from ..petrinet import PetriNet
from ..regions import RegionAnalysis, Synthesis, analyse_regions, synthesise
from ..transition_system import TransitionSystem, transition_system_from_log


@dataclass
class RegionResult:
    ts: TransitionSystem
    analysis: RegionAnalysis
    synthesis: Synthesis
    warnings: list[str] = field(default_factory=list)

    @property
    def net(self) -> PetriNet | None:
        return self.synthesis.net


def region_result(ts: TransitionSystem) -> RegionResult:
    """Regions and synthesis for a transition system already built (or typed)."""
    analysis = analyse_regions(ts)
    synthesis = synthesise(ts, analysis)
    return RegionResult(ts, analysis, synthesis, list(synthesis.warnings))


def region_miner(log: SimpleLog, direction: str = "prefix", representation: str = "set",
                 horizon: int | None = None) -> RegionResult:
    """Discover a Petri net from ``log`` through state-based regions.

    Raises ``ValueError`` when no net can be made (several initial states, or
    a transition system too large to search for regions).
    """
    if not any(n > 0 for n in log.values()):
        raise ValueError("The log is empty: there is nothing to discover a model from.")
    ts = transition_system_from_log(log, direction, representation, horizon)
    result = region_result(ts)
    if result.net is None:
        raise ValueError(" ".join(result.warnings) or "No net could be synthesised.")
    return result
