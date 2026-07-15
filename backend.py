"""Analytics backend — runs live against the SSMS database (18 real tables).

Real table names contain spaces, so every identifier is bracketed: [DRUG DIM],
[PX  CLM] (two spaces), [PRC ROLES], etc.

Business anchors (from the loaded data):
  • Revenue        = PLAN_PAY + PATIENT_PAY on [RX CLM]
  • Our company    = EISAI  (hero brands: BANZEL, FYCOMPA)
  • Specialty      = Rheumatoid Arthritis biologics (BB_USC_CODE 68200)
  • Untreated      = has a diagnosis in [DX CLM] but no [RX CLM] prescription
  • Undiagnosed    = only symptom ICD codes recorded, no definitive diagnosis
  • Adherence      = derived from refill recency (no status column exists)
PRIVACY: patients are identified by PATIENT_ID only (no patient-name columns exist).
"""
import pandas as pd
import db

REV = "(PLAN_PAY + PATIENT_PAY)"
OUR_MFR = "EISAI"                                # our company (hero brands: BANZEL, FYCOMPA)
ADH_WINDOW = 120                                 # days since last fill = still active
GENDER = {1: "M", 2: "F", 1.0: "M", 2.0: "F"}

# ── Epilepsy cohort: contexts by seizure duration (encoded in diagnosis codes) ──
EPI_CTX = {
    "EPILEPTIC": ["G40.909", "G40.109", "G40.209", "G40.A09", "G40.812"],   # < 2 min
    "PROLONGED": ["G40.919", "G40.119", "G40.219", "G40.814"],              # 2-5 min
    "STATUS":    ["G40.901", "G40.911", "G40.111", "G40.811", "G40.813"],   # >= 5 min
}
CTX_LABEL = {"EPILEPTIC": "Epileptic (< 2 min)", "PROLONGED": "Prolonged seizure (2-5 min)",
             "STATUS": "Status epilepticus (5+ min)"}
AGE_BANDS = [("0-1 years", 2024, 2100), ("2-11 years", 2014, 2023),
             ("12-17 years", 2008, 2013), ("18+ years", 1900, 2007)]


def _q(codes):
    return ",".join(f"'{c}'" for c in codes)


EPI_ICD = _q(EPI_CTX["EPILEPTIC"] + EPI_CTX["PROLONGED"] + EPI_CTX["STATUS"])  # confirmed epilepsy
SYMPTOM_ICD = "'R56.9','R56.00','R56.01'"        # seizure symptoms, not yet confirmed
ASM_USC = "72110,72120,72130,72140"              # anti-seizure medicine classes
# back-compat aliases used across queries
RA_ICD = EPI_ICD
RA_USC = ASM_USC

# max fill date in the data = the anchor for "recent" logic (data ends 2025)
_MAXD = "(SELECT MAX(RX_FILL_DATE) FROM [RX CLM])"


def _rng(col, d1, d2):
    """Optional date-range WHERE fragment: returns (sql, params).
    Every section's date slicer funnels through this."""
    if d1 and d2:
        return f" AND {col} BETWEEN ? AND ?", [d1, d2]
    return "", []


def get_date_bounds():
    """(min, max) event dates across prescriptions & diagnoses — bounds for the slicers."""
    df = db.run_query("""
        SELECT MIN(x) AS mn, MAX(x) AS mx FROM (
            SELECT MIN(RX_FILL_DATE) AS x FROM [RX CLM]
            UNION ALL SELECT MAX(RX_FILL_DATE) FROM [RX CLM]
            UNION ALL SELECT MIN(SERVICE_DATE) FROM [DX CLM]
            UNION ALL SELECT MAX(SERVICE_DATE) FROM [DX CLM]) t""")
    if df.empty or pd.isna(df.iloc[0]["mn"]):
        return None, None
    return pd.to_datetime(df.iloc[0]["mn"]), pd.to_datetime(df.iloc[0]["mx"])


# ═══════════════════════════════════════════════════
# EXECUTIVE KPIs
# ═══════════════════════════════════════════════════
# explicit anti-seizure-medicine drug filter — every executive KPI is scoped to the
# SEIZURE cohort (epilepsy/symptom diagnoses + ASM prescriptions), never "all patients"
_ASM_DRUGS = f"(SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({ASM_USC}))"
COHORT_KPIS = True   # marker for app.py's stale-module self-heal guard


