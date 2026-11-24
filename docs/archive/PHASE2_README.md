# Code Completion Lab — Phase 2 / Unified RQ2 Engine

**Branch to patch:** `refactor/rq2-unified-engine` (user checkpoint `341c5e4`).
**Type:** Additive and reversible. The archive **does not overwrite** `run_c.py`, `run_smallbasic.py`, `benchmark_entry.py`, legacy HJ scripts, or existing results. This is **not** a full new clone of the repository.

This version is built against the available source snapshots of `prepare_hj_benchmark.py`, `map_hj_candidates.py`, `hj_benchmark_tools.py`, `run_hj_c_batch.py`, and the known prepared JSONL schema. Actual runtime compatibility with the current private WSL working tree and live APIs is **not verified**.

## 1. Installation in WSL

From the **root** of the existing working repository:

```bash
cd ~/code-completion-lab
git branch --show-current      # refactor/rq2-unified-engine
git status --short

# Change to the actual path of the ZIP you downloaded from ChatGPT
V2_ZIP="/mnt/c/Users/arras/Downloads/code-completion-lab-v2-phase2.zip"
unzip -l "$V2_ZIP"
unzip -n "$V2_ZIP" -d .

# Optional: when missing from current .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install matplotlib

# Verify the four original v1 Grok smoke files have not changed.
sha256sum -c BASELINE_SMOKE_SHA256.txt

# Regression/unit tests; no paid API calls
.venv/bin/python -m unittest discover -s tests -v
```

The previous 21 tests should remain, and the new patch adds 17 synthetic tests. The total expected count is ~38 when both earlier ZIPs were installed correctly. This patch was tested with the available **frozen legacy HJ modules**, not via real HJ/API calls.

## 2. Prepare (offline, zero API calls)

```bash
.venv/bin/python run_smallbasic_v2.py --stage prepare --model grok
.venv/bin/python run_c_v2.py --stage prepare --model grok
```

Both use previously prepared HJ assets. **Do not run `run_c.py` or `run_smallbasic.py` again to set up v2**. Their original results remain untouched.

Defaults:
- `--scope smoke`: select **one eligible nonzero cursor** (not byte=0); if none exists, pass `--include-byte0` explicitly.
- `--model grok`: xAI model used in original smoke runs.
- C: 10-line prefix **including cursor's partial line**; the exact Monir context slicing is not claimed byte-identical.
- Small Basic: full prefix, frozen `hj_benchmark_tools.make_prompt` and `map_hj_candidates.map_structure`.
- 9 task requests/cursor: 3 unguided + 3 individual Top-3 guidance + 3 ideal (oracle) guidance.
- Ideal guidance uses reference **structure**, not reference text.

The preparation prints a run ID and output directory, such as `results/v2/c/grok/smoke/<hash>/files/<hash>/cases.jsonl`. This is an independent run namespace. Source hashes are checked before C prompts are built. Prepared files become immutable for the selected configuration.

### Inspect the cases before paying for generation

```bash
find results/v2 -name cases.jsonl -print
```

Look for the selected nonzero `cursor_byte`, the `prompt_profile`, and each `conditions[].prompt`; make sure **reference completion text is not present in the ideal prompt** (unless the same text legitimately appears earlier in the prefix).

## 3. Paid smoke tests: 9 requests each

```bash
# These command calls will send new requests; use only after inspecting preparation.
.venv/bin/python run_smallbasic_v2.py --stage run --model grok
.venv/bin/python run_c_v2.py --stage run --model grok
```

The tool asks for confirmation `RUN 9` and then requests `XAI_API_KEY` securely if not already set. Optional `--yes` explicitly bypasses the prompt; don't use it until ready to pay for requests. `gpt` is an alias for GPT-4o (`gpt-4o`), **not** the latest GPT model:

```bash
.venv/bin/python run_smallbasic_v2.py --model gpt --stage prepare
.venv/bin/python run_smallbasic_v2.py --model gpt --stage run
```

API key requirements: `XAI_API_KEY`, `OPENAI_API_KEY`, or `GROQ_API_KEY` based on chosen model. Models supported: `grok`, `gpt4o`, `gpt` (alias), `gptoss` (Groq).

The generation engine uses a shared OpenAI-compatible REST client, streams one response record per task to JSONL, resumes completed tasks after interruptions, and verifies immutable task IDs/manifest before any scoring. An empty `finish_reason='stop'` response is included as an explicit `empty_completion` failure rather than silently dropped. Incomplete/truncated output stops the batch until rerun.

### Review/report existing v2 results without API

```bash
.venv/bin/python run_smallbasic_v2.py --stage report --model grok
.venv/bin/python run_c_v2.py --stage report --model grok
```

When `tools/rq2_report.py` from the previous RQ2 ZIP is installed, the run automatically produces `analysis/rq2_table.csv`, `analysis/rq2_by_case.csv`, and figures after successful scoring. To bypass plotting if matplotlib is unavailable, add `--no-plots`.

