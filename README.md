# SIGMA — Seizure Intelligence & Growth Management Agent

**Intelligent Patient Journey Analyser · Agentic Pharma Strategy Platform**

Not just a dashboard — an **AI agent** (powered by **Claude**) that reads your live epilepsy
patient-journey database (SQL Server / SSMS), explains *what the numbers mean* in plain
business English, proposes *what to do next*, and — once a human approves — **actually does
it** (schedules appointments, sends notifications, orders diagnostic tests, flags prescription
changes, escalates denied insurance claims, queues marketing/payer strategy moves).

**Business identity:** OUR company is **EISAI** (hero brands **BANZEL** and **FYCOMPA**).
Competitors include Epidiolex (Jazz), Onfi (Lundbeck), Xcopri (SK Life Science), and the
branded/generic makers grouped under **OTHERS** (incl. Keppra, Vimpat, Briviact, Fintepla,
Nayzilam). The cohort is US **epilepsy** patients (incl. Lennox-Gastaut Syndrome).

---

## 1. Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.11+ (project venv was built with 3.14) |
| **SQL Server + SSMS** | A running SQL Server instance holding the 18 project tables (see §5). The app is **SSMS-only** — there is no SQLite/dummy fallback. |
| **ODBC driver** | *ODBC Driver 17 or 18 for SQL Server* (the app tries 18 → 17 → legacy "SQL Server" automatically) |
| **Claude API key** | From <https://console.anthropic.com> — required for all AI features (insight panels, chat agent, deep scan). The dashboards work without it; the AI features don't. |
| **OS** | Developed and tested on Windows (PowerShell commands below) |

---

## 2. Setup steps

```powershell
# 1. Clone / unzip the project, then create the environment
cd Intelligent_Patient_Jouney_Analyser-main
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure .env (see table below)
notepad .env

# 3. FIRST: verify the database connection
python test_connection.py

# 4. Run the app
streamlit run app.py
# opens http://localhost:8501
```

### .env configuration (all inputs the app reads at startup)

| Key | Required | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | for AI features | Your Claude API key (placeholder `xyz` = "not set") |
| `CLAUDE_MODEL` | no | Model for all AI calls (default `claude-opus-4-8`) |
| `DB_SERVER` | **yes** | SQL Server instance, e.g. `localhost` or `LAPTOP-123\SQLEXPRESS` |
| `DB_NAME` | **yes** | Database containing the project tables |
| `DB_TRUSTED_CONNECTION` | yes/no | `yes` = Windows auth; `no` = use the two keys below |
| `DB_USERNAME` / `DB_PASSWORD` | if not trusted | SQL login credentials |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | no | Real email delivery for notifications; if unset, notifications are **simulated** and logged to the DB |

---

## 3. Commands

| Command | What it does |
|---|---|
| `python test_connection.py` | Pre-flight check: ODBC driver → connection → lists tables → row counts → creates the 6 agent tables. Run this before anything else. |
| `streamlit run app.py` | Starts the web app on port 8501. |
| `python scheduler.py --once` | One autonomous agent cycle now: Watchdog sweep (auto-executes low-risk actions) + Claude deep scan (fills the approval queue). Prints a report to the console. |
| `python scheduler.py` | Runs that cycle **every day at 08:00** until stopped (Ctrl+C). |

---

## 4. The 8 sections (pages)

Every data page works like a **Power BI-style dashboard**: a 📅 **date-range slicer** at the top
re-filters every chart/number on the page, and **clicking a bar (or a state on a map)
cross-filters** every other chart in that section (✖ Clear resets). Each chart has a
plain-English caption.

1. **🏠 Executive Command Center** — KPI cards (patients, sales, untreated, undiagnosed, denied
   claims), sales trend, patients by medicine category, drill-down expanders with one-click
   queued actions.
2. **🧬 Patient Journey Explorer** — one patient's story as a square-grid timeline (visits,
   diagnoses, medicines, switches, side effects) with its own month-level date slider.
   Patient ID only — no names exist anywhere.
3. **🔬 Diagnosis Intelligence** — time-to-treatment by state, top diagnoses, undiagnosed
   suspects, diagnosed-but-untreated (state-wise charts + tables).
4. **💊 Treatment & Adherence** — epilepsy treatment funnel, switch/discontinue reasons,
   old→new medicine Sankey, **AI Switch Analyst** (one-click win-back plays + plain-English Q&A).
5. **🏥 Market Access & Payer** — insurer scorecard, refusal rates & reasons, prior-auth wait
   by state, recoverable denied claims.
6. **👨‍⚕️ Physician & Geo Intelligence** — doctor performance, sales by specialty,
   **manufacturer loyalty mix** (whose medicines each doctor prescribes — blue = EISAI),
   doctor profile with "EISAI share", US choropleth heatmaps for sales & marketing spend.
7. **🤖 AI Strategy Agent** — chat: Claude writes and runs its own T-SQL live, answers with
   real numbers, proposes actions (chat only; approvals live in the Action Center).
8. **⚡ Action Center** — pending patient & business actions (approve/reject), history,
   appointments, notifications, audit log. **All timestamps are stored & shown in UTC.**

### Agent autonomy (sidebar toggles)
- **Autonomous Mode** (default ON): the Watchdog auto-executes **low-risk** actions
  (outreach, claim escalation, appointments) as "Autopilot". **High-risk** actions
  (tests, prescriptions, referrals, budget moves) always wait for human approval.
- **Auto-generate AI insights** (default OFF): ON = every page generates its Claude insight
  on first visit (uses API credits); OFF = click **Generate** per page.