def get_kpi_summary(d1=None, d2=None):
    k = {}
    rx_w, rx_p = _rng("RX_FILL_DATE", d1, d2)
    dx_w, dx_p = _rng("SERVICE_DATE", d1, d2)
    cl_w, cl_p = _rng("claim_date", d1, d2)
    # SEIZURE COHORT = patients with an epilepsy/seizure-symptom diagnosis OR an
    # anti-seizure prescription (never the raw patient dimension)
    k["total_patients"] = int(db.scalar(f"""
        SELECT COUNT(*) FROM (
            SELECT PATIENT_ID FROM [DX CLM]
            WHERE DIAGNOSIS_CODE IN ({EPI_ICD},{SYMPTOM_ICD}){dx_w}
            UNION
            SELECT PATIENT_ID FROM [RX CLM]
            WHERE DRUG_ID IN {_ASM_DRUGS}{rx_w}) t""", dx_p + rx_p))
    if d1 and d2:
        # "active" anchored to the end of the selected window
        k["active_treatments"] = int(db.scalar(f"""
            SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM]
            WHERE DRUG_ID IN {_ASM_DRUGS}
              AND RX_FILL_DATE BETWEEN DATEADD(DAY, -{ADH_WINDOW}, ?) AND ?""", [d2, d2]))
    else:
        k["active_treatments"] = int(db.scalar(f"""
            SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM]
            WHERE DRUG_ID IN {_ASM_DRUGS}
              AND RX_FILL_DATE >= DATEADD(DAY, -{ADH_WINDOW}, {_MAXD})"""))
    k["total_revenue"] = float(db.scalar(
        f"SELECT COALESCE(SUM({REV}),0) FROM [RX CLM] WHERE DRUG_ID IN {_ASM_DRUGS}{rx_w}", rx_p))
    treated = int(db.scalar(
        f"SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM] WHERE DRUG_ID IN {_ASM_DRUGS}{rx_w}", rx_p))
    k["discontinued"] = max(treated - k["active_treatments"], 0)
    sw_w, sw_p = _rng("switch_date", d1, d2)
    k["total_switches"] = int(db.scalar(f"SELECT COUNT(*) FROM Fact_Drug_Switches WHERE 1=1{sw_w}", sw_p))
    k["denied_claims"] = int(db.scalar(
        f"SELECT COUNT(*) FROM Fact_Insurance_Claims "
        f"WHERE status='Denied' AND drug_id IN {_ASM_DRUGS}{cl_w}", cl_p))
    k["rare_diagnosed"] = int(db.scalar(
        f"SELECT COUNT(DISTINCT PATIENT_ID) FROM [DX CLM] WHERE DIAGNOSIS_CODE IN ({RA_ICD}){dx_w}", dx_p))
    k["untreated_rare"] = int(db.scalar(f"""
        SELECT COUNT(DISTINCT d.PATIENT_ID) FROM [DX CLM] d
        WHERE d.DIAGNOSIS_CODE IN ({RA_ICD}){dx_w.replace('SERVICE_DATE', 'd.SERVICE_DATE')}
          AND d.PATIENT_ID NOT IN (
              SELECT PATIENT_ID FROM [RX CLM]
              WHERE DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC})))""", dx_p))
    k["undiagnosed_suspects"] = int(db.scalar(f"""
        SELECT COUNT(*) FROM (
            SELECT PATIENT_ID FROM [DX CLM] WHERE 1=1{dx_w}
            GROUP BY PATIENT_ID
            HAVING SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 1 ELSE 0 END) >= 1
               AND SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 0 ELSE 1 END) = 0
        ) s""", dx_p))
    return k


def _usc_where(usc_name):
    if usc_name:
        return (" AND DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_NAME = ?)", [usc_name])
    return "", []


def get_revenue_by_month(d1=None, d2=None, usc_name=None):
    """Anti-seizure medicine sales only (the executive view is seizure-cohort scoped)."""
    w, p = _rng("RX_FILL_DATE", d1, d2)
    uw, up = _usc_where(usc_name)
    return db.run_query(f"""
        SELECT FORMAT(RX_FILL_DATE,'yyyy-MM') AS month, SUM({REV}) AS revenue
        FROM [RX CLM] WHERE RX_FILL_DATE IS NOT NULL
          AND DRUG_ID IN {_ASM_DRUGS}{w}{uw}
        GROUP BY FORMAT(RX_FILL_DATE,'yyyy-MM') ORDER BY month""", p + up)


def get_revenue_by_quarter(d1=None, d2=None, usc_name=None):
    """Anti-seizure medicine sales only (the executive view is seizure-cohort scoped)."""
    w, p = _rng("RX_FILL_DATE", d1, d2)
    uw, up = _usc_where(usc_name)
    df = db.run_query(f"""
        SELECT YEAR(RX_FILL_DATE) AS yr, DATEPART(QUARTER, RX_FILL_DATE) AS q, SUM({REV}) AS revenue
        FROM [RX CLM] WHERE RX_FILL_DATE IS NOT NULL
          AND DRUG_ID IN {_ASM_DRUGS}{w}{uw}
        GROUP BY YEAR(RX_FILL_DATE), DATEPART(QUARTER, RX_FILL_DATE)
        ORDER BY yr, q""", p + up)
    if not df.empty:
        df["period"] = df["yr"].astype(int).astype(str) + " Q" + df["q"].astype(int).astype(str)
    return df


def get_patient_distribution(d1=None, d2=None):
    """Seizure patients by anti-seizure medicine class (ASM classes only)."""
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    return db.run_query(f"""
        SELECT TOP 10 dd.BB_USC_NAME AS disease_name,
               COUNT(DISTINCT rx.PATIENT_ID) AS patient_count
        FROM [RX CLM] rx JOIN [DRUG DIM] dd ON rx.DRUG_ID = dd.DRUG_ID
        WHERE dd.BB_USC_NAME IS NOT NULL AND dd.BB_USC_CODE IN ({ASM_USC}){w}
        GROUP BY dd.BB_USC_NAME ORDER BY patient_count DESC""", p)


