"""The module alias of the product rename (Swarmify to Kriflow, kriflow#564, #593).

DBOS pickles workflow inputs, step outputs and errors by module path, so a record names the
modules of the code that wrote it. Before the rename the code was `swarmify_*`; now it is
`kriflow_*`. Runs waiting at a gate when the rename was deployed, and every record written
before it, name `swarmify_runs.machine.RunState` and the like, so an import of
`swarmify_<x>[.y]` returns a stand-in that reads through to the `kriflow_<x>[.y]` module (no copy,
so class identity and `isinstance` hold). It is a stand-in, not the module itself, because the
import system writes on what it imports: the alias's `__spec__`, and, after `import
swarmify_workflows.validate`, the submodule over the parent's attribute of that name, which there
is the function `validate` (kriflow#613). Helper scripts that import the old names keep running
too. Permanent: those records never change. Installed by importing the kernel, and by importing
any old top-level name first: the kernel ships a tiny `swarmify_<x>` package per old name that
calls `become`. The rollback target (kriflow#592) has the reverse alias.
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
        return _OldName(spec.name)

    def exec_module(self, module: ModuleType) -> None:
        pass  # the real module ran when it was imported


class _OldName(ModuleType):
    """`swarmify_<x>[.y]`: every read goes to `kriflow_<x>[.y]`, and so does every write except
    the import system's own. Its dunders (`__spec__`, `__loader__`) stay here, and a submodule it
    binds after an old-name import is dropped: the real package already has what it should under
    that name."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.__real__ = importlib.import_module(REAL + name[len(ALIAS) :])

    def __getattr__(self, name: str) -> object:
        return getattr(self.__real__, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("__"):
            super().__setattr__(name, value)
        elif not isinstance(value, ModuleType):
            setattr(self.__real__, name, value)


def install() -> None:
    if not any(isinstance(f, _Alias) for f in sys.meta_path):
        sys.meta_path.insert(0, _Alias())


def become(name: str) -> None:
    """Called by the shim module `name` (`swarmify_<x>`): installs the alias and puts the
    stand-in for `kriflow_<x>` in its place, so the old name imports in a fresh process too."""
    install()
    sys.modules[name] = _OldName(name)
