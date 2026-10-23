# Workflows: boxes, how they run, and how to write your own

The workflow framework is `openprocess.flow`. It has no Qt in it: everything
here works in a script, a notebook, a test and from `openprocess run`, and the
app only draws it. For *using* workflows in the app, see the README; this
page is for people who write boxes, read the code, or want to know what a
`.cpnflow` file guarantees.

## Three ideas

1. **Every algorithm is a box, and every box is a plain Python function.**
   The decorator reads the function's type hints and docstring; there is no
   base class and no registration call.
2. **Every analysis is a workflow file.** A `.cpnflow` file records the
   boxes, their settings and connections, and everything needed to get the
   same numbers again: fingerprints of the input files and of the box code,
   every seed, the versions and the installed packages.
3. **Every box shows how it got there.** A box reports notes, intermediate
   values and derivation steps while it runs; the app shows them on the
   *How* tab, next to the box's *Code*. A box is a thin wrapper, so the
   *Code* tab shows the box's few lines and, under them, the code of every
   algorithm it calls (`algorithm_calls(spec)` finds them: what the
   function calls outside the framework and the standard library), the
   published source it follows, and *Whole file* for the module itself.

## Writing a box

```python
# Week 5/boxes/last_two.py
from openprocess.flow import box, EventLog, TransitionSystem
from openprocess.mining.transition_system import transition_system_from_log
from openprocess import flow


@box(group="Discover")
def last_two(log: EventLog, representation: str = "multiset") -> TransitionSystem:
    """A transition system whose state is the last two activities.

    representation: multiset or sequence
    """
    ts = transition_system_from_log(log.simple_log(), "prefix", representation, 2)
    flow.note(f"{len(ts.states)} states")
    return ts
```

What the decorator reads, and nothing else:

