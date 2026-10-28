"""Apply pending Alembic migrations, then replace this process with the API."""

import os
import subprocess
import sys


def main() -> None:
    if sys.argv[1:] == ["--smoke"]:
        os.execv(sys.executable, [
            sys.executable, "-m", "investigation_engine.infrastructure.api.server", "--smoke",
        ])
    migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=False)
    if migration.returncode:
        raise SystemExit(migration.returncode)
    os.execv(sys.executable, [
        sys.executable, "-m", "investigation_engine.infrastructure.api.server",
    ])


if __name__ == "__main__":
    main()