# ═══════════════════════════════════════════════════
# PATIENT JOURNEY (patient_id only) — context / sub-context aware
# ═══════════════════════════════════════════════════
_SEV_CASE = f"""MAX(CASE WHEN d.DIAGNOSIS_CODE IN ({_q(EPI_CTX['STATUS'])}) THEN 3
                         WHEN d.DIAGNOSIS_CODE IN ({_q(EPI_CTX['PROLONGED'])}) THEN 2
                         WHEN d.DIAGNOSIS_CODE IN ({_q(EPI_CTX['EPILEPTIC'])}) THEN 1
                         ELSE 0 END)"""
_SEV_TO_CTX = {1: "EPILEPTIC", 2: "PROLONGED", 3: "STATUS"}


def band_of(yob):
    try:
        yob = int(yob)
    except (TypeError, ValueError):
        return "—"
    for label, lo, hi in AGE_BANDS:
        if lo <= yob <= hi:
            return label
    return "—"


def get_journey_patients(context="All", band="All"):
    """Diagnosed patients for the journey dropdown, filtered by seizure context and
    age band, richest journeys (most prescriptions) first."""
    sql = f"""
        SELECT TOP 1500 CAST(c.PATIENT_ID AS BIGINT) AS patient_id, c.sev,
               p.PATIENT_BIRTH_YEAR AS yob, p.PATIENT_GENDER AS gender_code,
               p.PATIENT_STATE AS state,
               (SELECT COUNT(*) FROM [RX CLM] r WHERE r.PATIENT_ID = c.PATIENT_ID) AS n_rx
        FROM (SELECT d.PATIENT_ID, {_SEV_CASE} AS sev
              FROM [DX CLM] d GROUP BY d.PATIENT_ID) c
        JOIN [PTNT DIM] p ON c.PATIENT_ID = p.PATIENT_ID
        WHERE c.sev > 0"""
    params = []
    if context and context != "All":
        key = next((k for k, v in CTX_LABEL.items() if v == context), None)
        if key:
            sev = {"EPILEPTIC": 1, "PROLONGED": 2, "STATUS": 3}[key]
            sql += " AND c.sev = ?"
            params.append(sev)
    if band and band != "All":
        b = next(((lo, hi) for label, lo, hi in AGE_BANDS if label == band), None)
        if b:
            sql += " AND p.PATIENT_BIRTH_YEAR BETWEEN ? AND ?"
            params.extend([b[0], b[1]])
    sql += " ORDER BY n_rx DESC, c.PATIENT_ID"
    df = db.run_query(sql, params)
    if not df.empty:
        df["context"] = df["sev"].map(lambda s: CTX_LABEL.get(_SEV_TO_CTX.get(int(s), ""), "—"))
        df["age_band"] = df["yob"].map(band_of)
        df["gender"] = df["gender_code"].map(GENDER).fillna("—")
    return df


def get_journey_patient_count(context="All", band="All"):
    """TRUE total of diagnosed epilepsy patients matching the filters (uncapped)."""
    sql = f"""
        SELECT COUNT(*) FROM (
            SELECT d.PATIENT_ID, {_SEV_CASE} AS sev
            FROM [DX CLM] d GROUP BY d.PATIENT_ID) c
        JOIN [PTNT DIM] p ON c.PATIENT_ID = p.PATIENT_ID
        WHERE c.sev > 0"""
    params = []
    if context and context != "All":
        key = next((k for k, v in CTX_LABEL.items() if v == context), None)
        if key:
            sql += " AND c.sev = ?"
            params.append({"EPILEPTIC": 1, "PROLONGED": 2, "STATUS": 3}[key])
    if band and band != "All":
        b = next(((lo, hi) for label, lo, hi in AGE_BANDS if label == band), None)
        if b:
            sql += " AND p.PATIENT_BIRTH_YEAR BETWEEN ? AND ?"
            params.extend([b[0], b[1]])
    return int(db.scalar(sql, params))


def get_patient_context(patient_id):
    """(context_label, age_band, yob, gender, state) for one patient."""
    df = db.run_query(f"""
        SELECT p.PATIENT_BIRTH_YEAR AS yob, p.PATIENT_GENDER AS g, p.PATIENT_STATE AS state,
               COALESCE((SELECT {_SEV_CASE} FROM [DX CLM] d WHERE d.PATIENT_ID = p.PATIENT_ID), 0) AS sev
        FROM [PTNT DIM] p WHERE p.PATIENT_ID = ?""", [int(patient_id)])
    if df.empty:
        return None
    r = df.iloc[0]
    ctx = CTX_LABEL.get(_SEV_TO_CTX.get(int(r["sev"]), ""), "Not yet confirmed (symptoms only)")
    yob = int(r["yob"]) if pd.notna(r["yob"]) else None
    return {"context": ctx, "age_band": band_of(yob), "yob": yob or "—",
            "gender": GENDER.get(r["g"], "—"), "state": r["state"]}


