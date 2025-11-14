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
- Define base models (`EmailMessage`, `Transaction`, `Category`, `Card`). ✅
- Setup virtualenv + requirements, configure `.env` scaffolding. ✅
- Add login/signup/password-reset templates + responsive base layout. ✅

### Phase 1: Gmail Connectivity
- Set up Google Cloud project + OAuth credentials. ✅ (local + Allauth Google login for app auth)
- Implement token storage/refresh (DB or encrypted file). ✅ (`GmailCredential` per user)
- Build Gmail sync command: list messages filtered by sender/subject, fetch bodies, persist raw content and mark processed historyId checkpoint. ⚠️ *HistoryId checkpoint stored but incremental logic still pending*
- Add management command / worker job for periodic sync. ✅ (`sync_gmail`, `run_pipeline`)
- **Next:** tie Gmail sync, parsing, categorization to per-user cron jobs (Render) and finish historyId incremental pull.

### Phase 2: Parsing & Deduplication
- Implement Bac Credomatic specific parser (HTML + regex) with unit tests using fixture emails. ✅ (multiple fixtures inc. USD)
- Create confidence scoring + fallback logic; flag low-confidence items for manual review. ⚠️ *Still pending confidence flag + review queue*
- Ensure idempotency using Gmail message ID + hash of body. ✅ (update_or_create + dedupe on reparse)
- Provide a one-shot management command (`python manage.py run_pipeline`) so Render cron/worker can sync Gmail → process emails → categorize transactions without manual steps. ✅
- Add “Reprocesar” controls in the UI to allow manual re-parsing of any transaction email when templates change. ✅
- **Next:** add transaction `parse_confidence`, flagging + listing low-confidence entries for manual review.

### Phase 3: Categorization Engine
- Build rules engine (keyword ↔ category mapping, card-based fallbacks).
- Add optional lightweight agent/LLM call (e.g., GPT-4o-mini) when rules fail; cache responses in `LLMDecisionLog`.
- Provide admin UI to correct categories and feed back into rules.
- Allow promoting an LLM decision to a deterministic rule directly from Django admin.
- Rules engine + LLM fallback complete, per-user aware. ✅
- Admin supports editing categories/rules and promoting LLM decisions. ✅
- Transaction detail UI lets users edit values inline; categorizer runs after parse/reparse. ✅
- **Next:** dedicated “categorize correction” workflow that captures manual adjustments + suggests new rules automatically.

### Phase 3.5: Categorization Workflow Enhancements
- Build a Categorization & Rules view where each user can inspect default rules (seeded at signup) and add/edit their own entries (priority, keyword, card filters, confidence toggle).
- Preload a baseline rule set per user derived from common merchants/categories so new accounts start with useful automation immediately.
- On manual transaction edits (especially category+merchant changes) automatically propose a new rule: `merchant_name` + chosen category, including card last4 when present. Let users confirm/adjust the suggested rule before saving.
- When a transaction is saved, persist both the corrected category and the auto-generated rule (if confirmed) so future transactions auto-classify; log the linkage for auditability and allow one-click disable.
- Surface pending suggestions in the rules view so users can accept/decline rules that were inferred but not auto-applied (e.g., low-confidence cases).

**Next up for Phase 3 / 3.5**
1. Inline rule toggles + bulk priority editing so users can quickly disable or reorder rules without leaving the list.
2. Show suggestion provenance (source transaction, fields changed, confidence) inside the dashboard manual-review card to triage faster.
3. Add rule-level analytics (match count, last-hit timestamp) so noisy rules can be tuned or retired automatically.
4. Rule priority should behave like a draggable list so users can grab a row and move it up/down to redefine execution order visually.

### Phase 3.6: Budgets, Categorías y Subcategorías
- Introduce first-class “Categorías” (budgets) and “Subcategorías” (granular tags) so spending can be tracked vs limit per parent category.
- Seed default Spanish categorias/subcategorias for every user (e.g., “Utilidades” → “Internet”, “Agua”, “Luz”, “Cuota condominal”, “Alquiler”; “Transporte” → “Gasolina”, “Uber/Taxi”, etc.) together with representative rules.
- Extend transaction forms, filters, and dashboards to capture/display both category + subcategory, and surface budget progress (spent vs limit, alert >90%).
- Add UI to manage categorias/subcategorias (create, edit, set monthly budget) so users can tailor budgets without touching the admin.
- Feed categorización rules + suggestions with subcategory context so future automation can tag the right sub-bucket automatically.

