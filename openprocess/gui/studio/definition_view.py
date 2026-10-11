"""Definitions on screen: hover tooltips, click pop-ups and the reference dialog.

Every property an Analysis tab reports (sound, live, free-choice, ...) is
defined in :mod:`openprocess.mining.definitions`.  Here that becomes:

* a **tooltip** when the mouse rests on the property: the definition's
  formulas and, underneath, the same formulas **filled in for this net**
  (its place names, and the marking and firing sequence that break the
  property when it fails -- see :mod:`.instances`);
* a **pop-up** when the property is clicked, which also has the notes, the
  definitions it builds on (click to follow) and the source;
* the **Definitions** dialog (Help ▸ Definitions, or *All definitions…*):
  the whole reference, the same text as ``docs/definitions.md``.

Formulas are typeset by :mod:`.mathtext`.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QUrl
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from ...mining.definitions import BY_KEY, DEFINITIONS, SECTIONS, Definition, lookup
from . import style
from .mathtext import display, render_text

WIDTH = 480


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def definition_html(definition: Definition, instance: list[str] | None = None,
                    compact: bool = False) -> str:
    """The definition (and its instance for this net) as rich text.

    ``compact`` is the tooltip: formulas and instance only, with a hint that
    a click shows more.
    """
    t = style.tokens()
    parts = [f"<div style='font-size: 15px; font-weight: 600'>{escape(definition.name)}"
             "</div>",
             f"<div style='color: {t.text_secondary}; margin-top: 2px'>"
             f"{render_text(definition.summary)}</div>"]
    parts += [display(formula) for formula in definition.formulas]
    if instance:
        parts.append(f"<div style='margin-top: 8px; font-weight: 600; color: {t.accent}'>"
                     "For this net</div>")
        parts += [display(line) for line in instance]
    if compact:
        parts.append(f"<div style='color: {t.text_muted}; font-size: 11px; margin-top: 6px'>"
                     "Click for notes and related definitions.</div>")
    else:
        for note in definition.notes:
            parts.append(f"<p style='color: {t.text_secondary}'>{render_text(note)}</p>")
        if definition.uses:
            links = ", ".join(f"<a href='def:{key}'>{escape(BY_KEY[key].name)}</a>"
                              for key in definition.uses)
            parts.append(f"<p>Builds on: {links}.</p>")
        if definition.source:
            parts.append(f"<p style='color: {t.text_muted}; font-size: 11px'>Source: "
                         f"{render_text(definition.source)}.</p>")
    body = "".join(parts)
    return (f"<table width='{WIDTH - 24}' cellpadding='0' cellspacing='0'><tr><td>"
            f"{body}</td></tr></table>")


def reference_html() -> str:
    """Every definition, grouped by section, with anchors for links."""
    t = style.tokens()
    parts = ["<h2>Definitions</h2>",
             f"<p style='color: {t.text_secondary}'>Every property the analyses report. "
             "Throughout, " + render_text("$N = (P, T, F)$") + " is a Petri net and, for "
             "WF-nets, " + render_text("$i$ and $o$") + " are its start and end places. "
             "The same text is in <code>docs/definitions.md</code>.</p>"]
    for section, title, intro in SECTIONS:
        parts.append(f"<h3 style='margin-top: 18px'>{escape(title)}</h3>"
                     f"<p style='color: {t.text_secondary}'>{render_text(intro)}</p>")
        for definition in DEFINITIONS:
            if definition.section == section:
                parts.append(f"<a name='{definition.key}'></a>"
                             f"<div style='margin: 10px 0 14px 0'>"
                             f"{definition_html(definition)}</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
class _Browser(QTextBrowser):
    """Rich text with ``def:key`` links handled by the owner."""

    def __init__(self, on_link) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.NoFrame)
        self.document().setDefaultStyleSheet(
            f"a {{ color: {style.tokens().accent}; text-decoration: none; }}")
        self.anchorClicked.connect(lambda url: on_link(url.toString()))


class DefinitionPopup(QFrame):
    """The pop-up a click on a property opens (closes on any click outside)."""

    def __init__(self, definition: Definition, instance: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup)
        self.setObjectName("definitionPopup")
        t = style.tokens()
        self.setStyleSheet(f"#definitionPopup {{ background: {t.surface}; "
                           f"border: 1px solid {t.border}; border-radius: 10px; }}"
                           f"QTextBrowser {{ background: transparent; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.browser = _Browser(self._link)
        self.browser.setFixedWidth(WIDTH)
        layout.addWidget(self.browser)
        row = QHBoxLayout()
        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self._back)
        everything = QPushButton("All definitions…")
        everything.clicked.connect(lambda: self._open_reference(self.current.key))
        row.addWidget(self.back_button)
        row.addStretch(1)
        row.addWidget(everything)
        layout.addLayout(row)
        self.history: list[tuple[Definition, list[str] | None]] = []
        self.current = definition
        self._show(definition, instance)

    def _show(self, definition: Definition, instance) -> None:
        self.current, self.instance = definition, instance
        self.browser.setHtml(definition_html(definition, instance))
        self.back_button.setVisible(bool(self.history))
        document = self.browser.document()
        # The frame (the stylesheet's padding, known once the widget is
        # polished) takes its share of the width and the height; counted
        # short, the card scrolled by a few pixels.  The text wraps, so there
        # is never anything to scroll sideways.
        self.browser.ensurePolished()
        frame = 2 * self.browser.frameWidth()
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        document.setTextWidth(WIDTH - frame)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        limit = int(screen.availableGeometry().height() * 0.7) if screen else 560
        self.browser.setFixedHeight(min(int(document.size().height()) + frame, limit))
        self.adjustSize()

    def _link(self, target: str) -> None:
        if target.startswith("def:") and target[4:] in BY_KEY:
            self.history.append((self.current, self.instance))
            self._show(BY_KEY[target[4:]], None)

    def _back(self) -> None:
        if self.history:
            self._show(*self.history.pop())

    def _open_reference(self, key: str) -> None:
        self.close()
        show_reference(key, self.parentWidget())

    def show_near(self, point: QPoint) -> None:
        """Open below-right of ``point``, kept on the screen."""
        self.adjustSize()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(point.x() + 8, area.right() - self.width() - 8)
        y = point.y() + 12
        if y + self.height() > area.bottom():
            y = max(area.top() + 8, point.y() - self.height() - 12)
        self.move(max(area.left() + 8, x), y)
        self.show()


class DefinitionsDialog(QDialog):
    """The complete reference (Help ▸ Definitions)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Definitions")
        self.resize(640, 760)
        layout = QVBoxLayout(self)
        self.browser = _Browser(self._link)
        self.browser.setHtml(reference_html())
        layout.addWidget(self.browser)

    def _link(self, target: str) -> None:
        if target.startswith("def:"):
            self.browser.scrollToAnchor(target[4:])

    def show_key(self, key: str | None) -> None:
        if key:
            self.browser.scrollToAnchor(key)


