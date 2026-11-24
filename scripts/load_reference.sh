#!/usr/bin/env bash
# Download Arkansas reference data, load it into PostGIS, build structure counts, run the gate check.
# Needs: curl, psql, ogr2ogr (GDAL). Usage: DATABASE_URL=... STRUCTURES_URL=... scripts/load_reference.sh
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL, e.g. postgresql://storm:storm@localhost:5432/storm}"
: "${STRUCTURES_URL:?set STRUCTURES_URL to the FEMA USA Structures Arkansas zip (see README)}"
DATA="${DATA_DIR:-data}"
mkdir -p "$DATA"
cd "$(dirname "$0")/.."

fetch() { [ -s "$DATA/$2" ] || curl -fL --retry 3 -o "$DATA/$2" "$1"; }
fetch https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip county.zip
fetch https://www2.census.gov/geo/tiger/TIGER2024/ZCTA520/tl_2024_us_zcta520.zip zcta.zip
fetch "$STRUCTURES_URL" structures.zip

sql() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$1"; }
ogr() { ogr2ogr -f PostgreSQL "PG:$DATABASE_URL" "$1" -nln "$2" -t_srs EPSG:4326 \
          -nlt PROMOTE_TO_MULTI -lco GEOMETRY_NAME=geom -overwrite "${@:3}"; }

sql db/schema.sql
ogr "/vsizip/$DATA/county.zip" raw_county -where "STATEFP = '05'"
ogr "/vsizip/$DATA/zcta.zip" raw_zcta -spat -94.62 33.00 -89.64 36.50 -spat_srs EPSG:4326
ogr "/vsizip/$DATA/structures.zip" raw_structure
sql db/load_reference.sql
sql db/build_counts.sql
sql db/check_counts.sql
echo "Reference data loaded and counts check passed."
