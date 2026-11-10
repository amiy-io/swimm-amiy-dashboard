"""
POST /api/login   {"email": "...", "password": "..."}  → sets the session cookie
GET  /api/login                                        → who am I? (cheap check)

Never says whether the e-mail exists — a wrong e-mail and a wrong password give
the exact same answer, and take the same amount of time.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _auth  # noqa: E402

MAX_BODY = 4096


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        email = _auth.session_email(self)
        _auth.send_json(self, {"auth": bool(email), "email": email or None})

    def do_POST(self):
        cfg = _auth.config_error()
        if cfg:
            # A setup problem, not a bad password — say so plainly so it can be
            # fixed, but don't let anyone in.
            _auth.send_json(
                self,
                {"error": "Login is not configured on the server. " + cfg,
                 "auth": False}, 503)
            return

        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            _auth.send_json(self, {"error": "Bad request.", "auth": False}, 400)
            return

        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            email = str(data.get("email", "")).strip()
            password = str(data.get("password", ""))
        except Exception:
            _auth.send_json(self, {"error": "Bad request.", "auth": False}, 400)
            return

        if not email or not password:
            _auth.send_json(
                self, {"error": "Enter your e-mail and password.",
                       "auth": False}, 400)
            return

        if not _auth.verify_password(email, password):
            time.sleep(0.4)          # slow down guessing
            _auth.send_json(
                self, {"error": "That e-mail and password don't match.",
                       "auth": False}, 401)
            return

        email = email.lower()
        token = _auth.make_token(email)
        _auth.send_json(
            self, {"auth": True, "email": email}, 200,
            extra=[("Set-Cookie", _auth.set_cookie_header(token))])

    def log_message(self, fmt, *args):
        pass
