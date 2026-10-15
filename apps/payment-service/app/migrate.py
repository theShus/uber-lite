"""Create or update payment-service's tables, then exit.

Run: DATABASE_URL=… python -m app.migrate   (a pre-install/pre-upgrade Job in Kubernetes, 12.6)
"""

from .core.db import migrate
from .core.logging import setup_logging
from .migrations import MIGRATIONS


def main() -> None:
    setup_logging("payment-service")
    migrate(MIGRATIONS)


if __name__ == "__main__":
    main()
