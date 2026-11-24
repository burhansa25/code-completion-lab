import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prepare_hj_benchmark as preparer

class SkipReportsTests(unittest.TestCase):
    def fixture(self, root, with_report=True):
        base = Path(root)
        src = base/'tree-sitter-smallbasic'/'SB_Sample'
        reports = base/'tree-sitter'/'reports'/'smallbasic'
        resources = base/'code-completion-extension'/'resources'/'smallbasic'
        for p in (src, reports, resources): p.mkdir(parents=True, exist_ok=True)
        (src/'01_HelloWorld.sb').write_text('TextWindow.WriteLine("Hello World")')
        (src/'Tetris.sb').write_text('TextWindow.WriteLine("Tetris")')
        if with_report:
            (reports/'01_HelloWorld.sb.json').write_text(json.dumps({'0':[{'candidate':'ID . ID ( Exprs )', 'candidate_text':'TextWindow.WriteLine("Hello World")','state_id':1}]}))
        (base/'tree-sitter'/'TreeSitterCutFile').write_text('mock binary')
        (base/'tree-sitter-smallbasic'/'smallbasic.so').write_text('mock grammar')
        (resources/'candidates.json').write_text(json.dumps({'1':[{'key':'ID . ID ( Exprs )','value':2}]}))
        return base

    def call(self, base, args):
        with patch.object(sys,'argv',['prepare_hj_benchmark.py','--base',str(base),'--output',str(base/'out.jsonl'),*args]), \
            patch.object(preparer,'retrieve',return_value=([1],[{'structure':'ID . ID ( Exprs )','frequency':2}])), \
            contextlib.redirect_stdout(io.StringIO()) as out:
            rc=preparer.main()
            return rc,out.getvalue()

    def test_filter_retains_original_source_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as td:
            base=self.fixture(td)
            rc, output=self.call(base,['--all','--skip-missing-reports'])
            self.assertEqual(rc,0)
            self.assertIn('Selected 1 source/report pairs; skipped 1',output)
            self.assertIn('Tetris.sb',output)
            rows=[json.loads(x) for x in (base/'out.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['source_path'],str((base/'tree-sitter-smallbasic'/'SB_Sample'/'01_HelloWorld.sb').resolve()))
            self.assertEqual(rows[0]['status'],'prepared')

    def test_missing_without_flag_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            base=self.fixture(td)
            with self.assertRaises(FileNotFoundError):self.call(base,['--all'])

    def test_requires_all(self):
        with tempfile.TemporaryDirectory() as td:
            base=self.fixture(td)
            with self.assertRaisesRegex(ValueError,'requires --all'):self.call(base,['--skip-missing-reports'])

    def test_no_pairs_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            base=self.fixture(td,False)
            with self.assertRaisesRegex(ValueError,'No source files have matching'):self.call(base,['--all','--skip-missing-reports'])

if __name__ == '__main__':unittest.main()
