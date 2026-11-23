import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import prepare_rq2_cases as p


class PrepareTests(unittest.TestCase):
    def get_case(self):
        return {"case_id": "c1", "language": "c", "status": "prepared", "prefix": "\n".join(f"line{i}" for i in range(15)),
                "references": [{"structure": "ID", "text": "x"}],
                "top_candidates": [{"structure": "NUM"}, {"structure": "STR"}, {"structure": "ID"}],
                "conditions": []}

    def test_last10(self):
        self.assertEqual(p.last_n_lines("\n".join(str(i) for i in range(13)), 10).splitlines(), [str(i) for i in range(3, 13)])

    def test_default_c_is_prefix_only_10(self):
        c = p.prepare_case(self.get_case())
        self.assertEqual(len(c["conditions"]), 5)
        self.assertEqual(c["planned_api_requests"], 9)
        self.assertNotIn("line0", c["conditions"][0]["prompt"])
        self.assertIn("line14", c["conditions"][0]["prompt"])
        self.assertEqual(c["conditions"][-1]["condition"], "ideal_guidance")
        self.assertEqual(c["conditions"][-1]["structure"], "ID")

    def test_full_context_available(self):
        c = p.prepare_case(self.get_case(), c_context="full", profile="monir-style")
        self.assertIn("line0", c["conditions"][0]["prompt"])

    def test_reject_multiple_references(self):
        case = self.get_case()
        case["references"].append({"structure": "STR"})
        with self.assertRaises(ValueError):
            p.prepare_case(case)

    def test_no_bogus_fim(self):
        with self.assertRaises(ValueError):
            p.prepare_case(self.get_case(), c_completion="infill")


if __name__ == "__main__":
    unittest.main()