def get_patient_ids():
    """Most active patients (by Rx volume) for the dropdown; text box allows any ID."""
    return db.run_query("""
        SELECT TOP 1500 CAST(rx.PATIENT_ID AS BIGINT) AS patient_id, p.PATIENT_STATE AS region
        FROM [RX CLM] rx JOIN [PTNT DIM] p ON rx.PATIENT_ID = p.PATIENT_ID
        GROUP BY rx.PATIENT_ID, p.PATIENT_STATE
        ORDER BY COUNT(*) DESC""")


def get_patient_info(patient_id):
    df = db.run_query("""
        SELECT CAST(PATIENT_ID AS BIGINT) AS patient_id, PATIENT_BIRTH_YEAR,
               PATIENT_GENDER, PATIENT_ZIP3, PATIENT_STATE
        FROM [PTNT DIM] WHERE PATIENT_ID = ?""", [int(patient_id)])
    if df.empty:
        return None
    r = df.iloc[0]
    yr = int(r["PATIENT_BIRTH_YEAR"]) if pd.notna(r["PATIENT_BIRTH_YEAR"]) else None
    return pd.Series({
        "patient_id": int(r["patient_id"]),
        "age": (2025 - yr) if yr else "—",
        "gender": GENDER.get(r["PATIENT_GENDER"], "—"),
        "state": r["PATIENT_STATE"],
        "zip_code": int(r["PATIENT_ZIP3"]) if pd.notna(r["PATIENT_ZIP3"]) else "—",
    })


def get_patient_timeline_events(patient_id):
    pid = [int(patient_id)]
    q_dx = """SELECT d.SERVICE_DATE AS event_date, 'Diagnosis' AS event_type,
              dd.DIAGNOSIS_DESCRIPTION AS details, d.DIAGNOSIS_CODE AS extra
              FROM [DX CLM] d LEFT JOIN [DX DIM] dd ON d.DIAGNOSIS_CODE = dd.DIAGNOSIS_CODE
              WHERE d.PATIENT_ID = ?"""
    q_rx = f"""SELECT rx.RX_FILL_DATE AS event_date, 'Prescription' AS event_type,
              dr.DRUG_NAME AS details, CAST(ROUND({REV},0) AS VARCHAR) AS extra
              FROM [RX CLM] rx LEFT JOIN [DRUG DIM] dr ON rx.DRUG_ID = dr.DRUG_ID
              WHERE rx.PATIENT_ID = ?"""
    q_px = """SELECT px.PROCEDURE_DATE AS event_date, 'Visit/Procedure' AS event_type,
              pd.PROCEDURE_DESCRIPTION AS details, px.PROCEDURE_CODE AS extra
              FROM [PX  CLM] px LEFT JOIN [PX DIM] pd ON px.PROCEDURE_CODE = pd.PROCEDURE_CODE
              WHERE px.PATIENT_ID = ?"""
    q_sx = """SELECT report_date AS event_date, 'Side Effect' AS event_type,
              side_effect_name AS details, severity AS extra
              FROM Fact_Sx WHERE patient_id = ?"""
    q_sw = """SELECT s.switch_date AS event_date, 'Drug Switch' AS event_type,
              (d1.DRUG_NAME + ' -> ' + d2.DRUG_NAME) AS details, s.switch_reason AS extra
              FROM Fact_Drug_Switches s
              LEFT JOIN [DRUG DIM] d1 ON s.from_drug_id = d1.DRUG_ID
              LEFT JOIN [DRUG DIM] d2 ON s.to_drug_id = d2.DRUG_ID
              WHERE s.patient_id = ?"""
    dfs = []
    for q in (q_dx, q_rx, q_px, q_sx, q_sw):
        try:
            dfs.append(db.run_query(q, pid))
        except Exception:
            pass
    out = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    if not out.empty:
        out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce")
        out = out.dropna(subset=["event_date"]).sort_values("event_date").reset_index(drop=True)
    return out


def get_patient_cost_series(patient_id):
    df = db.run_query(f"""
        SELECT rx.RX_FILL_DATE AS event_date, dr.DRUG_NAME AS brand_name,
               {REV} AS sales_revenue_usd, rx.PATIENT_PAY AS patient_out_of_pocket_cost
        FROM [RX CLM] rx LEFT JOIN [DRUG DIM] dr ON rx.DRUG_ID = dr.DRUG_ID
        WHERE rx.PATIENT_ID = ? AND rx.RX_FILL_DATE IS NOT NULL
        ORDER BY rx.RX_FILL_DATE""", [int(patient_id)])
    if not df.empty:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    return df


# ═══════════════════════════════════════════════════
# DIAGNOSIS INTELLIGENCE
# ═══════════════════════════════════════════════════
def get_diagnosis_delay_by_region(d1=None, d2=None):
    """Avg days from first diagnosis to first prescription (time-to-treatment) by state."""
    w, p = _rng("fdx.first_dx", d1, d2)
    df = db.run_query(f"""
        SELECT p.PATIENT_STATE AS state,
               AVG(CAST(DATEDIFF(DAY, fdx.first_dx, frx.first_rx) AS FLOAT)) AS avg_delay_days
        FROM [PTNT DIM] p
        JOIN (SELECT PATIENT_ID, MIN(SERVICE_DATE) AS first_dx FROM [DX CLM] GROUP BY PATIENT_ID) fdx
             ON p.PATIENT_ID = fdx.PATIENT_ID
        JOIN (SELECT PATIENT_ID, MIN(RX_FILL_DATE) AS first_rx FROM [RX CLM] GROUP BY PATIENT_ID) frx
             ON p.PATIENT_ID = frx.PATIENT_ID
        WHERE frx.first_rx >= fdx.first_dx{w}
        GROUP BY p.PATIENT_STATE
        HAVING COUNT(*) > 3
        ORDER BY avg_delay_days DESC""", p)
    if not df.empty:
        df["avg_delay_days"] = df["avg_delay_days"].round(0)
    return df


