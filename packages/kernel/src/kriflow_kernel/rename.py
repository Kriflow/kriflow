"""The module alias of the product rename (Swarmify to Kriflow, kriflow#564, #593).

DBOS pickles workflow inputs, step outputs and errors by module path, so a record names the
modules of the code that wrote it. Before the rename the code was `swarmify_*`; now it is
`kriflow_*`. Runs waiting at a gate when the rename was deployed, and every record written
before it, name `swarmify_runs.machine.RunState` and the like, so an import of
`swarmify_<x>[.y]` returns the `kriflow_<x>[.y]` module itself (no copy, so class identity and
`isinstance` hold). Helper scripts that import the old names keep running too. Permanent: those
records never change. Installed by importing the kernel, and by importing any old top-level name
first: the kernel ships a tiny `swarmify_<x>` package per old name that calls `become`. The
rollback target (kriflow#592) has the reverse alias.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from types import ModuleType

ALIAS = "swarmify_"
"""The prefix answered here."""
REAL = "kriflow_"
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


def become(name: str) -> None:
    """Called by the shim module `name` (`swarmify_<x>`): installs the alias and puts the real
    `kriflow_<x>` in its place, so the old name imports in a fresh process too."""
    install()
    sys.modules[name] = importlib.import_module(REAL + name[len(ALIAS) :])
