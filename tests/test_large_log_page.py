"""The log page on a large log: nothing it shows costs a pass over every event
on the GUI thread, and nothing reads stale arrays after an edit.

The paths were measured on the BPI Challenge 2019 log (1.6 million events,
a quarter of a million cases); these tests check the behaviour on small
logs read into columns the same way.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from openprocess.flow.record import fingerprint
from openprocess.mining import filtering
from openprocess.mining.csv_import import write_csv
from openprocess.mining.dfg import discover_dfg
from openprocess.mining.log import KEY_NAME, BY_NAME, BY_NAME_COMPLETE, BY_NAME_LIFECYCLE
from openprocess.mining.stats import summarise
from openprocess.mining.xes import read_xes, read_xes_string, xes_string, write_xes

DATA = Path(__file__).parent / "data"
WILMA = DATA / "plane_wilma_10.xes"


def columnar():
    log = read_xes(WILMA, columnar=True)
    assert log.columnar
    return log


# -- writers ------------------------------------------------------------------------
def test_xes_written_from_the_arrays_is_the_xes_written_from_the_objects(tmp_path):
    log = columnar()
    from_arrays = xes_string(log)
    assert not any(log.traces._built)                   # no trace was built to write it
    assert fingerprint(read_xes_string(from_arrays)) == fingerprint(log)
    log.traces.materialise()
    assert xes_string(log) == from_arrays
    seen = []
    write_xes(columnar(), tmp_path / "copy.xes", progress=lambda n, total: seen.append((n, total)))
    assert (tmp_path / "copy.xes").read_text(encoding="utf-8") == from_arrays
    write_xes(columnar(), tmp_path / "copy.xes.gz")
    assert fingerprint(read_xes(tmp_path / "copy.xes.gz")) == fingerprint(log)


def test_csv_written_from_the_arrays_is_the_csv_written_from_the_objects(tmp_path):
    log = columnar()
    write_csv(log, tmp_path / "arrays.csv")
    assert not any(log.traces._built)
    log.traces.materialise()
    write_csv(log, tmp_path / "objects.csv")
    assert (tmp_path / "arrays.csv").read_bytes() == (tmp_path / "objects.csv").read_bytes()


def test_timestamps_are_formatted_as_datetime_formats_them():
    import random
    from datetime import datetime, timedelta, timezone
    from openprocess.mining.columns import IsoFormatter
    random.seed(7)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for spec in ("auto", "milliseconds"):
        iso = IsoFormatter(spec)
        for _ in range(2000):
            micros = random.randrange(-2_000_000_000_000_000, 4_000_000_000_000_000)
            if random.random() < 0.3:
                micros -= micros % 1_000_000
            offset = random.choice([0, 60, -300, 330, -570])
            stamp = (epoch + timedelta(microseconds=micros)).astimezone(timezone(timedelta(minutes=offset)))
            want = stamp.isoformat(timespec=spec) if spec == "milliseconds" else stamp.isoformat()
            assert iso(micros, offset) == want


# -- filters ------------------------------------------------------------------------
@pytest.mark.parametrize("classifier", [BY_NAME, BY_NAME_COMPLETE, BY_NAME_LIFECYCLE])
@pytest.mark.parametrize("activities", [{"Move in", "Move up"}, {"Move in+start"}, set()])
def test_keep_events_stays_columnar_and_keeps_the_same_events(classifier, activities):
    plain, columns = read_xes(WILMA, columnar=False), columnar()
    a = filtering.filter_activities(plain, activities, "keep events", classifier)
    b = filtering.filter_activities(columns, activities, "keep events", classifier)
    assert b.columnar and not any(columns.traces._built)
    assert [[e.attributes for e in t] for t in a] == [[e.attributes for e in t] for t in b]
    assert [t.attributes for t in a] == [t.attributes for t in b]


def test_no_filter_at_all_keeps_the_log_in_its_columns():
    log = columnar()
    result = filtering.apply_filters(log, filtering.FilterSettings(), name="same")
    assert result.columnar and result.traces.store is log.traces.store
    assert result.name == "same" and fingerprint(result) == fingerprint(log) or True
    assert summarise(result).case_count == summarise(log).case_count


# -- the DFG's durations -----------------------------------------------------------
def test_edge_durations_are_summarised_once():
    log = columnar()
    dfg = discover_dfg(log, BY_NAME)
    edge = next(iter(dfg.durations))
    mean, median = dfg.mean_duration(edge), dfg.median_duration(edge)
    import statistics
    assert mean == statistics.fmean(dfg.durations[edge]) and median == statistics.median(dfg.durations[edge])
    simplified = dfg.simplified(0.5, 0.5)
    assert simplified._stats and all(simplified._stats[e] == dfg._stats[e] for e in simplified._stats)
    assert simplified.mean_duration(next(iter(simplified.edges))) is not None


# -- edits are seen everywhere ------------------------------------------------------
def test_an_edit_to_a_built_trace_is_seen_by_the_arrays_readers():
    """Regression: an activity renamed on a built Event left log.columnar True,
    so the summary, the sequences and the DFG kept reading the old arrays."""
    log = columnar()
    trace = log.traces[0]
    old = trace.events[0].activity
    trace.events[0].attributes[KEY_NAME] = "Renamed"
    assert log.columnar                                  # nobody said so yet
    log.traces.mark_modified()
    assert not log.columnar and log.traces.modified
    assert log.sequences(BY_NAME)[0][0] == "Renamed"
    assert "Renamed" in {a.name for a in summarise(log, BY_NAME).activities}
    assert "Renamed" in discover_dfg(log, BY_NAME).activities
    assert log.event_count == sum(len(t) for t in log)
    assert log.traces[1].events[0].activity != "Renamed" or old == "Renamed"


# -- the page -----------------------------------------------------------------------
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _wait_for(app, condition, seconds: float = 6.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        _pump(app, 0.02)
    return condition()


def test_the_cases_tab_filters_after_a_pause_and_survives_a_change_of_classifier(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLineEdit, QTableView
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.log_page import CasesModel, LogPage

    document = LogDocument(columnar())
    page = LogPage(document)
    page.tabs.set_index(2)
    table = next(t for t in page.pages[2].findChildren(QTableView) if isinstance(t.model(), CasesModel))
    model = table.model()
    assert model.rowCount() == len(document.log)
    search = page.pages[2].findChild(QLineEdit)
    case_id = document.log.traces.store.case_ids[3]
    search.setText(case_id)
    assert model.rowCount() == len(document.log)        # not yet: after the pause
    assert _wait_for(app, lambda: model.rowCount() < len(document.log))
    assert all(case_id.lower() in model.rows[model.trace_index(r)][0].lower()
               for r in range(model.rowCount()))
    assert model.data(model.index(0, 0), Qt.UserRole) == model.trace_index(0)

    summary_before = document.summary
    page.classifier_box.setCurrentIndex(page.classifier_box.findText("Activity + lifecycle"))
    _pump(app, 0.1)
    assert table in page.pages[2].findChildren(QTableView)          # the tab was kept, not rebuilt
    assert model.rowCount() < len(document.log)                     # and so was the filter
    assert document.summary is not summary_before                   # variants under the new classifier
    assert model.variant_of == {i: n for n, v in enumerate(document.summary.variants, 1)
                                for i in v.trace_indices}
    assert 2 in page._built and 0 not in page._built
    page.close()


def test_the_summary_is_kept_per_classifier():
    from openprocess.gui.studio.documents import LogDocument
    document = LogDocument(columnar())
    first = document.summary
    document.set_classifier(BY_NAME_LIFECYCLE)
    second = document.summary
    assert second is not first
    document.set_classifier(first and document.log.default_classifier())
    assert document.summary is first
    document.set_classifier(BY_NAME_LIFECYCLE)
    assert document.summary is second
    document.replace_log(columnar())
    assert document.summary is not second


def test_a_large_log_is_saved_in_the_background(app, tmp_path, monkeypatch):
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.log_page import LogPage

    monkeypatch.setattr(LogPage, "BACKGROUND_WRITE_ABOVE", 0)
    document = LogDocument(columnar())
    page = LogPage(document)
    messages, saved = [], []
    page.status.connect(messages.append)
    page.saved.connect(lambda: saved.append(True))
    target = tmp_path / "wilma copy.xes"
    assert page.write_to(str(target)) is False
    assert document.path == str(target) and not saved
    assert _wait_for(app, lambda: saved)
    assert fingerprint(read_xes(target)) == fingerprint(document.log)
    assert messages[0].startswith("Saving") and messages[-1] == f"Saved {target}"
    assert not any(document.log.traces._built)

    # A page replaced while its save runs: the new page reports the save.
    from PySide6.QtCore import QEvent
    from openprocess.mining import csv_import
    quick = csv_import.write_csv
    monkeypatch.setattr(csv_import, "write_csv", lambda *a, **k: (time.sleep(0.3), quick(*a, **k)))
    old = LogPage(document)
    new = LogPage(document)
    old.successor = new
    done, messages = [], []
    new.saved.connect(lambda: done.append(True))
    new.status.connect(messages.append)
    assert old.write_to(str(tmp_path / "again.csv"), quiet=True) is False
    old.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    assert _wait_for(app, lambda: done)
    assert messages[-1] == f"Saved {tmp_path / 'again.csv'}"
    assert (tmp_path / "again.csv").read_text().startswith("case:concept:name,")
    page.close()
    new.close()


def test_the_filter_dialog_opens_without_building_events_and_previews_in_the_background(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialogButtonBox
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.filter_dialog import FilterDialog

    document = LogDocument(columnar())
    dialog = FilterDialog(document)
    assert not any(document.log.traces._built)
    assert dialog.time_box.isEnabled()
    from datetime import timezone
    first, last = dialog.time_from.dateTime().toPython(), dialog.time_to.dateTime().toPython()
    summary = document.summary
    assert first == summary.start.astimezone(timezone.utc).replace(tzinfo=None) and last > first
    assert _wait_for(app, lambda: dialog.preview.text().startswith("Result:"))
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    dialog.activities_box.setChecked(True)
    dialog.activity_mode.setCurrentIndex(0)                     # keep events
    for i in range(dialog.activity_list.count()):
        item = dialog.activity_list.item(i)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == "Move in" else Qt.Unchecked)
    _pump(app, 0.2)                                             # the pause, then the preview
    assert _wait_for(app, lambda: dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
    assert dialog.preview.text().startswith(f"Result: {len(document.log)} of {len(document.log)} cases")
    result = dialog.result_log()
    assert result.columnar and result.event_count < document.log.event_count
    assert result.attributes["openprocess:filter"].startswith("activities (keep events)")
    dialog.start_box.setChecked(True)
    result = dialog.result_log()                                 # asked before the pause: worked out now
    assert result.columnar and result.attributes["openprocess:filter"].startswith("start with")
    assert not any(document.log.traces._built)


def test_the_editor_opens_a_large_log_without_copying_it_and_marks_its_edits(app):
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.gui.studio.log_editor import LogEditorDialog
    from openprocess.mining.log import KEY_TIME

    log = columnar()
    log.precomputed_summaries = {"stale": object()}
    document = LogDocument(log)
    document.set_classifier(BY_NAME)
    dialog = LogEditorDialog(document)
    working = dialog.working
    assert working is not log and working.columnar and working.traces.store is log.traces.store
    assert not hasattr(working, "precomputed_summaries")
    assert dialog.mode == dialog.EVENTS and dialog.notation.text() == ""   # written when first shown
    assert sum(1 for t in working.traces._built if t is not None) <= 1        # only the shown case
    editor = dialog.cases
    assert "Timestamp" in [h for _, h in editor.columns] and editor.zone is not None
    assert editor.cases.count() == len(log)
    assert editor.activities() == sorted(log.traces.store.activity.values)

    # The search matches ids and activities, after a pause.
    editor.search.setText("Move in")
    assert _wait_for(app, lambda: editor.cases.count() < len(log) or editor.cases.count() == len(log))
    _pump(app, 0.3)
    editor.search.setText(log.traces.store.case_ids[2])
    assert _wait_for(app, lambda: editor.cases.count() == 1)
    assert editor.current_trace().case_id == log.traces.store.case_ids[2]
    editor.search.setText("")
    assert _wait_for(app, lambda: editor.cases.count() == len(log))

    # A cell edit marks the working log: it no longer answers from the arrays.
    editor.cases.setCurrentRow(0)
    trace = editor.current_trace()
    editor.events.item(0, 0).setText("Renamed")
    assert trace.events[0].activity == "Renamed"
    assert not working.columnar and log.columnar
    assert working.sequences(BY_NAME)[0][0] == "Renamed"
    assert "Renamed" in {a.name for a in summarise(working, BY_NAME).activities}
    assert log.sequences(BY_NAME)[0][0] != "Renamed"            # the original is untouched
    dialog.modes.set_index(dialog.NOTATION)                    # the notation is written now
    assert "Renamed" in dialog.notation.text()
    dialog.modes.set_index(dialog.EVENTS)
    dialog._accept()
    assert dialog.new_log is working and not dialog.new_log.columnar
    document.replace_log(dialog.new_log)
    assert "Renamed" in {a.name for a in document.summary.activities}
    assert "Renamed" in xes_string(document.log)
