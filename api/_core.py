"""
_core.py — shared Apollo fetch + aggregation logic for Swimm.

Used by BOTH:
  • api/data.py    (Vercel serverless function — live data on request)
  • build_swimm.py (local CLI — writes a static swimm-payload.json)

Leading underscore means Vercel does NOT expose this file as a route.
"""
from __future__ import annotations

import json
import os
import re
import time
import datetime
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlencode

BASE_URL = "https://api.apollo.io"
PER_PAGE = 100

# How stubborn to be when Apollo answers 429. Defaults are sized for a web
# request; the nightly workflow overrides them to roughly twenty minutes of
# patience, which is what an hourly quota actually needs.
RETRY_429_ATTEMPTS = int(os.environ.get("SWIMM_429_ATTEMPTS", "5"))
RETRY_429_MAX_SLEEP = float(os.environ.get("SWIMM_429_MAX_SLEEP", "60"))
ACCOUNT  = {"id": "client-swimm", "name": "Swimm"}
_HERE    = Path(__file__).resolve().parent
_ROOT    = _HERE.parent


# ── key loading ──────────────────────────────────────────────────────────────
def load_key() -> str:
    key = (os.environ.get("APOLLO_SWIMM_KEY") or "").strip()
    if key:
        return key
    for p in (_ROOT / "swimm_key.txt", _HERE / "swimm_key.txt"):
        if p.exists():
            k = p.read_text(encoding="utf-8").strip()
            if k and "PASTE" not in k.upper():
                return k
    raise RuntimeError(
        "No Swimm Apollo key. Set env var APOLLO_SWIMM_KEY (Vercel) "
        "or put the raw key in swimm_key.txt (local)."
    )


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Apollo:
    def __init__(self, api_key: str):
        self.key = api_key

    def _req(self, method, path, body=None, params=None) -> dict:
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.key,
            "Connection": "close",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        # Apollo rate-limits per minute AND per hour. The defaults below add up
        # to about two minutes of patience, which suits a web request but reads
        # an HOURLY quota as a permanent failure — on 2026-09-06 that returned a
        # sync covering 4 days out of 30 and looked like success. The nightly job
        # has nowhere to be and raises both values via the environment, so it can
        # sit out a quota window instead of giving up inside it.
        attempts = max(5, RETRY_429_ATTEMPTS)
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < attempts - 1:
                    time.sleep(min(RETRY_429_MAX_SLEEP, 10 * (2 ** attempt)))
                    continue
                if exc.code in (500, 502, 503) and attempt < 4:
                    time.sleep(4 * (attempt + 1)); continue
                raise
            except Exception:
                if attempt < 4:
                    time.sleep(4 * (attempt + 1)); continue
                raise
        return {}

    def get(self, path, params=None): return self._req("GET",  path, params=params)
    def post(self, path, body):       return self._req("POST", path, body=body)


def _first_list(resp: dict) -> list:
    first = None
    for v in resp.values():
        if isinstance(v, list):
            if v:
                return v
            if first is None:
                first = v
    return first or []


# ── name helpers ─────────────────────────────────────────────────────────────
_PARTICLES = {"van", "von", "de", "del", "della", "di", "da", "der", "den",
              "la", "le", "el", "bin", "ibn", "al"}


def _is_hex_id(s: str) -> bool:
    return bool(s) and len(s) >= 20 and all(c in "0123456789abcdefABCDEF" for c in s)


def _cap_word(w: str) -> str:
    if not w:
        return w
    low = w.lower()
    if low in _PARTICLES:
        return low
    for sep in ("'", "-"):
        if sep in low:
            return sep.join(_cap_word(p) for p in low.split(sep))
    if low.startswith("mc") and len(low) > 3:
        return "Mc" + low[2:3].upper() + low[3:]
    if low.startswith("mac") and len(low) > 4:
        return "Mac" + low[3:4].upper() + low[4:]
    return low[:1].upper() + low[1:]


def name_from_email(email: str) -> str:
    local = email.split("@")[0].split("+")[0]
    local = re.sub(r"\d+$", "", local)
    parts = [p for p in re.split(r"[._]+", local) if p]
    out = [p.upper() if len(p) == 1 else _cap_word(p) for p in parts]
    return " ".join(out) if out else email


