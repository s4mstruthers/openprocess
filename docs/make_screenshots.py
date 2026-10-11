"""Make the README's screenshots from the app itself.

    python docs/make_screenshots.py            # writes docs/screenshots/*.png
    python docs/make_screenshots.py welcome compare   # only these

Nothing is shown on screen: Qt's "offscreen" platform renders the window into
memory at 1600 x 960, and the app is driven the way the GUI tests drive it.
The pictures come from a temporary folder, "Week 2", filled from the
repository's example files (tests/data, examples/petri, the demo exercises)
and two textbook logs written on the spot, so they never depend on a file of
your own.  Run it again whenever the app changes its looks: a stale
screenshot misleads more than none.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["OPENPROCESS_NO_MOTION"] = "1"            # no fades: every grab is a settled window
os.environ.setdefault("QT_SCALE_FACTOR", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "screenshots"
SIZE = (1600, 960)

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"
L2 = ("[<a,b,c,d>^3, <a,c,b,d>^4, <a,b,c,e,f,b,c,d>^2, <a,b,c,e,f,c,b,d>, "
      "<a,c,b,e,f,b,c,d>^2, <a,c,b,e,f,b,c,e,f,c,b,d>]")


def make_folder(where: Path) -> Path:
    """A course folder, "Week 2": logs and nets in subfolders, as a student keeps them."""
    from openprocess.mining.log import EventLog, parse_simple_log
    from openprocess.mining.xes import write_xes
    folder = where / "Week 2"
    (folder / "logs").mkdir(parents=True)
    (folder / "models").mkdir()
    (folder / "assignment 1").mkdir()
    shutil.copy(ROOT / "tests/data/plane_wilma_10.xes", folder / "logs/Wilma 10 passengers.xes")
    for name, text in (("Textbook L1", L1), ("Textbook L2", L2)):
        write_xes(EventLog.from_simple_log(parse_simple_log(text), name=name), folder / "logs" / f"{name}.xes")
    shutil.copy(ROOT / "examples/petri/order_handling_unsound.pnml", folder / "models/order handling.pnml")
    shutil.copy(ROOT / "examples/petri/order_handling_sound.pnml", folder / "models/order handling (sound).pnml")
    shutil.copy(ROOT / "examples/petri/loop_with_silent_step.pnml", folder / "models/loop with tau.pnml")
    shutil.copy(ROOT / "examples/petri/textbook_l1.pnml", folder / "textbook L1.pnml")
    shutil.copy(ROOT / "tests/data/plane_boarding.cpn", folder / "plane boarding.cpn")
    shutil.copytree(ROOT / "openprocess/exercises", where / "Exercises")
    return folder


class Shooter:
    def __init__(self, only: set[str]) -> None:
        from PySide6.QtWidgets import QApplication

        from openprocess.gui import theme
        from openprocess.gui.studio import style
        from openprocess.gui.studio.app import APPLICATION_NAME, app_icon
        self.app = QApplication.instance() or QApplication([sys.argv[0]])
        self.app.setApplicationName(APPLICATION_NAME)
        self.app.setWindowIcon(app_icon())
        self.app.setStyle("Fusion")
        self.app.setFont(theme.ui_font(13))
        self.app.setStyleSheet(style.stylesheet())
        self.only = only
        self.window = None
        self.made: list[str] = []

    # -- plumbing ----------------------------------------------------------------------------
    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(3):
            self.app.processEvents()

    def wait(self, condition, seconds: float = 30.0, what: str = "") -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            if condition():
                self.pump(0.05)
                return
            time.sleep(0.02)
        raise TimeoutError(f"gave up waiting for {what or condition}")

    def wait_run(self, page, seconds: float = 90.0) -> None:
        self.wait(lambda: page.run is not None and not page._running, seconds, "the analysis to run")
        failed = page.run.failed()
        if failed:
            raise RuntimeError(f"boxes failed: {[r.error for r in failed]}")

    def new_window(self):
        from openprocess.gui.studio.app import StudioWindow
        self.window = StudioWindow(persist=False)
        self.window.resize(*SIZE)
        self.window.show()
        self.pump(0.3)
        return self.window

    def wants(self, name: str) -> bool:
        return not self.only or name in self.only

    def shot(self, name: str, widget=None) -> None:
        if not self.wants(name):
            return
        self.pump(0.4)                                  # deferred fits and layouts
        (widget or self.window).grab().save(str(OUT / f"{name}.png"))
        self.made.append(name)
        print(f"  {name}.png")

    def open_log(self, path: Path):
        from openprocess.gui.studio.documents import LogDocument
        before = {d.id for d in self.window.documents}
        self.window.open_path(str(path))
        self.wait(lambda: any(isinstance(d, LogDocument) and d.id not in before for d in self.window.documents),
                  60, f"{path.name} to load")
        document = next(d for d in self.window.documents if isinstance(d, LogDocument) and d.id not in before)
        self.window._select_document(document)
        self.pump(0.3)
        return document, self.window.current_page()

    def open_net(self, path: Path):
        self.window.open_path(str(path))
        self.pump(0.5)
        return self.window.current_page()

    # -- the pictures --------------------------------------------------------------------------
    def run(self, folder: Path, exercises: Path) -> None:
        from openprocess.gui.flow.templates import TEMPLATES
        w = self.new_window()
        self.shot("studio-welcome")

        # Mine: a log's own pages, then the analyses built from it.
        assert w.open_workspace(str(folder))
        self.pump(0.5)
        w.set_space("mine")
        w.material_section.setExpanded(True)
        _wilma, page = self.open_log(folder / "logs/Wilma 10 passengers.xes")
        page.tabs.set_index(0)
        self.shot("studio-overview")
        page.tabs.set_index(3)
        self.shot("studio-dotted-chart")
        page.tabs.set_index(4)
        self.shot("studio-process-map")

        l1, _ = self.open_log(folder / "logs/Textbook L1.xes")
        l2, l2_page = self.open_log(folder / "logs/Textbook L2.xes")
        l2_page.tabs.set_index(6)                       # Discover: the hand-off to an analysis
        self.pump(0.2)
        w.discover_in_analysis(l2, "inductive_miner")
        analysis = w.current_page()
        self.wait_run(analysis)
        miner = next(n for n in analysis.workflow.nodes.values() if n.box.endswith("inductive_miner"))
        analysis.select(miner.id, 0)
        self.shot("studio-discover")

        w.discover_in_analysis(l1, "alpha_miner")
        alpha_page = w.current_page()
        self.wait_run(alpha_page)
        alpha = next(n for n in alpha_page.workflow.nodes.values() if n.box.endswith("alpha_miner"))
        alpha_page.select(alpha.id, 0)
        self.shot("studio-alpha")
        alpha_page.select(alpha.id, 1)                  # How: the eight steps
        self.shot("studio-alpha-how")

        w.action_new_workflow(TEMPLATES[0][1])          # Discover and check
        check = w.current_page()
        self.wait_run(check)
        fit = next(n for n in check.workflow.nodes.values() if n.box.endswith("check_fit"))
        check.select(fit.id, 0)
        self.shot("studio-conformance")
        check.close_panel()
        check.show_view(1)                              # Summary, with the process map
        self.pump(0.3)
        if check.summary.add_map_button.isVisible():
            check.summary.add_map_button.click()
            self.wait_run(check)
            check.show_view(1)
            self.pump(0.3)
        self.shot("workflow-summary")
        check.show_view(0)

        w.action_new_workflow(TEMPLATES[1][1])          # Compare discovery: three miners side by side
        compare_page = w.current_page()
        self.wait_run(compare_page)
        compare = next(n for n in compare_page.workflow.nodes.values() if n.box.endswith("compare"))
        compare_page.select(compare.id, 0)
        self.shot("workflow-canvas")

        w.compare([l1, l2])
        comparison = w.current_page()
        self.pump(0.5)
        comparison.tabs.set_index(1)
        self.shot("compare")
        comparison.tabs.set_index(3)
        self.shot("compare-dotted")

        # Model: WF-nets and their soundness.
        w.set_space("model")
        page = self.open_net(folder / "models/order handling.pnml")
        page.inspector_tabs.set_index(2)
        self.wait(lambda: page.soundness_card.findChildren(self._button_class()) and any(
            b.text().startswith("Show") for b in page.soundness_card.findChildren(self._button_class())),
            60, "the soundness check")
        self.shot("petri-analysis")
        self.definition_shot(page, "option_to_complete")
        page.start_rename(next(iter(page.net.all_transitions())).id)
        self.pump(0.2)
        self.shot("petri-editing")
        page.view.close_editor()
        show = next(b for b in page.soundness_card.findChildren(self._button_class()) if b.text().startswith("Show"))
        show.click()
        self.pump(0.5)
        self.shot("petri-counterexample")
        page.mode_switch.set_index(0)

        sound = self.open_net(folder / "models/order handling (sound).pnml")
        sound.inspector_tabs.set_index(2)
        self.wait(lambda: sound.footprint_card.isVisibleTo(sound), 60, "the sound net's analysis")
        self.pump(0.3)
        sound.inspector_stack.currentWidget().ensureWidgetVisible(sound.footprint_card)
        self.shot("petri-footprint")
        sound.inspector_tabs.set_index(0)
        sound.mode_switch.set_index(1)                  # Step through, with the folder beside it
        self.shot("workspace")
        sound.mode_switch.set_index(0)

        # Coloured nets.
        cpn = self.open_net(folder / "plane boarding.cpn")
        cpn.mode_switch.set_index(1)
        for _ in range(4):
            cpn.step_button.click()
            self.pump(0.1)
        self.shot("cpn-step-through")
        cpn.mode_switch.set_index(0)
        cpn.tool_switch.set_index(0)
        arc = max(cpn.scene.arc_items.values(), key=lambda a: len(a.arc.bendpoints))
        cpn.scene.clearSelection()
        arc.setSelected(True)
        cpn.inspector_tabs.set_index(1)
        self.shot("cpn-arc-editing")
        cpn.scene.clearSelection()
        cpn.inspector_tabs.set_index(3)
        cpn.space_button.click()
        self.wait(lambda: not cpn.busy, 300, "the state space")
        self.shot("cpn-state-space")

        # Learn: an exercise, its worksheet beside its materials.
        assert w.open_exercise(exercises / "7 Conformance/Exercise 7.1 Replay, alignments and workflows")
        self.pump(0.8)
        self.shot("learn-exercise")
        w.leave_learn()
        self.pump(0.3)

        # Dark mode: a fresh window, since the colours are set when widgets are built.
        self.dark_shot(folder)
        for document in w.documents:
            document.dirty = False
        w.close()

    def _button_class(self):
        from PySide6.QtWidgets import QPushButton
        return QPushButton

    def definition_shot(self, page, key: str) -> None:
        """The ⓘ card of one soundness property, grabbed on its own (it is a popup window)."""
        if not self.wants("petri-definition"):
            return
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from openprocess.gui.studio.widgets import Verdict
        verdicts = {v.definition.key: v for v in page.findChildren(Verdict) if getattr(v, "definition", None)}
        QTest.mouseClick(verdicts[key].title, Qt.LeftButton)
        self.pump(0.3)
        popup = verdicts[key].last_popup
        self.shot("petri-definition", popup)
        popup.close()
        self.pump(0.1)

    def dark_shot(self, folder: Path) -> None:
        if not self.wants("studio-alpha-dark"):
            return
        from openprocess.gui import theme
        from openprocess.gui.studio import style
        from openprocess.gui.studio.documents import LogDocument
        from openprocess.mining import read_xes
        original = theme.is_dark
        theme.is_dark = lambda: True
        self.app.setStyleSheet(style.stylesheet())
        try:
            w = self.new_window()
            path = folder / "logs/Textbook L1.xes"
            log = LogDocument(read_xes(path), path=str(path))
            w.add_document(log)
            self.pump(0.3)
            w.discover_in_analysis(log, "alpha_miner")
            page = w.current_page()
            self.wait_run(page)
            alpha = next(n for n in page.workflow.nodes.values() if n.box.endswith("alpha_miner"))
            page.select(alpha.id, 0)
            self.shot("studio-alpha-dark")
            for document in w.documents:
                document.dirty = False
            w.close()
        finally:
            theme.is_dark = original
            self.app.setStyleSheet(style.stylesheet())


def main(argv: list[str]) -> int:
    only = set(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openprocess-screenshots-") as temp:
        folder = make_folder(Path(temp))
        shooter = Shooter(only)
        print(f"Screenshots into {OUT}:")
        shooter.run(folder, Path(temp) / "Exercises")
    print(f"{len(shooter.made)} written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
