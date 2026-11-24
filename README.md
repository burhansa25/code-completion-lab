# Code Completion Lab

Reproducible **HJ grammar-guided code-fragment completion** benchmark for **C** and **Microsoft Small Basic**. Two entrypoints use a shared generation/scoring engine. The reference implementation is the frozen `fragment-v1` experimental protocol. Development logs and retired provider runners are **not** part of the current interface.

> **Research interpretation:** A reference is an HJ-collected fragment, not necessarily a full statement or an AST-certified completion. Text similarity does not establish program correctness. `WithinTop3Guide` is an *oracle* that selects the best candidate **after** looking at the reference; it is not a deployment-ready ranking policy.

## Quick start (Ubuntu / WSL)

```bash
git clone https://github.com/burhansa25/code-completion-lab.git
cd code-completion-lab
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -q
```

The repository's Python runners do **not** download the HJ parser, candidate databases, source corpora, or reference reports. These are separate research assets. See [Preparing HJ data](#preparing-hj-data) below. The preparer runs the external HJ binary; its architecture/permissions must be compatible with Ubuntu/WSL. Source/reports must be accessible in the same environment.

For an existing prepared dataset, the following commands make **zero API calls**:

```bash
python run_c.py --stage prepare --model grok
python run_smallbasic.py --stage prepare --model gpt
python -m benchmark_v2 results
python -m benchmark_v2 stats --scope study
python -m benchmark_v2 audit --language all --model all --scope study --limit-cases 0
```

`results` and `stats` read only existing results; they report when no saved results are available. The `--stage prepare` commands require the corresponding locally prepared HJ inputs; they do not call LLMs.

### Main commands

| Command | Purpose |
| --- | --- |
| `python run_c.py` | C benchmark; defaults to one smoke cursor |
| `python run_smallbasic.py` | Small Basic benchmark; defaults to one smoke cursor |
| `python -m benchmark_v2 results` | Read saved summary scores; no API |
| `python -m benchmark_v2 stats --scope study` | Paired Top-1 minus Without + source-cluster bootstrap CI; no API |
| `python -m benchmark_v2 audit --language all --model all --scope study` | Inspect saved predictions and formatting |
| `python -m benchmark_v2 boundaries --language all` | Check source/reference provenance, not AST completeness |

The two generation entrypoints default to `--stage all`: **without** `--stage prepare` or `--dry-run`, they may submit paid requests. Always specify a stage and read the count before authorizing an API run.

## Experiment protocol

Each eligible cursor has **9 tasks/model** with `--top-k 3 --ideal-repetitions 3`:

- Without Guidance: 3 independently requested completions.
- HJ guided ranks 1, 2 and 3: one completion each.
- Ideal Guidance (reference **structure**, not reference text): 3 completions; oracle diagnostic.

`WithoutGuide` is the mean of its three repetitions per cursor. `WithTop1Guide` uses ranked candidate 1; `WithinTop3Guide` is the **reference-scored best of the three guided predictions**, not the mean. Raw SacreBLEU 1-gram precision and SequenceMatcher are primary reported metrics; Sentence BLEU and exact-match/format checks are additional diagnostics. The same frozen prompts are used for both model providers, apart from the supplied structural hint and language-specific context. C defaults to last-10-line prefix-only; Small Basic uses full prefix. Do not pool cross-language performance as if context and corpus were identical.

For a reproducible six-cursor exploratory pilot (no API):

```bash
python run_c.py --scope pilot --pilot-size 6 --pilot-seed 2026 --stage prepare --model grok
python run_c.py --scope pilot --pilot-size 6 --pilot-seed 2026 --stage prepare --model gpt
```

For a **held-out** 30-cursor study, the default `--study-seed 2027` and `--study-size 30` exclude the previously frozen 6-cursor pilot cohort for the same language:

```bash
python run_smallbasic.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model grok
python run_smallbasic.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model gpt
```

Each study combination plans **270 API requests**. Generation is **paid** and requires BOTH `--yes` and an explicit request budget above the default cap (200). For example, after inspecting the cohort, provider balance and cost:

```bash
# No network: review exact run ID and number of planned requests first.
python run_c.py --scope study --stage run --model gpt --max-api-requests 270 --yes --dry-run

# Only run after you explicitly approve provider charges:
# python run_c.py --scope study --stage run --model gpt --max-api-requests 270 --yes
```

`--scope full` means **all eligible positions**, NOT a held-out study sample. It may schedule hundreds of thousands of requests. `--dry-run` performs no API calls and writes no new results. Existing tasks are resumed by Run ID/manifest without re-sending completed requests. Never modify an existing run's cases or scores.

## Provider configuration

