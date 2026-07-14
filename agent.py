"""Conversational, tool-using Pharma Strategy Agent (Claude).

This is a real agent, not a chatbot:
  • It writes and runs its own SQL against the live database (SQL Server or SQLite).
  • It can PROPOSE concrete actions (schedule appointment, send notification,
    order diagnostic test, change prescription, escalate claim, enroll support
    program) for specific patient IDs.
  • Proposals go into a human-approval queue — nothing executes until a human
    clicks Approve (in chat or in the Action Center).

Privacy: patients are identified ONLY by patient_id.
"""
import json
import anthropic
import config
import db
import actions
import ai_engine


def _schema_text():
    return """Database dialect: T-SQL (Microsoft SQL Server). Real table names contain SPACES,
so you MUST wrap every table name in square brackets, e.g. [DRUG DIM], [DX CLM], [PX  CLM]
(note: PX CLM has TWO spaces), [PRC ROLES], [PTNT ACT], [PTNT DIM], [PTNT_MPD], [RX CLM].

DIMENSION TABLES
1. [PTNT DIM] (PATIENT_ID, PATIENT_BIRTH_YEAR, PATIENT_GENDER [1=Male,2=Female], PATIENT_ZIP3, PATIENT_STATE)
   -- age = 2025 - PATIENT_BIRTH_YEAR. No patient names exist; identify patients by PATIENT_ID.
2. [DRUG DIM] (DRUG_ID, NDC11, DRUG_NAME, BGI ['B'=brand,'G'=generic], BB_USC_CODE, BB_USC_NAME [therapeutic class],
   DRUG_GENERIC_NAME, DRUG_STRENGTH, DRUG_FORM, PACKAGE_SIZE, MANUFACTURER, NDC_START_DATE)
   -- OUR COMPANY = 'EISAI' (MANUFACTURER='EISAI'); hero brands: BANZEL, FYCOMPA.
   -- Anti-seizure medicine (ASM) classes: BB_USC_CODE IN (72110 first-line, 72120 LGS/specialty,
      72130 rescue, 72140 adjunct/refractory). Competitors: EPIDIOLEX (Jazz), ONFI (Lundbeck),
      XCOPRI (SK), and MANUFACTURER='OTHERS' brands (KEPPRA, VIMPAT, BRIVIACT, FINTEPLA, NAYZILAM).
3. [DX DIM] (DIAGNOSIS_CODE, DIAGNOSIS_DESCRIPTION, ICD_VER_NBR) -- ICD-10 code lookup.
4. [PHSY DIM] (PRACTITIONER_ID, PHYSICIAN_ID, LAST_NAME, FIRST_NAME, CITY, STATE, ZIP,
   SPECIALTY_CODE, SPECIALTY_DESCRIPTION, NPI) -- physician names ARE allowed (used for targeting).
5. [PLAN] (PLAN_ID, PLAN_NAME, PLAN_TYPE, PAYMENT_TYPE, NATIONAL_INSURER_NAME, ADMIN_NAME [PBM]).
6. [PX DIM] (PROCEDURE_CODE, PROCEDURE_DESCRIPTION) -- CPT/HCPCS lookup.
7. [PRC ROLES] (PRACTITIONER_ROLE_CODE, PRACTITIONER_ROLE_DESC).

CLAIMS / FACT TABLES
8. [DX CLM] (PATIENT_ID, CLAIM_ID, CLAIM_TYPE, SERVICE_DATE, DIAGNOSIS_CODE, PRACTITIONER_ID, DELETION_FLAG)
   -- diagnosis events. Join DIAGNOSIS_CODE -> [DX DIM].
9. [RX CLM] (CLAIM_ID, PATIENT_ID, DRUG_ID, PRACTITIONER_ID, PLAN_ID, PATIENT_PAY, PLAN_PAY, REFILL_CODE,
   QUANTITY, DAYS_SUPPLY, RX_FILL_DATE, DAW, DELETION_FLAG)
   -- prescription fills. REVENUE / drug spend = (PLAN_PAY + PATIENT_PAY). Join DRUG_ID -> [DRUG DIM].
10. [PX  CLM] (PATIENT_ID, CLAIM_ID, PROCEDURE_CODE, PROCEDURE_DATE, PLACE_OF_SERVICE, PRACTITIONER_ID,
    UNITS_ADMINISTERED, CHARGE_AMOUNT, DRUG_ID, DELETION_FLAG) -- procedures/visits (TWO spaces in name).
11. [PTNT ACT] (PATIENT_ID, ACTIVITY_YEAR, ACTIVITY_QUARTER, RX_ACTIVITY_IND, MX_ACTIVITY_IND) -- activity flags.
12. [PTNT_MPD] (PATIENT_ID, MPD_YEAR, MPD_CLASS, INDEX_DATE, OUT_OF_POCKET, DRUG_SPEND, DRUG_SPEND_BRAND,
    CLAIM_COUNT, GAP_DATE, CATASTROPHIC_DATE ...) -- Medicare Part D benefit phases + spend.
13. [NEW_PTNT] (PATIENT_ID) -- newly captured / new-to-therapy patients.
14. Fact_Sx (event_id, patient_id, drug_id, side_effect_name, severity ['Low','Medium','High'], report_date)
15. Fact_Drug_Switches (switch_id, patient_id, from_drug_id, to_drug_id, switch_date,
    switch_reason ['Cost','Side Effect','Effectiveness','Insurance'])
16. Fact_Insurance_Claims (claim_id, patient_id, drug_id, payer_name, region, claim_amount, approved_amount,
    status ['Approved','Denied','Pending'], denial_reason, prior_auth_days, claim_date)
17. Fact_Marketing (campaign_id, region, drug_id, spend_amount, campaign_start, campaign_end)

AGENT OPERATIONAL TABLES (the app's own workflow tables)
18. Agent_Appointments, Agent_Notifications, Agent_Pending_Actions, Agent_Business_Actions, Agent_Audit_Log.
19. Agent_Chat_History (chat_id, role ['user'|'assistant'], content, created_at) — every past chat turn
    with you is stored here. If the user asks about earlier conversations ("what did we discuss...",
    "show my chat history", "what did you tell me yesterday"), query this table with query_database.

COHORT = EPILEPSY (incl. Lennox-Gastaut Syndrome). Contexts by seizure duration, encoded in
the diagnosis code (the description carries a plain tag like [STATUS EPILEPTICUS 5+ MIN]):
- EPILEPTIC (<2 min):  G40.909, G40.109, G40.209, G40.A09, G40.812
- PROLONGED (2-5 min): G40.919, G40.119, G40.219, G40.814
- STATUS EPILEPTICUS (5+ min): G40.901, G40.911, G40.111, G40.811, G40.813
Sub-contexts = age bands from PATIENT_BIRTH_YEAR (age = 2025 - yob): 0-1, 2-11, 12-17, 18+.

KEY DEFINITIONS (use these exactly):
- Total revenue / sales = SUM(PLAN_PAY + PATIENT_PAY) from [RX CLM].
- Confirmed epilepsy = any G40.* code above. "Untreated" epilepsy patient = has a G40 code in
  [DX CLM] but no [RX CLM] fill for a drug with BB_USC_CODE IN (72110,72120,72130,72140).
- "Undiagnosed suspect" = patient whose [DX CLM] rows are ONLY symptom codes
  ('R56.9','R56.00','R56.01') with no confirmed G40 diagnosis.
- "Active on therapy" = has an [RX CLM] fill within 120 days of the latest RX_FILL_DATE in the data.
- Use CAST(PATIENT_ID AS BIGINT) when returning patient IDs. Filter DELETION_FLAG = 0 on claim tables.
- Use TOP N (not LIMIT) for T-SQL. Keep results under 200 rows via aggregation."""


