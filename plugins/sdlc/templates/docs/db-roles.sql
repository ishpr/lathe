-- Database roles for a development database (REC-18). Roles are the real control; the guard
-- that blocks DROP and TRUNCATE in a shell command is a regex and can be bypassed.
--
-- Run once as a superuser against a throwaway dev instance. Pass the passwords as psql
-- variables so none is written in this file, and use values you do not reuse anywhere:
--   psql -v ON_ERROR_STOP=1 -v owner_password=... -v app_password=... -v ro_password=... \
--        -f docs/db-roles.sql
-- Never point this at a database that holds real data.
--
--   app_owner  runs migrations (DDL). Nothing else connects as it.
--   app_rw     the application and the tests: SELECT, INSERT, UPDATE, DELETE. No DDL, no TRUNCATE.
--   app_ro     read-only, for debugging and for agents that only need to look.

CREATE ROLE app_owner LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE app_rw    LOGIN PASSWORD :'app_password'   NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE app_ro    LOGIN PASSWORD :'ro_password'    NOSUPERUSER NOCREATEDB NOCREATEROLE;

CREATE DATABASE appdb OWNER app_owner;
\connect appdb

REVOKE ALL ON DATABASE appdb FROM PUBLIC;
GRANT CONNECT ON DATABASE appdb TO app_rw, app_ro;

ALTER SCHEMA public OWNER TO app_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_rw, app_ro;

-- Tables that already exist.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ro;

-- Tables the migrations create later (they run as app_owner).
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO app_ro;

-- Timeouts. Zero (the default) means no timeout, so a runaway query or a forgotten open
-- transaction holds locks forever. Set both on every role.
ALTER ROLE app_rw    SET statement_timeout = '30s';
ALTER ROLE app_rw    SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE app_rw    SET lock_timeout = '5s';
ALTER ROLE app_ro    SET statement_timeout = '60s';
ALTER ROLE app_ro    SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE app_owner SET statement_timeout = '5min';
ALTER ROLE app_owner SET idle_in_transaction_session_timeout = '2min';
ALTER ROLE app_owner SET lock_timeout = '10s';

-- Use: migrations connect as app_owner, the app and the test suite as app_rw, a person or an agent
-- who only reads as app_ro. Tests that need a clean slate build and drop their own test database
-- through fixtures, as app_owner on a separate database, not by truncating this one.
-- sdlc: REC-18
