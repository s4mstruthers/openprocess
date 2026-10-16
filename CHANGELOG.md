# What's new in OpenProcess

Newest first. When a new version is out, OpenProcess shows its section here
(and those of any other versions you skipped) before offering to install it,
and each [release on GitHub](https://github.com/s4mstruthers/openprocess/releases)
uses it as its description. Write for the people using the app: what they will
notice, not how it was done. Versions before 0.7.0 were released as CPNpy.

## 0.11.3

- **A τ tool.** The Petri net editor's toolbar and the Model palette have
  a fifth tool, **τ**: click the canvas and a silent transition (the black
  bar) is there, with no name to type. The Element tab's *Silent* box still
  turns an ordinary transition into τ and back, and PNML files keep it as
  before.
- **The Select tool's icon is a mouse pointer**, on the page's toolbar and in
  the Model palette, instead of a dotted box that looked like one more
  shape to draw. The palette now shows the same painted icons as the toolbar
  (a circle, a square, the τ bar, an arrow).
- **Subfolders fold.** A click on a subfolder's caption in the sidebar
  ("Week 5 / Part 1") folds its files away, or brings them back; the chevron
  shows which. The app remembers the folded ones in the folder's
  `.openprocess` file, like the LOGS section, and a file you open inside a
  folded subfolder unfolds it.
- **+ Add box shows every box, in sections that scroll.** One section per
  group, its name on a rule, the boxes in three columns: Input, Filter,
  Discover, Check, Compare, Output, then Science, Predict, Coloured nets,
  Sweep and your own. The list scrolls when it is taller than fits, so
  nothing is folded away under *Show all* and nothing runs off the screen.
  Search works as before: type, and Return adds the first match.
- **Export image draws the net, not the session.** No green on the
  transitions enabled now, no selection ring, no halo on the one that fired
  last, no trace badges, no arc-drawing arrow or hover handles, on the Petri
  net page and on Mine's model page alike. The tokens stay: the marking is
  part of the model. The canvas gets its highlights back the moment the file
  is written.
- **Fixed: alignments on an unbounded net ran the app out of memory.** A
  transition with no input place, or a loop that puts back more tokens than
  it takes, gives the model infinitely many runs cheaper than one deviation
  (a τ move costs 1, a deviation 10 000), and the search looked at all of
  them. Two changes in `align_trace`: a marking with tokens in a place no
  transition consumes from, beyond what the final marking asks for, is a dead
  end and is not explored (that alone settles the no-input-place case at
  once); and the state limit now counts the states discovered, frontier
  included, so the memory a search can take is bounded too. A search that
  still gives up says so: the variant's cost shows as "?", its fitness
  counts as 0, and the result's notes (on the Check tab's Variants card, in
  the box's How tab, from `openprocess mine conform`) say how many searches
  stopped and whether the net is unbounded.

## 0.11.2

- **The Inductive Miner's sequence cut now agrees with the reference
  implementation.** Two changes to `sequence_cut`, both from Leemans'
  thesis (*Robust Process Mining with Guarantees*, 2017), which is what
  ProM and PM4Py follow: (1) a cut is accepted only when *every* activity of
  an earlier group reaches *every* activity of a later one, as the
  definition says, not when some pair does; (2) the *strict* step: groups
  that can only be skipped together become one group, so an optional
  stretch `a, c` is `×(τ, →(a, c))` rather than `×(τ, a), ×(τ, c)`, which
  also allowed `<b, a>` and `<b, c>` that never happened. The How tab says
  when the strict step merged groups, and Cite on the box lists the thesis.
  The cross-check against PM4Py goes from 369 identical trees of 406 to
  401; the five that remain are fall-throughs on logs with no cut, tried in
  a different order, and the guarantees (a sound net, fitness 1) hold on
  every log before and after. Models discovered from logs with skippable
  stretches change; models of the textbook logs do not.
- **Fixed: Describe log and Correlate failed with "No converter from
  EventLog to pandas.DataFrame" whenever pandas was installed.** A converter
  was registered under the class's defining module
  (`pandas.core.frame.DataFrame`) and asked for by its public name
  (`pandas.DataFrame`); the registry now resolves both to the class, in
  either direction, and still imports nothing until a conversion is asked
  for. The same fix covers pandas 3, which reports its classes under
  `pandas` rather than `pandas.core.frame`.
- **What "reproducible" promises is written down**, in
  [docs/workflows.md](docs/workflows.md#what-reproducible-promises): the
  badge and `--check` mean fingerprints equal to the record's; three tiers
  say how far that travels (same machine, same lockfile, another platform),
  and what is and is not in the record.
- **docs/design.md: why OpenProcess is built the way it is.** The problem,
  the claim, the three ideas and the decisions that follow from them, with
  the open decisions (the `.cpnflow` name, what the 1.0 freeze covers).
- **A cross-check of the Inductive Miner against PM4Py**,
  `tests/cross_check_inductive_miner.py`: both miners on the textbook logs
  and 400 random ones, trees compared after sorting the commutative
  operators, the IM guarantees checked on every log, and the precision of
  both models wherever they differ. Baseline against PM4Py 2.7.23: the
  guarantees hold on all 406 logs; 369 trees are identical; on 37 the
  reference model is usually more precise, always for the same reason: after
  a sequence cut, groups that are only ever skipped together are made
  skippable separately. Not fixed yet; the script is there to show when it
  is.

## 0.11.1

- **A click no longer flings a box across the canvas.** Clicking a box
  opens the side panel, the canvas narrows and re-fits itself, and that
  happened while the mouse was still down on the box, so the slightest
  movement before letting go moved it far away. The canvas now waits for the
  button to come up before it re-fits.

## 0.11.0

- **Undo and redo on the canvas.** ⌘Z takes an analysis back to before the
  last change (a box added or removed, a connection, a move, a setting, a
  group), ⇧⌘Z brings it again, and the right-click menu says what each would
  undo. The boxes run again, with the cache serving the ones that did not
  change.
- **Export as Python script** (under ⋯): a `.py` with the same boxes,
  settings and connections, which `python analysis.py` runs on its own,
  printing what each box gives and writing the results beside it. The
  folder's own boxes come along from `boxes/`. The exported experiment zip
  carries the same script as `run.py`.
- **An exported experiment opens again.** Drop the zip on OpenProcess (or
  File ▸ Open it): it is unpacked into a folder of its own, the app works in
  that folder, and the analysis runs and is checked against its record. The
  inputs are found under `inputs/`, the folder's boxes under `boxes/`.
- **Every result knows where it came from.** A figure saved as SVG carries
  the analysis, the box, its settings and the result's fingerprint in its
  metadata; a table saved as CSV (and a figure as PNG) gets a
  `.provenance.json` beside it. A figure in a paper points back at the
  analysis that made it.
- **Installing shows pip's own lines** as they come, on the tool's card,
  with the command it runs: no more wondering whether it is stuck.
- **Result tables sort and copy.** Click a column heading to sort (numbers
  as numbers), ⌘C copies the selected rows for a spreadsheet, and *Copy as
  CSV* and *Save as CSV…* sit under the table.
- **Your own boxes reload when you save them.** A file under `boxes/` saved
  in an editor is loaded again by itself, the message names the file, and
  the boxes it defines run again.
- **The downloaded app checks itself for installs.** The build's self-test
  runs pip inside the app and imports the whole standard library, so a
  release in which Install… or an installed package would fail is never
  published.
- A folder's own boxes keep working when the folder is moved, copied or
  imported: a box is found by its function name in `boxes/` when its old id
  no longer matches.
- **Plot's *x* and *y* draw what they say.** Set them and the figure shows
  column *y* of the connected table against column *x*, bars when *x* holds
  names (a model per bar) and a line when it holds numbers, whatever else is
  connected. Before, scores plugged into the box always gave their bars and
  the two settings changed nothing. A column a table lacks is named in the
  error, with the columns it has.
- **The process map fits the window.** On the Summary page the map took the
  drawing's own height, up to three quarters of the screen, and the page
  scrolled; it now takes the room the window has below the tiles, and the
  map fits itself to it.
- A formula in a worksheet that wraps over two lines is typeset as one
  formula; before, the words between it and the next formula were taken for
  maths ("andthenetN"). A property's ⓘ card no longer scrolls by a few
  pixels. On Windows and Linux the menu bar follows dark mode.
- The README's screenshots show this version, made from the app itself by
  `docs/make_screenshots.py`.

## 0.10.6

- **The reproducibility badge is a quiet pill** beside the title: a soft tint
  with a dot and a few words ("● Reproducible", "● 2 changes since",
  "● Not recorded"), the full sentence on hover. It no longer pushes the
  title aside, and when the header is short of room it keeps only its dot.
- **Install a tool from the downloaded app too.** Connections ▸ *Install…*
  now works in the app you download, not only from a Python install: the
  app carries pip, installs into a folder of your own (on a Mac,
  Library/Application Support/OpenProcess/packages), and the boxes that need
  the package come alive without a restart. Ready-built packages only, so
  PM4Py still needs a Python install.
- **A page opened from a box is a view, not a file.** *Open as log ›* no
  longer writes a file into the folder the moment it opens. The page has
  *Keep in folder* for when you want one; ✕ closes it without a question and
  leaves nothing in the sidebar. Files you opened yourself still close to a
  lighter row, and deleting stays an explicit Move to Bin.
- The status chips on cards (*not installed*, *installing…*, *ready*, the
  Learn chips) are pills everywhere. They draw themselves now: a styled
  label's corners depended on when the style polished it, and the downloaded
  app showed square chips beside round ones.
- **A column setting is a dropdown.** Plot's *x* and *y* list the columns of
  the table connected to the box, once it has run; a name can still be
  typed. A box author writes `x: Column = ""`, nothing more: the new
  `Column` type is a plain string to the function.
- A box with nothing plugged in (Plot, just added to the canvas) waits with
  *Connect scores or tables* instead of failing with a traceback.
- The downloaded app carries the whole standard library, so a package
  installed from Connections can import what it needs (matplotlib wanted
  `unittest`, and Plot failed after the install).
- Releases are archived on Zenodo; the DOI goes into `CITATION.cff` and the
  app's citations once the first archive is there.

## 0.10.5

- **Object-centric event logs.** *Open object-centric log* reads OCEL 2.0
  JSON (`.jsonocel`) and the older JSON-OCEL: events that each involve
  several objects, by type, with no case id invented. *Flatten on an object
  type* makes a classical log with one case per object of a type, for the
  miners and checks that need one, and says what it copied. *Object-centric
  map* draws the object-centric directly-follows graph: the activities
  once, one set of arrows per object type in its own colour, with a
  legend and the counts per type. A `.jsonocel` in the folder is listed
  under LOGS, drops on the canvas, and appears in the Summary and the
  report like any other result.

## 0.10.4

- **A command palette.** ⌘K (Ctrl+K), or *View ▸ Command Palette…*: type a
  few letters and the open analyses, models and logs, the folder's files,
  every box (added to the current analysis), the three spaces, Connections
  and every menu action are found at once; Enter runs the best match, ↑ ↓
  pick another. Nothing new on the window; everything in it a few
  keystrokes away.

## 0.10.3

- **Runs: every finished run, kept.** An analysis has *Canvas | Summary |
  Runs*. Each finished run is a snapshot with the time, what changed before
  it ("Inductive Miner: noise 0 → 0.2", "+ Check fit", "Run again, same
  results") and every box's key figure. Pick A and B and the boxes line up
  side by side, those whose result differs marked. *Use A's settings* puts
  that run's settings back on the canvas and runs. The last twenty travel
  with the workflow file.

## 0.10.2

- **Export report.** ⋯ ▸ *Export report…* on an analysis writes one HTML
  page, with nothing to install: the reproducibility status, the key figure
  of every box, each box's result (nets, maps and trees drawn; tables and
  scores as tables), how it got there, the code it ran and the papers it
  follows, what it read with fingerprints, and how to cite. For a
  supervisor, a reviewer, a hand-in. It opens in your browser when written.

## 0.10.1

- **Step through an algorithm.** A box's *How* tab has *Step through ▸*: the
  report plays one moment at a time (a note, a step of the derivation, a
  value shown), with ◀ ▶, Play and the arrow keys, and the box's result drawn
  above it. Each step lights up on the drawing what it names and dims the
  rest, so the α-algorithm's eight steps become eight pictures: all the
  activities, the start ones, the end ones, the candidate pairs, the maximal
  pairs, the places. Nothing new is computed: the moments are the box's own
  report, in order.

## 0.10.0

Two things an academic looks for first: is this reproducible, and how do I
cite it.

- **A reproducibility badge beside every analysis's title.** "Not recorded
  yet"; "Recorded · this run reproduces it" when every result matches the
  record's fingerprints and nothing the analysis depends on has changed;
  "Recorded · n changes since" when an input file, a box file, a seed or a
  version differs (hover for which); "This run differs from the record"
  when the same inputs gave another result. Click it for the record. In a
  folder the record follows each run by itself.
- **Cite.** *Help ▸ Cite OpenProcess…* gives the app's BibTeX and a one-line
  citation. On a box's Code tab, each algorithm has *Cite* with BibTeX for
  the paper it follows, generated from the reference list. An exported
  experiment includes `CITATIONS.bib` and a *How to cite* section. The
  repository has a `CITATION.cff`, so GitHub offers *Cite this repository*.
- **Fixed: an input file edited on disk was served from the cache.** A box
  reading a file is now keyed by the file as it is (size and time), so a
  re-run after editing the log computes again instead of repeating the old
  result.

## 0.9.5

- **The log page no longer discovers models by itself.** Its *Discover* tab
  used to run a miner on the spot, with no code to read and no record. Now
  it hands the log to the canvas: pick a miner and a new analysis opens in
  Mine with *Open log* and the miner connected and run, the miner's Result
  open, its *How* and *Code* a click away. (In Learn, an exercise's log page
  keeps its miners, since the exercise is the context there.)
- **Every computed figure links to its code.** What a page computes on its
  own (the log page's process map and footprint, a net's soundness,
  short-circuited net, structure, invariants, behavioural properties and
  footprint, a coloured net's state space, a transition system's regions and
  synthesised net) now has a small *{ } code* link on its card that opens the
  file, scrolled to the function. Nothing in the app is a black box.
- **A model page's conformance is a handoff too.** *Check against a log ›*
  on a discovered model builds the analysis in Mine (the model is kept in
  the folder first). The Petri net page's *Conformance with a log…* is gone;
  its Analysis tab has the same button. (In Learn, the exercise's model page
  keeps its replay, as the exercise asks for it.)
- **Install a tool from Connections.** A tool that is not installed has
  *Install…* on its card: after asking, pip installs it into the app's
  Python in the background, and the boxes that need it come alive. The
  downloaded app, whose Python cannot install packages, says so and gives the
  command instead.

## 0.9.4

Motion: a few short transitions, so the window reads as one flow. Nothing
moves for its own sake.

- **A space switch settles.** Switching Mine, Model or Learn lifts a veil
  over the window in about a fifth of a second, so the new space arrives
  rather than snaps. The side panel on an analysis and Canvas ↔ Summary do
  the same.
- **A box pulses once when it finishes**, green for done, red for failed,
  so your eye finds what just changed on the canvas.
- **+ Add box fades in.**
- **Off switch:** set ``OPENPROCESS_NO_MOTION=1`` before starting the app
  (for screenshots, slow machines, or if motion bothers you).
- **Fixed:** zoomed out on a net, the arrow for drawing an arc could stay
  beside one node while the mouse was over its neighbour. Over a node the
  arrow now always gives way, whatever the zoom.

## 0.9.3

The patterns worth borrowing from the process-mining tools, without their
chrome.

- **Summary: the key figure of every box, in one place.** An analysis has
  *Canvas | Summary* in its header. The Summary is a tile per box, in the
  order the boxes run: a score's first metric, a table's size, a log's
  cases, a net's places and transitions. Nothing is computed there: a tile
  is the box's result, as on the canvas, so it can never drift from the
  workflow, and a box author gets a tile by returning a Scores. A tile
  opens the box.
- **The process map in the Summary is a box's result too**: the
  *Directly-follows graph* box's, drawn with a slider that hides the rarer
  activities and paths of the drawing (the result is unchanged) and a
  legend. An analysis without that box offers to add it, fed by the first
  log, in one click.
- **The Model palette.** In Model, a net page's Select, Place, Transition
  and Arc sit in the sidebar under the models, like a modeller's shape
  palette. The page's own buttons come back when the sidebar is hidden.
- **Connections.** At the bottom of the sidebar (and View ▸ Connections):
  the hub picture. What flows in (event logs, PNML, CPN Tools files,
  transition systems, typed logs, datasets, simulations), OpenProcess in the
  middle with its boxes and the folder's own, what flows out (PNML, XES,
  CSV, figures, the experiment zip, Python), and the tools that plug in
  (pandas, numpy, scipy, matplotlib, …), each saying whether it is
  installed, how to install it, and which boxes it brings. All read from
  the box library; nothing to declare.

## 0.9.2

The handoffs between Mine and Model: four, each one button, each moving the
whole window and leaving a way back.

- **Open a copy in Model ›** on a discovered net's Result. It was "Edit a
  copy"; the word *copy* stays everywhere: the model is named "… (copy)",
  its header says "‹ A copy, from Inductive Miner in Discover and check" (a
  link back to the analysis), and the result in the workflow is never
  changed.
- **A discovered net can be tidied in Mine, but not changed.** On a box's
  Result, drag places and transitions to make the drawing easier to read;
  the edges follow and the layout is saved with the workflow. Nothing can be
  added, removed or renamed there: that is what the copy in Model is for.
- **Mine a simulated log ›** on a Petri net, a coloured net or a model page
  (it was "Export as event log…" / "Generate event log…"). The log is saved
  in the folder next to the model and opens in Mine as a new analysis with
  an *Open log* box reading it. Without a folder it opens as a log page, as
  before.
- **Check against a log ›** on a Petri net's Analysis tab. Pick a log and
  Mine opens a new analysis with *Open net* (this net's file), *Open log*
  and *Check fit*, connected and run.
- *Open net*'s Choose… and file list already offer the folder's models.

## 0.9.1

Files flow into the canvas, so the analysis says what feeds it and the
sidebar is not the only way to a file.

- **An input box names its file.** *Open log* reading boarding.xes shows
  "INPUT · boarding.xes" on the canvas.
- **The folder's files are in + Add box.** Under *Input*, the folder's logs,
  nets and transition systems are listed by name; one click adds the box
  that reads the file, already set. Typing in the search finds them too.
- **Drop a file on the canvas** and it becomes its input box at that spot: a
  log an *Open log*, a PNML file an *Open net*, a .cpn an *Open coloured
  net*, a ts.txt an *Open transition system*. A file from outside the folder
  is kept by its full path.
- **Mine's *Open log…* and *Log from notation…* land as boxes** on the
  current analysis, or on a new one named after the file, instead of opening
  a page of their own. (File ▸ Open… still opens pages.)
- **A page opened from a box has a way back.** *Open as log ›*, *Open as
  model* and *Open as coloured net* now put "‹ Back to <analysis>" above the
  page's title; it returns to the analysis, switching the space if need be.

## 0.9.0

Three spaces: **Mine**, **Model** and **Learn**, switched at the top of the
window. Process mining and net modelling no longer share a sidebar, so the
window always says which job you are doing. One folder underneath; nothing
moves on disk.

- **Mine** is for event logs and analyses. Its sidebar lists the folder's
  analyses (workflows) and, folded under them, its logs. Everything you open
  or make here lands in this list: a log, a typed log, a comparison, a
  transition system, an analysis.
- **Model** is for Petri nets and coloured nets. Its sidebar lists the
  folder's models. New Petri net, New coloured net and Compare nets… live in
  its footer.
- **Learn** is the exercise view, as before. From the switcher it opens the
  folder's exercises, or the demo ones when the folder has none; Exit brings
  you back to the space you left.
- **A document opens in its own space.** Open a net from Mine and the window
  moves to Model; "Edit a copy" of a discovered net does the same. Each space
  remembers what you had selected, and the folder remembers which space you
  were in.
- **Flat lists, the same in both spaces.** The folder's own files come
  first, then each subfolder's under a small caption naming it ("Week 5 /
  Part 1"). Nothing is nested and nothing to unfold except LOGS, so the
  sidebar reads the same way whichever space you are in. A folder with
  nothing in it yet shows in both, so you can put things in it. Exercises
  and their materials belong to Learn and are not listed in Mine or Model.
- **Gone:** the Folders / By kind toggle, the UNSAVED, EVENT LOGS, PETRI NETS,
  MODELS, COLOURED NETS, TRANSITION SYSTEMS and WORKFLOWS headings in one
  list, and "Untitled" drafts of nets sitting among logs.
- The welcome page has one card per space. View ▸ Mine, Model and Learn
  (⌃⌥1, ⌃⌥2, ⌃⌥3) switch too.
- **A file box lists only the files it can read.** *Open log* offers the
  folder's event logs, *Open net* its PNML files, *Open coloured net* its
  .cpn files, *Open transition system* its ts.txt files, by name, with the
  subfolder shown only when two names clash. *Choose…* filters the same way.
  Picking from the list works on macOS now (it flickered shut before), and
  the panel no longer runs off the edge in a folder with long paths.

## 0.8.1

- **A file box has a *Choose…* button.** *Open log*, *Open net*, *Open
  coloured net* and *Open transition system* no longer ask you to type a
  path: press *Choose…* under *file* in Settings and pick the file. A file
  inside the workflow's folder is kept by its name, so the workflow still
  works when the folder moves; one elsewhere is kept by its full path.
- **A typed or pasted path works as it is.** Quotes around it, spaces
  escaped the shell's way (`Week\ 2`) and a leading `~` no longer make
  the box fail with "No such file".
- ***+ Add box* is a rounded card with a soft shadow**, like the app's
  menus, instead of a square box with a hard edge.
- **The Code tab says which code is which.** Two cards: *This box*, the
  few lines that make the box, and *The algorithm it calls*, the function
  the box hands the work to. Under that function a line says how much of
  its file it is ("inductive_miner is 12 of inductive.py's 478 lines; the
  other 466 are the algorithm's helpers and its docstring") and that
  *Whole file ⤢* shows them.

## 0.8.0

A calmer Workflows page. Nothing is gone; what you rarely need has moved out
of the way.

- **The page opens with the workflow alone.** No box list on the left, no
  empty side panel on the right, and three header buttons instead of five:
  *+ Add box*, *Run ▶*, and *⋯* for Re-run, Record, Export experiment and
  Save.
- **+ Add box** (or a double-click on the canvas) opens every box by group:
  the six core groups side by side (Input, Filter, Discover, Check, Compare,
  Output) and one line for the rest (Science, Predict, Coloured nets, Sweep,
  your own boxes) with *Show all*. Typing searches them all, and "alpha"
  now finds the α-algorithm. Enter adds the first match.
- **The side panel appears when you click a box**, on its *Result*, and the
  tab you pick stays while it is open. *✕*, or a click on the canvas, puts
  it away. *View ▸ Show Box List* and *Show Box Panel* are gone with the
  panels they toggled; *Reset Workflow Layout* stays, for the panel's width.
- **A log box says where the dotted chart is.** Its *Result* now has one
  *Open as log ›* card above the variants: dotted chart, process map,
  footprint, variants and cases, on the log page.
- **One way into workflows: File ▸ New Workflow** (and the welcome page).
  The *As a workflow* buttons on a log's Discover tab and a model's
  Conformance tab, and *Use in a workflow* on the net canvas, are gone: they
  built workflows nobody asked for, and the templates do the same thing with
  a name.
- **A box that needs a file waits for it.** *Open log*, *Open net* and the
  other boxes that read a file used to run the moment they were added, and
  fail in red with a traceback about a missing path. They now wait, say
  "Choose the file in Settings", and the Settings tab opens by itself when
  you add one.
- **Fixed: adding a box to an opened workflow could fail** with "There is
  already a box with id 'n3'". Box ids are numbered per app session, so a
  workflow opened from a file made earlier could already hold the next
  number; a new box now takes the first number the workflow does not have.

## 0.7.2

- **A failed box says what went wrong in its own words first.** *Open
  dataset* without the file in the cache, for example, now shows "Sepsis
  cases is not in the cache. Download … and put it in …" at the top of its
  Result tab, with the download page and the cache folder as links, and the
  traceback underneath for whoever fixes the box. Before, the traceback came
  first and the message was below the fold.

## 0.7.1

- **Fixed: *New workflow* did nothing in the downloaded app.** The app was
  built without the standard boxes (the library imports them by name, which
  the bundler could not see), so a new workflow failed before it opened.
  The app's build test now builds and runs a workflow before an app is
  published.
- **The Code tab works in the downloaded app.** The app now carries the
  source of every box and algorithm, so *Code* shows the code it runs, as
  it does from a checkout.
- **Fixed:** the Code tab's file paths were wrong when the repository's folder
  is itself called `openprocess` (as a clone now is).
- **An error you were never shown** (one the app did not expect) now opens
  a dialog with the details to report, instead of disappearing.
- **Updating from pip or from a checkout.** *Help ▸ Check for Updates…*
  now knows how OpenProcess was installed. A `pip install openprocess[app]`
  updates itself with pip and restarts; a git checkout pulls, reinstalls
  and restarts (it refuses while there are uncommitted changes); a
  downloaded app installs the download as before.
- **Clearer when it cannot update.** A release without a download for your
  system, or a copy of the source that is not a git checkout, now says so
  instead of the old "you are running from source" text.

## 0.7.0

- **CPNpy is now OpenProcess.** The name said "coloured Petri nets"; the app
  is a workbench for process mining, Petri nets and workflows where every
  algorithm is open to read, every result reproducible and every step
  teachable. Same code, new name and logo: the app is **OpenProcess
  Studio**, the teaching mode **OpenProcess Learn**, the package
  `openprocess` (`pip install openprocess`, with `[app]` for the window),
  the commands `openprocess`, `openprocess-studio` and `openprocess-cpn`.
- **Nothing of yours breaks.** `import cpnpy` keeps working (it is
  `openprocess` under its old name, with a one-line warning), so boxes and
  exercise packs written for CPNpy run unchanged. A folder's hidden state
  and notes, your settings and the datasets cache are carried over the
  first time you open them. Workflow files keep the `.cpnflow` extension.
- **Updating from CPNpy is by hand, once.** A CPNpy app looks for files
  named `CPNpy-…` and will not find this release's `OpenProcess-…` files,
  so its update dialog can only open the release page: download
  OpenProcess from there and drag it over. From 0.7.0 on, OpenProcess
  installs updates itself again.

## 0.6.0

- **Workflows.** A new kind of file, `.cpnflow`: boxes on a canvas, wired
  together. *File ▸ New Workflow* opens one that already runs (a log, a
  miner, a fitness check; or three miners compared; or a setting swept over
  a range; or the prediction pipeline). Click a box to see its **Result**,
  **How** it got there (the α-algorithm's eight steps, the Inductive Miner's
  cuts, the replay per variant), its **Code** (the box's own lines and the
  algorithm it calls, with the published source it follows) and its
  **Settings**; every tab
  opens in a window of its own. Change a setting and only the boxes after
  it run again. Drag from the dot on the right of a box to connect it: only
  the inputs that fit light up. Workflows live in the folder like logs and
  nets, and are saved as you go. Your own box is a Python function in the
  folder's `boxes/` subfolder; the app asks once before running them.
- **Workflows, from Python.** The new `cpnpy.flow` framework: every
  algorithm is a *box* (a Python function with type hints), an analysis is
  a *workflow* of boxes you can run, save as a `.cpnflow` file and re-run
  headless with `cpnpy run`. The file records the fingerprints of its
  inputs and box code, every seed, and the installed packages, and
  `cpnpy run --check` fails when a result no longer matches the record.
  Forty-seven boxes come with it (the discovery algorithms, conformance,
  filters, comparing, saving, simulating plain and coloured nets, sweeps,
  a prediction pipeline, and pandas, NumPy, SciPy and matplotlib examples
  behind `pip install cpnpy[science]`), and your own box is one function in
  a `boxes/` folder. See `docs/workflows.md`.
- **Groups and composite boxes.** Select boxes on the canvas and ⌘G makes
  one box of them, with the connections that reach outside; double-click
  opens the group's own canvas, ⇧⌘G ungroups. The side panel shows a group
  as Python, and *Save as a box* writes it to the folder's `boxes/`, where
  it is listed under *Yours*. In Python, a `@workflow` function with typed
  parameters is itself a box.
- **From a page to a workflow in one click.** *As a workflow* on a log's
  Discover tab and on a model page, and *Use in a workflow* on the net
  canvas, open a workflow with that file as its source.
- **Export experiment.** A button on the Workflows page (and
  `cpnpy run --export`) writes a zip with the workflow file and its record,
  the input files, your boxes, every result as a file (CSV, PNML, SVG,
  XES), a `requirements.lock` and a README that says what was run:
  supplementary material for a paper.
- **The Workflows page's panels** can be dragged, hidden (*View ▸ Show Box
  List*, *Show Box Panel*) and reset (*View ▸ Reset Workflow Layout*); the
  layout is remembered. The Result tab shows the discovery derivation the
  Discover tab shows.
- **Learn.** Exercises are now *CPNpy Learn*, a mode of its own on top of
  the app, under the new **Learn** menu. Twelve new kinds of answer box:
  a marking, a set of markings, the net as (P, T, F, m₀), a transition
  system, a matrix (incidence, M, M′), the Inductive Miner's cut, sublog and
  process tree, a replay table (p, c, m, r), an alignment, a ranking of
  models, a prediction of what a box will give, and a **workflow to build**
  on a canvas beside the sheet. What you type is read back as you type it,
  and Check says which cells, parts or items are off. Forty more computed
  answers (`cpnpy exercises computes` lists them), so authors write fewer
  answers by hand.
- **Points, exams and variants.** Every answer box can be worth points, a
  partly right answer earns a part, and the overview shows the score. A pack
  with `exam: yes` has a clock, no hints or answers, nothing to reveal, and
  locks the answers when the time is up; *⋯ ▸ Export Marks…* and
  `cpnpy exercises marks` give the marks as CSV. A pack can give every
  student their own variant of a log (`seed: student`).
- **A past exam becomes a pack.** *Learn ▸ Make a Pack from an Exam…* (or
  `cpnpy exercises import`) reads the exam's text and writes a skeleton pack,
  one exercise per question with an answer block per part and the points
  carried over, with a `TODO` wherever the author has to finish it.
- **Three more demo exercises**: markings and matrices, the Inductive Miner's
  cuts and trees, and replay, alignments and a workflow.
- **Public datasets by name**: `cpnpy datasets` lists the BPI Challenge,
  Sepsis, Road Traffic Fine and Hospital Billing logs with their DOIs,
  fetches them into a shared cache and checks their fingerprints.

## 0.5.5

- **Fixed:** the box that opens to name a new place or transition could end
  up below and beside the node instead of over it (the canvas re-fits itself
  a moment after the first node is added). The box now stays over the node's
  name, also while you pan or zoom.

## 0.5.4

- **Discover and Process map fit the window.** The model, the process map and
  their zoom bars stay in view without scrolling the page, and the canvases
  grow and shrink with the window, as in the editors.
- **The drawn process tree is sized to the tree**, so it no longer leaves a
  tall empty canvas, and its zoom bar is always visible.

## 0.5.3

- **Process trees read top-down.** *Discover ▸ Inductive Miner ▸ How it was
  derived* now draws the tree as in the course: the root on top, each
  operator's children underneath it in order (left to right, as in the
  written form), joined by plain lines. A silent step is a leaf labelled τ
  rather than a black bar.
- **✎ Notes has moved to the status bar**, bottom right, so it no longer sits
  on top of your net or log. It lights up while the notes are open. The notes
  themselves still open over the page, where you can move and resize them.

## 0.5.2

- **Notes everywhere.** ✎ *Notes* in the bottom-left corner opens scratch
  paper over whatever you are working on: a log, a net, the welcome page.
  Drag its header to move it out of the way and its corner to resize it;
  ⤢ makes it larger, and – folds it back into the button. *View ▸ Show
  Notes* (⌘⌥N) opens and closes it, and *View ▸ Show Notes Button* hides the
  button if you would rather just use the shortcut.
- **Each folder keeps its own notes**, so they travel with the folder (iCloud,
  Git). Outside a folder you have one set of notes, kept by the app.

## 0.5.1

- **Notes in exercises.** ✎ *Notes* in the top bar opens scratch paper under
  the worksheet, for working things out before you answer. It is kept with
  the exercise (*my notes.md*).
- **Older exercises are easier to answer.** A sheet written before answer
  boxes, in parts a., b., c., now has a box under each part, with that part
  of the worked answer behind *Show answer*.
- **The window's title names the exercise** you are working on.
- **Pictures in worksheets** wider than the sheet are scaled to fit.
- **Writing Exercise Packs** (Help menu) now walks through making a pack step
  by step, which box suits which kind of question, and turning a past exam
  into a pack.
- **Fixed:** in an answer block, `a # b` (the α-algorithm's choice relation)
  was taken for a comment and cut off. A comment now needs two spaces before
  the `#`.
- **Computed answers** `free-choice` and `dead transitions` also work for a
  net that is not a WF-net.
- **The demo exercises go where you say.** *Open Demo Exercises* asks where
  to put its copy (nothing is copied until you choose), remembers it, and no
  longer switches the folder you are working in.

## 0.5.0

- **Exercise mode.** Exercises now open in a view of their own, made for
  working through them: the worksheet on the left with an answer box
  wherever one is needed, and what the exercise gives (the log, the
  transition system, the net, your net) on the right. Answer as you would on
  paper — yes/no, choices, sets like `{a, b}` or `({a}, {b,d})`, a footprint
  matrix to fill in, a firing sequence, a number, a net — and press
  **Check**: most answers are checked straight away, and when one is not
  right you see how far off it is without being given the answer. *Hint*
  and *Show answer* when you want them. Your answers are saved in the
  exercise's folder as you go, and the top bar shows your progress and steps
  through the pack.
- **Writing exercise packs** is plain Markdown: an `answer` block wherever
  students should answer. Right answers can be worked out by the app from the
  exercise's log, net or transition system (`compute: alpha.Y_L`,
  `compute: sound`, `compute: pre-regions(c)`, …). *Help ▸ Writing Exercise
  Packs* has the details, and `cpnpy exercises check` finds mistakes in a
  pack before you share it. The demo exercises are rewritten this way.
- **Edit a log** after opening it (*Edit…* on its page): as notation — a log
  you typed comes back exactly as you typed it — or case by case: events,
  timestamps, resources, cases, and renaming or removing an activity
  everywhere. Changes are saved to the log's file. A log typed in notation is
  now kept as a `.log.txt` file, in notation.
- **The canvas goes on in every direction.** Scrolling (two fingers on a
  trackpad) pans whether or not the whole net is in view, so you can always
  make room to start a new part beside it. Hold Space and drag to pan with
  the mouse. Zooming with ⌘-scroll or a pinch keeps the point under the
  pointer where it is.
  Pan the net right out of view and a button at the top of the canvas
  points to where it went; click it to bring it back.
- **Help ▸ References** lists the books, papers and standards behind every
  notation and algorithm in CPNpy (also in `docs/references.md`).

## 0.4.1

- **The sidebar button is on the sidebar.** Hide it with the button at its
  top right; while it is hidden, a slim strip at the left edge of the window
  has the button that brings it back.
- **Exercises are tidier on a laptop.** Check and Reveal all sit at the foot
  of the question. The question folds away with ‹ (and comes back from the
  left edge). In a narrow window, opening an exercise folds the sidebar away
  until you close the exercise. *my answer* is listed inside its exercise.
- **Fixed:** with an exercise open the net editor could again need more room
  than the window had, so the page scrolled sideways and the divider beside
  the canvas would not move. When space is short the inspector now folds
  away by itself (*⇤ Inspector* brings it back) and returns when there is
  room.

## 0.4.0

- **Exercises.** A folder with a `question.md` (or `.pdf` / `.png`) is now an
  exercise: click it in the sidebar and the question appears beside the
  canvas, the given net or log is loaded, and the analysis results stay
  hidden until you reveal them, one at a time or with *Reveal all*. Your
  work is saved as `my answer.pnml`, so a given `net.pnml` is never changed.
  **Check** compares your net with the model answer (`answer.pnml`) on what
  it can do, not how it is drawn, and shows the shortest traces where they
  differ, each of which you can replay in the token game. Without a model
  answer, Check shows the worked answer (`answer.md`).
- **Demo exercises**: File ▸ Open Demo Exercises copies a few to
  *Documents/CPNpy Exercises* to try.
- **State-based regions** in Discover: the log becomes a transition system
  (choose the prefix or postfix, set, multiset or sequence, and how many
  events to look back), and its minimal regions become the places of a
  net. The derivation shows every step the way exam answers are written:
  the transition system, GER and minimal pre-regions of every event, state
  separation, forward closure, and whether the net behaves exactly like the
  transition system.
- **Transition systems** of your own: File ▸ New Transition System…, or a
  `ts.txt` file. *Is this a region?* checks any set of states, and says
  which event breaks it when it is not one.
- **Compare nets…** (File menu and sidebar): do two nets allow the same
  traces? With the shortest differences both ways.
- Logs written in textbook notation open from `log.txt` (or `name.log.txt`)
  files.
- New definitions on hover and in Help ▸ Definitions: region, minimal
  region, pre- and post-region, GER, state separation, forward closure,
  elementary transition system and synthesis.

## 0.3.10

- **Fixed:** in the net editor, dragging the gap between the canvas and the
  inspector did nothing unless the window was very wide, and on a laptop
  screen the inspector ran off the right edge behind a scroll bar. The
  editor's tool buttons now wrap onto a second line when space is short, so
  the page fits and the divider can be dragged.
- **Hide the sidebar** with the new button at the top left of every page
  (or View ▸ Show Sidebar, ⌘⌥S) to give the net the whole window. CPNpy
  remembers your choice.
- The arrow for drawing arcs now belongs to the node nearest the mouse; when
  zoomed out it could pick a neighbouring one.

## 0.3.9

- **Fixed:** adding a place or transition to a net saved in an earlier
  session could give it the same hidden id as an existing one, and then the
  net could not be saved ("An arc must connect a place and a transition").
  New places, transitions and arcs now always get an id of their own.

## 0.3.8

- **Draw nets faster.** Drag the arrow beside a place out onto empty canvas
  and let go: a new transition appears there, already joined by an arc (and
  a place, if you start from a transition). A see-through preview shows
  where it will go, and it lines up with the node you started from. Type
  its name and carry on from its own arrow. One undo takes both away.
- The arrow for drawing arcs is a little easier to see, and no longer gets
  in the way of dragging a name that sits outside its node.
- Adding a place or transition no longer shifts a small net across the
  window.

## 0.3.7

- **Snap All to Grid** replaces the *Tidy* menu: one button that moves every
  place, transition and arc bend to the nearest dot, keeping your layout.
  *Arrange Automatically* is gone: on models drawn by hand it made a worse
  layout than the one you had.
- **Fixed (mostly on Linux):** after an open file was deleted, a new file
  created in the folder could be mistaken for it having moved there -- and
  then saved over. A file now only counts as moved if it really is the same
  file.

## 0.3.6

- **Neat nets.** Tick *Snap to grid* in the editors to put new and moved
  places, transitions and arc bends on the canvas's dots. *Tidy ▸ Snap
  Everything to the Grid* neatens a net you drew freely, and *Tidy ▸ Arrange
  Automatically* lays it out afresh, left to right. Undo puts it back.
- **The arrow for drawing arcs stays out of the way.** It only appears when
  the mouse is just outside a place or transition (not over it), and it is
  fainter until you aim at it.

## 0.3.5

- **Files moved in Finder stay open.** Moving or renaming an open file inside
  the folder used to mark it as missing and list it a second time in its new
  place; now it simply follows the file.
- **Recent folders on the welcome page open again** when you click them.
- **CSV files saved by Excel on Windows** (with letters like é or ü) open
  instead of failing.
- If a net **cannot be saved automatically** (a read-only folder, a full
  disk), a bar says so once, instead of an error message after every edit.
- A log still loading when you switch to another folder no longer turns up
  in the new one.
- Check boxes, radio buttons and sliders are easier to see in dark mode.
- The `cpnpy` command line says plainly when a file is missing or cannot be
  read, instead of printing a Python error.

## 0.3.4

- **Smoother updates.** While an update is being prepared, its progress
  window now stays put and says "Preparing…" instead of flickering on and
  off. (Updating *to* this version may still flicker once: the fix is in the
  version doing the updating.)

## 0.3.3

- **A more modern look throughout.** Drop-down lists, check boxes, radio
  buttons, tooltips, the Filter dialog's sections and the other dialogs now
  have the same rounded style as the rest of the app. Drop-down lists are
  wide enough to show their longest entry in full.
- **Keyboard hints match your computer**: Windows and Linux show Ctrl and
  Backspace where a Mac shows ⌘ and ⌫.
- The dotted chart can be panned by dragging with the middle mouse button.

## 0.3.2

- **Update notices work.** In 0.3.0 and 0.3.1, checking for updates failed
  with a certificate error, so please download this version yourself once.
  From now on CPNpy tells you when a new version is out and can install it
  for you.
- **CPNpy checks for a new version every time it opens.** If there is one, a
  bar at the top of the window says so without interrupting you: *What's
  New* shows what changed in each version you don't have yet, *Install Now*
  installs it, *Skip This Version* stays quiet until the one after, and ✕
  closes the bar until next time. Turn the check off in *Settings*.

## 0.3.1

- **By kind** in the sidebar lists your files again (it showed only the
  headings).
- Pop-up menus have rounded corners, like the rest of the app.

## 0.3.0

- **Your folder and the app stay in sync.** New nets and logs are saved into
  the open folder straight away, edits are saved as you go, and files you add,
  change or delete in Finder show up in the app by themselves.
- **Subfolders in the sidebar**, with a *Folders* / *By kind* switch. Make,
  rename and move folders and files, or move them to the Bin, from the
  sidebar.
- **Opening a file from elsewhere** offers to copy or move it into the folder.
- **Middle-click and drag** pans the canvas reliably.
- **Help ▸ Check for Updates…** (it only works from 0.3.2 on).
- "Workspace" is now simply called a **folder**.

## 0.2.0

- **Work in a folder** such as *Week 2*: every log and net in it is listed in
  the sidebar, and what you had open comes back next time.
- *Save As* names a net after its file, and double-clicking a name renames
  it (and its file).

## 0.1.0

- The first standalone apps for macOS, Windows and Linux: no Python needed.
