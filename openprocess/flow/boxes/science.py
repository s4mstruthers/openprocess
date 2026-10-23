"""Science boxes: how any Python library plugs in.

Each box here is a few lines around one library call, so it reads as a
recipe: copy it, change three lines, save it in your folder's ``boxes/``.
They live behind ``pip install openprocess[science]``; without a library the box
is listed greyed out with "needs pandas" (the ``needs=`` of ``@box``).
"""

from __future__ import annotations

import random
from typing import Literal

from ... import flow
from ...mining.conformance.token_replay import token_replay
from ...mining.log import EventLog
from ...mining.petrinet import PetriNet
from ..box import box
from ..convert import convert
from ..types import Column, Figure, Scores, Table


@box(name="Describe log", group="Science", needs="pandas")
def describe_log(log: EventLog) -> Table:
    """pandas' ``describe()`` of the events per case and, when the log has
    timestamps, of the case durations in hours. The log goes in as a
    DataFrame through the converter; the table comes back as a Table."""
    import pandas as pd
    frame = convert(log, "pandas.DataFrame")
    per_case = frame.groupby("case:concept:name", sort=False).size().rename("events per case")
    columns = {"events per case": per_case.describe()}
    if "time:timestamp" in frame.columns and frame["time:timestamp"].notna().any():
        span = frame.groupby("case:concept:name")["time:timestamp"].agg(["min", "max"])
        hours = ((span["max"] - span["min"]).dt.total_seconds() / 3600).rename("case duration (h)")
        columns["case duration (h)"] = hours.describe()
    described = pd.DataFrame(columns)
    flow.note(f"df: {len(frame)} rows × {len(frame.columns)} columns; per_case = df.groupby(case).size()")
    rows = [[stat, *[round(float(described.loc[stat, c]), 3) for c in described.columns]] for stat in described.index]
    return Table("Describe", ["statistic", *described.columns], rows)


@box(name="Bootstrap fitness", group="Science", needs="numpy")
def bootstrap_fitness(model: PetriNet, log: EventLog, samples: int = 200, seed: int = 0) -> Table:
    """Fitness with a 95 % bootstrap interval: resample the cases with
    replacement *samples* times and replay each sample, so a number comes
    with its uncertainty.

    samples: how many resamples (more: a steadier interval)
    seed: the random seed, recorded in the workflow file
    """
    import numpy as np
    sequences = log.sequences()
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(samples):
        picked = rng.integers(0, len(sequences), len(sequences))
        from collections import Counter
        sample = Counter(sequences[i] for i in picked)
        values.append(token_replay(model, sample).fitness)
    values = np.array(values)
    low, high = np.percentile(values, [2.5, 97.5])
    flow.note(f"{samples} samples of {len(sequences)} cases, seed {seed}; "
              f"min {values.min():.3f}, max {values.max():.3f}")
    table = Table(f"Bootstrap fitness of {model.name}", ["statistic", "fitness"],
                  [["mean", round(float(values.mean()), 4)], ["2.5 %", round(float(low), 4)],
                   ["97.5 %", round(float(high), 4)], ["samples", samples], ["seed", seed]],
                  samples=[float(v) for v in values],
                  note=f"Fitness of {model.name}: {values.mean():.3f} (95 % interval {low:.3f} to {high:.3f}).")
    return table


def _row(table: Table, name: str):
    return next((row[1] for row in table.rows if row and row[0] == name), None)


