"""Phase 2: MRMS grid parsing, report parsing, storm clustering and per-ZIP scoring."""
from datetime import datetime, timedelta, timezone

import numpy as np

from conftest import SQL, box
from storm import events, mrms, reports

T0 = datetime(2025, 4, 2, 22, 0, tzinfo=timezone.utc)


def test_cells_from_grid():
    mm = np.array([[-3, 0, 0], [10, 0, 50.8], [0, 0, 0]])  # missing, under threshold, 2 in
    # Cell centres from 36.005 N, -92.995 W: [1, 2] is at 35.995 N, -92.975 W.
    assert mrms.cells_from_grid(mm, 36.005, -92.995) == [(-9298, 3599, 2.0)]


def test_valid_time():
    assert mrms.valid_time("CONUS/x/MRMS_MESH_Max_30min_00.50_20250402-223000.grib2.gz") == T0.replace(minute=30)


def test_assign_chains_and_splits():
    owner = {(0, 0): 7}
    new = {(5, 0), (14, 0), (100, 100), (105, 100)}
    out = events.assign(new, owner, radius=10)
    assert out[(5, 0)] == out[(14, 0)] == 7          # (14, 0) reaches the storm through (5, 0)
    assert out[(100, 100)] == out[(105, 100)] < 0    # a separate new storm


def test_parse_lsr_and_alerts():
    lsr = {"features": [
        {"geometry": {"coordinates": [-92.3, 34.7]},
         "properties": {"type": "H", "magnitude": "1.75", "valid": "2025-04-02T22:10:00Z", "wfo": "LZK", "remark": "ping pong"}},
        {"geometry": {"coordinates": [-92.3, 34.7]}, "properties": {"type": "T", "magnitude": "", "valid": "x", "wfo": "LZK"}}]}
    [r] = reports.parse_lsr(lsr)
    assert (r["type"], r["magnitude"], r["lon"]) == ("hail", 1.75, -92.3)
    alerts = {"features": [{"geometry": {"type": "Polygon", "coordinates": [[[-92, 34], [-91, 34], [-91, 35], [-92, 34]]]},
                            "properties": {"id": "a1", "event": "Severe Thunderstorm Warning", "sent": None, "expires": None,
                                           "parameters": {"maxHailSize": ["1.50"], "maxWindGust": ["70 MPH"]}}}]}
    [a] = reports.parse_alerts(alerts)
    assert (a["max_hail_in"], a["max_wind_mph"]) == (1.5, 70.0)


def test_build_and_score(db):
    db.execute(f"INSERT INTO zip_area (zip, geom) VALUES ('72201', {box(-92.30, 34.70, -92.20, 34.80)})")
    # Three houses in one cell, one more 2 cells east.
    for i, (x, y) in enumerate([(-92.255, 34.755)] * 3 + [(-92.235, 34.755)]):
        db.execute("INSERT INTO structure (id, geom, zip, occupancy) VALUES (%s, ST_Point(%s, %s, 4326), '72201', 'Residential')",
                   (i, x, y))
    db.execute((SQL / "build_counts.sql").read_text())

    mrms.store(db, T0, [(-9226, 3475, 1.2)])                        # first scan: 1.2 in over the 3 houses
    mrms.store(db, T0 + timedelta(minutes=30), [(-9226, 3475, 2.1), (-9224, 3475, 1.6)])  # grows and moves east
    mrms.store(db, T0, [(-9000, 3500, 1.0)])                        # unrelated storm far away
    reports.store_reports(db, [{"source_id": "r1", "type": "hail", "magnitude": 1.75, "reported_at": T0 + timedelta(minutes=10),
                                "lon": -92.254, "lat": 34.752, "remarks": None}])

    events.build(db, T0 + timedelta(minutes=40))                    # storms still active: nothing closed
    assert db.execute("SELECT count(*), count(*) FILTER (WHERE closed) FROM storm_event").fetchone() == (2, 0)

    events.build(db, T0 + timedelta(hours=3))
    ev = db.execute("SELECT id, max_hail_in, confidence FROM storm_event WHERE closed ORDER BY max_hail_in DESC").fetchall()
    assert len(ev) == 2 and ev[0][1:] == (2.1, "high") and ev[1][1:] == (1.0, "medium")
    impact = db.execute("SELECT zip, max_hail_in, buildings_ge_1in, buildings_ge_1_5in, buildings_ge_2in, report_count "
                        "FROM event_zip_impact WHERE event_id = %s", (ev[0][0],)).fetchall()
    assert impact == [("72201", 2.1, 4, 4, 3, 1)]
