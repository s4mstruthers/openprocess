"""The MCP server: OpenProcess's tools for Claude and other AI assistants.

Built on the MCP Python SDK (``pip install "openprocess[mcp]"``).  The tools
are thin wrappers over :mod:`.api`; what makes this server OpenProcess's is
the contract in :data:`INSTRUCTIONS`, which every connected assistant reads
first: cite the works the algorithms follow, keep every result reproducible
(workflow files with records, seeds, ``openprocess run --check``), work the
way the app works (boxes and workflows, a folder the user can open), show
the user what was done (open it in Studio; every write is journaled and
reviewable), and never make a number up.

Run it with ``openprocess mcp`` (stdio, for Claude Desktop, Claude Code,
Cursor and the like) or ``openprocess mcp --http`` (Streamable HTTP on
localhost, for clients that connect to a URL).  ``openprocess mcp install
claude-desktop`` writes the client's configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import __version__
from . import api

try:
    from mcp.server.mcpserver import Context, Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as _SDKToolError
except ImportError as error:  # pragma: no cover - the message is the point
    raise ImportError("The MCP server needs the MCP Python SDK: pip install \"openprocess[mcp]\" "
                      "(or pip install \"mcp>=2.3\")") from error


INSTRUCTIONS = f"""You are connected to OpenProcess {__version__}: process mining, Petri nets, coloured Petri nets,
workflows and teaching (OpenProcess Learn), in one open app. Every algorithm is implemented in the open,
every result can be reproduced, every step can be taught. Work the way the app works:

