"""OpenProcess Studio: the main window, with its three spaces.

Layout
------
::

    [ Mine | Model | Learn ]                           ← the switcher
    +------------+-------------------------------------------------+
    | FOLDER     |  Page header (title, subtitle, actions)          |
    | Week 2     |  [Result | How | Code | Settings]                |
    | ANALYSES   |                                                  |
    |  ⧉ Discover|                page content                      |
    | LOGS · 3  ▸|                                                  |
    |            |                                                  |
    | + New…     |                                                  |
    +------------+-------------------------------------------------+

One window, one folder, three spaces (:mod:`.spaces`).  **Mine** lists the
folder's analyses (workflows) with its event logs folded under them;
**Model** lists its Petri nets and coloured nets; **Learn** is the exercise
view, which takes the whole window.  Selecting a document shows its page;
opening one switches to its space.  Discovering a model from a log and
editing a copy of it moves the window to Model, and exporting a CPN
simulation as a log moves it to Mine, so a whole analysis lives in one
window without the two kinds of work sharing a list.

With a folder open (File ▸ Open Folder…, e.g. "Week 2"; internally a
:class:`.workspace.Workspace`), the folder and the sidebar always match:

* each space's sidebar lists its kinds of file in the folder, open or not
  (lighter), under their subfolders;
* folder → app: files added, renamed or deleted in Finder show up by
  themselves, an open file changed on disk is reloaded, and one deleted on
  disk is marked as missing;
* app → folder: new nets and logs are files in the folder from the start,
  edits are saved as you go (autosave), and files can be moved between
  subfolders, renamed or moved to the Bin from the sidebar;
* files opened from elsewhere can be copied or moved into the folder.

A subfolder with a ``question`` file is an *exercise*.  Learn (the switcher,
or Learn ▸ Open Exercise Pack…) swaps the window for OpenProcess Learn (see
:mod:`openprocess.gui.learn.mode`), a worksheet with answer boxes beside the
net, log or transition system the exercise gives.  *Exit* comes back to the
space you left, exactly as it was.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QFile, QFileSystemWatcher, QObject, QPoint, QRect, QRectF, QSettings, QSize, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QToolBar, QToolButton, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from ...mining.csv_import import ColumnMapping, guess_mapping, read_csv, sniff
from ...mining.log import EventLog, parse_simple_log
from ...mining.pnml import read_pnml, write_pnml
from ...mining.xes import read_xes, write_xes
from .. import theme
from . import motion, style
from ..canvas import NetScene, NetView
from .compare_page import ComparePage
from .connections import ConnectionsPage
from .cpn_page import CpnPage
from .petri_page import PetriNetPage
from ..flow.page import WorkflowPage
from ..flow.templates import TEMPLATES, first_log
from .documents import (WorkflowDocument, 
    ComparisonDocument, CpnDocument, LogDocument, ModelDocument, TransitionSystemDocument,
)
from .file_dialogs import IMPORT_CHOICES, ImportDialog, SettingsDialog, ask_about_clash
from .graph_view import GraphView, ZoomControls
from .log_editor import EXAMPLES, NotationDialog  # noqa: F401 - NotationDialog: older imports
from .log_page import LogPage
from .model_page import ModelPage
from .sidebar import FILE_ROLE, FOLDABLE_ROLE, FOLDER_ROLE, GROUP_ROLE, SidebarTree
from .spaces import FILE_KINDS, LABELS, LEARN, MAIN_KINDS, MINE, MODEL, SPACES, is_main, space_of
from .widgets import (
    Card, ElidedLabel, NoticeBar, SegmentedControl, button, dialog_folder, hbox, label,
    round_menus, scroll, set_dialog_folder, shortcut_text, vbox,
)
from .workspace import (
    FORBIDDEN_CHARACTERS, Workspace, WorkspaceFolder, display_name,
    exercise_files, file_kind, file_stem, file_suffix,
    made_by_openprocess, safe_file_name, unique_path,
)
from .workers import run_in_background

APPLICATION_NAME = "OpenProcess Studio"

#: Sidebar item data: the iCloud placeholder of a file that is only in iCloud.
CLOUD_ROLE = Qt.UserRole + 3
#: Sidebar item data: the folder is an exercise (it has a question file).
EXERCISE_ROLE = Qt.UserRole + 4

#: How long after the last edit a net in the folder is saved (milliseconds).
AUTOSAVE_DELAY = 1000

#: Name of the Bin on this system (macOS says Bin in British English, as here).
BIN = "Recycle Bin" if sys.platform == "win32" else "Bin"
def _run_pip(command: list[str], on_line=None) -> tuple[int, str]:
    """Run pip (``command``), handing each line to ``on_line`` as it comes;
    gives back the exit code and the whole output."""
    lines: list[str] = []
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               errors="replace", bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line)
        if on_line is not None:
            on_line(line)
    return process.wait(), "".join(lines)


class _InstallLines(QObject):
    """pip's lines, from the thread that reads them to the window."""

    line = Signal(str, str)


#: Where an imported experiment's folder goes: asked, next to the zip by default
#: (a test puts a stand-in here).
EXPERIMENT_FOLDER_CHOOSER = (lambda parent, title, default:
                             QFileDialog.getExistingDirectory(parent, title, default))


#: How Install… runs pip (a test puts a stand-in here).
PIP_RUNNER = _run_pip

#: The Model palette's tools, in the order of a net page's ``TOOLS`` (the τ
#: tool is a plain Petri net's; the palette hides it for a coloured net).
#: Each button shows the same painted icon as the page's own tool buttons.
PALETTE = [("select", "Select", "Select and move things; double-click a place or transition to rename it"),
           ("place", "Place", "Click on the canvas to add a place"),
           ("transition", "Transition", "Click on the canvas to add a transition"),
           ("silent", "τ", "Click on the canvas to add a silent (τ) transition: a black bar that moves "
            "tokens without an event in the trace"),
           ("arc", "Arc", "Drag from a place to a transition (or the other way) to connect them")]


def _empty_on_disk(folder: Path) -> bool:
    """Nothing in the folder but hidden files (a folder just made)."""
    try:
        return not any(not p.name.startswith(".") for p in folder.iterdir())
    except OSError:
        return False


def _file_suffix(path: Path) -> str:
    """The whole extension, so ``log.xes.gz`` gives ``.xes.gz`` (not just ``.gz``)."""
    return file_suffix(path.name)


def _file_stem(path: Path) -> str:
    """The file name without :func:`_file_suffix`: what a document opened from it is called."""
    return file_stem(path.name)


def _same_file(a: Path, b: Path) -> bool:
    """True when two paths name one file (e.g. a case-only change on macOS)."""
    try:
        return a.samefile(b)
    except OSError:
        return False


def _signature(path: str | Path) -> tuple[int, int] | None:
    """(modification time, size) of a file, or None when it is not there."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def move_to_trash(path: str | Path) -> bool:
    """Move a file or folder to the Bin (recoverable).  Tests replace this."""
    return bool(QFile.moveToTrash(str(path)))


def reveal_label() -> str:
    return {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(
        sys.platform, "Open Containing Folder")



def report_unexpected_errors() -> None:
    """An error nobody caught shows a dialog with the traceback instead of
    vanishing into a terminal the app may not have (in the bundle, it has none)."""
    import sys
    import traceback
    previous = sys.excepthook

    def hook(kind, value, trace) -> None:
        previous(kind, value, trace)
        text = "".join(traceback.format_exception(kind, value, trace))
        try:
            box = QMessageBox(QMessageBox.Critical, "Something went wrong",
                              f"{kind.__name__}: {value}")
            box.setInformativeText("OpenProcess keeps running. If it happens again, please report "
                                   "it with the details below at https://github.com/s4mstruthers/openprocess/issues.")
            box.setDetailedText(text)
            box.exec()
        except Exception:  # noqa: BLE001 - never fail inside the error reporter
            pass
    sys.excepthook = hook


def app_icon() -> QIcon:
    """The OpenProcess logo (Dock, window and About box)."""
    return QIcon(str(Path(__file__).resolve().parents[1] / "resources" / "openprocess-icon.png"))


def _icon(kind: str) -> QIcon:
    """Tiny painted icons, so the app ships no image files.

    Drawn twice: in colour, and in the selected row's text colour, which Qt
    uses for the selected row (a blue icon on the blue highlight vanished).
    """
    icon = QIcon(_icon_pixmap(kind, None))
    icon.addPixmap(_icon_pixmap(kind, QColor(style.tokens().accent_text)), QIcon.Selected)
    return icon


def _faded_icon(kind: str) -> QIcon:
    """The kind's icon at reduced opacity: a workspace file that is not open yet."""
    source = _icon_pixmap(kind, None)
    faded = QPixmap(source.size())
    faded.fill(Qt.transparent)
    painter = QPainter(faded)
    painter.setOpacity(0.45)
    painter.drawPixmap(0, 0, source)
    painter.end()
    icon = QIcon(faded)
    icon.addPixmap(_icon_pixmap(kind, QColor(style.tokens().accent_text)), QIcon.Selected)
    return icon


def _icon_pixmap(kind: str, ink: QColor | None) -> QPixmap:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = ink if ink is not None else QColor(
        {"log": style.categorical(0), "cpn": style.categorical(2)}.get(kind, style.categorical(6)))
    pen = QPen(colour, 3)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    if kind == "folder":
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink if ink is not None else QColor(style.tokens().accent))
        tab = QRectF(3, 7, 11, 6)
        painter.drawRoundedRect(tab, 2, 2)
        painter.drawRoundedRect(QRectF(3, 10, 26, 16), 2.5, 2.5)
    elif kind == "compare":
        painter.setPen(Qt.NoPen)
        for index, (x, height) in enumerate(((5, 12), (12, 20), (19, 8), (26, 16))):
            painter.setBrush(ink if ink is not None else QColor(style.categorical(index % 2)))
            painter.drawRoundedRect(QRectF(x - 2, 27 - height, 5, height), 1.5, 1.5)
    elif kind == "cpn":
        painter.drawEllipse(QRectF(3, 9, 13, 13))
        painter.drawRoundedRect(QRectF(19, 8, 10, 15), 2, 2)
        painter.setBrush(colour)
        painter.drawEllipse(QRectF(8, 14, 3, 3))
    elif kind == "log":
        for y in (9, 16, 23):
            painter.drawLine(7, y, 25, y)
        painter.setBrush(colour)
        painter.drawEllipse(4, 7, 1, 1)
    elif kind == "ts":
        # Two states and an arrow between them.
        painter.drawEllipse(QRectF(3, 11, 9, 9))
        painter.drawEllipse(QRectF(20, 11, 9, 9))
        painter.drawLine(13, 15, 18, 15)
        painter.drawLine(15, 12, 18, 15)
        painter.drawLine(15, 18, 18, 15)
    elif kind == "workflow":
        # Two boxes joined by a wire.
        painter.drawRoundedRect(QRectF(3, 6, 11, 9), 2, 2)
        painter.drawRoundedRect(QRectF(18, 17, 11, 9), 2, 2)
        painter.drawLine(14, 10, 18, 21)
    elif kind == "exercise":
        # A sheet with a question mark.
        painter.drawRoundedRect(QRectF(6, 4, 20, 24), 3, 3)
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(17)
        painter.setFont(font)
        painter.drawText(QRectF(6, 4, 20, 24), Qt.AlignCenter, "?")
    else:
        painter.drawEllipse(QRectF(4, 10, 11, 11))
        painter.drawRect(QRectF(19, 9, 9, 13))
    painter.end()
    return pixmap


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

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if index.data(Qt.UserRole) is None:
            return
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        if not (hovered or selected):
            return
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


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class CompareDialog(QDialog):
    """Pick the logs to compare (at least two)."""

    def __init__(self, logs: list, preselected: list, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        self.setWindowTitle("Compare logs")
        self.logs = logs
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label("Choose two or more event logs to show side by side.",
                               "muted", wrap=True))
        self.list = QListWidget()
        chosen = {d.id for d in preselected}
        for log in logs:
            item = QListWidgetItem(log.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if log.id in chosen or len(logs) == 2
                               else Qt.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _item: self._validate())
        layout.addWidget(self.list)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Compare")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def chosen(self) -> list:
        return [log for index, log in enumerate(self.logs)
                if self.list.item(index).checkState() == Qt.Checked]

    def _validate(self) -> None:
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(len(self.chosen()) >= 2)


