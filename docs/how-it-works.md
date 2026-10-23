# How OpenProcess works

Background for anyone reading or changing the code. For *using* the app,
see the [README](../README.md).

## Architecture

The map of the whole code base, drawn from its imports and checked by the
test suite, is [architecture.md](architecture.md): the four layers (engines,
framework, teaching, app), which package imports which, how a workflow runs
and every module. Everything below the app has **zero runtime
dependencies** (only the app needs PySide6), so the engines import fine in a
notebook, a script or CI. PM4Py is an *optional* extra; nothing requires it.

## The CPN ML subset

Inscriptions are written in CPN ML, a dialect of Standard ML. This
implementation has its own lexer, parser and evaluator for the practical
subset — no external SML runtime is involved.

**Supported**

- literals: `1`, `~3`, `3.14`, `"text"`, `#"c"`, `true`, `false`, `()`
- tuples `(a, b)`, lists `[a, b]` / `nil` / `::` / `^^`, records `{f = a}` and
  `#f r` / `#2 t`
- arithmetic `+ - * / div mod`, comparison `= <> < > <= >=`, `andalso`,
  `orelse`, `not`, string `^`
- `if … then … else`, `let … in … end`, `case … of … | …`, `fn … => …`
- `fun` declarations: recursive, curried, multi-clause, pattern-matched
- `op +` and friends: an infix operator as a function (`List.foldl (op +) 0 l`)
- options: `SOME x`, `NONE`, `valOf`, `isSome`, `getOpt`; `Int.fromString`
  and `Real.fromString` return options, as in Standard ML
- multisets: `` n`v ``, `++`, `--`, `empty`, `ms_to_col`, `cf`, `size`
- time: a transition-level delay, `@+ delay` on an output arc (the two add
  up), per-term delays joined with `+++` (`` 1`x@+2 +++ 1`y@+5 ``), delays
  inside `if`/`case` branches, and time stamps in initial markings
  (`` 1`3@5 +++ 2`4@0 ``)
- colour set functions for every colour set `CS`: `CS.all()`, `CS.size()`,
  `CS.ran()`, `CS.ord c`, `CS.col i`, `CS.legal c`, `CS.mkstr c`
- a Standard Basis subset: `List.map/filter/foldl/foldr/nth/take/drop/concat/
  find/partition/…`, `Int.toString`, `size`, `explode`, `implode`,
  `String.sub`, `Char.ord/chr`, `Math.*`

Declarations may come in any order: a colour set can use a value declared
later (`colset N = int with 1..n; val n = 5;`), and a subset colour set can
use a predicate function (`colset E = subset N by even;`). The compiler
works in passes until everything that can be compiled is.

**Not supported**: `exception` declarations and `handle`, `datatype`,
`structure` and `signature` declarations, and a separate character type
(`#"a"` is the one-letter string `"a"`).
- random distributions: `discrete`, `uniform`, `exponential`, `normal`,
  `poisson`, `binomial`, `erlang`, `bernoulli`

**Colour sets** — all thirteen CPN Tools forms:

```sml
colset U   = unit;                    colset U2 = unit with e;
colset B   = bool;                    colset I  = int with 1..10;
colset II  = intinf;                  colset R  = real;
colset S   = string with "a".."z" and 1..8;
colset E   = with red | green | blue;
colset X   = index ph with 1..5;
colset P   = product A * B;
colset Rec = record name:STRING * age:INT;
colset L   = list A with 0..3;
colset Un  = union Car:CARS + Walk;
colset Sub = subset A by pred;
colset Al  = A;                       (* alias *)
```

Any of them may be declared `timed`.

**Two deliberate departures**

1. `--` truncates at zero (`max(0, a − b)`), matching CPN Tools' multiset
   difference rather than producing negative coefficients.
2. A bare value on an arc means one token: `` x `` and `` 1`x `` are the same
   thing, again as in CPN Tools.

---

## How it works

### Multisets are the whole game

A place holds a *multiset* of coloured tokens, and the firing rule is stated
entirely in multiset terms. For a transition `t` under binding `b`:

```
enabled  iff   for every input place p:   E(p,t)<b>  ≤  M(p)

