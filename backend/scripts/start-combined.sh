#!/usr/bin/env bash
# QA and demo hosting only (Railway or any single-container host): synthetic data, never real
# borrower documents. The controlled pilot uses deploy/pilot (separate API and worker
# containers behind Caddy). See docs/QA-DEPLOY-RAILWAY-VERCEL.md.
#
# Runs the API and the background worker in one container, because the document volume can
# attach to only one service. If either process exits, the container exits and the host
# restarts it.
set -euo pipefail
umask 077

# The token signing key arrives as an environment variable (the host has no secret files).
# Literal "\n" sequences are accepted for hosts that flatten multi-line values.
if [[ -n "${MEIP_AUTH_SIGNING_KEY_PEM:-}" ]]; then
  key_path="${MEIP_AUTH_SIGNING_KEY_PATH:?MEIP_AUTH_SIGNING_KEY_PATH must be set}"
  mkdir -p "$(dirname "${key_path}")"
  # The pattern is quoted so it matches a backslash followed by "n". Unquoted, bash reads
  # \\n as the letter n and would corrupt every key.
  printf '%s\n' "${MEIP_AUTH_SIGNING_KEY_PEM//'\n'/$'\n'}" > "${key_path}"
fi
unset MEIP_AUTH_SIGNING_KEY_PEM

# First deployment only: create the database roles and database on managed PostgreSQL.
# Remove MEIP_BOOTSTRAP_SUPERUSER_URL from the service once this has run.
if [[ -n "${MEIP_BOOTSTRAP_SUPERUSER_URL:-}" ]]; then
  meip-admin bootstrap-database
fi
unset MEIP_BOOTSTRAP_SUPERUSER_URL MEIP_OWNER_DB_PASSWORD MEIP_APP_DB_PASSWORD

alembic upgrade head
# The API and worker run as the runtime role only; they don't need the owner credentials.
unset MEIP_MIGRATIONS_DATABASE_URL

trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM
meip-worker &
uvicorn meip.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log --proxy-headers &
wait -n
echo "A process exited; stopping the container so the host restarts it." >&2
exit 1
