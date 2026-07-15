# SIGMA — Seizure Intelligence & Growth Management Agent

**Intelligent Patient Journey Analyser · Agentic Pharma Strategy Platform**

SIGMA is an **AI agent** (powered by **Claude**) built around one cohort: **seizure & epilepsy
patients** (incl. Lennox-Gastaut Syndrome). It reads a live SQL Server database of their
journeys, explains *what the numbers mean* in plain business English — always leading with the
seizure patients first, then the business/physician action — and, once a human approves,
**actually executes** those actions (appointments, notifications, diagnostic-test orders,
claim escalations, marketing/payer strategy moves).

**Business identity:** OUR company is **EISAI** (hero brands **BANZEL** and **FYCOMPA**).
Competitors: Epidiolex (Jazz), Onfi (Lundbeck), Xcopri (SK Life Science), and branded/generic
makers grouped as **OTHERS** (incl. Keppra, Vimpat, Briviact, Fintepla, Nayzilam).
Every number in the app — patients, sales, claims — is scoped to the **seizure cohort only**,
never a general population.

---

## 1. What you need before starting (prerequisites)

| Requirement | Details |
|---|---|
| **Python 3.11+** | From python.org (tick "Add to PATH" while installing) |
| **A SQL Server database with the project data** | The repo contains **code only — no data**. You need the project database (18 tables). See §3 for your options. |
| **Microsoft ODBC Driver 17 or 18 for SQL Server** | Free ~5 MB download from Microsoft. This is how Python talks to SQL Server. *(SSMS itself is NOT required to run the app — it is only a management GUI.)* |
| **Claude API key** | From <https://console.anthropic.com>. Needed for all AI features (insights, chat agent, deep scan). The dashboards work without it; the AI does not. |
| **Windows** | Developed & tested on Windows (PowerShell commands below). |

---

## 2. Fresh setup — you just downloaded this repo, now what?

Open **PowerShell** inside the project folder and run, step by step:

```powershell
# Step 1 — create an isolated Python environment
python -m venv venv
venv\Scripts\activate

# Step 2 — install all required packages
pip install -r requirements.txt
```

### Step 3 — create your .env file (the repo does NOT include one, on purpose)

Secrets are never committed to git, so you must create a file named exactly **`.env`**
in the project root (same folder as `app.py`). Copy this template and fill your values:

```
# ── Claude AI (get a key at console.anthropic.com) ──
ANTHROPIC_API_KEY=sk-ant-your-real-key-here
CLAUDE_MODEL=claude-opus-4-8

# ── SQL Server connection ──
DB_SERVER=localhost               # where SQL Server runs (see §3)
DB_NAME=YourDatabaseName          # the database that holds the 18 project tables
DB_TRUSTED_CONNECTION=yes         # yes = Windows login on THIS pc; no = username/password below
DB_USERNAME=
DB_PASSWORD=

# ── Optional: real e-mail sending (leave empty = e-mails are simulated) ──
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=noreply@pharma-agent.com
```

What each key means:

| Key | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude key. The placeholder `xyz` counts as "not set". |
| `CLAUDE_MODEL` | Which Claude model the AI uses (default is fine). |
| `DB_SERVER` | `localhost` if SQL Server runs on this PC; `192.168.x.x` or `HOSTNAME\SQLEXPRESS` if it runs on another machine. |
| `DB_NAME` | Name of the database containing the project tables. |
| `DB_TRUSTED_CONNECTION` | `yes` = log in as your Windows user (only works on the same PC/domain). `no` = use `DB_USERNAME` + `DB_PASSWORD` (needed when connecting across machines). |
| `SMTP_*` | Only if you want real e-mails; otherwise notifications are simulated and stored in the database. |

### Step 4 — test the database connection BEFORE running the app

```powershell
python test_connection.py
```

This checks each link in the chain: ODBC driver found → can connect → your tables are
visible → row counts → the app's own 6 agent tables get created. **Fix whatever step it
flags before moving on.**

### Step 5 — run the app

```powershell
streamlit run app.py
```

Your browser opens at `http://localhost:8501`. Done ✅

---

## 3. Database setup — where does the data come from?

The repo ships **no data**. Pick the option that matches your situation:

**Option A — SQL Server already runs on this PC with the data loaded**
(the original development machine). Keep `DB_SERVER=localhost`,
`DB_TRUSTED_CONNECTION=yes`. Nothing else to do.

**Option B — You got a database backup file (`.bak`) from the team**
1. Install free **SQL Server Express** from Microsoft (and optionally SSMS as a GUI).
2. Restore the backup: in SSMS → right-click *Databases* → *Restore Database…* → pick the
   `.bak` → OK. (Or via command line: `sqlcmd -S localhost -E -Q "RESTORE DATABASE YourDatabaseName FROM DISK='C:\path\to\backup.bak'"`.)
3. Set `DB_NAME` in `.env` to the restored name; keep `DB_SERVER=localhost`.