firing         M'(p)  =  ( M(p) −− E(p,t)<b> )  ++  E(t,p)<b>
```

Get `≤`, `−−` and `++` right and the simulator is a short piece of code. That
is why `openprocess/ml/multiset.py` is separate and has its own test module with
hand-computed expected values.

### Binding search is the hard part

An arc inscription `(x, n)` does not say *which* token to take. Enumerating
every variable over its colour set is correct but hopeless — three variables
over a 100-element colour set is a million candidates per transition per step.

Instead, `openprocess/sim/binding.py` reads each input inscription as a **pattern**
and matches it against the tokens that are actually in the place, which yields
the variable values directly:

1. split `` 2`(x,n) ++ 1`y `` into *demands*: "take 2 tokens shaped `(x,n)`",
   "take 1 shaped `y`";
2. order demands so the most constrained come first;
3. satisfy them depth-first, backtracking on failure, threading a `remaining`
   multiset per place so no token is counted twice;
4. anything not a pattern (`` 1`(n+1) ``, `Chopsticks(p)`) is deferred until its
   variables are bound elsewhere, then *evaluated* and checked;
5. variables still free — ones used only in a guard or on an output arc — are
   enumerated over their colour sets;
6. finally the guard is evaluated.

If step 5 meets an infinite colour set, you get a clear error naming the
variable, not a hang.

### Timed nets

Three rules, implemented in `openprocess/sim/simulator.py`:

- a token in a timed place carries a **time stamp**; only tokens whose stamp
  has been reached may be consumed;
- produced tokens are stamped `clock + delay`, where the delay is the
  transition's own time inscription plus the output arc's `@+` (as in CPN
  Tools, the two add up); in `` 1`x@+2 +++ 1`y@+5 `` each term has its own
  delay;
- when nothing is enabled now but some place holds a token stamped for the
  future, the clock **jumps** to the earliest such stamp.

Model time therefore moves in event-driven jumps, never in ticks. Only when no
transition is enabled *and* no future stamp exists is the model dead.

### State space

`openprocess/analysis/state_space.py` explores breadth-first, keying the visited set
on `(marking, clock)` — markings are immutable and hashable, so this is an
ordinary Python `set`. Properties come from **Tarjan's SCC decomposition**
(written iteratively, so a hundred thousand nodes will not blow the recursion
limit):

- **dead markings** — nodes with no outgoing arcs;
- **live transitions** — those occurring inside *every* terminal SCC: whichever
  cycle the system settles into, they can still happen;
- **home markings** — if the condensed graph has exactly one terminal SCC,
  every state in it is a home marking; otherwise there are none;
- **bounds** — a direct scan for integer bounds and pointwise-maximum multiset
  bounds.

Timed states that enable nothing let time pass to the next token release,
and keep going if that release enables nothing either, exactly like the
simulator; otherwise a timed model would show false dead markings.

Many models have infinite state spaces, so `generate()` takes `max_nodes`.
Hitting the cap sets `partial`, and the report says so in capitals, because
properties from a partial state space are **not** proofs.

### Hierarchy

A substitution transition stands for a subpage. Each port place on the
subpage is assigned a socket place around the substitution transition, and
*is* that place: `CPNet.compile` puts every port and its socket (and every
member of a fusion set) under one marking key, with a union-find so that a
port of a port of a socket ends up with the socket. The simulator and the
state space tool then run the model as one flat net, without knowing about
pages; the substitution transitions themselves never fire. A subpage used by
several substitution transitions would need one copy of its places per use,
so that case is reported as a problem.

### File format

`.cpn` is XML with a `<workspaceElements>` root, a `<globbox>` of declarations
and one `<page>` per diagram. The reader (`openprocess/io/cpn_reader.py`) is
deliberately forgiving — unknown elements are ignored, every field has a
default, and declaration text is taken from the `<layout>` element, which is
the exact source the modeller typed. Entity resolution is off, so opening a
file cannot make the process read local files or hit the network.

The writer emits the full element set CPN Tools expects, and copies sections
this implementation does not model (monitors, binders, workspace options)
verbatim from the file that was opened. So an untouched open/save is
semantically identical, an edited save keeps your monitors, and a new file gets
a minimal valid set of those sections.

---

## Project layout

