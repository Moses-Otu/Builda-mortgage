# QA and Demo Deployment: Vercel (frontend) + Railway (backend)

A quick hosted environment for QA testing and MVP demos. **Synthetic documents only.** It is
not the controlled pilot: that uses `deploy/pilot` and the [pilot runbook](PILOT-RUNBOOK.md),
after the legal prerequisites. Never upload a real payslip or bank statement here.

```
browser ──HTTPS──> Vercel (Next.js frontend, London)
                      │  server-side only, HTTPS + bearer token
                      v
                   Railway "backend" service: API + worker in one container
                      ├── volume /data/documents (uploaded PDFs)
                      ├── Railway PostgreSQL (private network)
                      └──HTTPS──> OpenAI API (extraction)
```

## What's different from the pilot deployment (accepted for QA only)
| Pilot (`deploy/pilot`) | This QA setup | Why it's acceptable here |
|---|---|---|
| The API is private, reached only by the frontend | **The API has a public Railway URL** | Every endpoint requires a token, and the interactive docs and schema are off outside local and test. Synthetic data only |
| Separate API and worker containers | One container runs both (`scripts/start-combined.sh`) | A Railway volume attaches to one service only. If either process dies, the container restarts |
| Containers run as uid 10001 | **Runs as root** (`RAILWAY_RUN_UID=0`) | Railway volumes are root-owned |
| The retention approval is the real D10 decision | A QA placeholder (`QA demo only: synthetic documents…`) | No real data; D10 is still required before the pilot |
| A UK VM you control | Railway (EU West) and Vercel (London functions); OpenAI processes documents | Synthetic data only |
| No upload limit beyond the API's own | **Uploads over about 4.5 MB fail** (Vercel's request body limit) | Synthetic payslips are a few KB |

## 1. Prepare secrets (on your PC, from `backend\`)
Signing key for sign-in tokens:
```bash
.venv\Scripts\meip-admin generate-signing-key --out "$env:TEMP\meip-qa-key.pem"
```
Two database passwords (run twice, keep both):
```bash
.venv\Scripts\python -c "import secrets; print(secrets.token_hex(24))"
```
Also get your OpenAI API key. Keep these out of chat, email and the repository.

## 2. Railway: the backend
1. **New Project → Deploy from GitHub repo →** `BuildA_Mortgage`. It creates a service from the
   repository. Rename it `backend`.
2. Under the **backend service → Settings:**
   - **Root Directory:** `backend`
   - **Config file path** (Config-as-code): `/backend/railway.toml`. This sets the
     Dockerfile build, the start command and the `/ready` health check.
   - **Region:** EU West.
3. **+ New → Database → PostgreSQL.** Leave the service named `Postgres`.
4. **backend service → Settings → Volumes → Add volume,** mount path `/data/documents`.
5. **backend service → Variables → Raw Editor.** Paste this, then fill in the four `<…>`
   values:
   ```
   MEIP_ENV=staging
   MEIP_LOG_JSON=true
   RAILWAY_RUN_UID=0
   MEIP_OWNER_DB_PASSWORD=<first password>
   MEIP_APP_DB_PASSWORD=<second password>
   MEIP_BOOTSTRAP_SUPERUSER_URL=${{Postgres.DATABASE_URL}}
   MEIP_MIGRATIONS_DATABASE_URL=postgresql+asyncpg://meip_owner:${{MEIP_OWNER_DB_PASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/meip
   MEIP_DATABASE_URL=postgresql+asyncpg://meip_app:${{MEIP_APP_DB_PASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/meip
   MEIP_AUTH_MODE=password
   MEIP_AUTH_SIGNING_KEY_PATH=/tmp/meip/auth_signing_key.pem
   MEIP_AUTH_SIGNING_KEY_PEM=<the whole contents of meip-qa-key.pem>
   MEIP_DOCUMENTS_ENABLED=true
   MEIP_DOCUMENT_RETENTION_APPROVAL=QA demo only: synthetic documents, no real borrower data
   MEIP_BLOB_BACKEND=filesystem
   MEIP_BLOB_ROOT=/data/documents
   MEIP_EXTRACTION_PROVIDER=openai
   MEIP_OPENAI_API_KEY=<your OpenAI key>
   MEIP_OPENAI_MODEL=gpt-4.1
   MEIP_AGENT_PLANNER=openai
   ```
   A multi-line PEM pastes as is. If it ends up on one line with `\n` in it, that works too.
