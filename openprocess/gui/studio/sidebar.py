"""The sidebar's tree: open documents, files not open yet, and subfolders,
and the delegate that draws a row's name and its ✕.

Rows carry what they stand for in their item data:

* ``Qt.UserRole`` -- the id of an open document;
* :data:`FILE_ROLE` -- the path of a file in the folder that is not open yet;
* :data:`FOLDER_ROLE` -- the path of a subfolder;
* :data:`FOLDABLE_ROLE` -- a heading that folds open and shut (a click toggles it);
* :data:`GROUP_ROLE` -- a subfolder's caption: the rows under it are the files
  in it.  It folds too (a click, or its chevron), and the window remembers
  which captions are folded in the folder's settings.

The tree also handles dragging: rows can be dragged onto a subfolder (to move
the files on disk) or out of the window (to Finder, say), and files dragged
in from outside land in the subfolder they are dropped on.  What a drop means
is the window's business: the tree only reports it (:attr:`paths_dropped`).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QMimeData, QPointF, QRect, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QStyle, QStyledItemDelegate, QTreeWidget,
                               QTreeWidgetItem)

from . import style

#: Sidebar item data: the path of a file in the folder that is not open yet.
FILE_ROLE = Qt.UserRole + 1
#: Sidebar item data: the path of a subfolder.
FOLDER_ROLE = Qt.UserRole + 2
#: Sidebar item data: a heading that folds (the LOGS section in Mine): it gets a chevron.
FOLDABLE_ROLE = Qt.UserRole + 5
#: Sidebar item data: a subfolder's caption, which groups the files in it (folds, with a chevron).
GROUP_ROLE = Qt.UserRole + 6


class SidebarTree(QTreeWidget):
    """A ``QTreeWidget`` whose rows can be dragged to subfolders, Finder or back in."""

    #: Files were dropped: their paths, the folder they were dropped on (None:
    #: not on a folder), and whether they were dragged from this sidebar.
    paths_dropped = Signal(list, object, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        #: Set by the window: the file or folder a row stands for (None: nothing on disk).
        self.path_for: Callable[[QTreeWidgetItem], str | None] = lambda item: None
        #: Set by the window: the folder a drop on this row (None: the empty
        #: space below the rows) goes into, or None when drops do not go into folders.
        self.drop_folder: Callable[[QTreeWidgetItem | None], str | None] = lambda item: None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self._target: QTreeWidgetItem | None = None

    # -- the ▸ / ▾ of a subfolder ----------------------------------------------------------
    def drawBranches(self, painter: QPainter, rect, index) -> None:  # noqa: N802
        """A quiet chevron, and nothing else: Qt's own branch area also painted
        the selected row's highlight in the indentation, so that is covered up."""
        painter.fillRect(rect, QColor(style.tokens().sidebar))
        item = self.itemFromIndex(index)
        if item is None or item.childCount() == 0 \
                or not (item.data(0, FOLDER_ROLE) or item.data(0, FOLDABLE_ROLE)):
            return                      # only a folder's caption or a foldable heading gets one
        size = 3.5
        centre = QPointF(rect.right() - self.indentation() / 2 + 2, rect.center().y() + 1)
        path = QPainterPath()
        if self.isExpanded(index):
            path.moveTo(centre.x() - size, centre.y() - size / 2)
            path.lineTo(centre.x(), centre.y() + size / 2)
            path.lineTo(centre.x() + size, centre.y() - size / 2)
        else:
            path.moveTo(centre.x() - size / 2, centre.y() - size)
            path.lineTo(centre.x() + size / 2, centre.y())
            path.lineTo(centre.x() - size / 2, centre.y() + size)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(style.tokens().text_secondary), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.restore()

    # -- dragging out ------------------------------------------------------------------
    def startDrag(self, _actions) -> None:  # noqa: N802
        """Drag the selected rows' files.

        Qt's own implementation removes the rows after a move; here nothing
        changes until the window has moved the files, and the folder watcher
        or the window then updates the rows.  Copy is the default, so a drag
        to Finder copies the file (⌘ in Finder makes it a move).
        """
        paths = [self.path_for(item) for item in self.selectedItems()]
        paths = [path for path in paths if path]
        if not paths:
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    # -- dropping in -------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        folder = self.drop_folder(item)
        self._highlight(self._row_of_folder(item, folder))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._highlight(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._highlight(None)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            event.ignore()
            return
        folder = self.drop_folder(self.itemAt(event.position().toPoint()))
        internal = event.source() is self
        if internal:
            event.setDropAction(Qt.MoveAction)
        event.accept()
        self.paths_dropped.emit(paths, folder, internal)

    def _row_of_folder(self, item, folder: str | None):
        """The row to highlight for a drop into ``folder``: the folder's own row."""
        while item is not None and folder is not None:
            if item.data(0, FOLDER_ROLE) == folder:
                return item
            item = item.parent()
        return None

    def _highlight(self, item: QTreeWidgetItem | None) -> None:
        if item is self._target:
            return
        if self._target is not None:
            try:
                self._target.setData(0, Qt.BackgroundRole, None)
            except RuntimeError:            # the row was rebuilt meanwhile
                pass
        self._target = item
        if item is not None:
            item.setBackground(0, style.qc(style.tokens().accent, 0.22))


# ---------------------------------------------------------------------------
# Sidebar: a remove button that appears on hover
# ---------------------------------------------------------------------------
class SidebarDelegate(QStyledItemDelegate):
    """Draws a small ✕ on the hovered (or selected) document row.

    Clicking it asks the window to remove that document from the workspace --
    the same gesture as closing a tab in Safari or a document in Xcode's
    sidebar.  Section headers ("EVENT LOGS", "MODELS") never get one.
    """

    remove_clicked = Signal(int)        # document id
    BUTTON = 18

    def _button_rect(self, row: QRect) -> QRect:
        size = self.BUTTON
        return QRect(row.right() - size - 8, row.center().y() - size // 2 + 1, size, size)

    @staticmethod
    def _shows_button(option, index) -> bool:
        return index.data(Qt.UserRole) is not None and bool(
            option.state & (QStyle.State_MouseOver | QStyle.State_Selected))

    def initStyleOption(self, option, index) -> None:  # noqa: N802
        """A name uses the whole row; only a row that shows its ✕ gives up the
        room for it.  The name is shortened in the middle, so two files that
        start alike ("order handling", "order handling (sound)") still read
        apart and the • of unsaved changes at the end is never cut off."""
        super().initStyleOption(option, index)
        if not self._shows_button(option, index):
            return
        widget = option.widget
        qstyle = widget.style() if widget is not None else QApplication.style()
        text = qstyle.subElementRect(QStyle.SE_ItemViewItemText, option, widget)
        room = self._button_rect(option.rect).left() - text.left() - 8
        if room > 0:
            option.text = option.fontMetrics.elidedText(option.text, Qt.ElideMiddle, room)

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if not self._shows_button(option, index):
            return
        selected = bool(option.state & QStyle.State_Selected)
        t = style.tokens()
        rect = QRectF(self._button_rect(option.rect))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        ink = QColor(t.accent_text if selected else t.text_secondary)
        backdrop = QColor(ink)
        backdrop.setAlphaF(0.18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(backdrop)
        painter.drawEllipse(rect)
        pen = QPen(ink, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        inset = rect.adjusted(5.5, 5.5, -5.5, -5.5)
        painter.drawLine(inset.topLeft(), inset.bottomRight())
        painter.drawLine(inset.topRight(), inset.bottomLeft())
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if index.data(Qt.UserRole) is not None and event.type() in (
                QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            if self._button_rect(option.rect).contains(event.position().toPoint()):
                if event.type() == QEvent.MouseButtonRelease:
                    self.remove_clicked.emit(int(index.data(Qt.UserRole)))
                return True     # swallow the press too, so the row is not selected
        return super().editorEvent(event, model, option, index)
