"""Hail from NOAA MRMS MESH (Maximum Estimated Size of Hail), public on AWS Open Data."""
import gzip
import re
from datetime import datetime, timezone

import eccodes
import numpy as np
import requests

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "CONUS/MESH_Max_30min_00.50"
BBOX = (-94.82, 32.80, -89.44, 36.70)  # Arkansas plus ~20 km: west, south, east, north
MIN_IN = 0.75


def list_keys(day):
    """All MESH files for a UTC day, 'YYYYMMDD'."""
    keys, params = [], {"list-type": "2", "prefix": f"{PRODUCT}/{day}/"}
    while True:
        r = requests.get(BUCKET, params=params, timeout=60)
        r.raise_for_status()
        keys += re.findall(r"<Key>([^<]+)</Key>", r.text)
        token = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", r.text)
        if not token:
            return keys
        params["continuation-token"] = token.group(1)


def valid_time(key):
    stamp = re.search(r"_(\d{8}-\d{6})\.grib2", key).group(1)
    return datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)


def fetch(key):
    r = requests.get(f"{BUCKET}/{key}", timeout=120)
    r.raise_for_status()
    return gzip.decompress(r.content)


def read_cells(grib):
    gid = eccodes.codes_new_from_message(grib)
    try:
        ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
        lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees") - 360
        mm = eccodes.codes_get_values(gid).reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)
    return cells_from_grid(mm, lat0, lon0)


def cells_from_grid(mm, lat0, lon0):
    """mm: hail in millimetres, rows north to south from lat0, columns west to east from lon0
    (0.01 degree cell centres). Returns [(cell_x, cell_y, inches)] inside BBOX at or above MIN_IN,
    with the same cell ids as grid_cell: floor(lon / 0.01), floor(lat / 0.01)."""
    x0, y0 = round(lon0 * 100 - 0.5), round(lat0 * 100 - 0.5)
    west, south, east, north = BBOX
    c0, c1 = max(int((west - lon0) * 100), 0), int((east - lon0) * 100) + 1
    r0, r1 = max(int((lat0 - north) * 100), 0), int((lat0 - south) * 100) + 1
    inches = mm[r0:r1, c0:c1] / 25.4  # missing data is negative, so it drops out below
    rows, cols = np.nonzero(inches >= MIN_IN)
    return [(x0 + c0 + int(c), y0 - r0 - int(r), round(float(inches[r, c]), 2)) for r, c in zip(rows, cols)]


def store(conn, valid_at, cells):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO hail_cell (valid_at, cell_x, cell_y, mesh_in) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(valid_at, *c) for c in cells])
