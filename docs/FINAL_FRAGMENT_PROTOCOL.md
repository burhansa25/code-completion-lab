# Code Completion Lab — frozen fragment-completion pilot protocol (v1)

**Status:** proposed protocol implemented for deterministic offline preparation; **not** a certified Tree-sitter reference-boundary standard. Do not describe the pilot as a full benchmark until real HJ and provider runs have been verified. Historical `lab` and `monir-style` experiments are retained as separate profiles and are not pooled with `fragment-v1`.

## 1. Research question and experimental unit

- Unit: a **single HJ-collected reference fragment at a cursor**, not a complete program or function. The reference is immutable; it is never dynamically extended/shortened based on an LLM prediction.
- Primary comparison: text similarity against exactly that reference under identical cursor/prompt context and API settings. The core research question is whether structural guidance improves fragment matching.
- Conditions per case: Without Guidance ×3, Top-1 ×1, Top-2 ×1, Top-3 ×1, Ideal Structure ×3; total **9** API calls per cursor and model. `WithinTop3Guide` is an **oracle** max over the three guided predictions; it is not the deployable performance of a selected candidate. Ideal Structure knows the reference grammar structure, not the reference text, and is also an oracle.
- Primary outcomes: raw SequenceMatcher (0–100%) and SacreBLEU **1-gram precision** (0–100%). Sentence BLEU-13a, exact text match, and Markdown-fence-normalized matches are supplementary. Raw results stay unchanged. The 1-gram precision convention is closer to Monir's reported metric, **not** proof of an identical protocol.
- Evaluate a final held-out cohort using frozen prompts; the previously inspected HelloWorld cases are development/pilot evidence only.

## 2. Prompt & context (profile `fragment-v1`)

- The base prompt requests **one compact continuation fragment**, not an entire program; prevents explanations/code fences; and gives identical task directions to every condition within a language.
- Guided conditions append an HJ grammar structure; Without does not. Ideal uses the reference **structure** only. No reference text or reference length is given to the model.
- C uses the last ten pre-cursor lines by default, prefix-only; Small Basic uses full prefix and grammar placeholders. `--profile lab` preserves previous experimental wording, `--profile monir-style` is only an adaptation, and historical FIM is restricted to `--profile lab --context full --completion infill --allow-unverified-fim`.
- This protocol **cannot guarantee equal completion boundaries** across arms when the hidden reference is e.g. `int`; the boundary ambiguity is a research limitation and should be interpreted alongside output-length distributions and normalized outcomes. Do not suppress that limitation in the paper.

## 3. Pre-registered pilot cohort and inclusion criteria

- Choose **six** eligible positions per language by default, `--pilot-size 6 --pilot-seed 2026` (max 32). Both models **must use the same case IDs**; seed and selected IDs are frozen to `cohort.json` with SHA-256.
- Inclusion: HJ status `prepared`, exactly one non-empty byte-matching reference, ≥3 retrieved candidates, cursor >0. Default C primary pilot excludes any reference whose cursor or end falls inside a lexical comment/string. Those are separate follow-up cohorts, **not proven parser defects**.
- Sampling uses lexically grouped reference shapes and file diversity, with SHA-256 deterministic order. These are **lexical proxies, not AST classes or representative random samples of the full distribution**. Do not make population estimates from this pilot.
- Do not automatically delete existing `prepared.jsonl` records based on heuristic `reference_ends_inside_identifier` or similar. Reference byte matching verifies provenance, not syntax/grammar completeness.
- Small Basic pilot uses the already prepared and mapped `results/sb-full/cases_mapped.jsonl` (27 paired sources; six missing HJ reports are outside the current pool); C uses the existing `results/c-prepared/summary.json` and 106 prepared sources.

## 4. Costs, run IDs, reproducibility

- Preparation and `--dry-run` never send API calls. For **pilot/full** generation use explicit `--stage run --yes`, with a hard cap `--max-api-requests` (default 200). Request count is 9 × selected cases for Top-3.
- Configuration, selected case IDs, prompt code hashes, model ID, and API generation settings contribute to the Run ID. All new results go to `results/v2/<language>/<model>/pilot/<run-id>` and never overwrite old `smoke/` runs.
- The temporary `run_c_v2.py` and `run_smallbasic_v2.py` remain usable until verified entrypoint promotion; **do not rerun historical `--stage report` with changed CLI hashes**. Use `python3 -m benchmark_v2 results` / `audit` to read old runs.
- The commands below use the **finalized primary entrypoints**. Before promotion, append `_v2` to the names (`run_c_v2.py` and `run_smallbasic_v2.py`).
- The reporting stage is read-only with respect to model API. Keep `results/`, tokens, and private source datasets out of Git. Preserve the old experiment as an immutable development baseline.

