# Expense Tracker MVP Plan

## Active Work: Budget control remaining days (2024-11-26)
1. Review the current dashboard spend-control logic to understand how `days_remaining` and `daily_allowance` are derived today and identify required data points from `_resolve_period`.
2. Extend the period metadata/spend-control calculations so the total period length (e.g., full month or year) is known, enabling a correct “días restantes” count and a daily allowance that divides the remaining budget by the remaining days.
3. Back up the change with a dashboard view test that exercises `range=this_month`; run the suite if possible to ensure regressions are caught early.

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
   - Core tables: `EmailMessage`, `Transaction`, `Category`, `Subcategory`, `Card`, `ExpenseAccount`, `EmailAccount`, `MailSyncState`, `LLMDecisionLog`, `TransactionCorrection`, `RuleSuggestion`.
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
- Initialize Django project + git repo structure. ✅
- Configure environment variables, secret management, base settings for Render. ✅ (dotenv + env-driven settings; ready for Render once deploy configs land)
- Define base models (`EmailMessage`, `Transaction`, `Category`, `Card`). ✅
- Setup virtualenv + requirements, configure `.env` scaffolding. ✅
- Add login/signup/password-reset templates + responsive base layout. ✅

### Phase 1: Gmail Connectivity
- Set up Google Cloud project + OAuth credentials. ✅ (local + Allauth Google login for app auth)
- Implement token storage/refresh (DB or encrypted file). ✅ (`GmailCredential` per user)
- Build Gmail sync command: list messages filtered by sender/subject, fetch bodies, persist raw content and mark processed historyId checkpoint. ✅ (`GmailIngestionService` now prefers history-based incremental sync and records checkpoints via `MailSyncState`)
- Add management command / worker job for periodic sync. ✅ (`sync_gmail`, `sync_mailboxes`, `run_pipeline`, `import_recent_transactions`)
- **Next:** tie Gmail/Outlook sync plus parsing/categorization to per-user cron jobs on Render and expose scheduling knobs in the UI.

### Phase 2: Parsing & Deduplication
- Implement Bac Credomatic specific parser (HTML + regex) with unit tests using fixture emails. ✅ (multiple fixtures inc. USD)
- Create confidence scoring + fallback logic; flag low-confidence items for manual review. ⚠️ `parse_confidence` exists on `Transaction` but the parser never assigns it, so there is no automated review queue yet.
- Ensure idempotency using Gmail message ID + hash of body. ✅ (update_or_create + dedupe on reparse)
- Provide a one-shot management command (`python manage.py run_pipeline`) so Render cron/worker can sync Gmail → process emails → categorize transactions without manual steps. ✅
- Add “Reprocesar” controls in the UI to allow manual re-parsing of any transaction email when templates change. ✅
- **Next:** add transaction `parse_confidence` heuristics + a “needs review” list so low-confidence parses surface on the dashboard instead of relying solely on manual corrections.

### Phase 3: Categorization Engine
- Build rules engine (keyword ↔ category mapping, card-based fallbacks). ✅ (`tracker/services/categorizer.RuleEngine` with priority + card filters)
- Add optional lightweight agent/LLM call (e.g., GPT-4o-mini) when rules fail; cache responses in `LLMDecisionLog`. ✅ (OpenAI fallback + caching/throttling in `tracker/services/llm`)
- Provide admin UI to correct categories and feed back into rules. ✅ (CategoryRuleListView + inline forms + management commands)
- Allow promoting an LLM decision to a deterministic rule directly from Django admin. ⚠️ Action exists (`LLMDecisionLogAdmin.promote_to_rule`) but still references legacy `name`/`confidence` fields, so it needs to be rewritten against the current `CategoryRule` schema.
- Transaction detail UI lets users edit values inline; categorizer runs after parse/reparse. ✅ (`TransactionDetailView` supports edit/reparse/promote-rule)
- Dedicated “categorize correction” workflow that captures manual adjustments + suggests new rules automatically. ✅ (`TransactionCorrection` + `RuleSuggestion` queue, accept/reject in rules view)

