import asyncio
import base64
import random
import re
import time
import urllib.robotparser
from urllib.parse import urlparse

from playwright.async_api import async_playwright

import db
import notify

TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

STEP_TYPES = {"goto", "fill", "click", "wait_for", "assert_text", "extract", "screenshot"}


def render(template, data):
    if not isinstance(template, str):
        return template
    return TEMPLATE_RE.sub(lambda m: str(data.get(m.group(1), "")), template)


class AuthorizationError(Exception):
    """Raised when a workflow targets a host the operator has not allowlisted."""


class StepError(Exception):
    def __init__(self, message, issue):
        super().__init__(message)
        self.issue = issue


_robots_cache = {}


def robots_allows(url, user_agent="FlowPilot"):
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            # No reachable robots.txt is treated as no restrictions.
            rp = None
        _robots_cache[base] = rp
    if rp is None:
        return True
    return rp.can_fetch(user_agent, url)


def check_host_allowed(url):
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise AuthorizationError(f"Could not parse a hostname from {url!r}")
    allowed = db.allowed_domains()
    for entry in allowed:
        if host == entry or host.endswith("." + entry):
            return host
    raise AuthorizationError(
        f"{host} is not in the allowed domains list. Add it in Settings only if you "
        f"are authorized to automate that site."
    )


async def launch_browser(pw, preferred, headless):
    """Drive the browser already installed on this machine.

    Playwright's bundled Chromium needs the MSVC runtime, which is often missing on
    a clean Windows box, so an installed Chrome or Edge is both more reliable and a
    better match for a tool the operator runs on their own desktop.
    """
    order = [preferred] + [c for c in ("chrome", "msedge", None) if c != preferred]
    errors = []
    for channel in order:
        try:
            kwargs = {"headless": headless}
            if channel:
                kwargs["channel"] = channel
            browser = await pw.chromium.launch(**kwargs)
            return browser, channel or "bundled chromium"
        except Exception as exc:
            errors.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
    raise RuntimeError(
        "No usable browser found. Install Google Chrome or Microsoft Edge, or run "
        "'playwright install chromium' to fetch the bundled one. Tried "
        + "; ".join(errors)
    )


