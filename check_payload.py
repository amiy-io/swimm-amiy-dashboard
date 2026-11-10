import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD_JSON = ROOT / "swimm-payload.json"
PAYLOAD_PY = ROOT / "api" / "_payload.py"

FATAL_ERROR_WORDS = ("truncated", "429", "Too Many Requests", "calls:", "emails:")


def fail(msg: str) -> int:
    print(f"REFUSING TO DEPLOY: {msg}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-hours", type=float, default=6)
    ap.add_argument("--min-call-days", type=int, default=15)
    args = ap.parse_args()

    if not PAYLOAD_JSON.exists():
        return fail("swimm-payload.json is missing")
    if not PAYLOAD_PY.exists():
        return fail("api/_payload.py is missing")

    try:
        p = json.loads(PAYLOAD_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"payload is not valid JSON ({exc})")

    sys.path.insert(0, str(ROOT / "api"))
    try:
        import _payload
        baked = _payload.load()
    except Exception as exc:
        return fail(f"api/_payload.py does not load ({exc})")
    if baked.get("generated_at") != p.get("generated_at"):
        return fail("api/_payload.py and swimm-payload.json disagree")

    try:
        gen = datetime.datetime.fromisoformat(p["generated_at"])
    except Exception:
        return fail("generated_at is missing or unreadable")
    age_h = (datetime.datetime.now(datetime.timezone.utc) - gen).total_seconds() / 3600
    if age_h > args.max_age_hours:
        return fail(f"payload is {age_h:.1f}h old")

    calls = p.get("calls_daily") or []
    if not calls:
        return fail("no call data at all")
    call_days = sorted({r["date"] for r in calls})
    if len(call_days) < args.min_call_days:
        return fail(f"calls cover only {len(call_days)} days")
    first = datetime.date.fromisoformat(call_days[0])
    want = datetime.date.fromisoformat(p["date_min"])
    if (first - want).days > 4:
        return fail(f"calls start {first}, window starts {want}: the walk stopped early")

    if not p.get("emails_daily"):
        return fail("no email data at all")

    bad = [e for e in p.get("errors", []) if any(w in e for w in FATAL_ERROR_WORDS)]
    if bad:
        return fail("; ".join(bad))

    c = p.get("_counts", {})
    print(f"OK  {c.get('calls')} calls over {len(call_days)} days, "
          f"{c.get('emails')} emails, built {age_h:.1f}h ago")
    return 0


if __name__ == "__main__":
    sys.exit(main())