TOOLS = [
    {
        "name": "query_database",
        "description": (
            "Run a read-only T-SQL SELECT against the live SQL Server database and get the rows back. "
            "Use this for every factual question — never answer from memory. Table names have spaces so "
            "bracket them: [RX CLM], [DX CLM], [DRUG DIM]. Identify patients by PATIENT_ID (no patient "
            "names exist). Use TOP N and aggregation to keep results under 200 rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "propose_action",
        "description": (
            "Queue a real-world action for a specific patient. It goes to a HUMAN APPROVAL queue — "
            "it does not execute immediately. Call this whenever your analysis finds patients who need "
            "intervention (untreated, undiagnosed, discontinued, high-severity side effect, denied claim). "
            "One call per patient. After proposing, tell the user the actions are awaiting their approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["schedule_appointment", "send_notification", "order_diagnostic_test",
                             "change_prescription", "escalate_claim", "enroll_support_program",
                             "refer_to_specialist"],
                },
                "patient_id": {"type": "integer"},
                "details": {"type": "string", "description": "Exactly what should happen"},
                "reason": {"type": "string", "description": "Data-grounded justification"},
                "expected_impact": {"type": "string", "description": "Business/clinical impact"},
                "estimated_value_usd": {"type": "number",
                                        "description": "$ value this action captures/protects, from the data (0 if unquantifiable)"},
            },
            "required": ["action_type", "patient_id", "details", "reason", "expected_impact",
                         "estimated_value_usd"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "propose_business_action",
        "description": (
            "Queue a company-level STRATEGIC action (not tied to a single patient) for human approval — "
            "marketing budget reallocation, field-rep deployment, physician education, payer negotiation, "
            "screening campaigns, center partnerships. Use this when your analysis points at a region, "
            "payer, clinic, drug or specialty rather than individual patients (e.g. marketing/sales "
            "findings, systematic denial patterns, diagnosis deserts)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["reallocate_marketing_budget", "deploy_field_reps",
                             "launch_physician_education", "initiate_payer_negotiation",
                             "launch_screening_campaign", "partner_with_center"],
                },
                "target_type": {"type": "string", "enum": ["region", "payer", "clinic", "drug", "specialty"]},
                "target": {"type": "string", "description": "e.g. 'OH', 'HealthPlus HMO', 'Cleveland Neuro Associates'"},
                "details": {"type": "string", "description": "Exactly what should happen"},
                "reason": {"type": "string", "description": "Data-grounded justification"},
                "expected_impact": {"type": "string", "description": "Expected business impact ($ where possible)"},
                "estimated_value_usd": {"type": "number",
                                        "description": "$ value this action captures/protects, from the data (0 if unquantifiable)"},
            },
            "required": ["action_type", "target_type", "target", "details", "reason",
                         "expected_impact", "estimated_value_usd"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

_FORBIDDEN_SQL = ("insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "exec", "merge")


def _run_readonly_sql(sql):
    lowered = sql.strip().lower()
    if not lowered.startswith(("select", "with")):
        return None, "Only SELECT queries are allowed."
    if any(f" {kw} " in f" {lowered} " for kw in _FORBIDDEN_SQL):
        return None, "Query rejected: write/DDL keywords are not allowed."
    try:
        df = db.run_query(sql)
        if len(df) > 200:
            return df.head(200).to_string() + f"\n... ({len(df)} rows total, truncated to 200)", None
        return df.to_string() if not df.empty else "(no rows)", None
    except Exception as e:
        return None, str(e)


def _execute_tool(name, tool_input):
    if name == "query_database":
        result, err = _run_readonly_sql(tool_input["sql"])
        return (f"SQL ERROR: {err}" if err else result), bool(err)
    if name == "propose_action":
        msg = actions.propose_action(
            tool_input["action_type"], tool_input["patient_id"], tool_input["details"],
            tool_input["reason"], tool_input.get("expected_impact", ""),
            estimated_value_usd=tool_input.get("estimated_value_usd", 0),
        )
        return msg, False
    if name == "propose_business_action":
        msg = actions.propose_business_action(
            tool_input["action_type"], tool_input["target_type"], tool_input["target"],
            tool_input["details"], tool_input["reason"], tool_input.get("expected_impact", ""),
            estimated_value_usd=tool_input.get("estimated_value_usd", 0),
        )
        return msg, False
    return f"Unknown tool {name}", True


_AGENT_SYSTEM = ai_engine._SYSTEM + """

You have live database access via the query_database tool and can queue real interventions via
propose_action (patient-level) and propose_business_action (company-level strategy: marketing,
field force, payer negotiation, screening campaigns, partnerships). Everything is human-approved
before execution.

Working style:
1. For any question, first query the database — never invent numbers.
2. Present findings as a crisp executive answer: key numbers, what they mean for the business,
   and what to do next.
3. DECISION OPTIONS: for every significant finding, present 2-3 genuinely different courses of
   action (e.g. for undiagnosed suspects: A) confirmatory tests, B) specialist referrals,
   C) physician-education campaign in the hotspot region) with the trade-offs, then ask which the
   user prefers — or propose the strongest one if they asked you to just act.
4. Marketing/sales/payer findings deserve BUSINESS actions (propose_business_action), not patient
   outreach. Patient-level urgency (untreated, undiagnosed, severe side effects, drop-offs, denied
   claims) deserves propose_action for the ~5 most urgent patient_ids.
5. After proposing, tell the user the proposals are waiting in the approval queue. Never claim an
   action was executed unless a tool result says so.
6. Show the SQL you ran at the end of your answer in a ```sql block for transparency.
7. ANSWER STYLE — keep it simple and short. Your reader is a business person, not a doctor or
   insurance expert. Plain everyday English, short bullet points, only the numbers that matter.
   Aim for under ~150 words before the SQL block unless the user asks for detail. Never dump a
   wall of analysis. Always pair any diagnosis code with its plain-English meaning (join [DX DIM]
   for DIAGNOSIS_DESCRIPTION), e.g. "M06.9 (rheumatoid arthritis)"."""


class PharmaStrategyAgent:
    MAX_TURNS = 12

    def handle_query(self, user_query, history=None):
        """Runs the full agentic loop. `history` = prior [{'role','content'}] plain-text turns.
        Returns (answer_markdown, proposed_action_count)."""
        try:
            ai_engine.require_api_key()
        except RuntimeError as e:
            return f"⚠️ **{e}**", 0

        messages = list(history or []) + [{"role": "user", "content": user_query}]
        system = [
            {"type": "text", "text": _AGENT_SYSTEM},
            {"type": "text", "text": _schema_text()},
        ]
        # learning loop: remember what the business rejected recently
        try:
            rejections = actions.get_recent_rejections()
            if rejections:
                system.append({"type": "text", "text":
                               "The business REJECTED these proposals in the last 30 days — don't "
                               "re-propose them; offer different approaches instead:\n" + "\n".join(rejections)})
        except Exception:
            pass
        proposed = 0
        try:
            for _ in range(self.MAX_TURNS):
                response = ai_engine.client().messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                )
                if response.stop_reason == "refusal":
                    return "⚠️ The model declined this request.", proposed

                if response.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": response.content})
                    continue

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    answer = "\n".join(b.text for b in response.content if b.type == "text")
                    return answer or "(no answer)", proposed

                messages.append({"role": "assistant", "content": response.content})
                results = []
                for tu in tool_uses:
                    output, is_error = _execute_tool(tu.name, tu.input)
                    if tu.name in ("propose_action", "propose_business_action") and not is_error and "queued" in output:
                        proposed += 1
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": str(output),
                        "is_error": is_error,
                    })
                messages.append({"role": "user", "content": results})

            return "⚠️ Agent reached its step limit before finishing. Try a narrower question.", proposed
        except anthropic.AuthenticationError:
            return "⚠️ **Invalid Claude API key.** Check ANTHROPIC_API_KEY in `.env`.", proposed
        except anthropic.APIConnectionError:
            return "⚠️ **Network error** reaching the Claude API. Check your connection/proxy.", proposed
        except anthropic.RateLimitError:
            return "⚠️ **Rate limited** by the Claude API — wait a moment and retry.", proposed
        except anthropic.APIStatusError as e:
            return f"⚠️ **Claude API error {e.status_code}:** {e.message}", proposed
        except Exception as e:
            return f"⚠️ **Agent error:** {e}", proposed


