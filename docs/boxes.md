# The boxes

Every box OpenProcess comes with, read from its signature by `docs/make_docs.py` (do not edit
by hand). A box is a Python function; *Code* in the app shows it and the algorithm it calls.
How to write your own: [workflows.md](workflows.md#writing-a-box); what a box *is*, formally:
[framework.md](framework.md).

[Input](#input) · [Filter](#filter) · [Discover](#discover) · [Check](#check) · [Measure](#measure) · [Compare](#compare) · [Output](#output) · [Science](#science) · [Predict](#predict) · [Coloured nets](#coloured-nets) · [Sweep](#sweep)

56 boxes.

## Input

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Open log](../openprocess/flow/boxes/input.py#L26)**<br/>Reads an event log: XES (also gzipped), CSV, or a ``.txt`` file in the course notation ``[<a,b,c>^3, <a,c>]``. | – | Log | `file` = None | – |
| **[Typed log](../openprocess/flow/boxes/input.py#L48)**<br/>A log typed in the course notation: ``[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]``. | – | Log | `text` = '[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]', `name` = 'Typed log' | – |
| **[Open object-centric log](../openprocess/flow/boxes/input.py#L54)**<br/>Reads an object-centric event log (OCEL 2.0 JSON, ``.jsonocel``, or the older JSON-OCEL): events that each involve several objects, by type. | – | Object-centric log | `file` = None | – |
| **[Flatten on an object type](../openprocess/flow/boxes/input.py#L68)**<br/>A classical event log with one case per object of one type, for the miners and checks that need a case: an event that involves several objects of the type is… | ocel: Object-centric log | Log | `object_type` = '' | – |
| **[Open net](../openprocess/flow/boxes/input.py#L86)**<br/>Opens a Petri net: a PNML file, or a net drawn in the editor. | – | Petri net | `file` = None | – |
| **[Open coloured net](../openprocess/flow/boxes/input.py#L98)**<br/>Opens a coloured Petri net (a CPN Tools ``.cpn`` file). | – | Coloured Petri net | `file` = None | – |
| **[Open transition system](../openprocess/flow/boxes/input.py#L111)**<br/>Opens a transition system typed as ``s0 -a-> s1, s0 -b-> s2`` (a ``ts.txt`` file). | – | Transition system | `file` = None | – |
| **[Open dataset](../openprocess/flow/boxes/input.py#L121)**<br/>A standard public log by name, fetched once into a shared cache and checked against its fingerprint, so the same name means the same bytes in every workflow… | – | Log | `name` ∈ {BPI Challenge 2012, BPI Challenge 2013 incidents, BPI Challenge 2017, BPI Challenge 2019, BPI Challenge 2020 domestic declarations, Sepsis cases, Road traffic fine management, Hospital billing} | – |
| **[Simulate log](../openprocess/flow/boxes/input.py#L137)**<br/>Plays the net out into a log, then adds noise: with probability *noise* a case loses one event or has two neighbouring events swapped. The seed makes it repe… | net: Petri net | Log | `cases` = 200, `noise` = 0.0, `seed` = 0, `max_length` = 200 | – |

## Filter

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Top variants](../openprocess/flow/boxes/filter.py#L23)**<br/>Keeps the cases of the *k* most frequent variants. | log: Log | Log | `k` = 3 | – |
| **[Variants covering](../openprocess/flow/boxes/filter.py#L36)**<br/>Keeps just enough of the most frequent variants to cover *percent* of the cases. | log: Log | Log | `percent` = 80.0 | – |
| **[Activities](../openprocess/flow/boxes/filter.py#L44)**<br/>Keeps only the events of these activities, or the cases that must (*mandatory*) or must not (*forbidden*) contain one of them. | log: Log | Log | `activities` = 'a, b', `mode` ∈ {keep events, mandatory, forbidden} | – |
| **[Start activities](../openprocess/flow/boxes/filter.py#L57)**<br/>Keeps the cases that start with one of these activities. | log: Log | Log | `activities` = 'a' | – |
| **[End activities](../openprocess/flow/boxes/filter.py#L66)**<br/>Keeps the cases that end with one of these activities. | log: Log | Log | `activities` = 'd' | – |
| **[Case length](../openprocess/flow/boxes/filter.py#L75)**<br/>Keeps the cases with between *minimum* and *maximum* events. | log: Log | Log | `minimum` = 1, `maximum` = 100 | – |
| **[Time frame](../openprocess/flow/boxes/filter.py#L85)**<br/>Keeps the cases contained in, started in, or touching the period. | log: Log | Log | `start` = '2000-01-01', `end` = '2100-01-01', `mode` ∈ {contained, started, intersecting} | – |
| **[Case attribute](../openprocess/flow/boxes/filter.py#L101)**<br/>Keeps (or removes) the cases whose attribute has one of these values, e.g. one item category of a purchase-to-pay log. Sweep *values* to get one log, and so… | log: Log | Log | `attribute` = 'Item Category', `values` = '', `mode` ∈ {keep, remove} | – |

## Discover

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[α-algorithm](../openprocess/flow/boxes/discover.py#L22)**<br/>The α-algorithm (van der Aalst, Weijters & Maruster, 2004): a Petri net from the footprint of the log, in the eight steps of the book. | log: Log | Petri net | – | – |
| **[Inductive Miner](../openprocess/flow/boxes/discover.py#L36)**<br/>Splits the log recursively into sequence, choice, parallel and loop parts (Leemans, Fahland & van der Aalst). With *noise* above 0 this is IMf, which ignores… | log: Log | Petri net | `noise` = 0.0 | – |
| **[Inductive Miner tree](../openprocess/flow/boxes/discover.py#L51)**<br/>The Inductive Miner's process tree itself, to convert or inspect. | log: Log | Process tree | `noise` = 0.0 | – |
| **[Tree to net](../openprocess/flow/boxes/discover.py#L59)**<br/>Translates a process tree into a sound WF-net (block by block). | tree: Process tree | Petri net | – | – |
| **[Heuristics Miner](../openprocess/flow/boxes/discover.py#L65)**<br/>Keeps the directly-follows relations whose dependency measure a ⇒ b = (\|a>b\| − \|b>a\|) / (\|a>b\| + \|b>a\| + 1) reaches the threshold, then turns the dependency… | log: Log | Petri net | `dependency` = 0.9 | – |
| **[Classical states](../openprocess/flow/boxes/discover.py#L84)**<br/>A transition system from the log with a classical state function: each prefix (or postfix) becomes a state through its sequence, multiset or set, over the la… | log: Log | Transition system | `direction` ∈ {prefix, postfix, both}, `representation` ∈ {sequence, multiset, set}, `horizon` ∈ {1, 2, 3, all} | – |
| **[Regions to net](../openprocess/flow/boxes/discover.py#L97)**<br/>Synthesises a Petri net from the minimal regions of a transition system: one place per minimal region (Cortadella et al.). | ts: Transition system | Petri net | – | – |
| **[Object-centric map](../openprocess/flow/boxes/discover.py#L113)**<br/>The object-centric directly-follows graph: every object followed through its events, so each object type gets its own directly-follows counts, drawn in its o… | ocel: Object-centric log | Object-centric map | – | – |
| **[Directly-follows graph](../openprocess/flow/boxes/discover.py#L127)**<br/>The process map: which activity directly follows which, how often. | log: Log | Directly-follows graph | – | – |
| **[Footprint](../openprocess/flow/boxes/discover.py#L135)**<br/>The → ← ‖ # matrix of the log's ordering relations. | log: Log | Footprint | – | – |

## Check

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Check fit](../openprocess/flow/boxes/check.py#L28)**<br/>Replays every case of the log on the model and scores fitness, precision (escaping edges), generalisation and simplicity. | model: Petri net, log: Log | Scores | `method` ∈ {Token replay, Alignments} | – |
| **[Token replay](../openprocess/flow/boxes/check.py#L54)**<br/>Token-based replay with the counts the course uses: produced, consumed, missing and remaining tokens, per variant. | model: Petri net, log: Log | Replay | – | – |
| **[Alignments](../openprocess/flow/boxes/check.py#L63)**<br/>Optimal alignments of every variant with the model (A* over the synchronous product, standard cost function). Shown as ProM shows them: the log on the model,… | model: Petri net, log: Log | Alignments | `variants` = 0 | – |
| **[Soundness](../openprocess/flow/boxes/check.py#L89)**<br/>Is the net a sound WF-net? The three conditions, with each violation's counterexample in the notes. | model: Petri net | Scores | – | – |
| **[Compare to true net](../openprocess/flow/boxes/check.py#L108)**<br/>Model to model, on behaviour: of the true net's complete traces of up to *max_length* activities, the share the model allows (recall); of the model's, the sh… | model: Petri net, true_net: Petri net | Scores | `max_length` = 12 | – |
| **[Footprint conformance](../openprocess/flow/boxes/check.py#L142)**<br/>Compares the footprint of the log with the footprint of the net. | log: Log, model: Petri net | Scores | – | – |

## Measure

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Time between](../openprocess/flow/boxes/measure.py#L40)**<br/>Throughput: per case, the time from the first *start* to the first *end* after it, summarised (cases, mean, median, 90th percentile, longest), for all cases… | log: Log | Table | `start` = 'Record Invoice Receipt', `end` = 'Clear Invoice', `by` = '', `unit` ∈ {days, hours} | – |
| **[Order rule](../openprocess/flow/boxes/measure.py#L83)**<br/>Does *first* happen before *then*? Every case falls in one row: *first* before any *then* (and whether *then* follows at all), *then* first, only one of them… | log: Log | cases: Log, table: Table | `first` = 'Remove Payment Block', `then` = 'Record Goods Receipt', `value` = 'Cumulative net worth (EUR)' | – |
| **[Segregation of duties](../openprocess/flow/boxes/measure.py#L127)**<br/>Four eyes: the cases where the same person (the event attribute *by*) did both *first* and *second*, per person, with the cases' total value, most value firs… | log: Log | Table | `first` = 'Record Invoice Receipt', `second` = 'Clear Invoice', `by` = 'User', `value` = 'Cumulative net worth (EUR)' | – |
| **[Compliance by attribute](../openprocess/flow/boxes/measure.py#L158)**<br/>For every value of a case attribute (each vendor, say): how many cases, the share that fit the model (replay with no missing or remaining tokens), and their… | model: Petri net, log: Log | Table | `attribute` = 'Vendor', `value` = 'Cumulative net worth (EUR)', `colour` = 'Item Type' | – |

## Compare

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Compare](../openprocess/flow/boxes/compare.py#L10)**<br/>Puts any number of scores side by side, with columns taken from the scores themselves. Under a sweep it collects the scores of every value. | scores: Scores (any number) | Table | – | – |

## Output

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Save as PNML](../openprocess/flow/boxes/output.py#L20)**<br/>Writes the model as PNML (ProM, WoPeD and PM4Py read it). | model: Petri net | – | `file` = 'model.pnml' | – |
| **[Save log](../openprocess/flow/boxes/output.py#L28)**<br/>Writes the log as XES (``.xes`` or ``.xes.gz``) or CSV (``.csv``). | log: Log | – | `file` = 'log.xes' | – |
| **[Save table](../openprocess/flow/boxes/output.py#L41)**<br/>Writes a table as CSV. | table: Table | – | `file` = 'table.csv' | – |
| **[Save figure](../openprocess/flow/boxes/output.py#L48)**<br/>Writes a figure as SVG or PNG, by the file's extension. | figure: Figure | – | `file` = 'figure.svg' | – |

## Science

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Describe log](../openprocess/flow/boxes/science.py#L23)**<br/>pandas' ``describe()`` of the events per case and, when the log has timestamps, of the case durations in hours. The log goes in as a DataFrame through the co… | log: Log | Table | – | pandas |
| **[Bootstrap fitness](../openprocess/flow/boxes/science.py#L42)**<br/>Fitness with a 95 % bootstrap interval: resample the cases with replacement *samples* times and replay each sample, so a number comes with its uncertainty. | model: Petri net, log: Log | Table | `samples` = 200, `seed` = 0 | numpy |
| **[Compare samples](../openprocess/flow/boxes/science.py#L76)**<br/>Do two models score differently? *Bootstrap difference* (the default, for two Bootstrap fitness tables): the 95 % interval of A − B. When both tables were re… | a: Table, b: Table | Table | `test` ∈ {Bootstrap difference, Mann–Whitney U, t-test} | – |
| **[Correlate](../openprocess/flow/boxes/science.py#L146)**<br/>Correlations between the numeric columns of a table (pandas), e.g. a swept noise level against fitness. | table: Table | Table | – | pandas |
| **[Plot](../openprocess/flow/boxes/science.py#L158)**<br/>Bars per model for scores, histograms for bootstrap tables, or column *y* of a table against column *x*: bars when *x* holds names (a model per bar), a line… | scores: Scores (any number), tables: Table (any number) | Figure | `x` = '', `y` = '' | matplotlib |
| **[Bubble chart](../openprocess/flow/boxes/science.py#L223)**<br/>One bubble per row: *x* against *y*, its area by *size* and its colour by *colour* (a column of names), as in a compliance-per-vendor chart. | table: Table | Figure | `x` = 'cases', `y` = 'compliance (%)', `size` = '', `colour` = '', `log_x` = True | matplotlib |

## Predict

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Prefixes](../openprocess/flow/boxes/predict.py#L28)**<br/>One row per prefix of every case, encoded as features, with the label to predict: the next activity (⟂ when the case ends), the remaining time in hours, or t… | log: Log | Dataset | `label` ∈ {next activity, remaining time, outcome}, `encoding` ∈ {index, one-hot, frequency}, `min_prefix` = 1, `max_prefix` = 20 | – |
| **[Split by time](../openprocess/flow/boxes/predict.py#L97)**<br/>Train and test sets by case start: the earliest cases train, the latest test, so the model never sees the future. Without timestamps the cases are split in l… | dataset: Dataset | train: Dataset, test: Dataset | `train_fraction` = 0.7 | – |
| **[Frequency model](../openprocess/flow/boxes/predict.py#L151)**<br/>The baseline: for each last activity, the most frequent next activity (or the mean remaining time) in the training set. | train: Dataset | Prediction model | – | – |
| **[Predict](../openprocess/flow/boxes/predict.py#L160)**<br/>Applies a fitted model to a dataset (the test set). A Predictor of OpenProcess's own takes the dataset; any other model (a scikit-learn estimator) is given i… | model: Prediction model, dataset: Dataset | Predictions | – | – |
| **[Evaluate predictions](../openprocess/flow/boxes/predict.py#L170)**<br/>Accuracy (classification) or mean absolute error in hours (regression), overall and per prefix length. | predictions: Predictions | Table | – | – |

## Coloured nets

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Simulate CPN](../openprocess/flow/boxes/cpn.py#L17)**<br/>Runs the simulator for *steps* firings and turns the firing history into an event log: one case per value of *case_variable* (the variable that identifies a… | net: Coloured Petri net | Log | `steps` = 500, `case_variable` = '', `seed` = 0, `unit` ∈ {seconds, minutes, hours, days} | – |
| **[State space](../openprocess/flow/boxes/cpn.py#L41)**<br/>The reachability graph of a coloured net and its report: dead and home markings, bounds, dead and live transitions (as CPN Tools reports them). The full repo… | net: Coloured Petri net | Scores | `max_nodes` = 10000 | – |

## Sweep

| Box | Takes | Gives | Settings | Needs |
|---|---|---|---|---|
| **[Sweep table](../openprocess/flow/boxes/sweeps.py#L10)**<br/>Collects the scores of every value of a sweep into one table: the swept settings as columns (from the scores' context), then the scores. Feed it to Plot (wit… | scores: Scores (any number) | Table | – | – |
