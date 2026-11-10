"""GET or POST /api/logout — clears the session cookie."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _auth  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _out(self):
        _auth.send_json(self, {"auth": False}, 200,
                        extra=[("Set-Cookie", _auth.clear_cookie_header())])

    do_GET = _out
    do_POST = _out

    def log_message(self, fmt, *args):
        pass
