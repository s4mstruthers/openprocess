# A real-life analysis: the BPI Challenge 2019

A purchase-to-pay process at a large multinational coatings and paints
company, as recorded in its SAP system over 2018: **251,734 purchase-order
items, 1,595,923 events, 42 activities, 628 users**, and a few dates that are
plainly wrong (1948, 1993, 2020). It is the log of the
[BPI Challenge 2019](https://icpmconference.org/2019/icpm-2019/contests-challenges/bpi-challenge-2019/)
(van Dongen, 2019, [doi:10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1)).

This folder is the whole analysis as one workflow: every number below comes
from a box whose code, settings and inputs are in `BPI Challenge 2019.cpnflow`,
and the file's record lets anyone check that they get the same numbers.

## Run it

1. Download the log from the DOI above and save it in this folder as
   `BPI_Challenge_2019.xes` (it is 728 MB, so it is not in the repository).
2. In OpenProcess, open this folder and click **BPI Challenge 2019** under
   *Analyses*; or, from a terminal in this folder:

   ```bash
   openprocess run "BPI Challenge 2019.cpnflow" --check --report report.html
   ```

   `--check` re-runs everything and compares each result with the recorded
   one (it passes when your copy of the log is the one the record was made
   with: the record names its SHA-256); `--report` writes the analysis as one
   HTML page. The whole run takes about two minutes on a laptop.

`make_workflow.py` builds the workflow file from Python, so it is also an
example of the [framework's](../../docs/framework.md) Python side: run it and
it writes the same `.cpnflow`.

## The process

Every purchase-order item follows one of four ways of matching the invoice
(the case attribute *Item Category*; the cases in the log as published):

| Item category | Cases | What has to match |
|---|---:|---|
| 3-way match, invoice before GR | 221,010 | goods receipt, invoice and order; the invoice is blocked until the goods arrive |
| 3-way match, invoice after GR | 15,182 | goods receipt, invoice and order, the invoice after the goods |
| Consignment | 14,498 | no invoice at all |
| 2-way match | 1,044 | invoice and order; no goods receipt needed |

The workflow first drops the 82 cases with a date outside 2017–2019
(*Cases dated 2017 to 2019*, a *Time frame* filter), keeping 251,652.

## 1 · A collection of models

> Is there a collection of process models which together properly describe the process in this data?

One model per item category: a *Case attribute* filter whose value is
**swept** over the four categories feeds the Inductive Miner (IMf, noise
0.2) and *Check fit*; *Sweep table* stacks the four results.

| Item category | Fitness | Precision | Generalisation | Simplicity |
|---|---:|---:|---:|---:|
| 3-way match, invoice before GR | 0.993 | 0.323 | 0.894 | 0.593 |
| 3-way match, invoice after GR | 0.936 | 0.123 | 0.637 | 0.643 |
| Consignment | 0.996 | 0.777 | 0.761 | 0.610 |
| 2-way match | 0.986 | 0.620 | 0.748 | 0.670 |

The models fit (fitness 0.94 to 1.00) but the two 3-way categories are
imprecise: they allow much more than happens. That is the price of a single
model for behaviour in which goods receipts and invoices repeat in any
number and order (one item can have many monthly invoices). Click a model's
box and *How* shows the cuts the miner made; *Code* shows the miner.

## 2 · The throughput of invoicing

> What is the throughput of the invoicing process?

*Time between* measures, per case, the time from the first *Record Invoice
Receipt* to the first *Clear Invoice* after it, per item category
(consignment has no invoices):

| Item category | Cases | Mean (days) | Median (days) | 90 % within (days) | Longest (days) |
|---|---:|---:|---:|---:|---:|
| 3-way match, invoice before GR | 173,243 | 49.1 | 42.9 | 97.9 | 341.9 |
| 3-way match, invoice after GR | 9,652 | 33.3 | 26.3 | 70.9 | 210.8 |
| 2-way match | 303 | 9.8 | 5.2 | 22.0 | 202.1 |

An invoice that comes before the goods waits for them: it takes 17 days
longer to clear (median 42.9 days against 26.3) than one that comes after
them, and eight times as long as one that needs no goods receipt at all
(5.2 days).

## 3 · Deviations, and how severe they are

> Which purchase documents stand out? Where are deviations from the processes discovered in (1), and how severe?

The largest category (invoice before goods receipt, 220,948 cases once the
noise is dropped) is
aligned with its model, move by move. The *Alignments* box aligns the 200
most frequent of its 7,815 variants, which cover 201,949 cases (91.4 %):
196,683 of them align without a single deviation. Its Result shows the
log on the model, with each transition's synchronous moves (✓), model moves
(») and log moves, and every variant as a row of chevrons. Green is a
synchronous move, yellow a log move (the log did it where the model could
not), purple a model move (the model needed it and the log does not have
it).

Per vendor (*Compliance by attribute*: cases, the share that replays on the
model without a missing or remaining token, and their total value), the
large vendors comply at 89–98 %. A few vendors with a hundred or more items
stand out:

| Vendor | Cases | Compliance | Value (EUR) |
|---|---:|---:|---:|
| vendorID_0188 | 108 | 54.6 % | 78,225 |
| vendorID_1466 | 142 | 61.3 % | 9,768 |
| vendorID_0259 | 203 | 67.0 % | 10,057,877 |
| vendorID_0571 | 102 | 73.5 % | 38,888 |
| vendorID_0148 | 169 | 77.5 % | 26,499 |

*Bubble chart* draws all 1,374 vendors: cases across (log scale),
compliance up, value as the bubble's area, colour by item type, as CKM
Analytics drew it for the challenge.

In this category the payment block should only be lifted once the goods are
in. *Order rule* finds **557 items where *Remove Payment Block* comes before
any *Record Goods Receipt***, worth 2.45 million EUR; **208 of them never get
a goods receipt at all**. Its second output is those 557 cases, as a log to
look into further.

## The challenge's findings, checked

Several teams' findings, as reported at the challenge, tested on the same
log with the general *Order rule* and *Segregation of duties* boxes. Where
the numbers differ, the teams filtered or defined their measure differently;
the box's code says exactly what is counted here.

| Finding | Reported | This analysis |
|---|---|---|
| Purchase-order items that are deleted and then reactivated (PwC) | 331 of 8,500 deleted | **331** have both (327 deleted then reactivated, 4 the other way round), of 8,835 deleted |
| The vendor's invoice comes before the purchase order (PwC) | 3,416 cases | 3,620 cases (*Vendor creates invoice* before *Create Purchase Order Item*) |
| A debit memo before the invoice (PwC) | 4,000 cases, mostly one vendor | 2,364 cases with *Vendor creates debit memo* before any *Vendor creates invoice*, 634 of them without an invoice |
| Payment block lifted before the goods receipt (LogToValue) | 358 items, 77 without a goods receipt, over 1.5 M EUR | 557 items, 208 without a goods receipt, 2.45 M EUR (all items of the category, not only those after an invoice) |
| The same user records and clears the invoice (Agilos) | users 013, 004, 015, 012, 019, 020, 007, 001, 006 | 2,585 items, by 15 users; the most value: users 015, 004, 001, 013, 012, 020 (the same people) |

## What the run shows about OpenProcess

* **Size.** Reading the 728 MB file takes 12 to 15 s and about 250 MB of
  memory (the log is kept in columns, not as 1.6 million objects); every box
  after it takes under a second except the miner (1 s), the model check (11 s
  for replay and precision on 221,000 cases), compliance per vendor (5 s) and
  the alignments (50 s for 200 variants).
* **Reproducibility.** Re-running the workflow in a new Python session gives
  the same fingerprint for every one of its 18 results (`--check` passes).
* **Transparency.** Every box's *Code* tab shows the few lines of the box and
  the algorithm it calls; the measuring boxes are in
  [`openprocess/flow/boxes/measure.py`](../../openprocess/flow/boxes/measure.py).