_dialog: DefinitionsDialog | None = None


def show_reference(key: str | None = None, parent: QWidget | None = None) -> DefinitionsDialog:
    """Open (or raise) the Definitions dialog, scrolled to ``key``."""
    global _dialog
    if _dialog is None:
        _dialog = DefinitionsDialog(parent.window() if parent is not None else None)
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    _dialog.show_key(key)
    return _dialog


class _ClickFilter(QObject):
    """Opens the pop-up when the widget it watches is clicked."""

    def __init__(self, widget: QWidget, definition: Definition, instance) -> None:
        super().__init__(widget)
        self.widget, self.definition, self.instance = widget, definition, instance

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            popup = DefinitionPopup(self.definition, self.instance, self.widget)
            popup.show_near(QCursor.pos())
            self.widget.last_popup = popup          # for tests
            return True
        return False


def attach_definition(widget: QWidget, key: str | None, instance: list[str] | None = None,
                      targets: list[QWidget] | None = None) -> Definition | None:
    """Give ``widget`` the definition ``key``: tooltip on hover, pop-up on click.

    ``targets`` are the child widgets that react (default: the widget
    itself); selectable text labels are best left out, so their text can
    still be selected.
    """
    definition = lookup(key)
    if definition is None:
        return None
    tooltip = definition_html(definition, instance, compact=True)
    for target in targets or [widget]:
        target.setToolTip(tooltip)
        target.setCursor(Qt.PointingHandCursor)
        target.installEventFilter(_ClickFilter(widget, definition, instance))
    widget.definition = definition
    return definition


# ---------------------------------------------------------------------------
# Guides: References, Writing Exercise Packs
# ---------------------------------------------------------------------------
GUIDES = {"references": "References", "exercise-packs": "Writing Exercise Packs"}
_guides: dict[str, QDialog] = {}


def guide_markdown(name: str) -> str:
    """The text of a guide (Markdown)."""
    if name == "references":
        from ... import references
        text = references.markdown()
        return text.split("-->", 1)[-1].lstrip()
    from pathlib import Path
    from ...learn import __file__ as learn
    return (Path(learn).parent / f"{name}.md").read_text(encoding="utf-8")


def show_guide(name: str, parent: QWidget | None = None) -> QDialog:
    """Open (or raise) a guide in a window of its own."""
    from .markdown_view import markdown_html
    dialog = _guides.get(name)
    if dialog is None:
        dialog = QDialog(parent.window() if parent is not None else None)
        dialog.setWindowTitle(GUIDES.get(name, name))
        dialog.resize(760, 760)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        browser = QTextBrowser()
        browser.setObjectName("guideBrowser")
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.NoFrame)
        browser.setHtml(markdown_html(guide_markdown(name)))
        layout.addWidget(browser)
        dialog.browser = browser
        _guides[name] = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
