"""Frozen v1-equivalent scoring with extra RQ2 condition support."""
import csv
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def score_records(records, *, require_bleu=True):
    bleu = None
    if require_bleu:
        from sacrebleu.metrics import BLEU
        bleu = BLEU(effective_order=True, tokenize="13a")
    scored = []
    for r in records:
        references = [x["text"] for x in r["references"]]
        if not references:
            raise ValueError("Reference text missing; cannot evaluate")
        pred = r["content"]
        row = {"case_id": r["case_id"], "cursor_byte": r["cursor_byte"],
               "condition": r["condition"], "repetition": r["repetition"],
               "model": r["payload"]["model"], "prediction": pred,
               "exact_match_raw": int(pred in references),
               "exact_match_strip": int(any(pred.strip() == x.strip() for x in references)),
               "sequence_matcher_raw": max(SequenceMatcher(None, pred, x).ratio() for x in references),
               "latency_seconds": r["latency_seconds"],
               "reference_status": "end_completeness_unverified_exploratory",
               "generation_status": r["status"]}
        if bleu:
            metric = bleu.sentence_score(pred, references)
            row["sacrebleu_sentence_13a"] = metric.score
            row["sacrebleu_unigram_precision"] = metric.precisions[0]
        scored.append(row)
    return scored


def write_csv(path, rows):
    if not rows:
        raise ValueError("No complete tasks to score")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
