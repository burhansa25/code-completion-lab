"""Offline regression tests for the released paired-statistics CLI."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_v2.stats import paired_rows, paired_summary, collect_stats, export_stats
from benchmark_v2.__main__ import main


class StatsTests(unittest.TestCase):
    def fixture(self, root):
        p = root / "results/v2/c/grok/study/run123/analysis"
        p.mkdir(parents=True)
        cases = [
            ("c1", "a.c", 20, 40), ("c2", "a.c", 30, 25),
            ("c3", "b.c", 5, 10), ("c4", "b.c", 50, 50),
        ]
        with (p / "rq2_by_case.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["case_id", "source_relative", "status", "metric", "WithoutGuide", "WithTop1Guide"])
            w.writeheader()
            for cid, src, without, guided in cases:
                w.writerow({"case_id": cid, "source_relative": src, "status": "complete",
                            "metric": "sacrebleu_unigram_precision", "WithoutGuide": without,
                            "WithTop1Guide": guided})
        with (p / "rq2_table.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["metric", "condition", "aggregation", "score_pct", "n_cases", "n_files", "prompt_profile"])
            w.writeheader()
            for condition in ["WithoutGuide", "WithTop1Guide"]:
                w.writerow({"metric": "sacrebleu_unigram_precision", "condition": condition,
                            "aggregation": "micro_cursor", "score_pct": "20", "n_cases": "4", "n_files": "2", "prompt_profile": "v2_fragment-v1_last10_prefix"})
        return p

    def test_paired_and_bootstrap_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.fixture(Path(d)) / "rq2_by_case.csv"
            rows = paired_rows(path, "sacrebleu_unigram_precision")
            a = paired_summary(rows, bootstrap=500, seed=2027)
            b = paired_summary(rows, bootstrap=500, seed=2027)
            self.assertEqual(a, b)
            self.assertEqual(a["mean_delta_pp"], 5.0)
            self.assertEqual((a["wins"], a["losses"], a["ties"]), (2,1,1))
            self.assertEqual(a["source_count"], 2)
            self.assertLessEqual(a["ci95_low_pp"], a["mean_delta_pp"])
            self.assertGreaterEqual(a["ci95_high_pp"], a["mean_delta_pp"])
            with self.assertRaises(ValueError):
                paired_summary(rows, bootstrap=1)

    def test_stats_end_to_end_read_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            report = self.fixture(root) / "rq2_by_case.csv"
            before = report.read_bytes()
            rows = collect_stats(root / "results/v2", metric="unigram", bootstrap=200)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cursor_count"], 4)
            self.assertEqual(main(["stats", "--root", str(root/"results/v2"), "--metric", "unigram", "--bootstrap", "200"]),0)
            self.assertEqual(report.read_bytes(), before)
            out = root / "summary.csv"
            export_stats(rows, out)
            with self.assertRaises(FileExistsError):
                export_stats(rows, out)

    def test_reject_duplicate_or_incomplete_cases(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(Path(d)) / "rq2_by_case.csv"
            text = p.read_text()
            header, one, rest = text.split('\n',2)
            p.write_text(text + one + '\n')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                paired_rows(p, "sacrebleu_unigram_precision")
            p.write_text(text.replace('complete', 'incomplete', 1))
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                paired_rows(p, "sacrebleu_unigram_precision")

if __name__ == '__main__':
    unittest.main()
