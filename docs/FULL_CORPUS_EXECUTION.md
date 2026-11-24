# Full Corpus Execution: HJ → Prepared Cases → LLM Benchmark

> **Advanced workflow / potentially substantial paid API usage.** This document describes the **existing** `--scope full` implementation. It does not introduce a new runner, batch scheduler, or evaluation protocol. Follow the repository root [`README.md`](../README.md) for initial setup and all CLI flags, and [`FINAL_FRAGMENT_PROTOCOL.md`](FINAL_FRAGMENT_PROTOCOL.md) for the scientific protocol.

## 1. What “full” means, and what it does not mean

The collection of C (`.c`) and Microsoft Small Basic (`.sb`) program files under `~/PL` is a **source-code corpus**. In **this** project it is a **benchmark/evaluation corpus**: the programs provide source prefixes at cursor positions and the expected reference continuations (when corresponding HJ reports exist). We do not train or fine-tune GPT-4o or Grok on those programs.

`~/PL` is a **separate, external asset workspace**, not a Python package or subdirectory that must be checked into `code-completion-lab`. The relevant paths used by this project are, schematically:

```text
~/
├── code-completion-lab/                      # our Git repo, CLI, model adapters, scoring
│   ├── benchmark_v2/
│   ├── run_c.py, run_smallbasic.py
│   ├── prepare_c_batch.py, prepare_hj_benchmark.py, map_hj_candidates.py
│   └── results/                            # generated, .gitignored
└── PL/                                      # external HJ assets and source corpus
    ├── tree-sitter/                         # HJ parser source clone (optional)
    ├── code-completion-extension/           # HJ extension source clone (optional)
    ├── tree-sitter-c/                       # C grammar build
    └── codecompletion_benchmarks/           # corpus + HJ bundles (separate downloads)
        ├── c/TEST/                          # C source programs
        └── smallbasic/
            ├── tree-sitter/                 # parser binary and HJ reports
            ├── tree-sitter-smallbasic/      # SB samples + grammar
            └── code-completion-extension/   # candidate DB + token mapping
```

Actual `~/PL` folders can differ. The `.c`/`.sb` files are **source programs**. Together they are a **source corpus**. HJ `candidates.json` is a **state-conditioned grammar-candidate database**; the HJ reference reports link cursor positions to expected continuations. The corpus, grammar, candidate DB, and report versions must match. Adding new `.c`/`.sb` files to `~/PL` by itself does **not** make them eligible: the raw HJ preparation must have corresponding validated reference reports and parser/candidate resources.

**Full scope** means **every prepared, eligible cursor in the selected HJ input**, not every source byte or every program under `~/PL`. Standard `--top-k 3` eligibility in `benchmark_v2.cases.eligible()` requires:

- `status == "prepared"`;
- exactly **one reference** at that cursor;
- at least **three HJ candidates**, including `candidate_count >= 3`.

Excluded/invalid positions must be reported in preparation/audit logs, not silently treated as generated completions. The source-slice equality check validates **provenance**, not completeness of a grammar-unit reference span; see [`PHASE3_REFERENCE_AUDIT.md`](PHASE3_REFERENCE_AUDIT.md).

## 2. Verified full selection for the existing HJ dataset

Results of **offline** commands with `--scope full --stage prepare --model gpt --dry-run`:

| Language | Original paired corpus context | Eligible cursor count | Requests for one model | CLI grouping |
| --- | --- | ---: | ---: | --- |
| C | 106 input source/report files | **43,668** | **393,012** | `106 files` |
| Small Basic | 27 HJ-paired `.sb` programs in the earlier prepared dataset | **1,053** | **9,477** | `1 files` |
| **Total** | | **44,721** | **402,489** | Per model |

`smallbasic ... 1 files` is an **implementation detail of `discover_sources()`**: all Small Basic positions are loaded from **one combined** `results/sb-full/cases_mapped.jsonl`. It is **not** evidence that only one program was analyzed. To inspect distinct source programs in that JSONL (offline):

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('results/sb-full/cases_mapped.jsonl')
sources = set()
with p.open(encoding='utf-8') as fh:
    for line in fh:
        if line.strip():
            obj = json.loads(line)
            sources.add(obj['source_relative'])
print('Distinct Small Basic source programs:', len(sources))
PY
```

These numbers are *counts of eligible cases*, not a completed full-model benchmark. They change if the HJ corpus changes, different source/report pairs are used, or the Top-K requirement is changed.

## 3. Environment and offline raw preparation

All Python commands run from `~/code-completion-lab` using its `.venv` interpreter. Native HJ tools and datasets live in `~/PL` and are not installed by Python `pip`:

```bash
cd ~/code-completion-lab
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
```

**Stage A: raw HJ preparation**, only if the current prepared data does not exist already. C and Small Basic use different adapters:

```bash
# C: matching HJ reports + source files -> C prepared batch.
python prepare_c_batch.py --out results/c-prepared

