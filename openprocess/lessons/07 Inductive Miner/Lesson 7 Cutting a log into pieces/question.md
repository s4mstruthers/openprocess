# Lesson 7 — Cutting a log into pieces

The α-algorithm is easy to follow, but real logs break it: one odd case and
the net it draws may get stuck. The algorithm used most today, the
**Inductive Miner**, always gives a sound model. It works like someone
tidying a room: split the log into parts, then split each part again, until
every part is one activity.

Each split is called a **cut**, and there are four kinds:

| cut | sign | means |
|---|---|---|
| sequence | → | first this part, then that part |
| choice | × | this part or that part, never both |
| parallel | ∧ | both parts, mixed in any order |
| loop | ↺ | this part, and then maybe back again |

The log (also on the right):

$$L = [\langle order, confirm, pay, bake, deliver \rangle^5,\ \langle order, confirm, bake, pay, deliver \rangle^3,\ \langle order, cancel \rangle^2]$$

**a.** Every case starts with *order*, and everything else comes after it.
Which kind of cut separates *order* from the rest?

```answer
- [x] sequence →
- [ ] choice ×
- [ ] parallel ∧
- [ ] loop ↺
solution: Sequence. First order, then all the rest, in every case.
```

**b.** Type that first cut: the sign, then each part as a set. For example
`→ {a} {b, c}`.

```answer
type: cut
compute: im.cut
hint: Two parts. The first has one activity, the second the other five.
```

Now the algorithm looks only at the second part: what is left of each trace
when *order* is taken off.

**c.** What is that smaller log? Type it like `[<a, b>^2, <c>]`.

```answer
type: log
compute: im.split(2)
points: 2
hint: Cross out order in every trace of L and keep the small numbers.
```

**d.** In this smaller log a case is either just *cancel*, or it has the
other four activities. Which cut is that? Type it.

```answer
type: cut
compute: im.cut(2)
hint: Never both in one case: that is a choice, ×. One part is {cancel}.
```

The algorithm goes on like this inside *confirm, pay, bake, deliver* (first
*confirm*, then *pay* and *bake* in parallel, then *deliver*). Writing every
cut inside the one before gives a **process tree**: the whole model on one
line.

**e.** Type the process tree of $L$. Read `→(a, b)` as "a then b", `×(a, b)`
as "a or b" and `∧(a, b)` as "a and b in any order". You may type `seq`,
`xor` and `and` for the signs.

```answer
type: tree
compute: im.tree
points: 3
hint: Start with →(order, …). The second part is ×(cancel, …). Inside that
  comes →(confirm, ∧(…), deliver).
solution: →(order, ×(cancel, →(confirm, ∧(pay, bake), deliver))). Read it
  aloud and it is the pizza shop. order, then either cancel, or confirm
  followed by pay and bake in any order and then deliver.
```

**f.** A process tree can always be turned into a Petri net. Can that net
get stuck in a deadlock?

```answer
type: yesno
answer: no
solution: No. Every piece of a tree (a sequence, a choice, a parallel part,
  a loop) begins and ends properly, so the whole does. That is the
  Inductive Miner's guarantee: its model is always sound.
```
