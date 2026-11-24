# Arkansas storm-damage alerts

Storm alerts for Arkansas contractors. Plan: https://claude.ai/artifact/MsZpZ1fHYijpCa19q9iz8P

## Phase 1: reference data

Loads Arkansas counties, ZIPs (Census ZCTAs) and every building (FEMA USA Structures) into PostGIS, then precomputes building counts per ZIP and per 1 km MRMS grid cell. Later phases count buildings under a hail swath with these tables.

| File | What it does |
| --- | --- |
| `db/schema.sql` | Tables: `county`, `zip_area`, `structure`, `grid_cell` |
| `db/load_reference.sql` | Copies raw downloads into those tables, drops buildings under 400 sq ft |
| `db/build_counts.sql` | Assigns each building to a ZIP, county and grid cell, fills counts |
| `db/check_counts.sql` | Phase 1 gate: counts must add up to the buildings loaded |
| `scripts/load_reference.sh` | Runs the whole load end to end |

### Run it

You need Docker, `psql` and GDAL (`ogr2ogr`). On a Mac: `brew install gdal libpq`.

```sh
docker compose up -d db
export DATABASE_URL=postgresql://storm:storm@localhost:5432/storm
# Arkansas zip from https://gis-fema.hub.arcgis.com/pages/usa-structures
export STRUCTURES_URL='<link to the Arkansas download>'
scripts/load_reference.sh
```

Downloads go to `data/` (the national ZCTA file is about 500 MB) and are reused on later runs.

### Tests

```sh
pip install -r requirements-dev.txt
DATABASE_URL=postgresql://storm:storm@localhost:5432/storm pytest
```

The tests drop and recreate the reference tables, so point them at a scratch database.

## Phase 2: storm pipeline

| File | What it does |
| --- | --- |
| `storm/mrms.py` | Reads NOAA MRMS 30-minute max hail (MESH) and keeps every Arkansas 1 km cell with hail of 0.75 in or more |
| `storm/reports.py` | Ground storm reports (hail, wind, damage) from Iowa Environmental Mesonet, NWS severe thunderstorm and tornado warnings |
| `storm/events.py` | Groups hail and reports into storm events (within ~10 km and 45 min), closes a storm 60 min after its last hail, then scores it per ZIP: max hail, max wind, buildings under 1, 1.5 and 2 in hail |
| `python -m storm` | `replay YYYY-MM-DD` loads a past UTC day; `live` ingests the latest data (run every 2 to 5 minutes from cron) |

Hail cells use the same 0.01 degree ids as `grid_cell`, so buildings under a storm come from a join, not a polygon overlay.

```sh
pip install -r requirements.txt
psql "$DATABASE_URL" -f db/schema.sql   # adds the phase 2 tables
python -m storm replay 2025-04-02
CONTACT_EMAIL=you@example.com python -m storm live   # NWS asks for a contact in the User-Agent
```

A replay closes all of that day's storms at the end of it, so a storm that runs past midnight UTC shows up as two events.
