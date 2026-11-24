# Code Completion Lab

**Reproducible research pipeline for grammar-guided code-fragment completion in C and Microsoft Small Basic.** This repository integrates HJ's parser-state/candidate data with LLM generation, Monir-style experimental comparisons, integrity-controlled scoring, and paired statistical analysis. It is an **offline benchmark runner plus optional paid-model generation**, not a VS Code extension or a replacement for Tree-sitter.

> **Status and scope.** The frozen `fragment-v1` main study contains **30 held-out cursor positions per language**, evaluated with **GPT-4o** and **Grok** (four language–model runs, nine generations per cursor, **1,080 predictions total**). These numbers describe completed local research runs; raw responses are *not included in Git*. The public aggregate output is [`docs/main_study_paired_stats.csv`](docs/main_study_paired_stats.csv). A fresh clone has passed the Python test suite and a six-position Small Basic HJ preparation test, but reproducing a complete study requires the **separate HJ datasets, parser, grammar libraries, reference reports, and valid API keys**.
>
> **Scientific caveat.** The reference is an HJ-collected **source fragment**, not necessarily a complete syntax unit. Matching source bytes does not prove that the fragment ends at a correct grammar boundary; textual similarity is not equivalent to compilability or functional correctness. `WithinTop3Guide` is a reference-scored **oracle**, not a deployable ranker.

## Contents

1. [What this project studies](#1-what-this-project-studies)
2. [Where this repository sits relative to PL and HJ](#2-where-this-repository-sits-relative-to-pl-and-hj)
3. [Repository structure and file responsibilities](#3-repository-structure-and-file-responsibilities)
4. [Installation from scratch: Ubuntu / WSL](#4-installation-from-scratch-ubuntu--wsl)
5. [External HJ assets: layout and validation](#5-external-hj-assets-layout-and-validation)
6. [Prepare the HJ data (offline)](#6-prepare-the-hj-data-offline)
7. [Prepare experiment cohorts (offline)](#7-prepare-experiment-cohorts-offline)
8. [Run generation, resume, and report](#8-run-generation-resume-and-report)
9. [All runner options and advanced experiments](#9-all-runner-options-and-advanced-experiments)
10. [Results, statistics, provenance, and outputs](#10-results-statistics-provenance-and-outputs)
11. [Relationship to HJ and Monir](#11-relationship-to-hj-and-monir)
12. [Developer architecture and core functions](#12-developer-architecture-and-core-functions)
13. [Validation, troubleshooting, and limitations](#13-validation-troubleshooting-and-limitations)
14. [Research references and project notes](#14-research-references-and-project-notes)

## 1. What this project studies

The central question is **whether candidate grammar structures retrieved from parser states improve LLM code-fragment completion compared with a prompt containing no structural hint**, and whether the effect varies by programming language and model.

At every selected cursor, the engine applies the same prefix and evaluation reference across these arms:

| Experiment arm | Generation requests per cursor | Hint supplied to the LLM | Interpretation |
| --- | ---: | --- | --- |
| **Without Guidance** | 3 | No structure | Repeated unguided baseline; mean of three scores **per cursor** |
| **With Top-1 Guidance** | 1 | HJ's highest-frequency ranked candidate structure | Practical retrieval-guided condition |
| **Within Top-3 Guidance** | 3 (one per rank) | HJ candidate structures ranked 1, 2, 3 | Reported as the **maximum reference-scored result** (oracle best-of-three), **not** a production selector |
| **With Ideal Guidance** | 3 | Reference **grammar structure only** | Oracle diagnostic; reference code text is **never inserted into the prompt** |

Thus: **3 + 3 + 3 = 9** generation requests per eligible cursor with default `--top-k 3 --ideal-repetitions 3`. Top-10/20 is optional research expansion, not the frozen main-study design.

**Primary reported metrics:** SacreBLEU **1-gram precision (%)** and raw `difflib.SequenceMatcher` similarity (%). Sentence BLEU (SacreBLEU `13a` tokenization), raw exact match, and formatting-normalized comparisons are separate diagnostics. **1-gram precision must not be mislabeled full sentence BLEU.** The official study evaluates raw model text without reference-dependent cropping.

**How retrieval differs from generation:** if HJ reports active parser states `S = {s1, s2, ...}`, each grammar structure receives a frequency score summed across candidate entries associated with those states. The top structures are sorted by that combined score; they are **hints**, not a model prediction or a proof of what code must follow. For each eligible cursor, only the **one reference source fragment** is used to score generated text. The ideal arm can use the reference *structure*, but never injects the reference *text*. Eligibility requires `status="prepared"`, exactly one reference, and at least `K` candidate structures.

At the case level, the two central comparisons are:

- `WithoutGuide(case, metric)` = the **mean** of three independently requested unguided scores.
- `WithTop1Guide(case, metric)` = the single score generated with rank-1 HJ structure.
- `WithinTop3Guide(case, metric)` = **max(score(rank1), score(rank2), score(rank3))**, calculated with access to the reference; it is an oracle and should never be described as online selection.
- Paired treatment effect at a cursor = `WithTop1Guide − WithoutGuide` (**percentage points** when reported on 0–100 metrics).

The frozen main study uses `--profile fragment-v1 --completion prefix --top-k 3`, C with **last 10 prefix lines**, and Small Basic with its **full prefix**. Therefore, direct absolute-score comparisons between languages are descriptive only: dataset, prefix length, grammar, and reference distributions all differ.

## 2. Where this repository sits relative to PL and HJ

**The repository and `PL` are logically separate workspaces** (often siblings under `$HOME`). `PL` is a lab working directory containing multiple checkouts and downloaded datasets; it is not necessarily one Git repository. They do **not** have to be adjacent or nested. They only need to be reachable from the same Ubuntu/WSL environment. The current preparers default to absolute paths under `$HOME/PL`; pass explicit path flags to use a different location.

Recommended separation (surface-level overview; some paths represent external downloaded assets rather than Git clones):

```text
$HOME/                               # e.g., /home/<user>/ in WSL
├── code-completion-lab/             # THIS GitHub repository
│   ├── benchmark_v2/                # unified Python engine
│   ├── run_c.py, run_smallbasic.py   # language entrypoints
│   ├── prepare_*.py, map_*.py       # offline data adapters
│   ├── docs/, tests/, tools/        # documentation and verification
│   └── results/                     # generated locally; Git-ignored
└── PL/                              # EXTERNAL HJ / lab asset workspace
    ├── tree-sitter/                 # optional Candidate_Collection source clone
    ├── tree-sitter-smallbasic/      # optional grammar source checkout
    ├── code-completion-extension/   # optional extension source clone
    ├── tree-sitter-c/              # compiled C grammar assets
    └── codecompletion_benchmarks/   # downloaded benchmark data bundle
        ├── c/                       # C sources (TEST/)
        └── smallbasic/              # HJ parser, reports, SB grammar, candidate DB
```

The important **default asset paths** *inside* those PL directories are:

| Asset | Relative to `$HOME/PL/` |
| --- | --- |
| C source corpus | `codecompletion_benchmarks/c/TEST/` |
| C compiled grammar | `tree-sitter-c/c.so` |
| Small Basic source corpus | `codecompletion_benchmarks/smallbasic/tree-sitter-smallbasic/SB_Sample/` |
| Small Basic compiled grammar | `codecompletion_benchmarks/smallbasic/tree-sitter-smallbasic/smallbasic.so` |
| HJ native parser | `codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile` (or explicitly supplied `.exe`) |
| HJ reference reports | `codecompletion_benchmarks/smallbasic/tree-sitter/reports/{c,smallbasic}/` |
| HJ structure candidates | `codecompletion_benchmarks/smallbasic/code-completion-extension/resources/{c,smallbasic}/candidates.json` |
| Small Basic token map | `codecompletion_benchmarks/smallbasic/code-completion-extension/resources/smallbasic/token_mapping.json` |

**Dataset terminology: `source programs` versus `training data`.** The `.c` and `.sb` files stored under `~/PL` are **source programs / source-code files**. Taken together, they form a **source-code corpus**; in this project they are the **benchmark/evaluation corpus** (programs from which cursor positions, prefixes, and reference continuations are obtained). They are **not a model-training dataset in this pipeline**: `code-completion-lab` sends prompts to existing LLM APIs but does not fine-tune or update model weights. The HJ `candidates.json` files are **grammar-candidate databases** (parser-state → candidate structures/frequencies), not a trained neural model. HJ reports supply reference continuation metadata; source/program files and HJ reports must correspond to each other. Additional programs placed in `~/PL` are **not automatically benchmarked** unless the preparation pipeline can find and validate their matching reference reports and other required assets.

**Important distinction:** cloning `PL/tree-sitter` and `PL/code-completion-extension` **does not automatically put the required binaries, reports and candidate JSON files into** `PL/codecompletion_benchmarks/smallbasic/`. The parser output and candidate database locations above are **the current tool defaults**, built around the lab's separately provided benchmark assets. The exact PL layout can differ; adapt via `--base`, `--hj-base`, `--source-dir`, `--grammar`, `--reference-dir`, `--database`, or `--executable` instead of rewriting Python source paths.

The `PL` directory is **not a Python virtual environment**. Its parser executable is called as a separate process. You should run Python commands **from `code-completion-lab/`**, with that repository's `.venv` activated. The external parser and grammar are compiled/native assets; Python `pip install` does not build them.

**External relationships at a glance:**

```text
HJ parser (TreeSitterCutFile + grammar) ──> parser states at source-byte cursor
HJ extension resources/candidates.json ──> structure candidates + frequencies
HJ reference reports + source corpus ────> checked reference text/structure
                                      │
                                      ▼
            prepare_hj_benchmark.py / prepare_c_batch.py
                                      │
                        (Small Basic mapping if needed)
                                      ▼
                         benchmark_v2/cases.py
                          + pilot.py cohort
                                      ▼
                     engine.py → providers.py (paid API)
                                      │
                  scoring.py → tools/rq2_report.py
                                      │
                 results / audit / boundaries / stats
```

## 3. Repository structure and file responsibilities

The following is the **active layout**. It intentionally excludes retired provider-specific runners. `docs/archive/` contains old notes rather than active instructions.

```text
code-completion-lab/
├── README.md                         # start here; authoritative current workflow
├── requirements.txt                  # Python runtime dependencies
├── .gitignore                        # excludes keys, environments, results, native artifacts
├── run_c.py                          # thin C entrypoint → benchmark_v2.cli.main("c")
├── run_smallbasic.py                 # thin Small Basic entrypoint → cli.main("smallbasic")
├── prepare_c_batch.py                # collect/verify C HJ report corpus, prepare C batches
├── prepare_hj_benchmark.py           # run HJ parser on selected positions; emit JSONL
├── map_hj_candidates.py              # convert Small Basic internal tokens to readable hints
├── benchmark_v2/
│   ├── __init__.py
│   ├── __main__.py                    # results, stats, audit, boundaries commands
│   ├── cli.py                         # flags, cohort selection, budgets, orchestration
│   ├── cases.py                       # eligibility, prefix context, 9 conditions, prompts
│   ├── pilot.py                       # reproducible pilot/study sampling and exclusions
│   ├── prompts.py                     # frozen optional legacy-lab prompt templates
│   ├── engine.py                      # immutable manifests, tasks, safe resume, raw scoring
│   ├── providers.py                   # HTTP providers: OpenAI / xAI / Groq
│   ├── scoring.py                     # raw task-level similarity scores
│   ├── audit.py                       # read-only prediction/format inspection
│   ├── boundaries.py                  # source, reference, rank, lexical boundary checks
│   └── stats.py                       # paired delta and source-cluster bootstrap CI
├── mappings/                          # mapping resources (e.g., HJ token translations)
├── tools/
│   └── rq2_report.py                 # per-case RQ2 tables, retrieval coverage, plots
├── tests/                             # offline unit/regression tests
├── docs/
│   ├── ARCHITECTURE_AND_FUNCTIONS.md # detailed module, function, and call-flow map
│   ├── FINAL_FRAGMENT_PROTOCOL.md    # frozen research protocol
│   ├── PHASE3_REFERENCE_AUDIT.md      # reference validity and audit limitations
│   ├── main_study_paired_stats.csv    # published paired aggregate statistics
│   └── archive/                      # historical docs/tools; NOT current CLI guide
└── results/                           # LOCAL ONLY; not included in Git
    ├── c-prepared/                    # offline C HJ batch
    ├── sb-full/                       # offline Small Basic prepared/mapped cases
    └── v2/<language>/<model>/<scope>/<run_id>/
```

**Detailed function-level guide:** [`docs/ARCHITECTURE_AND_FUNCTIONS.md`](docs/ARCHITECTURE_AND_FUNCTIONS.md) explains the purpose, important functions, their callers, cross-module dependencies, and why each file exists. Every active source module is covered there. The optional `docs/archive/offline_eval_v1/` remains an archived, independent historical validator, **not** the current scoring engine.

## 4. Installation from scratch: Ubuntu / WSL

All commands below run in a **WSL Ubuntu shell**, *not* Windows PowerShell, unless explicitly indicated. Commands using `python` assume `.venv` is activated; using `.venv/bin/python` from the repository root is an equivalent way to avoid activation.

### 4.1 Prerequisites and clone

Install **Python 3.9+** (3.10+ recommended), Python `venv`, Git, and access to HJ assets. Some native tools/grammar builds additionally require a compatible compiler, but those builds are **not handled by this repository**.

```bash
# Run in WSL Ubuntu. Skip installation steps already satisfied.
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

cd "$HOME"
# Until this refactor is merged into main, check out the tested branch:
git clone --branch refactor/rq2-unified-engine --single-branch \
  https://github.com/burhansa25/code-completion-lab.git
cd code-completion-lab
```

After the branch is merged, users may clone `main` in the usual way, but **verify the README and revision** rather than assuming all versions have identical behavior.

### 4.2 Create the Python environment (once per clone)

```bash
# Working directory: $HOME/code-completion-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python --version
python -m unittest discover -s tests -q
```

The tested `requirements.txt` includes `sacrebleu==2.6.0` and `matplotlib>=3.8,<4`; the provider adapter uses Python's standard-library HTTP client, so **no `openai` or `xai` SDK installation is required**. `matplotlib` supports optional plots. If you do not need charts, use `--no-plots` for reports.

**Every new WSL terminal:**

```bash
cd "$HOME/code-completion-lab"
source .venv/bin/activate
```

**Where commands run:** `git clone`, `apt`, and native HJ setup are shell operations and are **not run inside Python**. `source .venv/bin/activate` is a shell command. Python runner, preparer, reporting, and test commands should use the `.venv` interpreter; the external HJ executable is launched automatically by the preparer. You can exit the virtual environment with `deactivate`.

### 4.3 Provider keys (needed ONLY for paid generation)

Create an **untracked** `.env` inside `code-completion-lab` when you plan to run a model (`nano .env` in the WSL shell is one way to edit it). Never commit it, share its content, or include credentials in screenshots. Only `source` an environment file you created or trust.

```bash
# Example .env contents; fill in locally, do not commit:
# OPENAI_API_KEY="..."
# XAI_API_KEY="..."
# GROQ_API_KEY="..."

chmod 600 .env
set -a
source .env
set +a
```

`gpt`/`gpt4o` uses `OPENAI_API_KEY` (**GPT-4o**, not a generic “latest GPT” model); `grok` uses `XAI_API_KEY`; `gptoss` uses `GROQ_API_KEY`. The library does not automatically load `.env` as a Python package; export the variables with the shell command above or configure them in your shell. API usage can incur charges independently of a ChatGPT subscription.

## 5. External HJ assets: layout and validation

HJ's relevant source repositories are:

- [SwlabTreeSitter/tree-sitter — `Candidate_Collection` branch](https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection): modified parser/state collection machinery and native `TreeSitterCutFile` interface.
- [chaendaya/code-completion-extension](https://github.com/chaendaya/code-completion-extension): HJ candidate-database resources; the **extension runtime/UI is not invoked** by our Python benchmark.
- **Grammar/corpora/reports**: additional lab datasets and compiled grammars provided separately. The existing [HJ research asset location referenced in the original setup](https://drive.google.com/drive/folders/1d95-yj7ysrPYE5agrE9aQ9HM_7niqy9Q?usp=sharing) may require access; confirm provenance and redistribution rights.

Optional Git source checkouts (not replacements for the prepared asset bundle):

```bash
mkdir -p "$HOME/PL"
# Source reference/inspection, if those paths are not already populated:
git clone --branch Candidate_Collection \
  https://github.com/SwlabTreeSitter/tree-sitter.git "$HOME/PL/tree-sitter"
git clone https://github.com/chaendaya/code-completion-extension.git \
  "$HOME/PL/code-completion-extension"
```

Do **not** run these clones over existing directories. The Small Basic grammar checkout and benchmark-data archive are **separate assets** and their exact source/version must be obtained from the lab's setup materials; this README does not invent a grammar build or dataset download command.

Check paths against the defaults **before** executing corpus preparation:

```bash
# Working directory can be anywhere; these refer to EXTERNAL HJ assets.
PL_BASE="$HOME/PL/codecompletion_benchmarks/smallbasic"
for p in \
  "$PL_BASE/tree-sitter/TreeSitterCutFile.exe" \
  "$PL_BASE/tree-sitter-smallbasic/smallbasic.so" \
  "$PL_BASE/tree-sitter-smallbasic/SB_Sample" \
  "$PL_BASE/tree-sitter/reports/smallbasic" \
  "$PL_BASE/code-completion-extension/resources/smallbasic/candidates.json" \
  "$PL_BASE/code-completion-extension/resources/smallbasic/token_mapping.json" \
  "$HOME/PL/tree-sitter-c/c.so" \
  "$HOME/PL/codecompletion_benchmarks/c/TEST" \
  "$PL_BASE/tree-sitter/reports/c" \
  "$PL_BASE/code-completion-extension/resources/c/candidates.json"
do
  if [ -e "$p" ]; then printf 'OK      %s\n' "$p"; else printf 'MISSING %s\n' "$p"; fi
done
```

The above example explicitly checks the `.exe` executable that succeeded in our WSL integration test. The default preparer looks for **`TreeSitterCutFile` without `.exe`**. Either set `--executable` to the working file or supply the correctly named native binary. The native file must actually be executable in your environment; its extension alone is not evidence of compatibility. If the check reports `MISSING`, correct the paths or obtain the assets **before** running preparation.

The lab bundle must contain **matching** source files, reference reports, candidate DB and grammar versions. Hashes recorded at preparation time help detect subsequent changes, but matching hashes do not certify reference-span correctness.

## 6. Prepare the HJ data (offline)

**Distinguish two operations**: raw HJ preparation **calls the external parser**, whereas the runner's `--stage prepare` **only builds LLM tasks from already prepared HJ cases**. Both are offline and send **zero provider requests**.

Raw HJ preparer (`python prepare_hj_benchmark.py --help`) accepts the following independent options:

| Option | Default / role |
| --- | --- |
| `--language {smallbasic,c}` | Small Basic by default; C requires explicit source/grammar paths when called directly |
| `--base PATH` | Default `$HOME/PL/codecompletion_benchmarks/smallbasic` |
| `--executable PATH` | Native `TreeSitterCutFile`; default filename has **no** `.exe` suffix |
| `--source-dir PATH` | Original source folder (C or Small Basic) |
| `--grammar PATH` | Corresponding grammar `.so` |
| `--reference-dir PATH` | HJ JSON report directory |
| `--database PATH` | HJ `resources/<language>/candidates.json` |
| `--file RELATIVE_PATH` / `--all` | One source file or every source with matching extension; mutually exclusive |
| `--skip-missing-reports` | Only with `--all`; exclude files without paired reference JSON |
| `--mode {0,2}` | HJ native parser mode, default `0` |
| `--timeout SECONDS` | Default 30 seconds per native parser invocation |
| `--output PATH` | **Required**, new JSONL path (no overwrite) |

The Small Basic mapper accepts `--input`, `--output` (both required) and optional `--mapping` to override the HJ `token_mapping.json`. The batch C preparer accepts `--out` (required) and `--hj-base`, `--source-dir`, `--grammar`, `--report-dir` (optional overrides).

### 6.1 Small Basic: raw parser output → mapped cases

From `code-completion-lab/` with `.venv` active:

```bash
mkdir -p results/sb-full

python prepare_hj_benchmark.py \
  --language smallbasic --all --skip-missing-reports \
  --executable "$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe" \
  --output results/sb-full/prepared.jsonl

python map_hj_candidates.py \
  --input results/sb-full/prepared.jsonl \
  --output results/sb-full/cases_mapped.jsonl
```

The Small Basic reference set used in this project has **27 source/report pairs out of 33 `.sb` sources**; six examples have no matching HJ reports and are skipped deliberately with `--skip-missing-reports`. These counts describe the frozen original lab assets, **not a promise about every download**. The preparer prints `prepared` and exclusion counts; some cases lack a verified reference or sufficient candidate structures. An exit code of **2** can indicate nonprepared positions even when it wrote the JSONL; inspect the log and row statuses before mapping/continuing. `map_hj_candidates.py` resolves selected internal grammar tokens without silently changing HJ rank or frequency; unmapped tokens cause a validation error.

To verify **one file** without processing the full corpus:

```bash
mkdir -p results/sb-smoke
python prepare_hj_benchmark.py \
  --language smallbasic --file 01_HelloWorld.sb \
  --executable "$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe" \
  --mode 0 \
  --output results/sb-smoke/prepared.jsonl
python map_hj_candidates.py \
  --input results/sb-smoke/prepared.jsonl \
  --output results/sb-smoke/cases_mapped.jsonl
```

**Verified integration example:** this single-file parser call produced **6 prepared cursor rows** for `01_HelloWorld.sb` in a clean WSL clone, with **no API calls**. Each output file is intentionally **non-overwriting**. If it already exists, choose a different filename/output directory instead of deleting or modifying prior research artifacts.

### 6.2 C: reproducible HJ batch → engine input

```bash
# Reads C source corpus, corresponding HJ reference reports and grammar.
python prepare_c_batch.py --out results/c-prepared

# Inspect the batch summary before selecting experiment positions.
python - <<'PY'
import json
from pathlib import Path
p = Path('results/c-prepared/summary.json')
if not p.is_file():
    raise SystemExit('C batch incomplete/missing: inspect summary.partial.json and logs')
s = json.loads(p.read_text())
print('reports:', s['reports'], 'files_prepared:', s['files_prepared'])
print('failures:', len(s['failures']), 'totals:', s['totals'])
PY
```

Defaults: HJ base `$HOME/PL/codecompletion_benchmarks/smallbasic`, source directory `$HOME/PL/codecompletion_benchmarks/c/TEST`, C grammar `$HOME/PL/tree-sitter-c/c.so`, reference reports `<HJ-base>/tree-sitter/reports/c`. Override any location explicitly:

```bash
python prepare_c_batch.py \
  --out results/c-alternative \
  --hj-base "$HOME/PL/codecompletion_benchmarks/smallbasic" \
  --source-dir "$HOME/PL/codecompletion_benchmarks/c/TEST" \
  --grammar "$HOME/PL/tree-sitter-c/c.so" \
  --report-dir "$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/reports/c"
```

**C preparation is a corpus operation**: it may be expensive in CPU/time because it invokes HJ per position, but never sends a paid LLM request. It refuses to reuse an output directory with an existing `summary.json`, and the engine checks file hashes. The original lab C batch covered 106 report/source files, but this is **dataset-version dependent**. `prepare_c_batch.py` produces `summary.json`, per-source `prepared.jsonl` and eligible `cases.jsonl`; the latter are then read by `run_c.py`. **C-preparer executable caveat:** unlike the general HJ preparer, `prepare_c_batch.py` currently checks for `<HJ-base>/tree-sitter/TreeSitterCutFile` (without `.exe`) and does **not** expose a separate `--executable` flag; a working `.exe` alone does not satisfy that precondition. Use a compatible executable at the required path or an already verified C batch. Do not rename/copy native binaries blindly without confirming OS/architecture compatibility. Historically collected reference end boundaries remain **unverified**, particularly important for optional C fill-in-middle.

### 6.3 Reference provenance / boundary audit

```bash
python -m benchmark_v2 boundaries --language smallbasic \
  --input results/sb-full/prepared.jsonl --limit 8
python -m benchmark_v2 boundaries --language c \
  --input results/c-prepared --limit 8
```

This read-only audit checks hashes, original source positions, HJ reference report linkage, rank/state evidence and conservative lexical boundaries. It **does not establish AST or grammar-unit completeness**. See [`docs/PHASE3_REFERENCE_AUDIT.md`](docs/PHASE3_REFERENCE_AUDIT.md).

## 7. Prepare experiment cohorts (offline)

Once the HJ JSONL/batch exists, the two entrypoints share the same CLI:

```bash
# One cursor (smoke) from the respective prepared inputs — no API.
python run_c.py --scope smoke --stage prepare --model gpt
python run_smallbasic.py --scope smoke --stage prepare --model grok

# Frozen six-cursor pilot, same cursor identities for both providers.
python run_c.py --scope pilot --pilot-size 6 --pilot-seed 2026 --stage prepare --model gpt
python run_c.py --scope pilot --pilot-size 6 --pilot-seed 2026 --stage prepare --model grok

# Frozen held-out study, 30 NEW cursors per language.
for lang in c smallbasic; do
  for model in gpt grok; do
    python "run_${lang}.py" --scope study --study-size 30 --study-seed 2027 \
      --stage prepare --model "$model"
  done
done
```

Study selection explicitly reconstructs and **excludes** the six-cursor 2026 pilot from the same HJ source. The deterministic stratified selection seeks lexical/category and **file diversity**; it is **not a random probability sample**. All models of the same language must have the same `selection_sha256` in their study `cohort.json`. The stored `cohort.json` also records excluded pilot identities/hash. Do **not** compare provider runs drawn from different cohorts.

A 30-cursor study with nine conditions/repetitions per cursor schedules **270 requests for each language/model**. Preparation writes cases and manifests but contacts no model. A `--dry-run` previews the selection without API calls or writing new cases.

## 8. Run generation, resume, and report

> **Paid operation:** the following `--stage run` examples send requests to the selected provider. Review model identity, account billing, task count, frozen prompt profile and request limit first. There is no monetary spending cap in this CLI; `--max-api-requests` limits **scheduled request count**, not currency.

```bash
# No API requests: inspect the planned run first.
python run_c.py --scope study --stage run --model gpt \
  --max-api-requests 270 --yes --dry-run

# Paid: exactly the prepared C/GPT-4o study configuration.
python run_c.py --scope study --stage run --model gpt \
  --max-api-requests 270 --yes
```

For the other combinations, reuse the same flags with `run_c.py`/`run_smallbasic.py` and `--model grok`/`gpt`. You must prepare the relevant cohort first. The default API budget is **200**, so the 270-request main-study run will be **blocked** unless you explicitly increase it. `--scope full` can schedule extremely large numbers of requests; never run full generation merely to test a parser or installation.

**Resume:** repeat the **same command and configuration** after an interrupted request. `engine.py` reads successful `task_id` records and skips them, validates immutable manifests, and refuses to mix altered cases, prompts, model IDs or responses. Unsuccessful/incomplete responses do not count as completed. Do **not** edit `responses.jsonl`, `manifest.json`, `scores.csv` or the stored case files to force a resume.

**Generate report for a completed run** (requires the *same version and input paths that created its Run ID*):

```bash
python run_c.py --scope study --stage report --model gpt --no-plots
```

For **historical runs whose code fingerprint changed during refactor**, do **not** use `--stage report` to recreate the old ID. Read their saved reports using the commands in Section 10. Every new code version can generate a different Run ID even with identical user flags.

### 8.1 Full corpus execution (advanced, high-cost)

`--scope full` selects **every eligible cursor from the prepared HJ input**, not every byte offset or every source file stored under `~/PL`. For the standard Top-3 experiment, eligibility requires `status=prepared`, exactly one reference, and at least three candidate structures. A full run follows the **same frozen 9-request-per-cursor protocol** as the study: Without ×3 + HJ guided rank 1/2/3 ×1 each + Ideal ×3. Top-3 in the reports is an **oracle evaluation over the three guided outputs**, not an extra model request or an automated deployable choice.

**Verified offline `--scope full --stage prepare --dry-run` on the current prepared inputs:**

| Language | Eligible cursor positions | Expected provider requests / model | Input grouping shown by CLI |
| --- | ---: | ---: | --- |
| C | 43,668 | **393,012** | `106 files` (C case files/source groups) |
| Small Basic | 1,053 | **9,477** | `1 files` (**one combined `cases_mapped.jsonl` input group**, **not one `.sb` program**) |
| Both | **44,721** | **402,489** | Per model; twice this total for two models |

These are verified **selection/request counts, not completed paid runs**. The original Small Basic paired dataset covered 27 `.sb` source/report pairs; the CLI groups those cases into one mapped JSONL. Your available sources, reference reports, and Top-K setting can change the eligible counts. This check does not verify every native HJ parser invocation or all paid API requests end to end.

**Recommended offline workflow** (from the `code-completion-lab` root, with `.venv` activated; first complete [Section 6](#6-prepare-the-hj-data-offline)):

```bash
# Preview selections without API requests or new output files.
python run_c.py --scope full --stage prepare --model gpt --dry-run
python run_smallbasic.py --scope full --stage prepare --model gpt --dry-run

# Optional: persist all frozen prepared LLM cases, still without an API call.
# Only run when storage and processing time are acceptable.
python run_c.py --scope full --stage prepare --model gpt
python run_smallbasic.py --scope full --stage prepare --model gpt
```

**Do not mistake the default budget for batching:** `--max-api-requests 200` is a **whole-run scheduled-request safeguard**. It does **not** execute the first 200 requests of a 393,012-request plan. There is currently **no cursor-chunk/offset selector for `--scope full`** and no currency-based spending cap. A paid full run requires explicitly raising `--max-api-requests` above the **entire** planned total and passing `--yes`; it can be extremely expensive and slow. **We do not recommend doing this for routine validation.** The existing engine can resume completed tasks after interruption, but `--max-api-requests` still compares against the total planned requests upon resumption, not only the pending ones.

Once a full run is prepared, the **paid execution pattern** (illustrative, **do not execute without budget approval, quotas, and adequate storage**) is:

```bash
# PAID / LARGE: C–GPT-4o (393,012 planned API requests at this dataset version).
python run_c.py --scope full --stage run --model gpt \
  --max-api-requests 393012 --yes --no-plots

# PAID / LARGE: Small Basic–GPT-4o (9,477 planned API requests).
python run_smallbasic.py --scope full --stage run --model gpt \
  --max-api-requests 9477 --yes --no-plots
```

To use Grok, **repeat both `--stage prepare` and the paid `--stage run` with `--model grok`**, confirming new Run IDs and request budgets for that provider. Re-run the **identical** command to resume a partially completed run; preserve its source, prepared cases, model ID, parameters, code version, and output directory. A failed/incomplete generation stops execution but retains existing completed responses. If you need affordable incremental paid batches, implement and test **explicit cursor chunking and per-batch budget controls in a subsequent version** rather than assuming `--max-api-requests` provides chunking.

After completion, the existing reporting commands can read the saved output:

```bash
# Report only if this exact code/configuration matches the prepared Run ID.
python run_c.py --scope full --stage report --model gpt --no-plots
python run_smallbasic.py --scope full --stage report --model gpt --no-plots

# Read-only summaries (requires completed full-run score files).
python -m benchmark_v2 results --scope full
python -m benchmark_v2 stats --scope full --metric unigram
python -m benchmark_v2 audit --language c --model gpt --scope full --limit-cases 0
```

For a full workflow checklist, expected output artifacts, non-eligible-case accounting, disk/cost precautions and interpretation, see **[`docs/FULL_CORPUS_EXECUTION.md`](docs/FULL_CORPUS_EXECUTION.md)**. Because the source, report and candidate DB assets live under `~/PL`, **do not upload them to Git automatically**; this repository stores generated data under the Git-ignored `results/` directory.

## 9. All runner options and advanced experiments

The complete authoritative argument list is always available through `python run_c.py --help` and `python run_smallbasic.py --help`. Both forward to `benchmark_v2.cli.main(language)`.

| Argument | Default / allowed values | What it changes |
| --- | --- | --- |
| `--model` | `grok` default; `gpt`, `gpt4o`, `gptoss` | Model/provider and required API key (`gpt` is GPT-4o alias) |
| `--scope` | `smoke` default; `pilot`, `study`, `full` | One cursor, deterministic pilot, held-out cohort, or **all eligible positions** |
| `--stage` | `all` default; `prepare`, `run`, `report` | Prepare tasks, generate/score, report, or all steps. **Bare runner may call API.** |
| `--profile` | `fragment-v1` default; `lab`, `monir-style` | Prompt design; different profile = different experiment |
| `--context` | `last10` default; `full` | C context, full prefix or last 10 lines; Small Basic always full |
| `--completion` | `prefix` default; `infill` | C FIM is restricted/experimental; Small Basic only supports prefix |
| `--top-k` | `3` default; `10`, `20` | HJ candidate ranks used as separate guided arms |
| `--ideal-repetitions` | `3` default; `1` | Number of ideal-hint generations (changes task count) |
| `--pilot-size`, `--pilot-seed` | `6`, `2026` | Frozen pilot selection; meaningful only for `pilot` |
| `--study-size`, `--study-seed` | `30`, `2027` | Held-out selection; meaningful only for `study` |
| `--cursor` | no default | Specific cursor byte for smoke selection; cannot combine with pilot/study/full |
| `--include-byte0` | off | Permit initial-byte cursor when no nonzero smoke cursor exists |
| `--temperature` | `0.5` | API sampling temperature (0–2) |
| `--max-tokens` | `512` | Provider output token cap |
| `--delay` | `0` | Seconds between pending API requests |
| `--max-api-requests` | `200` | Stop if scheduled run exceeds count limit |
| `--yes` | off | Explicit consent for paid pilot/study/full requests |
| `--dry-run` | off | Preview config/tasks **without API or new output** |
| `--output-root` | `results/v2` | Where run directories are stored |
| `--c-batch-dir` | autodetect `results/c-prepared` then historical fallback | Select C prepared batch |
| `--sb-input` | `results/sb-smoke/cases_mapped.jsonl` for smoke; `results/sb-full/cases_mapped.jsonl` otherwise | Select mapped SB cases |
| `--no-plots` | off | Skip optional graph generation |
| `--allow-topk-full` | off | Explicitly opt into Top-10/20 full-scope experiment |
| `--allow-unverified-fim` | off | Explicitly accept unverified C reference gap end |

**Examples beyond the frozen main study** (these deliberately create **new configurations**, not comparable main-study repeats):

```bash
# Smoke: inspect the first eligible nonzero C cursor, no API.
python run_c.py --scope smoke --stage prepare --model grok

# C with all preceding context rather than last ten lines.
python run_c.py --scope smoke --stage prepare --context full --model gpt

# Experimental Top-10: read frozen HJ DB and validate SHA/rank parity.
python run_c.py --scope smoke --stage prepare --top-k 10 --model gpt

# Historical C fill-in-middle prompt: ONLY for explicit gap exploration.
python run_c.py --scope smoke --stage prepare --profile lab --context full \
  --completion infill --allow-unverified-fim --model gpt

# Frozen Small Basic historical prompt, or a Monir-style adaptation.
python run_smallbasic.py --scope smoke --stage prepare --profile lab --model grok
python run_smallbasic.py --scope smoke --stage prepare --profile monir-style --model grok

# Point the runner at a different mapped case file / C batch.
python run_smallbasic.py --scope smoke --stage prepare \
  --sb-input results/sb-full/cases_mapped.jsonl
python run_c.py --scope smoke --stage prepare --c-batch-dir results/c-alternative
```

`fragment-v1` **study** is intentionally frozen to prefix-only and Top-3. The C FIM path requires full context and `--profile lab`; its suffix depends on HJ reference end bytes which have **not** been validated as complete grammar spans. The study is not interchangeable with Monir's historical C11 experiment or with the old experimental six-task wrapper. For more background, consult [`docs/FINAL_FRAGMENT_PROTOCOL.md`](docs/FINAL_FRAGMENT_PROTOCOL.md).

## 10. Results, statistics, provenance, and outputs

All score-report commands below are **read-only** unless `--export` is explicitly supplied. `results/` is excluded from Git, so a fresh clone shows **no raw study outputs** until they are regenerated or securely transferred.

```bash
# Summaries: raw 1-gram precision, raw SequenceMatcher, sentence BLEU.
python -m benchmark_v2 results --scope study
python -m benchmark_v2 results --scope study --paths
python -m benchmark_v2 results --scope study --metric unigram --aggregation macro_file
python -m benchmark_v2 results --scope study --all-runs

# Per-cursor paired effect; bootstrap clusters are SOURCE FILES.
python -m benchmark_v2 stats --scope study --metric unigram
python -m benchmark_v2 stats --scope study --metric all --bootstrap 5000 --seed 2027
python -m benchmark_v2 stats --scope study --language smallbasic --model gpt
python -m benchmark_v2 stats --scope study --export results/audits/study/new_stats.csv

# Formatting and exact-match audit (read-only, prints no long case details).
python -m benchmark_v2 audit --language all --model all --scope study --limit-cases 0
python -m benchmark_v2 audit --language c --model gpt --scope study \
  --run-id d398e7997c07c5f7 --limit-cases 5

# Reference provenance audits on HJ raw prepared cases, not LLM responses.
python -m benchmark_v2 boundaries --language all --limit 0
```

Report CLI flags: `results` supports `--root`, `--metric {unigram,sequence,bleu,all}`, `--aggregation {micro_cursor,macro_file,paired_micro}`, `--scope {smoke,pilot,study,full,all}`, `--all-runs`, `--paths`; `stats` supports `--root`, `--scope {pilot,study,full}`, `--metric`, `--language`, `--model`, `--bootstrap`, `--seed`, `--all-runs`, `--export`; `audit` supports `--root`, `--language`, `--model`, `--scope`, `--run-id`, `--limit-cases`, `--show-predictions`, `--show-prompts`; `boundaries` supports `--language`, `--input` (repeatable), `--root`, `--limit`, `--export`. For exact current options, use `python -m benchmark_v2 <subcommand> --help`.

### Data contract: one cursor and its recorded provenance

Each JSONL record represents a specific `(source file, cursor byte offset)` and carries the fields needed to reconstruct what was shown to the model. Important fields include:

| Field | Meaning / research constraint |
| --- | --- |
| `case_id`, `cursor_byte`, `source_relative` | Stable case identity and exact byte insertion location; a byte offset is **not** a character index |
| `source_path`, `source_sha256`, `prefix` | Original source and the verified text preceding the cursor |
| `reference_path`, `reference_sha256`, `references` | HJ report identity, reference text/structure and byte span; reference text is **scoring-only** |
| `provenance` | Native parser path/hash, grammar path/hash, candidate DB path/hash and HJ mode |
| `states`, `candidate_count`, `top_candidates` | Parser states and frequency-ranked HJ candidate structures |
| `token_mapping_path`, `token_mapping_sha256` | Small Basic mapping provenance when token translation is applied |
| `conditions[]`, `prompt_sha256` | Frozen per-arm prompts, structure hints, repetition count and prompt identity |
| `experiment_version`, `prompt_profile` | Protocol context preventing silent cross-version comparisons |

`prepare_hj_benchmark.py` produces the source-level records; `map_hj_candidates.py` preserves the retrieval evidence while adding readable Small Basic hints; `benchmark_v2.cases.prepare_case()` creates the final conditions/tasks. Source SHA mismatch and missing/unresolved mapping are **errors**, not quietly repaired examples.

**Important files inside a run:**

```text
results/v2/<language>/<model>/<scope>/<run_id>/
├── cohort.json                           # pilot/study selection SHA and exclusion record
├── files/<source-hash>/
│   ├── cases.jsonl                       # frozen per-cursor condition prompts
│   ├── preparation_meta.json             # SHA of prepared cases + config
│   ├── manifest.json                     # immutable task IDs and generation settings
│   ├── responses.jsonl                   # raw provider replies and statuses
│   ├── scores.csv                        # raw task-level metrics
│   └── scores_meta.json                  # checksum linkage to cases/responses
└── analysis/
    ├── rq2_by_case.csv                   # per-cursor metric and all four RQ2 arms
    ├── rq2_table.csv                     # micro/file/paired aggregate rows
    ├── retrieval_coverage.csv            # HJ Top-K retrieval coverage by source
    └── ...                               # optional figures/reports
```

The `run_id` is derived from **configuration + selected case IDs + implementation file hashes**. Do not mistake two IDs from different source commits for the same experiment. HJ prepared case IDs also include relevant file provenance. Keep source corpus, reference reports, grammar and candidate DB versions documented. Provider responses may contain source code, prompts and account-specific metadata; **do not upload them publicly without reviewing rights and privacy**.

### Frozen held-out main-study results

The following published figures are **micro-average percentages across the 30 selected cursors**; they are descriptive within the chosen cohort, not population estimates.

| Language / model | Unguided 1-gram | Top-1 1-gram | Ideal 1-gram | Top-3 oracle 1-gram |
| --- | ---: | ---: | ---: | ---: |
| C / GPT-4o | 26.58 | 37.08 | 42.14 | 46.59 |
| C / Grok | 16.18 | 25.64 | 28.80 | 30.67 |
| Small Basic / GPT-4o | 45.42 | 36.07 | 51.55 | 47.73 |
| Small Basic / Grok | 34.53 | 26.32 | 47.32 | 31.57 |

Paired **Top-1 minus Unguided**, raw SacreBLEU 1-gram precision (percentage-point difference; source-cluster bootstrap, 95% percentile CI):

| Language / model | Delta (pp) | 95% CI (pp) | Improved / worse / equal cursors |
| --- | ---: | --- | ---: |
| C / GPT-4o | +10.50 | [+2.91, +18.86] | 19 / 7 / 4 |
| C / Grok | +9.46 | [+3.78, +16.77] | 19 / 3 / 8 |
| Small Basic / GPT-4o | -9.35 | [-23.10, +3.57] | 7 / 8 / 15 |
| Small Basic / Grok | -8.21 | [-17.94, +0.27] | 7 / 9 / 14 |

**Interpretation:** Top-1 improved the observed average for both C models; those conditional intervals were entirely above zero. For Small Basic, average effects were negative but the reported 1-gram intervals included zero, so **a general harmful effect is not established**. Statistical intervals are *conditional on a deterministic, file-diverse sample*; they do not prove population-level generalization. Top-3 can be stronger than Ideal in an aggregate table because it selects the best **model output** among three candidates using the reference, not because HJ's ranking exceeds the ground-truth structure.

Historical main-study run IDs (from original report, not regenerated in the cleaned-up code): C/GPT-4o `d398e7997c07c5f7`, C/Grok `13f8968b8fec8990`, Small Basic/GPT-4o `d6be04607faaf086`, Small Basic/Grok `e4f5901177dfa785`.

## 11. Relationship to HJ and Monir

This study **combines research components**, rather than claiming to be a fork of or substitute for either reference project:

| Reference project | Direct input or conceptual contribution | What this repository does differently |
| --- | --- | --- |
| **HJ: [`SwlabTreeSitter/tree-sitter`, `Candidate_Collection`](https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection)** | Native parser-state retrieval (e.g. `@@PREDICT` states) for each cursor; HJ-collected reference reports | Calls the parser as an external process; verifies source offsets/hashes, labels uncertain reference spans, stores provenance and separate benchmark tasks |
| **HJ: [`chaendaya/code-completion-extension`](https://github.com/chaendaya/code-completion-extension)** | Frequency-weighted grammar candidates in `resources/<language>/candidates.json`; structure rank order | Reads the candidate database **offline**, aggregates frequencies across active states, checks Top-K rank/hash parity, maps Small Basic internal tokens; does not run the editor extension |
| **Monir: [`monircse061/ChatGPT-Code-Completion-Work`](https://github.com/monircse061/ChatGPT-Code-Completion-Work)** | Comparative experimental idea: unguided vs candidate guidance, ideal guidance, Top-3-oriented analysis; metric and visualization motivation | Re-implements an auditable 9-request-per-cursor protocol for C and Small Basic with model-independent frozen cohorts, raw scoring, fixed prompts, immutable resumes, held-out selection, and paired source-cluster CI |

### Technical lineage: where the ideas enter the implementation

| Previous research component | Concrete current path or operation | Retained versus newly implemented |
| --- | --- | --- |
| HJ `TreeSitterCutFile.cpp`/native executable and `@@PREDICT` output | `prepare_hj_benchmark.retrieve()` | **Uses an external HJ binary and output convention**; our Python subprocess isolation, timeout, and provenance checks are adapters |
| HJ extension's `resources/{c,smallbasic}/candidates.json` | `retrieve()` and `cases.ranked_pool()` | **Reuses actual external HJ candidate data** and its ranked frequency concept; recomputes Top-10/20 with DB SHA and Top-3 parity safeguards |
| HJ extension token-mapping concepts (including `mapLoader.ts`) | `map_hj_candidates.map_structure()` | **Python adaptation** of token translation; unknown tokens are rejected rather than guessed |
| HJ's original reference reports | `references_at()` and `boundaries.inspect_case()` | **Reads collected HJ references**; adds source/hash/report validation and explicit incomplete-span warning, not a new ground-truth collection mechanism |
| Monir's C11/SB comparisons and Top-3 experiment scripts | `cases.prepare_case()` + `rq2_report.aggregate_case()` | **Methodological adaptation** into a uniform multi-provider 9-task protocol with exact arm accounting |
| Monir's result-analysis/graph scripts (e.g. `C11_Analysis.py`, `SB_Analysis.py`, `drawAnalysisGraph.py`) | `tools/rq2_report.py`, `benchmark_v2.__main__`, `stats.py` | **New integrated analysis pipeline**: saved per-cursor/aggregate metrics, retrieval coverage, source-cluster bootstrap, non-overwriting exports |

The HJ extension is a **data/resource provider** for this batch benchmark. We do not launch its editor UI, extension host, or completion interaction loop. Monir is a **research-design precedent**, not the codebase that executes model requests here. A `--profile monir-style` option exists for *adapted* prompt experiments, but is **not asserted to be byte-identical** to a historical Monir prompt or API payload. A complete upstream source-to-source copyright audit would require pinning and inspecting exact historical commits.

**Attribution precision:** The current `benchmark_v2/` engine and HJ preparation adapters were implemented for this project. **No claim is made that any active Python function is a byte-for-byte copy of a Monir file**, because an exhaustive line-by-line copyright/provenance comparison of all historical Monir versions was not performed. The preserved optional `--profile lab` prompt templates were carried over from this lab's earlier local implementation; their precise upstream authorship has not been independently established. Conversely, the HJ parser executable, grammar and candidate DB remain **external HJ assets**, not newly implemented components in this repository. The names and conceptual four-arm comparison are retained to make research lineage visible, not to imply identical prompts/API settings/evaluation datasets.

Notable adaptations and additions: separate current entrypoints for C and Small Basic; reference text kept out of generation prompts; raw-cursor/source checksum checks; traceable HJ parser state and DB hashes; seed-fixed model-matched pilot/study cohorts with pilot exclusion; nine tasks per position; safe API count guard + resume; explicit oracle labeling; original versus normalized audit separation; retrieval coverage based on unfiltered prepared rows where available; and paired bootstrap by source file. The original Monir historical scores cannot be directly reproduced or compared numerically using these different prompts/models/corpora without an explicitly matched protocol.

## 12. Developer architecture and core functions

Use [`docs/ARCHITECTURE_AND_FUNCTIONS.md`](docs/ARCHITECTURE_AND_FUNCTIONS.md) as the **function-level code map**. The core call chain is:

```text
run_c.py / run_smallbasic.py
  → benchmark_v2.cli.main(language)
     → discover_sources() → pilot.stratified_{pilot,study}() / select_cases()
     → cases.prepare_case() → cases.context_parts() + cases.build_prompt()
     → cli.safe_write_cases() + safe_write_cohort()
     → engine.run_one_group()
        → build_tasks() → providers.payload_for() → providers.call_api()
        → scoring.score_records() → scoring.write_csv()
     → cli.report_group() → tools/rq2_report.py
        → aggregate_case() → compute() → rq2_by_case.csv / rq2_table.csv
  → python -m benchmark_v2 {results,stats,audit,boundaries}
     → stats.collect_stats() / audit.audit_run() / boundaries.audit_prepared()
```

**CORE functions worth reading first:** `benchmark_v2.cli.main` (orchestration and guardrails), `prepare_hj_benchmark.retrieve` (HJ subprocess and state parsing), `prepare_hj_benchmark.references_at` (source-slice validation), `benchmark_v2.cases.prepare_case` (experimental arms and prompt freeze), `benchmark_v2.engine.run_one_group` (append-only resumable generation), `tools.rq2_report.aggregate_case` (four-arm metric aggregation), and `benchmark_v2.stats.paired_summary` (source-cluster confidence interval).

## 13. Validation, troubleshooting, and limitations

**Validation before any paid experiment:**

```bash
python -m unittest discover -s tests -q
python run_c.py --help
python run_smallbasic.py --help
python prepare_c_batch.py --help
python prepare_hj_benchmark.py --help
python map_hj_candidates.py --help
python -m benchmark_v2 stats --help
python -m benchmark_v2 results --help
git status --short
```

A clean-clone validation of this research branch reported **88 passing tests**, all current entrypoint help commands working, and a **six-position** real HJ Small Basic preparation. This is evidence of baseline functionality, **not** proof that every C corpus item or paid provider request will succeed on every environment.

| Symptom | Likely cause / action |
| --- | --- |
| `No module named sacrebleu` | Wrong Python interpreter. Activate `.venv`, verify `which python`, reinstall requirements there. |
| `Mapped Small Basic cases missing` | Run raw `prepare_hj_benchmark.py` **and** `map_hj_candidates.py`; use correct scope-specific `results/sb-.../`. |
| `C preparation summary missing` | Run `prepare_c_batch.py` on the external C corpus; inspect `summary.partial.json` and logs for failures. |
| `TreeSitterCutFile` missing or not executable | Check external PL layout, native executable/OS compatibility, `--executable`/`--hj-base`. |
| `No source files have matching HJ reference reports` | Check HJ report naming and `--source-dir`; `--skip-missing-reports` avoids unpaired files, not bad report data. |
| `Output already exists` | Preparation is non-overwriting; use a **new** output name, do not destroy old data. |
| `Scheduled ... exceed --max-api-requests` | Safety guard: preview with `--dry-run`, then explicitly choose request budget if willing to pay. |
| Provider `401` / key missing | Check provider-specific key, model alias, account access, and variables exported into the active shell. |
| Resume `Manifest mismatch` / `cases SHA mismatch` | Configuration, code hash, source or prepared cases changed; use original version/run or start a **new ID**. Do not override it. |
| Results CLI has no reports on a fresh clone | `results/` is intentionally Git-ignored. The public `docs/main_study_paired_stats.csv` is a summary, not raw data. |
| Charts fail | Install `matplotlib` in `.venv`, or use `--no-plots`; core metric scoring uses `sacrebleu`. |

**Methodological limits:** the selected pilot/study are non-probability cohorts; reference spans are not universally grammar-certified; models may **over-complete** short HJ references; scores reflect lexical similarity more than functional correctness; C and Small Basic have unequal context policies; Top-3/Ideal use privileged reference information; model/API version behavior can change; external HJ asset distribution is outside this repository. Do not normalize by cropping to the reference length, silently remove code fences before official scoring, call Top-3 deployable, or treat the three repetitions as three independent cursors.

**Repository/security:** `.env`, `.venv/`, and `results/` are excluded by `.gitignore`. Do not stage keys, HJ corpora with unverified redistribution permissions, provider raw responses or locally generated native binaries. Archived documents in [`docs/archive/`](docs/archive/) preserve development history and can contain retired commands; **this README is the authoritative current setup guide**. Do not use blanket cleanup commands to delete a contributor's untracked local files.

## 14. Research references and project notes

- [HJ Candidate Collection: modified Tree-sitter](https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection)
- [HJ Code Completion Extension](https://github.com/chaendaya/code-completion-extension)
- [Monir: ChatGPT Code Completion Work](https://github.com/monircse061/ChatGPT-Code-Completion-Work)
- [Frozen experimental protocol](docs/FINAL_FRAGMENT_PROTOCOL.md)
- [HJ reference provenance / boundary audit](docs/PHASE3_REFERENCE_AUDIT.md)
- [Developer architecture and all core functions](docs/ARCHITECTURE_AND_FUNCTIONS.md)
- [Published paired-study statistics](docs/main_study_paired_stats.csv)
- [Historical implementation archive](docs/archive/)

For reproducibility, record the Git commit, platform, Python version, HJ binary and grammar hashes, dataset/report/candidate DB hashes, provider model IDs, prompt profile, selection SHA and run ID with any publication. The **current README documents the code path**; a complete external replication must also obtain the correctly matched HJ assets and ensure the APIs are still available.