def get_common_misdiagnoses(d1=None, d2=None, state=None):
    """Repurposed: top diagnoses recorded across the population (definitive codes)."""
    w, p = _rng("d.SERVICE_DATE", d1, d2)
    sw = ""
    if state and state != "All":
        sw = " AND d.PATIENT_ID IN (SELECT PATIENT_ID FROM [PTNT DIM] WHERE PATIENT_STATE = ?)"
        p = p + [state]
    return db.run_query(f"""
        SELECT TOP 10 dd.DIAGNOSIS_DESCRIPTION AS disease_name,
               COUNT(*) AS misdiagnosis_count
        FROM [DX CLM] d JOIN [DX DIM] dd ON d.DIAGNOSIS_CODE = dd.DIAGNOSIS_CODE
        WHERE d.DIAGNOSIS_CODE NOT IN ({SYMPTOM_ICD}){w}{sw}
        GROUP BY dd.DIAGNOSIS_DESCRIPTION ORDER BY misdiagnosis_count DESC""", p)


def get_undiagnosed_suspects(d1=None, d2=None):
    w, p = _rng("SERVICE_DATE", d1, d2)
    ow, op = _rng("d.SERVICE_DATE", d1, d2)
    return db.run_query(f"""
        SELECT CAST(d.PATIENT_ID AS BIGINT) AS patient_id,
               (2025 - p.PATIENT_BIRTH_YEAR) AS age,
               CASE p.PATIENT_GENDER WHEN 1 THEN 'M' WHEN 2 THEN 'F' ELSE '-' END AS gender,
               p.PATIENT_STATE AS state,
               STRING_AGG(dd.DIAGNOSIS_DESCRIPTION, ', ') AS symptom_signals,
               COUNT(DISTINCT d.DIAGNOSIS_CODE) AS signal_count
        FROM [DX CLM] d
        JOIN [DX DIM] dd ON d.DIAGNOSIS_CODE = dd.DIAGNOSIS_CODE
        JOIN [PTNT DIM] p ON d.PATIENT_ID = p.PATIENT_ID
        WHERE d.PATIENT_ID IN (
            SELECT PATIENT_ID FROM [DX CLM] WHERE 1=1{w} GROUP BY PATIENT_ID
            HAVING SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 1 ELSE 0 END) >= 1
               AND SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 0 ELSE 1 END) = 0){ow}
        GROUP BY d.PATIENT_ID, p.PATIENT_BIRTH_YEAR, p.PATIENT_GENDER, p.PATIENT_STATE
        ORDER BY signal_count DESC, patient_id""", p + op)


def get_undiagnosed_by_region(d1=None, d2=None):
    w, p = _rng("SERVICE_DATE", d1, d2)
    return db.run_query(f"""
        SELECT p.PATIENT_STATE AS state, COUNT(*) AS suspect_count
        FROM [PTNT DIM] p
        JOIN (SELECT PATIENT_ID FROM [DX CLM] WHERE 1=1{w} GROUP BY PATIENT_ID
              HAVING SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 1 ELSE 0 END) >= 1
                 AND SUM(CASE WHEN DIAGNOSIS_CODE IN ({SYMPTOM_ICD}) THEN 0 ELSE 1 END) = 0) s
             ON p.PATIENT_ID = s.PATIENT_ID
        GROUP BY p.PATIENT_STATE ORDER BY suspect_count DESC""", p)


def get_untreated_by_region(d1=None, d2=None):
    """Count of diagnosed-but-untreated epilepsy patients per state (for the map/bar)."""
    w, p = _rng("d.SERVICE_DATE", d1, d2)
    return db.run_query(f"""
        SELECT p.PATIENT_STATE AS state, COUNT(DISTINCT d.PATIENT_ID) AS untreated_count
        FROM [DX CLM] d JOIN [PTNT DIM] p ON d.PATIENT_ID = p.PATIENT_ID
        WHERE d.DIAGNOSIS_CODE IN ({RA_ICD}){w}
          AND d.PATIENT_ID NOT IN (
              SELECT PATIENT_ID FROM [RX CLM]
              WHERE DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC})))
        GROUP BY p.PATIENT_STATE ORDER BY untreated_count DESC""", p)


