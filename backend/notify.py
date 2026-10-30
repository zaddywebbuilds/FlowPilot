"""Outbound notifications.

A single webhook URL rather than SMTP: it covers Slack, Discord, ntfy.sh and
anything else that accepts a POST, needs no credentials stored on the server,
and cannot silently land in a spam folder.
"""

import json
import threading
import urllib.error
import urllib.request

import db

TIMEOUT = 10


def _payload_for(url: str, title: str, message: str) -> tuple[bytes, dict]:
    """Shape the body to whatever the target expects, guessed from the URL."""
    headers = {"Content-Type": "application/json", "User-Agent": "FlowPilot"}

    if "hooks.slack.com" in url:
        body = {"text": f"*{title}*\n{message}"}
    elif "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        body = {"content": f"**{title}**\n{message}"}
    elif "ntfy.sh" in url:
        # ntfy takes a plain body with metadata in headers.
        return message.encode("utf-8"), {
            "Title": title, "User-Agent": "FlowPilot", "Content-Type": "text/plain",
        }
    else:
        body = {"title": title, "message": message, "source": "FlowPilot"}

    return json.dumps(body).encode("utf-8"), headers


def send(title: str, message: str) -> bool:
    url = (db.get_setting("notify_webhook_url", "") or "").strip()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        return False

    data, headers = _payload_for(url, title, message)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def send_async(title: str, message: str) -> None:
    """Never let a slow or dead webhook hold up a run."""
    if not (db.get_setting("notify_webhook_url", "") or "").strip():
        return
    threading.Thread(target=send, args=(title, message), daemon=True).start()


def enabled_for(event: str) -> bool:
    key = {
        "finish": "notify_on_finish",
        "review": "notify_on_review",
        "failure": "notify_on_failure",
    }.get(event)
    return bool(key) and db.get_setting(key, "0") == "1"
