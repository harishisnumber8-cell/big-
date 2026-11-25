"""Customer web app: signup with Stripe checkout, storm history, event map and ZIP CSV.
Run with: uvicorn storm.web:app"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import time
from html import escape

import psycopg
import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

app = FastAPI()
PLANS = {"local": ("Local", 99, 3), "pro": ("Pro", 199, 15), "statewide": ("Statewide", 399, None)}  # name, $/mo, max counties
STYLE = """body{font-family:system-ui,sans-serif;max-width:960px;margin:0 auto;padding:16px;color:#1a1a1a}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd}
td.n,th.n{text-align:right}a{color:#0b5cad}#map{height:420px;margin:16px 0}.err{color:#b00020}
label{display:block;margin:10px 0 4px}input,select{font-size:16px;padding:6px;max-width:100%}button{font-size:16px;padding:8px 16px}"""


def db():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def page(title, body, head=""):
    return HTMLResponse(f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                        f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
                        f"<style>{STYLE}</style>{head}</head><body>{body}</body></html>")


def base_url():
    return os.environ.get("BASE_URL", "http://localhost:8000")


# ---------- signup and billing ----------

@app.get("/signup")
def signup_form(error: str = ""):
    with db() as c:
        counties = c.execute("SELECT fips, name FROM county ORDER BY name").fetchall()
    plans = "".join(f"<label><input type='radio' name='plan' value='{k}' {'checked' if k == 'pro' else ''}> {n}: ${p}/month, "
                    f"{f'up to {m} counties' if m else 'all of Arkansas'}</label>" for k, (n, p, m) in PLANS.items())
    opts = "".join(f"<option value='{f}'>{escape(n)}</option>" for f, n in counties)
    err = f"<p class='err' role='alert'>{escape(error)}</p>" if error else ""
    return page("Sign up", f"""<h1>Storm alerts for your service area</h1>{err}
<form method='post' action='/signup'>
<label for='name'>Company name</label><input id='name' name='name' required>
<label for='email'>Email for storm summaries</label><input id='email' name='email' type='email' required>
<label for='phone'>Mobile number for text alerts (optional)</label><input id='phone' name='phone' type='tel'>
<fieldset><legend>Plan</legend>{plans}</fieldset>
<label for='counties'>Counties you work in (hold Ctrl or Cmd to pick several; ignored on Statewide)</label>
<select id='counties' name='counties' multiple size='12'>{opts}</select>
<p><label><input type='checkbox' name='consent' required> I agree to receive storm alerts by email and text. Reply STOP to end texts.</label></p>
<button>Continue to payment</button></form>""")


def us_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or digits[0] != "1":
        raise ValueError("Enter a 10-digit US mobile number.")
    return "+" + digits


@app.post("/signup")
def signup(name: str = Form(...), email: str = Form(...), plan: str = Form(...), phone: str = Form(""),
           counties: list[str] = Form([]), consent: str = Form(...)):
    def back(msg):
        return RedirectResponse(f"/signup?error={requests.utils.quote(msg)}", status_code=303)
    if plan not in PLANS:
        return back("Pick a plan.")
    limit = PLANS[plan][2]
    if limit and not 1 <= len(counties) <= limit:
        return back(f"The {PLANS[plan][0]} plan covers 1 to {limit} counties.")
    try:
        phone = us_phone(phone)
    except ValueError as e:
        return back(str(e))
    with db() as c:
        zips = c.execute("SELECT array_agg(zip ORDER BY zip) FROM zip_area WHERE %s OR county_fips && %s::text[]",
                         (limit is None, counties)).fetchone()[0]
        if not zips:
            return back("Those counties have no ZIPs loaded yet.")
        cust, token = c.execute("""INSERT INTO customer (name, email, phone, zips, plan, active)
                                   VALUES (%s, %s, %s, %s, %s, false) RETURNING id, token""",
                                (name.strip(), email.strip(), phone, zips, plan)).fetchone()
        if os.environ.get("DEV_NO_BILLING") == "1":  # local development only: skip Stripe
            c.execute("UPDATE customer SET active = true WHERE id = %s", (cust,))
            return RedirectResponse(f"/c/{token}", status_code=303)
    r = requests.post("https://api.stripe.com/v1/checkout/sessions", auth=(os.environ["STRIPE_SECRET_KEY"], ""), timeout=30, data={
        "mode": "subscription",
        "line_items[0][price]": os.environ[f"STRIPE_PRICE_{plan.upper()}"],
        "line_items[0][quantity]": 1,
        "client_reference_id": cust,
        "customer_email": email.strip(),
        "success_url": f"{base_url()}/c/{token}",
        "cancel_url": f"{base_url()}/signup",
    })
    r.raise_for_status()
    return RedirectResponse(r.json()["url"], status_code=303)


def verify_stripe(payload, header, secret, tolerance=300):
    """Stripe signs '<timestamp>.<body>' with HMAC-SHA256; reject bad or stale signatures."""
    parts = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
    try:
        t = int(parts["t"])
    except (KeyError, ValueError):
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    return abs(time.time() - t) <= tolerance and any(hmac.compare_digest(expected, s) for s in sigs)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    if not verify_stripe(payload, request.headers.get("stripe-signature"), os.environ["STRIPE_WEBHOOK_SECRET"]):
        raise HTTPException(400, "bad signature")
    event = json.loads(payload)
    obj = event["data"]["object"]
    with db() as c:
        if event["type"] == "checkout.session.completed":
            c.execute("""UPDATE customer SET active = true, stripe_customer_id = %s, stripe_subscription_id = %s
                         WHERE id = %s""", (obj.get("customer"), obj.get("subscription"), int(obj["client_reference_id"])))
        elif event["type"] == "customer.subscription.deleted":
            c.execute("UPDATE customer SET active = false WHERE stripe_subscription_id = %s", (obj["id"],))
    return {"ok": True}


# ---------- customer dashboard (the private link in every alert) ----------

def customer_or_404(c, token):
    row = c.execute("SELECT id, name, zips, active, min_hail_in, min_wind_mph FROM customer WHERE token = %s", (token,)).fetchone()
    if not row:
        raise HTTPException(404)
    return row


@app.get("/c/{token}")
def dashboard(token: str):
    with db() as c:
        _, name, zips, active, hail, wind = customer_or_404(c, token)
        events = c.execute("""
            SELECT e.id, e.started_at, max(i.max_hail_in), max(i.max_wind_mph), sum(i.buildings_ge_1in), count(*)
            FROM storm_event e JOIN event_zip_impact i ON i.event_id = e.id
            WHERE e.closed AND i.zip = ANY (%s) AND e.started_at > now() - interval '365 days'
            GROUP BY e.id HAVING max(i.max_hail_in) >= %s OR max(i.max_wind_mph) >= %s
            ORDER BY e.started_at DESC""", (zips, hail, wind)).fetchall()
    rows = "".join(
        f"<tr><td><a href='/c/{token}/events/{ev}'>{t:%b %d, %Y %H:%M} UTC</a></td><td class='n'>{fmt(h, ' in')}</td>"
        f"<td class='n'>{fmt(w, ' mph', 0)}</td><td class='n'>{b:,}</td><td class='n'>{n}</td></tr>"
        for ev, t, h, w, b, n in events)
    status = "" if active else "<p class='err'>Alerts are off: your subscription is not active.</p>"
    return page(f"{name} storm history", f"""<h1>{escape(name)}</h1>{status}
<p>Watching {len(zips)} ZIPs. Alerts for hail of {hail:g} in or more, or wind of {wind:g} mph or more.</p>
<h2>Storms in your area, last 12 months</h2>
<table><tr><th>Storm</th><th class='n'>Max hail</th><th class='n'>Max wind</th><th class='n'>Buildings hit by 1 in+ hail</th><th class='n'>Your ZIPs hit</th></tr>
{rows or "<tr><td colspan='5'>No storms yet.</td></tr>"}</table>""")


def fmt(v, unit, digits=2):
    return f"{v:.{digits}f}{unit}" if v else "-"


def event_rows(c, token, ev):
    _, name, zips, *_ = customer_or_404(c, token)
    rows = c.execute("""SELECT zip, max_hail_in, max_wind_mph, buildings_ge_1in, buildings_ge_1_5in, buildings_ge_2in, report_count
                        FROM event_zip_impact WHERE event_id = %s AND zip = ANY (%s)
                        ORDER BY buildings_ge_1in DESC, zip""", (ev, zips)).fetchall()
    if not rows:
        raise HTTPException(404)  # not a storm in this customer's area
    return name, rows


# Must come before /events/{ev}, which would otherwise match '12.csv' and reject it as not a number.
@app.get("/c/{token}/events/{ev}.csv")
def event_csv(token: str, ev: int):
    with db() as c:
        _, rows = event_rows(c, token, ev)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["zip", "max_hail_in", "max_wind_mph", "buildings_ge_1in", "buildings_ge_1_5in", "buildings_ge_2in", "reports"])
    w.writerows(rows)
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=storm-{ev}.csv"})


@app.get("/c/{token}/events/{ev}")
def event_page(token: str, ev: int):
    with db() as c:
        name, rows = event_rows(c, token, ev)
        started, hail, confidence = c.execute(
            "SELECT started_at, max_hail_in, confidence FROM storm_event WHERE id = %s", (ev,)).fetchone()
    table = "".join(f"<tr><td>{z}</td><td class='n'>{fmt(h, ' in')}</td><td class='n'>{fmt(w, ' mph', 0)}</td>"
                    f"<td class='n'>{b1:,}</td><td class='n'>{b15:,}</td><td class='n'>{b2:,}</td><td class='n'>{r}</td></tr>"
                    for z, h, w, b1, b15, b2, r in rows)
    head = ("<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css'>"
            "<script src='https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js'></script>")
    # shortcut: OpenStreetMap's public tiles allow light use only; switch to a paid tile host before many customers.
    script = f"""<script>
const map = new maplibregl.Map({{container: 'map', style: {{version: 8, sources: {{osm: {{type: 'raster', tileSize: 256,
  tiles: ['https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'], attribution: '&copy; OpenStreetMap contributors'}}}},
  layers: [{{id: 'osm', type: 'raster', source: 'osm'}}]}}, center: [-92.4, 34.8], zoom: 6}});
map.on('load', async () => {{
  const data = await (await fetch('/c/{token}/events/{ev}/hail.geojson')).json();
  map.addSource('hail', {{type: 'geojson', data}});
  map.addLayer({{id: 'hail', type: 'fill', source: 'hail', paint: {{'fill-opacity': 0.6, 'fill-color':
    ['step', ['get', 'in'], '#f2d45c', 1, '#f29b30', 1.5, '#e0442b', 2, '#8e1b6f']}}}});
  const b = new maplibregl.LngLatBounds(); data.features.forEach(f => f.geometry.coordinates[0].forEach(p => b.extend(p)));
  if (!b.isEmpty()) map.fitBounds(b, {{padding: 30}});
}});</script>"""
    return page(f"Storm {started:%b %d, %Y}", f"""<p><a href='/c/{token}'>&larr; All storms</a></p>
<h1>Storm of {started:%b %d, %Y %H:%M} UTC</h1>
<p>Max hail {fmt(hail, ' in')}. Confidence: {escape(confidence or '')}
({'radar backed by ground reports' if confidence == 'high' else 'radar only'}).</p>
<div id='map' role='img' aria-label='Map of the hail swath, colored by hail size'></div>
<p>Hail: yellow under 1 in, orange 1 to 1.5 in, red 1.5 to 2 in, purple 2 in and up.</p>
<h2>Your ZIPs</h2><p><a href='/c/{token}/events/{ev}.csv'>Download CSV</a></p>
<table><tr><th>ZIP</th><th class='n'>Max hail</th><th class='n'>Max wind</th><th class='n'>Buildings &ge;1 in</th>
<th class='n'>&ge;1.5 in</th><th class='n'>&ge;2 in</th><th class='n'>Reports</th></tr>{table}</table>
<p>Building counts are structures in the hail path, not confirmed damage. Hail sizes are NOAA radar estimates.</p>{script}""",
                head)


@app.get("/c/{token}/events/{ev}/hail.geojson")
def event_hail(token: str, ev: int):
    with db() as c:
        event_rows(c, token, ev)  # access check
        fc = c.execute("""SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
                              'type', 'Feature', 'properties', json_build_object('in', m),
                              'geometry', ST_AsGeoJSON(ST_MakeEnvelope(cell_x * 0.01, cell_y * 0.01,
                                                                       (cell_x + 1) * 0.01, (cell_y + 1) * 0.01, 4326))::json)), '[]'))
                          FROM (SELECT cell_x, cell_y, max(mesh_in) AS m FROM hail_cell WHERE event_id = %s GROUP BY 1, 2) h""",
                       (ev,)).fetchone()[0]
    return fc
