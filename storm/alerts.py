"""Match storms to customers' service areas and send each customer at most one heads-up SMS
while a storm is active and one summary (email + SMS) when it closes."""
from storm import notify

# Active storms: hail at or above the customer's threshold over buildings in their ZIPs.
HEADSUPS = """
SELECT c.id, e.id, c.phone, max(h.mesh_in), array_agg(DISTINCT s.zip ORDER BY s.zip), count(DISTINCT s.id)
FROM storm_event e
JOIN hail_cell h ON h.event_id = e.id
JOIN structure s USING (cell_x, cell_y)
JOIN customer c ON s.zip = ANY (c.zips)
WHERE NOT e.closed AND c.active AND c.phone IS NOT NULL
  AND e.started_at >= c.created_at AND h.mesh_in >= c.min_hail_in
  AND NOT EXISTS (SELECT 1 FROM alert a WHERE a.customer_id = c.id AND a.event_id = e.id AND a.kind = 'headsup')
GROUP BY c.id, e.id, c.phone, c.min_buildings
HAVING count(DISTINCT s.id) >= c.min_buildings
"""

# Closed storms: per-ZIP impacts in the customer's area that meet their hail or wind threshold.
SUMMARIES = """
SELECT c.id, e.id, c.email, c.phone, e.max_hail_in, e.confidence,
       json_agg(json_build_object('zip', i.zip, 'hail', i.max_hail_in, 'wind', i.max_wind_mph, 'b1', i.buildings_ge_1in,
                                  'b15', i.buildings_ge_1_5in, 'b2', i.buildings_ge_2in, 'reports', i.report_count)
                ORDER BY i.buildings_ge_1in DESC, i.zip)
FROM storm_event e
JOIN event_zip_impact i ON i.event_id = e.id
JOIN customer c ON i.zip = ANY (c.zips)
WHERE e.closed AND c.active AND e.started_at >= c.created_at
  AND (i.max_hail_in >= c.min_hail_in OR i.max_wind_mph >= c.min_wind_mph)
  AND NOT EXISTS (SELECT 1 FROM alert a WHERE a.customer_id = c.id AND a.event_id = e.id AND a.kind = 'summary')
GROUP BY c.id, e.id, c.email, c.phone, e.max_hail_in, e.confidence, c.min_buildings, c.min_wind_mph
HAVING sum(i.buildings_ge_1in) >= c.min_buildings OR max(i.max_wind_mph) >= c.min_wind_mph
"""


def run(conn, send=notify.send):
    """Find due alerts, record each one, then send it. Recording first means a crash or an overlapping
    run never sends the same alert twice; a failed send is kept with its error."""
    out = []
    for cust, ev, phone, hail, zips, n in conn.execute(HEADSUPS).fetchall():
        shown = ", ".join(zips[:6]) + (f" and {len(zips) - 6} more" if len(zips) > 6 else "")
        out.append((cust, ev, "headsup", "sms", phone, None,
                    f"Storm alert: hail up to {hail:.2f} in now over {shown}, about {n:,} buildings. Summary to follow."))
    for cust, ev, email, phone, hail, confidence, zips in conn.execute(SUMMARIES).fetchall():
        subject, body, sms = summary(hail, confidence, zips)
        if email:
            out.append((cust, ev, "summary", "email", email, subject, body))
        if phone:
            out.append((cust, ev, "summary", "sms", phone, None, sms))

    for cust, ev, kind, channel, to, subject, body in out:
        row = conn.execute(
            """INSERT INTO alert (customer_id, event_id, kind, channel, body) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING id""", (cust, ev, kind, channel, body)).fetchone()
        if not row:
            continue  # another run already took it
        try:
            send(channel, to, subject, body)
            conn.execute("UPDATE alert SET status = 'sent', sent_at = now() WHERE id = %s", row)
        except Exception as e:  # keep going so one bad number doesn't block every other customer
            conn.execute("UPDATE alert SET status = 'failed', error = %s WHERE id = %s", (str(e)[:500], row[0]))
    return len(out)


def summary(hail, confidence, zips):
    hit = sum(z["b1"] for z in zips)
    wind = max((z["wind"] or 0) for z in zips)
    headline = f"hail up to {hail:.2f} in" + (f", wind to {wind:.0f} mph" if wind else "")
    subject = f"Storm summary: {headline}, {hit:,} buildings in your area"
    lines = [f"A storm with {headline} has passed through your service area.",
             f"Confidence: {confidence} ({'radar backed by ground reports' if confidence == 'high' else 'radar only'}).",
             "", "ZIP    Max hail  Wind     Bldgs >=1in  >=1.5in  >=2in  Reports"]
    for z in zips:
        hail_s = f"{z['hail']:.2f} in" if z["hail"] else "-"
        wind_s = f"{z['wind']:.0f} mph" if z["wind"] else "-"
        lines.append(f"{z['zip']:<6} {hail_s:<9} {wind_s:<8} {z['b1']:>11,}  {z['b15']:>7,}  {z['b2']:>5,}  {z['reports']:>7}")
    lines += ["", "Building counts are structures in the hail path, not confirmed damage.",
              "Hail sizes are radar estimates (NOAA MRMS) unless backed by ground reports."]
    top = ", ".join(z["zip"] for z in zips[:5])
    sms = f"Storm summary: {headline}. {hit:,} buildings hit in your ZIPs, most in {top}. Details in your email."
    return subject, "\n".join(lines), sms
