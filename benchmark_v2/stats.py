"""Read-only paired Top-1 vs unguided statistics from saved RQ2 per-case CSVs.

The cursor is the paired unit. Source files are bootstrap clusters. Repetitions
have already been aggregated at the case level by the RQ2 reporter. This is a
conditional interval for a deliberately selected cohort, not population inference.
"""
from collections import defaultdict
import csv
import hashlib
import math
from pathlib import Path
import random
from statistics import mean

METRICS = {
    "unigram": "sacrebleu_unigram_precision",
    "sequence": "sequence_matcher_raw",
    "bleu": "sacrebleu_sentence_13a",
}


def paired_rows(path, metric):
    """Load one observation per cursor; reject incomplete or corrupt RQ2 cases."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "source_relative", "status", "metric", "WithoutGuide", "WithTop1Guide"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing paired case fields in {path}: {sorted(required - set(reader.fieldnames or []))}")
        selected = [r for r in reader if r["metric"] == metric]
    if not selected:
        raise ValueError(f"Metric {metric} not found: {path}")
    seen = set()
    result = []
    for row in selected:
        ident = row["case_id"]
        if not ident or ident in seen:
            raise ValueError(f"Missing or duplicate case_id in {path}: {ident!r}")
        seen.add(ident)
        if row["status"] != "complete" or not row["source_relative"]:
            raise ValueError(f"Incomplete or missing source identity for {ident} in {path}")
        without = float(row["WithoutGuide"])
        top1 = float(row["WithTop1Guide"])
        if not (math.isfinite(without) and math.isfinite(top1)
                and 0 <= without <= 100 and 0 <= top1 <= 100):
            raise ValueError(f"Non-finite or out-of-range score for {ident}")
        result.append((row["source_relative"], ident, top1 - without))
    return result


def paired_summary(rows, *, bootstrap=5000, seed=2027):
    """Source-cluster bootstrap of mean cursor-level paired difference (pp)."""
    if bootstrap < 100:
        raise ValueError("--bootstrap must be >= 100")
    clusters = defaultdict(list)
    for source, ident, delta in rows:
        clusters[source].append(delta)
    if len(clusters) < 2:
        raise ValueError("Paired bootstrap needs >= 2 source files")
    ordered = [clusters[k] for k in sorted(clusters)]
    diffs = [v for cluster in ordered for v in cluster]
    rng = random.Random(seed)
    simulations = []
    for _ in range(bootstrap):
        drawn = [rng.choice(ordered) for _ in ordered]
        simulations.append(sum(sum(cluster) for cluster in drawn) / sum(len(cluster) for cluster in drawn))
    simulations.sort()
    lo = simulations[math.floor(0.025 * bootstrap)]
    hi = simulations[math.ceil(0.975 * bootstrap) - 1]
    tol = 1e-10
    return {"cursor_count": len(rows), "source_count": len(ordered),
            "mean_delta_pp": mean(diffs), "ci95_low_pp": lo, "ci95_high_pp": hi,
            "wins": sum(x > tol for x in diffs),
            "losses": sum(x < -tol for x in diffs),
            "ties": sum(abs(x) <= tol for x in diffs)}


def collect_stats(root, *, scope="study", metric="all", bootstrap=5000, seed=2027,
                  language="all", model="all", all_runs=False):
    from .__main__ import discover
    entries, _ = discover(Path(root), "micro_cursor", scope, all_runs)
    desired = METRICS if metric == "all" else {metric: METRICS[metric]}
    summary = []
    for (run_scope, lang, mdl, profile), run_id, table_path, report in entries:
        if language != "all" and lang != language:
            continue
        if model != "all" and mdl != ("gpt4o" if model == "gpt" else model):
            continue
        path = table_path.with_name("rq2_by_case.csv")
        if not path.is_file():
            raise FileNotFoundError(f"Missing per-case RQ2 data: {path}")
        for label, code in desired.items():
            paired = paired_rows(path, code)
            # Different model cohorts can share run selections; separate RNG streams
            # while keeping bootstrap results stable across filesystem discovery order.
            tag = f"{run_scope}|{lang}|{mdl}|{profile}|{run_id}|{code}"
            private_seed = seed ^ int(hashlib.sha256(tag.encode()).hexdigest()[:12], 16)
            stats = paired_summary(paired, bootstrap=bootstrap, seed=private_seed)
            summary.append({"scope": run_scope, "language": lang, "model": mdl,
                            "run_id": run_id, "prompt_profile": profile, "metric": code,
                            **stats})
    if not summary:
        raise ValueError(f"No completed {scope} reports found under {root}")
    return summary


def print_stats(rows):
    print("Paired Top-1 minus Without Guidance | source-cluster bootstrap, 95% percentile CI")
    print("Language     Model   Metric                      N  Files   Delta (pp)       95% CI (pp)       W/L/T")
    print("-" * 113)
    for row in rows:
        print(f"{row['language']:<12} {row['model']:<7} {row['metric']:<26} "
              f"{row['cursor_count']:>3} {row['source_count']:>6} "
              f"{row['mean_delta_pp']:+11.2f} "
              f"[{row['ci95_low_pp']:+7.2f}, {row['ci95_high_pp']:+7.2f}] "
              f"{row['wins']:>2}/{row['losses']:>2}/{row['ties']:>2}")
    print("NOTE: The 3 unguided repetitions are averaged per cursor; they are not independent samples.")
    print("NOTE: Bootstrap intervals are conditional on this selected cohort; no population generalization is claimed.")


def export_stats(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
