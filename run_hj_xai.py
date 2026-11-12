#!/usr/bin/env python3
"""xAI generation runner for prepare_hj_benchmark.py JSONL (Python 3.9+).

New adapter for HJ retrieval cases and Monir-style guided/unguided experiments.
No third-party dependency for models/run/score. The optional --sacrebleu score
flag needs sacrebleu. No parser files are modified. API key is read only
from XAI_API_KEY and is never persisted. Run only on the intended account plan.

Scores are exploratory text similarity, NOT functional code correctness.
The preparer's reference end boundaries remain unverified; check before final
benchmark reporting. Best-of-three is an oracle metric, not an online selector.
"""

import argparse
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API = "https://api.x.ai/v1"


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_jsonl(path):
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def api_call(route, payload=None):
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise ValueError("XAI_API_KEY belum diatur di terminal ini.")
    request = Request(API + route,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json", "User-Agent": "HJ-Benchmark/1.0"})
    try:
        with urlopen(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        raise RuntimeError(f"xAI HTTP {exc.code}: {detail[:1800]}") from None
    except URLError as exc:
        raise RuntimeError(f"Koneksi xAI gagal: {str(exc.reason).replace(key, '[REDACTED]')}") from None


def models():
    return sorted(item["id"] for item in api_call("/models")["data"])


def tasks_for(cases, settings):
    tasks = []
    for case in cases:
        if case.get("status") != "prepared":
            raise ValueError(f"Kasus belum siap: {case.get('case_id')}")
        conditions = case["conditions"]
        baseline = next(c for c in conditions if c["condition"] == "without_guidance")
        guided = [c for c in conditions if c["condition"].startswith("guided_rank_")]
        if len(guided) != 3:
            raise ValueError("Pilot seimbang ini memerlukan tepat tiga kandidat per kasus.")
        selections = [(baseline, i) for i in range(1, 4)]
        selections += [(c, 1) for c in guided]
        for condition, repetition in selections:
            body = {"model": settings["model"],
                    "messages": [{"role": "user", "content": condition["prompt"]}],
                    "temperature": settings["temperature"],
                    "max_tokens": settings["max_completion_tokens"]}
            identity = {"case_id": case["case_id"], "condition": condition["condition"],
                        "repetition": repetition, "payload": body}
            tasks.append({"task_id": fingerprint(identity), **identity,
                          "cursor_byte": case["cursor_byte"],
                          "source_relative": case["source_relative"],
                          "references": case["references"],
                          "structure": condition.get("structure")})
    # Stable shuffled order reduces systematic time/order differences. It does
    # not make stochastic model outputs deterministic.
    random.Random(42).shuffle(tasks)
    return tasks


def run(args):
    source = args.input.expanduser().resolve()
    cases = read_jsonl(source)
    if not args.all_cases:
        cases = [c for c in cases if c.get("cursor_byte") == args.cursor]
        if len(cases) != 1:
            raise ValueError("Pilot harus memilih tepat satu kasus; gunakan JSONL satu file sumber.")
    if not cases:
        raise ValueError("Tidak ada kasus yang dipilih.")
    settings = {"provider": "xai", "model": args.model, "temperature": args.temperature,
                "max_completion_tokens": args.max_tokens}
    tasks = tasks_for(cases, settings)
    manifest = {"record_type": "manifest", "schema_version": 1,
                "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "settings": settings, "task_ids": [t["task_id"] for t in tasks]}
    output = args.output.expanduser().resolve()
    completed = set()
    if output.exists():
        if not args.resume:
            raise FileExistsError("Output sudah ada. Tambahkan --resume untuk melanjutkan.")
        old = read_jsonl(output)
        if not old or old[0] != manifest:
            raise ValueError("Input/model/pengaturan berbeda dari output lama; pilih nama output baru.")
        completed = {r["task_id"] for r in old[1:] if r.get("status") == "ok"}
    elif args.resume:
        raise FileNotFoundError("Output untuk --resume belum ada.")
    available = models()
    if args.model not in available:
        raise ValueError("Model tidak ada dalam daftar aktif. Jalankan subperintah models.")
    pending = [t for t in tasks if t["task_id"] not in completed]
    print(f"Kasus: {len(cases)} | Total generasi: {len(tasks)} | Tersisa: {len(pending)}", flush=True)
    print("Tiga tanpa panduan versus tiga berpanduan; model/pengaturan sama.", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a" if args.resume else "x", encoding="utf-8") as handle:
        if not args.resume:
            handle.write(json.dumps(manifest) + "\n")
            handle.flush()
        for index, task in enumerate(pending, 1):
            if index > 1:
                time.sleep(args.delay)
            record = {"record_type": "generation", **task, "created_utc": stamp()}
            print(f"[{index}/{len(pending)}] byte={task['cursor_byte']} {task['condition']} #{task['repetition']}", flush=True)
            start = time.monotonic()
            try:
                response = api_call("/chat/completions", task["payload"])
                choice = response["choices"][0]
                content = choice["message"].get("content") or ""
                status = "ok" if choice.get("finish_reason") == "stop" and content.strip() else "incomplete"
                record.update(status=status, response=response, content=content,
                              latency_seconds=time.monotonic() - start)
            except (RuntimeError, ValueError, KeyError, IndexError, OSError) as exc:
                record.update(status="error", error=str(exc),
                              latency_seconds=time.monotonic() - start)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            if record["status"] != "ok":
                print("Berhenti:", record.get("error", "Respons kosong/terpotong."), flush=True)
                print("Hasil sebelumnya sudah disimpan. Gunakan --resume setelah masalah teratasi.")
                return 2
            print("Jawaban:", record["content"], flush=True)
    print("Selesai. Hasil:", output)
    return 0


def score(args):
    records = read_jsonl(args.input)
    # Keep the last successful record for each task, ignoring failed attempts.
    latest = {r["task_id"]: r for r in records
              if r.get("record_type") == "generation" and r.get("status") == "ok"}
    if not records or records[0].get("record_type") != "manifest":
        raise ValueError("File hasil tidak mempunyai manifest.")
    expected = set(records[0]["task_ids"])
    if set(latest) != expected:
        raise ValueError("Generasi belum lengkap; lanjutkan run --resume sebelum menghitung skor.")
    bleu = None
    if args.sacrebleu:
        try:
            from sacrebleu.metrics import BLEU
            bleu = BLEU(effective_order=True, tokenize="13a")
        except ImportError:
            raise ValueError("Opsi ini memerlukan paket sacrebleu di environment Python aktif.") from None
    rows = []
    for record in latest.values():
        prediction = record["content"]  # No regex rewriting of code/operators.
        references = [r["text"] for r in record["references"]]
        if not references:
            raise ValueError("Referensi kosong.")
        row = {"case_id": record["case_id"], "cursor_byte": record["cursor_byte"],
               "condition": record["condition"], "repetition": record["repetition"],
               "model": record["payload"]["model"], "prediction": prediction,
               "exact_match_raw": int(prediction in references),
               "exact_match_strip": int(any(prediction.strip() == r.strip() for r in references)),
               "sequence_matcher_raw": max(SequenceMatcher(None, prediction, r).ratio() for r in references),
               "latency_seconds": record["latency_seconds"],
               "reference_status": "end_completeness_unverified_exploratory"}
        if bleu is not None:
            metric = bleu.sentence_score(prediction, references)
            row.update(sacrebleu_sentence_13a=metric.score,
                       sacrebleu_unigram_precision=metric.precisions[0])
        rows.append(row)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['condition']} #{row['repetition']}: SequenceMatcher={row['sequence_matcher_raw']:.3f}; exact_strip={row['exact_match_strip']}")
    print("Saved:", output)
    print("Skor eksplorasi; bukan akurasi fungsional dan belum hasil benchmark akhir.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("models", help="List model IDs; no code generation")
    r = sub.add_parser("run")
    r.add_argument("--input", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--model", required=True)
    selection = r.add_mutually_exclusive_group()
    selection.add_argument("--cursor", type=int, default=10)
    selection.add_argument("--all-cases", action="store_true")
    r.add_argument("--temperature", type=float, default=0.5)
    r.add_argument("--max-tokens", type=int, default=2048)
    r.add_argument("--delay", type=float, default=20)
    r.add_argument("--resume", action="store_true")
    s = sub.add_parser("score")
    s.add_argument("--input", type=Path, required=True)
    s.add_argument("--output", type=Path, required=True)
    s.add_argument("--sacrebleu", action="store_true")
    args = p.parse_args()
    if args.command == "models":
        print("\n".join(models()))
        return 0
    if args.command == "score":
        return score(args)
    if args.delay < 0 or args.max_tokens <= 0 or not 0 <= args.temperature <= 2:
        raise ValueError("Periksa delay, max-tokens, dan temperature.")
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
