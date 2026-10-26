"""The MCP server's API, the change journal, the derivation export and the drawing.

The API (:mod:`openprocess.mcp.api`) needs neither the MCP SDK nor Qt; the
server tests run when the SDK is installed (``pip install "openprocess[mcp]"``,
part of the ``dev`` extra).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprocess.flow import changes
from openprocess.mcp import api

ROOT = Path(__file__).resolve().parents[1]
L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


@pytest.fixture
def folder(tmp_path):
    """A folder as the app keeps one (its .openprocess state), with a log in it."""
    (tmp_path / ".openprocess").write_text("{}")
    (tmp_path / "log.txt").write_text(L1, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Logs, discovery, conformance, analysis
# ---------------------------------------------------------------------------
def test_log_summary_of_a_typed_log_says_where_it_comes_from():
    summary = api.log_summary(L1)
    assert (summary["cases"], summary["events"], summary["variants"], summary["activities"]) == (6, 23, 3, 5)
    assert summary["start_activities"] == ["a"] and summary["end_activities"] == ["d"]
    assert summary["cite"] and all(c["citation"] for c in summary["cite"])
    assert "summarise" in summary["reproduce"]


def test_discover_saves_the_model_beside_the_log_and_journals_it(folder):
    found = api.discover(str(folder / "log.txt"), "alpha")
    target = Path(found["file"])
    assert target == folder / "log (alpha).pnml" and target.is_file()
    assert found["model"]["summary"] == "6 places, 5 transitions (0 silent), 14 arcs"
    assert found["soundness"]["sound"] is True and found["fitness_token_replay"] == 1.0
    assert sorted(map(tuple, (tuple(map(tuple, p)) for p in found["Y_L"]))) == sorted(
        [(("a",), ("b", "e")), (("a",), ("c", "e")), (("b", "e"), ("d",)), (("c", "e"), ("d",))])
    # The How tab's content: the eight steps, as the app shows them.
    assert "T_L" in found["how"] and "Y_L" in found["how"]
    assert any(c["key"] == "aalst2004" for c in found["cite"])
    assert "openprocess mine discover" in found["reproduce"]["command"]
    # Journaled where the app will show it, and never overwritten silently.
    assert [c.path for c in changes.pending(folder)] == [str(target)]
    with pytest.raises(api.ToolError, match="exists"):
        api.discover(str(folder / "log.txt"), "alpha")
    again = api.discover(str(folder / "log.txt"), "alpha", overwrite=True)
    assert again["file"] == str(target)


def test_inductive_miner_reports_its_cuts_and_sources(folder):
    found = api.discover(str(folder / "log.txt"), "inductive")
    assert found["process_tree"] == "→(a, ×(∧(b, c), e), d)"
    assert "cut" in found["how"]
    assert any(c["key"].startswith("leemans") for c in found["cite"])
    with pytest.raises(api.ToolError, match="Unknown algorithm"):
        api.discover(str(folder / "log.txt"), "genetic")


def test_conformance_and_net_analysis(folder):
    model = api.discover(str(folder / "log.txt"), "inductive")["file"]
    fit = api.conformance(str(folder / "log.txt"), model, "alignments")
    assert fit["token_replay"]["fitness"] == 1.0 and fit["alignments"]["fitness"] == 1.0
    assert 0 < fit["precision"] <= 1
    keys = [c["key"] for c in fit["cite"]]
    assert len(keys) == len(set(keys))                       # each work once
    report = api.analyse_net(str(ROOT / "examples" / "petri" / "order_handling_unsound.pnml"))
    assert report["soundness"]["sound"] is False
    assert all(f["firing_sequence"] for f in report["soundness"]["findings"])
    assert {p["property"] for p in report["properties"]} >= {"Bounded", "Terminating", "Live"}


def test_make_net_draw_and_compare(folder):
    spec = {"name": "Choice", "places": ["i", "o"], "transitions": [{"id": "a"}, {"id": "b"}],
            "arcs": [["i", "a"], ["a", "o"], ["i", "b"], ["b", "o"]]}
    made = api.make_net(spec, str(folder / "choice.pnml"))
    assert made["soundness"]["sound"] is True
    assert made["model"]["initial_marking"] == "[i]" and made["model"]["final_marking"] == "[o]"
    with pytest.raises(api.ToolError, match="place and a transition"):
        api.make_net({"places": ["p", "q"], "arcs": [["p", "q"]]}, str(folder / "bad.pnml"))
    drawn = api.draw(str(folder / "choice.pnml"), fmt="svg")
    assert drawn["svg"].startswith("<svg") and ">a<" in drawn["svg"]
    same = api.compare_models(str(folder / "choice.pnml"), str(folder / "choice.pnml"))
    assert same["equivalent"] is True


# ---------------------------------------------------------------------------
# Workflows and boxes
# ---------------------------------------------------------------------------
def test_build_and_check_a_workflow(folder):
    spec = {"name": "Demo", "boxes": [
        {"id": "log", "box": "open_log", "settings": {"file": "log.txt"}},
        {"id": "im", "box": "inductive_miner"}, {"id": "fit", "box": "check_fit"}],
        "connections": [{"from": "log", "to": "im"}, {"from": "im", "to": "fit", "input": "model"},
                        {"from": "log", "to": "fit", "input": "log"}]}
    built = api.build_workflow(spec, str(folder / "Demo.cpnflow"))
    assert built["ran"] and not built["problems"]
    assert all(r["status"] == "done" for r in built["results"].values())
    assert "fitness" in built["results"]["fit"]["result"]
    checked = api.run_workflow(str(folder / "Demo.cpnflow"), check=True)
    assert checked["check"]["status"] == "reproduced"
    with pytest.raises(api.ToolError, match="could not be built"):
        api.build_workflow({"boxes": [{"id": "x", "box": "no_such_box"}]}, str(folder / "Bad.cpnflow"))


def test_a_box_is_loaded_before_it_is_written(folder):
    good = '''from openprocess.flow import box, EventLog, Scores


@box(group="Measure")
def case_count(log: EventLog) -> Scores:
    """How many cases."""
    return Scores(log.name, {"cases": len(log)})
'''
    written = api.write_box(str(folder), "case_count", good)
    assert Path(written["file"]) == folder / "boxes" / "case_count.py"
    assert any(b["id"].endswith("case_count") for b in api.list_boxes(str(folder))["boxes"])
    with pytest.raises(api.ToolError, match="does not load"):
        api.write_box(str(folder), "broken", "from openprocess.flow import box\n@box\ndef broken(x):\n    return x\n")
    assert not (folder / "boxes" / "broken.py").exists()
    # Journaled in the folder, not in boxes/.
    assert any(c.path.endswith("case_count.py") for c in changes.pending(folder))


# ---------------------------------------------------------------------------
# Learn
# ---------------------------------------------------------------------------
QUESTION = '''# Exercise 1

**a.** Give $T_I$.

```answer
type: set
compute: alpha.T_I
```

**b.** Give the places of $\\alpha(L)$.

```answer
type: set
compute: alpha.P_L
```

**c.** The transitions of the α net:

```answer
type: set
compute: box(alpha_miner).net.T
```
'''


def test_write_validate_and_compute_an_exercise(folder):
    made = api.write_exercise(str(folder / "Week 3"), "Exercise 1", QUESTION, {"log.txt": L1},
                              pack_title="Week 3")
    assert made["valid"], made["problems"]
    report = api.validate_pack(str(folder / "Week 3"))
    answers = {b["id"]: b["answer"] for b in report["exercises"][0]["blocks"]}
    assert answers["q1"] == "{a}" and answers["q3"] == "{a, b, c, d, e}"
    assert "p〈a | b·e〉" in answers["q2"]
    assert api.compute_answer(str(folder / "Week 3" / "Exercise 1"), "im.tree")["answer"] == "→(a, ×(∧(b, c), e), d)"
    # Every file of the pack is in the folder's journal, where the app shows it.
    assert {Path(c.path).name for c in changes.pending(folder)} >= {"pack.md", "log.txt", "question.md"}
    with pytest.raises(api.ToolError, match="no ```answer block"):
        api.write_exercise(str(folder / "Week 3"), "Exercise 2", "# Nothing to answer\n")


def test_computes_and_definitions_are_listed():
    assert api.list_computes()["count"] > 100
    terminating = api.definition("terminating")
    assert "Desel" in terminating["source"] and "course lecture" not in terminating["source"]
    assert any(t["topic"].startswith("Boundedness") for t in api.references_list("termination")["topics"])


# ---------------------------------------------------------------------------
# The journal: review and undo
# ---------------------------------------------------------------------------
def test_the_journal_backs_up_and_undoes(folder):
    target = folder / "notes.txt"
    target.write_text("mine\n")
    change = changes.record(target, "the assistant's\n", "test", "Rewrote the notes", client="Claude")
    assert change.kind == "changed" and Path(change.backup).read_text() == "mine\n"
    assert "-mine" in changes.diff_of(change) and "+the assistant's" in changes.diff_of(change)
    created = changes.record(folder / "new.txt", "new\n", "test", "A new file")
    assert [c.id for c in changes.pending(folder)] == [change.id, created.id]
    changes.undo(folder, change.id)
    assert target.read_text() == "mine\n"
    changes.undo(folder, created.id)
    assert not (folder / "new.txt").exists()
    assert changes.pending(folder) == []
    kept = changes.record(target, "again\n", "test", "Again")
    changes.mark_reviewed(folder, kept.id)
    assert changes.pending(folder) == [] and target.read_text() == "again\n"
    # Writing the same bytes is not a change.
    changes.record(target, "again\n", "test", "Same")
    assert len(changes.changes(folder)) == 3


# ---------------------------------------------------------------------------
# Derivations and drawings
# ---------------------------------------------------------------------------
def test_a_derivation_exports_with_its_sources():
    from openprocess.flow import Runner, Workflow
    from openprocess.flow.boxes.discover import alpha_miner
    from openprocess.flow.boxes.input import typed_log
    from openprocess.flow.derivation import derivation, latex_text
    wf = Workflow("d")
    log = wf.add(typed_log, {"text": L1})
    model = wf.add(alpha_miner)
    wf.connect(log, model)
    result = Runner().run(wf).result(model)
    markdown = derivation(result.explanation, result.value, "α", wf.spec(model))
    assert "| 5. Y_L — maximal pairs |" in markdown and "**Sources**" in markdown and "Workflow Mining" in markdown
    latex = derivation(result.explanation, result.value, "α", wf.spec(model), "latex")
    assert "\\begin{tabular}" in latex and "@article" in latex
    assert latex_text("a → b & c_1") == "a $\\rightarrow$ b \\& c\\_1"


def test_diagrams_without_qt():
    from openprocess.mining import alpha_miner, discover_dfg, inductive_miner, parse_simple_log
    from openprocess.mining.drawing import svg_of
    log = parse_simple_log(L1)
    for value in (alpha_miner(log).net, discover_dfg(log), inductive_miner(log).tree):
        text = svg_of(value)
        assert text.startswith("<svg") and text.rstrip().endswith("</svg>")
    assert "↺" not in svg_of(inductive_miner(log).tree) and "∧" in svg_of(inductive_miner(log).tree)


# ---------------------------------------------------------------------------
# The server (needs the MCP SDK)
# ---------------------------------------------------------------------------
def test_the_server_lists_its_tools_and_answers(folder):
    pytest.importorskip("mcp")
    import anyio
    from openprocess.mcp.server import INSTRUCTIONS, build_server
    server = build_server()

    async def go():
        tools = {t.name for t in await server.list_tools()}
        assert {"discover", "build_workflow", "write_box", "write_exercise", "open_in_studio", "journal"} <= tools
        result = await server.call_tool("log_summary", {"log": L1})
        text = result.content[0].text if hasattr(result, "content") else str(result)
        assert '"cases": 6' in text
        failed = await server.call_tool("log_summary", {"log": str(folder / "missing.xes")})
        assert getattr(failed, "is_error", False) or "No such log file" in str(failed)
    try:
        anyio.run(go)
    except Exception as error:  # noqa: BLE001 - the SDK raises a ToolError for the failing call
        assert "No such log file" in str(error)
    assert "CITE" in INSTRUCTIONS and "REPRODUCE" in INSTRUCTIONS and "@box" in INSTRUCTIONS


def test_install_writes_a_client_configuration(tmp_path, monkeypatch):
    from openprocess.mcp import install
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    monkeypatch.setattr(install, "config_path", lambda client: target)
    message = install.install("claude-desktop")
    data = json.loads(target.read_text())
    assert "other" in data["mcpServers"] and "openprocess" in data["mcpServers"] and data["theme"] == "dark"
    assert data["mcpServers"]["openprocess"]["command"] and "Claude" in message
    assert target.with_suffix(".json.before-openprocess").is_file()
    assert '"openprocess"' in install.snippet()


def test_bpmn_round_trip_and_cpn_performance(folder):
    made = api.discover(str(folder / "log.txt"), "inductive")
    as_bpmn = api.convert_model(made["file"], str(folder / "model.bpmn"))
    assert Path(as_bpmn["file"]).read_text().count("bpmn:task") >= 5
    back = api.convert_model(str(folder / "model.bpmn"), str(folder / "back.pnml"))
    assert back["soundness"]["sound"] is True
    assert api.compare_models(made["file"], back["file"])["equivalent"] is True
    assert api.draw(str(folder / "model.bpmn"), fmt="svg")["svg"].startswith("<svg")
    performance = api.cpn_performance(str(ROOT / "examples" / "timed_conveyor.cpn"), runs=2, steps=20)
    assert performance["rows"] and "throughput" in performance["columns"]
