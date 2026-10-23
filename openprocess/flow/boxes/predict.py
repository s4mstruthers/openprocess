"""Predict boxes: the prediction pipeline of predictive process monitoring.

*Prefixes* turns a log into a dataset of prefix encodings with a label
(the next activity, the remaining time, or the outcome); *Split by time*
makes a train and a test set by case start, never at random; *Evaluate
predictions* scores a model's predictions per prefix length.  A model of
your own goes in between as a box that takes a :class:`~..types.Dataset`
and gives a :class:`~..types.Predictor` (a scikit-learn estimator fits the
shape; see ``examples/boxes/next_activity_sklearn.py``).  *Frequency
model* is a baseline that needs no library.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ... import flow
from ...mining.log import EventLog
from ..box import box
from ..types import Dataset, Predictions, Predictor, Table

END = "⟂"       # the label of "the case ends here"


@box(name="Prefixes", group="Predict")
def prefixes(log: EventLog, label: Literal["next activity", "remaining time", "outcome"] = "next activity",
             encoding: Literal["index", "one-hot", "frequency"] = "frequency",
             min_prefix: int = 1, max_prefix: int = 20) -> Dataset:
    """One row per prefix of every case, encoded as features, with the
    label to predict: the next activity (⟂ when the case ends), the
    remaining time in hours, or the outcome (the last activity). For the
    outcome the whole case is not a prefix: its last activity *is* the
    outcome, so that row would give the answer away.

    encoding: frequency = how often each activity occurred so far, plus the last activity; index = the last k activities as indices; one-hot = the last k activities as 0/1 columns
    min_prefix: the shortest prefix to include
    max_prefix: the longest prefix to include (longer cases give more rows)
    """
    activities = sorted({a for seq in log.sequences() for a in seq})
    index = {a: i for i, a in enumerate(activities)}
    k = max_prefix if encoding != "frequency" else 1
    if encoding == "frequency":
        names = [f"count {a}" for a in activities] + ["prefix length"] + [f"last is {a}" for a in activities]
    elif encoding == "index":
        names = [f"activity −{i}" for i in range(k, 0, -1)]
    else:
        names = [f"−{i} is {a}" for i in range(k, 0, -1) for a in activities]
    X, y, case_ids, lengths, starts = [], [], [], [], []
    classifier = log.default_classifier()
    for trace in log:
        events = [e for e in trace if classifier.accepts(e)]
        seq = [classifier.label(e) for e in events]
        times = [e.timestamp for e in events]
        start = times[0] if times and times[0] else None
        end_time = times[-1] if times and times[-1] else None
        longest = len(seq) - 1 if label == "outcome" else len(seq)      # outcome: no peeking at the end
        for n in range(min_prefix, min(longest, max_prefix) + 1):
            prefix = seq[:n]
            if encoding == "frequency":
                counts = Counter(prefix)
                row = [counts[a] for a in activities] + [n] + [1 if prefix[-1] == a else 0 for a in activities]
            elif encoding == "index":
                window = ([-1] * k + [index[a] for a in prefix])[-k:]
                row = window
            else:
                window = ([None] * k + prefix)[-k:]
                row = [1 if w == a else 0 for w in window for a in activities]
            if label == "next activity":
                target = seq[n] if n < len(seq) else END
            elif label == "outcome":
                target = seq[-1]
            else:
                if end_time is None or times[n - 1] is None:
                    continue
                target = (end_time - times[n - 1]).total_seconds() / 3600
            X.append(row)
            y.append(target)
            case_ids.append(trace.case_id)
            lengths.append(n)
            starts.append(start)
    kind = "regression" if label == "remaining time" else "classification"
    classes = sorted(set(y)) if kind == "classification" else None
    flow.note(f"{len(X)} prefixes from {len(log)} cases, {len(names)} features, label: {label}")
    return Dataset(X, y, names, label, kind, case_ids, lengths, starts, activities, classes,
                   name=f"Prefixes of {log.name}")


@dataclass
class Split:
    train: Dataset
    test: Dataset


@box(name="Split by time", group="Predict")
def split_by_time(dataset: Dataset, train_fraction: float = 0.7) -> Split:
    """Train and test sets by case start: the earliest cases train, the
    latest test, so the model never sees the future. Without timestamps the
    cases are split in log order."""
    cases = list(dict.fromkeys(dataset.case_ids))
    starts = {c: s for c, s in zip(dataset.case_ids, dataset.case_starts)}
    if any(isinstance(starts.get(c), datetime) for c in cases):
        cases.sort(key=lambda c: (starts.get(c) is None, starts.get(c) or datetime.min))
    cut = max(1, int(round(len(cases) * train_fraction)))
    train_cases = set(cases[:cut])
    train = [i for i, c in enumerate(dataset.case_ids) if c in train_cases]
    test = [i for i, c in enumerate(dataset.case_ids) if c not in train_cases]
    flow.note(f"{len(train_cases)} cases ({len(train)} prefixes) train, "
              f"{len(cases) - len(train_cases)} cases ({len(test)} prefixes) test")
    return Split(dataset.subset(train, "train"), dataset.subset(test, "test"))


class FrequencyModel(Predictor):
    """Predicts, for each last activity, what most often came next (or the
    mean remaining time): the baseline every paper has to beat."""

    name = "Frequency model"

    def __init__(self) -> None:
        self.table: dict = {}
        self.default = None
        self.kind = "classification"

    def _key(self, row, dataset: Dataset):
        """The last activity of the prefix, from whichever encoding made the row."""
        names, n = dataset.feature_names, len(dataset.activities)
        if names and n and (names[-1].startswith("last is ") or names[-1].startswith("−1 is ")):
            last = list(row[-n:])                     # frequency and one-hot: one 0/1 column per activity
            return next((a for a, flag in zip(dataset.activities, last) if flag), None)
        return tuple(row[-1:])                        # index: the last column is the last activity

    def fit(self, dataset: Dataset) -> "FrequencyModel":
        self.kind = dataset.kind
        seen: dict = {}
        for row, label in zip(dataset.X, dataset.y):
            seen.setdefault(self._key(row, dataset), []).append(label)
        if dataset.kind == "classification":
            self.table = {key: Counter(labels).most_common(1)[0][0] for key, labels in seen.items()}
            self.default = Counter(dataset.y).most_common(1)[0][0] if dataset.y else None
        else:
            self.table = {key: sum(labels) / len(labels) for key, labels in seen.items()}
            self.default = sum(dataset.y) / len(dataset.y) if dataset.y else 0.0
        return self

    def predict(self, dataset: Dataset) -> list:
        return [self.table.get(self._key(row, dataset), self.default) for row in dataset.X]


@box(name="Frequency model", group="Predict")
def frequency_model(train: Dataset) -> Predictor:
    """The baseline: for each last activity, the most frequent next activity
    (or the mean remaining time) in the training set."""
    model = FrequencyModel().fit(train)
    flow.note(f"{len(model.table)} last activities seen in {len(train)} prefixes")
    return model


@box(name="Predict", group="Predict")
def predict(model: Predictor, dataset: Dataset) -> Predictions:
    """Applies a fitted model to a dataset (the test set). A Predictor of
    OpenProcess's own takes the dataset; any other model (a scikit-learn
    estimator) is given its feature rows, ``dataset.X``."""
    values = list(model.predict(dataset) if isinstance(model, Predictor) else model.predict(dataset.X))
    flow.note(f"{len(values)} predictions by {getattr(model, 'name', type(model).__name__)}")
    return Predictions(values, dataset, getattr(model, "name", type(model).__name__))


@box(name="Evaluate predictions", group="Predict")
def evaluate_predictions(predictions: Predictions) -> Table:
    """Accuracy (classification) or mean absolute error in hours
    (regression), overall and per prefix length."""
    dataset = predictions.dataset
    if len(predictions.values) != len(dataset):
        raise ValueError("The predictions and the dataset have different lengths")
    by_length: dict[int, list] = {}
    for value, truth, length in zip(predictions.values, dataset.y, dataset.prefix_lengths or [0] * len(dataset)):
        by_length.setdefault(length, []).append((value, truth))
    classification = dataset.kind == "classification"

    def score(pairs) -> float:
        if classification:
            return sum(1 for v, t in pairs if v == t) / len(pairs)
        return sum(abs(float(v) - float(t)) for v, t in pairs) / len(pairs)
    overall = score(list(zip(predictions.values, dataset.y)))
    metric = "accuracy" if classification else "MAE (hours)"
    rows = [["all", len(dataset), round(overall, 4)]]
    rows += [[str(length), len(pairs), round(score(pairs), 4)] for length, pairs in sorted(by_length.items())]
    flow.note(f"{metric} over {len(dataset)} prefixes: {overall:.3f}")
    return Table(f"{predictions.name}: {metric}", ["prefix length", "prefixes", metric], rows,
                 note=f"{metric} of {predictions.name} on {dataset.name}: {overall:.3f}.")