**Option C — The database lives on ANOTHER machine (team server / colleague's laptop)**
1. On the machine that HAS the database: enable **TCP/IP** (SQL Server Configuration
   Manager → Protocols → TCP/IP → Enable → restart SQL Server), start **SQL Server
   Browser**, allow firewall ports **TCP 1433 + UDP 1434**, and create a SQL login:
   ```sql
   CREATE LOGIN sigma_user WITH PASSWORD = 'YourStrongPassword!123';
   USE [YourDatabaseName];
   CREATE USER sigma_user FOR LOGIN sigma_user;
   ALTER ROLE db_datareader ADD MEMBER sigma_user;
   ALTER ROLE db_datawriter ADD MEMBER sigma_user;
   ALTER ROLE db_ddladmin  ADD MEMBER sigma_user;   -- app creates its Agent_* tables
   ```
   (Also enable "SQL Server and Windows Authentication mode" in server properties, then
   restart SQL Server.)
2. On YOUR machine: install the ODBC driver (§1) and set in `.env`:
   `DB_SERVER=<that machine's IP>`, `DB_TRUSTED_CONNECTION=no`,
   `DB_USERNAME=sigma_user`, `DB_PASSWORD=YourStrongPassword!123`.
3. Both machines must be on the same network/VPN, and the database machine must be ON
   whenever you run the app.

Whatever option you choose — **always run `python test_connection.py` first.**

---

## 4. Commands

| Command | What it does |
|---|---|
| `python test_connection.py` | Pre-flight database check — run this first, always. |
| `streamlit run app.py` | Starts the web app on port 8501. |
| `python scheduler.py --once` | One autonomous agent cycle now (Watchdog + Claude deep scan) — fills the approval queue, prints a report. |
| `python scheduler.py` | Same cycle automatically **every day at 08:00** (Ctrl+C to stop). |

---

## 5. The 8 sections

Every data page works like a **Power BI-style dashboard**: a 📅 **date-range slicer** at the
top re-filters every chart/number on the page, and **clicking a bar (or a state on a map)
cross-filters** every other chart in that section (✖ Clear resets). Every chart has a
plain-English caption; every KPI card has a one-line hover description.

1. **🏠 Executive Command Center — Seizure & Epilepsy Patients** — six KPI cards scoped to the
   seizure cohort only (Seizure Patients, Seizure-Med Sales, On Seizure Therapy, Diagnosed-Not-
   Treated, Possibly Undiagnosed, Denied Seizure-Med Claims) + a "How the numbers add up" line
   that reconciles the counts, seizure-medicine sales trend, patients by medicine category,
   drill-down worklists with one-click queued actions.
2. **🧬 Patient Journey Explorer** — one seizure patient's story as a square-grid timeline
   (visits, diagnoses, medicines, switches, side effects) with a month-level date slider.
   Patient ID only — no names exist anywhere.
3. **🔬 Diagnosis Intelligence** — time-to-treatment by state, top epilepsy diagnoses,
   undiagnosed suspects, diagnosed-but-untreated (state-wise charts + worklists).
4. **💊 Treatment & Adherence** — epilepsy treatment funnel, switch/discontinue reasons,
   old→new medicine Sankey, **AI Switch Analyst** (one-click win-back plays + plain-English Q&A).
5. **🏥 Market Access & Payer** — insurer scorecard, refusal rates & reasons, prior-auth wait
   by state, recoverable denied claims.
6. **👨‍⚕️ Physician & Geo Intelligence** — doctor performance, sales by specialty,
   **manufacturer loyalty mix** (whose medicines each doctor prescribes — blue = EISAI),
   doctor profile with "EISAI share", US choropleth heatmaps for sales & marketing spend.
7. **🤖 AI Strategy Agent** — chat: Claude writes and runs its own T-SQL live, answers
   seizure-patient-first with real numbers, proposes actions (approvals live in Action Center).
8. **⚡ Action Center** — pending patient & business actions (approve/reject), history,
   appointments, notifications, audit log. **All timestamps stored & shown in UTC.**

### Agent autonomy (sidebar toggles — they stay as you set them)
- **Autonomous Mode** (default ON): the Watchdog auto-executes **low-risk** actions
  (outreach, claim escalation, appointments) as "Autopilot". **High-risk** actions
  (tests, prescriptions, referrals, budget moves) always wait for your approval.
- **Auto-generate AI insights** (default OFF): ON = every page generates its Claude insight
  on first visit (uses API credits); OFF = click **Generate** per page.

---

## 6. Inputs & outputs

### Data the app READS (never modifies)
18 SSMS tables incl. `[PTNT DIM]`, `[DRUG DIM]`, `[DX DIM]`, `[PHSY DIM]`, `[PLAN]`,
`[PX DIM]`, `[DX CLM]`, `[RX CLM]`, `[PX  CLM]` *(two spaces!)*, `[PTNT ACT]`, `[PTNT_MPD]`,
`[NEW_PTNT]`, `Fact_Sx`, `Fact_Drug_Switches`, `Fact_Insurance_Claims`, `Fact_Marketing`.
Revenue = `PLAN_PAY + PATIENT_PAY` on `[RX CLM]`, anti-seizure medicines only.
Seizure cohort = patients with an epilepsy (G40.\*) or seizure-symptom (R56.\*) diagnosis,
or an anti-seizure prescription (BB_USC_CODE 72110/72120/72130/72140).

### Data the app WRITES (only its own 6 tables, auto-created)
`Agent_Pending_Actions`, `Agent_Business_Actions`, `Agent_Appointments`,
`Agent_Notifications`, `Agent_Audit_Log`, `Agent_Chat_History` — all rows UTC-timestamped.
The agent's SQL tool is SELECT-only (write/DDL statements are blocked).

### What you interact with at runtime
Sidebar navigation + autonomy toggles + "Refresh Data from SSMS" · per-section date
slicers · chart clicks (cross-filter) · dropdown filters (insurer / specialty / state /
patient) · ✨ Generate-insight buttons · Approve / Queue / Reject buttons · free-text
questions to the AI Strategy Agent and AI Switch Analyst.

---

## 7. Privacy by design

- Seizure patients are identified **only by Patient ID** — no name columns exist in the data.
- Notifications go to de-identified aliases (`patient_<id>@notify.pharma`) unless SMTP is set.
- Physician names *are* used (legitimate commercial targeting).
- The agent's SQL layer rejects write statements and caps results at 200 rows.

---

## 8. Limitations

- **SQL Server only** — no local/offline mode; if the database is unreachable the app stops
  at startup with a clear error.
- **AI features need a Claude API key** and consume API credits per insight/chat/scan.
- The dataset ends in 2025 — "active/adherent" anchors to the latest fill date (or the end of
  your selected date window), not to today's date.
- E-mails are **simulated** unless SMTP is configured (recorded in `Agent_Notifications`).
- Chart cross-filter supports **one active dimension per section** at a time (by design).
- Insight panels & opportunity cards live in the browser session — refreshing regenerates or
  clears them (approved/queued items persist in the database).
- The chat agent is capped at 12 tool-use turns per question — ask narrower questions if it
  hits the limit.
- Rows written before the UTC change keep their original (local-time) timestamps.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `Database not reachable` at startup | Run `python test_connection.py`. Check `.env` server/database name, SQL Server running, TCP/IP enabled, firewall, credentials (§3). |
| `pyodbc` / driver errors | Install **ODBC Driver 18 (or 17) for SQL Server** from Microsoft; the test script's step [1] lists detected drivers. |
| *"server not found / named pipes error"* when DB is on another machine | Other machine off/asleep, different network, TCP/IP not enabled, or firewall — try `ping <that IP>` first (§3 Option C). |
| *"Login failed for user"* | Mixed-mode authentication not enabled (or SQL Server not restarted after enabling); wrong username/password. |
| `AttributeError: module 'backend' has no attribute ...` after pulling new code | Stale module in the running server — the app **self-heals on browser refresh**. If it persists: stop Streamlit, delete `__pycache__`, restart. |
| 🟠 *Claude API key not set* in sidebar | Put a real key in `.env` (`ANTHROPIC_API_KEY=sk-ant-...`). Placeholders like `xyz` are treated as unset. |
| AI errors: invalid key / rate limited / network | Shown inline — check the key, wait and retry, or check connectivity/proxy. |
| Port 8501 busy | `streamlit run app.py --server.port 8502` |
| Charts empty after narrowing the date slicer | No rows in that window — widen the range or hit ✖ Clear on the cross-filter chip. |
| Queued action "missing" | Check the right Action Center tab — patient vs **🏢 Business Actions**. With Autonomous Mode ON, low-risk items auto-execute → see **📜 History**. |
| Websocket reconnect errors | `uvicorn` is pinned `<0.44` in `requirements.txt` on purpose — don't upgrade it. |
| New SSMS rows not showing | Sidebar → **🔄 Refresh Data from SSMS**. |

---

## 10. Project structure

```
├── app.py                    # Streamlit UI — 8 pages, dashboard slicers, cross-filters, AI panels
├── agent.py                  # Claude tool-use agent (writes/runs its own T-SQL, proposes actions)
├── ai_engine.py              # Claude client + structured page-insight generation (seizure-first prompts)
├── actions.py                # Action queue + executors + notifications + audit (human-approved)
├── agent_watchdog.py         # Always-on reflex layer (safety / denials / untreated) + Autopilot
├── backend.py                # All analytics SQL (seizure-cohort scoped, date-filterable, ID-only)
├── db.py                     # SQL Server access layer + agent tables (UTC timestamps)
├── config.py                 # .env loader — nothing hard-coded
├── scheduler.py              # Daily 08:00 autonomous cycle (or --once)
├── test_connection.py        # Pre-flight database check — run this first
├── requirements.txt          # streamlit, pandas, plotly, anthropic, pyodbc, schedule, ...
├── assets/logo.jpg           # Branding asset
└── .env                      # YOU create this — API key, DB, SMTP (never committed; see §2 Step 3)
```
