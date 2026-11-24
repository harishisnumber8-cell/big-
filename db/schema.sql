-- Phase 1 reference tables. Storm and customer tables arrive in later phases.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS county (
  fips text PRIMARY KEY,
  name text NOT NULL,
  geom geometry(MultiPolygon, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS zip_area (
  zip text PRIMARY KEY,
  county_fips text[] NOT NULL DEFAULT '{}',
  geom geometry(MultiPolygon, 4326) NOT NULL,
  building_count int NOT NULL DEFAULT 0,
  residential_count int NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS structure (
  id bigint PRIMARY KEY,
  geom geometry(Point, 4326) NOT NULL,  -- point on the footprint
  footprint_sqft real,
  occupancy text,
  zip text,
  county_fips text,
  cell_x int,  -- MRMS 0.01 degree grid column: floor(lon / 0.01)
  cell_y int   -- MRMS 0.01 degree grid row: floor(lat / 0.01)
);

CREATE TABLE IF NOT EXISTS grid_cell (
  cell_x int,
  cell_y int,
  building_count int NOT NULL,
  residential_count int NOT NULL,
  geom geometry(Polygon, 4326) NOT NULL,
  PRIMARY KEY (cell_x, cell_y)
);

CREATE INDEX IF NOT EXISTS county_geom_idx ON county USING gist (geom);
CREATE INDEX IF NOT EXISTS zip_area_geom_idx ON zip_area USING gist (geom);
CREATE INDEX IF NOT EXISTS structure_geom_idx ON structure USING gist (geom);
CREATE INDEX IF NOT EXISTS structure_zip_idx ON structure (zip);
CREATE INDEX IF NOT EXISTS grid_cell_geom_idx ON grid_cell USING gist (geom);

-- Phase 2: storm observations and events.
CREATE TABLE IF NOT EXISTS storm_event (
  id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL,
  last_at timestamptz NOT NULL,  -- newest hail cell or report in the storm
  closed boolean NOT NULL DEFAULT false,
  max_hail_in real,
  max_wind_mph real,
  confidence text  -- high: a ground hail report backs the radar; medium: radar only
);

-- MRMS MESH 30-minute max, one row per hailed 0.01 degree cell per file (same cell ids as grid_cell).
CREATE TABLE IF NOT EXISTS hail_cell (
  valid_at timestamptz,
  cell_x int,
  cell_y int,
  mesh_in real NOT NULL,
  event_id bigint REFERENCES storm_event,
  PRIMARY KEY (valid_at, cell_x, cell_y)
);

CREATE TABLE IF NOT EXISTS storm_report (
  source_id text PRIMARY KEY,
  type text NOT NULL,  -- hail (inches), wind (mph), damage (no magnitude)
  magnitude real,
  reported_at timestamptz NOT NULL,
  geom geometry(Point, 4326) NOT NULL,
  cell_x int NOT NULL,
  cell_y int NOT NULL,
  remarks text,
  event_id bigint REFERENCES storm_event
);

CREATE TABLE IF NOT EXISTS warning (
  id text PRIMARY KEY,
  phenomenon text NOT NULL,
  issued_at timestamptz,
  expires_at timestamptz,
  max_hail_in real,
  max_wind_mph real,
  geom geometry(Geometry, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS event_zip_impact (
  event_id bigint REFERENCES storm_event,
  zip text,
  max_hail_in real,
  max_wind_mph real,
  buildings_ge_1in int NOT NULL,
  buildings_ge_1_5in int NOT NULL,
  buildings_ge_2in int NOT NULL,
  report_count int NOT NULL,
  PRIMARY KEY (event_id, zip)
);

CREATE INDEX IF NOT EXISTS hail_cell_event_idx ON hail_cell (event_id);
CREATE INDEX IF NOT EXISTS hail_cell_new_idx ON hail_cell (valid_at) WHERE event_id IS NULL;
CREATE INDEX IF NOT EXISTS storm_report_event_idx ON storm_report (event_id);
CREATE INDEX IF NOT EXISTS storm_report_geom_idx ON storm_report USING gist (geom);
CREATE INDEX IF NOT EXISTS structure_cell_idx ON structure (cell_x, cell_y);
