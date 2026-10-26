"""Outcome metrics: before-window vs during-window comparison from a
message/response CSV export. Columns: message_id, property, received_at,
first_response_at, category, responded_by (host|va|system), dispatch_at.
Missing timestamps are excluded from their metric and counted, never
silently dropped. Response anchor for dispatch latency is received_at
(the live pipeline decides within seconds of receipt); the report says so.
"""
import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone


def parse_ts(value: str):
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _median(xs):
    return statistics.median(xs) if xs else None


def _p90(xs):
    if not xs:
        return None
    s = sorted(xs)
    import math
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


def summarize(rows: list[dict]) -> dict:
    urgent_resp, dispatch, missing_resp = [], [], 0
    volumes, no_touch, touch_base = {}, 0, 0
    for r in rows:
        cat = r.get("category", "")
        volumes[cat] = volumes.get(cat, 0) + 1
        recv, resp = parse_ts(r.get("received_at", "")), parse_ts(r.get("first_response_at", ""))
        if cat == "Act":
            if recv and resp:
                urgent_resp.append((resp - recv).total_seconds() / 60)
            else:
                missing_resp += 1
        if r.get("responded_by"):
            touch_base += 1
            if r["responded_by"] != "host":
                no_touch += 1
        disp = parse_ts(r.get("dispatch_at", ""))
        if recv and disp:
            dispatch.append((disp - recv).total_seconds() / 60)
    return {
        "n": len(rows),
        "volumes": volumes,
        "urgent_n": sum(1 for r in rows if r.get("category") == "Act"),
        "urgent_response_median_min": _median(urgent_resp),
        "urgent_response_p90_min": _p90(urgent_resp),
        "urgent_missing_timestamps": missing_resp,
        "no_touch_rate": (no_touch / touch_base) if touch_base else None,
        "no_touch_base": touch_base,
        "dispatch_median_min": _median(dispatch),
        "dispatch_n": len(dispatch),
    }


def load(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def render(before: dict, during: dict) -> str:
    def f(x, suffix=""):
        return "n/a" if x is None else f"{x:.1f}{suffix}"
    flag = ""
    if before["urgent_n"] < 10 or during["urgent_n"] < 10:
        flag = "\n> Directional only: at least one window has fewer than 10 urgent messages.\n"
    lines = [
        "# Outcome metrics: before vs during", flag,
        "| Metric | Before | During |",
        "|---|---|---|",
        f"| Messages | {before['n']} | {during['n']} |",
        f"| Urgent (Act) messages | {before['urgent_n']} | {during['urgent_n']} |",
        f"| Urgent response, median | {f(before['urgent_response_median_min'], ' min')} | {f(during['urgent_response_median_min'], ' min')} |",
        f"| Urgent response, p90 | {f(before['urgent_response_p90_min'], ' min')} | {f(during['urgent_response_p90_min'], ' min')} |",
        f"| Urgent rows missing timestamps (excluded) | {before['urgent_missing_timestamps']} | {during['urgent_missing_timestamps']} |",
        f"| No-touch rate (responder was not the host) | {f(before['no_touch_rate'] * 100 if before['no_touch_rate'] is not None else None, '%')} (n={before['no_touch_base']}) | {f(during['no_touch_rate'] * 100 if during['no_touch_rate'] is not None else None, '%')} (n={during['no_touch_base']}) |",
        f"| Cleaner dispatch latency, median (from receipt) | {f(before['dispatch_median_min'], ' min')} (n={before['dispatch_n']}) | {f(during['dispatch_median_min'], ' min')} (n={during['dispatch_n']}) |",
        "",
        "Dispatch latency is anchored at message receipt; the live pipeline decides within seconds of receipt.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--during", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import os
    before, during = summarize(load(args.before)), summarize(load(args.during))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "report.md"), "w") as fh:
        fh.write(render(before, during))
    with open(os.path.join(args.out, "stats.json"), "w") as fh:
        json.dump({"before": before, "during": during}, fh, indent=1, default=str)
    print(f"Report written to {args.out}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
