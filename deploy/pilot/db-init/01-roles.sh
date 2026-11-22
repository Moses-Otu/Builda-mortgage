#!/bin/sh
# Runs once, when the PostgreSQL volume is first created. Passwords come from the
# environment (.env), never from this file.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE meip_owner LOGIN PASSWORD '${MEIP_OWNER_DB_PASSWORD}' NOSUPERUSER NOCREATEROLE;
-- Runtime role: not a table owner, cannot bypass row-level security.
CREATE ROLE meip_app LOGIN PASSWORD '${MEIP_APP_DB_PASSWORD}' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE meip OWNER meip_owner;
REVOKE ALL ON DATABASE meip FROM PUBLIC;
GRANT CONNECT ON DATABASE meip TO meip_app;
SQL
