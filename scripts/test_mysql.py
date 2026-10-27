"""Run tests against only the dedicated local disposable schema, without printing secrets."""

import os
import subprocess
import sys

from sqlalchemy.engine import make_url

from shopresolve.config import Settings


def main():
    source = make_url(Settings().database_url)
    if (
        source.get_backend_name() != "mysql"
        or source.database != "shopresolve"
        or source.host != "127.0.0.1"
        or source.port != 13316
    ):
        raise RuntimeError("Helper requires this project's loopback Compose MySQL configuration")
    target = source.set(database="shopresolve_test")
    environment = os.environ.copy()
    environment["SHOPRESOLVE_TEST_DATABASE_URL"] = target.render_as_string(hide_password=False)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], env=environment, check=False
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
