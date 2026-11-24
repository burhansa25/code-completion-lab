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


def stratified_pilot(language, sources, *, size=6, seed=2026, top_k=3):
    """Select unique cursor cases across lexical strata and files, deterministically.

    The same input/seed yields identical case IDs for every LLM provider.
    Strata are lexical *proxies*, not verified AST labels. This is a pilot only.
    """
    if not 1 <= size <= 32:
        raise ValueError("Pilot size must be between 1 and 32")
    if not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("Pilot seed must be a nonnegative 32-bit integer")
    buckets = {k: [] for k in STRATA}
    seen = set()
    for name, case, kind in pilot_candidates(language, sources, top_k):
        identity = (name, int(case["cursor_byte"]))
        if identity in seen:
            raise ValueError(f"Duplicate pilot cursor: {identity}")
        seen.add(identity)
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
        raise ValueError(f"Only {len(selected)} eligible pilot cursors; requested {size}")
    by_source = {}
    for name, case in selected:
        by_source.setdefault(name, []).append(case)
    return list(by_source.items())
