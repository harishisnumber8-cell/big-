"""Phase 3: heads-up and summary alerts reach the right customers, once each."""
from datetime import timedelta

import pytest

from conftest import SQL, box
from storm import alerts, events, mrms
from test_storm import T0


@pytest.fixture
def storm(db):
    """ZIP 72201 with 30 houses in one cell, hail growing over them in two scans."""
    db.execute(f"INSERT INTO zip_area (zip, geom) VALUES ('72201', {box(-92.30, 34.70, -92.20, 34.80)})")
    for i in range(30):
        db.execute("INSERT INTO structure (id, geom, zip) VALUES (%s, ST_Point(-92.255, 34.755, 4326), '72201')", (i,))
    db.execute((SQL / "build_counts.sql").read_text())
    mrms.store(db, T0, [(-9226, 3475, 1.2)])
    mrms.store(db, T0 + timedelta(minutes=30), [(-9226, 3475, 2.1)])
    return db


def customer(db, zips, phone="+15015550100", created=T0 - timedelta(days=1), **kw):
    cols = {"name": "Roofer", "email": "r@example.com", "phone": phone, "zips": zips, "created_at": created, **kw}
    return db.execute(f"INSERT INTO customer ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                      list(cols.values())).fetchone()[0]


def run(db):
    sent = []
    alerts.run(db, send=lambda channel, to, subject, body: sent.append((channel, to, body)))
    return sent


def test_headsup_then_summary_once(storm):
    customer(storm, ["72201"])
    events.build(storm, T0 + timedelta(minutes=40))  # storm still active
    [(channel, to, body)] = run(storm)
    assert (channel, to) == ("sms", "+15015550100") and "2.10 in" in body and "72201" in body
    assert run(storm) == []                          # no repeat heads-up

    events.build(storm, T0 + timedelta(hours=3))     # storm closes
    sent = run(storm)
    assert sorted(c for c, _, _ in sent) == ["email", "sms"]
    email = next(b for c, _, b in sent if c == "email")
    assert "72201" in email and "30" in email and "/c/" in email
    assert run(storm) == []
    assert storm.execute("SELECT count(*) FROM alert WHERE status = 'sent'").fetchone()[0] == 3


def test_no_alert_outside_area_below_threshold_or_before_signup(storm):
    customer(storm, ["72999"])                                  # different ZIP
    customer(storm, ["72201"], min_hail_in=2.5)                 # hail too small for them
    customer(storm, ["72201"], min_buildings=31)                # too few buildings
    customer(storm, ["72201"], created=T0 + timedelta(hours=1)) # signed up after the storm
    events.build(storm, T0 + timedelta(hours=3))
    assert run(storm) == []


def test_failed_send_is_recorded_not_retried(storm):
    customer(storm, ["72201"], phone=None)
    events.build(storm, T0 + timedelta(hours=3))

    def boom(*_):
        raise RuntimeError("SMTP down")
    alerts.run(storm, send=boom)
    assert storm.execute("SELECT channel, status, error FROM alert").fetchall() == [("email", "failed", "SMTP down")]
