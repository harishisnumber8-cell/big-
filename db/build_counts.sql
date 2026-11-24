-- Assign every structure to a ZIP, county and MRMS grid cell, then precompute counts.
UPDATE structure s SET zip = z.zip FROM zip_area z WHERE ST_Intersects(z.geom, s.geom);
UPDATE structure s SET county_fips = c.fips FROM county c WHERE ST_Intersects(c.geom, s.geom);
-- numeric avoids float error putting points on a cell edge into the neighbour cell
UPDATE structure SET cell_x = floor(ST_X(geom)::numeric / 0.01)::int, cell_y = floor(ST_Y(geom)::numeric / 0.01)::int;

UPDATE zip_area z SET county_fips = ARRAY(
  SELECT c.fips FROM county c
  WHERE ST_Intersects(c.geom, z.geom) AND NOT ST_Touches(c.geom, z.geom)
  ORDER BY c.fips);

UPDATE zip_area z
SET building_count = t.n, residential_count = t.r
FROM (SELECT z2.zip,
             count(s.id) AS n,
             count(s.id) FILTER (WHERE s.occupancy = 'Residential') AS r
      FROM zip_area z2 LEFT JOIN structure s ON s.zip = z2.zip
      GROUP BY z2.zip) t
WHERE t.zip = z.zip;

TRUNCATE grid_cell;
INSERT INTO grid_cell (cell_x, cell_y, building_count, residential_count, geom)
SELECT cell_x, cell_y, count(*), count(*) FILTER (WHERE occupancy = 'Residential'),
       ST_MakeEnvelope(cell_x * 0.01, cell_y * 0.01, (cell_x + 1) * 0.01, (cell_y + 1) * 0.01, 4326)
FROM structure GROUP BY cell_x, cell_y;
