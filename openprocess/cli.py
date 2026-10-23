"""Command-line interface: ``openprocess <command> [options]``.

The CLI exists so that the engine is usable without the GUI -- in a script, in
a notebook, from a Makefile, or over SSH.  Every command works on a ``.cpn``
file, except ``example``, which writes one of the built-in demo models so you
have something to try immediately.

Commands
--------
``check``       parse a model and list any problems
``info``        summarise pages, declarations and net elements
``simulate``    run the simulator and print the firing log
``statespace``  generate the reachability graph and print the report
``example``     write a built-in example model to a ``.cpn`` file
``gui``         launch the CPN editor
``studio``      launch OpenProcess Studio (the desktop app)
``mine``        process mining from the command line:
                ``stats``, ``filter``, ``discover``, ``conform``,
                ``soundness``, ``invariants``
``exercises``   ``check``, ``marks``, ``import``, ``computes``: exercise packs (OpenProcess Learn)
``run``         run a ``.cpnflow`` workflow headless; ``--check`` re-runs and
                compares with the record, ``--sweep`` varies a setting,
                ``--lock`` writes the requirements the record names,
                ``--export`` and ``--report`` write a zip and an HTML page
``boxes``       list the boxes (OpenProcess's own, a folder's ``boxes/``, packages)
``datasets``    the public logs known by name: ``list``, ``fetch``, ``where``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis.state_space import StateSpace
from .io.cpn_reader import read_cpn
from .io.cpn_writer import write_cpn
from .model.net import CPNet
from .sim.simulator import DeadMarkingError, Simulator


def _load(path: str) -> CPNet:
    net = read_cpn(path)
    if net.errors:
        print(f"{len(net.errors)} problem(s) found while compiling '{path}':",
              file=sys.stderr)
        for issue in net.errors:
            print(f"  {issue}", file=sys.stderr)
    return net


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def command_check(arguments: argparse.Namespace) -> int:
    net = _load(arguments.model)
    if net.errors:
        return 1
    print(f"'{net.name}' compiled cleanly: "
          f"{_count(net.all_places(), 'place')}, "
          f"{_count(net.all_transitions(), 'transition')}, "
          f"{_count(net.all_arcs(), 'arc')}.")
    return 0


def _count(items, noun: str) -> str:
    """``1 place`` / ``3 places``."""
    number = sum(1 for _ in items)
    return f"{number} {noun}{'' if number == 1 else 's'}"


def command_info(arguments: argparse.Namespace) -> int:
    net = _load(arguments.model)
    print(f"Model: {net.name}")
    print(f"Pages: {len(net.pages)}")
    for page in net.pages:
        print(f"  {page.name}: {len(page.places)} places, "
              f"{len(page.transitions)} transitions, {len(page.arcs)} arcs")

    print("\nColour sets:")
    for name, colour_set in sorted(net.declarations.colour_sets.items()):
        finite = "finite" if colour_set.is_finite() else "infinite"
        timed = ", timed" if colour_set.timed else ""
        print(f"  {name:<16}{type(colour_set).__name__:<22}{finite}{timed}")

    print("\nVariables:")
    for name, colour_set_name in sorted(net.declarations.variables.items()):
        print(f"  {name} : {colour_set_name}")

    print("\nInitial marking:")
    print(net.initial_marking().describe(net) or "  (empty)")
    return 0


def command_simulate(arguments: argparse.Namespace) -> int:
    net = _load(arguments.model)
    if net.errors:
        return 1
    simulator = Simulator(net, seed=arguments.seed)

    print("Initial marking:")
    print(simulator.marking.describe(net))
    print()

    taken = simulator.run(arguments.steps)
    for record in simulator.log:
        print(record.describe(net))

    print()
    print(f"Fired {taken} step(s); final state at time {simulator.clock}:")
    print(simulator.marking.describe(net))

    if taken < arguments.steps:
        print("\nStopped early: the marking is dead (nothing further is enabled).")
    return 0


def command_statespace(arguments: argparse.Namespace) -> int:
    net = _load(arguments.model)
    if net.errors:
        return 1
    space = StateSpace(net).generate(max_nodes=arguments.max_nodes)
    print(space.report())
    return 0


def command_example(arguments: argparse.Namespace) -> int:
    # Imported lazily so that the examples directory is optional at runtime.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    try:
        import models  # type: ignore[import-not-found]
    except ImportError:
        print("The examples directory is not available.", file=sys.stderr)
        return 1

    if arguments.name not in models.ALL_EXAMPLES:
        print(f"Unknown example '{arguments.name}'. Available: "
              f"{', '.join(sorted(models.ALL_EXAMPLES))}", file=sys.stderr)
        return 1

    net = models.ALL_EXAMPLES[arguments.name]()
    path = write_cpn(net, arguments.output)
    print(f"Wrote {path}")
    return 0


def command_gui(arguments: argparse.Namespace) -> int:
    try:
        from .gui.app import main as gui_main
    except ImportError:
        print("The GUI needs PySide6. Install it with:\n"
              "  pip install 'openprocess[app]'", file=sys.stderr)
        return 1
    argv = ["openprocess-cpn"] + ([arguments.model] if arguments.model else [])
    return gui_main(argv)


def command_studio(arguments: argparse.Namespace) -> int:
    try:
        from .gui.studio.app import main as studio_main
    except ImportError:
        print("OpenProcess Studio needs PySide6. Install it with:\n"
              "  pip install 'openprocess[app]'", file=sys.stderr)
        return 1
    return studio_main(["openprocess-studio"] + list(arguments.files))


# ---------------------------------------------------------------------------
# Process mining commands
# ---------------------------------------------------------------------------
def _read_log(path: str):
    """An event log from .xes/.xes.gz/.csv, or textbook notation in a .txt file."""
    from .mining import EventLog, parse_simple_log, read_csv, read_xes
    lower = path.lower()
    if lower.endswith(".csv"):
        return read_csv(path)
    if lower.endswith(".txt"):
        return EventLog.from_simple_log(parse_simple_log(Path(path).read_text(encoding="utf-8")), Path(path).stem)
    return read_xes(path)


def command_mine_stats(arguments: argparse.Namespace) -> int:
    from .mining import summarise
    from .mining.stats import format_duration
    log = _read_log(arguments.log)
    s = summarise(log)
    print(f"{log.name}: {s.case_count:,} cases, {s.event_count:,} events, "
          f"{_plural(s.activity_count, 'activity').replace('activitys', 'activities')}, "
          f"{_plural(s.variant_count, 'variant')} "
          f"(classifier: {log.default_classifier().name})")
    if s.start:
        print(f"From {s.start} to {s.end}; median case duration "
              f"{format_duration(s.median_case_duration)}")
    print("\nActivities:")
    for a in s.activities:
        print(f"  {a.name:<30} {a.occurrences:>8,} events  {a.cases:>6,} cases")
    print(f"\nTop variants (of {s.variant_count:,}):")
    for variant in s.variants[:arguments.top]:
        print(f"  {variant.count:>6,} x  " + ", ".join(variant.sequence))
    return 0


def command_mine_discover(arguments: argparse.Namespace) -> int:
    from .mining import alpha_miner, inductive_miner, write_pnml
    simple = _read_log(arguments.log).simple_log()
    if arguments.algorithm == "alpha":
        result = alpha_miner(simple)
        for step, content in result.steps():
            print(f"{step}\n    {content}")
        for warning in result.warnings:
            print(f"warning: {warning}")
    elif arguments.algorithm == "heuristics":
        from .mining.discovery.heuristics import END, START, heuristics_net
        result = heuristics_net(simple, dependency_threshold=arguments.dependency)
        print(f"Dependency graph: {len(result.graph.edges)} arcs "
              f"(a => b >= {arguments.dependency:g})")

        def shown(binding) -> str:
            return "{" + ", ".join(sorted("start" if a == START else "end" if a == END
                                          else a for a in binding)) + "}"
        for activity in sorted((set(result.causal.inputs) | set(result.causal.outputs))
                               - {START, END}):
            for side, bindings in (("in ", result.causal.inputs), ("out", result.causal.outputs)):
                listed = " or ".join(f"{shown(b)} x{n}" for b, n in
                                     bindings.get(activity, {}).most_common())
                print(f"  {activity} {side}: {listed or '-'}")
    else:
        noise = arguments.noise if arguments.algorithm == "imf" else 0.0
        result = inductive_miner(simple, noise_threshold=noise)
        print("Process tree:", result.tree)
        print("\n".join(result.trace_of_steps))
    print(f"\nModel: {result.net.summary()}")
    if arguments.output:
        write_pnml(result.net, arguments.output)
        print(f"Wrote {arguments.output}")
    return 0


def command_mine_conform(arguments: argparse.Namespace) -> int:
    from .mining import align_log, generalisation, precision, read_pnml, simplicity, token_replay
    net = read_pnml(arguments.model)
    simple = _read_log(arguments.log).simple_log()
    replay = token_replay(net, simple)
    alignments = align_log(net, simple)
    print(f"Fitness (alignments)   {alignments.average_fitness:.4f}   "
          f"log-level {alignments.log_fitness:.4f}")
    print(f"Fitness (token replay) {replay.fitness:.4f}   "
          f"p={replay.produced} c={replay.consumed} m={replay.missing} r={replay.remaining}")
    print(f"Precision (ETC)        {precision(net, simple):.4f}")
    print(f"Generalisation         {generalisation(replay):.4f}")
    print(f"Simplicity             {simplicity(net):.4f}")
    print(f"Fitting cases          {alignments.fitting_traces} / {alignments.trace_count}")
    for note in alignments.notes:
        print(f"Note: {note}")
    return 0


def command_mine_soundness(arguments: argparse.Namespace) -> int:
    from .mining import check_soundness, read_pnml
    report = check_soundness(read_pnml(arguments.model))
    verdict = {True: "SOUND", False: "NOT SOUND", None: "UNDECIDED"}[report.sound]
    print(verdict)
    for finding in report.findings:
        print(f"  - {finding}")
    return 0 if report.sound else 1


def command_mine_filter(arguments: argparse.Namespace) -> int:
    from .mining.filtering import FilterSettings, apply_filters
    from .mining.stats import summarise

    def names(text: str | None) -> set[str] | None:
        return None if text is None else {n.strip() for n in text.split(",") if n.strip()}

    log = _read_log(arguments.log)
    settings = FilterSettings(
        start_activities=names(arguments.start), end_activities=names(arguments.end),
        variant_coverage=arguments.variants, top_variants=arguments.top_variants)
    for option, mode in (("keep", "keep events"), ("mandatory", "mandatory"),
                         ("forbidden", "forbidden")):
        if getattr(arguments, option) is not None:
            settings.activities = (names(getattr(arguments, option)), mode)
    if arguments.min_length is not None or arguments.max_length is not None:
        settings.case_length = (arguments.min_length or 0,
                                arguments.max_length if arguments.max_length is not None
                                else 10**9)
    filtered = apply_filters(log, settings)
    before, after = summarise(log), summarise(filtered)
    print(f"Filters: {filtered.attributes['openprocess:filter']}")
    print(f"Kept {after.case_count:,} of {_plural(before.case_count, 'case')}, "
          f"{_plural(after.event_count, 'event')}, {_plural(after.variant_count, 'variant')}")
    if arguments.output:
        if arguments.output.lower().endswith(".csv"):
            from .mining.csv_import import write_csv
            write_csv(filtered, arguments.output)
        else:
            from .mining import write_xes
            write_xes(filtered, arguments.output)
        print(f"Wrote {arguments.output}")
    return 0


def command_mine_invariants(arguments: argparse.Namespace) -> int:
    from .mining import read_pnml
    from .mining.analysis import check_workflow_net, short_circuit
    from .mining.invariants import invariants
    net = read_pnml(arguments.model)
    found = invariants(net)
    names = [net.node_name(t) for t in found.transitions]
    width = max([len(net.node_name(p)) for p in found.places] + [5])
    print("Incidence matrix C (rows: places, columns: transitions)")
    print(" " * width + "  " + "  ".join(f"{n:>{max(3, len(n))}}" for n in names))
    for place, row in zip(found.places, found.incidence):
        cells = "  ".join(f"{value:>{max(3, len(n))}d}" for value, n in zip(row, names))
        print(f"{net.node_name(place):<{width}}  {cells}")
    print("\nP-invariants (weighted token count in the initial marking):")
    for invariant in found.p_invariants:
        print("  " + found.describe(invariant, with_value=True))
    uncovered = found.uncovered_places()
    print("  covered by P-invariants: " + ("yes (structurally bounded)" if not uncovered else
          "no, missing " + ", ".join(net.node_name(p) for p in uncovered)))
    workflow = check_workflow_net(net)
    if workflow.is_workflow_net:
        found = invariants(short_circuit(net, workflow.source, workflow.sink))
        print("\nT-invariants of the short-circuited net (with t* from o to i):")
    else:
        print("\nT-invariants:")
    for invariant in found.t_invariants:
        print("  " + found.describe(invariant))
    uncovered = found.uncovered_transitions()
    print("  covered by T-invariants: " + ("yes" if not uncovered else
          "no, missing " + ", ".join(found.net.node_name(t) for t in uncovered)))
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def command_exercises_check(arguments: argparse.Namespace) -> int:
    """List a pack's exercises and answer boxes; report mistakes in them."""
    from .learn.checks import Context, TaskError, model_answer_text, validate
    from .learn.pack import load_pack
    pack = load_pack(arguments.folder)
    if not pack.exercises:
        print(f"No exercises in {arguments.folder} (an exercise is a folder with a "
              "question.md).", file=sys.stderr)
        return 1
    print(f"{pack.title}: {len(pack.exercises)} exercise(s)")
    problems = 0
    for exercise in pack.exercises:
        chapter = pack.chapter(exercise)
        print(f"\n{chapter + ' › ' if chapter else ''}{exercise.title}  "
              f"({exercise.folder.name})")
        found = validate(exercise)
        context = Context(exercise)
        for task in exercise.sheet.tasks:
            checked = "checked" if task.checkable else "self-checked"
            line = f"  - {task.id:<8} {task.type:<10} {checked}"
            if arguments.answers and task.type not in ("net", "footprint", "trace", "open"):
                try:
                    answer = model_answer_text(exercise, task, context)
                except TaskError as error:
                    answer = f"(cannot work it out: {error})"
                line += "  →  " + " ".join(answer.split())[:100]
            print(line)
        for problem in found:
            print(f"  ! {problem}")
        problems += len(found)
    print(f"\n{problems} problem(s) found." if problems else "\nNo problems found.")
    return 1 if problems else 0


