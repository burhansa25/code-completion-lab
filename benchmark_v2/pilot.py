"""Deterministic, conservative pilot selection from frozen HJ prepared cases.

This is an experimental sampling aid, not a grammar correctness certificate.
No network, no source changes, no API calls.  The first primary pilot excludes
comment/string contexts in C; those deserve a separate diagnostic cohort.
"""
import hashlib
from pathlib import Path

from .cases import eligible, read_jsonl
from .boundaries import c_lexical_context_at, text_kind


STRATA = ("single_identifier_or_keyword", "punctuation_fragment", "compound_fragment",
          "multiline_fragment", "numeric_token", "other_fragment")


def pilot_candidates(language, sources, top_k):
    """Yield (source, case, stratum). All eligibility checks are answer-blind."""
    if language not in ("c", "smallbasic"):
        raise ValueError("Unknown pilot language")
    cached_sources = {}
    for source_name, cases_path in sources:
        for case in read_jsonl(cases_path):
            if not eligible(case, top_k) or int(case.get("cursor_byte", 0)) == 0:
                continue
            reference = case["references"][0]
            text = reference.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            if case.get("language") != language:
                raise ValueError("Prepared file language mismatch")
            # Guard against exposing an accidental source/reference mismatch.
            path = Path(case["source_path"])
            if path not in cached_sources:
                cached_sources[path] = path.read_bytes()
            contents = cached_sources[path]
            cursor = int(case["cursor_byte"])
            end = cursor + len(text.encode("utf-8"))
            if hashlib.sha256(contents).hexdigest() != case.get("source_sha256"):
                raise ValueError(f"Source SHA mismatch at {case['case_id']}")
            try:
                source_prefix = contents[:cursor].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Cursor splits UTF-8 at {case['case_id']}") from exc
            if source_prefix != case.get("prefix"):
                raise ValueError(f"Prefix/source mismatch at {case['case_id']}")
            if contents[cursor:end] != text.encode("utf-8"):
                raise ValueError(f"Reference/source mismatch at {case['case_id']}")
            if language == "c":
                # A fragment may open+close a comment while both endpoints
                # remain in 'code'; exclude explicit comment delimiters too.
                if (c_lexical_context_at(contents, cursor) != "code" or
                        c_lexical_context_at(contents, end) != "code" or
                        any(marker in text for marker in ("//", "/*", "*/"))):
                    continue
            kind = text_kind(text)
            if kind == "empty":
                continue
            # A single SB JSONL contains many original source files; stratify
            # by the original source filename, never the JSONL batch label.
            true_source_name = (case.get("source_relative") or source_name)
            yield true_source_name, case, kind


def _stratified_groups(language, sources, *, size, seed, top_k, exclude=(), max_size=32):
    """Deterministic lexical/file-diverse selection, with optional held-out exclusions."""
    if not 1 <= size <= max_size:
        raise ValueError(f"Cohort size must be between 1 and {max_size}")
    if not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("Pilot seed must be a nonnegative 32-bit integer")
    buckets = {k: [] for k in STRATA}
    seen = set()
    excluded = set(exclude)
    for name, case, kind in pilot_candidates(language, sources, top_k):
        identity = (name, int(case["cursor_byte"]))
        if identity in seen:
            raise ValueError(f"Duplicate pilot cursor: {identity}")
        seen.add(identity)
        if (name, case["case_id"], int(case["cursor_byte"])) in excluded:
            continue
        buckets.setdefault(kind, []).append((name, case))

    def stable_score(item):
        name, case = item
        token = f"{seed}|{language}|{name}|{case['case_id']}|{case['cursor_byte']}"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    for group in buckets.values():
        group.sort(key=stable_score)
    selected = []
    used_files = set()
    used_cursor = set()
    # Two passes: first maximize file diversity, then allow repeated files.
    for require_new_file in (True, False):
        while len(selected) < size:
            changed = False
            for kind in STRATA:
                group = buckets.get(kind, [])
                item = next((it for it in group
                             if (it[0], it[1]["cursor_byte"]) not in used_cursor
                             and (not require_new_file or it[0] not in used_files)), None)
                if item is None:
                    continue
                selected.append(item)
                used_files.add(item[0]); used_cursor.add((item[0], item[1]["cursor_byte"]))
                changed = True
                if len(selected) == size:
                    break
            if not changed:
                break
    if len(selected) < size:
        raise ValueError(f"Only {len(selected)} eligible held-out cursors; requested {size}")
    by_source = {}
    for name, case in selected:
        by_source.setdefault(name, []).append(case)
    return list(by_source.items())


def stratified_pilot(language, sources, *, size=6, seed=2026, top_k=3):
    """Select an exploratory cohort; sample is not population-representative."""
    return _stratified_groups(language, sources, size=size, seed=seed,
                              top_k=top_k, max_size=32)


def stratified_study(language, sources, *, size=30, seed=2027, top_k=3):
    """Select a held-out study cohort, excluding the frozen primary pilot.

    Reconstruct the original six-cursor pilot directly from its public seed,
    not from model-specific result folders. This guarantees the same exclusion
    when an independent user prepares the same corpus from a fresh clone.
    Return (study_groups, excluded_pilot_groups) for auditability.
    """
    if not 1 <= size <= 120:
        raise ValueError("Study size must be between 1 and 120")
    pilot_groups = stratified_pilot(language, sources, size=6, seed=2026,
                                    top_k=top_k)
    excluded = {(name, c["case_id"], int(c["cursor_byte"]))
                for name, items in pilot_groups for c in items}
    study_groups = _stratified_groups(language, sources, size=size, seed=seed,
                                       top_k=top_k, exclude=excluded, max_size=120)
    return study_groups, pilot_groups
