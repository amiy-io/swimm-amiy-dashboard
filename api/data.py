"""
GET /api/data?k=<SWIMM_DATA_KEY> — live Swimm activity, fetched from Apollo.

NOT a browser endpoint.  Its answer is cached at Vercel's edge, and an edge
cache is shared by every visitor — so if this URL were reachable without a
key, the cached copy would be readable by anyone who guessed the path.  The
key is part of the URL on purpose: the cache key includes the query string,
so the cached copy only exists at an address nobody can guess.

Browsers go through /api/feed (session cookie required), which reads this
cache server-side.  The nightly cron warms it via /api/warm.

Env: APOLLO_SWIMM_KEY (Apollo), SWIMM_DATA_KEY (this endpoint's machine key).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _auth   # noqa: E402
import _core   # noqa: E402

# How long the CDN may serve a cached copy (seconds), and how long it may keep
# serving the stale copy while it revalidates in the background.
# One full sync per night (cron at 21:00 UTC = midnight UTC+3). The cache is
# held nearly a full day so ONLY the cron ever triggers an Apollo fetch.
CACHE_S       = int(os.environ.get("SWIMM_CACHE_SECONDS", "82800"))  # 23 h
STALE_S       = 172800                                              # stale up to 48 h
WINDOW_DAYS   = int(os.environ.get("SWIMM_WINDOW_DAYS", "30"))
# Swimm is ~8.8k calls (≈88 pages) and Apollo answers in ~2s/page, so a full
# sync needs ≈4 min. Budget sits under maxDuration (800s) so we always respond.
TIME_BUDGET_S = float(os.environ.get("SWIMM_TIME_BUDGET", "700"))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _auth.data_key_ok(self, self.path):
            # no-store matters: a cached 401 would poison the warm copy.
            _auth.send_json(self, {"error": "Not allowed."}, 401)
            return
        try:
            payload = _core.build_payload(days=WINDOW_DAYS,
                                          time_budget_s=TIME_BUDGET_S)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            # Only cache a sync that actually came back with activity.
            # A rate-limited sync is NOT an exception: _core catches the 429s,
            # keeps the previous figures and returns a perfectly valid 200 with
            # an empty calls_daily. Caching that for 23 hours would serve the
            # emptiness long past the point where a retry would have worked —
            # which is exactly what happened on 2026-09-06. An empty result is
            # therefore held fresh, so the next call re-tries Apollo.
            healthy = bool(payload.get("calls_daily"))
            cache = (f"public, s-maxage={CACHE_S}, "
                     f"stale-while-revalidate={STALE_S}"
                     if healthy else "no-store")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
