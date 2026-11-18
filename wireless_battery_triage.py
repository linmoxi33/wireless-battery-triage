#!/usr/bin/env python3
"""wireless-battery-triage — classify the battery surface of a Windows HID device dump.

Reads the technical summary that the Wireless Battery app embeds in its
"Device request" issues ("IDs VVVV:PPPP. HID: if0 0001:0002 8/0/0, ...") and
answers one question per device: which HID path could a battery reading come
from, and is a query channel even available?

The dump grammar comes from the upstream formatter:
  src/report.cpp:195  swprintf(line, 64, L"if%d %04X:%04X %u/%u/%u",
                               iface, page, usage, inLen, outLen, featLen)

Exit codes: 0 = every device has a usable battery surface,
            1 = at least one device has no battery surface / needs RE,
            2 = usage or input error.
"""
import argparse
import json
import re
import sys

SCHEMA = "wireless-battery-triage/1"

# HID usage pages (HID 1.11 spec + HID Usage Tables 1.5).
PAGE_GENERIC_DESKTOP = 0x0001
PAGE_GENERIC_DEVICE = 0x0006
USAGE_BATTERY_STRENGTH = 0x0020  # Generic Device Controls, Battery Strength
VENDOR_PAGE_MIN = 0xFF00

HID_RE = re.compile(
    r"if(\d+)\s+(?:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\s+(\d+)/(\d+)/(\d+)|\(could not open\))"
)
IDS_RE = re.compile(r"IDs\s+([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})")
USB_RE = re.compile(r"USB:\s*(.+?)\.(?:\s|$)")
DEVICE_RE = re.compile(r"\*\*Device:\*\*\s*(.+)")
TYPE_RE = re.compile(r"\*\*Type:\*\*\s*(\S+)")
REQUESTS_RE = re.compile(r"\*\*Requests:\*\*\s*(\d+)")

VERDICTS = (
    "generic-battery",       # standard Battery Strength usage is exposed
    "vendor-feature-control",  # vendor page with a feature report -> query channel
    "vendor-notify-only",    # vendor collection, but no feature report to talk back
    "no-battery-surface",    # nothing battery-shaped in HID at all
    "unparseable",           # no IDs and no HID collections
)


def _unescape(s):
    """GitHub issue bodies escape (), [] with backslashes."""
    return s.replace("\\(", "(").replace("\\)", ")").replace("\\[", "[").replace("\\]", "]")


def parse_dump_text(text):
    """Parse the one-line technical summary. Returns dict (never raises)."""
    text = text or ""
    ids = IDS_RE.search(text)
    collections = []
    open_errors = 0
    for m in HID_RE.finditer(text):
        if m.group(2) is None:
            open_errors += 1
            collections.append({
                "iface": int(m.group(1)),
                "open_error": True,
            })
            continue
        page = int(m.group(2), 16)
        usage = int(m.group(3), 16)
        collections.append({
            "iface": int(m.group(1)),
            "page": page,
            "usage": usage,
            "page_hex": "%04X" % page,
            "usage_hex": "%04X" % usage,
            "in_len": int(m.group(4)),
            "out_len": int(m.group(5)),
            "feat_len": int(m.group(6)),
            "vendor": page >= VENDOR_PAGE_MIN,
        })
    usb = USB_RE.search(text)
    transports = []
    if usb:
        for part in usb.group(1).split(","):
            part = _unescape(part.strip())
            if part:
                transports.append(part)
    return {
        "vid": ids.group(1).upper() if ids else None,
        "pid": ids.group(2).upper() if ids else None,
        "collections": collections,
        "open_errors": open_errors,
        "transports": transports,
        "has_dump": bool(ids or collections),
    }


def parse_issue(body):
    """Parse a GitHub device-request issue body into a record."""
    body = body or ""
    text = " ".join(body.split())
    dump = parse_dump_text(text)
    device = DEVICE_RE.search(body)
    dtype = TYPE_RE.search(body)
    requests = REQUESTS_RE.search(body)
    out = {
        "device": device.group(1).strip().replace("\\[", "[").replace("\\]", "]") if device else None,
        "type": dtype.group(1).strip().lower() if dtype else None,
        "requests_merged": int(requests.group(1)) if requests else 1,
        "note": _note_text(body),
    }
    out.update(dump)
    return out


def _note_text(body):
    m = re.search(r"\*\*Note from the form:\*\*\s*\n+(.*?)(?:\n+---|\Z)", body, re.S)
    if not m:
        return ""
    note = m.group(1)
    note = re.sub(r"^\s*>\s?", "", note, flags=re.M)
    note = _unescape(re.sub(r"\\\[", "", note))
    return " ".join(note.split())[:400]


