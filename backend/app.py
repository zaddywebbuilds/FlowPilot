import asyncio
import csv
import io
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import db
import notify
import sheets
from picker import PickerError, picker
from runner import AuthorizationError, engine
from runner import test_workflow as run_test
from scheduler import compute_next_run, scheduler

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

DEMO_WORKFLOW = {
    "name": "Quote capture demo",
    "description": (
        "Opens a public scraping-practice site, captures the featured quote and "
        "verifies the page rendered. Safe to run as-is."
    ),
    "target_domain": "quotes.toscrape.com",
    "authorized_by": "Public demo site (toscrape.com), published for automation practice",
    "steps": [
        {"type": "goto", "url": "https://quotes.toscrape.com/tag/{{tag}}/", "label": "Open tag page"},
        {"type": "wait_for", "selector": ".quote", "label": "Wait for quotes"},
        {"type": "extract", "selector": ".quote .text", "field": "quote", "label": "Extract quote"},
        {"type": "extract", "selector": ".quote .author", "field": "author", "label": "Extract author"},
        {"type": "assert_text", "selector": ".quote .text", "value": "", "label": "Verify quote present"},
    ],
}

DEMO_RECORDS = [
    {"name": "Love tag", "tag": "love", "dedupe_key": "tag:love"},
    {"name": "Humor tag", "tag": "humor", "dedupe_key": "tag:humor"},
    {"name": "Books tag", "tag": "books", "dedupe_key": "tag:books"},
    {"name": "Truth tag", "tag": "truth", "dedupe_key": "tag:truth"},
    {"name": "Life tag", "tag": "life", "dedupe_key": "tag:life"},
]


