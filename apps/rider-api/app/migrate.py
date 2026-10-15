"""Create or update rider-api's tables, then exit.

Run: DATABASE_URL=… python -m app.migrate
In Kubernetes this runs as a Job before the Deployment (the chart's pre-install/pre-upgrade hook, 12.6).
"""

from .core.db import migrate
from .core.logging import setup_logging
from .migrations import MIGRATIONS


def main() -> None:
    setup_logging("rider-api")
    migrate(MIGRATIONS)


if __name__ == "__main__":
    main()
