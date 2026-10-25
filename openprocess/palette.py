"""The data colours, without Qt: the Studio's style and the figures that
boxes draw (the dotted chart's SVG) take them from here, so a figure in a
report has the colours the same chart has on screen.

Eight categorical hues in a fixed order, assigned by frequency rank; a
ninth value never gets a generated hue but folds into a neutral grey
"Other".  See :mod:`openprocess.gui.studio.style` for how they were chosen.
"""

CATEGORICAL_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                     "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CATEGORICAL_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
                    "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER_LIGHT, OTHER_DARK = "#a8a7a0", "#6b6a66"
