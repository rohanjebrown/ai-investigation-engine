"""Run an Alembic upgrade in a standalone process for migration concurrency tests."""

import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

database_url, config_path, ready_path, start_path = sys.argv[1:]
engine = create_engine(database_url, hide_parameters=True)
try:
    with engine.connect():
        pass
    Path(ready_path).touch()
    deadline = time.monotonic() + 30
    while not Path(start_path).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Migration process did not receive the start signal")
        time.sleep(0.01)
    config = Config(config_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
finally:
    engine.dispose()
