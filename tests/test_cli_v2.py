"""CLI integration tests with synthetic HJ results. No network or parser calls."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_v2 import cli


class CLITests(unittest.TestCase):
    def c_fixture(self, root):
        prefix = "int main(){\n    return "
        source = root / "hello.c"
        reference = "0;"
        source.write_text(prefix + reference + "\n}", encoding="utf-8")
        case = {"schema_version":1,"language":"c","status":"prepared","case_id":"c1",
                "source_relative":"hello.c","source_path":str(source),
                "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                "cursor_byte":len(prefix.encode()),"prefix":prefix,"candidate_count":3,
                "top_candidates":[{"structure":"NUM","frequency":3},
                                  {"structure":"STR","frequency":2},
                                  {"structure":"ID","frequency":1}],
                "references":[{"structure":"NUM","text":"0;","start_byte":len(prefix.encode()),
                               "end_byte_from_text":len(prefix.encode())+2}]}
        batch=root/"results/c-prepared"; cases=batch/"files/hello/cases.jsonl"
        cases.parent.mkdir(parents=True)
        content=json.dumps(case)+"\n"; cases.write_text(content)
        summ={"reports":1,"files_prepared":1,"failures":[],
              "files":[{"source":"hello.c","cases":"files/hello/cases.jsonl",
                        "cases_sha256":hashlib.sha256(content.encode()).hexdigest(),
                        "counts":{"llm_positions":1}}]}
        (batch/"summary.json").write_text(json.dumps(summ))
        return batch

    def test_prepare_run_dry_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.c_fixture(root)
            with patch.object(cli,"REPO",root):
                out=root/"results/v2"
                self.assertEqual(cli.main("c",["--stage","all","--dry-run","--output-root",str(out)]),0)
                self.assertFalse(out.exists())
                self.assertEqual(cli.main("c",["--stage","prepare","--output-root",str(out)]),0)
                created=list(out.rglob("cases.jsonl"))
                self.assertEqual(len(created),1)
                prep=json.loads(created[0].read_text().splitlines()[0])
                self.assertEqual(prep["planned_api_requests"],9)
                self.assertEqual(prep["completion_mode"],"prefix")
                self.assertIn("ideal_guidance",[r["condition"] for r in prep["conditions"]])
                # Running prepare again must reuse exactly the same frozen output.
                self.assertEqual(cli.main("c",["--stage","prepare","--output-root",str(out)]),0)
                with patch.object(cli,"run_one_group",return_value={"complete":9}) as mocked:
                    with patch.object(cli,"report_group"):
                        self.assertEqual(cli.main("c",["--stage","run","--yes","--output-root",str(out)]),0)
                    self.assertEqual(mocked.call_count,1)

    def test_protection_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.c_fixture(root)
            with patch.object(cli,"REPO",root):
                with self.assertRaises(SystemExit):
                    cli.main("c",["--scope","full","--stage","all"])
                with self.assertRaises(SystemExit):
                    cli.main("c",["--completion","infill"])
                with self.assertRaises(SystemExit):
                    cli.main("c",["--top-k","20","--scope","full","--yes"])


if __name__ == '__main__':
    unittest.main()
