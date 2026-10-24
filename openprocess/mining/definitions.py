"""Mathematical definitions of every property the analyses report.

This is the single source for

* ``docs/definitions.md`` -- generated from this module with
  ``python -m openprocess.mining.definitions > docs/definitions.md`` (a test checks
  that the file is up to date), where GitHub renders the maths; and
* the pop-ups in the app: hover over or click a property in an Analysis tab
  to see its definition, typeset by :mod:`openprocess.gui.studio.mathtext`.

Formulas are written in a small subset of LaTeX that both GitHub (MathJax)
and the app's renderer understand; :mod:`openprocess.gui.studio.mathtext` lists it.  Braces are
written ``\\lbrace`` / ``\\rbrace`` because GitHub's Markdown would otherwise
eat the backslash of ``\\{``.

The definitions follow W.M.P. van der Aalst, *Workflow Verification: Finding
Control-Flow Errors Using Petri-Net-Based Techniques* (2000) and *Process
Mining: Data Science in Action* (2016), with the notation of the course.
"""

from __future__ import annotations

from dataclasses import dataclass, field

AALST_2000 = ("W.M.P. van der Aalst, *Workflow Verification: Finding Control-Flow Errors "
              "Using Petri-Net-Based Techniques* (2000)")
AALST_2016 = "W.M.P. van der Aalst, *Process Mining: Data Science in Action* (2016)"


@dataclass(frozen=True)
class Definition:
    #: Identifier used by the app (``"sound"``, ``"live"``, ...).
    key: str
    #: Heading, e.g. "Sound".
    name: str
    #: One or two sentences in words: what it means, why it matters.
    summary: str
    #: Display formulas, one per line, in the LaTeX subset.
    formulas: tuple[str, ...]
    #: Extra remarks in words (may contain inline maths between ``$``).
    notes: tuple[str, ...] = ()
    #: Where it comes from, e.g. "van der Aalst 2000, Definition 12".
    source: str = ""
    #: The section it belongs to (see :data:`SECTIONS`).
    section: str = ""
    #: Keys of definitions this one builds on.
    uses: tuple[str, ...] = field(default=())


SECTIONS = [
    ("notation", "Notation",
     "The basic vocabulary every other definition is written in."),
    ("behaviour", "Behavioural properties",
     "Properties of a *marked* Petri net $(N, M_0)$: they depend on the initial "
     "marking and are decided on the reachability (or coverability) graph."),
    ("workflow", "Workflow nets and soundness",
     "A WF-net models the life cycle of one case, from its start place $i$ to its end "
     "place $o$."),
    ("structure", "Structural properties",
     "Properties of the drawing alone — places, transitions and arcs, no markings. They "
     "are quick to check and point at the construct behind a problem."),
    ("invariants", "Invariants",
     "Linear algebra on the drawing: what firing does to each place, written as a "
     "matrix, and the weightings and firing counts it leaves unchanged. Like the "
     "structural properties they need no state space."),
    ("footprint", "Footprints",
     "The ordering relations of the α-algorithm, for an event log or for the behaviour "
     "of a net."),
    ("regions", "Transition systems and regions",
     "Two-phase discovery: an event log becomes a transition system, and the transition "
     "system's regions become the places of a Petri net. Throughout, "
     "$TS = (S, E, T, s_{in})$ is a transition system."),
]

MURATA_1989 = ("T. Murata, *Petri Nets: Properties, Analysis and Applications*, "
               "Proceedings of the IEEE 77(4) (1989)")
CORTADELLA_1998 = ("J. Cortadella, M. Kishinevsky, L. Lavagno and A. Yakovlev, *Deriving Petri "
                   "Nets from Finite Transition Systems*, IEEE Transactions on Computers 47(8) "
                   "(1998)")


