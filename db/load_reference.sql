-- Copy the raw_* tables that ogr2ogr loaded into the reference tables.
TRUNCATE county, zip_area, structure, grid_cell;

INSERT INTO county (fips, name, geom)
SELECT geoid, name, geom FROM raw_county;

-- Keep ZCTAs that overlap Arkansas, including ones that cross the state line.
INSERT INTO zip_area (zip, geom)
SELECT z.zcta5ce20, z.geom FROM raw_zcta z
WHERE EXISTS (SELECT 1 FROM county c
              WHERE ST_Intersects(c.geom, z.geom) AND NOT ST_Touches(c.geom, z.geom));

-- shortcut: field names follow the FEMA USA Structures schema (occ_cls, sqfeet); check them on the first real load.
INSERT INTO structure (id, geom, footprint_sqft, occupancy)
SELECT ogc_fid, ST_PointOnSurface(geom), sqfeet, occ_cls
FROM raw_structure
WHERE sqfeet IS NULL OR sqfeet >= 400;  -- drop sheds and outbuildings
