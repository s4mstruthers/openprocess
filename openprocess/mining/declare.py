"""Rules a process should keep: Declare constraints, checked and discovered
(Pesic, Schonenberg & van der Aalst 2007).

A Petri net says what *may* happen, step by step.  A rule says one thing
that must hold, and leaves the rest free: "every *register* is eventually
followed by *pay*", "*ship* never comes before *check*".  Declare is a
catalogue of such rules, each a template with one or two activities.  For a
process with a lot of freedom a handful of rules says more than a net with
an arc between everything.

The templates
-------------
With ``a`` and ``b`` activities and σ one case (a sequence of activities):

=========================  ===================================================
template                   holds in σ when
=========================  ===================================================
``existence(a)``           a occurs
``init(a)``                σ starts with a
``end(a)``                 σ ends with a
``responded_existence``    if a occurs, b occurs too (before or after)
``co_existence``           a and b occur together or not at all
``response``               every a is followed, some time later, by a b
``precedence``             every b has an a somewhere before it
``succession``             response and precedence
``alternate_response``     every a is followed by a b before the next a
``alternate_precedence``   every b has an a before it, after the previous b
``alternate_succession``   both alternate rules
``chain_response``         every a is directly followed by b
``chain_precedence``       every b is directly preceded by a
``chain_succession``       both chain rules
``not_co_existence``       a and b never occur in the same case
``not_succession``         no a is ever followed, later, by a b
``not_chain_succession``   no a is directly followed by b
=========================  ===================================================

Each row from *response* to *chain_succession* is a stricter form of the
plain rule above it: chain implies alternate implies plain, and succession
implies its response and its precedence.

When a rule says nothing
------------------------
"Every a is followed by a b" holds in a case with no a at all, but that case
is no evidence for it.  A rule **applies** to a case that contains what the
rule is about: a for the response rules, b for the precedence rules, a or b
for the succession, co-existence and "not" rules, and every case for
existence, init and end.  A case the rule does not apply to neither
supports nor breaks it.  Two numbers are reported for every rule:

* the cases it applies to, and
* of those, the cases it holds in.

A rule is *discovered* when it applies to at least a given share of the
cases and holds in at least a given share of those.  Reporting the two
numbers side by side is OpenProcess's own convention; the templates and
their meaning are Declare's.

Discovery tries every template on every activity (or pair), which is
quadratic in the number of activities.  A stricter rule that was found
hides the weaker rules it implies, so the list says each thing once.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .log import SimpleLog

#: template -> how many activities it takes
ARITY = {
    "existence": 1, "init": 1, "end": 1,
    "responded_existence": 2, "co_existence": 2,
    "response": 2, "precedence": 2, "succession": 2,
    "alternate_response": 2, "alternate_precedence": 2, "alternate_succession": 2,
    "chain_response": 2, "chain_precedence": 2, "chain_succession": 2,
    "not_co_existence": 2, "not_succession": 2, "not_chain_succession": 2,
}
TEMPLATES = tuple(ARITY)
NEGATIVE = ("not_co_existence", "not_succession", "not_chain_succession")

#: What a rule says, in words; ``{a}`` and ``{b}`` are its activities.
MEANING = {
    "existence": "{a} occurs",
    "init": "the case starts with {a}",
    "end": "the case ends with {a}",
    "responded_existence": "if {a} occurs, {b} occurs too",
    "co_existence": "{a} and {b} occur together or not at all",
    "response": "every {a} is followed later by {b}",
    "precedence": "every {b} has {a} before it",
    "succession": "every {a} is followed later by {b}, and every {b} has {a} before it",
    "alternate_response": "every {a} is followed by {b} before the next {a}",
    "alternate_precedence": "every {b} has {a} before it, after the previous {b}",
    "alternate_succession": "{a} and {b} alternate, {a} first",
    "chain_response": "every {a} is directly followed by {b}",
    "chain_precedence": "every {b} is directly preceded by {a}",
    "chain_succession": "{a} is always directly followed by {b}, and {b} directly preceded by {a}",
    "not_co_existence": "{a} and {b} never occur in the same case",
    "not_succession": "{a} is never followed later by {b}",
    "not_chain_succession": "{a} is never directly followed by {b}",
}

#: stricter template -> the weaker ones it implies for the same (a, b)
_IMPLIES = {
    "chain_succession": ("chain_response", "chain_precedence", "alternate_succession"),
    "alternate_succession": ("alternate_response", "alternate_precedence", "succession"),
    "succession": ("response", "precedence", "co_existence"),
    "chain_response": ("alternate_response",),
    "alternate_response": ("response",),
    "chain_precedence": ("alternate_precedence",),
    "alternate_precedence": ("precedence",),
    "response": ("responded_existence",),
    "not_co_existence": ("not_succession",),
    "not_succession": ("not_chain_succession",),
}


@dataclass(frozen=True)
class Rule:
    """One constraint: a template and its activities (``b`` is "" for the
    templates that take one)."""

    template: str
    a: str
    b: str = ""

    def __post_init__(self) -> None:
        if self.template not in ARITY:
            raise ValueError(f"There is no rule template {self.template!r}; the templates are: "
                             + ", ".join(TEMPLATES))
        if ARITY[self.template] == 2 and not self.b:
            raise ValueError(f"{self.template} takes two activities: {self.template}(a, b)")
        if ARITY[self.template] == 1 and self.b:
            raise ValueError(f"{self.template} takes one activity: {self.template}(a)")

    def __str__(self) -> str:
        return f"{self.template}({self.a}, {self.b})" if self.b else f"{self.template}({self.a})"

    @property
    def meaning(self) -> str:
        return MEANING[self.template].format(a=self.a, b=self.b)


@dataclass
class RuleCheck:
    """A rule and how a log does on it."""

    rule: Rule
    #: cases the rule applies to (see the module's "When a rule says nothing")
    applies: int
    #: of those, the cases it holds in
    holds: int
    case_count: int

    @property
    def broken(self) -> int:
        return self.applies - self.holds

    @property
    def share(self) -> float:
        """The share of the cases it applies to in which it holds (1.0 when it applies to none)."""
        return self.holds / self.applies if self.applies else 1.0

    @property
    def coverage(self) -> float:
        """The share of all cases it applies to."""
        return self.applies / self.case_count if self.case_count else 0.0


# ---------------------------------------------------------------------------
# One rule, one case: the definitions, written plainly
# ---------------------------------------------------------------------------
def applies_to(rule: Rule, trace: tuple[str, ...]) -> bool:
    """Is ``trace`` a case the rule says something about?"""
    template, a, b = rule.template, rule.a, rule.b
    if ARITY[template] == 1:
        return True
    if template in ("responded_existence", "response", "alternate_response", "chain_response"):
        return a in trace
    if template in ("precedence", "alternate_precedence", "chain_precedence"):
        return b in trace
    return a in trace or b in trace


def holds_in(rule: Rule, trace: tuple[str, ...]) -> bool:
    """Does the rule hold in ``trace``?  (True also for a case it does not apply to.)"""
    template, a, b = rule.template, rule.a, rule.b
    if template == "existence":
        return a in trace
    if template == "init":
        return bool(trace) and trace[0] == a
    if template == "end":
        return bool(trace) and trace[-1] == a
    if template == "responded_existence":
        return a not in trace or b in trace
    if template == "co_existence":
        return (a in trace) == (b in trace)
    if template == "not_co_existence":
        return not (a in trace and b in trace)
    if template == "succession":
        return _response(trace, a, b) and _precedence(trace, a, b)
    if template == "alternate_succession":
        return _alternate_response(trace, a, b) and _alternate_precedence(trace, a, b)
    if template == "chain_succession":
        return _chain_response(trace, a, b) and _chain_precedence(trace, a, b)
    if template == "not_succession":
        return not any(x == a and b in trace[i + 1:] for i, x in enumerate(trace))
    if template == "not_chain_succession":
        return not any(x == a and y == b for x, y in zip(trace, trace[1:]))
    return _PLAIN[template](trace, a, b)


def _response(trace, a, b) -> bool:
    return all(b in trace[i + 1:] for i, x in enumerate(trace) if x == a)


def _precedence(trace, a, b) -> bool:
    return all(a in trace[:i] for i, x in enumerate(trace) if x == b)


def _alternate_response(trace, a, b) -> bool:
    waiting = False                       # an a still waiting for its b
    for x in trace:
        if x == a:
            if waiting:
                return False
            waiting = True
        elif x == b:
            waiting = False
    return not waiting


def _alternate_precedence(trace, a, b) -> bool:
    ready = False                         # an a seen since the last b
    for x in trace:
        if x == a:
            ready = True
        elif x == b:
            if not ready:
                return False
            ready = False
    return True


def _chain_response(trace, a, b) -> bool:
    return all(i + 1 < len(trace) and trace[i + 1] == b for i, x in enumerate(trace) if x == a)


def _chain_precedence(trace, a, b) -> bool:
    return all(i > 0 and trace[i - 1] == a for i, x in enumerate(trace) if x == b)


_PLAIN = {"response": _response, "precedence": _precedence,
          "alternate_response": _alternate_response, "alternate_precedence": _alternate_precedence,
          "chain_response": _chain_response, "chain_precedence": _chain_precedence}


# ---------------------------------------------------------------------------
# Checking given rules
# ---------------------------------------------------------------------------
def check(log: SimpleLog, rules: Iterable[Rule]) -> list[RuleCheck]:
    """Every rule with the cases it applies to and holds in, in the order given."""
    total = sum(log.values())
    checks = []
    for rule in rules:
        applies = holds = 0
        for trace, count in log.items():
            if applies_to(rule, trace):
                applies += count
                if holds_in(rule, trace):
                    holds += count
        checks.append(RuleCheck(rule, applies, holds, total))
    return checks


def broken_by(trace: tuple[str, ...], rules: Iterable[Rule]) -> list[Rule]:
    """The rules ``trace`` breaks."""
    return [rule for rule in rules if applies_to(rule, trace) and not holds_in(rule, trace)]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _pairs_holding(trace: tuple[str, ...]) -> dict[tuple[str, str], frozenset[str]]:
    """For every ordered pair of different activities that both occur in
    ``trace``: the two-activity templates that hold, worked out from the
    positions of the two (the same answers as :func:`holds_in`, without
    reading the trace once per rule)."""
    positions: dict[str, list[int]] = {}
    for index, activity in enumerate(trace):
        positions.setdefault(activity, []).append(index)
    found: dict[tuple[str, str], frozenset[str]] = {}
    for a, pa in positions.items():
        for b, pb in positions.items():
            if a == b:
                continue
            holding = {"responded_existence", "co_existence"}
            response = pb[-1] > pa[-1]
            precedence = pa[0] < pb[0]
            # every a has a b before the next a (or the end)
            alternate_response = response and all(
                bisect_left(pb, nxt) > bisect_right(pb, cur) for cur, nxt in zip(pa, pa[1:]))
            # every b has an a after the previous b (or the start)
            alternate_precedence = precedence and all(
                bisect_left(pa, nxt) > bisect_right(pa, cur) for cur, nxt in zip(pb, pb[1:]))
            following = set(pb)
            chain_response = all(i + 1 in following for i in pa)
            preceding = set(pa)
            chain_precedence = all(j - 1 in preceding for j in pb)
            if response:
                holding.add("response")
            if precedence:
                holding.add("precedence")
            if response and precedence:
                holding.add("succession")
            if alternate_response:
                holding.add("alternate_response")
            if alternate_precedence:
                holding.add("alternate_precedence")
            if alternate_response and alternate_precedence:
                holding.add("alternate_succession")
            if chain_response:
                holding.add("chain_response")
            if chain_precedence:
                holding.add("chain_precedence")
            if chain_response and chain_precedence:
                holding.add("chain_succession")
            if not pa[0] < pb[-1]:
                holding.add("not_succession")
            if not any(i + 1 in following for i in pa):
                holding.add("not_chain_succession")
            found[(a, b)] = frozenset(holding)
    return found


#: two-activity templates by what makes them apply: a, b, or either
_APPLIES_A = ("responded_existence", "response", "alternate_response", "chain_response")
_APPLIES_B = ("precedence", "alternate_precedence", "chain_precedence")
_APPLIES_EITHER = ("co_existence", "succession", "alternate_succession", "chain_succession",
                   "not_co_existence", "not_succession", "not_chain_succession")


def discover(log: SimpleLog, holds: float = 1.0, applies: float = 0.1, negative: bool = False,
             all_rules: bool = False) -> list[RuleCheck]:
    """The rules that apply to at least ``applies`` of the cases and hold in at
    least ``holds`` of the cases they apply to (both shares, 0 to 1).

    ``negative``: also the "not" rules, which a log with many activities that
    never meet has a great many of.  ``all_rules``: keep the weaker rules a
    stricter one implies (by default only the strictest of each family is
    listed).  Sorted: the rules that apply to most cases first.
    """
    if not 0 <= holds <= 1 or not 0 <= applies <= 1:
        raise ValueError("holds and applies are shares between 0 and 1")
    total = sum(log.values())
    if not total:
        return []
    cases_with: Counter = Counter()
    starts: Counter = Counter()
    ends: Counter = Counter()
    both: Counter = Counter()                 # unordered pair -> cases with both
    holding: Counter = Counter()              # (template, a, b) -> cases with both in which it holds
    for trace, count in log.items():
        present = set(trace)
        for activity in present:
            cases_with[activity] += count
        if trace:
            starts[trace[0]] += count
            ends[trace[-1]] += count
        for (a, b), templates in _pairs_holding(trace).items():
            if a < b:
                both[(a, b)] += count
            for template in templates:
                holding[(template, a, b)] += count

    def together(a: str, b: str) -> int:
        return both[(a, b) if a < b else (b, a)]

    checks: dict[Rule, RuleCheck] = {}

    def consider(rule: Rule, applied: int, held: int) -> None:
        if applied and applied >= applies * total and held >= holds * applied:
            checks[rule] = RuleCheck(rule, applied, held, total)

    activities = sorted(cases_with)
    for a in activities:
        consider(Rule("existence", a), total, cases_with[a])
        consider(Rule("init", a), total, starts[a])
        consider(Rule("end", a), total, ends[a])
    for a in activities:
        for b in activities:
            if a == b:
                continue
            with_both = together(a, b)
            either = cases_with[a] + cases_with[b] - with_both
            # With one of the two missing, a rule that needs both is broken; the
            # "not" rules hold.  So only the cases with both need looking at.
            for template in _APPLIES_A:
                consider(Rule(template, a, b), cases_with[a], holding[(template, a, b)])
            for template in _APPLIES_B:
                consider(Rule(template, a, b), cases_with[b], holding[(template, a, b)])
            for template in _APPLIES_EITHER:
                if template in NEGATIVE:
                    if not negative:
                        continue
                    held = either - with_both if template == "not_co_existence" \
                        else either - with_both + holding[(template, a, b)]
                else:
                    held = holding[(template, a, b)]
                if template in ("co_existence", "not_co_existence") and a > b:
                    continue                       # symmetric: listed once
                consider(Rule(template, a, b), either, held)
    if not all_rules:
        for rule in list(checks):
            for weaker in _implied(rule):
                checks.pop(weaker, None)
    return sorted(checks.values(), key=lambda c: (-c.applies, -c.holds, TEMPLATES.index(c.rule.template),
                                                  c.rule.a, c.rule.b))


def _implied(rule: Rule) -> list[Rule]:
    """Every weaker rule that follows from ``rule``."""
    found: list[Rule] = []
    pending = [rule]
    while pending:
        current = pending.pop()
        for template in _IMPLIES.get(current.template, ()):
            weaker = Rule(template, current.a, current.b)
            if template in ("co_existence",) and weaker.a > weaker.b:
                weaker = Rule(template, weaker.b, weaker.a)
            if weaker not in found:
                found.append(weaker)
                pending.append(weaker)
        if current.template == "precedence":            # every b has a before it: if b occurs, a does
            found.append(Rule("responded_existence", current.b, current.a))
        if current.template == "co_existence":
            found += [Rule("responded_existence", current.a, current.b),
                      Rule("responded_existence", current.b, current.a)]
    return found


# ---------------------------------------------------------------------------
# Rules as text
# ---------------------------------------------------------------------------
_RULE = re.compile(r"^\s*([a-z_]+)\s*\((.*)\)\s*$")


def parse_rule(text: str) -> Rule:
    """``response(register, pay)`` or ``init(register)``.  An activity may
    contain anything but a comma."""
    match = _RULE.match(text)
    if not match:
        raise ValueError(f"A rule is written template(a) or template(a, b), e.g. response(register, pay); "
                         f"got {text.strip()!r}")
    arguments = [part.strip() for part in match.group(2).split(",")]
    if not all(arguments) or len(arguments) > 2:
        raise ValueError(f"{text.strip()!r}: a rule takes one or two activities, separated by a comma")
    return Rule(match.group(1), *arguments)


def parse_rules(text: str) -> list[Rule]:
    """Rules separated by semicolons or line ends; empty parts are skipped."""
    return [parse_rule(part) for part in re.split(r"[;\n]", text) if part.strip()]


__all__ = ["ARITY", "MEANING", "NEGATIVE", "TEMPLATES", "Rule", "RuleCheck", "applies_to", "broken_by",
           "check", "discover", "holds_in", "parse_rule", "parse_rules"]