def tidy_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not n:
        return n
    if n == n.lower() or n == n.upper():
        return " ".join(_cap_word(w) for w in n.split(" "))
    return n


def _load_overrides(errors: list) -> dict:
    for p in (_ROOT / "name_overrides.json", _HERE / "name_overrides.json"):
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                return {k.strip().lower(): v for k, v in raw.items()
                        if not k.startswith("_") and isinstance(v, str) and v.strip()}
            except Exception as exc:
                errors.append(f"name_overrides: {exc}")
    return {}


def resolve_names(api: Apollo, calls: list, emails: list, errors: list) -> dict:
    names: dict[str, str] = {}
    emails_map: dict[str, str] = {}

    for c in calls:
        uid = str(c.get("user_id") or "")
        if not uid or uid in names:
            continue
        cn = (c.get("caller_name") or "").strip()
        if cn and not _is_hex_id(cn):
            names[uid] = cn

    for m in emails:
        uid = str(m.get("user_id") or m.get("emailer_user_id") or "")
        if not uid:
            continue
        fe = (m.get("from_email") or "").strip()
        if fe:
            emails_map.setdefault(uid, fe)
        if uid in names:
            continue
        fn = (m.get("from_name") or "").strip()
        if fn and not _is_hex_id(fn):
            names[uid] = fn
        elif fe:
            names[uid] = name_from_email(fe)

    all_ids = {str(c.get("user_id") or "") for c in calls if c.get("user_id")} | \
              {str(m.get("user_id") or m.get("emailer_user_id") or "") for m in emails
               if (m.get("user_id") or m.get("emailer_user_id"))}
    missing = {u for u in all_ids if u and u not in names}

    if missing:
        try:
            resp = api.get("/v1/users", {"page": 1, "per_page": PER_PAGE})
            for u in (resp.get("users") or _first_list(resp)):
                uid = str(u.get("id") or "")
                nm = (u.get("name") or
                      f"{u.get('first_name','') or ''} {u.get('last_name','') or ''}".strip())
                if uid and nm and not _is_hex_id(nm):
                    names.setdefault(uid, nm)
                if uid and u.get("email"):
                    emails_map.setdefault(uid, u["email"])
            missing = {u for u in missing if u not in names}
        except Exception as exc:
            errors.append(f"users list: {exc}")

    if missing:
        ids = list(missing)
        for i in range(0, len(ids), 50):
            try:
                resp = api.post("/v1/users/search",
                                {"q_keywords": "", "user_ids": ids[i:i + 50]})
                for u in (resp.get("users") or _first_list(resp)):
                    uid = str(u.get("id") or "")
                    nm = (u.get("name") or
                          f"{u.get('first_name','') or ''} {u.get('last_name','') or ''}".strip())
                    if uid and nm and not _is_hex_id(nm):
                        names[uid] = nm
                    if uid and u.get("email"):
                        emails_map.setdefault(uid, u["email"])
            except Exception:
                pass

    for uid in list(names.keys()):
        names[uid] = tidy_name(names[uid])

    for uid in list(all_ids):
        if uid and uid not in names and emails_map.get(uid):
            names[uid] = name_from_email(emails_map[uid])

    for uid in all_ids:
        if uid and uid not in names:
            names[uid] = "SDR-" + uid[:8]

    ov = _load_overrides(errors)
    if ov:
        for uid in list(names.keys()):
            em = (emails_map.get(uid) or "").strip().lower()
            if em and em in ov:
                names[uid] = ov[em]
            elif uid.lower() in ov:
                names[uid] = ov[uid.lower()]

    return {"names": names, "emails": emails_map}


# ── fetchers ─────────────────────────────────────────────────────────────────
# 0.8s between pages is roughly 35 requests/minute — comfortably under Apollo's
# per-minute limit. Slower than necessary for a small sync, but the nightly job
# has nowhere to be, and pushing harder is what earns the 429s.
PAGE_DELAY_S = float(os.environ.get("SWIMM_PAGE_DELAY", "0.8"))

