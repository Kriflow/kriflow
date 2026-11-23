from pathlib import Path

import pytest

from kriflow_workflows import SqliteStorage, Workflows
from samples import CONNECTORS, migrated_db


@pytest.fixture
def workflows(tmp_path: Path) -> Workflows:
    return Workflows(SqliteStorage(migrated_db(tmp_path)), lambda *_: CONNECTORS)
