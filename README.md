# Swimm Activity Dashboard

Live at https://swimm-dashboard.vercel.app (login required).

## How it stays up to date

Every night at 01:00 UTC, the GitHub Action in `.github/workflows/nightly-sync.yml`:

1. Pulls the last 30 days of calls, emails and pipeline stages from Swimm's Apollo sub-account (`build_swimm.py`).
2. Checks the result is complete and fresh (`check_payload.py`). If anything looks off, it stops and yesterday's dashboard stays up.
3. Deploys the site to Vercel with the new data baked in.

Pushing to `main` does not deploy on its own. Only the nightly job deploys.

## Secrets (GitHub → Settings → Secrets and variables → Actions)

| Name | What it is |
|---|---|
| `APOLLO_SWIMM_KEY` | API key from the Swimm Apollo sub-account |
| `VERCEL_TOKEN` | Token created in Vercel while logged in as the amiy-dev owner account |

## Run it now

GitHub → Actions → Nightly Swimm sync → Run workflow. Takes 5 to 25 minutes depending on Apollo rate limits.

## If the dashboard goes stale

Open the latest run under Actions. A red "Refuse to deploy" step means Apollo didn't return a full sync. A red "Deploy to Vercel" step means the Vercel token is wrong or expired.

## Vercel env vars (already set on the project)

`APOLLO_SWIMM_KEY`, `SWIMM_SECRET`, `SWIMM_USERS`, `SWIMM_DATA_KEY`, and optionally `CRON_SECRET`. `SWIMM_USERS` controls who can log in.
