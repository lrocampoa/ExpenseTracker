# Expense Tracker MVP Plan

## 1. Goals & Constraints
- **Primary goal:** Automatically ingest Bac Credomatic expense emails from Gmail, extract transaction details (amount, date, last 4 digits, merchant), categorize them, and store structured entries in a database.
- **Tech stack constraint:** Python + Django, deployable on Render.
- **Cost sensitivity:** Favor serverless / autoscaling with minimal idle cost; limited use of paid agent/LLM calls.
- **Scalability:** Design for easy expansion to other banks, additional users, and richer analytics later.

## 2. High-Level Architecture
1. **Gmail Ingestion Service**
   - OAuth2 client with Gmail API (read-only scope) pulling messages via incremental sync (historyId + labels) into a queue.
   - Stores Gmail message metadata (message id, thread id, snippet, internalDate, payload) and raw body for traceability.
2. **Parsing & Extraction Layer**
   - Deterministic filters confirm the email is a transaction alert (subject + sender + key phrases).
   - Rule-based parser (regex + HTML parsing) extracts structured fields.
   - Optional lightweight LLM/agent fallback (only when rules fail) to keep cost low.
3. **Categorization Engine**
   - Primary: deterministic mapping using user-defined categories (merchant keywords, MCC-like tags, card last 4).
   - Secondary: compact LLM or embedding lookup for fuzzy cases.
4. **Persistence & API**
   - Postgres (Render managed) via Django ORM.
   - Core tables: `EmailMessage`, `Transaction`, `Category`, `Card`, `LLMDecisionLog`.
   - Django admin/API endpoints for browsing, corrections, and reprocessing.
5. **Background Workers**
   - RQ/Celery (Redis) or Django-Q on Render for async parsing & categorization.
6. **Monitoring & Ops**
   - Structured logging, retry queues, dashboards (e.g., Django admin, optional Grafana/cron email reports).

## 3. Data Flow
1. Scheduler triggers Gmail sync job every 5 minutes.
2. Fetches new Bac Credomatic emails → stores raw message and enqueues parsing task.
3. Parser extracts amount/date/card/merchant → creates/updates `Transaction` linked to `EmailMessage` by Gmail message ID (dedupe).
4. Categorization engine assigns category + confidence; manual overrides stored in DB.
5. Expose data through Django admin/API/web UI.

## 4. Implementation Phases
### Phase 0: Foundations
- Initialize Django project + git repo structure.
- Configure environment variables, secret management, base settings for Render.
- Define base models (`EmailMessage`, `Transaction`, `Category`, `Card`).

### Phase 1: Gmail Connectivity
- Set up Google Cloud project + OAuth credentials.
- Implement token storage/refresh (DB or encrypted file).
- Build Gmail sync command: list messages filtered by sender/subject, fetch bodies, persist raw content and mark processed historyId checkpoint.
- Add management command / worker job for periodic sync.

### Phase 2: Parsing & Deduplication
- Implement Bac Credomatic specific parser (HTML + regex) with unit tests using fixture emails.
- Create confidence scoring + fallback logic; flag low-confidence items for manual review.
- Ensure idempotency using Gmail message ID + hash of body.

### Phase 3: Categorization Engine
- Build rules engine (keyword ↔ category mapping, card-based fallbacks).
- Add optional lightweight agent/LLM call (e.g., GPT-4o-mini) when rules fail; cache responses in `LLMDecisionLog`.
- Provide admin UI to correct categories and feed back into rules.

### Phase 4: API & UI
- Django REST endpoints for transactions, categories, recount triggers.
- Minimal dashboard (table + filters) served via Django templates or DRF + React-lite later.
- Manual reprocess button for any email.

### Phase 5: Deployment & Ops
- Containerize (Docker) or use Render native buildpacks.
- Provision managed Postgres + Redis (if using Celery/RQ).
- Set up Render cron job for Gmail sync, environment secrets, health checks.
- Add logging/monitoring (Sentry or simple email alerts) and cost guardrails (limits on LLM calls per day).

## 5. Agent / LLM Usage Strategy
- **Parsing:** rules-first, call agent only when regex fails; log tokens + cost per call.
- **Categorization:** rules + embeddings; fallback to LLM for ambiguous cases with strict budget (e.g., max 50 calls/day).
- **Manual review:** Provide UI to override results; feedback loop updates rule sets to reduce LLM reliance.

## 6. Cost Optimization
- Use Render free/low-tier web service for Django API, background worker on same instance if possible.
- Batch Gmail fetches to minimize API calls; store processed historyId to avoid re-reading.
- Cache LLM results and throttle usage via settings.
- Prefer open-source models (e.g., local regex/NER) for deterministic tasks; only escalate to hosted LLM when necessary.

## 7. Future Enhancements
- Support multiple banks/inboxes via pluggable parser interface.
- Multi-user auth + sharing budgets.
- Budgeting rules and alerting (notify when category spending exceeds limits).
- Mobile-friendly PWA UI.
- Advanced analytics (monthly trends, forecast) and invoice attachment OCR.
- Automate tagging feedback loop to retrain categorizer.
