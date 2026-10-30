import json
import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Overridable so tests and deployments can put state somewhere else.
DB_PATH = Path(os.environ.get("FLOWPILOT_DB_PATH") or ROOT / "flowpilot.db")
EVIDENCE_DIR = Path(os.environ.get("FLOWPILOT_EVIDENCE_DIR") or ROOT / "evidence")

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    target_domain   TEXT NOT NULL,
    steps_json      TEXT NOT NULL DEFAULT '[]',
    authorized_by   TEXT NOT NULL DEFAULT '',
    authorized_at   REAL,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id     INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    data_json       TEXT NOT NULL,
    dedupe_key      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    source          TEXT NOT NULL DEFAULT 'manual',
    last_run_at     REAL,
    created_at      REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_records_dedupe
    ON records(workflow_id, dedupe_key);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(workflow_id, status);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id     INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    batch_size      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    total           INTEGER NOT NULL DEFAULT 0,
    completed       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    review          INTEGER NOT NULL DEFAULT 0,
    started_at      REAL NOT NULL,
    finished_at     REAL
);

CREATE TABLE IF NOT EXISTS run_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    record_id       INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending',
    error           TEXT,
    issue           TEXT,
    evidence_json   TEXT NOT NULL DEFAULT '{}',
    duration_ms     INTEGER,
    started_at      REAL,
    finished_at     REAL
);

CREATE INDEX IF NOT EXISTS idx_run_items_run ON run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_run_items_status ON run_items(status);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    ts              REAL NOT NULL,
    level           TEXT NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    data_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash      TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    label           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
