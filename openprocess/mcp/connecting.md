# Connecting an AI assistant

OpenProcess comes with an **MCP server**: a connector that lets Claude (or
any other assistant that speaks the Model Context Protocol) use OpenProcess
the way you do. It can summarise and filter logs, discover models, check
conformance, analyse nets, draw diagrams, build and run workflows, write
boxes of your own, write and check exercises for Learn, and simulate
coloured nets. It opens what it made in OpenProcess so you see it.

## Set it up (once)

Install the connector's one dependency with OpenProcess:

```
pip install "openprocess[app,mcp]"
```

Then tell your assistant about it, with one command:

| Assistant | Command |
|---|---|
| Claude Desktop | `openprocess mcp install claude-desktop`, then quit and reopen Claude |
| Claude Code | `openprocess mcp install claude-code` |
| Cursor | `openprocess mcp install cursor` |
| Anything else | `openprocess mcp install print` prints the JSON to paste into its settings |

The command adds an `openprocess` entry to the assistant's configuration
and keeps everything else that is there (the old file is kept beside it as
`….before-openprocess`). `openprocess mcp status` says which assistants have
it.

For a client that connects to a URL instead of starting a program,
`openprocess mcp --http` serves on `http://127.0.0.1:8765/mcp`.

## What it is like to use

Ask in your own words, naming your files:

- *"Summarise `Week 2/plane_boarding.xes` and show me its dotted chart."*
- *"Discover a model with the Inductive Miner, check how well it fits, and
  open it in OpenProcess."*
- *"Is `order_handling.pnml` sound? If not, show me the firing sequence that
  breaks it."*
- *"Build me an analysis that compares the α-algorithm and IMf at noise 0.2 on
  this log, and save it so I can rerun it."*
- *"Write three exercises about token replay for Week 7, with answers the app
  checks."*
- *"Draw the process map of the exam log."*

## What the assistant is told

Every assistant that connects is told, before anything else, how
OpenProcess works and what it expects:

1. **Cite.** Every result comes with the published works the algorithm
   follows; the assistant repeats them and never invents a source.
2. **Reproduce.** Results come with the command or the Python that gives
   the same numbers. Analyses you keep are workflow files (`.cpnflow`) with
   a record that `openprocess run FILE --check` verifies later.
3. **Show how.** Results carry the derivation the app shows on the *How*
   tab: the α-algorithm's eight steps, the Inductive Miner's cuts, the
   replay per variant.
4. **Use the framework.** Analyses are workflows of boxes; a new algorithm
   is a `@box` function in the folder's `boxes/`, so it is reused and
   recorded like any other.
5. **Be transparent.** It works in your folder and opens what it made in
   OpenProcess.
6. **Numbers come from the tools,** with their method and scope.

## Reviewing what it changed

Every file the assistant writes (a model, a filtered log, a workflow, an
exercise, a box) is **journaled**: what it replaced is kept as a backup in
the folder's `.openprocess-changes/` and the change is listed. While there
are changes you have not looked at, the status bar shows
**AI changed 3 files · Review**. Click it (or *Help ▸ Review AI Changes…*) to
see each change as a diff, with **Undo** (the file goes back as it was) and
**Keep**. Nothing waits for you: the files are already there and open as
usual, so the review never gets in the way. The assistant never overwrites a
file it did not make unless you ask it to.

## Without an assistant

The same operations are plain Python in `openprocess.mcp.api`, and most of
them are also `openprocess` commands (`openprocess mine …`, `openprocess run …`,
`openprocess exercises …`). From a notebook, `workflow.open_in_studio()` opens
a workflow in the running app, and `my_box.publish()` adds a box defined in
the notebook to the folder open in OpenProcess.
