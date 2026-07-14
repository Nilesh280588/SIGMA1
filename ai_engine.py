"""Claude AI engine.

Two capabilities, both fully model-generated (no hard-coded insights anywhere):

1. generate_page_insight()  — every dashboard page sends its live data to Claude
   and gets back an executive insight + risks + concrete recommended actions
   (with target patient IDs) as validated JSON. Recommended actions can be
   queued for approval with one click.

2. Used by agent.py for the conversational, tool-using strategy agent.
"""
import json
import anthropic
import config

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def require_api_key():
    if not config.api_key_is_set():
        raise RuntimeError(
            "Claude API key is not set. Open `.env` and replace ANTHROPIC_API_KEY=xyz with your real key."
        )


PATIENT_ACTIONS = ["schedule_appointment", "send_notification", "order_diagnostic_test",
                   "change_prescription", "escalate_claim", "enroll_support_program",
                   "refer_to_specialist"]
BUSINESS_ACTIONS = ["reallocate_marketing_budget", "deploy_field_reps", "launch_physician_education",
                    "initiate_payer_negotiation", "launch_screening_campaign", "partner_with_center"]

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": PATIENT_ACTIONS + BUSINESS_ACTIONS},
        "patient_ids": {"type": "array", "items": {"type": "integer"},
                        "description": "Target patient IDs for patient-level actions (max 5, most urgent "
                                       "first). MUST be empty [] for business-level actions."},
        "target_type": {"type": "string",
                        "description": "For business actions: region|payer|clinic|drug|specialty. "
                                       "Empty string for patient actions."},
        "target": {"type": "string",
                   "description": "For business actions: the concrete target, e.g. 'OH', 'HealthPlus HMO', "
                                  "'Cleveland Neuro Associates'. Empty string for patient actions."},
        "details": {"type": "string", "description": "Exactly what should happen in this step"},
    },
    "required": ["action_type", "patient_ids", "target_type", "target", "details"],
    "additionalProperties": False,
}

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One-line executive takeaway"},
        "insights": {
            "type": "array", "items": {"type": "string"},
            "description": "3-5 specific, number-backed observations from the data",
        },
        "risks": {
            "type": "array", "items": {"type": "string"},
            "description": "1-3 business/clinical risks visible in the data",
        },
        "business_opportunity": {
            "type": "string",
            "description": "Where the pharma company can gain revenue or reduce leakage, with a $ estimate if possible",
        },
        "opportunities": {
            "type": "array",
            "description": "1-4 opportunity cards. Each = one concrete finding + 2-3 ALTERNATIVE ways to act "
                           "on it, so the business can choose.",
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string",
                                "description": "The specific, number-backed finding this card addresses"},
                    "value_at_stake": {"type": "string",
                                       "description": "Revenue/risk at stake, $ estimate where possible"},
                    "options": {
                        "type": "array",
                        "description": "2-3 genuinely different alternatives (not variations of the same "
                                       "action). Mix patient-level and business-level where sensible. An "
                                       "option may be a multi-step playbook (steps run in order).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string",
                                          "description": "Short name, e.g. 'Fast confirmatory testing'"},
                                "approach": {"type": "string",
                                             "description": "patient_outreach | clinical_pathway | "
                                                            "commercial | market_access | marketing"},
                                "effort": {"type": "string", "enum": ["Low", "Medium", "High"]},
                                "cost": {"type": "string", "enum": ["Low", "Medium", "High"]},
                                "timeframe": {"type": "string", "description": "e.g. '1-2 weeks'"},
                                "confidence": {"type": "string", "enum": ["Low", "Medium", "High"]},
                                "reason": {"type": "string", "description": "Why choose THIS option; trade-off vs the others"},
                                "expected_impact": {"type": "string"},
                                "estimated_value_usd": {
                                    "type": "number",
                                    "description": "Best numeric estimate of the $ value this option captures "
                                                   "or protects, derived from the data (0 if unquantifiable)"},
                                "steps": {"type": "array", "items": _STEP_SCHEMA,
                                          "description": "1 step for a simple action; 2-4 ordered steps for a playbook"},
                            },
                            "required": ["label", "approach", "effort", "cost", "timeframe",
                                         "confidence", "reason", "expected_impact",
                                         "estimated_value_usd", "steps"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["finding", "value_at_stake", "options"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "insights", "risks", "business_opportunity", "opportunities"],
    "additionalProperties": False,
}