DEFINITIONS: tuple[Definition, ...] = (
    # -- notation ----------------------------------------------------------------------
    Definition(
        "petri_net", "Petri net",
        "A bipartite graph of places (circles, conditions) and transitions (boxes, "
        "actions), connected by arcs.",
        (r"N = (P, T, F)",
         r"P \cap T = \emptyset, \quad F \subseteq (P \times T) \cup (T \times P)"),
        ("Plain nets in the course have arc weight 1. The editor also allows a weight "
         "$W(f) \\in \\mathbb{N}$ on an arc $f \\in F$; a transition then needs, and "
         "produces, that many tokens.",),
        f"{AALST_2000}, Definition 1", "notation"),
    Definition(
        "preset", "Pre-set and post-set",
        "The input nodes and the output nodes of a node.",
        (r"\bullet x = \lbrace y \mid (y, x) \in F \rbrace",
         r"x \bullet = \lbrace y \mid (x, y) \in F \rbrace"),
        ("For a transition $t$, $\\bullet t$ are its input places and $t \\bullet$ its "
         "output places.",),
        f"{AALST_2000}, Section 3", "notation", ("petri_net",)),
    Definition(
        "marking", "Marking",
        "The state of a net: how many tokens each place holds.",
        (r"M \colon P \to \mathbb{N}",
         r"M_1 \geq M_2 \iff \forall p \in P \colon M_1(p) \geq M_2(p)"),
        ("Markings are written as multisets: $[i]$ is one token in $i$, "
         "$[p_1, 2 p_2]$ is one token in $p_1$ and two in $p_2$.",),
        f"{AALST_2000}, Section 3", "notation", ("petri_net",)),
    Definition(
        "firing", "Enabling and firing",
        "A transition is enabled when every input place has a token. Firing it takes one "
        "token from each input place and puts one in each output place.",
        (r"t \text{ enabled in } M \iff \forall p \in \bullet t \colon M(p) \geq 1",
         r"M \xrightarrow{t} M' \iff t \text{ enabled in } M \land "
         r"M' = M - \bullet t + t \bullet"),
        (),
        f"{AALST_2000}, Section 3", "notation", ("marking", "preset")),
    Definition(
        "reachability", "Reachable markings",
        "The markings a net can get to by firing transitions, one after the other.",
        (r"M \xrightarrow{\sigma} M' \iff \sigma = \langle t_1, \ldots, t_n \rangle "
         r"\land M \xrightarrow{t_1} M_1 \xrightarrow{t_2} \cdots \xrightarrow{t_n} M'",
         r"M \xrightarrow{*} M' \iff \exists \sigma \colon M \xrightarrow{\sigma} M'",
         r"R(N, M_0) = \lbrace M \mid M_0 \xrightarrow{*} M \rbrace"),
        ("The empty sequence is allowed, so $M \\xrightarrow{*} M$ always holds. The "
         "reachability graph has the markings in $R(N, M_0)$ as nodes and an edge for "
         "every firing between them.",),
        f"{AALST_2000}, Section 3", "notation", ("firing",)),

    # -- behaviour ---------------------------------------------------------------------
    Definition(
        "bounded", "Bounded",
        "No place can ever hold more than some fixed number of tokens. Then the "
        "reachability graph is finite.",
        (r"(N, M_0) \text{ is } k\text{-bounded} \iff \forall M \in R(N, M_0) \; "
         r"\forall p \in P \colon M(p) \leq k",
         r"(N, M_0) \text{ is bounded} \iff \exists k \in \mathbb{N} \colon (N, M_0) "
         r"\text{ is } k\text{-bounded}"),
        ("If the net is unbounded, the app builds the coverability graph instead, where "
         "$\\omega$ marks a place that can hold arbitrarily many tokens.",),
        f"{AALST_2000}, Definition 3", "behaviour", ("reachability",)),
    Definition(
        "safe", "Safe",
        "Every place holds at most one token, so a place is a condition that is either "
        "true or false.",
        (r"(N, M_0) \text{ is safe} \iff \forall M \in R(N, M_0) \; \forall p \in P "
         r"\colon M(p) \leq 1",),
        ("Safe is the same as 1-bounded. A sound free-choice WF-net and a sound "
         "well-structured WF-net are always safe.",),
        f"{AALST_2000}, Definition 3, Lemmas 1 and 3", "behaviour", ("bounded",)),
    Definition(
        "terminating", "Terminating",
        "Every run ends: there is no infinite firing sequence.",
        (r"(N, M_0) \text{ is terminating} \iff \neg \exists \text{ infinite } "
         r"\sigma \colon M_0 \xrightarrow{\sigma}",),
        ("On a finite reachability graph: terminating iff the graph has no cycle. An "
         "unbounded net is never terminating, and a terminating net always has a dead "
         "marking, so it is not live.",),
        "course lecture on Petri net properties", "behaviour", ("reachability",)),
    Definition(
        "deadlock_free", "Deadlock-free",
        "No reachable marking is dead, i.e. the net can never get stuck with nothing "
        "enabled.",
        (r"M \text{ is dead} \iff \forall t \in T \colon t \text{ is not enabled in } M",
         r"(N, M_0) \text{ is deadlock-free} \iff \forall M \in R(N, M_0) \colon "
         r"M \text{ is not dead}"),
        ("A WF-net always stops in $[o]$, which is a dead marking, so for WF-nets the "
         "question is asked about the short-circuited net $\\overline{N}$, where $[o]$ "
         "enables $t^*$.",
         "Deadlock-free is weaker than live: a net can keep firing in a loop while some "
         "transition is dead. Live implies deadlock-free (when $T \\neq \\emptyset$), "
         "not the other way round."),
        "course lecture on Petri net properties", "behaviour",
        ("reachability", "short_circuit")),
    Definition(
        "dead_transition", "Dead transition",
        "A transition that can never fire, whatever happens first.",
        (r"t \text{ is dead in } (N, M_0) \iff \neg \exists M \in R(N, M_0) \colon "
         r"t \text{ enabled in } M",),
        (),
        f"{AALST_2000}, Section 5", "behaviour", ("reachability",)),
    Definition(
        "live", "Live",
        "Whatever has happened so far, every transition can still fire again later.",
        (r"(N, M_0) \text{ is live} \iff \forall t \in T \; \forall M \in R(N, M_0) \; "
         r"\exists M' \in R(N, M) \colon t \text{ enabled in } M'",),
        ("On a finite reachability graph: $t$ is live iff it can fire inside every "
         "*bottom* strongly connected component (one with no edge leaving it), because "
         "every run ends up trapped in one of those.",),
        f"{AALST_2000}, Definition 2", "behaviour", ("reachability",)),
    Definition(
        "reversible", "Reversible",
        "From every reachable marking the initial marking can be reached again.",
        (r"(N, M_0) \text{ is reversible} \iff \forall M \in R(N, M_0) \colon "
         r"M \xrightarrow{*} M_0",),
        (),
        "course lecture on Petri net properties", "behaviour", ("reachability",)),

    # -- workflow nets and soundness ---------------------------------------------------
    Definition(
        "wf_net", "WF-net",
        "A workflow net: one start place $i$, one end place $o$, and nothing that is "
        "not on the way from $i$ to $o$.",
        (r"\text{(i)} \; \exists! \, i \in P \colon \bullet i = \emptyset",
         r"\text{(ii)} \; \exists! \, o \in P \colon o \bullet = \emptyset",
         r"\text{(iii)} \; \forall x \in P \cup T \colon x \text{ is on a path from } i "
         r"\text{ to } o"),
        ("A case starts as the marking $[i]$ and should end as $[o]$.",),
        f"{AALST_2000}, Definition 11", "workflow", ("preset",)),
    Definition(
        "short_circuit", "Short-circuited net",
        "The WF-net with one extra transition $t^*$ that takes the token from $o$ back "
        "to $i$, so that finishing a case starts the next one.",
        (r"\overline{N} = (P, \; T \cup \lbrace t^* \rbrace, \; F \cup "
         r"\lbrace (o, t^*), (t^*, i) \rbrace)",),
        ("$N$ is a WF-net exactly when $\\overline{N}$ is strongly connected (every node "
         "can reach every other).",),
        f"{AALST_2000}, Section 5", "workflow", ("wf_net",)),
    Definition(
        "option_to_complete", "(i) Option to complete",
        "From every marking a case can reach, it is still possible to finish.",
        (r"\forall M \colon [i] \xrightarrow{*} M \Rightarrow M \xrightarrow{*} [o]",),
        ("Violated by a deadlock (the case is stuck) or a livelock (the case can only "
         "loop forever).",),
        f"{AALST_2000}, Definition 12 (i)", "workflow", ("reachability", "wf_net")),
    Definition(
        "proper_completion", "(ii) Proper completion",
        "The moment a token reaches $o$, every other place is empty: nothing is left "
        "behind.",
        (r"\forall M \colon [i] \xrightarrow{*} M \land M \geq [o] \Rightarrow M = [o]",),
        (),
        f"{AALST_2000}, Definition 12 (ii)", "workflow", ("reachability", "wf_net")),
    Definition(
        "no_dead_transitions", "(iii) No dead transitions",
        "Every transition can fire in some run of the case.",
        (r"\forall t \in T \; \exists M, M' \colon [i] \xrightarrow{*} M "
         r"\xrightarrow{t} M'",),
        (),
        f"{AALST_2000}, Definition 12 (iii)", "workflow",
        ("reachability", "dead_transition")),
    Definition(
        "sound", "Sound",
        "A WF-net is sound when every case can always finish, finishes cleanly, and "
        "every transition is useful.",
        (r"N \text{ is sound} \iff \text{(i) option to complete} \land "
         r"\text{(ii) proper completion} \land \text{(iii) no dead transitions}",),
        ("Checked on the reachability graph from $[i]$. A sound WF-net is always "
         "bounded, so an unbounded net is unsound straight away.",),
        f"{AALST_2000}, Definition 12", "workflow",
        ("option_to_complete", "proper_completion", "no_dead_transitions")),
    Definition(
        "soundness_theorem", "Soundness theorem",
        "Soundness is the same as liveness plus boundedness of the short-circuited net.",
        (r"N \text{ is sound} \iff (\overline{N}, [i]) \text{ is live and bounded}",),
        ("Why: $t^*$ can only fire when a case has finished, so *live* includes "
         "\"every case can finish\" (i) and \"no transition is dead\" (iii). If a case "
         "could finish with tokens left behind, $t^*$ would start the next case on top "
         "of them and tokens would pile up — so *bounded* rules out improper "
         "completion (ii).",),
        f"{AALST_2000}, Theorem 1", "workflow",
        ("sound", "short_circuit", "live", "bounded")),

    # -- structure ---------------------------------------------------------------------
    Definition(
        "free_choice", "Free-choice",
        "Transitions that share an input place have exactly the same input places, so "
        "every choice is free: it never depends on what happened in a parallel branch.",
        (r"\forall t_1, t_2 \in T \colon \bullet t_1 \cap \bullet t_2 \neq \emptyset "
         r"\Rightarrow \bullet t_1 = \bullet t_2",),
        ("Soundness of a free-choice WF-net can be decided in polynomial time, and a "
         "sound free-choice WF-net is safe.",),
        f"{AALST_2000}, Definition 7, Corollary 1, Lemma 1", "structure", ("preset",)),
    Definition(
        "elementary_path", "Elementary path",
        "A path along the arcs that visits no node twice.",
        (r"C = \langle n_1, \ldots, n_k \rangle \text{ with } (n_j, n_{j+1}) \in F "
         r"\text{ for } 1 \leq j < k",
         r"C \text{ is elementary} \iff \forall j, l \colon j \neq l \Rightarrow "
         r"n_j \neq n_l",
         r"\alpha(C) = \lbrace n_1, \ldots, n_k \rbrace"),
        (),
        f"{AALST_2000}, Definition 5", "structure", ("petri_net",)),
    Definition(
        "well_handled", "Well-handled",
        "No place and transition are joined by two separate routes. Such a pair is a "
        "*handle*: a PT-handle is a choice that is later synchronised, a TP-handle is "
        "parallel branches that are later merged as alternatives.",
        (r"\forall x, y \text{ (one a place, the other a transition)} \; "
         r"\forall \text{ elementary paths } C_1, C_2 \text{ from } x \text{ to } y "
         r"\colon",
         r"\alpha(C_1) \cap \alpha(C_2) = \lbrace x, y \rbrace \Rightarrow C_1 = C_2"),
        ("The app finds handles as two internally disjoint paths from $x$ to $y$ "
         "(Menger's theorem: a max-flow problem with capacity 1 on every node).",),
        f"{AALST_2000}, Definition 13", "structure", ("elementary_path",)),
    Definition(
        "well_structured", "Well-structured",
        "Every AND-split is closed by an AND-join and every OR-split by an OR-join, "
        "also around the loop through $t^*$.",
        (r"N \text{ is well-structured} \iff \overline{N} \text{ is well-handled}",),
        ("Soundness of a well-structured WF-net can be decided in polynomial time, and a "
         "sound well-structured WF-net is safe.",),
        f"{AALST_2000}, Definition 14, Corollary 2, Lemma 3", "structure",
        ("well_handled", "short_circuit")),
    Definition(
        "state_machine", "State machine",
        "Every transition has exactly one input and one output place, so a single "
        "token moves around.",
        (r"\forall t \in T \colon |\bullet t| = |t \bullet| = 1",),
        (),
        f"{AALST_2000}, Definition 8", "structure", ("preset",)),
    Definition(
        "s_component", "S-component",
        "A part of the net that behaves like one token moving around: a strongly "
        "connected state machine that keeps every arc of its places.",
        (r"N_s = (P_s, T_s, F_s) \text{ with } P_s \subseteq P, \; T_s \subseteq T, \; "
         r"F_s \subseteq F",
         r"N_s \text{ is strongly connected and a state machine}",
         r"\forall q \in P_s \; \forall t \in T \colon ((q, t) \in F \Rightarrow "
         r"(q, t) \in F_s) \land ((t, q) \in F \Rightarrow (t, q) \in F_s)"),
        (),
        f"{AALST_2000}, Definition 9", "structure", ("state_machine",)),
    Definition(
        "s_coverable", "S-coverable",
        "Every node lies in some S-component of the short-circuited net: the net is "
        "a set of threads (one \"document\" each) that synchronise on shared tasks.",
        (r"N \text{ is S-coverable} \iff \forall x \in P \cup T \cup \lbrace t^* "
         r"\rbrace \; \exists \text{ S-component } N_s \text{ of } \overline{N} \colon "
         r"x \in N_s",),
        ("Sound free-choice and sound well-structured WF-nets are S-coverable, and an "
         "S-coverable WF-net is safe. Unsound nets are often not S-coverable, so a "
         "node outside every S-component deserves a close look.",),
        f"{AALST_2000}, Definitions 10 and 16, Corollaries 3 and 4", "structure",
        ("s_component", "short_circuit")),
    Definition(
        "start_end_rule", "Start and end rule",
        "A quick necessary condition for soundness: a transition that takes from $i$ "
        "takes only from $i$, and one that puts into $o$ puts only into $o$.",
        (r"N \text{ is sound} \Rightarrow \forall t \in T \colon (i \in \bullet t "
         r"\Rightarrow \bullet t = \lbrace i \rbrace) \land (o \in t \bullet "
         r"\Rightarrow t \bullet = \lbrace o \rbrace)",),
        ("Otherwise $t$ needs $i$ (or $o$) marked together with another place, which "
         "never happens in a sound net, so $t$ is dead.",),
        f"{AALST_2000}, Lemma 4", "structure", ("sound",)),

    # -- invariants --------------------------------------------------------------------
    Definition(
        "incidence_matrix", "Incidence matrix",
        "What firing each transition does to each place: the tokens it puts in minus "
        "the tokens it takes out.",
        (r"C(p, t) = W(t, p) - W(p, t) \quad \text{for } p \in P, \; t \in T",
         r"M \xrightarrow{\sigma} M' \Rightarrow M' = M + C \cdot \overline{\sigma}"),
        ("$W(x, y)$ is the weight of the arc from $x$ to $y$, and $0$ if there is none. "
         r"$\overline{\sigma}$, the *Parikh vector* of $\sigma$, counts how often each "
         r"transition occurs in $\sigma$. This *marking equation* is only a necessary "
         "condition: it ignores the order of the firings.",),
        f"{MURATA_1989}, Section VII", "invariants", ("firing",)),
    Definition(
        "p_invariant", "P-invariant",
        "A weighting of the places whose weighted token count never changes, "
        "whatever fires: a conservation law of the net.",
        (r"y \colon P \to \mathbb{N}, \quad y \neq 0, \quad y \cdot C = 0",
         r"M_0 \xrightarrow{*} M \Rightarrow y \cdot M = y \cdot M_0"),
        ("The app lists the *minimal* ones (no other uses a strict subset of their "
         "places); every other invariant with non-negative weights is a sum of "
         "multiples of these.",
         "*Covered by P-invariants* (every place has a positive weight in some "
         "P-invariant) implies that the net is bounded from every initial marking. In "
         "a WF-net, $i + p + o = 1$ says: one token travels through these places."),
        f"{MURATA_1989}, Section VII-A", "invariants", ("incidence_matrix", "bounded")),
    Definition(
        "t_invariant", "T-invariant",
        "How often to fire each transition to end up in the marking you started from: "
        "a cycle of the behaviour.",
        (r"x \colon T \to \mathbb{N}, \quad x \neq 0, \quad C \cdot x = 0",
         r"M \xrightarrow{\sigma} M' \land \overline{\sigma} = x \Rightarrow M' = M"),
        ("A net that is live and bounded is *covered by T-invariants*: every transition "
         "occurs in one. By the soundness theorem, a sound WF-net's short-circuited net "
         r"$\overline{N}$ is live and bounded, so a transition in no T-invariant of "
         r"$\overline{N}$ proves that the WF-net is not sound. (Covered does not imply "
         "sound.)",),
        f"{MURATA_1989}, Section VII-A", "invariants",
        ("incidence_matrix", "live", "short_circuit", "soundness_theorem")),

    # -- footprint ---------------------------------------------------------------------
    Definition(
        "footprint", "Footprint",
        "The ordering relations between activities: which can directly follow which.",
        (r"a >_L b \iff \exists \sigma = \langle t_1, \ldots, t_n \rangle \in L \; "
         r"\exists j \colon t_j = a \land t_{j+1} = b",
         r"a \rightarrow_L b \iff a >_L b \land \neg (b >_L a)",
         r"a \leftarrow_L b \iff b \rightarrow_L a",
         r"a \parallel_L b \iff a >_L b \land b >_L a",
         r"a \#_L b \iff \neg (a >_L b) \land \neg (b >_L a)"),
        ("For a net, $L$ is the set of its complete firing sequences (visible labels "
         "only). Comparing a log's footprint with a model's is a simple conformance "
         "check.",),
        f"{AALST_2016}, Section 6.2", "footprint"),

    # -- transition systems and regions ------------------------------------------------
    Definition(
        "transition_system", "Transition system",
        "States, and arrows between them labelled with events. The simplest model of a "
        "process: it has no concurrency, so two events in either order make a diamond.",
        (r"TS = (S, E, T, s_{in})",
         r"T \subseteq S \times E \times S, \quad s_{in} \in S"),
        ("From an event log, the *state function* decides the state an event happens in: "
         "it looks at the events before it (the prefix), after it (the postfix) or both, "
         "keeps the last $k$ of them (the horizon) or all, and forgets their order (a "
         "multiset) or also their frequency (a set). Every trace then walks from state to "
         "state, one event at a time: with the prefix, $s_k = "
         "\\text{rep}(\\text{last}_h \\langle e_1, \\ldots, e_k \\rangle)$ and "
         "$(s_{k-1}, e_k, s_k) \\in T$.",
         "A coarser abstraction (a set, a short horizon) merges states and so generalises; "
         "the full sequence gives a tree that allows exactly the log."),
        f"{AALST_2016}, Section 7.4.1", "regions"),
    Definition(
        "region", "Region",
        "A set of states that every event treats the same way each time: all its arrows "
        "enter the set, all exit it, or none crosses it.",
        (r"R \subseteq S \text{ is a region} \iff \forall e \in E \colon "
         r"\text{enter}(e, R) \lor \text{exit}(e, R) \lor \text{nocross}(e, R)",
         r"\text{enter}(e, R) \iff \forall (s, e, s') \in T \colon s \notin R \land s' \in R",
         r"\text{exit}(e, R) \iff \forall (s, e, s') \in T \colon s \in R \land s' \notin R",
         r"\text{nocross}(e, R) \iff \forall (s, e, s') \in T \colon "
         r"(s \in R \Leftrightarrow s' \in R)"),
        ("$\\emptyset$ and $S$ are the *trivial* regions. The complement $S \\setminus R$ of "
         "a region is a region too, with enter and exit swapped.",
         "A region is a place in disguise: it is marked exactly in its states, an event "
         "that enters it puts a token in, one that exits it takes the token out."),
        f"{CORTADELLA_1998}; {AALST_2016}, Section 7.4.2", "regions", ("transition_system",)),
    Definition(
        "minimal_region", "Minimal region",
        "A non-empty region with no smaller non-empty region inside it. The synthesised "
        "net has one place per minimal region.",
        (r"R \text{ is minimal} \iff R \neq \emptyset \land \neg \exists \text{ region } "
         r"R' \colon \emptyset \neq R' \subsetneq R",),
        (),
        CORTADELLA_1998, "regions", ("region",)),
    Definition(
        "pre_region", "Pre-region and post-region",
        "The regions an event leaves (its input places) and the regions it enters (its "
        "output places).",
        (r"R \in \text{pre}(e) \iff \text{exit}(e, R)",
         r"R \in \text{post}(e) \iff \text{enter}(e, R)"),
        ("Exam questions often ask for the *minimal* pre-regions: the minimal regions that "
         "are pre-regions, i.e. the input places of $e$ in the synthesised net.",),
        CORTADELLA_1998, "regions", ("region",)),
    Definition(
        "ger", "Generalised excitation region",
        "The states in which an event is enabled.",
        (r"\text{GER}(e) = \lbrace s \in S \mid \exists s' \colon (s, e, s') \in T \rbrace",),
        ("Every pre-region of $e$ contains $\\text{GER}(e)$: $e$ exits it, so it starts "
         "inside.",),
        CORTADELLA_1998, "regions", ("transition_system",)),
    Definition(
        "state_separation", "State separation",
        "Any two states are told apart by some region: in the net they will be different "
        "markings.",
        (r"\forall s_1, s_2 \in S \colon s_1 \neq s_2 \Rightarrow \exists \text{ region } "
         r"R \colon s_1 \in R \land s_2 \notin R",),
        ("Since the complement of a region is a region, it does not matter which of the two "
         "is inside. Two states with the same event to the same state, as in "
         "$s_1 \\xrightarrow{e} s$ and $s_2 \\xrightarrow{e} s$, can never be separated: a "
         "region with $s_1$ but not $s_2$ would have one $e$-arrow cross it and the other "
         "not.",),
        CORTADELLA_1998, "regions", ("region",)),
    Definition(
        "forward_closure", "Forward closure",
        "An event's input places are all marked only where the event really is enabled.",
        (r"\forall e \in E \colon \bigcap_{R \in \text{pre}(e)} R = \text{GER}(e)",),
        ("Also called *event/state separation*. When it fails, the net would enable $e$ in "
         "a state where the transition system does not. With no pre-region at all, the "
         "intersection is all of $S$.",),
        CORTADELLA_1998, "regions", ("pre_region", "ger")),
    Definition(
        "elementary_ts", "Elementary transition system",
        "A transition system that a Petri net can mimic exactly, with one place per "
        "region.",
        (r"TS \text{ is elementary} \iff \text{state separation} \land "
         r"\text{forward closure}",),
        ("A transition system that is not elementary can be made so by *label splitting*: "
         "renaming some occurrences of an event (e.g. $a$ into $a_1$ and $a_2$), which the "
         "net then shows as two transitions with the same label.",),
        CORTADELLA_1998, "regions", ("state_separation", "forward_closure")),
    Definition(
        "region_synthesis", "Synthesis from regions",
        "The Petri net of a transition system: a place per minimal region, the events as "
        "transitions, and arcs from pre-regions and to post-regions.",
        (r"P = \lbrace p_R \mid R \text{ is a minimal region} \rbrace, \quad T = E",
         r"F = \lbrace (p_R, e) \mid R \in \text{pre}(e) \rbrace \cup "
         r"\lbrace (e, p_R) \mid R \in \text{post}(e) \rbrace",
         r"M_0 = [\, p_R \mid s_{in} \in R \,]",
         r"TS \text{ elementary} \Rightarrow RG(N, M_0) \cong TS"),
        ("$\\cong$: the reachability graph is the transition system with its states "
         "renamed (isomorphic). For a transition system that is not elementary the net can "
         "allow more.",),
        f"{CORTADELLA_1998}; {AALST_2016}, Section 7.4.2", "regions",
        ("minimal_region", "pre_region", "elementary_ts")),
)

