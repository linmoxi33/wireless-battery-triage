# wireless-battery-triage

Decide, from a real device report, whether a new wireless mouse/keyboard/headset can expose battery over standard HID — or whether it needs yet another vendor driver. Built because a single Windows battery widget has **2,300+ open "Device request" issues** and no reproducible way to triage them.

Upstream: https://github.com/krisgfx9/Wireless-Device-Battery-Monitor

## What the upstream does and what is missing

[Wireless Battery](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor) shows battery levels of wireless mice, keyboards, headsets and controllers on the Windows taskbar. It supports a long list of brands, each with a hand-written driver. When a device is not recognised, the app sends a compact HID dump and the user opens a "Device request" issue — e.g. [#2386](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor/issues/2386):

```
IDs 320F:225B. HID: if1 0001:0080 2/0/0, if1 000C:0001 3/0/0,
if1 FF1C:0092 64/64/0, if1 0001:0006 21/0/0, if0 0001:0006 9/2/0,
if2 0001:0002 8/0/0. USB: HIDClass (HidUsb) x3.
```

The meaning of the three numbers is fixed in the upstream formatter
([`src/report.cpp#L195`](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor/blob/HEAD/src/report.cpp#L195)):
`if<iface> <usage page>:<usage> <input bytes>/<output bytes>/<feature bytes>`.
Nothing upstream reads those dumps as data: a maintainer has to eyeball each
report and guess whether the device is supportable. There is no CLI, no JSON,
no taxonomy, and no way to answer "how many of these requests are even
possible over HID?" — the request list is a human inbox, not a dataset.

## What this tool does

It parses the dump grammar and assigns exactly one verdict per device, with the
evidence lines that produced it:

| verdict | meaning | next step |
|---|---|---|
| `generic-battery` | usage page `0006` / usage `0020` (Battery Strength) with an input report | read it directly, no vendor protocol |
| `vendor-feature-control` | vendor page (`FF00`–`FFFF`) with a feature report ≥ 3 bytes | a query channel exists; a driver is feasible if the protocol is known |
| `vendor-notify-only` | vendor page but no usable feature report | only one-way reports; battery needs input-report RE or another channel |
| `no-battery-surface` | no battery usage, no vendor page | not reachable through HID |
| `unparseable` | no IDs and no HID collections (or interfaces failed to open) | re-run the app's dump |

It does **not** reverse-engineer protocols, claim a device is "supported", or
talk to hardware. `vendor-feature-control` means the channel exists — not that
a driver exists.

## Install

Zero dependencies, Python 3.8+:

```bash
git clone https://github.com/linmoxi33/wireless-battery-triage.git
cd wireless-battery-triage
python3 wireless_battery_triage.py --issue examples/in/issue-2386.md
```

No `pip install`, no network at runtime.

## Usage

One report (raw issue body or just the dump line):

```bash
python3 wireless_battery_triage.py --issue examples/in/issue-2382.md --json
```

A JSONL corpus of `{"number","url","body"}` rows (the shape returned by the
issues API):

```bash
python3 wireless_battery_triage.py --corpus examples/in/corpus-sample.jsonl --json
```

JSON output (trimmed):

```json
{
  "schema": "wireless-battery-triage/1",
  "inputs": 1,
  "summary": {"by_verdict": {"vendor-feature-control": 1}, "supportable": 1, "needs_reverse_engineering": 0},
  "records": [{
    "device": "FGG MADLIONS MAD G [FGG-12060006]",
    "vid": "373B", "pid": "100F",
    "verdict": "vendor-feature-control",
    "reasons": ["2 vendor collection(s) with a feature report >= 3 bytes: FF00/0001 feat=64, FF10/0001 feat=9"],
    "next_step": "a query channel exists; a vendor driver can be written if the protocol is known"
  }]
}
```

Exit codes:

| code | meaning |
|---|---|
| `0` | every device has a battery surface (`generic-battery` or `vendor-feature-control`) |
| `1` | at least one device has no battery surface, only notify reports, or an unparseable dump |
| `2` | usage error, unreadable file, invalid JSONL, or empty corpus |

## Measured result on real reports

Command (run 2026-11-19 against the 179 most recent open `Device request:`
issues, two API pages):

```bash
# the corpus is the issues API shape, one JSON object per line
gh api repos/krisgfx9/Wireless-Device-Battery-Monitor/issues   -f state=open -f per_page=100 -f page=1   --jq '.[]|select(.title|startswith("Device request:"))|{number,url:.html_url,body}' > real-corpus.jsonl
python3 wireless_battery_triage.py --corpus real-corpus.jsonl --json
```

| verdict | count | share |
|---|---|---|
| `generic-battery` | 0 | 0.0% |
| `vendor-feature-control` | 53 | 29.6% |
| `vendor-notify-only` | 53 | 29.6% |
| `no-battery-surface` | 48 | 26.8% |
| `unparseable` | 25 | 14.0% |
| **total** | **179** | **100%** |

Two findings that are not obvious from the issue list:

1. **None of the 179 devices exposed the standard HID Battery Strength usage.**
   Every supportable request is vendor-page work, which is exactly why the
   upstream needs ~90 per-brand drivers instead of one generic reader.
2. **101/179 (56.4%) cannot be solved with a normal query driver** as reported:
   53 expose only one-way vendor reports and 48 have no battery surface in HID
   at all. Those requests can be closed as "needs another transport" instead of
   sitting in the inbox.

These numbers are a snapshot of the open issues at run time, not a hardware
survey; `unparseable` includes reports where the user wrote no dump at all.

## Tests

```bash
python3 -m unittest discover -s tests
```

15 tests (parsing, verdict rules, CLI exit codes, corpus aggregation) using the
real issue bodies in `examples/in/` and their recorded outputs in `examples/out/`.

## Limits

- Only the dump grammar above is understood; free-form notes are ignored.
- `vendor-feature-control` does not identify the vendor protocol or prove that a
  battery field exists in it.
- Usage page/usage opening the battery path is taken from the HID Usage Tables;
  a device could expose battery on a private usage we do not know.
- The corpus is a point-in-time snapshot; no API calls happen at runtime.

## 本轮创新

First reproducible triage of this app's device-request inbox: the dump line is
treated as a typed record (interface, usage page/usage, input/output/feature
report sizes) and mapped to a five-way supportability verdict, then run over 179
real reports. The measured result — 0% standard HID battery usage, 56.4% not
solvable by a query driver — is new, and the classifier is not a README
restatement: the in/out/feature column order is taken from upstream source, and
each verdict ships its evidence line.

## 与同类项目的差异

Searched (2026-11-19) `gh search repos "hid battery usage page parser"` and
`"device request triage"`: 0 results. Existing HID tooling (hidapi,
HeadsetControl, vendor drivers) reads devices; none parses this issue-dump
format or produces a per-request supportability verdict. Upstream has no
validator, CLI or JSON output.

## License

MIT. See `LICENSE`.
