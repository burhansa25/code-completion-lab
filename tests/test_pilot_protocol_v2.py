"""Frozen fragment protocol and cohort; no API, no corpus modifications."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_v2 import cli, cases
from benchmark_v2.pilot import stratified_pilot


class PilotProtocolTests(unittest.TestCase):
    def setup_fixture(self, root, *, n=8, suffix="c"):
        prepared = root / "prepared.jsonl"
        items = []
        for i in range(n):
            content = f"int item{i} = " + f"{i};\n"
            src = root / f"file{i}.{suffix}"
            src.write_text(content)
            cursor = content.index("= ") + 2
            text = f"{i};"
            self.assertEqual(content[cursor:cursor+len(text)], text)
            structure = "NUM" if i % 2 == 0 else "NUM ;"
            items.append({
                "language": "c", "status": "prepared", "source_relative": src.name,
                "case_id": f"c-{i}", "source_path": str(src),
                "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "prefix": content[:cursor], "cursor_byte": cursor, "candidate_count": 3,
                "top_candidates": [
                    {"structure": "NUM", "frequency": 5},
                    {"structure": "ID", "frequency": 4},
                    {"structure": "STR", "frequency": 1},
                ], "references": [{"structure": structure, "text": text,
                                  "start_byte": cursor, "end_byte_from_text": cursor + len(text)}]
            })
        prepared.write_text("".join(json.dumps(x)+"\n" for x in items))
        return [(f"file{i}.{suffix}", self._onecase(root, items[i])) for i in range(n)]

    @staticmethod
    def _onecase(root, item):
        target = root / (item["case_id"] + ".jsonl")
        target.write_text(json.dumps(item)+"\n")
        return target

    def test_fragment_prompt_does_not_see_reference(self):
        prefix = "#include <stdio.h>\n\n"
        blank = cases.build_prompt({"language": "c"}, prefix, None, None,
                                   profile="fragment-v1", completion="prefix")
        guided = cases.build_prompt({"language": "c"}, prefix, None, "primitive_type",
                                    profile="fragment-v1", completion="prefix")
        self.assertIn("Markdown", blank)
        self.assertIn("compact continuation fragment", blank)
        self.assertIn("primitive_type", guided)
        self.assertNotIn("primitive_type", blank)
        self.assertIn(blank.split("\nPREFIX START")[0], guided)

    def test_seed_reproducible_and_different(self):
        with tempfile.TemporaryDirectory() as d:
            sources = self.setup_fixture(Path(d), n=15)
            a = stratified_pilot("c", sources, size=6, seed=13)
            b = stratified_pilot("c", list(reversed(sources)), size=6, seed=13)
            ids = lambda groups: {c["case_id"] for _, cases in groups for c in cases}
            self.assertEqual(ids(a), ids(b))
            self.assertEqual(len(ids(a)), 6)
            c = stratified_pilot("c", sources, size=6, seed=777)
            self.assertNotEqual(ids(a), ids(c))

    def test_quoted_and_comment_spans_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sources = self.setup_fixture(root, n=5)
            item = json.loads(sources[0][1].read_text())
            text = 'printf("hel'
            src = root / 'comment.c'
            content = '// abcde\n' + text + 'lo");\n'
            src.write_text(content)
            cursor = len('// abcde\n'.encode())
            item.update(case_id='other', source_relative='comment.c', source_path=str(src),
                        source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
                        cursor_byte=cursor, prefix=content[:cursor],
                        references=[{'text': text, 'structure': 'code', 'start_byte': cursor}])
            p = root / 'commentcase.jsonl'; p.write_text(json.dumps(item)+'\n')
            chosen = stratified_pilot('c', sources+[('comment.c',p)],size=5)
            self.assertNotIn('comment.c',[name for name,_ in chosen])

    def test_smallbasic_pilot_uses_full_cohort_and_is_model_independent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            input_file = root / "results/sb-full/cases_mapped.jsonl"
            input_file.parent.mkdir(parents=True)
            legacy = root / "results/sb-smoke/cases_mapped.jsonl"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('[]\n')
            source = root / "hello.sb"
            source.write_text('TextWindow.WriteLine("hello")')
            prefix = "TextWindow"
            item = {"case_id": "smallbasic-1", "language": "smallbasic",
                    "status": "prepared", "source_path": str(source),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "source_relative": "hello.sb", "cursor_byte": len(prefix),
                    "prefix": prefix, "candidate_count": 3,
                    "top_candidates": [{"structure": "= Expr", "frequency": 3},
                                       {"structure": ". ID ( Exprs )", "frequency": 2},
                                       {"structure": ". ID = Expr", "frequency": 1}],
                    "references": [{"structure": ". ID ( Exprs )",
                                    "text": '.WriteLine("hello")',
                                    "start_byte": len(prefix)}]}
            input_file.write_text(json.dumps(item)+'\n')
            with patch.object(cli, 'REPO', root):
                sources = cli.discover_sources('smallbasic','pilot',root/'not_needed',None)
                self.assertEqual(sources, [('smallbasic',input_file)])
                c = stratified_pilot('smallbasic',sources,size=1,seed=5)
                self.assertEqual(c[0][0],'hello.sb')
                self.assertEqual(c[0][1][0]['case_id'],'smallbasic-1')
                self.assertEqual(len(c[0][1]),1)

    def test_default_boundary_input_is_full_when_available(self):
        from benchmark_v2.boundaries import discover_inputs
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            full = root / 'sb-full/prepared.jsonl'; full.parent.mkdir(parents=True)
            smoke = root / 'sb-smoke/prepared.jsonl'; smoke.parent.mkdir(parents=True)
            full.write_text('{}\n'); smoke.write_text('{}\n')
            self.assertEqual(discover_inputs('smallbasic',root=root),[full])
            full.unlink()
            self.assertEqual(discover_inputs('smallbasic',root=root),[smoke])

    def test_reject_invalid_size_and_insufficient(self):
        with tempfile.TemporaryDirectory() as d:
            sources = self.setup_fixture(Path(d),n=3)
            with self.assertRaises(ValueError): stratified_pilot('c',sources,size=0)
            with self.assertRaisesRegex(ValueError,'Only 3'):
                stratified_pilot('c',sources,size=4)

    def test_cli_pilot_prepares_without_api(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            sources=self.setup_fixture(root,n=8)
            with patch.object(cli,'REPO',root), patch.object(cli,'discover_sources',return_value=sources):
                out=root/'results/v2'
                self.assertEqual(cli.main('c',[
                    '--scope','pilot','--pilot-size','3','--pilot-seed','44',
                    '--stage','prepare','--model','grok','--output-root',str(out)]),0)
                rows=[json.loads(line) for p in out.rglob('cases.jsonl') for line in p.read_text().splitlines()]
                self.assertEqual(len(rows),3)
                self.assertEqual(sum(x['planned_api_requests'] for x in rows),27)
                cohorts = list(out.rglob('cohort.json'))
                self.assertEqual(len(cohorts),1)
                cohort = json.loads(cohorts[0].read_text())
                self.assertEqual(len(cohort['selected']),3)
                self.assertEqual(cohort['pilot_seed'],44)
                # Two providers on same language/seed must use the same cohort.
                self.assertEqual(cli.main('c',['--scope','pilot','--pilot-size','3',
                    '--pilot-seed','44','--stage','prepare','--model','gpt',
                    '--output-root',str(out)]),0)
                candidates = [json.loads(x.read_text()) for x in out.rglob('cohort.json')]
                self.assertEqual(len(candidates),2)
                self.assertEqual({x['selection_sha256'] for x in candidates},
                                 {cohort['selection_sha256']})
                self.assertTrue(all(x['prompt_profile'].startswith('v2_fragment-v1') for x in rows))
                for x in rows:
                    no_guide=next(c for c in x['conditions'] if c['condition']=='without_guidance')
                    self.assertNotIn('TARGET GRAMMAR STRUCTURE',no_guide['prompt'])
                with self.assertRaises(SystemExit):
                    cli.main('c',['--scope','pilot','--pilot-size','3',
                                  '--stage','run','--output-root',str(out)])
                with self.assertRaises(SystemExit):
                    cli.main('c',['--scope','pilot','--pilot-size','3','--pilot-seed','44',
                                  '--stage','run','--yes','--max-api-requests','10',
                                  '--output-root',str(out)])


if __name__ == '__main__':
    unittest.main()