---

## 5. Inputs & outputs

### Data inputs (read-only, from SSMS)
18 tables incl. `[PTNT DIM]`, `[DRUG DIM]`, `[DX DIM]`, `[PHSY DIM]`, `[PLAN]`, `[PX DIM]`,
`[DX CLM]`, `[RX CLM]`, `[PX  CLM]` *(two spaces)*, `[PTNT ACT]`, `[PTNT_MPD]`, `[NEW_PTNT]`,
`Fact_Sx`, `Fact_Drug_Switches`, `Fact_Insurance_Claims`, `Fact_Marketing`.
Revenue = `PLAN_PAY + PATIENT_PAY` on `[RX CLM]`. Table names contain spaces → always
`[bracketed]` in SQL.

### Outputs (written by the app — only to its own 6 agent tables)
`Agent_Pending_Actions`, `Agent_Business_Actions`, `Agent_Appointments`,
`Agent_Notifications`, `Agent_Audit_Log`, `Agent_Chat_History` — created automatically at
startup. The app **never writes to your source claims/dimension tables** (the agent's SQL tool
is SELECT-only and blocks INSERT/UPDATE/DELETE/DDL). All rows are timestamped in **UTC**.

### Expected runtime inputs (what you interact with)
- Sidebar: page navigation, the two autonomy toggles, **Refresh Data from SSMS**.
- Per page: date-range slicers, chart clicks (cross-filter), dropdown filters
  (insurer / specialty / state / patient), ✨ Generate insight buttons.
- Approvals: **Approve & Execute** / **Queue for review** on opportunity cards;
  **Approve / Reject** in the Action Center.
- Free-text questions to the AI Strategy Agent and the AI Switch Analyst.

---

## 6. Privacy by design

- Patients are identified **only by Patient ID** — no name columns exist in the data.
- Notifications go to de-identified aliases (`patient_<id>@notify.pharma`) unless SMTP is set.
- Physician names *are* used (legitimate commercial targeting).
- The agent's SQL layer rejects write statements and returns max 200 rows per query.

---

## 7. Limitations

- **SSMS only** — no local/offline mode; if SQL Server is down the app stops at startup.
- **AI features need a Claude API key** and consume API credits per insight/chat/scan.
- The dataset ends in 2025 — "active/adherent" logic anchors to the latest fill date (or the
  end of your selected date window), not to today's date.
- Emails are **simulated** unless SMTP is configured (recorded in `Agent_Notifications`).
- Chart cross-filter supports **one active dimension per section** at a time (by design,
  keeps the story readable).
- Insight panels & their opportunity cards live in the browser session — refreshing the tab
  regenerates or clears them (approved/queued items persist in the DB).
- The agent chat is capped at 12 tool-use turns per question; very broad questions can hit
  the limit — ask narrower questions.
- Historical rows written before the UTC change keep their original (local-time) timestamps.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Database not reachable` at startup | Run `python test_connection.py`. Check `.env` server/database, SQL Server running, TCP/IP enabled (SQL Server Configuration Manager), firewall, credentials. |
| `pyodbc` install/driver errors | Install **ODBC Driver 18 (or 17) for SQL Server** from Microsoft; re-run the test script — step [1] lists detected drivers. |
| `AttributeError: module 'backend' has no attribute ...` after pulling new code | The running server holds a stale module. The app **self-heals on page refresh** (guard at the top of `app.py`). If it persists: stop Streamlit, `Remove-Item -Recurse __pycache__`, restart. |
| 🟠 *Claude API key not set* in sidebar | Put a real key in `.env` (`ANTHROPIC_API_KEY=sk-ant-...`) and restart. Placeholders `xyz`/`changeme` are treated as unset. |
| AI errors: *Invalid API key / rate limited / network* | Shown inline by the agent — check the key, wait and retry, or check proxy/connectivity. |
| Port 8501 busy | `streamlit run app.py --server.port 8502` |
| Charts empty after narrowing the date slicer | No rows in that window — widen the range or hit ✖ Clear on the cross-filter chip. |
| Queued action "missing" | Check the correct tab in ⚡ Action Center: patient actions vs **🏢 Business Actions**. With Autonomous Mode ON, low-risk items are auto-executed → see **📜 History**. |
| Websocket reconnect errors | `uvicorn` is pinned `<0.44` in `requirements.txt` on purpose — don't upgrade it. |
| Refreshed data not showing | Sidebar → **🔄 Refresh Data from SSMS** (clears cached insights and re-runs the Watchdog). |

---

## 9. Project structure

```
├── app.py                    # Streamlit UI — 8 pages, dashboard slicers, cross-filters, AI panels
├── agent.py                  # Claude tool-use agent (writes/runs its own T-SQL, proposes actions)
├── ai_engine.py              # Claude client + structured page-insight generation (JSON schema)
├── actions.py                # Action queue + executors + notifications + audit (human-approved)
├── agent_watchdog.py         # Always-on reflex layer (safety / denials / untreated) + Autopilot
├── backend.py                # All analytics SQL (date-filterable, patient-ID only)
├── db.py                     # SQL Server access layer + agent tables (UTC timestamps)
├── config.py                 # .env loader — nothing hard-coded
├── scheduler.py              # Daily 08:00 autonomous cycle (or --once)
├── test_connection.py        # Pre-flight SSMS connectivity check — run this first
├── requirements.txt          # streamlit, pandas, plotly, anthropic, pyodbc, schedule, ...
├── assets/logo.jpg           # Branding asset
└── .env                      # API key, DB, SMTP settings (never commit real secrets)
```
