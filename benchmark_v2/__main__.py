"""Read-only results entrypoint: python -m benchmark_v2 results.

No provider imports, network connections, writes, or API calls.
"""
import argparse
import csv
import math
from pathlib import Path

CONDITIONS = ("WithoutGuide", "WithIdealGuide", "WithinTop3Guide", "WithTop1Guide")
METRICS = {
    "unigram": ("sacrebleu_unigram_precision", "SacreBLEU 1-gram precision (%)"),
    "sequence": ("sequence_matcher_raw", "SequenceMatcher (%)"),
    "bleu": ("sacrebleu_sentence_13a", "Sentence BLEU 13a (%)"),
}
AGGREGATIONS = ("micro_cursor", "macro_file", "paired_micro")


def load_report(path, aggregation):
    """Return score rows indexed by (metric, condition), ignoring other aggregations."""
    data = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"metric", "condition", "aggregation", "score_pct", "n_cases", "n_files"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Missing columns: " + ", ".join(sorted(required - set(reader.fieldnames or []))))
        for row in reader:
            if row["aggregation"] != aggregation:
                continue
            key = (row["metric"], row["condition"])
            if key in data:
                raise ValueError(f"Duplicate metric/condition: {key}")
            value = float(row["score_pct"]) if row["score_pct"] else None
            if value is not None and not math.isfinite(value):
                raise ValueError(f"Non-finite score: {key}")
            data[key] = (value, row["n_cases"], row["n_files"])
    return data


def discover(root, aggregation, scope, all_runs):
    """Pick the most recently modified report for each language/model/scope/profile."""
    candidates = {}
    for report in root.glob("*/*/*/*/analysis/rq2_table.csv"):
        run = report.parent.parent
        run_scope, model, language = run.parent.name, run.parent.parent.name, run.parent.parent.parent.name
        if scope != "all" and run_scope != scope:
            continue
        try:
            rows = load_report(report, aggregation)
        except (ValueError, OSError) as exc:
            print(f"WARNING: skipping invalid report {report}: {exc}")
            continue
        if not rows:
            continue
        # Keep prompt profiles distinct: they are different experiments.
        with report.open(encoding="utf-8-sig", newline="") as fh:
            meta = next(csv.DictReader(fh), {})
        profile = meta.get("prompt_profile", "unknown")
        key = (run_scope, language, model, profile)
        candidates.setdefault(key, []).append((report.stat().st_mtime, run.name, report, rows))
    selected = []
    omitted = 0
    for key, matches in sorted(candidates.items()):
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen = matches if all_runs else matches[:1]
        omitted += len(matches) - len(chosen)
        for _, run_id, path, rows in chosen:
            selected.append((key, run_id, path, rows))
    return selected, omitted


def format_pct(entry):
    if entry is None or entry[0] is None:
        return "—"
    return f"{entry[0]:.2f}"


