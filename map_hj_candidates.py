#!/usr/bin/env python3
"""Map HJ internal keyword tokens before constructing frozen v2 prompts.

Python 3.9+, standard library. Requires sibling hj_benchmark_tools.py.
Adapts the token-mapping step in HJ's extension (src/mapLoader.ts), with a
conservative regex-to-keyword conversion: ambiguous regexes are never guessed.
Keeps raw candidates, frequencies, parser states, references and retrieval ranks.
No database/parser changes and no API calls. Supported language: Small Basic.
"""

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

from hj_benchmark_tools import make_prompt


GENERATED = re.compile(r"_token\d+$")


def keyword_from_pattern(pattern):
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", pattern):
        return pattern
    pairs = re.findall(r"\[([A-Za-z])([A-Za-z])\]", pattern)
    if pairs and "".join(f"[{a}{b}]" for a, b in pairs) == pattern:
        if all(a != b and a.lower() == b.lower() for a, b in pairs):
            # Match HJ's first-letter convention. Small Basic ignores case.
            return "".join(a for a, _ in pairs)
    if pattern in {r"\n", r"\r\n"}:
        return "<CR>"
    return None


def map_structure(structure, mapping):
    converted = []
    unknown = []
    for token in structure.split():
        info = mapping.get(token)
        replacement = token
        if info and info.get("type") == "STRING":
            replacement = info["content"]
            if replacement in {"\n", "\r\n"}:
                replacement = "<CR>"
        elif info and info.get("type") == "PATTERN" and GENERATED.search(token):
            replacement = keyword_from_pattern(info["content"])
            if replacement is None:
                replacement = token
        if GENERATED.search(replacement):
            unknown.append(token)
        converted.append(replacement)
    return " ".join(converted), unknown


def prepare(args):
    if args.output.resolve() == args.input.resolve() or args.output.exists():
        raise FileExistsError("Pilih output baru; input/hasil lama tidak ditimpa.")
    raw = args.input.read_bytes()
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not cases:
        raise ValueError("Tidak ada kasus input.")
    updated = []
    changes = Counter()
    loaded = {}
    unresolved = set()
    coverage = 0
    for original in cases:
        if original.get("language") != "smallbasic":
            raise ValueError("Adapter prompt ini khusus Small Basic.")
        mapping_path = (args.mapping or Path(original["provenance"]["database_path"]).with_name("token_mapping.json")).expanduser().resolve()
        if mapping_path not in loaded:
            data = mapping_path.read_bytes()
            loaded[mapping_path] = (json.loads(data), hashlib.sha256(data).hexdigest())
        mapping, mapping_hash = loaded[mapping_path]
        case = copy.deepcopy(original)
        for item in case["top_candidates"]:
            readable, unknown = map_structure(item["structure"], mapping)
            unresolved.update(unknown)
            item["readable_structure"] = readable
        for condition in case["conditions"]:
            if condition["condition"] == "without_guidance":
                condition.update(structure=None, prompt=make_prompt(case["prefix"], None))
                continue
            if not condition["condition"].startswith("guided_rank_"):
                raise ValueError("Kondisi tidak dikenal.")
            raw_structure = condition.get("raw_structure", condition["structure"])
            readable, unknown = map_structure(raw_structure, mapping)
            unresolved.update(unknown)
            condition.update(raw_structure=raw_structure, structure=readable,
                             prompt=make_prompt(case["prefix"], readable))
            if readable != raw_structure:
                changes[(raw_structure, readable)] += 1
        coverage += any(isinstance(ref.get("retrieval_rank"), int) and
                        1 <= ref["retrieval_rank"] <= 3
                        for ref in case["references"])
        case.update(prompt_profile="smallbasic_v2_hj_keyword_mapping_1",
                    prompt_parent_sha256=hashlib.sha256(raw).hexdigest(),
                    token_mapping_path=str(mapping_path), token_mapping_sha256=mapping_hash,
                    evaluation_role="development_pilot_not_held_out_test")
        updated.append(case)
    if unresolved:
        raise ValueError("Token internal belum dapat diterjemahkan dengan aman: " + ", ".join(sorted(unresolved)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for case in updated:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"Prepared: {len(updated)} kasus -> {args.output}")
    for (before, after), count in sorted(changes.items()):
        print(f"Mapping ({count} kondisi): {before} -> {after}")
    print(f"Referensi struktur dalam Top-3: {coverage}/{len(updated)} (pencocokan persis struktur asli)")
    print("Frekuensi, urutan kandidat, state, dan referensi tidak diubah.")
    print("Nonterminal seperti CRStmtCRs tetap merupakan placeholder, bukan kode literal.")
    print("Tidak ada panggilan API. Pemetaan ini tidak memperbaiki ranking/state parser.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mapping", type=Path)
    prepare(p.parse_args())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
