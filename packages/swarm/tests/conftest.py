from pathlib import Path

import pytest

from kriflow_swarm import SqliteStorage
from swarm_db import migrated


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return migrated(tmp_path)


@pytest.fixture
def board(db: Path) -> SqliteStorage:
    return SqliteStorage(db)
