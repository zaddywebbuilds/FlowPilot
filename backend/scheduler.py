"""Starts scheduled runs and does periodic housekeeping.

An in-process loop rather than cron: it can see whether a run is already in
flight, it keeps the schedule editable from the dashboard, and it means a
deployment is one service instead of a service plus crontab entries.
"""

import asyncio
import time
from datetime import datetime, timedelta

import db
import notify
from runner import AuthorizationError, engine

TICK_SECONDS = 30
HOUSEKEEPING_SECONDS = 3600


def compute_next_run(workflow, after=None):
    """When this workflow should run next, as a unix timestamp."""
    now = datetime.fromtimestamp(after or time.time())

    if workflow.get("schedule_kind") == "daily":
        raw = (workflow.get("schedule_at") or "09:00").strip()
        try:
            hour, minute = (int(part) for part in raw.split(":", 1))
        except (ValueError, TypeError):
            hour, minute = 9, 0
        target = now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()

    minutes = max(1, int(workflow.get("schedule_minutes") or 60))
    return (now + timedelta(minutes=minutes)).timestamp()


def due_workflows(now=None):
    now = now or time.time()
    due = []
    for wf in db.scheduled_workflows():
        if wf.get("next_run_at") is None:
            # Newly enabled: arm it rather than firing immediately.
            db.update_workflow(wf["id"], next_run_at=compute_next_run(wf))
            continue
        if wf["next_run_at"] <= now:
            due.append(wf)
    return due


class Scheduler:
    def __init__(self):
        self.task = None
        self.loop = None
        self.last_housekeeping = 0.0
        self.last_tick = None

    def start(self):
        running = asyncio.get_running_loop()
        # A task left over from a previous loop (tests, or a reload) can never be
        # awaited from this one, so drop it rather than hold a dead reference.
        if self.task and not self.task.done() and self.loop is running:
            return
        self.loop = running
        self.task = running.create_task(self._loop())

    async def stop(self):
        task, self.task = self.task, None
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except RuntimeError:
            # Belongs to another event loop; cancelling is all we can do.
            pass

    def status(self):
        return {
            "enabled": db.get_setting("scheduler_enabled", "1") == "1",
            "running": bool(self.task and not self.task.done()),
            "last_tick": self.last_tick,
            "upcoming": [
                {
                    "workflow_id": wf["id"],
                    "name": wf["name"],
                    "next_run_at": wf.get("next_run_at"),
                    "kind": wf.get("schedule_kind"),
                    "pending": wf.get("pending", 0),
                }
                for wf in db.scheduled_workflows()
            ],
        }

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                self.last_tick = time.time()

                if time.time() - self.last_housekeeping > HOUSEKEEPING_SECONDS:
                    self.last_housekeeping = time.time()
                    pruned = db.prune_old_data()
                    if pruned["runs"] or pruned["items"]:
                        db.log_event(
                            None,
                            f"Housekeeping: removed {pruned['files']} screenshot(s), "
                            f"{pruned['items']} record result(s) and {pruned['events']} "
                            f"log entries from {pruned['runs']} old run(s).",
                        )

                if db.get_setting("scheduler_enabled", "1") != "1":
                    continue
                if engine.status != "idle":
                    continue

                for wf in due_workflows():
                    await self._fire(wf)
                    break  # one run at a time; the rest catch the next tick

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a bad schedule must not kill the loop
                db.log_event(None, f"Scheduler error: {exc}", "error")

    async def _fire(self, workflow):
        # Re-arm first, so a failure cannot cause a tight retry loop.
        db.update_workflow(workflow["id"], next_run_at=compute_next_run(workflow))

        pending = db.count_records(workflow["id"]).get("pending", 0)
        if not pending:
            db.log_event(None, f"Scheduled run for '{workflow['name']}' skipped: queue empty.")
            return

        batch = max(1, int(workflow.get("schedule_batch") or 25))
        try:
            run_id = await engine.start(workflow["id"], batch, trigger="schedule")
            db.log_event(run_id, f"Started by schedule for '{workflow['name']}'.")
        except AuthorizationError as exc:
            db.log_event(None, f"Scheduled run refused: {exc}", "error")
            if notify.enabled_for("failure"):
                notify.send_async("FlowPilot: scheduled run refused", str(exc))
        except (RuntimeError, ValueError) as exc:
            db.log_event(None, f"Scheduled run could not start: {exc}", "warn")


scheduler = Scheduler()
