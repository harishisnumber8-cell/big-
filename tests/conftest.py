import os
from pathlib import Path

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL")
SQL = Path(__file__).resolve().parent.parent / "db"
TABLES = "alert, customer, event_zip_impact, warning, storm_report, hail_cell, storm_event, county, zip_area, structure, grid_cell"


def box(x0, y0, x1, y1):
    return f"ST_Multi(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 4326))"


@pytest.fixture
def db():
    """A fresh schema in DATABASE_URL (tables are dropped, so use a scratch database)."""
    if not DB:
        pytest.skip("set DATABASE_URL to a PostGIS database")
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute(f"DROP TABLE IF EXISTS {TABLES} CASCADE")
        c.execute((SQL / "schema.sql").read_text())
        yield c
