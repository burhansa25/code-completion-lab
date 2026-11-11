#!/usr/bin/env python3
"""Prepare auditable HJ structural-guidance cases; no network or LLM calls.

Python 3.9+, standard library only. HJ's modified Tree-sitter remains the
parser/database backend. Experiment design follows guided/unguided comparisons
in Monir's project; this adapter is newly written, not a copied parser.

Sources:
https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection
https://github.com/chaendaya/code-completion-extension
https://github.com/monircse061/ChatGPT-Code-Completion-Work

candidate_text is checked against source bytes at its recorded start. This
does NOT prove its end is complete: HJ's multiline reference extraction can
truncate text. Do not use these cases for final scoring until spans are checked.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


DEFAULT_BASE = Path.home() / "PL/codecompletion_benchmarks/smallbasic"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def make_prompt(prefix, language, structure=None):
    prompt = (
        f"Complete the following {language} code at the cursor.\n"
        "Return only the code to insert after the prefix. "
        "Do not repeat the prefix or include Markdown.\n\n"
        f"Code prefix:\n{prefix}\n"
    )
    if structure is None:
        return prompt + "\nProvide the next natural code continuation.\n"
    return prompt + f"\nRequired continuation structure:\n{structure}\n"


def retrieve(executable, grammar, language, source, offset, mode, db, timeout):
    # The native CLI writes logs/Test.data to its cwd. Isolate each query.
    with tempfile.TemporaryDirectory(prefix="hj_benchmark_") as work:
        run = subprocess.run(
            [str(executable), language, str(grammar), str(source),
             str(offset), str(mode)],
            cwd=work, capture_output=True, text=True, errors="replace",
            timeout=timeout,
        )
    if run.returncode:
        raise RuntimeError(
            f"parser exit={run.returncode}; stderr={run.stderr[-1200:]}"
        )
    matches = re.findall(r"@@PREDICT:[ \t]*([0-9 \t]*)", run.stdout)
    if not matches or not matches[-1].strip():
        raise RuntimeError("parser returned no @@PREDICT states")
    states = list(dict.fromkeys(int(s) for s in matches[-1].split()))
    scores = Counter()
    for state in states:
        for candidate in db.get(str(state), []):
            scores[candidate["key"]] += candidate["value"]
    # Stable ties follow state/DB encounter order, matching HJ's lookup behavior.
    ranked = [{"structure": key, "frequency": value}
              for key, value in scores.most_common()]
    return states, ranked


def references_at(entries, source_bytes, offset):
    valid, rejected = [], []
    for entry in entries:
        text = entry.get("candidate_text")
        structure = entry.get("candidate")
        if not isinstance(text, str) or not text or not isinstance(structure, str):
            rejected.append({"reason": "missing reference text/structure"})
            continue
        encoded = text.encode("utf-8")
        if source_bytes[offset:offset + len(encoded)] != encoded:
            rejected.append({"reason": "reference does not match source at offset",
                             "structure": structure})
            continue
        valid.append({
            "structure": structure, "text": text,
            "start_byte": offset, "end_byte_from_text": offset + len(encoded),
            "collection_state_id": entry.get("state_id"),
            "validation": "matches_source_slice_end_completeness_unverified",
        })
    return valid, rejected


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", type=Path, default=DEFAULT_BASE)
    p.add_argument("--language", choices=["smallbasic", "c"], default="smallbasic")
    p.add_argument("--source-dir", type=Path)
    p.add_argument("--grammar", type=Path)
    p.add_argument("--executable", type=Path)
    p.add_argument("--reference-dir", type=Path)
    p.add_argument("--database", type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--file", default=None,
                       help="Source path relative to source-dir; default 01_HelloWorld.sb")
    group.add_argument("--all", action="store_true", help="Process all source files")
    p.add_argument("--mode", type=int, choices=[0, 2], default=0)
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--output", type=Path, required=True,
                   help="New JSONL file; existing files are never overwritten")
    return p.parse_args()


def main():
    args = parse_args()
    base = args.base.expanduser().resolve()
    parser = base / "tree-sitter"
    extension = base / "code-completion-extension"
    if args.language == "c" and (not args.source_dir or not args.grammar):
        raise ValueError("C requires explicit --source-dir and --grammar")
    source_dir = (args.source_dir or base / "tree-sitter-smallbasic/SB_Sample").expanduser().resolve()
    grammar = (args.grammar or base / "tree-sitter-smallbasic/smallbasic.so").expanduser().resolve()
    executable = (args.executable or parser / "TreeSitterCutFile").expanduser().resolve()
    reference_dir = (args.reference_dir or parser / "reports" / args.language).expanduser().resolve()
    database = (args.database or extension / "resources" / args.language / "candidates.json").expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new name: {output}")
    for path in (executable, grammar, database):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (source_dir, reference_dir):
        if not path.is_dir():
            raise NotADirectoryError(path)
    db = json.loads(database.read_text(encoding="utf-8"))
    suffix = ".sb" if args.language == "smallbasic" else ".c"
    if args.all:
        sources = sorted(p for p in source_dir.rglob("*" )
                         if p.is_file() and p.suffix.lower() == suffix
                         and not any(part in {".git", "node_modules", "build"}
                                     for part in p.relative_to(source_dir).parts))
    else:
        sources = [(source_dir / (args.file or "01_HelloWorld.sb")).resolve()]
    if not sources:
        raise ValueError("No source files selected")
    provenance = {
        "parser_path": str(executable), "parser_sha256": digest(executable),
        "grammar_path": str(grammar), "grammar_sha256": digest(grammar),
        "database_path": str(database), "database_sha256": digest(database),
        "mode": args.mode,
    }
    rows = []
    for source in sources:
        relative = source.relative_to(source_dir).as_posix()
        safe_name = relative.replace("/", "_").replace("..", "")
        reference = reference_dir / (safe_name + ".json")
        answers = json.loads(reference.read_text(encoding="utf-8"))
        reference_hash = digest(reference)
        raw = source.read_bytes()
        for key, entries in sorted(answers.items(), key=lambda pair: int(pair[0])):
            offset = int(key)
            row = {
                "schema_version": 1, "language": args.language,
                "source_path": str(source), "source_relative": relative,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "reference_path": str(reference),
                "reference_sha256": reference_hash,
                "cursor_byte": offset, "provenance": provenance,
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
            row["case_id"] = hashlib.sha256(
                json.dumps({k: row[k] for k in (
                    "language", "source_relative", "source_sha256",
                    "reference_sha256", "cursor_byte", "provenance")},
                    sort_keys=True).encode()
            ).hexdigest()[:24]
            try:
                if offset < 0 or offset > len(raw):
                    raise ValueError("cursor is outside the source")
                prefix = raw[:offset].decode("utf-8")
                refs, rejected = references_at(entries, raw, offset)
                row.update(prefix=prefix, references=refs, rejected_references=rejected)
                states, ranked = retrieve(executable, grammar, args.language,
                    source, offset, args.mode, db, args.timeout)
                for reference_entry in refs:
                    target = reference_entry["structure"].replace(" ", "")
                    reference_entry["retrieval_rank"] = next((
                        rank for rank, item in enumerate(ranked, 1)
                        if item["structure"].replace(" ", "") == target
                    ), None)
                row.update(states=states, candidate_count=len(ranked),
                           top_candidates=ranked[:3])
                label = "Microsoft Small Basic" if args.language == "smallbasic" else "C"
                # Prompts use ONLY prefix and retrieved structures. Reference
                # text and collection states above never enter the requests.
                conditions = [{"condition": "without_guidance", "structure": None,
                               "prompt": make_prompt(prefix, label)}]
                conditions.extend({
                    "condition": f"guided_rank_{rank}",
                    "structure": item["structure"],
                    "prompt": make_prompt(prefix, label, item["structure"]),
                } for rank, item in enumerate(ranked[:3], 1))
                row["conditions"] = conditions
                row["status"] = ("prepared" if refs and ranked else
                                 "no_verified_reference" if not refs else
                                 "no_candidates")
                print(f"{relative} byte={offset}: {row['status']}; states={states}; candidates={len(ranked)}")
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                row.update(status="error", error=str(exc))
                print(f"{relative} byte={offset}: ERROR: {exc}", file=sys.stderr)
            rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = dict(Counter(row["status"] for row in rows))
    print("\nSummary:", json.dumps(counts))
    print("Saved:", output)
    print("No API calls made. Reference slice matches do not prove complete spans.")
    return 2 if any(row["status"] != "prepared" for row in rows) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
