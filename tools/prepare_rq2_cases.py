#!/usr/bin/env python3
"""Prepare RQ2 conditions (unguided, ideal, ranked) without API calls.

Output adds explicit condition repetitions. Existing legacy API runners must be
extended to honor ideal_guidance and repetitions before directly running it.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def last_n_lines(prefix, n):
    if not n or n < 1:
        return prefix
    return "\n".join(prefix.split("\n")[-n:])


def monir_prompt(language, prefix, structure):
    label = "Microsoft Small Basic" if language == "smallbasic" else "C"
    if structure is None:
        target = "next token or line"
    else:
        target = structure
    return (f"This is the incomplete {label} programming language code:\n"
            f"{prefix}\n'{target}'\n"
            f"Complete the '{target}' part of the code in the {label} programming language. "
            f"Just show your answer in place of '{target}'.\n")


def lab_prompt(language, prefix, structure):
    # Small Basic copies the existing v2 prompt function, so both guided and
    # unguided messages remain identical to that profile when run in the repo.
    if language == "smallbasic":
        try:
            from hj_benchmark_tools import make_prompt
        except ImportError as exc:
            raise RuntimeError("Install this tool in the existing repo root under tools/; "
                               "hj_benchmark_tools.py is required for Small Basic lab prompts.") from exc
        return make_prompt(prefix, structure)
    base = ("Complete the following C code at the cursor.\n"
            "Return only the code to insert after the prefix. "
            "Do not repeat the prefix or include Markdown.\n\n"
            f"Code prefix:\n{prefix}\n")
    return base + ("\nProvide the next natural code continuation.\n" if structure is None
                   else f"\nRequired continuation structure:\n{structure}\n")


def map_ideal(case):
    reference = case["references"][0]
    structure = reference["structure"]
    mapping_path = case.get("token_mapping_path")
    if case["language"] == "smallbasic" and mapping_path:
        try:
            from map_hj_candidates import map_structure
        except ImportError as exc:
            raise RuntimeError("map_hj_candidates.py must be importable from repo root") from exc
        mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
        structure, unknown = map_structure(structure, mapping)
        if unknown:
            raise ValueError(f"Unresolved internal tokens for {case['case_id']}: {unknown}")
    return structure


def prepare_case(case, *, profile="lab", c_context="last10", c_completion="prefix", ideal_repetitions=3):
    if case.get("status") != "prepared":
        raise ValueError(f"Case not prepared: {case.get('case_id')}")
    if len(case.get("references", [])) != 1:
        raise ValueError("RQ2 requires exactly one verified reference in this controlled cohort")
    candidates = case.get("top_candidates", [])
    if len(candidates) < 3:
        raise ValueError("RQ2 requires at least three ranked candidates")
    lang = case["language"]
    if lang not in ("smallbasic", "c"):
        raise ValueError(f"Unsupported language: {lang}")
    if lang == "smallbasic" and c_completion == "infill":
        # c_completion affects only C; no suffix is supplied to Small Basic.
        pass
    prefix = case["prefix"]
    if lang == "c" and c_context == "last10":
        prefix = last_n_lines(prefix, 10)
    if lang == "c" and c_completion == "infill":
        raise ValueError("FIM must be prepared through the existing C batch with its "
                         "verified suffix, not via the prefix-only RQ2 preparer")
    # monir-style is a conceptual reconstruction, not a byte-for-byte replica
    # of Monir's archived API messages. Golden prompt parity is a later phase.
    prompt = monir_prompt if profile == "monir-style" else lab_prompt
    ideal = map_ideal(case)
    rows = [{"condition": "without_guidance", "structure": None,
             "prompt": prompt(lang, prefix, None), "repetitions": 3}]
    for rank, item in enumerate(candidates[:3], 1):
        candidate = item.get("readable_structure") or item["structure"]
        rows.append({"condition": f"guided_rank_{rank}", "structure": candidate,
                     "raw_structure": item["structure"],
                     "prompt": prompt(lang, prefix, candidate), "repetitions": 1})
    rows.append({"condition": "ideal_guidance", "structure": ideal,
                 "raw_structure": case["references"][0]["structure"],
                 "prompt": prompt(lang, prefix, ideal), "repetitions": ideal_repetitions,
                 "oracle_reference_structure": True})
    updated = copy.deepcopy(case)
    updated["conditions"] = rows
    updated["prompt_profile"] = f"rq2_{profile}_prefix_{c_context if lang == 'c' else 'full'}_v1"
    updated["completion_mode"] = "prefix"
    updated["reference_text_never_in_prompt"] = True
    updated["planned_api_requests"] = sum(x["repetitions"] for x in rows)
    updated["experiment_version"] = "rq2_preparation_v1"
    return updated


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--profile", choices=("monir-style", "lab"), default="lab")
    p.add_argument("--c-context", choices=("last10", "full"), default="last10")
    p.add_argument("--c-completion", choices=("prefix", "infill"), default="prefix")
    p.add_argument("--ideal-repetitions", choices=(1, 3), type=int, default=3)
    p.add_argument("--limit", type=int, help="Number of eligible positions for dry-run/pilot")
    a = p.parse_args(argv)
    if a.output.exists():
        raise FileExistsError(f"Never overwrite prepared cases: {a.output}")
    if a.input.resolve() == a.output.resolve():
        raise ValueError("Input and output must differ")
    count = skipped = 0
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.input.open(encoding="utf-8") as source, a.output.open("x", encoding="utf-8") as output:
        for line in source:
            if not line.strip():
                continue
            case = json.loads(line)
            if case.get("status") != "prepared" or len(case.get("references", [])) != 1 or len(case.get("top_candidates", [])) < 3:
                skipped += 1
                continue
            updated = prepare_case(case, profile=a.profile, c_context=a.c_context,
                                   c_completion=a.c_completion, ideal_repetitions=a.ideal_repetitions)
            output.write(json.dumps(updated, ensure_ascii=False) + "\n")
            count += 1
            if a.limit and count >= a.limit:
                break
    print(f"Prepared {count} RQ2 cases ({skipped} excluded); {count*(6+a.ideal_repetitions)} planned requests.")
    print("No API requests were made. Generation engine MUST support ideal_guidance + repetitions.")
    print("Never compare rows produced with different prefix profiles as a single paired experiment.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as err:
        print("ERROR:", err, file=sys.stderr)
        sys.exit(2)
