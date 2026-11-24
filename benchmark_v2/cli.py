"""Two language entrypoints sharing one RQ2 generation/scoring engine.

Existing HJ preparation is reused; historical provider runners are NOT called.
C default is 10-line prefix-only. Small Basic uses frozen prompt-v2 mapping.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .cases import eligible, prepare_case, read_jsonl, sha_obj
from .engine import run_one_group
from .providers import MODELS, resolve_model
from .boundaries import text_kind

REPO = Path(__file__).resolve().parents[1]


def rel_case_name(s):
    # Hash avoids collisions when source names include same basename.
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def c_batch_dir(outroot, custom=None):
    if custom:
        return Path(custom).expanduser().resolve()
    for path in (REPO / "results/c-prepared", REPO / "results/c-full-infill-v1"):
        if (path / "summary.json").is_file():
            return path
    return REPO / "results/c-prepared"


def discover_sources(lang, scope, c_batch, sb_input):
    if lang == "smallbasic":
        # Full/pilot/study reuse the mapped 27 paired HJ Small Basic files.
        default = "sb-full" if scope in {"pilot", "study", "full"} else "sb-smoke"
        path = Path(sb_input).expanduser().resolve() if sb_input else REPO / f"results/{default}/cases_mapped.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"Mapped Small Basic cases missing: {path}. Run legacy preparation first.")
        return [("smallbasic", path)]
    summary = c_batch / "summary.json"
    if not summary.is_file():
        raise FileNotFoundError(f"C preparation summary missing: {summary}. Run legacy preparation first.")
    info = json.loads(summary.read_text(encoding="utf-8"))
    if info.get("failures") or info.get("files_prepared") != info.get("reports"):
        raise ValueError(f"C preparation incomplete: {summary}")
    items = []
    for item in info["files"]:
        if item["counts"].get("llm_positions", 0) <= 0:
            continue
        path = c_batch / item["cases"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["cases_sha256"]:
            raise ValueError(f"C prepared cases changed: {path}")
        items.append((item["source"], path))
    if not items:
        raise ValueError("No eligible C prepared files")
    return items


def select_cases(lang, sources, scope, cursor, top_k, *, include_byte0=False):
    if scope == "full":
        if cursor is not None:
            raise ValueError("--cursor cannot be combined with --scope full")
        return [(key, [c for c in read_jsonl(path) if eligible(c, top_k)]) for key, path in sources]
    candidates = []
    for key, path in sources:
        for case in read_jsonl(path):
            if eligible(case, top_k) and (cursor is None or case["cursor_byte"] == cursor):
                candidates.append((key, case))
        if candidates and cursor is None:
            # Prefer a meaningful nonzero cursor even if first file only has byte=0.
            nonzero = next((x for x in candidates if x[1]["cursor_byte"] > 0), None)
            if nonzero:
                return [(nonzero[0], [nonzero[1]])]
            # Check subsequent files for a nonzero cursor, before falling back.
    if not candidates:
        raise ValueError(f"No eligible cursor found (cursor={cursor})")
    if cursor is not None and len(candidates) > 1:
        raise ValueError("Cursor matches multiple source files. Use a more specific prepared input.")
    selected = next((x for x in candidates if x[1]["cursor_byte"] > 0), candidates[0])
    if selected[1]["cursor_byte"] == 0 and not include_byte0:
        raise ValueError("Only byte=0 available; pass --include-byte0 to allow exploratory smoke")
    return [(selected[0], [selected[1]])]


def verify_prepared_cases(path):
    path = Path(path)
    meta_path = path.parent / "preparation_meta.json"
    if not path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(f"Prepared cases/metadata missing: {path}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("cases_sha256"):
        raise ValueError(f"Prepared cases modified after preparation: {path}")
    return read_jsonl(path)


def safe_write_cases(path, cases, config):
    content = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    config_path = path.parent / "preparation_meta.json"
    metadata = {"config": config, "cases_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    if path.exists():
        if path.read_bytes() != content.encode("utf-8") or not config_path.is_file() or json.loads(config_path.read_text()) != metadata:
            raise ValueError(f"Prepared cases differ from frozen run directory; use new configuration/run ID: {path}")
        return
    path.write_text(content, encoding="utf-8")
    config_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def safe_write_cohort(path, groups, config):
    """Save a reproducible selection manifest; no target reference text is copied."""
    cohorts = []
    for name, items in groups:
        for case in items:
            ref = case["references"][0]
            cohorts.append({
                "source": name,
                "case_id": case["case_id"],
                "cursor_byte": case["cursor_byte"],
                "reference_kind_lexical": text_kind(ref["text"]),
                "reference_retrieval_rank": ref.get("retrieval_rank"),
            })
    manifest = {"selection_schema": 1,
                "method": "stratified_lexical_source_diversity_pre-registered" if config["scope"] == "pilot"
                          else "held_out_lexical_file_diversity" if config["scope"] == "study"
                          else config["scope"],
                "pilot_size": config.get("pilot_size"), "pilot_seed": config.get("pilot_seed"),
                "selected": cohorts,
                "selection_sha256": sha_obj(cohorts)}
    if config["scope"] == "study":
        manifest.update({
            "study_size": config["study_size"], "study_seed": config["study_seed"],
            "excluded_pilot_seed": 2026, "excluded_pilot_size": 6,
            "excluded_pilot_selection_sha256": config["excluded_pilot_selection_sha256"],
            "excluded_pilot_case_ids": config["excluded_pilot_case_ids"],
            "warning": "Balanced lexical/file-diverse cohort; not a probability sample."
        })
    content = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Selection manifest changed: {path}")
    else:
        path.write_text(content, encoding="utf-8")
    return manifest


def report_group(runroot, groups, *, ideal_reps, plots, retrieval_sources=()):
    reporter = REPO / "tools/rq2_report.py"
    if not reporter.is_file():
        print("RQ2 reporter missing: tools/rq2_report.py (skip charts)")
        return
    case_paths = [str(cases_path) for cases_path, dirname in groups if (dirname / "scores.csv").exists()]
    score_paths = [str(dirname / "scores.csv") for cases_path, dirname in groups if (dirname / "scores.csv").exists()]
    if not score_paths:
        return
    command = [sys.executable, str(reporter), "--cases", *case_paths, "--scores", *score_paths,
               "--out", str(runroot / "analysis"), "--ideal-repetitions", str(ideal_reps)]
    retrieved = [str(path) for path in retrieval_sources if Path(path).is_file()]
    if retrieved:
        command.extend(["--retrieval-cases", *retrieved])
    if plots:
        command.append("--plots")
    subprocess.run(command, check=True, cwd=REPO)


def main(language, argv=None):
    p = argparse.ArgumentParser(description="Code Completion Lab v2 - RQ2 3+3+3 experiment")
    p.add_argument("--model", default="grok", choices=[*MODELS, "gpt"])
    p.add_argument("--scope", choices=["smoke", "pilot", "study", "full"], default="smoke")
    p.add_argument("--stage", choices=["prepare", "run", "all", "report"], default="all")
    p.add_argument("--context", choices=["last10", "full"], default="last10")
    p.add_argument("--completion", choices=["prefix", "infill"], default="prefix")
    p.add_argument("--profile", choices=["fragment-v1", "lab", "monir-style"], default="fragment-v1")
    p.add_argument("--pilot-size", type=int, default=6, help="1-32 representative cursors (pilot scope only)")
    p.add_argument("--pilot-seed", type=int, default=2026, help="Reproducible selection across providers")
    p.add_argument("--study-size", type=int, default=30, help="1-120 held-out study cursors per language")
    p.add_argument("--study-seed", type=int, default=2027, help="Frozen study cohort seed")
    p.add_argument("--max-api-requests", type=int, default=200, help="Hard request budget; never exceed")
    p.add_argument("--top-k", choices=[3, 10, 20], type=int, default=3)
    p.add_argument("--ideal-repetitions", choices=[1, 3], type=int, default=3)
    p.add_argument("--cursor", type=int)
    p.add_argument("--include-byte0", action="store_true")
    p.add_argument("--temperature", type=float, default=0.5)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--delay", type=float, default=0)
    p.add_argument("--output-root", type=Path, default=REPO / "results/v2")
    p.add_argument("--c-batch-dir", type=Path)
    p.add_argument("--sb-input", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="Explicit authorization for pilot/study/full paid API calls")
    p.add_argument("--allow-topk-full", action="store_true")
    p.add_argument("--allow-unverified-fim", action="store_true")
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args(argv)
    if args.delay < 0 or args.max_tokens <= 0 or not 0 <= args.temperature <= 2:
        p.error("delay, max-tokens, or temperature outside supported range")
    if language == "smallbasic" and args.completion != "prefix":
        p.error("Small Basic v2 supports prefix-only")
    if args.completion == "infill" and args.context != "full":
        p.error("FIM requires --context full (keeps the historical C prompt)")
    if args.completion == "infill" and args.profile == "fragment-v1":
        p.error("Fragment-v1 protocol is prefix-only; choose --profile lab for historical FIM")
    if args.completion == "infill" and not args.allow_unverified_fim:
        p.error("FIM reference end completeness is unverified; explicitly pass --allow-unverified-fim")
    if args.scope == "full" and args.top_k > 3 and not args.allow_topk_full:
        p.error("Top-10/20 full run disabled until stratified experiment is planned: --allow-topk-full")
    if args.max_api_requests < 1:
        p.error("--max-api-requests must be positive")
    if args.scope in {"pilot", "study", "full"} and args.stage in {"run", "all"} and not args.yes:
        p.error("Pilot/full API runs require explicit --yes")
    if args.scope == "pilot" and not 1 <= args.pilot_size <= 32:
        p.error("--pilot-size must be 1-32")
    if args.scope == "study" and not 1 <= args.study_size <= 120:
        p.error("--study-size must be 1-120")
    if args.scope != "study" and (args.study_size != 30 or args.study_seed != 2027):
        p.error("--study-size and --study-seed require --scope study")
    if args.scope != "pilot" and (args.pilot_size != 6 or args.pilot_seed != 2026):
        p.error("--pilot-size and --pilot-seed require --scope pilot")
    model_name, model = resolve_model(args.model)
    sources = discover_sources(language, args.scope,
                               c_batch_dir(args.output_root, args.c_batch_dir), args.sb_input)
    pilot_groups = None
    if args.scope == "pilot":
        if args.cursor is not None:
            p.error("--cursor is not compatible with --scope pilot")
        from .pilot import stratified_pilot
        groups = stratified_pilot(language, sources, size=args.pilot_size,
                                  seed=args.pilot_seed, top_k=args.top_k)
    elif args.scope == "study":
        if args.cursor is not None:
            p.error("--cursor is not compatible with --scope study")
        if args.profile != "fragment-v1" or args.completion != "prefix" or args.top_k != 3:
            p.error("Frozen study cohort requires --profile fragment-v1 --completion prefix --top-k 3")
        from .pilot import stratified_study
        groups, pilot_groups = stratified_study(language, sources,
                                               size=args.study_size, seed=args.study_seed,
                                               top_k=args.top_k)
    else:
        groups = select_cases(language, sources, args.scope, args.cursor, args.top_k,
                              include_byte0=args.include_byte0)
    config = {"language": language, "scope": args.scope, "profile": args.profile,
              "context": args.context if language == "c" else "full", "completion": args.completion,
              "top_k": args.top_k, "ideal_repetitions": args.ideal_repetitions,
              "model": model_name, "model_id": model["id"], "temperature": args.temperature,
              "max_tokens": args.max_tokens, "delay": args.delay,
              "cursor_selection": args.cursor,
              "pilot_size": args.pilot_size if args.scope == "pilot" else None,
              "pilot_seed": args.pilot_seed if args.scope == "pilot" else None,
              "schema": "v2-rq2-2"}
    if pilot_groups is not None:
        # Hash the same manifest columns and order used by frozen pilot results.
        excluded_rows = [{"source": name, "case_id": c["case_id"],
                          "cursor_byte": c["cursor_byte"],
                          "reference_kind_lexical": text_kind(c["references"][0]["text"]),
                          "reference_retrieval_rank": c["references"][0].get("retrieval_rank")}
                         for name, items in pilot_groups for c in items]
        config.update({"study_size": args.study_size, "study_seed": args.study_seed,
                       "excluded_pilot_selection_sha256": sha_obj(excluded_rows),
                       "excluded_pilot_case_ids": [c["case_id"] for row, items in pilot_groups for c in items]})
    # Keep experiment runs isolated across implementation revisions. Do not
    # mix generated outputs from different prompt/evaluation source versions.
    code_names = ("benchmark_v2/cases.py", "benchmark_v2/cli.py",
                  "benchmark_v2/engine.py", "benchmark_v2/providers.py",
                  "benchmark_v2/pilot.py",
                  "benchmark_v2/scoring.py", "hj_benchmark_tools.py",
                  "map_hj_candidates.py", "run_hj_c_batch.py")
    code_hashes = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                   for name in code_names if (REPO / name).is_file()}
    config["source_hashes"] = code_hashes
    run_id = sha_obj({"config": config, "inputs": [(name, [c["case_id"] for c in items]) for name, items in groups]})[:16]
    root = args.output_root.expanduser().resolve() / language / model_name / args.scope / run_id
    prepared = []
    total = 0
    for source_name, old_cases in groups:
        if not old_cases:
            continue
        dirname = root / "files" / rel_case_name(source_name)
        case_path = dirname / "cases.jsonl"
        if args.stage in {"prepare", "all"}:
            cases = [prepare_case(case, top_k=args.top_k, context=args.context,
                                  completion=args.completion, profile=args.profile,
                                  ideal_repetitions=args.ideal_repetitions) for case in old_cases]
            if not args.dry_run:
                safe_write_cases(case_path, cases, {**config, "source": source_name})
            total += sum(c["planned_api_requests"] for c in cases)
        else:
            if not case_path.exists():
                raise FileNotFoundError(f"Run --stage prepare first: {case_path}")
            total += sum(c["planned_api_requests"] for c in verify_prepared_cases(case_path))
        prepared.append((case_path, dirname))
    if not prepared:
        raise ValueError("No eligible prepared cases")
    if args.scope in {"pilot", "study"} and not args.dry_run:
        manifest = safe_write_cohort(root / "cohort.json", groups, config)
        print(f"{args.scope.title()} selection frozen: {len(manifest['selected'])} cursors | "
              f"SHA-256 {manifest['selection_sha256'][:16]} | {root / 'cohort.json'}")
    print(f"Run ID {run_id} | {language} | {model_name} | {args.scope} | {len(prepared)} files | {total:,} planned API calls")
    print(f"Output: {root}")
    if args.dry_run:
        print("DRY RUN: no API requests; no new results are written.")
        return 0
    if args.stage in {"run", "all"} and total > args.max_api_requests:
        p.error(f"Planned {total:,} API requests exceed --max-api-requests={args.max_api_requests:,}. "
                "Increase the limit explicitly only after reviewing cost.")
    if args.stage == "prepare":
        return 0
    if args.stage in {"all", "run"}:
        if not args.yes and args.scope != "smoke":
            if not sys.stdin.isatty():
                raise ValueError("Use --yes for noninteractive API runs")
            typed = input(f"Authorize up to {total} paid API requests? Type RUN {total}: ").strip()
            if typed != f"RUN {total}":
                raise ValueError("Run cancelled; no new API requests sent")
        for cases_path, dirname in prepared:
            run_one_group(dirname, cases_path, model, config, yes=True)
    if args.stage in {"all", "run", "report"}:
        # Unfiltered HJ preparation is the correct denominator for Top-K
        # retrieval recall; do NOT use the 3-candidate/ideal-eligible cohort.
        retrieval_sources = [src.parent / "prepared.jsonl" for _, src in sources]
        report_group(root, prepared, ideal_reps=args.ideal_repetitions,
                     plots=not args.no_plots, retrieval_sources=retrieval_sources)
    return 0