@box(name="Compare samples", group="Science")
def compare_samples(a: Table, b: Table,
                    test: Literal["Bootstrap difference", "Mann–Whitney U", "t-test"] = "Bootstrap difference"
                    ) -> Table:
    """Do two models score differently? *Bootstrap difference* (the default,
    for two Bootstrap fitness tables): the 95 % interval of A − B. When both
    tables were resampled with the same seed and sample count from the same
    log they used the same resamples, so the difference is taken resample by
    resample (paired). The Mann–Whitney U test and Welch's t-test (SciPy) are
    for two samples of independent observations, such as per-case values;
    on bootstrap resamples their p-value only shrinks as you ask for more
    samples, so it is not evidence.

    test: Bootstrap difference for bootstrap tables; the tests for independent samples
    """
    if not a.samples or not b.samples:
        raise ValueError("Both tables need samples (connect two Bootstrap fitness boxes)")
    x, y = a.samples, b.samples
    mean_a, mean_b = sum(x) / len(x), sum(y) / len(y)
    flow.note(f"A = {a.name} ({len(x)} samples, mean {mean_a:.3f}); B = {b.name} ({len(y)} samples, mean {mean_b:.3f})")
    if test == "Bootstrap difference":
        return _bootstrap_difference(a, b, x, y, mean_a, mean_b)
    try:
        from scipy import stats
    except ImportError:
        raise ValueError(f"The {test} needs SciPy (pip install scipy); Bootstrap difference does not") from None
    if _row(a, "seed") is not None or _row(b, "seed") is not None:
        flow.note("These are bootstrap resamples, not independent observations: the p-value depends on how many "
                  "samples were drawn. Use Bootstrap difference to compare them.")
    if test == "t-test":
        result = stats.ttest_ind(x, y, equal_var=False)
        statistic_name = "t"
    else:
        result = stats.mannwhitneyu(x, y, alternative="two-sided")
        statistic_name = "U"
    p = float(result.pvalue)
    verdict = (f"The samples differ (p {'< 0.001' if p < 0.001 else f'= {p:.3f}'}): "
               f"{a.name if mean_a > mean_b else b.name} is higher."
               if p < 0.05 else f"No difference the test can see (p = {p:.3f}).")
    return Table(test, ["", "value"], [[statistic_name, round(float(result.statistic), 3)],
                                      ["p-value", "< 0.001" if p < 0.001 else round(p, 4)],
                                      ["mean A", round(mean_a, 4)], ["mean B", round(mean_b, 4)]], note=verdict)


def _bootstrap_difference(a: Table, b: Table, x: list[float], y: list[float], mean_a: float, mean_b: float) -> Table:
    """The 95 % interval of A − B from two bootstrap distributions."""
    import statistics
    paired = (len(x) == len(y) and _row(a, "seed") is not None
              and _row(a, "seed") == _row(b, "seed") and _row(a, "samples") == _row(b, "samples"))
    if paired:
        differences = sorted(p - q for p, q in zip(x, y))
        low = differences[int(0.025 * (len(differences) - 1))]
        high = differences[int(round(0.975 * (len(differences) - 1)))]
        how = "paired: the same resamples (same seed and sample count), the difference per resample"
    else:
        spread = (statistics.pvariance(x) + statistics.pvariance(y)) ** 0.5
        low, high = mean_a - mean_b - 1.96 * spread, mean_a - mean_b + 1.96 * spread
        how = "unpaired: the two bootstrap standard errors combined (normal approximation)"
    flow.note(f"Interval of A − B: {how}")
    if low > 0 or high < 0:
        verdict = (f"{a.name if low > 0 else b.name} scores higher: the 95 % interval of the difference, "
                   f"{low:+.4f} to {high:+.4f}, does not contain 0.")
    else:
        verdict = f"No difference shown: the 95 % interval of A − B, {low:+.4f} to {high:+.4f}, contains 0."
    return Table("Bootstrap difference", ["", "value"],
                 [["mean A − B", round(mean_a - mean_b, 4)], ["2.5 %", round(low, 4)], ["97.5 %", round(high, 4)],
                  ["paired", "yes" if paired else "no"], ["mean A", round(mean_a, 4)], ["mean B", round(mean_b, 4)]],
                 note=verdict)


@box(name="Correlate", group="Science", needs="pandas")
def correlate(table: Table) -> Table:
    """Correlations between the numeric columns of a table (pandas), e.g.
    a swept noise level against fitness."""
    frame = convert(table, "pandas.DataFrame").select_dtypes("number")
    if frame.shape[1] < 2:
        raise ValueError("The table needs at least two numeric columns")
    matrix = frame.corr()
    rows = [[row, *[round(float(v), 3) for v in matrix.loc[row]]] for row in matrix.index]
    return Table("Correlations", ["", *matrix.columns], rows)