"""

# Columns added after the first release. Existing databases are migrated in
# place on startup, so an upgrade never needs the file to be deleted.
MIGRATIONS = [
    ("workflows", "schedule_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("workflows", "schedule_kind", "TEXT NOT NULL DEFAULT 'interval'"),
    ("workflows", "schedule_minutes", "INTEGER NOT NULL DEFAULT 60"),
    ("workflows", "schedule_at", "TEXT NOT NULL DEFAULT '09:00'"),
    ("workflows", "schedule_batch", "INTEGER NOT NULL DEFAULT 25"),
    ("workflows", "next_run_at", "REAL"),
    ("runs", "trigger", "TEXT NOT NULL DEFAULT 'manual'"),
    # Cookies captured after a human signed in once. Never a password.
    ("workflows", "session_state", "TEXT"),
    ("workflows", "session_saved_at", "REAL"),
]

DEFAULT_SETTINGS = {
    "workspace_name": "FlowPilot",
    "default_batch_size": "25",
    "min_delay_seconds": "2",
    "max_delay_seconds": "5",
    "step_timeout_seconds": "20",
    "capture_screenshots": "1",
    "skip_duplicates": "1",
    "stop_on_consecutive_failures": "5",
    "respect_robots_txt": "1",
    "browser_channel": "chrome",
    "headless": "0",
    "session_days": "14",
    "password_is_temporary": "0",
    "evidence_retention_days": "14",
    "scheduler_enabled": "1",
    "notify_webhook_url": "",
    "notify_on_finish": "1",
    "notify_on_review": "1",
    "notify_on_failure": "1",
    "allowed_domains": "quotes.toscrape.com\nbooks.toscrape.com\nlocalhost\n127.0.0.1",
}


def conn():
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
        return _conn


# Only these may be seeded from the environment, so a server install can set
# headless mode and the bundled browser without a first visit to Settings.
ENV_SEEDABLE = ("headless", "browser_channel", "min_delay_seconds", "max_delay_seconds")


def _ensure_column(c, table, column, declaration):
    existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def recover_interrupted_runs():
    """Close out runs that were in flight when the process stopped.

    Without this a crash or reboot leaves the run marked 'running' forever and
    its records stuck on 'processing', so they are never picked up again.
    """
    stuck = query("SELECT id FROM runs WHERE status IN ('running', 'paused')")
    if not stuck:
        return 0
    ids = [r["id"] for r in stuck]
    placeholders = ",".join("?" * len(ids))
    execute(
        f"UPDATE runs SET status = 'interrupted', finished_at = ? "
        f"WHERE id IN ({placeholders})",
        (time.time(), *ids),
    )
    execute(
        "UPDATE run_items SET status = 'failed', issue = 'interrupted', "
        f"error = 'Server stopped during this run' WHERE status = 'processing' "
        f"AND run_id IN ({placeholders})",
        tuple(ids),
    )
    execute("UPDATE records SET status = 'pending' WHERE status = 'processing'")
    for run_id in ids:
        log_event(run_id, "Run was interrupted by a server restart.", "warn")
    return len(ids)


def init_db():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    c = conn()
    with _lock:
        c.executescript(SCHEMA)
        for table, column, declaration in MIGRATIONS:
            _ensure_column(c, table, column, declaration)
        for key, value in DEFAULT_SETTINGS.items():
            if key in ENV_SEEDABLE:
                value = os.environ.get(f"FLOWPILOT_{key.upper()}", value)
            c.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        c.commit()


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def execute_count(sql, params=()):
    """Like execute, but returns how many rows were affected."""
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.rowcount


def execute_many(sql, seq):
    with _lock:
        c = conn()
        cur = c.executemany(sql, seq)
        c.commit()
        return cur.rowcount


# settings -------------------------------------------------------------------

def get_settings():
    return {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}


def get_setting(key, default=None):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def session_days():
    try:
        return max(1, int(get_setting("session_days", "14")))
    except ValueError:
        return 14


def allowed_domains():
    raw = get_setting("allowed_domains", "") or ""
    return [d.strip().lower() for d in raw.splitlines() if d.strip()]


# events ---------------------------------------------------------------------

def log_event(run_id, message, level="info", data=None):
    return execute(
        "INSERT INTO events(run_id, ts, level, message, data_json) VALUES (?,?,?,?,?)",
        (run_id, time.time(), level, message, json.dumps(data or {})),
    )


# workflows ------------------------------------------------------------------

def create_workflow(name, description, target_domain, steps, authorized_by):
    return execute(
        "INSERT INTO workflows(name, description, target_domain, steps_json, "
        "authorized_by, authorized_at, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            name,
            description,
            target_domain.lower(),
            json.dumps(steps),
            authorized_by,
            time.time() if authorized_by else None,
            time.time(),
        ),
    )


def _public_workflow(wf):
    """Strip the saved cookies: the dashboard only needs to know one exists."""
    wf["steps"] = json.loads(wf.pop("steps_json"))
    wf["has_session"] = bool(wf.pop("session_state", None))
    return wf


def get_workflow(workflow_id):
    wf = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    return _public_workflow(wf) if wf else None


def list_workflows():
    out = []
    for wf in query("SELECT * FROM workflows ORDER BY id"):
        _public_workflow(wf)
        wf["pending"] = count_records(wf["id"]).get("pending", 0)
        out.append(wf)
    return out


def update_workflow(workflow_id, **fields):
    if "steps" in fields:
        fields["steps_json"] = json.dumps(fields.pop("steps"))
    if "target_domain" in fields:
        fields["target_domain"] = fields["target_domain"].lower()
    allowed = {
        "name", "description", "target_domain", "steps_json", "authorized_by",
        "schedule_enabled", "schedule_kind", "schedule_minutes", "schedule_at",
        "schedule_batch", "next_run_at",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return get_workflow(workflow_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE workflows SET {sets} WHERE id = ?", (*fields.values(), workflow_id))
    return get_workflow(workflow_id)


def delete_workflow(workflow_id):
    execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))


def save_session(workflow_id, state):
    """Keep the cookies from a sign-in a human did, so runs start logged in."""
    execute(
        "UPDATE workflows SET session_state = ?, session_saved_at = ? WHERE id = ?",
        (json.dumps(state), time.time(), workflow_id),
    )


def clear_session(workflow_id):
    execute(
        "UPDATE workflows SET session_state = NULL, session_saved_at = NULL WHERE id = ?",
        (workflow_id,),
    )


def get_session(workflow_id):
    row = query_one("SELECT session_state FROM workflows WHERE id = ?", (workflow_id,))
    if not row or not row["session_state"]:
        return None
    try:
        return json.loads(row["session_state"])
    except (ValueError, TypeError):
        return None


def scheduled_workflows():
    out = []
    for wf in query("SELECT * FROM workflows WHERE schedule_enabled = 1 ORDER BY id"):
        _public_workflow(wf)
        wf["pending"] = count_records(wf["id"]).get("pending", 0)
        out.append(wf)
    return out


# evidence ------------------------------------------------------------------

def prune_old_data():
    """Drop per-record detail for runs past the retention window.

    Each record writes a screenshot per step plus a run_items row, and each run
    writes event rows, so at any real volume both the disk and the database grow
    without bound. The run summary row is small and is kept, so Run History and
    Analytics stay intact; only the bulky per-record detail goes.
    """
    try:
        days = int(get_setting("evidence_retention_days", "14"))
    except ValueError:
        days = 14
    if days <= 0:
        return {"runs": 0, "files": 0, "items": 0, "events": 0}

    cutoff = time.time() - days * 86400
    old = query(
        "SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?",
        (cutoff,),
    )
    if not old:
        return {"runs": 0, "files": 0, "items": 0, "events": 0}

    ids = [row["id"] for row in old]
    files = 0
    folders = 0

    for run_id in ids:
        folder = EVIDENCE_DIR / f"run_{run_id}"
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            try:
                child.unlink()
                files += 1
            except OSError:
                pass
        try:
            folder.rmdir()
            folders += 1
        except OSError:
            pass

    placeholders = ",".join("?" * len(ids))
    items = execute_count(
        f"DELETE FROM run_items WHERE run_id IN ({placeholders})", tuple(ids)
    )
    events = execute_count(
        f"DELETE FROM events WHERE run_id IN ({placeholders})", tuple(ids)
    )

    return {"runs": folders, "files": files, "items": items, "events": events}


def database_size():
    try:
        return DB_PATH.stat().st_size
    except OSError:
        return 0


def row_counts():
    return {
        table: query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        for table in ("records", "runs", "run_items", "events")
    }


def evidence_usage():
    total = 0
    count = 0
    if EVIDENCE_DIR.is_dir():
        for path in EVIDENCE_DIR.rglob("*.jpg"):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                pass
    return {"files": count, "bytes": total}


# records --------------------------------------------------------------------

def add_records(workflow_id, rows, source="manual"):
    """rows: list of dicts. Returns (inserted, skipped_duplicates)."""
    inserted = 0
    skipped = 0
    now = time.time()
    for data in rows:
        key = (data.get("dedupe_key") or data.get("email") or json.dumps(data, sort_keys=True)).strip().lower()
        try:
            execute(
                "INSERT INTO records(workflow_id, data_json, dedupe_key, status, source, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (workflow_id, json.dumps(data), key, "pending", source, now),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    return inserted, skipped


def list_records(workflow_id, status=None, search=None, limit=100, offset=0):
    sql = "SELECT * FROM records WHERE workflow_id = ?"
    params = [workflow_id]
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    if search:
        sql += " AND data_json LIKE ?"
        params.append(f"%{search}%")
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = query(sql, tuple(params))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json"))
    return rows


def count_matching_records(workflow_id, status=None, search=None):
    """How many records the current filter matches, for pagination."""
    sql = "SELECT COUNT(*) AS n FROM records WHERE workflow_id = ?"
    params = [workflow_id]
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    if search:
        sql += " AND data_json LIKE ?"
        params.append(f"%{search}%")
    row = query_one(sql, tuple(params))
    return row["n"] if row else 0


def count_records(workflow_id):
    rows = query(
        "SELECT status, COUNT(*) AS n FROM records WHERE workflow_id = ? GROUP BY status",
        (workflow_id,),
    )
    counts = {r["status"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def claim_pending_records(workflow_id, limit):
    return list_records(workflow_id, status="pending", limit=limit)


def set_record_status(record_id, status):
    execute(
        "UPDATE records SET status = ?, last_run_at = ? WHERE id = ?",
        (status, time.time(), record_id),
    )


# runs -----------------------------------------------------------------------

def create_run(workflow_id, batch_size, total, trigger="manual"):
    return execute(
        "INSERT INTO runs(workflow_id, batch_size, status, total, started_at, trigger) "
        "VALUES (?,?,?,?,?,?)",
        (workflow_id, batch_size, "running", total, time.time(), trigger),
    )


def get_run(run_id):
    return query_one("SELECT * FROM runs WHERE id = ?", (run_id,))


def list_runs(limit=50, workflow_id=None):
    if workflow_id:
        return query(
            "SELECT * FROM runs WHERE workflow_id = ? ORDER BY id DESC LIMIT ?",
            (workflow_id, limit),
        )
    return query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def update_run(run_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))


def bump_run(run_id, column):
    execute(f"UPDATE runs SET {column} = {column} + 1 WHERE id = ?", (run_id,))


# run items ------------------------------------------------------------------

def create_run_item(run_id, record_id):
    return execute(
        "INSERT INTO run_items(run_id, record_id, status, started_at) VALUES (?,?,?,?)",
        (run_id, record_id, "processing", time.time()),
    )


def finish_run_item(item_id, status, error=None, issue=None, evidence=None, started_at=None):
    now = time.time()
    duration = int((now - started_at) * 1000) if started_at else None
    execute(
        "UPDATE run_items SET status = ?, error = ?, issue = ?, evidence_json = ?, "
        "duration_ms = ?, finished_at = ? WHERE id = ?",
        (status, error, issue, json.dumps(evidence or {}), duration, now, item_id),
    )


def list_run_items(run_id):
    rows = query(
        "SELECT ri.*, r.data_json FROM run_items ri "
        "JOIN records r ON r.id = ri.record_id WHERE ri.run_id = ? ORDER BY ri.id",
        (run_id,),
    )
    for r in rows:
        r["data"] = json.loads(r.pop("data_json"))
        r["evidence"] = json.loads(r.pop("evidence_json"))
    return rows


def list_review_items(issue=None, limit=200, workflow_id=None):
    sql = (
        "SELECT ri.*, r.data_json FROM run_items ri "
        "JOIN records r ON r.id = ri.record_id WHERE ri.status = 'review'"
    )
    params = []
    if workflow_id:
        sql += " AND r.workflow_id = ?"
        params.append(workflow_id)
    if issue and issue != "all":
        sql += " AND ri.issue = ?"
        params.append(issue)
    sql += " ORDER BY ri.id DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, tuple(params))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json"))
        r["evidence"] = json.loads(r.pop("evidence_json"))
    return rows


def resolve_run_item(item_id, resolution):
    item = query_one("SELECT * FROM run_items WHERE id = ?", (item_id,))
    if not item:
        return None
    new_status = "completed" if resolution == "approve" else "failed"
    execute("UPDATE run_items SET status = ? WHERE id = ?", (new_status, item_id))
    set_record_status(item["record_id"], "completed" if resolution == "approve" else "pending")
    return new_status


# analytics ------------------------------------------------------------------

def analytics_summary(workflow_id=None):
    scope = " WHERE workflow_id = ?" if workflow_id else ""
    args = (workflow_id,) if workflow_id else ()

    totals = query_one(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(completed),0) AS completed, "
        "COALESCE(SUM(failed),0) AS failed, COALESCE(SUM(review),0) AS review "
        f"FROM runs{scope}",
        args,
    ) or {"runs": 0, "completed": 0, "failed": 0, "review": 0}

    item_scope = (
        " AND ri.run_id IN (SELECT id FROM runs WHERE workflow_id = ?)"
        if workflow_id else ""
    )

    avg = query_one(
        "SELECT AVG(ri.duration_ms) AS avg_ms FROM run_items ri "
        f"WHERE ri.duration_ms IS NOT NULL{item_scope}",
        args,
    )
    totals["avg_ms"] = int(avg["avg_ms"]) if avg and avg["avg_ms"] else 0

    daily = query(
        "SELECT CAST(strftime('%s', date(started_at, 'unixepoch')) AS INTEGER) AS day, "
        "COALESCE(SUM(completed),0) AS completed, COALESCE(SUM(failed),0) AS failed, "
        "COALESCE(SUM(review),0) AS review FROM runs "
        f"WHERE started_at IS NOT NULL{' AND workflow_id = ?' if workflow_id else ''} "
        "GROUP BY day ORDER BY day DESC LIMIT 7",
        args,
    )
    totals["daily"] = list(reversed(daily))

    totals["issues"] = query(
        "SELECT COALESCE(ri.issue, 'unknown') AS issue, COUNT(*) AS n FROM run_items ri "
        f"WHERE ri.status IN ('failed','review'){item_scope} "
        "GROUP BY ri.issue ORDER BY n DESC",
        args,
    )
    return totals
