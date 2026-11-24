"""Runs the phase 1 SQL on a tiny synthetic Arkansas: 2 counties, 2 ZIPs, 5 buildings."""
import psycopg
import pytest

from conftest import SQL, box


@pytest.fixture
def conn(db):
    # Two side-by-side counties; ZIP 72001 sits in the west one, 72002 straddles both.
    db.execute(f"INSERT INTO county VALUES ('05001', 'West', {box(-92.10, 34.00, -92.00, 34.10)}),"
               f" ('05003', 'East', {box(-92.00, 34.00, -91.90, 34.10)})")
    db.execute(f"INSERT INTO zip_area (zip, geom) VALUES ('72001', {box(-92.10, 34.00, -92.05, 34.10)}),"
               f" ('72002', {box(-92.05, 34.00, -91.90, 34.10)})")
    for i, (x, y, occ) in enumerate([(-92.08, 34.05, "Residential"), (-92.078, 34.051, "Residential"),
                                     (-92.03, 34.02, "Commercial"), (-91.95, 34.08, "Residential"),
                                     (-91.50, 34.50, "Residential")]):  # last one is outside every ZIP
        db.execute("INSERT INTO structure (id, geom, occupancy) VALUES (%s, ST_Point(%s, %s, 4326), %s)",
                   (i, x, y, occ))
    yield db


def test_counts(conn):
    conn.execute((SQL / "build_counts.sql").read_text())
    zips = conn.execute("SELECT zip, building_count, residential_count, county_fips FROM zip_area ORDER BY zip").fetchall()
    assert zips == [("72001", 2, 2, ["05001"]), ("72002", 2, 1, ["05001", "05003"])]

    # The first two buildings share one 0.01 degree cell.
    cells = conn.execute("SELECT building_count FROM grid_cell ORDER BY building_count DESC").fetchall()
    assert [n for (n,) in cells] == [2, 1, 1, 1]
    assert conn.execute("SELECT cell_x, cell_y FROM structure WHERE id = 0").fetchone() == (-9208, 3405)


def test_check_fails_when_too_many_outside_zips(conn):
    conn.execute((SQL / "build_counts.sql").read_text())
    with pytest.raises(psycopg.errors.RaiseException, match="fall inside a ZIP"):
        conn.execute((SQL / "check_counts.sql").read_text())


def test_check_passes(conn):
    conn.execute("DELETE FROM structure WHERE id = 4")
    conn.execute((SQL / "build_counts.sql").read_text())
    conn.execute((SQL / "check_counts.sql").read_text())
