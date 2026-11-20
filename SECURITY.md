# Security

## Reporting a problem

Please report a security problem privately, not in a public issue: open
[a private advisory](https://github.com/s4mstruthers/openprocess/security/advisories/new)
(the repository's **Security** tab ▸ **Report a vulnerability**). Say what
you found, how to reproduce it and which version you used. You will get an
answer as soon as the maintainer can give one; this is a small project
without a response-time promise.

Fixes go into the next release. Only the latest release is supported: the
app offers it at launch (Help ▸ Check for Updates…).

## What OpenProcess does that is worth knowing

- **Boxes are code.** A workflow runs Python functions: OpenProcess's own,
  those of installed packages, and any `.py` file in the `boxes/` subfolder
  of the folder you have open. Opening a folder from someone else and
  running its analysis runs their code with your rights, exactly as running
  their script would. Read a `boxes/` folder before you run it.
- **Models contain expressions.** A `.cpn` model's inscriptions are CPN ML,
  evaluated by OpenProcess's own interpreter (`openprocess/ml`), not by
  Python: a model cannot reach the file system or the network.
- **Event logs and nets are data.** XES, CSV, OCEL, PNML and `.analysis`
  files are parsed, never executed.
- **Updates.** The app asks GitHub for the latest release, downloads it over
  HTTPS and checks the download against the SHA-256 GitHub publishes for it
  before installing (`openprocess/gui/studio/updates.py`). The apps are not
  signed with a paid developer certificate; the README says what the system
  asks the first time because of that.
- **AI assistants.** `openprocess mcp` lets an assistant you connect read and
  change the files of the folder you point it at. Every change it makes is
  listed under Help ▸ Review AI Changes…, with an Undo for each.
- **Nothing is sent anywhere.** There is no telemetry. The only network
  requests are the update check, a public log the *Open dataset* box
  downloads when you ask for it (checked against its recorded SHA-256 from
  then on), and whatever a box of your own makes.
