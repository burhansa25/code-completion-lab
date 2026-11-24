#!/usr/bin/env python3
"""Evaluate saved HJ/Monir pilot responses without LLM calls.

Explicit task: replace exactly [cursor_byte, reference_end_byte) in the original
source with the complete raw model response. Do not truncate, strip, or repair
the response before parsing. Reference spans/suffixes are evaluation-only data.

Syntax success means no Tree-sitter error/missing nodes in the resulting full
source. This is not compilation, execution, semantic or functional correctness.
Exact structure matching checks a leaf-and-following-siblings sequence over the
entire response's non-whitespace span. Public tree nodes can differ from the
collection internals; original-reference calibration gates interpretation.
Non-match does not establish that every equivalent grammar derivation is invalid.
"""

import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(data):return hashlib.sha256(data).hexdigest()


def parse_span(validator,grammar,symbol,source,start,end,target,work,timeout):
    path=Path(work)/'completed.sb'
    path.write_bytes(source)
    result=subprocess.run([str(validator),str(grammar),symbol,str(path),
                           str(start),str(end),target],cwd=work,
                          capture_output=True,text=True,errors='replace',timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Validator exit {result.returncode}: {result.stderr[-1500:]}')
    try:return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError('Validator output bukan JSON: '+result.stdout[-1000:]) from None


def write_csv(path,rows):
    if not rows:return
    with path.open('x',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def evaluate(args):
    for path in [args.cases,args.responses,args.validator]:
        if not path.expanduser().is_file():raise FileNotFoundError(path)
    if args.output_dir.exists():
        raise FileExistsError('Output directory sudah ada; pilih nama baru.')
    cases=load_jsonl(args.cases)
    by_id={c['case_id']:c for c in cases}
    if len(by_id)!=len(cases):raise ValueError('case_id duplikat.')
    records=load_jsonl(args.responses)
    manifest=records[0]
    if manifest.get('record_type')!='manifest':raise ValueError('Manifest tidak ditemukan.')
    if manifest['input_sha256']!=sha(args.cases.read_bytes()):
        raise ValueError('Hash input berbeda dari manifest generasi.')
    expected=set(manifest['task_ids'])
    if len(expected)!=len(manifest['task_ids']):raise ValueError('Task duplikat dalam manifest.')
    successful={r['task_id']:r for r in records[1:] if r.get('status')=='ok'}
    if set(successful)!=expected:
        raise ValueError('Generasi belum lengkap atau terdapat task di luar manifest.')
    scores={}
    if args.scores:
        with args.scores.open(encoding='utf-8',newline='') as handle:
            score_rows=list(csv.DictReader(handle))
        scores={(r['case_id'],r['condition'],int(r['repetition'])):r for r in score_rows}
        if len(scores)!=len(score_rows):raise ValueError('Skor CSV duplikat.')
    validator=args.validator.expanduser().resolve()
    cache={};calibration={};rows=[];source_metadata={};grammar_metadata={}
    with tempfile.TemporaryDirectory(prefix='hj_offline_eval_') as work:
        for task_id in manifest['task_ids']:
            g=successful[task_id]
            c=by_id[g['case_id']]
            if c.get('language')!='smallbasic':
                raise ValueError('Versi ini divalidasi untuk Small Basic saja.')
            if len(c['references'])!=1:
                raise ValueError('Kasus memiliki beberapa referensi; tentukan satu rentang tetap sebelum evaluasi.')
            condition=next(x for x in c['conditions'] if x['condition']==g['condition'])
            if (g['payload']['messages']!=[{'role':'user','content':condition['prompt']}]
                    or g['references']!=c['references']
                    or g['structure']!=condition.get('structure')
                    or g['cursor_byte']!=c['cursor_byte']):
                raise ValueError('Prompt/referensi/kursor tidak cocok dengan input.')
            identity={k:g[k] for k in ['case_id','condition','repetition','payload']}
            if sha(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode())!=task_id:
                raise ValueError('task_id tidak cocok dengan request.')
            content=g['content']
            if content!=(g['response']['choices'][0]['message'].get('content') or ''):
                raise ValueError('Respons tersimpan berbeda dari respons provider.')
            ref=c['references'][0];cursor=c['cursor_byte'];ref_end=ref['end_byte_from_text']
            source_path=(args.source or Path(c['source_path'])).expanduser().resolve()
            if source_path not in cache:cache[source_path]=source_path.read_bytes()
            original=cache[source_path]
            if sha(original)!=c['source_sha256']:
                raise ValueError('Source berubah dari saat persiapan: '+str(source_path))
            if original[:cursor].decode('utf-8')!=c['prefix']:
                raise ValueError('Prefix tidak cocok dengan source.')
            if ref['start_byte']!=cursor or original[cursor:ref_end].decode('utf-8')!=ref['text']:
                raise ValueError('Rentang referensi tidak cocok dengan source.')
            grammar=(args.grammar or Path(c['provenance']['grammar_path'])).expanduser().resolve()
            if grammar not in cache:cache[grammar]=grammar.read_bytes()
            grammar_hash=sha(cache[grammar])
            same_grammar=grammar_hash==c['provenance']['grammar_sha256']
            if not same_grammar and not args.allow_grammar_mismatch:
                raise ValueError('Hash grammar berbeda. Gunakan grammar asli, bukan mengganti flag tanpa alasan.')
            source_metadata[str(source_path)]=sha(original)
            grammar_metadata[str(grammar)]={'sha256':grammar_hash,'same_as_prepared':same_grammar}
            if c['case_id'] not in calibration:
                check=parse_span(validator,grammar,args.symbol,original,cursor,ref_end,
                                 ref['structure'],work,args.timeout)
                if not check['syntax_valid']:
                    raise ValueError('Source referensi mempunyai parse error; evaluasi dihentikan.')
                calibration[c['case_id']]={'cursor_byte':cursor,
                    'exact_reference_structure_match':check['exact_structure_match'],
                    'reference_structure':ref['structure'],
                    'span_candidates':check['span_candidates']}
                print(f"Kalibrasi byte {cursor}: {check['exact_structure_match']}",flush=True)
            inserted=content.encode('utf-8')
            completed=original[:cursor]+inserted+original[ref_end:]
            # Whitespace remains in completed source. Only the measurement
            # interval excludes outer whitespace, since TS node spans do.
            leading=content[:len(content)-len(content.lstrip())]
            start=cursor+len(leading.encode('utf-8'))
            end=cursor+len(content.rstrip().encode('utf-8'))
            if not content.strip():start=end=cursor
            guided=g['condition'].startswith('guided_rank_')
            target=(condition.get('raw_structure') or condition.get('structure')) if guided else ref['structure']
            parsed=parse_span(validator,grammar,args.symbol,completed,start,end,target,work,args.timeout)
            calibrated=calibration[c['case_id']]['exact_reference_structure_match']
            if not content.strip():status='empty_response'
            elif not parsed['syntax_valid']:status='syntax_invalid_after_replacement'
            elif not calibrated:status='reference_structure_uncalibrated'
            elif parsed['exact_structure_match']:status='exact_structure_match'
            else:status='no_exact_structure_match'
            joined=scores.get((g['case_id'],g['condition'],g['repetition']))
            if args.scores and (joined is None or joined['prediction']!=content):
                raise ValueError('Skor CSV tidak cocok dengan respons.')
            rows.append({'case_id':g['case_id'],'cursor_byte':cursor,'condition':g['condition'],
                'repetition':g['repetition'],'reference_end_byte':ref_end,
                'reference_in_top3':any(isinstance(r.get('retrieval_rank'),int) and 1<=r['retrieval_rank']<=3 for r in c['references']),
                'evaluated_structure_kind':'hint' if guided else 'reference',
                'target_raw_structure':target,'response':content,
                'syntax_valid_after_replacement':parsed['syntax_valid'],
                'calibrated_on_reference':calibrated,'structure_status':status,
                'span_candidates':json.dumps(parsed['span_candidates'],ensure_ascii=False),
                'error_nodes':parsed['error_nodes'],'missing_nodes':parsed['missing_nodes'],
                'exact_match_strip':int(content.strip()==ref['text'].strip()),
                'bleu_existing':float(joined['sacrebleu_sentence_13a']) if joined else '',
                'completed_source_sha256':sha(completed),
                'completed_span_start':start,'completed_span_end':end})
    by_case=defaultdict(list)
    for row in rows:by_case[row['case_id']].append(row)
    for group in by_case.values():
        un=[row for row in group if row['condition']=='without_guidance']
        gd=[row for row in group if row['condition'].startswith('guided_rank_')]
        if (len(group)!=6 or len(un)!=3 or len(gd)!=3
                or {r['repetition'] for r in un}!={1,2,3}
                or {r['condition'] for r in gd}!={f'guided_rank_{i}' for i in range(1,4)}):
            raise ValueError('Kasus tidak lengkap atau anggaran generasi tidak seimbang.')
    summaries=[]
    for label,selection in [('without_guidance',[r for r in rows if r['condition']=='without_guidance']),
                            ('guided_all',[r for r in rows if r['condition'].startswith('guided_rank_')])]:
        n=len(selection)
        if not n:continue
        summaries.append({'group':label,'responses':n,
            'syntax_valid':sum(r['syntax_valid_after_replacement'] for r in selection),
            'syntax_valid_pct':100*sum(r['syntax_valid_after_replacement'] for r in selection)/n,
            'exact_match_strip':sum(r['exact_match_strip'] for r in selection),
            'structure_status_counts':dict(Counter(r['structure_status'] for r in selection)),
            'mean_bleu_all_responses':statistics.mean(r['bleu_existing'] for r in selection) if args.scores else None})
    meta={'protocol':'fixed_reference_span_raw_response_replacement_v1',
        'evaluation_role':'post_hoc_fixed_span_diagnostic_for_historical_responses',
        'syntax_definition':'no Tree-sitter error/missing nodes in full replaced source',
        'structure_definition':'exact leaf-and-right-siblings sequence over complete non-whitespace response span, gated by reference calibration',
        'limitations':['not semantic/functional validation','non-match does not rule out equivalent grammar derivations',
                       'unguided target is reference structure; guided target is hint, so their structure-status counts answer different questions',
                       'reference span is evaluation-only; model did not receive its end offset; historical outputs remain an exploratory boundary diagnostic'],
        'cases_sha256':sha(args.cases.read_bytes()),'responses_sha256':sha(args.responses.read_bytes()),
        'validator_sha256':sha(validator.read_bytes()),'grammar':grammar_metadata,'sources':source_metadata,
        'reference_calibrated':sum(c['exact_reference_structure_match'] for c in calibration.values()),
        'reference_cases':len(calibration),'summary':summaries}
    args.output_dir.mkdir(parents=True,exist_ok=False)
    write_csv(args.output_dir/'response_validation.csv',rows)
    write_csv(args.output_dir/'reference_calibration.csv',list(calibration.values()))
    (args.output_dir/'summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    for s in summaries:
        print(f"\n{s['group']}: syntax valid {s['syntax_valid']}/{s['responses']} ({s['syntax_valid_pct']:.2f}%)")
        print('Structure statuses:',json.dumps(s['structure_status_counts']))
    print(f"\nKalibrasi referensi: {meta['reference_calibrated']}/{meta['reference_cases']}")
    print('Saved:',args.output_dir)
    print('Tidak ada API call. Tidak ada respons dipotong/diperbaiki atau dikeluarkan dari skor teks.')
    print('Diagnostik pada respons lama: model saat generasi belum diberi batas akhir span.')


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--cases',type=Path,required=True)
    p.add_argument('--responses',type=Path,required=True)
    p.add_argument('--scores',type=Path)
    p.add_argument('--validator',type=Path,default=Path(__file__).resolve().parent/'bin/validate_span')
    p.add_argument('--grammar',type=Path)
    p.add_argument('--symbol',default='tree_sitter_smallbasic')
    p.add_argument('--source',type=Path,help='Explicit source override; must match saved SHA-256')
    p.add_argument('--allow-grammar-mismatch',action='store_true',help='For explicitly labelled alternate-grammar diagnostics only')
    p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--output-dir',type=Path,required=True)
    evaluate(p.parse_args())


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,RuntimeError,KeyError,subprocess.TimeoutExpired) as exc:
        print('ERROR:',exc,file=sys.stderr);sys.exit(1)
