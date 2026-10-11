"""Markdown with maths, as the exercises and the definitions show it.

``markdown_html`` turns GitHub-flavoured Markdown (tables, lists, emphasis)
with maths between ``$…$`` or ``$$…$$`` (typeset by :mod:`.mathtext`) into
Qt rich text; :class:`MarkdownLabel` shows it in a label that grows with its
text, so a worksheet reads as one page.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QTextBrowser

from .mathtext import MATH_FONT, render


# ---------------------------------------------------------------------------
# Markdown with maths
# ---------------------------------------------------------------------------
_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
# A formula may wrap over a line break inside its paragraph (a worksheet's
# long log), never over a blank line: that would pair the `$` of two formulas.
_INLINE = re.compile(r"(?<![\\$])\$((?:[^$\n]|\n(?!\n))+?)\$")


def _maths(source: str, size: int) -> str:
    try:
        body = render(source.strip())
    except ValueError:                 # not in the supported subset: show it as written
        body = escape(source.strip())
    return f"<span style='font-family: {MATH_FONT}; font-size: {size}px'>{body}</span>"


def markdown_html(text: str) -> str:
    """Markdown (GitHub flavour: tables, lists, emphasis) with ``$…$`` maths, as HTML."""
    formulas: list[str] = []

    def stash(match, size: int) -> str:
        formulas.append(_maths(match.group(1), size))
        return f"MATHXPLACEHOLDER{len(formulas) - 1}X"

    text = _DISPLAY.sub(lambda m: "\n\n" + stash(m, 17) + "\n\n", text)
    text = _INLINE.sub(lambda m: stash(m, 15), text)
    document = QTextDocument()
    document.setMarkdown(text, QTextDocument.MarkdownDialectGitHub)
    html = document.toHtml()
    return re.sub(r"MATHXPLACEHOLDER(\d+)X", lambda m: formulas[int(m.group(1))], html)


def plain_browser() -> QTextBrowser:
    browser = QTextBrowser()
    browser.setObjectName("plainBrowser")        # borderless inside its card
    browser.setOpenExternalLinks(True)
    browser.setFrameShape(QFrame.NoFrame)
    return browser


def show_document(browser: QTextBrowser, path: Path) -> None:
    """A Markdown (or plain text) file in ``browser``, images relative to its folder."""
    text = path.read_text(encoding="utf-8", errors="replace")
    browser.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
    browser.setSearchPaths([str(path.parent)])
    if path.suffix.lower() == ".md":
        browser.setHtml(markdown_html(text))
    else:
        browser.setPlainText(text)



#: Pictures wider than this are shown scaled down to it (the worksheet's width).
IMAGE_WIDTH = 640


def _absolute_images(html: str, folder: Path) -> str:
    """``<img src="figure.png">`` relative to the question's folder, scaled
    down when wider than the worksheet."""
    from PySide6.QtGui import QImageReader

    def absolute(match) -> str:
        source = match.group(2)
        if re.match(r"^[a-z]+:", source):
            return match.group(0)
        path = folder / source
        width = QImageReader(str(path)).size().width()
        scaled = f' width="{IMAGE_WIDTH}"' if width > IMAGE_WIDTH else ""
        return f"{match.group(1)}{QUrl.fromLocalFile(str(path)).toString()}{match.group(3)}{scaled}"
    return re.sub(r'(<img[^>]*src=")([^"]+)(")', absolute, html)


class MarkdownLabel(QLabel):
    """Markdown in a label: wraps, grows with its text, links open in the browser."""

    def __init__(self, text: str = "", folder: Path | None = None, size: int | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("markdown")
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.folder = folder
        if size:
            self.setStyleSheet(f"font-size: {size}px;")
        self.set_markdown(text)

    def set_markdown(self, text: str) -> None:
        self.source = text
        html = markdown_html(text)
        # The label's own font (the document pins one of its own), and room
        # between paragraphs so a worksheet reads easily.
        html = re.sub(r"<body style=\"[^\"]*\">", "<body>", html)
        html = re.sub(r"<p style=\" margin-top:\d+px; margin-bottom:\d+px;",
                      "<p style=\" margin-top:4px; margin-bottom:10px;", html)
        html = re.sub(r"<li style=\" margin-top:\d+px; margin-bottom:\d+px;",
                      "<li style=\" margin-top:2px; margin-bottom:3px;", html)
        if self.folder is not None:
            html = _absolute_images(html, self.folder)
        self.setText(html)

    def plain_text(self) -> str:
        document = QTextDocument()
        document.setHtml(self.text())
        return document.toPlainText()
