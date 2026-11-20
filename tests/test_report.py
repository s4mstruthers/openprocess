"""The HTML report of an analysis: one self-contained page."""

from __future__ import annotations


from openprocess.flow import Runner, Workflow, standard_library
from openprocess.flow.boxes.check import check_fit
from openprocess.flow.boxes.discover import alpha_miner, directly_follows
from openprocess.flow.boxes.input import typed_log
from openprocess.flow.record import make_record
from openprocess.flow.report import html_report, write_report
from openprocess.flow.reproducibility import status


def _analysis(tmp_path):
    wf = Workflow("Report me")
    log = wf.add(typed_log, {"text": "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"})
    miner = wf.add(alpha_miner)
    fit = wf.add(check_fit)
    dfg = wf.add(directly_follows)
    wf.connect(log, miner)
    wf.connect(miner, fit)
    wf.connect(log, fit)
    wf.connect(log, dfg)
    run = Runner(standard_library(), folder=tmp_path).run(wf)
    assert run.ok, [r.error for r in run.failed()]
    return wf, run


def test_the_report_has_everything_a_reader_needs(tmp_path):
    wf, run = _analysis(tmp_path)
    record = make_record(wf, run, tmp_path, lock=False)
    page = html_report(wf, run, tmp_path, drawings={run.result(wf.order()[1]).node: "<svg data-test='net'></svg>"},
                       record=record, status=status(wf, record, run, tmp_path))
    assert page.startswith("<!doctype html>") and "<link" not in page and "<script" not in page   # self-contained
    assert "Report me" in page and "Recorded · this run reproduces it" in page
    # The summary tiles, in run order, from the boxes' own results.
    assert page.index("6 cases") < page.index("places · transitions") < page.index("fitness")
    # Each box: its result, how it got there, the code, the papers.
    assert "<svg data-test='net'></svg>" in page                       # the drawing the app made
    assert "1. T_L" in page and "def alpha_miner" in page and "W.M.P. van der Aalst" in page
    assert "Fitness" in page or "fitness" in page
    assert "activities · paths" in page                                 # the map's tile
    # What it read, and how to cite.
    assert "What it read" in page and "How to cite" in page and "@software{openprocess," in page
    assert "@article{aalst2004," in page


def test_the_report_is_written_as_a_file_and_survives_a_run_that_has_not_happened(tmp_path):
    wf, run = _analysis(tmp_path)
    target = write_report(tmp_path / "out.html", workflow=wf, run=run, folder=tmp_path)
    assert target.exists() and target.read_text(encoding="utf-8").count("<section class='box'") == 4
    unrun = html_report(wf, None, tmp_path)
    assert "has not run" in unrun and unrun.count("Not run.") == 4
