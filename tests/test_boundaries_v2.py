"""All synthetic: no HJ parser, live filesystem outside tempdir, or API calls."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_v2.boundaries import (inspect_case, text_kind, discover_inputs,
                                     audit_prepared, _reference_in_report,
                                     c_lexical_context_at)
from benchmark_v2.__main__ import main


def sha(b):
    return hashlib.sha256(b).hexdigest()


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.prefix = '#include <stdio.h>\n\n'
        self.source = self.base / 'hello_world.c'
        self.source.write_text(self.prefix + 'int main(){return 0;}\n', encoding='utf-8')
        self.ref_path = self.base / 'hello_world.json'
        self.offset = len(self.prefix.encode('utf-8'))
        self.ref_json = {str(self.offset): [{'candidate_text': 'int', 'candidate': 'primitive_type', 'state_id': 123}]}
        self.ref_path.write_text(json.dumps(self.ref_json), encoding='utf-8')
        self.ref = {'structure': 'primitive_type', 'text': 'int', 'start_byte': self.offset,
                    'end_byte_from_text': self.offset + 3, 'collection_state_id': 123,
                    'retrieval_rank': 2}
        self.case = {'case_id': 'c1', 'language': 'c', 'status': 'prepared',
                     'source_relative': 'hello_world.c', 'source_path': str(self.source),
                     'source_sha256': sha(self.source.read_bytes()),
                     'reference_path': str(self.ref_path),
                     'reference_sha256': sha(self.ref_path.read_bytes()),
                     'cursor_byte': self.offset, 'prefix': self.prefix,
                     'references': [dict(self.ref)],
                     'top_candidates': [{'structure': 'static'}, {'structure': 'primitive_type'},
                                        {'structure': 'preproc_include'}]}
        self.prepared_dir = self.base/'results/c-prepared/files/hello_world'
        self.prepared_dir.mkdir(parents=True)
        self.prepared = self.prepared_dir/'prepared.jsonl'
        self.prepared.write_text(json.dumps(self.case)+'\n', encoding='utf-8')

    def audit_row(self, case=None):
        return next(inspect_case(case or self.case, {}, {}))

    def test_int_matches_original_HJ_source_but_boundary_not_confirmed(self):
        row = self.audit_row()
        self.assertEqual(row['slice_verified'], 'yes')
        self.assertEqual(row['report_verified'], 'yes')
        self.assertEqual(row['text_kind'], 'single_identifier_or_keyword')
        self.assertEqual(row['after_span'], 'whitespace')
        self.assertTrue(row['source_suffix_preview'].startswith(' main()'))
        self.assertEqual(row['rank_check'], 'rank_matches_top_candidates')
        self.assertEqual(row['issues'], '')
        self.assertEqual(row['boundary_confirmed'], 'no')
        self.assertIn('grammar_boundary_requires_manual_verification', row['notes'])

    def test_reference_hash_drift_detected(self):
        modified = dict(self.case, reference_sha256='0'*64)
        row = self.audit_row(modified)
        self.assertIn('reference_report_hash_mismatch', row['issues'])

    def test_source_hash_drift_detected(self):
        modified = dict(self.case, source_sha256='0'*64)
        row = self.audit_row(modified)
        self.assertIn('source_hash_mismatch', row['issues'])

    def test_source_slice_mismatch_detected(self):
        mod = dict(self.case, references=[dict(self.ref, text='fluff', end_byte_from_text=self.offset+5)])
        row = self.audit_row(mod)
        self.assertIn('reference_source_slice_mismatch', row['issues'])
        self.assertIn('reference_not_in_original_HJ_report', row['issues'])

    def test_reference_end_inside_identifier(self):
        mod = dict(self.case, references=[dict(self.ref, text='in', end_byte_from_text=self.offset+2)])
        row = self.audit_row(mod)
        self.assertIn('reference_ends_inside_identifier', row['issues'])

    def custom_fragment(self, prefix, reference, suffix, language="c"):
        """Build a fully hash-consistent case for a lexical context example."""
        source = self.base / "fragment.c"
        source.write_text(prefix + reference + suffix, encoding="utf-8")
        offset = len(prefix.encode("utf-8"))
        end = offset + len(reference.encode("utf-8"))
        report_path = self.base / "fragment.json"
        report_path.write_text(json.dumps({str(offset): [
            {"candidate_text": reference, "candidate": "fragment", "state_id": 123}
        ]}), encoding="utf-8")
        return self.audit_row(dict(self.case, language=language,
            source_path=str(source), source_sha256=sha(source.read_bytes()),
            reference_path=str(report_path), reference_sha256=sha(report_path.read_bytes()),
            cursor_byte=offset, prefix=prefix,
            references=[{"structure":"fragment", "text":reference,
                         "start_byte":offset, "end_byte_from_text":end,
                         "collection_state_id":123,"retrieval_rank":2}],
            top_candidates=[{"structure":"static"},{"structure":"fragment"},{"structure":"other"}]))

    def test_line_comment_word_is_not_c_identifier_issue(self):
        row = self.custom_fragment("// If the s ", "str", "ing contains more characters\n")
        self.assertEqual(row["after_span"], "inside_comment")
        self.assertNotIn("reference_ends_inside_identifier", row["issues"])
        self.assertIn("lexical_word_continues_in_comment", row["notes"])
        self.assertEqual(row["slice_verified"], "yes")
        self.assertEqual(row["report_verified"], "yes")
        self.assertEqual(row["boundary_confirmed"], "no")

    def test_block_comment_word_is_not_c_identifier_issue(self):
        row = self.custom_fragment("/* Par", "t", "ial description */ int main(void);")
        self.assertEqual(row["after_span"], "inside_comment")
        self.assertFalse(row["issues"])

    def test_string_escape_followed_by_letters_is_not_identifier(self):
        row = self.custom_fragment('printf("Fahr', r"\t", 'Celsius\\n");')
        self.assertEqual(row["after_span"], "inside_quoted_literal")
        self.assertIn("lexical_word_continues_in_quoted_literal", row["notes"])
        self.assertFalse(row["issues"])

    def test_string_with_comment_markers_remains_string(self):
        row = self.custom_fragment('printf("// Par', 't', 'ial /* still string */");')
        self.assertEqual(row["after_span"], "inside_quoted_literal")
        self.assertFalse(row["issues"])

    def test_real_split_identifier_is_still_flagged(self):
        row = self.custom_fragment("int ", "pr", "int_value = 1;")
        self.assertEqual(row["after_span"], "inside_identifier")
        self.assertIn("reference_ends_inside_identifier", row["issues"])

    def test_comment_end_then_real_identifier_is_flagged(self):
        row = self.custom_fragment("/* comment */ int ", "pr", "int_value = 1;")
        self.assertEqual(row["after_span"], "inside_identifier")
        self.assertIn("reference_ends_inside_identifier", row["issues"])

    def test_lexical_context_scanner_respects_escaped_quotes_and_newlines(self):
        text = (b'printf("escaped ' + bytes((92, 34)) + b'quote' + bytes((92, 34))
                + b' and // marker");\n// comment\nint value;')
        self.assertEqual(c_lexical_context_at(text, text.index(b'// marker') + 5), "string_literal")
        self.assertEqual(c_lexical_context_at(text, text.index(b'// comment') + 6), "line_comment")
        self.assertEqual(c_lexical_context_at(text, len(text)), "code")

    def test_lexical_context_scanner_rejects_invalid_offsets(self):
        with self.assertRaises(ValueError):
            c_lexical_context_at(b"abc", 4)

    def test_end_byte_mismatch_detected(self):
        mod = dict(self.case, references=[dict(self.ref, end_byte_from_text=self.offset+7)])
        row = self.audit_row(mod)
        self.assertIn('end_byte_mismatch', row['issues'])

    def test_prefix_mismatch_detected(self):
        row = self.audit_row(dict(self.case, prefix='wrong'))
        self.assertIn('prefix_mismatch', row['issues'])

    def test_retrieval_rank_inconsistent(self):
        mod = dict(self.case, references=[dict(self.ref, retrieval_rank=1)])
        row = self.audit_row(mod)
        self.assertIn('rank_conflicts_top_candidates', row['issues'])

    def test_original_report_missing(self):
        mod = dict(self.case, reference_path=str(self.base/'not-found.json'))
        row = self.audit_row(mod)
        self.assertIn('reference_report_unavailable', row['issues'])

    def test_multiple_references_marked_for_review(self):
        mod = dict(self.case, references=[dict(self.ref), dict(self.ref)])
        rows = list(inspect_case(mod, {}, {}))
        self.assertEqual(len(rows), 2)
        self.assertIn('reference_count_not_one', rows[0]['notes'])

    def test_no_references_preserved_in_audit(self):
        mod = dict(self.case, references=[])
        row = self.audit_row(mod)
        self.assertEqual(row['text_kind'], 'no_reference')
        self.assertIn('no_reference', row['notes'])

    def test_wrong_state_id_not_verified(self):
        mod = dict(self.case, references=[dict(self.ref, collection_state_id=999)])
        row = self.audit_row(mod)
        self.assertIn('reference_not_in_original_HJ_report', row['issues'])

    def test_utf8_cursor_splits_character_detected(self):
        src = self.base/'sb.sb'
        src.write_text('π = 1\n', encoding='utf-8')
        raw = src.read_bytes()
        report_path = self.base/'sb.json'
        report_path.write_text(json.dumps({'1':[{'candidate_text': 'x', 'candidate': 'ID'}]}))
        case = dict(self.case, language='smallbasic', source_path=str(src), source_sha256=sha(raw),
                    cursor_byte=1, prefix='', reference_path=str(report_path),
                    reference_sha256=sha(report_path.read_bytes()), references=[])
        row = self.audit_row(case)
        self.assertIn('cursor_splits_utf8', row['issues'])

    def test_text_kinds_not_syntax_validation(self):
        self.assertEqual(text_kind('int'), 'single_identifier_or_keyword')
        self.assertEqual(text_kind(';'), 'punctuation_fragment')
        self.assertEqual(text_kind('int main()'), 'compound_fragment')
        self.assertEqual(text_kind('int\nmain()'), 'multiline_fragment')

    def test_discovery_c_and_export_is_non_overwriting(self):
        self.assertEqual(discover_inputs('c', root=self.base/'results'), [self.prepared])
        output = self.base/'audit/c_boundaries.csv'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(['boundaries', '--language','c', '--root',str(self.base/'results'),
                                   '--export',str(output)]), 0)
        self.assertIn('source slices matched=1', stdout.getvalue())
        self.assertIn('grammar', stdout.getvalue().lower())
        self.assertEqual(list(csv.DictReader(output.open(encoding='utf-8', newline='')))[0]['reference_text'], 'int')
        with self.assertRaises(FileExistsError):
            audit_prepared('c', root=self.base/'results', export=output)

    def test_read_only_and_other_language_discovery(self):
        smallbasic_dir = self.base/'results/sb-smoke'
        smallbasic_dir.mkdir(parents=True)
        sb_file = smallbasic_dir/'prepared.jsonl'
        sb_file.write_text(json.dumps(dict(self.case, language='smallbasic'))+'\n')
        before = {p: p.read_bytes() for p in (self.source,self.ref_path,self.prepared,sb_file)}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['boundaries','--language','smallbasic', '--root',str(self.base/'results')]),0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_issue_exit_status_one(self):
        invalid = dict(self.case, source_sha256='0'*64)
        self.prepared.write_text(json.dumps(invalid)+'\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audit_prepared('c', root=self.base/'results', limit=0), 1)

    def test_all_language_auto_discovery_and_status(self):
        sb_dir = self.base / 'results/sb-smoke'
        sb_dir.mkdir(parents=True)
        (sb_dir/'prepared.jsonl').write_text(json.dumps(dict(self.case, language='smallbasic'))+'\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['boundaries','--language','all','--root',str(self.base/'results'),'--limit','0'])
        self.assertEqual(code, 0)
        self.assertIn('Top-3 RQ2 eligibility', out.getvalue())
        self.assertIn('HJ REFERENCE BOUNDARY AUDIT | c', out.getvalue())
        self.assertIn('HJ REFERENCE BOUNDARY AUDIT | smallbasic', out.getvalue())

    def test_audit_all_models_language_reuses_existing_runner(self):
        from unittest.mock import patch
        with patch('benchmark_v2.audit.audit_run', return_value=0) as mocked:
            self.assertEqual(main(['audit','--language','all','--model','all']),0)
            self.assertEqual(mocked.call_count, 4)

    def test_override_specific_file(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['boundaries','--language','c','--input',str(self.prepared)]),0)


if __name__ == '__main__':
    unittest.main()
