"""Unit tests. No browser, no server, no network: these run anywhere."""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A throwaway database per test."""
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(db_module, "_conn", None)
    db_module.init_db()
    yield db_module
    if db_module._conn:
        db_module._conn.close()
        db_module._conn = None


# -- templating --------------------------------------------------------------

def test_render_substitutes_record_fields():
    from runner import render
    assert render("https://x.test/{{tag}}/", {"tag": "love"}) == "https://x.test/love/"
    assert render("{{a}}-{{b}}", {"a": "1", "b": "2"}) == "1-2"


def test_render_missing_field_becomes_empty():
    from runner import render
    assert render("x={{nope}}", {}) == "x="


def test_render_passes_non_strings_through():
    from runner import render
    assert render(None, {}) is None
    assert render(7, {}) == 7


# -- allowlist ---------------------------------------------------------------

def test_allowlist_accepts_exact_and_subdomain(fresh_db):
    from runner import check_host_allowed
    fresh_db.set_setting("allowed_domains", "example.com")
    assert check_host_allowed("https://example.com/x") == "example.com"
    assert check_host_allowed("https://staging.example.com/x") == "staging.example.com"


def test_allowlist_rejects_unlisted_host(fresh_db):
    from runner import AuthorizationError, check_host_allowed
    fresh_db.set_setting("allowed_domains", "example.com")
    with pytest.raises(AuthorizationError):
        check_host_allowed("https://evil.test/x")


def test_allowlist_rejects_lookalike_suffix(fresh_db):
    """notexample.com must not pass because it ends with example.com."""
    from runner import AuthorizationError, check_host_allowed
    fresh_db.set_setting("allowed_domains", "example.com")
    with pytest.raises(AuthorizationError):
        check_host_allowed("https://notexample.com/x")


# -- schedule maths ----------------------------------------------------------

def test_interval_schedule_adds_minutes():
    from scheduler import compute_next_run
    base = time.time()
    nxt = compute_next_run({"schedule_kind": "interval", "schedule_minutes": 30}, base)
    assert 29 * 60 < nxt - base < 31 * 60


def test_daily_schedule_picks_today_when_time_is_ahead():
    from scheduler import compute_next_run
    base = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    nxt = datetime.fromtimestamp(
        compute_next_run({"schedule_kind": "daily", "schedule_at": "17:30"}, base.timestamp())
    )
    assert (nxt.hour, nxt.minute) == (17, 30)
    assert nxt.date() == base.date()


def test_daily_schedule_rolls_to_tomorrow_when_time_has_passed():
    from scheduler import compute_next_run
    base = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0)
    nxt = datetime.fromtimestamp(
        compute_next_run({"schedule_kind": "daily", "schedule_at": "09:00"}, base.timestamp())
    )
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt.date() == (base + timedelta(days=1)).date()


def test_malformed_schedule_time_falls_back_to_nine():
    from scheduler import compute_next_run
    nxt = datetime.fromtimestamp(
        compute_next_run({"schedule_kind": "daily", "schedule_at": "not-a-time"})
    )
    assert (nxt.hour, nxt.minute) == (9, 0)


def test_interval_never_goes_below_one_minute():
    from scheduler import compute_next_run
    base = time.time()
    nxt = compute_next_run({"schedule_kind": "interval", "schedule_minutes": 0}, base)
    assert nxt > base


# -- google sheets -----------------------------------------------------------

def test_sheet_url_converts_to_csv_export():
    from sheets import export_url
    out = export_url("https://docs.google.com/spreadsheets/d/ABC123_x-y/edit#gid=42")
    assert "/spreadsheets/d/ABC123_x-y/export?format=csv" in out
    assert "gid=42" in out


def test_sheet_url_defaults_to_first_tab():
    from sheets import export_url
    assert "gid=0" in export_url("https://docs.google.com/spreadsheets/d/ABC/edit")


def test_sheet_url_rejects_other_hosts():
    from sheets import SheetError, export_url
    for bad in (
        "https://evil.test/spreadsheets/d/ABC/edit",
        "http://127.0.0.1:8000/spreadsheets/d/ABC/edit",
        "https://docs.google.com.evil.test/spreadsheets/d/ABC/edit",
    ):
        with pytest.raises(SheetError):
            export_url(bad)


def test_sheet_url_rejects_link_without_id():
    from sheets import SheetError, export_url
    with pytest.raises(SheetError):
        export_url("https://docs.google.com/document/d/ABC/edit")


def test_sheet_csv_parses_and_drops_blank_rows():
    from sheets import parse_csv
    rows = parse_csv("name,tag\nLove,love\n,\nHumor,humor\n")
    assert [r["tag"] for r in rows] == ["love", "humor"]


def test_sheet_csv_rejects_header_only():
    from sheets import SheetError, parse_csv
    with pytest.raises(SheetError):
        parse_csv("name,tag\n")


# -- notification shaping ----------------------------------------------------

def test_notification_shapes_per_destination():
    from notify import _payload_for
    slack, _ = _payload_for("https://hooks.slack.com/services/x", "T", "M")
    assert b"text" in slack

    discord, _ = _payload_for("https://discord.com/api/webhooks/x", "T", "M")
    assert b"content" in discord

    body, headers = _payload_for("https://ntfy.sh/topic", "T", "M")
    assert body == b"M" and headers["Title"] == "T"

    generic, _ = _payload_for("https://example.test/hook", "T", "M")
    assert b"title" in generic and b"message" in generic


# -- passwords and sessions --------------------------------------------------

def test_password_round_trip(fresh_db):
    import auth
    auth.set_password("correct-horse")
    assert auth.is_configured()
    assert auth.verify_password("correct-horse")
    assert not auth.verify_password("wrong-horse")


def test_short_password_is_refused(fresh_db):
    import auth
    with pytest.raises(ValueError):
        auth.set_password("short")


def test_password_is_not_stored_in_clear(fresh_db):
    import auth
    auth.set_password("super-secret-value")
    stored = fresh_db.get_setting("auth_password")
    assert "super-secret-value" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_session_lifecycle(fresh_db):
    import auth
    auth.set_password("correct-horse")
    token, max_age = auth.create_session("test")
    assert max_age > 0
    assert auth.validate_token(token)
    assert not auth.validate_token("not-a-token")
    auth.destroy_session(token)
    assert not auth.validate_token(token)


def test_changing_password_drops_sessions(fresh_db):
    import auth
    auth.set_password("first-password")
    token, _ = auth.create_session("test")
    assert auth.validate_token(token)
    auth.set_password("second-password")
    assert not auth.validate_token(token)


def test_expired_session_is_rejected(fresh_db):
    import auth
    auth.set_password("correct-horse")
    token, _ = auth.create_session("test")
    fresh_db.execute(
        "UPDATE sessions SET expires_at = ?", (time.time() - 1,)
    )
    assert not auth.validate_token(token)


# -- interrupted run recovery ------------------------------------------------

def test_interrupted_runs_are_recovered(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    fresh_db.add_records(wf, [{"email": "a@test"}, {"email": "b@test"}])
    run_id = fresh_db.create_run(wf, 2, 2)

    records = fresh_db.list_records(wf)
    item = fresh_db.create_run_item(run_id, records[0]["id"])
    fresh_db.set_record_status(records[0]["id"], "processing")

    assert fresh_db.get_run(run_id)["status"] == "running"

    recovered = fresh_db.recover_interrupted_runs()
    assert recovered == 1
    assert fresh_db.get_run(run_id)["status"] == "interrupted"

    # The record goes back to pending so it is picked up again, not lost.
    assert fresh_db.list_records(wf)[0]["status"] == "pending"
    items = fresh_db.list_run_items(run_id)
    assert items[0]["status"] == "failed"
    assert items[0]["issue"] == "interrupted"


def test_recovery_is_a_no_op_when_nothing_is_stuck(fresh_db):
    assert fresh_db.recover_interrupted_runs() == 0


# -- duplicates --------------------------------------------------------------

def test_duplicate_records_are_skipped(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    inserted, skipped = fresh_db.add_records(wf, [{"email": "a@test"}, {"email": "b@test"}])
    assert (inserted, skipped) == (2, 0)
    inserted, skipped = fresh_db.add_records(wf, [{"email": "a@test"}, {"email": "c@test"}])
    assert (inserted, skipped) == (1, 1)


def test_dedupe_key_is_case_insensitive(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    fresh_db.add_records(wf, [{"email": "Person@Test.com"}])
    inserted, skipped = fresh_db.add_records(wf, [{"email": "person@test.com"}])
    assert (inserted, skipped) == (0, 1)


def test_same_record_allowed_in_a_different_workflow(fresh_db):
    a = fresh_db.create_workflow("a", "", "example.com", [], "tester")
    b = fresh_db.create_workflow("b", "", "example.com", [], "tester")
    fresh_db.add_records(a, [{"email": "x@test"}])
    inserted, _ = fresh_db.add_records(b, [{"email": "x@test"}])
    assert inserted == 1


# -- evidence retention ------------------------------------------------------

def test_prune_removes_old_runs_only(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    old_run = fresh_db.create_run(wf, 1, 1)
    new_run = fresh_db.create_run(wf, 1, 1)

    fresh_db.update_run(old_run, status="completed",
                        finished_at=time.time() - 40 * 86400)
    fresh_db.update_run(new_run, status="completed", finished_at=time.time())

    for run_id in (old_run, new_run):
        folder = fresh_db.EVIDENCE_DIR / f"run_{run_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "item_1_step_1.jpg").write_bytes(b"x")

    fresh_db.set_setting("evidence_retention_days", "14")
    pruned = fresh_db.prune_old_data()
    assert pruned["runs"] == 1
    assert not (fresh_db.EVIDENCE_DIR / f"run_{old_run}").exists()
    assert (fresh_db.EVIDENCE_DIR / f"run_{new_run}").exists()


def test_prune_also_removes_database_rows(fresh_db):
    """Pruning screenshots but not their rows would leave the database growing."""
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    fresh_db.add_records(wf, [{"email": "a@test"}, {"email": "b@test"}])
    records = fresh_db.list_records(wf)

    run_id = fresh_db.create_run(wf, 2, 2)
    for record in records:
        item = fresh_db.create_run_item(run_id, record["id"])
        fresh_db.finish_run_item(item, "completed", started_at=time.time())
    fresh_db.log_event(run_id, "something happened")
    fresh_db.update_run(run_id, status="completed",
                        finished_at=time.time() - 40 * 86400)

    assert fresh_db.row_counts()["run_items"] == 2
    assert fresh_db.row_counts()["events"] >= 1

    fresh_db.set_setting("evidence_retention_days", "14")
    pruned = fresh_db.prune_old_data()

    assert pruned["items"] == 2
    assert pruned["events"] >= 1
    assert fresh_db.row_counts()["run_items"] == 0
    assert fresh_db.row_counts()["events"] == 0

    # The run summary is small and is kept, so history and analytics survive.
    assert fresh_db.get_run(run_id) is not None
    assert fresh_db.row_counts()["runs"] == 1


def test_retention_of_zero_keeps_everything(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    run_id = fresh_db.create_run(wf, 1, 1)
    fresh_db.update_run(run_id, status="completed",
                        finished_at=time.time() - 400 * 86400)
    folder = fresh_db.EVIDENCE_DIR / f"run_{run_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(b"x")

    fresh_db.set_setting("evidence_retention_days", "0")
    assert fresh_db.prune_old_data()["runs"] == 0
    assert folder.exists()


# -- workflow scoping --------------------------------------------------------

def test_runs_and_analytics_are_scoped_to_a_workflow(fresh_db):
    a = fresh_db.create_workflow("a", "", "example.com", [], "tester")
    b = fresh_db.create_workflow("b", "", "example.com", [], "tester")

    run_a = fresh_db.create_run(a, 1, 1)
    fresh_db.update_run(run_a, status="completed", completed=5, finished_at=time.time())
    run_b = fresh_db.create_run(b, 1, 1)
    fresh_db.update_run(run_b, status="completed", completed=2, finished_at=time.time())

    assert len(fresh_db.list_runs()) == 2
    assert [r["id"] for r in fresh_db.list_runs(workflow_id=a)] == [run_a]

    assert fresh_db.analytics_summary()["completed"] == 7
    assert fresh_db.analytics_summary(a)["completed"] == 5
    assert fresh_db.analytics_summary(b)["completed"] == 2


def test_review_items_are_scoped_to_a_workflow(fresh_db):
    a = fresh_db.create_workflow("a", "", "example.com", [], "tester")
    b = fresh_db.create_workflow("b", "", "example.com", [], "tester")
    fresh_db.add_records(a, [{"email": "a@test"}])
    fresh_db.add_records(b, [{"email": "b@test"}])

    for wf in (a, b):
        record = fresh_db.list_records(wf)[0]
        run_id = fresh_db.create_run(wf, 1, 1)
        item = fresh_db.create_run_item(run_id, record["id"])
        fresh_db.finish_run_item(item, "review", issue="assertion_failed",
                                 started_at=time.time())

    assert len(fresh_db.list_review_items()) == 2
    scoped = fresh_db.list_review_items(workflow_id=a)
    assert len(scoped) == 1
    assert scoped[0]["data"]["email"] == "a@test"


def test_matching_record_count_respects_the_filter(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    fresh_db.add_records(wf, [{"email": f"user{i}@test"} for i in range(25)])

    assert fresh_db.count_matching_records(wf) == 25
    assert fresh_db.count_matching_records(wf, status="pending") == 25
    assert fresh_db.count_matching_records(wf, status="completed") == 0
    assert fresh_db.count_matching_records(wf, search="user7@") == 1


def test_record_paging_walks_the_whole_queue(fresh_db):
    wf = fresh_db.create_workflow("w", "", "example.com", [], "tester")
    fresh_db.add_records(wf, [{"email": f"user{i:03}@test"} for i in range(250)])

    seen = []
    for offset in range(0, 250, 100):
        page = fresh_db.list_records(wf, limit=100, offset=offset)
        seen.extend(r["data"]["email"] for r in page)

    assert len(seen) == 250
    assert len(set(seen)) == 250, "pages must not overlap"


# -- migrations --------------------------------------------------------------

def test_migrations_add_columns_to_an_existing_database(tmp_path, monkeypatch):
    """An older database must gain the new columns, not need deleting."""
    import sqlite3

    import db as db_module

    path = tmp_path / "old.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    monkeypatch.setattr(db_module, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(db_module, "_conn", None)

    # Build a pre-migration workflows table by hand.
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE workflows (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', target_domain TEXT NOT NULL,"
        " steps_json TEXT NOT NULL DEFAULT '[]', authorized_by TEXT NOT NULL DEFAULT '',"
        " authorized_at REAL, created_at REAL NOT NULL)"
    )
    raw.execute(
        "INSERT INTO workflows(name, target_domain, created_at) VALUES ('legacy','x.test',1)"
    )
    raw.commit()
    raw.close()

    db_module.init_db()

    columns = {r["name"] for r in db_module.query("PRAGMA table_info(workflows)")}
    assert {"schedule_enabled", "schedule_kind", "next_run_at"} <= columns

    survivor = db_module.get_workflow(1)
    assert survivor["name"] == "legacy"
    assert survivor["schedule_enabled"] == 0

    if db_module._conn:
        db_module._conn.close()
        db_module._conn = None


# -- environment seeding -----------------------------------------------------

def test_server_settings_seed_from_environment(tmp_path, monkeypatch):
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "env.db")
    monkeypatch.setattr(db_module, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(db_module, "_conn", None)
    monkeypatch.setenv("FLOWPILOT_HEADLESS", "1")
    monkeypatch.setenv("FLOWPILOT_BROWSER_CHANNEL", "")

    db_module.init_db()
    assert db_module.get_setting("headless") == "1"
    assert db_module.get_setting("browser_channel") == ""
    # Something not in ENV_SEEDABLE keeps its default.
    assert db_module.get_setting("workspace_name") == "FlowPilot"

    if db_module._conn:
        db_module._conn.close()
        db_module._conn = None


def test_arbitrary_settings_cannot_be_set_from_environment(tmp_path, monkeypatch):
    """Only the deployment-relevant keys are seedable."""
    import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "env2.db")
    monkeypatch.setattr(db_module, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(db_module, "_conn", None)
    monkeypatch.setenv("FLOWPILOT_ALLOWED_DOMAINS", "evil.test")

    db_module.init_db()
    assert "evil.test" not in db_module.get_setting("allowed_domains")

    if db_module._conn:
        db_module._conn.close()
        db_module._conn = None