# Apollo returns 100 records per page, so max_pages is a hard ceiling on how
# much a sync can see: the old default of 100 pages meant 10,000 calls, and
# Swimm passed that long ago (Apollo's own total read 15,923 on 2026-09-06).
# The dashboard was reporting a suspiciously round 10,000 for exactly this
# reason — it was showing the ceiling, not the count. The ceiling is meant to
# stop a runaway walk, not to cap real data, so it now sits well above the
# actual volume. Callers that must answer within a request still pass a
# deadline, and the loops below truncate cleanly when it expires.
MAX_PAGES = int(os.environ.get("SWIMM_MAX_PAGES", "400"))      # 40,000 records


def fetch_calls(api: Apollo, min_date: str, errors: list,
                max_pages: int = MAX_PAGES,
                deadline: float | None = None) -> list:
    calls: list = []
    for page in range(1, max_pages + 1):
        if deadline and time.monotonic() > deadline:
            errors.append("calls: truncated (time budget)"); break
        if page > 1:
            time.sleep(PAGE_DELAY_S)   # stay under Apollo's per-minute limit
        try:
            resp = api.post("/v1/phone_calls/search", {
                "sort_by_field": "start_time", "sort_ascending": False,
                "per_page": 100, "page": page,
            })
        except Exception as exc:
            errors.append(f"calls: {exc}"); break
        page_calls = resp.get("calls") or resp.get("phone_calls")
        if not page_calls:
            break
        calls.extend(page_calls)
        if any((c.get("start_time") or "") < min_date
               for c in page_calls if c.get("start_time")):
            break
        pg = resp.get("pagination") or {}
        if pg.get("total_pages") and page >= pg["total_pages"]:
            break
    return calls


def fetch_emails(api: Apollo, min_date: str, max_date: str, errors: list,
                 max_pages: int = MAX_PAGES,
                 deadline: float | None = None) -> list:
    out: list = []
    for page in range(1, max_pages + 1):
        if deadline and time.monotonic() > deadline:
            errors.append("emails: truncated (time budget)"); break
        if page > 1:
            time.sleep(PAGE_DELAY_S)
        try:
            resp = api.post("/v1/emailer_messages/search", {
                "filter_by_email_status": ["sent", "opened", "replied", "bounced"],
                "date_range": {"min_date": min_date, "max_date": max_date},
                "page": page, "per_page": PER_PAGE,
            })
        except Exception as exc:
            errors.append(f"emails: {exc}"); break
        page_em = _first_list(resp)
        if not page_em:
            break
        out.extend(page_em)
        pg = resp.get("pagination") or {}
        if pg.get("total_pages") and page >= pg["total_pages"]:
            break
    return out


# Swimm's actual Apollo dispositions, with Apollo's own Connected/Not-Connected
# classification and sentiment. Taken from Settings → Calls → Dispositions.
# Note two near-identical names that Apollo classifies DIFFERENTLY:
#   "Bad / Wrong Number" → Not Connected      "Bad/wrong number" → Connected
DISPOSITIONS = {
    # ── Not Connected ──
    "no answer":                    ("not_connected", ""),
    "left voicemail":               ("not_connected", ""),
    "busy":                         ("not_connected", ""),
    "gatekeeper":                   ("not_connected", ""),
    "bad / wrong number":           ("not_connected", ""),
    "not in service":               ("not_connected", ""),
    "auto vm":                      ("not_connected", ""),
    # ── Connected ──
    "connected - positive":         ("connected", "positive"),
    "connected - neutral":          ("connected", "neutral"),
    "connected - negative":         ("connected", "negative"),
    "rejected before the pitch":    ("connected", "negative"),
    "rejected after the pitch":     ("connected", "negative"),
    "do not contact":               ("connected", "negative"),
    "not the right person":         ("connected", "negative"),
    "not the right company":        ("connected", "negative"),
    "does not work at the company": ("connected", "neutral"),
    "out of the office":            ("connected", "neutral"),
    "bad/wrong number":             ("connected", "negative"),
    "meeting scheduled":            ("connected", "positive"),
}