def get_untreated_rare_patients(d1=None, d2=None):
    """Diagnosed with epilepsy but never filled an ASM drug (direct revenue opportunity)."""
    w, p = _rng("d.SERVICE_DATE", d1, d2)
    return db.run_query(f"""
        SELECT CAST(d.PATIENT_ID AS BIGINT) AS patient_id,
               (2025 - p.PATIENT_BIRTH_YEAR) AS age,
               CASE p.PATIENT_GENDER WHEN 1 THEN 'M' WHEN 2 THEN 'F' ELSE '-' END AS gender,
               p.PATIENT_STATE AS state, MIN(d.SERVICE_DATE) AS diagnosed_on,
               'Rheumatoid arthritis (painful joint inflammation)' AS diagnosis
        FROM [DX CLM] d JOIN [PTNT DIM] p ON d.PATIENT_ID = p.PATIENT_ID
        WHERE d.DIAGNOSIS_CODE IN ({RA_ICD}){w}
          AND d.PATIENT_ID NOT IN (
              SELECT PATIENT_ID FROM [RX CLM]
              WHERE DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC})))
        GROUP BY d.PATIENT_ID, p.PATIENT_BIRTH_YEAR, p.PATIENT_GENDER, p.PATIENT_STATE
        ORDER BY MIN(d.SERVICE_DATE)""", p)


# ═══════════════════════════════════════════════════
# TREATMENT & ADHERENCE
# ═══════════════════════════════════════════════════
def get_treatment_funnel(d1=None, d2=None):
    dx_w, dx_p = _rng("SERVICE_DATE", d1, d2)
    rx_w, rx_p = _rng("RX_FILL_DATE", d1, d2)
    diagnosed = int(db.scalar(f"SELECT COUNT(DISTINCT PATIENT_ID) FROM [DX CLM] WHERE 1=1{dx_w}", dx_p))
    treated = int(db.scalar(f"SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM] WHERE 1=1{rx_w}", rx_p))
    if d2:
        adherent = int(db.scalar(f"""
            SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM]
            WHERE RX_FILL_DATE BETWEEN DATEADD(DAY, -{ADH_WINDOW}, ?) AND ?""", [d2, d2]))
    else:
        adherent = int(db.scalar(f"""
            SELECT COUNT(DISTINCT PATIENT_ID) FROM [RX CLM]
            WHERE RX_FILL_DATE >= DATEADD(DAY, -{ADH_WINDOW}, {_MAXD})"""))
    return {"Diagnosed": diagnosed, "Treated": treated, "Adherent": adherent}


def get_adherence_breakdown():
    """Active / Lapsed / Discontinued, derived from days since each patient's last fill."""
    return db.run_query(f"""
        WITH last_fill AS (
            SELECT PATIENT_ID, DATEDIFF(DAY, MAX(RX_FILL_DATE), {_MAXD}) AS days_since
            FROM [RX CLM] GROUP BY PATIENT_ID)
        SELECT CASE WHEN days_since <= {ADH_WINDOW} THEN 'Active'
                    WHEN days_since <= 240 THEN 'Lapsed'
                    ELSE 'Discontinued' END AS adherence_status,
               COUNT(*) AS count
        FROM last_fill GROUP BY CASE WHEN days_since <= {ADH_WINDOW} THEN 'Active'
                    WHEN days_since <= 240 THEN 'Lapsed' ELSE 'Discontinued' END""")


def get_discontinuation_by_reason(d1=None, d2=None):
    w, p = _rng("switch_date", d1, d2)
    return db.run_query(f"""
        SELECT switch_reason AS reason, COUNT(*) AS count
        FROM Fact_Drug_Switches WHERE 1=1{w} GROUP BY switch_reason ORDER BY count DESC""", p)


def get_discontinued_our_drug(d1=None, d2=None):
    """Patients who switched AWAY from an EISAI (our) drug — churn we want to win back."""
    w, p = _rng("s.switch_date", d1, d2)
    return db.run_query(f"""
        SELECT CAST(s.patient_id AS BIGINT) AS patient_id, p.PATIENT_STATE AS state,
               d1.DRUG_NAME AS from_drug, d2.DRUG_NAME AS to_drug, s.switch_reason, s.switch_date
        FROM Fact_Drug_Switches s
        JOIN [DRUG DIM] d1 ON s.from_drug_id = d1.DRUG_ID
        JOIN [DRUG DIM] d2 ON s.to_drug_id = d2.DRUG_ID
        LEFT JOIN [PTNT DIM] p ON s.patient_id = p.PATIENT_ID
        WHERE d1.MANUFACTURER = '{OUR_MFR}'{w}
        ORDER BY s.switch_date DESC""", p)


def get_switch_flow(d1=None, d2=None):
    w, p = _rng("s.switch_date", d1, d2)
    return db.run_query(f"""
        SELECT d1.DRUG_NAME AS source, d2.DRUG_NAME AS target,
               s.switch_reason, COUNT(*) AS value
        FROM Fact_Drug_Switches s
        JOIN [DRUG DIM] d1 ON s.from_drug_id = d1.DRUG_ID
        JOIN [DRUG DIM] d2 ON s.to_drug_id = d2.DRUG_ID
        WHERE 1=1{w}
        GROUP BY d1.DRUG_NAME, d2.DRUG_NAME, s.switch_reason""", p)


