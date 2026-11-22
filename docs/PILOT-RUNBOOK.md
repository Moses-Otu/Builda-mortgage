# Pilot Runbook: Controlled Broker Pilot (Phase 4)

How to deploy, onboard a firm, operate, back up, restore and erase data for a small
controlled pilot with 2–3 broker firms. **Technical controls are not legal compliance.**
Section 1 lists what has to be in place outside the code before any real borrower document is
uploaded.

## 1. Before any real borrower data (external prerequisites)

The platform refuses to process documents in a deployment until
`MEIP_DOCUMENT_RETENTION_APPROVAL` records your approved retention decision. Treat everything
below as **required** before setting `MEIP_DOCUMENTS_ENABLED=true` with real data:

| # | Prerequisite | Owner |
|---|---|---|
| 1 | **DPIA** covering payslips (and bank statements, if used), OpenAI as a processor, the hosting provider, retention and erasure | Controller, with the DPO or privacy adviser |
| 2 | **Controller and processor roles agreed** with each pilot firm; a data processing agreement and pilot agreement signed (scope, duration, synthetic or authorised data, exit and deletion) | Legal |
| 3 | **OpenAI terms reviewed:** the business terms and DPA; confirm API data isn't used for training; agree data retention (zero data retention where available); record the region where data is processed and any international transfer mechanism (UK IDTA or addendum) | Legal and privacy |
| 4 | **Hosting provider agreement:** UK data location for the VM, disks and backups; disk encryption at rest; access controls | Operator |
| 5 | **Retention decision (D10):** how long case data and documents are kept after a case closes; written down and referenced in `MEIP_DOCUMENT_RETENTION_APPROVAL` | Controller |
| 6 | **Independent penetration test** of the deployed pilot, with findings fixed or accepted | Operator, with a tester |
| 7 | **Borrower transparency:** the firms' privacy notices cover use of this platform and OpenAI | Pilot firms |
| 8 | **Incident response:** who to call, how to revoke access, breach reporting (72 hours to the ICO) | Operator and firms |
| 9 | **Bank statements** only after the DPIA explicitly covers them; then set `MEIP_BANK_STATEMENTS_ENABLED=true` and a random `MEIP_ACCOUNT_FINGERPRINT_KEY` | Controller |

Until then, use synthetic or properly authorised test documents only.

## 2. Hosting assumptions
- **One Linux VM** (2 vCPU, 4–8 GB RAM, 40 GB+ disk) at a provider with a **UK region**,
  **encrypted disks** and **snapshots**. Any provider works; nothing is provider-specific.
- **Docker Engine** with the Compose plugin.
- A **DNS name** pointing at the VM.
- **Firewall:** only 80 and 443 open to the internet; SSH restricted to the operator's
  addresses, with keys only.
- **Encryption:**
  - in transit: HTTPS with automatic Let's Encrypt certificates (Caddy), HSTS;
  - inside the host: services talk over private Docker networks;
  - at rest: the provider's disk encryption covers the database and document volumes;
  - backups can be encrypted with gpg (section 6).
- **Not exposed:** PostgreSQL, the API and the document volume. Only Caddy publishes ports;
  the API is reached only by the frontend, on an internal network.

## 3. Deploy

```bash
git clone <repository> meip && cd meip/deploy/pilot
cp .env.example .env && chmod 600 .env      # fill in every value (openssl rand -base64 32)
sudo install -d -o 10001 -m 700 secrets    # 10001 = the backend container's user
```

Generate the token signing key with the backend image (no tools needed on the host). The key
file belongs to the container user and is readable only by it:

```bash
docker compose build migrate
docker compose run --rm --no-deps -v "$PWD/secrets:/out" migrate meip-admin generate-signing-key --out /out/auth_signing_key.pem
```

Start:

```bash
docker compose up -d --build
docker compose ps                     # every service healthy
curl -fsS https://$MEIP_DOMAIN/sign-in >/dev/null && echo up
```

The `migrate` service applies database migrations on every start, then exits.

The backend image installs exactly what `backend/uv.lock` pins (`uv sync --frozen`). After
changing dependencies in `backend/pyproject.toml`, run `uv lock` in `backend/` and commit the
result.

## 4. Onboard a firm

