"""The rename's module alias (kriflow#593): a record pickled by the code before the rename
(`swarmify_*` modules) loads in this code, which has only `kriflow_*` modules."""

import pickle
import subprocess
import sys

import pytest

from kriflow_kernel import Event

LOAD = """
import pickle, sys
import kriflow_kernel
from kriflow_kernel.events import Event
loaded = pickle.loads(bytes.fromhex(sys.argv[1]))
assert type(loaded) is Event, type(loaded)
import swarmify_kernel.events
assert swarmify_kernel.events.Event is Event
try:
    import swarmify_nothing
except ModuleNotFoundError:
    print(loaded.world_id)
"""


def swarmify_pickle(obj: object) -> bytes:
    """`obj` pickled as the code before the rename did: its class named by its `swarmify_`
    module path. Protocol 0 names classes as text, so the path can be rewritten in place."""
    data = pickle.dumps(obj, protocol=0)
    old = data.replace(b"kriflow_kernel.events\n", b"swarmify_kernel.events\n")
    assert old != data and b"kriflow_" not in old
    return old


def test_a_pickle_naming_a_swarmify_module_loads_as_the_kriflow_class_after_a_kernel_import() -> (
    None
):
    event = Event("evt_1", "world_1", "run.updated", {"a": 1}, "t")
    out = subprocess.run(
        [sys.executable, "-c", LOAD, swarmify_pickle(event).hex()],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "world_1"


OLD_NAMES = [
    "swarmify_api",
    "swarmify_connectors",
    "swarmify_kernel",
    "swarmify_learning",
    "swarmify_memory",
    "swarmify_notifications",
    "swarmify_runs",
    "swarmify_swarm",
    "swarmify_workflows",
    "swarmify_worlds",
]


@pytest.mark.parametrize("old", OLD_NAMES)
def test_each_old_top_level_name_imports_first_in_a_fresh_process(old: str) -> None:
    new = "kriflow_" + old.removeprefix("swarmify_")
    code = f"import sys, {old}; assert {old}.__file__ == sys.modules['{new}'].__file__"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


EVERY_OLD_NAME = """
import sys
import kriflow_api.main  # imports nearly every kriflow_* module
before = {n: dict(vars(m)) for n, m in list(sys.modules.items()) if n.startswith("kriflow_")}
for name in before:
    old = __import__("swarmify_" + name.removeprefix("kriflow_"), fromlist=["_"])
    assert old.__file__ == sys.modules[name].__file__
changed = [
    f"{n}.{k}" for n, attrs in before.items() for k, v in attrs.items()
    if vars(sys.modules[n]).get(k) is not v
]
assert not changed, changed
"""


def test_importing_old_submodule_names_leaves_the_real_packages_attributes_alone() -> None:
    """`import swarmify_workflows.validate` once bound the module `validate` over the function
    `kriflow_workflows.validate`, so saving a workflow failed with "'module' object is not
    callable"."""
    out = subprocess.run(
        [sys.executable, "-c", EVERY_OLD_NAME], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize(
    "first",
    [
        "import swarmify_workflows.validate",
        "from swarmify_workflows.validate import validate",
        "from swarmify_workflows import validate",
        "import kriflow_workflows, swarmify_workflows.validate",
    ],
)
def test_the_workflows_validate_function_stays_callable_after_an_old_import(first: str) -> None:
    code = f"{first}\nimport kriflow_workflows\nassert callable(kriflow_workflows.validate)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