def _categorize_outcome(label: str) -> str:
    """'connected' / 'not_connected' for a disposition label."""
    key = " ".join((label or "").lower().split())
    if key in DISPOSITIONS:
        return DISPOSITIONS[key][0]
    # Unknown/new disposition — fall back to wording.
    if "connected" in key or "meeting" in key or "rejected" in key:
        return "connected"
    if any(k in key for k in ("no answer", "voicemail", "busy", "vm",
                              "not in service", "gatekeeper")):
        return "not_connected"
    return label or "Unknown"


def _sentiment(label: str) -> str:
    key = " ".join((label or "").lower().split())
    return DISPOSITIONS.get(key, ("", ""))[1]


def fetch_outcome_map(api: Apollo, errors: list) -> dict:
    """
    {outcome_id: (label, bucket)} — the rep-selected disposition.

    Apollo's lookup endpoints (/v1/phone_call_outcomes and friends) return 404
    on this account, so the names come from outcome_names.json, which maps the
    ids seen on call records to the labels shown in the Apollo UI.
    """
    out: dict = {}

    # Preferred: the API, if this account ever exposes it.
    try:
        resp = api.get("/v1/phone_call_outcomes")
        raw = resp.get("phone_call_outcomes") or resp.get("call_outcomes") or _first_list(resp)
        for o in raw:
            oid = str(o.get("id") or "")
            label = o.get("name") or o.get("label") or ""
            if oid and label:
                out[oid] = (label, _categorize_outcome(label))
    except Exception:
        pass   # expected on this account; the local map covers it

    # Local id → label map (edit outcome_names.json to name them).
    for p in (_ROOT / "outcome_names.json", _HERE / "outcome_names.json"):
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                for oid, label in raw.items():
                    if oid.startswith("_") or not isinstance(label, str) or not label.strip():
                        continue
                    out[oid.strip()] = (label.strip(), _categorize_outcome(label))
            except Exception as exc:
                errors.append(f"outcome_names: {exc}")
            break

    if not out:
        errors.append("no call-outcome labels available "
                      "(fill outcome_names.json to name them)")
    return out


def _contact_name(c: dict) -> str:
    ct = c.get("contact") or {}
    if isinstance(ct, dict):
        nm = (ct.get("name") or
              f"{ct.get('first_name','') or ''} {ct.get('last_name','') or ''}".strip())
        if nm:
            return nm
        if ct.get("email"):
            return ct["email"]
    for k in ("contact_name", "to_name", "callee_name"):
        if c.get(k):
            return str(c[k])
    return ""


def _recording_url(c: dict) -> str:
    """
    Playable recording URL, or "".

    Apollo hands back two kinds. Its own S3 objects are publicly readable and
    stream fine (verified: HTTP 200, ~1.3 MB for a 40s call). Raw api.twilio.com
    links need Twilio credentials we don't have and answer 404, so they are
    dropped rather than rendered as a button that goes nowhere.
    """
    for k in ("recording_url", "call_recording_url", "recording",
              "audio_url", "media_url"):
        v = c.get(k)
        url = ""
        if isinstance(v, str) and v.startswith("http"):
            url = v
        elif isinstance(v, dict):
            for kk in ("url", "link", "href"):
                if isinstance(v.get(kk), str) and v[kk].startswith("http"):
                    url = v[kk]
                    break
        if url and "twilio.com" not in url:
            return url
    return ""


def fetch_sequences(api: Apollo, errors: list) -> int:
    try:
        resp = api.post("/v1/emailer_campaigns/search",
                        {"q_keywords": "", "page": 1, "per_page": 1})
        return int((resp.get("pagination") or {}).get("total_entries") or 0)
    except Exception as exc:
        errors.append(f"sequences: {exc}")
        return 0


# Stages worth listing contact-by-contact (the ones a manager actually reviews).
DRILL_STAGES = {
    "interested", "meeting scheduled", "meeting qualified",
    "meeting disqualified", "meeting unqualified", "follow up",
    "hot lead", "replied", "meeting rescheduled",
    "meeting needs to be rescheduled", "nurturing", "open deal",
}
DRILL_MAX = 150     # never pull a huge stage like "Cold" (5k+) contact-by-contact