| From | Becomes |
|---|---|
| a parameter whose type is an OpenProcess type (`EventLog`, `PetriNet`, `TransitionSystem`, …) | an input connection point, named after the parameter |
| `list[Scores]` | an input that takes any number of connections |
| `Table | None`, or a default of `None` | an optional input |
| the return type | the output; a `@dataclass` whose fields are OpenProcess types gives several outputs (one per field) |
| `int`, `float`, `bool`, `str` with a default | a setting, the default as its value |
| `Literal["a", "b"]` | a setting that is a choice |
| `Column` | a setting that is a column of a table connected to the box: in the app, a list of its columns to pick from (a name can still be typed) |
| `Path` | a setting that is a file in the workflow's folder |
| the docstring | the help text; a `name: text` line in it is the help of that setting |
| `@box(name=…, group=…)` | the name on the canvas and the group under *+ Add box* (default: the function's name, and *Other*, which is listed last) |
| `@box(needs="pandas")` | the box is listed greyed out with "needs pandas" when the module is not installed |
| `@box(heavy=True)` | the box may take minutes: the app runs it where it can be stopped |

A mistake (a setting without a default, an unknown type, a missing return
hint) raises `BoxError` with a plain message when the file is imported.
The app keeps such a file in the list, greyed out, with the reason.

The function stays a function: `last_two(my_log)` runs it, in a notebook
or a test, exactly as the app would.

### The types

`openprocess.flow.types` re-exports the engine's types and adds the few that
only workflows need:

| Type | What it is |
|---|---|
| `EventLog`, `SimpleLog` | a log; the control-flow view (a multiset of sequences) |
| `PetriNet`, `Marking`, `ProcessTree`, `TransitionSystem`, `DFG`, `Footprint`, `Regions` | models and the structures the algorithms work on |
| `CPNet` | a coloured Petri net (a CPN Tools model) |
| `ReplayResult`, `AlignmentResult` | conformance results, per variant |
| `Scores` | named numbers about one model: one row of a Compare table |
| `Table` | anything tabular; `samples` holds the numbers behind a distribution |
| `Figure` | a picture (SVG, and PNG when there is one) |
| `Text` | a report |
| `Dataset`, `Predictions`, `Predictor` | the prediction pipeline's rows, labels and models |
| `typing.Any` | an escape hatch: a grey connection point that connects only to one of the same declared class |

`register_type(cls, key, name)` adds a type of your own.

### Reporting how (the *How* tab)

```python
from openprocess import flow

flow.note("Split on XOR: {a, e} | {b, c}")     # one line
flow.show(dfg, "Filtered DFG")                # an intermediate value, in its own viewer
flow.steps(result.steps())                    # (title, content) rows, as the book writes them
```

Outside a running box the calls do nothing. A result object with a
`steps()` method (like `AlphaResult`) is shown without any call, and its
`warnings` become notes.

### Other libraries

`openprocess.flow.convert` turns OpenProcess types into other libraries' objects and
back, in one line, importing nothing until asked:

```python
from openprocess.flow.convert import convert

df = convert(log, "pandas.DataFrame")      # one row per event
figure = convert(fig, Figure)               # a matplotlib figure as SVG
table = convert(df, Table)
```

Built in: `EventLog ↔ pandas.DataFrame`, `SimpleLog ↔ EventLog`,
`Table ↔ DataFrame`, `Series → Table`, `numpy.ndarray → Table`,
`matplotlib Figure → Figure`, `PetriNet ↔ PM4Py` (through PNML).
`register(source, target, function)` adds one. The runner also applies a
converter when an output is connected to an input of a convertible type.

The *Science* boxes in `openprocess/flow/boxes/science.py` (pandas, NumPy, SciPy,
matplotlib) are written to be copied: each is a few lines around one
library call.

## Workflows in Python

```python
from openprocess.flow import Workflow, Runner, Sweep, save, load
from openprocess.flow.boxes.input import typed_log
from openprocess.flow.boxes.discover import inductive_miner
from openprocess.flow.boxes.check import check_fit

wf = Workflow("demo")
log = wf.add(typed_log, {"text": "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"})
model = wf.add(inductive_miner, {"noise": 0.2})
fit = wf.add(check_fit)
wf.connect(log, model)                 # the input is found by type
wf.connect(model, fit, "model")
wf.connect(log, fit, "log")

run = Runner().run(wf)
print(run.value(fit).metrics)          # {'fitness': 1.0, 'precision': ..., ...}
print(run.result(model).explanation.steps)
save(wf, "demo.cpnflow", run)
```

`wf.can_connect(a, b)` says whether a connection is allowed and, if not,
why in plain words (the same words the canvas shows). `wf.validate()`
lists what stops the workflow from running.

Or write the workflow as a function and record it:

```python
from openprocess.flow import workflow, to_python

@workflow(name="demo")
def demo():
    log = typed_log(text="[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]")
    model = inductive_miner(log, noise=0.2)
    return check_fit(model, log)

wf = demo.workflow                      # the boxes it calls become nodes and edges
print(to_python(wf))                    # and back to source
```

Calling a box inside a recorded function makes a node instead of running
it; the function itself still runs normally when called.

### Groups and composite boxes

A **group** is a set of nodes shown as one box (`Workflow.groups`; ⌘G on
the canvas, ⇧⌘G to ungroup). Its connection points are the inputs and
outputs of its members that reach outside the group, and double-clicking
it opens the group's own canvas. `to_python(wf)` writes every group as a
function of its own, called once; `group_to_python(wf, group_id)` gives
one group.

A `@workflow` function **with typed parameters** is itself a box (a
*composite*, `box.composite`): its parameters are its inputs and settings,
its return value its output, and recording it records its members and
groups them. *Save as a box* on the canvas writes a group as such a
function into the folder's `boxes/`, where the library lists it under
*Yours*; the standard boxes it imports stay standard (only the boxes a
module defines are registered from it).

### How the runner works

- **Order.** Topological, left to right on ties.
- **Keys.** Each result is kept under `sha256(box code + settings + input keys)`.
  Change a setting and only the boxes after it run again; open a workflow
  whose inputs have not changed and nothing runs; the same box with the
  same inputs anywhere is computed once (`Runner(cache=…)` shares a cache).
- **Statuses.** `waiting`, `running`, `done`, `failed` (the exception and
  its traceback are in the result), `idle` (an input is missing, or a box
  before it failed; the message says which), `blocked` (a library is
  missing or the box is not allowed to run).
- **Stopping.** `Runner.run(wf, stop=threading.Event())` checks the event
  between boxes. A `heavy` box can be run with `run_in_process`, which
  can be killed.
- **Conversions.** An output is converted to an input's type when a
  converter exists; a converter that fails fails that box, with a message
  naming both boxes, and the rest of the run goes on.
- **Outputs are checked.** What a box returns is checked against its return
  type: a box declared `-> PetriNet` that returns a dict fails with a plain
  message instead of puzzling the box after it. `None` is allowed only for
  `-> PetriNet | None`.
- **Strict.** `Runner(strict=True)` (`openprocess run --strict`) also fails a
  box that changes one of its inputs in place, which would quietly change
  the result of every other box reading the same value. Use it in your
  tests and CI.

### Sweeps

```python
wf.set(model, noise=Sweep.parse("0..0.5 step 0.1"))     # or Sweep([0, 1, 2]) for seeds
run = Runner().run(wf)
run.variants                       # [{(node, 'noise'): 0.0}, {...: 0.1}, ...]
run.value(fit, variant=3)          # one result per value
```

The boxes after a swept setting run once per value (the cartesian product
when several are swept). A box with an input that takes any number of
connections (*Compare*, *Plot*, *Sweep table*) collects the results of
every value; every `Scores` and `Table` made under a sweep carries the
values in its `context`, which *Sweep table* turns into columns.

## The workflow file

A `.cpnflow` file is JSON:

```json
{
  "format": "cpnflow/1",
  "name": "compare discovery",
  "boxes": [{"id": "n1", "box": "openprocess.flow.boxes.input.open_log",
             "settings": {"file": "orders.xes"}, "position": [0, 0]}, ...],
  "connections": [{"from": "n1", "output": "out", "to": "n2", "input": "log"}, ...],
  "groups": [],
  "record": {
    "versions": {"openprocess": "0.6.0", "python": "3.12.4", "numpy": "1.26.4"},
    "environment": {"platform": "...", "packages": {"numpy": "1.26.4", ...}},
    "inputs": [{"node": "n1", "setting": "file", "file": "orders.xes", "sha256": "..."}],
    "custom_boxes": [{"file": "boxes/last_two.py", "sha256": "..."}],
    "seeds": {"n4": 0},
    "results": {"n2": "...", "n3": "..."},
    "saved": "2026-10-10T12:00:00+00:00"
  }
}
```

- `differences(record, workflow, folder)` says what has changed since the
  file was saved: an input file, a box file, a seed, the OpenProcess version.
  The app shows it before *Re-run*.
- `check(workflow, record, run)` says which boxes give a different result
  than recorded (by the fingerprint of the result's content).
- `requirements_lock(record)` is a `requirements.lock` from the record's
  environment.

### What "reproducible" promises

The badge beside an analysis, `--check` and the record all mean one precise
thing: **re-running the workflow gives results whose fingerprints equal the
recorded ones.** A fingerprint is the SHA-256 of a canonical form of a
result's whole content (a net's places, transitions, arcs and markings, every
attribute of every event of a log, the rows of a table, the numbers of a
score), independent of the order a set was filled in or where a net is
drawn, so "reproduces" is "the same content", not "close enough"
([framework.md §7](framework.md#7-fingerprints) defines it exactly). How far
that promise travels depends on what else is the same:

| Tier | What is the same | What the record gives you | What can still differ |
|---|---|---|---|
| **1. Same machine** | the folder, the installed packages, the OpenProcess version | the badge: *Reproducible*, or exactly which input, box file, seed or version changed since the record (`differences`) | nothing, for the built-in boxes: the engines are pure Python and every random choice is seeded |
| **2. Same environment, another machine** | `requirements.lock` installed (from the export, or `--lock`), the inputs from the zip (their hashes are checked), the same Python minor version | `openprocess run --check` passes or names the box whose result differs | a box of your own that depends on something outside the record: the clock, the file system's order, an unpinned download |
| **3. Another platform or version** | the same workflow and inputs, but another OS, Python or package version | the same: a pass is a pass; a failure names the box | floating-point results of numeric boxes (SciPy's tests, matplotlib's figures as SVG, a model trained with another BLAS) can differ in the last digits, and so can their fingerprints; the built-in miners and checks are integer and set arithmetic and do not |

So a passing `--check` on the author's machine is tier 1; the claim a paper
can make is tier 2, and that is what the experiment zip is for. For tier 3,
compare the *values* the report shows rather than the fingerprints: a
fitness of 0.8712 on one platform and 0.8712 on another is the same result
even when an SVG of the plot is not.

What is in the record, and what is not:

- **In:** the SHA-256 of every input file and every box file in `boxes/`;
  every seed; the OpenProcess, Python and package versions; the fingerprint
  of every result; the platform.
- **Not in:** the code of OpenProcess's own boxes and the libraries they
  call (their *versions* are; that is what the lock pins), the hardware, and
  anything a box reads that was not declared as a `Path` setting (such a box
  is reproducible only by the discipline of its author).

### Headless, and in CI

```bash
openprocess run experiment.cpnflow                 # run it, print what every box gave
openprocess run experiment.cpnflow --check         # re-run; exit 1 if any result differs from the record
                                                   #   (or if nothing was recorded to compare with)
openprocess run experiment.cpnflow --strict        # also fail a box that changes its inputs
openprocess run experiment.cpnflow --report r.html # the analysis as one HTML page
openprocess run experiment.cpnflow --save          # record the results in the file
openprocess run experiment.cpnflow --sweep noise=0..0.5 step 0.1
openprocess run experiment.cpnflow -o results/     # every result as a file (CSV, PNML, SVG)
openprocess run experiment.cpnflow --lock          # write requirements.lock
openprocess run experiment.cpnflow --export experiment.zip   # the workflow, its record, inputs, boxes,
                                             # results, requirements.lock and a README: for a paper
openprocess boxes [folder]                         # the boxes, with a folder's boxes/
openprocess datasets list | fetch "Sepsis cases" | where
```

A paper's repository can run `openprocess run --check` on every push; the
passing badge is the reproducibility claim.

## Public datasets

`openprocess.flow.datasets` knows the standard logs by name (the BPI Challenge
logs, Sepsis, Road Traffic Fine Management, Hospital Billing) with where
they are published and, once fetched, their SHA-256. The *Open dataset*
box fetches a log once into `~/.openprocess/datasets` (or `$OPENPROCESS_DATASETS`),
checks the hash on every later use, and records the name and hash in the
workflow file. 4TU.ResearchData serves the files behind DOI pages, so the
first fetch says where to download the file by hand and where to put it;
a direct `url` can be given in `~/.openprocess/datasets/datasets.json`, which
also takes datasets of your own.

## Where boxes come from

- `openprocess.flow.boxes`: the standard library, one module per group.
- a folder's `boxes/` subfolder: every `.py` file, loaded with
  `library_for(folder)`; saving the file reloads the box in the app.
- installed packages that declare an entry point:

  ```toml
  [project.entry-points."openprocess.boxes"]
  my_pack = "my_pack.boxes"
  ```

A box file runs arbitrary Python, so the app asks once before running a
folder's boxes.

## The prediction pipeline

*Prefixes* turns a log into a `Dataset` (one row per prefix, with the
next activity, the remaining time or the outcome as the label); *Split by
time* makes train and test sets by case start; *Frequency model* is a
baseline that needs no library; *Predict* applies any `Predictor`;
*Evaluate predictions* scores accuracy or MAE per prefix length. A model
of your own is a box from `Dataset` to `Predictor`:

```python
from openprocess.flow import box, Dataset, Predictor

@box(group="Predict", needs="sklearn")
def random_forest(train: Dataset, trees: int = 100, seed: int = 0) -> Predictor:
    """A scikit-learn random forest on the prefix features."""
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    cls = RandomForestClassifier if train.kind == "classification" else RandomForestRegressor
    model = cls(n_estimators=trees, random_state=seed).fit(train.X, train.y)
    model.name = f"Random forest ({trees} trees)"
    return model          # it has fit() and predict(): that is all a Predictor needs
```

(`examples/boxes/next_activity_sklearn.py` is this file.)

## Module map

Every module of `openprocess/flow` (and of the rest of the code), with what
it is, is in [architecture.md](architecture.md#every-module-generated),
generated from the code so it is never out of date. The precise rules this
page describes by example are in [framework.md](framework.md).
