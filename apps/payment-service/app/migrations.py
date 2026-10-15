"""payment-service's schema, as ordered migrations. Never edit an applied one: add the next version."""

MIGRATIONS = [
    (
        1,
        "create payments",
        """
        CREATE TABLE payments (
            ride_id       uuid PRIMARY KEY,   -- the idempotency key: at most one payment per ride
            amount_cents  integer NOT NULL,
            currency      text NOT NULL,
            status        text NOT NULL CHECK (status IN ('pending', 'charged', 'failed')),
            attempts      integer NOT NULL DEFAULT 0,
            psp_reference text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
        """,
    ),
]
