"""The ``openprocess-cpn`` entry point: the CPN editor on its own.

The editor itself is :class:`openprocess.gui.studio.cpn_page.CpnPage`, the same page
OpenProcess Studio shows for a ``.cpn`` file.  ``openprocess-cpn`` simply opens Studio with
that model (or a new, empty one), so there is one application with one look,
whether you start from a CPN model or from an event log.

:class:`MainWindow` is kept for scripts and tests that want a bare window
around a single model, without Studio's sidebar.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMainWindow

from ..model.net import CPNet

APPLICATION_NAME = "OpenProcess"


def _empty_model(name: str = "Untitled") -> CPNet:
    net = CPNet(name)
    net.add_declaration("colset UNIT = unit;")
    net.add_declaration("colset INT = int;")
    net.add_page("Top")
    net.compile()
    return net


class MainWindow(QMainWindow):
    """A window holding one CPN page (no sidebar)."""

    def __init__(self, net: CPNet | None = None, path: Path | None = None) -> None:
        super().__init__()
        from .studio import style
        from .studio.cpn_page import CpnPage
        from .studio.documents import CpnDocument
        self.document = CpnDocument(net if net is not None else _empty_model(),
                                    path=str(path) if path else None)
        self.page = CpnPage(self.document)
        self.page.setObjectName("studioRoot")
        self.setCentralWidget(self.page)
        self.setStyleSheet(style.stylesheet())
        self.page.status.connect(lambda message: self.statusBar().showMessage(message, 8000))
        self.page.dirty_changed.connect(lambda _dirty: self._update_title())
        self.page.saved.connect(self._update_title)
        menu = self.menuBar().addMenu("&File")
        menu.addAction("Save", QKeySequence.Save, self.page.save)
        menu.addAction("Save As…", QKeySequence.SaveAs, self.page.export)
        view = self.menuBar().addMenu("&View")
        view.addAction("Zoom In", QKeySequence.ZoomIn, self.page.view.zoom_in)
        view.addAction("Zoom Out", QKeySequence.ZoomOut, self.page.view.zoom_out)
        view.addAction("Zoom to Fit", "Ctrl+0", self.page.view.zoom_to_fit)
        self.resize(1440, 900)
        self._update_title()

    # Convenience accessors used by scripts.
    @property
    def net(self) -> CPNet:
        return self.document.net

    @property
    def view(self):
        return self.page.view

    @property
    def scene(self):
        return self.page.scene

    def _update_title(self) -> None:
        name = Path(self.document.path).name if self.document.path else self.net.name
        self.setWindowTitle(f"{name}{' •' if self.document.dirty else ''} — {APPLICATION_NAME}")


def main(argv: list[str] | None = None) -> int:
    """``openprocess-cpn [model.cpn]``: open Studio with a CPN model (or a new one)."""
    arguments = list(sys.argv if argv is None else argv)
    from .studio.app import main as studio_main
    if len(arguments) > 1:
        return studio_main(arguments)
    return studio_main(arguments + ["--new-cpn"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
