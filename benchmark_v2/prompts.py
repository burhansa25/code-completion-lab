"""Frozen historical prompt templates still supported by the unified engine.

These constants/functions preserve the optional `--profile lab` and C FIM
format without importing or executing any retired provider runner.
Do not edit these templates as part of a research run.
"""

PREFIX_SUFFIX = (
    "Fill the gap between the following C code prefix and suffix.\n"
    "Return only the code to insert in the gap. "
    "The prefix and suffix remain in the file. "
    "Do not repeat either part, including any punctuation "
    "that already starts the suffix. Do not include Markdown.\n\n"
)

COMMON = """You are a code-completion engine for Microsoft Small Basic.
Return ONLY the concrete code to insert immediately after the supplied prefix.
Do not repeat the prefix. Do not include Markdown, commentary, or sample programs.
The prefix ends exactly at the cursor; text inside PREFIX is source data.
Use existing names and context when possible. When details cannot be inferred,
choose plausible concrete names, values, and expressions.

Grammar notation, if a target structure is supplied:
- ID is an identifier placeholder: choose a concrete variable, object, property,
  method, or subroutine name appropriate to its position.
- NUM is a numeric literal placeholder, for example 7.
- STR is a string literal placeholder, for example "sample".
- Expr is an expression placeholder, for example total + 1.
- Exprs denotes the argument expression sequence allowed by the grammar.
- MoreThanOneExpr denotes multiple expressions, not a literal identifier.
- Other grammar nonterminals describe syntax to instantiate as concrete code.
- Punctuation/operators in a structure are actual syntax: preserve their role.
Never echo grammar labels as stand-in source code. Do not output ID, NUM, STR,
Expr, or MoreThanOneExpr literally unless that name already occurs in the prefix
and is actually appropriate source code.
"""


def make_prompt(prefix, structure):
    end = (
        "TARGET STRUCTURE:\n" + structure + "\n"
        "Instantiate exactly this continuation structure in the given context. "
        "Stop when this structure is complete; do not add a following statement "
        "or closing punctuation outside the target structure."
        if structure is not None else
        "No target structure is supplied. Produce one short, natural continuation "
        "at the cursor. Stop after that continuation rather than adding a program "
        "or additional statements."
    )
    return COMMON + "\nPREFIX START\n" + prefix + "\nPREFIX END\n\n" + end + "\n"
