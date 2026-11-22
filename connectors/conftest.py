"""Each package's tests, until the packages leave the core (issue 587): the Default List names only
the package under test, released from its folder in a local packages repository, as the box
installs it from Kriflow/packages."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from swarmify_connectors.fake import default_list
from swarmify_connectors.packages import MANIFEST


@pytest.fixture(scope="module", autouse=True)
def own_default_list(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    folder = Path(request.path).parent
    if not (folder / MANIFEST).is_file():
        yield
        return
    listed = default_list(tmp_path_factory.mktemp("default-list"), {folder.name: folder})
    with pytest.MonkeyPatch.context() as m:
        m.setenv("SWARMIFY_DEFAULT_PACKAGES", str(listed))
        yield
