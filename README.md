# FlowPilot

A local-first browser automation control center. Define a workflow, queue the records
it should run against, then watch each step execute in a real browser with full
evidence capture and a human review queue for anything the engine could not confirm.

Everything runs on your machine. No hosting, no accounts, no data leaves the computer.

## What it does

- **Workflows** are declarative step lists (`goto`, `fill`, `click`, `wait_for`,
  `assert_text`, `extract`) with `{{field}}` templating from each record.
- **The element picker** builds them without anyone writing a selector: it opens the
  target page, you click the field or button you mean, and the step is written for
  you. Steps can still be edited as JSON.
- **Test on one record** walks a workflow through a single record and reports each
  step with a screenshot, so a broken selector is found before a queue runs on it.
- **Records** come from CSV, a Google Sheet shared as view-by-link, or the API.
  Duplicates are rejected on a unique key, so re-importing is safe.
- **Runs** process a configurable batch, pacing themselves between records, with a
  circuit breaker that halts the run after N consecutive failures.
- **Schedules** start a run on their own, every N minutes or once a day, but only
  when the queue has pending records and nothing else is running.
- **Notifications** post to a webhook when a run finishes, when records need review,
  or when a run fails. Works with ntfy.sh, Slack, Discord or any JSON endpoint.
- **Evidence** is a screenshot after every step plus any extracted values, kept per
  run and pruned automatically once past the retention window.
- **Review Center** holds anything uncertain. Nothing uncertain is ever retried
  automatically, so a record cannot be submitted twice by accident.
- **Analytics** covers outcomes, error categories and processing time.

A run interrupted by a crash or reboot is closed out on the next start and its
records returned to pending, rather than being left stuck mid-flight forever.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

58 tests covering the engine's logic, the API surface, authentication, scheduling,
migrations and retention. No browser or network needed, so they run in CI on every
push (see `.github/workflows/tests.yml`).

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

## Running it 24/7 on a server

On a fresh Debian or Ubuntu box, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/zaddywebbuilds/FlowPilot/main/deploy/install.sh -o install.sh
DOMAIN=flowpilot.example.com bash install.sh
```

That installs Python and Chromium with its system libraries, creates a `flowpilot`
service account, runs the app under systemd with headless mode on, and puts Caddy in
front so HTTPS works with a real certificate. Point the domain's DNS at the server
first, otherwise the certificate request fails.

### No domain? Get one free

You cannot get an HTTPS certificate for a bare IP address, so `https://1.2.3.4` is not
an option no matter what. A free subdomain fixes that in about two minutes:

1. Sign in at [duckdns.org](https://www.duckdns.org) with GitHub
2. Create a subdomain, for example `flowpilot-acme`
3. Put your server's IP in the box and press update

Then deploy with `DOMAIN=flowpilot-acme.duckdns.org bash install.sh` and share the
https link. [sslip.io](https://sslip.io) needs no signup at all, since
`1.2.3.4.sslip.io` already resolves to `1.2.3.4`, though it is shared with many people
and occasionally hits certificate rate limits.

### Or keep it private

Drop `DOMAIN=` and run `bash install.sh`. FlowPilot then listens only on 127.0.0.1, so
it is not reachable from the internet at all, and you reach it through an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 root@your-server-ip
```

Then open http://127.0.0.1:8000 on your own machine. This is the right choice when the
dashboard is only for you, and the wrong one when you need to send someone a link.

Either way port 8000 is never exposed directly. The dashboard requires a password
before anything is reachable, and the session cookie sets its Secure flag automatically
once requests arrive over HTTPS. Avoid serving it over plain HTTP on a public address,
since the password would cross the network in the clear.

To update later: `bash /opt/flowpilot/deploy/update.sh`. Your database, password and run
history are kept.

| Task | Command |
|---|---|
| Logs | `journalctl -u flowpilot -f` |
| Restart | `systemctl restart flowpilot` |
| Status | `systemctl status flowpilot` |

## Screens

| Screen | Purpose |
|---|---|
| Overview | Stats, workflow picker, run controls, live progress |
| Automation Studio | Create, edit, delete and schedule workflows |
| Record Queue | Import from CSV or Google Sheets, filter, reset, delete |
| Live Browser | Per-step screenshots, step timeline, event log |
| Run History | Every batch, drill-down detail, CSV export |
| Review Center | Approve or requeue uncertain outcomes |
| Analytics | Daily outcomes, success rate, error categories |
| Integrations | Allowlist, scheduler state, evidence disk usage |
| Settings | Pacing, retention, notifications, allowed domains, password |

## Layout

```
backend/
  app.py        FastAPI routes, auth middleware, WebSocket
  auth.py       Password hashing and sessions
  db.py         SQLite schema, migrations, queries
  runner.py     Playwright engine, allowlist and robots.txt enforcement
  scheduler.py  Scheduled runs and housekeeping
  notify.py     Webhook notifications
  sheets.py     Google Sheets import
frontend/
  index.html    Dashboard shell
  login.html    Sign-in and first-run setup
  styles.css    Design system (light and dark)
  app.js        Router and all nine screens
  login.js      Auth page
deploy/
  install.sh    One-command server install
  update.sh     Pull and restart
tests/          Unit and API tests
flowpilot.db    Created on first run
evidence/       Screenshots, one folder per run
```