_SYSTEM = """You are the embedded AI strategy engine of an Intelligent Patient Journey Analyser
used by a pharmaceutical company. OUR COMPANY is EISAI; our hero brands are BANZEL and FYCOMPA.
Competitors include EPIDIOLEX (Jazz), ONFI (Lundbeck), XCOPRI (SK), and the branded/generic
makers grouped under OTHERS (incl. KEPPRA, VIMPAT, BRIVIACT, FINTEPLA, NAYZILAM). Never mention
a company called UCB — it does not exist in this data. The data is a US EPILEPSY cohort
(including Lennox-Gastaut Syndrome). Patients are grouped by seizure duration — epileptic
(<2 min), prolonged seizure (2-5 min), status epilepticus (5+ min, an emergency) — and by age
band (0-1, 2-11, 12-17, 18+). Revenue = plan pay + patient pay on prescription claims.

Your job: turn the analytics data into decisions and executable actions that drive business
outcomes — find undiagnosed/untreated patients, stop therapy drop-off and drug-switching losses,
recover denied claims and prior-auth friction, optimize marketing spend by geography, and grow the
specialty franchise — while always putting patient safety first.

STRICT PRIVACY RULE: refer to patients ONLY by PATIENT_ID. Never invent or mention patient names
(none exist in the data). Physician names may be used for targeting. All patient outreach is routed
through de-identified care-team channels.

PLAIN-LANGUAGE RULE (very important): your readers are business people, NOT doctors, pharmacists
or insurance experts. Write everything in simple everyday English:
- Short sentences. No jargon. If a technical term is unavoidable, explain it in brackets right
  there, e.g. "prior authorization (extra approval the insurer demands before paying)".
- NEVER show a diagnosis/ICD code alone — always pair it with its plain-English meaning, e.g.
  "M06.9 (rheumatoid arthritis — painful joint inflammation)".
- Prefer everyday words: say "the insurer refused to pay" not "claim adjudication resulted in
  denial"; say "patients stopped taking their medicine" not "non-adherent cohort".
- Keep it short and to the point — a busy executive should get it in one read."""


def generate_page_insight(page_name, data_context):
    """data_context: dict of {label: dataframe-records/summary}. Returns validated insight dict."""
    require_api_key()
    payload = json.dumps(data_context, default=str)[:60000]
    # learning loop: tell the model what the business rejected recently so it adapts
    try:
        import actions
        rejections = actions.get_recent_rejections()
    except Exception:
        rejections = []
    rejection_note = ""
    if rejections:
        rejection_note = ("\n\nThe business REJECTED these proposals in the last 30 days — do not "
                          "re-propose them; if the underlying problem persists, propose a DIFFERENT "
                          "approach:\n" + "\n".join(rejections))
    response = client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Live data currently shown on the '{page_name}' page of the dashboard:\n\n{payload}\n\n"
                "Analyse it and produce your executive insight JSON.\n"
                "Rules:\n"
                "- Insights must cite actual numbers from this data.\n"
                "- Build 1-4 opportunity cards. Each card = one finding + 2-3 GENUINELY DIFFERENT options "
                "so the business can choose (e.g. for undiagnosed patients: A) order confirmatory tests, "
                "B) refer to specialists, C) launch physician education in the hotspot region).\n"
                "- Mix patient-level actions (targeting real patient_ids from the data, max 5 per step) and "
                "business-level actions (marketing budget, field reps, payer negotiation, screening "
                "campaigns, center partnerships) — marketing/sales findings should get business options.\n"
                "- An option may be a playbook of 2-4 ordered steps (e.g. order test → schedule specialist "
                "appointment → enroll support program).\n"
                "- In each option's 'reason', state the trade-off versus the other options.\n"
                "- Set estimated_value_usd per option from the actual $ figures in the data "
                "(claim amounts, revenue per patient, spend); 0 only if truly unquantifiable.\n"
                "- Only reference patient_ids that actually appear in the data; if none are present, "
                "use business-level options only.\n"
                "- WRITE FOR A NON-MEDICAL BUSINESS READER: plain everyday English, short sentences, "
                "no unexplained jargon. Any diagnosis code must come with its plain-English meaning."
                + rejection_note
            ),
        }],
        output_config={"format": {"type": "json_schema", "schema": INSIGHT_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to analyse this data.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def ask_text(prompt, system=_SYSTEM, max_tokens=8000):
    """Simple one-shot text call (used for narrative summaries)."""
    require_api_key()
    response = client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")