BY_KEY = {definition.key: definition for definition in DEFINITIONS}

#: Property names as the analysis tabs show them -> definition key.
FOR_TITLE = {
    "WF-net": "wf_net", "WF-net structure": "wf_net",
    "Sound": "sound", "Not sound": "sound", "Undecided": "sound",
    "(i) Option to complete": "option_to_complete", "Option to complete": "option_to_complete",
    "(ii) Proper completion": "proper_completion", "Proper completion": "proper_completion",
    "(iii) No dead transitions": "no_dead_transitions",
    "No dead transitions": "dead_transition",
    "Bounded": "bounded", "Safe": "safe", "Terminating": "terminating",
    "Deadlock-free": "deadlock_free",
    "Live": "live", "Reversible": "reversible",
    "Live and bounded ⇒ sound": "soundness_theorem",
    "Unbounded ⇒ not sound": "soundness_theorem",
    "Not live ⇒ not sound": "soundness_theorem",
    "Free-choice": "free_choice", "Well-structured": "well_structured",
    "S-coverable": "s_coverable", "State machine": "state_machine",
    "Covered by P-invariants": "p_invariant", "Covered by T-invariants": "t_invariant",
    "Incidence matrix": "incidence_matrix",
    "Transition system": "transition_system", "Region": "region", "Not a region": "region",
    "Minimal regions": "minimal_region", "State separation": "state_separation",
    "Forward closure": "forward_closure", "Elementary": "elementary_ts",
    "Not elementary": "elementary_ts", "Reachability graph ≅ transition system":
    "region_synthesis", "Reachability graph ≇ transition system": "region_synthesis",
}


