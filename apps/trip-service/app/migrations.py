"""trip-service's schema, as ordered migrations. Never edit an applied one: add the next version."""

MIGRATIONS = [
    (
        1,
        "create trips",
        """
        CREATE TABLE trips (
            ride_id      uuid PRIMARY KEY,      -- one trip per ride: duplicate ride.matched events are ignored
            rider_id     text NOT NULL,
            driver_id    text NOT NULL,
            amount_cents integer NOT NULL,
            currency     text NOT NULL,
            status       text NOT NULL CHECK (status IN ('started', 'completed')),
            started_at   timestamptz NOT NULL DEFAULT now(),
            completes_at timestamptz NOT NULL,  -- when the simulated drive ends
            completed_at timestamptz
        );
        -- the scheduler's query: started trips that are due
        CREATE INDEX trips_due_idx ON trips (completes_at) WHERE status = 'started';
        """,
    ),
]
