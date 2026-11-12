#!/usr/bin/env python3
"""Prepare and run the HJ C benchmark one report at a time in Ubuntu/WSL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from collections import Counter, defaultdict


PREFIX_SUFFIX = (
    "Fill the gap between the following C code prefix and suffix.\n"
    "Return only the code to insert in the gap. "
    "The prefix and suffix remain in the file. "
    "Do not repeat either part, including any punctuation "
    "that already starts the suffix. Do not include Markdown.\n\n"
)
MODELS = {
    "gpt4o": ("run_hj_openai.py", "gpt-4o", "OPENAI_API_KEY"),
    "grok": ("run_hj_xai.py", "grok-4.20-0309-non-reasoning", "XAI_API_KEY"),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_atomic(path, text):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def report_key(relative):
    return relative.replace("/", "_").replace("..", "") + ".json"


def required(path):
    if not path.is_file():
        raise ValueError(f"File tidak ditemukan: {path}")
    return str(path)


def make_infill(case, original, reference):
    start = reference["start_byte"]
    end = reference["end_byte_from_text"]
    prefix = original[:start].decode("utf-8")
    suffix = original[end:].decode("utf-8")
    if (
        start != case["cursor_byte"]
        or not 0 <= start < end <= len(original)
        or prefix != case["prefix"]
        or original[start:end].decode("utf-8") != reference["text"]
    ):
        raise ValueError(f"Rentang source tidak cocok: {case['case_id']}")
    base = PREFIX_SUFFIX + (
        f"PREFIX START\n{prefix}\nPREFIX END\n\n"
        f"SUFFIX START\n{suffix}\nSUFFIX END\n"
    )
    for condition in case["conditions"]:
        structure = condition.get("structure")
        condition["prompt"] = (
            base + f"\nRequired continuation structure:\n{structure}\n"
            if structure is not None
            else base + "\nProvide the next natural code continuation.\n"
        )
    case["prompt_profile"] = "c_prefix_suffix_v1"
    return case


def prepare(args, repo, batch):
    if (batch / "summary.json").exists():
        raise ValueError("Batch sudah disiapkan. Pilih --out baru untuk regenerasi.")
    base = args.hj_base.resolve()
    sources = args.source_dir.resolve()
    grammar = args.grammar.resolve()
    report_dir = (args.report_dir or base / "tree-sitter/reports/c").resolve()
    required(grammar)
    required(base / "tree-sitter/TreeSitterCutFile")
    required(base / "code-completion-extension/resources/c/candidates.json")
    reports = sorted(report_dir.glob("*.c.json"))
    if not reports or not sources.is_dir():
        raise ValueError(f"Periksa laporan di {report_dir} dan source di {sources}.")

    source_index = defaultdict(list)
    for path in sources.rglob("*.c"):
        if path.is_file():
            relative = path.relative_to(sources).as_posix()
            source_index[report_key(relative)].append(relative)

    batch.mkdir(parents=True, exist_ok=True)
    failures = []
    files = []
    totals = Counter()
    cutoffs = (1, 3, 5, 10, 20)
    preparer = required(repo / "prepare_hj_benchmark.py")

    for index, report in enumerate(reports, 1):
        expected = {int(offset) for offset in json.loads(report.read_bytes())}
        totals["report_positions"] += len(expected)
        choices = source_index.get(report.name, [])
        if len(choices) != 1:
            reason = f"pasangan source ditemukan: {len(choices)}"
            failures.append({"report": report.name, "reason": reason})
            print(f"[{index}/{len(reports)}] {report.name}: {reason}", flush=True)
            continue

        relative = choices[0]
        folder = batch / "files" / report.stem
        folder.mkdir(parents=True, exist_ok=True)
        prepared = folder / "prepared.jsonl"
        log_path = folder / "prepare.log"
        if not prepared.is_file():
            temporary = folder / "preparing.jsonl"
            temporary.unlink(missing_ok=True)
            with log_path.open("w", encoding="utf-8") as log:
                status = subprocess.run(
                    [
                        sys.executable, preparer, "--base", str(base),
                        "--language", "c", "--source-dir", str(sources),
                        "--grammar", str(grammar), "--reference-dir", str(report_dir),
                        "--file", relative, "--mode", "0", "--output", str(temporary),
                    ],
                    cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                ).returncode
            if status not in (0, 2) or not temporary.is_file():
                failures.append({"report": report.name, "reason": str(log_path)})
                print(f"[{index}/{len(reports)}] Persiapan gagal: {log_path}", flush=True)
                continue
            temporary.replace(prepared)

        rows = read_rows(prepared)
        if len(rows) != len(expected) or {r["cursor_byte"] for r in rows} != expected:
            raise ValueError(f"Daftar cursor tidak cocok: {prepared}")
        original = (sources / relative).read_bytes()
        source_hash = digest(original)
        report_hash = digest(report.read_bytes())
        if any(
            row["source_sha256"] != source_hash or row["reference_sha256"] != report_hash
            for row in rows
        ):
            raise ValueError(f"Cache memakai source/laporan berbeda: {prepared}")

        cases = []
        counts = Counter()
        for row in rows:
            refs = row.get("references", [])
            if refs and row["status"] in {"prepared", "no_candidates"}:
                counts["retrieval_evaluated"] += 1
                ranks = [
                    ref["retrieval_rank"] for ref in refs
                    if type(ref.get("retrieval_rank")) is int and ref["retrieval_rank"] > 0
                ]
                if ranks:
                    best = min(ranks)
                    counts["retrieval_found"] += 1
                    for cutoff in cutoffs:
                        counts[f"top_{cutoff}"] += best <= cutoff

            if row["status"] != "prepared":
                counts["excluded_" + row["status"]] += 1
            elif len(refs) != 1:
                counts["excluded_reference_count"] += 1
            elif len(row["top_candidates"]) != 3:
                counts["excluded_fewer_than_three_candidates"] += 1
            else:
                cases.append(make_infill(row, original, refs[0]))

        cases_path = folder / "cases.jsonl"
        payload = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)
        write_atomic(cases_path, payload)
        counts["llm_positions"] = len(cases)
        totals.update(counts)
        files.append({
            "report": report.name, "source": relative,
            "positions": len(expected), "counts": dict(counts),
            "cases": cases_path.relative_to(batch).as_posix(),
            "cases_sha256": digest(payload.encode("utf-8")),
        })
        print(
            f"[{index}/{len(reports)}] {relative}: "
            f"retrieval {counts['retrieval_evaluated']}/{len(expected)}, "
            f"LLM 3+3 {len(cases)}/{len(expected)}",
            flush=True,
        )

    summary = {
        "language": "c", "prompt_profile": "c_prefix_suffix_v1",
        "reports": len(reports), "files_prepared": len(files),
        "totals": dict(totals), "requests_per_model": 6 * totals["llm_positions"],
        "failures": failures, "files": files,
    }
    output = batch / ("summary.partial.json" if failures else "summary.json")
    write_atomic(output, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "files"}, indent=2))
    print(f"Ringkasan: {output}")
    return 2 if failures else 0


def run_batch(args, repo, batch):
    summary_path = batch / "summary.json"
    required(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["failures"] or summary["files_prepared"] != summary["reports"]:
        raise ValueError("Persiapan laporan C belum lengkap.")
    runner, model, key = MODELS[args.model]
    required(repo / runner)
    if not os.environ.get(key, "").strip():
        raise ValueError(f"Masukkan dan export {key} di terminal ini.")
    if args.delay < 0:
        raise ValueError("--delay harus nol atau lebih.")

    selected = [item for item in summary["files"] if item["counts"]["llm_positions"]]
    if args.limit_files:
        selected = selected[:args.limit_files]
    if not selected:
        raise ValueError("Tidak ada file C dengan posisi siap.")
    print(
        f"{model}: {len(selected)} file, "
        f"{6 * sum(item['counts']['llm_positions'] for item in selected)} "
        "task terjadwal termasuk task yang sudah selesai.",
        flush=True,
    )
    for index, item in enumerate(selected, 1):
        cases = batch / item["cases"]
        if digest(cases.read_bytes()) != item["cases_sha256"]:
            raise ValueError(f"Input batch berubah: {cases}")
        responses = cases.parent / f"{args.model}_responses.jsonl"
        scores = cases.parent / f"{args.model}_scores.csv"
        if scores.is_file():
            print(f"[{index}/{len(selected)}] Skor sudah ada: {scores}", flush=True)
            continue
        print(f"[{index}/{len(selected)}] {item['source']}", flush=True)
        command = [
            sys.executable, runner, "run", "--input", str(cases),
            "--output", str(responses), "--model", model, "--all-cases",
            "--temperature", "0.5", "--max-tokens", "512",
            "--delay", str(args.delay),
        ]
        if responses.exists():
            command.append("--resume")
        subprocess.run(command, cwd=repo, check=True)
        temporary = scores.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        subprocess.run(
            [
                sys.executable, runner, "score", "--input", str(responses),
                "--output", str(temporary), "--sacrebleu",
            ],
            cwd=repo, check=True,
        )
        temporary.replace(scores)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Persiapan lokal dan Top-N, tanpa API")
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--hj-base", type=Path,
                      default=Path.home() / "PL/codecompletion_benchmarks/smallbasic")
    prep.add_argument("--source-dir", type=Path,
                      default=Path.home() / "PL/codecompletion_benchmarks/c/TEST")
    prep.add_argument("--grammar", type=Path,
                      default=Path.home() / "PL/tree-sitter-c/c.so")
    prep.add_argument("--report-dir", type=Path)
    run = commands.add_parser("run", help="Generasi berbayar dan scoring per file")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--model", choices=MODELS, required=True)
    run.add_argument("--delay", type=float, default=0)
    run.add_argument("--limit-files", type=int)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent
    batch = args.out.expanduser().resolve()
    try:
        if args.command == "prepare":
            return prepare(args, repo, batch)
        if args.limit_files is not None and args.limit_files < 1:
            raise ValueError("--limit-files harus minimal 1.")
        return run_batch(args, repo, batch)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
