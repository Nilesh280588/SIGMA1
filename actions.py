"""Action layer — the agent's "hands".

The AI agent PROPOSES actions (rows in Agent_Pending_Actions). Nothing touches
a patient until a human clicks Approve. On approval the matching executor here
runs, the patient is notified (real SMTP if configured, otherwise a simulated
notification stored in Agent_Notifications), and everything is audit-logged.

Patient privacy: patients are addressed strictly by patient_id — names are
never read, displayed or sent anywhere.
"""
from datetime import datetime, timedelta
import db
import config

ACTION_TYPES = {
    "schedule_appointment": "Schedule a follow-up appointment for the patient",
    "send_notification": "Send an outreach notification/email to the patient's care team",
    "order_diagnostic_test": "Order a confirmatory diagnostic test (e.g. GAA gene sequencing)",
    "change_prescription": "Flag a prescription change / therapy re-initiation for physician sign-off",
    "escalate_claim": "Escalate a denied insurance claim to the Market Access team",
    "enroll_support_program": "Enroll the patient in a Patient Support Program to improve adherence",
    "refer_to_specialist": "Refer the patient to a rare-disease specialist / center of excellence",
}

# Strategic actions executed at company level (region / payer / clinic / drug), not per-patient.
BUSINESS_ACTION_TYPES = {
    "reallocate_marketing_budget": ("marketing-team", "Shift or resize marketing budget between regions/drugs"),
    "deploy_field_reps": ("field-force-team", "Deploy/increase field representatives in a region"),
    "launch_physician_education": ("medical-affairs-team", "Launch physician education program (region/specialty)"),
    "initiate_payer_negotiation": ("market-access-team", "Open negotiation / submit value dossier to a payer"),
    "launch_screening_campaign": ("medical-affairs-team", "Run a diagnostic screening campaign in a region"),
    "partner_with_center": ("commercial-team", "Establish partnership/KOL engagement with a treatment center"),
}

# Autonomy policy: LOW-risk actions may auto-execute when Autonomous Mode is ON.
# HIGH-risk actions (clinical interventions & budget/strategy moves) ALWAYS need a human.
RISK_TIERS = {
    "send_notification": "low",
    "schedule_appointment": "low",
    "escalate_claim": "low",
    "enroll_support_program": "low",
    "order_diagnostic_test": "high",
    "change_prescription": "high",
    "refer_to_specialist": "high",
    # every business action is high-risk by policy
    **{k: "high" for k in BUSINESS_ACTION_TYPES},
}


def _recently_rejected(table, id_col, where_sql, params):
    """True if the same action was rejected in the last 30 days — the system
    remembers the business's decisions and doesn't nag."""
    df = db.run_query(
        f"SELECT {id_col} FROM {table} WHERE {where_sql} AND status='Rejected' "
        f"AND resolved_at >= {db.sql_days_ago(30)}", params)
    return not df.empty


# ═══════════════════════════════════════════════════
# Proposal queue
# ═══════════════════════════════════════════════════
def propose_action(action_type, patient_id, details, reason, expected_impact="",
                   proposed_by="AI Agent", estimated_value_usd=0, auto_execute=False):
    """Queues a patient action. If auto_execute=True AND the action is low-risk
    (RISK_TIERS), it executes immediately as 'Autopilot' — full autonomy for
    safe actions, human gate for clinical/strategic ones."""
    if action_type not in ACTION_TYPES:
        return f"Unknown action_type '{action_type}'. Valid: {list(ACTION_TYPES)}"
    # avoid duplicate open proposals for the same patient+type
    dup = db.run_query(
        "SELECT action_id FROM Agent_Pending_Actions WHERE patient_id=? AND action_type=? AND status='Pending'",
        [patient_id, action_type],
    )
    if not dup.empty:
        return f"Skipped: a pending '{action_type}' already exists for patient {patient_id} (action_id={int(dup.iloc[0,0])})."
    # respect the business's past decisions — don't re-propose what was rejected recently
    if _recently_rejected("Agent_Pending_Actions", "action_id",
                          "patient_id=? AND action_type=?", [patient_id, action_type]):
        return (f"Skipped: '{action_type}' for patient {patient_id} was rejected by the business "
                "in the last 30 days.")
    db.execute(
        """INSERT INTO Agent_Pending_Actions
           (action_type, patient_id, details, reason, expected_impact, estimated_value_usd, status, proposed_by, created_at)
           VALUES (?,?,?,?,?,?, 'Pending', ?, SYSUTCDATETIME())""",
        [action_type, patient_id, str(details)[:900], str(reason)[:900],
         str(expected_impact)[:450], float(estimated_value_usd or 0), proposed_by],
    )
    db.audit(proposed_by, f"Proposed {action_type} for patient {patient_id}: {details}", "Proposed", patient_id)
    if auto_execute and RISK_TIERS.get(action_type) == "low":
        new_id = db.scalar(
            "SELECT MAX(action_id) FROM Agent_Pending_Actions WHERE patient_id=? AND action_type=? AND status='Pending'",
            [patient_id, action_type])
        if new_id:
            result = approve_action(int(new_id), actor="Autopilot")
            return f"🤖 AUTO-EXECUTED (low-risk): {result}"
    return f"Action '{action_type}' for patient {patient_id} queued for human approval."


