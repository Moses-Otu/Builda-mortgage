#!/usr/bin/env bash
# Back up the pilot: the database (pg_dump, custom format) and the document volume (tar).
# Run on the host from deploy/pilot. Writes backups/<UTC timestamp>/ with a SHA256SUMS file.
#
# Optional encryption: set BACKUP_GPG_PASSPHRASE_FILE to a file holding a long passphrase;
# each file is then encrypted with gpg (AES256) and the plaintext removed.
#
# Copy the backup directory off this host (to encrypted storage in the UK) after it finishes.
set -euo pipefail
cd "$(dirname "$0")"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="backups/${stamp}"
mkdir -p "${out}"
chmod 700 backups "${out}"

echo "Database..."
docker compose exec -T postgres pg_dump -U postgres -d meip -Fc --no-password > "${out}/meip.dump"

echo "Documents..."
docker compose exec -T api tar -C /data/documents -cf - . > "${out}/documents.tar"

(cd "${out}" && sha256sum meip.dump documents.tar > SHA256SUMS)

if [[ -n "${BACKUP_GPG_PASSPHRASE_FILE:-}" ]]; then
  for f in meip.dump documents.tar; do
    gpg --batch --yes --pinentry-mode loopback --passphrase-file "${BACKUP_GPG_PASSPHRASE_FILE}" \
        --symmetric --cipher-algo AES256 -o "${out}/${f}.gpg" "${out}/${f}"
    shred -u "${out}/${f}" 2>/dev/null || rm -f "${out}/${f}"
  done
fi

chmod 600 "${out}"/*
echo "Backup written to deploy/pilot/${out}"
