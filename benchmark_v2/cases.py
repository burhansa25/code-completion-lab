"""Transform frozen HJ cases into clearly identified, auditable RQ2 tasks.

Reference *structure* is included only in ideal_guidance; reference completion
text is retained for scoring but never used as prompt text. C prefixes are
computed from original source bytes, with integrity checks.
"""
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha_obj(obj):
    return hash_bytes(canonical(obj).encode("utf-8"))


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(s) for s in f if s.strip()]


def last_n_lines(prefix, n):
    """Include the cursor's partial line in the n line segments."""
    if n < 1:
        raise ValueError("n must be positive")
    return "\n".join(prefix.split("\n")[-n:])


def ranked_pool(case, top_k):
    """Recompute HJ's frequency aggregation; verify first 3 against source.

    No parser rerun. The database path and its hash originate in HJ preparation.
    """
    top = case.get("top_candidates") or []
    if top_k <= len(top):
        return top[:top_k]
    provenance = case.get("provenance") or {}
    path = Path(provenance.get("database_path", ""))
    if not path.is_file():
        raise FileNotFoundError(f"Candidate DB required for Top-{top_k}: {path}")
    actual_sha = hash_bytes(path.read_bytes())
    if actual_sha != provenance.get("database_sha256"):
        raise ValueError(f"Candidate DB changed since preparation: {path}")
    db = json.loads(path.read_text(encoding="utf-8"))
    scores = Counter()
    for state in case.get("states") or []:
        for item in db.get(str(state), []):
            scores[item["key"]] += item["value"]
    pool = [{"structure": structure, "frequency": freq}
            for structure, freq in scores.most_common()]
    for idx, previous in enumerate(top):
        if idx >= len(pool) or previous["structure"] != pool[idx]["structure"] or previous["frequency"] != pool[idx]["frequency"]:
            raise ValueError(f"Candidate rank parity failure at {case.get('case_id')} index={idx}")
    return pool[:top_k]


def mapped_structure(case, raw, *, allow_unmapped=False):
    if case["language"] != "smallbasic":
        return raw
    mapping_path = case.get("token_mapping_path")
    if not mapping_path:
        if allow_unmapped:
            return raw
        raise ValueError("Small Basic mapped cases required (token_mapping_path missing)")
    path = Path(mapping_path)
    if not path.is_file():
        raise FileNotFoundError(f"Token mapping file not found: {path}")
    if hash_bytes(path.read_bytes()) != case.get("token_mapping_sha256"):
        raise ValueError("token_mapping.json differs from the mapped HJ cases")
    # Existing HJ token-mapper is kept as the frozen implementation.
    from map_hj_candidates import map_structure
    result, unknown = map_structure(raw, json.loads(path.read_text(encoding="utf-8")))
    if unknown:
        raise ValueError(f"Unresolved internal grammar tokens for {case['case_id']}: {unknown}")
    return result


def context_parts(case, mode, completion):
    prefix = case["prefix"]
    suffix = None
    if case["language"] == "c":
        source_file = Path(case["source_path"])
        source = source_file.read_bytes()
        if hash_bytes(source) != case.get("source_sha256"):
            raise ValueError(f"C source modified since HJ preparation: {source_file}")
        cursor = int(case["cursor_byte"])
        if source[:cursor].decode("utf-8") != prefix:
            raise ValueError(f"C prefix at cursor differs from HJ preparation: {case['case_id']}")
        if mode == "last10":
            prefix = last_n_lines(prefix, 10)
        elif mode != "full":
            raise ValueError(f"Unknown C context mode: {mode}")
        if completion == "infill":
            ref = case["references"][0]
            end = ref["end_byte_from_text"]
            if source[cursor:end] != ref["text"].encode("utf-8"):
                raise ValueError(f"Reference gap does not match source: {case['case_id']}")
            suffix = source[end:].decode("utf-8")
    elif case["language"] != "smallbasic":
        raise ValueError(f"Unknown language: {case['language']}")
    elif completion != "prefix":
        raise ValueError("Small Basic only supports prefix completion")
    return prefix, suffix


