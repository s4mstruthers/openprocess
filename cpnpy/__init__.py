"""``cpnpy``: the old name of :mod:`openprocess` (CPNpy, up to 0.6).

``import cpnpy`` and ``from cpnpy.flow import box`` keep working: every
``cpnpy.*`` module is the ``openprocess.*`` module under its old name, so a
box file or an exercise pack written for CPNpy runs unchanged.  New code
should import ``openprocess``; this shim warns once per process.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings

import openprocess as _new

__version__ = _new.__version__
__all__ = list(getattr(_new, "__all__", []))


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """``cpnpy.x.y`` resolves to the already-imported ``openprocess.x.y``."""

    def find_spec(self, name, path=None, target=None):
        if name == __name__ or not name.startswith(__name__ + "."):
            return None
        return importlib.machinery.ModuleSpec(name, self)

    def __init__(self):
        self._specs = {}

    def create_module(self, spec):
        module = importlib.import_module(_new.__name__ + spec.name[len(__name__):])
        sys.modules[spec.name] = module
        # The import system is about to write the cpnpy spec onto the module;
        # exec_module puts its own back, so its relative imports keep
        # resolving against openprocess (and Python does not warn about it).
        self._specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module):
        spec = getattr(module, "__spec__", None)
        own = self._specs.pop(getattr(spec, "name", ""), None)
        if own is not None:
            module.__spec__ = own


sys.meta_path.insert(0, _Alias())
warnings.warn("cpnpy is now openprocess: import openprocess instead (cpnpy keeps working for now)",
              DeprecationWarning, stacklevel=2)


def __getattr__(name):
    return getattr(_new, name)