### Phase 3.5: Categorization Workflow Enhancements
- Build a Categorization & Rules view where each user can inspect default rules (seeded at signup) and add/edit their own entries (priority, keyword, card filters, confidence toggle). ✅
- Preload a baseline rule set per user derived from common merchants/categories so new accounts start with useful automation immediately. ✅ (`rule_seeding.ensure_defaults` + post-save signal)
- On manual transaction edits (especially category+merchant changes) automatically propose a new rule: `merchant_name` + chosen category, including card last4 when present. Let users confirm/adjust the suggested rule before saving. ✅ (correction logging + rule_suggestions)
- When a transaction is saved, persist both the corrected category and the auto-generated rule (if confirmed) so future transactions auto-classify; log the linkage for auditability and allow one-click disable. ✅ (`rule_suggestions.apply_suggestion` promotes to `CategoryRule`)
- Surface pending suggestions in the rules view so users can accept/decline rules that were inferred but not auto-applied (e.g., low-confidence cases). ✅ (Rules page lists `RuleSuggestion` objects with accept/reject controls)

**Next up for Phase 3 / 3.5**
1. Inline rule toggles + bulk priority editing so users can quickly disable or reorder rules without leaving the list.
2. Show suggestion provenance (source transaction, fields changed, confidence) inside the dashboard manual-review card to triage faster.
3. Add rule-level analytics (match count, last-hit timestamp) so noisy rules can be tuned or retired automatically.
4. Rule priority should behave like a draggable list so users can grab a row and move it up/down to redefine execution order visually.

### Phase 3.6: Budgets, Categorías y Subcategorías
- Introduce first-class “Categorías” (budgets) and “Subcategorías” (granular tags) so spending can be tracked vs limit per parent category. ✅ (models + dashboard budget visualizations)
- Seed default Spanish categorias/subcategorias for every user (e.g., “Utilidades” → “Internet”, “Agua”, “Luz”, “Cuota condominal”, “Alquiler”; “Transporte” → “Gasolina”, “Uber/Taxi”, etc.) together with representative rules. ✅ (`category_seeding.ensure_defaults`)
- Extend transaction forms, filters, and dashboards to capture/display both category + subcategory, and surface budget progress (spent vs limit, alert >90%). ✅ (Transaction filters/forms + dashboard category table)
- Add UI to manage categorias/subcategorias (create, edit, set monthly budget) so users can tailor budgets without touching the admin. ✅ (`CategoryManageView` + inline forms)
- Feed categorización rules + suggestions with subcategory context so future automation can tag the right sub-bucket automatically. ⚠️ Rules + suggestions currently ignore subcategory assignments; need to flow `subcategory` through `RuleSuggestion` + `CategoryRule`.

