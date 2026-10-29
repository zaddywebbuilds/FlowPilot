import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "flowpilot.db"
EVIDENCE_DIR = ROOT / "evidence"

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


def init_db():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    c = conn()
    with _lock:
        c.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
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


def get_workflow(workflow_id):
    wf = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if wf:
        wf["steps"] = json.loads(wf.pop("steps_json"))
    return wf


def list_workflows():
    out = []
    for wf in query("SELECT * FROM workflows ORDER BY id"):
        wf["steps"] = json.loads(wf.pop("steps_json"))
        out.append(wf)
    return out


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

def create_run(workflow_id, batch_size, total):
    return execute(
        "INSERT INTO runs(workflow_id, batch_size, status, total, started_at) "
        "VALUES (?,?,?,?,?)",
        (workflow_id, batch_size, "running", total, time.time()),
    )


def get_run(run_id):
    return query_one("SELECT * FROM runs WHERE id = ?", (run_id,))


def list_runs(limit=50):
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


def list_review_items(issue=None, limit=200):
    sql = (
        "SELECT ri.*, r.data_json FROM run_items ri "
        "JOIN records r ON r.id = ri.record_id WHERE ri.status = 'review'"
    )
    params = []
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

def analytics_summary():
    totals = query_one(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(completed),0) AS completed, "
        "COALESCE(SUM(failed),0) AS failed, COALESCE(SUM(review),0) AS review FROM runs"
    ) or {"runs": 0, "completed": 0, "failed": 0, "review": 0}

    avg = query_one(
        "SELECT AVG(duration_ms) AS avg_ms FROM run_items WHERE duration_ms IS NOT NULL"
    )
    totals["avg_ms"] = int(avg["avg_ms"]) if avg and avg["avg_ms"] else 0

    daily = query(
        "SELECT CAST(strftime('%s', date(started_at, 'unixepoch')) AS INTEGER) AS day, "
        "COALESCE(SUM(completed),0) AS completed, COALESCE(SUM(failed),0) AS failed, "
        "COALESCE(SUM(review),0) AS review "
        "FROM runs WHERE started_at IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 7"
    )
    totals["daily"] = list(reversed(daily))

    totals["issues"] = query(
        "SELECT COALESCE(issue, 'unknown') AS issue, COUNT(*) AS n FROM run_items "
        "WHERE status IN ('failed','review') GROUP BY issue ORDER BY n DESC"
    )
    return totals
