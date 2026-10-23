"""Alignments drawn as ProM draws them: moves as chevrons, and the log on the model.

An optimal alignment pairs the log's events with the model's steps.  Each
**move** is drawn as a chevron with two rows, the log's activity above and
the model's below (``»`` where one side has nothing):

=========================  ======  =============================================
synchronous move           green   log and model do the same activity
log move                   yellow  the log did it; the model could not here
model move                 purple  the model had to do it; the log did not
silent move (τ)            black   the model moved on its own; nothing to see
=========================  ======  =============================================

:class:`AlignmentList` shows every variant's alignment that way, one row per
variant, most frequent first, and scrolls through thousands of them (a row
is painted only when it is on screen).  :func:`projection` puts the
alignments *on the model*: per transition, how often its move was
synchronous and how often the model had to make it alone, its colour going
from green (always matched) to purple (often skipped), and the log moves at
the transitions with their label.  Nothing here computes an alignment: it
draws :class:`~openprocess.mining.conformance.alignments.AlignmentResult`.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QWidget

from ...mining.conformance.alignments import SKIP
from .. import theme
from . import style

#: The colours of the four kinds of move (ProM's).
MOVE_COLOURS = {"sync": "#2fa84f", "log": "#f2c230", "model": "#8e44ad", "silent": "#2b2b2b"}
MOVE_NAMES = {"sync": "synchronous move", "log": "log move", "model": "model move", "silent": "silent move (τ)"}

CHEVRON_WIDTH = 104.0
CHEVRON_HEIGHT = 46.0
NOTCH = 10.0
#: A variant longer than this shows its first moves and says how many more.
MOST_MOVES = 400


def _chevron(x: float, y: float, width: float, height: float, first: bool) -> QPolygonF:
    """An arrow-shaped box: a notch on the left (except the first), a point on the right."""
    middle = y + height / 2
    points = [QPointF(x, y), QPointF(x + width - NOTCH, y), QPointF(x + width, middle),
              QPointF(x + width - NOTCH, y + height), QPointF(x, y + height)]
    if not first:
        points.append(QPointF(x + NOTCH, middle))
    return QPolygonF(points)


def paint_moves(painter: QPainter, x: float, y: float, moves, width: float = CHEVRON_WIDTH,
                height: float = CHEVRON_HEIGHT, clip: QRectF | None = None) -> float:
    """Paint ``moves`` as chevrons from (x, y); returns the x after the last.
    Chevrons outside ``clip`` are skipped (a long variant scrolls)."""
    font = painter.font()
    theme.set_px(font, 10.5)
    painter.setFont(font)
    metrics = QFontMetricsF(font)
    half = height / 2
    for index, move in enumerate(moves[:MOST_MOVES]):
        left = x + index * (width - NOTCH + 3)
        if clip is not None and (left > clip.right() or left + width < clip.left()):
            continue
        kind = move.kind
        fill = MOVE_COLOURS[kind]
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawPolygon(_chevron(left, y, width, height, index == 0))
        painter.setPen(QColor(style.text_on(fill)))
        inner = width - NOTCH * 2 - 4
        top = "»" if move.log == SKIP else move.log
        bottom = "»" if move.model == SKIP else ("τ" if kind == "silent" else move.model)
        for text, row in ((top, 0), (bottom, 1)):
            shown = metrics.elidedText(text, Qt.ElideRight, inner)
            painter.drawText(QRectF(left + NOTCH + 2, y + row * half, inner, half), Qt.AlignCenter, shown)
    count = min(len(moves), MOST_MOVES)
    end = x + count * (width - NOTCH + 3)
    if len(moves) > MOST_MOVES:
        painter.setPen(QColor(style.tokens().text_muted))
        painter.drawText(QRectF(end + 6, y, 160, height), Qt.AlignVCenter | Qt.AlignLeft,
                         f"… {len(moves) - MOST_MOVES:,} more moves")
        end += 170
    return end


def moves_width(moves) -> float:
    count = min(len(moves), MOST_MOVES)
    return count * (CHEVRON_WIDTH - NOTCH + 3) + (170 if len(moves) > MOST_MOVES else 0) + 16


class AlignmentStrip(QWidget):
    """One alignment as a row of chevrons (the model page's selected variant)."""

    def __init__(self) -> None:
        super().__init__()
        self.moves = []
        self.setMinimumHeight(int(CHEVRON_HEIGHT) + 8)

    def set_moves(self, moves) -> None:
        self.moves = list(moves)
        self.setMinimumWidth(int(moves_width(self.moves)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_moves(painter, 0, 4, self.moves, clip=QRectF(self.rect()))


class _Alignments(QAbstractListModel):
    def __init__(self, alignments, parent=None) -> None:
        super().__init__(parent)
        self.rows = sorted(alignments, key=lambda a: (-a.count, a.fitness))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        alignment = self.rows[index.row()]
        if role == Qt.UserRole:
            return alignment
        if role == Qt.ToolTipRole:
            return "⟨" + ", ".join(alignment.trace) + "⟩"
        return None


class _Delegate(QStyledItemDelegate):
    LINE = 20.0

    def sizeHint(self, option, index) -> QSize:
        alignment = index.data(Qt.UserRole)
        return QSize(int(moves_width(alignment.moves) if alignment.complete else 400),
                     int(self.LINE + CHEVRON_HEIGHT + 14))

    def paint(self, painter: QPainter, option, index) -> None:
        alignment = index.data(Qt.UserRole)
        t = style.tokens()
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        font = painter.font()
        theme.set_px(font, 11)
        painter.setFont(font)
        painter.setPen(QColor(t.text_secondary))
        deviations = alignment.log_moves + alignment.model_moves
        line = (f"{alignment.count:,} case{'s' if alignment.count != 1 else ''} · fitness {alignment.fitness:.3f} · "
                + (f"{deviations} deviation{'s' if deviations != 1 else ''}" if alignment.complete
                   else "the search stopped before finding an optimal alignment"))
        painter.drawText(QRectF(rect.left() + 8, rect.top() + 4, 1200, self.LINE), Qt.AlignVCenter | Qt.AlignLeft,
                         line)
        if alignment.complete:
            paint_moves(painter, rect.left() + 8, rect.top() + self.LINE + 6, alignment.moves, clip=rect)
        painter.restore()


class AlignmentList(QListView):
    """Every variant's optimal alignment as chevrons, most frequent first."""

    def __init__(self, result, parent=None) -> None:
        super().__init__(parent)
        self.setModel(_Alignments(result.alignments, self))
        self.setItemDelegate(_Delegate(self))
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setMinimumHeight(260)
        self.setObjectName("alignmentList")


def legend_items() -> list[tuple[str, str]]:
    """(colour, words) for each kind of move, for a legend."""
    return [(MOVE_COLOURS[kind], MOVE_NAMES[kind]) for kind in ("sync", "log", "model", "silent")]


def _mix(a: str, b: str, share: float) -> str:
    ca, cb = QColor(a), QColor(b)
    mixed = QColor(round(ca.red() + (cb.red() - ca.red()) * share), round(ca.green() + (cb.green() - ca.green()) * share),
                   round(ca.blue() + (cb.blue() - ca.blue()) * share))
    return mixed.name()


def projection(net, result) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str], dict[str, int]]:
    """The alignments on the model: ``(badges, fills, unknown)``.

    Per visible transition, the cases' synchronous moves (``✓ n``) and model
    moves (``» n``), weighted by how many cases each variant has, and a fill
    from green to purple by the share of model moves.  Log moves (``n »``)
    sit on the transitions with that activity as their label; ``unknown`` is
    the log moves of activities no transition has."""
    sync: dict[str, int] = {}
    model: dict[str, int] = {}
    log_moves: dict[str, int] = {}
    for alignment in result.alignments:
        for move in alignment.moves:
            kind = move.kind
            if kind == "sync" and move.transition:
                sync[move.transition] = sync.get(move.transition, 0) + alignment.count
            elif kind == "model" and move.transition:
                model[move.transition] = model.get(move.transition, 0) + alignment.count
            elif kind == "log":
                log_moves[move.log] = log_moves.get(move.log, 0) + alignment.count
    badges: dict[str, list[tuple[str, str]]] = {}
    fills: dict[str, str] = {}
    labelled: set[str] = set()
    pale = style.tokens().surface
    for transition in net.transitions.values():
        if transition.silent:
            continue
        done, skipped = sync.get(transition.id, 0), model.get(transition.id, 0)
        rows = []
        if done:
            rows.append((f"✓ {done:,}", MOVE_COLOURS["sync"]))
        if skipped:
            rows.append((f"» {skipped:,}", MOVE_COLOURS["model"]))
        if transition.label in log_moves and transition.label not in labelled:
            rows.append((f"{log_moves[transition.label]:,} »", MOVE_COLOURS["log"]))
            labelled.add(transition.label)
        if rows:
            badges[transition.id] = rows
        if done or skipped:
            share = skipped / (done + skipped)
            colour = _mix(MOVE_COLOURS["sync"], MOVE_COLOURS["model"], share)
            fills[transition.id] = _mix(pale, colour, 0.35)
    unknown = {activity: n for activity, n in log_moves.items() if activity not in labelled}
    return badges, fills, unknown


__all__ = ["AlignmentList", "AlignmentStrip", "MOVE_COLOURS", "legend_items", "paint_moves", "projection"]
