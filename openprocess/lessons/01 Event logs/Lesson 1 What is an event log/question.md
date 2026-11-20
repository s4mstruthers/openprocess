# Lesson 1 — What is an event log?

A pizza shop takes orders on its website. Every time something happens to an
order, the computer writes a line down: *order 17 was confirmed at 18:02*.
Such a line is an **event**.

Three words carry the whole subject, so we fix them now:

- a **case** is one run of the process from start to end. Here: one order.
- an **activity** is a kind of step: *order*, *confirm*, *pay*, *bake*,
  *deliver*, *cancel*.
- an **event** is one activity happening for one case.

An **event log** is simply all the events, grouped per case. If we only look
at the *order* in which the steps happened, a case becomes a list of
activities, called a **trace**:

$$\langle order, confirm, pay, bake, deliver \rangle$$

Many orders go the same way. Instead of writing the same trace five times we
write it once with a small number: $\langle \dots \rangle^5$ means *five
cases went like this*. The shop's log $L$, also open on the right, is

$$L = [\langle order, confirm, pay, bake, deliver \rangle^5,\ \langle order, confirm, bake, pay, deliver \rangle^3,\ \langle order, cancel \rangle^2]$$

**a.** How many **cases** (orders) are in $L$?

```answer
type: number
compute: cases
hint: Add the small numbers: each one says how many orders went that way.
solution: 5 + 3 + 2 = 10 orders.
```

**b.** How many **events** are in $L$? An event is one step of one order.

```answer
type: number
compute: events
hint: The first trace has 5 steps and happened 5 times: that is 25 events.
  Do the same for the other two and add up.
solution: 5 × 5 + 3 × 5 + 2 × 2 = 25 + 15 + 4 = 44 events.
```

**c.** Which **activities** occur in $L$? Type them as a set, like
`{order, pay}`.

```answer
type: set
compute: activities
hint: Every different word that appears in a trace, each once.
```

Different orders that went exactly the same way are one **variant**. A
variant is a *way of doing the process*.

**d.** How many variants does $L$ have?

```answer
type: number
compute: variants
hint: How many different traces are written in L, whatever their small number?
solution: Three: paid then baked, baked then paid, and cancelled.
```

**e.** With which activities can an order **start**, and with which can it
**end**?

Start activities:

```answer
type: set
compute: start activities
hint: The first word of each trace.
```

End activities:

```answer
type: set
compute: end activities
hint: The last word of each trace. There are two different ones.
```

**f.** One customer ordered on Monday and again on Friday. In the log, that is…

```answer
- [ ] one case with two traces
- [x] two cases
- [ ] one event
solution: Two cases. A case is one run of the process (one order), not one
  customer. What counts as "the case" is a choice you make when you build a
  log, and here the shop chose the order.
```

Look at the log on the right: the **Variants** tab lists the three ways with
how often each happened. That list is the same thing as the notation above.
