-- Cluster-level roles. Run once per PostgreSQL server as a superuser.
-- LOCAL DEVELOPMENT PASSWORDS ONLY. In Azure, roles are provisioned by IaC with secrets
-- from Key Vault (or Entra-authenticated managed identities) - never these values.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meip_owner') THEN
    CREATE ROLE meip_owner LOGIN PASSWORD 'meip_owner_local' NOSUPERUSER NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meip_app') THEN
    -- Runtime role: not a table owner, cannot bypass row-level security.
    CREATE ROLE meip_app LOGIN PASSWORD 'meip_app_local' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;