def get_pending_actions():
    return db.run_query(
        "SELECT action_id, action_type, patient_id, details, reason, expected_impact, "
        "estimated_value_usd, proposed_by, created_at "
        "FROM Agent_Pending_Actions WHERE status='Pending' "
        "ORDER BY estimated_value_usd DESC, action_id DESC"
    )


def get_action_history(limit=100):
    df = db.run_query(
        "SELECT action_id, action_type, patient_id, details, status, estimated_value_usd, "
        "proposed_by, created_at, resolved_at "
        "FROM Agent_Pending_Actions WHERE status<>'Pending' ORDER BY action_id DESC"
    )
    return df.head(limit)


def reject_action(action_id, actor="Human Operator"):
    db.execute(
        f"UPDATE Agent_Pending_Actions SET status='Rejected', resolved_at={db.sql_now()} WHERE action_id=?",
        [action_id],
    )
    db.audit(actor, f"Rejected action #{action_id}", "Rejected")


def approve_action(action_id, actor="Human Operator"):
    """Approve → execute the real action → notify patient → audit. Returns a result message."""
    row = db.run_query("SELECT * FROM Agent_Pending_Actions WHERE action_id=?", [action_id])
    if row.empty:
        return "Action not found."
    a = row.iloc[0]
    if a["status"] != "Pending":
        return f"Action #{action_id} was already {a['status']}."

    executor = {
        "schedule_appointment": _exec_schedule_appointment,
        "send_notification": _exec_send_notification,
        "order_diagnostic_test": _exec_order_diagnostic_test,
        "change_prescription": _exec_change_prescription,
        "escalate_claim": _exec_escalate_claim,
        "enroll_support_program": _exec_enroll_support_program,
        "refer_to_specialist": _exec_refer_to_specialist,
    }[a["action_type"]]

    try:
        result = executor(int(a["patient_id"]), a["details"])
        db.execute(
            f"UPDATE Agent_Pending_Actions SET status='Executed', resolved_at={db.sql_now()} WHERE action_id=?",
            [action_id],
        )
        db.audit(actor, f"Approved & executed action #{action_id} ({a['action_type']}): {result}",
                 "Success", int(a["patient_id"]))
        return result
    except Exception as e:
        db.audit(actor, f"Action #{action_id} ({a['action_type']}) FAILED: {e}", "Failed", int(a["patient_id"]))
        return f"Execution failed: {e}"


# ═══════════════════════════════════════════════════
# Executors — each performs a real, recorded operation
# ═══════════════════════════════════════════════════
def _exec_schedule_appointment(patient_id, details):
    appt_date = (datetime.now() + timedelta(days=7)).date().isoformat()
    db.execute(
        "INSERT INTO Agent_Appointments (patient_id, appointment_type, appointment_date, notes, status, created_at) "
        "VALUES (?,?,?,?, 'Scheduled', SYSUTCDATETIME())",
        [patient_id, "Follow-up Consultation", appt_date, details],
    )
    notify = send_patient_notification(
        patient_id, subject="Appointment Scheduled",
        body=f"An appointment has been scheduled on {appt_date}. Details: {details}",
    )
    return f"Appointment booked for patient {patient_id} on {appt_date}. {notify}"


def _exec_send_notification(patient_id, details):
    return send_patient_notification(patient_id, subject="Care Team Outreach", body=details)


