import datetime
import json
import os
import urllib.request

EMET_URL = "https://account-management-hub-1e875b2b.base44.app/functions/swimmDashboardMeetings"
STATUSES = {"SQL", "No Show", "Reschedule", "Not Relevant"}


def parse_when(s: str):
    s = " ".join((s or "").split())
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%B %d, %Y, %I:%M:%S %p",
                "%b %d, %Y, %I:%M %p", "%B %d, %Y, %I:%M %p",
                "%b %d, %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def shape(raw: list) -> list:
    out = []
    for m in raw:
        booked = parse_when(m.get("booked") or "")
        when = parse_when(m.get("meeting") or "")
        status = m.get("status") if m.get("status") in STATUSES else None
        out.append({
            "booked": booked.strftime("%Y-%m-%d") if booked else "",
            "meeting": when.strftime("%Y-%m-%d %H:%M") if when else "",
            "status": status,
            "company": (m.get("company") or "").strip(),
            "contact": (m.get("contact") or "").strip(),
            "title": (m.get("title") or "").strip(),
            "sdr": " ".join((m.get("sdr") or "").split()),
        })
    out.sort(key=lambda r: (r["booked"], r["meeting"]), reverse=True)
    return out


def fetch_meetings(errors: list):
    key = (os.environ.get("SWIMM_EMET_KEY") or "").strip()
    if not key:
        errors.append("meetings: SWIMM_EMET_KEY is not set")
        return None
    req = urllib.request.Request(
        EMET_URL,
        data=json.dumps({"key": key}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        errors.append(f"meetings: {exc}")
        return None
    raw = body.get("meetings")
    if not isinstance(raw, list):
        errors.append(f"meetings: unexpected answer from EMET ({str(body)[:120]})")
        return None
    return shape(raw)
