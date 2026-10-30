"""API tests against the real app, in-process. No browser, no network."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

PASSWORD = "integration-password"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["FLOWPILOT_DB_PATH"] = str(Path(tmp.name) / "api.db")
    os.environ["FLOWPILOT_EVIDENCE_DIR"] = str(Path(tmp.name) / "evidence")

    for module in ("app", "db", "auth", "runner", "scheduler", "notify", "sheets"):
        sys.modules.pop(module, None)

    import app as app_module
    import db as db_module

    with TestClient(app_module.app) as c:
        yield c

    # Windows will not delete an open SQLite file.
    if db_module._conn:
        db_module._conn.close()
        db_module._conn = None
    tmp.cleanup()


# -- lockout before setup ----------------------------------------------------

def test_api_is_closed_before_a_password_exists(client):
    assert client.get("/api/overview").status_code == 401


def test_pages_redirect_to_setup_before_a_password_exists(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_assets_stay_public(client):
    for path in ("/login", "/login.js", "/styles.css"):
        assert client.get(path).status_code == 200, path


def test_status_endpoint_reports_unconfigured(client):
    body = client.get("/api/auth/status").json()
    assert body == {"configured": False, "authenticated": False}


# -- setup and login ---------------------------------------------------------

def test_setup_rejects_a_short_password(client):
    assert client.post("/api/auth/setup", json={"password": "short"}).status_code == 400


def test_setup_creates_the_password_and_signs_in(client):
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_setup_cannot_run_twice(client):
    assert client.post("/api/auth/setup", json={"password": "another-one"}).status_code == 400


def test_wrong_password_is_refused(client):
    from fastapi.testclient import TestClient

    import app as app_module
    with TestClient(app_module.app) as stranger:
        assert stranger.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert stranger.get("/api/overview").status_code == 401
        assert stranger.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        assert stranger.get("/api/overview").status_code == 200


# -- workflows ---------------------------------------------------------------

def test_workflow_requires_an_authorization_record(client):
    r = client.post("/api/workflows", json={
        "name": "No attestation", "target_domain": "example.com",
        "authorized_by": "   ", "steps": [],
    })
    assert r.status_code == 400


def test_workflow_create_update_delete(client):
    created = client.post("/api/workflows", json={
        "name": "Temp", "description": "d", "target_domain": "Example.COM",
        "authorized_by": "tester",
        "steps": [{"type": "goto", "url": "https://example.com/"}],
    }).json()
    assert created["target_domain"] == "example.com"

    patched = client.patch(f"/api/workflows/{created['id']}", json={"name": "Renamed"}).json()
    assert patched["name"] == "Renamed"
    assert len(patched["steps"]) == 1, "patching the name must not drop steps"

    assert client.delete(f"/api/workflows/{created['id']}").status_code == 200
    assert client.delete(f"/api/workflows/{created['id']}").status_code == 404


def test_patch_cannot_blank_the_authorization(client):
    wf = client.post("/api/workflows", json={
        "name": "Auth test", "target_domain": "example.com",
        "authorized_by": "tester", "steps": [{"type": "goto", "url": "https://example.com/"}],
    }).json()
    assert client.patch(f"/api/workflows/{wf['id']}", json={"authorized_by": "  "}).status_code == 400
    client.delete(f"/api/workflows/{wf['id']}")


# -- scheduling --------------------------------------------------------------

def test_schedule_can_be_enabled_and_disabled(client):
    wf = client.get("/api/workflows").json()[0]

    on = client.put(f"/api/workflows/{wf['id']}/schedule", json={
        "enabled": True, "kind": "interval", "minutes": 15, "batch": 5,
    }).json()
    assert on["schedule_enabled"] == 1
    assert on["next_run_at"] is not None

    off = client.put(f"/api/workflows/{wf['id']}/schedule", json={"enabled": False}).json()
    assert off["schedule_enabled"] == 0
    assert off["next_run_at"] is None


def test_schedule_rejects_an_unknown_kind(client):
    wf = client.get("/api/workflows").json()[0]
    r = client.put(f"/api/workflows/{wf['id']}/schedule",
                   json={"enabled": True, "kind": "hourly"})
    assert r.status_code == 400


def test_schedule_status_lists_scheduled_workflows(client):
    wf = client.get("/api/workflows").json()[0]
    client.put(f"/api/workflows/{wf['id']}/schedule",
               json={"enabled": True, "kind": "daily", "at": "06:30", "batch": 3})

    status = client.get("/api/schedule").json()
    assert status["enabled"] is True
    assert any(u["workflow_id"] == wf["id"] for u in status["upcoming"])

    client.put(f"/api/workflows/{wf['id']}/schedule", json={"enabled": False})


# -- records -----------------------------------------------------------------

def test_csv_import_and_duplicate_rejection(client):
    wf = client.get("/api/workflows").json()[0]
    payload = {"workflow_id": wf["id"], "csv_text": "name,tag\nA,alpha\nB,beta\n"}

    first = client.post("/api/records/import", json=payload).json()
    assert first["inserted"] == 2

    again = client.post("/api/records/import", json=payload).json()
    assert (again["inserted"], again["skipped_duplicates"]) == (0, 2)


def test_csv_import_rejects_empty_input(client):
    assert client.post("/api/records/import", json={"csv_text": "   "}).status_code == 400


def test_sheet_import_rejects_non_google_hosts(client):
    for url in ("https://evil.test/spreadsheets/d/ABC/edit",
                "http://127.0.0.1:8000/spreadsheets/d/ABC/edit"):
        r = client.post("/api/records/import-sheet", json={"url": url})
        assert r.status_code == 400, url


# -- run control -------------------------------------------------------------

def test_run_is_refused_for_a_target_that_is_not_allowlisted(client):
    wf = client.post("/api/workflows", json={
        "name": "Unlisted", "target_domain": "not-allowed.test",
        "authorized_by": "tester",
        "steps": [{"type": "goto", "url": "https://not-allowed.test/"}],
    }).json()
    client.post("/api/records/import",
                json={"workflow_id": wf["id"], "csv_text": "name\nrow\n"})

    r = client.post("/api/run/start", json={"workflow_id": wf["id"], "batch_size": 1})
    assert r.status_code == 403
    assert "allowed domains" in r.json()["detail"]

    client.delete(f"/api/workflows/{wf['id']}")


def test_run_is_refused_when_the_queue_is_empty(client):
    wf = client.post("/api/workflows", json={
        "name": "Empty queue", "target_domain": "quotes.toscrape.com",
        "authorized_by": "tester",
        "steps": [{"type": "goto", "url": "https://quotes.toscrape.com/"}],
    }).json()
    r = client.post("/api/run/start", json={"workflow_id": wf["id"], "batch_size": 1})
    assert r.status_code == 400
    client.delete(f"/api/workflows/{wf['id']}")


# -- settings and housekeeping -----------------------------------------------

def test_settings_round_trip(client):
    client.put("/api/settings", json={"values": {"min_delay_seconds": "7"}})
    assert client.get("/api/settings").json()["min_delay_seconds"] == "7"


def test_notification_test_requires_a_url(client):
    client.put("/api/settings", json={"values": {"notify_webhook_url": ""}})
    assert client.post("/api/notify/test").status_code == 400


def test_evidence_endpoint_reports_usage(client):
    body = client.get("/api/evidence").json()
    assert {"files", "bytes", "megabytes", "retention_days"} <= set(body)


def test_evidence_prune_is_safe_to_call(client):
    assert client.post("/api/evidence/prune").status_code == 200


# -- logout ------------------------------------------------------------------

def test_logout_locks_the_caller_out(client):
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
