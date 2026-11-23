#!/usr/bin/env python3
"""Create Monir-style RQ2 summaries from code-completion-lab score CSVs.

Offline-only, no API calls. Works with the existing per-task score CSV schema.
This script does not modify experiments or reproduce Monir's historical scores.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import csv
import glob
import json
from pathlib import Path
from statistics import mean
import sys

CONDITIONS = ("WithoutGuide", "WithIdealGuide", "WithinTop3Guide", "WithTop1Guide")
METRICS = {
    "sacrebleu_unigram_precision": "SacreBLEU 1-gram precision (%)",
    "sequence_matcher_raw": "SequenceMatcher (%)",
    "sacrebleu_sentence_13a": "Sentence BLEU 13a (%)",
}


def paths(patterns):
    found = []
    for pattern in patterns:
        matched = sorted(Path(p) for p in glob.glob(str(pattern), recursive=True))
        if not matched and Path(pattern).is_file():
            matched = [Path(pattern)]
        if not matched:
            raise FileNotFoundError(f"No files match: {pattern}")
        found.extend(p for p in matched if p.is_file())
    return list(dict.fromkeys(p.resolve() for p in found))


def read_cases(files):
    cases = {}
    for path in files:
        with path.open(encoding="utf-8") as stream:
            for lineno, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                key = row.get("case_id")
                if not key:
                    raise ValueError(f"Missing case_id: {path}:{lineno}")
                if key in cases:
                    old = cases[key]
                    for field in ("source_sha256", "cursor_byte", "source_relative"):
                        if field in old and field in row and old[field] != row[field]:
                            raise ValueError(f"Inconsistent duplicate case_id: {key}")
                    if len(row.get("references", [])) > len(old.get("references", [])):
                        cases[key] = row
                else:
                    cases[key] = row
    return cases


def read_scores(files):
    scores = {}
    for path in files:
        with path.open(encoding="utf-8", newline="") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                try:
                    case_id = row["case_id"]
                    model = row["model"]
                    condition = row["condition"]
                except KeyError as exc:
                    raise ValueError(f"Missing score field {exc}: {path}") from exc
                if not (case_id and model and condition):
                    raise ValueError(f"Empty score identity: {path}:{line}")
                repetition = str(row.get("repetition", "1") or "1")
                key = (case_id, model, condition, repetition)
                if key in scores:
                    raise ValueError(f"Duplicate score task {key} in {path}:{line}")
                scores[key] = row
    return scores


def parsed_metric(row, key):
    value = row.get(key, "")
    if value is None or str(value).strip() == "":
        return None
    value = float(value)
    # SacreBLEU returns percentages [0,100]. SequenceMatcher returns a ratio [0,1].
    if key == "sequence_matcher_raw":
        if not 0 <= value <= 1:
            raise ValueError(f"Unexpected SequenceMatcher ratio outside [0,1]: {value}")
        value *= 100
    elif not 0 <= value <= 100:
        raise ValueError(f"Unexpected {key} outside [0,100]: {value}")
    return value


def reference_rank(case):
    ranks = [r.get("retrieval_rank") for r in case.get("references", [])]
    ranks = [r for r in ranks if type(r) is int and r >= 1]
    return min(ranks) if ranks else None


def source_id(case, score):
    return (case.get("source_relative") or score.get("source_relative")
            or case.get("source_path") or "UNKNOWN_SOURCE")


def normalize_condition(label):
    text = label.lower().strip()
    if text in {"without_guidance", "withoutguide", "unguided"}:
        return ("without", 0)
    if text in {"ideal_guidance", "withidealguide", "guided_ideal", "ideal"}:
        return ("ideal", 0)
    if text.startswith("guided_rank_") and text[12:].isdigit():
        return ("rank", int(text[12:]))
    if text.startswith("guided_rank") and text[11:].isdigit():
        return ("rank", int(text[11:]))
    raise ValueError(f"Unsupported condition: {label}")


def aggregate_case(case, score_rows, metric, min_ideal_reps):
    groups = defaultdict(list)
    for row in score_rows:
        value = parsed_metric(row, metric)
        if value is None:
            continue
        category, rank = normalize_condition(row["condition"])
        groups[(category, rank)].append(value)
    u = groups.get(("without", 0), [])
    i = groups.get(("ideal", 0), [])
    # Require exactly one score per rank for oracle, no silent partial top-3.
    top = [groups.get(("rank", rank), []) for rank in (1, 2, 3)]
    status = []
    if len(u) != 3:
        status.append(f"without={len(u)}/3")
    if len(i) < min_ideal_reps:
        status.append(f"ideal={len(i)}/{min_ideal_reps}")
    if any(len(x) != 1 for x in top):
        status.append("top3=incomplete")
    return {
        "WithoutGuide": mean(u) if len(u) == 3 else None,
        "WithIdealGuide": mean(i) if len(i) >= min_ideal_reps else None,
        "WithinTop3Guide": max(x[0] for x in top) if all(len(x) == 1 for x in top) else None,
        "WithTop1Guide": top[0][0] if len(top[0]) == 1 else None,
        "status": ";".join(status) or "complete",
    }


def save_csv(path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compute(cases, scores, *, min_ideal_reps=3):
    by_task = defaultdict(list)
    for (case_id, model, condition, rep), row in scores.items():
        by_task[(case_id, model)].append(row)
    by_case = []
    for (case_id, model), rows in sorted(by_task.items()):
        case = cases.get(case_id, {})
        lang = case.get("language") or rows[0].get("language") or "unknown"
        profile = case.get("prompt_profile") or rows[0].get("prompt_profile") or "unknown"
        base = {"case_id": case_id, "model": model,
                "language": lang, "prompt_profile": profile,
                "source_relative": source_id(case, rows[0]),
                "cursor_byte": case.get("cursor_byte", rows[0].get("cursor_byte", "")),
                "reference_rank": reference_rank(case) if case else "",
                "candidate_count": case.get("candidate_count", "")}
        for metric in METRICS:
            if not any(r.get(metric, "") not in (None, "") for r in rows):
                continue
            arm = aggregate_case(case, rows, metric, min_ideal_reps)
            by_case.append({**base, "metric": metric, **arm})
    # Micro: equal weight to each cursor. Macro: equal weight to source file.
    summarized = []
    by_lmp = defaultdict(list)
    for row in by_case:
        by_lmp[(row["language"], row["model"], row["prompt_profile"], row["metric"])].append(row)
    for (lang, model, profile, metric), items in sorted(by_lmp.items()):
        for cond in CONDITIONS:
            eligible = [r for r in items if r[cond] is not None]
            sources = defaultdict(list)
            for r in eligible:
                sources[r["source_relative"]].append(r[cond])
            summarized.append({"language": lang, "model": model, "prompt_profile": profile,
                               "metric": metric, "metric_label": METRICS[metric],
                               "condition": cond, "aggregation": "micro_cursor",
                               "score_pct": mean([r[cond] for r in eligible]) if eligible else "",
                               "n_cases": len(eligible), "n_files": len(sources)})
            summarized.append({"language": lang, "model": model, "prompt_profile": profile,
                               "metric": metric, "metric_label": METRICS[metric],
                               "condition": cond, "aggregation": "macro_file",
                               "score_pct": mean([mean(v) for v in sources.values()]) if sources else "",
                               "n_cases": len(eligible), "n_files": len(sources)})
            # Strict comparison: same case IDs present in all four arms.
            complete = [r for r in items if all(r[k] is not None for k in CONDITIONS)]
            full_sources = defaultdict(list)
            for r in complete:
                full_sources[r["source_relative"]].append(r[cond])
            summarized.append({"language": lang, "model": model, "prompt_profile": profile,
                               "metric": metric, "metric_label": METRICS[metric],
                               "condition": cond, "aggregation": "paired_micro",
                               "score_pct": mean([r[cond] for r in complete]) if complete else "",
                               "n_cases": len(complete), "n_files": len(full_sources)})
    return by_case, summarized


def retrieval_rows(cases):
    # Includes all provided rows; for unbiased retrieval coverage, pass PREPARED cases,
    # not only the filtered eligible/mapped cases.
    out = []
    for lang in sorted({c.get("language", "unknown") for c in cases.values()}):
        rows = [c for c in cases.values() if c.get("language", "unknown") == lang]
        verified = [c for c in rows if c.get("references")]
        total = len(verified)
        for k in (1, 3, 5, 10, 20):
            found = sum(reference_rank(c) is not None and reference_rank(c) <= k for c in verified)
            out.append({"language": lang, "top_k": k, "retrieved": found,
                        "n_verified_reference_positions": total,
                        "coverage_pct": 100 * found / total if total else "",
                        "n_all_positions": len(rows)})
    return out


def draw_graphs(summary, by_case, coverage, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("Install matplotlib to use --plots")
    plotdir = outdir / "figures"
    plotdir.mkdir(parents=True, exist_ok=True)
    by_group = defaultdict(dict)
    for row in summary:
        if row["aggregation"] != "micro_cursor" or row["score_pct"] == "":
            continue
        group = (row["language"], row["model"], row["prompt_profile"], row["metric"])
        by_group[group][row["condition"]] = float(row["score_pct"])
    for (lang, model, profile, metric), scores in by_group.items():
        names = [c for c in CONDITIONS if c in scores]
        if not names:
            continue
        fig, ax = plt.subplots(figsize=(9, 4.8))
        ax.bar(range(len(names)), [scores[n] for n in names])
        ax.set_xticks(range(len(names)), [n.replace("Guide", "\nGuide") for n in names])
        ax.set_ylabel(METRICS[metric])
        ax.set_ylim(0, max(10, min(100, max(scores.values()) * 1.2)))
        ax.set_title(f"RQ2 | {lang} | {model} | {profile}\n{METRICS[metric]}")
        for i, n in enumerate(names):
            ax.text(i, scores[n] + 0.4, f"{scores[n]:.2f}", ha="center", fontsize=9)
        fig.tight_layout()
        def safe(s):
            return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
        name = "_".join(safe(str(v)) for v in (lang, model, profile, metric))
        fig.savefig(plotdir / f"{name}.png", dpi=170)
        fig.savefig(plotdir / f"{name}.svg")
        plt.close(fig)
    # Reconstruct Monir-style greater/equal/lower figures, with counts per
    # candidate-list length. Limit chart range to 1..20; 20 includes 20+.
    comparisons = defaultdict(list)
    for row in by_case:
        if row["WithoutGuide"] is None:
            continue
        try:
            n = int(row["candidate_count"])
        except (ValueError, TypeError):
            continue
        if n < 1:
            continue
        keybase = (row["language"], row["model"], row["prompt_profile"], row["metric"])
        for condition in ("WithIdealGuide", "WithinTop3Guide", "WithTop1Guide"):
            if row[condition] is not None:
                comparisons[keybase + (condition,)].append((min(n, 20), row[condition] - row["WithoutGuide"]))
    for (lang, model, profile, metric, condition), rows in comparisons.items():
        counts = {k: [0, 0, 0] for k in range(1, 21)}
        for n, diff in rows:
            counts[n][0 if diff > 1e-10 else 1 if abs(diff) <= 1e-10 else 2] += 1
        fig, ax = plt.subplots(figsize=(9, 4.8))
        for column, label, marker in ((0, "Guided > unguided", "^"),
                                      (1, "Guided = unguided", "o"),
                                      (2, "Guided < unguided", "v")):
            ax.plot(range(1, 21), [counts[i][column] for i in range(1, 21)],
                    label=label, marker=marker, linewidth=1.5, markersize=4)
        ax.set_xlabel("Number of retrieved candidates (20 = 20+)")
        ax.set_ylabel("Number of evaluated cursor positions")
        ax.set_title(f"{lang} | {condition} vs WithoutGuide | {metric}")
        ax.legend()
        ax.grid(alpha=0.25)
        fig.tight_layout()
        def safe(s):
            return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s))
        name = "_".join(safe(v) for v in ("comparison", lang, model, profile, metric, condition))
        fig.savefig(plotdir / f"{name}.png", dpi=170)
        plt.close(fig)
    # Offline Top-K retrieval recall (does not involve any LLM calls).
    by_language = defaultdict(list)
    for row in coverage:
        if row["coverage_pct"] != "":
            by_language[row["language"]].append(row)
    for language, rows in by_language.items():
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot([x["top_k"] for x in rows], [x["coverage_pct"] for x in rows], marker="o")
        ax.set_xticks([1, 3, 5, 10, 20]); ax.set_ylim(0, 100)
        ax.set_ylabel("Reference structure in Top-K (%)")
        ax.set_xlabel("K")
        ax.set_title(f"HJ structural retrieval coverage | {language}")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(plotdir / f"retrieval_{language}.png", dpi=170)
        plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", nargs="+", default=[], help="JSONL paths/globs; use unfiltered prepared cases for retrieval coverage")
    p.add_argument("--retrieval-cases", nargs="+", default=[], help="Unfiltered prepared JSONL for unbiased Top-K coverage")
    p.add_argument("--scores", nargs="+", required=True, help="Score CSV paths/globs")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ideal-repetitions", type=int, choices=(1, 3), default=3)
    p.add_argument("--plots", action="store_true", help="Make figures after CSV evaluation")
    a = p.parse_args(argv)
    cases = read_cases(paths(a.cases)) if a.cases else {}
    scores = read_scores(paths(a.scores))
    if not scores:
        raise ValueError("No score rows")
    by_case, summary = compute(cases, scores, min_ideal_reps=a.ideal_repetitions)
    a.out.mkdir(parents=True, exist_ok=True)
    percase = ["case_id", "language", "model", "prompt_profile", "source_relative", "cursor_byte",
               "reference_rank", "candidate_count", "metric", *CONDITIONS, "status"]
    save_csv(a.out / "rq2_by_case.csv", by_case, percase)
    save_csv(a.out / "rq2_table.csv", summary,
             ["language", "model", "prompt_profile", "metric", "metric_label", "condition",
              "aggregation", "score_pct", "n_cases", "n_files"])
    retrieval_cases = read_cases(paths(a.retrieval_cases)) if a.retrieval_cases else cases
    coverage = retrieval_rows(retrieval_cases) if retrieval_cases else []
    if retrieval_cases:
        save_csv(a.out / "retrieval_coverage.csv", coverage,
                 ["language", "top_k", "retrieved", "n_verified_reference_positions", "coverage_pct", "n_all_positions"])
    if a.plots:
        draw_graphs(summary, by_case, coverage, a.out)
    missing = Counter(r["status"] for r in by_case)
    print("Saved:", a.out / "rq2_by_case.csv", "and", a.out / "rq2_table.csv")
    if retrieval_cases:
        print("Saved:", a.out / "retrieval_coverage.csv")
        if not a.retrieval_cases:
            print("CAUTION: Top-K denominator uses --cases; supply --retrieval-cases with all prepared positions for full coverage.")
    print("Completeness status by metric-case:", dict(missing))
    print("NOTE: WithinTop3Guide is an oracle max across three generated candidates.")
    print("NOTE: Monir's reported SacreBLEU precision corresponds to 1-gram precision, not full BLEU.")
    if not cases:
        print("WARNING: no cases supplied; language/source/profile cannot be verified.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(2)