# Small Basic: raw HJ parser + source/report pairs -> mapped cases.
mkdir -p results/sb-full
python prepare_hj_benchmark.py   --language smallbasic --all --skip-missing-reports   --executable "$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe"   --output results/sb-full/prepared.jsonl
python map_hj_candidates.py   --input results/sb-full/prepared.jsonl   --output results/sb-full/cases_mapped.jsonl
```

This is separate from **Stage B: LLM task preparation**, described below. Raw HJ preparation may take substantial time but does **not** call paid LLM APIs. Note the C batch preparer expects a compatible `TreeSitterCutFile` native executable **without** `.exe` at its default HJ base; pass `--hj-base`, `--grammar`, `--source-dir`, and `--report-dir` as needed. Do not overwrite frozen preparation outputs with a different corpus snapshot: use a new output root and pass `--c-batch-dir`/`--sb-input` to the runner.

## 4. Stage B: full-scope dry-run and preparation, both offline

**Dry-run (no new output files, no API calls):**

```bash
python run_c.py --scope full --stage prepare --model gpt --dry-run
python run_smallbasic.py --scope full --stage prepare --model gpt --dry-run
```

The runner scans and filters all eligible cursor cases before printing the total. With tens of thousands of C cursor positions, it can be quiet for some time. The values `393,012` and `9,477` above come from this check, **not** from paid provider calls.

**Persist all LLM cases and prompts (no API calls):**

```bash
python run_c.py --scope full --stage prepare --model gpt
python run_smallbasic.py --scope full --stage prepare --model gpt
```

This creates output under `results/v2/<language>/<model>/full/<run_id>/files/`, including `cases.jsonl` and `preparation_meta.json`. For C, files are grouped by source. For Small Basic, all source programs in the combined mapped JSONL form one input group. These prepared case files can be large; allow sufficient memory, CPU, and disk space. To use Grok, prepare again with `--model grok` so its model-specific Run ID and frozen metadata are recorded separately.

## 5. Stage C: paid API generation (not a routine test)

The CLI currently has **no cursor-chunk or request-chunk selector for Full Scope**. The flag `--max-api-requests` is a **whole-run budget guard**: it blocks a run whose planned total exceeds the limit; it **cannot** be used to run the first 200 requests of a large full plan. There is also **no currency cap**. Prompt lengths, response lengths and provider pricing matter; thousands to hundreds of thousands of API calls can be very expensive.

Only after completing offline case preparation, verifying costs and quotas, and explicitly approving such a large paid run, the command pattern for this particular dataset would be:

```bash
# LARGE PAID RUN: 393,012 C requests (GPT-4o); not recommended as a test.
python run_c.py --scope full --stage run --model gpt   --max-api-requests 393012 --yes --no-plots

# LARGE PAID RUN: 9,477 Small Basic requests (GPT-4o).
python run_smallbasic.py --scope full --stage run --model gpt   --max-api-requests 9477 --yes --no-plots
```

The two models use different Runs IDs: if switching to Grok, prepare again with `--model grok` and review the planned count before running it. A complete C + SB run for *one* model would involve **402,489 requests** for the current input snapshot; two models would involve **804,978 requests**.

**Resume:** if the API fails or you interrupt a running process, repeat the *exact* paid command with the same model, input dataset, code version, context/profile flags, and output root. Successful response `task_id` records are reused; errors and incomplete outputs remain for investigation. The request-limit check still uses the **full planned total**, even if only a few requests remain. Do not edit `responses.jsonl`, `manifest.json`, task fingerprints or case files to force resume. For practical incremental research, a tested `--batch-size`/`--cursor-range` feature would need to be added in a future version; do not assume it exists now.

## 6. Stage D: reporting completed runs

```bash
# Use --stage report only when the same original code/config builds the Run ID.
python run_c.py --scope full --stage report --model gpt --no-plots
python run_smallbasic.py --scope full --stage report --model gpt --no-plots

# Read completed reports/statistics across saved full runs.
python -m benchmark_v2 results --scope full
python -m benchmark_v2 stats --scope full --metric all
python -m benchmark_v2 audit --language c --model gpt --scope full --limit-cases 0
```

The `results/` directory is Git-ignored. Published `docs/main_study_paired_stats.csv` contains the **held-out 30-cursor per-language Main Study summary**, not Full Scope raw results. The Main Study's 1,080 predictions are complete; **the current Full Scope has only been dry-run**, and should not be described as experimentally completed.

## 7. Interpretation and reproducibility checklist

- Record exact Git commit, prompt profile, HJ/parser/grammar versions and hashes, candidate DB and source/report hashes, model IDs, model parameters, requested Top-K, and Run ID.
- Report exclusions and denominators. More `.c`/`.sb` files under `~/PL` do **not** automatically mean more eligible cursor cases.
- Official scores use raw completion text, without cropping to the ground-truth reference length. Syntax correctness and grammar-span completeness are not proven by string-similarity metrics.
- `WithinTop3Guide` is an oracle maximum based on reference scores; `WithIdealGuide` receives a reference-derived **structure**, not a deployable production hint.
- Keep statistics clustered at the source-program level when multiple cursor positions come from one file. A full corpus benchmark is not automatically a probability sample of software projects or languages.
- Never commit API credentials, locally collected raw model responses, or third-party corpora without explicit rights to redistribute.