def fetch_pipeline(api: Apollo, errors: list, uname: dict | None = None) -> list:
    """
    Contact-stage counts, plus the actual contacts for the review-worthy stages.

    Counting is one cheap call per stage (per_page=1 → pagination.total_entries).
    Contacts are only pulled for DRILL_STAGES that are small enough to be worth
    reading, so "Cold" with 5,000 rows never gets walked.
    """
    try:
        resp = api.get("/v1/contact_stages")
        stages = resp.get("contact_stages") or _first_list(resp)
    except Exception as exc:
        errors.append(f"contact_stages: {exc}")
        return []

    uname = uname or {}
    out = []
    for s in stages:
        sid = str(s.get("id") or "")
        name = (s.get("name") or s.get("display_name") or "").strip()
        if not sid or not name:
            continue
        try:
            r = api.post("/v1/contacts/search",
                         {"contact_stage_ids": [sid], "page": 1, "per_page": 1})
            total = int((r.get("pagination") or {}).get("total_entries") or 0)
        except Exception as exc:
            errors.append(f"stage '{name}': {exc}")
            continue

        entry = {"stage": name, "count": total, "contacts": []}
        key = " ".join(name.lower().split())
        if total and key in DRILL_STAGES and total <= DRILL_MAX:
            time.sleep(PAGE_DELAY_S)
            try:
                people = []
                for page in range(1, 3):          # ≤200 rows is plenty
                    rr = api.post("/v1/contacts/search",
                                  {"contact_stage_ids": [sid],
                                   "page": page, "per_page": 100})
                    got = _first_list(rr)
                    if not got:
                        break
                    people.extend(got)
                    if len(got) < 100:
                        break
                    time.sleep(PAGE_DELAY_S)
                for c in people:
                    nm = (c.get("name") or
                          f"{c.get('first_name','') or ''} {c.get('last_name','') or ''}").strip()
                    org = c.get("organization") or {}
                    entry["contacts"].append({
                        "name":  nm,
                        "company": (c.get("organization_name")
                                    or (org.get("name") if isinstance(org, dict) else "") or ""),
                        "title": c.get("title") or "",
                        "email": c.get("email") or "",
                        "owner": uname.get(str(c.get("owner_id") or ""), ""),
                        "url":   ("https://app.apollo.io/#/people/" + str(c.get("id"))
                                  if c.get("id") else ""),
                    })
            except Exception as exc:
                errors.append(f"stage '{name}' contacts: {exc}")

        out.append(entry)
        time.sleep(PAGE_DELAY_S)
    return out


def _day(ts: str) -> str:
    return (ts or "")[:10]


