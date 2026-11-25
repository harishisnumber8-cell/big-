"""Phase 4: signup, Stripe webhook, dashboard, event page, CSV and map data."""
import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import box
from storm import events, web
from test_alerts import customer, storm  # noqa: F401  (storm is a fixture)
from test_storm import T0

client = TestClient(web.app)


@pytest.fixture
def area(db):
    for i, fips in enumerate(["05001", "05003", "05005", "05007"]):
        db.execute(f"INSERT INTO county VALUES (%s, %s, {box(-92 + i, 34, -91 + i, 35)})", (fips, f"County {i}"))
        db.execute(f"INSERT INTO zip_area (zip, county_fips, geom) VALUES (%s, %s, {box(-92 + i, 34, -91 + i, 35)})",
                   (f"7200{i}", [fips]))
    return db


def form(**kw):
    return {"name": "Acme Roofing", "email": "a@example.com", "phone": "(501) 555-0100", "plan": "local",
            "counties": ["05001", "05003"], "consent": "on", **kw}


def test_signup_dev_mode_creates_active_customer(area, monkeypatch):
    monkeypatch.setenv("DEV_NO_BILLING", "1")
    r = client.post("/signup", data=form(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/c/")
    assert area.execute("SELECT zips, phone, plan, active FROM customer").fetchone() == (
        ["72000", "72001"], "+15015550100", "local", True)
    assert client.get(r.headers["location"]).status_code == 200


def test_signup_rejects_bad_input(area):
    too_many = client.post("/signup", data=form(counties=["05001", "05003", "05005", "05007"]), follow_redirects=False)
    assert "error=" in too_many.headers["location"]
    bad_phone = client.post("/signup", data=form(phone="555"), follow_redirects=False)
    assert "error=" in bad_phone.headers["location"]
    assert area.execute("SELECT count(*) FROM customer").fetchone()[0] == 0


def signed(body, secret="whsec_test", t=None):
    t = t or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_verify_stripe():
    body = b'{"x":1}'
    assert web.verify_stripe(body, signed(body), "whsec_test")
    assert not web.verify_stripe(body, signed(body, "wrong"), "whsec_test")
    assert not web.verify_stripe(body, signed(body, t=int(time.time()) - 3600), "whsec_test")
    assert not web.verify_stripe(body, None, "whsec_test")


def test_webhook_turns_alerts_on_and_off(db, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    cust = customer(db, ["72201"], active=False)

    def post(event):
        body = json.dumps(event).encode()
        return client.post("/stripe/webhook", content=body, headers={"stripe-signature": signed(body)})

    assert client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"}).status_code == 400
    post({"type": "checkout.session.completed",
          "data": {"object": {"client_reference_id": str(cust), "customer": "cus_1", "subscription": "sub_1"}}})
    assert db.execute("SELECT active, stripe_subscription_id FROM customer").fetchone() == (True, "sub_1")
    post({"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1"}}})
    assert db.execute("SELECT active FROM customer").fetchone() == (False,)


def test_dashboard_event_csv_and_map(storm):
    cust = customer(storm, ["72201"])
    other = customer(storm, ["72999"])
    events.build(storm, T0 + timedelta(hours=3))
    token, other_token = (storm.execute("SELECT token FROM customer WHERE id = %s", (i,)).fetchone()[0] for i in (cust, other))
    ev = storm.execute("SELECT id FROM storm_event").fetchone()[0]
    storm.execute("UPDATE storm_event SET started_at = now() - interval '1 day'")  # dashboard shows the last 12 months

    assert f"/c/{token}/events/{ev}" in client.get(f"/c/{token}").text
    page = client.get(f"/c/{token}/events/{ev}")
    assert page.status_code == 200 and "72201" in page.text
    csv = client.get(f"/c/{token}/events/{ev}.csv").text.splitlines()
    assert csv[1] == "72201,2.1,,30,30,30,0"
    fc = client.get(f"/c/{token}/events/{ev}/hail.geojson").json()
    assert [f["properties"]["in"] for f in fc["features"]] == [2.1]

    assert client.get(f"/c/{other_token}/events/{ev}").status_code == 404  # not their storm
    assert client.get("/c/not-a-token").status_code == 404
