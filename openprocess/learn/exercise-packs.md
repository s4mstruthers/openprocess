# Writing exercise packs

An exercise pack is a folder of worksheets. Students open it in OpenProcess
(*Learn ▸ Open Exercise Pack…*, or click an exercise in the sidebar), answer
in the boxes on each sheet, and press **Check**. Most answers are checked by
the app — often against answers it works out itself from the log, net or
transition system you give, so you do not have to. A pack can also be an
**exam**: a clock, points, no hints, and marks you export at the end.

## Making a pack, step by step

1. **Make the folder.** One folder for the pack, with a `pack.md` (its title
   and a short introduction), and one subfolder per exercise. Name them so they
   sort in order: `1 Dotted chart`, `2 Process modelling`, …
2. **Add what each exercise gives.** A log as `log.txt` in the course's notation
   (or `log.xes` / `log.csv`), a net as `net.pnml`, a transition system as
   `ts.txt`. To make a net, draw it in OpenProcess (*File ▸ New Petri Net*), give it
   its initial (and final) marking, and save it into the exercise folder as
   `net.pnml`. Pictures (`.png`) go in the folder too.
3. **Write `question.md`.** The question as students would read it on paper,
   with an `answer` block wherever they should answer (see *Choosing a box*
   below). Give every block a `solution` that explains the answer, or a grading
   scheme for open questions.
4. **Check it.** Run `openprocess exercises check "My pack" --answers` (see
   *Checking a pack*): it finds mistakes in the blocks and prints every answer
   the app works out, so you can compare them with your own.
5. **Try it.** Open the pack in OpenProcess, answer a few boxes right and wrong, and
   look at what Check says. Then delete the `my answers.json`, `my answer.pnml`,
   `my workflow.analysis` and `my notes.md` files your try left behind.
6. **Share it.** Zip the folder or put it in a shared drive. Students open it
   with *Learn ▸ Open Exercise Pack…*.

Starting from a **past exam**? *Learn ▸ Make a Pack from an Exam…* (or
`openprocess exercises import exam.txt "Exam 2025"`) turns its text into a skeleton
pack: see *Turning a past exam into a pack* below.

### Choosing a box