def _exec_order_diagnostic_test(patient_id, details):
    # Record the order as an agent appointment (never write into the source claims tables).
    order_date = (datetime.now() + timedelta(days=5)).date().isoformat()
    db.execute(
        "INSERT INTO Agent_Appointments (patient_id, appointment_type, appointment_date, notes, status, created_at) "
        "VALUES (?,?,?,?, 'Test Ordered', SYSUTCDATETIME())",
        [patient_id, "Diagnostic Test", order_date, str(details)[:900]],
    )
    notify = send_patient_notification(
        patient_id, subject="Diagnostic Test Ordered",
        body=f"A diagnostic test has been ordered: {details}. Please contact your clinic to book a slot.",
    )
    return f"Diagnostic test ordered for patient {patient_id} (target {order_date}). {notify}"


def _exec_change_prescription(patient_id, details):
    db.execute(
        "INSERT INTO Agent_Appointments (patient_id, appointment_type, appointment_date, notes, status, created_at) "
        "VALUES (?,?,?,?, 'Awaiting Physician', SYSUTCDATETIME())",
        [patient_id, "Prescription Review", (datetime.now() + timedelta(days=3)).date().isoformat(), details],
    )
    notify = send_patient_notification(
        patient_id, subject="Prescription Review Scheduled",
        body=f"Your care team has scheduled a prescription review. Proposed change: {details}",
    )
    return f"Prescription change flagged for physician sign-off (patient {patient_id}). {notify}"


def _exec_escalate_claim(patient_id, details):
    notify = send_patient_notification(
        patient_id, channel="internal", recipient="market-access-team",
        subject=f"Denied claim escalation — patient {patient_id}", body=details,
    )
    return f"Claim escalated to Market Access team for patient {patient_id}. {notify}"


def _exec_enroll_support_program(patient_id, details):
    notify = send_patient_notification(
        patient_id, subject="Patient Support Program Enrollment",
        body=f"You have been enrolled in a support program. {details}",
    )
    return f"Patient {patient_id} enrolled in support program. {notify}"


def _exec_refer_to_specialist(patient_id, details):
    appt_date = (datetime.now() + timedelta(days=10)).date().isoformat()
    db.execute(
        "INSERT INTO Agent_Appointments (patient_id, appointment_type, appointment_date, notes, status, created_at) "
        "VALUES (?,?,?,?, 'Referral Sent', SYSUTCDATETIME())",
        [patient_id, "Specialist Referral", appt_date, details],
    )
    notify = send_patient_notification(
        patient_id, subject="Specialist Referral",
        body=f"You have been referred to a rare-disease specialist. Target date {appt_date}. {details}",
    )
    return f"Specialist referral created for patient {patient_id} (target {appt_date}). {notify}"


# ═══════════════════════════════════════════════════
# Business (strategic) actions — company-level execution
# ═══════════════════════════════════════════════════
def propose_business_action(action_type, target_type, target, details, reason,
                            expected_impact="", proposed_by="AI Agent", estimated_value_usd=0):
    if action_type not in BUSINESS_ACTION_TYPES:
        return f"Unknown business action_type '{action_type}'. Valid: {list(BUSINESS_ACTION_TYPES)}"
    dup = db.run_query(
        "SELECT business_action_id FROM Agent_Business_Actions "
        "WHERE action_type=? AND target=? AND status='Pending'",
        [action_type, str(target)[:110]],
    )
    if not dup.empty:
        return (f"Skipped: a pending '{action_type}' for target '{target}' already exists "
                f"(id={int(dup.iloc[0, 0])}).")
    if _recently_rejected("Agent_Business_Actions", "business_action_id",
                          "action_type=? AND target=?", [action_type, str(target)[:110]]):
        return (f"Skipped: '{action_type}' for '{target}' was rejected by the business in the "
                "last 30 days.")
    db.execute(
        """INSERT INTO Agent_Business_Actions
           (action_type, target_type, target, details, reason, expected_impact, estimated_value_usd, status, proposed_by, created_at)
           VALUES (?,?,?,?,?,?,?, 'Pending', ?, SYSUTCDATETIME())""",
        [action_type, str(target_type)[:28], str(target)[:110], str(details)[:900],
         str(reason)[:900], str(expected_impact)[:450], float(estimated_value_usd or 0), proposed_by],
    )
    db.audit(proposed_by, f"Proposed business action {action_type} on {target_type} '{target}': {details}", "Proposed")
    return f"Business action '{action_type}' targeting {target_type} '{target}' queued for approval."


def get_pending_business_actions():
    return db.run_query(
        "SELECT business_action_id, action_type, target_type, target, details, reason, "
        "expected_impact, estimated_value_usd, proposed_by, created_at "
        "FROM Agent_Business_Actions WHERE status='Pending' "
        "ORDER BY estimated_value_usd DESC, business_action_id DESC"
    )


