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


@box(name="Compare samples", group="Science", needs="scipy")
def compare_samples(a: Table, b: Table,
                    test: Literal["Mann–Whitney U", "t-test"] = "Mann–Whitney U") -> Table:
    """Are two samples (two bootstrap tables) from the same distribution?
    A Mann–Whitney U test, or Welch's t-test, from SciPy."""
    from scipy import stats
    if not a.samples or not b.samples:
        raise ValueError("Both tables need samples (connect two Bootstrap fitness boxes)")
    x, y = a.samples, b.samples
    if test == "t-test":
        result = stats.ttest_ind(x, y, equal_var=False)
        statistic_name = "t"
    else:
        result = stats.mannwhitneyu(x, y, alternative="two-sided")
        statistic_name = "U"
    p = float(result.pvalue)
    mean_a, mean_b = sum(x) / len(x), sum(y) / len(y)
    verdict = (f"The samples differ (p {'< 0.001' if p < 0.001 else f'= {p:.3f}'}): "
               f"{a.name if mean_a > mean_b else b.name} is higher, and the bootstrap says it is not luck."
               if p < 0.05 else f"No difference the test can see (p = {p:.3f}).")
    flow.note(f"A = {a.name} ({len(x)} samples, mean {mean_a:.3f}); B = {b.name} ({len(y)} samples, mean {mean_b:.3f})")
    return Table(test, ["", "value"], [[statistic_name, round(float(result.statistic), 3)],
                                      ["p-value", "< 0.001" if p < 0.001 else round(p, 4)],
                                      ["mean A", round(mean_a, 4)], ["mean B", round(mean_b, 4)]], note=verdict)


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
    """Bars per model for scores, histograms for bootstrap tables, or a line
    of column *y* against column *x* of a table (a sweep). The figure is a
    matplotlib figure, converted to SVG; its code is this box's code.

    x: a column of the table to put on the x axis (lines only)
    y: the column to plot against it
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.5, 3.2))
    scores, tables = list(scores), list(tables)
    if scores:
        metrics = [m for m in dict.fromkeys(k for s in scores for k in s.metrics)
                   if all(isinstance(s.metrics.get(m, 0.0), (int, float)) for s in scores)]
        width = 0.8 / max(1, len(metrics))
        for j, metric in enumerate(metrics):
            ax.bar([i + j * width for i in range(len(scores))], [float(s.metrics.get(metric, 0)) for s in scores],
                   width=width, label=metric)
        ax.set_xticks([i + 0.4 - width / 2 for i in range(len(scores))])
        ax.set_xticklabels([s.model for s in scores], rotation=15, ha="right")
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=8)
        caption = f"{len(scores)} models, {len(metrics)} scores each."
    elif tables and x and y:
        for table in tables:
            ax.plot(table.column(x), table.column(y), marker="o", label=table.name)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if len(tables) > 1:
            ax.legend(fontsize=8)
        caption = f"{y} against {x}."
    elif tables:
        for table in tables:
            if table.samples:
                ax.hist(table.samples, bins=12, alpha=0.6, label=table.name)
        ax.set_xlabel("fitness over bootstrap samples")
        ax.legend(fontsize=8)
        caption = f"{len(tables)} bootstrap distribution{'s' if len(tables) != 1 else ''}."
    else:
        raise ValueError("Connect scores, or tables with samples or an x and y column")
    fig.tight_layout()
    figure = convert(fig, Figure)
    plt.close(fig)
    figure.caption = caption
    flow.note(f"matplotlib {matplotlib.__version__}: {caption}")
    return figure
