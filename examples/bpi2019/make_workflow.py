"""Build the BPI Challenge 2019 analysis as a workflow file.

    python make_workflow.py                   # writes "BPI Challenge 2019.cpnflow" beside this file
    openprocess run "BPI Challenge 2019.cpnflow" --save --report report.html

Each part of the challenge is a group of boxes on the canvas (double-click a
group to open it), and every number in the answer comes from a box whose
code, settings and inputs are recorded in the file.  See README.md for the
questions, the log and what the analysis finds.
"""

from pathlib import Path

from openprocess.flow import Sweep, Workflow, save

HERE = Path(__file__).resolve().parent
LOG = "BPI_Challenge_2019.xes"                      # put the log beside this file under this name
VALUE = "Cumulative net worth (EUR)"
CATEGORIES = ["3-way match, invoice before GR", "3-way match, invoice after GR", "Consignment", "2-way match"]


def build() -> Workflow:
    wf = Workflow("BPI Challenge 2019 · purchase to pay")

    # The log, without the cases whose dates are noise (1948, 1993, ..., 2020).
    log = wf.add("open_log", {"file": LOG}, (0, 360), title="BPI Challenge 2019")
    clean = wf.add("time_frame", {"start": "2017-01-01", "end": "2019-12-31", "mode": "contained"}, (260, 360),
                   title="Cases dated 2017 to 2019")
    wf.connect(log, clean)
    process_map = wf.add("directly_follows", {}, (520, 600), title="Process map")
    wf.connect(clean, process_map)

    # 1. A collection of models: one per item category (the four ways of matching).
    category = wf.add("case_attribute", {"attribute": "Item Category", "values": Sweep(tuple(CATEGORIES))},
                      (520, 0), title="One item category")
    miner = wf.add("inductive_miner", {"noise": 0.2}, (780, 0), title="Model of the category")
    fit = wf.add("check_fit", {}, (1040, 0), title="How well it fits")
    table = wf.add("sweep_table", {}, (1300, 0), title="Models per category")
    wf.connect(clean, category)
    wf.connect(category, miner)
    wf.connect(miner, fit, "model")
    wf.connect(category, fit, "log")
    wf.connect(fit, table)
    wf.group([category.id, miner.id, fit.id, table.id], "1 · Models per category")

    # 2. The throughput of invoicing, per item category.
    throughput = wf.add("time_between", {"start": "Record Invoice Receipt", "end": "Clear Invoice",
                                         "by": "Item Category"}, (520, 180), title="Invoice to clearing")
    wf.connect(clean, throughput)
    wf.group([throughput.id], "2 · Invoicing time")

    # 3. Deviations in the largest category: where, how severe, and which vendors.
    before = wf.add("case_attribute", {"attribute": "Item Category", "values": CATEGORIES[0]}, (520, 360),
                    title="Invoice before goods receipt")
    model = wf.add("inductive_miner", {"noise": 0.2}, (780, 300), title="Its model")
    aligned = wf.add("alignments", {"variants": 200}, (1040, 300), title="Deviations, move by move")
    vendors = wf.add("compliance_by", {"attribute": "Vendor", "value": VALUE, "colour": "Item Type"}, (1040, 440),
                     title="Compliance per vendor")
    bubbles = wf.add("bubble_chart", {"x": "cases", "y": "compliance (%)", "size": VALUE, "colour": "Item Type"},
                     (1300, 440), title="Vendors: cases, compliance, value")
    blocked = wf.add("order_rule", {"first": "Remove Payment Block", "then": "Record Goods Receipt", "value": VALUE},
                     (780, 460), title="Payment block lifted before goods receipt")
    wf.connect(clean, before)
    wf.connect(before, model)
    wf.connect(model, aligned, "model")
    wf.connect(before, aligned, "log")
    wf.connect(model, vendors, "model")
    wf.connect(before, vendors, "log")
    wf.connect(vendors, bubbles)
    wf.connect(before, blocked)
    wf.group([before.id, model.id, aligned.id, vendors.id, bubbles.id, blocked.id], "3 · Deviations")

    # Findings the challenge's winners reported, checked on the same log.
    rules = [("Vendor creates debit memo", "Vendor creates invoice", "Debit memo before the invoice"),
             ("Delete Purchase Order Item", "Reactivate Purchase Order Item", "Deleted, then reactivated"),
             ("Vendor creates invoice", "Create Purchase Order Item", "Invoice before the purchase order")]
    found = []
    for row, (first, then, title) in enumerate(rules):
        rule = wf.add("order_rule", {"first": first, "then": then, "value": VALUE}, (520, 740 + 130 * row),
                      title=title)
        wf.connect(clean, rule)
        found.append(rule.id)
    duties = wf.add("segregation_of_duties", {"first": "Record Invoice Receipt", "second": "Clear Invoice",
                                              "by": "User", "value": VALUE}, (520, 740 + 130 * len(rules)),
                    title="Same user records and clears")
    wf.connect(clean, duties)
    wf.group(found + [duties.id], "Findings to check")
    return wf


if __name__ == "__main__":
    workflow = build()
    target = HERE / "BPI Challenge 2019.cpnflow"
    save(workflow, target, folder=HERE)
    print(f"Wrote {target.name}: {len(workflow.nodes)} boxes in {len(workflow.groups)} groups")
