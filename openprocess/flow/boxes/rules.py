"""Rule boxes: Declare constraints, discovered from a log and checked on one.

*Discover rules* reads the rules a log keeps; *Typed rules* takes rules
written by hand; *Check rules* takes either and says, for another log (or the
same one), how often each rule applies and holds, and which cases break one.
The usual workflow: discover the rules of last year's log, check this year's
against them.  The engine is :mod:`openprocess.mining.declare`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ... import flow
from ...mining import declare
from ...mining.cases import cases
from ...mining.log import EventLog
from ..box import box
from ..types import Table

_COLUMNS = ["rule", "meaning", "applies to (cases)", "holds in (cases)", "holds (%)", "broken in (cases)"]


def _rows(checks: list[declare.RuleCheck]) -> list[list]:
    return [[str(c.rule), c.rule.meaning, c.applies, c.holds, round(100 * c.share, 2), c.broken] for c in checks]


def _rules_of(table: Table) -> list[declare.Rule]:
    if "rule" not in table.columns:
        raise ValueError(f"{table.name} has no column 'rule': connect Discover rules or Typed rules")
    return [declare.parse_rule(str(text)) for text in table.column("rule")]


@box(name="Discover rules", group="Discover")
def discover_rules(log: EventLog, holds: float = 1.0, applies: float = 0.1, negative: bool = False,
                   every_rule: bool = False) -> Table:
    """The Declare rules a log keeps (Pesic, Schonenberg & van der Aalst 2007):
    "every a is followed by b", "b never comes before a", "the case starts
    with a". Every template is tried on every activity and pair; a rule is
    listed when it applies to enough cases and holds in enough of those.

    holds: the least share (0 to 1) of the cases a rule applies to in which it must hold; 1 = no exception
    applies: the least share (0 to 1) of all cases a rule must apply to, so a rule about a rare activity is not listed on one case
    negative: also the "not" rules (a and b never together, a never followed by b); there can be very many
    every_rule: also the weaker rules a stricter one implies (response when chain response holds)
    """
    simple = log.simple_log()
    checks = declare.discover(simple, holds, applies, negative, every_rule)
    by_template: dict[str, int] = {}
    for found in checks:
        by_template[found.rule.template] = by_template.get(found.rule.template, 0) + 1
    flow.note(f"{len(checks):,} rules hold in at least {100 * holds:g} % of the cases they apply to "
              f"(and apply to at least {100 * applies:g} % of the {sum(simple.values()):,} cases)")
    if by_template:
        flow.note("By template: " + ", ".join(f"{name} {count}" for name, count in by_template.items()))
    return Table(f"Rules of {log.name}", _COLUMNS, _rows(checks),
                 note="A rule applies to a case that contains what the rule is about; "
                      "'holds (%)' is of the cases it applies to.")


@box(name="Typed rules", group="Input")
def typed_rules(rules: str = "response(a, b); precedence(a, d)") -> Table:
    """Declare rules written by hand, to check a log against: template(a) or
    template(a, b), separated by semicolons. The templates: existence, init,
    end, responded_existence, co_existence, response, precedence, succession,
    alternate_response, alternate_precedence, alternate_succession,
    chain_response, chain_precedence, chain_succession, not_co_existence,
    not_succession, not_chain_succession.

    rules: e.g. init(register); response(register, pay); not_succession(ship, check)
    """
    parsed = declare.parse_rules(rules)
    if not parsed:
        raise ValueError("No rule given: write e.g. response(register, pay)")
    return Table("Typed rules", ["rule", "meaning"], [[str(rule), rule.meaning] for rule in parsed])


@dataclass
class RulesChecked:
    breaking: EventLog
    table: Table


@box(name="Check rules", group="Check")
def check_rules(log: EventLog, rules: Table) -> RulesChecked:
    """Does the log keep the rules? For every rule (from Discover rules or
    Typed rules): the cases it applies to, the cases it holds in and the
    cases that break it. The cases that break at least one rule are the
    first output, to look into further; the table is the second.
    """
    wanted = _rules_of(rules)
    checks = declare.check(log.simple_log(), wanted)
    broken_by: dict[tuple[str, ...], bool] = {}
    breaking: list[int] = []
    for case in cases(log):
        trace = tuple(case.activities)
        broken = broken_by.get(trace)
        if broken is None:
            broken = broken_by[trace] = bool(declare.broken_by(trace, wanted))
        if broken:
            breaking.append(case.index)
    kept = sum(1 for c in checks if not c.broken)
    total = sum(log.simple_log().values())
    flow.note(f"{kept} of {len(checks)} rules hold in every case they apply to; "
              f"{len(breaking):,} of {total:,} cases break at least one rule")
    worst = max(checks, key=lambda c: c.broken, default=None)
    if worst is not None and worst.broken:
        flow.note(f"Broken most often: {worst.rule} ({worst.rule.meaning}), in {worst.broken:,} cases")
    unused = [c for c in checks if not c.applies]
    if unused:
        flow.note(f"{len(unused)} rules apply to no case of this log: " + ", ".join(str(c.rule) for c in unused[:5])
                  + (" …" if len(unused) > 5 else ""))
    cases_breaking = log.filtered(breaking)
    cases_breaking.attributes["concept:name"] = f"{log.name}: cases that break a rule"
    return RulesChecked(cases_breaking, Table(f"Rules checked on {log.name}", _COLUMNS, _rows(checks),
                                              note="'holds (%)' is of the cases a rule applies to."))


__all__ = ["RulesChecked", "check_rules", "discover_rules", "typed_rules"]