### Phase 3.7: Expense Accounts & Card Labeling
- Add `ExpenseAccount` model + seeding so cards can be grouped under high-level accounting buckets. ✅ (`account_seeding` seeds “Personal/Familiar/Ahorros`)
- Detect card last4s automatically and provide a card labeling UI to set nicknames + expense accounts, with a detail form for full metadata. ✅ (`CardListView`, inline CardLabelForm, `CardUpdateView`, `static/tracker/cards.js`)
- Surface expense-account filters + dashboard breakdowns so spend can be sliced by accounting bucket. ✅ (DashboardView expense filter + `_build_expense_accounts`; transaction filters respect expense accounts)
- **Next:** extend the Tarjetas screen with per-card usage stats, color tags, and default category/LLM preferences, and expose CRUD API endpoints for cards/expense accounts to support mobile clients. ⚠️

### Phase 4: API & UI
- Responsive transaction dashboard (filters, quick months, inline edit, Gmail email preview). ✅ (TransactionListView + detail + email previews)
- Import wizard (connect Gmail/Outlook, choose years, run pipeline) with OAuth start/callback. ✅
- Manual reprocess buttons for any email or filtered set of transactions. ✅ (TransactionDetailView + list-level “Reprocesar” form)
- Tarjetas screen for card labeling + expense accounts. ✅ (CardListView + CardUpdateView; usage stats + per-card defaults still TODO — see Phase 3.7 follow-ups)
- Minimal dashboard (table + filters) served via Django templates while we defer SPA/React work. ✅
- **Next:** Add DRF endpoints for mobile/app integrations and expose import/sync status/progress via those APIs. ⚠️

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
   - Tie `EmailMessage`, `Transaction`, `Card`, `Category`, `CategoryRule`, `EmailAccount`, `MailSyncState`, `GmailCredential`, and future settings to a `User` foreign key. ✅
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
1. **User Preferences + Sensitive Data** (Phase 6.3 & 6.5) ⚠️
   - Implement a `UserPreference` model storing Gmail/Outlook query overrides, default currency, notification toggles, and LLM budgets per tenant.
   - Encrypt Gmail/Outlook token JSON + sync checkpoints (e.g., Fernet field) and record audit logs for imports, edits, and “reprocesar” actions.
2. **Confidence Scoring & Review Queue** (Phase 2 pending) ⚠️
   - Add `parse_confidence`/`needs_review` fields and surface a “Revisar pendientes” table so low-confidence parses are actioned without manual detective work.
3. **Deterministic Rule Feedback Loop** (Phase 3 enhancement) ✅
   - Completed via `TransactionCorrection` snapshots + `RuleSuggestion` queue; rules page already lets users accept/reject suggestions, but we can keep iterating on provenance UX.
4. **API + Deployment** (Phase 4 & 5) ⚠️
   - Build DRF endpoints (transactions, categories, cards, import runs) and create Docker/Render configs + cron schedule for `run_pipeline`/`import_recent_transactions`.
5. **Hotmail Integration** ✅
   - Microsoft identity device + OAuth views/commands (`OutlookOAuthStart/Callback`, `python manage.py outlook_auth`) allow linking multiple Outlook/Hotmail inboxes per user.
   - Unified `sync_mailboxes` command runs Gmail history fetches and Microsoft Graph delta queries, persisting checkpoints per account in `MailSyncState`.
6. **Promerica / Banco Nacional / Banco de Costa Rica Ingestion** ⚠️
   - Expand parser + rule set to cover Promerica, Banco Nacional, and Banco de Costa Rica email templates (HTML + plain text variants).
   - Provide fixtures/tests per bank and allow per-bank routing inside the ingestion pipeline.

## 8. Insightful Dashboard Plan
- **Available data to leverage**
  - `Transaction`: amount, currency, transaction_date, category, merchant, location, parse/category confidence, metadata, card linkage.
  - `Category`, `Subcategory` & `CategoryRule`: semantic grouping, budgets/notes, rule priority, and which rule triggered the category assignment.
  - `Card` & `ExpenseAccount`: label, bank, network, last4, accounting bucket, active status for attribution and filtering.
  - `TransactionCorrection` & `RuleSuggestion`: manual override history plus pending automation suggestions.
  - `LLMDecisionLog`: frequency/cost of AI fallbacks for parsing/categorization plus cache hits. 
  - `EmailMessage`, `EmailAccount` & `MailSyncState`: ingestion timestamps, sync counts, provider checkpoints, and pipeline freshness.
- **Dashboard objectives**
  - Help users answer “Where is my money going?”, “Is this period trending higher or lower?”, “Which merchants/cards drive the change?”, and “What needs my action now?”.
  - Mix retrospective insight (totals, trends) with proactive nudges (overspend alerts, pending reviews, sync health).
- **Layout & modules**
  1. **Hero KPIs & Alerts** ✅
     - Total spend for selected period vs previous window (% change), avg daily spend, number of transactions, active cards used plus alert badges (uncategorized count, failed parses, LLM cost, stale syncs).
  2. **Category & Budget Insights** ✅
     - Ranked table with spend, budget usage, delta vs prior period, plus a compact budget-progress chart for top categories.
  3. **Spend Control Panel** ✅
     - Projected burn vs budget, remaining allowance, run rate vs ideal rate, and daily recommendation.
  4. **Spending Trend (Monthly for now)** ⚠️
     - Currently a monthly area/line chart with MoM delta/averages; need to evolve into the planned daily/weekly stacked view with spike annotations.
  5. **Expense Accounts** ✅
     - Breakdown of spending per `ExpenseAccount`, stacked bars per card, pie chart, and share-of-wallet stats tied to the dashboard’s expense-account filter.
  6. **Merchant & Location Signals** ⚠️
     - Top/new merchants and suspected subscriptions exist; still need the planned location heat map/table once geo metadata is captured.
  7. **Card Health** ✅
     - Spend by card, transaction counts, average ticket, and an idle-card list flagging cards without recent activity.
  8. **Anomalies & Action Queue** ✅
     - Transactions needing attention (uncategorized, manual flags, high amounts) with quick links to detail/reprocess; will benefit from parse_confidence once implemented.
  9. **Convertir en Regla CTA** ✅
     - Repeated merchants without categories + CTA that pre-fills the rule form (ties into `RuleSuggestion` backlog).
 10. **Automation Insight & LLM Usage** ✅
     - Breakdown of category sources (rule/llm/manual) plus aggregated LLM token/cost stats for the selected period.
 11. **Sync & Data Freshness** ✅
     - `MailSyncState` stats (history/delta checkpoints, last sync, fetched count, retries) with stale warnings per label.
 12. **Manual Corrections Digest** ✅
     - Snapshot of recent `TransactionCorrection` entries, counts, and top merchants driving manual work.
- **Implementation notes**
  - Keep queryset helpers per module to scope aggregations per user/time window (DashboardView already centralizes most of this).
  - Monitor DB load; consider caching heavy aggregates (monthly totals, expense-account breakdowns) via Redis/materialized tables to keep render times <200 ms.
  - Expose dashboard data through API endpoints once DRF is in place so future clients (mobile/PWA) can reuse the same analytics.

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
   - Outlook/Hotmail accounts piggyback on `sync_mailboxes`, which stores Microsoft Graph `deltaLink` cursors in `MailSyncState` alongside Gmail history IDs.

3. **Targeted “import recent” management command**
   - Create `python manage.py import_recent_transactions` (wrapper around `run_pipeline`) that: (a) triggers Gmail/Outlook sync in incremental mode, (b) processes only the newly stored `EmailMessage` ids, and (c) categorizes just the transactions touched in that run.
   - Support `--user-email`, `--account-email`, `--since`/`--limit` flags so support can backfill a single tenant or catch up after downtime without replaying months of data.
   - Emit summary stats (“fetched X, already in DB Y, transactions created Z”) so operators can confirm that only missing rows were imported.

4. **Verification, alerts, and UI hooks**
   - Surface last import timestamp, next scheduled sync, and “pending emails” count on the settings/dashboard screens using `MailSyncState` + `EmailMessage` data.
   - Add unit tests around the Gmail history path, the new command wrapper, and regression tests that prove re-running the import when nothing is missing performs zero writes.
   - Log or notify (email/slack) when sync falls behind a threshold (e.g., >2 hours since last historyId) so we can proactively trigger the “import recent” task.

## 10. Multi-Account & Family Sharing Roadmap

-### 10.1 Product Goals
- Allow a single user to connect **multiple email inboxes and cards** (already mostly supported by `EmailAccount`, `Card`, and `EmailMessage`) and treat them as one personal financial view.
- Introduce **shared group budgets** (families, roommates, business partners, etc.) where multiple users can see and contribute to shared expense accounts, without exposing all of their private accounts.
- Keep **categories, subcategories, and budgets** consistent at the family level, while allowing users to maintain separate personal taxonomies.
- Support **permissioned collaboration**: admins manage family structure and budgets; members can spend, categorize, and propose changes without accidentally mutating global settings.

### 10.2 Core Data Model Extensions
1. **Family / Group entity**
   - Add `SpendingGroup` (UI string: Family / Roommates / Business – generic “Shared Group”) capturing: `name`, `slug`, `created_by`, optional `currency`, `group_type`, `is_active`.
   - Add `GroupMembership` with FK to `SpendingGroup` and `User`, plus `role` (Admin, Member), `status` (Invited, Active, Left), `budget_share_percent`, and timestamps.
   - `budget_share_percent` tracks how much of the group budget each member is expected to cover (e.g., Dad 70%, Mom 30%, Roommates 50/50) and defaults to equal shares until an admin edits.
   - Only Admins can edit group settings, manage members, configure budget shares, and change shared categories/budgets.

2. **Expense accounts: personal vs shared**
   - Extend `ExpenseAccount` so it can belong to either a **user** (personal) or a **group** (shared account for family/roommates/business):
     - Keep existing `user` FK for personal accounts.
     - Add optional FK `group = models.ForeignKey(SpendingGroup, null=True, blank=True, related_name="expense_accounts")`.
     - Enforce invariant: exactly one of (`user`, `group`) must be non-null; unique constraint per (`user`, `name`) and (`group`, `name`).
   - UX: when creating/editing an expense account, user chooses **scope**: “Personal” or “Shared group: [name]”. If switching from personal → shared, prompt whether to expose **all historical transactions** or **only new ones going forward** (recorded via a flag on the account so ingestion knows whether to re-tag history).
   - This satisfies: a) each member keeps private personal accounts, and b) they can add **specific new accounts** into any group (family, roommates, business) without sharing all of their cards.

3. **Transactions & group attribution**
   - Add optional FK `group` on `Transaction` pointing to `SpendingGroup`.
   - When a transaction is tied to an `ExpenseAccount` that belongs to a group, set `transaction.group = expense_account.group` automatically.
   - Keep existing `transaction.user` as the **originating user** (owner of the email/card); the **group** is a secondary dimension used for family dashboards and budgets.

4. **Categories, subcategories, and budgets: scoped**
   - Generalize `Category` and `Subcategory` to be **scoped to either a user or a group**:
     - Add optional FK `group` to both models.
     - Enforce invariant: exactly one of `user` or `group` is non-null.
     - Unique constraints:
       - Personal: (`user`, `code`) as today.
       - Family: (`group`, `code`) for shared taxonomies.
   - `budget_limit` on `Category`/`Subcategory` becomes **scope-aware**:
     - Personal categories: personal monthly budget.
     - Group categories: shared family budget managed by group admins.

5. **Category suggestions for families**
   - Introduce a lightweight `CategorySuggestion` model (or extend `RuleSuggestion`) scoped to a group:
     - Fields: `group`, `requested_by`, `name`, `parent_category`, `type` (Category/Subcategory), `status` (Pending, Approved, Rejected), and optional notes.
   - Any family member can propose new categories/subcategories; admins approve to turn them into real group-scoped categories.

### 10.3 Multi-Email & Account UX Improvements
1. **Email account management**
   - Reuse existing `EmailAccount` model to support **multiple Gmail and Outlook accounts** per user (already in place).
   - Settings page lists connected inboxes (`provider`, `email_address`, last sync, errors) with controls to:
     - Pause/resume syncing for a given inbox.
     - Configure per-account filters (labels/queries) so users can limit which messages become transactions.

2. **Transaction source clarity**
   - For each transaction, render **email source** as: `provider` + `account.email_address`, and deep-link to the message:
     - Gmail: current `gmail_message_url`.
     - Outlook: future `outlook_message_url` based on stored `internet_message_id` / Graph link.
   - In the transaction detail, update “Origen del correo” to show: `Cuenta: <email_address>` plus provider-specific “Abrir en Gmail/Outlook” links.

### 10.4 Group Membership & Sharing Flows
1. **Creating and joining a family**
   - A user can create a `SpendingGroup` (choose type e.g., Family / Roommates / Business) and becomes **Group Admin**.
   - Admin invites other users via email; accepted invites create `GroupMembership` with role Member.
   - Users can belong to **multiple groups** (e.g., “Familia Ocampo”, “Roommates CDMX”).

2. **Sharing expense accounts**
   - When creating/editing an `ExpenseAccount`, user chooses:
     - `Personal` → remains visible only in personal dashboards.
     - `Shared group: [name]` → becomes visible in that group’s dashboards.
   - During the sharing flow, ask whether to **share existing historical transactions** or **only new ones**. Implementation: keep a `share_history_since` timestamp on the account; when toggled, existing transactions older than that date remain private, newer ones adopt the group reference.
   - Constraint: any member of the group can **post transactions** into shared group accounts, but only the account owner or admins may delete/rename the account.

3. **Permissions**
   - **Group Admins**:
     - Manage memberships, roles, and `budget_share_percent` allocations.
     - Create/edit/delete group-level `ExpenseAccount`s.
     - Define and edit group `Category`/`Subcategory` and budgets.
     - Approve or reject `CategorySuggestion`s.
   - **Group Members**:
     - View all group-level expense accounts, transactions, dashboards, and budget-share breakdowns.
     - Assign their transactions to group accounts and categories.
     - Propose category/subcategory changes via suggestions (UI for now; email notifications to be added later).
   - **Personal scope** remains fully controlled by each user and never exposed to groups unless explicitly assigned to a group account.

### 10.5 Dashboards: Personal vs Family Views
1. **Personal dashboard (per user)**
   - Aggregations based on `Transaction.user = current_user` across:
     - Personal expense accounts.
     - Group accounts (since these still represent real spending by the user).
   - Budgets:
     - Personal categories (`Category.user = current_user`).
     - Show “family contributions” as a separate section (optional) to highlight how much of personal spending is in shared accounts.

2. **Family dashboard (per group)**
   - User selects a group (or default to primary family) from a switcher.
   - Aggregations based on `Transaction.group = selected_group`, regardless of which member originated the transaction.
   - Use group-level categories/subcategories for budget progress, top merchants, and alerts.
   - Optional filters per member (e.g., “show only mamá’s transactions in family account”).

3. **Navigation & UX**
   - Clear toggle between **“Personal”** and **“Family: [name]”** in the dashboard header.
   - Ensure filters (date ranges, cards, expense accounts) respect the selected scope and never mix group data into personal views without clear labeling.

### 10.6 Incremental Implementation Plan
1. **Step 1 – Data model scaffolding**
   - Add `SpendingGroup`, `GroupMembership`, `Transaction.group`, `ExpenseAccount.group`, and `Category`/`Subcategory.group` with migrations and admin screens.
   - Backfill existing rows: all current categories/subcategories/expense accounts remain **personal** (user-set, group = NULL).

2. **Step 2 – Family creation & membership UX**
   - Add settings pages to create families, invite members, and manage roles.
   - Add tests for membership permissions and group visibility.

3. **Step 3 – Group-scoped expense accounts & transactions**
   - Extend account creation/edit forms to support group scope.
   - Wire `Transaction.group` assignment via `ExpenseAccount.group` and update queries used by dashboards and reports.

4. **Step 4 – Family taxonomies and budgets**
   - Add group-scoped categories/subcategories plus budget configuration UI for admins.
   - Implement `CategorySuggestion` flow and admin approval screens.

5. **Step 5 – Dashboards and filters**
   - Add personal/family scope switcher and adapt existing dashboard modules to filter by user or group as described.
   - Add member-level filters on family dashboards where useful.

6. **Step 6 – Polishing & guardrails**
   - Tidy up “Origen del correo” and transaction detail to surface source account + group.
   - Add audit logs for membership changes and group budget edits.
   - Document permission rules clearly in the UI to avoid surprises for end users.

## 11. Subscription Surfacer Feature Plan

### 11.1 Problem Statement & Goals
- Surface recurring subscriptions automatically so users can evaluate spend, downgrade, or cancel without hunting through statements.
- Tie recurring insights into the dashboard (card + table) and a dedicated action flow that guides cancellation or downgrade steps.
- Capitalize on existing “suspected subscriptions” logic inside the Merchant & Location signals and expand it into a persistent, reviewable artifact.

### 11.2 Detection Inputs & Signals
- **Transaction patterns:** recurring merchants, identical amounts, subscription-tagged categories, charges near the same day each month, and multi-month aggregations for annual plans.
- **Email intelligence:** scan Gmail/Outlook messages for subscription keywords (trial ending, renewal confirmation, receipt, “you have been charged”, “subscription paused/canceled”) and metadata such as merchant support links.
- **LLM augmentation:** when deterministic rules are uncertain, invoke a lightweight LLM prompt with the raw email snippet/transaction context to classify if it is a subscription and capture cancellation hints.
- **False-positive guards:** exclude transactions already labeled as one-off (travel, taxes) or flagged as reimbursable.

### 11.3 Data Model & Storage
- Add `SubscriptionCandidate` (user, optional group, merchant, product_name, amount, cadence, confidence, first_seen, last_charge_date, detection_sources[], status: active/paused/canceled/ignored, cancellation_support: manual/link/auto).
- Link candidates to `Transaction` rows for provenance and to the originating `EmailMessage` when email evidence exists.
- Store `CancellationAction` entries when a user documents that they called, emailed, or used an automated cancel helper.
- Extend `Merchant`/`Category` metadata with hints like `is_subscription_heavy`, cancellation URLs, and trial duration defaults.

### 11.4 Pipeline & Automation
- Nightly job aggregates `Transaction` data to refresh/expire candidates and mark inactive ones after N missed billing cycles.
- Email ingestion step pushes subscription-relevant messages into a queue so the detector can run close to real time.
- Add a “subscription intelligence” worker that enriches candidates (customer support email, account portal link) either from known merchant playbooks or scraped email content.
- Provide manual override tools to mark false positives or convert ad-hoc recurring reminders into subscriptions.

### 11.5 Dashboard & UX
- Dashboard module showing total monthly subscription spend, delta vs prior period, and “save X if you cancel highlighted items”.
- Detailed table with merchant, plan tier, next renewal date, cadence, amount, owner (personal vs group member), detected source, and CTA (cancel/downgrade/pause).
- Individual subscription drawer linking back to all related transactions and emails, plus status timeline and notes.
- Surface proactive alerts (e.g., “Disney+ also used by 3 family members—consider family plan”, “Free trial ends in 3 days”).

### 11.6 Cancellation Experience
- Tiered approach: (1) Show instructions and contact info extracted from emails/knowledge base; (2) Provide deep links to manage page; (3) For supported merchants, trigger automated cancellation (e.g., autopopulate email template or scripted flow through the merchant portal/API).
- Log each attempt and prompt users to confirm success, updating status to canceled and optionally tagging the last transaction as final.
- Offer “monitor for confirmation” toggle that watches inbox for confirmation emails to auto-close the loop.

### 11.7 Group & Family Sharing
- Subscription candidates inherit `group` when linked expense accounts or merchants are shared; roll up amounts into family dashboards.
- Highlight overlapping subscriptions across members (e.g., individual Spotify plans) and estimate savings from switching to a family plan, using membership count + merchant pricing models.
- Allow group admins to suggest consolidated plans and push reminders/approvals to members.
- Respect privacy by letting members mark certain subscriptions as personal even inside a family budget.

### 11.8 Security, Privacy & Compliance
- Keep subscription-related email snippets minimized/redacted; store references to Gmail message IDs rather than full bodies when possible.
- Provide opt-outs per user for scanning non-transaction emails.
- Document automated cancellation flows clearly and require explicit consent before acting on a user’s behalf for each merchant.
- Honor the current constraint of **not flagging free trials before the first charge**; only surface subscriptions once a transaction or renewal confirmation email exists.

### 11.9 Open Questions / Clarifications
1. Which cancellation automation scope is acceptable initially—just templated emails or full scripted browser/API flows?
2. Are we allowed to store/share email snippets across family members when surfacing shared opportunities, or must we abstract the insight?
3. Do we need to support subscription splitting (one membership billed to a single card but reimbursed by multiple members) as part of the action flow?

## 12. Balancing of Shared Costs Feature Plan

### 12.1 Problem Statement & Goals
- Ensure each spending group member pays the agreed percentage of shared budgets, even when some members advance more expenses within a period.
- Provide a settlement workflow that calculates how much each participant owes/is owed, using both imported transactions and manual entries.
- Allow users to add manual expenses tied to expense accounts and reassign existing transactions to the correct account/group for accurate reporting.

### 12.2 Data Model & Inputs
- Extend `SpendingGroup`/`GroupMembership` with persisted `budget_share_percent` (already referenced) and add `GroupSettlement` (period_start, period_end, total_shared_spend, status, locked_by, locked_at).
- Create `ManualExpense` (user, group, expense_account, amount, currency, date, merchant/description, payer member, optional attachment, included_in_settlement flag, optional `Transaction` link for reimbursements).
- Add `TransactionAccountChange` history capturing old/new expense account, group, timestamp, and actor to audit reassignment events.
- Tag each `Transaction` with a `payer_member` derived from the owning card by default, but editable when moving transactions or logging manual expenses.

### 12.3 Balancing Algorithm
- Inputs: group, date range (default current month), member share percentages, transactions tagged to group accounts, manual expenses.
- Steps per settlement:
  1. Aggregate total shared spend = sum(transactions + manual expenses marked for inclusion).
  2. For each member, compute `owed_share = total * budget_share_percent`.
  3. Sum actual contributions per member (transactions they paid + manual expenses they entered).
  4. Delta = contributions − owed_share (positive → member is owed; negative → member owes).
  5. Generate transfer suggestions using a minimal-transfer algorithm and store them in `GroupSettlementRecommendation` (from_member, to_member, amount, status).
- Allow settlements to be regenerated while unlocked; once locked, future adjustments roll into the next period.

### 12.4 Manual Expense & Transaction Reassignment UX
- Manual expense form: select group + expense account, payer member, date, description, amount, currency, optional note/receipt, toggle for settlement inclusion.
- Transaction detail drawer: “Move to expense account…” flow updates `expense_account`, `group`, and optionally `payer_member`; prompts for confirmation and logs the change.
- Bulk reassignment mode from dashboards to move multiple entries (filters by merchant/date/card) to another expense account.
- Activity log surfaces manual expenses, moves, and settlement actions to keep group members informed.

### 12.5 Dashboard & Workflow
- Add “Shared Cost Balancing” module inside family dashboard summarizing total shared spend vs budget, per-member owed vs contributed amounts, and outstanding deltas.
- CTA buttons for “Add manual expense”, “Move transactions”, and “Generate settlement”.
- Settlement detail page lists included transactions/manual entries, adjustment notes, recommended transfers, and status toggles (“Requested”, “Paid”).
- Optional notifications/reminders when a member exceeds their share or when settlements remain unlocked past period end.

### 12.6 Edge Cases & Clarifications
1. Should manual expenses support file attachments/receipts for later audits?
2. When reassigning transactions, do we need dual approval (payer + admin) or is single-user confirmation acceptable?
3. Are settlements always calendar-month based, or should custom ranges (biweekly, custom cutoff) be supported?
