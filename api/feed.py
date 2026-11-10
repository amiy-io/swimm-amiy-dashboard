"""
GET /api/feed          → the dashboard's data.  Requires a valid session cookie.
GET /api/feed?live=1   → same, but tries the freshly-synced copy first.

WHY THIS EXISTS
    /api/data does the real Apollo fetch and takes ~4 minutes cold, so its
    answer is cached at Vercel's edge.  An edge cache is shared by everyone,
    which means a cached copy could be handed to someone who never logged in —
    so /api/data is locked behind a machine key that only the server knows, and
    this endpoint is the only door a browser uses.

    Default responses come from the payload baked into the deployment
    (api/_payload.py), so a signed-in user sees the dashboard instantly.  The
    ?live=1 call then pulls the nightly-refreshed copy in the background.

    Everything here is sent as `private, no-store` — never cached anywhere.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _auth  # noqa: E402

LIVE_TIMEOUT_S = float(os.environ.get("SWIMM_LIVE_TIMEOUT", "760"))


def baked():
    """The payload committed with this deployment. Instant, no network."""
    try:
        import _payload
        return _payload.load()
    except Exception as exc:
        return {"error": f"no baked payload ({exc})"}


def origin() -> str:
    host = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
            or os.environ.get("VERCEL_URL") or "").strip()
    return f"https://{host}" if host else ""


def fetch_live(timeout=LIVE_TIMEOUT_S):
    """Server-to-server GET of /api/data using the machine key. Normally this
    is answered straight from the edge cache the nightly cron warmed."""
    key = os.environ.get("SWIMM_DATA_KEY", "").strip()
    base = origin()
    if not key or not base:
        raise RuntimeError("SWIMM_DATA_KEY or deployment URL not set")
    req = urllib.request.Request(f"{base}/api/data?k={key}",
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _auth.require_session(self):
            return

        want_live = "live=1" in (self.path or "")
        data, note = None, None

        if want_live:
            try:
                j = fetch_live()
                if j and j.get("calls_daily"):
                    data = j
                else:
                    note = "live copy was empty"
            except Exception as exc:
                note = f"live refresh unavailable ({type(exc).__name__})"

        if data is None:
            data = baked()
            data["_source"] = "baked"
            if note:
                data.setdefault("errors", []).append(note)
        else:
            data["_source"] = "live"

        _auth.send_json(self, data)

    def log_message(self, fmt, *args):
        pass