class Engine:
    """Executes a workflow over a batch of records in a real browser."""

    def __init__(self):
        self.status = "idle"          # idle | running | paused | stopping
        self.run_id = None
        self.workflow_id = None
        self.current = {}             # live snapshot for the Live Browser screen
        self.subscribers = set()
        self._task = None
        self._consecutive_failures = 0

    # -- event plumbing ------------------------------------------------------

    def subscribe(self, queue):
        self.subscribers.add(queue)

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def emit(self, kind, payload=None, persist=True, level="info"):
        message = {"kind": kind, "ts": time.time(), **(payload or {})}
        if persist and kind == "log":
            db.log_event(self.run_id, message.get("message", ""), level, payload)
        for q in list(self.subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def log(self, message, level="info", **data):
        self.emit("log", {"message": message, "level": level, **data}, level=level)

    def snapshot(self):
        return {
            "status": self.status,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "current": self.current,
        }

    # -- control -------------------------------------------------------------

    async def start(self, workflow_id, batch_size, trigger="manual"):
        if self.status in ("running", "paused"):
            raise RuntimeError("A run is already in progress.")

        workflow = db.get_workflow(workflow_id)
        if not workflow:
            raise ValueError("Workflow not found.")
        if not workflow["authorized_by"]:
            raise AuthorizationError(
                "This workflow has no authorization attestation. Record who authorized "
                "automating this target before running it."
            )

        # Fail before any browser opens rather than per record.
        target = (workflow["target_domain"] or "").lower()
        if not any(target == d or target.endswith("." + d) for d in db.allowed_domains()):
            raise AuthorizationError(
                f"{target} is not in the allowed domains list. Add it in Settings only "
                f"if you are authorized to automate that site."
            )

        records = db.claim_pending_records(workflow_id, batch_size)
        if not records:
            raise ValueError("No pending records for this workflow.")

        self.run_id = db.create_run(workflow_id, batch_size, len(records), trigger)
        self.workflow_id = workflow_id
        self.status = "running"
        self._consecutive_failures = 0
        self._task = asyncio.create_task(self._run(workflow, records))
        return self.run_id

    def pause(self):
        if self.status == "running":
            self.status = "paused"
            db.update_run(self.run_id, status="paused")
            self.log("Run paused by operator.", "warn")

    def resume(self):
        if self.status == "paused":
            self.status = "running"
            db.update_run(self.run_id, status="running")
            self.log("Run resumed by operator.")

    def stop(self):
        if self.status in ("running", "paused"):
            self.status = "stopping"
            self.log("Stop requested. Finishing current record.", "warn")

    # -- execution -----------------------------------------------------------

    async def _run(self, workflow, records):
        settings = db.get_settings()
        min_delay = float(settings.get("min_delay_seconds", 2))
        max_delay = float(settings.get("max_delay_seconds", 5))
        timeout = float(settings.get("step_timeout_seconds", 20)) * 1000
        shoot = settings.get("capture_screenshots", "1") == "1"
        respect_robots = settings.get("respect_robots_txt", "1") == "1"
        max_failures = int(settings.get("stop_on_consecutive_failures", 5))
        preferred = settings.get("browser_channel", "chrome") or None
        headless = settings.get("headless", "0") == "1"

        run_id = self.run_id
        evidence_root = db.EVIDENCE_DIR / f"run_{run_id}"
        evidence_root.mkdir(parents=True, exist_ok=True)

        self.log(f"Run #{run_id} started: {len(records)} record(s) for '{workflow['name']}'.")

        browser = None
        try:
            async with async_playwright() as pw:
                browser, channel = await launch_browser(pw, preferred, headless)
                saved = db.get_session(workflow["id"])
                if saved:
                    self.log(f"Browser ready ({channel}), using the saved sign-in.")
                else:
                    self.log(f"Browser ready ({channel}).")
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    storage_state=saved,
                )
                page = await context.new_page()

                for index, record in enumerate(records, start=1):
                    if self.status == "stopping":
                        break
                    while self.status == "paused":
                        await asyncio.sleep(0.4)
                    if self.status == "stopping":
                        break

                    await self._process_record(
                        page, workflow, record, index, len(records),
                        evidence_root, timeout, shoot, respect_robots,
                    )

                    if self._consecutive_failures >= max_failures:
                        self.log(
                            f"Stopping: {self._consecutive_failures} consecutive failures. "
                            f"Check the Review Center before resuming.",
                            "error",
                        )
                        break

                    if index < len(records) and self.status == "running":
                        delay = random.uniform(min_delay, max_delay)
                        self.current = {**self.current, "step": f"Waiting {delay:.1f}s"}
                        self.emit("state", self.snapshot(), persist=False)
                        await asyncio.sleep(delay)

                await context.close()
        except Exception as exc:  # engine-level failure
            self.log(f"Engine error: {exc}", "error")
            db.update_run(run_id, status="failed")
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

        run = db.get_run(run_id) or {}
        final = "stopped" if self.status == "stopping" else run.get("status", "completed")
        if final not in ("failed", "stopped"):
            final = "completed"
        db.update_run(run_id, status=final, finished_at=time.time())

        self.status = "idle"
        self.current = {}
        self.log(f"Run #{run_id} finished with status '{final}'.")
        self.emit("state", self.snapshot(), persist=False)
        self.emit("run_finished", {"run_id": run_id, "status": final}, persist=False)
        self._notify_finished(run_id, final, workflow["name"])

    def _notify_finished(self, run_id, status, workflow_name):
        totals = db.get_run(run_id) or {}
        completed = totals.get("completed", 0)
        failed = totals.get("failed", 0)
        review = totals.get("review", 0)

        summary = (
            f"{workflow_name}\n"
            f"{completed} completed, {review} need review, {failed} failed."
        )

        if status == "failed" and notify.enabled_for("failure"):
            notify.send_async(f"FlowPilot: run #{run_id} failed", summary)
        elif review and notify.enabled_for("review"):
            notify.send_async(
                f"FlowPilot: {review} record(s) need review", summary
            )
        elif notify.enabled_for("finish"):
            notify.send_async(f"FlowPilot: run #{run_id} {status}", summary)

    async def _process_record(
        self, page, workflow, record, index, total,
        evidence_root, timeout, shoot, respect_robots,
    ):
        data = record["data"]
        item_id = db.create_run_item(self.run_id, record["id"])
        started = time.time()
        db.set_record_status(record["id"], "processing")

        label = data.get("email") or data.get("name") or f"record {record['id']}"
        self.current = {
            "index": index,
            "total": total,
            "label": label,
            "step": "Starting",
            "step_number": 0,
            "step_count": len(workflow["steps"]),
            "screenshot": None,
        }
        self.emit("state", self.snapshot(), persist=False)

        evidence = {"steps": [], "extracted": {}}
        try:
            for n, step in enumerate(workflow["steps"], start=1):
                self.current.update(
                    {"step": step.get("label") or step["type"], "step_number": n}
                )
                self.emit("state", self.snapshot(), persist=False)

                await self._execute_step(
                    page, step, data, timeout, respect_robots, evidence
                )

                if shoot:
                    shot = evidence_root / f"item_{item_id}_step_{n}.jpg"
                    try:
                        await page.screenshot(path=str(shot), quality=60, type="jpeg")
                        rel = f"/evidence/run_{self.run_id}/{shot.name}"
                        self.current["screenshot"] = rel
                        evidence["steps"].append({"step": n, "screenshot": rel})
                        self.emit("state", self.snapshot(), persist=False)
                    except Exception:
                        pass

            db.finish_run_item(item_id, "completed", evidence=evidence, started_at=started)
            db.set_record_status(record["id"], "completed")
            db.bump_run(self.run_id, "completed")
            self._consecutive_failures = 0
            self.log(f"[{index}/{total}] {label}: completed.", "success")

        except AuthorizationError as exc:
            db.finish_run_item(
                item_id, "review", error=str(exc), issue="not_authorized",
                evidence=evidence, started_at=started,
            )
            db.set_record_status(record["id"], "review")
            db.bump_run(self.run_id, "review")
            self._consecutive_failures += 1
            self.log(f"[{index}/{total}] {label}: {exc}", "error", issue="not_authorized")

        except StepError as exc:
            needs_review = exc.issue in ("assertion_failed", "blocked", "unclear")
            status = "review" if needs_review else "failed"
            db.finish_run_item(
                item_id, status, error=str(exc), issue=exc.issue,
                evidence=evidence, started_at=started,
            )
            db.set_record_status(record["id"], status)
            db.bump_run(self.run_id, "review" if needs_review else "failed")
            self._consecutive_failures += 1
            self.log(f"[{index}/{total}] {label}: {exc}", "error", issue=exc.issue)

        except Exception as exc:
            db.finish_run_item(
                item_id, "failed", error=str(exc), issue="engine_error",
                evidence=evidence, started_at=started,
            )
            db.set_record_status(record["id"], "failed")
            db.bump_run(self.run_id, "failed")
            self._consecutive_failures += 1
            self.log(f"[{index}/{total}] {label}: {exc}", "error")

        self.emit("item_finished", {"run_id": self.run_id}, persist=False)

    async def _execute_step(self, page, step, data, timeout, respect_robots, evidence):
        return await execute_step(page, step, data, timeout, respect_robots, evidence)