6. **Settings → Networking → Generate Domain.** Note the URL, for example
   `https://backend-production-1234.up.railway.app`.
7. **Deploy.** In the deployment logs, expect:
   - `role meip_owner created`, `role meip_app created`, `database meip created`;
   - then `worker_started` and `startup` with `"env": "staging"`.

   Opening `https://<backend URL>/ready` should show a 200 response.
8. **Delete `MEIP_BOOTSTRAP_SUPERUSER_URL`** from the variables. It's needed only for the first
   boot, and the container shouldn't keep the database superuser. Railway redeploys.

## 3. Onboard the demo firm
Install the Railway CLI (`npm i -g @railway/cli`), then from the repository folder:
```bash
railway login
```
```bash
railway link
```
```bash
railway ssh --service backend
```
Inside that shell:
```bash
meip-admin create-tenant --name "Demo Mortgages Ltd" --admin-email you@example.com --admin-name "Your Name" --operator your-name
```
It prints the temporary password **once**. The first sign-in forces a new password.

## 4. Vercel: the frontend
1. **Add New → Project → import** `BuildA_Mortgage`.
2. **Root Directory:** `frontend`. Next.js is detected automatically. `frontend/vercel.json`
   runs the server functions in London (`lhr1`).
3. **Environment variables:**
   ```
   MEIP_API_URL=https://<backend URL from Railway, no trailing slash>
   MEIP_UI_AUTH=password
   ```
   Don't set `MEIP_UI_DEV_SIGN_IN`. Dev sign-in is off in production builds anyway.
4. **Deploy,** then open `https://<project>.vercel.app/sign-in`.
5. Keep **Deployment Protection** on (Settings → Deployment Protection) for preview
   deployments. Every branch preview talks to the same backend.

## 5. Demo and QA flow
1. Sign in with the temporary password and choose a new one (at least 12 characters).
2. **Team:** invite an adviser. Their temporary password is shown once. They sign in, change
   it and accept the invitation.
3. Create a case, add an applicant, and upload **synthetic** payslips, made from `backend\`
   with:
   ```bash
   .venv\Scripts\python scripts\make_synthetic_payslip.py series demo-payslips --months 3
   ```
   Extraction runs through OpenAI. Each page takes a few seconds; the document goes from
   received → ready → extracted.
4. Review the fields, save, check the profile, approve, and download the evidence pack.

**If extraction fails:** "extraction failed" usually means a bad OpenAI key or one without
credit. The Railway logs show the HTTP status (never document content). Rate limits and
timeouts retry automatically.

## 6. Operating and tearing down
- **Logs:** Railway → backend → Deployments → View logs. Vercel → Project → Logs.
- **Updates:** pushing to `main` redeploys both (migrations run on each backend start).
- **Reset all data:** delete and re-add the Postgres service and the volume, add
  `MEIP_BOOTSTRAP_SUPERUSER_URL` back for one boot, then onboard again.
- **Tear down:** delete the Railway project (database and volume included) and the Vercel
  project. Rotate (revoke) the OpenAI key if it was created only for this.
- **Cost:** Railway usage-based (a small API, worker and database plus a volume is roughly
  $5–20 a month); the Vercel Hobby plan for a personal demo; OpenAI per page processed.

**Verified locally (2026-11-22)** with the same image and start script, against a fresh
PostgreSQL 17:
- the bootstrap created both roles and the database, and a rerun was harmless;
- migrations reached head, and the roles had no superuser or RLS bypass;
- `/ready` returned 200, and `/docs` and `/openapi.json` returned 404;
- the temporary-password flow worked, and a wrong-firm header got 403;
- case, applicant and upload worked, and the worker processed the upload (ingest ready;
  extraction reached OpenAI and failed cleanly with a dummy key);
- the PDF survived a restart and downloaded identical;
- killing the worker stopped the container for a restart.

**Not verified:** on Railway and Vercel themselves, or with a live OpenAI key.
