# Phase 3 — Reference boundary and prompt-alignment audit

**Status:** diagnostic implementation; does not certify final benchmark validity.
**Scope:** HJ-prepared source/reference evidence, and existing RQ2 predictions.
**No LLM API use.** Raw `scores.csv`, `responses.jsonl`, frozen `cases.jsonl`, and HJ preparation files are not modified.

## One-command checks

From repository root:

```bash
python3 -m benchmark_v2 audit --language all --model all --limit-cases 1
python3 -m benchmark_v2 boundaries --language all --limit 5
```

The first reuses all four existing language/model scored runs. `audit` reports raw and secondary Markdown-normalized exact match and SequenceMatcher. A *stray closing fence* removal is tentative and is separately labelled; it must **not** replace frozen raw metrics. To inspect every prediction use `--show-predictions` with a selected language/model.

The second audits **all original prepared cases**, not just the single cursor selected for the smoke test. Automatic discovery expects:

- C: `results/c-prepared/files/**/prepared.jsonl` (or `cases.jsonl` if preparation was imported without intermediate files).
- Small Basic: `results/sb-smoke/prepared.jsonl` (original unmapped HJ preparation).

If cases are elsewhere, specify one language and a file/directory explicitly:

```bash
python3 -m benchmark_v2 boundaries --language c --input results/c-prepared --limit 20
python3 -m benchmark_v2 boundaries --language smallbasic --input results/sb-full/prepared.jsonl --limit 20
```

Optional detailed *new* CSV export (refuses overwrite):

```bash
python3 -m benchmark_v2 boundaries --language c \
  --export results/audits/c-reference-boundaries-v1.csv
```

`boundaries` exits **1** when provenance/integrity issues are discovered and **2** on usage/error. Exit 0 means byte-level evidence was consistent, **not** that syntactic completeness was proven. `--language all` processes both languages; if either is missing or fails, returns a nonzero exit status.

## Evidence checked

1. Original source is present and SHA-256 matches `source_sha256`.
2. Cursor is inside file and aligned with valid UTF-8; stored prefix matches source before cursor.
3. Reference `start_byte` and `end_byte_from_text` match offset and encoded text length.
4. Reference text matches *exactly* the bytes at that offset; flag reference that ends *within* an ASCII identifier.
5. Original HJ reference-report JSON is present and SHA-256 matches `reference_sha256`.
6. `candidate_text`, `candidate`, and optional `state_id` correspond to the same cursor in the original report.
7. Recorded `retrieval_rank` matches `top_candidates` when that rank is 1–3; larger ranks cannot be verified from Top-3 alone.
8. Categorize reference into **heuristic** lexical shapes (identifier/keyword, punctuation, numeric, compound, multiline, other), and display source immediately **after** the reference.
9. Count original HJ cases considered eligible for a Top-3 RQ2 cohort **before** provenance checks. This is a count, not final approval.

`boundary_confirmed` is intentionally **always `no`**: lexical matching is not a formal grammar-boundary proof. There is no automatic alteration of source, references, span ends, prompt or runner.

## HJ interpretation

The `Candidate_Collection` branch README describes (1) traversing recorded SHIFT tokens, (2) using a REDUCE that consumes the cursor to find a grammatical unit, and (3) emitting multiple subsequences/windows of the unit. Thus *a short fragment can be a legitimate collection target*. `primitive_type` → `int` can be a valid lexical fragment without being a whole statement. Verify any stronger claim against the native collection code, the emitted raw JSON, and the study's chosen completion definition.

HJ source: https://github.com/SwlabTreeSitter/tree-sitter/tree/Candidate_Collection

Our adapter's `prepare_hj_benchmark.py:references_at` accepts a reference when `source_bytes[offset:offset+len(encoded)] == encoded`; it does **not** infer a syntactic unit end. `end_byte_from_text` is a **computed end**, not an independently observed HJ boundary. Therefore do **not** use it as proof of an FIM suffix boundary or statement completeness.

## Protocol decision before spending on more APIs

Choose and freeze a **single** objective for the main benchmark:

- **HJ fragment completion**: predict the same HJ-provided reference fragment span (token/fragment/structure), not an arbitrary whole-program completion. The neutral instructions must specify the same fragment-level objective for all arms. Reference *text* must not appear in prompts. Ideal guidance necessarily reveals the reference **structure** and remains an oracle/diagnostic upper bound, not a deployable condition.
- **Longer semantic code completion**: define a new, independently verifiable reference-span policy at a semantic or AST boundary and rebuild dataset/prompt/scoring **for all arms**. Never extend spans merely to reward observed predictions.

Current `v2_lab` C prompt says “next natural code continuation”; the Small Basic v2 prompt also asks for a short natural continuation. These instructions do **not** explicitly constrain completion to the HJ fragment, and the observed C examples sometimes generate full programs despite a token reference `int`. Flag this as **prompt-target mismatch**, not proof that a model or HJ parser is wrong.

Recommended primary reporting: preserve frozen raw exact, Sentence BLEU 13a, unigram precision, and SequenceMatcher. Add Markdown-normalized diagnostic scores separately, with full-fence vs tentative unmatched-fence flags. Keep Top-3 as **reference-conditioned oracle best-of-three**, not a real retriever selection method. Monir-style is labelled an adaptation until a prompt-for-prompt replication is verified.

**Stop/go for full benchmark**: no high-severity provenance issues; manually reviewed stratified reference samples (both languages and fragment categories); clearly specified completion objective; neutral prompt uniform across conditions; independently checked data leakage and Top-K coverage denominators; distinct run IDs for protocol changes; representative multi-cursor pilot; budget approval. Current one-cursor smoke tests do not meet these requirements.
