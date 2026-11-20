# Lesson 4 — Choice and parallel

Two shapes in a net matter more than all others. You met both in Lesson 3.

**Choice.** One place, arrows to two transitions. There is one token and two
takers, so only one can have it. After *order*, the shop **either** cancels
**or** confirms.

**Parallel.** One transition, arrows to two places. It makes two tokens, and
each goes its own way. After *confirm*, the customer pays **and** the shop
bakes, in whichever order they get to it. The step that waits for both
(*deliver*) has two arrows coming in.

A way to remember: **a place splits into a choice, a transition splits into
parallel.**

The net on the right is the same pizza shop.

**a.** Can this happen in the net: *order, cancel, deliver*?

```answer
type: yesno
answer: no
hint: Play it. Where is the token after cancel?
solution: No. cancel puts the token in o, the end. deliver needs tokens in
  c4 and c5, and there are none.
```

**b.** Can this happen: *order, confirm, bake, pay, deliver*?

```answer
type: yesno
answer: yes
solution: Yes. After confirm, pay and bake are both enabled and neither
  takes the other's token, so either can go first.
```

**c.** Can this happen: *order, confirm, pay, deliver, bake*?

```answer
type: yesno
answer: no
hint: What does deliver need?
solution: No. deliver needs a token in c5, which only bake puts there.
  Parallel means "in any order", not "skip one".
```

**d.** How many different complete runs (from $[i]$ to $[o]$) does the net
have?

```answer
type: number
answer: 3
hint: One run through the choice's first branch, and the other branch can be
  walked in two orders.
solution: Three: order cancel; order confirm pay bake deliver; order confirm
  bake pay deliver.
```

Compare with the log of Lesson 1: it had exactly these three variants. The
model allows what the log shows, and nothing else. We will make that precise
in Lessons 9 to 11.

**e.** Give every marking the net can be in, the start included. Type them
like `[i], [c1], [c2, c3]`.

```answer
type: markings
compute: reachable
points: 2
hint: Play every run and write down each marking you pass. The parallel part
  has four: neither done, only pay done, only bake done, both done.
```

**f.** Suppose the shop added a third parallel step after *confirm*, say
*box* (fold the pizza box). How many orders of *pay*, *bake* and *box* would
the model allow?

```answer
type: number
answer: 6
hint: Three things in a row: 3 choices for the first, 2 for the second, 1
  for the last.
solution: 3 × 2 × 1 = 6. That is why parallel steps make logs look messy:
  a few of them give a great many variants, all of the same simple process.
```
