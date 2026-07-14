"""Database access layer — SQL Server (SSMS) ONLY.

The single source of truth is the SSMS database configured in .env
(DB_SERVER / DB_NAME / DB_USERNAME / DB_PASSWORD). There is no dummy/SQLite
fallback — all data is read live from SSMS, so anything added in SSMS shows up
in the app the moment you refresh.

Real table names contain spaces (e.g. "DRUG DIM", "PX  CLM", "PRC ROLES"),
so always wrap identifiers in [brackets] when writing queries: [DRUG DIM].
"""
import warnings
import pandas as pd
import config

# pandas prints a UserWarning when reading via a raw pyodbc connection instead of
# SQLAlchemy. The pyodbc path is fully supported here; silence the cosmetic warning.
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")


# Marker used by app.py's self-heal guard to detect a stale in-memory module
# after this file changes. Bump the name/value when db.py changes materially.
TIMESTAMPS_UTC = True


def dialect():
    return "sqlserver"


def _connection_string():
    if config.DB_TRUSTED_CONNECTION:
        auth = "Trusted_Connection=yes;"
    else:
        auth = f"UID={config.DB_USERNAME};PWD={config.DB_PASSWORD};"
    return auth


def get_connection():
    import pyodbc  # imported lazily so a missing driver fails with a clear message
    auth = _connection_string()
    last_err = None
    for driver in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
        try:
            return pyodbc.connect(
                f"DRIVER={{{driver}}};SERVER={config.DB_SERVER};DATABASE={config.DB_NAME};"
                f"{auth}TrustServerCertificate=yes;Encrypt=no;", timeout=10,
            )
        except Exception as e:  # try the next installed driver
            last_err = e
    raise ConnectionError(
        f"Could not connect to SQL Server '{config.DB_SERVER}/{config.DB_NAME}'. "
        f"Last error: {last_err}"
    )


def connection_status():
    """Returns (label, ok, detail) for the sidebar."""
    label = f"SQL Server · {config.DB_SERVER}/{config.DB_NAME}"
    try:
        conn = get_connection()
        conn.close()
        return (label, True, "Connected")
    except Exception as e:
        return (label, False, str(e))


def run_query(sql, params=None):
    """Read query → DataFrame."""
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or [])
    finally:
        conn.close()


