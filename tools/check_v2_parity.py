#!/usr/bin/env python3
"""Offline golden comparison of old six-task prompt inputs against new RQ2 cases.

Only compare like-for-like protocols: SB lab-v2 prefix or C lab FIM/full.
New default C prefix-last10 is intentionally NOT identical to legacy FIM.
"""
import argparse
import json
from pathlib import Path


def read_cases(path):
    return {x["case_id"]: x for x in
            (json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip())}


def compare(original, updated):
    if updated.get("prompt_profile", "").startswith("v2_lab_") is False:
        raise ValueError("Only lab profile supports exact prompt parity")
    if original["language"] == "c" and not (updated["completion_mode"] == "infill" and updated["context_mode"] == "full"):
        raise ValueError("C prompt parity requires --completion infill --context full")
    if original["language"] != "c" and updated["completion_mode"] != "prefix":
        raise ValueError("Small Basic prompt parity requires prefix mode")
    old = {x["condition"]: x["prompt"] for x in original["conditions"]}
    new = {x["condition"]: x["prompt"] for x in updated["conditions"]}
    expected = ["without_guidance", "guided_rank_1", "guided_rank_2", "guided_rank_3"]
    diffs = [label for label in expected if label not in old or new.get(label) != old[label]]
    if diffs:
        raise ValueError(f"Prompt mismatch in case={original['case_id']}: {diffs}")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original", type=Path, required=True, help="Original mapped SB / prepared C cases")
    p.add_argument("--v2", type=Path, required=True, help="RQ2 v2 cases.jsonl")
    a = p.parse_args()
    old = read_cases(a.original)
    cases = read_cases(a.v2)
    for key, current in cases.items():
        if key not in old:
            raise ValueError(f"Missing original case_id={key}")
        compare(old[key], current)
    print(f"PASS: {len(cases)} cases, 4 prompt templates each, exact match")

if __name__ == "__main__":
    main()
