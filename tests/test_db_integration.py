import os

import pytest

from glazedco.db import Database, load_environment
from glazedco.db_check import check_database


@pytest.mark.db
def test_database_readonly_integration() -> None:
    load_environment()
    if not os.getenv("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL no está definida")
    with Database() as database:
        result = check_database(database)
    assert result.select_one == 1
    assert result.server_version
    assert result.transaction_read_only
    assert result.ssl