```
openprocess/
  ml/
    values.py        runtime values; hashable and totally ordered
    multiset.py      Multiset and TimedMultiset algebra
    colorsets.py     the 13 colour set forms: membership + enumeration
    lexer.py         ML tokeniser (~ negation, nesting comments, ++ / -- / @+)
    ast_nodes.py     expression, pattern and declaration nodes
    parser.py        recursive descent + precedence climbing
    evaluator.py     environments, closures, pattern matching
    builtins.py      Standard Basis subset + random distributions
    errors.py        one exception family for everything the modeller can break
  model/
    declarations.py  the colset / var / val / fun declaration compiler
    net.py           Place, Transition, Arc, Page, CPNet, Marking
  io/
    cpn_reader.py    .cpn XML -> model
    cpn_writer.py    model -> .cpn XML
  sim/
    binding.py       pattern-directed binding search
    simulator.py     the firing rule, the clock, step / run / rewind
  analysis/
    state_space.py   reachability graph, SCCs, the CPN Tools style report
  gui/
    theme.py         palette, fonts and stylesheet; light/dark aware
    items.py         Qt graphics items for places, transitions, arcs
    canvas.py        the scene, the editing tools (drag to connect, rename), the view
    panning.py       the endless canvas: panning and zooming, shared by every canvas
    arc_editing.py   CPN IDE's rules for bending, sliding and reconnecting arcs
    app.py           `openprocess-cpn`: OpenProcess Studio opened on a new coloured net
cpnpy/               the old name (CPNpy, up to 0.6): `import cpnpy` is `openprocess`
                     under that name, so boxes and packs written for it run unchanged
  cli.py             the `openprocess` command (`run`, `boxes`, `datasets`, `mine`, `exercises`)
  flow/              workflows, no Qt (docs/workflows.md): types.py, box.py (@box),
                     explain.py, workflow.py, runner.py, sweep.py, record.py
                     (.cpnflow files, fingerprints, the environment lock), convert.py,
                     library.py (a folder's boxes/, entry points), datasets.py,
                     boxes/ (the standard boxes, one module per group)
  sim/export.py      a simulation's firing history as an event log
  references.py      the sources of every notation and algorithm (docs/references.md)
  learn/             OpenProcess Learn, no Qt: sheet.py (question.md with answer blocks),
                     pack.py (folders, progress, points), answers.py and notation.py
                     (reading typed sets, markings, cuts, trees, matrices, alignments…),
                     computed.py (the right answers worked out: @computed registry),
                     checks.py (checking, partial credit), exam.py (clock, marks,
                     variants), importer.py (a past exam into a pack);
                     teaching/ is its old name, kept for imports
examples/models.py   four complete models, also used as test fixtures
  mining/
    log.py           events, traces, logs, classifiers, textbook notation
    xes.py           streaming XES reader / writer (.xes, .xes.gz)
    csv_import.py    CSV logs with column mapping
    stats.py         variants, activity statistics, durations
    dfg.py           directly-follows graphs, simplification
    footprint.py     ordering relations, footprint matrix and comparison
    petrinet.py      labelled P/T nets, markings, firing rule
    pnml.py          PNML reader / writer (ProM / PM4Py compatible)
    analysis.py      reachability + coverability graphs, properties, soundness
    invariants.py    incidence matrix, P- and T-invariants (Farkas)
    filtering.py     log filters: time frame, start/end, activities, length, variants
    processtree.py   process trees and their translation to WF-nets
    layout.py        Sugiyama layered graph layout (no Graphviz needed)
    discovery/       alpha.py, inductive.py (IM, IMf), heuristics.py (dependency
                     graph, causal net, Petri net)
    conformance/     token_replay.py, alignments.py (A*), quality.py
    pm4py_bridge.py  optional extra miners via PM4Py
  model/
    plain.py         plain Petri nets in the editor (black tokens, weights, τ)
    examples.py      example Petri nets (File ▸ Open Example Petri Net)
  gui/flow/
    canvas.py        the workflow canvas: box items, wires, dragging a wire to connect
    viewers.py       a box's result, how it got there, its code, its settings; pop-out windows
    page.py          the Workflows page: canvas, side panel on demand, running in the background
    picker.py        the + Add box popover: every box by group, in a list that scrolls; search
    templates.py     the ready-made workflows (File ▸ New Workflow)
  gui/learn/
    mode.py          the Learn window: pack overview, worksheet beside the materials,
                     the exam clock, the Workflow tab
    answer_boxes.py  the answer boxes of a worksheet (one editor per type)
    concealment.py   hiding analysis results until they are revealed
  gui/studio/
    app.py           window, sidebar rows, welcome page, file opening, keeping the
                     open folder and the app in step (watching, autosave, moving)
    workspace.py     the open folder: its tree of files, the hidden .openprocess state,
                     unique names, safe (atomic) writes
    sidebar.py       the sidebar tree: dragging files to subfolders, Finder, back in
    file_dialogs.py  copy/move a file into the folder, name clashes, Settings
    updates.py       checking GitHub for a newer release and installing it
    petri_page.py    the Petri net editor with the Analysis tab
    cpn_page.py      the coloured-net editor, simulator and state space tool
    tool_icons.py    painted tool icons and cursors
    compare_page.py  logs side by side
    log_page.py      overview, variants, cases, dotted chart, map, footprint, discover
    model_page.py    canvas, token game, state space, analysis, conformance
    graph_view.py    zoomable canvas for nets, maps and state spaces
    graph_builders.py  mining objects -> canvas descriptions
    filter_dialog.py the Filter… dialog of a log page
    log_editor.py    Edit… on a log page: its notation, or case by case
    markdown_view.py Markdown with maths; net_comparison.py: Compare nets' result
    dotted_chart.py, charts.py, widgets.py, style.py, workers.py, documents.py
tests/               the test suite (tests/data holds a small course plane-boarding log)
packaging/           the standalone apps: PyInstaller recipe (openprocess.spec), entry
                     script, icons, build.py (build, smoke-test, pack)
.github/workflows/   build-apps.yml: builds the macOS, Windows and Linux apps
```