# ── the one function both entry points call ──────────────────────────────────
def build_payload(days: int = 30, time_budget_s: float | None = None) -> dict:
    """Fetch from Apollo and return the dashboard payload (daily buckets)."""
    api = Apollo(load_key())
    now = datetime.datetime.now(datetime.timezone.utc)
    min_date = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    max_date = now.strftime("%Y-%m-%d")
    deadline = (time.monotonic() + time_budget_s) if time_budget_s else None

    errors: list = []
    # Cheapest first: if the time budget runs out it should cost us the tail of
    # the call list, not the whole email dataset.
    seqs     = fetch_sequences(api, errors)
    omap     = fetch_outcome_map(api, errors)
    emails = fetch_emails(api, min_date, max_date, errors, deadline=deadline)
    calls  = fetch_calls(api, min_date, errors, deadline=deadline)

    resolved = resolve_names(api, calls, emails, errors)
    uname, uemail = resolved["names"], resolved["emails"]

    # after uname, so each contact's owner_id resolves to a real SDR name
    pipeline = fetch_pipeline(api, errors, uname)

    calls_daily: dict = {}
    connected: list = []          # individual connected calls, for the drill-down
    unmapped: dict = {}           # outcome ids with no label yet
    for c in calls:
        d = _day(c.get("start_time"))
        if not d or d < min_date:
            continue
        nm = uname.get(str(c.get("user_id") or ""), "Unknown")
        try:
            secs = int(c.get("duration") or c.get("call_duration")
                       or c.get("duration_in_seconds") or 0)
        except Exception:
            secs = 0
        secs = max(0, secs)

        oid = str(c.get("phone_call_outcome_id") or "")
        raw_label, bucket = omap.get(oid, ("", ""))
        if oid and not raw_label:
            unmapped[oid] = unmapped.get(oid, 0) + 1
        tw_status = str(c.get("status") or "")          # completed / no-answer / busy
        # Rep-selected disposition wins. Reps only log ~27% of calls, so for the
        # rest fall back to telephony: the line completed AND someone talked.
        is_conn = (bucket == "connected") or (
            not raw_label and tw_status == "completed" and secs >= 30)

        cur = calls_daily.setdefault((d, nm), {"count": 0, "secs": 0, "connected": 0})
        cur["count"] += 1
        cur["secs"]  += secs
        if is_conn:
            cur["connected"] += 1
            note = (c.get("note") or c.get("notes") or "")
            if isinstance(note, dict):
                note = note.get("body") or note.get("text") or ""
            connected.append({
                "date": d,
                "at":   (c.get("start_time") or "")[11:16],   # HH:MM
                "sdr":  nm,
                "contact": _contact_name(c),
                "phone":   c.get("to_number") or c.get("phone_number") or "",
                "secs":    secs,
                # what the rep logged; falls back to the telephony status
                "outcome": raw_label or (tw_status or "—"),
                "logged":  bool(raw_label),      # true = rep actually dispositioned it
                "note":    str(note or c.get("note_text") or "")[:400],
                "rec":     _recording_url(c),
                "cid":     str(c.get("id") or ""),
            })

    # Engagement per message. Apollo may express it as a single `email_status`
    # or as timestamp fields, so check both. 'replied' implies it was opened.
    emails_daily: dict = {}
    for m in emails:
        d = _day(m.get("completed_at") or m.get("created_at") or m.get("sent_at"))
        if not d:
            continue
        nm = uname.get(str(m.get("user_id") or m.get("emailer_user_id") or ""), "Unknown")
        st = str(m.get("email_status") or m.get("status") or "").lower()

        bounced = bool(m.get("bounced_at")) or "bounce" in st
        replied = bool(m.get("replied_at")) or "replied" in st
        opened  = (bool(m.get("opened_at")) or "opened" in st
                   or replied
                   or bool(m.get("is_opened")) or int(m.get("open_count") or 0) > 0)

        cur = emails_daily.setdefault((d, nm),
                                      {"count": 0, "opened": 0, "replied": 0, "bounced": 0})
        cur["count"] += 1
        if opened:  cur["opened"]  += 1
        if replied: cur["replied"] += 1
        if bounced: cur["bounced"] += 1

    return {
        "generated_at": now.isoformat(),
        "account": ACCOUNT,
        "window_days": days,
        "date_min": min_date,
        "date_max": max_date,
        "sequences": seqs,
        "pipeline": pipeline,
        "sdr_emails": {uname[u]: e for u, e in uemail.items() if u in uname},
        "calls_daily":  [{"date": d, "sdr": s, "count": v["count"], "secs": v["secs"],
                          "connected": v.get("connected", 0)}
                         for (d, s), v in sorted(calls_daily.items())],
        # newest first, capped so the payload stays light
        "connected_calls": sorted(connected,
                                  key=lambda r: (r["date"], r["at"]),
                                  reverse=True)[:2500],
        "emails_daily": [{"date": d, "sdr": s, "count": v["count"],
                          "opened": v["opened"], "replied": v["replied"],
                          "bounced": v["bounced"]}
                         for (d, s), v in sorted(emails_daily.items())],
        "errors": errors,
        "_counts": {"calls": len(calls), "emails": len(emails),
                    "sdrs": len(uname),
                    "named": sum(1 for v in uname.values() if not v.startswith("SDR-")),
                    "unmapped_outcomes": dict(sorted(unmapped.items(),
                                                     key=lambda kv: -kv[1]))},
    }
