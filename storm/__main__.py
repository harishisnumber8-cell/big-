"""Usage:
  python -m storm replay 2025-04-02 [--skip-reports]   load a past UTC day and build its storm events
  python -m storm live                                 ingest the latest data, build events, send alerts (run every 2-5 min)
  python -m storm alerts                               send any alerts that are due
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

import psycopg

from storm import alerts, events, mrms, reports


def main():
    ap = argparse.ArgumentParser(prog="python -m storm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay")
    rp.add_argument("date", help="UTC day, YYYY-MM-DD")
    rp.add_argument("--skip-reports", action="store_true", help="radar only, for networks that block IEM")
    sub.add_parser("live")
    sub.add_parser("alerts")
    args = ap.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if args.cmd == "replay":
            start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            # Every 30-minute file of the 30-minute max covers the whole day without overlap.
            keys = [k for k in mrms.list_keys(start.strftime("%Y%m%d")) if k.endswith(("0000.grib2.gz", "3000.grib2.gz"))]
            for k in keys:
                cells = mrms.read_cells(mrms.fetch(k))
                mrms.store(conn, mrms.valid_time(k), cells)
                print(k.rsplit("/", 1)[1], len(cells), "hail cells")
            if not args.skip_reports:
                reports.store_reports(conn, reports.parse_lsr(reports.fetch_lsr(start, end)))
            events.build(conn, end + events.CLOSE_AFTER + timedelta(hours=1))
        elif args.cmd == "alerts":
            print(alerts.run(conn), "alerts due")
        else:
            now = datetime.now(timezone.utc)
            key = mrms.list_keys(now.strftime("%Y%m%d"))[-1]  # shortcut: just after 00Z today's folder may be empty; fall back to yesterday if that bites
            mrms.store(conn, mrms.valid_time(key), mrms.read_cells(mrms.fetch(key)))
            reports.store_reports(conn, reports.parse_lsr(reports.fetch_lsr(now - timedelta(hours=2), now)))
            reports.store_alerts(conn, reports.parse_alerts(reports.fetch_alerts()))
            events.build(conn, now)
            alerts.run(conn)


if __name__ == "__main__":
    main()
