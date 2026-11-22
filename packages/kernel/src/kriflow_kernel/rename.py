"""The module alias of the product rename (Swarmify to Kriflow, kriflow#564).

DBOS pickles workflow inputs, step outputs and errors by module path, so a record names the
modules of the code that wrote it. Before the rename the code is `swarmify_*`; after it,
`kriflow_*`. This commit is the rollback target of the rename, so it answers the new names:
an import of `kriflow_<x>[.y]` returns the `swarmify_<x>[.y]` module itself (no copy, so class
identity and `isinstance` hold), and runs recorded by the renamed code load here. The rename
turns it round (old names to new) and keeps it forever. Installed by importing the kernel.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from types import ModuleType

ALIAS = "kriflow_"
"""The prefix answered here."""
REAL = "swarmify_"
"""The prefix of the modules this code has."""


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(
        self, fullname: str, path: object = None, target: object = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(ALIAS):
            return None
        try:
            if importlib.util.find_spec(REAL + fullname[len(ALIAS) :]) is None:
                return None
        except ModuleNotFoundError:
            return None  # no real parent either: unknown, as before
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        return importlib.import_module(REAL + spec.name[len(ALIAS) :])

    def exec_module(self, module: ModuleType) -> None:
        pass  # the real module ran when it was imported


def install() -> None:
    if not any(isinstance(f, _Alias) for f in sys.meta_path):
        sys.meta_path.insert(0, _Alias())
