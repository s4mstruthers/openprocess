## What changes

<!-- What someone using OpenProcess will notice, in a sentence or two. -->

## Why

<!-- The issue it closes (Closes #123), or the problem it solves. -->

## Checked

- [ ] `pytest -q` passes, and a new test fails without this change
- [ ] `ruff check .` is clean
- [ ] A line under the newest version in `CHANGELOG.md`, if people using the app will notice
- [ ] `python docs/make_docs.py` was run, if a box or a module was added, renamed or removed
- [ ] An algorithm changed: the regression set still holds, or the diff of
      `tests/regression/expected.json` is explained above
- [ ] A new algorithm or definition names its source in `openprocess/references.py`
