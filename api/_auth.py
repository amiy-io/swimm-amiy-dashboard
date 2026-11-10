"""
_auth.py — session auth for the Swimm dashboard.  Not a route (leading
underscore), just a helper module the real endpoints import.

WHO CAN LOG IN
    The SWIMM_USERS env var, one record per user, ";" between them:
        email|iterations|salt_hex|hash_hex
    Passwords are never stored — only a PBKDF2-HMAC-SHA256 hash.
    Build the value locally with:  python3 make_users.py
    Deleting someone from SWIMM_USERS revokes them on the next request, even
    if their cookie hasn't expired yet (read_token re-checks the user list).

HOW A SESSION WORKS
    Stateless signed cookie:  base64(json).hmac_sha256, signed with
    SWIMM_SECRET. No database, nothing to keep in sync, nothing to clean up.

FAIL-CLOSED
    If SWIMM_SECRET or SWIMM_USERS is missing, nobody gets in. A
    misconfiguration locks the dashboard rather than opening it.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from http import cookies as _cookies

COOKIE = "swimm_session"
TTL_S = int(os.environ.get("SWIMM_SESSION_DAYS", "14")) * 86400
MIN_ITERATIONS = 100_000


# ── secret ────────────────────────────────────────────────────────────────────
def _secret() -> bytes:
    s = os.environ.get("SWIMM_SECRET", "").strip()
    return s.encode("utf-8") if len(s) >= 32 else b""


def config_error() -> str:
    """Human-readable reason the login system can't work, or ''."""
    if not _secret():
        return "SWIMM_SECRET is missing (or shorter than 32 characters)."
    if not load_users():
        return "SWIMM_USERS is missing or empty."
    return ""


# ── users ─────────────────────────────────────────────────────────────────────
def load_users() -> dict:
    """{email_lower: (iterations, salt_hex, hash_hex)}"""
    raw = os.environ.get("SWIMM_USERS", "")
    out = {}
    for rec in raw.replace("\n", ";").split(";"):
        parts = [p.strip() for p in rec.strip().split("|")]
        if len(parts) != 4 or not parts[0]:
            continue
        email, iters, salt, digest = parts
        try:
            iters = int(iters)
        except ValueError:
            continue
        if iters < MIN_ITERATIONS:
            continue
        out[email.lower()] = (iters, salt, digest)
    return out


def verify_password(email: str, password: str) -> bool:
    rec = load_users().get((email or "").strip().lower())
    if not rec:
        # Spend the same time on an unknown e-mail as on a wrong password, so
        # response timing can't be used to enumerate who has an account.
        hashlib.pbkdf2_hmac("sha256", b"-", b"-", MIN_ITERATIONS)
        return False
    iters, salt, digest = rec
    try:
        salt_b = bytes.fromhex(salt)
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), salt_b, iters
    ).hex()
    return hmac.compare_digest(calc, digest)


# ── tokens ────────────────────────────────────────────────────────────────────
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(msg: str) -> str:
    return _b64e(hmac.new(_secret(), msg.encode("ascii"), hashlib.sha256).digest())


def make_token(email: str) -> str:
    body = json.dumps(
        {"e": email, "x": int(time.time()) + TTL_S}, separators=(",", ":")
    ).encode("utf-8")
    b = _b64e(body)
    return b + "." + _sign(b)


def read_token(tok: str):
    """Return the e-mail if the token is valid, signed, unexpired and the user
    still exists.  Otherwise None."""
    if not tok or not _secret():
        return None
    b, _, sig = tok.partition(".")
    if not sig or not hmac.compare_digest(sig, _sign(b)):
        return None
    try:
        data = json.loads(_b64d(b))
        if int(data.get("x", 0)) < time.time():
            return None
        email = str(data.get("e", ""))
    except Exception:
        return None
    if email.lower() not in load_users():
        return None
    return email


# ── request helpers ───────────────────────────────────────────────────────────
def session_email(handler):
    jar = _cookies.SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", "") or "")
    except Exception:
        return None
    m = jar.get(COOKIE)
    return read_token(m.value) if m else None


def set_cookie_header(token: str) -> str:
    return (f"{COOKIE}={token}; Path=/; Max-Age={TTL_S}; "
            "HttpOnly; Secure; SameSite=Lax")


def clear_cookie_header() -> str:
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"


def send_json(handler, obj, status=200, extra=None, cache="private, no-store"):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", cache)
    handler.send_header("X-Content-Type-Options", "nosniff")
    for k, v in (extra or []):
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def deny(handler, msg="Not signed in."):
    send_json(handler, {"error": msg, "auth": False}, 401)


def require_session(handler):
    """Return the e-mail, or write a 401 and return None."""
    email = session_email(handler)
    if not email:
        deny(handler)
        return None
    return email


# ── machine-to-machine key (cron / cache warming) ─────────────────────────────
def data_key_ok(handler, path: str = "") -> bool:
    """True when the request carries the SWIMM_DATA_KEY, either as ?k= or as
    an Authorization: Bearer header."""
    want = os.environ.get("SWIMM_DATA_KEY", "").strip()
    if len(want) < 24:
        return False
    got = ""
    if "?" in path:
        from urllib.parse import parse_qs
        got = (parse_qs(path.split("?", 1)[1]).get("k") or [""])[0]
    if not got:
        auth = handler.headers.get("Authorization", "") or ""
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
    return bool(got) and hmac.compare_digest(got, want)