Supported aliases: `grok` (xAI), `gpt`/`gpt4o` (OpenAI GPT-4o), `gptoss` (Groq). They require `XAI_API_KEY`, `OPENAI_API_KEY`, or `GROQ_API_KEY` respectively. Keys belong in **environment variables**, not in source code, a shell command pasted to chat, or Git.

```bash
# Example: load an untracked .env containing KEY="value" lines.
set -a; source .env; set +a
```

Protect `.env` (`chmod 600 .env`) and ensure it is excluded from Git; do not upload provider responses or secrets unintentionally. A ChatGPT subscription does not supply OpenAI API credit.

## Preparing HJ data

Required external components: HJ `Candidate_Collection` parser and candidate database, its generated C/Small Basic reference reports, original source files, and compatible grammar shared objects. The original academic project is [SwlabTreeSitter/tree-sitter (Candidate_Collection)](https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection). The earlier setup references a separately provided [research asset archive](https://drive.google.com/drive/folders/1d95-yj7ysrPYE5agrE9aQ9HM_7niqy9Q?usp=sharing); access and distribution rights depend on the asset owners.

By default, the preparers expect the historical lab layout under `~/PL/`:

```text
~/PL/
├── codecompletion_benchmarks/
│   ├── c/TEST/
│   └── smallbasic/
│       ├── tree-sitter/TreeSitterCutFile{,.exe}
│       ├── tree-sitter/reports/{c,smallbasic}/
│       ├── tree-sitter-smallbasic/SB_Sample/
│       └── code-completion-extension/resources/{c,smallbasic}/
└── tree-sitter-c/c.so
```

**C (offline):**

```bash
python prepare_c_batch.py --out results/c-prepared
# Use --source-dir, --hj-base, --grammar, --report-dir for different assets.
python run_c.py --stage prepare --model grok
```

`prepare_c_batch.py` writes the same HJ batch layout (`summary.json`, per-file `prepared.jsonl` and `cases.jsonl`) as the historical C preparer, **without its retired provider runner**. An existing `results/c-prepared` or `results/c-full-infill-v1` batch can be reused; do not overwrite it or mix source versions. Preparing the entire C corpus can be computationally expensive even though it makes no API calls.

**Small Basic (offline):**

```bash
mkdir -p results/sb-full
python prepare_hj_benchmark.py --all --skip-missing-reports \
  --executable "$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe" \
  --output results/sb-full/prepared.jsonl
python map_hj_candidates.py --input results/sb-full/prepared.jsonl \
  --output results/sb-full/cases_mapped.jsonl
python run_smallbasic.py --scope study --stage prepare --model grok
```

The Small Basic preparer may exit **2** when some positions have no verified reference, even if it wrote the expected JSONL. Inspect the summary and errors; do not blindly ignore exit codes. In the original lab assets, **27** sources had matching HJ reports while six did not; the `--skip-missing-reports` flag deliberately excludes those six. Mapping rejects unresolved generated grammar tokens. The engine independently filters to eligible cases.

For a one-source Small Basic smoke input, use `--file 01_HelloWorld.sb` instead of `--all --skip-missing-reports` and output under `results/sb-smoke/`.

## Results, statistics, and limitations

Outputs live in `results/v2/<language>/<model>/<scope>/<run_id>/`. Immutable manifests, cohort selection SHA-256, provider replies, raw scoring and per-case reports are kept separately. The CLI reads historical report directories even after source cleanup:

```bash
python -m benchmark_v2 results --scope study --paths
python -m benchmark_v2 stats --scope study --metric unigram
python -m benchmark_v2 stats --scope study --export results/audits/study/paired_stats_v2.csv
python -m benchmark_v2 audit --language c --model gpt --scope study --limit-cases 0
```

The bootstrap treats **cursor** as the paired observation and **source file** as the resampling cluster; repeated model generations are not independent cases. Intervals describe the selected deterministic/file-balanced cohort and do not guarantee generalization to all repository files. Compare model results only for identical selection hashes and matching prompt profiles. More details: [frozen protocol](docs/FINAL_FRAGMENT_PROTOCOL.md) and [reference audit limits](docs/PHASE3_REFERENCE_AUDIT.md).

**Legacy history:** The pre-refactor provider runners, six-task wrapper and old C batch runner were removed from the active working tree. They remain in earlier Git commits. C FIM and Small Basic lab prompt templates used for explicit historical comparisons are frozen under `benchmark_v2/prompts.py`. Removing old files changes the implementation fingerprint and hence the Run ID of any **new** generation; previously saved report directories retain their original IDs and must not be migrated or rewritten. Old `--stage report` cannot be used to recompute an old Run ID from changed source files; use the saved report and original commit instead.

Before releasing publicly, verify your external data distribution rights, local path overrides, Python dependencies, `.gitignore`, fresh-clone setup, and secret scanning. The external HJ binaries/corpora are not bundled in this source archive.
