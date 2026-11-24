"""Test the read-only RQ2 summary command with synthetic CSV fixtures."""
import contextlib
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_v2.__main__ import main


class ResultsCommandTests(unittest.TestCase):
    def make_report(self, root, run="abcd123", value=42.123, scope="smoke"):
        target = Path(root) / "smallbasic/gpt4o" / scope / run / "analysis/rq2_table.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["language", "model", "prompt_profile", "metric", "condition", "aggregation", "score_pct", "n_cases", "n_files"])
            writer.writeheader()
            for metric in ("sacrebleu_unigram_precision", "sacrebleu_sentence_13a", "sequence_matcher_raw"):
                for condition in ("WithoutGuide", "WithIdealGuide", "WithinTop3Guide", "WithTop1Guide"):
                    for aggregation in ("micro_cursor", "macro_file", "paired_micro"):
                        writer.writerow(dict(language="smallbasic", model="gpt-4o", prompt_profile="v2_lab_full_prefix", metric=metric,
                                             condition=condition, aggregation=aggregation, score_pct=value, n_cases=1, n_files=1))
        return target

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["results", *args])
        return code, output.getvalue()

    def test_three_metric_blocks_and_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_report(tmp)
            before = path.read_bytes()
            code, output = self.invoke("--root", tmp)
            self.assertEqual(code, 0)
            self.assertIn("SacreBLEU 1-gram", output)
            self.assertIn("Sentence BLEU", output)
            self.assertIn("SequenceMatcher", output)
            self.assertIn("42.12", output)
            self.assertEqual(path.read_bytes(), before)

    def test_single_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_report(tmp)
            code, output = self.invoke("--root", tmp, "--metric", "unigram")
            self.assertEqual(code, 0)
            self.assertNotIn("Sentence BLEU", output)

    def test_missing_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.invoke("--root", tmp)
            self.assertEqual(code, 1)
            self.assertIn("No RQ2 reports", output)

    def test_latest_and_all_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_report(tmp, "old", 11)
            newer = self.make_report(tmp, "new", 77)
            import os
            os.utime(path, (10, 10))
            os.utime(newer, (20, 20))
            _, output = self.invoke("--root", tmp, "--metric", "unigram")
            self.assertIn("new", output)
            self.assertNotIn("  old\n", output)
            self.assertIn("older run(s) hidden", output)
            _, output = self.invoke("--root", tmp, "--metric", "unigram", "--all-runs")
            self.assertIn("  old\n", output)
            self.assertIn("  new\n", output)

    def test_scope_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_report(tmp, "smoke_run", scope="smoke")
            self.make_report(tmp, "full_run", scope="full")
            _, output = self.invoke("--root", tmp, "--metric", "sequence", "--scope", "full")
            self.assertIn("full_run", output)
            self.assertNotIn("smoke_run", output)

    def test_malformed_csv_gracefully_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_report(tmp)
            path.write_text("bad,data\n1,2\n", encoding="utf-8")
            code, output = self.invoke("--root", tmp)
            self.assertEqual(code, 1)
            self.assertIn("WARNING", output)


if __name__ == "__main__":
    unittest.main()
