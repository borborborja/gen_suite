"""Conservative comparison of PG16 catalog deparsing after a dump/restore.

PG may move varchar→text casts from an ARRAY to each literal element on reparse.
Only this exact, typmod-free literal-array equivalence is recognized. Everything
else (names, predicates, casts, values, ordering, policies, grants) remains compared.
No SQL is executed and the original manifest is never rewritten.
"""
from __future__ import annotations

import re

TOKENS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|::|[A-Za-z_][A-Za-z_0-9$]*|\s+|.", re.DOTALL)


def array_cast(tokens, start):
    wrapped = tokens[start] == "("
    index = start + int(wrapped)
    if tokens[index:index + 2] != ["ARRAY", "["]: return None
    index += 2
    literals, conversions = [], []
    while index < len(tokens):
        parenthesized = tokens[index] == "("
        index += int(parenthesized)
        if index >= len(tokens) or not tokens[index].startswith("'"): return None
        literal = tokens[index]; index += 1
        if tokens[index:index + 3] != ["::", "character", "varying"]: return None
        index += 3
        if parenthesized:
            if tokens[index:index + 1] != [")"]: return None
            index += 1
        converted = tokens[index:index + 2] == ["::", "text"]
        index += 2 * int(converted)
        literals.append(literal); conversions.append(converted)
        if tokens[index:index + 1] == ["]"]:
            index += 1; break
        if tokens[index:index + 1] != [","]: return None
        index += 1
    else: return None
    if wrapped:
        if tokens[index:index + 1] != [")"]: return None
        index += 1
    whole_cast = tokens[index:index + 4] == ["::", "text", "[", "]"]
    if whole_cast: index += 4
    # Normalize either a varchar[] cast to text[] or literals each cast to text.
    # Do not discard arbitrary parentheses or coerce uncast/mixed/typed arrays.
    if not ((whole_cast and not any(conversions)) or (not whole_cast and not wrapped and all(conversions))):
        return None
    replacement = ["ARRAY", "["]
    for literal in literals:
        if len(replacement) > 2: replacement.append(",")
        replacement += [literal, "::", "text"]
    return index, replacement + ["]"]


def comparison_key(sql):
    if not isinstance(sql, str): return ("unrecognized", repr(sql))
    # Expressions outside this deliberately small lexical subset require exact
    # equality, including escapes, comments and dollar-quoted strings.
    if len(sql) > 1024 * 1024 or any(value in sql for value in ("\\", "$", "--", "/*")):
        return ("exact", sql)
    tokens = [token for token in TOKENS.findall(sql) if not token.isspace()]
    output, index, changed = [], 0, False
    while index < len(tokens):
        found = array_cast(tokens, index) if tokens[index] in ("ARRAY", "(") else None
        if found:
            index, replacement = found; output.extend(replacement); changed = True
        else:
            output.append(tokens[index]); index += 1
    return ("literal-text-array/v1", tuple(output)) if changed else ("exact", sql)


def metadata_matches(actual, expected):
    if not isinstance(actual, dict) or not isinstance(expected, dict) or actual.keys() != expected.keys():
        return False
    for key, left in actual.items():
        right = expected[key]
        if left == right: continue
        field = {"constraints": "definition", "indexes": "indexdef"}.get(key)
        if not field or not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        for first, second in zip(left, right):
            if not isinstance(first, dict) or not isinstance(second, dict) or field not in first or field not in second:
                return False
            if {k: v for k, v in first.items() if k != field} != {k: v for k, v in second.items() if k != field}:
                return False
            if comparison_key(first[field]) != comparison_key(second[field]): return False
    return True
