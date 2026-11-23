import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rq2_report as rq


def row(condition, rep, value):
    return {"case_id": "a", "model": "demo", "condition": condition,
            "repetition": str(rep), "sacrebleu_unigram_precision": str(value),
            "sacrebleu_sentence_13a": str(value / 2),
            "sequence_matcher_raw": str(value / 100)}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.cases = {"a": {"case_id": "a", "language": "smallbasic", "prompt_profile": "test",
                            "source_relative": "abc.sb", "references": [{"retrieval_rank": 4}],
                            "cursor_byte": 15, "candidate_count": 9}}
        parts = ([row("without_guidance", i, val) for i, val in enumerate((20, 30, 40), 1)]
                 + [row(f"guided_rank_{i}", 1, val) for i, val in enumerate((10, 60, 50), 1)]
                 + [row("ideal_guidance", i, val) for i, val in enumerate((70, 80, 90), 1)])
        self.scores = {(r["case_id"], r["model"], r["condition"], r["repetition"]): r for r in parts}

    def test_four_arms(self):
        by_case, table = rq.compute(self.cases, self.scores)
        row = next(x for x in by_case if x["metric"] == "sacrebleu_unigram_precision")
        self.assertEqual([row[c] for c in rq.CONDITIONS], [30, 80, 60, 10])
        self.assertEqual(row["status"], "complete")
        self.assertEqual(next(x for x in table if x["condition"] == "WithinTop3Guide" and x["metric"] == "sacrebleu_unigram_precision" and x["aggregation"] == "paired_micro")["score_pct"], 60)

    def test_rank_coverage(self):
        result = rq.retrieval_rows(self.cases)
        self.assertEqual([r["retrieved"] for r in result], [0, 0, 1, 1, 1])

    def test_missing_ideal_is_not_zero(self):
        scores = {k:v for k,v in self.scores.items() if k[2] != "ideal_guidance"}
        by_case, table = rq.compute(self.cases, scores)
        row = next(x for x in by_case if x["metric"] == "sacrebleu_unigram_precision")
        self.assertIsNone(row["WithIdealGuide"])
        paired = [x for x in table if x["aggregation"] == "paired_micro"]
        self.assertTrue(all(x["n_cases"] == 0 for x in paired))

    def test_partial_top3_disallowed(self):
        scores = {k:v for k,v in self.scores.items() if k[2] != "guided_rank_2"}
        rows, _ = rq.compute(self.cases, scores)
        row = next(x for x in rows if x["metric"] == "sacrebleu_unigram_precision")
        self.assertIsNone(row["WithinTop3Guide"])
        self.assertEqual(row["WithTop1Guide"], 10)

    def test_end_to_end_cli(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "cases.jsonl").write_text(json.dumps(self.cases["a"]) + "\n")
            with (base / "scores.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(next(iter(self.scores.values()))))
                writer.writeheader(); writer.writerows(self.scores.values())
            rq.main(["--cases", str(base / "cases.jsonl"), "--scores", str(base / "scores.csv"), "--out", str(base / "report")])
            self.assertTrue((base / "report/rq2_by_case.csv").exists())
            self.assertTrue((base / "report/retrieval_coverage.csv").exists())

    def test_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"dup.csv"
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(next(iter(self.scores.values()))))
                writer.writeheader(); writer.writerow(next(iter(self.scores.values()))); writer.writerow(next(iter(self.scores.values())))
            with self.assertRaisesRegex(ValueError, "Duplicate score task"):
                rq.read_scores([path])

    def test_sequence_ratio_normalized(self):
        self.assertEqual(rq.parsed_metric({"sequence_matcher_raw":"0.45"}, "sequence_matcher_raw"), 45)

    def test_unknown_condition_rejected(self):
        with self.assertRaises(ValueError):
            rq.normalize_condition("unexpected")


if __name__ == "__main__":
    unittest.main()
