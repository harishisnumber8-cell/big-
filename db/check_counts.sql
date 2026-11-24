-- Phase 1 gate: per-ZIP and per-cell counts must add up to the structures loaded.
DO $$
DECLARE
  total bigint := (SELECT count(*) FROM structure);
  in_zip bigint := (SELECT count(*) FROM structure WHERE zip IS NOT NULL);
  zip_sum bigint := (SELECT coalesce(sum(building_count), 0) FROM zip_area);
  cell_sum bigint := (SELECT coalesce(sum(building_count), 0) FROM grid_cell);
BEGIN
  RAISE NOTICE 'structures=% in_zip=% zip_sum=% cell_sum=%', total, in_zip, zip_sum, cell_sum;
  IF total = 0 THEN RAISE EXCEPTION 'no structures loaded'; END IF;
  IF zip_sum <> in_zip THEN RAISE EXCEPTION 'ZIP counts (%) do not match structures in ZIPs (%)', zip_sum, in_zip; END IF;
  IF cell_sum <> total THEN RAISE EXCEPTION 'grid counts (%) do not match structures (%)', cell_sum, total; END IF;
  -- shortcut: 2% allowance for buildings outside any ZCTA; tighten once real numbers are known.
  IF in_zip < total * 0.98 THEN RAISE EXCEPTION 'only % of % structures fall inside a ZIP', in_zip, total; END IF;
END $$;