def run_autopilot_scan():
    """Autonomous scan: the agent inspects the whole database for risk & opportunity
    and fills the approval queue. Returns its summary report."""
    agent = PharmaStrategyAgent()
    report, proposed = agent.handle_query(
        "Run a full autonomous risk & opportunity scan of the live database. Query each category, "
        "then act:\n"
        "1) RA patients diagnosed ([DX CLM] code IN ('M05.79','M06.9','M06.00')) but UNTREATED "
        "(no [RX CLM] for a drug with BB_USC_CODE=68200).\n"
        "2) Undiagnosed suspects (patients whose [DX CLM] rows are only symptom codes "
        "'R56.9','R56.00','R56.01').\n"
        "3) Patients with HIGH severity side effects (Fact_Sx severity='High').\n"
        "4) Patients who switched AWAY from one of OUR EISAI drugs (Fact_Drug_Switches where from_drug_id "
        "is a drug with MANUFACTURER='EISAI') — win-back targets.\n"
        "5) Denied insurance claims (Fact_Insurance_Claims status='Denied'), highest claim_amount first.\n"
        "For each category report counts + the most urgent PATIENT_IDs, and propose the right patient "
        "action for the top 3 per category. Where a pattern is regional/payer/clinic-level (denial-heavy "
        "payer, diagnosis-desert state, weak marketing ROI), ALSO call propose_business_action. Finish "
        "with a short executive summary of total revenue at stake and expected recovery if approved."
    )
    return report, proposed


agent = PharmaStrategyAgent()
