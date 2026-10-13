"""Where OpenProcess's notation and algorithms come from.

Every algorithm and notation convention OpenProcess implements follows a published
source.  This module lists them once:

* :data:`REFERENCES` -- the works, as full citations;
* :data:`TOPICS` -- what OpenProcess does, grouped as in the app, with the works it
  follows and the module that implements it.

Every detail given (pages, DOIs, links) was checked against the publisher, a
bibliography database or the paper itself; anything that could not be
confirmed is left out rather than guessed.

The app shows this as *Help ▸ References*, and ``docs/references.md`` is
generated from it with ``python -m openprocess.references > docs/references.md``
(a test checks the two agree).  Module docstrings cite the same works at the
point of use, often down to the definition or section.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reference:
    key: str
    #: The citation, in Markdown (titles in italics).
    citation: str
    #: A DOI (without https://doi.org/) or a URL, when there is one.
    link: str = ""

    @property
    def url(self) -> str:
        if not self.link:
            return ""
        return self.link if self.link.startswith("http") else f"https://doi.org/{self.link}"


REFERENCES: tuple[Reference, ...] = (
    # -- process mining ---------------------------------------------------------------------
    Reference("aalst2016", "W.M.P. van der Aalst. *Process Mining: Data Science in Action*, "
              "2nd edition. Springer, 2016.", "10.1007/978-3-662-49851-4"),
    Reference("aalst2004", "W.M.P. van der Aalst, A.J.M.M. Weijters and L. Maruster. "
              "*Workflow Mining: Discovering Process Models from Event Logs*. IEEE "
              "Transactions on Knowledge and Data Engineering 16(9):1128–1142, 2004.",
              "10.1109/TKDE.2004.47"),
    Reference("aalst2013", "W.M.P. van der Aalst and B.F. van Dongen. *Discovering Petri "
              "Nets from Event Logs*. Transactions on Petri Nets and Other Models of "
              "Concurrency VII, LNCS 7480. Springer, 2013."),
    Reference("leemans2013", "S.J.J. Leemans, D. Fahland and W.M.P. van der Aalst. "
              "*Discovering Block-Structured Process Models from Event Logs – A Constructive "
              "Approach*. Application and Theory of Petri Nets and Concurrency (PETRI NETS "
              "2013), LNCS 7927, pp. 311–329. Springer, 2013.", "10.1007/978-3-642-38697-8_17"),
    Reference("leemans2014", "S.J.J. Leemans, D. Fahland and W.M.P. van der Aalst. "
              "*Discovering Block-Structured Process Models from Event Logs Containing "
              "Infrequent Behaviour*. Business Process Management Workshops (BPM 2013), "
              "LNBIP 171, pp. 66–78. Springer, 2014.", "10.1007/978-3-319-06257-0_6"),
    Reference("leemans2017", "S.J.J. Leemans. *Robust Process Mining with Guarantees*. PhD "
              "thesis, Eindhoven University of Technology, 2017. ISBN 978-90-386-4257-4."),
    Reference("weijters2003", "A.J.M.M. Weijters and W.M.P. van der Aalst. *Rediscovering "
              "Workflow Models from Event-Based Data using Little Thumb*. Integrated "
              "Computer-Aided Engineering 10(2):151–162, 2003."),
    Reference("weijters2006", "A.J.M.M. Weijters, W.M.P. van der Aalst and A.K. Alves de "
              "Medeiros. *Process Mining with the HeuristicsMiner Algorithm*. BETA Working "
              "Paper Series, WP 166, Eindhoven University of Technology, 2006."),
    Reference("aalst2010", "W.M.P. van der Aalst, V. Rubin, H.M.W. Verbeek, B.F. van Dongen, "
              "E. Kindler and C.W. Günther. *Process Mining: A Two-Step Approach to Balance "
              "Between Underfitting and Overfitting*. Software and Systems Modeling 9(1):87–111, "
              "2010.", "10.1007/s10270-008-0106-z"),
    Reference("leemans2019", "S.J.J. Leemans, E. Poppe and M.T. Wynn. *Directly "
              "Follows-Based Process Mining: Exploration & a Case Study*. International "
              "Conference on Process Mining (ICPM 2019), 2019."),
    Reference("song2007", "M. Song and W.M.P. van der Aalst. *Supporting Process Mining by "
              "Showing Events at a Glance*. 17th Annual Workshop on Information Technologies "
              "and Systems (WITS 2007), pp. 139–145, 2007."),
    # -- conformance --------------------------------------------------------------------------
    Reference("carmona2018", "J. Carmona, B. van Dongen, A. Solti and M. Weidlich. "
              "*Conformance Checking: Relating Processes and Models*. Springer, 2018.",
              "10.1007/978-3-319-99414-7"),
    Reference("rozinat2008", "A. Rozinat and W.M.P. van der Aalst. *Conformance Checking of "
              "Processes Based on Monitoring Real Behavior*. Information Systems 33(1):64–95, "
              "2008.", "10.1016/j.is.2007.07.001"),
    Reference("adriansyah2014", "A. Adriansyah. *Aligning Observed and Modeled Behavior*. "
              "PhD thesis, Eindhoven University of Technology, 2014."),
    Reference("munoz2010", "J. Muñoz-Gama and J. Carmona. *A Fresh Look at Precision in "
              "Process Conformance*. Business Process Management (BPM 2010), LNCS 6336, "
              "pp. 211–226. Springer, 2010.", "10.1007/978-3-642-15618-2_16"),
    Reference("berti2019", "A. Berti, S.J. van Zelst and W.M.P. van der Aalst. *Process "
              "Mining for Python (PM4Py): Bridging the Gap Between Process- and Data "
              "Science*. ICPM Demo Track 2019, CEUR-WS 2374, pp. 13–16, 2019.",
              "https://ceur-ws.org/Vol-2374/paper4.pdf"),
    # -- Petri nets ------------------------------------------------------------------------------
    Reference("aalst2000", "W.M.P. van der Aalst. *Workflow Verification: Finding "
              "Control-Flow Errors Using Petri-Net-Based Techniques*. Business Process "
              "Management: Models, Techniques, and Empirical Studies, LNCS 1806, "
              "pp. 161–183. Springer, 2000.", "10.1007/3-540-45594-9_11"),
    Reference("murata1989", "T. Murata. *Petri Nets: Properties, Analysis and Applications*. "
              "Proceedings of the IEEE 77(4):541–580, 1989."),
    Reference("karp1969", "R.M. Karp and R.E. Miller. *Parallel Program Schemata*. Journal of "
              "Computer and System Sciences 3(2):147–195, 1969."),
    Reference("desel1995", "J. Desel and J. Esparza. *Free Choice Petri Nets*. Cambridge "
              "Tracts in Theoretical Computer Science 40. Cambridge University Press, 1995."),
    Reference("cortadella1998", "J. Cortadella, M. Kishinevsky, L. Lavagno and A. Yakovlev. "
              "*Deriving Petri Nets from Finite Transition Systems*. IEEE Transactions on "
              "Computers 47(8):859–882, 1998."),
    Reference("ehrenfeucht1990", "A. Ehrenfeucht and G. Rozenberg. *Partial (Set) "
              "2-Structures. Part I: Basic Notions and the Representation Problem*, Acta "
              "Informatica 27(4):315–342, 1990; *Part II: State Spaces of Concurrent "
              "Systems*, Acta Informatica 27(4):343–368, 1990."),
    Reference("jensen2009", "K. Jensen and L.M. Kristensen. *Coloured Petri Nets: Modelling "
              "and Validation of Concurrent Systems*. Springer, 2009.", "10.1007/b95112"),
    # -- formats and drawing -------------------------------------------------------------------
    Reference("xes", "IEEE Standard for eXtensible Event Stream (XES) for Achieving "
              "Interoperability in Event Logs and Event Streams. IEEE Std 1849-2016 "
              "(revised as IEEE Std 1849-2023).", "https://xes-standard.org"),
    Reference("pnml", "ISO/IEC 15909-2:2011. *Systems and software engineering — High-level "
              "Petri nets — Part 2: Transfer format*."),
    Reference("cpntools", "CPN Tools and its model file format (`.cpn`)."),
    Reference("sugiyama1981", "K. Sugiyama, S. Tagawa and M. Toda. *Methods for Visual "
              "Understanding of Hierarchical System Structures*. IEEE Transactions on "
              "Systems, Man, and Cybernetics 11(2):109–125, 1981.",
              "10.1109/TSMC.1981.4308636"),
)

BY_KEY = {reference.key: reference for reference in REFERENCES}


@dataclass(frozen=True)
class Topic:
    name: str
    #: What OpenProcess does, and which convention it follows (Markdown).
    note: str
    #: Keys of :data:`REFERENCES`.
    sources: tuple[str, ...]
    #: Where it is implemented.
    module: str = ""


#: (section, topics) in the order the app and the docs show them.
TOPICS: tuple[tuple[str, tuple[Topic, ...]], ...] = (
    ("Notation", (
        Topic("Event logs as multisets of traces",
              "A log written as $L = [\\langle a,b,c \\rangle^3, \\langle a,c \\rangle]$: a "
              "multiset of traces, the exponent the number of cases. *Log from notation…* "
              "and `log.txt` files use it.", ("aalst2016",), "openprocess.mining.log"),
        Topic("Events, traces, classifiers and the XES attributes",
              "`concept:name`, `time:timestamp`, `org:resource` and `lifecycle:transition`, "
              "and classifiers that turn events into activity labels.", ("xes", "aalst2016"),
              "openprocess.mining.log, openprocess.mining.xes"),
        Topic("Petri nets, markings and WF-nets",
              "$N = (P, T, F)$, markings as multisets of places, a WF-net's source place $i$ "
              "and sink place $o$, and $[i]$ / $[o]$ for its initial and final marking.",
              ("aalst2000", "murata1989", "aalst2016"), "openprocess.mining.petrinet"),
        Topic("Footprint relations",
              "$a \\rightarrow_L b$, $a \\leftarrow_L b$, $a \\parallel_L b$ and $a \\#_L b$, "
              "derived from directly-follows $a >_L b$.", ("aalst2004", "aalst2016"),
              "openprocess.mining.footprint"),
        Topic("Process trees",
              "The operators $\\rightarrow$ (sequence), $\\times$ (exclusive choice), "
              "$\\wedge$ (parallel) and $\\circlearrowleft$ (loop), and $\\tau$ for a silent "
              "step.", ("leemans2013", "aalst2016"), "openprocess.mining.processtree"),
        Topic("Transition systems and regions",
              "$TS = (S, E, T, s_{in})$; a region entered, exited or not crossed by each "
              "event; generalised excitation regions $GER(e)$ and pre-/post-regions.",
              ("cortadella1998", "aalst2010", "aalst2016"), "openprocess.mining.transition_system, "
              "openprocess.mining.regions"),
        Topic("Alignments",
              "Moves $(a, a)$, $(a, \\gg)$ and $(\\gg, a)$, with the standard cost "
              "function.", ("carmona2018", "adriansyah2014"),
              "openprocess.mining.conformance.alignments"),
        Topic("Coloured Petri nets and CPN ML",
              "Colour sets, variables, arc inscriptions, guards and timed tokens as in CPN "
              "Tools.", ("jensen2009", "cpntools"), "openprocess.ml, openprocess.model, openprocess.sim"),
    )),
    ("Discovery", (
        Topic("α-algorithm", "The eight steps from $T_L$ to $\\alpha(L)$, with $X_L$ and "
              "$Y_L$.", ("aalst2004", "aalst2013", "aalst2016"), "openprocess.mining.discovery.alpha"),
        Topic("Inductive Miner (IM) and IM – infrequent (IMf)",
              "Cuts, base cases and fall-throughs on the directly-follows graph; the strict "
              "sequence cut; IMf filters infrequent behaviour.",
              ("leemans2013", "leemans2014", "leemans2017"),
              "openprocess.mining.discovery.inductive"),
        Topic("Heuristics Miner",
              "Dependency measures $a \\Rightarrow b$, length-one and length-two loops, and "
              "the thresholds.", ("weijters2003", "weijters2006", "aalst2016"),
              "openprocess.mining.discovery.heuristics"),
        Topic("State-based regions (two-phase discovery)",
              "A transition system from the log (prefix, postfix; sequence, multiset, set; a "
              "horizon), its minimal regions, and the synthesised net.",
              ("aalst2010", "aalst2013", "cortadella1998", "ehrenfeucht1990", "aalst2016"),
              "openprocess.mining.discovery.state_regions"),
        Topic("Directly-follows graphs (process maps)",
              "Frequencies and times on the edges, and simplification.", ("leemans2019",
                                                                          "aalst2016"),
              "openprocess.mining.dfg"),
        Topic("ILP Miner and heuristics nets (optional)",
              "Provided by PM4Py when it is installed.", ("berti2019",),
              "openprocess.mining.pm4py_bridge"),
    )),
    ("Conformance and quality", (
        Topic("Token-based replay", "Produced, consumed, missing and remaining tokens, and "
              "fitness.", ("rozinat2008", "aalst2016"), "openprocess.mining.conformance.token_replay"),
        Topic("Alignments", "Optimal alignments by search over the synchronous product.",
              ("carmona2018", "adriansyah2014"), "openprocess.mining.conformance.alignments"),
        Topic("Precision (escaping edges)", "ETConformance precision.", ("munoz2010",
                                                                         "carmona2018"),
              "openprocess.mining.conformance.quality"),
        Topic("Generalisation and simplicity", "Token-based generalisation and simplicity, "
              "computed as in PM4Py.", ("berti2019", "aalst2016"),
              "openprocess.mining.conformance.quality"),
    )),
    ("Petri net analysis", (
        Topic("Soundness of WF-nets", "Option to complete, proper completion, no dead "
              "transitions, and the short-circuited net $\\overline{N}$.",
              ("aalst2000",), "openprocess.mining.analysis"),
        Topic("Free-choice, well-structured, S-coverable",
              "Structural properties and the handles behind a problem.",
              ("aalst2000", "desel1995"), "openprocess.mining.structure"),
        Topic("Boundedness, liveness, deadlocks, reversibility",
              "Decided on the reachability graph, or the coverability graph for unbounded "
              "nets.", ("murata1989", "karp1969"), "openprocess.mining.analysis"),
        Topic("Incidence matrix and invariants", "P- and T-invariants.", ("murata1989",),
              "openprocess.mining.invariants"),
        Topic("CPN simulation and state spaces", "Bindings, enabling and the state space "
              "report.", ("jensen2009",), "openprocess.sim, openprocess.analysis"),
    )),
    ("Files and pictures", (
        Topic("XES event logs", "Reading and writing `.xes` and `.xes.gz`.", ("xes",),
              "openprocess.mining.xes"),
        Topic("PNML Petri nets", "Reading and writing `.pnml`.", ("pnml",), "openprocess.mining.pnml"),
        Topic("CPN Tools models", "Reading and writing `.cpn`.", ("cpntools", "jensen2009"),
              "openprocess.io"),
        Topic("Dotted chart", "Events as dots over time, per case or per resource.",
              ("song2007",), "openprocess.gui.studio.dotted_chart"),
        Topic("Layered graph layout", "How discovered models and graphs are drawn.",
              ("sugiyama1981",), "openprocess.mining.layout"),
    )),
)


def markdown() -> str:
    """The page as Markdown (``docs/references.md``)."""
    lines = ["<!-- Generated by `python -m openprocess.references > docs/references.md`. "
             "Do not edit by hand. -->", "", "# References", "",
             "Where OpenProcess's notation and algorithms come from. Each topic names the works "
             "it follows and the module that implements it; the module's docstring cites "
             "the work again at the point of use. The definitions of every property the "
             "analyses report are in [definitions.md](definitions.md).", ""]
    numbers = {reference.key: index for index, reference in enumerate(REFERENCES, 1)}
    for section, topics in TOPICS:
        lines += [f"## {section}", ""]
        for topic in topics:
            cited = ", ".join(f"[{numbers[key]}]" for key in topic.sources)
            where = f" *(`{topic.module}`)*" if topic.module else ""
            lines.append(f"- **{topic.name}** — {topic.note} {cited}{where}")
        lines.append("")
    lines += ["## Works cited", ""]
    for index, reference in enumerate(REFERENCES, 1):
        link = f" <{reference.url}>" if reference.url else ""
        lines.append(f"{index}. {reference.citation}{link}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(markdown(), end="")


def topics_for_module(module: str) -> list[Topic]:
    """The topics implemented in ``module`` (``openprocess.mining.discovery.alpha``),
    for showing an algorithm's sources next to its code."""
    found = []
    for _section, topics in TOPICS:
        for topic in topics:
            names = [m.strip() for m in topic.module.split(",") if m.strip()]
            if any(module == m or module.startswith(m + ".") for m in names):
                found.append(topic)
    return found


def reference(key: str) -> Reference | None:
    """The work with ``key``, or None."""
    return next((r for r in REFERENCES if r.key == key), None)
