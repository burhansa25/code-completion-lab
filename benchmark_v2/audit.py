"""Read-only RQ2 prediction audit; does not mutate historical scores or runs.

Normalization removes only recognisable Markdown wrappers. A trailing unmatched
fence is treated as a *tentative* formatting artifact and labelled separately.
This is a secondary diagnostic, never a substitute for frozen raw scoring.
"""
import csv
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import re


_COMPLETE_FENCE = re.compile(
    r"\A```(?:[A-Za-z0-9_+.-]+)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z"
)
_TRAILING_FENCE = re.compile(r"\A(?P<body>[\s\S]*?)\r?\n[ \t]*```[ \t]*\Z")


def normalize_prediction(prediction):
    """Return (code, reason); no reference-based clipping or code rewriting."""
    text = prediction.strip()
    complete = _COMPLETE_FENCE.fullmatch(text)
    if complete:
        return complete.group("body").strip(), "complete_fence"
    trailing = _TRAILING_FENCE.fullmatch(text)
    if trailing and "```" not in trailing.group("body"):
        return trailing.group("body").strip(), "unmatched_closing_fence_tentative"
    return text, "whitespace_only" if text != prediction else "unchanged"


def _read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _select_run(root, language, model, scope, run_id):
    model = {"gpt": "gpt4o"}.get(model, model)
    base = Path(root) / language / model / scope
    if run_id:
        match = base / run_id
        if not match.is_dir():
            raise ValueError(f"Run directory does not exist: {match}")
        return match
    runs = [folder for folder in base.iterdir() if folder.is_dir()] if base.is_dir() else []
    runs = [folder for folder in runs if list(folder.glob("files/*/scores.csv"))]
    if not runs:
        raise ValueError(f"No scored runs found under {base}")
    # Select most recently scored run; never merge incompatible run configurations.
    return max(runs, key=lambda folder: (max(p.stat().st_mtime for p in folder.glob("files/*/scores.csv")), folder.name))


def _load_predictions(run):
    cases = {}
    scores = []
    for file_dir in sorted((run / "files").iterdir()):
        if not file_dir.is_dir():
            continue
        case_path, score_path = file_dir / "cases.jsonl", file_dir / "scores.csv"
        if not score_path.is_file():
            continue
        if not case_path.is_file():
            raise ValueError(f"Cases missing for scores: {score_path}")
        for case in _read_jsonl(case_path):
            cid = case["case_id"]
            if cid in cases:
                raise ValueError(f"Duplicate case ID: {cid}")
            cases[cid] = case
        with score_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if row["case_id"] not in cases:
                raise ValueError(f"Scores refer to an unknown case: {row['case_id']}")
            scores.append(row)
    if not cases or not scores:
        raise ValueError(f"No complete cases/scores found in {run}")
    return cases, scores


def audit_run(root, language, model, scope="smoke", run_id=None,
              *, limit_cases=3, show_predictions=False, show_prompts=False):
    if limit_cases < 0:
        raise ValueError("--limit-cases must be zero or greater")
    run = _select_run(root, language, model, scope, run_id)
    cases, scores = _load_predictions(run)
    detail = defaultdict(list)
    aggregated = defaultdict(lambda: Counter())
    format_counts = Counter()
    for row in scores:
        case = cases[row["case_id"]]
        reference_texts = [ref["text"] for ref in case.get("references", [])]
        if not reference_texts:
            raise ValueError(f"Reference missing for {row['case_id']}")
        prediction = row["prediction"]
        normalized, reason = normalize_prediction(prediction)
        raw_exact = any(prediction == ref for ref in reference_texts)
        norm_exact = any(normalized == ref.strip() for ref in reference_texts)
        raw_seq = max(SequenceMatcher(None, prediction, ref).ratio() for ref in reference_texts)
        norm_seq = max(SequenceMatcher(None, normalized, ref.strip()).ratio() for ref in reference_texts)
        enriched = {"row": row, "normalized": normalized, "reason": reason,
                    "raw_exact": raw_exact, "norm_exact": norm_exact,
                    "raw_seq": raw_seq, "norm_seq": norm_seq}
        detail[row["case_id"]].append(enriched)
        stats = aggregated[row["condition"]]
        stats["n"] += 1
        stats["raw_exact"] += int(raw_exact)
        stats["norm_exact"] += int(norm_exact)
        stats["raw_seq_sum"] += raw_seq
        stats["norm_seq_sum"] += norm_seq
        format_counts[reason] += 1

    print(f"AUDIT (read-only) | {language} / {model} / {scope} | run_id={run.name}")
    print(f"Cases={len(cases)} | Predictions={len(scores)} | Normalization is secondary, not official scoring")
    print("Format categories:", ", ".join(f"{k}={v}" for k, v in sorted(format_counts.items())))
    print("\nCONDITION                 N   Raw exact   Normalized exact    Raw seq   Normalized seq")
    print("-" * 88)
    ordered = ["without_guidance", "guided_rank_1", "guided_rank_2", "guided_rank_3", "ideal_guidance"]
    for condition in sorted(aggregated, key=lambda item: (ordered.index(item) if item in ordered else len(ordered), item)):
        st = aggregated[condition]
        n = st["n"]
        print(f"{condition:<25} {n:>2}   {st['raw_exact']:>2}/{n:<2}         {st['norm_exact']:>2}/{n:<2}             "
              f"{100*st['raw_seq_sum']/n:>6.2f}%      {100*st['norm_seq_sum']/n:>6.2f}%")

    for case_id in list(cases)[:limit_cases]:
        case = cases[case_id]
        print(f"\nCASE {case_id} | source={case.get('source_relative')} | cursor_byte={case.get('cursor_byte')}")
        print(f"  prefix={case.get('prefix', '')!r}")
        for ref in case["references"]:
            print(f"  reference={ref.get('text')!r} | structure={ref.get('structure')!r} | "
                  f"validation={ref.get('validation', 'unverified')}")
        if case.get("conditions"):
            print("  guidance:", ", ".join(
                f"{cond['condition']}={cond.get('structure')!r}" for cond in case["conditions"]))
            if show_prompts:
                for cond in case["conditions"]:
                    print(f"  PROMPT {cond['condition']}:\n{cond.get('prompt', '')}")
        for item in sorted(detail[case_id], key=lambda x: (x['row']['condition'], int(x['row']['repetition']))):
            row = item["row"]
            print(f"  {row['condition']} rep={row['repetition']} | "
                  f"raw_exact={int(item['raw_exact'])} normalized_exact={int(item['norm_exact'])} "
                  f"format={item['reason']} | normalized_preview={item['normalized'][:70]!r}")
            if show_predictions:
                print(f"    RAW: {row['prediction']!r}\n    NORMALIZED: {item['normalized']!r}")
    if len(cases) > limit_cases:
        print(f"\n{len(cases)-limit_cases} additional cases hidden; use --limit-cases 0 to hide details or a larger number.")
    print("\nNOTE: An unmatched closing fence is ambiguous. Its removal is exploratory; raw data is unchanged.")
    print("NOTE: These metrics do not validate reference span completeness or code correctness.")
    return 0