1. CITE. Every discovery, conformance and analysis result comes with `cite`: the published works the
   algorithm follows (van der Aalst's alpha algorithm, Leemans' Inductive Miner, Weijters' Heuristics
   Miner, Rozinat's token replay, Adriansyah's alignments, Munoz-Gama's ETC precision, van der Aalst's
   soundness theorem, Desel's termination…). Repeat them when you report numbers, and cite OpenProcess
   itself (`references` gives BibTeX). Never attribute a definition or a figure to a source the tools did
   not give you. If something lacks a source, say so rather than inventing one.

2. REPRODUCE. Prefer `build_workflow` over one-off calls when the user will keep the result: a
   .cpnflow file holds the boxes, settings, connections and a record (input hashes, seeds, versions,
   result fingerprints) that `openprocess run FILE --check` verifies later, and the app shows every
   box's result, how it got there, and its code. Every answer has `reproduce`: the command or Python
   that gives the same numbers. Report fingerprints and seeds when they matter. Deterministic
   algorithms give the same model from the same log; say which settings you used (noise, dependency
   threshold, classifier).

3. SHOW HOW. Results carry `how`: the derivation as the box reports it (the alpha algorithm's eight
   steps, the Inductive Miner's cuts and sublogs, the replay per variant). Use it to explain, and
   paste it (Markdown) when the user wants the working. Properties have definitions (`definition`).

4. USE THE FRAMEWORK. Analyses are workflows of typed boxes (`list_boxes`, `box_info`): Open log →
   Inductive Miner → Check fit, connected by type. A new algorithm is one Python function with type
   hints decorated with @box, saved in the folder's boxes/ (`write_box`); it then appears in the app
   under "Yours" and its hash is recorded with every analysis that uses it. Do not hand-roll an
   algorithm in a notebook when a box would make it reusable and recorded.

5. BE TRANSPARENT IN THE APP. Work in the user's folder (the logs and nets beside each other; a
   folder per week of a course). Save models as PNML beside the log, workflows as .cpnflow, and open
   them with `open_in_studio` so the user sees them in OpenProcess Studio. Every file you write is
   journaled with a backup: the app shows "AI changed N files · Review" with a diff and Undo per
   change, and `journal` lists them. Never overwrite a file you did not create unless the user asks
   (pass overwrite=true then). Say what you wrote and where.

6. TEACH. For exercises, read the guide (openprocess://guide/exercise-packs) and use `compute:` blocks
   so the app checks answers against values it works out from the exercise's own log or net; validate
   with `validate_pack` before handing a pack over; open it in Learn with `open_in_studio`.

7. NUMBERS COME FROM TOOLS. Report fitness, precision, counts and verdicts exactly as returned, with
   their method (token replay vs alignments) and scope (which cases). Where a verdict is undecided
   (a truncated state space), say so.

Logs: a path (.xes, .xes.gz, .csv, .txt in course notation, .jsonocel) or a log typed as
[<a,b,c>^3, <a,c>]. Models: .pnml (or .bpmn). Coloured nets: .cpn. Paths are on the user's machine.
Guides (resources): openprocess://guide/workflows, boxes, framework, exercise-packs, definitions,
references, connecting, architecture."""


def _friendly(function):
    """A tool's failure as one readable sentence for the assistant (and the
    user), not a hidden crash: a missing file, a log that is not a log, a
    setting out of range.  The traceback goes to the server's log (stderr)."""
    import functools
    import sys
    import traceback

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except _SDKToolError:
            raise
        except api.ToolError as error:
            raise _SDKToolError(str(error)) from error
        except Exception as error:  # noqa: BLE001 - every failure gets words
            traceback.print_exc(file=sys.stderr)
            raise _SDKToolError(f"{type(error).__name__}: {error}") from error
    return wrapper


def _client_name(ctx: Context | None) -> str:
    try:
        params = ctx.session.client_params               # type: ignore[union-attr]
        info = getattr(params, "clientInfo", None) or getattr(params, "client_info", None)
        return str(getattr(info, "name", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def build_server() -> MCPServer:
    """The server with every tool, resource and prompt registered."""
    server = MCPServer("OpenProcess", title="OpenProcess", instructions=INSTRUCTIONS, version=__version__,
                       website_url="https://github.com/s4mstruthers/openprocess")

    # -- logs -------------------------------------------------------------------------
    @server.tool(title="Summarise a log")
    @_friendly
    def log_summary(log: str, top: int = 10) -> dict:
        """Cases, events, activities, variants, time span, start and end activities, the most frequent
        variants and activities of an event log. `log`: a path (.xes/.xes.gz/.csv/.txt/.jsonocel) or a
        log in course notation like [<a,b,c>^3, <a,c>]."""
        return api.log_summary(log, top)

    @server.tool(title="List variants")
    @_friendly
    def log_variants(log: str, top: int = 50) -> dict:
        """The distinct traces (variants) of a log, most frequent first, with shares and the log in
        course notation."""
        return api.log_variants(log, top)

    @server.tool(title="Filter a log")
    @_friendly
    def filter_log(log: str, output: str | None = None, start_activities: list[str] | None = None,
                   end_activities: list[str] | None = None, activities: list[str] | None = None,
                   activity_mode: str = "keep events", min_length: int | None = None, max_length: int | None = None,
                   top_variants: int | None = None, variant_coverage: float | None = None,
                   time_start: str | None = None, time_end: str | None = None, time_mode: str = "contained",
                   overwrite: bool = False, ctx: Context = None) -> dict:
        """Keep part of a log as a new XES file (default: "<log> (filtered).xes" beside it): by start/end
        activities, activities (mode: keep events | mandatory | forbidden), case length, the k most frequent
        variants or the variants covering a percentage of cases, or a time frame (ISO dates; mode: contained
        | started | intersecting). Journaled for review."""
        return api.filter_log(log, output, start_activities=start_activities, end_activities=end_activities,
                              activities=activities, activity_mode=activity_mode, min_length=min_length,
                              max_length=max_length, top_variants=top_variants, variant_coverage=variant_coverage,
                              time_start=time_start, time_end=time_end, time_mode=time_mode, overwrite=overwrite,
                              client=_client_name(ctx))

    @server.tool(title="Dotted chart")
    @_friendly
    def dotted_chart(log: str, output: str | None = None, x: str = "actual", rows: str = "Case",
                     colour: str = "Activity", sort: str = "first", unit: str = "Auto", overwrite: bool = False,
                     ctx: Context = None) -> dict:
        """The dotted chart (Song & van der Aalst 2007) as an SVG file: x = actual | since start | relative |
        order in log | position in case; rows = Case | an attribute such as org:resource | Activity; colour =
        Activity | an attribute; sort = first | last | duration | events | name. Returns how to read it."""
        return api.dotted_chart(log, output, x=x, rows=rows, colour=colour, sort=sort, unit=unit,
                                overwrite=overwrite, client=_client_name(ctx))

    # -- discovery, conformance, analysis ------------------------------------------------
    @server.tool(title="Discover a model")
    @_friendly
    def discover(log: str, algorithm: str = "inductive", noise: float = 0.0, dependency: float = 0.9,
                 output: str | None = None, overwrite: bool = False, draw: bool = False,
                 ctx: Context = None) -> dict:
        """Discover a process model from a log and save it as PNML beside the log (default name "<log>
        (IM).pnml" etc.). algorithm: alpha (the eight steps), inductive (noise 0 = IM, 0 < noise ≤ 1 = IMf),
        heuristics (dependency threshold), regions (state-based), dfg (the process map, no file). Returns the
        model, its derivation (how), soundness, token-replay fitness on the same log, the sources (cite) and
        how to reproduce it; draw=true adds an SVG."""
        return api.discover(log, algorithm, noise=noise, dependency=dependency, output=output,
                            overwrite=overwrite, draw=draw, client=_client_name(ctx))

    @server.tool(title="Check conformance")
    @_friendly
    def conformance(log: str, model: str, method: str = "token replay", top: int = 20) -> dict:
        """Fitness (token replay: produced, consumed, missing, remaining per variant; or method=alignments:
        optimal alignments with their moves), precision (ETC), generalisation and simplicity of a PNML model
        against a log, with the sources of each measure."""
        return api.conformance(log, model, method, top)

    @server.tool(title="Analyse a net")
    @_friendly
    def analyse_net(model: str, max_states: int = 100000) -> dict:
        """Everything the Analysis tab says about a Petri net (.pnml or .bpmn): WF-net, soundness (option to
        complete, proper completion, no dead transitions) with a firing sequence for every violation, the
        short-circuited net's liveness and boundedness, behavioural properties (bounded, safe, terminating,
        deadlock-free, live, reversible), structure (free-choice, well-structured, S-coverable), P- and
        T-invariants, the incidence matrix and the footprint."""
        return api.analyse_net(model, max_states)

    @server.tool(title="Compare two nets")
    @_friendly
    def compare_models(first: str, second: str, max_length: int = 12) -> dict:
        """Do two nets allow the same complete traces? With the shortest traces only one of them allows."""
        return api.compare_models(first, second, max_length)

    @server.tool(title="Make a net")
    @_friendly
    def make_net(spec: dict, output: str, overwrite: bool = False, ctx: Context = None) -> dict:
        """Write a Petri net described as JSON to a PNML file (which opens in the app's editor) and check its
        soundness. spec = {"name": ..., "places": [{"id": "i", "tokens": 1}, ...], "transitions": [{"id":
        "a", "label": "register"}, {"id": "t1", "silent": true}], "arcs": [["i", "a"], ["a", "p1", 2]],
        "initial": {"i": 1}?, "final": {"o": 1}?}. A WF-net without markings gets [i] and [o]."""
        return api.make_net(spec, output, overwrite=overwrite, client=_client_name(ctx))

    @server.tool(title="Convert between PNML and BPMN")
    @_friendly
    def convert_model(source: str, output: str, overwrite: bool = False, ctx: Context = None) -> dict:
        """Convert a model by the files' extensions: a .pnml WF-net to .bpmn (BPMN 2.0 with a diagram, opens in
        Camunda Modeler and bpmn.io; exact for free-choice nets), or a .bpmn model to its Petri net semantics
        (.pnml) with a soundness check."""
        return api.convert_model(source, output, overwrite=overwrite, client=_client_name(ctx))

    @server.tool(title="Draw a diagram")
    @_friendly
    def draw(what: str, fmt: str = "png", kind: str = "auto", mode: str = "frequency", output: str | None = None,
             overwrite: bool = False, ctx: Context = None) -> Any:
        """A diagram: of a .pnml model (kind net) or a .bpmn model (kind bpmn), of a log's process map (kind dfg; mode frequency |
        performance) or Inductive Miner tree (kind tree), or of a transition system file (kind ts). fmt png
        returns an image you can look at; fmt svg returns the SVG text. output saves the file too."""
        result = api.draw(what, fmt=fmt, output=output, kind=kind, mode=mode, overwrite=overwrite,
                          client=_client_name(ctx))
        if "png" in result:
            return Image(data=result["png"], format="png")
        return result

    # -- workflows ----------------------------------------------------------------------
    @server.tool(title="List boxes")
    @_friendly
    def list_boxes(folder: str | None = None, group: str | None = None) -> dict:
        """The boxes an analysis is built from (Input, Filter, Discover, Check, Measure, Compare, Output,
        Science, Predict, Coloured nets, Sweep), with inputs, outputs and settings; a folder's own boxes/
        too when a folder is given."""
        return api.list_boxes(folder, group)

    @server.tool(title="Describe a box")
    @_friendly
    def box_info(box: str, folder: str | None = None) -> dict:
        """One box in full: description, settings, its source code, the algorithms it calls and the published
        works they follow."""
        return api.box_info(box, folder)

    @server.tool(title="Build a workflow")
    @_friendly
    def build_workflow(spec: dict, output: str, folder: str | None = None, run: bool = True, how: bool = True,
                       overwrite: bool = False, ctx: Context = None) -> dict:
        """Build an analysis as a .cpnflow file, run it and keep the record that makes it reproducible. spec =
        {"name": "...", "boxes": [{"id": "n1", "box": "open_log", "settings": {"file": "log.xes"}}, {"id":
        "n2", "box": "inductive_miner", "settings": {"noise": 0.2}}, {"id": "n3", "box": "check_fit"}],
        "connections": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3", "input": "model"}, {"from":
        "n1", "to": "n3", "input": "log"}]}. Box ids from list_boxes; file settings are relative to the
        workflow's folder. Returns every box's result and derivation; open the file with open_in_studio."""
        return api.build_workflow(spec, output, folder=folder, run=run, how=how, overwrite=overwrite,
                                  client=_client_name(ctx))

    @server.tool(title="Run a workflow")
    @_friendly
    def run_workflow(path: str, check: bool = False, how: bool = False, report: str | None = None,
                     ctx: Context = None) -> dict:
        """Run a saved .cpnflow headless; check=true compares every result with the file's record (the
        reproducibility check); report writes the analysis as one HTML page."""
        return api.run_workflow(path, check=check, how=how, report=report, client=_client_name(ctx))

    @server.tool(title="Write a box")
    @_friendly
    def write_box(folder: str, name: str, source: str, overwrite: bool = False, ctx: Context = None) -> dict:
        """Add an algorithm of your own as a box: Python source with a @box function (from openprocess.flow
        import box, EventLog, PetriNet, Scores, Table, ...; type hints make the inputs and settings, the
        docstring the help; flow.note()/flow.steps() report the derivation) written to <folder>/boxes/
        <name>.py after it has been loaded once to catch mistakes. The app lists it under "Yours"."""
        return api.write_box(folder, name, source, overwrite=overwrite, client=_client_name(ctx))

    # -- Learn --------------------------------------------------------------------------
    @server.tool(title="List computes")
    @_friendly
    def list_computes() -> dict:
        """Every `compute:` an exercise's answer block can use (alpha.Y_L, sound, im.tree, fire(a, [i]),
        regions, box(alpha_miner).net.transitions …), with what each works out from the exercise's files."""
        return api.list_computes()

    @server.tool(title="Validate a pack")
    @_friendly
    def validate_pack(folder: str, answers: bool = True) -> dict:
        """Check an exercise pack: every exercise and answer block, the answers the app works out (to compare
        with yours), and every problem, as `openprocess exercises check --answers` reports them."""
        return api.validate_pack(folder, answers)

    @server.tool(title="Compute an answer")
    @_friendly
    def compute_answer(exercise: str, expression: str, of: str | None = None, trace: str | None = None) -> dict:
        """Work out one compute expression for an exercise folder, as the app checks it: compute_answer(
        "Week 3/Exercise 1", "alpha.Y_L"); `of` names another file of the exercise (of: m2.pnml), `trace`
        a trace for replay/alignment computes."""
        return api.compute_answer(exercise, expression, of=of or "", trace=trace or "")

    @server.tool(title="Write an exercise")
    @_friendly
    def write_exercise(pack: str, name: str, question: str, files: dict[str, str] | None = None,
                       pack_title: str | None = None, overwrite: bool = False, ctx: Context = None) -> dict:
        """Write one exercise into a pack folder: <pack>/<name>/question.md (Markdown with ```answer blocks;
        read openprocess://guide/exercise-packs first) and the files it gives as {"log.txt": "[<a,b>^3]",
        "net.pnml": "...", "ts.txt": "s0 -a-> s1\\ninitial: s0"}; then validates it. Use compute: blocks
        so the app checks answers against the exercise's own log or net."""
        return api.write_exercise(pack, name, question, files, pack_title=pack_title, overwrite=overwrite,
                                  client=_client_name(ctx))

    @server.tool(title="Import an exam")
    @_friendly
    def import_exam(text: str, target: str, title: str = "Exam", ctx: Context = None) -> dict:
        """Turn a past exam's text (questions 1., 2., … with parts a), b), … and points in brackets) into a
        skeleton pack with TODOs to finish."""
        return api.import_exam(text, target, title, client=_client_name(ctx))

    # -- coloured nets ------------------------------------------------------------------
    @server.tool(title="Describe a coloured net")
    @_friendly
    def cpn_info(path: str) -> dict:
        """A CPN Tools model (.cpn): pages, colour sets, variables, initial marking, compile problems."""
        return api.cpn_info(path)

    @server.tool(title="Simulate a coloured net")
    @_friendly
    def cpn_simulate(path: str, steps: int = 50, seed: int = 0) -> dict:
        """Run the simulator for a number of firings (seeded, so repeatable) and return the firing log and the
        final marking."""
        return api.cpn_simulate(path, steps, seed)

    @server.tool(title="State space of a coloured net")
    @_friendly
    def cpn_state_space(path: str, max_nodes: int = 10000) -> dict:
        """The reachability graph and its report (bounds, dead and home markings, dead and live transitions)."""
        return api.cpn_state_space(path, max_nodes)

    @server.tool(title="Performance of a timed coloured net")
    @_friendly
    def cpn_performance(path: str, runs: int = 10, steps: int = 1000, warm_up: float = 0.0, seed: int = 0) -> dict:
        """Waiting times, utilisation and throughput per transition and place of a timed coloured net, over
        seeded simulation runs (run k uses seed + k) with a warm-up excluded."""
        return api.cpn_performance(path, runs, steps, warm_up, seed)

    # -- the app, references, the journal -------------------------------------------------
    @server.tool(title="Open in OpenProcess")
    @_friendly
    def open_in_studio(paths: list[str], folder: str | None = None, start: bool = True) -> dict:
        """Open files in OpenProcess Studio so the user sees them: a .cpnflow analysis, a .pnml net, a log, an
        exercise pack folder (opens in Learn). Starts the app if it is not running; folder makes the app
        work in that folder."""
        return api.open_in_studio(paths, folder, start)

    @server.tool(title="Is OpenProcess running?")
    @_friendly
    def studio_status() -> dict:
        """Whether OpenProcess Studio is running, its version and the folder it has open."""
        return api.studio_status()

    @server.tool(title="References")
    @_friendly
    def references(topic: str | None = None) -> dict:
        """What OpenProcess's notation and algorithms follow, topic by topic, with the published sources, and
        how to cite OpenProcess itself (BibTeX)."""
        return api.references_list(topic)

    @server.tool(title="Definition")
    @_friendly
    def definition(key: str | None = None) -> dict:
        """The mathematical definition of a property the analyses report (sound, live, bounded, safe,
        terminating, free_choice, well_structured, …) with its source; without a key, the list."""
        return api.definition(key)

    @server.tool(title="What was changed")
    @_friendly
    def journal(folder: str) -> dict:
        """The files an assistant wrote in a folder (the review journal the app shows), pending review and all."""
        return api.journal(folder)

    # -- resources: the guides ---------------------------------------------------------------
    @server.resource("openprocess://guide/{name}", title="OpenProcess guide", mime_type="text/markdown")
    def guide(name: str) -> str:
        """A guide: workflows (boxes and workflows in Python), boxes (every box), framework (the formal
        specification), exercise-packs (writing exercises), definitions (every property, mathematically),
        references (the works followed), connecting (this server), architecture (the code, mapped)."""
        return guide_text(name)

    @server.resource("openprocess://guides", title="OpenProcess guides", mime_type="text/markdown")
    def guides() -> str:
        return "\n".join(f"- openprocess://guide/{name}: {text}" for name, text in GUIDE_NAMES.items())

    # -- prompts -------------------------------------------------------------------------------
    @server.prompt(title="Analyse an event log")
    def analyse_log(log: str, folder: str | None = None) -> str:
        """A full, cited, reproducible analysis of a log, as a workflow the user can open."""
        return (f"Analyse the event log {log}" + (f" in the folder {folder}" if folder else "") + " with OpenProcess.\n"
                "1. log_summary and log_variants: describe the process (cases, variants, start/end activities, "
                "time span) in words.\n2. dotted_chart (actual time by case, and by resource if there is one): "
                "say what the chart shows.\n3. discover with the Inductive Miner (and the alpha algorithm when "
                "the log is small enough to show its eight steps); explain the derivation from `how`.\n"
                "4. conformance of each model (token replay, and alignments for the final one): fitness, "
                "precision, generalisation, simplicity, where the log deviates.\n5. analyse_net the chosen model: "
                "soundness and properties.\n6. build_workflow with the same steps (Open log → Inductive Miner → "
                "Check fit → Soundness, plus Dotted chart) saved beside the log, and open_in_studio it.\n"
                "Report every number with its method and source (cite), say how to reproduce it, and list the "
                "files you wrote.")

    @server.prompt(title="Make an exercise")
    def make_exercise(topic: str, pack: str, material: str | None = None) -> str:
        """An exercise for OpenProcess Learn with answers the app checks itself."""
        return (f"Write an exercise about {topic} into the pack folder {pack}"
                + (f", using {material} as the given log or net" if material else "") + ".\n"
                "Read openprocess://guide/exercise-packs first. Give the exercise a small log (log.txt in course "
                "notation) or a net (net.pnml via make_net) that makes the concept visible. Write question.md "
                "with answer blocks that use compute: wherever the app can work the answer out (list_computes), "
                "a hint for each, and a solution in words; then write_exercise, fix every problem validate_pack "
                "reports, and compare the computed answers with your own. Finally open_in_studio the pack so the "
                "user can try it in Learn.")

    @server.prompt(title="Check a model against a log")
    def check_model(log: str, model: str) -> str:
        """Conformance checking with the figures the course uses, explained."""
        return (f"Check the model {model} against the log {log}: conformance with token replay (report p, c, m, r "
                "per variant and the fitness formula), then alignments (the optimal alignment of each deviating "
                "variant), precision (ETC), generalisation and simplicity; analyse_net for soundness. Explain each "
                "figure in the course's terms, cite the sources the tools give, and say how to reproduce the "
                "numbers (openprocess mine conform, or a workflow with Check fit).")

    return server


GUIDE_NAMES = {
    "workflows": "boxes, workflows, the runner and the record, with Python examples",
    "boxes": "every box OpenProcess comes with: inputs, outputs, settings",
    "framework": "the workflow framework, specified",
    "exercise-packs": "writing exercise packs for OpenProcess Learn",
    "definitions": "every property the analyses report, defined mathematically, with sources",
    "references": "the works OpenProcess's notation and algorithms follow",
    "connecting": "connecting an AI assistant to OpenProcess (this server)",
    "architecture": "the code, mapped: layers, packages, modules",
    "design": "why OpenProcess is built the way it is",
}


def guide_text(name: str) -> str:
    """A guide's Markdown: generated where it is generated, otherwise from the
    copies shipped with the package (``mcp/guides/``) or the repository's docs."""
    name = name.strip().lower()
    if name not in GUIDE_NAMES:
        raise api.ToolError(f"No guide called {name!r}; the guides are: " + ", ".join(GUIDE_NAMES))
    if name == "definitions":
        from ..mining.definitions import markdown
        return markdown()
    if name == "references":
        from .. import references
        return references.markdown()
    if name == "exercise-packs":
        return api.exercise_guide()
    here = Path(__file__).parent
    for candidate in (here / "connecting.md" if name == "connecting" else None, here / "guides" / f"{name}.md",
                      here.parents[1] / "docs" / f"{name}.md"):
        if candidate is not None and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return f"The guide {name} is not shipped with this build; it is at " \
           f"https://github.com/s4mstruthers/openprocess/blob/main/docs/{name}.md"


def main(argv: list[str] | None = None) -> int:
    """``openprocess-mcp``: serve on stdio (default) or Streamable HTTP."""
    import argparse
    parser = argparse.ArgumentParser(prog="openprocess-mcp",
                                     description="OpenProcess as an MCP server for Claude and other AI assistants.")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP on localhost instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")     # PNG rendering without a window
    server = build_server()
    if arguments.http:
        server.run("streamable-http", host=arguments.host, port=arguments.port)
    else:
        server.run("stdio")
    return 0


__all__ = ["GUIDE_NAMES", "INSTRUCTIONS", "build_server", "guide_text", "main"]