def execute(sql, params=None):
    """Write statement (INSERT/UPDATE/DELETE/DDL)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        conn.commit()
    finally:
        conn.close()


def scalar(sql, params=None, default=0):
    df = run_query(sql, params)
    if df.empty or df.iloc[0, 0] is None:
        return default
    return df.iloc[0, 0]


def list_tables():
    """All base tables in the connected database (for verification)."""
    return run_query(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")


# ═══════════════════════════════════════════════════
# T-SQL dialect helpers (kept so query code stays readable)
# ═══════════════════════════════════════════════════
def sql_month(col):
    return f"FORMAT(CAST({col} AS DATE), 'yyyy-MM')"


def sql_datediff_days(start_col, end_col):
    return f"DATEDIFF(DAY, CAST({start_col} AS DATE), CAST({end_col} AS DATE))"


def sql_group_concat(col):
    return f"STRING_AGG({col}, ', ')"


def sql_now():
    # UTC everywhere — all human/agent action timestamps are stored in UTC.
    return "SYSUTCDATETIME()"


def sql_days_ago(days):
    return f"DATEADD(DAY, -{int(days)}, SYSUTCDATETIME())"


# ═══════════════════════════════════════════════════
# Agent operational tables (auto-created in the SSMS database)
# ═══════════════════════════════════════════════════
def ensure_agent_tables():
    stmts = [
        """IF OBJECT_ID('Agent_Pending_Actions','U') IS NULL
           CREATE TABLE Agent_Pending_Actions (
               action_id INT IDENTITY(1,1) PRIMARY KEY,
               action_type NVARCHAR(60), patient_id BIGINT, details NVARCHAR(1000),
               reason NVARCHAR(1000), expected_impact NVARCHAR(500),
               estimated_value_usd DECIMAL(14,2) DEFAULT 0,
               status NVARCHAR(20) DEFAULT 'Pending', proposed_by NVARCHAR(60),
               created_at DATETIME2 DEFAULT SYSUTCDATETIME(), resolved_at DATETIME2 NULL)""",
        """IF OBJECT_ID('Agent_Notifications','U') IS NULL
           CREATE TABLE Agent_Notifications (
               notification_id INT IDENTITY(1,1) PRIMARY KEY,
               patient_id BIGINT, channel NVARCHAR(20), recipient NVARCHAR(200),
               subject NVARCHAR(300), body NVARCHAR(2000),
               status NVARCHAR(20), sent_at DATETIME2 DEFAULT SYSUTCDATETIME())""",
        """IF OBJECT_ID('Agent_Appointments','U') IS NULL
           CREATE TABLE Agent_Appointments (
               appointment_id INT IDENTITY(1,1) PRIMARY KEY,
               patient_id BIGINT, appointment_type NVARCHAR(120),
               appointment_date DATE, notes NVARCHAR(1000),
               status NVARCHAR(20) DEFAULT 'Scheduled',
               created_at DATETIME2 DEFAULT SYSUTCDATETIME())""",
        """IF OBJECT_ID('Agent_Audit_Log','U') IS NULL
           CREATE TABLE Agent_Audit_Log (
               log_id INT IDENTITY(1,1) PRIMARY KEY,
               actor NVARCHAR(60), action NVARCHAR(1000), status NVARCHAR(20),
               related_patient_id BIGINT NULL,
               created_at DATETIME2 DEFAULT SYSUTCDATETIME())""",
        """IF OBJECT_ID('Agent_Business_Actions','U') IS NULL
           CREATE TABLE Agent_Business_Actions (
               business_action_id INT IDENTITY(1,1) PRIMARY KEY,
               action_type NVARCHAR(60), target_type NVARCHAR(30), target NVARCHAR(120),
               details NVARCHAR(1000), reason NVARCHAR(1000), expected_impact NVARCHAR(500),
               estimated_value_usd DECIMAL(14,2) DEFAULT 0,
               status NVARCHAR(20) DEFAULT 'Pending', proposed_by NVARCHAR(60),
               result NVARCHAR(500) NULL,
               created_at DATETIME2 DEFAULT SYSUTCDATETIME(), resolved_at DATETIME2 NULL)""",
        """IF OBJECT_ID('Agent_Chat_History','U') IS NULL
           CREATE TABLE Agent_Chat_History (
               chat_id INT IDENTITY(1,1) PRIMARY KEY,
               role NVARCHAR(20), content NVARCHAR(MAX),
               created_at DATETIME2 DEFAULT SYSUTCDATETIME())""",
    ]
    conn = get_connection()
    try:
        cur = conn.cursor()
        for s in stmts:
            cur.execute(s)
        conn.commit()
    finally:
        conn.close()
    _ensure_column("Agent_Pending_Actions", "estimated_value_usd", "DECIMAL(14,2) DEFAULT 0")
    _ensure_column("Agent_Business_Actions", "estimated_value_usd", "DECIMAL(14,2) DEFAULT 0")


def _ensure_column(table, column, col_def):
    try:
        run_query(f"SELECT {column} FROM {table} WHERE 1=0")
    except Exception:
        try:
            execute(f"ALTER TABLE {table} ADD {column} {col_def}")
        except Exception:
            pass


def audit(actor, action, status="Success", patient_id=None):
    # created_at written explicitly in UTC (existing tables may still default to local time)
    execute(
        "INSERT INTO Agent_Audit_Log (actor, action, status, related_patient_id, created_at) "
        "VALUES (?,?,?,?, SYSUTCDATETIME())",
        [actor, action, status, patient_id],
    )


# ═══════════════════════════════════════════════════
# Chat history — persisted in the Analyser Agent database
# ═══════════════════════════════════════════════════
def save_chat(role, content):
    execute("INSERT INTO Agent_Chat_History (role, content, created_at) VALUES (?,?, SYSUTCDATETIME())",
            [role, str(content)])


def load_chat(limit=30):
    """Last N chat turns, oldest first."""
    df = run_query(
        f"SELECT * FROM (SELECT TOP {int(limit)} chat_id, role, content "
        f"FROM Agent_Chat_History ORDER BY chat_id DESC) t ORDER BY chat_id ASC")
    return df
