"""Ready-made workflows, so a new workflow never opens as an empty canvas.

Each template builds a :class:`~openprocess.flow.workflow.Workflow` around a log:
a file in the folder when there is one, else a log typed in the course
notation.  They are the examples the README describes, and the starting
points *New Workflow* offers.
"""

from __future__ import annotations

from pathlib import Path

from ...flow.library import Library
from ...flow.workflow import Workflow

L1 = "[<a,b,c,d>^3, <a,c,b,d>^2, <a,e,d>]"


def _log(wf: Workflow, log_file: str | None, position=(0.0, 190.0)):
    if log_file:
        return wf.add("open_log", {"file": log_file}, position)
    return wf.add("typed_log", {"text": L1}, position)


def discover_and_check(library: Library, log_file: str | None = None) -> Workflow:
    """Log → Inductive Miner → Check fit: the three-box workflow."""
    wf = Workflow("Discover and check", library)
    log = _log(wf, log_file, (0.0, 120.0))
    model = wf.add("inductive_miner", {"noise": 0.2}, (240.0, 120.0))
    fit = wf.add("check_fit", {}, (480.0, 100.0))
    wf.connect(log, model)
    wf.connect(model, fit, "model")
    wf.connect(log, fit, "log")
    return wf


def compare_discovery(library: Library, log_file: str | None = None) -> Workflow:
    """One log, three miners, their fit side by side."""
    wf = Workflow("Compare discovery", library)
    log = _log(wf, log_file)
    filtered = wf.add("top_variants", {"k": 5}, (230.0, 190.0))
    wf.connect(log, filtered)
    miners = [wf.add("alpha_miner", {}, (470.0, 30.0)), wf.add("inductive_miner", {"noise": 0.2}, (470.0, 190.0)),
              wf.add("heuristics_miner", {"dependency": 0.9}, (470.0, 350.0))]
    compare = wf.add("compare", {}, (960.0, 190.0))
    for index, miner in enumerate(miners):
        check = wf.add("check_fit", {}, (720.0, 20.0 + index * 160.0))
        wf.connect(filtered, miner)
        wf.connect(miner, check, "model")
        wf.connect(log, check, "log")
        wf.connect(check, compare)
    return wf


def fitness_with_confidence(library: Library, log_file: str | None = None) -> Workflow:
    """Does the α-algorithm really fit this log better than the Heuristics Miner?
    Bootstrap both on the same resamples (one seed), the interval of the
    difference, a plot (needs NumPy and matplotlib)."""
    wf = Workflow("Fitness with confidence", library)
    log = _log(wf, log_file)
    alpha = wf.add("alpha_miner", {}, (240.0, 50.0))
    heuristics = wf.add("heuristics_miner", {"dependency": 0.9}, (240.0, 330.0))
    describe = wf.add("describe_log", {}, (240.0, 190.0))
    b1 = wf.add("bootstrap_fitness", {"samples": 200, "seed": 0}, (480.0, 30.0))
    b2 = wf.add("bootstrap_fitness", {"samples": 200, "seed": 0}, (480.0, 310.0))   # same resamples: paired
    test = wf.add("compare_samples", {}, (720.0, 150.0))
    plot = wf.add("plot", {}, (720.0, 330.0))
    for miner, boot in ((alpha, b1), (heuristics, b2)):
        wf.connect(log, miner)
        wf.connect(miner, boot, "model")
        wf.connect(log, boot, "log")
    wf.connect(log, describe)
    wf.connect(b1, test, "a")
    wf.connect(b2, test, "b")
    wf.connect(b1, plot, "tables")
    wf.connect(b2, plot, "tables")
    return wf


def predict_next_activity(library: Library, log_file: str | None = None) -> Workflow:
    """Prefixes → split by time → a baseline model → predict → evaluate."""
    wf = Workflow("Predict the next activity", library)
    log = _log(wf, log_file, (0.0, 150.0))
    pre = wf.add("prefixes", {}, (240.0, 150.0))
    split = wf.add("split_by_time", {"train_fraction": 0.7}, (480.0, 150.0))
    model = wf.add("frequency_model", {}, (720.0, 40.0))
    predict = wf.add("predict", {}, (960.0, 150.0))
    evaluate = wf.add("evaluate_predictions", {}, (1200.0, 150.0))
    wf.connect(log, pre)
    wf.connect(pre, split)
    wf.connect(split, model, "train", "train")
    wf.connect(split, predict, "dataset", "test")
    wf.connect(model, predict, "model")
    wf.connect(predict, evaluate)
    return wf


def noise_sweep(library: Library, log_file: str | None = None) -> Workflow:
    """The Inductive Miner's noise threshold swept from 0 to 0.5, scores stacked."""
    from ...flow.sweep import Sweep
    wf = Workflow("Noise sweep", library)
    log = _log(wf, log_file, (0.0, 120.0))
    model = wf.add("inductive_miner", {"noise": Sweep.parse("0..0.5 step 0.1")}, (240.0, 120.0))
    fit = wf.add("check_fit", {}, (480.0, 100.0))
    table = wf.add("sweep_table", {}, (720.0, 120.0))
    wf.connect(log, model)
    wf.connect(model, fit, "model")
    wf.connect(log, fit, "log")
    wf.connect(fit, table)
    return wf


#: (menu text, builder, one line about it)
TEMPLATES = [
    ("Discover and check", discover_and_check, "a log, the Inductive Miner and a fitness check"),
    ("Compare discovery", compare_discovery, "three miners on one log, side by side"),
    ("Noise sweep", noise_sweep, "one setting swept over a range, the scores stacked"),
    ("Fitness with confidence", fitness_with_confidence, "bootstrap intervals, their difference and a plot (NumPy, matplotlib)"),
    ("Predict the next activity", predict_next_activity, "the prediction pipeline with a baseline model"),
]


def first_log(folder: str | Path | None) -> str | None:
    """A log file of the folder to start from, relative to it (None: none)."""
    if folder is None:
        return None
    folder = Path(folder)
    for pattern in ("*.xes", "*.xes.gz", "*.csv", "*log.txt"):
        for path in sorted(folder.glob(pattern)):
            if not path.name.startswith("."):
                return path.name
    return None
