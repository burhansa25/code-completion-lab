"""Resumable task scheduler and strict output-integrity verification."""
from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

from .cases import canonical, hash_bytes, read_jsonl, sha_obj
from .providers import payload_for, call_api
from .scoring import score_records, write_csv, sha_file

SUCCESS = {"ok", "empty_completion"}
SCHEMA = 2


def fingerprint_task(case, cond, rep, body):
    return sha_obj({"case_id": case["case_id"], "condition": cond["condition"],
                   "repetition": rep, "payload": body, "prompt_profile": case["prompt_profile"],
                   "experiment_version": case["experiment_version"]})


def build_tasks(cases, model, *, temperature, max_tokens):
    tasks = []
    for case in cases:
        for cond in case["conditions"]:
            body = payload_for(model, cond["prompt"], temperature, max_tokens)
            for rep in range(1, int(cond["repetitions"]) + 1):
                tid = fingerprint_task(case, cond, rep, body)
                tasks.append({"task_id": tid, "case_id": case["case_id"],
                              "cursor_byte": case["cursor_byte"], "source_relative": case["source_relative"],
                              "condition": cond["condition"], "repetition": rep,
                              "prompt_profile": case["prompt_profile"],
                              "references": case["references"], "structure": cond["structure"],
                              "payload": body})
    ids = [x["task_id"] for x in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task_id generated")
    random.Random(42).shuffle(tasks)
    return tasks


def ensure_key(model):
    key_name = model["key_env"]
    if os.environ.get(key_name, "").strip():
        return
    if not sys.stdin.isatty():
        raise ValueError(f"Set {key_name} in environment (noninteractive mode)")
    key = getpass(f"{key_name} (hidden): ").strip()
    if not key:
        raise ValueError(f"Missing {key_name}")
    os.environ[key_name] = key


def read_manifest(run_dir):
    path = run_dir / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_immutable_manifest(run_dir, manifest):
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    if path.exists():
        existing = read_manifest(run_dir)
        if existing != manifest:
            raise ValueError(f"Manifest mismatch; refuse to resume changed experiment: {path}")
    else:
        with path.open("x", encoding="utf-8") as f:
            f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def read_success(run_dir, manifest):
    path = run_dir / "responses.jsonl"
    successes = {}
    if not path.exists():
        return successes
    for record in read_jsonl(path):
        if record.get("record_type") != "generation":
            raise ValueError("Unexpected response record type")
        if record.get("task_id") not in manifest["task_ids"]:
            raise ValueError("Response task not in immutable manifest")
        if record.get("status") in SUCCESS:
            prev = successes.get(record["task_id"])
            if prev is not None and canonical(prev) != canonical(record):
                raise ValueError(f"Multiple successful records for task {record['task_id']}")
            successes[record["task_id"]] = record
    return successes


def run_one_group(run_dir, cases_path, model, config, *, dry_run=False,
                  require_consent=False, yes=False, api_fn=call_api):
    run_dir = Path(run_dir)
    cases_path = Path(cases_path)
    cases = read_jsonl(cases_path)
    if not cases:
        raise ValueError(f"Empty cases: {cases_path}")
    tasks = build_tasks(cases, model, temperature=config["temperature"], max_tokens=config["max_tokens"])
    manifest = {"schema_version": SCHEMA, "engine": "code-completion-v2",
                "cases_sha256": sha_file(cases_path),
                "settings": config, "model": model, "task_ids": [x["task_id"] for x in tasks]}
    if dry_run:
        print(f"DRY-RUN: {run_dir} | {len(cases)} positions, {len(tasks)} scheduled requests")
        return {"scheduled": len(tasks), "complete": 0, "dry_run": True}
    if require_consent and not yes:
        if not sys.stdin.isatty():
            raise ValueError("Use --yes for noninteractive paid runs")
        typed = input(f"{len(tasks)} API requests scheduled for {run_dir}. Type RUN {len(tasks)}: ").strip()
        if typed != f"RUN {len(tasks)}":
            raise ValueError("User did not authorize API requests")
    # Compare immutable manifest BEFORE looking at success/skipping files.
    write_immutable_manifest(run_dir, manifest)
    completed = read_success(run_dir, manifest)
    pending = [t for t in tasks if t["task_id"] not in completed]
    print(f"{run_dir}: {len(completed)}/{len(tasks)} already completed; {len(pending)} pending")
    if pending:
        ensure_key(model)
        path = run_dir / "responses.jsonl"
        with path.open("a", encoding="utf-8") as out:
            for i, task in enumerate(pending, 1):
                if i > 1 and config["delay"]:
                    time.sleep(config["delay"])
                start = time.monotonic()
                record = {"record_type": "generation", **task,
                          "created_utc": datetime.now(timezone.utc).isoformat()}
                try:
                    response = api_fn(model, task["payload"])
                    choice = response["choices"][0]
                    content = choice["message"].get("content") or ""
                    finish = choice.get("finish_reason")
                    status = ("ok" if finish == "stop" and content.strip()
                              else "empty_completion" if finish == "stop" and not content.strip()
                              else "incomplete")
                    record.update(status=status, content=content, finish_reason=finish,
                                  response=response, latency_seconds=time.monotonic()-start)
                except (RuntimeError, ValueError, KeyError, IndexError, OSError) as exc:
                    record.update(status="error", error=str(exc), latency_seconds=time.monotonic()-start)
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                print(f"[{i}/{len(pending)}] cursor={task['cursor_byte']} {task['condition']} rep={task['repetition']} {record['status']}", flush=True)
                if record["status"] not in SUCCESS:
                    raise RuntimeError("Generation incomplete; retained partial responses. Rerun same command to resume.")
    completed = read_success(run_dir, manifest)
    if set(completed) != set(manifest["task_ids"]):
        raise ValueError("Missing tasks after generation; refuse scoring")
    responses_path = run_dir / "responses.jsonl"
    scores_path = run_dir / "scores.csv"
    meta_path = run_dir / "scores_meta.json"
    meta = {"manifest_sha256": sha_file(run_dir / "manifest.json"),
            "responses_sha256": sha_file(responses_path),
            "cases_sha256": sha_file(cases_path)}
    if scores_path.exists():
        if not meta_path.is_file():
            raise ValueError(f"Existing scores lack integrity metadata: {scores_path}")
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        if any(previous.get(k) != v for k, v in meta.items()):
            raise ValueError(f"Scores generated from different manifest/responses: {scores_path}")
        if previous.get("scores_sha256") != sha_file(scores_path):
            raise ValueError("Existing scores modified since original scoring")
        print(f"Verified existing scores: {scores_path}")
        return {"scheduled": len(tasks), "complete": len(completed), "scored": True}
    ordered = [completed[t["task_id"]] for t in tasks]
    scored = score_records(ordered, require_bleu=True)
    write_csv(scores_path, scored)
    meta["scores_sha256"] = sha_file(scores_path)
    with meta_path.open("x", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    print(f"Saved scores: {scores_path}")
    return {"scheduled": len(tasks), "complete": len(completed), "scored": True}
