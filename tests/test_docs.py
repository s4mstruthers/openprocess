"""The documentation read from the code is current, and the layers stay apart."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_docs_are_current_and_layers_hold():
    """docs/architecture.md (the map) and docs/boxes.md (the catalogue) are generated from the code;
    after changing a module's docstring, a package's imports or a box, run: python docs/make_docs.py.
    The same command fails when an engine imports the framework or the app, or the framework imports Qt."""
    done = subprocess.run([sys.executable, str(ROOT / "docs" / "make_docs.py"), "--check"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr
