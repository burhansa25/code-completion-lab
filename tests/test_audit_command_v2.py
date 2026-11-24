"""Regression tests for read-only RQ2 normalized prediction audit."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_v2.audit import normalize_prediction, audit_run
from benchmark_v2.__main__ import main


class NormalizationTests(unittest.TestCase):
    def test_complete_fence(self):
        self.assertEqual(normalize_prediction('```c\nint\n```'), ('int', 'complete_fence'))

    def test_trailing_unmatched_fence(self):
        self.assertEqual(normalize_prediction('int\n\n```'), ('int', 'unmatched_closing_fence_tentative'))

    def test_raw_token_unchanged(self):
        self.assertEqual(normalize_prediction('int'), ('int', 'unchanged'))

    def test_never_reference_truncates_program(self):
        program = '```c\nint main() { return 0; }\n```'
        result, cat = normalize_prediction(program)
        self.assertEqual(cat, 'complete_fence')
        self.assertEqual(result, 'int main() { return 0; }')
        self.assertNotEqual(result, 'int')

    def test_preserve_interior_backticks_and_no_fence_stripping(self):
        source = 'int x;\nconst char *s = "```";'
        self.assertEqual(normalize_prediction(source)[0], source)

    def test_incomplete_opening_kept(self):
        self.assertEqual(normalize_prediction('```c\nint')[0], '```c\nint')


class AuditCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'v2'
        self.directory = self.root / 'c/gpt4o/smoke/run1234/files/fileA'
        self.directory.mkdir(parents=True)
        self.case = {
            'case_id': 'id1', 'cursor_byte': 20, 'source_relative': 'hello.c',
            'prefix': '#include <stdio.h>\n\n',
            'references': [{'text': 'int', 'structure': 'primitive_type',
                            'validation': 'matches_source_slice_end_completeness_unverified'}],
            'conditions': [
                {'condition': 'without_guidance', 'structure': None, 'prompt': 'Complete'},
                {'condition': 'guided_rank_2', 'structure': 'primitive_type', 'prompt': 'Primitive'},
                {'condition': 'ideal_guidance', 'structure': 'primitive_type', 'prompt': 'Ideal'}],
        }
        self.cases_path = self.directory / 'cases.jsonl'
        self.cases_path.write_text(json.dumps(self.case) + '\n', encoding='utf-8')
        self.score_path = self.directory / 'scores.csv'
        with self.score_path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['case_id','cursor_byte','condition',
                                                   'repetition','model','prediction'])
            writer.writeheader()
            for cond, rep, pred in [
                ('guided_rank_2', 1, 'int\n\n```'),
                ('ideal_guidance', 1, '```c\nint\n```'),
                ('ideal_guidance', 2, 'int'),
                ('ideal_guidance', 3, '```c\nint main() {}\n```'),
                ('without_guidance', 1, '```c\nint main() {}\n```'),
            ]:
                writer.writerow(dict(case_id='id1', cursor_byte=20, condition=cond,
                                     repetition=rep, model='gpt-4o', prediction=pred))

    def test_one_command_audit_and_immutable_files(self):
        cases_before = self.cases_path.read_bytes()
        scores_before = self.score_path.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['audit', '--root', str(self.root), '--language', 'c', '--model', 'gpt'])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn('reference=\'int\'', text)
        self.assertIn('guided_rank_2', text)
        self.assertIn('normalized_exact=1', text)
        self.assertIn('unmatched_closing_fence_tentative=1', text)
        self.assertIn('5', text)
        self.assertEqual(self.cases_path.read_bytes(), cases_before)
        self.assertEqual(self.score_path.read_bytes(), scores_before)

    def test_show_predictions_and_prompts(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['audit', '--root', str(self.root), '--language', 'c',
                         '--model', 'gpt4o', '--show-predictions', '--show-prompts'])
        self.assertEqual(code, 0)
        self.assertIn('RAW:', out.getvalue())
        self.assertIn('PROMPT', out.getvalue())

    def test_limit_cases_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit_run(self.root, 'c', 'gpt', limit_cases=0)
        self.assertEqual(code, 0)
        self.assertNotIn('CASE id1', out.getvalue())

    def test_explicit_bad_run_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            audit_run(self.root, 'c', 'gpt', run_id='wrong')

    def test_missing_results_nonzero(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(['audit', '--root', str(self.root), '--language', 'smallbasic', '--model', 'grok'])
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