def classify(rec):
    """Return (verdict, reasons, next_step). Deterministic, ordered rules."""
    if not rec.get("has_dump"):
        return (
            "unparseable",
            ["no 'IDs VVVV:PPPP' and no 'HID:' collection in the report"],
            "ask the reporter to rerun the app's dump on the device and paste the line",
        )

    cols = [c for c in rec.get("collections", []) if not c.get("open_error")]
    reasons = []

    battery = [c for c in cols
               if c["page"] == PAGE_GENERIC_DEVICE
               and c["usage"] == USAGE_BATTERY_STRENGTH
               and c["in_len"] >= 1]
    if battery:
        reasons.append(
            "Battery Strength (page 0006 usage 0020) is exposed by iface(s) %s"
            % ",".join(str(c["iface"]) for c in battery))
        return ("generic-battery", reasons,
                "read the standard HID battery value; no vendor protocol needed")

    vendor = [c for c in cols if c["vendor"]]
    feature = [c for c in vendor if c["feat_len"] >= 3]
    if feature:
        reasons.append(
            "%d vendor collection(s) with a feature report >= 3 bytes: %s"
            % (len(feature), ", ".join("%s/%s feat=%d" % (c["page_hex"], c["usage_hex"], c["feat_len"]) for c in feature)))
        return ("vendor-feature-control", reasons,
                "a query channel exists; a vendor driver can be written if the protocol is known")

    if vendor:
        reasons.append(
            "%d vendor collection(s) but no feature report >= 3 bytes: %s"
            % (len(vendor), ", ".join("%s/%s in=%d out=%d feat=%d" % (c["page_hex"], c["usage_hex"], c["in_len"], c["out_len"], c["feat_len"]) for c in vendor)))
        return ("vendor-notify-only", reasons,
                "only one-way reports; battery needs input-report reverse engineering or a USB/other channel")

    if rec.get("open_errors"):
        reasons.append("%d HID interface(s) could not be opened by the app" % rec["open_errors"])
        return ("unparseable", reasons,
                "re-run the dump as admin/exclusive-owner so the interfaces open")

    pages = sorted({c["page_hex"] for c in cols})
    reasons.append("no battery usage and no vendor page; HID pages present: %s"
                   % (", ".join(pages) if pages else "none"))
    return ("no-battery-surface", reasons,
            "no HID battery surface; support would need USB/Bluetooth vendor data outside HID")


SUPPORTABLE = {"generic-battery", "vendor-feature-control"}


def build_report(records):
    by_verdict = {v: 0 for v in VERDICTS}
    for r in records:
        by_verdict[r["verdict"]] += 1
    total = len(records) or 1
    shares = {k: round(v / total, 4) for k, v in by_verdict.items()}
    return {
        "schema": SCHEMA,
        "inputs": len(records),
        "summary": {
            "by_verdict": by_verdict,
            "share_by_verdict": shares,
            "supportable": sum(by_verdict[k] for k in SUPPORTABLE),
            "needs_reverse_engineering": by_verdict["vendor-notify-only"]
            + by_verdict["no-battery-surface"],
        },
        "records": records,
    }


def load_corpus(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d: invalid JSON (%s)" % (path, lineno, exc))
            body = obj.get("body") or obj.get("text") or ""
            rec = parse_issue(body)
            rec["source"] = obj.get("number") or obj.get("id") or "%s:%d" % (path, lineno)
            rec["url"] = obj.get("url") or obj.get("html_url")
            rows.append(rec)
    return rows


def finalize(rec):
    rec["verdict"], rec["reasons"], rec["next_step"] = classify(rec)
    rec.pop("has_dump", None)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="wireless-battery-triage",
        description="Classify the HID battery surface of Wireless Battery device dumps.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--issue", metavar="FILE", help="one issue body / dump text file")
    src.add_argument("--corpus", metavar="JSONL", help="JSONL of {number,url,body}")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    try:
        if args.issue:
            try:
                text = sys.stdin.read() if args.issue == "-" else open(args.issue, encoding="utf-8").read()
            except OSError as exc:
                print("error: cannot read %s: %s" % (args.issue, exc), file=sys.stderr)
                return 2
            rec = parse_issue(text)
            rec["source"] = args.issue
            rec["url"] = None
            records = [rec]
        else:
            try:
                records = load_corpus(args.corpus)
            except (OSError, ValueError) as exc:
                print("error: cannot read corpus: %s" % exc, file=sys.stderr)
                return 2
    except Exception as exc:  # defensive: never traceback on user input
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if not records:
        print("error: no records found", file=sys.stderr)
        return 2

    records = [finalize(r) for r in records]
    report = build_report(records)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("wireless-battery-triage: %d input(s)" % report["inputs"])
        for r in records:
            print("[%s] %s  %s%s" % (
                r["verdict"],
                r.get("device") or "(no device name)",
                ("%s:%s" % (r["vid"], r["pid"])) if r.get("vid") else "no-ids",
                "  <- " + r["next_step"] if r["verdict"] not in SUPPORTABLE else "",
            ))
        s = report["summary"]
        print("-- supportable %d/%d, needs reverse engineering %d/%d" % (
            s["supportable"], report["inputs"],
            s["needs_reverse_engineering"], report["inputs"]))

    return 0 if all(r["verdict"] in SUPPORTABLE for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())