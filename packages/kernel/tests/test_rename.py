"""The rename's module alias: a record pickled by the renamed code (`kriflow_*` modules) loads in
this code, the rollback target, which has only `swarmify_*` modules."""

import pickle
import subprocess
import sys

from swarmify_kernel import Event

LOAD = """
import pickle, sys
import swarmify_kernel
from swarmify_kernel.events import Event
loaded = pickle.loads(bytes.fromhex(sys.argv[1]))
assert type(loaded) is Event, type(loaded)
import kriflow_kernel.events
assert kriflow_kernel.events is sys.modules["swarmify_kernel.events"]
try:
    import kriflow_nothing
except ModuleNotFoundError:
    print(loaded.world_id)
"""


def kriflow_pickle(obj: object) -> bytes:
    """`obj` pickled as the renamed code would: its class named by its `kriflow_` module path.
    Protocol 0 names classes as text, so the path can be rewritten in place."""
    data = pickle.dumps(obj, protocol=0)
    renamed = data.replace(b"swarmify_kernel.events\n", b"kriflow_kernel.events\n")
    assert renamed != data and b"swarmify_" not in renamed
    return renamed


def test_a_pickle_naming_a_kriflow_module_loads_as_the_swarmify_class_after_a_kernel_import() -> (
    None
):
    event = Event("evt_1", "world_1", "run.updated", {"a": 1}, "t")
    out = subprocess.run(
        [sys.executable, "-c", LOAD, kriflow_pickle(event).hex()],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "world_1"
