# Lesson 10 — Alignments

Token replay gives a number. An **alignment** gives an explanation: it puts
a case and a run of the model side by side, step by step, and marks where
they part.

Write the case on the top line and a complete run of the model on the
bottom line. In every column one of three things happens:

| column | name | meaning |
|---|---|---|
| step above, same step below | **synchronous move** | log and model agree |
| step above, `≫` below | **log move** | the log did it, the model could not |
| `≫` above, step below | **model move** | the model had to do it, the log did not |

For the unpaid pizza $\langle order, confirm, bake, deliver \rangle$ on the
model on the right:

| | | | | | |
|---|---|---|---|---|---|
| **log** | order | confirm | ≫ | bake | deliver |
| **model** | order | confirm | pay | bake | deliver |

Four columns agree and one is a model move: *the model had to pay, the log
did not*. That one column is the whole diagnosis.

The **cost** of an alignment is the number of log moves and model moves.
There are always many alignments; the ones with the lowest cost are
**optimal**, and those are the ones we want: the kindest explanation of the
case.

**a.** What is the cost of the alignment above?

```answer
type: number
answer: 1
solution: One: the single model move on pay.
```

**b.** Now do it yourself for $\langle order, deliver \rangle$. Give an
optimal alignment: the log's moves on the first line, the model's on the
second, `>>` for ≫.

```answer
type: alignment
trace: order, deliver
answer: optimal
points: 3
hint: There are two good explanations. Either the order was confirmed, paid
  and baked and three steps were not recorded (three model moves), or the
  order was cancelled and the delivery should not have happened (one model
  move and one log move). Which is cheaper?
solution: "log: order, >>, deliver and model: order, cancel, >>. The model
  cancels (a model move) and the delivery is a log move: cost 2. Assuming
  confirm, pay and bake all happened unrecorded would cost 3."
```

**c.** What is the cost of an optimal alignment of $\langle order, deliver
\rangle$?

```answer
type: number
compute: alignment cost(⟨order, deliver⟩)
hint: Count the columns of your answer to b that have a ≫.
```

**d.** A case fits the model perfectly. What is the cost of its optimal
alignment?

```answer
type: number
answer: 0
solution: Zero: every column is a synchronous move.
```

**e.** Why look for the alignment with the *lowest* cost, and not just any?

```answer
- [x] it blames the case for as little as possible, so what remains is a real difference
- [ ] it is the fastest to compute
- [ ] it always has no log moves
solution: Any case can be "explained" by throwing all its steps away as log
  moves and adding a whole model run as model moves. That explains nothing.
  The cheapest alignment keeps everything that agrees, so each remaining ≫
  is something that really went differently.
```

Alignments are slower to compute than token replay, and more honest: replay
can be fooled by tokens left over from an earlier mistake. In the app, the
*Alignments* box draws them as rows of coloured arrows, one row per variant.