def lookup(key_or_title: str | None) -> Definition | None:
    """The definition with this key, or for this displayed property name."""
    if not key_or_title:
        return None
    return BY_KEY.get(key_or_title) or BY_KEY.get(FOR_TITLE.get(key_or_title, ""))


# ---------------------------------------------------------------------------
# docs/definitions.md
# ---------------------------------------------------------------------------
def markdown() -> str:
    """The reference page, with ``$$`` display maths as GitHub renders it."""
    lines = [
        "<!-- Generated by `python -m openprocess.mining.definitions > docs/definitions.md`.",
        "     Edit openprocess/mining/definitions.py, not this file. -->",
        "",
        "# Definitions",
        "",
        "Every property the analyses in OpenProcess report, defined precisely. In the app, "
        "hover over a property in an **Analysis** tab to see its definition, or click "
        "it to keep the definition open.",
        "",
        "Throughout, $N = (P, T, F)$ is a Petri net and, for WF-nets, $i$ and $o$ are "
        "its start and end places.",
        "",
    ]
    for section, title, _ in SECTIONS:
        lines.append(f"- [{title}](#{_anchor(title)})")
    lines.append("")
    for section, title, intro in SECTIONS:
        lines += [f"## {title}", "", intro, ""]
        for definition in DEFINITIONS:
            if definition.section != section:
                continue
            lines += [f"### {definition.name}", "", definition.summary, ""]
            for formula in definition.formulas:
                lines += ["$$", formula, "$$", ""]
            for note in definition.notes:
                lines += [note, ""]
            if definition.uses:
                links = ", ".join(f"[{BY_KEY[k].name}](#{_anchor(BY_KEY[k].name)})"
                                  for k in definition.uses)
                lines += [f"Builds on: {links}.", ""]
            if definition.source:
                lines += [f"*Source: {definition.source.replace('*', '')}.*", ""]
    lines += ["## Sources", "",
              f"- {AALST_2000}.", f"- {AALST_2016}.", f"- {CORTADELLA_1998}.", ""]
    return "\n".join(lines)


def _anchor(title: str) -> str:
    """GitHub's heading anchor: lower case, spaces to dashes, punctuation dropped."""
    keep = []
    for char in title.lower():
        if char.isalnum() or char in "-_":
            keep.append(char)
        elif char == " ":
            keep.append("-")
    return "".join(keep)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(markdown())
