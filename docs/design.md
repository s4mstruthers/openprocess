# Why OpenProcess is built the way it is

The reasoning behind the design, in one place, so that a decision can be
checked against it before it is made. The *what* is in the README; the
*how* is in [how-it-works.md](how-it-works.md) and
[workflows.md](workflows.md); the precise rules of the workflow framework
are in [framework.md](framework.md), and the map of the code is
[architecture.md](architecture.md). This page is the *why*.

## The problem

A process-mining analysis, as it is usually done, cannot answer three
questions a reviewer, a supervisor or the analyst a year later will ask:

1. **What exactly ran?** The model came from a plug-in, the map from
   another tool, the table from a notebook. Which implementation, which
   version, which settings?
2. **How did it get there?** A fitness of 0.87 is a number. The replay
   behind it, the cuts the miner chose, the places the α-algorithm derived
   from which pairs: not shown anywhere.
3. **Can I get the same numbers again?** Settings, seeds and input files
   live in someone's memory. Re-running means reconstructing.

The same gap shows up in teaching. A student sees the output of an
algorithm and is then asked, on an exam, to produce its derivation by hand.
The tool that could show the derivation shows only the result.

## The claim

**Code is mathematics, and an analysis should carry its own derivation.**
A proof in a paper shows its steps; a discovery algorithm is a procedure
whose model is its conclusion. A tool that gives only the conclusion is a
black box, however good the conclusion is. Everything in OpenProcess
follows from taking that seriously: what ran, how it got there, and how to
get the same numbers again are kept *with* the result, by the framework,
not by the discipline of the person using it.

## Three ideas, and what each one buys

1. **Every algorithm is a box, and every box is a plain Python function.**
   The decorator reads the function's type hints and docstring; there is no
   base class, no registration call, no plug-in descriptor. *Buys:* a
   researcher's algorithm is on the canvas in the time it takes to add type
   hints; the function still runs in a notebook or a test exactly as the
   app runs it; and the Code tab can show the box's few lines and the
   algorithm it calls, because the box *is* the code.
2. **Every analysis is a workflow file that can be re-run.** The `.cpnflow`
   file records the boxes, settings and connections, plus fingerprints of
   the inputs and the box code, every seed, the versions and the installed
   packages. *Buys:* the badge, `--check` in CI, the experiment zip for a
   paper, and the Runs tab; see "What reproducible promises" in
   [workflows.md](workflows.md#what-reproducible-promises) for exactly
   what is and is not promised.
3. **Every box shows how it got there.** A box reports notes, intermediate
   values and derivation steps as it runs; a result object that keeps its
   intermediate sets (`AlphaResult`, `InductiveResult`) is shown step by
   step without any call. *Buys:* the How tab, Step through, and the same
   object that an exam exercise is checked against. One source of truth for
   the derivation, three consumers: the test suite, the app, and Learn.

## Decisions, and the reasons

- **Pure Python, no runtime dependencies for the engine.** The miners, the
  conformance checks, the Petri net analysis, the CPN ML interpreter and the
  simulator are standard library. *Reason:* the same code must run in a
  notebook, in CI, in a student's exercise checker and in the app; a
  150 MB GUI toolkit or an AGPL library in the import path would rule out
  the first three. Qt is an extra (`openprocess[app]`); PM4Py is an extra
  that adds boxes and is the reference the built-in miners are checked
  against.
- **The framework has no Qt in it.** `openprocess.flow` works from a script,
  a test and `openprocess run`; the app only draws it. *Reason:* nothing
  shown in the GUI may be GUI-only, or the record would describe something
  the command line cannot reproduce.
- **Thin boxes over the engines, not algorithms written in the boxes.** A
  box in `openprocess/flow/boxes/` is a few lines around one engine call.
  *Reason:* the Code tab shows the box and then the algorithm it calls; a
  box that contained the algorithm would hide it in the wrapper, and the
  engine would no longer be usable without the framework.
- **Sources from the code, not beside it.** `docs/references.md` and
  `docs/definitions.md` are generated from `openprocess/references.py` and
  `openprocess/mining/definitions.py`, and a test fails when they drift.
  Each algorithm's module docstring cites the work it follows; the Cite
  button reads the same list. *Reason:* a citation that lives only in a
  document goes stale; one that lives in the code is checked with it.
- **Results keep their intermediate steps.** `AlphaResult` keeps `T_L`,
  `T_I`, `T_O`, `X_L` and `Y_L`; `InductiveResult` keeps every cut and
  fall-through. *Reason:* see idea 3. It also makes the exam exercises
  possible: "give `Y_L` for this log" is checked against the miner's own
  `Y_L`.
- **Nothing on a page computes behind the user's back.** The log page's
  Discover tab used to run a miner on the spot, with no code to read and no
  record; since 0.9.5 it hands the log to the canvas. Every computed figure
  on a page links to the function that computed it. *Reason:* a number
  with no How and no Code is exactly the black box the claim rules out.
- **Three spaces, one engine.** *Mine* (workflows), *Model* (nets drawn by
  hand, the token game, the CPN simulator) and *Learn* (worksheets) are
  views on the same objects. *Reason:* the net a miner discovers and the
  net a student draws are the same class; the soundness check, the
  definitions and the counterexample replay are written once. The spaces
  exist so that each audience sees one set of rules, not so that anything is
  built twice.
- **Folders, not projects.** The app opens a folder (a week of a course, a
  paper's repository); files in it are the sidebar; the app and the folder
  stay in step both ways. *Reason:* the record refers to files by path and
  hash, and a paper's repository is already a folder. A project file of our
  own would be one more thing to lose.
- **Teaching and research share one transparency mechanism.** Learn's
  checks, concealment and exam mode are built on the same results and
  viewers as Mine. *Reason:* a derivation that can be shown is a derivation
  that can be checked, in both directions.

## What this is not

- Not a replacement for pandas, scikit-learn or PM4Py. The Science and
  Predict boxes are examples of how any Python library plugs in; OpenProcess
  is where their results meet a log and a model, and get recorded.
- Not a workflow engine for anything but process mining. The types are the
  types of the field (logs, nets, trees, transition systems, replays,
  scores); `typing.Any` is an escape hatch, not the design.
- Not a research result in itself. The algorithms follow published work and
  are checked against a reference implementation
  (`tests/cross_check_inductive_miner.py`); what is new is the combination:
  workbench, record and derivation in one place.

## Open decisions

- **The workflow file's name.** `.cpnflow` and `"format": "cpnflow/1"`
  predate the rename from CPNpy to OpenProcess. Changing them is a
  compatibility decision to take with the 1.0 API freeze, with a reader that
  accepts both.
- **What the 1.0 freeze covers.** The proposal: `@box` and what it reads
  from a signature, the types in `openprocess.flow.types`, the `.cpnflow`
  format and its record, and the `openprocess run` command line. Everything
  in the GUI stays free to change.
- **Where heavy results live.** In memory and recomputed on open (what the
  cache does today), or written next to the workflow so a heavy experiment
  opens instantly. The record makes both reproducible.
