# The 2AMI10 exam log: energy contracts

The event log that is part of the exam material of *Process Mining*
(2AMI10, TU/e, 2026). A company sells energy: customers apply for a new
contract online, a few pre-checks follow, the back office adds the customer's
and the meter's details and cancels the customer's previous contract, and a
manager approves or rejects the contract. **1,201 cases, 10,206 events, 10
activities, 18 variants**, from 6 June to 23 August 2022, with a timestamp
and a resource on every event.

The exam asks about the dotted charts of this log, the models that the
course's discovery algorithms give, their fitness, where the log deviates
from them, and the properties of the process. This folder is all of that as
one workflow, `Energy contracts.cpnflow`, with a group of boxes for each.

## Run it

1. Download *Exam Eventlog 2026.xes.gz* from Canvas, unzip it, and save it in
   this folder as `Exam Eventlog 2026.xes`. It is course material, so it is
   not in the repository. (OpenProcess also reads the `.gz` directly: rename
   the file in the *Exam event log* box's settings.)
2. Double-click `Energy contracts.cpnflow`, or open this folder in OpenProcess
   and click *Energy contracts* under *Analyses*. Everything runs in about a
   second. Double-click a group to open it, and click a box to see its result,
   *How* it got there, and its *Code*.

From a terminal in this folder:

```bash
openprocess run "Energy contracts.cpnflow" --report report.html
```

Add `--check` to re-run everything and compare each result with the one
recorded in the file. It passes when your copy of the log is the one the
record was made with. `make_workflow.py` builds the workflow file from
Python. Run it and it writes the same `.cpnflow`.

For the interactive dotted chart (zooming, choosing other rows and colours,
clicking a dot to see its case), click the *Exam event log* box and choose
*Open as log ›*. Its *Dotted chart* tab is the chart that the boxes below save.

## 1 · Dotted charts

| Box | Rows | x | Colour |
|---|---|---|---|
| Cases over time | case | actual time | activity |
| Cases from their start | case | time since the case started (hours) | activity |
| Cases by duration | case, shortest first | time since the case started (hours) | activity |
| Resources over time | resource | actual time | activity |
| Activities within a case | activity | share of the case's duration (%) | resource |

What they show:

* **Arrivals are steady.** The first dots of the cases form a straight
  diagonal: applications arrive at a steady rate, about 20 a weekday, and
  fewer at weekends (about 5 on a Saturday and 4 on a Sunday).
* **The back office works in batches, on working days only.** In *Cases over
  time* the back-office steps form vertical stripes, and they stop at every
  weekend.
* **Every resource has fixed working days** (*Resources over time*). SYSTEM
  and WEB are automatic and work at any hour, every day. Charles works Monday,
  Tuesday, Thursday and Friday; George works Tuesday to Friday; Mary works
  every weekday from 11:00 to 19:00; Claire works Monday to Wednesday and
  Nancy Thursday and Friday, both mornings only (9:00 to 14:00). Of the
  managers, Noel works Tuesday to Friday, and Joey decides **only on Monday
  and Tuesday, all at 15:00**: a batch.
* **How long a case takes** (*Cases from their start*). Half of the cases end
  within 33 hours, and 90 % within 86. The bands of later dots are the cases
  that met a weekend or a manager's day off. From application to approval takes a median of 33
  hours (mean 42, 90 % within 86, longest 145). Rejection takes about as long.
* **Two rounds of the file** (*Activities within a case*). *Open file* and
  *Close file* come at two points of some cases: those cases open the file a
  second time after *Start cancellation*, only to cancel the previous
  contract.

*Who does what* puts the roles in one table:

| Role | Who | Activities |
|---|---|---|
| system | SYSTEM | Receive application, Open file, Close file, Start cancellation |
| web site | WEB | pre-check |
| back office | Charles, George, Mary, Claire, Nancy | Add customer details, Add meter details, Cancel previous contract |
| managers | Noel (872 decisions), Joey (329) | Approve contract, Reject contract |

(The log writes the resource names in quotes, `"Mary"`, as ProM shows them.)

## 2 · Discovery

*Process map* (the directly-follows graph) and *Footprint* show the
relations. Then come the four miners of the course:

**Inductive Miner** (*How* shows every cut):

```
→( Receive application, pre-check,
   ↺( →( Open file, ∧( ×(τ, Add customer details), ×(τ, Add meter details), ×(τ, Cancel previous contract) ), Close file ),
      Start cancellation ),
   ×( Approve contract, Reject contract ) )
```

The case is received and pre-checked. Then the file is opened, the three
back-office steps happen in any order, and the file is closed. After *Start
cancellation* that part can repeat, and the case ends with an approval or a
rejection. Each back-office step is optional (×(τ, …)) because the second
round does only one of them.

**IMf (noise 0.2)** drops the rare behaviour: the customer and meter details
become mandatory in every round, and only *Cancel previous contract* stays
optional.

**The α-algorithm** gives a sound WF-net, but it has no silent transitions.
After *Open file* it requires all three back-office steps, in every round.

**The Heuristics Miner** (dependency 0.9) connects everything the log does,
but its net is **not sound**: it is unbounded, so tokens can pile up. The
*Soundness* box gives the firing sequence that shows it.

## 3 · Fitness

Alignments over all 1,201 cases (*Fitness of the models* compares them):

| Model | Fitness | Precision | Generalisation | Simplicity | Sound | Cases without a deviation |
|---|---:|---:|---:|---:|---|---:|
| α-algorithm | 0.944 | 0.954 | 0.966 | 0.778 | yes | 607 |
| Inductive Miner | 1.000 | 0.728 | 0.964 | 0.727 | yes | 1,201 |
| IMf 0.2 | 0.972 | 0.863 | 0.967 | 0.789 | yes | 903 |
| Heuristics Miner | 1.000 | 0.767 | 0.844 | 0.617 | **no** | 1,201 |

The Inductive Miner guarantees perfect fitness, but it pays for it in
precision: its optional steps and its loop allow far more than the 18
variants. The α-algorithm is the most precise and the least fitting. The
Heuristics Miner's net can replay every case, but token replay finds only
296 cases without missing or remaining tokens (fitness 0.955), because
token replay guesses which silent transitions to fire and leaves tokens
behind. The net is also unbounded, so a fitness of 1 here does not mean it
is a good model of the process.

## 4 · Deviations

The *Alignments* boxes show each model with the log drawn on it (✓
synchronous moves, » model moves at every transition), and every variant as
a row of chevrons. *Token replay* gives the course's p, c, m and r per variant.
No model has a single **log move**: every event the log has, each model can
do. All the deviations are **model moves**, steps that a model requires and
the log does not have:

* **α-algorithm:** *Cancel previous contract* is skipped 594 times. That is
  the 296 rejected cases that never cancel, and the 298 cases that cancel only
  in the second round. *Add customer details* and *Add meter details* are each
  skipped 298 times, in the second round, where the file is opened only to
  cancel.
* **IMf 0.2:** *Add customer details* and *Add meter details* are each skipped
  298 times, all of them in the second round after *Start cancellation*. The
  298 cases with a second round are exactly the cases that deviate (1,201 −
  903).

## 5 · The process

| Question | Box | Answer |
|---|---|---|
| Is the previous contract cancelled before an approval? | Order rule | Always: all 798 approved cases cancel first; no case is approved without cancelling |
| Do cases lose their old contract and still get rejected? | Order rule (same) | **Yes, 107**: they cancel and are then rejected, so the customer has no contract left. 296 more are rejected without cancelling |
| What happens after *Start cancellation*? | Order rule | All 298 cases then cancel, in a second Open file / Close file round, and only 607 cases cancel in the first round |
| Does one person add both the customer and the meter details? | Segregation of duties | In 330 of 1,201 cases (George 104, Charles 94, Mary 83, Claire 25, Nancy 24) |
| How long from application to decision? | Time between | Approval: median 33.1 h, mean 41.8 h; rejection: median 32.6 h, mean 41.3 h |

Approvals outnumber rejections two to one (798 to 403).
