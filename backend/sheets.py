"""Reading records from a Google Sheet shared as view-by-link.

Deliberately limited to docs.google.com. This fetches a URL server-side, so
accepting arbitrary hosts would turn an authenticated page into an SSRF tool.
"""

import csv
import io
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)")
GID_RE = re.compile(r"[#&?]gid=([0-9]+)")
ALLOWED_HOSTS = {"docs.google.com", "www.docs.google.com"}
TIMEOUT = 30


class SheetError(Exception):
    """Raised with a message meant to be shown to the operator."""


def export_url(url: str) -> str:
    """Turn a Sheets link into its CSV export URL."""
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise SheetError("That is not a docs.google.com URL.")

    match = SHEET_ID_RE.search(url)
    if not match:
        raise SheetError(
            "Could not find a sheet id in that link. "
            "Paste the full /spreadsheets/d/... URL."
        )

    gid = GID_RE.search(url)
    return (
        f"https://docs.google.com/spreadsheets/d/{match.group(1)}"
        f"/export?format=csv&gid={gid.group(1) if gid else '0'}"
    )


def parse_csv(text: str) -> list[dict]:
    rows = [
        row for row in csv.DictReader(io.StringIO(text))
        if any((value or "").strip() for value in row.values())
    ]
    if not rows:
        raise SheetError("That sheet has a header but no data rows.")
    return rows


def fetch(url: str) -> list[dict]:
    target = export_url(url)
    request = urllib.request.Request(target, headers={"User-Agent": "FlowPilot"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if "text/csv" not in response.headers.get("Content-Type", ""):
                raise SheetError(
                    "Google did not return CSV. Share the sheet with "
                    "'Anyone with the link can view' and try again."
                )
            text = response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SheetError(
            f"Google refused that sheet ({exc.code}). Make sure it is shared as "
            f"'Anyone with the link can view'."
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SheetError(f"Could not reach Google Sheets: {exc}") from exc

    return parse_csv(text)
