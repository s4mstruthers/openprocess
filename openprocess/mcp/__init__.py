"""OpenProcess for AI assistants: an MCP server, and the plain API behind it.

* :mod:`.api` -- the operations as functions over paths and plain values (no
  MCP SDK, no Qt): summarise and filter logs, discover models, check
  conformance, analyse nets, draw diagrams, build and run workflows, write
  boxes, validate and write exercises, simulate coloured nets, open things in
  the app.  Every answer carries its sources (``cite``), its derivation
  (``how``) and how to get it again (``reproduce``); every file written is
  journaled for review in the app.
* :mod:`.server` -- those functions as MCP tools, the guides as resources, a
  few prompts, and the instructions that tell an assistant how OpenProcess
  wants to be used: cited, reproducible, through boxes and workflows, in the
  open.
* :mod:`.install` -- writing the configuration of Claude Desktop, Claude Code
  and Cursor, so connecting is one command.

``openprocess mcp`` (or ``openprocess-mcp``) starts the server.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """The ``openprocess-mcp`` console script."""
    from .server import main as serve
    return serve(argv)


__all__ = ["main"]