@box(name="Plot", group="Science", needs="matplotlib")
def plot(scores: list[Scores] = (), tables: list[Table] = (),
         x: Column = "", y: Column = "") -> Figure:
    """Bars per model for scores, histograms for bootstrap tables, or column
    *y* of a table against column *x*: bars when *x* holds names (a model per
    bar), a line when it holds numbers (a sweep). Once *x* and *y* are set
    they decide the figure, whatever else is connected. The figure is a
    matplotlib figure, converted to SVG; its code is this box's code.

    x: a column of the table to put on the x axis
    y: the column to plot against it
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    scores, tables = list(scores), list(tables)
    if tables and x and y:
        for table in tables:
            for name in (x, y):
                if name not in table.columns:
                    raise ValueError(f"{table.name} has no column {name!r}; it has {', '.join(table.columns)}")
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for t in tables for v in t.column(x))
        for table in tables:
            values = [float(v) for v in table.column(y)]
            if numeric:
                ax.plot(table.column(x), values, marker="o", label=table.name)
            else:
                ax.bar([str(v) for v in table.column(x)], values, label=table.name)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if len(tables) > 1:
            ax.legend(fontsize=8)
        caption = f"{y} against {x}."
    elif scores:
        metrics = [m for m in dict.fromkeys(k for s in scores for k in s.metrics)
                   if all(isinstance(s.metrics.get(m, 0.0), (int, float)) for s in scores)]
        width = 0.8 / max(1, len(metrics))
        for j, metric in enumerate(metrics):
            ax.bar([i + j * width for i in range(len(scores))], [float(s.metrics.get(metric, 0)) for s in scores],
                   width=width, label=metric)
        ax.set_xticks([i + 0.4 - width / 2 for i in range(len(scores))])
        ax.set_xticklabels([s.model for s in scores], rotation=15, ha="right")
        shown = [float(s.metrics.get(m, 0)) for s in scores for m in metrics]
        if shown and all(0.0 <= v <= 1.0 for v in shown):       # scores in [0, 1]: the whole scale
            ax.set_ylim(0, 1.05)
        ax.legend(fontsize=8)
        caption = f"{len(scores)} models, {len(metrics)} scores each."
    elif tables:
        for table in tables:
            if table.samples:
                ax.hist(table.samples, bins=12, alpha=0.6, label=table.name)
        ax.set_xlabel("fitness over bootstrap samples")
        ax.legend(fontsize=8)
        caption = f"{len(tables)} bootstrap distribution{'s' if len(tables) != 1 else ''}."
    else:
        raise ValueError("Connect scores, or tables with samples, or a table and set both x and y")
    fig.tight_layout()
    figure = convert(fig, Figure)
    plt.close(fig)
    figure.caption = caption
    flow.note(f"matplotlib {matplotlib.__version__}: {caption}")
    return figure


@box(name="Bubble chart", group="Science", needs="matplotlib")
def bubble_chart(table: Table, x: Column = "cases", y: Column = "compliance (%)", size: Column = "",
                 colour: Column = "", log_x: bool = True) -> Figure:
    """One bubble per row: *x* against *y*, its area by *size* and its colour
    by *colour* (a column of names), as in a compliance-per-vendor chart.

    x: the column across (a number)
    y: the column up (a number)
    size: a column of numbers for the bubbles' area (empty: all the same)
    colour: a column of names to colour by (empty: one colour)
    log_x: a logarithmic x axis, for counts that span orders of magnitude
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for name in (x, y, size, colour):
        if name and name not in table.columns:
            raise ValueError(f"{table.name} has no column {name!r}; it has {', '.join(table.columns)}")
    xs, ys = [float(v) for v in table.column(x)], [float(v) for v in table.column(y)]
    sizes = [float(v or 0) for v in table.column(size)] if size else [1.0] * len(xs)
    biggest = max(sizes, default=1.0) or 1.0
    areas = [12 + 900 * s / biggest for s in sizes]
    names = [str(v) for v in table.column(colour)] if colour else [""] * len(xs)
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    order = sorted(set(names), key=lambda n: -names.count(n))
    for index, name in enumerate(order):
        picked = [i for i, n in enumerate(names) if n == name]
        ax.scatter([xs[i] for i in picked], [ys[i] for i in picked], s=[areas[i] for i in picked],
                   alpha=0.55, edgecolors="black", linewidths=0.4, label=name or None, color=f"C{index % 10}")
    if log_x:
        ax.set_xscale("log")
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    if colour:
        ax.legend(fontsize=8, markerscale=0.5)
    fig.tight_layout()
    figure = convert(fig, Figure)
    plt.close(fig)
    figure.caption = f"{y} against {x}" + (f", area by {size}" if size else "") + (f", colour by {colour}" if colour else "")
    flow.note(f"{len(xs):,} bubbles")
    return figure
