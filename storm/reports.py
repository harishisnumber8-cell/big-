"""Ground storm reports (NWS Local Storm Reports via Iowa Environmental Mesonet) and NWS warnings."""
import json
import os

import requests

IEM_LSR = "https://mesonet.agron.iastate.edu/geojson/lsr.geojson"
NWS_ALERTS = "https://api.weather.gov/alerts/active"
LSR_TYPES = {"H": "hail", "G": "wind", "D": "damage"}  # IEM one-letter LSR codes we keep
WARNINGS = {"Severe Thunderstorm Warning", "Tornado Warning"}


def _get(url, params):
    # NWS asks API clients to identify themselves with a contact in the User-Agent.
    ua = f"arkansas-storm-alerts ({os.environ.get('CONTACT_EMAIL', 'no contact set')})"
    r = requests.get(url, params=params, headers={"User-Agent": ua}, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_lsr(start, end):
    # shortcut: parameter names from IEM's docs, not yet exercised from a networked run; check on first live use.
    return _get(IEM_LSR, {"states": "AR", "sts": start.strftime("%Y%m%d%H%M"), "ets": end.strftime("%Y%m%d%H%M")})


def parse_lsr(geojson):
    out = []
    for f in geojson.get("features", []):
        p = f["properties"]
        kind = LSR_TYPES.get(p.get("type"))
        if not kind or not f.get("geometry"):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        mag = p.get("magnitude")
        out.append({
            "source_id": f"{p.get('wfo')}|{p['valid']}|{p['type']}|{lon:.4f}|{lat:.4f}",
            "type": kind,
            "magnitude": float(mag) if mag not in (None, "") else None,
            "reported_at": p["valid"],
            "lon": lon, "lat": lat,
            "remarks": p.get("remark"),
        })
    return out


def store_reports(conn, reports):
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO storm_report (source_id, type, magnitude, reported_at, geom, cell_x, cell_y, remarks)
               VALUES (%(source_id)s, %(type)s, %(magnitude)s, %(reported_at)s, ST_Point(%(lon)s, %(lat)s, 4326),
                       floor(%(lon)s::numeric / 0.01), floor(%(lat)s::numeric / 0.01), %(remarks)s)
               ON CONFLICT DO NOTHING""", reports)


def fetch_alerts():
    return _get(NWS_ALERTS, {"area": "AR"})


def parse_alerts(geojson):
    out = []
    for f in geojson.get("features", []):
        p = f["properties"]
        if p.get("event") not in WARNINGS or not f.get("geometry"):
            continue
        params = p.get("parameters") or {}
        hail = (params.get("maxHailSize") or [None])[0]
        wind = (params.get("maxWindGust") or [None])[0]  # e.g. "60 MPH"
        out.append({
            "id": (params.get("VTEC") or [p["id"]])[0],
            "phenomenon": p["event"],
            "issued_at": p.get("sent"),
            "expires_at": p.get("expires"),
            "max_hail_in": float(hail) if hail else None,
            "max_wind_mph": float(wind.split()[0]) if wind else None,
            "geom": json.dumps(f["geometry"]),
        })
    return out


def store_alerts(conn, alerts):
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO warning (id, phenomenon, issued_at, expires_at, max_hail_in, max_wind_mph, geom)
               VALUES (%(id)s, %(phenomenon)s, %(issued_at)s, %(expires_at)s, %(max_hail_in)s, %(max_wind_mph)s,
                       ST_SetSRID(ST_GeomFromGeoJSON(%(geom)s), 4326))
               ON CONFLICT (id) DO UPDATE SET expires_at = EXCLUDED.expires_at""", alerts)
