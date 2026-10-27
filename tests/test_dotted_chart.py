"""The dotted chart on a large log: the dots as arrays, the window's dots by
bisection, the dense picture, the preview during a gesture, and the pointer.

The log here is small; the thresholds are lowered so the paths a
million-event log takes are the ones exercised.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from openprocess.mining.dotted import Dots, DotSettings
from openprocess.mining.log import BY_NAME, EventLog, parse_simple_log

LOG = EventLog.from_simple_log(parse_simple_log("[<a,b,c>^30, <a,c,b>^20, <b,a>^10]"))


def test_dots_are_arrays_sorted_by_x_with_cases_consecutive():
    dots = Dots(LOG, BY_NAME, DotSettings(x_mode=4))           # position in the case
    assert isinstance(dots.xs, np.ndarray) and len(dots) == 170
    assert np.all(np.diff(dots.xs_sorted) >= 0)
    assert np.array_equal(dots.xs[dots.order], dots.xs_sorted)
    assert np.all(np.diff(dots.case) >= 0)
    # between: every dot at x == 1 (the second event of each case), by increasing x
    second = dots.between(1.0, 1.0)
    assert len(second) == 60 and np.all(dots.xs[second] == 1.0)
    assert dots.between(5.0, 9.0).size == 0
    # the dots of a case
    span = dots.case_dots(0)
    assert list(dots.case[list(span)]) == [0, 0, 0] and dots.case_dots(999) == range(170, 170)


def test_rows_colours_and_counts_match_the_log():
    dots = Dots(LOG, BY_NAME, DotSettings(x_mode=4, rows="Activity", sort=3, descending=True))
    assert dots.row_values == ["b", "a", "c"]                   # by count: 60, 60, 50; descending, ties too
    assert dots.colour_values == ["a", "b", "c"]
    assert dots.colour_counts == {"a": 60, "b": 60, "c": 50}
    assert [dots.row_stats[v][2] for v in dots.row_values] == [60, 60, 50]


@pytest.fixture
def app():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _chart(app, monkeypatch, batch_from: int):
    from openprocess.gui.studio import dotted_chart
    from openprocess.gui.studio.documents import LogDocument
    monkeypatch.setattr(dotted_chart, "BATCH_FROM", batch_from)
    chart = dotted_chart.DottedChart()
    chart.resize(600, 400)
    chart.set_data(dotted_chart.DotData(LogDocument(LOG), DotSettings(x_mode=4)))
    return chart


def test_a_dense_picture_is_rasterised_from_arrays_and_hidden_colours_stay_out(app, monkeypatch):
    chart = _chart(app, monkeypatch, batch_from=10)             # 170 dots: the dense path
    before = chart._render().toImage()
    chart.hidden_colours = {0}                                   # hide "a"
    chart._invalidate()
    after = chart._render().toImage()
    assert before != after
    chart.selected_case = 3                                      # everything faded, one case on top
    chart._invalidate()
    assert chart._render().toImage() != after


def test_a_slow_picture_is_previewed_during_a_gesture_then_drawn_for_real(app, monkeypatch):
    from PySide6.QtGui import QPainter, QPixmap
    chart = _chart(app, monkeypatch, batch_from=10)
    chart.show()
    app.processEvents()
    chart.repaint()                                              # the real picture, cached
    assert chart._cache is not None and chart._cache_view is not None
    chart._render_cost = 1.0                                     # pretend it was slow
    chart.set_zoom(2.0, 2.0)
    cached = chart._cache
    chart.repaint()
    assert chart._cache is cached and chart._settle.isActive()   # a preview; the real one is due
    assert chart.dot_at(chart.plot_rect().center()) is None      # nothing is in place yet
    end = time.time() + 3
    while time.time() < end and chart._settle.isActive():
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    chart.repaint()
    assert chart._cache is not cached and not chart._render_due


def test_the_pointer_finds_the_nearest_shown_dot(app, monkeypatch):
    chart = _chart(app, monkeypatch, batch_from=10_000)
    chart.show()
    app.processEvents()
    chart.repaint()
    d = chart.data
    rect = chart.plot_rect()
    i = int(np.flatnonzero((d.rows == 0) & (d.xs == 1.0))[0])   # row 0, x 1: a dot
    point = chart.to_screen(d.xs[i], d.rows[i], rect)
    found = chart.dot_at(point)
    assert found is not None and d.rows[found] == 0 and d.xs[found] == 1.0
    chart.hidden_colours = set(range(len(d.colour_values)))
    assert chart.dot_at(point) is None


def test_the_hover_counts_the_dots_drawn_under_one(app, monkeypatch):
    from openprocess.gui.studio import dotted_chart
    from openprocess.gui.studio.documents import LogDocument
    chart = _chart(app, monkeypatch, batch_from=10_000)
    # Rows by activity, x by position in case (from 0): the 10 <b,a> cases
    # put 10 dots on one spot.
    chart.set_data(dotted_chart.DotData(LogDocument(LOG), DotSettings(x_mode=4, rows="Activity")))
    chart.show()
    app.processEvents()
    chart.repaint()
    d = chart.data
    a_row = d.rows[next(j for j in range(len(d.xs)) if d.event(j).activity == "a" and d.xs[j] == 1.0)]
    pile = np.flatnonzero((d.rows == a_row) & (d.xs == 1.0))
    assert len(pile) == 10
    i = int(pile[0])
    stack = chart.stacked_with(i)
    assert stack[0] == i and len(stack) == len(pile)
    text = chart.describe_dot(i)
    assert f"{len(pile) - 1} more events under this dot" in text
    # Hidden colours are not counted, and a lone dot says nothing.
    chart.hidden_colours = set(range(len(d.colour_values)))
    assert len(chart.stacked_with(i)) == 1
    assert "under this dot" not in chart.describe_dot(i)
