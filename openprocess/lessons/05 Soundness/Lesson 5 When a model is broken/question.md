# Lesson 5 — When a model is broken

Somebody redrew the pizza shop's model and made a slip. The net on the right
is their version. Play it before you read on (click the green transitions):
try to get a confirmed order delivered.

A model of a process with one start and one end is called a **workflow
net**: one place where every case begins (*i*) and one where it ends (*o*).
Such a net is **sound** when three things hold:

1. **You can always finish.** Wherever a case is, there is still a way to
   reach the end.
2. **You finish cleanly.** When the token arrives in *o*, nothing is left
   lying around elsewhere.
3. **Nothing is useless.** Every transition can fire in some run.

A marking where the case is not finished and nothing can fire is a
**deadlock**: the case is stuck for ever.

**a.** Is the net on the right sound?

```answer
type: yesno
compute: sound
hint: Play order, confirm, and keep going.
```

**b.** Give a sequence of transitions that ends in a **deadlock**.

```answer
type: trace
ends: deadlock
hint: Take the confirm branch and do everything you can.
solution: "order, confirm, pay. The token is in c4, deliver also wants one
  in c5, and nothing can put it there: pay took the only token out of c2, so
  bake cannot fire any more."
```

**c.** Which transitions can **never** fire in this net?

```answer
type: set
compute: dead transitions
hint: Which step needs two tokens that are never there together?
solution: "{deliver}. It needs a token in c4 and one in c5, but confirm
  makes only one token, and it goes to pay or to bake, never to both."
```

**d.** Which of the three conditions does the net break?

```answer
- [x] 1: you can always finish
- [ ] 2: you finish cleanly
- [x] 3: nothing is useless
hint: Two of them.
solution: Conditions 1 and 3. After confirm the case can never reach the
  end (1), and deliver never fires (3). Condition 2 holds: the only run
  that does reach o, order then cancel, leaves nothing behind.
```

**e.** Repair the net in the editor on the right, so that it is sound and
behaves like the pizza shop of Lesson 3: after *confirm*, *pay* and *bake*
both happen, in any order, and then *deliver*.

```answer
type: net
start: net.pnml
answer: answer.pnml
sound: yes
points: 3
hint: The drawer made a choice where parallel was meant (Lesson 4). pay and
  bake share one place, c2, so only one of them gets the token. Give bake a
  place of its own: add a place, draw an arc from confirm to it and from it
  to bake, and delete the arc from c2 to bake (click it, then Backspace).
solution: pay and bake must each have their own place after confirm. Add a
  place c3, with arcs confirm to c3 and c3 to bake, and remove the arc from
  c2 to bake. Then confirm puts down two tokens, pay and bake both fire, and
  deliver gets both of its tokens.
```

Soundness is the first thing to check about any model, drawn by hand or
found by an algorithm: a model that can get stuck describes no working
process. In the app the **Analysis** tab checks it for you and shows the run
that goes wrong.
