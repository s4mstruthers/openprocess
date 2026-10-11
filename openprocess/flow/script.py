"""A workflow as a runnable Python script.

Connections promises that every box is a function you can call from a
script.  :func:`script_text` makes that true for a whole analysis: the
``@workflow`` function :func:`~openprocess.flow.workflow.to_python` writes,
with the folder's own boxes loaded from ``boxes/`` beside the script, and a
main block that runs it, prints what each box gave, and writes every result
worth a file into ``results/``.  Supplementary material for a paper, or a
lab handout, that runs with ``python analysis.py``.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .workflow import Workflow, _identifier, to_python

_LOADER = '''

def _box_from_file(path, name):
    """A box from one of the folder's own files (boxes/), as the app loads it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)
'''

_MAIN = '''

if __name__ == "__main__":
    from openprocess.flow import Runner
    from openprocess.flow.record import as_file

    wf = {function}.workflow
    run = Runner(wf.library, folder=HERE).run(wf)
    results = HERE / "results"
    for node in wf.order():
        result = run.result(node)
        print(f"{{wf.title(node)}}: {{result.status}}" + (f" — {{result.error}}" if result.error else ""))
        if result.status == "done":
            payload = as_file(result.value, str(results / wf.title(node).replace("/", "-")))
            if payload is not None:
                name, content = payload
                results.mkdir(exist_ok=True)
                Path(name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    sys.exit(0 if all(run.result(n).status == "done" for n in wf.order()) else 1)
'''


def script_text(workflow: Workflow, file_name: str = "analysis.py") -> str:
    """The script for ``workflow``: see the module's docstring."""
    from .. import __version__
    code = to_python(workflow)
    customs: list[tuple[str, str]] = []
    for node in workflow.nodes.values():
        spec = workflow.spec(node)
        if spec.custom and spec.file:
            function = spec.id.rsplit(".", 1)[-1]
            code = code.replace(f"from {spec.module} import {function}\n", "")
            if (Path(spec.file).name, function) not in customs:
                customs.append((Path(spec.file).name, function))
    name = workflow.name or "workflow"
    today = _dt.date.today().isoformat()
    lines = [f'"""{name}: an OpenProcess analysis as a script.', "",
             f"Written by OpenProcess {__version__} on {today}: the same boxes, settings and connections",
             f"as the analysis in the app.  Run it with", "", f"    python {file_name}", "",
             "with OpenProcess installed (pip install openprocess).  It prints what each box gives and",
             "writes every result worth a file into results/ beside this script.  Each box is a plain",
             "Python function: change a setting here, or open the function to see its code."]
    if customs:
        lines += ["", "The analysis uses the folder's own boxes: keep boxes/ beside this script."]
    lines += ['"""', "", "import sys", "from pathlib import Path", "", "HERE = Path(__file__).resolve().parent", ""]
    text = "\n".join(lines) + "\n"
    if customs:
        text += _LOADER + "\n"
    if customs:
        # Named before the @workflow function that calls them.
        header, _, body = code.partition("\n\n\n")
        loaders = "\n".join(f"{function} = _box_from_file(HERE / 'boxes' / {file!r}, {function!r})"
                            for file, function in customs)
        code = f"{header}\n{loaders}\n\n\n{body}"
    return text + code + _MAIN.format(function=_identifier(name))


__all__ = ["script_text"]
