-- Local development database. Run as a superuser after roles.sql.
CREATE DATABASE meip OWNER meip_owner;
REVOKE ALL ON DATABASE meip FROM PUBLIC;
GRANT CONNECT ON DATABASE meip TO meip_app;
