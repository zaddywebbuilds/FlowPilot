# FlowPilot

A local-first browser automation control center. Define a workflow, queue the records
it should run against, then watch each step execute in a real browser with full
evidence capture and a human review queue for anything the engine could not confirm.

Everything runs on your machine. No hosting, no accounts, no data leaves the computer.

## What it does

- **Workflows** are declarative step lists (`goto`, `fill`, `click`, `wait_for`,
  `assert_text`, `extract`) with `{{field}}` templating from each record.
- **Records** come from CSV or the API. Duplicates are rejected on a unique key.
- **Runs** process a configurable batch, pacing themselves between records, with a
  circuit breaker that halts the run after N consecutive failures.
- **Evidence** is a screenshot after every step plus any extracted values, kept per run.
- **Review Center** holds anything uncertain. Nothing uncertain is ever retried
  automatically, so a record cannot be submitted twice by accident.
- **Analytics** covers outcomes, error categories and processing time.

## What it will not do

Runs are refused before the browser opens unless the target host is on the allowlist
in Settings, and every workflow must record who authorized automating that target.
`robots.txt` is honoured by default. These are deliberate, and they are the reason
this is safe to hand to a client.

Use it for systems you own, a client's systems with written approval, or sites whose
terms permit automated access.

## Run it

Double-click `run.bat`. It checks for Python, sets up a virtual environment, installs
dependencies and opens http://127.0.0.1:8000. First launch takes about a minute.

It drives the Chrome or Edge already installed on the machine, so there is no large
browser download. If you want Playwright's bundled Chromium as well, run
`.venv\Scripts\python -m playwright install chromium` (note it needs the Microsoft
Visual C++ runtime, which is missing on some clean Windows installs).

Manual equivalent:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app:app --app-dir backend --port 8000
```

## First run

You are asked to create a password. That password protects the dashboard, and
nothing else is reachable until it is set, so an instance left running is never open.
Change it later under Settings, which signs out every other device.

A demo workflow against `quotes.toscrape.com` (a site published for automation
practice) is seeded with five records. Open Overview and press **Start new run** to
watch the whole pipeline work end to end.

## Running it 24/7 on a VPS

The app is plain Python, so it runs anywhere. On a Linux box:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
.venv/bin/uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

On a server you need the bundled Chromium, since there is no installed Chrome and no
desktop: run `.venv/bin/playwright install --with-deps chromium`, then set **Browser**
to "Playwright bundled Chromium" and turn **Run headless** on in Settings. Put it under
systemd so it survives a reboot.

**Put TLS in front of it before exposing the port.** The dashboard has a password, and
nothing is reachable until that password is set, but plain HTTP sends it in cleartext
and anyone on the network path can read it. Two options:

*Private, no certificate needed.* Keep it bound to 127.0.0.1 and tunnel in:

```bash
ssh -L 8000:127.0.0.1:8000 you@your-vps
```

Then browse to http://127.0.0.1:8000 on your own machine.

*Shared with someone else.* Put Caddy or nginx in front with a real certificate, proxy
to 127.0.0.1:8000, and leave port 8000 itself closed in the firewall. The session cookie
sets its Secure flag automatically once requests arrive over HTTPS.

## Screens

| Screen | Purpose |
|---|---|
| Overview | Stats, run controls, live progress, recent activity |
| Automation Studio | Create workflows and record authorization |
| Record Queue | Import CSV, filter, reset or delete records |
| Live Browser | Per-step screenshots, step timeline, event log |
| Run History | Every batch, drill-down detail, CSV export |
| Review Center | Approve or requeue uncertain outcomes |
| Analytics | Daily outcomes, success rate, error categories |
| Integrations | Allowlist status and local storage paths |
| Settings | Pacing, timeouts, safety toggles, allowed domains |

## Layout

```
backend/
  app.py       FastAPI routes and WebSocket
  db.py        SQLite schema and queries
  runner.py    Playwright engine, allowlist and robots.txt enforcement
frontend/
  index.html   Shell
  styles.css   Design system (light and dark)
  app.js       Router and all nine screens
flowpilot.db   Created on first run
evidence/      Screenshots, one folder per run
```