**The RQ2 reporter's `WithinTop3Guide` is an oracle maximum by metric**, not an online chooser. It reports both 1-gram SacreBLEU precision and full sentence BLEU-13a distinctly, alongside SequenceMatcher.

## 4. Other context and Top-K experiments

```bash
# Entire C prefix (still prefix-only)
.venv/bin/python run_c_v2.py --stage prepare --context full

# Historical C FIM, on a separate run ID; span completeness is unverified
.venv/bin/python run_c_v2.py --stage prepare --context full \
  --completion infill --allow-unverified-fim

# Inspect Top-20 structures on a single cursor (offline preparation only)
.venv/bin/python run_c_v2.py --stage prepare --top-k 20

# The Top-10/20 tasks are costly. Full generation requires explicit flags.
# Do NOT run this unless the retrieval coverage and cohort are ready:
# .venv/bin/python run_c_v2.py --scope full --top-k 20 --allow-topk-full --yes
```

Top-3 default: 9 tasks per case. Top-10: 16 tasks. Top-20: 26 tasks. Top-10/20 ranks are recovered from the **frozen database and stored parser states**, without rerunning Tree-sitter. The new `candidate_pool` is separate from the historical Top-3. The database SHA-256 and Top-3 frequencies/rank order are checked. Small Basic extra raw candidates are mapped through the frozen token-mapping file.

**Full C default = potentially hundreds of thousands of API requests; never run before full-run review and real smoke validation.** Top-K experiments should use a scientifically selected subset rather than blindly expanding all cursors.

## 5. Legacy prompt parity

Prefix-only Small Basic with `--profile lab` imports the actual legacy `hj_benchmark_tools.make_prompt`. C FIM imports the actual legacy `PREFIX_SUFFIX` constant. A static offline comparison tool is included:

```bash
# Replace both paths with actual original and v2 files of the same cases.
.venv/bin/python tools/check_v2_parity.py \
  --original results/sb-smoke/cases_mapped.jsonl \
  --v2 "$(find results/v2/smallbasic/grok/smoke -name cases.jsonl | head -n1)"
```

For C parity, **only** compare v2 `--context full --completion infill` to the original C `cases.jsonl`; new C prefix-only contexts are intentionally different and should fail a "same prompt" expectation.

## 6. Rollback/promotion

The old six-task `run_c.py` and `run_smallbasic.py` are **untouched**. The new engine is used only by `run_c_v2.py` and `run_smallbasic_v2.py`. There is no need to edit or delete the legacy runner files during testing. After the actual WSL smoke run, scoring, RQ2 reports and parity tests are confirmed, the two old entrypoints can be swapped to delegate to `benchmark_v2.cli.main`. Keep the old six-task behavior available as an explicit `legacy` profile or in archived scripts until there is a signed-off result parity test. Do not silently merge the old 6-task data into the v2 9-task cohort.

## 7. Important limitations and research controls

- The native HJ parser and corpus preparation (`prepare_hj_benchmark.py`, `run_hj_c_batch.py`) remain in the repository and are **reused**. The four separate **LLM generation scripts are no longer used by the v2 engine**. No provider script was deleted.
- Ideal guidance gives the model reference **structure**, which makes it an **oracle experiment**. It is not directly deployable.
- `end_completeness_unverified_exploratory` still applies to all text reference spans. FIM requires explicit opt-in because it is sensitive to reference end boundaries.
- Newlines, output code fences, and other raw response text are **not automatically rewritten** before scoring. Exact text and grammar/syntax are different evaluation dimensions.
- C 10-line prefix is the **new treatment**. Never present its scores as matched with historical C FIM experiments.
- `monir-style` is a conceptual reconstruction, not exact Monir API payload parity. It is a separate experimental profile.
- Synthetic tests demonstrate expected state, API adapter contract and resume logic; actual xAI/OpenAI/Groq calls, complete WSL HJ assets, and real SacreBLEU results must still be verified on your machine.
- `tools/rq2_report.py` from the earlier add-on is used for reporting. It must be present; otherwise scoring still works but automatic graphs are skipped with a warning.
- Retrieval Top-K reporting uses unfiltered HJ `prepared.jsonl` where present. For a **smoke** run the denominator only includes the relevant source file(s); to report full corpus coverage, run a separate analysis over all HJ prepared cases.

## 8. Suggested commit checkpoint

After test success (and **before** paid v2 smoke):

```bash
git status --short
git add benchmark_v2/ run_c_v2.py run_smallbasic_v2.py tests/test_engine_v2.py tests/test_cli_v2.py tools/check_v2_parity.py PHASE2_README.md BASELINE_SMOKE_SHA256.txt
git commit -m "Add unified RQ2 engine with ideal guidance and safe resume"
```

Do not push `results/`, credentials, secret environment variables, or raw API responses into the repository.