### Phase 4: API & UI
- Responsive transaction dashboard (filters, quick months, inline edit, Gmail email preview). ✅
- Import wizard (connect Gmail, choose years, run pipeline) with Google OAuth start/callback. ✅
- Manual reprocess button for any email. ✅
- **Next:** Add DRF endpoints for mobile/app integrations and expose import status/progress via API.
- **Upcoming:** Build a “Tarjetas” screen where users can (a) view masked cards with usage stats, (b) add/edit card metadata (nickname, bank, color), (c) toggle active/inactive, and (d) assign default categories/LLM preferences per card; expose CRUD endpoints to support it.
- Minimal dashboard (table + filters) served via Django templates or DRF + React-lite later.
- Manual reprocess button for any email.

### Phase 5: Deployment & Ops
- **Pending:** Docker/Render setup, Postgres/Redis provisioning, cron schedule for `run_pipeline`, monitoring/alerts, and cost-tracking dashboards. Add to upcoming backlog once pipeline is production-ready.

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

## Phase 6: User Accounts & Access Control
1. **Authentication & Onboarding**
   - Integrate Django allauth (or social-auth) for Google OAuth so users can register/login with their Gmail account. Store Google profile info + refresh tokens securely per user. ✅ (Allauth login/signup + Google provider)
   - Provide optional email/password signup for non-Google users (with email verification) and password reset flow. ✅ (custom templates, console email backend)

2. **Data Ownership**
   - Tie `EmailMessage`, `Transaction`, `Card`, `Category`, `CategoryRule`, `GmailCredential`, `GmailSyncState`, and future settings to a `User` foreign key. ✅
   - Enforce per-user queryset filtering in views/APIs and admin (or add staff dashboards for multi-tenant support). ✅ (views/forms/commands scoped)

3. **User Preferences & Settings**
   - Model a `UserPreference` object storing per-user Gmail labels, default categories, LLM budgets, notification settings. ⚠️ *Next work item*
   - Add UI for changing Gmail search query, quick month chips, default currency, and enabling/disabling LLM fallback per user. ⚠️ *Next work item*

4. **Access Policies**
   - Require login for transaction dashboard, allow superusers/admins to impersonate or switch tenants safely. ✅ (LoginRequired mixins; admin handles staff)
   - Ensure management commands accept a `--user` or operate per-user queues when scheduling pipeline jobs. ✅ (`--user-email` flags)

5. **Security & Compliance**
   - Encrypt sensitive fields (refresh tokens, Gmail history IDs) at rest. ⚠️ *Planned* (currently plaintext JSON)
   - Add audit logs for edits/reprocess actions; expose last updated/edited by in the UI. ⚠️ *Planned*

### Upcoming Next Steps
1. **User Preferences + Sensitive Data** (Phase 6.3 & 6.5)
   - Implement a `UserPreference` model storing Gmail query overrides, LLM budgets, default currency, etc.
   - Encrypt Gmail token JSON/history IDs (e.g., using `FernetField` or custom encryption) and add basic audit logging for critical actions (imports, edits, reprocesar).
2. **Confidence Scoring & Review Queue** (Phase 2 pending)
   - Add `parse_confidence`/`needs_review` fields and surface a “Revisar pendientes” table.
3. **Deterministic Rule Feedback Loop** (Phase 3 enhancement)
   - When a user changes a category, offer “convert to rule” suggestions and log manual overrides.
4. **API + Deployment** (Phase 4 & 5)
   - Build DRF endpoints (transactions, categories, import runs) and create Docker/Render configs + cron schedule for `run_pipeline`.
5. **Hotmail Integration** ✅
   - Microsoft identity device + OAuth flows allow linking multiple Outlook/Hotmail inboxes per user.
   - Unified mailbox sync command runs both Gmail history-based fetches and Microsoft Graph delta queries, storing per-account checkpoints.
6. **Promerica / Banco Nacional / Banco de Costa Rica Ingestion**
   - Expand parser + rule set to cover Promerica, Banco Nacional, and Banco de Costa Rica email templates (HTML + plain text variants).
   - Provide fixtures/tests per bank and allow per-bank routing inside the ingestion pipeline.

## 8. Insightful Dashboard Plan
- **Available data to leverage**
  - `Transaction`: amount, currency, transaction_date, category, merchant, location, parse/category confidence, metadata, card linkage.
  - `Category` & `CategoryRule`: semantic grouping, budgets/notes, and which rule triggered the category assignment.
  - `Card`: label, bank, network, last4, active status for per-card spend trends.
  - `LLMDecisionLog`: frequency/cost of AI fallbacks for parsing/categorization plus cache hits. 
  - `EmailMessage` & `GmailSyncState`: ingestion timestamps, sync counts, and pipeline freshness.
