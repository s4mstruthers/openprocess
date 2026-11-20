# Lesson 9 — Does reality follow the model?

The pizza shop's model (on the right, the sound one of Lesson 3) says how
orders *should* go. A month later the shop looks at what its staff really
did. The new log $L$ (also on the right):

$$L = [\langle order, confirm, pay, bake, deliver \rangle^6,\ \langle order, confirm, bake, deliver \rangle^3,\ \langle order, deliver \rangle]$$

Comparing a log with a model is called **conformance checking**. The
simplest way is to **replay** each case on the model as a token game, and
when the model says "that step is not allowed now", to cheat and count the
cheating:

- **p**roduced: every token put into a place (the first token in *i* counts);
- **c**onsumed: every token taken out (the last one out of *o* counts);
- **m**issing: tokens you had to *invent* because a step happened that the
  model was not ready for;
- **r**emaining: tokens *left lying* at the end, because the model expected
  a step that never came.

A case that fits has $m = 0$ and $r = 0$.

Take $\langle order, confirm, bake, deliver \rangle$: nobody paid.

- *order*: takes the token from *i* (put there at the start), puts one in *c1*.
- *confirm*: takes *c1*, puts tokens in *c2* and *c3*.
- *bake*: takes *c3*, puts one in *c5*.
- *deliver*: wants *c4* and *c5*. *c5* is there. *c4* is empty, because *pay*
  never happened: **invent** that token (1 missing). deliver puts one in *o*.
- The end takes the token from *o*. The token in *c2* was never used: 1
  remaining.

**a.** Do the counting for all three kinds of case. Fill in p, c, m and r.

```answer
type: replay
compute: replay
points: 4
hint: Count every arrow head as a token produced and every arrow tail as a
  token consumed, for the transitions that fire, plus one produced at the
  start and one consumed at the end. For ⟨order, deliver⟩, deliver finds
  both of its places empty.
```

**b.** Which of these does a **missing** token point at?

```answer
- [x] a step happened that the model did not allow at that moment
- [ ] a step the model expected never happened
- [ ] the log has too few cases
solution: Missing: the log did something the model was not ready for
  (delivering an unpaid pizza). Remaining is the other one: the model was
  left waiting for something (the payment).
```

The four counts of all cases together give one number between 0 and 1, the
**fitness**:

$$\text{fitness} = \tfrac12\left(1 - \frac{m}{c}\right) + \tfrac12\left(1 - \frac{r}{p}\right)$$

Use the totals over the whole log: multiply each row by its number of cases
before you add.

**c.** What is the fitness of $L$ on the model? Two decimals are enough.

```answer
type: number
compute: fitness
tolerance: 0.005
points: 2
hint: Add up p, c, m and r over all 10 cases (6 times the first row, 3 times
  the second, once the third), then fill in the formula.
```

**d.** In three of the ten orders the pizza was delivered without payment.
Is the model wrong, or the shop?

```answer
type: open
solution: Conformance checking cannot say; it only shows the difference.
  If unpaid delivery is allowed (pay at the door), the model is out of date
  and should be changed. If it is not, the model is right and the shop has
  found three orders to look into. Deciding which is the analyst's job, and
  it is the reason to check at all.
```

The net's **Conformance** tab on the right paints the missing and remaining
tokens on the places once you reveal it: you can see where it goes wrong.