def build_prompt(case, prefix, suffix, structure, *, profile, completion):
    language = case["language"]
    if profile == "lab":
        if language == "smallbasic":
            from hj_benchmark_tools import make_prompt
            return make_prompt(prefix, structure)
        if completion == "infill":
            # Same frozen C FIM template as run_hj_c_batch.py.
            from run_hj_c_batch import PREFIX_SUFFIX
            base = (PREFIX_SUFFIX + f"PREFIX START\n{prefix}\nPREFIX END\n\n"
                    f"SUFFIX START\n{suffix}\nSUFFIX END\n")
            return (base + f"\nRequired continuation structure:\n{structure}\n" if structure is not None
                    else base + "\nProvide the next natural code continuation.\n")
        base = ("Complete the following C code at the cursor.\n"
                "Return only the code to insert after the prefix. "
                "Do not repeat the prefix or include Markdown.\n\n"
                f"Code prefix:\n{prefix}\n")
        return (base + f"\nRequired continuation structure:\n{structure}\n" if structure is not None
                else base + "\nProvide the next natural code continuation.\n")
    if profile == "fragment-v1":
        # Experimental protocol, frozen across all guidance arms.  The target
        # reference text, reference length, and retrieval rank are never used
        # to form the prompt.  Only the guidance arm adds a grammar structure.
        label = "Microsoft Small Basic" if language == "smallbasic" else "C"
        base = (
            f"You are a code-fragment completion engine for {label}.\n"
            "Continue the source code immediately after the cursor.\n"
            "Return ONE compact continuation fragment, not an entire example "
            "program or an explanation.\n"
            "Do not repeat any existing prefix. Do not output Markdown fences.\n"
            "Preserve source spelling, indentation, punctuation and language syntax.\n"
            "When there is insufficient context, choose a plausible short "
            "continuation; do not hallucinate an entire function.\n"
        )
        if language == "smallbasic":
            base += (
                "Grammar placeholders, when provided: ID denotes an identifier; "
                "NUM a number, STR a quoted string, Expr an expression, and "
                "Exprs an argument expression sequence. Instantiate placeholders "
                "as code, never print their names literally.\n"
            )
        base += f"\nPREFIX START\n{prefix}\nPREFIX END\n"
        if structure is None:
            return base + "\nContinue once at the cursor. Output only the fragment.\n"
        return (base + "\nTARGET GRAMMAR STRUCTURE:\n" + structure + "\n"
                "Instantiate exactly one continuation matching this grammar "
                "structure, and stop when that fragment is complete.\n")
    if profile == "monir-style":
        # A clearly labeled adaptation, NOT a claim of byte-identical reproduction.
        label = "Microsoft Small Basic" if language == "smallbasic" else "C"
        target = structure if structure is not None else "next token or line"
        return (f"This is the incomplete {label} programming language code:\n{prefix}\n"
                f"'{target}'\nComplete the '{target}' part of the code in the {label} programming language. "
                f"Just show your answer in place of '{target}'.\n")
    raise ValueError(f"Unknown profile: {profile}")


def eligible(case, top_k):
    return (case.get("status") == "prepared" and len(case.get("references") or []) == 1
            and len(case.get("top_candidates") or []) >= 3
            and int(case.get("candidate_count", 0)) >= top_k)


def prepare_case(original, *, top_k=3, context="last10", completion="prefix",
                 profile="lab", ideal_repetitions=3):
    if not eligible(original, top_k):
        raise ValueError(f"Ineligible case: {original.get('case_id')}")
    case = copy.deepcopy(original)
    prefix, suffix = context_parts(case, context, completion)
    pool = ranked_pool(case, top_k)
    old_top = case["top_candidates"]
    if case["language"] == "smallbasic":
        for idx, item in enumerate(pool):
            if idx < len(old_top) and "readable_structure" in old_top[idx]:
                item["readable_structure"] = old_top[idx]["readable_structure"]
            else:
                item["readable_structure"] = mapped_structure(case, item["structure"])
    reference = case["references"][0]
    ideal = mapped_structure(case, reference["structure"])
    if not ideal.strip():
        raise ValueError(f"Blank ideal structure: {case['case_id']}")
    items = [("without_guidance", None, 3)]
    items += [(f"guided_rank_{i}", x.get("readable_structure", x["structure"]), 1)
              for i, x in enumerate(pool, 1)]
    items.append(("ideal_guidance", ideal, ideal_repetitions))
    conditions = []
    for name, structure, repetitions in items:
        prompt = build_prompt(case, prefix, suffix, structure, profile=profile, completion=completion)
        # The reference text is a secret target. It must never be in the *ideal
        # structure*; a code prefix may legitimately contain text seen earlier.
        conditions.append({"condition": name, "prompt": prompt, "structure": structure,
                           "repetitions": repetitions, "oracle_reference_structure": name == "ideal_guidance"})
    case["candidate_pool"] = pool  # preserve `top_candidates` for legacy parity
    case["conditions"] = conditions
    case["prompt_profile"] = f"v2_{profile}_{context if case['language']=='c' else 'full'}_{completion}"
    case["completion_mode"] = completion
    case["context_mode"] = context if case["language"] == "c" else "full"
    case["experiment_version"] = "v2-rq2-2" if profile == "fragment-v1" else "v2-rq2-1"
    case["planned_api_requests"] = sum(x["repetitions"] for x in conditions)
    case["prompt_sha256"] = sha_obj([c["prompt"] for c in conditions])
    return case