def get_business_action_history(limit=100):
    return db.run_query(
        "SELECT business_action_id, action_type, target_type, target, details, status, result, "
        "proposed_by, created_at, resolved_at "
        "FROM Agent_Business_Actions WHERE status<>'Pending' ORDER BY business_action_id DESC"
    ).head(limit)


def reject_business_action(business_action_id, actor="Human Operator"):
    db.execute(
        f"UPDATE Agent_Business_Actions SET status='Rejected', resolved_at={db.sql_now()} "
        "WHERE business_action_id=?",
        [business_action_id],
    )
    db.audit(actor, f"Rejected business action #{business_action_id}", "Rejected")


def approve_business_action(business_action_id, actor="Human Operator"):
    """Approve → create the internal task, notify the owning team, audit-log."""
    row = db.run_query(
        "SELECT * FROM Agent_Business_Actions WHERE business_action_id=?", [business_action_id])
    if row.empty:
        return "Business action not found."
    a = row.iloc[0]
    if a["status"] != "Pending":
        return f"Business action #{business_action_id} was already {a['status']}."
    try:
        team, _ = BUSINESS_ACTION_TYPES[a["action_type"]]
        notify = send_patient_notification(
            patient_id=None, channel="internal", recipient=f"{team}@pharma.internal",
            subject=f"APPROVED: {a['action_type'].replace('_', ' ').title()} — {a['target_type']} {a['target']}",
            body=f"{a['details']}\n\nRationale: {a['reason']}\nExpected impact: {a['expected_impact']}",
        )
        result = (f"Task created for {team} — {a['action_type'].replace('_', ' ')} on "
                  f"{a['target_type']} '{a['target']}'. {notify}")
        db.execute(
            f"UPDATE Agent_Business_Actions SET status='Executed', result=?, resolved_at={db.sql_now()} "
            "WHERE business_action_id=?",
            [result[:450], business_action_id],
        )
        db.audit(actor, f"Approved & executed business action #{business_action_id} "
                        f"({a['action_type']} → {a['target']}): {result}", "Success")
        return result
    except Exception as e:
        db.audit(actor, f"Business action #{business_action_id} FAILED: {e}", "Failed")
        return f"Execution failed: {e}"


# ═══════════════════════════════════════════════════
# Learning & value metrics
# ═══════════════════════════════════════════════════
def get_recent_rejections(limit=15):
    """What the business said NO to recently — fed back to the AI so it adapts."""
    pat = db.run_query(
        "SELECT action_type, patient_id, details FROM Agent_Pending_Actions "
        "WHERE status='Rejected' ORDER BY action_id DESC").head(limit)
    biz = db.run_query(
        "SELECT action_type, target_type, target, details FROM Agent_Business_Actions "
        "WHERE status='Rejected' ORDER BY business_action_id DESC").head(limit)
    lines = [f"- {r.action_type} for patient {r.patient_id}: {str(r.details)[:100]}"
             for r in pat.itertuples()]
    lines += [f"- {r.action_type} for {r.target_type} '{r.target}': {str(r.details)[:100]}"
              for r in biz.itertuples()]
    return lines[:limit]


def get_value_metrics():
    """The ROI loop: what the agentic system has proposed, executed and captured."""
    m = {}
    m["value_captured"] = float(db.scalar(
        "SELECT COALESCE(SUM(estimated_value_usd),0) FROM Agent_Pending_Actions WHERE status='Executed'")) + \
        float(db.scalar(
            "SELECT COALESCE(SUM(estimated_value_usd),0) FROM Agent_Business_Actions WHERE status='Executed'"))
    m["value_pending"] = float(db.scalar(
        "SELECT COALESCE(SUM(estimated_value_usd),0) FROM Agent_Pending_Actions WHERE status='Pending'")) + \
        float(db.scalar(
            "SELECT COALESCE(SUM(estimated_value_usd),0) FROM Agent_Business_Actions WHERE status='Pending'"))
    m["executed"] = int(db.scalar(
        "SELECT COUNT(*) FROM Agent_Pending_Actions WHERE status='Executed'")) + \
        int(db.scalar("SELECT COUNT(*) FROM Agent_Business_Actions WHERE status='Executed'"))
    m["auto_executed"] = int(db.scalar(
        "SELECT COUNT(*) FROM Agent_Audit_Log WHERE actor='Autopilot' AND status='Success'"))
    m["rejected"] = int(db.scalar(
        "SELECT COUNT(*) FROM Agent_Pending_Actions WHERE status='Rejected'")) + \
        int(db.scalar("SELECT COUNT(*) FROM Agent_Business_Actions WHERE status='Rejected'"))
    m["pending"] = int(db.scalar(
        "SELECT COUNT(*) FROM Agent_Pending_Actions WHERE status='Pending'")) + \
        int(db.scalar("SELECT COUNT(*) FROM Agent_Business_Actions WHERE status='Pending'"))
    m["notifications"] = int(db.scalar("SELECT COUNT(*) FROM Agent_Notifications"))
    m["appointments"] = int(db.scalar("SELECT COUNT(*) FROM Agent_Appointments"))
    total_resolved = m["executed"] + m["rejected"]
    m["approval_rate"] = round(100.0 * m["executed"] / total_resolved, 1) if total_resolved else 0.0
    return m