- **Dashboard objectives**
  - Help users answer “Where is my money going?”, “Is this period trending higher or lower?”, “Which merchants/cards drive the change?”, and “What needs my action now?”.
  - Mix retrospective insight (totals, trends) with proactive nudges (overspend alerts, pending reviews, sync health).
- **Layout & modules**
  1. **Hero KPIs**
     - Total spend for selected period vs previous window (% change), avg daily spend, number of transactions, active cards used.
     - Alert badges: uncategorized count, failed parses, LLM token cost vs budget, days since last sync.
  2. **Spending Trend**
     - Daily/weekly time-series stacked by category or card with 7-day moving average.
     - Spike annotations linking to top merchants or large single transactions.
  3. **Category Insights**
     - Treemap or ranked bars showing share of wallet + delta vs prior month.
     - Budget progress (Category metadata) with warning colors once >90% of limit.
     - Drill-down table for a selected category listing merchants, avg ticket, recurring charges (std dev < threshold).
  4. **Merchant & Location Signals**
     - Top merchants by spend, new merchants this period, suspected subscriptions (3+ equal charges).
     - Location heat map/table using `Transaction.location` + metadata geocodes when available.
  5. **Card Health**
     - Spend by card, average transaction size, idle cards (no transactions in N days), high-value alerts per card.
     - Flag cards tied to failed parses or high manual overrides.
  6. **Anomalies & Action Queue**
     - Transactions with low parse/category confidence, missing category, or amount above user-set threshold.
     - Quick actions (reprocess, recategorize, open Gmail via `gmail_message_url`) and ability to promote to a rule.
  7. **Automation Insight**
     - Breakdown of categorization source (rule vs LLM vs manual), manual overrides count, token cost trend.
  8. **Sync & Data Freshness**
     - `GmailSyncState` stats: last historyId, last_synced_at, messages fetched, retries; notify if sync is stale.
- **Implementation notes**
  - Add queryset helpers to scope aggregations per user/time window and reuse them across API + templates.
  - Materialize frequently used aggregates (daily spend, category totals) via cached tables or Redis to keep dashboard <200ms.
  - Expose dashboard data through dedicated API endpoints to power both Django templates now and richer clients later.

## 9. Recent Missing Transactions Import Plan ✅
**Goal:** Automatically pull only the newest card notifications that do not yet exist as `Transaction` rows so users always see fresh data without reprocessing the full mailbox. Implemented via Gmail history-based incremental sync + the `python manage.py import_recent_transactions` command (scoped by user/email/since) introduced in this iteration.

1. **Detect the gap & scope processing**
   - Add helper queries (e.g., on `Transaction` or `EmailMessage`) that capture the latest `history_id`, `internal_date`, and `transaction_date` per `EmailAccount` via `MailSyncState`.
   - When running `process_emails`, allow filtering by `account`/`user` and `processed_at__isnull=True`, plus a `--since` timestamp derived from the last stored transaction so we only parse emails that could produce missing rows.
   - Keep existing dedupe guarantees (`gmail_message_id`, `reference_id`) so retrying the import never creates duplicates even if Gmail re-sends history fragments.

2. **Incremental Gmail fetch for “latest only” imports**
   - Extend `GmailIngestionService.sync()` to read the stored `history_id`; if present, call `users().history().list` to collect only message ids added since that checkpoint, then fetch just those full messages.
   - Fallback to the current search-based listing when there is no checkpoint or the Gmail history window expired (404), and immediately seed a fresh checkpoint with the returned `historyId`.
   - Persist richer checkpoint metadata (history id, cursor timestamp, batch size) on `MailSyncState` so each account knows exactly which messages have already been ingested.

3. **Targeted “import recent” management command**
   - Create `python manage.py import_recent_transactions` (wrapper around `run_pipeline`) that: (a) triggers Gmail/Outlook sync in incremental mode, (b) processes only the newly stored `EmailMessage` ids, and (c) categorizes just the transactions touched in that run.
   - Support `--user-email`, `--account-email`, `--since`/`--limit` flags so support can backfill a single tenant or catch up after downtime without replaying months of data.
   - Emit summary stats (“fetched X, already in DB Y, transactions created Z”) so operators can confirm that only missing rows were imported.

4. **Verification, alerts, and UI hooks**
   - Surface last import timestamp, next scheduled sync, and “pending emails” count on the settings/dashboard screens using `MailSyncState` + `EmailMessage` data.
   - Add unit tests around the Gmail history path, the new command wrapper, and regression tests that prove re-running the import when nothing is missing performs zero writes.
   - Log or notify (email/slack) when sync falls behind a threshold (e.g., >2 hours since last historyId) so we can proactively trigger the “import recent” task.
