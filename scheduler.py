"""Autonomous Agent Scheduler.

Runs the Claude-powered autopilot scan on a schedule (default: daily at 08:00).
The scan queries the live database, finds risk & opportunity (untreated patients,
undiagnosed suspects, severe side effects, drop-offs, denied claims) and fills the
human-approval queue in the app's ⚡ Action Center.

Usage:
    python scheduler.py            # run forever, daily at 08:00
    python scheduler.py --once     # run a single scan now and exit
"""
import sys
import time
import schedule

import db
import agent_watchdog as watchdog
from agent import run_autopilot_scan


def scan():
    print("=" * 60)
    print("🤖 Autonomous agent cycle starting...")
    db.ensure_agent_tables()

    # 1) Reflex layer — instant detection; low-risk actions auto-execute unattended
    wd = watchdog.sweep(auto_execute=True)
    found = wd["safety"] + wd["claims"] + wd["untreated"]
    print(f"🐕 Watchdog: {found} detection(s) — {wd['auto_executed']} auto-executed, "
          f"{found - wd['auto_executed']} queued for approval.")
    for m in wd["messages"]:
        print("   -", m)

    # 2) Strategic layer — Claude deep scan (proposes patient + business actions)
    report, proposed = run_autopilot_scan()
    print(report)
    print(f"\n✅ Cycle complete — {proposed} strategic action(s) queued for human approval.")
    print("=" * 60)


if __name__ == "__main__":
    if "--once" in sys.argv:
        scan()
        sys.exit(0)
    schedule.every().day.at("08:00").do(scan)
    print("Scheduler running — autonomous agent scan every day at 08:00 (Ctrl+C to stop).")
    while True:
        schedule.run_pending()
        time.sleep(30)
