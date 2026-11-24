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
