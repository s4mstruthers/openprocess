# Contributing to OpenProcess

Thank you for wanting to help. OpenProcess is a small project with one aim:
process mining, Petri nets and workflows in one app where every algorithm can
be read, every result can be reproduced and every step can be taught.
Contributions that serve that aim are welcome, from a one-line bug report to
a new discovery algorithm.

## Ways to contribute

- **Report a wrong or unclear result.** This is the most useful thing you can
  do. Open an [issue](https://github.com/s4mstruthers/openprocess/issues) with
  the log or net (or the smallest one that shows it), what OpenProcess said,
  and what you expected and why. A result that differs from ProM, PM4Py or
  CPN Tools is worth an issue even when you are not sure which is right.
- **Add an algorithm as a box.** A box is a plain Python function with type
  hints and a docstring; see [Adding a box](#adding-a-box) below.
- **Write or improve exercises.** Exercise packs are folders of Markdown;
  [docs/exercise-packs.md](docs/exercise-packs.md) is the guide.
- **Fix a bug or improve the app.** For anything larger than a small fix,
  open an issue first so the approach can be agreed before you write it.
- **Improve the documentation.** If something took you a while to work out,
  it is probably missing from the README or `docs/`.

## Setting up

You need Python 3.10 or newer and git.

```bash
git clone https://github.com/s4mstruthers/openprocess.git
```

```bash
cd openprocess
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e ".[app,dev]"
```

`openprocess-studio` starts the app from your checkout. The README's
[From source](README.md#from-source) section has the Windows commands and the
system libraries Qt needs on Linux.

## Running the tests

```bash
pytest -q
```

The suite includes the GUI tests, which draw their windows offscreen. On a
machine without a display, set `QT_QPA_PLATFORM=offscreen` first. A pull
request runs the same suite on GitHub; please run it locally before you open
one.

## What a change should come with

- **A test.** A fix comes with a test that fails without it. A new algorithm
  comes with tests on small logs or nets whose answer can be checked by hand,
  and where a textbook or paper gives a worked example, that example.
- **An honest result.** If an algorithm cannot decide something (a state
  space that was cut off, a search that gave up), the result must say so. A
  confident wrong answer is the worst bug this project can have.
- **A source, or none.** Every algorithm and definition names the published
  work it follows, in `openprocess/references.py`. Cite only what you have
  read and can point to. If you cannot find a source for something, do not
  guess one: say that it is the project's own convention, or open an issue.
- **A changelog line**, when people using the app will notice the change. Add
  it under the newest version in [CHANGELOG.md](CHANGELOG.md), written for
  users: what they will see, not how it was done.
- **Up-to-date generated docs.** Some pages are generated from the code and a
  test fails when they are stale:

  ```bash
  python docs/make_docs.py
  ```

  regenerates `docs/boxes.md` and the code map in `docs/architecture.md`.
  After editing a definition or a reference, also run
  `python -m openprocess.mining.definitions > docs/definitions.md` or
  `python -m openprocess.references > docs/references.md`.

## Adding a box

A box is what an analysis is built from. It stays a plain function:

```python
from openprocess import flow
from openprocess.flow import EventLog, PetriNet, box


@box(name="My miner", group="Discover")
def my_miner(log: EventLog, threshold: float = 0.2) -> PetriNet:
    """One line on what it does and whose method it follows.

    threshold: what the setting means
    """
    ...
    flow.note("what the How tab should say about this run")
    return net
```

The type hints are its inputs and output, the other parameters are its
settings, and the docstring is its help text. The app's Result, How and Code
tabs, and the record that makes a run reproducible, come from that with no
further work.

- To try an idea, put the file in the `boxes/` subfolder of the folder you
  have open in the app; it appears under *Yours*.
  [examples/boxes/](examples/boxes/) has worked examples.
- To contribute it to OpenProcess itself, add it to the fitting module in
  `openprocess/flow/boxes/`, keep the algorithm in `openprocess/mining/` (so
  it also works from a script, with nothing but numpy), and add its source to
  `openprocess/references.py`.

[docs/framework.md](docs/framework.md) describes the box API in full, and
[docs/architecture.md](docs/architecture.md) maps the code and says which
package may import which (a test checks it).

## Pull requests

1. Branch from `main`.
2. Keep the pull request to one change; two fixes are two pull requests.
3. Say in the description what changed, why, and how you checked it.
4. The tests must pass on GitHub before a merge.

Version numbers and releases are handled by the maintainer; you do not need
to bump the version.

## Using AI tools

Much of OpenProcess's interface and plumbing was written with the help of AI
coding assistants, and you are welcome to use them too. Two things hold
however the code was written:

- You are responsible for what you submit: read it, run it, and be able to
  explain it.
- An algorithm must be checked against its source (the paper, the textbook
  example, another implementation), not against what an assistant says the
  source says. The same goes for citations.

## Licence

OpenProcess is released under the [MIT licence](LICENSE). By contributing you
agree that your contribution is released under the same licence.
