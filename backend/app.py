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
from runner import AuthorizationError, engine

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
    yield


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
    }


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
    return {
        "records": db.list_records(wid, status, search, limit, offset),
        "counts": db.count_records(wid),
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
def get_runs(limit: int = 50):
    runs = db.list_runs(limit)
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
def get_review(issue: str = "all"):
    items = db.list_review_items(issue)
    counts = db.query(
        "SELECT COALESCE(issue,'unknown') AS issue, COUNT(*) AS n FROM run_items "
        "WHERE status = 'review' GROUP BY issue"
    )
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
def analytics():
    return db.analytics_summary()


# -- integrations ------------------------------------------------------------

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
    """Keep the dashboard assets out of the browser cache.

    Without this a client keeps running an old app.js after an update and reports
    bugs that were already fixed.
    """
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html") or path.endswith((".js", ".css")):
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
