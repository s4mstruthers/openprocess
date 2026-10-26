"""Snapshots: every finished run kept, what changed, two compared, settings restored;
versions: the record kept each time you say so."""

from __future__ import annotations

from openprocess.flow import Runner, Workflow, standard_library
from openprocess.flow.boxes.check import soundness
from openprocess.flow.boxes.discover import inductive_miner
from openprocess.flow.boxes.input import typed_log
from openprocess.flow.record import load, save
from openprocess.flow.snapshots import LIMIT, History, Snapshot, compare, settings_to_apply, take


def _run(wf):
    run = Runner(standard_library()).run(wf)
    assert run.ok, [r.error for r in run.failed()]
    return run


def test_snapshots_say_what_changed_and_compare_box_by_box():
    wf = Workflow("Snaps")
    log = wf.add(typed_log, {"text": "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"})
    miner = wf.add(inductive_miner)
    wf.connect(log, miner)
    history = History()
    first = take(wf, _run(wf), None)
    assert first.label == "First run" and first.figures[log.id][0] == "6 cases" and history.add(first)
    assert not history.add(take(wf, _run(wf), history.latest))         # the same again: not kept
    wf.set(miner, noise=0.3)
    second = take(wf, _run(wf), history.latest)
    assert second.label == "Inductive Miner: noise 0.0 → 0.3" and history.add(second)
    by_title = {r.title: r for r in compare(first, second)}
    assert not by_title["Typed log"].changed
    assert by_title["Inductive Miner"].changes == ["Inductive Miner: noise 0.0 → 0.3"]
    assert settings_to_apply(first, miner.id)["noise"] == 0.0
    check = wf.add(soundness)
    wf.connect(miner, check)
    third = take(wf, _run(wf), history.latest)
    assert third.label == "+ Soundness"
    for _ in range(LIMIT + 5):
        history.add(Snapshot(f"t{_}", settings={"n": {"x": _}}))
    assert len(history.snapshots) == LIMIT


def test_the_history_travels_with_the_workflow_file(tmp_path):
    wf = Workflow("Kept")
    log = wf.add(typed_log, {"text": "[<a,b>^2]"})
    run = _run(wf)
    history = History()
    history.add(take(wf, run, None))
    path = tmp_path / "kept.analysis"
    save(wf, path, run, tmp_path, lock=False, history=history.to_dict())
    again, record = load(path, wf.library)
    restored = History.from_dict(record.history)
    assert len(restored.snapshots) == 1 and restored.latest.label == "First run"
    assert restored.latest.figures[log.id] == ("2 cases", "4 events")


def test_versions_keep_the_workflow_and_the_record(tmp_path):
    """Keeping a version stores the whole workflow, the record and a snapshot;
    the record only moves with a version; a file from before versions gives v1."""
    import json

    from openprocess.flow.record import load_all, make_record
    from openprocess.flow.versions import Versions
    wf = Workflow("Kept")
    log = wf.add(typed_log, {"text": "[<a,b>^2]"})
    miner = wf.add(inductive_miner, {"noise": 0.0}); wf.connect(log, miner)
    path = tmp_path / "kept.analysis"
    versions = Versions()
    record = save(wf, path, _run(wf), tmp_path, lock=False, versions=versions)
    assert [v.title for v in versions.items] == ["v1 · First run"] and versions.latest.record.results == record.results
    wf.set(miner.id, noise=0.3)
    run = _run(wf)
    kept = save(wf, path, run, tmp_path, lock=False, previous=record, versions=versions)
    assert kept.results == record.results and [v.number for v in versions.items] == [1]      # not moved
    kept = save(wf, path, run, tmp_path, lock=False, previous=record, versions=versions, keep="more noise")
    assert kept.results != record.results
    second = versions.latest
    assert second.title == "v2 · more noise" and second.label == "Inductive Miner: noise 0.0 → 0.3"
    assert second.workflow["boxes"][1]["settings"]["noise"] == 0.3 and second.snapshot.figures[log.id][0] == "2 cases"
    again, loaded, back = load_all(path, wf.library)
    assert [v.title for v in back.items] == ["v1 · First run", "v2 · more noise"]
    assert back.get(1).workflow["boxes"][1]["settings"]["noise"] == 0.0 and loaded.results == kept.results
    data = json.loads(path.read_text())
    assert data["format"] == "analysis/1" and "history" not in data["versions"][0]["record"]
    # A file from before versions: its record is v1.
    old = {"format": "cpnflow/1", **wf.to_dict(), "record": make_record(wf, run, tmp_path, lock=False).to_dict()}
    (tmp_path / "old.cpnflow").write_text(json.dumps(old, default=str))
    _wf, record, versions = load_all(tmp_path / "old.cpnflow", wf.library)
    assert [v.title for v in versions.items] == ["v1 · Recorded before versions"]
    assert versions.latest.snapshot.fingerprints == record.results
    try:
        save(wf, path, None, tmp_path, keep="nothing ran")
    except ValueError as error:
        assert "no box failed" in str(error)
    else:
        raise AssertionError("a version needs a run")
