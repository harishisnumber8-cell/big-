"""Group hail cells and storm reports into storm events, and score each closed event by ZIP."""
from datetime import timedelta
from itertools import groupby

RADIUS = 10                         # cells (~10 km): new hail this close to a storm joins it
JOIN_GAP = timedelta(minutes=45)    # a storm quiet longer than this stops taking new hail
CLOSE_AFTER = timedelta(minutes=60)  # ...and is closed and scored after this


def assign(new, owner, radius=RADIUS):
    """new: set of (x, y) cells. owner: {(x, y): event id} of storms still growing.
    Returns {cell: event id}; cells that join no storm form new clusters with ids -1, -2, ...
    Chains count: a cell joins a storm if it is within `radius` of any cell already joined."""
    out, todo, new_id = {}, set(new), -1
    frontier = list(owner.items())
    while True:
        while frontier:
            (x, y), ev = frontier.pop()
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    c = (x + dx, y + dy)
                    if c in todo:
                        todo.discard(c)
                        out[c] = ev
                        frontier.append((c, ev))
        if not todo:
            return out
        c = todo.pop()
        out[c] = new_id
        frontier.append((c, new_id))
        new_id -= 1


def build(conn, now):
    """Attach every unassigned observation up to `now` to a storm, then close and score quiet storms."""
    cur = conn.cursor()
    obs = cur.execute(
        """SELECT valid_at, cell_x, cell_y FROM hail_cell WHERE event_id IS NULL AND valid_at <= %(now)s
           UNION SELECT reported_at, cell_x, cell_y FROM storm_report WHERE event_id IS NULL AND reported_at <= %(now)s
           ORDER BY 1""", {"now": now}).fetchall()
    for t, group in groupby(obs, key=lambda o: o[0]):
        owner = {(x, y): ev for ev, x, y in cur.execute(
            """SELECT e.id, h.cell_x, h.cell_y FROM storm_event e JOIN hail_cell h ON h.event_id = e.id
               WHERE NOT e.closed AND e.last_at >= %(since)s
               UNION SELECT e.id, r.cell_x, r.cell_y FROM storm_event e JOIN storm_report r ON r.event_id = e.id
               WHERE NOT e.closed AND e.last_at >= %(since)s""", {"since": t - JOIN_GAP})}
        cells = assign({(x, y) for _, x, y in group}, owner)
        ids = {}
        for ev in sorted(set(cells.values())):
            ids[ev] = ev if ev > 0 else cur.execute(
                "INSERT INTO storm_event (started_at, last_at) VALUES (%s, %s) RETURNING id", (t, t)).fetchone()[0]
        rows = [(ids[ev], t, x, y) for (x, y), ev in cells.items()]
        cur.executemany("UPDATE hail_cell SET event_id = %s WHERE valid_at = %s AND cell_x = %s AND cell_y = %s", rows)
        cur.executemany("UPDATE storm_report SET event_id = %s WHERE reported_at = %s AND cell_x = %s AND cell_y = %s", rows)
        cur.execute("UPDATE storm_event SET last_at = greatest(last_at, %s) WHERE id = ANY(%s)", (t, list(ids.values())))

    for (ev,) in cur.execute("SELECT id FROM storm_event WHERE NOT closed AND last_at < %s ORDER BY id",
                             (now - CLOSE_AFTER,)).fetchall():
        close(cur, ev)


def close(cur, ev):
    p = {"e": ev}
    cur.execute("DELETE FROM event_zip_impact WHERE event_id = %(e)s", p)
    cur.execute(
        """INSERT INTO event_zip_impact (event_id, zip, max_hail_in, max_wind_mph, buildings_ge_1in,
                                         buildings_ge_1_5in, buildings_ge_2in, report_count)
           WITH hail AS (SELECT cell_x, cell_y, max(mesh_in) AS m FROM hail_cell WHERE event_id = %(e)s GROUP BY 1, 2),
           hz AS (SELECT s.zip, max(h.m) AS hail, count(*) FILTER (WHERE h.m >= 1) AS b1,
                         count(*) FILTER (WHERE h.m >= 1.5) AS b15, count(*) FILTER (WHERE h.m >= 2) AS b2
                  FROM structure s JOIN hail h USING (cell_x, cell_y) WHERE s.zip IS NOT NULL GROUP BY s.zip),
           rz AS (SELECT z.zip, max(r.magnitude) FILTER (WHERE r.type = 'hail') AS hail,
                         max(r.magnitude) FILTER (WHERE r.type = 'wind') AS wind, count(*) AS n
                  FROM storm_report r JOIN zip_area z ON ST_Intersects(z.geom, r.geom)
                  WHERE r.event_id = %(e)s GROUP BY z.zip)
           SELECT %(e)s, coalesce(hz.zip, rz.zip), greatest(hz.hail, rz.hail), rz.wind,
                  coalesce(hz.b1, 0), coalesce(hz.b15, 0), coalesce(hz.b2, 0), coalesce(rz.n, 0)
           FROM hz FULL JOIN rz ON rz.zip = hz.zip""", p)
    cur.execute(
        """UPDATE storm_event SET closed = true,
             max_hail_in = greatest((SELECT max(mesh_in) FROM hail_cell WHERE event_id = %(e)s),
                                    (SELECT max(magnitude) FROM storm_report WHERE event_id = %(e)s AND type = 'hail')),
             max_wind_mph = (SELECT max(magnitude) FROM storm_report WHERE event_id = %(e)s AND type = 'wind'),
             confidence = CASE WHEN EXISTS (SELECT 1 FROM storm_report WHERE event_id = %(e)s AND type = 'hail')
                          THEN 'high' ELSE 'medium' END
           WHERE id = %(e)s""", p)