def get_switch_patients(from_drug=None, to_drug=None, reason=None, state=None):
    """Patient-level drug-switch drill-down (patient_id only)."""
    sql = """
        SELECT CAST(s.patient_id AS BIGINT) AS patient_id,
               (2025 - p.PATIENT_BIRTH_YEAR) AS age,
               CASE p.PATIENT_GENDER WHEN 1 THEN 'M' WHEN 2 THEN 'F' ELSE '-' END AS gender,
               p.PATIENT_STATE AS state, d1.DRUG_NAME AS from_drug, d2.DRUG_NAME AS to_drug,
               s.switch_reason, s.switch_date
        FROM Fact_Drug_Switches s
        JOIN [PTNT DIM] p ON s.patient_id = p.PATIENT_ID
        JOIN [DRUG DIM] d1 ON s.from_drug_id = d1.DRUG_ID
        JOIN [DRUG DIM] d2 ON s.to_drug_id = d2.DRUG_ID
        WHERE 1=1"""
    params = []
    if from_drug and from_drug != "All":
        sql += " AND d1.DRUG_NAME = ?"; params.append(from_drug)
    if to_drug and to_drug != "All":
        sql += " AND d2.DRUG_NAME = ?"; params.append(to_drug)
    if reason and reason != "All":
        sql += " AND s.switch_reason = ?"; params.append(reason)
    if state and state != "All":
        sql += " AND p.PATIENT_STATE = ?"; params.append(state)
    sql += " ORDER BY s.switch_date DESC"
    return db.run_query(sql, params)


# ═══════════════════════════════════════════════════
# PHYSICIAN & CENTERS
# ═══════════════════════════════════════════════════
def get_physician_performance(d1=None, d2=None):
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    # "active" anchored to the end of the selected window (or data max if no filter)
    anchor = "?" if d2 else "m.maxd"
    anchor_p = [d2, d2] if d2 else []
    return db.run_query(f"""
        SELECT TOP 200 ph.PRACTITIONER_ID AS practitioner_id,
               (ph.FIRST_NAME + ' ' + ph.LAST_NAME) AS doctor_name,
               ph.SPECIALTY_DESCRIPTION AS specialty, ph.STATE AS region, ph.CITY AS clinic_name,
               COUNT(DISTINCT rx.PATIENT_ID) AS patient_volume,
               ROUND(SUM({REV}),0) AS total_revenue,
               COUNT(DISTINCT CASE WHEN rx.RX_FILL_DATE >= DATEADD(DAY,-{ADH_WINDOW}, {anchor}) THEN rx.PATIENT_ID END) AS active_patients,
               COUNT(DISTINCT CASE WHEN rx.RX_FILL_DATE < DATEADD(DAY,-{ADH_WINDOW}, {anchor}) THEN rx.PATIENT_ID END) AS discontinued
        FROM [RX CLM] rx
        JOIN [PHSY DIM] ph ON rx.PRACTITIONER_ID = ph.PRACTITIONER_ID
        CROSS JOIN (SELECT MAX(RX_FILL_DATE) AS maxd FROM [RX CLM]) m
        WHERE 1=1{w}
        GROUP BY ph.PRACTITIONER_ID, ph.FIRST_NAME, ph.LAST_NAME, ph.SPECIALTY_DESCRIPTION, ph.STATE, ph.CITY
        ORDER BY patient_volume DESC""", anchor_p + p)


def get_physician_manufacturer_mix(d1=None, d2=None):
    """Per doctor, how much of their prescribing (fills & revenue) comes from each
    drug manufacturer — the commercial 'loyalty mix' (EISAI vs competitors)."""
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    return db.run_query(f"""
        SELECT ph.PRACTITIONER_ID AS practitioner_id,
               (ph.FIRST_NAME + ' ' + ph.LAST_NAME) AS doctor_name,
               COALESCE(dd.MANUFACTURER, 'UNKNOWN') AS manufacturer,
               COUNT(*) AS fills, ROUND(SUM({REV}),0) AS revenue
        FROM [RX CLM] rx
        JOIN [PHSY DIM] ph ON rx.PRACTITIONER_ID = ph.PRACTITIONER_ID
        JOIN [DRUG DIM] dd ON rx.DRUG_ID = dd.DRUG_ID
        WHERE 1=1{w}
        GROUP BY ph.PRACTITIONER_ID, ph.FIRST_NAME, ph.LAST_NAME, dd.MANUFACTURER""", p)


def get_doctor_drugs(practitioner_id, d1=None, d2=None):
    """What one doctor prescribes: drug, fills, patients, revenue."""
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    return db.run_query(f"""
        SELECT TOP 10 d.DRUG_NAME AS drug, COUNT(*) AS fills,
               COUNT(DISTINCT rx.PATIENT_ID) AS patients,
               ROUND(SUM({REV}),0) AS revenue
        FROM [RX CLM] rx JOIN [DRUG DIM] d ON rx.DRUG_ID = d.DRUG_ID
        WHERE rx.PRACTITIONER_ID = ?{w}
        GROUP BY d.DRUG_NAME ORDER BY revenue DESC""", [int(practitioner_id)] + p)


