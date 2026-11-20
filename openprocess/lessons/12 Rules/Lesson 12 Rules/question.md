# Lesson 12 — Rules

A Petri net describes a process by saying, step by step, what may happen
next. That suits a process with a fixed route. Some processes have hardly
any route (think of treating a patient): there, a net ends up as a flower
model, or as spaghetti.

The other way to describe a process is to say only **what must always
hold**, and leave the rest free. Such a statement is a **rule**. A few
kinds, with *a* and *b* standing for activities:

| rule | says |
|---|---|
| `init(a)` | every case starts with a |
| `existence(a)` | a happens in every case |
| `response(a, b)` | every a is followed, some time later, by a b |
| `precedence(a, b)` | every b has an a somewhere before it |
| `not_co_existence(a, b)` | a and b never happen in the same case |

The pizza shop's log (on the right):

$$L = [\langle order, confirm, pay, bake, deliver \rangle^5,\ \langle order, confirm, bake, pay, deliver \rangle^3,\ \langle order, cancel \rangle^2]$$

**a.** Does `init(order)` hold in $L$?

```answer
type: yesno
answer: yes
solution: Yes: all ten cases start with order.
```

**b.** Does `existence(deliver)` hold in every case of $L$?

```answer
type: yesno
answer: no
hint: Look at the third variant.
solution: No. The two cancelled orders have no deliver. It holds in 8 of 10
  cases.
```

**c.** Does `response(confirm, deliver)` hold: is every confirmed order
delivered later?

```answer
type: yesno
answer: yes
solution: Yes. And note what happens with the cancelled orders: they have
  no confirm at all, so the rule says nothing about them. A rule only
  applies to the cases that contain what it is about.
```

**d.** Does `precedence(pay, bake)` hold: is every pizza paid before it is
baked?

```answer
type: yesno
answer: no
hint: Look at the second variant.
solution: No. In three orders bake comes before pay. If the owner wants
  payment first, these are the three cases to look at, and a rule found
  them without any model.
```

**e.** Which rule says "a cancelled order is never delivered"?

```answer
- [ ] response(cancel, deliver)
- [ ] precedence(cancel, deliver)
- [x] not_co_existence(cancel, deliver)
solution: not_co_existence(cancel, deliver): the two never share a case.
  response(cancel, deliver) would say the opposite, that every cancelled
  order is delivered afterwards.
```

Finding the rules by hand is slow. Let the app do it.

**f.** In the **Workflow** tab, add **Discover rules** (from *Discover*) and
connect the log to it. Then add **Check rules** (from *Check*) and connect
both the log and the rules to it. Click the boxes to read what they found.

```answer
type: workflow
needs: discover_rules, check_rules
points: 3
hint: Check rules has two dots on the left: log and rules. The rules come
  from the Discover rules box.
```

**g.** In *Discover rules*, set **holds** to `0.8` (Settings tab): rules may
now have exceptions in up to 20 % of the cases they apply to. *Check rules*
then gives the cases that break one of them. Which orders are they?

```answer
- [ ] the paid-then-baked orders
- [ ] the baked-then-paid orders
- [x] the cancelled orders
solution: The cancelled orders. With exceptions allowed, "deliver happens",
  "confirm happens" and the like become rules (they hold in 8 of 10 cases),
  and the two cancelled orders are the ones that break them. Rules that
  nearly always hold, and the cases that break them, are often the most
  interesting thing in a log.
```

That is the course. You can read a log, play and repair a model, follow two
discovery algorithms, measure how well a model fits, explain a deviation
with an alignment, and describe a process by rules. The demo exercises
(*Learn ▸ Open Demo Exercises*) go on from here.
