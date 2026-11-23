"""The rename's module alias (kriflow#593): a record pickled by the code before the rename
(`swarmify_*` modules) loads in this code, which has only `kriflow_*` modules."""

import pickle
import subprocess
import sys

from kriflow_kernel import Event

LOAD = """
import pickle, sys
import kriflow_kernel
from kriflow_kernel.events import Event
loaded = pickle.loads(bytes.fromhex(sys.argv[1]))
assert type(loaded) is Event, type(loaded)
import swarmify_kernel.events
assert swarmify_kernel.events is sys.modules["kriflow_kernel.events"]
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
