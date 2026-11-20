# Lesson 3 — The token game

A log says what *did* happen. A **model** says what *can* happen. The models
of this course are **Petri nets**, and a Petri net is a board game with three
kinds of pieces:

- **transitions** (squares) are the steps: *order*, *pay*, …
- **places** (circles) are the waiting spots between steps;
- **tokens** (black dots) lie in places and show where the case is right now.

The rule of the game is one sentence:

> A transition may **fire** when every place with an arrow *into* it holds a
> token. Firing takes one token from each of those places and puts one token
> in every place it has an arrow *to*.

The net on the right is the pizza shop. It starts with one token in the
place *i*. Where the tokens are is called the **marking**; we write it as a
list of places, so the start is $[i]$.

You can play it: the net on the right is in **Step through**, where the
transitions that may fire are green and a click fires one. **Reset** puts
the token back at the start.

**a.** In the marking $[i]$, which transitions may fire (are **enabled**)?

```answer
type: set
compute: enabled([i])
hint: Only one transition has all its input places filled.
```

**b.** Fire *order*. What is the marking now? Type it like `[c1]`.

```answer
type: marking
compute: fire(order, [i])
hint: order takes the token from i and puts one in the place after it.
```

**c.** Which transitions are enabled now?

```answer
type: set
compute: enabled([c1])
hint: Two transitions take their token from c1.
solution: {cancel, confirm}. Both want the same token, so only one of them
  can have it: this is a choice.
```

**d.** From $[c1]$ fire *confirm*. What is the marking?

```answer
type: marking
compute: fire(confirm, [c1])
hint: confirm has two arrows going out, so it puts down two tokens.
solution: "[c2, c3]. One step made two tokens: from here two things can go
  on side by side."
```

**e.** In $[c2, c3]$, is *deliver* enabled?

```answer
type: yesno
answer: no
hint: deliver needs a token in c4 and a token in c5.
solution: No. deliver has two input places, c4 and c5, and both are empty.
  It has to wait until pay and bake are both done.
```

**f.** Give a complete run of the net: a sequence of transitions from the
start until the token is in *o* and nothing else is left. Type it like
`order, cancel`.

```answer
type: trace
ends: final
hint: The shortest one has two steps.
```

**g.** After the whole sequence *order, confirm, bake*, what is the marking?

```answer
type: marking
compute: fire(⟨order, confirm, bake⟩, [i])
hint: After confirm you have [c2, c3]. bake moves one of the two tokens.
solution: "[c2, c5]: the pizza is baked (token in c5) and the payment is
  still waiting (token in c2)."
```

That is all there is to a Petri net. Everything in the next lessons is this
game.