def get_rare_disease_centers(d1=None, d2=None):
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    return db.run_query(f"""
        SELECT TOP 50 ph.CITY AS clinic_name, (ph.FIRST_NAME + ' ' + ph.LAST_NAME) AS doctor_name,
               ph.STATE AS region, COUNT(DISTINCT rx.PATIENT_ID) AS rare_patients_treated,
               ROUND(SUM({REV}),0) AS revenue
        FROM [RX CLM] rx JOIN [PHSY DIM] ph ON rx.PRACTITIONER_ID = ph.PRACTITIONER_ID
        WHERE rx.DRUG_ID IN (SELECT DRUG_ID FROM [DRUG DIM] WHERE BB_USC_CODE IN ({RA_USC})){w}
        GROUP BY ph.PRACTITIONER_ID, ph.CITY, ph.FIRST_NAME, ph.LAST_NAME, ph.STATE
        ORDER BY rare_patients_treated DESC""", p)


# ═══════════════════════════════════════════════════
# MARKET ACCESS & PAYER
# ═══════════════════════════════════════════════════
def _payer_where(payer):
    return (" AND payer_name = ?", [payer]) if payer and payer != "All" else ("", [])


def get_payer_list():
    return db.run_query(
        "SELECT DISTINCT payer_name FROM Fact_Insurance_Claims ORDER BY payer_name")["payer_name"].tolist()


def get_payer_denial_rates(payer=None, d1=None, d2=None):
    w, p = _payer_where(payer)
    dw, dp = _rng("claim_date", d1, d2)
    return db.run_query(f"""
        SELECT payer_name, COUNT(*) AS total_claims,
               SUM(CASE WHEN status='Denied' THEN 1 ELSE 0 END) AS denied,
               ROUND(100.0*SUM(CASE WHEN status='Denied' THEN 1 ELSE 0 END)/COUNT(*),1) AS denial_pct,
               ROUND(AVG(CAST(prior_auth_days AS FLOAT)),0) AS avg_auth_days,
               ROUND(SUM(claim_amount),0) AS total_billed,
               ROUND(SUM(approved_amount),0) AS total_paid,
               ROUND(SUM(CASE WHEN status='Denied' THEN claim_amount ELSE 0 END),0) AS denied_value
        FROM Fact_Insurance_Claims WHERE 1=1{w}{dw}
        GROUP BY payer_name ORDER BY denial_pct DESC""", p + dp)


def get_denial_reasons(payer=None, d1=None, d2=None, asm_only=False):
    w, p = _payer_where(payer)
    dw, dp = _rng("claim_date", d1, d2)
    aw = f" AND drug_id IN {_ASM_DRUGS}" if asm_only else ""
    return db.run_query(f"""
        SELECT denial_reason, COUNT(*) AS count
        FROM Fact_Insurance_Claims WHERE status='Denied' AND denial_reason IS NOT NULL{w}{dw}{aw}
        GROUP BY denial_reason ORDER BY count DESC""", p + dp)


def get_denied_claims_patients(payer=None, d1=None, d2=None, asm_only=False):
    w, p = _payer_where(payer)
    dw, dp = _rng("fic.claim_date", d1, d2)
    aw = f" AND fic.drug_id IN {_ASM_DRUGS}" if asm_only else ""
    return db.run_query(f"""
        SELECT CAST(fic.patient_id AS BIGINT) AS patient_id, fic.payer_name, fic.claim_amount,
               fic.denial_reason, fic.prior_auth_days, d.DRUG_NAME AS brand_name
        FROM Fact_Insurance_Claims fic LEFT JOIN [DRUG DIM] d ON fic.drug_id = d.DRUG_ID
        WHERE fic.status='Denied'{w}{dw}{aw} ORDER BY fic.claim_amount DESC""", p + dp)


def get_auth_delay_by_state(payer=None, d1=None, d2=None):
    w, p = _payer_where(payer)
    dw, dp = _rng("claim_date", d1, d2)
    return db.run_query(f"""
        SELECT region AS state, ROUND(AVG(CAST(prior_auth_days AS FLOAT)),0) AS avg_auth_days,
               COUNT(*) AS total_claims
        FROM Fact_Insurance_Claims WHERE 1=1{w}{dw}
        GROUP BY region ORDER BY avg_auth_days DESC""", p + dp)


# ═══════════════════════════════════════════════════
# GEO & MARKETING ROI
# ═══════════════════════════════════════════════════
def get_geo_combined(d1=None, d2=None):
    w, p = _rng("rx.RX_FILL_DATE", d1, d2)
    rev = db.run_query(f"""
        SELECT p.PATIENT_STATE AS state, ROUND(SUM({REV}),0) AS total_revenue,
               COUNT(DISTINCT rx.PATIENT_ID) AS treated_patients
        FROM [RX CLM] rx JOIN [PTNT DIM] p ON rx.PATIENT_ID = p.PATIENT_ID
        WHERE 1=1{w}
        GROUP BY p.PATIENT_STATE""", p)
    mw, mp = _rng("campaign_start", d1, d2)
    mkt = db.run_query(f"""
        SELECT region AS state, SUM(spend_amount) AS total_marketing
        FROM Fact_Marketing WHERE 1=1{mw} GROUP BY region""", mp)
    merged = pd.merge(rev, mkt, on="state", how="outer").fillna(0)
    if not merged.empty:
        merged["roi"] = (merged["total_revenue"] / merged["total_marketing"].replace(0, 1)).round(1)
    return merged.sort_values("total_revenue", ascending=False)
