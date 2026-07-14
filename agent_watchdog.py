"""Autonomous Watchdog — the always-on reflex layer, running against live SSMS data.

Runs by itself on app start (and from scheduler.py). Detects three conditions
that shouldn't wait for a human to open a page, and proposes the right action
for each. In Autonomous Mode, LOW-risk actions auto-execute as 'Autopilot';
HIGH-risk actions always wait for approval.

  1. HIGH-severity side effects (Fact_Sx) with no follow-up   -> safety appointment
  2. Denied insurance claims (Fact_Insurance_Claims), $ desc   -> escalate claim
  3. Epilepsy patients diagnosed but never treated (no ASM Rx) -> outreach

Deep strategic analysis stays with Claude (agent.run_autopilot_scan); this is
the fast reflex layer.
"""
import db
import actions

MAX_PER_CATEGORY = 5
import backend as _bk
RA_ICD = _bk.EPI_ICD          # confirmed epilepsy codes
RA_USC = _bk.ASM_USC          # anti-seizure medicine classes


def _unhandled(patient_id, action_type):
    df = db.run_query(
        "SELECT action_id FROM Agent_Pending_Actions WHERE patient_id=? AND action_type=? "
        "AND status IN ('Pending','Executed')",
        [int(patient_id), action_type])
    return df.empty


def sweep(auto_execute=False):
    summary = {"safety": 0, "claims": 0, "untreated": 0, "auto_executed": 0, "messages": []}

    # ── 1. Clinical safety: recent HIGH severity side effects ──
    try:
        sev = db.run_query("""
            SELECT TOP 40 s.patient_id, s.side_effect_name, s.report_date, d.DRUG_NAME
            FROM Fact_Sx s LEFT JOIN [DRUG DIM] d ON s.drug_id = d.DRUG_ID
            WHERE s.severity = 'High'
              AND s.report_date >= DATEADD(DAY, -365, (SELECT MAX(report_date) FROM Fact_Sx))
            ORDER BY s.report_date DESC""")
        for r in sev.itertuples():
            if summary["safety"] >= MAX_PER_CATEGORY:
                break
            if not _unhandled(r.patient_id, "schedule_appointment"):
                continue
            msg = actions.propose_action(
                "schedule_appointment", int(r.patient_id),
                f"URGENT safety follow-up: high-severity '{r.side_effect_name}' on {r.DRUG_NAME} "
                f"({str(r.report_date)[:10]}).",
                "Watchdog: high-severity adverse event needs clinical follow-up within days.",
                "Patient safety; prevents therapy discontinuation and liability.",
                proposed_by="Watchdog", auto_execute=auto_execute)
            summary["safety"] += 1
            summary["auto_executed"] += 1 if msg.startswith("\U0001f916") else 0
            summary["messages"].append(msg)
    except Exception as e:
        summary["messages"].append(f"(safety scan skipped: {str(e)[:80]})")

    # ── 2. Revenue leakage: denied claims not yet escalated ──
    try:
        denied = db.run_query("""
            SELECT TOP 40 patient_id, payer_name, claim_amount, denial_reason
            FROM Fact_Insurance_Claims WHERE status='Denied'
            ORDER BY claim_amount DESC""")
        for r in denied.itertuples():
            if summary["claims"] >= MAX_PER_CATEGORY:
                break
            if not _unhandled(r.patient_id, "escalate_claim"):
                continue
            amt = float(r.claim_amount or 0)
            msg = actions.propose_action(
                "escalate_claim", int(r.patient_id),
                f"Escalate denied claim of ${amt:,.0f} at {r.payer_name} (reason: {r.denial_reason}).",
                "Watchdog: high-value denial — appeal window is time-limited.",
                f"Recoverable revenue up to ${amt:,.0f}.",
                proposed_by="Watchdog", estimated_value_usd=amt, auto_execute=auto_execute)
            summary["claims"] += 1
            summary["auto_executed"] += 1 if msg.startswith("\U0001f916") else 0
            summary["messages"].append(msg)
    except Exception as e:
        summary["messages"].append(f"(claims scan skipped: {str(e)[:80]})")

    # ── 3. Growth: epilepsy diagnosed but never on anti-seizure therapy ──
    try:
        avg_rev = float(db.scalar(f"""
            SELECT COALESCE(AVG(PLAN_PAY + PATIENT_PAY),0) * 12 FROM [RX CLM]
            WHERE DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC}))"""))
        untreated = db.run_query(f"""
            SELECT TOP 40 CAST(d.PATIENT_ID AS BIGINT) AS patient_id, MIN(d.SERVICE_DATE) AS diagnosed_on
            FROM [DX CLM] d
            WHERE d.DIAGNOSIS_CODE IN ({RA_ICD})
              AND d.PATIENT_ID NOT IN (
                  SELECT PATIENT_ID FROM [RX CLM]
                  WHERE DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC})))
            GROUP BY d.PATIENT_ID ORDER BY MIN(d.SERVICE_DATE)""")
        for r in untreated.itertuples():
            if summary["untreated"] >= MAX_PER_CATEGORY:
                break
            if not _unhandled(r.patient_id, "send_notification"):
                continue
            msg = actions.propose_action(
                "send_notification", int(r.patient_id),
                f"Treatment-initiation outreach: confirmed epilepsy diagnosis on {str(r.diagnosed_on)[:10]}, "
                "no anti-seizure medicine started. Care team to discuss options.",
                "Watchdog: diagnosed-but-untreated = clinical risk + unrealized revenue.",
                f"Therapy initiation ~ ${avg_rev:,.0f}/yr revenue per patient; better outcomes.",
                proposed_by="Watchdog", estimated_value_usd=avg_rev, auto_execute=auto_execute)
            summary["untreated"] += 1
            summary["auto_executed"] += 1 if msg.startswith("\U0001f916") else 0
            summary["messages"].append(msg)
    except Exception as e:
        summary["messages"].append(f"(untreated scan skipped: {str(e)[:80]})")

    total = summary["safety"] + summary["claims"] + summary["untreated"]
    if total:
        try:
            db.audit("Watchdog",
                     f"Sweep: {summary['safety']} safety, {summary['claims']} claims, "
                     f"{summary['untreated']} untreated -> {summary['auto_executed']} auto-executed",
                     "Success")
        except Exception:
            pass
    return summary
