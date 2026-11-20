# Lesson 6 — From log to model

So far somebody drew the model. **Process discovery** is letting an
algorithm draw it from the log. The oldest such algorithm is the
**α-algorithm** (alpha), and it uses nothing but the signs of Lesson 2.

The log is the pizza shop's again:

$$L = [\langle order, confirm, pay, bake, deliver \rangle^5,\ \langle order, confirm, bake, pay, deliver \rangle^3,\ \langle order, cancel \rangle^2]$$

The idea in three steps.

**Step 1: every activity becomes a transition.** The start activities get a
start place in front of them, the end activities an end place behind them.

**a.** The start activities of $L$ (the α-algorithm calls them $T_I$):

```answer
type: set
compute: alpha.T_I
```

**b.** The end activities ($T_O$):

```answer
type: set
compute: alpha.T_O
```

**Step 2: every "causes" becomes a place.** Where the footprint says
$a \rightarrow b$, the algorithm puts a place between a and b: a puts a
token there, b takes it.

**c.** After *order* comes *confirm* or *cancel*, never both ($confirm \;\#\;
cancel$). How should that look in the net?

```answer
- [x] one place after order, with arrows to confirm and to cancel
- [ ] two places after order, one for confirm and one for cancel
solution: One place. One token and two takers is a choice (Lesson 4), and a
  choice is what the log shows: an order is confirmed or cancelled. Two
  places would make two tokens, and both would happen.
```

**d.** After *confirm* come *pay* and *bake*, side by side ($pay \parallel
bake$). How should that look?

```answer
- [ ] one place after confirm, with arrows to pay and to bake
- [x] two places after confirm, one for pay and one for bake
solution: Two places. Each gets its own token, so both happen, in either
  order. That is how "‖" in the footprint turns into parallel in the net.
```

So the signs decide the shape: **#** between the followers means *one shared
place* (choice), **‖** means *a place each* (parallel).

**e.** Could the α-algorithm make a place between *order* on one side and
*cancel* and *confirm* together on the other?

```answer
type: yesno
answer: yes
hint: It may when order → cancel, order → confirm, and the two followers
  never follow each other.
solution: Yes: order → cancel, order → confirm and cancel # confirm.
```

**f.** And a place between *confirm* and *pay* and *bake* together?

```answer
type: yesno
answer: no
hint: Do pay and bake ever follow each other?
solution: No. pay ‖ bake, so they may not share a place: sharing would turn
  the parallel steps into a choice.
```

**Step 3: draw it.** Before you let the app do that:

**g.** How many transitions will the discovered net have?

```answer
type: predict
box: alpha_miner
value: net.transitions
as: set
hint: One per activity. Type the set of them.
```

Now open the log's **Discover** tab on the right (reveal it), and let the
α-algorithm run. Compare the net with the one you played in Lesson 3: it is
the same net, found from 10 orders without anyone drawing anything.
