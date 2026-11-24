"""Read-only provenance and reference-span diagnostics for HJ prepared cases.

Important: byte matching and lexical-token boundaries cannot establish that a
reference is a *complete grammar unit*. Output labels are audit evidence, not
certification for a final research benchmark. No parser/API calls or mutations.
"""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PUNCTUATION = re.compile(r"^[^\w\s]+$")
IDENT_BYTE = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def c_lexical_context_at(source, end):
    """Return a conservative C lexical context just before byte offset ``end``.

    Only used after a possible identifier-boundary warning. Comments and quoted
    text may contain adjacent letters without representing a split C identifier.
    This is a scanner for *diagnostics*, not a Tree-sitter grammar validator.
    """
    if not 0 <= end <= len(source):
        raise ValueError("C lexical context offset is outside the source")
    state = "code"
    i = 0
    while i < end:
        byte = source[i]
        next_byte = source[i + 1] if i + 1 < end else None
        if state == "code":
            if byte == 47 and next_byte == 47:  # //
                state = "line_comment"
                i += 2
                continue
            if byte == 47 and next_byte == 42:  # /*
                state = "block_comment"
                i += 2
                continue
            if byte == 34:  # double quote
                state = "string_literal"
            elif byte == 39:  # single quote
                state = "character_literal"
        elif state == "line_comment":
            if byte in (10, 13):  # newline
                state = "code"
        elif state == "block_comment":
            if byte == 42 and next_byte == 47:  # */
                state = "code"
                i += 2
                continue
        elif state in ("string_literal", "character_literal"):
            if byte == 92:  # escaped next byte, including escaped quote
                i += 2
                continue
            if (state == "string_literal" and byte == 34) or (state == "character_literal" and byte == 39):
                state = "code"
        i += 1
    return state


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path):
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("case must be an object")
                yield obj
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid JSONL at {path}:{number}: {exc}") from exc


def discover_inputs(language, paths=(), root=Path("results")):
    """Discover baseline HJ prepared cases, not v2 generated cases."""
    if paths:
        found = []
        for arg in paths:
            path = Path(arg).expanduser()
            if path.is_file() and path.suffix == ".jsonl":
                found.append(path)
            elif path.is_dir():
                found.extend(sorted(path.rglob("prepared.jsonl")))
            else:
                raise ValueError(f"No JSONL input or preparation directory: {path}")
    elif language == "smallbasic":
        found = [root / "sb-smoke" / "prepared.jsonl"]
    else:
        found = sorted((root / "c-prepared" / "files").rglob("prepared.jsonl"))
        if not found:
            found = sorted((root / "c-prepared" / "files").rglob("cases.jsonl"))
    paths = sorted(set(Path(item) for item in found if Path(item).is_file()))
    if not paths:
        raise ValueError(f"No HJ prepared cases for {language}. Pass --input <prepared.jsonl|directory>.")
    return paths


def text_kind(text):
    """Purely lexical heuristics; no assertion of syntax/semantic completeness."""
    if not text:
        return "empty"
    if IDENTIFIER.fullmatch(text):
        return "single_identifier_or_keyword"
    if PUNCTUATION.fullmatch(text):
        return "punctuation_fragment"
    if "\n" in text or "\r" in text:
        return "multiline_fragment"
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return "numeric_token"
    if len(text.split()) > 1 or any(char in text for char in "()[]{};=+-*/<>\""):
        return "compound_fragment"
    return "other_fragment"


def _load_bytes(path, cache):
    path = str(path)
    if path not in cache:
        try:
            cache[path] = (Path(path).read_bytes(), None)
        except OSError as exc:
            cache[path] = (None, str(exc))
    return cache[path]


def _load_reference_report(path, cache):
    path = str(path)
    if path not in cache:
        data, error = _load_bytes(path, cache.setdefault("_byte_cache", {}))
        if error:
            cache[path] = (None, None, error)
        else:
            try:
                report = json.loads(data)
                if not isinstance(report, dict):
                    raise ValueError("report must be a JSON object")
                cache[path] = (report, _sha(data), None)
            except (ValueError, UnicodeError) as exc:
                cache[path] = (None, _sha(data), str(exc))
    return cache[path]


def _reference_in_report(ref, raw_entries):
    if not isinstance(raw_entries, list):
        return False
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        if item.get("candidate_text") != ref.get("text") or item.get("candidate") != ref.get("structure"):
            continue
        state = ref.get("collection_state_id")
        if state is None or str(item.get("state_id")) == str(state):
            return True
    return False


