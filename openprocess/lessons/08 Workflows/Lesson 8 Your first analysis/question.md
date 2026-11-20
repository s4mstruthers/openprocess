# Lesson 8 — Your first analysis

Time to do it yourself. In OpenProcess an analysis is built from **boxes**
joined by lines. A box does one thing: one holds a log, one discovers a
model, one checks how well a model fits. The line from one box to the next
carries its result.

Open the **Workflow** tab on the right. One box is there already: the pizza
shop's log.

**a.** Build this analysis:

1. Press **+ Add box** (or double-click the canvas) and add **Inductive
   Miner** from *Discover*.
2. Drag from the dot on the right of the log box to the miner: the log now
   flows into the miner.
3. Add **Check fit** from *Check*. It has two dots on its left: connect the
   miner to *model*, and the log to *log*.

Click a box to see what it gives. Then press **Check** here.

```answer
type: workflow
needs: inductive_miner, check_fit
result: Check fit.metrics.fitness
answer: 1
tolerance: 0.01
points: 3
hint: Check fit needs both lines: the miner's model and the log itself. A
  box with a missing input shows which one it is waiting for.
```

**b.** Click the **Inductive Miner** box and look at its *Result*. How many
transitions with a name (not the small black ones) does the model have?

```answer
type: number
answer: 6
hint: One per activity.
solution: Six, one per activity. The black bars without a name are silent
  steps: the algorithm adds them to route tokens, and they stand for nothing
  the log records.
```

**c.** Now click the miner's **How** tab. What do you see there?

```answer
- [ ] only the finished model again
- [x] the cuts the miner made, one after the other
- [ ] the Python code of the app
solution: The cuts, in order: the same steps you did by hand in Lesson 7.
  Every box shows how it got to its result, and the Code tab shows the
  algorithm itself.
```

**d.** *Check fit* says the fitness is 1. Lesson 9 explains the number; for
now, what do you expect it to mean?

```answer
- [x] every order in the log can be played on the model from start to end
- [ ] the model has one transition per activity
- [ ] the model is drawn tidily
solution: Fitness 1 means every case of the log is a run of the model. That
  is no surprise here: the model was discovered from this very log.
```

Change something and watch: click the log box, open *Settings* and add a
strange order, for example `<order, deliver>`, to the text. Only the boxes
after the change run again, and the fitness may drop below 1. Put the log
back before you go on.
