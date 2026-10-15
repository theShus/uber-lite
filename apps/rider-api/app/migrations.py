"""rider-api's schema, as ordered migrations. Never edit an applied one: add the next version."""

MIGRATIONS = [
    (
        1,
        "create rides",
        """
        CREATE TABLE rides (
            id           uuid PRIMARY KEY,
            rider_id     text NOT NULL,
            pickup_lat   double precision NOT NULL,
            pickup_lon   double precision NOT NULL,
            dropoff_lat  double precision NOT NULL,
            dropoff_lon  double precision NOT NULL,
            amount_cents integer NOT NULL,
            currency     text NOT NULL,
            surge        numeric(3, 1) NOT NULL,
            quote_id     text,
            status       text NOT NULL DEFAULT 'requested',
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX rides_rider_id_idx ON rides (rider_id, created_at DESC);
        """,
    ),
    (
        2,
        "create outbox",
        """
        -- The transactional outbox: an event row is written in the SAME transaction as the ride,
        -- and the relay publishes it to Kafka afterwards. No ride without its event, or vice versa.
        CREATE TABLE outbox (
            id         bigserial PRIMARY KEY,
            topic      text NOT NULL,
            key        text NOT NULL,
            payload    jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
    ),
]
