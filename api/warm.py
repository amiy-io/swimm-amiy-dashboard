"""
GET /api/warm — the nightly cron target.

Vercel's cron hits this; it then calls /api/data with the machine key, which
does the real Apollo sync and leaves the answer in the edge cache under a
secret URL.  /api/feed reads that cache for the rest of the day.

The cron secret never appears in vercel.json (which is in git) — it lives only
in the SWIMM_DATA_KEY env var, and only the server ever puts it in a URL.
"""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _auth  # noqa: E402
import feed as _feed  # noqa: E402


def _is_cron(h) -> bool:
    """Vercel signs cron requests with Authorization: Bearer $CRON_SECRET."""
    want = os.environ.get("CRON_SECRET", "").strip()
    auth = (h.headers.get("Authorization", "") or "").strip()
    if want and auth == f"Bearer {want}":
        return True
    # No CRON_SECRET configured: fall back to Vercel's cron marker header.
    return not want and bool(h.headers.get("x-vercel-cron"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not (_is_cron(self) or _auth.data_key_ok(self, self.path)
                or _auth.session_email(self)):
            _auth.deny(self, "Not allowed.")
            return

        t0 = time.time()
        try:
            j = _feed.fetch_live()
            _auth.send_json(self, {
                "ok": True,
                "calls_days": len(j.get("calls_daily") or []),
                "connected": len(j.get("connected_calls") or []),
                "seconds": round(time.time() - t0, 1),
            })
        except Exception as exc:
            _auth.send_json(self, {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - t0, 1),
            }, 500)

    def log_message(self, fmt, *args):
        pass
