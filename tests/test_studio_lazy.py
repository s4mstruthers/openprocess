"""Reopening a folder builds the page of the file it shows, and the others when they are first shown."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(__file__).parent / "data"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "petri"


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _folder(tmp_path, selected: str | None) -> Path:
    week = tmp_path / "Week 3"
    week.mkdir()
    for name in ("order_handling_sound.pnml", "order_handling_unsound.pnml", "textbook_l1.pnml"):
        shutil.copy(EXAMPLES / name, week / name)
    shutil.copy(DATA / "plane_wilma_10.xes", week / "wilma.xes")
    state = {"about": "test", "version": 1, "selected": selected,
             "open": [{"path": n} for n in ("order_handling_sound.pnml", "order_handling_unsound.pnml",
                                           "textbook_l1.pnml", "wilma.xes")],
             "settings": {"space": "model"}}
    (week / ".openprocess").write_text(json.dumps(state))
    return week


@pytest.mark.parametrize("selected", ["textbook_l1.pnml", None])
def test_only_the_shown_file_is_built(app, tmp_path, selected):
    from openprocess.gui.studio.app import StudioWindow
    week = _folder(tmp_path, selected)
    window = StudioWindow()
    window.show()
    assert window.open_workspace(str(week))
    _pump(app, 1.5)
    assert len(window.documents) == 4                              # every file came back, listed
    built = [d for d in window.documents if window.pages.built(d.id) is not None]
    assert len(built) == 1                                         # only the one on screen has a page
    shown = Path(built[0].path).name
    assert shown == (selected or "order_handling_sound.pnml")      # none saved: the first of the space
    # Showing another builds it; the file's content is all there.
    other = next(d for d in window.documents if Path(d.path).name == "order_handling_unsound.pnml")
    window._select_document(other)
    _pump(app, 0.3)
    page = window.pages.built(other.id)
    assert page is not None and window.content.currentWidget() is window.holders[other.id]
    # Closing a file whose page was never built works, and is remembered.
    log = next(d for d in window.documents if d.path.endswith("wilma.xes"))
    assert window.pages.built(log.id) is None
    window._drop_page(log)
    assert log.id not in window.pages
    window.close()