## 5. Validation gates (before paying for pilot)

1. Run all unit tests offline and check historical baseline SHA-256.
2. Run pilot `--stage prepare` for each language/model, verify **same selection SHA** across models and expected task counts (six cases ⇒ 54 calls per model/language).
3. Inspect `cohort.json` and at least several `cases.jsonl` prompts, ensuring the reference text was not injected by construction. The reference may naturally appear in prefix or grammar as part of a genuine oracle, so do not use naive substring checks.
4. Confirm the 27 Small Basic HJ pairs and 106 C prepared files are still available, dependencies installed, and actual API usage pricing/quotas reviewed.
5. Pilot/API execution **requires fresh, explicit consent** (via `--yes`) and still costs money; generating the patch is not authorization to incur charges.
6. Review length distributions, code fences, reference boundary limitations, oracle vs deployment results, and retrieval coverage before any full run. Full C means hundreds of thousands of API requests with current coverage; protect with explicit cap and do not blindly run full scope.

## 6. Offline commands

```bash
# From the repository root; preparers already produced the input HJ JSONL.
.venv/bin/python run_c.py --scope pilot --pilot-size 6 --stage prepare --model grok
.venv/bin/python run_smallbasic.py --scope pilot --pilot-size 6 --stage prepare --model grok
.venv/bin/python run_c.py --scope pilot --pilot-size 6 --stage prepare --model gpt
.venv/bin/python run_smallbasic.py --scope pilot --pilot-size 6 --stage prepare --model gpt

# View historical completed results (no API calls).
python3 -m benchmark_v2 results
```

**Only after approving expected request count, prompt, and cohort:**

```bash
# Each line sends up to 54 paid API calls (for pilot-size 6, Top-3):
.venv/bin/python run_c.py --scope pilot --pilot-size 6 --stage run --model grok --yes
.venv/bin/python run_smallbasic.py --scope pilot --pilot-size 6 --stage run --model grok --yes
.venv/bin/python run_c.py --scope pilot --pilot-size 6 --stage run --model gpt --yes
.venv/bin/python run_smallbasic.py --scope pilot --pilot-size 6 --stage run --model gpt --yes
```

Then: `python3 -m benchmark_v2 results --scope pilot`, `python3 -m benchmark_v2 audit --language all --model all --scope pilot`.

## 7. Promotion to two main entrypoints

Once WSL offline unit/integration tests and pilot *preparation* have passed, replace the old wrappers (preserved in Git history) with the already-tested v2 3-line entrypoints:

```bash
cp run_c_v2.py run_c.py
cp run_smallbasic_v2.py run_smallbasic.py
# Verify: git diff -- run_c.py run_smallbasic.py
# Optional after all compatibility checks: git rm run_c_v2.py run_smallbasic_v2.py
```

Do not delete `benchmark_entry.py` or legacy provider files as part of this change; they remain accessible to reproduce the historical six-task protocol, though no longer called by the primary entrypoints. Keep a Git tag to locate the last v1 runner checkpoint if necessary.

## 7. Held-out main-study cohort (after the exploratory pilot)

The research run is named `--scope study`; **never use `--scope full` to mean a
small sample**. The default study cohort uses 30 cases per language and seed
2027. It reconstructs and excludes the frozen six-case pilot (`size=6, seed=2026`)
from the same prepared corpus, irrespective of model or the presence of saved
pilot results. A new `cohort.json` stores selected IDs, lexical strata,
selection SHA-256, and the excluded pilot SHA-256. Use equal study seed and
input data for GPT-4o and Grok; both must yield the same selection hash.

Sampling is **deterministic and balanced by lexical reference shape/file**,
not a simple random or probability-proportional sample. Therefore the study
estimates describe the chosen cohort, not necessarily all HJ positions. The
30-case initial size is a pragmatic research batch, not a statistical power
calculation. Report paired per-cursor differences and confidence intervals,
with uncertainty assessed at the source-file cluster level when relevant.
Do not use the same six pilot positions to choose prompt changes for the
held-out study. Exact reference boundaries are not AST-certified.

```bash
# Only prepares inputs and manifests; 0 API calls.
python run_c.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model grok
python run_c.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model gpt
python run_smallbasic.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model grok
python run_smallbasic.py --scope study --study-size 30 --study-seed 2027 --stage prepare --model gpt

# Examine the frozen manifests and API cost before any model calls.
# Each study run plans 30 * 9 = 270 requests, which exceeds the default 200
# request safety cap. Execution requires explicit --yes AND an increased cap.
```