def command_exercises_marks(arguments: argparse.Namespace) -> int:
    """The points a pack's answers earn, per exercise and per answer box."""
    from .learn.exam import marks, marks_csv
    from .learn.pack import load_pack
    pack = load_pack(arguments.folder)
    if not pack.exercises:
        print(f"No exercises in {arguments.folder}.", file=sys.stderr)
        return 1
    if arguments.csv:
        print(marks_csv(pack), end="")
        return 0
    student = pack.student()
    print(f"{pack.title}" + (f" — {student}" if student else ""))
    total = earned = 0.0
    for row in marks(pack):
        where = (row["chapter"] + " › " if row["chapter"] else "") + row["exercise"]
        print(f"  {where:<56} {row['earned']:>6g} / {row['points']:<6g}  "
              f"({row['answered']} of {row['of']} answered)")
        if arguments.blocks:
            for block_id, block in row["blocks"].items():
                print(f"      {block_id:<10} {block['status'] or '—':<10} "
                      f"{block['earned']:>6g} / {block['points']:g}")
        total += row["points"]
        earned += row["earned"]
    print(f"  {'TOTAL':<56} {earned:>6g} / {total:<6g}")
    return 0


def command_exercises_import(arguments: argparse.Namespace) -> int:
    """A past exam's text into a skeleton pack, one exercise per question."""
    from .learn.importer import todo_list, write_pack
    source = Path(arguments.exam)
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
        written = write_pack(text, arguments.target, arguments.title or source.stem)
    except (OSError, ValueError) as error:
        print(f"Could not make a pack from {source}: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(f"  wrote {path}")
    todos = todo_list(arguments.target)
    print(f"\n{len(todos)} TODO(s) left for you to finish; then run: openprocess exercises check "
          f"\"{arguments.target}\"")
    for todo in todos:
        print("  " + todo)
    return 0


def command_exercises_computes(arguments: argparse.Namespace) -> int:
    """Every ``compute:`` an answer block may use, with what it works out."""
    from .learn.computed import describe_all
    groups = {"log": "Of the log", "net": "Of the net (net.pnml, or of: file.pnml)",
              "ts": "Of the transition system (ts.txt)", "box": "Of a box or a workflow",
              "": "Other"}
    rows = describe_all()
    for key, heading in groups.items():
        found = [(name, text) for name, of, text in rows if of == key]
        if not found:
            continue
        print(heading)
        for name, text in found:
            print(f"  {name:<28} {text}")
        print()
    return 0


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
def command_run(arguments: argparse.Namespace) -> int:
    """Run a workflow file without the app, and say what every box gave."""
    from .flow import Runner, Sweep, check, differences, library_for, load, save
    from .flow.record import requirements_lock
    path = Path(arguments.workflow)
    folder = path.parent
    library = library_for(folder)
    workflow, record = load(path, library)
    if arguments.lock:
        target = folder / "requirements.lock"
        target.write_text(requirements_lock(record), encoding="utf-8")
        print(f"Wrote {target}")
        return 0
    for spec in arguments.sweep or []:
        name, _, values = spec.partition("=")
        node_id, _, setting = name.rpartition(".")
        targets = [workflow.nodes[node_id]] if node_id in workflow.nodes else \
            [n for n in workflow.nodes.values() if workflow.spec(n).setting(setting) is not None]
        if not targets:
            print(f"openprocess: no box has a setting {setting!r}", file=sys.stderr)
            return 2
        for node in targets:
            node.settings[setting] = Sweep.parse(values)
    for line in differences(record, workflow, folder):
        print(f"note: {line}")
    problems = workflow.validate()
    for line in problems:
        print(f"openprocess: {line}", file=sys.stderr)
    if problems:
        return 2
    run = Runner(library, folder=folder, strict=arguments.strict).run(workflow)   # file settings: relative to the folder
    for node in workflow.order():
        result = run.result(node)
        value = run.value(node)
        line = f"{workflow.title(node):28} {result.status:8}"
        if result.status == "done":
            line += f" {result.duration * 1000:7.1f} ms  {_brief(value)}"
        elif result.error:
            line += f"  {result.error}"
        elif result.message:
            line += f"  {result.message}"
        print(line)
        if arguments.verbose and result.explanation.notes:
            for note in result.explanation.notes:
                print(f"{'':28}   · {note}")
    if len(run.variants) > 1:
        print(f"{len(run.variants)} sweep variants")
    if arguments.output:
        _write_outputs(workflow, run, Path(arguments.output))
    if arguments.export:
        from .flow.record import export_experiment
        print(f"Wrote {export_experiment(workflow, run, arguments.export, folder, library)}")
    if arguments.report:
        from .flow.report import write_report
        print(f"Wrote {write_report(arguments.report, workflow=workflow, run=run, folder=folder)}")
    if arguments.check:
        if not record.results:
            # Nothing to compare is not a pass: a CI job would claim a reproduction that never happened.
            print(f"openprocess: {path.name} has no recorded results to check against; "
                  f"record them first with: openprocess run {path.name} --save", file=sys.stderr)
            return 1
        report = check(workflow, record, run, complete=True)
        if report:
            for line in report:
                print(f"DIFFERS: {line}")
            return 1
        print(f"Every recorded result was reproduced ({len(record.results)} boxes).")
    elif arguments.save:
        save(workflow, path, run, folder)
        print(f"Recorded the results in {path.name}")
    return 0 if run.ok else 1


def _brief(value) -> str:
    import dataclasses
    from .flow.figures import tile_parts
    from .flow.types import Figure, Scores, Table
    if dataclasses.is_dataclass(value) and not isinstance(value, type) and tile_parts(value):
        return " · ".join(tile_parts(value))
    if isinstance(value, Scores):
        return ", ".join(f"{k} {v:.3f}" if isinstance(v, float) else f"{k} {v}" for k, v in value.metrics.items())
    if isinstance(value, Table):
        return f"table {len(value.rows)} × {len(value.columns)}"
    if isinstance(value, Figure):
        return "figure"
    summary = getattr(value, "summary", None)
    if callable(summary):
        return summary()
    if hasattr(value, "event_count"):
        return f"{len(value)} cases, {value.event_count} events"
    return type(value).__name__ if value is not None else ""


def _write_outputs(workflow, run, folder: Path) -> None:
    from .flow.types import Figure, Scores, Table
    folder.mkdir(parents=True, exist_ok=True)
    for node in workflow.order():
        value = run.value(node)
        stem = f"{node.id} {workflow.title(node)}".replace("/", "-")
        if isinstance(value, Table):
            value.to_csv(folder / f"{stem}.csv")
        elif isinstance(value, Scores):
            Table.from_scores([value]).to_csv(folder / f"{stem}.csv")
        elif isinstance(value, Figure):
            value.save(folder / f"{stem}.svg" if value.svg else folder / f"{stem}.png")
        elif value.__class__.__name__ == "PetriNet":
            from .mining.pnml import write_pnml
            write_pnml(value, folder / f"{stem}.pnml")
    print(f"Wrote the results into {folder}")


def command_boxes(arguments: argparse.Namespace) -> int:
    from .flow import library_for
    library = library_for(arguments.folder)
    for group, specs in library.by_group().items():
        print(f"{group}")
        for spec in specs:
            print("  " + spec.describe().replace("\n", "\n  ") if arguments.verbose else
                  f"  {spec.name:28} {spec.function.__name__:24} "
                  f"{'(' + spec.unavailable_reason + ')' if not spec.available else ''}")
    for broken in library.broken:
        print(f"broken: {broken.file}: {broken.reason}")
    return 0


def command_datasets(arguments: argparse.Namespace) -> int:
    from .flow import datasets
    if arguments.datasets_command == "list":
        for name in datasets.names():
            entry = datasets.info(name)
            mark = "cached" if (datasets.cache_dir() / entry.file).is_file() else "not fetched"
            print(f"{entry.name:42} {entry.size:>7}  {mark}\n    {entry.description}\n    {entry.page}")
        return 0
    if arguments.datasets_command == "where":
        print(datasets.cache_dir())
        return 0
    try:
        path = datasets.fetch(arguments.name)
    except datasets.DatasetMissing as error:
        print(f"openprocess: {error}", file=sys.stderr)
        return 1
    print(f"{arguments.name}: {path}\nsha256 {datasets.sha256_file(path)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openprocess",
        description="Coloured Petri Net tools: edit, simulate and analyse .cpn models.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="parse a model and report problems")
    check.add_argument("model")
    check.set_defaults(handler=command_check)

    info = subparsers.add_parser("info", help="summarise a model")
    info.add_argument("model")
    info.set_defaults(handler=command_info)

    simulate = subparsers.add_parser("simulate", help="run the simulator")
    simulate.add_argument("model")
    simulate.add_argument("-n", "--steps", type=int, default=50,
                          help="maximum number of steps (default 50)")
    simulate.add_argument("--seed", type=int, default=None,
                          help="seed for reproducible random choices")
    simulate.set_defaults(handler=command_simulate)

    statespace = subparsers.add_parser("statespace", help="generate the state space")
    statespace.add_argument("model")
    statespace.add_argument("--max-nodes", type=int, default=10_000,
                            help="stop after this many nodes (default 10000)")
    statespace.set_defaults(handler=command_statespace)

    example = subparsers.add_parser("example", help="write a built-in example model")
    example.add_argument("name", help="simple_transfer | dining_philosophers | "
                                      "timed_conveyor | guarded_choice")
    example.add_argument("-o", "--output", default="example.cpn")
    example.set_defaults(handler=command_example)

    gui = subparsers.add_parser("gui", help="launch the desktop application")
    gui.add_argument("model", nargs="?", default=None)
    gui.set_defaults(handler=command_gui)

    studio = subparsers.add_parser("studio", help="launch OpenProcess Studio (process mining)")
    studio.add_argument("files", nargs="*", help="logs (.xes/.csv) or nets (.pnml) to open")
    studio.set_defaults(handler=command_studio)

    exercises = subparsers.add_parser("exercises", help="exercise packs")
    exercise_commands = exercises.add_subparsers(dest="exercises_command", required=True)
    exercise_check = exercise_commands.add_parser(
        "check", help="list a pack's exercises and report mistakes in its answer blocks")
    exercise_check.add_argument("folder", help="the pack (or one exercise) folder")
    exercise_check.add_argument("--answers", action="store_true",
                                help="also print the right answers the app works out")
    exercise_check.set_defaults(handler=command_exercises_check)
    exercise_marks = exercise_commands.add_parser(
        "marks", help="the points a pack's answers earn, per exercise (and per box)")
    exercise_marks.add_argument("folder", help="the pack folder")
    exercise_marks.add_argument("--csv", action="store_true", help="as CSV, one row per answer box")
    exercise_marks.add_argument("--blocks", action="store_true", help="also every answer box")
    exercise_marks.set_defaults(handler=command_exercises_marks)
    exercise_import = exercise_commands.add_parser(
        "import", help="a past exam's text (numbered questions, lettered parts) into a skeleton pack")
    exercise_import.add_argument("exam", help="the exam as a text or Markdown file")
    exercise_import.add_argument("target", help="the pack folder to write")
    exercise_import.add_argument("--title", default=None, help="the pack's title (default: the file's name)")
    exercise_import.set_defaults(handler=command_exercises_import)
    exercise_computes = exercise_commands.add_parser(
        "computes", help="list every compute: an answer block may use")
    exercise_computes.set_defaults(handler=command_exercises_computes)

    run = subparsers.add_parser("run", help="run a .cpnflow workflow without the app")
    run.add_argument("workflow", help="the .cpnflow file")
    run.add_argument("--check", action="store_true",
                     help="re-run and fail if any result differs from the recorded one")
    run.add_argument("--save", action="store_true", help="record the results in the file")
    run.add_argument("--sweep", action="append", metavar="SETTING=VALUES",
                     help="vary a setting, e.g. noise=0..0.5 step 0.1 or n3.seed=1,2,3")
    run.add_argument("--lock", action="store_true",
                     help="write requirements.lock from the record's environment")
    run.add_argument("-o", "--output", metavar="FOLDER", help="write every result as a file")
    run.add_argument("--export", metavar="FILE.zip",
                     help="zip the workflow, its inputs, boxes and results, with a README")
    run.add_argument("--strict", action="store_true",
                     help="also fail a box that changes its inputs in place (slower; for tests and CI)")
    run.add_argument("--report", metavar="FILE.html",
                     help="write the analysis as one HTML page: results, how, code, inputs, citations")
    run.add_argument("-v", "--verbose", action="store_true", help="also print the boxes' notes")
    run.set_defaults(handler=command_run)

    boxes = subparsers.add_parser("boxes", help="list the boxes")
    boxes.add_argument("folder", nargs="?", help="a folder whose boxes/ to include")
    boxes.add_argument("-v", "--verbose", action="store_true", help="inputs, outputs and settings")
    boxes.set_defaults(handler=command_boxes)

    datasets = subparsers.add_parser("datasets", help="the public logs known by name")
    dataset_commands = datasets.add_subparsers(dest="datasets_command", required=True)
    dataset_commands.add_parser("list", help="every dataset, and whether it is cached")
    fetch = dataset_commands.add_parser("fetch", help="fetch one into the cache and print its hash")
    fetch.add_argument("name")
    dataset_commands.add_parser("where", help="print the cache folder")
    datasets.set_defaults(handler=command_datasets)

    mine = subparsers.add_parser("mine", help="process mining on event logs")
    mining = mine.add_subparsers(dest="mining_command", required=True)
    stats = mining.add_parser("stats", help="summarise an event log")
    stats.add_argument("log", help=".xes, .xes.gz, .csv, or .txt in textbook notation")
    stats.add_argument("--top", type=int, default=10, help="variants to list (default 10)")
    stats.set_defaults(handler=command_mine_stats)
    discover = mining.add_parser("discover", help="discover a Petri net")
    discover.add_argument("log")
    discover.add_argument("-a", "--algorithm", choices=["alpha", "im", "imf", "heuristics"],
                          default="im")
    discover.add_argument("--noise", type=float, default=0.2, help="IMf noise threshold")
    discover.add_argument("--dependency", type=float, default=0.5,
                          help="Heuristics Miner dependency threshold (default 0.5)")
    discover.add_argument("-o", "--output", help="write the model as PNML")
    discover.set_defaults(handler=command_mine_discover)
    conform = mining.add_parser("conform", help="fitness, precision, ... of a model on a log")
    conform.add_argument("model", help="a .pnml file")
    conform.add_argument("log")
    conform.set_defaults(handler=command_mine_conform)
    soundness = mining.add_parser("soundness", help="check WF-net soundness")
    soundness.add_argument("model", help="a .pnml file")
    soundness.set_defaults(handler=command_mine_soundness)
    filtering = mining.add_parser("filter", help="keep part of a log (variants, "
                                  "activities, start/end, length)")
    filtering.add_argument("log")
    filtering.add_argument("--variants", type=float, metavar="PERCENT",
                           help="keep the most frequent variants covering PERCENT of cases")
    filtering.add_argument("--top-variants", type=int, metavar="K",
                           help="keep the K most frequent variants")
    filtering.add_argument("--keep", metavar="A,B",
                           help="keep only the events of these activities")
    filtering.add_argument("--mandatory", metavar="A,B",
                           help="keep cases that contain one of these activities")
    filtering.add_argument("--forbidden", metavar="A,B",
                           help="remove cases that contain one of these activities")
    filtering.add_argument("--start", metavar="A,B", help="keep cases starting with these")
    filtering.add_argument("--end", metavar="A,B", help="keep cases ending with these")
    filtering.add_argument("--min-length", type=int, help="at least this many events")
    filtering.add_argument("--max-length", type=int, help="at most this many events")
    filtering.add_argument("-o", "--output", help="write the result (.xes, .xes.gz or .csv)")
    filtering.set_defaults(handler=command_mine_filter)
    invariant = mining.add_parser("invariants",
                                  help="incidence matrix, P- and T-invariants of a net")
    invariant.add_argument("model", help="a .pnml file")
    invariant.set_defaults(handler=command_mine_invariants)

    return parser


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except BrokenPipeError:
        # The output went into `head` or similar, which stopped reading: not
        # an error.  Point stdout at nowhere so the exit flush stays quiet.
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except FileNotFoundError as error:
        # A file that is not there, or a file that is not what it should be:
        # one line saying so, not a traceback.
        print(f"openprocess: no such file: {error.filename}", file=sys.stderr)
        return 1
    except (OSError, ValueError, SyntaxError) as error:     # ParseError is a SyntaxError
        print(f"openprocess: could not read the input: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