---


## Using the command line

```bash
openprocess check       model.cpn              # compile and report problems
openprocess info        model.cpn              # colour sets, variables, initial marking
openprocess simulate    model.cpn -n 100 --seed 7
openprocess statespace  model.cpn --max-nodes 50000
openprocess example     timed_conveyor -o demo.cpn

openprocess mine stats      log.xes                       # cases, activities, variants
openprocess mine filter     log.xes --variants 80 --end d -o filtered.xes
openprocess mine discover   log.xes -a heuristics -o model.pnml   # or alpha, im, imf
openprocess mine conform    model.pnml log.xes            # fitness, precision, ...
openprocess mine soundness  model.pnml
openprocess mine invariants model.pnml                    # incidence matrix, P-/T-invariants
```

`--seed` makes a run reproducible: the same seed replays the same choices,
and the same draws from the model's own random functions (`uniform`,
`discrete`, …), which is what makes a surprising result investigable.
A log can be a `.xes`, `.xes.gz` or `.csv` file, or a `.txt` file in the
textbook notation.

---

## Using it as a library

```python
from openprocess import read_cpn, Simulator, StateSpace

net = read_cpn("philosophers.cpn")
assert net.errors == []                  # a list of CompileIssue, empty when clean

sim = Simulator(net, seed=0)
for element in sim.all_enabled():
    print(element.describe(net))         # take <p = ph(1)>, …

sim.step()                               # fire one, chosen at random
sim.run(100)                             # or many
print(sim.marking.describe(net))

space = StateSpace(net).generate(max_nodes=20_000)
print(space.report())
print(space.dead_markings())             # [] means no deadlock
```

Building a model in code (see `examples/models.py` for four complete ones):

```python
from openprocess import CPNet, Page, Place, Transition, Arc

net = CPNet("Demo")
net.add_declaration("colset COLOUR = with red | green;")
net.add_declaration("var c : COLOUR;")

page = net.add_page("Top")
a = Place(name="A", colour_set_name="COLOUR", initial_marking_text="1`red")
b = Place(name="B", colour_set_name="COLOUR")
t = Transition(name="move")
page.places += [a, b]
page.transitions.append(t)
page.arcs += [
    Arc(place_id=a.id, transition_id=t.id, orientation="PtoT", expression_text="c"),
    Arc(place_id=b.id, transition_id=t.id, orientation="TtoP", expression_text="c"),
]

print(net.compile())                     # [] when everything parses
```

---

