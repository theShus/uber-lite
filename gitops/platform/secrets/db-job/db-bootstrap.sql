-- One login role and one database per service on RDS. Idempotent: safe to re-run.
-- \getenv load the value of the secret that the job yaml has imported as env vars
\getenv rides_pw RIDES_PW 
\getenv trips_pw TRIPS_PW
\getenv payments_pw PAYMENTS_PW

SELECT format('CREATE ROLE %I LOGIN', r)
FROM unnest(ARRAY['rides', 'trips', 'payments']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \gexec

ALTER ROLE rides PASSWORD :'rides_pw';
ALTER ROLE trips PASSWORD :'trips_pw';
ALTER ROLE payments PASSWORD :'payments_pw';

-- PG16+: CREATE ROLE gave the master only ADMIN; owning-by-another-role needs SET
GRANT rides, trips, payments TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;

SELECT format('CREATE DATABASE %I OWNER %I', r, r)
FROM unnest(ARRAY['rides', 'trips', 'payments']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = r) \gexec

-- only the owner can revoke PUBLIC's default CONNECT, so act as the owner
SET ROLE rides;    REVOKE CONNECT, TEMPORARY ON DATABASE rides FROM PUBLIC;
SET ROLE trips;    REVOKE CONNECT, TEMPORARY ON DATABASE trips FROM PUBLIC;
SET ROLE payments; REVOKE CONNECT, TEMPORARY ON DATABASE payments FROM PUBLIC;
RESET ROLE;