def print_results(root, metric, aggregation, scope, all_runs, paths=False):
    reports, omitted = discover(root, aggregation, scope, all_runs)
    if not reports:
        print(f"No RQ2 reports found under {root.resolve()}. Finish --stage report first.")
        return 1
    wanted = list(METRICS) if metric == "all" else [metric]
    for metric_name in wanted:
        metric_key, label = METRICS[metric_name]
        print(f"\n{label}  |  aggregation={aggregation}")
        print(f"{'Language':<12} {'Model':<9} {'Scope':<6} {'N':>5} "
              f"{'Without':>9} {'Ideal':>9} {'Top3*':>9} {'Top1':>9}  Run ID")
        print("-" * 95)
        for (run_scope, language, model, profile), run_id, path, rows in reports:
            scores = [rows.get((metric_key, condition)) for condition in CONDITIONS]
            if not any(item is not None for item in scores):
                continue
            counts = {item[1] for item in scores if item is not None}
            n_cases = next(iter(counts)) if len(counts) == 1 else "varies"
            print(f"{language:<12} {model:<9} {run_scope:<6} {n_cases:>5} "
                  + " ".join(f"{format_pct(item):>9}" for item in scores)
                  + f"  {run_id}")
            if paths:
                print(f"  profile={profile} | {path}")
    print("\n* Top3 = oracle best-of-three guided predictions (uses reference scores).")
    if omitted:
        print(f"Showing latest report per language/model/scope/prompt profile; {omitted} older run(s) hidden. Use --all-runs.")
    if all(key[0] == "smoke" for key, *_ in reports):
        print("NOTE: Smoke-test results are exploratory, not full-benchmark conclusions.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m benchmark_v2", description="RQ2 benchmark utilities")
    sub = p.add_subparsers(dest="command", required=True)
    res = sub.add_parser("results", help="Show saved RQ2 scores without running any API")
    res.add_argument("--root", type=Path, default=Path("results/v2"))
    res.add_argument("--metric", choices=[*METRICS, "all"], default="all")
    res.add_argument("--aggregation", choices=AGGREGATIONS, default="micro_cursor")
    res.add_argument("--scope", choices=["smoke", "pilot", "study", "full", "all"], default="all")
    res.add_argument("--all-runs", action="store_true", help="Include superseded runs")
    res.add_argument("--paths", action="store_true", help="Print source CSV paths and prompt profiles")
    audit = sub.add_parser("audit", help="Inspect saved predictions and normalize Markdown for secondary analysis")
    audit.add_argument("--root", type=Path, default=Path("results/v2"))
    audit.add_argument("--language", choices=["c", "smallbasic", "all"], required=True)
    audit.add_argument("--model", required=True, help="Provider alias: grok, gpt or gpt4o")
    audit.add_argument("--scope", choices=["smoke", "pilot", "study", "full"], default="smoke")
    audit.add_argument("--run-id", help="Select an exact existing run ID")
    audit.add_argument("--limit-cases", type=int, default=3)
    audit.add_argument("--show-predictions", action="store_true")
    audit.add_argument("--show-prompts", action="store_true")
    stats = sub.add_parser("stats", help="Read-only paired Top-1 effect with file-cluster bootstrap CI")
    stats.add_argument("--root", type=Path, default=Path("results/v2"))
    stats.add_argument("--scope", choices=["pilot", "study", "full"], default="study")
    stats.add_argument("--metric", choices=["unigram", "sequence", "bleu", "all"], default="all")
    stats.add_argument("--language", choices=["c", "smallbasic", "all"], default="all")
    stats.add_argument("--model", choices=["grok", "gpt", "gpt4o", "all"], default="all")
    stats.add_argument("--bootstrap", type=int, default=5000)
    stats.add_argument("--seed", type=int, default=2027)
    stats.add_argument("--all-runs", action="store_true")
    stats.add_argument("--export", type=Path, help="Optional new CSV path (never overwrites)")
    boundaries = sub.add_parser("boundaries", help="Audit HJ reference provenance and span boundaries without LLM calls")
    boundaries.add_argument("--language", choices=["c", "smallbasic", "all"], required=True)
    boundaries.add_argument("--input", type=Path, action="append", default=[],
                            help="Prepared JSONL file or directory; repeatable")
    boundaries.add_argument("--root", type=Path, default=Path("results"),
                            help="Base results path used for automatic discovery")
    boundaries.add_argument("--limit", type=int, default=8,
                            help="How many example rows to print (0 hides details)")
    boundaries.add_argument("--export", type=Path, help="Optional new CSV path (never overwrites)")
    args = p.parse_args(argv)
    if args.command == "results":
        return print_results(args.root, args.metric, args.aggregation, args.scope, args.all_runs, args.paths)
    if args.command == "stats":
        from .stats import collect_stats, print_stats, export_stats
        try:
            rows = collect_stats(args.root, scope=args.scope, metric=args.metric,
                                 bootstrap=args.bootstrap, seed=args.seed,
                                 language=args.language, model=args.model,
                                 all_runs=args.all_runs)
            print_stats(rows)
            if args.export:
                export_stats(rows, args.export)
                print(f"Saved: {args.export}")
            return 0
        except (OSError, ValueError, KeyError) as exc:
            p.error(str(exc))
    if args.command == "audit":
        from .audit import audit_run
        languages = ["c", "smallbasic"] if args.language == "all" else [args.language]
        models = ["grok", "gpt"] if args.model == "all" else [args.model]
        if args.run_id and len(languages) * len(models) != 1:
            p.error("--run-id requires a single language and model")
        status = 0
        for language in languages:
            for model in models:
                try:
                    status = max(status, audit_run(args.root, language, model, args.scope, args.run_id,
                                                   limit_cases=args.limit_cases,
                                                   show_predictions=args.show_predictions,
                                                   show_prompts=args.show_prompts))
                except (OSError, ValueError, KeyError) as exc:
                    if len(languages) * len(models) == 1:
                        p.error(str(exc))
                    print(f"WARNING: audit unavailable for {language}/{model}: {exc}")
                    status = 1
        return status
    if args.command == "boundaries":
        from .boundaries import audit_prepared
        languages = ["c", "smallbasic"] if args.language == "all" else [args.language]
        if len(languages) > 1 and (args.input or args.export):
            p.error("For --language all, omit --input and --export; run each language separately to override paths/export")
        status = 0
        for language in languages:
            try:
                status = max(status, audit_prepared(language, args.input, root=args.root,
                                                    limit=args.limit, export=args.export))
            except (OSError, ValueError, KeyError) as exc:
                if len(languages) == 1:
                    p.error(str(exc))
                print(f"WARNING: boundaries unavailable for {language}: {exc}")
                status = 1
        return status
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