1. **Create the firm and its first administrator** (prints a temporary password once):
   ```bash
   set -a; . ./.env; set +a      # load the owner password into this shell only
   docker compose exec -e MEIP_MIGRATIONS_DATABASE_URL="postgresql+asyncpg://meip_owner:${MEIP_OWNER_DB_PASSWORD}@postgres:5432/meip" api \
     meip-admin create-tenant --name "Example Mortgages Ltd" --fca-frn 123456 \
     --admin-email admin@example-mortgages.co.uk --admin-name "Alex Admin" --operator "your-name"
   ```
   The `api` container already has the backend settings. The owner role is passed for this one
   command only. Run `erase-case` and `retention-sweep` (section 7) the same way.
2. **Give the temporary password to the administrator by a different channel** from the sign-in
   address (for example, a phone call). At first sign-in they must choose their own password
   (at least 12 characters).
3. **The administrator invites colleagues** from **Team** (account menu): email, name, role
   (adviser, case administrator, read only, firm admin). Each new person gets a temporary
   password, shown once to the administrator. They sign in, choose a password, and **accept
   the invitation** on the firm screen.
4. **Access:** firms see only their own cases (row-level security in the database, plus checks
   on every request). Removing access is **Team → Remove access**. An administrator can reset
   a member's password, except for someone who also belongs to another firm (ask the
   operator).

## 5. Operate
- **Logs:** `docker compose logs -f api worker frontend caddy`. They're JSON, rotated at 10 MB ×
  5 files per service. They contain ids and codes, never document text, emails or passwords.
- **Health:**
  - the API: `/health` (process) and `/ready` (database), used by the container health
    checks;
  - the frontend: `/sign-in`.
- **Extraction failures:**
  - provider outages, rate limits and timeouts are retried (three attempts per job, one
    minute apart), then the document shows "failed";
  - the broker can upload again;
  - errors are recorded as codes on the job.
- **Updates:** `git pull && docker compose up -d --build` (migrations run first).
- **Secrets rotation:**
  - the OpenAI key: edit `.env`, then `docker compose up -d`;
  - the signing key: replace the file and restart, which signs everyone out;
  - database passwords: `ALTER ROLE` in PostgreSQL and `.env` together.

## 6. Back up and restore

**Back up** daily, for example from cron. The database is dumped in pg_dump custom format and
the documents are archived with tar, each with a SHA-256 checksum:
```bash
BACKUP_GPG_PASSPHRASE_FILE=/root/backup-passphrase ./backup.sh
```
Copy `backups/<timestamp>/` off the host to encrypted storage in the UK, and keep the
passphrase separately. Provider disk snapshots are a useful second layer, not a replacement.

**Restore** (destructive: replaces the database and documents):
```bash
BACKUP_GPG_PASSPHRASE_FILE=/root/backup-passphrase ./restore.sh backups/<timestamp>
```
Then check `/ready`, sign in, open a case, download a document and an evidence pack.

**Verified (2026-11-20, local PostgreSQL 17, synthetic demo data):**
- a custom-format dump restored into a fresh database gave identical row counts (cases,
  applicants, documents, observations, approvals, audit events);
- both approval snapshots still matched their SHA-256;
- table ownership and all 26 row-level security policies, and the runtime role's grants, were
  intact;
- all 26 restored originals matched the SHA-256 recorded in the restored database.

**Not verified:** the scripts end to end on a deployed host. Do a restore drill on a spare VM
before real data.

## 7. Retention and erasure

Erasure is permanent and run by the operator with the owner database role. It is never done
by the application.
- **One case** (for example, a data subject's erasure request, or a firm leaving the pilot):
  ```bash
  meip-admin erase-case --tenant <firm id> --case <case id> --reason "<why>" --confirm
  ```
  It deletes every row of the case (applicants, figures, documents, extractions, reviews,
  accounts, checks, acknowledgements, approvals and snapshots) and its stored originals. It
  keeps the append-only audit skeleton (ids and codes only), removes the audit details, and
  records a `case.erased` event.
- **Retention sweep**, using the approved period (no default on purpose):
  ```bash
  meip-admin retention-sweep --days-after-closure <N>            # dry run: lists what's due
  meip-admin retention-sweep --days-after-closure <N> --apply    # erases them
  ```
- **Outside the platform's reach:** downloaded evidence packs and originals; backups (they
  expire on your backup schedule); any copies at OpenAI under its retention terms.

## 8. What's audited
Every change to firm data, and also:
- **each original document download** (`document.downloaded`);
- **each evidence pack download** (`evidence_pack.downloaded`, with the pack's hash);
- **password resets by administrators** (`member.password_reset`, never the password);
- **profile approvals**;
- **case erasure**.

Sign-ins and password changes go to the operational log, without emails.
