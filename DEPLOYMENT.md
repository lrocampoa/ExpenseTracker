# Deployment Guide

This document summarizes how to run ExpenseTracker inside Docker and how to deploy it to Render within the $20/month target.

## 1. Environment variables

Create a `.env` file (or Render environment group) with the settings already documented in `.env.example`. Minimum required values:

```
DJANGO_SECRET_KEY=super-secret
DJANGO_DEBUG=0
DATABASE_URL=postgres://...
GOOGLE_OAUTH_CLIENT_SECRET_PATH=/etc/secrets/google_client.json
GMAIL_USER_EMAIL=you@example.com
GMAIL_SEARCH_QUERY=from:(notificacion@notificacionesbaccr.com OR notificaciones@baccredomatic.com)
```

When running in Docker/Render, make sure the Google client secret JSON is available (Render secrets, or mount a volume) and point `GOOGLE_OAUTH_CLIENT_SECRET_PATH` to it.

## 2. Docker

Build the container locally:

```bash
docker build -t expense-tracker .
```

Run it (using SQLite and DB migrations for quick testing):

```bash
docker run --env-file .env -p 8000:8000 expense-tracker
```

The container now runs migrations and `collectstatic` automatically before launching `gunicorn`. You can still override the command (e.g. `docker run ... python manage.py shell`) and the entrypoint will execute it after migrations.

## 3. Render setup

1. **Web Service**  
   - Type: Web Service (Docker, free or $7 Starter).  
   - Build command: `docker build -t expense-tracker .` (handled automatically).  
   - Start command: keep Render’s default; the entrypoint already calls `gunicorn` with `--bind 0.0.0.0:$PORT`.
   - Add environment variables from `.env`. Use Render’s free Postgres first; upgrade later if needed.
   - Tune `WEB_CONCURRENCY`, `GUNICORN_THREADS`, and `GUNICORN_TIMEOUT` environment variables (defaults: 2 workers, 1 thread, 120 s). Increase `GUNICORN_TIMEOUT` if manual imports need more than 30 s, or schedule imports outside the request/response cycle.
   - Set `GMAIL_MAX_MESSAGES_PER_SYNC`/`OUTLOOK_MAX_MESSAGES_PER_SYNC` to conservative values (25–50) so manual imports finish inside the gunicorn timeout. Use scheduled jobs for large backfills.

2. **Database**  
   - Add Render Postgres (free tier). Copy the connection string into `DATABASE_URL`.

3. **Cron Jobs / Scheduled Tasks**  
   1. **Pipelines refresher** – add a cron job with:
      ```
      python manage.py run_scheduled_pipelines --limit 100
      ```
      Run it every 15–30 minutes so every user mailbox gets synced regularly.
   2. **Manual import queue** – add a second cron job for:
      ```
      python manage.py process_import_jobs
      ```
      Run it every 5 minutes (or faster) so `ImportJob`s created from `/importar/` are processed even if the web dyno restarts. The command will exit quickly when there are no jobs in queue.

4. **Google secrets**  
   - Upload the OAuth client JSON as a Render secret file or recreate credentials using environment variables. Make sure `GOOGLE_OAUTH_CLIENT_SECRET_PATH` matches the location inside the container (e.g. `/etc/secrets/google_client.json`).

## 4. Staying within budget

- Web service: start with the free tier. Upgrade to Starter ($7) when traffic increases.  
- Postgres: free tier covers development. Upgrade to Standard ($7) once you need more storage.  
- Cron jobs are free.  
- No background worker/Redis is required initially; `run_scheduled_pipelines` keeps the dataset fresh.

This setup keeps total costs at $0 initially and ~$14/month once both web + database use Starter plans.
