#!/usr/bin/env bash
# Restore the pilot from a backup made by backup.sh. DESTRUCTIVE: replaces the database and
# the documents. Run on the host from deploy/pilot:   ./restore.sh backups/<timestamp>
#
# The stack must be set up (same .env, so the database roles exist). Steps:
#   1. stop the API, worker and frontend so nothing writes;
#   2. verify checksums; decrypt if needed (BACKUP_GPG_PASSPHRASE_FILE);
#   3. recreate the database and restore it (owners and grants included);
#   4. replace the document volume's contents;
#   5. start everything; check /ready, sign in, open a case and download a document.
set -euo pipefail
cd "$(dirname "$0")"
src="${1:?usage: restore.sh backups/<timestamp>}"

read -r -p "This replaces the database and documents with ${src}. Type RESTORE to continue: " ok
[[ "${ok}" == "RESTORE" ]] || { echo "Cancelled."; exit 1; }

sums="$(realpath "${src}")/SHA256SUMS"
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
for f in meip.dump documents.tar; do
  if [[ -f "${src}/${f}.gpg" ]]; then
    gpg --batch --pinentry-mode loopback --passphrase-file "${BACKUP_GPG_PASSPHRASE_FILE:?needed to decrypt}" \
        -o "${work}/${f}" -d "${src}/${f}.gpg"
  else
    cp "${src}/${f}" "${work}/${f}"
  fi
done
(cd "${work}" && sha256sum -c "${sums}")

docker compose stop frontend api worker

docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS meip WITH (FORCE)"
docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE meip OWNER meip_owner"
docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -c "REVOKE ALL ON DATABASE meip FROM PUBLIC; GRANT CONNECT ON DATABASE meip TO meip_app"
docker compose exec -T postgres pg_restore -U postgres -d meip --exit-on-error < "${work}/meip.dump"

docker compose run --rm --no-deps --entrypoint sh api -c 'find /data/documents -mindepth 1 -delete && tar -C /data/documents -xf -' < "${work}/documents.tar"

docker compose up -d
echo "Restored from ${src}. Check https://\$MEIP_DOMAIN, /ready, a sign-in and a document download."