class CsvDialog(QDialog):
    """Confirm which CSV columns hold case id, activity, timestamp, ..."""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Import {Path(path).name}")
        _, header = sniff(path)
        guess = guess_mapping(header) or ColumnMapping(header[0] if header else "",
                                                       header[1] if len(header) > 1 else "")
        self.boxes: dict[str, QComboBox] = {}
        form = QFormLayout()
        for role, title, optional in (("case", "Case id", False), ("activity", "Activity", False),
                                      ("timestamp", "Timestamp", True),
                                      ("resource", "Resource", True),
                                      ("lifecycle", "Lifecycle", True)):
            box = QComboBox()
            if optional:
                box.addItem("— none —", None)
            for column in header:
                box.addItem(column, column)
            current = getattr(guess, role)
            if current is not None:
                box.setCurrentIndex(box.findData(current))
            self.boxes[role] = box
            form.addRow(title, box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Choose the columns that identify cases and activities. "
                               "Other columns become event attributes.", "muted", wrap=True))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def mapping(self) -> ColumnMapping:
        return ColumnMapping(**{role: box.currentData() for role, box in self.boxes.items()})


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class StudioWindow(QMainWindow):
    """The Studio window.

    ``persist=True`` (what the real app uses) remembers the files that were
    open and reopens them on the next launch, and keeps an *Open Recent*
    list.  Tests pass ``persist=False`` so they never touch your settings.
    """

    RECENT_LIMIT = 12

    def __init__(self, persist: bool = False) -> None:
        super().__init__()
        round_menus(QApplication.instance())
        self.setWindowTitle(APPLICATION_NAME)
        self.setAcceptDrops(True)
        self.documents: list[LogDocument | ModelDocument] = []
        self.pages: dict[int, QWidget] = {}
        self.items: dict[int, QTreeWidgetItem] = {}
        self.holders: dict[int, QWidget] = {}
        self.persist = persist
        self.settings = QSettings("OpenProcess", "Studio")
        if not self.settings.allKeys():                 # first start since CPNpy: keep its settings
            old = QSettings("CPNpy", "Studio")
            for key in old.allKeys():
                self.settings.setValue(key, old.value(key))
        #: How each file-backed document was opened (CSV column mapping etc.),
        #: keyed by document id, so the session can reopen it the same way.
        self.open_options: dict[int, dict] = {}
        self._restoring = False
        #: The open folder (None: just loose files, as before).
        self.workspace: Workspace | None = None
        #: Its subfolders and files, as last listed (see _rescan_workspace).
        self._tree: WorkspaceFolder | None = None
        self._tree_signature: tuple = ()
        #: The space shown, Mine or Model (Learn takes the whole window: see in_learn).
        self.space = MINE
        #: Each space's current document (its id), so switching back finds it again.
        self._space_current: dict[str, int | None] = {MINE: None, MODEL: None}
        #: Mine's LOGS section (the folder's logs) is unfolded; remembered per folder.
        self._material_expanded = False
        #: The subfolders whose captions are folded (relative paths); remembered per folder.
        self._collapsed_folders: set[str] = set()
        #: The space that was shown before Learn took the window.
        self._space_before_learn = MINE
        #: Sidebar rows by what they stand for (rebuilt by _rebuild_sidebar).
        self.placeholders: dict[str, QTreeWidgetItem] = {}       # files not open, by path key
        self.folder_items: dict[str, QTreeWidgetItem] = {}       # subfolder captions, by relative path
        self._folder_rows: list[QTreeWidgetItem] = []           # every caption (a path may have two)
        #: Reopening a folder: files still loading, and the one that was selected.
        self._restore_paths: dict[str, dict] = {}
        self._restore_selected: str | None = None
        #: When a click last opened a file of the folder (see _on_double_click).
        self._clicked_open_at = 0.0
        #: Files being opened by a click (shown with "…"), and iCloud downloads to open.
        self._opening: set[str] = set()
        self._pending_downloads: set[str] = set()
        # Files added, removed or renamed in the folder (in Finder, say) show
        # up by themselves; the timer folds a burst of changes into one rescan.
        self.watcher = QFileSystemWatcher(self)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        # macOS reports a change about half a second after it happens; this
        # only folds a burst of events (a whole folder copied in) into one.
        self._rescan_timer.setInterval(100)
        self._rescan_timer.timeout.connect(self._rescan_workspace)
        self.watcher.directoryChanged.connect(lambda _path: self._rescan_timer.start())
        # Open files are watched too: one changed on disk is reloaded.
        self._changed_files: set[str] = set()
        self._file_timer = QTimer(self)
        self._file_timer.setSingleShot(True)
        self._file_timer.setInterval(150)       # also: how long a file must stay unchanged
        self._file_timer.timeout.connect(self._check_changed_files)
        self.watcher.fileChanged.connect(self._file_changed)
        #: (mtime, size) of files the app wrote itself, so its own saves are not
        #: mistaken for changes made elsewhere; and of changes not settled yet.
        self._own_writes: dict[str, tuple] = {}
        self._unsettled: dict[str, tuple] = {}
        self._gone_checks: dict[str, int] = {}
        self._reload_attempts: dict[str, int] = {}
        #: Each open file's identity on disk, so a file moved or renamed in
        #: Finder is recognised in its new place (see _relocate).
        self._identities: dict[int, tuple] = {}
        #: Documents with a "changed on disk" question open: not autosaved meanwhile.
        self._conflicts: set[int] = set()
        # One Snap to grid setting for every editor, kept between launches.
        NetScene.snap_to_grid = self._setting("editor/snap_to_grid", False)
        #: Autosave: one timer per document, restarted on each edit.
        self.autosave_enabled = self._setting("files/autosave", True)
        self._autosave_timers: dict[int, QTimer] = {}
        #: Documents whose autosave failed (read-only folder, full disk): saved
        #: with ⌘S again, with the "edited" dot, until a save works.
        self._autosave_failed: set[int] = set()
        #: .cpn documents whose file OpenProcess wrote (only those are autosaved).
        self._openprocess_files: set[int] = set()
        #: Files the app created by itself this session (a new net's "Untitled 1.pnml").
        self._created: set[str] = set()
        #: The bytes of each net's file as it was opened (File ▸ Revert to Saved).
        self._originals: dict[int, bytes] = {}
        #: Each page's notice bar ("changed on disk", "missing").
        self.banners: dict[int, NoticeBar] = {}
        #: Counts folder switches (opening or closing one), so a log still
        #: loading from the folder that was left is not added to the new one.
        self._folder_switches = 0
        #: Software updates: a check is running; the installer to run on quitting.
        self._checking_updates = False
        self._installer: list[str] | None = None
        #: The tab you were last on, per kind of page.  Selecting another
        #: document opens the same tab, so e.g. flicking between logs in the
        #: dotted chart keeps showing dotted charts.
        self.remembered = {"log_tab": 0, "model_inspector": 0, "model_view": 0}
        saved = self._setting("workflows/layout", "")
        if saved:
            try:
                WorkflowPage.LAYOUT = {**WorkflowPage.LAYOUT, **json.loads(saved)}
            except (ValueError, TypeError):
                pass
        #: The boxes available to workflows: OpenProcess's, installed packages', and the folder's boxes/.
        self._library = None
        self._boxes_signature: tuple = ()
        # Any size down to this works: pages scroll when they do not fit.
        self.setMinimumSize(720, 480)
        self._fit_to_screen()

        root = QSplitter()
        root.setObjectName("studioRoot")
        root.setHandleWidth(1)
        root.addWidget(self._build_sidebar())
        # Hidden and shown only by toggle_sidebar, so its button and menu tick agree.
        root.setCollapsible(0, False)
        self.content = QStackedWidget()
        self.content.addWidget(scroll(self._build_welcome(), horizontal=True))
        #: The hub picture: what flows in and out, and the tools that plug in (index 1).
        self.connections_page = ConnectionsPage()
        self.connections_page.install_requested.connect(self.install_tool)
        self.content.addWidget(self.connections_page)
        # Above the pages: "a new version is out" (see check_automatically).
        from .updates import UpdateBar
        self.update_bar = UpdateBar()
        self.update_bar.whats_new.connect(lambda: self._show_update_dialog(
            self.update_bar.release))
        self.update_bar.install.connect(lambda: self._update_action(
            self.update_bar.release, "install"))
        self.update_bar.skip.connect(lambda: self._update_action(
            self.update_bar.release, "skip"))
        right = QWidget()
        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self._build_rail())
        middle.addWidget(self.content, 1)
        right.setLayout(vbox(self.update_bar, middle, spacing=0))
        right.layout().setStretch(1, 1)
        # Scratch notes floating over the pages (Learn has its own).
        from .notes_overlay import NotesOverlay
        self.notes = NotesOverlay(self.content)
        self.notes.edited.connect(self._save_notes)
        self.notes.open_changed.connect(self._notes_opened)
        #: The notes kept outside a folder (in the settings; only in memory in tests).
        self._loose_notes = self._setting("notes/text", "")
        self.notes.set_text(self._loose_notes)
        root.addWidget(right)
        root.setStretchFactor(1, 1)
        root.setSizes([220, 1260])
        # Learn takes the whole window (see open_exercise); the
        # folder's view waits underneath, as it was.
        from ..learn.mode import LearnMode
        self.learn_mode = LearnMode()
        self.learn_mode.exit_requested.connect(self.leave_learn)
        self.learn_mode.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.learn_mode.title_changed.connect(
            lambda title: self.in_learn and self.setWindowTitle(title))
        self.modes = QStackedWidget()
        self.modes.addWidget(root)
        self.modes.addWidget(self.learn_mode)
        self.setCentralWidget(self.modes)
        self._build_space_bar()
        self.statusBar().showMessage("Ready")
        # ✎ Notes in the status bar: it never covers the page (see toggle_notes).
        self.notes_button = QToolButton()
        self.notes_button.setObjectName("notesStatusButton")
        self.notes_button.setText("✎  Notes")
        self.notes_button.setToolTip(f"Scratch paper, kept with the folder "
                                     f"({shortcut_text('Ctrl+Alt+N')})")
        self.notes_button.setCheckable(True)
        self.notes_button.setCursor(Qt.PointingHandCursor)
        self.notes_button.setFocusPolicy(Qt.NoFocus)
        self.notes_button.clicked.connect(lambda: self.toggle_notes())
        self.statusBar().addPermanentWidget(self.notes_button)
        self.modes.currentChanged.connect(lambda _index: self._update_notes_button())
        self._build_menus()
        if not self._setting("window/sidebar", True):
            self.toggle_sidebar(False)
        self.set_notes_button(self._setting("notes/button", True))
        if self._setting("notes/open", False):
            self.notes.set_open(True)

    # -- the editor fits the space it gets -----------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_fit()

    def _schedule_fit(self) -> None:
        QTimer.singleShot(0, self._fit_page)

    def _fit_page(self) -> None:
        """Fold the editor's inspector away when the page would not fit beside the
        sidebar (it would scroll sideways and pin the canvas divider), and bring
        it back when there is room again."""
        page = self.current_page()
        panel = getattr(page, "inspector_panel", None)
        if panel is None or not hasattr(page, "_toggle_inspector"):
            return
        from PySide6.QtWidgets import QScrollArea
        area = page.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is None:
            return
        available = area.viewport().width()
        if panel.isVisible():
            if page.minimumSizeHint().width() > available:
                page._toggle_inspector()
                page._inspector_auto_hidden = True
                self.statusBar().showMessage("The inspector is folded away to fit the window: "
                                             "“⇤ Inspector” brings it back", 6000)
        elif getattr(page, "_inspector_auto_hidden", False):
            needed = (page.minimumSizeHint().width() + panel.minimumWidth()
                      + page.splitter.handleWidth())
            if needed <= available:
                page._toggle_inspector()
                page._inspector_auto_hidden = False

    def _fit_to_screen(self) -> None:
        """Start at a size that fits the screen we are on (at most 1440 × 900).

        A fixed size larger than a laptop's screen is what made the window run
        off the edge; macOS cannot shrink a window below its minimum size.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 800)
            return
        area = screen.availableGeometry()
        width = min(1440, int(area.width() * 0.94))
        height = min(900, int(area.height() * 0.94))
        self.resize(width, height)
        self.move(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)

    def _setting(self, key: str, default):
        """A setting of the app (the default in tests, which must not see yours)."""
        if not self.persist:
            return default
        return self.settings.value(key, default, type=type(default))

    def _set_setting(self, key: str, value) -> None:
        if self.persist:
            self.settings.setValue(key, value)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(360)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(8, 16, 8, 10)
        layout.setSpacing(8)
        layout.addWidget(self._build_sidebar_header())

        self.tree = SidebarTree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setRootIsDecorated(False)
        self.tree.setMouseTracking(True)                  # hover state for the ✕ button
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)   # ⌘/⇧-click
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.path_for = self._row_path
        self.tree.drop_folder = self._drop_folder
        self.tree.paths_dropped.connect(self._sidebar_drop)
        self.delegate = SidebarDelegate(self.tree)
        self.delegate.remove_clicked.connect(lambda doc_id: self.remove_documents([doc_id]))
        self.tree.setItemDelegate(self.delegate)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._sidebar_menu)
        # ⌫ / Delete removes the selection while the sidebar has focus.
        for key in (QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)):
            shortcut = QShortcut(key, self.tree)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(self.remove_selected)
        # One space at a time (see _fill_space_view).  Mine: documents with no
        # file on top, the analyses, then the logs folded away under them.
        # Model: the models.  Both: open files from elsewhere, comparisons.
        # Exercises are Learn's: they are not listed here.
        self.unsaved_section = self._section("UNSAVED")
        self.analyses_section = self._section("ANALYSES")
        self.models_section = self._section("MODELS")
        self.material_section = self._section("LOGS")
        self.material_section.setData(0, FOLDABLE_ROLE, True)
        self.material_section.setToolTip(0, "The folder's event logs and transition systems: "
                                            "what analyses start from. Click to fold or unfold.")
        self.elsewhere_section = self._section("OTHER FILES")
        self.elsewhere_section.setToolTip(0, "Open files that are not in the folder")
        self.compare_section = self._section("COMPARISONS")
        # Shown in an empty folder, so an empty sidebar is not a mystery.
        self.empty_hint = QTreeWidgetItem(self.tree, ["No analyses or logs yet"])
        self.empty_hint.setFlags(Qt.NoItemFlags)
        self.empty_hint.setToolTip(0, "Save a net or export a log into the folder, or add "
                                      "files to it in Finder: they appear here.")
        self.empty_hint.setHidden(True)
        # Shown when a huge folder was only partly listed.
        self.more_row = QTreeWidgetItem(["… more files not shown"])
        self.more_row.setFlags(Qt.NoItemFlags)
        self.more_row.setToolTip(0, "The sidebar lists up to 500 files, three subfolders deep. "
                                    "Open a smaller folder to see everything.")
        self.tree.currentItemChanged.connect(self._on_select)
        # A file that is not open yet opens with one click (or Return); the LOGS heading folds.
        self.tree.itemClicked.connect(lambda item, _column: self._sidebar_clicked(item))
        self.tree.itemActivated.connect(lambda item, _column: self._open_placeholder(item))
        # Double-click an open document to rename it (section headings have no id).
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemExpanded.connect(lambda item: self._folder_toggled(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._folder_toggled(item, False))
        layout.addWidget(self.tree, 1)

        # Model: the drawing palette, for the net page that is open (it replaces
        # the page's own tool buttons while the sidebar shows).
        self.palette = QWidget()
        self.palette.setObjectName("palette")
        grid = QGridLayout(self.palette)
        grid.setContentsMargins(6, 4, 6, 8)
        grid.setSpacing(4)
        grid.addWidget(label("PALETTE", "sectionLabel"), 0, 0, 1, 2)
        self.palette_group = QButtonGroup(self.palette)
        self.palette_group.setExclusive(True)
        from .tool_icons import tool_icon
        self.palette_tools: dict = {}                  # tool kind -> its button
        for index, (kind, text, tip) in enumerate(PALETTE):
            tool = button(text, kind="paletteTool", tooltip=tip)
            tool.setIcon(tool_icon(kind))
            tool.setCheckable(True)
            self.palette_group.addButton(tool, index)
            self.palette_tools[kind] = tool
            grid.addWidget(tool, 1 + index // 2, index % 2)
        self.palette_group.idClicked.connect(self._palette_tool)
        self.palette.setHidden(True)
        layout.addWidget(self.palette)
        # Both spaces: the hub picture.
        connections = QWidget()
        connections.setObjectName("sidebarFooter")
        connections.setLayout(vbox(button("⚲  Connections", self.show_connections,
                                          tooltip="What flows in, what flows out, and the tools that plug in"),
                                   spacing=0))
        layout.addWidget(connections)
        # A footer per space: what you start in it.
        self.footers = {}
        self.footers[MINE] = QWidget()
        self.footers[MINE].setObjectName("sidebarFooter")
        self.footers[MINE].setLayout(vbox(
            button("＋  New analysis", lambda: self.action_new_workflow(None)),
            button("＋  Open log…", self.action_open_log_box,
                   tooltip="An Open log box on the current analysis (or a new one)"),
            button("✎  Log from notation…", self.action_notation_box,
                   tooltip="A Typed log box on the current analysis (or a new one)"),
            button("⇄  Compare logs…", lambda: self.action_compare()),
            spacing=0))
        self.footers[MODEL] = QWidget()
        self.footers[MODEL].setObjectName("sidebarFooter")
        self.footers[MODEL].setLayout(vbox(
            button("＋  New Petri net", self.action_new_petri),
            button("＋  New coloured net", self.action_new_cpn),
            button("＋  Open net…", self.action_open),
            button("⇄  Compare nets…", self.action_compare_nets),
            spacing=0))
        self.footers[MODEL].setHidden(True)
        layout.addWidget(self.footers[MINE])
        layout.addWidget(self.footers[MODEL])
        return sidebar

    # -- the spaces: Mine, Model, Learn ------------------------------------------------
    def _build_space_bar(self) -> None:
        """The switcher at the top of the window: Mine, Model, Learn, in a slim
        bar under the title (and the menu, where the menu is in the window)."""
        bar = QToolBar("Spaces")
        bar.setObjectName("spaceBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.space_switch = SegmentedControl([LABELS[space] for space in SPACES])
        tips = {MINE: "Event logs and analyses: discover, check, compare",
                MODEL: "Petri nets and coloured nets: draw, play, simulate, analyse",
                LEARN: "Exercises: a worksheet beside its materials"}
        for space, control in zip(SPACES, self.space_switch.buttons):
            control.setToolTip(tips[space])
        self.space_switch.changed.connect(lambda index: self.set_space(SPACES[index]))
        bar.addWidget(self.space_switch)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self.space_bar = bar

    def set_space(self, space: str, remember: bool = True) -> bool:
        """Show ``space``.  Mine and Model swap the sidebar and the page; Learn
        opens the folder's exercises (or the demo ones) over the whole window.
        Returns False when nothing changed (Learn had nothing to open)."""
        if space == LEARN:
            if self.in_learn:
                return True
            opened = self._open_learn()
            if not opened:
                self._sync_space_switch()
            return opened
        was_learn = self.in_learn
        if was_learn:
            self.leave_learn(to=space)
        if space == self.space and not was_learn:
            self._sync_space_switch()
            return False
        current = self._current_document()
        if current is not None and space_of(current) == self.space:
            self._space_current[self.space] = current.id
        self._set_space_quietly(space)
        self._rebuild_sidebar()
        if remember and self.workspace is not None:
            self.workspace.update_settings(space=space)
        wanted = self._space_current.get(space)
        document = self._document(wanted) if wanted is not None else None
        if document is not None and document in self.documents:
            self._select_document(document)
        else:
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
            self.content.setCurrentIndex(0)
            self._set_title(None)
        motion.lift(self.modes)                      # the whole window moved: let it settle
        return True

    def _set_space_quietly(self, space: str) -> None:
        """Make ``space`` current without rebuilding or selecting anything."""
        self.space = space
        for key, footer in self.footers.items():
            footer.setVisible(key == space)
        self._sync_space_switch()
        self._update_palette()

    # -- the Model palette and the Connections view ----------------------------------------
    def _palette_tool(self, index: int) -> None:
        """A palette button: the page's tool of the same kind (the palette and
        a coloured net's toolbar do not number their tools alike)."""
        page = self.current_page()
        if isinstance(page, CpnPage):
            kind = PALETTE[index][0]
            kinds = [tool_kind for tool_kind, _text, _tip in page.TOOLS]
            if kind in kinds:
                page.tool_switch.set_index(kinds.index(kind))

    def _update_palette(self) -> None:
        """The palette shows for a net page in Model, ticked at the page's tool;
        the τ button only for a plain Petri net, which is the page that has it."""
        if not hasattr(self, "palette"):
            return
        page = self.current_page()
        show = self.space == MODEL and isinstance(page, CpnPage) and not self.in_learn
        self.palette.setVisible(show)
        if show:
            kinds = [tool_kind for tool_kind, _text, _tip in page.TOOLS]
            for kind, tool in self.palette_tools.items():
                tool.setVisible(kind in kinds)
            current = kinds[page.tool_switch.index()]
            tool = self.palette_tools.get(current)
            if tool is not None and not tool.isChecked():
                tool.setChecked(True)

    def install_tool(self, name: str) -> None:
        """*Install…* on a Connections card: pip installs the package into this
        app's Python, in the background; the boxes that need it come alive."""
        import importlib
        from ... import packages
        from .connections import package_for
        package = package_for(name)
        command = packages.install_command(package)
        answer = QMessageBox.question(
            self, "Install a tool", f"Install {package} {packages.where_text()}?\n\nThis runs:\n"
            f"{' '.join(command)}\n\nThe app stays usable meanwhile.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        self.connections_page.installing.add(name)
        self.connections_page.logs[name] = ["$ " + " ".join(command)]
        self.connections_page.refresh(self.library(), self.workspace.folder if self.workspace else None)
        self.statusBar().showMessage(f"Installing {package}…")
        lines = _InstallLines(self)
        lines.line.connect(self.connections_page.append_install_log)

        def done(result) -> None:
            code, output = result
            self.connections_page.installing.discard(name)
            packages.activate()                    # the downloaded app: the new folder, on the path
            importlib.invalidate_caches()
            self.connections_page.refresh(self.library(), self.workspace.folder if self.workspace else None)
            if code == 0:
                self.connections_page.logs.pop(name, None)
                for page in self.pages.values():
                    if isinstance(page, WorkflowPage) and hasattr(page, "reload_library"):
                        page.reload_library()
                self.statusBar().showMessage(f"Installed {package}: its boxes are available (Run ▶ an "
                                             "analysis that was waiting for it)", 10000)
            else:
                tail = "\n".join(output.strip().splitlines()[-20:])
                QMessageBox.warning(self, "Install a tool", f"pip could not install {package} "
                                    f"(exit code {code}).\n\n{tail}")
                self.statusBar().showMessage(f"Could not install {package}", 8000)

        run_in_background(lambda: PIP_RUNNER(command, lambda text: lines.line.emit(name, text)), done,
                          lambda message: done((1, str(message))))

    # -- the command palette: ⌘K ------------------------------------------------------------
    def palette_entries(self) -> list:
        """Everything the palette can run: documents, the folder's files, boxes
        for the current analysis, the spaces and Connections, every menu action."""
        from .command_palette import Entry
        from .documents import TransitionSystemDocument
        entries: list[Entry] = []
        kinds = {WorkflowDocument: "Analysis", CpnDocument: "Model", ModelDocument: "Model", LogDocument: "Log",
                 ComparisonDocument: "Comparison", TransitionSystemDocument: "Transition system"}
        for document in self.documents:
            kind = next((k for cls, k in kinds.items() if isinstance(document, cls)), "Document")
            where = Path(document.path).name if document.path else "not saved"
            entries.append(Entry(kind, document.name, where, lambda d=document: self._select_document(d),
                                 keywords="open"))
        if self._tree is not None:
            open_paths = {self._key(d.path) for d in self.documents if d.path}
            for folder in self._tree.walk():
                for file in folder.files:
                    if self._key(file.path) in open_paths:
                        continue
                    entries.append(Entry("File", file.name, file.relative, lambda p=str(file.path): self.open_path(p),
                                         keywords=file.kind, rank=1))
        page = self.current_page()
        library = self.library()
        for group, specs in library.by_group().items():
            for spec in specs:
                if isinstance(page, WorkflowPage):
                    run = lambda s=spec, pg=page: pg.add_box_in_view(s.id)
                    detail = f"{group} · add to {page.workflow.name}"
                else:
                    run = lambda s=spec: self.analysis_for("Untitled").add_box_in_view(s.id)
                    detail = f"{group} · add to a new analysis"
                entries.append(Entry("Box", spec.name, detail, run, keywords=f"{spec.id} {group}", rank=2))
        for space in SPACES:
            entries.append(Entry("Space", LABELS[space], {MINE: "event logs and analyses", MODEL: "Petri nets and "
                                 "coloured nets", LEARN: "exercises"}[space], lambda sp=space: self.set_space(sp)))
        entries.append(Entry("Space", "Connections", "what flows in and out, and the tools that plug in",
                             self.show_connections))
        for top in self.menuBar().actions():
            menu = top.menu()
            if menu is None:
                continue
            path = top.text().replace("&", "")
            stack = [(menu, path)]
            while stack:
                current, trail = stack.pop(0)
                for action in current.actions():
                    if action.isSeparator() or not action.text():
                        continue
                    if action.menu() is not None:
                        stack.append((action.menu(), f"{trail} ▸ {action.text().replace('&', '')}"))
                        continue
                    shortcut = action.shortcut().toString()
                    entries.append(Entry("Action", action.text().replace("&", ""),
                                         trail + (f" · {shortcut}" if shortcut else ""),
                                         lambda a=action: a.trigger(), rank=3))
        return entries

    def show_command_palette(self) -> None:
        """⌘K: find and run anything by typing."""
        from .command_palette import CommandPalette
        if self.in_learn:
            self.leave_learn()
        self.command_palette = CommandPalette(self.palette_entries(), self)
        centre = self.mapToGlobal(QPoint(self.width() // 2 - 280, 120))
        self.command_palette.open_at(centre)

    def show_connections(self) -> None:
        """The Connections view: what flows in and out, and the tools that plug in."""
        if self.in_learn:
            self.leave_learn()
        self.connections_page.refresh(self.library(), self.workspace.folder if self.workspace else None)
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)
        self.content.setCurrentWidget(self.connections_page)
        self._set_title(None)
        self._update_palette()

    def _sync_space_switch(self) -> None:
        shown = LEARN if self.in_learn else self.space
        index = SPACES.index(shown)
        if self.space_switch.index() != index:
            self.space_switch.blockSignals(True)
            self.space_switch.buttons[index].setChecked(True)
            self.space_switch.blockSignals(False)

    def _open_learn(self) -> bool:
        """Learn from the switcher: the folder's exercises, else the demo ones."""
        from ...learn.pack import find_exercises
        if self.workspace is not None and find_exercises(self.workspace.folder):
            return self.open_exercise(self.workspace.folder)
        self.open_demo_exercises()
        return self.in_learn

    def _build_sidebar_header(self) -> QWidget:
        """"OpenProcess Studio", or the workspace's name with a ⋯ menu."""
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 2, 6)
        row.setSpacing(4)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.workspace_caption = label("FOLDER", "sidebarCaption")
        self.workspace_caption.setHidden(True)
        self.sidebar_title = ElidedLabel(APPLICATION_NAME, "sidebarTitle")
        text.addWidget(self.workspace_caption)
        text.addWidget(self.sidebar_title)
        row.addLayout(text, 1)
        self.workspace_button = QToolButton()
        self.workspace_button.setObjectName("sidebarMenuButton")
        self.workspace_button.setText("⋯")
        self.workspace_button.setToolTip("Folder")
        self.workspace_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.workspace_button)
        reveal = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(
            sys.platform, "Open Folder")
        menu.addAction(reveal, lambda: self.workspace and self.reveal(str(self.workspace.folder)))
        menu.addAction("New Folder…", lambda: self.new_folder())
        menu.addSeparator()
        menu.addAction("Open Another Folder…", self.action_open_workspace)
        menu.addSeparator()
        menu.addAction("Close Folder", self.close_workspace)
        self.workspace_button.setMenu(menu)
        self.workspace_button.setHidden(True)
        row.addWidget(self.workspace_button, 0, Qt.AlignVCenter)
        # On the sidebar it hides, so what it does is plain to see; when the
        # sidebar is hidden, the rail at the left edge brings it back.
        self.sidebar_hide_button = self._rail_button(
            "sidebar", f"Hide the sidebar ({shortcut_text('Ctrl+Alt+S')})",
            lambda: self.toggle_sidebar(False))
        row.addWidget(self.sidebar_hide_button, 0, Qt.AlignVCenter)
        return header

    def toggle_sidebar(self, show: bool | None = None, remember: bool = True) -> None:
        """Show or hide the sidebar (hidden, every page gets the full width).

        ``remember=False``: a change the app made by itself, not kept for the
        next launch."""
        if show is None:
            show = self.sidebar.isHidden()
        self.sidebar.setHidden(not show)
        for page in self.pages.values():                 # no sidebar, no palette: the page's own tools
            if isinstance(page, CpnPage):
                page.tool_switch.setVisible(not show)
        self._update_palette()
        action = getattr(self, "sidebar_action", None)
        if action is not None and action.isChecked() != show:
            action.blockSignals(True)
            action.setChecked(show)
            action.blockSignals(False)
        if remember:
            self._set_setting("window/sidebar", show)
        self._update_rail()
        self._schedule_fit()

    # -- notes: scratch paper over every page (see notes_overlay) --------------------------
    def toggle_notes(self, show: bool | None = None) -> None:
        """Open or fold away the notes (in Learn: the exercise's own)."""
        if self.in_learn:
            if self.learn_mode.view is None:
                self.notes_action.setChecked(False)
                return
            if show is None:
                show = not self.learn_mode.view.notes_visible
            self.learn_mode.set_notes(show)
            return
        self.notes.set_open(not self.notes.is_open if show is None else show)

    def _sync_notes_action(self) -> None:
        """Tick Show Notes (and ✎ Notes) for the notes in view: the exercise's, or
        the window's."""
        if self.in_learn:
            view = self.learn_mode.view
            self._notes_opened(view is not None and view.notes_visible, remember=False)
            self.notes_action.setEnabled(view is not None)
            self.notes_button.setEnabled(view is not None)
        else:
            self._notes_opened(self.notes.is_open, remember=False)
            self.notes_action.setEnabled(True)
            self.notes_button.setEnabled(True)

    def _notes_opened(self, show: bool, remember: bool = True) -> None:
        if self.notes_action.isChecked() != show:
            self.notes_action.blockSignals(True)
            self.notes_action.setChecked(show)
            self.notes_action.blockSignals(False)
        self.notes_button.setChecked(show)
        if remember:
            self._set_setting("notes/open", show)

    def set_notes_button(self, show: bool) -> None:
        """Show the ✎ Notes button in the status bar (or rely on ⌘⌥N alone)."""
        self._notes_button_wanted = show
        self._update_notes_button()
        if self.notes_button_action.isChecked() != show:
            self.notes_button_action.blockSignals(True)
            self.notes_button_action.setChecked(show)
            self.notes_button_action.blockSignals(False)
        self._set_setting("notes/button", show)

    def _update_notes_button(self) -> None:
        """Learn has a ✎ Notes of its own, in its top bar."""
        self.notes_button.setVisible(getattr(self, "_notes_button_wanted", True)
                                     and not self.in_learn)

    def _save_notes(self, text: str) -> None:
        """Keep the notes: with the open folder, or in the app's settings."""
        if self.workspace is not None:
            try:
                self.workspace.save_notes(text)
            except OSError as error:
                self.statusBar().showMessage(f"Could not save your notes: {error}", 8000)
        else:
            self._loose_notes = text
            self._set_setting("notes/text", text)

    def _show_notes_of_folder(self) -> None:
        """The notes of the folder now open (or the loose ones, outside a folder)."""
        self.notes.set_text(self.workspace.load_notes() if self.workspace is not None
                            else self._loose_notes)

    # -- the rail: what brings hidden panels back --------------------------------------
    def _rail_button(self, icon: str, tooltip: str, slot) -> QToolButton:
        from .tool_icons import question_icon, sidebar_icon
        toggle = QToolButton()
        toggle.setObjectName("sidebarToggle")
        toggle.setIcon(sidebar_icon() if icon == "sidebar" else question_icon())
        toggle.setIconSize(QSize(20, 20))
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setToolTip(tooltip)
        toggle.clicked.connect(slot)
        return toggle

    def _build_rail(self) -> QWidget:
        """A slim strip at the left edge, shown while the sidebar is hidden, with
        the button that brings it back."""
        self.rail = QFrame()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(44)
        layout = QVBoxLayout(self.rail)
        layout.setContentsMargins(6, 14, 6, 10)
        layout.setSpacing(6)
        self.rail_sidebar = self._rail_button(
            "sidebar", f"Show the sidebar ({shortcut_text('Ctrl+Alt+S')})",
            lambda: self.toggle_sidebar(True))
        layout.addWidget(self.rail_sidebar, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        self.rail.setHidden(True)
        return self.rail

    def _update_rail(self) -> None:
        if not hasattr(self, "rail"):
            return
        sidebar_hidden = self.sidebar.isHidden()
        self.rail_sidebar.setVisible(sidebar_hidden)
        self.rail.setVisible(sidebar_hidden)

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [title])
        # No ▸ for a heading: the sidebar only draws one for subfolders (a
        # DontShowIndicator policy would also make Qt hide the heading's rows).
        item.setFlags(Qt.ItemIsEnabled)
        font = theme.ui_font(10, theme.QFont.Bold)
        item.setFont(0, font)
        item.setForeground(0, QColor(style.tokens().text_muted))
        item.setExpanded(True)
        item.setHidden(True)
        return item

    # ---------------------------------------------------------------- welcome
    def _build_welcome(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(20, 16, 32, 32)
        outer.addSpacing(24)
        outer.addStretch(1)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(96, 96))
        outer.addWidget(logo, 0, Qt.AlignHCenter)
        title = label(APPLICATION_NAME)
        title.setStyleSheet("font-size: 34px; font-weight: 700;")
        outer.addWidget(title, 0, Qt.AlignHCenter)
        subtitle = label("Process mining, Petri nets and workflows in one open app: "
                         "every algorithm there to read, every result reproducible.", "pageSubtitle")
        subtitle.setStyleSheet("font-size: 15px;")
        outer.addWidget(subtitle, 0, Qt.AlignHCenter)
        outer.addSpacing(30)

        grid = QGridLayout()
        grid.setSpacing(14)
        # A whole-width card on top: working in a folder is the main way in.
        workspace_card = Card("Work in a folder")
        workspace_card.add(label(
            "Open a folder such as “Week 2”: its event logs and Petri nets are listed in "
            "the sidebar, and the nets you draw are saved there. "
            "Next time, everything you had open comes back.", "muted", wrap=True))
        self.welcome_recent = QWidget()
        self.welcome_recent.setObjectName("plain")
        self.welcome_recent.setStyleSheet("#plain { background: transparent; border: none; }")
        self.welcome_recent.setLayout(hbox(spacing=8))
        workspace_card.add(hbox(button("Open folder…", self.action_open_workspace,
                                       kind="primary"), self.welcome_recent, None))
        self.welcome_folder_card = workspace_card
        grid.addWidget(workspace_card, 0, 0, 1, 3)
        # Inside a workspace the top card is about that folder instead.
        inside = Card("Folder")
        self.welcome_inside_title = inside.title_label
        self.welcome_inside_text = label("", "muted", wrap=True)
        inside.add(self.welcome_inside_text)
        reveal = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(
            sys.platform, "Open Folder")
        inside.add(hbox(button("New analysis", lambda: self.action_new_workflow(None), kind="primary"),
                        button("New Petri net", self.action_new_petri),
                        button(reveal, lambda: self.workspace and
                               self.reveal(str(self.workspace.folder))),
                        None,
                        button("Close folder", self.close_workspace)))
        inside.setHidden(True)
        self.welcome_inside_card = inside
        grid.addWidget(inside, 0, 0, 1, 3)
        cards = [
            ("Mine", "Process mining. Open an event log and build an analysis from boxes: a "
             "log, a miner, a check, a comparison. Every result shows how it got there, with "
             "the code and the paper behind it.",
             [("New analysis", lambda: self.action_new_workflow(TEMPLATES[0][1])),
              ("Open event log…", self.action_open_log_box),
              ("Log from notation…", self.action_notation_box)]),
            ("Model", "Petri nets and coloured nets. Draw places, transitions and arcs, play "
             "the token game, simulate, analyse the state space, and mine the simulated "
             "behaviour.",
             [("New Petri net", self.action_new_petri),
              ("New coloured net", self.action_new_cpn),
              ("Open CPN model…", self.action_open_cpn)]),
            ("Learn", "Exercise packs. A worksheet beside its materials, answers checked as "
             "you go, results hidden until you have worked them out.",
             [("Demo exercises", self.open_demo_exercises),
              ("Open exercise pack…", self.action_open_exercise)]),
        ]
        for index, (heading, text, actions) in enumerate(cards):
            card = Card(heading)
            card.setMinimumWidth(220)
            card.add(label(text, "muted", wrap=True))
            buttons = QVBoxLayout()
            buttons.setSpacing(6)
            for action, slot in actions:
                buttons.addLayout(hbox(button(action, slot), None))
            card.add(buttons)
            grid.addWidget(card, 1, index)
        holder = QWidget()
        holder.setLayout(grid)
        holder.setMaximumWidth(760)
        holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Centre with stretches rather than an alignment flag: an aligned
        # widget is sized by its sizeHint and its wrapped labels get clipped.
        outer.addLayout(hbox(None, holder, None))
        outer.addSpacing(20)
        tip = label("Tip: drop files (or a folder, to work in it) anywhere on this "
                    "window. Hover a file in the sidebar and click ✕ (or press "
                    f"{shortcut_text('Backspace')}) to close "
                    "it; double-click a name to rename it.", "muted", wrap=True)
        tip.setAlignment(Qt.AlignHCenter)
        tip.setMaximumWidth(760)
        tip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer.addLayout(hbox(None, tip, None))
        outer.addStretch(2)
        self._refresh_welcome_recent()
        return page

    # ---------------------------------------------------------------- menus
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._action(file_menu, "Open…", QKeySequence.Open, self.action_open)
        self._action(file_menu, "Open Folder…", "Ctrl+Alt+O", self.action_open_workspace)
        self.recent_workspaces_menu = file_menu.addMenu("Open Recent Folder")
        self.recent_workspaces_menu.aboutToShow.connect(self._fill_recent_workspaces_menu)
        self.close_workspace_action = self._action(file_menu, "Close Folder", None,
                                                   self.close_workspace)
        self.close_workspace_action.setEnabled(False)
        file_menu.addSeparator()
        self._action(file_menu, "New Log from Notation…", "Ctrl+L", self.action_notation)
        self._action(file_menu, "New Petri Net", QKeySequence.New, self.action_new_petri)
        self._action(file_menu, "New Coloured Petri Net", "Ctrl+Shift+N", self.action_new_cpn)
        self._action(file_menu, "New Transition System…", None, self.action_new_ts)
        workflows = file_menu.addMenu("New Workflow")
        for text, build, about in TEMPLATES:
            action = workflows.addAction(text, lambda b=build: self.action_new_workflow(b))
            action.setToolTip(about)
        workflows.addSeparator()
        workflows.addAction("Empty workflow", lambda: self.action_new_workflow(None))
        examples = file_menu.addMenu("Open Example Log")
        for name, text in EXAMPLES.items():
            examples.addAction(name, lambda n=name, t=text: self.add_document(LogDocument(
                EventLog.from_simple_log(parse_simple_log(t), n.split(" (")[0]), notation=t)))
        from ...model.examples import EXAMPLES as PETRI_EXAMPLES
        petri_examples = file_menu.addMenu("Open Example Petri Net")
        for name, build in PETRI_EXAMPLES.items():
            petri_examples.addAction(name, lambda b=build: self.open_example_net(b()))
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addSeparator()
        self._action(file_menu, "Open Coloured Petri Net…", "Ctrl+Shift+O", self.action_open_cpn)
        self._action(file_menu, "Compare Logs…", "Ctrl+Shift+C", lambda: self.action_compare())
        self._action(file_menu, "Compare Nets…", None, self.action_compare_nets)
        file_menu.addSeparator()
        self._action(file_menu, "Save", QKeySequence.Save, self.action_save)
        self._action(file_menu, "Save As / Export…", "Ctrl+Shift+S", self.export_selected)
        self._action(file_menu, "Export Selected…", "Ctrl+E", self.export_selected)
        self._action(file_menu, "Rename…", None, self.rename_selected)
        self._action(file_menu, "Revert to Saved…", None, self.revert_selected)
        self.autosave_action = QAction("Autosave", self)
        self.autosave_action.setCheckable(True)
        self.autosave_action.setChecked(self.autosave_enabled)
        self.autosave_action.setToolTip("Save edits to nets in the open folder as you go")
        self.autosave_action.toggled.connect(self.set_autosave)
        file_menu.addAction(self.autosave_action)
        file_menu.addSeparator()
        self._action(file_menu, f"Move to {BIN}", None, self.trash_selected)
        self._action(file_menu, "Close", QKeySequence.Close, self.remove_selected)
        self._action(file_menu, "Close All", "Ctrl+Shift+W", self.remove_all)
        file_menu.addSeparator()
        self.restore_action = QAction("Reopen Files at Launch", self)
        self.restore_action.setCheckable(True)
        self.restore_action.setChecked(self._setting("session/restore", True))
        self.restore_action.toggled.connect(lambda on: self._set_setting("session/restore", on))
        file_menu.addAction(self.restore_action)
        settings = self._action(file_menu, "Settings…", QKeySequence.Preferences,
                                self.show_settings)
        settings.setMenuRole(QAction.PreferencesRole)      # the app menu, on macOS

        view_menu = self.menuBar().addMenu("&View")
        view_menu.aboutToShow.connect(self._sync_notes_action)
        for index, space in enumerate(SPACES):
            self._action(view_menu, LABELS[space], f"Ctrl+Alt+{index + 1}",
                         lambda _on=False, sp=space: self.set_space(sp))
        self._action(view_menu, "Connections", None, self.show_connections)
        self._action(view_menu, "Command Palette…", "Ctrl+K", self.show_command_palette)
        view_menu.addSeparator()
        self._action(view_menu, "Show Welcome Page", "Ctrl+1",
                     lambda: (self.in_learn and self.leave_learn(),
                              self.content.setCurrentIndex(0)))
        self.sidebar_action = self._action(view_menu, "Show Sidebar", "Ctrl+Alt+S",
                                           lambda on: self.toggle_sidebar(on))
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(True)
        self.notes_action = self._action(view_menu, "Show Notes", "Ctrl+Alt+N",
                                         lambda on: self.toggle_notes(on))
        self.notes_action.setCheckable(True)
        self.notes_button_action = self._action(view_menu, "Show Notes in Status Bar", None,
                                                lambda on: self.set_notes_button(on))
        self.notes_button_action.setCheckable(True)
        self.notes_button_action.setChecked(True)
        view_menu.addSeparator()
        # The Workflows page's side panel (the action acts on the current page).
        self.reset_layout_action = self._action(view_menu, "Reset Workflow Layout", None, self._reset_workflow_layout)
        view_menu.aboutToShow.connect(self._sync_workflow_actions)
        view_menu.addSeparator()
        zoom_in = self._action(view_menu, "Zoom In", QKeySequence.ZoomIn,
                               lambda: self._zoom("zoom_in"))
        zoom_in.setShortcuts([QKeySequence.ZoomIn, QKeySequence("Ctrl+=")])
        self._action(view_menu, "Zoom Out", QKeySequence.ZoomOut, lambda: self._zoom("zoom_out"))
        self._action(view_menu, "Zoom to Fit", "Ctrl+0", lambda: self._zoom("fit"))
        self._action(view_menu, "Actual Size", "Ctrl+Alt+0", lambda: self._zoom("actual_size"))

        # Learn: exercise packs on top of the app (openprocess.learn, openprocess.gui.learn).
        learn_menu = self.menuBar().addMenu("&Learn")
        self._action(learn_menu, "Open Exercise Pack…", None, self.action_open_exercise)
        self._action(learn_menu, "Open Demo Exercises", None, self.open_demo_exercises)
        learn_menu.addSeparator()
        self._action(learn_menu, "Make a Pack from an Exam…", None, self.action_import_exam)
        self._action(learn_menu, "Writing Exercise Packs", None,
                     lambda: self.show_guide("exercise-packs"))

        help_menu = self.menuBar().addMenu("&Help")
        self._action(help_menu, "Definitions", None, self._definitions)
        self._action(help_menu, "References", None, lambda: self.show_guide("references"))
        self._action(help_menu, "Writing Exercise Packs", None,
                     lambda: self.show_guide("exercise-packs"))
        self._action(help_menu, "Cite OpenProcess…", None, self.cite_app)
        self._action(help_menu, "Check for Updates…", None,
                     lambda: self.check_for_updates(manual=True))
        self._action(help_menu, f"About {APPLICATION_NAME}", None, self._about)

    def _reset_workflow_layout(self) -> None:
        page = self.current_page()
        if not isinstance(page, WorkflowPage):
            self.statusBar().showMessage("Open a workflow first: the side panel belongs to the Workflows page", 6000)
            return
        page.reset_layout()

    def _sync_workflow_actions(self) -> None:
        self.reset_layout_action.setEnabled(isinstance(self.current_page(), WorkflowPage))

    def _action(self, menu, text, shortcut, slot) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def cite_app(self) -> None:
        """Help ▸ Cite OpenProcess…: the app's BibTeX and a one-line citation."""
        from .cite import cite_app
        self.cite_dialog = cite_app(self)

    def _definitions(self) -> None:
        """Every analysis property, defined mathematically (docs/definitions.md)."""
        from .definition_view import show_reference
        show_reference(None, self)

    def show_guide(self, name: str) -> None:
        """References, or the guide to writing exercise packs."""
        from .definition_view import show_guide
        show_guide(name, self)

    def _about(self) -> None:
        from ... import __version__
        QMessageBox.about(self, APPLICATION_NAME,
                          f"<b>{APPLICATION_NAME}</b> {__version__}"
                          "<p>Process mining and Petri nets in pure "
                          "Python. Algorithms follow the TU/e course readings; see the "
                          "docstrings in <code>openprocess.mining</code>.</p>")

    # ---------------------------------------------------------------- documents
    def logs(self) -> list[LogDocument]:
        return [d for d in self.documents if isinstance(d, LogDocument)]

    def add_document(self, document, options: dict | None = None,
                     near: str | None = None, keep_unsaved: bool = False) -> None:
        """Add a document to the window and show it.

        Opening a file that is already open just selects it, so the sidebar
        never fills up with duplicates.  With a folder open, a new log or net
        (one with no file yet) is saved into the folder straight away, next
        to ``near`` (the file it was made from) when that is in the folder.
        """
        if self.in_learn:              # File ▸ New… or Open… from Learn
            self.leave_learn()
        if document.path:
            for existing in self.documents:
                if existing.path and self._key(existing.path) == self._key(document.path) \
                        and type(existing) is type(document):
                    self._select_document(existing)
                    self.statusBar().showMessage(f"{Path(document.path).name} is already open", 5000)
                    return
        self.documents.append(document)
        if options:
            self.open_options[document.id] = options
        self._make_page(document)
        # A view of a box's result waits for Keep in folder (a model has its own Keep,
        # which _materialise shows); everything else becomes a file now.
        if not keep_unsaved and (not self.is_view(document) or isinstance(document, ModelDocument)):
            self._materialise(document, near)
        if document.path:
            key = self._key(document.path)
            self._opening.discard(key)
            self._remember_identity(document)
            if isinstance(document, CpnDocument):
                if not document.path.lower().endswith(".cpn") or made_by_openprocess(document.path):
                    self._openprocess_files.add(document.id)
                self._keep_original(document)
        self._update_autosave(document)
        # Reopening a folder loads logs in the background, in any order: of
        # those files, only the one that was selected last time takes the
        # selection.  Anything you open yourself is always selected.
        key = self._key(document.path) if document.path else None
        restored = key is not None and key in self._restore_paths
        self._restore_paths.pop(key, None)
        if not restored and space_of(document) != self.space:
            self.set_space(space_of(document))          # a document opens in its own space
        self._rebuild_sidebar()
        if not restored or self._restore_selected in (None, key):
            self._select_document(document)
        self._refresh_log_choices()
        if document.path:
            self._remember_recent(document.path)
        self._save_session()

    def _make_page(self, document) -> QWidget:
        """Build the page showing ``document`` (and its holder in the stack)."""
        if isinstance(document, LogDocument):
            page = LogPage(document, discover="handoff")
            page.open_model.connect(self.add_document)
            page.discover_requested.connect(lambda key, doc=document: self.discover_in_analysis(doc, key))
            page.open_log.connect(lambda log, source=document: self.add_document(
                log, near=source.path))
            page.edited.connect(lambda doc=document: self._log_edited(doc))
            page.tabs.changed.connect(lambda i: self.remembered.__setitem__("log_tab", i))
        elif isinstance(document, TransitionSystemDocument):
            from .regions_view import TransitionSystemPage
            page = TransitionSystemPage(document)
            page.open_model.connect(self.add_document)
        elif isinstance(document, ComparisonDocument):
            page = ComparePage(document)
            page.tabs.changed.connect(lambda i: self.remembered.__setitem__("compare_tab", i))
        elif isinstance(document, WorkflowDocument):
            page = WorkflowPage(document, self.library(),
                                self.workspace.folder if self.workspace is not None else None)
            page.open_document.connect(lambda doc, source=document: self.add_document(doc, near=source.path))
            page.edit_requested.connect(lambda net, origin, doc=document: self.edit_petri_net(net, origin, doc))
            page.keep_requested.connect(lambda doc=document: self.keep_workflow(doc))
            page.edited.connect(lambda doc=document: (self._refresh_item(doc), self._schedule_autosave(doc)))
            page.ran.connect(lambda doc=document: self._schedule_autosave(doc))   # the record follows the run
            if self.workspace is not None:
                page.custom_allowed = bool(self.workspace.settings().get("boxes_allowed"))
                page._refresh_custom_bar()
                page.custom_allowed_changed.connect(
                    lambda: self.workspace and self.workspace.update_settings(boxes_allowed=True))
            page.layout_changed.connect(lambda p=page: self._set_setting("workflows/layout", json.dumps(p.layout_state())))
            QTimer.singleShot(0, lambda p=page: p.run_from(None))
        elif isinstance(document, CpnDocument):
            plain = getattr(document.net, "plain", False)
            page = PetriNetPage(document) if plain else CpnPage(document)
            page.log_generated.connect(lambda log, source=document: self.mine_log(log, source))
            # The sidebar's palette stands in for the page's tool buttons while it shows.
            page.tool_switch.changed.connect(lambda _i: self._update_palette())
            page.tool_switch.setVisible(self.sidebar.isHidden())
            if plain:
                page.check_requested.connect(lambda doc=document: self.check_against_log(doc))
            page.dirty_changed.connect(lambda _dirty, doc=document: self._refresh_item(doc))
            page.edited.connect(lambda doc=document: self._schedule_autosave(doc))
            page.snap_changed.connect(self._snap_changed)
            page.inspector_tabs.changed.connect(
                lambda i, key="petri_inspector" if plain else "cpn_inspector":
                self.remembered.__setitem__(key, i))
            # Shown or hidden by hand: the window no longer folds it by itself.
            page.inspector_toggle.clicked.connect(
                lambda _=False, p=page: setattr(p, "_inspector_auto_hidden", False))
            if plain:
                page.open_model.connect(self.add_document)
        else:
            page = ModelPage(document, self.logs, conformance="handoff")
            page.check_requested.connect(lambda doc=document: self.check_against_log(doc))
            page.inspector_tabs.changed.connect(
                lambda i: self.remembered.__setitem__("model_inspector", i))
            page.view_switch.changed.connect(
                lambda i: self.remembered.__setitem__("model_view", i))
            page.log_generated.connect(lambda log, source=document: self.mine_log(log, source))
            page.edit_requested.connect(lambda net, doc=document: self.edit_petri_net(
                net, f"from {doc.name}", doc))
            page.keep_requested.connect(lambda doc=document: self.keep_model(doc))
        page.status.connect(lambda message: self.statusBar().showMessage(message, 8000))
        page.saved.connect(lambda doc=document: self._document_saved(doc))
        if not isinstance(document, ComparisonDocument):
            page.header.title_double_clicked.connect(
                lambda doc=document: self.rename_document(doc))
            page.header.title.set_hint("Double-click to rename")
        source = getattr(document, "opened_from", None)
        if source is not None and hasattr(page, "header"):
            # Opened from a box's result (or a copy of one): a way back to that analysis.
            origin = getattr(document, "origin", "") if isinstance(document, CpnDocument) else ""
            text = f"‹ A copy, {origin}" if origin else f"‹ Back to {source.name}"
            page.header.set_back(text, lambda _checked=False, s=source: self.back_to(s))
            if self.is_view(document) and self.workspace is not None and not hasattr(page, "keep_button"):
                # A view is not a file of the folder until you say so.
                page.keep_view_button = button("Keep in folder", lambda _checked=False, d=document: self.keep_view(d),
                                               kind="primary", tooltip="Save this as a file in the folder; until "
                                               "then it is only a view of the box's result")
                page.header.actions.insertWidget(0, page.keep_view_button)
        self.pages[document.id] = page
        # The page sits in a scroll area: if the window is made smaller than
        # the page's minimum size, scroll bars appear instead of the window
        # refusing to shrink (which is what pushed it off the screen).
        # Above it, a bar for news about its file ("changed on disk", …).
        banner = NoticeBar()
        holder = QWidget()
        holder.setLayout(vbox(banner, scroll(page, horizontal=True), spacing=0))
        holder.layout().setStretch(1, 1)
        self.banners[document.id] = banner
        self.holders[document.id] = holder
        self.content.addWidget(holder)
        return page

    @staticmethod
    def is_view(document) -> bool:
        """A page opened from a box's result (Open as log ›): a view of something
        the workflow can make again.  Not saved to the folder unless kept, and
        closed without a question."""
        return getattr(document, "opened_from", None) is not None and not document.path and \
            isinstance(document, (LogDocument, TransitionSystemDocument, ModelDocument))

    def keep_view(self, document) -> None:
        """A view's *Keep in folder*: now it is a file of the folder like any other."""
        source = getattr(document, "opened_from", None)
        near = getattr(source, "path", None)
        self._materialise(document, near)
        if document.path:
            page = self.pages.get(document.id)
            keep = getattr(page, "keep_view_button", None)
            if keep is not None:
                keep.setVisible(False)
            self._remember_identity(document)
            self._update_autosave(document)
            self._rebuild_sidebar()
            self._select_document(document)
            self.statusBar().showMessage(f"Kept as {Path(document.path).name}", 6000)

    def back_to(self, source) -> None:
        """A page's Back link: the analysis it was opened from, if it is still open."""
        if source in self.documents:
            self._select_document(source)
        else:
            self.statusBar().showMessage(f"{source.name} is no longer open", 6000)

    def _snap_changed(self, on: bool) -> None:
        """Snap to grid was ticked in one editor: the same everywhere, and next time."""
        self._set_setting("editor/snap_to_grid", on)
        for page in self.pages.values():
            box = getattr(page, "grid_box", None)
            if box is not None and box.isChecked() != on:
                box.blockSignals(True)
                box.setChecked(on)
                box.blockSignals(False)

    def _select_document(self, document) -> None:
        """Make ``document`` the current one, switching to its space if need be.
        While a folder is being reopened, a document of the other space is
        only remembered as that space's current one."""
        space = space_of(document)
        if space != self.space:
            if self._restoring:
                self._space_current[space] = document.id
                return
            self.set_space(space)
        item = self.items.get(document.id)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:                  # inside a collapsed subfolder
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.clearSelection()
        self.tree.setCurrentItem(item)

    def _refresh_item(self, document) -> None:
        """Sidebar text: the name, plus a dot while a model has unsaved edits."""
        item = self.items.get(document.id)
        if item is None:
            return
        if item.text(0) != self._row_text(document) and self.workspace is not None:
            self._rebuild_sidebar()               # a new name: keep the rows in order
        else:
            self._style_document_row(item, document)

    def _row_text(self, document) -> str:
        dirty = getattr(document, "dirty", False) and not getattr(document, "autosave", False)
        return document.name + ("  •" if dirty else "")

    def _style_document_row(self, item: QTreeWidgetItem, document) -> None:
        item.setText(0, self._row_text(document))
        font = item.font(0)
        font.setItalic(bool(getattr(document, "missing", False)))
        item.setFont(0, font)
        self._set_item_tooltip(item, document)

    def _set_item_tooltip(self, item: QTreeWidgetItem, document) -> None:
        if getattr(document, "missing", False):
            where = f"{document.path}\nThe file is missing (moved or deleted) — Save As… to keep it"
        else:
            where = document.path or "Not saved to a file — export it to keep it"
        item.setToolTip(0, f"{document.name}\n{where}")

    def _refresh_log_choices(self) -> None:
        for page in self.pages.values():
            if isinstance(page, ModelPage):
                page.refresh_logs()

    def _on_select(self, current, previous=None) -> None:
        # Switching to another file saves the one you were editing.
        try:
            left = previous.data(0, Qt.UserRole) if previous is not None else None
        except RuntimeError:                      # a row the sidebar has just rebuilt
            left = None
        if left is not None:
            self._flush_autosaves([left])
        if current is None or current.data(0, Qt.UserRole) is None:
            return
        holder = self.holders.get(current.data(0, Qt.UserRole))
        if holder is not None:
            self._restore_tab(self.pages[current.data(0, Qt.UserRole)])
            self.content.setCurrentWidget(holder)
            self._schedule_fit()
            self._set_title(self._document(current.data(0, Qt.UserRole)))
        self._update_palette()

    def _restore_tab(self, page: QWidget) -> None:
        """Show the same tab on the newly selected page as on the last one."""
        if isinstance(page, LogPage):
            wanted = self.remembered["log_tab"]
            if page.tabs.index() != wanted:
                page.tabs.set_index(wanted)
        elif isinstance(page, ModelPage):
            if page.inspector_tabs.index() != self.remembered["model_inspector"]:
                page.inspector_tabs.set_index(self.remembered["model_inspector"])
            if page.view_switch.index() != self.remembered["model_view"]:
                page.view_switch.set_index(self.remembered["model_view"])
        elif isinstance(page, ComparePage):
            wanted = self.remembered.get("compare_tab", 0)
            if page.tabs.index() != wanted:
                page.tabs.set_index(wanted)
        elif isinstance(page, CpnPage):
            wanted = self.remembered.get("cpn_inspector", 0)
            if page.inspector_tabs.index() != wanted:
                page.inspector_tabs.set_index(wanted)

    def active_graph_view(self) -> GraphView | None:
        """The canvas the zoom commands act on: the focused one, else the
        largest canvas visible on the current page."""
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (GraphView, NetView)) and widget.isVisible():
                return widget
            widget = widget.parentWidget()
        page = self.current_page()
        if page is None:
            return None
        views = [v for v in page.findChildren(GraphView) + page.findChildren(NetView)
                 if v.isVisible()]
        return max(views, key=lambda v: v.width() * v.height(), default=None)

    def _zoom(self, how: str) -> None:
        view = self.active_graph_view()
        if view is not None:
            getattr(view, how)()

    def restyle(self) -> None:
        """Re-apply colours after a light/dark switch."""
        for controls in self.findChildren(ZoomControls):
            controls.restyle()
        for page in self.pages.values():
            if isinstance(page, CpnPage):
                page.restyle()
        self.update()

    def current_page(self) -> QWidget | None:
        """The LogPage or ModelPage being shown (None on the welcome page)."""
        holder = self.content.currentWidget()
        for document_id, candidate in self.holders.items():
            if candidate is holder:
                return self.pages[document_id]
        return None

    def _document(self, document_id: int):
        return next(d for d in self.documents if d.id == document_id)

    def selected_ids(self) -> list[int]:
        """Selected documents in sidebar order; the current one if none is selected."""
        ids = [item.data(0, Qt.UserRole) for item in self.tree.selectedItems()
               if item.data(0, Qt.UserRole) is not None]
        if not ids and self.tree.currentItem() is not None:
            current = self.tree.currentItem().data(0, Qt.UserRole)
            if current is not None:
                ids = [current]
        return ids

    # -- removing ----------------------------------------------------------------
    def remove_selected(self) -> None:
        self.remove_documents(self.selected_ids())

    def remove_all(self) -> None:
        if self.documents:
            self.remove_documents([d.id for d in self.documents])

    def remove_documents(self, ids: list[int], confirm: bool = True) -> bool:
        """Close documents.  Files on disk are never touched (edits are saved
        first where autosave applies).

        Returns False when the user cancelled.  In a folder, a closed file
        stays listed (lighter) so it can be opened again with one click.

        Only documents that exist nowhere else (a log typed in notation, a
        model just discovered) need a confirmation, because removing them
        loses them; anything opened from a file can simply be opened again.
        ``confirm=False`` skips that question (the caller asked already).
        """
        documents = [d for d in self.documents if d.id in set(ids)]
        if not documents:
            return True
        self._flush_autosaves([d.id for d in documents])
        # Comparisons of removed logs go too (they cannot outlive their logs).
        removed_logs = {d.id for d in documents if isinstance(d, LogDocument)}
        for other in self.documents:
            if isinstance(other, ComparisonDocument) and other not in documents and \
                    any(log.id in removed_logs for log in other.logs):
                documents.append(other)
        # A comparison is recreated in a moment, and a view of a box's result
        # can be opened again from the box, so neither needs a confirmation.
        unsaved = [d for d in documents if not isinstance(d, ComparisonDocument) and not self.is_view(d) and
                   (not d.path or d.missing or getattr(d, "dirty", False))]
        if unsaved and confirm:
            names = "\n".join(f"• {d.name}" for d in unsaved[:8])
            more = f"\n… and {len(unsaved) - 8} more" if len(unsaved) > 8 else ""
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Close")
            box.setText(f"Close {len(documents)} item(s)? These have changes that exist only "
                        "in this window and will be lost:")
            box.setInformativeText(names + more + "\n\nSave or export them first to keep "
                                   "them.")
            remove = box.addButton("Close", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is not remove:
                return False

        # Choose what to select afterwards: the row below the last removed one,
        # in this space (the other space keeps its own current document).
        order = [item.data(0, Qt.UserRole) for item in self._document_items()]
        order += [d.id for d in self.documents if d.id not in order]
        removed = {d.id for d in documents}
        shown = [i for i in order if space_of(self._document(i)) == self.space]
        last = max((i for i in removed if i in shown), key=order.index, default=None)
        after = [i for i in shown[shown.index(last) + 1:] if i not in removed] \
            if last is not None else []
        before = [i for i in shown if i not in removed]
        target = after[0] if after else (before[-1] if before else None)
        for space, current in list(self._space_current.items()):
            if current in removed:
                self._space_current[space] = None

        for document in documents:
            self._drop_page(document)
            self.open_options.pop(document.id, None)
            for store in (self._originals, self._autosave_timers):
                store.pop(document.id, None)
            self._openprocess_files.discard(document.id)
            self._conflicts.discard(document.id)
        self.documents = [d for d in self.documents if d.id not in removed]
        self._rebuild_sidebar()             # closed files of the folder are listed again
        self._refresh_log_choices()
        self._save_session()

        if target is not None:
            self._select_document(self._document(target))
        else:
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
            self.content.setCurrentIndex(0)
            self._set_title(None)
        self._update_palette()
        noun = "item" if len(documents) == 1 else "items"
        self.statusBar().showMessage(f"Closed {len(documents)} {noun}", 5000)
        return True

    # Kept for the ⌘W binding in older code paths.
    close_current = remove_selected

    def _drop_page(self, document) -> None:
        page = self.pages.pop(document.id)
        if hasattr(page, "shutdown"):
            page.shutdown()
        holder = self.holders.pop(document.id)
        self.content.removeWidget(holder)
        holder.deleteLater()
        self.banners.pop(document.id, None)
        timer = self._autosave_timers.get(document.id)
        if timer is not None:
            timer.stop()

    def _document_items(self) -> list[QTreeWidgetItem]:
        """The open documents' rows, top to bottom (not the "not open" ones)."""
        items = []
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value() is not None:
            item = iterator.value()
            if item.data(0, Qt.UserRole) is not None:
                items.append(item)
            iterator += 1
        return items

    # -- renaming, exporting, revealing ---------------------------------------------
    def rename_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) == 1:
            self.rename_document(self._document(ids[0]))

    #: Characters macOS, Windows or Linux do not allow in a file name.
    FILE_NAME_FORBIDDEN = FORBIDDEN_CHARACTERS

    def _rename_on_disk(self, source: Path, target: Path) -> bool:
        """Rename a file or folder, explaining why not if it cannot be done."""
        problem = None
        name = target.name if source.is_dir() else _file_stem(target)
        if self.FILE_NAME_FORBIDDEN & set(name) or name in (".", "..") \
                or name.startswith("."):
            problem = ("A name cannot start with a dot or contain any of  "
                       "/ \\ : * ? \" < > |")
        elif target.exists() and not _same_file(source, target):
            problem = f"There is already something called “{target.name}” in that folder."
        else:
            self._flush_autosaves()
            try:
                source.rename(target)          # a case-only change works too (same file)
            except OSError as error:
                problem = f"It could not be renamed: {error.strerror or error}"
        if problem is not None:
            QMessageBox.warning(self, "Could not rename", problem)
            return False
        self._paths_moved(source, target)
        return True

    def rename_document(self, document) -> None:
        """Ask for a new name; if the document is named after its file, rename the file too.

        A net opened from ``order.pnml`` (or saved as it) is called "order", so
        renaming it to "order v2" also renames the file to ``order v2.pnml``
        in the same folder.  A document whose name is not its file's name (an
        event log is named by its ``concept:name``, e.g. "Wilma 50 passengers"
        in ``PlaneBoarding_Wilma_50.xes``) only gets a new name in the app.
        """
        if isinstance(document, ComparisonDocument):
            return
        old_name = document.name
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=old_name)
        name = name.strip()
        if not ok or not name or name == old_name:
            return

        source = Path(document.path) if document.path else None
        renames_file = (source is not None and source.exists()
                        and _file_stem(source) == old_name)
        if renames_file:
            if self.FILE_NAME_FORBIDDEN & set(name) or name in (".", ".."):
                QMessageBox.warning(self, "Could not rename", "A file name cannot contain any "
                                    "of  / \\ : * ? \" < > |")
                return
            target = source.with_name(name + _file_suffix(source))
            if not self._rename_on_disk(source, target):
                return
            self.statusBar().showMessage(f"Renamed the file to {target.name}", 8000)
        elif isinstance(document, CpnDocument):
            # Only the name in the model changes: it is an edit, so it can be undone.
            self.pages[document.id]._checkpoint()

        if isinstance(document, LogDocument):
            document.log.attributes["concept:name"] = name
        elif isinstance(document, TransitionSystemDocument):
            document.ts.name = name
        elif isinstance(document, WorkflowDocument):
            document.workflow.name = name
            document.dirty = True
            self._schedule_autosave(document)
        else:
            document.net.name = name
        if isinstance(document, CpnDocument) and not renames_file:
            self.pages[document.id].set_dirty(True)
        self._rebuild_sidebar()
        self.pages[document.id].refresh_title()
        self._set_title(document)
        self._refresh_log_choices()
        self._save_session()

    def rename_path(self, path: str) -> None:
        """Rename a file that is not open, or a subfolder (the sidebar's Rename…)."""
        source = Path(path)
        folder = source.is_dir()
        old_name = source.name if folder else _file_stem(source)
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=old_name)
        name = name.strip()
        if not ok or not name or name == old_name:
            return
        target = source.with_name(name if folder else name + _file_suffix(source))
        if self._rename_on_disk(source, target):
            self.statusBar().showMessage(f"Renamed to {target.name}", 6000)
            self._rescan_workspace()

    def _paths_moved(self, old: Path, new: Path) -> None:
        """A file or folder was moved or renamed (by the app): follow it.

        Open documents in it get their new path (unsaved edits stay, with
        their dot), and so do the remembered recent files and expanded folders.
        """
        old, new = Path(old), Path(new)

        base = old.resolve()

        def moved(path: str) -> Path | None:
            candidate = Path(path).resolve()
            if candidate == base:
                return new
            try:
                return new / candidate.relative_to(base)
            except ValueError:
                return None

        for document in self.documents:
            if document.path and (target := moved(document.path)) is not None:
                self._forget_recent(document.path)
                previous_key = self._key(document.path)
                document.path = str(target)
                if previous_key in self._own_writes:
                    self._own_writes[self._key(target)] = self._own_writes.pop(previous_key)
                if previous_key in self._created:     # still the app's own "Untitled 1"
                    self._created.discard(previous_key)
                    self._created.add(self._key(target))
                if getattr(document, "_last_path", None):
                    document._last_path = str(target)
                self._remember_recent(document.path)
                self.pages[document.id].refresh_title()
                self._update_autosave(document)
        self._save_session()

    def export_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) == 1:
            self.pages[ids[0]].export()

    def _document_saved(self, document) -> None:
        """A page saved or exported its document: it is now backed by that file."""
        previous = getattr(document, "_last_path", None)
        document._last_path = document.path
        signature = _signature(document.path) if document.path else None
        if signature is not None:
            self._own_writes[self._key(document.path)] = signature
            self._remember_identity(document)     # a safe save is a new file on disk
        if previous and self._key(previous) != self._key(document.path) \
                and self._key(previous) in self._created and Path(previous).exists():
            # Save As of a net the app created ("Untitled 1.pnml"): the old file
            # was only ever the app's, so it does not stay behind as a duplicate.
            self._created.discard(self._key(previous))
            move_to_trash(previous)
        if isinstance(document, CpnDocument):
            self._openprocess_files.add(document.id)      # OpenProcess wrote it: autosaving is safe
        if document.id in self._autosave_failed:    # saving works again
            self._autosave_failed.discard(document.id)
            self.banners[document.id].clear("unsaved")
        self._update_autosave(document)
        banner = self.banners.get(document.id)
        if banner is not None:
            banner.clear("missing")
        if document.id in self.items:
            self._refresh_item(document)
            if self.selected_ids() == [document.id]:
                # Save As may have renamed it after the file (see CpnPage._take_file_name).
                self._set_title(document)
        self.open_options.pop(document.id, None)      # it is XES/PNML now, not CSV
        if document.path and document.path.lower().endswith(".csv"):
            # Reopen the exported CSV with its own (standard) columns, unasked.
            mapping = guess_mapping(sniff(document.path)[1])
            if mapping is not None:
                self.open_options[document.id] = {"csv_mapping": asdict(mapping)}
        self._remember_recent(document.path)
        self._save_session()

    def reveal(self, path: str) -> None:
        """Show the file in Finder / Explorer (selected), or open its folder."""
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(Path(path))], check=False)
        else:   # Linux: no standard "select this file", so open the folder
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _sidebar_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        menu = QMenu(self)
        in_folder = self.workspace is not None
        if item is not None and item.data(0, FOLDER_ROLE):
            path = item.data(0, FOLDER_ROLE)
            if item.data(0, EXERCISE_ROLE):
                menu.addAction("Open Exercise", lambda: self.open_exercise(path))
                menu.addSeparator()
            menu.addAction("New Folder…", lambda: self.new_folder(path))
            menu.addAction("Rename…", lambda: self.rename_path(path))
            menu.addAction(reveal_label(), lambda: self.reveal(path))
            menu.addSeparator()
            menu.addAction(f"Move to {BIN}", lambda: self.trash_paths([path]))
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        if item is not None and item.data(0, FILE_ROLE):
            path = item.data(0, FILE_ROLE)
            if item.data(0, CLOUD_ROLE):
                menu.addAction("Download from iCloud", lambda: self._open_placeholder(item))
            else:
                menu.addAction("Open", lambda: self._open_placeholder(item))
                menu.addAction("Rename…", lambda: self.rename_path(path))
            menu.addAction(reveal_label(), lambda: self.reveal(path))
            menu.addSeparator()
            menu.addAction(f"Move to {BIN}", lambda: self.trash_paths([path]))
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        if item is not None and item.data(0, Qt.UserRole) is None:
            # A section header: offer to clear that section.
            section = item
            ids = [section.child(i).data(0, Qt.UserRole) for i in range(section.childCount())]
            kind = {id(self.analyses_section): "Analyses", id(self.models_section): "Models",
                    id(self.material_section): "Logs", id(self.unsaved_section): "Unsaved",
                    id(self.elsewhere_section): "Other Files",
                    id(self.compare_section): "Comparisons"}.get(id(section), "Items")
            ids = [i for i in ids if i is not None]          # not the "not open" rows
            if ids:
                menu.addAction(f"Close All {kind}", lambda: self.remove_documents(ids))
        else:
            if item is not None and not item.isSelected():
                self.tree.clearSelection()
                self.tree.setCurrentItem(item)
            ids = self.selected_ids() if item is not None else []
            if not ids:
                if in_folder:
                    menu.addAction("New Folder…", lambda: self.new_folder())
                    menu.addSeparator()
                if self.space == MODEL:
                    menu.addAction("New Petri Net", self.action_new_petri)
                    menu.addAction("Open Net…", self.action_open)
                else:
                    menu.addAction("New Analysis", lambda: self.action_new_workflow(None))
                    menu.addAction("Open Log…", self.action_open)
                    menu.addAction("New Log from Notation…", self.action_notation)
            elif len(ids) == 1 and isinstance(self._document(ids[0]), ComparisonDocument):
                menu.addAction("Export Figures as CSV…", self.export_selected)
                menu.addSeparator()
                menu.addAction("Close", self.remove_selected)
            elif len(ids) == 1:
                document = self._document(ids[0])
                menu.addAction("Rename…", self.rename_selected)
                if isinstance(document, LogDocument) and len(self.logs()) > 1:
                    menu.addAction("Compare with…", lambda d=document: self.action_compare([d]))
                if isinstance(document, CpnDocument):
                    menu.addAction("Save", self.pages[document.id].save)
                    label_text = "Save As…"
                elif isinstance(document, LogDocument):
                    menu.addAction("Filter…", self.pages[document.id].filter_log)
                    label_text = "Export Log (XES or CSV)…"
                elif isinstance(document, TransitionSystemDocument):
                    label_text = "Export as Text…"
                else:
                    if in_folder and not document.path:
                        menu.addAction("Keep in Folder", lambda d=document: self.keep_model(d))
                    label_text = "Export Model as PNML…"
                menu.addAction(label_text, self.export_selected)
                if document.path and not document.missing:
                    menu.addAction(reveal_label(), lambda p=document.path: self.reveal(p))
                menu.addSeparator()
                menu.addAction("Close", self.remove_selected)
                if document.path and not document.missing:
                    menu.addAction(f"Move to {BIN}", self.trash_selected)
            else:
                chosen = [self._document(i) for i in ids]
                if all(isinstance(d, LogDocument) for d in chosen):
                    menu.addAction(f"Compare {len(ids)} Logs", lambda: self.compare(chosen))
                    menu.addSeparator()
                menu.addAction(f"Close {len(ids)} Items", self.remove_selected)
                if all(d.path and not d.missing for d in chosen):
                    menu.addAction(f"Move {len(ids)} Items to {BIN}", self.trash_selected)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    # -- session and recent files -----------------------------------------------------
    def _session_entries(self) -> list[dict]:
        entries = []
        for document in self.documents:
            if document.path:
                entry = {"path": document.path}
                entry.update(self.open_options.get(document.id, {}))
                entries.append(entry)
        # Files of a reopened workspace still loading in the background count as open.
        entries += list(self._restore_paths.values())
        return entries

    def _save_session(self) -> None:
        """Remember what is open: in the workspace folder, or in the app's settings."""
        if self._restoring:
            return
        if self.workspace is not None:
            # Written even in tests (persist=False): it lives in the folder, not in
            # your settings.
            self.workspace.save_state(self._session_entries(), self._selected_path())
            if self.persist:
                self.settings.setValue("workspace/current", str(self.workspace.folder))
        elif self.persist:
            self.settings.setValue("session/files", json.dumps(self._session_entries()))

    def _selected_path(self) -> str | None:
        ids = self.selected_ids()
        if len(ids) == 1:
            return self._document(ids[0]).path
        return None

    def restore_session(self) -> None:
        """Reopen the files that were open when the app was last closed."""
        if not self.settings.value("session/restore", True, type=bool):
            return
        folder = self.settings.value("workspace/current", "") or ""
        if folder and Path(folder).is_dir():
            self.open_workspace(folder)
            return
        try:
            entries = json.loads(self.settings.value("session/files", "[]"))
        except (TypeError, ValueError):
            entries = []
        missing = []
        self._restoring = True
        try:
            for entry in entries:
                path = entry.get("path", "")
                if not Path(path).exists():
                    missing.append(Path(path).name)
                    continue
                self.open_path(path, csv_mapping=entry.get("csv_mapping"))
        finally:
            self._restoring = False
        self._save_session()
        if missing:
            self.statusBar().showMessage("Could not find: " + ", ".join(missing), 10000)

    def _remember_recent(self, path: str) -> None:
        if not self.persist or not path:
            return
        recent = [p for p in self._recent() if Path(p) != Path(path)]
        recent.insert(0, path)
        self.settings.setValue("recent", json.dumps(recent[:self.RECENT_LIMIT]))

    def _forget_recent(self, path: str) -> None:
        if self.persist and path:
            recent = [p for p in self._recent() if Path(p) != Path(path)]
            self.settings.setValue("recent", json.dumps(recent))

    def _recent(self) -> list[str]:
        if not self.persist:                      # tests: never your recent files
            return []
        try:
            return list(json.loads(self.settings.value("recent", "[]")))
        except (TypeError, ValueError):
            return []

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = [p for p in self._recent() if Path(p).exists()]
        if not recent:
            action = self.recent_menu.addAction("No Recent Files")
            action.setEnabled(False)
            return
        for path in recent:
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_files([p])) \
                .setToolTip(path)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Menu", lambda: self.settings.setValue("recent", "[]"))

    # ---------------------------------------------------------------- opening
    def action_open(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open", dialog_folder(),
            "Supported files (*.xes *.gz *.csv *.pnml *.cpn *.cpnflow *.zip);;Event logs (*.xes *.gz *.csv);;"
            "Petri nets (*.pnml);;CPN Tools models (*.cpn);;Analyses (*.cpnflow);;"
            "Exported experiments (*.zip);;All files (*)")
        self.open_files(paths)

    def action_open_cpn(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open coloured Petri net", dialog_folder(),
                                              "CPN Tools models (*.cpn)")
        if path:
            self.open_files([path])

    def action_notation(self) -> None:
        dialog = NotationDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.add_document(LogDocument(dialog.log(), notation=dialog.text()))

    # -- Mine: files and typed logs land as boxes -----------------------------------------
    def analysis_for(self, name: str) -> WorkflowPage:
        """The current analysis, or a new one called ``name`` when none is open."""
        page = self.current_page()
        if isinstance(page, WorkflowPage) and not self.in_learn:
            return page
        from ...flow.workflow import Workflow
        workflow = Workflow(self._unique_name(name or "Untitled"), self.library())
        self.add_document(WorkflowDocument(workflow))
        return self.current_page()

    def action_open_log_box(self) -> None:
        """Mine's *Open log…*: the log as an Open log box on the current analysis."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open log", dialog_folder(),
            "Event logs (*.xes *.xes.gz *.gz *.csv *.txt);;All files (*)")
        if paths:
            self.boxes_for_files(paths)

    def boxes_for_files(self, paths: list[str]) -> list:
        """Input boxes for ``paths`` on the current analysis (or a new one named
        after the first file).  Returns the nodes."""
        from .workspace import file_stem
        page = self.analysis_for(file_stem(Path(paths[0]).name))
        nodes = page.add_files(paths)
        self._select_document(page.document)
        return nodes

    def action_notation_box(self) -> None:
        """Mine's *Log from notation…*: a Typed log box with the text."""
        from ..flow.picker import input_box_for
        dialog = NotationDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        log = dialog.log()
        page = self.analysis_for(log.name)
        page.add_box_in_view(input_box_for(self.library(), "typed"),
                             {"text": dialog.text(), "name": log.name})
        self._select_document(page.document)

    def open_files(self, paths: list[str], folder: str | None = None) -> None:
        """Open files the user chose (Open…, Open Recent, a drop, the command line).

        With a folder open, files from elsewhere are first copied or moved
        into it (or ``folder``, a subfolder), or opened where they are, as the
        user says (once, or always for this folder): see :meth:`_bring_in`.
        """
        from ...flow.record import is_experiment
        experiments = [p for p in paths if p.lower().endswith(".zip") and is_experiment(p)]
        paths = [p for p in paths if p not in experiments]
        for archive in experiments:
            self.import_experiment(archive)
        outside = [p for p in paths if self.workspace is not None and Path(p).is_file()
                   and not self.workspace.contains(p)]
        for path in paths:
            if path not in outside:
                self.open_path(path)
        if outside:
            for path in self._bring_in(outside, folder):
                self.open_path(path)

    def import_experiment(self, archive: str) -> None:
        """An exported experiment (a zip from ⋯ ▸ Export experiment): unpack it
        into a folder of its own, work in that folder, and open the analysis,
        which runs and is checked against its record."""
        import zipfile
        from ...flow.record import import_experiment
        name = Path(archive).name
        into = EXPERIMENT_FOLDER_CHOOSER(self, f"Where should the folder for {name} go?", str(Path(archive).parent))
        if not into:
            return
        try:
            flow_file = import_experiment(archive, into)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            QMessageBox.warning(self, "Could not import the experiment", str(error))
            return
        folder = flow_file.parent
        if not self.open_workspace(str(folder)):
            return
        self.open_path(str(flow_file))
        self.statusBar().showMessage(f"Unpacked {name} into the folder {folder.name} and opened its analysis: "
                                     "it runs now, and the badge says whether it reproduces the record", 12000)

    def _import_choice(self, names: list[str], allow_open: bool) -> str | None:
        """Copy, move or open in place?  The folder's choice, the app's, or the user's."""
        remembered = self.workspace.settings().get("import")
        if remembered not in IMPORT_CHOICES:
            remembered = self._setting("files/import", "ask")
        if remembered in ("copy", "move"):
            return remembered
        if remembered == "open":
            return "open" if allow_open else "copy"
        dialog = ImportDialog(names, self.workspace.name, self, allow_open=allow_open)
        if dialog.exec() != QDialog.Accepted:
            return None
        choice = dialog.choice()
        if dialog.always.isChecked():
            self.workspace.update_settings(**{"import": choice})
        return choice

    def _bring_in(self, paths: list[str], folder: str | None = None) -> list[str]:
        """Copy or move files from elsewhere into the open folder; the paths to open.

        ``folder``: the subfolder they were dropped on (then "open where it
        is" is not on offer: the drop says where they go).  A file with the
        same name already there is never overwritten silently: the user
        keeps both (``"name 2.xes"``) or replaces it (the old one goes to the Bin).
        """
        target_folder = Path(folder) if folder else self.workspace.folder
        choice = self._import_choice([Path(p).name for p in paths],
                                     allow_open=folder is None
                                     or Path(folder).resolve() == self.workspace.folder)
        if choice is None:
            return []
        if choice == "open":
            return list(paths)
        result = []
        for source in map(Path, paths):
            target = target_folder / source.name
            if target.exists():
                if _same_file(source, target):
                    result.append(str(target))
                    continue
                answer = ask_about_clash(self, target)
                if answer is None:
                    continue
                if answer == "keep":
                    target = unique_path(target_folder, source.name)
                elif not move_to_trash(target):
                    QMessageBox.warning(self, "Could not replace", f"“{target.name}” could not "
                                        f"be moved to the {BIN}, so it was left as it is.")
                    continue
            try:
                if choice == "move":
                    shutil.move(str(source), str(target))
                    self._forget_recent(str(source))
                else:
                    shutil.copy2(source, target)          # keeps the file's dates
            except OSError as error:
                QMessageBox.warning(self, "Could not add the file",
                                    f"{source.name}\n\n{error.strerror or error}")
                continue
            result.append(str(target))
        if result:
            verb = "Moved" if choice == "move" else "Copied"
            where = target_folder.name
            self.statusBar().showMessage(
                f"{verb} {Path(result[0]).name if len(result) == 1 else f'{len(result)} files'} "
                f"into {where}", 8000)
        self._rescan_workspace()
        return result

    def open_path(self, path: str, csv_mapping: dict | None = None) -> None:
        lower = path.lower()
        name = Path(path).name
        if lower.endswith(".icloud") and Path(path).name.startswith("."):
            self._download_from_icloud(Path(path).with_name(name[1:-len(".icloud")]), Path(path))
            return
        size = _signature(path)
        try:
            if lower.endswith(".cpnflow"):
                from ...flow.record import load as load_workflow
                workflow, record = load_workflow(path, self.library())
                self.add_document(WorkflowDocument(workflow, path=path, record=record))
                self.statusBar().showMessage(f"Opened {name}: click a box to see its result and how it "
                                             "got there", 10000)
            elif lower.endswith(".pnml"):
                self.add_document(CpnDocument(self._read_net(path), path=path))
                self.statusBar().showMessage(f"Opened {name} — edit it, play the token game, "
                                             "or see the Analysis tab for soundness and more",
                                             10000)
            elif lower.endswith(".cpn"):
                self.statusBar().showMessage(f"Reading {name}…")
                net = self._read_net(path)
                self.add_document(CpnDocument(net, path=path))
                problems = len(net.errors)
                self.statusBar().showMessage(
                    f"Opened {name}" + (f" — {problems} problem(s), see the Problems tab"
                                        if problems else ""), 10000)
            elif lower.endswith(".txt") and file_kind(Path(path)) in ("log", "ts"):
                self.add_document(self._read_text_file(path))
            elif file_kind(Path(path)) == "ocel":
                # An object-centric log has no page of its own: it is a box on an analysis.
                self.boxes_for_files([path])
            elif lower.endswith(".csv"):
                if csv_mapping is not None:          # reopening: reuse the saved mapping
                    mapping = ColumnMapping(**csv_mapping)
                else:
                    dialog = CsvDialog(path, self)
                    if dialog.exec() != QDialog.Accepted:
                        self._opening.discard(self._key(path))
                        self._rebuild_sidebar()
                        return
                    mapping = dialog.mapping()
                log = read_csv(path, mapping)
                self.add_document(LogDocument(log, path=path),
                                  options={"csv_mapping": asdict(mapping)})
            elif lower.endswith((".xes", ".xes.gz", ".gz")):
                self.statusBar().showMessage(f"Reading {name}…")
                folder = self._folder_switches

                def loaded(log) -> None:
                    if folder != self._folder_switches:
                        # The folder was switched or closed while this was
                        # loading: it belongs to the folder that was left.
                        self._opening.discard(self._key(path))
                        return
                    self.add_document(LogDocument(log, path=path))
                    self.statusBar().showMessage(f"Opened {name}: {len(log):,} cases", 6000)

                run_in_background(lambda: read_xes(path), loaded,
                                  lambda message: self._could_not_open(path, message, size))
            else:
                QMessageBox.information(self, "Unsupported file",
                                        f"{name}: open .xes, .csv, .pnml or .cpn files (or a "
                                        "log in textbook notation named log.txt or "
                                        "….log.txt, a transition system named ts.txt or "
                                        "….ts.txt).")
        except Exception as error:  # noqa: BLE001 - surface any import problem
            self._could_not_open(path, str(error), size)

    def _could_not_open(self, path: str, message: str, before: tuple | None) -> None:
        self._opening.discard(self._key(path))
        self._rebuild_sidebar()
        name = Path(path).name
        if before is not None and _signature(path) != before:
            # A big file still being copied in (from Finder, a download, iCloud):
            # what was read was only part of it.
            self.statusBar().showMessage(f"{name} is still being copied — open it again when "
                                         "it has finished", 10000)
            return
        QMessageBox.warning(self, "Could not open file", f"{name}\n\n{message}")

    @staticmethod
    def _read_text_file(path: str):
        """A log in textbook notation (``log.txt``) or a transition system (``ts.txt``)."""
        from ...mining.transition_system import parse_transition_system
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        stem = display_name(Path(path).name)
        if Path(path).name.lower() in ("log.txt", "ts.txt"):
            stem = Path(path).parent.name or stem     # "log" says little; its folder more
        if file_kind(Path(path)) == "ts":
            return TransitionSystemDocument(parse_transition_system(text, stem), path=path)
        return LogDocument(EventLog.from_simple_log(parse_simple_log(text), stem), path=path,
                           notation=text.strip())

    @staticmethod
    def _read_net(path: str):
        """The net in a .pnml or .cpn file, as the editor edits it."""
        if path.lower().endswith(".pnml"):
            from ...model.plain import from_petri_net
            petri = read_pnml(path)
            petri.name = _file_stem(Path(path))
            return from_petri_net(petri)
        from ...io.cpn_reader import read_cpn
        return read_cpn(path)

    # ---------------------------------------------------------------- comparing
    def action_compare(self, preselected: list | None = None) -> None:
        """Compare the selected logs, or ask which logs to compare."""
        logs = self.logs()
        if len(logs) < 2:
            QMessageBox.information(self, "Compare logs", "Open at least two event logs to "
                                    "compare them.")
            return
        if preselected is None:
            chosen = [self._document(i) for i in self.selected_ids()]
            chosen = [d for d in chosen if isinstance(d, LogDocument)]
            if len(chosen) >= 2:
                self.compare(chosen)
                return
            preselected = chosen
        dialog = CompareDialog(logs, preselected, self)
        if dialog.exec() == QDialog.Accepted:
            self.compare(dialog.chosen())

    def compare(self, logs: list) -> None:
        for existing in self.documents:
            if isinstance(existing, ComparisonDocument) and \
                    [d.id for d in existing.logs] == [d.id for d in logs]:
                self.tree.setCurrentItem(self.items[existing.id])
                return
        self.add_document(ComparisonDocument(list(logs)))

    def action_new_petri(self) -> None:
        from ...model.plain import new_plain_net
        self.add_document(CpnDocument(new_plain_net(self._unique_cpn_name())))
        self.statusBar().showMessage("New Petri net: pick Place or Transition and click on the "
                                     "canvas; drag from one node to another to connect them",
                                     10000)

    def open_example_net(self, net) -> None:
        from ...model.plain import from_petri_net
        self.add_document(CpnDocument(from_petri_net(net)))

    def edit_petri_net(self, net, origin: str = "", source=None) -> None:
        """*Open a copy in Model*: a discovered or imported Petri net on the net
        canvas, as a copy named "… (copy)".  The original (a box's result, a
        model page) is never changed.  ``origin`` says where it came from
        ("from Inductive Miner in Discover and check") and ``source`` is the
        document to go back to."""
        from ...model.plain import from_petri_net
        editable = from_petri_net(net)
        editable.name = f"{net.name} (copy)"
        document = CpnDocument(editable, origin=origin)
        document.opened_from = source
        self.add_document(document)
        self.statusBar().showMessage(f"A copy of {net.name}: change it here; the original stays as it is",
                                     8000)

    def mine_log(self, log, source) -> None:
        """*Mine a simulated log ›*: the log becomes a file in the folder, next
        to the model it came from, and opens in Mine as a new analysis with an
        Open log box reading it.  Without a folder it opens as a log page."""
        from ...flow.workflow import Workflow
        from ..flow.picker import input_box_for
        if self.workspace is None:
            self.add_document(LogDocument(log), near=getattr(source, "path", None))
            return
        near = getattr(source, "path", None)
        folder = Path(near).resolve().parent if near and self.workspace.contains(near) else self.workspace.folder
        target = unique_path(folder, safe_file_name(log.name) + ".xes")
        try:
            write_xes(log, str(target))
        except OSError as error:
            QMessageBox.warning(self, "Mine the simulated log", f"Could not save the log:\n\n{error}")
            return
        self._created.add(self._key(target))
        library = self.library()
        workflow = Workflow(self._unique_name(log.name), library)
        workflow.add(input_box_for(library, "log"), {"file": self.workspace.relative(target)}, (0.0, 0.0))
        self.add_document(WorkflowDocument(workflow))
        self.statusBar().showMessage(f"Saved {target.name} into the folder; this analysis reads it", 8000)

    def _log_box_for(self, workflow, document, library, position=(0.0, 0.0)):
        """A box that gives ``document``'s log in ``workflow``: Open log for a
        file, Typed log for a log written in notation; a log that is neither
        is written into the folder first.  None (with a message) when it
        cannot be done."""
        from ..flow.picker import input_box_for
        path = document.path if document.path and not document.missing else None
        if path is None and self.workspace is not None:
            target = unique_path(self.workspace.folder, safe_file_name(document.name) + ".xes")
            try:
                write_xes(document.log, str(target))
            except OSError as error:
                self.statusBar().showMessage(f"Could not save the log: {error}", 8000)
                return None
            self._created.add(self._key(target))
            path = str(target)
        if path is not None:
            relative = self.workspace.relative(path) if self.workspace is not None and \
                self.workspace.contains(path) else path
            return workflow.add(input_box_for(library, "log"), {"file": relative}, position)
        if document.notation:
            return workflow.add(input_box_for(library, "typed"),
                                {"text": document.notation, "name": document.name}, position)
        self.statusBar().showMessage("Export the log first (Export…): the analysis reads it from a file", 8000)
        return None

    def discover_in_analysis(self, document, key: str) -> None:
        """The log page's Discover: a new analysis in Mine with this log and the
        chosen miner, connected and run, the miner's Result open."""
        from ...flow.workflow import Workflow
        library = self.library()

        def spec_id(function: str) -> str | None:
            return next((s.id for s in library.specs.values() if s.id.endswith("." + function)), None)

        miner = spec_id(key)
        if miner is None:
            self.statusBar().showMessage(f"The {key} box is not available", 8000)
            return
        workflow = Workflow(self._unique_name(f"Discover {document.name}"), library)
        log = self._log_box_for(workflow, document, library)
        if log is None:
            return
        node = workflow.add(miner, None, (300.0, 0.0))
        workflow.connect(log, node)
        last = node
        if key == "classical_states":                      # regions: the net is one box further
            to_net = spec_id("regions_to_net")
            if to_net is not None:
                last = workflow.add(to_net, None, (600.0, 0.0))
                workflow.connect(node, last)
        self.add_document(WorkflowDocument(workflow))
        page = self.current_page()
        if isinstance(page, WorkflowPage):
            page.select(last.id, 0)
        self.statusBar().showMessage(f"{workflow.name}: click the miner for its result, how it was derived, "
                                     "and the code", 10000)

    def check_against_log(self, document) -> None:
        """*Check against a log ›* on a Petri net: in Mine, a new analysis with
        Open net (this net's file), Open log (a file you choose) and Check fit,
        connected and run."""
        from ...flow.workflow import Workflow
        from ..flow.picker import input_box_for
        if not document.path and isinstance(document, ModelDocument) and self.workspace is not None:
            self.keep_model(document)                  # a discovered model: kept in the folder first
        if not document.path or document.missing:
            self.statusBar().showMessage("Save the net first (it has no file yet): the analysis reads "
                                         "it from its file", 8000)
            return
        start = str(self.workspace.folder) if self.workspace is not None else dialog_folder()
        path, _ = QFileDialog.getOpenFileName(self, "Check against a log", start,
                                              "Event logs (*.xes *.xes.gz *.gz *.csv *.txt);;All files (*)")
        if not path:
            return
        library = self.library()
        check = next((s.id for s in library.specs.values() if s.id.endswith(".check_fit")), None)
        if check is None:
            self.statusBar().showMessage("The Check fit box is not available", 8000)
            return

        def file_setting(p: str) -> str:
            return self.workspace.relative(p) if self.workspace is not None and self.workspace.contains(p) else p

        workflow = Workflow(self._unique_name(f"{document.name} vs {file_stem(Path(path).name)}"), library)
        net = workflow.add(input_box_for(library, file_kind(Path(document.path)) or "petri"),
                           {"file": file_setting(document.path)}, (0.0, 0.0))
        log = workflow.add(input_box_for(library, "log"), {"file": file_setting(path)}, (0.0, 140.0))
        fit = workflow.add(check, None, (300.0, 70.0))
        workflow.connect(net, fit)
        workflow.connect(log, fit)
        self.add_document(WorkflowDocument(workflow))
        self.statusBar().showMessage(f"{document.name} against {Path(path).name}: click Check fit for the "
                                     "scores and how they were computed", 10000)

    def library(self):
        """The boxes available in this window (the folder's boxes/ included)."""
        from ...flow.library import library_for
        folder = self.workspace.folder if self.workspace is not None else None
        signature = self._boxes_files(folder)
        if self._library is None or signature != self._boxes_signature:
            self._library = library_for(folder)
            self._boxes_signature = signature
        return self._library

    @staticmethod
    def _boxes_files(folder) -> tuple:
        if folder is None:
            return ()
        boxes = Path(folder) / "boxes"
        if not boxes.is_dir():
            return ()
        try:
            return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in boxes.glob("*.py")))
        except OSError:
            return ()

    def _is_box_file(self, path: str) -> bool:
        return (self.workspace is not None and path.endswith(".py")
                and Path(path).parent == Path(self.workspace.folder) / "boxes")

    def _reload_boxes(self) -> None:
        """The folder's boxes/ changed (a file saved, added or removed): open
        workflow pages get the new boxes and run again; the message names the file."""
        if self.workspace is None:
            return
        signature = self._boxes_files(self.workspace.folder)
        if signature == self._boxes_signature:
            return
        before, after = dict(self._boxes_signature), dict(signature)
        changed = sorted([n for n, t in after.items() if before.get(n) != t] + [n for n in before if n not in after])
        self._boxes_signature = signature
        if self._library is not None:
            self._library.load_folder(Path(self.workspace.folder) / "boxes")
        for document in self.documents:
            if isinstance(document, WorkflowDocument):
                page = self.pages.get(document.id)
                if page is not None:
                    page.reload_library()
        self._watch_open_files()                     # an editor's safe save replaced the file: watch the new one
        if before and changed:
            names = ", ".join(changed[:3]) + (" …" if len(changed) > 3 else "")
            self.statusBar().showMessage(
                f"Reloaded {names}: the boxes it defines ran again" if len(changed) == 1
                else f"Reloaded the folder's boxes ({names})", 6000)

    def action_new_workflow(self, build=None) -> None:
        """A new workflow from a template (or empty), on the first log of the folder."""
        from ...flow.workflow import Workflow
        folder = self.workspace.folder if self.workspace is not None else None
        library = self.library()
        if build is None:
            workflow = Workflow(self._unique_cpn_name(), library)
        else:
            workflow = build(library, first_log(folder))
            workflow.name = self._unique_name(workflow.name)
        self.add_document(WorkflowDocument(workflow))
        self.statusBar().showMessage("New workflow: click a box to see what it gives, drag from a dot to "
                                     "connect boxes, + Add box (or a double-click on the canvas) to add one", 10000)

    def _unique_name(self, base: str) -> str:
        names = {d.name for d in self.documents}
        if self.workspace is not None:
            names |= {f.path.stem for folder in (self._tree.walk() if self._tree else []) for f in folder.files}
        name, index = base, 2
        while name in names:
            name = f"{base} {index}"
            index += 1
        return name

    def keep_workflow(self, document) -> None:
        """Save a workflow that is not in the folder yet (the Keep button)."""
        if self.workspace is None or document.path:
            self.pages[document.id].export()
            return
        target = unique_path(self.workspace.folder, safe_file_name(document.name) + ".cpnflow")
        self.pages[document.id].write_to(str(target), quiet=True)
        self.statusBar().showMessage(f"Kept as {target.name}", 8000)
        self._rebuild_sidebar()

    def action_new_cpn(self) -> None:
        from ...model.net import CPNet
        net = CPNet(self._unique_cpn_name())
        net.add_declaration("colset UNIT = unit;")
        net.add_declaration("colset INT = int;")
        net.add_page("Top")
        document = CpnDocument(net)
        self.add_document(document)
        self.statusBar().showMessage("New coloured Petri net: pick Place or Transition and "
                                     "click on the canvas to draw", 8000)

    def _unique_cpn_name(self) -> str:
        names = {d.name for d in self.documents}
        index = 1
        while f"Untitled {index}" in names:
            index += 1
        return f"Untitled {index}"

    def action_save(self) -> None:
        page = self.current_page()
        if isinstance(page, (CpnPage, WorkflowPage)):
            page.save()
        elif page is not None:
            page.export()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._flush_autosaves()
        self.notes.flush()
        if self.learn_mode.view is not None:
            self.learn_mode.view.flush()
        dirty = [d for d in self.documents if isinstance(d, (CpnDocument, WorkflowDocument)) and d.dirty]
        if dirty:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Unsaved changes")
            box.setText("Save changes to " + ", ".join(f"“{d.name}”" for d in dirty[:4])
                        + (" and others" if len(dirty) > 4 else "") + " before quitting?")
            save = box.addButton("Save", QMessageBox.AcceptRole)
            discard = box.addButton("Don’t Save", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is save:
                if not all(self.pages[d.id].save() for d in dirty):
                    event.ignore()
                    return
            elif clicked is not discard:
                event.ignore()
                return
        self._save_session()
        event.accept()
        if self._installer is not None:         # an update is ready: swap the apps now
            from . import updates
            updates.run_detached(self._installer)

    # ---------------------------------------------------------------- updates
    def check_automatically(self) -> None:
        """At launch: look for a newer version, quietly unless there is one.

        Every time the app opens, so nobody misses a release; "Skip This
        Version" in the dialog keeps a version from being offered again.
        """
        if not self.persist or not self._setting("updates/automatic", True):
            return
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = True) -> None:
        """Help ▸ Check for Updates…: compare with the latest release on GitHub.

        ``manual=False`` (the check at launch) says nothing unless there is a
        new version the user has not skipped, and nothing at all when offline.
        """
        from . import updates
        if self._checking_updates:
            return
        self._checking_updates = True
        if manual:
            self.statusBar().showMessage("Checking for updates…")

        def failed(message: str) -> None:
            self._checking_updates = False
            if manual:
                self.statusBar().clearMessage()
                QMessageBox.warning(self, "Software Update", "Could not check for updates "
                                    f"(are you online?).\n\n{message}")

        run_in_background(updates.fetch_latest,
                          lambda release: self._update_found(release, manual), failed)

    def _update_found(self, release, manual: bool) -> None:
        from ... import __version__
        from . import updates
        self._checking_updates = False
        if manual:
            self.statusBar().clearMessage()
        if release.prerelease or not updates.is_newer(release.version):
            if manual:
                QMessageBox.information(self, "Software Update", "You have the latest version "
                                        f"of OpenProcess ({__version__}).")
            return
        if not manual and self._setting("updates/skipped", "") == release.version:
            return
        if manual:
            self._show_update_dialog(release)      # asked for: the dialog with the notes
        else:
            # At launch: a bar at the top that does not stop anyone working.
            self.update_bar.offer(release, self._can_install(release), self._update_way(release)[1])

    def _update_way(self, release):
        """How this copy was installed, and how ``release`` can be put over it
        (see :func:`updates.update_way`; None: only the release page helps)."""
        from . import updates
        install = updates.how_installed()
        return install, updates.update_way(install, release)

    def _can_install(self, release) -> bool:
        return self._update_way(release)[1] is not None

    def _show_update_dialog(self, release) -> None:
        """What's new in ``release`` (and the versions before it), and what to do."""
        from . import updates
        install, way = self._update_way(release)
        dialog = updates.UpdateDialog(release, self._can_install(release), self, install, way)
        dialog.exec()
        self._update_action(release, {dialog.SKIP: "skip", dialog.PAGE: "page",
                                      dialog.INSTALL: "install"}.get(dialog.outcome))

    def _update_action(self, release, action: str | None) -> None:
        if action is None:                          # Later
            return
        self.update_bar.hide()
        if action == "skip":
            self._set_setting("updates/skipped", release.version)
            self.statusBar().showMessage(f"OpenProcess {release.version} skipped — you will be told "
                                         "about the next version", 6000)
        elif action == "page":
            QDesktopServices.openUrl(QUrl(release.page))
        elif not self._can_install(release):       # "How to Update": the dialog says how
            self._show_update_dialog(release)
        else:
            install, way = self._update_way(release)
            if way == "app":
                self._install_update(release)
            else:
                self._run_update(release, install, way)

    def _run_update(self, release, install, way: str) -> None:
        """A pip install or a checkout: run the update commands in the background,
        then offer to restart into the new version."""
        from PySide6.QtWidgets import QProgressDialog
        from . import updates
        progress = QProgressDialog(f"Updating to OpenProcess {release.version}…", None, 0, 0, self)
        progress.setWindowTitle("Software Update")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.show()

        def failed(message: str) -> None:
            progress.close()
            QMessageBox.warning(self, "Software Update", "The update did not go through.\n\n"
                                f"{message}\n\nYou can also get it from {release.page}")

        def done(output: str) -> None:
            progress.close()
            self._after_update(release, output)

        run_in_background(lambda: updates.run_update(install, release, way), done, failed)

    def _after_update(self, release, output: str) -> None:
        from . import updates
        box = QMessageBox(self)
        box.setWindowTitle("Software Update")
        box.setText(f"OpenProcess {release.version} is installed.")
        box.setInformativeText("OpenProcess starts again to use it. Your open files come back.")
        box.setDetailedText(output)
        restart = box.addButton("Restart Now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is restart and self.close():
            updates.run_detached(updates.restart_command())
            QApplication.quit()

    def _install_update(self, release) -> None:
        """Download and unpack the update in the background, then restart into it."""
        from PySide6.QtWidgets import QProgressDialog
        from . import updates
        progress = QProgressDialog(f"Downloading OpenProcess {release.version}…", "Cancel", 0, 100,
                                   self)
        progress.setWindowTitle("Software Update")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        # By default the dialog resets and hides itself at 100 %; with the
        # value set to 100 again every 0.1 s while the download is checked and
        # unpacked, it kept popping up and vanishing.  It stays up until done.
        progress.setAutoReset(False)
        progress.setAutoClose(False)
        self.update_progress = progress
        state = {"done": 0, "total": 0, "cancelled": False, "unpacking": False}
        progress.canceled.connect(lambda: state.__setitem__("cancelled", True))

        def tick() -> None:
            if state["unpacking"]:
                return
            if state["total"] and state["done"] >= state["total"]:
                # Downloaded: now checked and unpacked, which cannot be
                # stopped half-way -- a busy bar, no Cancel.
                state["unpacking"] = True
                progress.setLabelText(f"Preparing OpenProcess {release.version}…")
                progress.setCancelButton(None)
                progress.setRange(0, 0)
            else:
                progress.setValue(int(100 * state["done"] / state["total"])
                                  if state["total"] else 0)
        poll = QTimer(self)
        poll.timeout.connect(tick)
        poll.start(100)
        progress.show()

        def finish() -> None:
            poll.stop()
            progress.close()

        def failed(message: str) -> None:
            finish()
            if not state["cancelled"]:
                QMessageBox.warning(self, "Software Update", "The update could not be "
                                    f"installed.\n\n{message}\n\nYou can download it from "
                                    f"{release.page}")

        def ready(command: list[str]) -> None:
            finish()
            box = QMessageBox(self)
            box.setWindowTitle("Software Update")
            box.setText(f"OpenProcess {release.version} is ready.")
            box.setInformativeText("OpenProcess quits and starts again to finish updating. Your "
                                   "open files come back.")
            restart = box.addButton("Restart Now", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is restart:
                self._installer = command
                if self.close():
                    QApplication.quit()
                    return
                self._installer = None         # quitting was cancelled
            # Not installed: tidy up the unpacked copy.
            work = Path(command[-1]).parent
            if work.name.startswith(".openprocess-update-"):
                shutil.rmtree(work, ignore_errors=True)

        run_in_background(lambda: updates.prepare(
            release, progress=lambda done, total: state.update(done=done, total=total),
            cancelled=lambda: state["cancelled"]), ready, failed)

    # ---------------------------------------------------------------- folders
    def action_open_workspace(self) -> None:
        start = str(self.workspace.folder.parent) if self.workspace else dialog_folder()
        folder = QFileDialog.getExistingDirectory(self, "Open folder",
                                                  start or str(Path.home()))
        if folder:
            self.open_workspace(folder)

    def open_workspace(self, folder: str) -> bool:
        """Work in ``folder``: list its logs and nets, reopen what was open there.

        Documents that are not in the folder are closed first (asking about
        unsaved changes), as when switching projects in other editors; files
        of the folder that are already open stay open.
        """
        workspace = Workspace(folder)
        if not workspace.exists():
            QMessageBox.warning(self, "Open folder", f"{folder}\n\nThis folder no longer "
                                "exists.")
            self._forget_workspace(folder)
            return False
        if self.workspace is not None and self.workspace.folder == workspace.folder:
            return True
        self.notes.flush()                      # the old folder's notes, as they were
        self._save_session()                    # the old folder, as it was
        outside = [d.id for d in self.documents
                   if not (d.path and workspace.contains(d.path))]
        self._restoring = True                  # closing is not a change to remember
        try:
            if outside and not self.remove_documents(outside):
                return False
        finally:
            self._restoring = False
        self._stop_watching()
        self._folder_switches += 1

        self.workspace = workspace
        self._show_notes_of_folder()
        settings = workspace.settings()
        self._material_expanded = bool(settings.get("material_expanded", False))
        self._collapsed_folders = {str(r) for r in settings.get("collapsed_folders") or []}
        self._space_current = {MINE: None, MODEL: None}
        saved = settings.get("space")
        self._set_space_quietly(saved if saved in (MINE, MODEL) else MINE)
        set_dialog_folder(str(workspace.folder))
        self._remember_workspace(str(workspace.folder))
        self._show_workspace_header()
        for document in self.documents:        # files of this folder that stayed open
            self._update_autosave(document)
        self._rescan_workspace(force=True)

        state = workspace.load_state()
        entries = [e for e in state["open"] if Path(e["path"]).exists()]
        self._restore_paths = {self._key(e["path"]): e for e in entries}
        selected = state["selected"]
        self._restore_selected = (self._key(selected) if selected and
                                  self._key(selected) in self._restore_paths else None)
        self._restoring = True
        try:
            for entry in entries:
                self.open_path(entry["path"], csv_mapping=entry.get("csv_mapping"))
        finally:
            self._restoring = False
        self._save_session()
        if not self.documents:
            self.content.setCurrentIndex(0)
        self._set_title(self._current_document())
        count = len(self.placeholders) + sum(1 for d in self.documents if d.path)
        self.statusBar().showMessage(
            f"Opened {workspace.name}: "
            f"{count} file{'s' if count != 1 else ''} to work with", 6000)
        return True

    def close_workspace(self) -> bool:
        """Close every document and go back to working with loose files."""
        if self.workspace is None:
            return True
        self.notes.flush()
        self._save_session()
        self._restoring = True
        try:
            if not self.remove_documents([d.id for d in self.documents]):
                return False
        finally:
            self._restoring = False
        self._stop_watching()
        self._folder_switches += 1
        name, self.workspace = self.workspace.name, None
        self._show_notes_of_folder()
        self._tree, self._tree_signature = None, ()
        set_dialog_folder(None)
        self.statusBar().showMessage(f"Closed {name}", 5000)
        if self.persist:
            self.settings.remove("workspace/current")
        self._show_workspace_header()
        self._rebuild_sidebar()
        self.content.setCurrentIndex(0)
        self._set_title(None)
        self._save_session()
        return True

    def _rescan_workspace(self, force: bool = False) -> None:
        """Bring the sidebar in line with the folder's files (and watch its subfolders)."""
        if self.workspace is None:
            return
        if not self.workspace.exists():
            self.statusBar().showMessage(f"The folder {self.workspace.folder} is "
                                         "gone (moved or deleted?)", 10000)
            return
        self._tree = self.workspace.tree()
        self._reload_boxes()
        signature = tuple((f.relative, tuple((x.relative, x.in_cloud) for x in f.files))
                          for f in self._tree.walk())
        missing_changed = self._check_missing()
        self._open_downloaded()
        if force or signature != self._tree_signature or missing_changed:
            self._tree_signature = signature
            self._rebuild_sidebar()
        # Watch the folder and its subfolders (new subfolders included).
        watched = set(self.watcher.directories())
        current = {str(d) for d in self.workspace.directories(self._tree)}
        if watched - current:
            self.watcher.removePaths(list(watched - current))
        if current - watched:
            self.watcher.addPaths(sorted(current - watched))
            if watched:
                # A file copied into a brand-new subfolder before it was watched
                # would be missed: look once more.
                self._rescan_timer.start()
        self._watch_open_files()                     # the folder's box files among them

    def _stop_watching(self) -> None:
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self._tree, self._tree_signature = None, ()

    # -- the sidebar's rows ---------------------------------------------------------
    def _sections(self) -> list[QTreeWidgetItem]:
        return [self.unsaved_section, self.analyses_section, self.models_section,
                self.material_section, self.elsewhere_section, self.compare_section]

    def _row_id(self, item: QTreeWidgetItem | None):
        """What a row stands for, so the same row can be found after a rebuild."""
        if item is None:
            return None
        try:
            if item.data(0, Qt.UserRole) is not None:
                return ("document", item.data(0, Qt.UserRole))
            if item.data(0, FILE_ROLE):
                return ("file", self._key(item.data(0, FILE_ROLE)))
            if item.data(0, FOLDER_ROLE):
                return ("folder", self._key(item.data(0, FOLDER_ROLE)))
        except RuntimeError:
            return None
        return None

    def _row_path(self, item: QTreeWidgetItem) -> str | None:
        """The file or folder a row stands for (for dragging it, trashing it, …)."""
        if item.data(0, Qt.UserRole) is not None:
            document = self._document(item.data(0, Qt.UserRole))
            return document.path if document.path and not document.missing else None
        return item.data(0, FOLDER_ROLE) or (item.data(0, FILE_ROLE)
                                              if not item.data(0, CLOUD_ROLE) else None)

    def _listed_files(self) -> list:
        """The folder's files that are not open (iCloud-only ones included)."""
        if self._tree is None:
            return []
        open_paths = {self._key(d.path) for d in self.documents if d.path}
        return [f for folder in self._tree.walk() for f in folder.files
                if self._key(f.path) not in open_paths]

    def _rebuild_sidebar(self) -> None:
        """Lay out the sidebar for the current space from the open documents
        and the folder's files.

        Called after anything that changes them.  The current row, the
        selection and the scroll position stay as they were, and the page
        shown does not change.
        """
        tree = self.tree
        current = self._row_id(tree.currentItem())
        selected = {self._row_id(item) for item in tree.selectedItems()}
        scroll_position = tree.verticalScrollBar().value()
        tree.blockSignals(True)
        tree._target = None
        try:
            while tree.topLevelItemCount():
                tree.takeTopLevelItem(0)
            for section in self._sections():
                section.takeChildren()
            self.items, self.placeholders, self.folder_items, self._folder_rows = {}, {}, {}, []
            tree.setRootIsDecorated(True)
            self._fill_space_view()
            for section in self._sections():
                section.setExpanded(section is not self.material_section or self._material_expanded
                                    or self.workspace is None)
                section.setHidden(section.childCount() == 0)
            self.more_row.setHidden(not (self._tree is not None and self._tree.truncated))
            empty = self.workspace is not None and not self.items and not self.placeholders \
                and not self.folder_items
            self.empty_hint.setText(0, "No analyses or logs yet" if self.space == MINE else "No models yet")
            self.empty_hint.setHidden(not empty)
            for item in self._folder_rows:              # captions as they were left
                item.setExpanded(self._folder_relative(item) not in self._collapsed_folders)
            # Put back the current row and the selection.  A current row inside
            # a folded caption shows, and the caption stays open from then on.
            rows = {self._row_id(item): item for item in self._all_rows()}
            if current in rows:
                item = rows[current]
                parent = item.parent()
                while parent is not None:
                    if not parent.isExpanded():
                        parent.setExpanded(True)
                        self._remember_folded(parent, False)
                    parent = parent.parent()
                tree.setCurrentItem(item)
            for key in selected:
                if key in rows:
                    rows[key].setSelected(True)
            tree.verticalScrollBar().setValue(scroll_position)
        finally:
            tree.blockSignals(False)
        self._watch_open_files()
        self._update_welcome()

    def _all_rows(self) -> list[QTreeWidgetItem]:
        rows = []
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value() is not None:
            rows.append(iterator.value())
            iterator += 1
        return rows

    def _document_row(self, document) -> QTreeWidgetItem:
        kind = ("compare" if isinstance(document, ComparisonDocument) else
                "ts" if isinstance(document, TransitionSystemDocument) else
                "workflow" if isinstance(document, WorkflowDocument) else
                "log" if isinstance(document, LogDocument) else
                "cpn" if isinstance(document, CpnDocument)
                and not getattr(document.net, "plain", False) else "model")
        item = QTreeWidgetItem([""])
        item.setIcon(0, _icon(kind))
        item.setData(0, Qt.UserRole, document.id)
        self._style_document_row(item, document)
        self.items[document.id] = item
        return item

    def _file_row(self, file, text: str) -> QTreeWidgetItem:
        """A file of the folder that is not open: lighter, opens with a click."""
        kind = {"log": "log", "ocel": "log", "cpn": "cpn", "ts": "ts", "workflow": "workflow"}.get(file.kind, "model")
        key = self._key(file.path)
        if file.in_cloud:
            text += "  ☁"
        if key in self._opening or key in self._pending_downloads:
            text += "  …"
        item = QTreeWidgetItem([text])
        item.setIcon(0, _faded_icon(kind))
        item.setData(0, FILE_ROLE, str(file.path))
        item.setForeground(0, QColor(style.tokens().text_muted))
        if file.in_cloud:
            item.setData(0, CLOUD_ROLE, str(file.cloud_placeholder))
            item.setToolTip(0, f"{file.relative}\nIn iCloud — click to download and open it")
        else:
            item.setToolTip(0, f"{file.relative}\nNot open — click to open")
        self.placeholders[key] = item
        return item

    def _fill_space_view(self) -> None:
        """The rows of the current space.

        Mine: UNSAVED, ANALYSES (the workflows), LOGS (the folder's event
        logs and transition systems, folded away by default).  Model: UNSAVED,
        MODELS (Petri nets and coloured nets).  Both end with OTHER FILES (open
        files from outside the folder) and COMPARISONS.  Inside a section the
        list is flat: the folder's own files first, then the files of each
        subfolder under a caption naming it ("Week 5 / Part 1"), the same
        way in both spaces, so nothing is nested and nothing moves between
        them.  Exercises are not listed: they are Learn's (the switcher opens
        the folder's pack).
        """
        tree = self.tree
        space = self.space
        in_folder = self.workspace is not None
        main_section = self.analyses_section if space == MINE else self.models_section
        tree.addTopLevelItem(self.unsaved_section)
        # Every section goes in the tree (an empty one is hidden), so a section
        # the space does not use is hidden rather than merely absent.
        for section in (self.analyses_section, self.models_section, self.material_section):
            tree.addTopLevelItem(section)
        main_kinds = MAIN_KINDS[space]
        self._fill_tree_section(main_section, main_kinds,
                                [d for d in self.documents if is_main(d, space)], empty_folders=True)
        if space == MINE:
            material_kinds = tuple(k for k in FILE_KINDS[space] if k not in main_kinds)
            material = [d for d in self.documents
                        if isinstance(d, (LogDocument, TransitionSystemDocument))]
            count = self._fill_tree_section(self.material_section, material_kinds, material)
            self.material_section.setText(0, f"LOGS  ·  {count}" if in_folder else "LOGS")
        for document in self.documents:
            if isinstance(document, ComparisonDocument):
                self.compare_section.addChild(self._document_row(document))
        tree.addTopLevelItem(self.more_row)
        for section in (self.elsewhere_section, self.compare_section):
            self._sort_rows(section)
            tree.addTopLevelItem(section)
        self._sort_rows(self.unsaved_section)
        tree.addTopLevelItem(self.empty_hint)

    def _fill_tree_section(self, section: QTreeWidgetItem, kinds: tuple, documents: list,
                           empty_folders: bool = False) -> int:
        """Fill ``section`` with the folder's files of ``kinds`` that are not
        open (lighter rows) and with the open ``documents``: the folder's own
        files first, then each subfolder's under a caption naming it.  A
        document with no file goes to UNSAVED, one from outside the folder to
        OTHER FILES.  With ``empty_folders``, a subfolder with nothing in it
        at all gets a caption too (a folder just made, waiting for files; it
        shows in both spaces until it has some).  Returns how many files and
        documents the section holds."""
        count = 0
        captions: dict[str, QTreeWidgetItem] = {}
        by_relative = {f.relative: f for f in (self._tree.walk() if self._tree else [])}
        # An exercise's materials belong to Learn: they are not listed here.
        exercises = tuple(rel + "/" for rel, folder in by_relative.items() if folder.exercise and rel)

        def caption(relative: str) -> QTreeWidgetItem:
            if relative not in captions:
                folder = by_relative.get(relative)
                item = self._caption_row(relative, folder.path if folder is not None
                                         else self.workspace.folder / relative)
                section.addChild(item)
                captions[relative] = item
                self._folder_rows.append(item)
                self.folder_items.setdefault(relative, item)
            return captions[relative]

        for file in self._listed_files():
            if file.kind not in kinds or file.relative.startswith(exercises):
                continue
            item = self._file_row(file, display_name(file.path.name))
            above = file.relative.rpartition("/")[0]
            (caption(above) if above else section).addChild(item)
            count += 1
        for document in documents:
            item = self._document_row(document)
            if not document.path:
                self.unsaved_section.addChild(item)
            elif self.workspace is None:
                section.addChild(item)
                count += 1
            elif not self.workspace.contains(document.path):
                self.elsewhere_section.addChild(item)
            else:
                above = self.workspace.relative(document.path).rpartition("/")[0]
                (caption(above) if above else section).addChild(item)
                count += 1
        if empty_folders:
            for relative, folder in by_relative.items():
                if relative and not folder.exercise and relative not in captions \
                        and not relative.startswith(exercises) and _empty_on_disk(folder.path):
                    caption(relative)
        for item in (section, *captions.values()):
            self._sort_rows(item)
        return count

    def _caption_row(self, relative: str, path: Path) -> QTreeWidgetItem:
        """A subfolder's caption ("Week 5 / Part 1"): the rows under it are its
        files.  Not a folder to open, but one to fold (a click), and it takes
        a drop and a right-click (New Folder…, Rename…, Show in Finder, Move
        to Bin)."""
        item = QTreeWidgetItem([relative.replace("/", "  /  ")])
        item.setFlags(Qt.ItemIsEnabled)
        item.setData(0, FOLDER_ROLE, str(path))
        item.setData(0, GROUP_ROLE, True)
        item.setFont(0, theme.ui_font(11))
        item.setForeground(0, QColor(style.tokens().text_muted))
        item.setToolTip(0, f"{relative}\nA subfolder: the files in it are listed under it. "
                           "Click to fold or unfold them.")
        return item

    def _folder_relative(self, item: QTreeWidgetItem) -> str:
        """A caption's subfolder, relative to the open folder (the key it is remembered by)."""
        path = item.data(0, FOLDER_ROLE) or ""
        return self.workspace.relative(path) if self.workspace is not None else path

    def _remember_folded(self, item: QTreeWidgetItem, folded: bool) -> None:
        """Keep a caption's fold in the folder's settings, and fold its twin in
        the other section the same way (Mine lists a subfolder under both
        ANALYSES and LOGS)."""
        relative = self._folder_relative(item)
        if folded:
            self._collapsed_folders.add(relative)
        else:
            self._collapsed_folders.discard(relative)
        if self.workspace is not None:
            self.workspace.update_settings(collapsed_folders=sorted(self._collapsed_folders) or None)
        for twin in self._folder_rows:
            if twin is not item and self._folder_relative(twin) == relative \
                    and twin.isExpanded() == folded:
                self.tree.blockSignals(True)
                try:
                    twin.setExpanded(not folded)
                finally:
                    self.tree.blockSignals(False)

    @staticmethod
    def _sort_key(item: QTreeWidgetItem):
        """Files first, by name ignoring case, then the subfolders' captions by path."""
        return (1 if item.data(0, GROUP_ROLE) else 0, item.text(0).casefold())

    def _sort_rows(self, parent: QTreeWidgetItem) -> None:
        children = parent.takeChildren()
        parent.addChildren(sorted(children, key=self._sort_key))

    def _folder_toggled(self, item: QTreeWidgetItem, expanded: bool) -> None:
        """The LOGS section or a subfolder's caption was folded or unfolded:
        remember it in the folder."""
        if item is self.material_section:
            self._material_expanded = expanded
            if self.workspace is not None:
                self.workspace.update_settings(material_expanded=expanded)
            return
        if item.data(0, GROUP_ROLE):
            self._remember_folded(item, not expanded)

    def _sidebar_clicked(self, item) -> None:
        if item is self.material_section or item.data(0, GROUP_ROLE):
            item.setExpanded(not item.isExpanded())     # a heading or a caption folds on a click
            return
        self._open_placeholder(item)

    def _open_placeholder(self, item) -> None:
        try:
            path = item.data(0, FILE_ROLE) if item is not None else None
            exercise = item.data(0, EXERCISE_ROLE) if item is not None else None
        except RuntimeError:                         # a row rebuilt meanwhile
            return
        if exercise and not path:
            self.open_exercise(item.data(0, FOLDER_ROLE))
            return
        if not path:
            return
        now = time.monotonic()
        # One click and a double-click (which also "activates") must not open
        # the file twice: a log loading in the background is not open yet.
        if self._key(path) == getattr(self, "_last_click_key", None) and \
                now - self._clicked_open_at < 3:
            return
        self._last_click_key = self._key(path)
        self._clicked_open_at = now
        self._restore_paths.pop(self._key(path), None)    # the user chose: it gets selected
        if item.data(0, CLOUD_ROLE):
            self._download_from_icloud(Path(path), Path(item.data(0, CLOUD_ROLE)))
            return
        if path.lower().endswith((".xes", ".gz")):
            self._opening.add(self._key(path))
            item.setText(0, item.text(0) + "  …")
            item.setToolTip(0, "Opening…")
        self.open_path(path)

    def _on_double_click(self, item, _column) -> None:
        """Rename an open document, but not the one a click has just opened."""
        recently = time.monotonic() - self._clicked_open_at
        if recently < QApplication.doubleClickInterval() / 1000 * 2:
            return
        if item.data(0, Qt.UserRole) is not None:
            self.rename_document(self._document(item.data(0, Qt.UserRole)))

    def _current_document(self):
        ids = self.selected_ids()
        return self._document(ids[0]) if len(ids) == 1 else None

    @staticmethod
    def _key(path: str | Path) -> str:
        """One spelling per file, for comparing paths."""
        return os.path.normcase(str(Path(path).resolve()))

    def _update_welcome(self) -> None:
        """The welcome page's top card: "Work in a folder", or about the open one."""
        if not hasattr(self, "welcome_inside_card"):
            return
        inside = self.workspace is not None
        self.welcome_folder_card.setHidden(inside)
        self.welcome_inside_card.setHidden(not inside)
        if inside:
            files = len(self.placeholders) + sum(
                1 for d in self.documents if d.path and self.workspace.contains(d.path)
                and not d.missing)
            self.welcome_inside_title.setText(self.workspace.name)
            if files:
                what = f"{files} logs and nets" if files != 1 \
                    else "1 file"
                self.welcome_inside_text.setText(
                    f"{what} in this folder: click one in the sidebar to open it. "
                    "New nets and logs are saved into this folder, edits are saved as you "
                    "go, and what you have open is remembered for next time.")
            else:
                self.welcome_inside_text.setText(
                    "This folder has no event logs or nets yet. Start a new net (it is saved "
                    "here), or add .xes, .csv, .pnml or .cpn files to the folder: they appear "
                    "in the sidebar by themselves.")

    def _show_workspace_header(self) -> None:
        active = self.workspace is not None
        self.workspace_caption.setHidden(not active)
        self.workspace_button.setHidden(not active)
        self.sidebar_title.setText(self.workspace.name if active else APPLICATION_NAME)
        self.sidebar_title.setToolTip(str(self.workspace.folder) if active else "")
        self.close_workspace_action.setEnabled(active)

    def _set_title(self, document) -> None:
        """"order — Week 2" in a workspace, "order — OpenProcess Studio" without one."""
        context = self.workspace.name if self.workspace else APPLICATION_NAME
        if document is None:
            self.setWindowTitle(context if self.workspace is None
                                else f"{context} — {APPLICATION_NAME}")
        else:
            self.setWindowTitle(f"{document.name} — {context}")

    # -- recent workspaces
    def _recent_workspaces(self) -> list[str]:
        if not self.persist:                      # tests: never your recent folders
            return []
        try:
            return [p for p in json.loads(self.settings.value("workspaces/recent", "[]"))
                    if isinstance(p, str)]
        except (TypeError, ValueError):
            return []

    def _remember_workspace(self, folder: str) -> None:
        if self.persist:
            recent = [p for p in self._recent_workspaces() if Path(p) != Path(folder)]
            self.settings.setValue("workspaces/recent", json.dumps([folder] + recent[:7]))
        self._refresh_welcome_recent()

    def _forget_workspace(self, folder: str) -> None:
        if self.persist:
            recent = [p for p in self._recent_workspaces() if Path(p) != Path(folder)]
            self.settings.setValue("workspaces/recent", json.dumps(recent))
        self._refresh_welcome_recent()

    def _fill_recent_workspaces_menu(self) -> None:
        menu = self.recent_workspaces_menu
        menu.clear()
        recent = [p for p in self._recent_workspaces() if Path(p).is_dir()]
        if not recent:
            menu.addAction("No Recent Folders").setEnabled(False)
            return
        for folder in recent:
            menu.addAction(Path(folder).name, lambda f=folder: self.open_workspace(f)) \
                .setToolTip(folder)
        menu.addSeparator()
        menu.addAction("Clear Menu", lambda: (self.settings.setValue("workspaces/recent", "[]"),
                                              self._refresh_welcome_recent()))

    def _refresh_welcome_recent(self) -> None:
        """Up to three recent workspaces as quick buttons on the welcome page."""
        holder = getattr(self, "welcome_recent", None)
        if holder is None:
            return
        layout = holder.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        recent = [p for p in self._recent_workspaces() if Path(p).is_dir()][:3]
        if recent:
            layout.addWidget(label("Recent:", "muted"))
        for folder in recent:
            # clicked sends "checked" (False) first: it must not land in f.
            shortcut = button(Path(folder).name,
                              lambda _checked=False, f=folder: self.open_workspace(f))
            shortcut.setToolTip(f"Open the folder {folder}")
            layout.addWidget(shortcut)

    # ---------------------------------------------------------------- app → folder
    def _materialise(self, document, near: str | None = None) -> None:
        """In a folder, a new log or net becomes a file in it straight away.

        A net is named after its file, so "Untitled 1" is ``Untitled 1.pnml``
        (or ``Untitled 2`` when that name is taken).  It goes next to ``near``
        (the log a filtered log came from, the net a simulation ran on) when
        that is in the folder, else at the top of the folder.  A discovered
        model waits for Keep, so trying algorithms does not fill the folder.
        """
        if self.workspace is None or document.path or self._restoring:
            return
        page = self.pages[document.id]
        if isinstance(document, ModelDocument):
            page.keep_button.setVisible(True)
            return
        if not isinstance(document, (LogDocument, CpnDocument, TransitionSystemDocument,
                                     WorkflowDocument)):
            return
        folder = self.workspace.folder
        if near and self.workspace.contains(near) and Path(near).parent.is_dir():
            folder = Path(near).resolve().parent
        try:
            if isinstance(document, LogDocument):
                # A log written in notation stays in notation (Edit… goes back to it).
                target = unique_path(folder, safe_file_name(document.name)
                                     + (".log.txt" if document.notation else ".xes"))
                page.write_to(str(target), quiet=True)
            elif isinstance(document, TransitionSystemDocument):
                target = unique_path(folder, safe_file_name(document.name) + ".ts.txt")
                page.write_to(str(target), quiet=True)
            elif isinstance(document, WorkflowDocument):
                target = unique_path(folder, safe_file_name(document.name) + ".cpnflow")
                page.write_to(str(target), quiet=True)
            else:
                plain = getattr(document.net, "plain", False)
                target = unique_path(folder, safe_file_name(document.name)
                                     + (".pnml" if plain else ".cpn"))
                if document.net.name != _file_stem(target):
                    document.net.name = _file_stem(target)     # named after its file
                    page.refresh_title()
                if not page._write(target, quiet=True):
                    self.statusBar().showMessage(
                        f"Could not save {document.name} into the folder "
                        f"({getattr(page, 'save_error', 'unknown error')}) — it stays "
                        "unsaved here", 10000)
                    return
        except Exception as error:  # noqa: BLE001 - the document stays, unsaved
            self.statusBar().showMessage(f"Could not save {document.name} into the folder: "
                                         f"{error}", 10000)
            return
        self._created.add(self._key(target))

    def keep_model(self, document) -> None:
        """Save a discovered model into the folder (the Keep button)."""
        if self.workspace is None or document.path:
            self.pages[document.id].export()
            return
        source = document.source_log.path if document.source_log else None
        folder = Path(source).resolve().parent if source and self.workspace.contains(source) \
            else self.workspace.folder
        target = unique_path(folder, safe_file_name(document.name) + ".pnml")
        try:
            self.pages[document.id].write_to(str(target), quiet=True)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Could not keep the model", str(error))
            return
        self.statusBar().showMessage(f"Kept as {target.name}", 8000)
        self._rebuild_sidebar()

    # -- autosave
    def _autosaves(self, document) -> bool:
        """Edits to this document are saved by themselves.

        Only nets, only in the open folder, and a ``.cpn`` only if OpenProcess wrote
        it: a model made in CPN Tools is saved (in OpenProcess's writer) only when you
        press ⌘S, after which it is OpenProcess's too.
        """
        return (self.autosave_enabled and self.workspace is not None
                and document.id not in self._autosave_failed
                and isinstance(document, (CpnDocument, WorkflowDocument)) and bool(document.path)
                and self.workspace.contains(document.path) and not document.missing
                and (not document.path.lower().endswith(".cpn")
                     or document.id in self._openprocess_files))

    def _update_autosave(self, document) -> None:
        if not isinstance(document, (CpnDocument, WorkflowDocument)):
            return
        autosave = self._autosaves(document)
        if autosave != document.autosave:
            document.autosave = autosave
            page = self.pages.get(document.id)
            if page is not None:
                page.refresh_title()
            if document.id in self.items:
                self._refresh_item(document)
        if autosave and document.dirty:
            self._schedule_autosave(document)

    def _schedule_autosave(self, document) -> None:
        """An edit: save a moment after the last one."""
        if not document.autosave or document.id in self._conflicts:
            return
        timer = self._autosave_timers.get(document.id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(AUTOSAVE_DELAY)
            timer.timeout.connect(lambda doc=document: self._autosave(doc))
            self._autosave_timers[document.id] = timer
        timer.start()

    def _autosave(self, document) -> None:
        page = self.pages.get(document.id)
        if page is None or not document.autosave or document.id in self._conflicts:
            return
        if not Path(document.path).exists():
            self._set_missing(document, True)      # do not quietly bring a deleted file back
            return
        if not page.autosave():
            # Say so once, and fall back to saving by hand (the dot comes back).
            self._autosave_failed.add(document.id)
            self._update_autosave(document)
            name = Path(document.path).name
            self.banners[document.id].show_notice(
                "unsaved", f"<b>{name} could not be saved</b> automatically: "
                f"{getattr(page, 'save_error', 'unknown error')}. Your edits are still "
                "here; save it somewhere else to keep them.",
                [("Save As…", page.export),
                 ("Try Again", lambda: page.save())])

    def _flush_autosaves(self, ids: list[int] | None = None) -> None:
        """Save now what autosave would save in a moment (switching, closing, quitting)."""
        for document in list(self.documents):
            timer = self._autosave_timers.get(document.id)
            pending = timer is not None and timer.isActive()        # a run waiting to be recorded
            if (ids is None or document.id in ids) and getattr(document, "autosave", False) \
                    and (document.dirty or pending):
                if timer is not None:
                    timer.stop()
                self._autosave(document)

    def set_autosave(self, on: bool) -> None:
        """File ▸ Autosave."""
        if on == self.autosave_enabled:
            return
        self.autosave_enabled = on
        self._set_setting("files/autosave", on)
        if self.autosave_action.isChecked() != on:
            self.autosave_action.setChecked(on)
        for document in self.documents:
            self._update_autosave(document)
        self.statusBar().showMessage("Edits to nets in the folder are saved as you go" if on
                                     else f"Autosave is off: save with {shortcut_text('Ctrl+S')}", 6000)

    # -- revert
    def _keep_original(self, document) -> None:
        """Remember a net's file as it was opened, for Revert to Saved."""
        try:
            if os.path.getsize(document.path) <= 50_000_000:
                self._originals[document.id] = Path(document.path).read_bytes()
        except OSError:
            pass

    def revert_selected(self) -> None:
        """File ▸ Revert to Saved: back to the file as it was when it was opened."""
        document = self._current_document()
        original = self._originals.get(document.id) if document is not None else None
        if document is None or original is None or not document.path:
            QMessageBox.information(self, "Revert to Saved", "Select a net opened from a file "
                                    "to go back to how it was when you opened it.")
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Revert to Saved")
        box.setText(f"Revert “{document.name}” to how it was when you opened it?")
        box.setInformativeText("The changes made since then are lost, and undo cannot bring "
                               "them back.")
        revert = box.addButton("Revert", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not revert:
            return
        from .workspace import atomic_write
        timer = self._autosave_timers.get(document.id)
        if timer is not None:
            timer.stop()
        try:
            atomic_write(document.path, lambda temporary: temporary.write_bytes(original))
        except OSError as error:
            QMessageBox.warning(self, "Revert to Saved", str(error))
            return
        self._own_writes[self._key(document.path)] = _signature(document.path)
        self._reload_document(document, f"Reverted {Path(document.path).name}")

    # ---------------------------------------------------------------- folder → app
    def _watch_open_files(self) -> None:
        """Watch the open documents' files, so a change made elsewhere is noticed."""
        wanted = {str(Path(d.path)) for d in self.documents
                  if d.path and not d.missing and os.path.exists(d.path)}
        # The folder's own boxes too: a box file saved in an editor is reloaded.
        if self.workspace is not None and (Path(self.workspace.folder) / "boxes").is_dir():
            wanted |= {str(p) for p in (Path(self.workspace.folder) / "boxes").glob("*.py")}
        current = set(self.watcher.files())
        if current - wanted:
            self.watcher.removePaths(sorted(current - wanted))
        if wanted - current:
            self.watcher.addPaths(sorted(wanted - current))

    def _file_changed(self, path: str) -> None:
        self._changed_files.add(path)
        self._file_timer.start()

    def _check_changed_files(self) -> None:
        """Open files changed (or vanished) on disk: reload, ask, or mark them missing.

        A change is acted on once the file has stopped changing (a big file
        being copied in is not read half-way), and the app's own saves are
        recognised and ignored.
        """
        pending, self._changed_files = self._changed_files, set()
        again: set[str] = set()
        if any(self._is_box_file(path) for path in pending):
            self._rescan_timer.start()               # _reload_boxes: the file's boxes, loaded again
        for path in pending:
            if self._is_box_file(path):
                continue
            key = self._key(path)
            documents = [d for d in self.documents if d.path and self._key(d.path) == key]
            if not documents:
                continue
            signature = _signature(path)
            if signature is None:
                # Gone, or being replaced by a safe save (write elsewhere, then
                # rename): look again a few times before calling it missing.
                tries = self._gone_checks.get(key, 0)
                if tries < 3:
                    self._gone_checks[key] = tries + 1
                    again.add(path)
                else:
                    self._gone_checks.pop(key, None)
                    if self.workspace is not None:
                        # Moved within the folder, perhaps: the rescan looks
                        # for it (_relocate) before calling it missing.
                        self._rescan_workspace(force=True)
                    else:
                        for document in documents:
                            self._set_missing(document, True)
                continue
            self._gone_checks.pop(key, None)
            if path not in self.watcher.files():
                self.watcher.addPath(path)          # a safe save replaced the watched file
            if self._own_writes.get(key) == signature:
                continue
            if self._unsettled.get(key) != signature:
                self._unsettled[key] = signature    # still being written? look again
                again.add(path)
                continue
            self._unsettled.pop(key, None)
            self._own_writes[key] = signature       # handled: not again for the same version
            for document in documents:
                if document.missing:
                    self._set_missing(document, False)
                self._changed_on_disk(document)
        if again:
            self._changed_files |= again
            self._file_timer.start()

    def _changed_on_disk(self, document) -> None:
        name = Path(document.path).name
        if not getattr(document, "dirty", False):
            self._reload_document(document, f"{name} changed on disk — reloaded")
            return
        # Edits here too: the user decides whose version wins.
        self._conflicts.add(document.id)
        timer = self._autosave_timers.get(document.id)
        if timer is not None:
            timer.stop()

        def reload() -> None:
            self._conflicts.discard(document.id)
            self._reload_document(document, f"Reloaded {name}")

        def keep_mine() -> None:
            self._conflicts.discard(document.id)
            self.banners[document.id].clear("changed")
            if document.autosave:
                self._autosave(document)            # ours replaces theirs now
            self.statusBar().showMessage(f"Kept your version of {name}", 6000)

        self.banners[document.id].show_notice(
            "changed", f"<b>{name} changed on disk</b> (in another app?), and it has edits "
            "here that are not saved.",
            [("Reload (lose my edits)", reload), ("Keep Mine", keep_mine)])

    def _reload_document(self, document, message: str) -> None:
        """Read the document's file again and show it afresh."""
        path = document.path
        key = self._key(path)

        def failed(error) -> None:
            tries = self._reload_attempts.get(key, 0)
            if tries < 5:                           # half-written still? try again soon
                self._reload_attempts[key] = tries + 1
                self._unsettled.pop(key, None)
                self._own_writes.pop(key, None)
                QTimer.singleShot(1000, lambda: self._file_changed(path))
                return
            self._reload_attempts.pop(key, None)
            banner = self.banners.get(document.id)
            if banner is not None:
                banner.show_notice("changed", f"<b>{Path(path).name} changed on disk</b> but "
                                   f"could not be read: {error}", [])

        def done(content) -> None:
            if document not in self.documents:
                return
            self._reload_attempts.pop(key, None)
            if isinstance(document, LogDocument):
                document.replace_log(content)
            else:
                document.net = content
                if isinstance(document, CpnDocument):
                    document.dirty = False
            self._remember_identity(document)       # another app's safe save: a new file
            self._replace_page(document)
            for other in self.documents:            # comparisons show the new log too
                if isinstance(other, ComparisonDocument) and document in other.logs:
                    self._replace_page(other)
            self.statusBar().showMessage(message, 8000)

        try:
            if isinstance(document, TransitionSystemDocument):
                content = self._read_text_file(path)
                document.ts = content.ts
                self._replace_page(document)
                self.statusBar().showMessage(message, 8000)
                return
            if isinstance(document, LogDocument):
                lower = path.lower()
                if lower.endswith(".txt"):
                    content = self._read_text_file(path)
                    document.notation = content.notation
                    done(content.log)
                elif lower.endswith(".csv"):
                    mapping = self.open_options.get(document.id, {}).get("csv_mapping")
                    done(read_csv(path, ColumnMapping(**mapping) if mapping
                                  else guess_mapping(sniff(path)[1])))
                else:
                    run_in_background(lambda: read_xes(path), done, failed)
            elif isinstance(document, CpnDocument):
                done(self._read_net(path))
            elif isinstance(document, ModelDocument):
                net = read_pnml(path)
                net.name = document.net.name
                done(net)
        except Exception as error:  # noqa: BLE001
            failed(error)

    def _log_edited(self, document) -> None:
        """A log was edited on its page: write it back to its file and show it afresh."""
        page = self.pages[document.id]
        if document.path and not document.missing:
            try:
                page.write_to(document.path, quiet=True)
            except Exception as error:  # noqa: BLE001 - the edit stays, unsaved
                QMessageBox.warning(self, "Could not save the log",
                                    f"{Path(document.path).name}\n\n{error}\n\nYour changes "
                                    "are still open here: export the log to keep them.")
            else:
                self.statusBar().showMessage(f"Saved your changes to "
                                             f"{Path(document.path).name}", 6000)
        else:
            self._materialise(document)
        self._replace_page(document)
        for other in self.documents:                # comparisons show the new log too
            if isinstance(other, ComparisonDocument) and document in other.logs:
                self._replace_page(other)
        self._rebuild_sidebar()

    def _replace_page(self, document) -> None:
        """Rebuild a document's page (after reloading it), keeping it in view."""
        shown = self.content.currentWidget() is self.holders.get(document.id)
        self._drop_page(document)
        self._make_page(document)
        self._update_autosave(document)
        if document.missing:
            self._show_missing(document)
        if shown:
            self._restore_tab(self.pages[document.id])
            self.content.setCurrentWidget(self.holders[document.id])
        self._refresh_item(document)
        self._refresh_log_choices()

    def _set_missing(self, document, missing: bool) -> None:
        """An open document's file was deleted or moved away (or came back)."""
        if document.missing == missing:
            return
        document.missing = missing
        self._update_autosave(document)
        if missing:
            self._show_missing(document)
            self.statusBar().showMessage(f"{Path(document.path).name} is no longer on disk — "
                                         "it is still open here", 10000)
        else:
            self.banners[document.id].clear("missing")
        if document.id in self.items:
            self._style_document_row(self.items[document.id], document)

    def _show_missing(self, document) -> None:
        name = Path(document.path).name
        self.banners[document.id].show_notice(
            "missing", f"<b>{name} is missing</b>: it was moved or deleted outside the app. "
            "It is still open here — save it to keep it.",
            [("Save As…", self.pages[document.id].export),
             ("Close", lambda: self.remove_documents([document.id]))])

    def _check_missing(self) -> bool:
        """Follow open files moved within the folder; mark the ones that are gone
        (or back) as missing.  True if anything changed."""
        changed = False
        for document in self.documents:
            if not document.path or isinstance(document, ComparisonDocument):
                continue
            there = os.path.exists(document.path)
            if there:
                self._remember_identity(document)
            elif self._relocate(document):
                changed = True
                continue
            if there == document.missing:
                self._set_missing(document, not there)
                if there:
                    self._file_changed(document.path)    # back, perhaps changed
                changed = True
        return changed

    @staticmethod
    def _identity(path) -> tuple | None:
        """What a move or rename keeps: the file's number on disk (device,
        inode), and its size and modification time.  The number alone is not
        enough: Linux hands a deleted file's number to the next new file, and
        that file must not be taken for the one that was open."""
        try:
            status = os.stat(path)
        except (OSError, TypeError):
            return None
        return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns

    def _remember_identity(self, document) -> None:
        identity = self._identity(document.path)
        if identity is not None:
            self._identities[document.id] = identity

    def _relocate(self, document) -> bool:
        """An open file vanished from its place: was it moved within the folder?

        A file moved or renamed in Finder (or with ``mv``) keeps its identity
        on disk, so it is looked for among the folder's files; if found, the
        document follows it -- open, unsaved edits and all -- instead of being
        marked missing while the file also shows up, unopened, in its new place.
        """
        identity = self._identities.get(document.id)
        if identity is None or self.workspace is None or self._tree is None:
            return False
        taken = {self._key(d.path) for d in self.documents if d.path}
        kind = file_kind(Path(document.path))
        for file in self.workspace.files(self._tree):
            if self._key(file.path) in taken or file.kind != kind:
                continue
            if self._identity(file.path) != identity:
                continue
            old = document.path
            self._paths_moved(Path(old), file.path)
            if document.missing:
                self._set_missing(document, False)
            self.statusBar().showMessage(
                f"{Path(old).name} was moved to {self.workspace.relative(file.path)} — it "
                "is still open", 8000)
            return True
        return False

    # -- iCloud
    def _download_from_icloud(self, path: Path, placeholder: Path) -> None:
        """Ask iCloud Drive for a file that is only in the cloud; open it when it is here."""
        key = self._key(path)
        try:
            subprocess.run(["brctl", "download", str(placeholder)], check=True,
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            self.statusBar().showMessage(f"{path.name} is in iCloud: open it in Finder to "
                                         "download it", 10000)
            self.reveal(str(placeholder.parent))
            return
        self._pending_downloads.add(key)
        self.statusBar().showMessage(f"Downloading {path.name} from iCloud…", 10000)
        self._rebuild_sidebar()

    def _open_downloaded(self) -> None:
        for key in list(self._pending_downloads):
            path = next((f.path for f in self.workspace.files(self._tree)
                         if self._key(f.path) == key), None) if self._tree else None
            if path is not None:
                self._pending_downloads.discard(key)
                QTimer.singleShot(0, lambda p=str(path): self.open_path(p))

    # ---------------------------------------------------------------- organising
    def _selected_paths(self) -> list[str]:
        paths = [self._row_path(item) for item in self.tree.selectedItems()]
        if not paths and self.tree.currentItem() is not None:
            paths = [self._row_path(self.tree.currentItem())]
        return [p for p in paths if p]

    def trash_selected(self) -> None:
        self.trash_paths(self._selected_paths())

    def trash_paths(self, paths: list[str]) -> None:
        """Move files or folders to the Bin (after asking); open documents in them close."""
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        what = f"“{Path(paths[0]).name}”" if len(paths) == 1 else f"{len(paths)} items"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"Move to {BIN}")
        box.setText(f"Move {what} to the {BIN}?")
        inside = [d for d in self.documents if d.path and any(
            self._key(d.path) == self._key(p) or self._key(d.path).startswith(
                self._key(p) + os.sep) for p in paths)]
        unsaved = [d for d in inside if getattr(d, "dirty", False)]
        box.setInformativeText(
            f"You can put it back from the {BIN}." if not unsaved else
            f"You can put it back from the {BIN}, but the edits to "
            + ", ".join(f"“{d.name}”" for d in unsaved) + " that are not saved are lost.")
        trash = box.addButton(f"Move to {BIN}", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not trash:
            return
        for document in inside:
            timer = self._autosave_timers.get(document.id)
            if timer is not None:
                timer.stop()
            if isinstance(document, CpnDocument):
                document.dirty = False          # gone to the Bin: nothing to save
        self.remove_documents([d.id for d in inside], confirm=False)
        failed = []
        for path in paths:
            if move_to_trash(path):
                self._forget_recent(path)
                self._created.discard(self._key(path))
            else:
                failed.append(Path(path).name)
        if failed:
            QMessageBox.warning(self, f"Move to {BIN}", "Could not move to the "
                                f"{BIN}: " + ", ".join(failed))
        else:
            self.statusBar().showMessage(f"Moved {what} to the {BIN}", 6000)
        self._rescan_workspace()

    def new_folder(self, inside: str | None = None) -> None:
        """Make a subfolder (in ``inside``, else at the top of the open folder)."""
        if self.workspace is None:
            return
        parent = Path(inside) if inside else self.workspace.folder
        name, ok = QInputDialog.getText(self, "New Folder", "Name:", text="untitled folder")
        name = name.strip()
        if not ok or not name:
            return
        if FORBIDDEN_CHARACTERS & set(name) or name.startswith("."):
            QMessageBox.warning(self, "New Folder", "A folder name cannot start with a dot or "
                                "contain any of  / \\ : * ? \" < > |")
            return
        target = parent / name
        try:
            target.mkdir()
        except FileExistsError:
            QMessageBox.warning(self, "New Folder", f"There is already something called "
                                f"“{name}” in {parent.name}.")
            return
        except OSError as error:
            QMessageBox.warning(self, "New Folder", str(error.strerror or error))
            return
        self._rescan_workspace()
        item = self.folder_items.get(self.workspace.relative(target))
        if item is not None:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(item)
            self.tree.blockSignals(False)
        self.statusBar().showMessage(f"Made the folder {name}", 5000)

    def move_paths(self, paths: list[str], folder: str | Path) -> None:
        """Move files or folders of the open folder into ``folder`` (dragging in the sidebar)."""
        target_folder = Path(folder)
        moved = []
        for source in map(Path, paths):
            if not source.exists() or source.resolve().parent == target_folder.resolve():
                continue
            if source.is_dir() and (source.resolve() == target_folder.resolve()
                                    or source.resolve() in target_folder.resolve().parents):
                QMessageBox.warning(self, "Move", f"“{source.name}” cannot go inside itself.")
                continue
            target = target_folder / source.name
            if target.exists():
                answer = ask_about_clash(self, target)
                if answer is None:
                    continue
                if answer == "keep":
                    target = unique_path(target_folder, source.name)
                else:
                    self.trash_paths_quietly([str(target)])
            self._flush_autosaves()
            try:
                shutil.move(str(source), str(target))
            except OSError as error:
                QMessageBox.warning(self, "Could not move",
                                    f"{source.name}\n\n{error.strerror or error}")
                continue
            self._paths_moved(source, target)
            moved.append(target)
        if moved:
            what = moved[0].name if len(moved) == 1 else f"{len(moved)} items"
            where = target_folder.name if target_folder.resolve() != self.workspace.folder \
                else self.workspace.name
            self.statusBar().showMessage(f"Moved {what} to {where}", 6000)
        self._rescan_workspace(force=True)

    def trash_paths_quietly(self, paths: list[str]) -> None:
        """Replace: the file being replaced goes to the Bin (its document closes)."""
        inside = [d.id for d in self.documents
                  if d.path and any(self._key(d.path) == self._key(p) for p in paths)]
        self.remove_documents(inside, confirm=False)
        for path in paths:
            move_to_trash(path)

    def _drop_folder(self, item) -> str | None:
        """Where a drop on this sidebar row goes (None: no folder is open)."""
        if self.workspace is None:
            return None
        root = str(self.workspace.folder)
        if item is None:
            return root
        if item.data(0, FOLDER_ROLE):
            return item.data(0, FOLDER_ROLE)
        path = self._row_path(item)
        if path and self.workspace.contains(path):
            return str(Path(path).resolve().parent)
        return root

    def _sidebar_drop(self, paths: list[str], folder: str | None, internal: bool) -> None:
        """Files dropped on the sidebar: moved within the folder, or brought in."""
        if folder is None:                          # no folder open
            if not internal:
                self._open_dropped(paths)
            return
        inside = [p for p in paths if self.workspace.contains(p)]
        outside = [p for p in paths if p not in inside]
        if inside:
            self.move_paths(inside, folder)
        folders = [p for p in outside if Path(p).is_dir()]
        if folders:
            self.open_workspace(folders[0])         # a folder from elsewhere: work in it
            return
        for path in self._bring_in(outside, folder) if outside else []:
            self.open_path(path)

    # ---------------------------------------------------------------- settings
    def show_settings(self) -> None:
        dialog = SettingsDialog({
            "autosave": self.autosave_enabled,
            "import": self._setting("files/import", "ask"),
            "restore": self.restore_action.isChecked(),
            "check_updates": self._setting("updates/automatic", True),
        }, self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        self.set_autosave(values["autosave"])
        self._set_setting("files/import", values["import"])
        self.restore_action.setChecked(values["restore"])
        self._set_setting("updates/automatic", values["check_updates"])

    # ---------------------------------------------------------------- exercises
    def action_open_exercise(self) -> None:
        """File ▸ Open Exercise Pack…: a pack, a chapter of one, or one exercise."""
        start = str(self.workspace.folder) if self.workspace else dialog_folder()
        folder = QFileDialog.getExistingDirectory(self, "Open exercise pack",
                                                  start or str(Path.home()))
        if folder:
            self.open_exercise(folder)

    def action_import_exam(self) -> None:
        """Learn ▸ Make a Pack from an Exam…: the exam's text (numbered questions
        with lettered parts) becomes a skeleton pack for the author to finish."""
        from ...learn.importer import todo_list, write_pack
        start = str(self.workspace.folder) if self.workspace else dialog_folder()
        source, _ = QFileDialog.getOpenFileName(self, "The exam's text", start or str(Path.home()),
                                                "Text or Markdown (*.txt *.md);;All files (*)")
        if not source:
            return
        parent = QFileDialog.getExistingDirectory(self, "Where should the pack go?",
                                                  str(Path(source).parent))
        if not parent:
            return
        title = Path(source).stem
        target = unique_path(parent, title)
        try:
            text = Path(source).read_text(encoding="utf-8", errors="replace")
            write_pack(text, target, title)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Make a pack", f"Could not make a pack from "
                                f"{Path(source).name}:\n\n{error}")
            return
        todos = todo_list(target)
        QMessageBox.information(
            self, "Make a pack", f"Made “{target.name}” with one exercise per question.\n\n"
            f"{len(todos)} TODO{'s' * (len(todos) != 1)} left for you: the nets and logs the exam "
            "showed as pictures, the computes to pick, the model answers. Open the question.md "
            "files, then run openprocess exercises check on the folder.")
        self.open_exercise(target)

    @staticmethod
    def demo_exercises_source() -> Path:
        """The demo exercises that ship with OpenProcess (read-only in an installed app)."""
        return Path(__file__).resolve().parents[2] / "exercises"

    def demo_exercises_target(self) -> Path | None:
        """Where the demo exercises are copied (so your answers can be saved): the
        copy made before, else a folder you choose now (None: you cancelled)."""
        remembered = self._setting("exercises/demo_copy", "")
        if remembered and Path(remembered).is_dir():
            return Path(remembered)
        start = str(self.workspace.folder) if self.workspace else dialog_folder()
        parent = QFileDialog.getExistingDirectory(
            self, "Where should the demo exercises go? (Your answers are saved there.)",
            start or str(Path.home()))
        if not parent:
            return None
        return unique_path(parent, "OpenProcess Demo Exercises")

    def open_demo_exercises(self) -> None:
        """Copy the demo exercises (once, where you say) and do them."""
        target = self.demo_exercises_target()
        if target is None:
            return
        source = self.demo_exercises_source()
        try:
            if not target.exists():
                shutil.copytree(source, target)
                self.statusBar().showMessage(f"Copied the demo exercises to {target}", 8000)
            elif not (target / "pack.md").exists():
                # A copy made by an older version: bring its worksheets up to
                # date; your answers (my answer…) are left alone.
                shutil.copytree(source, target, dirs_exist_ok=True)
        except OSError as error:
            QMessageBox.warning(self, "Demo exercises", f"Could not copy the demo "
                                f"exercises to {target}:\n\n{error}")
            return
        self._set_setting("exercises/demo_copy", str(target))
        self.open_exercise(target)

    def open_exercise(self, folder: str | Path) -> bool:
        """Do the exercises in ``folder`` (a pack, or one exercise of one) in
        Learn.  An exercise belongs to the nearest folder above it with
        a ``pack.md``, else to the folder open in the sidebar."""
        from ...learn.pack import find_exercises, pack_root
        folder = Path(folder).resolve()
        if exercise_files(folder) is not None:
            boundary = self.workspace.folder if self.workspace is not None and \
                self.workspace.contains(folder) else None
            root, start = pack_root(folder, boundary), folder
        elif find_exercises(folder):
            root, start = folder, None
        else:
            QMessageBox.information(self, "Open exercises", f"{folder}\n\nThere are no "
                                    "exercises here: an exercise is a folder with a "
                                    "question.md (or question.pdf or question.png).")
            return False
        self._flush_autosaves()
        if not self.learn_mode.open_pack(root, start):
            return False
        self._space_before_learn = self.space
        self.modes.setCurrentWidget(self.learn_mode)
        self._sync_space_switch()
        motion.lift(self.modes)
        self.learn_mode._update_bar()               # the window's title names the exercise
        self.statusBar().showMessage("Answers are saved in each exercise's folder as you go",
                                     6000)
        return True

    @property
    def in_learn(self) -> bool:
        return self.modes.currentWidget() is self.learn_mode

    def leave_learn(self, to: str | None = None) -> None:
        """Exit: back to the space you came from (``to``: another), as it was,
        with your answers' files now in the folder."""
        self.learn_mode.close_view()
        self.modes.setCurrentIndex(0)
        if to is not None and to != self.space:
            self._set_space_quietly(to)
        self._sync_space_switch()
        motion.lift(self.modes)
        if self.workspace is not None:
            self._rescan_workspace(force=True)
        self._set_title(self._current_document())    # back to the file you had open

    def _petri_of(self, document):
        """The :class:`PetriNet` a document stands for (a drawn net as it is now)."""
        if isinstance(document, ModelDocument):
            return document.net
        from ...model.plain import to_petri_net
        return to_petri_net(document.net)

    def replay_trace(self, document, trace, mapping: dict | None = None) -> None:
        """Fire as much of ``trace`` (labels) as the net allows, in its token game."""
        page = self.pages.get(document.id)
        if not isinstance(page, PetriNetPage):
            return
        from ...mining.analysis import check_workflow_net
        from ...mining.compare_nets import replayable_prefix
        petri = page.petri_net()
        source = None
        if not petri.initial_marking:
            workflow = check_workflow_net(petri)
            source = workflow.source if workflow.is_workflow_net else None
        path, done = replayable_prefix(petri, trace, mapping)
        self._select_document(document)
        page.replay(path, source)
        shown = "⟨" + ", ".join(trace) + "⟩"
        if done < len(trace):
            self.statusBar().showMessage(
                f"{shown}: your net can do the first {done} step(s), then not "
                f"“{trace[done]}” — this is where it differs", 12000)
        else:
            self.statusBar().showMessage(f"{shown}: replayed on your net", 8000)

    # ---------------------------------------------------------------- comparing nets
    def _nets(self) -> list:
        return [d for d in self.documents if isinstance(d, ModelDocument) or
                (isinstance(d, CpnDocument) and getattr(d.net, "plain", False))]

    def action_compare_nets(self) -> None:
        """Compare two open Petri nets on behaviour (complete traces)."""
        nets = self._nets()
        if len(nets) < 2:
            QMessageBox.information(self, "Compare nets", "Open at least two Petri nets to "
                                    "compare them.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Compare nets")
        form = QFormLayout(dialog)
        first, second = QComboBox(), QComboBox()
        for box in (first, second):
            for document in nets:
                box.addItem(document.name, document.id)
        selected = [i for i in self.selected_ids() if any(d.id == i for d in nets)]
        if selected:
            first.setCurrentIndex(first.findData(selected[0]))
        second.setCurrentIndex(1 if first.currentIndex() == 0 else 0)
        form.addRow(label("Do the two nets allow the same complete traces (silent steps "
                          "ignored, transitions matched by label)?", "muted", wrap=True))
        form.addRow("Net", first)
        form.addRow("Compare with", second)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Compare")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted or first.currentData() == second.currentData():
            return
        self.compare_nets(self._document(first.currentData()),
                          self._document(second.currentData()))

    def compare_nets(self, first, second, mapping: dict | None = None) -> None:
        from ...mining.compare_nets import compare_nets
        mine, other = self._petri_of(first), self._petri_of(second)
        self.statusBar().showMessage(f"Comparing {first.name} with {second.name}…")

        def done(comparison) -> None:
            from .net_comparison import NetComparisonView
            dialog = getattr(self, "net_comparison_dialog", None)
            if dialog is None:
                dialog = QDialog(self)
                dialog.resize(560, 420)
                dialog.setLayout(QVBoxLayout())
                self.net_comparison_dialog = dialog
            while dialog.layout().count():
                item = dialog.layout().takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            dialog.setWindowTitle(f"{first.name} vs {second.name}")
            view = NetComparisonView(
                comparison, f"“{first.name}”", f"“{second.name}”",
                replay=(lambda trace, _f: self.replay_trace(first, trace, mapping))
                if isinstance(self.pages.get(first.id), PetriNetPage) else None,
                recompare=lambda m: self.compare_nets(first, second, m))
            dialog.layout().addWidget(scroll(view), 1)
            self.statusBar().showMessage(comparison.summary(first.name, second.name), 8000)
            dialog.show()
            dialog.raise_()

        run_in_background(lambda: compare_nets(mine, other, mapping), done,
                          lambda message: self.statusBar().showMessage(
                              f"Comparison failed: {message}", 8000))

    def action_new_ts(self) -> None:
        """Type a transition system to study its regions."""
        from ...mining.transition_system import parse_transition_system
        text, ok = QInputDialog.getMultiLineText(
            self, "New transition system", "Transitions, as  s0 -a-> s1  (one per line or "
            "separated by commas). Optional: initial: s0",
            "s0 -a-> s1, s0 -b-> s2\ns1 -b-> s3, s2 -a-> s3\ninitial: s0")
        if not ok or not text.strip():
            return
        names = {d.name for d in self.documents}
        number = 1
        while f"Transition system {number}" in names:
            number += 1
        try:
            ts = parse_transition_system(text, f"Transition system {number}")
        except ValueError as error:
            QMessageBox.warning(self, "New transition system", str(error))
            return
        self.add_document(TransitionSystemDocument(ts))

    # ---------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._open_dropped([url.toLocalFile() for url in event.mimeData().urls()
                            if url.isLocalFile()])

    def _open_dropped(self, paths: list[str]) -> None:
        folders = [p for p in paths if Path(p).is_dir()]
        if folders and exercise_files(folders[0]) is not None:
            self.open_exercise(folders[0])                # an exercise: do it
        elif folders:
            self.open_workspace(folders[0])               # a folder: work in it
        self.open_files([p for p in paths if not Path(p).is_dir()])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if sys.platform == "win32":
        # Without its own AppUserModelID, Windows groups the window under
        # python.exe and shows Python's icon in the taskbar.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenProcess.Studio")
        except (AttributeError, OSError):
            pass
    from ... import packages
    packages.activate()                           # packages installed from Connections
    application = QApplication.instance() or QApplication(argv)
    application.setApplicationName(APPLICATION_NAME)
    application.setWindowIcon(app_icon())
    report_unexpected_errors()
    application.setStyle("Fusion")
    application.setFont(theme.ui_font(13))
    application.setStyleSheet(style.stylesheet())
    window = StudioWindow(persist=True)
    # Follow the system appearance live: every colour is looked up through
    # style.tokens() at paint time, so re-applying the stylesheet and
    # repainting is enough when the user switches light <-> dark.
    hints = application.styleHints()
    if hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(
            lambda *_: (application.setStyleSheet(style.stylesheet()), window.restyle()))
    window.show()
    window.restore_session()
    QTimer.singleShot(3000, window.check_automatically)
    for path in argv[1:]:
        if path == "--new-cpn":             # `openprocess-cpn` without a file
            window.action_new_cpn()
        elif not path.startswith("-"):
            window.open_files([path])
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