# ═══════════════════════════════════════════════════
# Option / playbook execution (multi-step, mixed patient + business)
# ═══════════════════════════════════════════════════
def execute_option_steps(steps, proposed_by="AI Insight", execute_now=True, option_value_usd=0):
    """Runs (or queues) the ordered steps of one chosen option.
    Each step: {action_type, patient_ids[], target_type, target, details}.
    Patient steps fan out per patient; business steps run once. Returns messages.
    option_value_usd is credited to the FIRST step only (no double counting)."""
    messages = []
    value_left = float(option_value_usd or 0)
    for step in steps:
        a_type = step.get("action_type", "")
        details = step.get("details", "")
        reason = step.get("reason", "") or details
        impact = step.get("expected_impact", "")
        step_value, value_left = value_left, 0
        if a_type in BUSINESS_ACTION_TYPES:
            msg = propose_business_action(a_type, step.get("target_type", "region"),
                                          step.get("target", ""), details, reason, impact,
                                          proposed_by, estimated_value_usd=step_value)
            messages.append(msg)
            if execute_now:
                pend = get_pending_business_actions()
                mine = pend[(pend.action_type == a_type) & (pend.target == str(step.get("target", ""))[:110])]
                if not mine.empty:
                    messages.append(approve_business_action(int(mine.iloc[0]["business_action_id"])))
        elif a_type in ACTION_TYPES:
            pids = (step.get("patient_ids") or [])[:5]
            per_pid_value = step_value / len(pids) if pids else 0
            for pid in pids:
                msg = propose_action(a_type, int(pid), details, reason, impact, proposed_by,
                                     estimated_value_usd=per_pid_value)
                messages.append(msg)
                if execute_now:
                    pend = get_pending_actions()
                    mine = pend[(pend.patient_id == int(pid)) & (pend.action_type == a_type)]
                    if not mine.empty:
                        messages.append(approve_action(int(mine.iloc[0]["action_id"])))
        else:
            messages.append(f"Skipped unknown action_type '{a_type}'.")
    return messages


# ═══════════════════════════════════════════════════
# Notification / email delivery
# ═══════════════════════════════════════════════════
def send_patient_notification(patient_id, subject, body, channel="email", recipient=None):
    """Sends via SMTP if configured; otherwise records a simulated notification.
    Recipient is a de-identified alias — patient names are never used."""
    recipient = recipient or f"patient_{patient_id}@notify.pharma"
    status = "Simulated"
    if config.SMTP_HOST and config.SMTP_USER:
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = config.SMTP_FROM
            msg["To"] = recipient
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
            status = "Sent"
        except Exception as e:
            status = f"Failed: {str(e)[:80]}"
    db.execute(
        "INSERT INTO Agent_Notifications (patient_id, channel, recipient, subject, body, status, sent_at) "
        "VALUES (?,?,?,?,?,?, SYSUTCDATETIME())",
        [patient_id, channel, recipient, subject[:290], str(body)[:1900], status],
    )
    return f"Notification to {recipient}: {status}."


def get_notifications(limit=100):
    return db.run_query(
        "SELECT notification_id, patient_id, channel, recipient, subject, status, sent_at "
        "FROM Agent_Notifications ORDER BY notification_id DESC"
    ).head(limit)


def get_appointments(limit=100):
    return db.run_query(
        "SELECT appointment_id, patient_id, appointment_type, appointment_date, status, notes, created_at "
        "FROM Agent_Appointments ORDER BY appointment_id DESC"
    ).head(limit)


def get_audit_log(limit=200):
    return db.run_query(
        "SELECT log_id, actor, action, status, related_patient_id, created_at "
        "FROM Agent_Audit_Log ORDER BY log_id DESC"
    ).head(limit)