def _rank_check(case, ref):
    rank = ref.get("retrieval_rank")
    if rank is None:
        return "not_retrieved_or_unknown"
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        return "invalid_rank"
    top = case.get("top_candidates") or []
    if 1 <= rank <= len(top):
        predicted = top[rank - 1].get("structure", "")
        actual = ref.get("structure", "")
        if isinstance(predicted, str) and isinstance(actual, str) and predicted.replace(" ", "") == actual.replace(" ", ""):
            return "rank_matches_top_candidates"
        return "rank_conflicts_top_candidates"
    return "rank_not_checkable_from_top3"


def inspect_case(case, source_cache, report_cache):
    """Yield one audit row per reference, retaining cases with zero references."""
    offset = case.get("cursor_byte")
    try:
        offset = int(offset)
    except (ValueError, TypeError):
        offset = -1
    ref_list = case.get("references") or []
    if not isinstance(ref_list, list):
        ref_list = []
    source_path = case.get("source_path")
    source, source_error = _load_bytes(source_path, source_cache) if source_path else (None, "source_path missing")
    report_path = case.get("reference_path")
    report, report_sha, report_error = (_load_reference_report(report_path, report_cache) if report_path
                                        else (None, None, "reference_path missing"))

    for ref_idx, ref in enumerate(ref_list if ref_list else [None]):
        issues = []
        notes = []
        row = {"case_id": case.get("case_id", ""), "language": case.get("language", ""),
               "source_relative": case.get("source_relative", ""), "cursor_byte": offset,
               "status": case.get("status", ""), "reference_count": len(ref_list),
               "ref_index": ref_idx if ref is not None else "", "reference_text": "",
               "reference_structure": "", "reference_bytes": 0, "text_kind": "no_reference",
               "after_span": "", "source_suffix_preview": "", "retrieval_rank": "",
               "rank_check": "", "slice_verified": "no", "report_verified": "no",
               "boundary_confirmed": "no", "issues": "", "notes": ""}
        if len(ref_list) != 1:
            notes.append("reference_count_not_one")
        if source_error:
            issues.append("source_unavailable")
        elif _sha(source) != case.get("source_sha256"):
            issues.append("source_hash_mismatch")
        if report_error:
            issues.append("reference_report_unavailable")
        elif report_sha != case.get("reference_sha256"):
            issues.append("reference_report_hash_mismatch")
        if offset < 0 or (source is not None and offset > len(source)):
            issues.append("cursor_offset_invalid")

        if source is not None and 0 <= offset <= len(source):
            try:
                prefix = source[:offset].decode("utf-8")
                if prefix != case.get("prefix"):
                    issues.append("prefix_mismatch")
            except UnicodeDecodeError:
                issues.append("cursor_splits_utf8")
            if offset > 0 and offset < len(source) and source[offset - 1] in IDENT_BYTE and source[offset] in IDENT_BYTE:
                notes.append("cursor_inside_identifier")

        if ref is None:
            notes.append("no_reference")
        elif not isinstance(ref, dict) or not isinstance(ref.get("text"), str) or not isinstance(ref.get("structure"), str):
            issues.append("invalid_reference_schema")
        else:
            txt = ref["text"]
            encoded = txt.encode("utf-8")
            end = offset + len(encoded)
            row.update(reference_text=txt, reference_structure=ref["structure"],
                       reference_bytes=len(encoded), text_kind=text_kind(txt),
                       retrieval_rank=ref.get("retrieval_rank"),
                       rank_check=_rank_check(case, ref))
            if not txt:
                issues.append("empty_reference")
            if ref.get("start_byte") != offset:
                issues.append("start_byte_mismatch")
            if ref.get("end_byte_from_text") != end:
                issues.append("end_byte_mismatch")
            if source is not None and 0 <= offset <= len(source):
                if source[offset:end] != encoded:
                    issues.append("reference_source_slice_mismatch")
                else:
                    row["slice_verified"] = "yes"
                try:
                    source[:end].decode("utf-8")
                except UnicodeDecodeError:
                    issues.append("end_splits_utf8")
                if end == len(source):
                    row["after_span"] = "end_of_file"
                elif 0 <= end < len(source):
                    following = source[end]
                    previous = source[end - 1] if end > 0 else None
                    if following in IDENT_BYTE and previous in IDENT_BYTE:
                        # A byte-level letter/letter boundary is not sufficient to
                        # identify a C identifier: it may lie within a comment or
                        # a string/character literal. Preserve these as audit notes.
                        if case.get("language") == "c":
                            context = c_lexical_context_at(source, end)
                        else:
                            context = "code"
                        if context in ("line_comment", "block_comment"):
                            row["after_span"] = "inside_comment"
                            notes.append("lexical_word_continues_in_comment")
                        elif context in ("string_literal", "character_literal"):
                            row["after_span"] = "inside_quoted_literal"
                            notes.append("lexical_word_continues_in_quoted_literal")
                        else:
                            row["after_span"] = "inside_identifier"
                            issues.append("reference_ends_inside_identifier")
                    elif following in b" \t\n\r":
                        row["after_span"] = "whitespace"
                    elif following in IDENT_BYTE:
                        row["after_span"] = "identifier_start"
                    else:
                        row["after_span"] = "other_character"
                    row["source_suffix_preview"] = source[end:end+90].decode("utf-8", errors="replace")
            if report is not None:
                if _reference_in_report(ref, report.get(str(offset))):
                    row["report_verified"] = "yes"
                else:
                    issues.append("reference_not_in_original_HJ_report")
            if row["rank_check"] in {"rank_conflicts_top_candidates", "invalid_rank"}:
                issues.append(row["rank_check"])
            if row["text_kind"] == "single_identifier_or_keyword":
                notes.append("lexical_token_only_semantic_extent_unconfirmed")
            if row["text_kind"] == "multiline_fragment":
                notes.append("multiline_extent_requires_manual_review")
        # Important: never assert syntactic completion from source matching alone.
        notes.append("grammar_boundary_requires_manual_verification")
        row["issues"] = ";".join(sorted(set(issues)))
        row["notes"] = ";".join(sorted(set(notes)))
        yield row


