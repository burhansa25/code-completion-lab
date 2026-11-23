"""Contract tests with fake legacy executables: no HJ binaries or API calls."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import benchmark_entry as be

FAKE_LEGACY = r'''
import argparse, hashlib, json, pathlib, sys
p = argparse.ArgumentParser()
p.add_argument('command', nargs='?')
p.add_argument('--out'); p.add_argument('--output'); p.add_argument('--input')
p.add_argument('--model'); p.add_argument('--cursor'); p.add_argument('--all-cases', action='store_true')
args, extra = p.parse_known_args()
name = pathlib.Path(sys.argv[0]).name
case = {'status':'prepared', 'case_id':'fixture', 'cursor_byte':10, 'source_relative':'x',
        'top_candidates':[{'structure':'a'},{'structure':'b'},{'structure':'c'}],
        'conditions':[{'condition':'without_guidance','prompt':'prefix'},
                      {'condition':'guided_rank_1','prompt':'a'},
                      {'condition':'guided_rank_2','prompt':'b'},
                      {'condition':'guided_rank_3','prompt':'c'}],
        'references':[{'text':'z'}], 'prefix':'abc','language':'smallbasic',
        'provenance':{'database_path':'/tmp/does-not-matter/candidates.json'}}
if name == 'run_hj_c_batch.py':
    if args.command == 'prepare':
        out = pathlib.Path(args.out)
        cases = out/'files'/'file.c.json'/'cases.jsonl'; cases.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(case)+'\n'; cases.write_text(content)
        summary = {'reports':1, 'files_prepared':1,'failures':[], 'requests_per_model':6,
                   'files':[{'source':'file.c', 'cases':'files/file.c.json/cases.jsonl',
                   'cases_sha256':hashlib.sha256(content.encode()).hexdigest(),
                   'counts':{'llm_positions':1}}]}
        (out/'summary.json').write_text(json.dumps(summary))
    elif args.command == 'run':
        out = pathlib.Path(args.out); (out/'full-mode-called').write_text('true')
elif name == 'prepare_hj_benchmark.py':
    out = pathlib.Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(case)+'\n')
elif name == 'map_hj_candidates.py':
    src = pathlib.Path(args.input); out = pathlib.Path(args.output)
    records = [json.loads(line) for line in src.read_text().splitlines()]
    for row in records:
        row['prompt_parent_sha256'] = hashlib.sha256(src.read_bytes()).hexdigest()
    out.write_text(''.join(json.dumps(row)+'\n' for row in records))
elif name.startswith('run_hj_'):
    if args.command == 'run':
        if args.cursor is None and not args.all_cases: sys.exit(12)
        out = pathlib.Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({'count':6,'cursor':args.cursor,'all':args.all_cases})+'\n')
    elif args.command == 'score':
        out = pathlib.Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('case_id,score\nfixture,1\n')
'''


class StarterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        for name in ('run_hj_c_batch.py', 'prepare_hj_benchmark.py', 'map_hj_candidates.py',
                     'hj_benchmark_tools.py', 'run_hj_xai.py', 'run_hj_openai.py',
                     'run_hj_openai_sb_resume.py'):
            (self.repo / name).write_text(FAKE_LEGACY, encoding='utf-8')
        self.patcher = patch.object(be, 'REPO', self.repo)
        self.patcher.start()
        self.env = patch.dict(os.environ, {'XAI_API_KEY':'fake-test-key', 'OPENAI_API_KEY':'fake-test-key'})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.patcher.stop(); self.temp.cleanup()

    def test_c_smoke_schedules_single_cursor_only(self):
        self.assertEqual(be.main('c', ['--model','grok']), 0)
        item = json.loads((self.repo/'results/c-smoke/grok_responses.jsonl').read_text())
        self.assertEqual(item, {'count':6,'cursor':'10','all':False})
        self.assertTrue((self.repo/'results/c-smoke/grok_scores.csv').is_file())
        # rerun is idempotent, score is not recreated
        self.assertEqual(be.main('c', ['--model','grok']), 0)

    def test_smallbasic_smoke_prepares_maps_and_scores(self):
        self.assertEqual(be.main('smallbasic', ['--model','gpt4o']), 0)
        item = json.loads((self.repo/'results/sb-smoke/gpt4o_responses.jsonl').read_text())
        self.assertEqual(item, {'count':6,'cursor':'10','all':False})
        self.assertTrue((self.repo/'results/sb-smoke/cases_mapped.jsonl').is_file())
        self.assertTrue((self.repo/'results/sb-smoke/gpt4o_scores.csv').is_file())

    def test_full_requires_confirm(self):
        be.main('c', ['--stage','prepare'])
        with patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError,'--yes'):
                be.main('c', ['--stage','run','--scope','full'])
        self.assertFalse((self.repo/'results/c-prepared/full-mode-called').exists())
        self.assertEqual(be.main('c', ['--stage','run','--scope','full','--yes']), 0)
        self.assertTrue((self.repo/'results/c-prepared/full-mode-called').exists())

    def test_dry_run_never_calls_generation_or_creates_output(self):
        self.assertEqual(be.main('smallbasic', ['--dry-run']), 0)
        self.assertFalse((self.repo/'results').exists())

    def test_c_detects_corrupted_prepared_cases(self):
        be.main('c', ['--stage','prepare'])
        cases = self.repo/'results/c-prepared/files/file.c.json/cases.jsonl'
        cases.write_text('corrupt', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'modified since preparation'):
            be.main('c', ['--stage','run'])


    def test_smallbasic_full_requires_yes_and_uses_all_cases(self):
        be.main('smallbasic', ['--stage','prepare','--scope','full'])
        with patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, '--yes'):
                be.main('smallbasic', ['--stage','run','--scope','full'])
        self.assertEqual(be.main('smallbasic', ['--stage','run','--scope','full','--yes']), 0)
        item = json.loads((self.repo/'results/sb-full/grok_responses.jsonl').read_text())
        self.assertEqual(item, {'count':6, 'cursor':None, 'all':True})

    def test_existing_legacy_c_preparation_is_reused(self):
        old = self.repo/'results/c-full-infill-v1'
        be.prepare_c(old, self.repo/'PL', dry_run=False)
        self.assertEqual(be.c_batch_path(self.repo/'results', None), old)
        self.assertEqual(be.main('c', ['--model','grok','--stage','run']), 0)
        self.assertTrue((self.repo/'results/c-smoke/grok_scores.csv').exists())
        self.assertFalse((self.repo/'results/c-prepared').exists())

    def test_smallbasic_cached_mapping_detects_stale_eligible(self):
        self.assertEqual(be.main('smallbasic', ['--stage','prepare']), 0)
        eligibility = self.repo/'results/sb-smoke/eligible.jsonl'
        # Two changes which match prepared rows? Test mapped hash against unchanged eligible
        eligibility.write_text(eligibility.read_text() + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            be.main('smallbasic', ['--stage','prepare'])


if __name__ == '__main__':
    unittest.main()
