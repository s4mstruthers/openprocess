"""Small things the window does for whoever sits in front of it: names in the
sidebar that can be told apart, a window that opens the way it was left, a
status bar that does not talk about another file, a note that is not cut short.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionViewItem  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _log(name: str, text: str = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"):
    from openprocess.gui.studio.documents import LogDocument
    from openprocess.mining.log import EventLog, parse_simple_log
    document = LogDocument(EventLog.from_simple_log(parse_simple_log(text), name))
    document.path = name                   # no "save this log?" question on close
    return document


def test_a_long_name_keeps_its_end_and_makes_room_for_the_close_button_only_when_shown(app):
    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.show()
    document = _log("order handling from the second lecture (sound)")
    window.add_document(document)
    index = window.tree.indexFromItem(window.items[document.id])
    assert window.tree.textElideMode() == Qt.ElideMiddle

    def text_drawn(state) -> str:
        option = QStyleOptionViewItem()
        option.initFrom(window.tree)
        option.widget = window.tree
        option.rect = QRect(0, 0, 200, 28)
        option.state = state
        window.delegate.initStyleOption(option, index)
        return option.text

    plain = text_drawn(QStyle.State_Enabled)
    assert plain == "order handling from the second lecture (sound)"      # the view shortens it to fit
    for state in (QStyle.State_Enabled | QStyle.State_MouseOver, QStyle.State_Enabled | QStyle.State_Selected):
        shown = text_drawn(state)
        assert shown.startswith("order") and shown.endswith("(sound)") and "…" in shown
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 200, 28)
        width = window.tree.fontMetrics().horizontalAdvance(shown)
        assert width < window.delegate._button_rect(option.rect).left()   # clear of the ✕
    window.close()


def test_the_window_opens_at_the_size_and_sidebar_width_it_was_left_at(app, tmp_path, monkeypatch):
    from openprocess.gui.studio import app as studio_app
    store = str(tmp_path / "settings.ini")
    monkeypatch.setattr(studio_app, "QSettings", lambda *_names: QSettings(store, QSettings.IniFormat))
    monkeypatch.setattr(studio_app.StudioWindow, "_save_session", lambda self: None)
    monkeypatch.setattr(studio_app.StudioWindow, "_restore_session", lambda self: None, raising=False)

    window = studio_app.StudioWindow(persist=True)
    window.show()
    window.resize(760, 600)                 # within the offscreen screen (800 x 800)
    window._root.setSizes([300, 460])
    app.processEvents()
    left_at = window.sidebar.width()
    assert 280 <= left_at <= 320
    window.close()

    again = studio_app.StudioWindow(persist=True)
    again.show()
    app.processEvents()
    assert (again.width(), again.height()) == (760, 600)
    assert abs(again.sidebar.width() - left_at) <= 2
    again.close()


def test_a_saved_size_larger_than_the_screen_is_cut_to_it(app, tmp_path, monkeypatch):
    from openprocess.gui.studio import app as studio_app
    store = str(tmp_path / "settings.ini")
    settings = QSettings(store, QSettings.IniFormat)
    settings.setValue("window/width", 50_000)
    settings.setValue("window/height", 40_000)
    settings.setValue("window/sidebar_width", 5_000)
    settings.sync()
    monkeypatch.setattr(studio_app, "QSettings", lambda *_names: QSettings(store, QSettings.IniFormat))
    monkeypatch.setattr(studio_app.StudioWindow, "_save_session", lambda self: None)
    window = studio_app.StudioWindow(persist=True)
    area = window.screen().availableGeometry()
    assert window.width() <= area.width() and window.height() <= area.height()
    window.show()
    app.processEvents()
    assert window.sidebar.width() <= 360
    window.close()


def test_the_status_bar_does_not_keep_a_hint_about_the_file_you_left(app):
    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.show()
    first, second = _log("first"), _log("second")
    window.add_document(first)
    window.add_document(second)
    window.tree.setCurrentItem(window.items[first.id])
    window.statusBar().showMessage("a hint about the first log")
    window.tree.setCurrentItem(window.items[first.id])                 # the same file: the hint stays
    assert window.statusBar().currentMessage() == "a hint about the first log"
    window.tree.setCurrentItem(window.items[second.id])
    assert window.statusBar().currentMessage() == ""
    # What is said while a file is shown stays for that file.
    window.statusBar().showMessage("about the second")
    assert window.statusBar().currentMessage() == "about the second"
    window.close()


def test_opening_a_file_still_says_what_was_opened(app, tmp_path):
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.mining.log import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes
    path = tmp_path / "orders.xes"
    write_xes(EventLog.from_simple_log(parse_simple_log("[<a,b>^2, <a,c>]"), "orders"), path)
    window = StudioWindow()
    window.show()
    window.add_document(_log("already open"))
    window.statusBar().showMessage("a hint about the open log")
    window.open_path(str(path))
    for _ in range(200):
        app.processEvents()
        if "orders" in window.statusBar().currentMessage():
            break
    assert "orders" in window.statusBar().currentMessage()
    for document in window.documents:
        document.path = document.path or "x"
    window.close()


def test_the_note_of_a_comparison_is_shown_whole(app):
    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.show()
    one = _log("Textbook L1")
    two = _log("Textbook L2", "[<a,b,c,d>^3, <a,f,d>]")
    window.add_document(one)
    window.add_document(two)
    window.compare([one, two])
    page = window.current_page()
    page.tabs.set_index(1)                                             # Activities
    app.processEvents()
    from PySide6.QtWidgets import QTableView
    tables = [t for t in page.findChildren(QTableView)
              if t.model() is not None and t.model().headerData(t.model().columnCount() - 1, Qt.Horizontal) == "Note"]
    assert tables
    header = tables[0].horizontalHeader()
    last = tables[0].model().columnCount() - 1
    assert header.sectionResizeMode(last) == QHeaderView.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.Stretch
    window.close()


def test_the_command_line_says_its_version(capsys):
    import openprocess
    from openprocess import cli
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"OpenProcess {openprocess.__version__}"


def test_a_log_without_times_says_so_instead_of_an_empty_mean(app):
    from PySide6.QtWidgets import QLabel

    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.show()
    window.add_document(_log("typed"))
    app.processEvents()
    texts = [found.text() for found in window.current_page().findChildren(QLabel)]
    assert "the log has no timestamps" in texts
    assert not any(text.startswith("mean") for text in texts)
    window.close()


def test_a_button_that_shows_only_a_sign_has_a_name_for_a_screen_reader(app):
    from PySide6.QtWidgets import QAbstractButton, QLineEdit, QPushButton

    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.widgets import spoken_name

    button = QPushButton("−")
    button.setToolTip("Zoom out (Ctrl+-)\nHold to keep zooming")
    assert spoken_name(button) == "Zoom out"
    assert spoken_name(QPushButton("Save As…")) == ""                  # its text is read
    assert spoken_name(QPushButton("✕")) == "Close"
    field = QLineEdit()
    field.setPlaceholderText("Filter…")
    assert spoken_name(field) == "Filter"

    window = StudioWindow()
    window.show()
    window.add_document(_log("named"))
    app.processEvents()
    nameless = [found for found in window.findChildren(QAbstractButton)
                if found.isVisible() and not found.accessibleName()
                and sum(c.isalnum() for c in found.text()) < 2]
    assert not nameless, [(found.objectName(), found.text(), found.toolTip()) for found in nameless]
    assert window.sidebar_toggle.accessibleName().startswith("Hide the sidebar") \
        if hasattr(window, "sidebar_toggle") else True
    window.close()


def test_a_result_table_keeps_the_order_its_box_gave_it(app):
    from openprocess.gui.flow.viewers import _table
    table = _table(["place", "total"], [["p5", 9.0], ["source", 0.0], ["p1", 4.0]])
    assert [table.item(row, 0).text() for row in range(3)] == ["p5", "source", "p1"]
    assert table.isSortingEnabled()                                    # a header click still sorts
    table.sortByColumn(0, Qt.AscendingOrder)
    assert [table.item(row, 0).text() for row in range(3)] == ["p1", "p5", "source"]


def test_the_rules_template_runs_on_a_typed_log(app):
    import time

    from openprocess.gui.flow.templates import TEMPLATES
    from openprocess.gui.studio.app import StudioWindow
    window = StudioWindow()
    window.show()
    builder = next(build for name, build, _about in TEMPLATES if name == "Rules and exceptions")
    window.action_new_workflow(builder)
    page = window.current_page()
    end = time.time() + 30
    while time.time() < end and (page.run is None or page._running):
        app.processEvents()
        time.sleep(0.02)
    assert page.run is not None and page.run.ok, [(r.node, r.error) for r in page.run.failed()]
    check = next(n for n in page.workflow.nodes.values() if n.box.endswith("check_rules"))
    result = page.run.value(check)
    assert sum(result.breaking.simple_log().values()) == 1            # <a,e,d>: no b, no c
    for document in window.documents:
        document.dirty = False
    window.close()
