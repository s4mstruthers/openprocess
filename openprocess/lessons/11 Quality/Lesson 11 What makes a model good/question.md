# Lesson 11 — What makes a model good?

Three models of the pizza shop are in this lesson's folder, and the shop's
first log $L$ (Lesson 1) is on the right:

- **shop.pnml**: the model you know. Cancel or confirm; then pay and bake in
  any order; then deliver.
- **strict.pnml**: one straight line. order, confirm, pay, bake, deliver,
  always in that order. No cancelling.
- **anything.pnml**: after *order*, any of confirm, pay, bake and cancel may
  happen, as often as you like, in any order; then deliver, or just stop.

Open them with *Open Folder* in the ⋯ menu and play each one.

**Fitness** you know: how much of the log can the model replay?

**a.** Rank the three models by their fitness on $L$, best first. Models
that fit equally well may go in either order.

```answer
type: ranking
over: shop.pnml, strict.pnml, anything.pnml
by: fitness
points: 2
hint: Which models can replay ⟨order, cancel⟩? Which can replay bake before
  pay?
```

**b.** *anything.pnml* fits the log perfectly. Is it therefore a good model
of the pizza shop?

```answer
type: yesno
answer: no
solution: No. It also allows delivering an unbaked pizza, paying five times
  and cancelling after baking. A model that allows everything tells you
  nothing.
```

That is the second measure. **Precision** asks the opposite question of
fitness: how much does the model allow that the log never shows? A model
that allows far too much is called a **flower model**, after its shape: one
place in the middle with every activity as a petal around it.

**c.** Which of the three has the lowest precision?

```answer
- [ ] shop.pnml
- [ ] strict.pnml
- [x] anything.pnml
solution: anything.pnml: almost all of what it allows never happens.
```

**d.** *strict.pnml* allows only one run, and that run is in the log. Its
precision is perfect. What is wrong with it?

```answer
- [x] its fitness is low: it cannot replay half of what the shop does
- [ ] it has too many places
- [ ] nothing
solution: It is precise and does not fit. 5 of the 10 orders (bake before
  pay, and the cancelled ones) are not runs of it.
```

So the two pull against each other. Allow more and fitness goes up while
precision goes down; allow less and it is the other way round. A good model
is high on both.

Two more measures finish the list:

- **Simplicity**: of two models that say the same, the smaller one is
  better. People have to read it.
- **Generalisation**: the log is a sample. Next month a case will come that
  is not in it. A good model already allows the sensible ones.

**e.** Next month the shop's log shows $\langle order, confirm, bake, pay,
deliver \rangle$ for the first time ever. A model that had learnt the old
log *by heart*, one separate path per variant seen, would call it a
deviation. Which measure was that model bad at?

```answer
- [ ] fitness
- [ ] precision
- [ ] simplicity
- [x] generalisation
solution: Generalisation. It fitted the old log exactly and allowed nothing
  new: it described the sample, not the process. shop.pnml, with pay and
  bake in parallel, allows the new order without ever having seen it.
```

**f.** Which model would you hand to the shop's owner?

```answer
- [x] shop.pnml
- [ ] strict.pnml
- [ ] anything.pnml
solution: shop.pnml: it replays every order (fitness 1), allows nothing the
  shop does not do (high precision), and fits on one screen.
```

In the app, the *Check fit* box of Lesson 8 gives all four numbers for a
model and a log.