async def read_text(locator):
    """What a human would call the element's content.

    inner_text is always empty for form fields, so reading one would silently
    yield nothing; their content is the value.
    """
    tag = (await locator.evaluate("el => el.tagName.toLowerCase()")) or ""
    if tag in ("input", "textarea", "select"):
        return (await locator.input_value()).strip()
    return (await locator.inner_text()).strip()


async def execute_step(page, step, data, timeout, respect_robots, evidence):
    kind = step.get("type")
    if kind not in STEP_TYPES:
        raise StepError(f"Unsupported step type '{kind}'.", "config_error")

    selector = render(step.get("selector", ""), data)
    value = render(step.get("value", ""), data)

    try:
        if kind == "goto":
            url = render(step["url"], data)
            check_host_allowed(url)
            if respect_robots and not robots_allows(url):
                raise StepError(
                    f"robots.txt disallows automated access to {url}", "blocked"
                )
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

        elif kind == "wait_for":
            await page.wait_for_selector(selector, timeout=timeout)

        elif kind == "fill":
            await page.fill(selector, value, timeout=timeout)

        elif kind == "click":
            await page.click(selector, timeout=timeout)

        elif kind == "assert_text":
            locator = page.locator(selector).first
            await locator.wait_for(timeout=timeout)
            text = await read_text(locator)
            expected = value.strip()
            if expected and expected.lower() not in text.lower():
                raise StepError(
                    f"Expected {expected!r} in {selector!r} but found {text[:120]!r}",
                    "assertion_failed",
                )
            evidence.setdefault("assertions", []).append(
                {"selector": selector, "text": text[:300]}
            )

        elif kind == "extract":
            locator = page.locator(selector).first
            await locator.wait_for(timeout=timeout)
            evidence["extracted"][step.get("field", selector)] = (
                await read_text(locator)
            )[:500]

        elif kind == "screenshot":
            pass  # handled by the per-step capture in _process_record

    except AuthorizationError:
        raise
    except StepError:
        raise
    except Exception as exc:
        name = type(exc).__name__
        if "Timeout" in name:
            raise StepError(
                f"Timed out on {kind} ({selector or step.get('url', '')})", "timeout"
            ) from exc
        raise StepError(f"{kind} failed: {exc}", "step_error") from exc


