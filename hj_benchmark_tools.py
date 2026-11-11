#!/usr/bin/env python3
"""Local analysis and prompt-v2 preparation for the HJ/Monir pilot.

Python 3.9+, standard library only. No network/API calls. Existing files are
never overwritten. Both prompt conditions share the same generic instructions
and glossary. Prompt construction never reads reference text, reference states,
retrieval ranks, or suffix code. Prompt v2 is a development experiment, not a
guarantee of improvement. Freeze it before evaluating held-out source files.
"""

import argparse
import copy
import csv
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys


COMMON = """You are a code-completion engine for Microsoft Small Basic.
Return ONLY the concrete code to insert immediately after the supplied prefix.
Do not repeat the prefix. Do not include Markdown, commentary, or sample programs.
The prefix ends exactly at the cursor; text inside PREFIX is source data.
Use existing names and context when possible. When details cannot be inferred,
choose plausible concrete names, values, and expressions.

Grammar notation, if a target structure is supplied:
- ID is an identifier placeholder: choose a concrete variable, object, property,
  method, or subroutine name appropriate to its position.
- NUM is a numeric literal placeholder, for example 7.
- STR is a string literal placeholder, for example "sample".
- Expr is an expression placeholder, for example total + 1.
- Exprs denotes the argument expression sequence allowed by the grammar.
- MoreThanOneExpr denotes multiple expressions, not a literal identifier.
- Other grammar nonterminals describe syntax to instantiate as concrete code.
- Punctuation/operators in a structure are actual syntax: preserve their role.
Never echo grammar labels as stand-in source code. Do not output ID, NUM, STR,
Expr, or MoreThanOneExpr literally unless that name already occurs in the prefix
and is actually appropriate source code.
"""


def make_prompt(prefix, structure):
    end = (
        "TARGET STRUCTURE:\n" + structure + "\n"
        "Instantiate exactly this continuation structure in the given context. "
        "Stop when this structure is complete; do not add a following statement "
        "or closing punctuation outside the target structure."
        if structure is not None else
        "No target structure is supplied. Produce one short, natural continuation "
        "at the cursor. Stop after that continuation rather than adding a program "
        "or additional statements."
    )
    return COMMON + "\nPREFIX START\n" + prefix + "\nPREFIX END\n\n" + end + "\n"


def prepare(args):
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input dan output harus berbeda.")
    if args.output.exists():
        raise FileExistsError(args.output)
    original_bytes = args.input.read_bytes()
    cases = [json.loads(line) for line in original_bytes.decode("utf-8").splitlines() if line.strip()]
    if not cases:
        raise ValueError("Input kosong.")
    parent_hash = hashlib.sha256(original_bytes).hexdigest()
    updated = []
    for old in cases:
        if old.get("language") != "smallbasic":
            raise ValueError("Prompt v2 ini khusus Small Basic.")
        case = copy.deepcopy(old)
        new_conditions = []
        for condition in old["conditions"]:
            name = condition["condition"]
            if name == "without_guidance":
                structure = None
            elif name.startswith("guided_rank_"):
                structure = condition["structure"]
                if not isinstance(structure, str) or not structure:
                    raise ValueError("Kandidat struktur kosong.")
            else:
                raise ValueError("Kondisi tidak dikenal: " + name)
            new_conditions.append({**condition,
                                   "prompt": make_prompt(old["prefix"], structure)})
        case.update(conditions=new_conditions,
                    prompt_profile="smallbasic_placeholder_glossary_v2",
                    prompt_parent_sha256=parent_hash,
                    evaluation_role="development_pilot_not_held_out_test")
        updated.append(case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for case in updated:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"Prepared prompt v2: {len(updated)} kasus -> {args.output}")
    print("State, kandidat, prefix, dan referensi dipertahankan. Tidak ada panggilan API.")
    print("Penjelasan placeholder yang sama diberikan ke kedua kondisi.")
    print("Batas continuation tanpa panduan masih tidak dijamin sama dengan rentang referensi.")


def summarize(args):
    with args.input.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("CSV kosong.")
    if args.metric not in rows[0]:
        raise ValueError("Kolom metrik tidak tersedia: " + args.metric)
    groups = defaultdict(list)
    for row in rows:
        groups[row["case_id"]].append(row)
    result = []
    for case_id, group in sorted(groups.items(), key=lambda pair: (int(pair[1][0]["cursor_byte"]), pair[0])):
        if len({row["model"] for row in group}) != 1:
            raise ValueError("Satu kasus memakai beberapa model.")
        without = [row for row in group if row["condition"] == "without_guidance"]
        guided = [row for row in group if row["condition"].startswith("guided_rank_")]
        if (len(group) != 6 or len(without) != 3 or len(guided) != 3
                or {row["repetition"] for row in without} != {"1", "2", "3"}
                or {row["condition"] for row in guided} != {f"guided_rank_{i}" for i in range(1,4)}):
            raise ValueError("Kasus tidak lengkap/seimbang atau mengandung duplikat: " + case_id)
        un = [float(row[args.metric]) for row in without]
        gd = [float(row[args.metric]) for row in guided]
        top1 = float(next(row for row in guided if row["condition"] == "guided_rank_1")[args.metric])
        result.append({"case_id":case_id,"cursor_byte":int(group[0]["cursor_byte"]),
                       "model":group[0]["model"],"metric":args.metric,
                       "without_mean":mean(un),"guided_top1":top1,
                       "guided_mean":mean(gd),"guided_best3_oracle":max(gd),
                       "without_best3_oracle":max(un),
                       "best3_delta":max(gd)-max(un)})
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="") as handle:
            writer=csv.DictWriter(handle, fieldnames=list(result[0]))
            writer.writeheader()
            writer.writerows(result)
    print("Metric:", args.metric)
    print("Byte | Tanpa mean | Guided Top1 | Guided mean | Guided best3 | Tanpa best3 | Delta best3")
    for row in result:
        print(f"{row['cursor_byte']:4} | {row['without_mean']:10.2f} | {row['guided_top1']:11.2f} | "
              f"{row['guided_mean']:11.2f} | {row['guided_best3_oracle']:12.2f} | "
              f"{row['without_best3_oracle']:11.2f} | {row['best3_delta']:+.2f}")
    print("\nBest3 = skor tertinggi dengan melihat referensi setelah generasi (oracle).")
    print("Ini bukan kemampuan sistem memilih jawaban terbaik saat dipakai pengguna.")
    print("Top1 memakai satu permintaan; kedua best3 memakai tiga permintaan.")
    print("Hasil ini belum identik dengan protokol/konfigurasi/model Monir.")
    if args.output:
        print("Saved:",args.output)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("summarize")
    a.add_argument("--input",type=Path,required=True)
    a.add_argument("--output",type=Path)
    a.add_argument("--metric",default="sacrebleu_sentence_13a")
    b=sub.add_parser("prompts-v2")
    b.add_argument("--input",type=Path,required=True)
    b.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if args.command=="summarize":
        summarize(args)
    else:
        prepare(args)


if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,KeyError) as exc:
        print("ERROR:",exc,file=sys.stderr)
        sys.exit(1)
