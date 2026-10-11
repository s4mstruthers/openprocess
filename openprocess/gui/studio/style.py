"""Design tokens and the stylesheet for the Studio window.

Every colour is named by *role* and has a light and a dark value, resolved
through :func:`tokens` at the moment it is needed, so the whole window
follows the system appearance.

Chart colours follow a validated data-visualisation palette:

* **categorical** -- eight hues in a *fixed order*, assigned to activities by
  frequency rank (most frequent activity = slot 1).  The order was checked for
  colour-vision-deficiency separation between neighbours; a ninth activity
  never gets a generated hue but folds into a neutral grey "Other".  Colour
  always follows the activity, so filtering never repaints the survivors.
* **sequential** -- one blue ramp, light to dark, for magnitudes (frequency
  in the process map).
* **status** -- good / warning / critical, reserved for verdicts (sound,
  fitting) and always shown with an icon and a word, never colour alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor

from .. import theme


@dataclass(frozen=True)
class Tokens:
    page: str            # window background behind content
    sidebar: str         # source list background
    surface: str         # cards, canvas paper
    surface_alt: str     # table stripes, hovered rows, inputs
    border: str          # hairlines
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_soft: str
    accent_text: str     # text on accent fills
    grid: str
    axis: str
    canvas: str
    node_fill: str
    node_stroke: str
    silent_fill: str
    selection: str


LIGHT = Tokens(
    page="#f5f5f7", sidebar="#ebebee", surface="#ffffff", surface_alt="#f6f6f8",
    border="#e1e1e6", text="#1d1d1f", text_secondary="#52514e", text_muted="#86868b",
    accent="#0a84ff", accent_soft="#dcebff", accent_text="#ffffff",
    grid="#e1e0d9", axis="#c3c2b7", canvas="#fcfcfb", node_fill="#ffffff",
    node_stroke="#3a3a3c", silent_fill="#3a3a3c", selection="#0a84ff",
)

DARK = Tokens(
    page="#1c1c1e", sidebar="#242427", surface="#2a2a2d", surface_alt="#323236",
    border="#3a3a3e", text="#f5f5f7", text_secondary="#c3c2b7", text_muted="#98989d",
    accent="#0a84ff", accent_soft="#153a63", accent_text="#ffffff",
    grid="#2c2c2a", axis="#383835", canvas="#1a1a19", node_fill="#2a2a2d",
    node_stroke="#d1d1d6", silent_fill="#d1d1d6", selection="#409cff",
)


def tokens() -> Tokens:
    return DARK if theme.is_dark() else LIGHT


def qc(hex_value: str, alpha: float = 1.0) -> QColor:
    colour = QColor(hex_value)
    colour.setAlphaF(alpha)
    return colour


# ---------------------------------------------------------------------------
# Data colours
# ---------------------------------------------------------------------------
CATEGORICAL_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                     "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CATEGORICAL_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
                    "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER_LIGHT, OTHER_DARK = "#a8a7a0", "#6b6a66"

SEQUENTIAL_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
                   "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_ICON = {"good": "✓", "warning": "!", "serious": "!", "critical": "✕", "unknown": "?",
               "info": "i"}


def categorical(slot: int) -> str:
    palette = CATEGORICAL_DARK if theme.is_dark() else CATEGORICAL_LIGHT
    if 0 <= slot < len(palette):
        return palette[slot]
    return OTHER_DARK if theme.is_dark() else OTHER_LIGHT


def sequential(fraction: float) -> str:
    """A step of the blue ramp for a magnitude in [0, 1]."""
    fraction = min(max(fraction, 0.0), 1.0)
    # Do not use the palest steps for real data: they vanish into the surface.
    low, high = 1, len(SEQUENTIAL_BLUE) - 2
    return SEQUENTIAL_BLUE[round(low + fraction * (high - low))]


def text_on(fill: str) -> str:
    """Ink colour that stays legible on ``fill``."""
    colour = QColor(fill)
    luminance = 0.2126 * colour.redF() + 0.7152 * colour.greenF() + 0.0722 * colour.blueF()
    return "#0b0b0b" if luminance > 0.55 else "#ffffff"


class ActivityColours:
    """Stable activity -> colour assignment for one log (by frequency rank)."""

    def __init__(self, activities_by_frequency: list[str]) -> None:
        self.rank = {name: i for i, name in enumerate(activities_by_frequency)}

    def slot(self, activity: str) -> int:
        return self.rank.get(activity, 99)

    def colour(self, activity: str) -> str:
        return categorical(self.slot(activity))

    def is_other(self, activity: str) -> bool:
        return self.slot(activity) >= len(CATEGORICAL_LIGHT)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
RADIUS = 10


def _arrow_images(colour: str) -> dict[str, str]:
    """Small up/down chevrons (spin boxes, combo boxes) and the tick and dash of a
    check box, as PNG files.

    A stylesheet that styles a spin box's buttons must also supply the
    arrows (Qt then stops drawing its own), and stylesheets can only take
    images from files -- so they are painted once per colour into a cache
    folder.  Returns ``{"up": path, "down": path}`` with forward slashes.
    """
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage, QPainter, QPen
    folder = Path(tempfile.gettempdir()) / "openprocess-ui"
    folder.mkdir(exist_ok=True)
    paths = {}
    for name, points in (("up", ((3, 10), (8, 5), (13, 10))),
                         ("down", ((3, 6), (8, 11), (13, 6))),
                         ("check", ((4, 8.5), (7, 11.5), (12.5, 5))),
                         ("dash", ((4.5, 8), (11.5, 8)))):
        path = folder / f"chevron-{name}-{colour.lstrip('#')}.png"
        if not path.exists():
            image = QImage(32, 32, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.scale(2, 2)
            pen = QPen(QColor(colour), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([QPointF(x, y) for x, y in points])
            painter.end()
            image.save(str(path))
        paths[name] = path.as_posix()
    return paths


def stylesheet() -> str:
    t = tokens()
    # Outlines of check boxes, radio buttons and slider handles: the text
    # colour, faded -- visible on light and dark (the axis grey all but
    # vanished on the dark surface).
    outline = qc(t.text, 0.38).name(QColor.HexArgb)
    track = qc(t.text, 0.16).name(QColor.HexArgb)
    arrows = _arrow_images(t.text_secondary)
    ticks = _arrow_images(t.accent_text)            # white, on the accent fill
    return f"""
    QWidget {{ color: {t.text}; }}
    QMainWindow, #studioRoot {{ background: {t.page}; }}
    QDialog, QMessageBox {{ background: {t.page}; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
                padding: 6px 8px; border-radius: 8px; }}

    /* ---- sidebar -------------------------------------------------------- */
    #sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
    #sidebar QTreeWidget {{ background: transparent; border: none; outline: none; }}
    #sidebar QTreeWidget::item {{ padding: 5px 28px 5px 6px; border-radius: 7px; margin: 1px 6px; }}
    #sidebar QTreeWidget::item:hover {{ background: {qc(t.text, 0.06).name(QColor.HexArgb)}; }}
    #sidebar QTreeWidget::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    #sidebarTitle {{ color: {t.text}; font-weight: 700; font-size: 15px; padding: 0 2px; }}
    #sidebarCaption {{ color: {t.text_muted}; font-size: 10px; font-weight: 700;
        letter-spacing: 0.8px; padding: 0 2px; }}
    #sidebarMenuButton {{ background: transparent; border: none; border-radius: 6px;
        color: {t.text_secondary}; font-size: 16px; padding: 0 6px 2px 6px; }}
    #sidebarMenuButton:hover {{ background: {qc(t.text, 0.08).name(QColor.HexArgb)}; }}
    #sidebarMenuButton::menu-indicator {{ image: none; width: 0; }}
    #sidebarToggle {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
    #rail {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
    #sidebarToggle:hover {{ background: {qc(t.text, 0.08).name(QColor.HexArgb)}; }}
    #sidebarFooter QPushButton {{ background: transparent; border: none; color: {t.text_secondary};
        padding: 6px 8px; border-radius: 6px; text-align: left; }}
    #sidebarFooter QPushButton:hover {{ background: {qc(t.text, 0.06).name(QColor.HexArgb)}; }}

    /* ---- page chrome -------------------------------------------------- */
    #pageHeader {{ background: {t.page}; }}
    #pageTitle {{ font-size: 22px; font-weight: 700; }}
    #pageSubtitle {{ color: {t.text_muted}; }}
    #noticeBar {{ background: {qc(STATUS["warning"], 0.16).name(QColor.HexArgb)};
                  border-bottom: 1px solid {qc(STATUS["warning"], 0.45).name(QColor.HexArgb)}; }}
    #noticeBar QLabel {{ color: {t.text}; }}
    #updateBar {{ background: {t.accent_soft}; border-bottom: 1px solid {t.border}; }}
    #updateBar QLabel {{ color: {t.text}; }}
    #updateBarClose {{ background: transparent; border: none; border-radius: 6px;
                       color: {t.text_secondary}; padding: 3px 7px; }}
    #updateBarClose:hover {{ background: {qc(t.text, 0.08).name(QColor.HexArgb)}; }}
    #card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS}px; }}
    #cardTitle {{ font-weight: 600; font-size: 13px; }}
    /* A section inside a card (no second frame around it). */
    #flatCard {{ background: transparent; border: none; }}
    #cardCaption, #muted {{ color: {t.text_muted}; }}
    #statLabel {{ color: {t.text_muted}; font-size: 12px; }}
    #statValue {{ font-size: 22px; font-weight: 600; }}
    #statSub {{ color: {t.text_muted}; font-size: 11px; }}
    #sectionLabel {{ color: {t.text_muted}; font-size: 11px; font-weight: 700;
                     letter-spacing: 0.6px; }}

    /* The canvas's "out of view — show it" hint (see panning.py). */
    QPushButton#offscreenHint {{ background: {t.accent}; border: none; color: {t.accent_text};
        border-radius: 13px; padding: 5px 14px; font-weight: 600; }}
    QPushButton#offscreenHint:hover {{ background: {QColor(t.accent).lighter(110).name()}; }}

    /* ---- exercise mode -------------------------------------------------------- */
    #exerciseBar {{ background: {t.page}; border-bottom: 1px solid {t.border}; }}
    #exerciseWhere {{ color: {t.text_secondary}; font-weight: 600; }}
    #exercisePosition {{ color: {t.text_muted}; font-size: 12px; min-width: 34px;
                         qproperty-alignment: AlignCenter; }}
    #exerciseBarButton {{ background: transparent; border: none; border-radius: 6px;
        color: {t.text_secondary}; padding: 4px 9px; font-size: 13px; }}
    #exerciseBarButton:hover {{ background: {qc(t.text, 0.08).name(QColor.HexArgb)};
        color: {t.text}; }}
    #exerciseBarButton:disabled {{ color: {qc(t.text, 0.22).name(QColor.HexArgb)}; }}
    #exerciseBarButton::menu-indicator {{ image: none; width: 0; }}
    QPushButton#exerciseExit {{ padding: 4px 14px; }}
    #progressDot {{ border-radius: 6px; border: 1.5px solid {qc(t.text, 0.28).name(QColor.HexArgb)};
        background: transparent; padding: 0; }}
    #progressDot[state="started"] {{ border-color: {STATUS["warning"]};
        background: {qc(STATUS["warning"], 0.35).name(QColor.HexArgb)}; }}
    #progressDot[state="done"] {{ border-color: {STATUS["good"]}; background: {STATUS["good"]}; }}
    #progressDot[current="true"] {{ border: 2px solid {t.accent}; }}
    #exerciseHome, #worksheet {{ background: {t.page}; }}
    #materials {{ background: {t.page}; }}
    #sheetChapter {{ color: {t.accent}; font-size: 11px; font-weight: 700;
                     letter-spacing: 0.8px; }}
    #sheetTitle {{ font-size: 24px; font-weight: 700; }}
    #sheetProblem {{ color: {t.text}; background: {qc(STATUS["warning"], 0.16).name(QColor.HexArgb)};
        border-radius: 8px; padding: 8px 10px; }}
    QLabel#markdown {{ font-size: 14px; line-height: 150%; }}
    #notesPanel {{ background: {t.page}; border-top: 1px solid {t.border}; }}
    QPlainTextEdit#notesEdit {{ font-size: 14px; padding: 8px 10px; }}
    #notesOverlay {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    #notesOverlayButton {{ background: transparent; border: none; border-radius: 6px;
        padding: 2px 7px; color: {t.text_secondary}; font-size: 14px; }}
    #notesOverlayButton:hover {{ background: {qc(t.text, 0.08).name(QColor.HexArgb)}; color: {t.text}; }}
    QToolButton#notesStatusButton {{ background: {t.surface}; border: 1px solid {t.border};
        border-radius: 7px; padding: 2px 10px; margin: 2px 6px; color: {t.text_secondary}; }}
    QToolButton#notesStatusButton:hover {{ color: {t.text}; }}
    QToolButton#notesStatusButton:checked {{ background: {t.accent_soft}; color: {t.text};
        border-color: {qc(t.accent, 0.45).name(QColor.HexArgb)}; }}
    #exerciseBarButton:checked {{ background: {t.accent_soft}; color: {t.text}; }}
    #taskCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    #taskCard[state="good"] {{ border-color: {qc(STATUS["good"], 0.55).name(QColor.HexArgb)}; }}
    #taskCard[state="critical"] {{ border-color: {qc(STATUS["critical"], 0.45).name(QColor.HexArgb)}; }}
    #taskCard[state="warning"] {{ border-color: {qc(STATUS["warning"], 0.7).name(QColor.HexArgb)}; }}
    #taskCaption {{ color: {t.text_muted}; font-size: 10px; font-weight: 700;
                    letter-spacing: 0.8px; }}
    #feedback {{ border-radius: 8px; padding: 7px 10px;
        background: {qc(t.text, 0.05).name(QColor.HexArgb)}; }}
    #feedback[state="good"] {{ background: {qc(STATUS["good"], 0.12).name(QColor.HexArgb)}; }}
    #feedback[state="warning"] {{ background: {qc(STATUS["warning"], 0.2).name(QColor.HexArgb)}; }}
    #feedback[state="critical"] {{ background: {qc(STATUS["critical"], 0.1).name(QColor.HexArgb)}; }}
    #taskNote {{ background: {qc(t.accent, 0.07).name(QColor.HexArgb)};
        border-left: 3px solid {qc(t.accent, 0.55).name(QColor.HexArgb)}; border-radius: 6px; }}
    #taskNote QLabel {{ background: transparent; }}
    #answerReading {{ color: {t.text_muted}; font-size: 12px; }}
    #answerReading[ok="false"] {{ color: {STATUS["critical"]}; }}
    QLineEdit#answerLine {{ padding: 6px 9px; font-size: 14px; }}
    #choiceOption {{ border: 1px solid {t.border}; border-radius: 8px; background: {t.surface}; }}
    #choiceOption:hover {{ border-color: {t.accent}; }}
    #choiceOption QLabel {{ background: transparent; }}
    QPushButton#choicePill {{ min-width: 56px; padding: 5px 16px; border-radius: 12px; }}
    QPushButton#choicePill:checked {{ background: {t.accent}; border-color: {t.accent};
        color: {t.accent_text}; font-weight: 600; }}
    QToolButton#footprintCell {{ background: {t.surface_alt}; border: 1px solid {t.border};
        border-radius: 6px; font-size: 15px; }}
    QToolButton#footprintCell:hover {{ border-color: {t.accent}; }}
    QToolButton#footprintCell:focus {{ border: 1.5px solid {t.accent}; }}
    QToolButton#footprintCell[wrong="true"] {{ border: 2px solid {STATUS["critical"]};
        background: {qc(STATUS["critical"], 0.08).name(QColor.HexArgb)}; }}
    #footprintHeading {{ color: {t.text_secondary}; font-weight: 600; padding: 0 4px; }}
    QLineEdit#gridCell {{ background: {t.surface_alt}; border: 1px solid {t.border};
        border-radius: 6px; font-size: 14px; padding: 2px 4px; }}
    QLineEdit#gridCell:focus {{ border: 1.5px solid {t.accent}; }}
    QLineEdit#gridCell[wrong="true"] {{ border: 2px solid {STATUS["critical"]};
        background: {qc(STATUS["critical"], 0.08).name(QColor.HexArgb)}; }}
    #tupleName {{ color: {t.text_secondary}; font-weight: 600; font-size: 14px; min-width: 34px; }}
    #tupleMark {{ font-weight: 700; font-size: 14px; }}
    #tupleMark[state="good"] {{ color: {STATUS["good"]}; }}
    #tupleMark[state="warning"] {{ color: {STATUS["warning"]}; }}
    #tupleMark[state="critical"] {{ color: {STATUS["critical"]}; }}
    QListWidget#rankingList {{ background: {t.surface_alt}; border: 1px solid {t.border};
        border-radius: 8px; font-size: 14px; padding: 4px; }}
    QListWidget#rankingList::item {{ padding: 5px 8px; border-radius: 6px; }}
    QListWidget#rankingList::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    #examClock {{ color: {t.text_secondary}; font-weight: 600; font-variant-numeric: tabular-nums;
        padding: 2px 10px; border-radius: 9px; background: {qc(t.text, 0.06).name(QColor.HexArgb)}; }}
    #examClock[state="soon"] {{ color: {t.text};
        background: {qc(STATUS["warning"], 0.3).name(QColor.HexArgb)}; }}
    #examClock[state="over"] {{ color: {STATUS["critical"]};
        background: {qc(STATUS["critical"], 0.12).name(QColor.HexArgb)}; }}
    #exerciseRow {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
    #exerciseRow:hover {{ border-color: {t.accent}; }}
    #exerciseRow QLabel {{ background: transparent; }}
    #rowTitle {{ font-weight: 600; font-size: 14px; }}
    #rowChevron {{ color: {t.text_muted}; font-size: 18px; }}
    #rowMark {{ color: {t.text_muted}; font-size: 15px; }}
    #rowMark[state="done"] {{ color: {STATUS["good"]}; font-weight: 700; }}
    #rowMark[state="started"] {{ color: {STATUS["warning"]}; }}
    QTextBrowser#guideBrowser {{ border: none; border-radius: 0; padding: 18px 26px; }}
    QTextBrowser#plainBrowser {{ background: transparent; border: none; padding: 0; }}
    /* A result an exercise hides: a soft inset with its Reveal button. */
    #revealRow {{ background: {qc(t.text, 0.045).name(QColor.HexArgb)};
                  border: 1px dashed {qc(t.text, 0.18).name(QColor.HexArgb)};
                  border-radius: 9px; }}
    #revealRow QLabel {{ background: transparent; }}
    #revealTitle {{ font-weight: 600; }}

    /* The command palette's list. */
    QListWidget#paletteList {{ background: transparent; border: none; outline: none; font-size: 13px; }}
    QListWidget#paletteList::item {{ padding: 5px 8px; border-radius: 6px; }}
    QListWidget#paletteList::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}

    /* The reproducibility badge beside an analysis's title. */
    QPushButton#reproBadge {{ border: none; border-radius: 10px; padding: 3px 10px 3px 8px; font-size: 11px;
        font-weight: 600; letter-spacing: 0.1px; }}
    QPushButton#reproBadge:hover {{ text-decoration: underline; }}

    /* The Summary's tiles and the Model palette. */
    #summaryValue {{ font-size: 22px; font-weight: 600; }}
    QPushButton#paletteTool {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 7px;
        padding: 5px 8px; text-align: left; color: {t.text_secondary}; }}
    QPushButton#paletteTool:hover {{ color: {t.text}; }}
    QPushButton#paletteTool:checked {{ background: {t.accent_soft}; color: {t.text}; border-color: {t.accent}; }}

    /* A page's way back to the analysis it was opened from. */
    QPushButton#backLink {{ background: transparent; border: none; padding: 0 0 2px 0;
        color: {t.accent}; font-size: 12px; font-weight: 500; text-align: left; }}
    QPushButton#backLink:hover {{ text-decoration: underline; }}

    /* ---- the space bar: Mine / Model / Learn at the top of the window ---- */
    QToolBar#spaceBar {{ background: {t.sidebar}; border: none; border-bottom: 1px solid {t.border};
        padding: 4px 10px; spacing: 8px; }}
    QToolBar#spaceBar::separator {{ width: 0; }}

    /* ---- segmented control ---------------------------------------------- */
    #segmented {{ background: {qc(t.text, 0.07).name(QColor.HexArgb)}; border-radius: 8px; }}
    #segmented QPushButton {{ background: transparent; border: none; border-radius: 6px;
        padding: 5px 11px; color: {t.text_secondary}; font-weight: 500; }}
    #segmented QPushButton:hover {{ color: {t.text}; }}
    #segmented QPushButton:checked {{ background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; }}
    #segmented[compact="true"] QPushButton {{ padding: 4px 8px; font-size: 12px; }}

    /* ---- buttons ------------------------------------------------------ */
    QPushButton {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 7px;
        padding: 6px 14px; }}
    QPushButton:hover {{ background: {t.surface_alt}; }}
    QPushButton:disabled {{ color: {t.text_muted}; }}
    QPushButton#primary {{ background: {t.accent}; border-color: {t.accent};
        color: {t.accent_text}; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {QColor(t.accent).lighter(110).name()}; }}
    QPushButton#primary:disabled {{ background: {t.border}; border-color: {t.border};
        color: {t.text_muted}; }}
    QPushButton#ghost {{ background: transparent; border: none; color: {t.accent};
        padding: 4px 6px; }}
    QPushButton#ghost:hover {{ text-decoration: underline; }}
    QToolButton#canvasTool {{ background: {t.surface}; border: 1px solid {t.border};
        border-radius: 7px; padding: 4px 9px; }}
    QToolButton#canvasTool:hover {{ background: {t.surface_alt}; }}
    QToolButton#canvasTool::menu-indicator {{ image: url("{arrows['down']}");
        subcontrol-position: right center; width: 9px; height: 9px; right: 6px; }}
    QToolButton#canvasTool[popupMode="2"] {{ padding-right: 20px; }}
    QToolButton#canvasTool:checked {{ background: {t.accent}; color: {t.accent_text};
        border-color: {t.accent}; }}

    /* ---- inputs ------------------------------------------------------- */
    QTextBrowser, QTextEdit {{ background: {t.surface}; border: 1px solid {t.border};
        border-radius: 10px; padding: 6px; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 7px;
        padding: 4px 8px; selection-background-color: {t.accent}; }}
    QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
    QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;
        width: 18px; border: none; border-radius: 4px; margin: 2px 3px;
        background: transparent; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {qc(t.text, 0.08).name(QColor.HexArgb)}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right;
        margin-bottom: 0; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right;
        margin-top: 0; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{arrows['up']}");
        width: 10px; height: 10px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{arrows['down']}");
        width: 10px; height: 10px; }}
    QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled,
    QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
    /* combobox-popup: 0 -- a list that drops down below the box (as on the
       web), which follows the stylesheet; the menu-like one paints a square
       panel of its own around the rounded list. */
    QComboBox {{ padding-right: 22px; combobox-popup: 0; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox::down-arrow {{ image: url("{arrows['down']}"); width: 10px; height: 10px; }}
    /* The list that drops down: rounded like the menus (widgets.round_menus
       makes its window see-through, so the corners show). */
    QComboBoxPrivateContainer {{ background: transparent; border: none; }}
    QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {t.border};
        border-radius: 10px; padding: 5px; outline: none;
        selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
    QComboBox QAbstractItemView::item {{ padding: 6px 12px; border-radius: 6px;
        min-height: 18px; }}
    QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
        background: {t.accent}; color: {t.accent_text}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {track}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t.surface}; border: 1px solid {outline};
        width: 16px; height: 16px; margin: -7px 0; border-radius: 8px; }}
    QRadioButton, QCheckBox {{ spacing: 8px; }}
    /* Check boxes (also in lists, e.g. a chart's legend) and radio buttons:
       rounded, filled with the accent colour when on. */
    QCheckBox::indicator, QAbstractItemView::indicator {{ width: 14px; height: 14px;
        border: 1px solid {outline}; border-radius: 4px; background: {t.surface}; }}
    QCheckBox::indicator:hover, QAbstractItemView::indicator:hover {{
        border-color: {t.accent}; }}
    QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{
        background: {t.accent}; border-color: {t.accent}; image: url("{ticks['check']}"); }}
    QCheckBox::indicator:indeterminate, QAbstractItemView::indicator:indeterminate {{
        background: {t.accent}; border-color: {t.accent}; image: url("{ticks['dash']}"); }}
    QCheckBox::indicator:disabled {{ background: {t.surface_alt}; border-color: {t.border}; }}
    QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {outline};
        border-radius: 8px; background: {t.surface}; }}
    QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
    QRadioButton::indicator:checked {{ width: 6px; height: 6px; border: 5px solid {t.accent};
        background: {t.surface}; }}

    /* Framed sections (the Filter dialog's): rounded like the cards. */
    QGroupBox {{ border: 1px solid {t.border}; border-radius: 10px; margin-top: 14px;
        padding: 10px 8px 8px 8px; background: {t.surface}; }}
    QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
        left: 4px; padding: 0 4px; color: {t.text}; font-weight: 600; }}
    QGroupBox::indicator {{ width: 14px; height: 14px; border: 1px solid {outline};
        border-radius: 4px; background: {t.surface}; }}
    QGroupBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent};
        image: url("{ticks['check']}"); }}

    /* ---- tables and lists --------------------------------------------- */
    QTableView, QTreeView, QListView {{ background: {t.surface}; border: none;
        alternate-background-color: {t.surface_alt}; gridline-color: {t.border};
        selection-background-color: {t.accent_soft}; selection-color: {t.text}; outline: none; }}
    QHeaderView::section {{ background: {t.surface}; color: {t.text_muted}; border: none;
        border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600;
        font-size: 11px; }}
    QTableCornerButton::section {{ background: {t.surface}; border: none; }}
    /* The Workflows page's + Add box popover: a rounded card in a see-through
       window (the shadow is drawn by the picker), rows like the sidebar's,
       group names as captions. */
    #boxPicker {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    QLineEdit#boxSearch {{ border-radius: 8px; padding: 7px 10px; font-size: 13px; }}
    QPushButton#boxChoice {{ background: transparent; border: none; border-radius: 5px;
        padding: 4px 8px; text-align: left; }}
    QPushButton#boxChoice:hover {{ background: {qc(t.text, 0.06).name(QColor.HexArgb)}; }}
    QPushButton#boxChoice:focus {{ background: {t.accent_soft}; }}
    /* A result's "Open as log ›" card: one big link, in the accent colour. */
    #openCard {{ background: {t.accent_soft}; border: 1px solid {t.accent}; border-radius: 10px; }}
    #openCard:hover {{ border-width: 2px; }}
    #openCardTitle {{ color: {t.accent}; font-weight: 700; font-size: 14px; }}
    /* The side panel's ✕ and the header's ⋯: quiet until hovered. */
    QPushButton#panelClose, QPushButton#moreButton {{ background: transparent; border: 1px solid transparent;
        color: {t.text_muted}; padding: 4px 6px; }}
    QPushButton#panelClose:hover, QPushButton#moreButton:hover {{ background: {t.surface_alt};
        border-color: {t.border}; color: {t.text}; }}
    /* A list on its own in a dialog (Compare logs, the Filter dialog's
       activities) gets a rounded frame, like a text field. */
    QDialog QListWidget, QDialog QListView {{ border: 1px solid {t.border};
        border-radius: 8px; padding: 4px; }}

    /* ---- scroll bars: thin overlays ------------------------------------- */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent;
        width: 10px; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {qc(t.text, 0.22).name(QColor.HexArgb)};
        border-radius: 4px; min-height: 30px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none; height: 0; width: 0; }}

    QSplitter::handle {{ background: {t.border}; }}
    /* The Workflows page: gaps between cards, lit under the mouse so they read as handles. */
    WorkflowPage QSplitter::handle {{ background: transparent; }}
    WorkflowPage QSplitter::handle:hover {{ background: {qc(t.accent, 0.35).name(QColor.HexArgb)};
        margin: 6px 5px; border-radius: 2px; }}
    QGraphicsView {{ background: {t.canvas}; border: none; }}
    QStatusBar {{ background: {t.page}; color: {t.text_muted}; border-top: 1px solid {t.border}; }}
    /* Pop-up menus: rounded like the cards (see widgets.round_menus, which
       gives them the see-through window the corners need). */
    QMenu {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
             padding: 5px; }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; margin: 1px 0; }}
    QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu::item:disabled {{ color: {t.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}
    QProgressBar {{ background: {t.border}; border: none; border-radius: 2px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
    QTabWidget::pane {{ border: none; }}
    """
