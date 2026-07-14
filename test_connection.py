"""Standalone SSMS connection tester.

Run this FIRST (before the app) to confirm the app can reach your SQL Server:

    python test_connection.py

It verifies: driver present -> can connect -> can read your tables ->
can create the agent operational tables. If every step shows OK, the app
will connect too.
"""
import config
import db


def main():
    print("=" * 60)
    print("SSMS CONNECTION TEST")
    print("=" * 60)
    print(f"Server   : {config.DB_SERVER}")
    print(f"Database : {config.DB_NAME}")
    print(f"Auth     : {'Windows (trusted)' if config.DB_TRUSTED_CONNECTION else 'SQL login (' + config.DB_USERNAME + ')'}")
    print("-" * 60)

    # 1) installed ODBC drivers
    try:
        import pyodbc
        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        print(f"[1] ODBC drivers found : {drivers or 'NONE — install ODBC Driver 17/18 for SQL Server'}")
    except Exception as e:
        print(f"[1] pyodbc not available: {e}")
        return

    # 2) connect
    try:
        conn = db.get_connection()
        conn.close()
        print("[2] Connection         : OK")
    except Exception as e:
        print(f"[2] Connection         : FAILED\n    {e}")
        print("\nCommon fixes: check server IP/firewall, TCP/IP enabled in SQL Server "
              "Configuration Manager, SQL Server Browser running, and the username/password.")
        return

    # 3) read tables
    try:
        tables = db.list_tables()
        print(f"[3] Tables visible     : {len(tables)}")
        for t in tables["TABLE_NAME"]:
            print(f"       - {t}")
    except Exception as e:
        print(f"[3] Reading tables     : FAILED\n    {e}")
        return

    # 4) quick row counts on a few core tables (bracketed for names with spaces)
    print("[4] Sample row counts  :")
    for t in ["PTNT DIM", "RX CLM", "DRUG DIM", "Fact_Insurance_Claims"]:
        try:
            n = db.scalar(f"SELECT COUNT(*) FROM [{t}]")
            print(f"       [{t}] = {n:,}")
        except Exception as e:
            print(f"       [{t}] -> could not read ({str(e)[:60]})")

    # 5) create agent operational tables
    try:
        db.ensure_agent_tables()
        print("[5] Agent tables       : OK (created / already present)")
    except Exception as e:
        print(f"[5] Agent tables       : FAILED\n    {e}")
        return

    print("-" * 60)
    print("ALL GOOD ✔  The app can connect to your SSMS database.")
    print("=" * 60)


if __name__ == "__main__":
    main()
