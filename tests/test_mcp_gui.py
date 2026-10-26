"""The app's side of the assistant: the command port, the review pill and dialog,
and Copy as Markdown on the How tab.  Offscreen, like the other GUI tests."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _ask(app, message: dict, socket_name: str, monkeypatch) -> dict:
    """Send a message from a thread while the event loop runs here."""
    from openprocess.flow import studio
    monkeypatch.setattr(studio, "socket_path", lambda: socket_name)
    answer: dict = {}

    def client():
        answer.update(studio.send(message, timeout=10))
    thread = threading.Thread(target=client)
    thread.start()
    end = time.time() + 10
    while thread.is_alive() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    thread.join(1)
    return answer


def test_the_command_port_opens_files_and_publishes_boxes(app, tmp_path, monkeypatch):
    from openprocess.flow import changes
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.remote import RemoteServer
    from openprocess.mining import alpha_miner, parse_simple_log, write_pnml

    week = tmp_path / "Week 2"
    week.mkdir()
    write_pnml(alpha_miner(parse_simple_log(L1)).net, week / "alpha.pnml")
    import tempfile
    name = str(Path(tempfile.mkdtemp(prefix="op", dir="/tmp")) / "s")     # AF_UNIX paths are short
    from openprocess.flow import studio
    monkeypatch.setattr(studio, "socket_path", lambda: name)
    window = StudioWindow()
    window.show()
    server = RemoteServer(window)
    assert server.listening
    try:
        pong = _ask(app, {"ping": True}, name, monkeypatch)
        assert pong["ok"] and pong["folder"] is None
        opened = _ask(app, {"open": [str(week / "alpha.pnml")], "folder": str(week)}, name, monkeypatch)
        _pump(app, 0.5)
        assert opened["ok"], opened
        assert window.workspace is not None and Path(window.workspace.folder) == week.resolve()
        assert any(getattr(d, "path", None) and Path(d.path).name == "alpha.pnml" for d in window.documents)
        source = ("from openprocess.flow import box, EventLog, Scores\n\n\n@box\n"
                  "def cases(log: EventLog) -> Scores:\n    \"\"\"How many cases.\"\"\"\n"
                  "    return Scores(log.name, {'cases': len(log)})\n")
        published = _ask(app, {"publish_box": {"name": "cases", "source": source}}, name, monkeypatch)
        assert published["ok"], published
        assert (week / "boxes" / "cases.py").read_text() == source
        assert [Path(c.path).name for c in changes.pending(week)] == ["cases.py"]
        refused = _ask(app, {"publish_box": {"name": "x y", "source": "print(1)"}}, name, monkeypatch)
        assert not refused["ok"]
        unknown = _ask(app, {"format": "c:"}, name, monkeypatch)
        assert not unknown["ok"] and "unknown request" in unknown["error"]
    finally:
        server.close()
        window.close()


def test_the_review_pill_and_dialog(app, tmp_path):
    from openprocess.flow import changes
    from openprocess.gui.studio.app import StudioWindow
    from openprocess.gui.studio.review import ReviewDialog

    week = tmp_path / "Week 3"
    week.mkdir()
    (week / "log.txt").write_text(L1)
    window = StudioWindow()
    window.show()
    assert window.open_workspace(str(week))
    window.refresh_review()
    assert window.review_button.isHidden()
    (week / "notes.md").write_text("mine\n")
    change = changes.record(week / "notes.md", "theirs\n", "test", "Rewrote the notes", client="Claude")
    changes.record(week / "model.pnml", "<pnml/>", "test", "A model", client="Claude")
    window.refresh_review()
    assert not window.review_button.isHidden() and window.review_button.text() == "AI changed 2 files · Review"
    dialog = ReviewDialog(week, window=window)
    assert dialog.list.count() == 2
    for row in range(dialog.list.count()):
        if "notes.md" in dialog.list.item(row).text():
            dialog.list.setCurrentRow(row)
    assert "+theirs" in dialog.diff.toPlainText()
    dialog._undo()
    assert (week / "notes.md").read_text() == "mine\n" and change.id not in [c.id for c in changes.pending(week)]
    dialog._keep_all()
    window.refresh_review()
    assert window.review_button.isHidden()
    dialog.close()
    window.close()


def test_copy_as_markdown_on_the_how_tab(app):
    from PySide6.QtWidgets import QApplication, QPushButton
    from openprocess.flow import Runner, Workflow
    from openprocess.flow.boxes.discover import alpha_miner
    from openprocess.flow.boxes.input import typed_log
    from openprocess.gui.flow.viewers import how_widget

    wf = Workflow("d")
    log = wf.add(typed_log, {"text": L1})
    model = wf.add(alpha_miner)
    wf.connect(log, model)
    result = Runner().run(wf).result(model)
    widget = how_widget(result.explanation, None, result.value, spec=wf.spec(model), title="α")
    button = next(b for b in widget.findChildren(QPushButton) if b.text() == "Copy as Markdown")
    button.click()
    text = QApplication.clipboard().text()
    assert text.startswith("## α") and "Y_L" in text and "**Sources**" in text