def audit_prepared(language, paths=(), *, root=Path("results"), limit=8, export=None):
    if limit < 0:
        raise ValueError("--limit must be >= 0")
    inputs = discover_inputs(language, paths, root=root)
    source_cache = {}
    report_cache = {}
    counter = Counter()
    kinds = Counter()
    after = Counter()
    ranks = Counter()
    examples = []
    total = 0
    statuses = Counter()
    eligible_three = 0
    manual_review_rows = 0
    full_rows = [] if export is not None else None
    for path in inputs:
        for case in _read_jsonl(path):
            if case.get("language") != language:
                raise ValueError(f"Unexpected language {case.get('language')} in {path}")
            total += 1
            statuses[case.get("status", "missing_status")] += 1
            if (case.get("status") == "prepared" and len(case.get("references") or []) == 1
                and len(case.get("top_candidates") or []) >= 3
                and int(case.get("candidate_count", 0)) >= 3):
                eligible_three += 1
            for row in inspect_case(case, source_cache, report_cache):
                counter["references_checked"] += 1
                if "grammar_boundary_requires_manual_verification" in row["notes"]:
                    manual_review_rows += 1
                kinds[row["text_kind"]] += 1
                if row["after_span"]:
                    after[row["after_span"]] += 1
                if row["rank_check"]:
                    ranks[row["rank_check"]] += 1
                if row["issues"]:
                    counter["references_with_issues"] += 1
                    for issue in row["issues"].split(";"):
                        counter[f"issue:{issue}"] += 1
                if row["slice_verified"] == "yes":
                    counter["slice_matches"] += 1
                if row["report_verified"] == "yes":
                    counter["HJ_report_matches"] += 1
                if len(examples) < limit and (row["issues"] or row["notes"]):
                    examples.append(row)
                if full_rows is not None:
                    full_rows.append(row)
    print(f"HJ REFERENCE BOUNDARY AUDIT | {language} | READ-ONLY | files={len(inputs)} | cases={total}")
    print(f"References/empty-case rows={counter['references_checked']}; source slices matched={counter['slice_matches']}; "
          f"raw HJ report matches={counter['HJ_report_matches']}; rows with issues={counter['references_with_issues']}")
    print("Preparation statuses:", dict(statuses))
    print(f"Top-3 RQ2 eligibility before provenance audit: {eligible_three}/{total} cases")
    print(f"Manual grammar-boundary review required: {manual_review_rows} reference/empty-case rows")
    print("Lexical shape (heuristic):", dict(kinds))
    print("After reference:", dict(after))
    print("Retrieval checks:", dict(ranks))
    print("Issues:", {key.split(':', 1)[1]: value for key, value in counter.items() if key.startswith("issue:")})
    for row in examples:
        print(f"  {row['source_relative']} byte={row['cursor_byte']} | "
              f"reference={row['reference_text'][:45]!r} structure={row['reference_structure'][:45]!r} "
              f"kind={row['text_kind']} next={row['source_suffix_preview'][:45]!r}")
        print(f"    issues={row['issues'] or 'none'} | notes={row['notes']}")
    print("NOTE: 'slice matched' verifies bytes only; it does NOT certify syntax/grammar completion.")
    print("NOTE: Token/fragment labels are lexical heuristics, not Tree-sitter AST labels.")
    print("NOTE: Do not use this audit as an automatic eligibility filter for a full benchmark.")
    if export is not None:
        output = Path(export)
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite existing audit: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=list(full_rows[0]) if full_rows else [
                "case_id", "language", "source_relative", "cursor_byte", "issues"])
            writer.writeheader()
            writer.writerows(full_rows)
        print(f"Saved audit CSV: {output}")
    return 0 if counter['references_with_issues'] == 0 else 1