def seed():
    if db.list_workflows():
        return
    wf_id = db.create_workflow(
        DEMO_WORKFLOW["name"],
        DEMO_WORKFLOW["description"],
        DEMO_WORKFLOW["target_domain"],
        DEMO_WORKFLOW["steps"],
        DEMO_WORKFLOW["authorized_by"],
    )
    db.add_records(wf_id, DEMO_RECORDS, source="seed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    seed()
    recovered = db.recover_interrupted_runs()
    if recovered:
        print(f"[flowpilot] recovered {recovered} interrupted run(s) from a previous stop")
    db.prune_old_data()
    scheduler.start()
    yield
    await scheduler.stop()
    await picker.close()


app = FastAPI(title="FlowPilot", lifespan=lifespan)


def active_workflow_id(workflow_id=None):
    if workflow_id:
        return workflow_id
    workflows = db.list_workflows()
    if not workflows:
        raise HTTPException(404, "No workflows configured.")
    return workflows[0]["id"]


# -- models ------------------------------------------------------------------

class StartRun(BaseModel):
    workflow_id: int | None = None
    batch_size: int = 25


class ImportPayload(BaseModel):
    workflow_id: int | None = None
    csv_text: str


class WorkflowPayload(BaseModel):
    name: str
    description: str = ""
    target_domain: str
    authorized_by: str
    steps: list[dict]


class WorkflowPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    target_domain: str | None = None
    authorized_by: str | None = None
    steps: list[dict] | None = None


class SchedulePayload(BaseModel):
    enabled: bool
    kind: str = "interval"          # interval | daily
    minutes: int = 60
    at: str = "09:00"
    batch: int = 25


class SheetPayload(BaseModel):
    workflow_id: int | None = None
    url: str


class SettingsPayload(BaseModel):
    values: dict


class ResolvePayload(BaseModel):
    resolution: str  # approve | reject


class PasswordPayload(BaseModel):
    password: str


# -- authentication ----------------------------------------------------------

PUBLIC_PATHS = {
    "/login",
    "/login.html",
    "/login.js",
    "/styles.css",
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
}

# Reachable while signed in with a handover password, and nothing else.
PASSWORD_CHANGE_PATHS = {"/api/auth/password", "/api/auth/logout"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    # Until a password exists nothing else is reachable, so an instance that is
    # deployed but not yet set up is never left open.
    if not auth.is_configured():
        if path.startswith("/api/") or path == "/ws":
            return JSONResponse({"detail": "Setup required."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    if auth.validate_token(request.cookies.get(auth.COOKIE_NAME)):
        # A handover password gets you exactly as far as replacing it.
        if auth.must_change_password() and path not in PASSWORD_CHANGE_PATHS:
            if path.startswith("/api/") or path == "/ws":
                return JSONResponse(
                    {"detail": "Set your own password before using the dashboard."},
                    status_code=403,
                )
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    if path.startswith("/api/") or path == "/ws":
        return JSONResponse({"detail": "Not authenticated."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _issue_cookie(response: Response, request: Request, label: str) -> None:
    token, max_age = auth.create_session(label)
    response.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=max_age, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {
        "configured": auth.is_configured(),
        "authenticated": auth.validate_token(request.cookies.get(auth.COOKIE_NAME)),
        "must_change": auth.must_change_password(),
    }


@app.post("/api/auth/handover")
def auth_handover(request: Request):
    """Replace the password with a one-time one for handing the box to someone.

    Returned once and never stored in the clear, so it cannot be recovered later:
    if it is lost, generate another.
    """
    temporary = auth.generate_temporary_password()
    # set_password drops every session, including this one. That is correct:
    # handing the instance over should sign the previous owner out.
    auth.set_password(temporary, temporary=True)
    response = JSONResponse({"password": temporary})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.post("/api/auth/setup")
def auth_setup(payload: PasswordPayload, request: Request):
    if auth.is_configured():
        raise HTTPException(400, "A password is already set.")
    try:
        auth.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    response = JSONResponse({"ok": True})
    _issue_cookie(response, request, "setup")
    return response


@app.post("/api/auth/login")
def auth_login(payload: PasswordPayload, request: Request):
    if not auth.is_configured():
        raise HTTPException(400, "No password has been set yet.")
    if not auth.verify_password(payload.password):
        raise HTTPException(401, "Incorrect password.")
    response = JSONResponse({"ok": True})
    _issue_cookie(response, request, "login")
    return response


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.destroy_session(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.post("/api/auth/password")
def auth_change_password(payload: PasswordPayload, request: Request):
    """Changing the password invalidates every session, including this one."""
    try:
        auth.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    response = JSONResponse({"ok": True})
    _issue_cookie(response, request, "password change")
    return response


# -- overview ----------------------------------------------------------------

@app.get("/api/overview")
def overview(workflow_id: int | None = None):
    wid = active_workflow_id(workflow_id)
    counts = db.count_records(wid)
    runs = db.list_runs(limit=5)
    recent = db.query(
        "SELECT ri.id, ri.status, ri.issue, ri.finished_at, r.data_json "
        "FROM run_items ri JOIN records r ON r.id = ri.record_id "
        "WHERE ri.finished_at IS NOT NULL ORDER BY ri.id DESC LIMIT 8"
    )
    for r in recent:
        r["data"] = json.loads(r.pop("data_json"))
    return {
        "workflow": db.get_workflow(wid),
        "workflows": db.list_workflows(),
        "counts": {
            "total": counts.get("total", 0),
            "completed": counts.get("completed", 0),
            "pending": counts.get("pending", 0),
            "review": counts.get("review", 0),
            "failed": counts.get("failed", 0),
        },
        "recent_runs": runs,
        "recent_activity": recent,
        "engine": engine.snapshot(),
    }


# -- workflows ---------------------------------------------------------------

@app.get("/api/workflows")
def get_workflows():
    return db.list_workflows()


@app.post("/api/workflows")
def post_workflow(payload: WorkflowPayload):
    if not payload.authorized_by.strip():
        raise HTTPException(
            400,
            "Record who authorized automating this target before saving the workflow.",
        )
    wf_id = db.create_workflow(
        payload.name, payload.description, payload.target_domain,
        payload.steps, payload.authorized_by,
    )
    return db.get_workflow(wf_id)


@app.patch("/api/workflows/{workflow_id}")
def patch_workflow(workflow_id: int, payload: WorkflowPatch):
    if not db.get_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found.")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "authorized_by" in fields and not fields["authorized_by"].strip():
        raise HTTPException(400, "Authorization cannot be blank.")
    return db.update_workflow(workflow_id, **fields)


@app.delete("/api/workflows/{workflow_id}")
def remove_workflow(workflow_id: int):
    if not db.get_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found.")
    if engine.status != "idle" and engine.workflow_id == workflow_id:
        raise HTTPException(409, "That workflow is running. Stop the run first.")
    db.delete_workflow(workflow_id)
    return {"ok": True}


@app.put("/api/workflows/{workflow_id}/schedule")
def set_schedule(workflow_id: int, payload: SchedulePayload):
    workflow = db.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found.")
    if payload.kind not in ("interval", "daily"):
        raise HTTPException(400, "kind must be 'interval' or 'daily'.")

    updated = {
        "schedule_enabled": 1 if payload.enabled else 0,
        "schedule_kind": payload.kind,
        "schedule_minutes": max(1, payload.minutes),
        "schedule_at": payload.at,
        "schedule_batch": max(1, payload.batch),
    }
    updated["next_run_at"] = (
        compute_next_run({**workflow, **updated}) if payload.enabled else None
    )
    return db.update_workflow(workflow_id, **updated)


@app.get("/api/schedule")
def schedule_status():
    return scheduler.status()


# -- element picker ----------------------------------------------------------

class PickerOpen(BaseModel):
    url: str
    workflow_id: int | None = None


class PickerText(BaseModel):
    text: str


class PickerKey(BaseModel):
    key: str


class PickerPoint(BaseModel):
    x: float
    y: float


class PickerScroll(BaseModel):
    direction: str = "down"


def _picker_guard():
    if engine.status != "idle":
        raise HTTPException(409, "A run is in progress. Stop it before picking elements.")


@app.post("/api/picker/open")
async def picker_open(payload: PickerOpen):
    _picker_guard()
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        return await picker.open(url, payload.workflow_id)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc))
    except PickerError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Could not open that page: {exc}")


@app.post("/api/picker/scroll")
async def picker_scroll(payload: PickerScroll):
    try:
        return await picker.scroll(payload.direction)
    except PickerError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/picker/pick")
async def picker_pick(payload: PickerPoint):
    try:
        return await picker.pick(payload.x, payload.y)
    except PickerError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/picker/click")
async def picker_click(payload: PickerPoint):
    try:
        return await picker.click(payload.x, payload.y)
    except PickerError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/picker/type")
async def picker_type(payload: PickerText):
    try:
        return await picker.type(payload.text)
    except PickerError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/picker/key")
async def picker_key(payload: PickerKey):
    try:
        return await picker.press(payload.key)
    except PickerError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/picker/close")
async def picker_close():
    await picker.close()
    return {"ok": True}


# -- saved sign-in -----------------------------------------------------------

@app.post("/api/workflows/{workflow_id}/session")
async def save_workflow_session(workflow_id: int):
    """Keep the cookies from the sign-in the operator just did in the picker.

    Only the resulting cookies are stored, never the password that produced them.
    """
    if not db.get_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found.")
    try:
        state = await picker.storage_state()
    except PickerError as exc:
        raise HTTPException(400, str(exc))

    cookies = state.get("cookies", [])
    if not cookies:
        raise HTTPException(
            400,
            "That page set no cookies, so there is no sign-in to save. "
            "Log in first, then save.",
        )
    db.save_session(workflow_id, state)
    return {"ok": True, "cookies": len(cookies)}


@app.delete("/api/workflows/{workflow_id}/session")
def clear_workflow_session(workflow_id: int):
    if not db.get_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found.")
    db.clear_session(workflow_id)
    return {"ok": True}


# -- test a workflow against one record --------------------------------------

class TestPayload(BaseModel):
    steps: list[dict] | None = None
    target_domain: str | None = None
    record_id: int | None = None
    data: dict | None = None


@app.post("/api/workflows/{workflow_id}/test")
async def test_workflow_endpoint(workflow_id: int, payload: TestPayload):
    _picker_guard()
    workflow = db.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found.")

    # Unsaved edits can be tested, so the editor does not force a save first.
    # None means "not sent"; an empty list means the editor really is empty, and
    # silently testing the saved steps instead would be a lie.
    if payload.steps is not None:
        workflow = {**workflow, "steps": payload.steps}
    if payload.target_domain:
        workflow = {**workflow, "target_domain": payload.target_domain.lower()}
    if not workflow["steps"]:
        raise HTTPException(400, "This workflow has no steps.")

    data = payload.data
    if data is None:
        if payload.record_id:
            row = db.query_one("SELECT data_json FROM records WHERE id = ?", (payload.record_id,))
            data = json.loads(row["data_json"]) if row else None
        else:
            pending = db.list_records(workflow_id, status="pending", limit=1)
            any_record = pending or db.list_records(workflow_id, limit=1)
            data = any_record[0]["data"] if any_record else {}

    try:
        result = await run_test(workflow, data or {})
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Test could not run: {exc}")

    return {**result, "record": data}


# -- records / queue ---------------------------------------------------------

@app.get("/api/records")
def get_records(
    workflow_id: int | None = None,
    status: str = "all",
    search: str = "",
    limit: int = 100,
    offset: int = 0,
):
    wid = active_workflow_id(workflow_id)
    counts = db.count_records(wid)
    matching = db.count_matching_records(wid, status, search)
    return {
        "records": db.list_records(wid, status, search, limit, offset),
        "counts": counts,
        "matching": matching,
        "offset": offset,
        "limit": limit,
    }


@app.post("/api/records/import")
def import_records(payload: ImportPayload):
    wid = active_workflow_id(payload.workflow_id)
    text = payload.csv_text.strip()
    if not text:
        raise HTTPException(400, "Nothing to import.")

    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader if any((v or "").strip() for v in r.values())]
    if not rows:
        raise HTTPException(400, "No rows parsed. Include a header row.")

    inserted, skipped = db.add_records(wid, rows, source="csv")
    return {"inserted": inserted, "skipped_duplicates": skipped}


@app.post("/api/records/import-sheet")
def import_sheet(payload: SheetPayload):
    wid = active_workflow_id(payload.workflow_id)
    try:
        rows = sheets.fetch(payload.url.strip())
    except sheets.SheetError as exc:
        raise HTTPException(400, str(exc))

    inserted, skipped = db.add_records(wid, rows, source="sheet")
    return {
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "columns": list(rows[0].keys()),
    }


@app.delete("/api/records/{record_id}")
def delete_record(record_id: int):
    db.execute("DELETE FROM records WHERE id = ?", (record_id,))
    return {"ok": True}


@app.post("/api/records/{record_id}/reset")
def reset_record(record_id: int):
    db.set_record_status(record_id, "pending")
    return {"ok": True}


# -- run control -------------------------------------------------------------

@app.get("/api/state")
def get_state():
    return engine.snapshot()


@app.post("/api/run/start")
async def start_run(payload: StartRun):
    wid = active_workflow_id(payload.workflow_id)
    try:
        run_id = await engine.start(wid, max(1, payload.batch_size))
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id}


@app.post("/api/run/pause")
def pause_run():
    engine.pause()
    return engine.snapshot()


@app.post("/api/run/resume")
def resume_run():
    engine.resume()
    return engine.snapshot()


@app.post("/api/run/stop")
def stop_run():
    engine.stop()
    return engine.snapshot()


# -- run history -------------------------------------------------------------

@app.get("/api/runs")
def get_runs(limit: int = 50, workflow_id: int | None = None):
    runs = db.list_runs(limit, workflow_id)
    for r in runs:
        if r["finished_at"] and r["started_at"]:
            r["duration_s"] = int(r["finished_at"] - r["started_at"])
    return runs


@app.get("/api/runs/{run_id}")
def get_run_detail(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    return {"run": run, "items": db.list_run_items(run_id)}


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int):
    items = db.list_run_items(run_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["item_id", "status", "issue", "duration_ms", "error", "data", "extracted"])
    for i in items:
        writer.writerow([
            i["id"], i["status"], i["issue"] or "", i["duration_ms"] or "",
            i["error"] or "", json.dumps(i["data"]),
            json.dumps(i["evidence"].get("extracted", {})),
        ])
    from fastapi.responses import Response
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id}.csv"'},
    )


# -- review center -----------------------------------------------------------

@app.get("/api/review")
def get_review(issue: str = "all", workflow_id: int | None = None):
    items = db.list_review_items(issue, workflow_id=workflow_id)
    sql = (
        "SELECT COALESCE(ri.issue,'unknown') AS issue, COUNT(*) AS n FROM run_items ri "
        "JOIN records r ON r.id = ri.record_id WHERE ri.status = 'review'"
    )
    params = []
    if workflow_id:
        sql += " AND r.workflow_id = ?"
        params.append(workflow_id)
    counts = db.query(sql + " GROUP BY ri.issue", tuple(params))
    return {"items": items, "counts": {c["issue"]: c["n"] for c in counts}}


@app.post("/api/review/{item_id}/resolve")
def resolve(item_id: int, payload: ResolvePayload):
    if payload.resolution not in ("approve", "reject"):
        raise HTTPException(400, "resolution must be 'approve' or 'reject'.")
    status = db.resolve_run_item(item_id, payload.resolution)
    if status is None:
        raise HTTPException(404, "Review item not found.")
    return {"status": status}


# -- analytics ---------------------------------------------------------------

@app.get("/api/analytics")
def analytics(workflow_id: int | None = None):
    return db.analytics_summary(workflow_id)


# -- integrations ------------------------------------------------------------

@app.post("/api/notify/test")
def notify_test():
    url = (db.get_setting("notify_webhook_url", "") or "").strip()
    if not url:
        raise HTTPException(400, "No webhook URL is set.")
    ok = notify.send(
        "FlowPilot test",
        "If you are reading this, notifications are working.",
    )
    if not ok:
        raise HTTPException(
            502, "The webhook did not accept the message. Check the URL."
        )
    return {"ok": True}


@app.get("/api/evidence")
def evidence_status():
    usage = db.evidence_usage()
    return {
        **usage,
        "megabytes": round(usage["bytes"] / 1048576, 1),
        "retention_days": db.get_setting("evidence_retention_days", "14"),
        "database_mb": round(db.database_size() / 1048576, 1),
        "rows": db.row_counts(),
    }


@app.post("/api/evidence/prune")
def evidence_prune():
    pruned = db.prune_old_data()
    return {"pruned": pruned, **db.evidence_usage(), "rows": db.row_counts()}


@app.get("/api/integrations")
def integrations():
    workflows = db.list_workflows()
    return {
        "allowed_domains": db.allowed_domains(),
        "workflows": [
            {
                "id": w["id"],
                "name": w["name"],
                "target_domain": w["target_domain"],
                "authorized_by": w["authorized_by"],
                "authorized_at": w["authorized_at"],
                "allowlisted": w["target_domain"] in db.allowed_domains(),
                "step_count": len(w["steps"]),
            }
            for w in workflows
        ],
        "respect_robots_txt": db.get_setting("respect_robots_txt") == "1",
        "evidence_dir": str(db.EVIDENCE_DIR),
        "database": str(db.DB_PATH),
    }


# -- settings ----------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(payload: SettingsPayload):
    for key, value in payload.values.items():
        db.set_setting(key, value)
    return db.get_settings()


# -- live events -------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # HTTP middleware does not run for websocket scopes, so check the cookie here.
    if auth.is_configured() and not auth.validate_token(ws.cookies.get(auth.COOKIE_NAME)):
        await ws.close(code=1008)
        return
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    engine.subscribe(queue)
    try:
        await ws.send_json({"kind": "state", **engine.snapshot()})
        while True:
            message = await queue.get()
            await ws.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        engine.unsubscribe(queue)


# -- static ------------------------------------------------------------------

@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Keep the dashboard out of the browser cache.

    Assets, because a client otherwise keeps running an old app.js after an
    update and reports bugs that were already fixed. API responses, because a
    cached one shows stale state: a cached /api/auth/status will offer the
    first-run setup screen on an instance that already has a password.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path in ("/", "/index.html", "/login") or path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


db.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/evidence", StaticFiles(directory=str(db.EVIDENCE_DIR)), name="evidence")


def _render(filename: str) -> HTMLResponse:
    """Stamp each asset URL with its file mtime so an update always busts the cache."""
    html = (FRONTEND / filename).read_text(encoding="utf-8")
    for asset in ("app.js", "styles.css", "login.js"):
        path = FRONTEND / asset
        if path.exists():
            html = html.replace(f'"/{asset}"', f'"/{asset}?v={int(path.stat().st_mtime)}"')
    return HTMLResponse(html)


@app.get("/")
def index():
    return _render("index.html")


@app.get("/login")
def login_page():
    return _render("login.html")


app.mount("/", StaticFiles(directory=str(FRONTEND)), name="frontend")
