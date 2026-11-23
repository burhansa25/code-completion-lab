# Code Completion Lab — Refactor Starter (Phase 1)

This is a **non-destructive orchestration refactor** for the public branch `hj-editable-token-mappings`. Copy the three entrypoint Python files into your existing repository root. **Do not replace or delete the original experiment scripts yet.** This starter is designed to preserve the experiment protocol and outputs while making each language runnable with one command.

## Prerequisites

- Ubuntu / WSL2 and the same `~/PL` benchmark archive documented in the repository README.
- Python 3.9+; `.venv/bin/python` containing `sacrebleu==2.6.0`. The entrypoints prefer `.venv/bin/python` for subprocesses when present.
- From the repository root, the following original scripts must remain: `prepare_hj_benchmark.py`, `map_hj_candidates.py`, `hj_benchmark_tools.py`, `run_hj_c_batch.py`, `run_hj_xai.py`, `run_hj_openai.py`, `run_hj_openai_sb_resume.py`.
- API key for the selected provider, entered in a hidden prompt (interactive terminal) or set as `XAI_API_KEY` / `OPENAI_API_KEY`.

## One command per language

```bash
python3 run_c.py --model grok
python3 run_smallbasic.py --model grok
```

Defaults: `--scope smoke`, `--stage all`, `--model grok`. A **smoke run selects exactly one eligible cursor, schedules 6 generations** (3 baseline + guided rank 1/2/3), and scores them. In C, **one file is not the same as one cursor**; the new C smoke mode directly invokes the legacy provider runner with `--cursor` and avoids an accidentally large test.

```bash
# Another provider
python3 run_c.py --model gpt4o
python3 run_smallbasic.py --model gpt4o

# Preparation only: no API calls
python3 run_c.py --stage prepare
python3 run_smallbasic.py --stage prepare --scope full

# Print commands only, no API calls
python3 run_c.py --dry-run
python3 run_smallbasic.py --dry-run

# Full dataset: requires a typed confirmation or explicit --yes flag
python3 run_c.py --scope full --model grok
python3 run_smallbasic.py --scope full --model grok

# If your HJ assets do not live in ~/PL
python3 run_smallbasic.py --assets-root /absolute/path/to/PL

# Reuse an existing prepared C batch without repeating preparation
python3 run_c.py --c-batch-dir results/c-full-infill-v1

# For automatic CI/noninteractive full runs only (can incur significant costs)
python3 run_smallbasic.py --scope full --model grok --yes
```

C preparation always scans the full C corpus (offline/no API). It caches outputs to `results/c-prepared` by default. The entrypoint also detects existing `results/c-full-infill-v1/summary.json` and reuses it automatically. Small Basic maintains separate smoke/full prepared datasets.

### What is preserved

- HJ parser states, candidate frequency aggregation and rank order.
- C prefix-suffix infill prompts and Small Basic mapped prompt v2.
- Exactly 3 without-guidance and 1 completion for each of 3 guided ranks.
- Existing provider model IDs and generation settings (`temperature=0.5`, `max_tokens=512`, `delay=0`).
- Current response manifest and CSV scoring, provider-specific handling of empty completions, per-task resume, and existing C per-file resume.
- Existing analysis tools and offline native syntax validator are intentionally left unchanged.

### What is not yet merged

This patch **does not collapse** `run_hj_openai.py`, `run_hj_openai_sb_resume.py`, `run_hj_xai.py`, and `run_hj_groq.py` into a single generation engine. The first step is a safe façade that runs the original scientific implementation with predictable subcommands. A later Phase 2 should move common code into a shared backend **after golden-file parity tests** on unchanged cases (task IDs, manifest, provider payload, scored CSV headers/values, empty response handling and resume). Do not delete legacy scripts until parity passes with actual test data.

## Test results

Test from this starter directory:

```bash
python3 -m unittest discover -s tests -v
```

The supplied 8 tests run against fake legacy subprocesses and synthetic cases, not the HJ parser or real external LLM APIs. They cover C smoke, Small Basic smoke, full-run consent, Small Basic full selection, cache reuse, stale mapping detection, corrupted C input, and side-effect-free dry run. They validate orchestrator contracts only.

## Real-environment verification (required before marking the refactor production-ready)

1. Back up or checkout a clean copy of branch `hj-editable-token-mappings`; record the commit SHA with `git rev-parse HEAD`.
2. Install `.venv` dependencies and ensure the `~/PL` archive paths exist.
3. Run `python3 run_smallbasic.py --stage prepare` and inspect `results/sb-smoke/cases_mapped.jsonl`. Compare eligible count and `prompt_profile` to the existing README flow.
4. Run `python3 run_c.py --stage prepare` (or reuse `results/c-full-infill-v1`). Verify `summary.json` and compare retrieval/case counts against the old runner.
5. Run one *paid* 6-request smoke run per language using a key, then inspect manifests, task IDs and scoring. Repeat same command to check resume/skip semantics.
6. Run parity tests against the original README procedure on a separate output root **using the same frozen candidate input and provider settings**. Stochastic output text may differ; task definitions, manifest construction and scoring formulas must match.
7. Only after those checks, implement Phase 2 (shared generation/scoring internals) and replace the old README shell blocks with these two short commands.

## Safety notes

- C full preparation still requires working native parser binaries. Parsing the full C corpus may be lengthy even for a smoke API run, but incurs **no API cost** during preparation.
- Full C was documented at 262,008 calls/model. Full Small Basic at 6,318 calls/model. Verify **your local** `summary.json` and mapped-case count; do not rely on archived estimates.
- A missing provider model or unsupported token limit will stop the API run, with partial output kept according to the original runner's resume design.
- Syntax validation is optional and remains separate, as in the source README.
- The original caution about unverified reference end-span completeness still applies. Do not treat text scores as functional correctness.
