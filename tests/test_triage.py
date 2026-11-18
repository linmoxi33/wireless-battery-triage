import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wireless_battery_triage as wbt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def run_main(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = wbt.main(args)
    return rc, out.getvalue()


class ParseTests(unittest.TestCase):
    def test_ids_and_collection_count(self):
        rec = wbt.parse_issue(read("examples/in/issue-2386.md"))
        self.assertEqual((rec["vid"], rec["pid"]), ("320F", "225B"))
        self.assertEqual(len(rec["collections"]), 6)

    def test_length_trio_order_is_in_out_feat(self):
        # if2 FF10:0001 65/65/9 -> in=65, out=65, feat=9 (report.cpp:195)
        rec = wbt.parse_issue(read("examples/in/issue-2382.md"))
        c = [x for x in rec["collections"] if x["iface"] == 2][0]
        self.assertEqual((c["in_len"], c["out_len"], c["feat_len"]), (65, 65, 9))

    def test_lowercase_hex_is_accepted(self):
        rec = wbt.parse_dump_text("IDs abcd:0f0f. HID: if0 0006:0020 1/0/0.")
        self.assertEqual((rec["vid"], rec["pid"]), ("ABCD", "0F0F"))

    def test_usb_transports(self):
        rec = wbt.parse_issue(read("examples/in/issue-2381.md"))
        self.assertEqual(rec["transports"], ["HIDClass (HidUsb)", "MEDIA (usbaudio)"])


class ClassifyTests(unittest.TestCase):
    def verdict(self, path):
        return wbt.finalize(wbt.parse_issue(read(path)))["verdict"]

    def test_vendor_feature_control(self):
        self.assertEqual(self.verdict("examples/in/issue-2382.md"), "vendor-feature-control")

    def test_vendor_notify_only(self):
        self.assertEqual(self.verdict("examples/in/issue-2386.md"), "vendor-notify-only")

    def test_no_battery_surface(self):
        self.assertEqual(self.verdict("examples/in/issue-2381.md"), "no-battery-surface")

    def test_device_not_in_list_is_unparseable(self):
        self.assertEqual(self.verdict("examples/in/issue-2385.md"), "unparseable")

    def test_standard_battery_usage(self):
        rec = wbt.parse_issue("**Device:** X\n**Type:** mouse\n IDs 1111:2222. HID: if0 0006:0020 1/0/0.")
        self.assertEqual(wbt.finalize(rec)["verdict"], "generic-battery")

    def test_open_error_path_is_unparseable(self):
        rec = wbt.parse_issue("IDs 1111:2222. HID: if0 (could not open).")
        self.assertEqual(wbt.finalize(rec)["verdict"], "unparseable")


class CliTests(unittest.TestCase):
    def test_exit_1_when_issue_has_no_query_channel(self):
        rc, out = run_main(["--issue", "examples/in/issue-2386.md", "--json"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["records"][0]["verdict"], "vendor-notify-only")

    def test_exit_0_when_query_channel_present(self):
        rc, out = run_main(["--issue", "examples/in/issue-2382.md", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["schema"], wbt.SCHEMA)

    def test_exit_2_on_missing_file(self):
        rc, _ = run_main(["--issue", "examples/in/does-not-exist.md"])
        self.assertEqual(rc, 2)

    def test_exit_2_on_empty_corpus(self):
        rc, _ = run_main(["--corpus", "examples/in/corpus-sample.jsonl.bak"])
        self.assertEqual(rc, 2)

    def test_corpus_summary_sums_to_inputs(self):
        rc, out = run_main(["--corpus", "examples/in/corpus-sample.jsonl", "--json"])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertEqual(report["inputs"], 12)
        self.assertEqual(sum(report["summary"]["by_verdict"].values()), 12)
        self.assertEqual(len(report["records"]), 12)


if __name__ == "__main__":
    unittest.main()