async def test_workflow(workflow, data):
    """Run a workflow's steps once against one record, reporting each step.

    Nothing is written to the database and no record changes status. The point is
    to find out whether the selectors work before committing a whole queue to
    them, which previously meant starting a real batch and hoping.
    """
    target = (workflow.get("target_domain") or "").lower()
    if not any(target == d or target.endswith("." + d) for d in db.allowed_domains()):
        raise AuthorizationError(
            f"{target} is not in the allowed domains list. Add it in Settings only "
            f"if you are authorized to automate that site."
        )

    settings = db.get_settings()
    timeout = float(settings.get("step_timeout_seconds", 20)) * 1000
    respect_robots = settings.get("respect_robots_txt", "1") == "1"
    preferred = settings.get("browser_channel", "chrome") or None

    evidence = {"steps": [], "extracted": {}}
    results = []
    browser = None

    async with async_playwright() as pw:
        try:
            browser, channel = await launch_browser(pw, preferred, True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                storage_state=db.get_session(workflow.get("id")) if workflow.get("id") else None,
            )
            page = await context.new_page()

            for n, step in enumerate(workflow["steps"], start=1):
                entry = {
                    "n": n,
                    "type": step.get("type"),
                    "label": step.get("label") or step.get("type"),
                    "target": step.get("url") or step.get("selector") or "",
                }
                try:
                    await execute_step(page, step, data, timeout, respect_robots, evidence)
                    entry["status"] = "ok"
                except StepError as exc:
                    entry["status"] = "failed"
                    entry["issue"] = exc.issue
                    entry["error"] = str(exc)
                except Exception as exc:
                    entry["status"] = "failed"
                    entry["issue"] = "engine_error"
                    entry["error"] = str(exc)

                try:
                    shot = await page.screenshot(type="jpeg", quality=55)
                    entry["screenshot"] = (
                        "data:image/jpeg;base64," + base64.b64encode(shot).decode()
                    )
                except Exception:
                    pass

                results.append(entry)
                if entry["status"] == "failed":
                    break  # later steps depend on this one having worked

            await context.close()
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    return {
        "ok": all(r["status"] == "ok" for r in results) and len(results) == len(workflow["steps"]),
        "steps": results,
        "extracted": evidence.get("extracted", {}),
        "browser": channel,
    }


engine = Engine()