- One right answer from a list (true/false, "optimal / sub-optimal / not an
  alignment", "bounded with k = 1"): `choice`, or `yesno` for yes/no.
- A property of the given net, log or transition system: `yesno` or `set`
  with `compute:`, so the app works the answer out and it cannot be wrong.
- A set, a set of sets, pairs ($Y_L$, regions, dead transitions): `set`.
- A figure (fitness, costs, counts): `number`, with `tolerance:` when it is
  rounded (`tolerance: 0.00005` for four decimals).
- The footprint of the given log: `footprint`.
- "Give a firing sequence that…": `trace`.
- A marking, or the set of reachable or terminal markings: `marking`,
  `markings`.
- "Write the net as $(P, T, F, m_0)$": `tuple`.
- The incidence matrix, $M$ or $M'$ of region theory: `matrix`.
- "Draw the reachability graph": `ts` (typed as `s0 -a-> s1` lines).
- The Inductive Miner's steps: `cut`, `log` (a sublog), `tree`.
- Token replay by hand: `replay` (p, c, m, r per trace); an alignment to
  construct: `alignment`; models to put in order: `ranking`.
- "Before running it, what will the algorithm give?": `predict`, checked
  against a box of the workflow library run on the exercise's log.
- "Build an analysis": `workflow`, built on the canvas beside the sheet.
- "Draw a net" or "correct this net": `net`, with `sound: yes` and, when the
  behaviour has one right answer, `answer:`. To have students correct a given
  net, give it as a file and `start:` from it.
- Explanations, derivations, drawings other than nets: `open`, with the
  grading scheme as `solution`. Students write in the box (or in Notes) and
  compare.

### Pictures

`![Model (a)](model-a.png)` shows a picture from the exercise folder.
Pictures wider than the worksheet are scaled to fit. A picture in a
`solution` is only shown after *Show answer*, which is the place for model
answers drawn as pictures.

## The folder

```
Week 3 — Discovery/
    pack.md                          title and introduction (optional)
    1 Footprints/                    chapters are just subfolders (optional)
        Exercise 1.1 First log/
            question.md              the worksheet
            log.txt                  files the exercise gives
    2 Alpha/
        Exercise 2.1 …/
```

- Any folder with a `question.md` is an exercise. Exercises and chapters are
  listed in name order, with numbers sorted as numbers.
- `pack.md` starts with `# Title`; the rest is shown on the pack's overview.
  It may start with *front matter* (`---` lines) for the whole pack: `exam`,
  `time`, `deadline`, `seed`, `generate` (see *Exams* and *Variants*).
- An exercise can give **one** of each: `log.txt` (the course's notation,
  `[<a,b,c>^3, <a,c>]`), `log.xes` or `log.csv`; `net.pnml`; `ts.txt`
  (`s0 -a-> s1`, one per line or comma-separated, plus `initial: s0`). They are
  opened beside the worksheet. Other files can be referred to by name
  (`of: m2.pnml`, `over: m1.pnml, m2.pnml`).
- Students' work is saved next to the question: `my answers.json`,
  `my answer.pnml` for a drawn net, `my workflow.analysis` for a built
  workflow and `my notes.md` for their scratch notes. The pack's folder gets
  `my name.txt` (variants) and `my exam.json` (when an exam was started).
  Delete them to reset; leave them out when you share the pack.

## The worksheet

`question.md` is ordinary Markdown, with maths between `$…$` (inline) or
`$$…$$` (display). Its first `# heading` is the exercise's title. Wherever
students should answer, put an **answer block**:

````
**a.** Give the start activities $T_I$ of $L$.

```answer
type: set
compute: alpha.T_I
hint: Which activities does a trace begin with?
```
````

The sheet is shown top to bottom with each block as an answer box, so it reads
like the paper version. Inside a block:

- `key: value` lines;
- lines starting with spaces continue the value above (for a long `solution`);
- a comment starts with at least two spaces and `#` (one space before `#` is
  kept, since `a # b` is the α-algorithm's choice relation);
- `- [x] …` / `- [ ] …` lines are the options of a `choice`;
- `- …` lines are more accepted answers of a `text` question.

Every block can have:

| Key | Meaning |
|---|---|
| `type` | The kind of box (below). Required, except that a block with options is a `choice`. |
| `id` | A name for the answer in `my answers.json`. Default `q1`, `q2`, … by position: give ids if you will reorder questions after students started. |
| `points` | What a right answer earns (1 when left out). A partly right answer earns a part: 2 of 4 items, 3 of 4 parts of a tuple, 10 of 12 cells. |
| `hint` | Shown when the student asks for a hint (never in an exam). |
| `solution` | The model answer, shown on *Show answer* (Markdown; never in an exam). Without it, the right answer the app knows is shown. |

A sheet may start with front matter of its own (`points: 10` for the whole
exercise, `seed`, `generate`).

## Types of answer box

| `type` | The student… | Checked against |
|---|---|---|
| `yesno` | picks Yes or No | `answer: yes` / `no`, or `compute:` a property |
| `choice` | picks one option (or several, when several are marked `[x]`) | the `[x]` options |
| `set` | types a set: `{a, b}`, sets of sets `{s1, s3}, {s2, s3}`, or pairs `({a}, {b, d}), …` | `answer:` in the same notation, or `compute:` |
| `number` | types a number (`0.75`, `3/4`, `75%`) | `answer:` (with `tolerance:`), or `compute:` |
| `text` | types a short answer | `answer:` and any `- alternative` lines (case and spaces ignored), or a regular expression `pattern:` |
| `footprint` | fills in a matrix of → ← ‖ # | the footprint of the exercise's log, or `of: some.pnml` for a net's; `pairs: (a, b), (b, c)` to ask about some cells only |
| `trace` | types a firing sequence `register, send letter` | firable in `net.pnml` (or `net: other.pnml`) and ending as `ends:` says; `unseen: yes` for a trace the log never shows |
| `net` | draws a Petri net in the editor beside the sheet | `answer:`, `sound:`, `fits:` (any combination); `exact: yes` for the same net, not just the same behaviour |
| `marking` | types a marking `[p1, p4^2]` (also `p1 + 2p4`) | `answer:` or `compute:` (`fire(a, [p1])`, `net.m0`, `state equation(⟨a, b⟩)`) |
| `markings` | types a set of markings `[p1], [p2, p3]` | `answer:` or `compute:` (`reachable`, `terminal`) |
| `tuple` | fills in $P$, $T$, $F$ (as pairs) and $m_0$ | the exercise's net (or `of:`), part by part |
| `ts` | types a transition system, `s0 -a-> s1` per line, `initial: s0` | `answer:` or `compute: reachability graph`, the same up to the names of states |
| `matrix` | fills in a grid with the rows and columns given | `answer:` (typed as rows with headers) or `compute:` (`incidence`, `language.M`, `language.M'`); `rows:` and `columns:` fix the headers |
| `cut` | types a cut `→ {a} {b, c, e} {d}` | `answer:` or `compute: im.cut` (`im.cut(2)` for the second group's sublog) |
| `log` | types a log `[<b,c>^3, <e>]` | `answer:` or `compute: im.split(2)` |
| `tree` | types a process tree `→(a, ×(∧(b, c), e), d)` | `answer:` or `compute: im.tree`; children of × and ∧ in any order |
| `replay` | fills in produced, consumed, missing, remaining per trace | `compute: replay` on the exercise's net (one trace with `trace:`) |
| `alignment` | types two rows of moves, `≫` for no move | the net: `answer: optimal` (default), `sub-optimal` or `not an alignment`, for the trace in `trace:` |
| `ranking` | puts the models in `over:` in order, best first | `by:` a figure (`fitness` by default; `order: low` when lower is better) |
| `predict` | answers as a set, number, yes/no or text (`as:`) | `box:` a box of the workflow library run on the exercise's log, then `value:` a part of its result (`net.transitions`) |
| `workflow` | builds a workflow in the Workflow tab | `needs:` boxes it must have; `result:` a box's value (`Check fit.metrics.fitness`) against `answer:` / `compute:` |
| `open` | writes freely | nothing: the student compares with `solution` and says how it went |

Answers are read leniently: case, spaces, `$…$`, `\{ \}` and LaTeX subscripts
(`s_1`, `s_{1}`) do not matter, a single set may be written with or without
its braces, and the typed notations show how they are read as the student
types. Operators may be written as words (`seq`, `xor`, `and`, `loop`) and
`≫` as `>>`.

### Computed answers

`compute:` works the answer out from the exercise's own files, so it stays
right if you change the log or the net. `openprocess exercises computes` lists them
all with what each works out; the main ones:

| Of the log | Of the net (`net.pnml`, or `of: file.pnml`) | Of the transition system (`ts.txt`) |
|---|---|---|
| `activities`, `start activities`, `end activities`, `variants`, `cases`, `events` | `wf-net`, `sound`, `option to complete`, `proper completion`, `no dead transitions`, `dead transitions`, `dead(t)` | `regions`, `minimal regions` |
| `alpha.T_L`, `alpha.T_I`, `alpha.T_O`, `alpha.X_L`, `alpha.Y_L`, `alpha.P_L` (places typed `i_L, p({a},{b,e}), o_L`), `alpha.place(({a}, {b}))` | `bounded`, `bound`, `bound(p)`, `safe`, `live`, `deadlock-free`, `deadlocks`, `reversible`, `terminating` | `ger(e)`, `pre-regions(e)`, `post-regions(e)` (minimal) |
| `dfg`, `prefixes`, `language.M`, `language.M'` | `free-choice`, `well-structured`, `s-coverable` | `region({s1, s3})` (yes/no) |
| `im.cut`, `im.cut(2)`, `im.split(2)`, `im.tree`, `im.dfg(2)` | `net.P`, `net.T`, `net.F`, `net.m0`, `pre(t)`, `post(t)`, `enabled([p1])`, `fire(t, [p1])` | `elementary`, `state separation`, `forward closure` |
| `fitness`, `replay`, `alignment fitness(⟨a, b⟩)`, `alignment cost(⟨a, b⟩)` | `reachable`, `terminal`, `reachability graph`, `incidence`, `parikh(⟨a, b⟩)`, `state equation(⟨a, b⟩)`, `synchronous product(⟨a, b⟩)` | |
| `batches(a)`, `weekends`, `timeout(14 days)`, `arrival rate` (logs with timestamps) | | |

**Of a box or a workflow:** `box(alpha_miner).net.transitions` runs a box of
the workflow library (`openprocess boxes` lists them) on the exercise's log or net
and follows the path into its result; `settings: {"noise": 0.2}` sets the
box's settings. `workflow(orders.analysis).Check fit.metrics.fitness` runs a
workflow file given with the exercise and reads one box's result by its title.

### Nets

```answer
type: net
start: net.pnml        # start from this net (default: the exercise's net.pnml, else empty)
answer: answer.pnml    # same complete traces as this net (or: alpha, inductive)
sound: yes             # must be a sound WF-net
fits: log              # must replay every trace of the log
```

`answer:` compares *behaviour*, not drawings: the student's net is right when it
allows exactly the same complete traces (silent steps ignored, transitions
matched by label). When it is not, the student sees the shortest traces that
differ and can replay them in the token game. `answer: alpha` compares with the
net the α-algorithm discovers from the exercise's log; `exact: yes` asks for
the same net (places and arcs), not just the same behaviour.

### Traces

`ends:` is one of `any` (just firable), `final` (ends in the final marking),
`deadlock` (nothing enabled, case not completed), `stuck` (the final marking can
no longer be reached) or `improper` (a token in the sink with others left
behind).

### Workflows

```answer
type: workflow
needs: inductive_miner, check_fit      # boxes the workflow must contain (ids from openprocess boxes)
result: Check fit.metrics.fitness      # a box's result, by the box's title
answer: 1
tolerance: 0.01
```

The Workflow tab beside the sheet starts with the exercise's log in a box (or
from `start: given.analysis`); the student adds boxes and wires them, and the
workflow is saved as `my workflow.analysis`. Check runs it and looks for the
boxes in `needs:`, then compares the result named in `result:`.

## Points, exams and marks

Every block is worth its `points:` (1 by default), an exercise the sum of
its blocks (or `points:` in its front matter), and a partly right answer
earns a part. The overview and the foot of every sheet show the points so
far. `openprocess exercises marks "My pack"` prints them per exercise (`--blocks`
per answer box, `--csv` for a spreadsheet), and *⋯ ▸ Export Marks…* in the
app writes the same CSV.

An **exam** is a pack whose `pack.md` starts with

```
---
exam: yes
time: 120                 # minutes from the moment the pack is first opened
deadline: 2026-11-01 12:00   # or a fixed moment (the earlier of the two counts)
---
```

In an exam there are no hints and no *Show answer*, nothing in the materials
can be revealed, Check only says whether an answer is right, and the top bar
shows the clock. When the time is up the answers stay as they are and can
still be read, but not changed. The start is kept in `my exam.json` in the
pack's folder.

## Variants

With `seed: student` in `pack.md` (or in an exercise's front matter) and
`generate: net.pnml, cases: 20, length: 50` in the exercise, the exercise has
no log file: its log is played out from the net with a seed made from the
student's name, which the app asks for once (kept as `my name.txt`). Every
student gets a log of their own, and every `compute:` still checks every
answer. `seed: 7` gives one fixed log instead.

## Hiding results

While students work on an exercise, everything that would give answers away —
soundness and the other analysis results, footprints, discovered models,
conformance figures, regions — is hidden in the materials beside the sheet,
with a *Reveal* button on each (none in an exam).

## Checking a pack

Before sharing a pack, check it from a terminal:

```
openprocess exercises check "Week 3 — Discovery"
```

It lists every exercise and answer box, works out every computed answer, and
reports mistakes (an unknown key, a missing file, a compute it cannot do) with
their line numbers. Add `--answers` to print the right answers.
`openprocess exercises computes` lists every compute with what it works out.

## Turning a past exam into a pack

*Learn ▸ Make a Pack from an Exam…* (or `openprocess exercises import exam.txt
"Exam 2025"`) reads the exam's text — questions numbered `1.`, `2.`, … with
parts `a)`, `b)`, … and points in brackets — and writes a skeleton pack: one
exercise per question, an answer block per part whose type is guessed from
the wording ("is the net sound?" → `yesno` with `compute: sound`; "give a
firing sequence that ends in a deadlock" → `trace`; "draw" → `net`), and the
points carried over. Every `TODO` in the written `question.md` files is
yours to finish; `openprocess exercises check` then lists what is still missing.

- **Recreate given nets and logs as files** rather than pictures, so students
  can play the token game and the app can compute the answers. Check that the
  computed answers agree with the grading scheme (`--answers`): when they do
  not, look at the net again — a misread arc is the usual cause, but grading
  schemes have slips too.
- **Statements to judge** ("the model is live", "transition a is dead") become
  `yesno` blocks with `compute:`. A run of questions with the same answer for
  each transition can become one `set` block (`compute: dead transitions`).
- **Multiple-choice questions** keep their options. Where the exam accepted
  two answers, mark the best one and say in the `solution` that the other was
  accepted too (or make it `open` when both are equally right).
- **Calculations** become `number` blocks for the result, and a `replay`
  block for the intermediate figures (produced, consumed, missing and
  remaining tokens), so a student finds where they went wrong.
- **Modelling questions** become `net` blocks with `sound: yes`, the grading
  scheme as `solution` and the model answer as a picture in it.
- **Questions about material that is not in the pack** (a course dataset) can
  stay, with a sentence saying so and the figures they rely on.

## Older exercises

An exercise without answer blocks still works. A sheet written in lettered
parts (`a.`, `b.`, … at the start of a paragraph) gets a box under each part:
the net editor for the part that asks to draw or change a net, a text box for
the others, each with the same part of `answer.md` (`**a.** …`) as its model
answer. Otherwise, with an `answer.pnml` the student draws a net that is
compared with it, and with only an `answer.md` that file is the worked answer.
