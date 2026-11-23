#!/usr/bin/env python3
"""Conservative, one-command HJ benchmark orchestration.

Drop this file and run_c.py / run_smallbasic.py in the EXISTING repository
root. Existing scientific scripts remain unchanged. Python 3.9+.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parent
MODELS = {
    "grok": ("run_hj_xai.py", "grok-4.20-0309-non-reasoning", "XAI_API_KEY"),
    "gpt4o": ("run_hj_openai_sb_resume.py", "gpt-4o", "OPENAI_API_KEY"),
}
C_RUNNERS = {"grok": "run_hj_xai.py", "gpt4o": "run_hj_openai.py"}


def legacy_python():
    """Prefer the repository venv even when the entrypoint used system Python."""
    venv = REPO / ".venv/bin/python"
    return venv if venv.is_file() else Path(sys.executable)


def c_batch_path(root: Path, explicit: Path | None):
    if explicit is not None:
        path = explicit.expanduser()
        return (path if path.is_absolute() else REPO / path).resolve()
    preferred = root / "c-prepared"
    old = root / "c-full-infill-v1"
    if not (preferred / "summary.json").is_file() and (old / "summary.json").is_file():
        print(f"Reuse existing legacy C batch: {old}", flush=True)
        return old
    return preferred


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def show_command(command):
    import shlex
    print("$ " + shlex.join([str(item) for item in command]), flush=True)


def execute(command, *, allowed=(0,), dry_run=False):
    command = [str(item) for item in command]
    if dry_run:
        show_command(command)
        return
    proc = subprocess.run(command, cwd=REPO, check=False)
    if proc.returncode not in allowed:
        raise RuntimeError(f"Exit code {proc.returncode}: {command[1] if len(command)>1 else command[0]}")


def require_files(*names):
    for name in names:
        if not (REPO / name).is_file():
            raise FileNotFoundError(f"Script belum ada di root repository: {name}")


def choose_first_case(path: Path):
    for case in read_jsonl(path):
        if case.get("status") == "prepared" and len(case.get("top_candidates", [])) == 3:
            return case
    raise ValueError(f"Tidak ada satu pun kasus eligible dalam {path}")


def validate_sb_rows(prepared: Path):
    rows = read_jsonl(prepared)
    if not rows:
        raise ValueError(f"Prepared JSONL kosong: {prepared}")
    errors = [row for row in rows if row.get("status") == "error"]
    if errors:
        bad = errors[0]
        raise ValueError(f"Preparation error: {bad.get('source_relative')}: {bad.get('error')}")
    eligible = [row for row in rows if row.get("status") == "prepared" and len(row.get("top_candidates", [])) == 3]
    if not eligible:
        raise ValueError("Tidak ada kasus prepared dengan 3 candidates.")
    print(f"Small Basic: {len(rows)} positions, {len(eligible)} eligible positions", flush=True)
    return eligible


def prepare_c(out: Path, assets: Path, dry_run: bool):
    require_files("run_hj_c_batch.py", "prepare_hj_benchmark.py")
    summary = out / "summary.json"
    partial = out / "summary.partial.json"
    if partial.exists() and not summary.exists():
        raise ValueError(f"Persiapan C sebelumnya memiliki failures: {partial}; perbaiki dahulu.")
    if summary.exists():
        print(f"Reuse C preparation: {summary}", flush=True)
        return
    command = [legacy_python(), REPO / "run_hj_c_batch.py", "prepare", "--out", out,
               "--hj-base", assets / "codecompletion_benchmarks/smallbasic",
               "--source-dir", assets / "codecompletion_benchmarks/c/TEST",
               "--grammar", assets / "tree-sitter-c/c.so"]
    execute(command, dry_run=dry_run)


def prepare_smallbasic(out: Path, assets: Path, scope: str, dry_run: bool):
    require_files("prepare_hj_benchmark.py", "map_hj_candidates.py", "hj_benchmark_tools.py")
    prepared = out / "prepared.jsonl"
    eligible = out / "eligible.jsonl"
    mapped = out / "cases_mapped.jsonl"
    if not prepared.exists():
        executable = assets / "codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe"
        command = [legacy_python(), REPO / "prepare_hj_benchmark.py",
                   "--language", "smallbasic", "--mode", "0", "--executable", executable,
                   "--base", assets / "codecompletion_benchmarks/smallbasic",
                   "--all" if scope == "full" else "--file"]
        if scope != "full":
            command.append("01_HelloWorld.sb")
        command.extend(["--output", prepared])
        execute(command, allowed=(0, 2), dry_run=dry_run)
    else:
        print(f"Reuse Small Basic preparation: {prepared}", flush=True)
    if dry_run and not prepared.exists():
        print("[dry-run] Eligible filtering + token mapping will follow preparation.")
        return
    positions = validate_sb_rows(prepared)
    if eligible.exists():
        if read_jsonl(eligible) != positions:
            raise ValueError(f"Existing eligible.jsonl differs from prepared.jsonl: {eligible}")
    elif not dry_run:
        eligible.parent.mkdir(parents=True, exist_ok=True)
        with eligible.open("x", encoding="utf-8") as stream:
            for row in positions:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if mapped.exists():
        mapped_rows = read_jsonl(mapped)
        if len(mapped_rows) != len(positions):
            raise ValueError("Mapped case count differs from eligible case count.")
        if not eligible.exists() or any(r.get("prompt_parent_sha256") != digest(eligible) for r in mapped_rows):
            raise ValueError("Mapped input is stale against eligible.jsonl; use a fresh output directory.")
        print(f"Reuse Small Basic mapping: {mapped}", flush=True)
    else:
        execute([legacy_python(), REPO / "map_hj_candidates.py", "--input", eligible,
                 "--output", mapped], dry_run=dry_run)


def api_key_env(model: str):
    key_name = MODELS[model][2]
    env = os.environ.copy()
    if not env.get(key_name, "").strip():
        if not sys.stdin.isatty():
            raise ValueError(f"Set {key_name} securely in environment for non-interactive runs.")
        value = getpass(f"{key_name} (hidden): ").strip()
        if not value:
            raise ValueError(f"{key_name} cannot be empty")
        # Only current Python process and child requests; never write key to files.
        os.environ[key_name] = value
    return key_name


def ask_full_consent(count: int, *, yes: bool, dry_run: bool):
    print(f"FULL benchmark: {count:,} scheduled API calls/model (before any resume).", flush=True)
    if dry_run:
        return
    if yes:
        return
    if not sys.stdin.isatty():
        raise ValueError("Non-interactive full run requires explicit --yes.")
    typed = input(f"Type RUN {count} to authorize the full batch: ").strip()
    if typed != f"RUN {count}":
        raise ValueError("Full benchmark cancelled. No API requests sent.")


def generate_and_score(*, path: Path, scope: str, model: str, language: str, output_dir: Path,
                       dry_run: bool):
    runner, model_id, _ = MODELS[model]
    if language == "c":
        runner = C_RUNNERS[model]
    require_files(runner)
    response_file = output_dir / f"{model}_responses.jsonl"
    scores_file = output_dir / f"{model}_scores.csv"
    if scores_file.exists():
        print(f"Existing scores (skipped): {scores_file}", flush=True)
        return
    if not dry_run:
        api_key_env(model)
    runner_path = REPO / runner
    run_cmd = [legacy_python(), runner_path, "run", "--input", path,
               "--output", response_file, "--model", model_id]
    if scope == "full":
        run_cmd.append("--all-cases")
    else:
        selected = choose_first_case(path)
        run_cmd.extend(["--cursor", str(selected["cursor_byte"])])
    run_cmd.extend(["--temperature", "0.5", "--max-tokens", "512", "--delay", "0"])
    if response_file.exists():
        run_cmd.append("--resume")
    execute(run_cmd, dry_run=dry_run)
    execute([legacy_python(), runner_path, "score", "--input", response_file,
             "--output", scores_file, "--sacrebleu"], dry_run=dry_run)
    print(f"Scores: {scores_file}")


def run_c(root: Path, batch: Path, scope: str, model: str, yes: bool, dry_run: bool):
    summary_path = batch / "summary.json"
    if not summary_path.exists():
        if dry_run:
            print("[dry-run] Prepare C first to obtain exact request counts.")
            return
        raise FileNotFoundError(f"C summary missing: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("failures") or summary.get("files_prepared") != summary.get("reports"):
        raise ValueError("C preparation is not complete.")
    files = [row for row in summary["files"] if row["counts"].get("llm_positions", 0)]
    if not files:
        raise ValueError("No eligible C cases.")
    if scope == "full":
        ask_full_consent(int(summary["requests_per_model"]), yes=yes, dry_run=dry_run)
        if not dry_run:
            api_key_env(model)
        require_files("run_hj_c_batch.py")
        execute([legacy_python(), REPO / "run_hj_c_batch.py", "run", "--out", batch,
                 "--model", model, "--delay", "0"], dry_run=dry_run)
        return
    first = files[0]
    cases = batch / first["cases"]
    if digest(cases) != first["cases_sha256"]:
        raise ValueError("C cases file modified since preparation; aborting.")
    print(f"C smoke: {first['source']}, 1 cursor, exactly 6 tasks.")
    generate_and_score(path=cases, scope="smoke", model=model,
                       language="c", output_dir=root / "c-smoke", dry_run=dry_run)


def run_smallbasic(out: Path, scope: str, model: str, yes: bool, dry_run: bool):
    mapped = out / f"sb-{scope}/cases_mapped.jsonl"
    if not mapped.is_file():
        if dry_run:
            print("[dry-run] Small Basic mapping must be created first.")
            return
        raise FileNotFoundError(f"Small Basic mapping missing: {mapped}")
    cases = read_jsonl(mapped)
    if not cases:
        raise ValueError("Small Basic mapping empty.")
    count = len(cases) * 6 if scope == "full" else 6
    if scope == "full":
        ask_full_consent(count, yes=yes, dry_run=dry_run)
    else:
        print("Small Basic smoke: 1 cursor, exactly 6 tasks.")
    generate_and_score(path=mapped, scope=scope, model=model,
                       language="smallbasic", output_dir=out / f"sb-{scope}", dry_run=dry_run)


def main(language: str, argv=None):
    if language not in {"c", "smallbasic"}:
        raise ValueError("Unknown language")
    parser = argparse.ArgumentParser(description=f"One-command {language} HJ benchmark (Python 3.9+)")
    parser.add_argument("--model", choices=MODELS, default="grok", help="Default: grok")
    parser.add_argument("--scope", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--stage", choices=("prepare", "run", "all"), default="all")
    parser.add_argument("--assets-root", type=Path, default=Path.home() / "PL")
    parser.add_argument("--output-root", type=Path, default=REPO / "results")
    if language == "c":
        parser.add_argument("--c-batch-dir", type=Path, help="Reuse an existing prepared C batch directory")
    parser.add_argument("--yes", action="store_true", help="Explicitly authorize full-batch API calls")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands; no paid API calls")
    args = parser.parse_args(argv)
    output_root = args.output_root.expanduser()
    if not output_root.is_absolute():
        output_root = REPO / output_root
    output_root = output_root.resolve()
    assets_root = args.assets_root.expanduser().resolve()
    batch = c_batch_path(output_root, args.c_batch_dir) if language == "c" else None
    if args.stage in {"prepare", "all"}:
        if language == "c":
            prepare_c(batch, assets_root, args.dry_run)
        else:
            prepare_smallbasic(output_root / f"sb-{args.scope}", assets_root, args.scope, args.dry_run)
    if args.stage in {"run", "all"}:
        if language == "c":
            run_c(output_root, batch, args.scope, args.model, args.yes, args.dry_run)
        else:
            run_smallbasic(output_root, args.scope, args.model, args.yes, args.dry_run)
    return 0
