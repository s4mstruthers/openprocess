# Lesson 2 — What follows what?

The simplest question you can ask of a log: **which step comes right after
which?** We say *b directly follows a* when somewhere in the log, a is
immediately followed by b in the same case. We write that as the pair
$(a, b)$.

In $\langle order, cancel \rangle$ the pair $(order, cancel)$ occurs. In
$\langle order, confirm, pay, bake, deliver \rangle$ there are four pairs:
$(order, confirm)$, $(confirm, pay)$, $(pay, bake)$, $(bake, deliver)$.

The log is the same as in Lesson 1:

$$L = [\langle order, confirm, pay, bake, deliver \rangle^5,\ \langle order, confirm, bake, pay, deliver \rangle^3,\ \langle order, cancel \rangle^2]$$

**a.** Does *bake* directly follow *confirm* somewhere in $L$?

```answer
type: yesno
answer: yes
hint: Look at the second trace.
solution: Yes: in ⟨order, confirm, bake, pay, deliver⟩, bake comes right
  after confirm.
```

**b.** Does *deliver* directly follow *confirm* somewhere in $L$?

```answer
type: yesno
answer: no
hint: "Directly" means with nothing in between.
solution: No. Deliver always comes later than confirm, but never right after
  it: pay and bake are in between.
```

**c.** Give **all** directly-follows pairs of $L$, like `(order, confirm),
(confirm, pay)`. There are eight.

```answer
type: set
compute: dfg
points: 2
hint: Walk through each of the three traces and write down every two
  neighbours. Pairs you meet twice count once.
```

Draw every activity as a box and every pair as an arrow and you have a
**process map** (its proper name is *directly-follows graph*). Open the
**Process map** tab on the right: it is exactly your answer to c, as a
picture. The numbers on the arrows say how often.

Now compare two activities at a time. Four things can be true:

| we write | when | it suggests |
|---|---|---|
| $a \rightarrow b$ | b directly follows a, never the other way round | a causes b |
| $a \leftarrow b$ | the other way round | b causes a |
| $a \parallel b$ | both orders occur | they happen side by side |
| $a \;\#\; b$ | neither order occurs | they have nothing to do with each other |

**d.** *pay* and *bake*: which is it?

```answer
- [ ] pay → bake
- [ ] pay ← bake
- [x] pay ‖ bake
- [ ] pay # bake
solution: Both orders occur: pay then bake (first trace) and bake then pay
  (second trace). So pay ‖ bake: the shop bakes while the customer pays.
```

**e.** *cancel* and *pay*: which is it?

```answer
- [ ] cancel → pay
- [ ] cancel ← pay
- [ ] cancel ‖ pay
- [x] cancel # pay
solution: Neither ever directly follows the other: an order is either
  cancelled or paid. So cancel # pay.
```

The table of all these signs is the **footprint** of the log.

**f.** Fill in these cells of the footprint. Click a cell to change it; the
row is the first activity, the column the second.

```answer
type: footprint
pairs: (order, confirm), (order, cancel), (confirm, pay), (pay, bake), (pay, deliver), (cancel, deliver)
points: 2
hint: For each cell ask two questions: does the column ever come right after
  the row? Does the row ever come right after the column?
```

The footprint is all the α-algorithm of Lesson 6 looks at. The log's
**Footprint** tab shows the whole table once you reveal it.
